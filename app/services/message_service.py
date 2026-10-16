"""Inbound messages: what happens when one arrives, and what other services may read about them.

    on_new_message()     your business logic: called once per new inbox message (after dedupe)
    get_message() ...    reads behind the internal API (C1, C3 and ToolSvc call it)
"""

import logging

from app.clients.auth_client import verified_user_id
from app.models import ProcessedMessage
from app.models.session import session_scope
from app.repositories import message_repo
from app.services import summary_service

log = logging.getLogger(__name__)


def on_new_message(session, message: ProcessedMessage) -> None:
    """`message` is already stored (not yet committed) with its content and thread_id.

    Raise to have the message retried on the next sync; return normally to mark it processed.
    """
    # The first message of a thread shares its id with the thread; replies get their own id.
    is_reply = message.message_id != message.thread_id
    log.info(
        "[%s] new message id=%s thread=%s (%s) from=%r subject=%r body=%d chars",
        message.email,
        message.message_id,
        message.thread_id,
        "reply in existing thread" if is_reply else "new thread",
        message.sender,
        message.subject,
        len(message.body_text or ""),
    )

    # Strict for now: only registered, active customers are processed. The message is still stored
    # (dedupe), but with no user_id it is never summarized or replied to. If the auth service is down
    # this raises, so the message is retried on the next sync instead of being dropped.
    message.user_id = verified_user_id(message.sender)
    if message.user_id is None:
        log.info("[%s] sender %r is not a registered customer; not processing msg %d",
                 message.email, message.sender, message.id)
        return
    log.info("[%s] msg %d belongs to user_id=%d", message.email, message.id, message.user_id)
    summary_service.mark_for_summary(message)  # summarized with the next batch, not right away


# ------------------------------------------------------------------ reads for the internal API

def get_message(mailbox: str, message_id: int) -> ProcessedMessage | None:
    """The message, if it belongs to this mailbox."""
    with session_scope() as session:
        message = message_repo.get(session, message_id)
        return message if message is not None and message.email == mailbox else None


def owner_of(message_id: int) -> int | None:
    """The verified customer (user_id) who sent this message, or None."""
    with session_scope() as session:
        message = message_repo.get(session, message_id)
        return message.user_id if message is not None else None


def inbound_after(mailbox: str, thread_id: str, after_id: int) -> list[ProcessedMessage]:
    """The thread's verified customer messages after after_id, oldest first (what C1 summarizes and answers)."""
    with session_scope() as session:
        return message_repo.inbound_after(session, mailbox, thread_id, after_id)


def sent_replies(mailbox: str, thread_id: str, limit: int) -> list[ProcessedMessage]:
    """Our last `limit` replies the customer received in this thread, oldest first."""
    with session_scope() as session:
        return message_repo.sent_replies(session, mailbox, thread_id, limit)
