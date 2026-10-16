"""Our replies: store what ai_analyzer (C3) hands us, hold escalated ones for a human, send through Gmail.

C3 calls POST /internal/replies. Each inbound message gets at most one reply: the reply's message_id
is derived from the inbound id, so the same reply handed over twice is stored, and sent, once.

Sending happens after the reply is stored and committed (and only when SEND_REPLIES is on):
    created now, not escalated  -> send it
    created now, escalated      -> send_status "awaiting_approval": a human decides first
    already stored, approved    -> send it (a human approved it; approve_reply() re-published it to C3)
    already stored, failed      -> send it again (the earlier attempt failed)
    already stored, sent / awaiting_approval / rejected -> nothing
    stored before sending existed (send_status NULL) -> nothing, so old drafts are never sent by surprise
A failed send is recorded as send_status="failed" and the error is raised, so C3's event is retried.

Human approval of escalated replies (python -m app.cli approvals | approve <id> | reject <id>):
    awaiting_approval --approve--> approved --(REPLY_READY -> C3 -> save_reply)--> sent
                      --reject---> rejected   (never published, never sent)
"""

import base64
import logging
from email.utils import parseaddr

from app.clients import stream_client
from app.clients.gmail_client import gmail_client_for
from app.config import get_settings
from app.models import ProcessedMessage
from app.models.session import session_scope
from app.repositories import mailbox_repo, message_repo
from app.utils import email_text

log = logging.getLogger(__name__)


def save_reply(reply: dict) -> tuple[int, bool, str | None]:
    """Returns (processed_messages.id of the reply, whether it was newly stored, send_status)."""
    reply_id = f"local-reply-{reply['in_reply_to_id']}"  # our id, not Gmail's (that is gmail_sent_id)
    with session_scope() as session:
        existing = message_repo.find_by_gmail_id(session, reply["mailbox"], reply_id)
        if existing is not None:
            log.info("reply to msg %d already stored as id=%d (send_status=%s)",
                     reply["in_reply_to_id"], existing.id, existing.send_status)
            row_id, created = existing.id, False
            # Retry a failed send, or send what a human approved; anything else was already decided.
            should_send = existing.send_status in ("failed", "approved")
        else:
            row_id, created = _store_new_reply(session, reply, reply_id), True
            should_send = not reply.get("escalate")

    if get_settings().send_replies and should_send:
        return row_id, created, _send(row_id)
    return row_id, created, _status(row_id)


def awaiting_approval(mailbox: str) -> list[ProcessedMessage]:
    """Escalated replies waiting for a human, oldest first."""
    with session_scope() as session:
        return message_repo.replies_with_send_status(session, mailbox, "awaiting_approval")


def approve_reply(row_id: int) -> None:
    """Mark the reply approved and hand it to C3 again on REPLY_READY; C3 -> save_reply() sends it."""
    with session_scope() as session:
        row = _awaiting(session, row_id)
        row.send_status = "approved"
        event = {
            "event_id": f"approval-{row.id}",
            "approved_reply_id": row.id,     # tells C3: a human decided, skip the "is it stale?" check
            "mailbox": row.email,
            "thread_id": row.thread_id,
            "in_reply_to_id": row.in_reply_to_id,
            "reply": row.body_text,
            "reply_kind": row.reply_kind,
            "escalate": True,
        }
    stream_client.publish(stream_client.REPLY_READY, event)  # after the commit: C3 always sees "approved"
    log.info("reply %d approved and handed to C3", row_id)


def reject_reply(row_id: int) -> None:
    """A rejected reply is never published and never sent."""
    with session_scope() as session:
        _awaiting(session, row_id).send_status = "rejected"
    log.info("reply %d rejected; it will not be sent", row_id)


# ------------------------------------------------------------------ storing

def _store_new_reply(session, reply: dict, reply_id: str) -> int:
    """Add the reply as an outbound row and return its id. An escalated reply waits for a human."""
    inbound = message_repo.get(session, reply["in_reply_to_id"])
    row = message_repo.add(session, ProcessedMessage(
        email=reply["mailbox"],
        message_id=reply_id,
        thread_id=reply["thread_id"],
        direction="outbound",
        in_reply_to_id=reply["in_reply_to_id"],
        user_id=inbound.user_id if inbound else None,  # the reply belongs to the same customer
        sender=reply["mailbox"],
        subject=reply["subject"],
        body_text=reply["reply"],
        reply_kind=reply.get("reply_kind"),
        send_status="awaiting_approval" if reply.get("escalate") else None,
    ))
    log.info("stored reply id=%d (in reply to msg %d, thread %s)",
             row.id, reply["in_reply_to_id"], reply["thread_id"])
    if reply.get("escalate"):
        log.info("reply id=%d is escalated: awaiting human approval, not sent", row.id)
    return row.id


def _awaiting(session, row_id: int) -> ProcessedMessage:
    """The reply with this id, if it is waiting for a human; raises ValueError otherwise."""
    row = message_repo.get(session, row_id)
    if row is None or row.direction != "outbound" or row.send_status != "awaiting_approval":
        status = row.send_status if row else "no such reply"
        raise ValueError(f"reply {row_id} is not awaiting approval (status: {status})")
    return row


def _status(row_id: int) -> str | None:
    with session_scope() as session:
        return message_repo.get(session, row_id).send_status


# ------------------------------------------------------------------ sending

def _send(row_id: int) -> str:
    """Send a stored reply through Gmail and record the outcome. Raises if sending failed."""
    failure = None
    with session_scope() as session:
        row = message_repo.get(session, row_id)
        inbound = message_repo.get(session, row.in_reply_to_id)
        if inbound is None or inbound.user_id is None:  # only verified customers ever get email from us
            log.warning("not sending reply %d: inbound msg %s has no verified customer",
                        row_id, row.in_reply_to_id)
            return row.send_status
        try:
            row.gmail_sent_id = _send_in_thread(session, inbound, row.subject, row.body_text)
            row.send_status = "sent"
            log.info("SENT reply %d to %r (gmail id %s, thread %s)",
                     row_id, inbound.sender, row.gmail_sent_id, row.thread_id)
        except Exception as error:  # noqa: BLE001  recorded, then raised below so C3 retries
            row.send_status = "failed"
            failure = error
            log.exception("sending reply %d failed; will be retried", row_id)
    if failure is not None:
        raise failure
    return "sent"


def _send_in_thread(session, inbound: ProcessedMessage, subject: str, body: str) -> str:
    """Send `body` as a reply to the inbound message, threaded under it. Returns Gmail's id.

    Gmail threads a reply when the threadId, the subject ("Re: ...") and the In-Reply-To /
    References headers all match the original. The session is committed by the caller, so a
    refreshed OAuth token is saved too.
    """
    gmail = gmail_client_for(mailbox_repo.get(session, inbound.email))
    original = gmail.message_headers(inbound.message_id, ["Message-ID", "References", "Reply-To", "From"])
    reply_to = (
        email_text.header(original, "Reply-To")
        or email_text.header(original, "From")
        or inbound.sender
    )
    message = email_text.build_reply(
        mailbox=inbound.email,
        to=parseaddr(reply_to)[1],
        subject=subject,
        body=body,
        original_message_id=email_text.header(original, "Message-ID"),
        original_references=email_text.header(original, "References"),
    )
    raw_email = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return gmail.send(raw_email, thread_id=inbound.thread_id)
