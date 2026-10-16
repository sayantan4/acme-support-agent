"""Queries on processed_messages: inbound emails and the replies we wrote. Every function takes the
caller's session."""

from sqlalchemy.orm import Session

from app.models import ProcessedMessage


def get(session: Session, message_id: int) -> ProcessedMessage | None:
    """By our id (processed_messages.id)."""
    return session.get(ProcessedMessage, message_id)


def find_by_gmail_id(session: Session, mailbox: str, gmail_message_id: str) -> ProcessedMessage | None:
    """By the message_id column: Gmail's id for inbound mail, "local-reply-<n>" for our replies."""
    return session.query(ProcessedMessage).filter(
        ProcessedMessage.email == mailbox,
        ProcessedMessage.message_id == gmail_message_id,
    ).first()


def is_stored(session: Session, mailbox: str, gmail_message_id: str) -> bool:
    stored = session.query(ProcessedMessage.id).filter(
        ProcessedMessage.email == mailbox,
        ProcessedMessage.message_id == gmail_message_id,
    ).first()
    return stored is not None


def add(session: Session, message: ProcessedMessage) -> ProcessedMessage:
    """Add and flush, so message.id is assigned."""
    session.add(message)
    session.flush()
    return message


def with_summary_status(session: Session, status: str) -> list[ProcessedMessage]:
    """Messages in this summary batch state, oldest first."""
    return session.query(ProcessedMessage).filter(
        ProcessedMessage.summary_status == status,
    ).order_by(
        ProcessedMessage.id
    ).all()


def inbound_after(session: Session, mailbox: str, thread_id: str, after_id: int) -> list[ProcessedMessage]:
    """Verified customers' inbound messages of a thread with id > after_id, oldest first.

    Our replies are left out, and so are messages from unverified senders (no user_id): their text never
    leaves the main app, so a stranger in the thread can't put instructions in front of the agent.
    """
    return session.query(ProcessedMessage).filter(
        ProcessedMessage.email == mailbox,
        ProcessedMessage.thread_id == thread_id,
        ProcessedMessage.direction == "inbound",
        ProcessedMessage.user_id.isnot(None),
        ProcessedMessage.id > after_id,
    ).order_by(
        ProcessedMessage.id
    ).all()


def sent_replies(session: Session, mailbox: str, thread_id: str, limit: int) -> list[ProcessedMessage]:
    """Our replies in a thread that the customer actually received: the newest `limit`, oldest first."""
    newest_first = session.query(ProcessedMessage).filter(
        ProcessedMessage.email == mailbox,
        ProcessedMessage.thread_id == thread_id,
        ProcessedMessage.direction == "outbound",
        ProcessedMessage.send_status == "sent",
    ).order_by(
        ProcessedMessage.id.desc()
    ).limit(limit).all()
    return list(reversed(newest_first))


def replies_with_send_status(session: Session, mailbox: str, send_status: str) -> list[ProcessedMessage]:
    """Our replies in this send state (e.g. awaiting_approval), oldest first."""
    return session.query(ProcessedMessage).filter(
        ProcessedMessage.email == mailbox,
        ProcessedMessage.direction == "outbound",
        ProcessedMessage.send_status == send_status,
    ).order_by(
        ProcessedMessage.id
    ).all()
