"""C3's logic: store the replies the agent (C1) wrote, unless they are already out of date.

Reads REPLY_READY: {mailbox, thread_id, in_reply_to_id, last_summary_msg_id, reply, escalate, ...}

Before storing, C3 checks the thread: if a newer inbound message arrived after the agent read the
thread (id > last_summary_msg_id), the reply may answer the wrong question, so C3 drops it. The next
batch covers the newer message and the agent writes a new reply then.

The main app stores the reply (POST /internal/replies) and, when SEND_REPLIES is on, sends it through
Gmail in the same thread. If sending fails the main app answers 500, so this event is retried.

Escalated replies are stored "awaiting_approval" and not sent. When a human approves one, the main app
publishes it to REPLY_READY again (approved_reply_id); C3 then hands it straight back and it is sent.

C1 already saved the agent's run and a PENDING review in our own DB (event["agent_run_id"]); C3 records
what happened to the reply: stored (with its processed_messages id) or dropped (review SKIPPED).
"""

import logging
import textwrap

from ai_analyzer.clients.main_app_client import get_message, get_messages, store_reply
from ai_analyzer.services.review_service import reply_not_sent, reply_stored

log = logging.getLogger(__name__)


# How the main app's send_status reads in our log.
SEND_OUTCOMES = {
    "sent": "SENT via Gmail",
    "failed": "send FAILED (will retry)",
    "awaiting_approval": "AWAITING APPROVAL (escalated): python -m app.cli approve|reject <id>",
}


def handle_reply(event: dict) -> None:
    """Store (and maybe send) the agent's reply, unless it is out of date or answers automated mail."""
    mailbox, thread_id = event["mailbox"], event["thread_id"]
    last_msg_id = event.get("last_summary_msg_id")

    # A human approved this escalated reply (app.cli approve): send it, even if newer mail arrived.
    if event.get("approved_reply_id"):
        send_approved_reply(event)
        return

    # Inbound only (that's what get_messages returns): our own stored replies don't make it stale.
    newer = get_messages(mailbox, thread_id, after=last_msg_id)
    if newer:
        log.info("dropping reply for thread %s (agent read up to msg %d): newer message(s) %s arrived; "
                 "the next batch will cover them", thread_id, last_msg_id, [m["id"] for m in newer])
        skip_review(event)
        return

    message = get_message(mailbox, event["in_reply_to_id"])
    if message["is_automated"]:  # C1 already skips these; checked again in case of an old event
        log.info("msg %d from %r is automated; not storing a reply", message["id"], message["from"])
        skip_review(event)
        return

    stored = store_reply({
        "mailbox": mailbox,
        "thread_id": thread_id,
        "in_reply_to_id": message["id"],
        "subject": reply_subject(message["subject"]),
        "reply": event["reply"],
        "reply_kind": event.get("reply_kind"),
        "escalate": event.get("escalate", False),  # escalated replies wait for a human's approval
    })
    if event.get("agent_run_id"):
        reply_stored(event["agent_run_id"], reply_id=stored["id"])

    stored_or_had = "stored" if stored["created"] else "already had"
    send_outcome = SEND_OUTCOMES.get(stored.get("send_status"), "stored only (not sent)")
    log.info("main app %s reply id=%d: %s\n%s", stored_or_had, stored["id"], send_outcome,
             _pretty(message, event))


def send_approved_reply(event: dict) -> None:
    """The reply is already stored; posting it again makes the main app send it."""
    stored = store_reply({
        "mailbox": event["mailbox"],
        "thread_id": event["thread_id"],
        "in_reply_to_id": event["in_reply_to_id"],
        "subject": "",
        "reply": event["reply"],
    })
    log.info("approved reply id=%d: %s", stored["id"], stored.get("send_status"))


def skip_review(event: dict) -> None:
    """The reply won't be stored, so its review (if C1 saved one) has nothing to judge."""
    if event.get("agent_run_id"):
        reply_not_sent(event["agent_run_id"])


def reply_subject(subject: str | None) -> str:
    subject = subject or ""
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _pretty(message: dict, event: dict) -> str:
    """The block we log for a stored reply: who it answers, SOPs and tools used, and the reply."""
    line = "-" * 72
    sop_ids = ", ".join(event.get("sop_ids_used") or []) or "none"
    tools = ", ".join(event.get("tools_called") or []) or "none"
    escalation = f"YES - {event.get('escalation_note')}" if event.get("escalate") else "no"
    return (
        f"{line}\n"
        f"REPLYING TO  : msg {message['id']} from {message['from']!r}\n"
        f"SUBJECT      : {message['subject']!r}\n"
        f"SOPs / TOOLS : {sop_ids} / {tools}\n"
        f"ESCALATE     : {escalation}\n"
        f"REPLY        :\n{indented(event['reply'])}\n"
        f"{line}"
    )


def indented(text: str) -> str:
    """Wrap each paragraph at 70 columns, indented by two spaces; keep blank lines blank."""
    lines = []
    for paragraph in text.splitlines():
        if paragraph.strip():
            lines.append(textwrap.fill(paragraph, width=70, initial_indent="  ", subsequent_indent="  "))
        else:
            lines.append("")
    return "\n".join(lines)
