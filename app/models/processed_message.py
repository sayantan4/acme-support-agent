"""processed_messages: every inbound email we stored, and every reply we wrote."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class ProcessedMessage(Base):
    """Every inbox message we've handled, with its content.

    Also the dedupe table: Pub/Sub and history.list can deliver the same message more than once.
    `id` increases in the order we stored messages; summaries point at it.
    """

    __tablename__ = "processed_messages"
    # ids come from a Postgres sequence, so they only grow and are never reused, even after deletes.
    __table_args__ = (UniqueConstraint("email", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320))
    message_id: Mapped[str] = mapped_column(String(64))  # Gmail's message id
    thread_id: Mapped[Optional[str]] = mapped_column(String(64))
    sender: Mapped[Optional[str]] = mapped_column(Text)
    subject: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[Optional[str]] = mapped_column(Text)  # the email's Date header, as sent
    body_text: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # "inbound" = arrived in the inbox; "outbound" = a reply we wrote (stored only, not sent)
    direction: Mapped[str] = mapped_column(String(10), default="inbound")
    # Outbound only: processed_messages.id of the inbound message this replies to
    in_reply_to_id: Mapped[Optional[int]] = mapped_column(Integer)
    # Inbound only: sent by a machine (newsletter, notification, auto-reply), so we don't reply
    is_automated: Mapped[Optional[bool]] = mapped_column(Boolean)
    # Inbound only: "pending" = waiting for the next summary batch; "requested" = sent to C1.
    # NULL = not tracked (outbound replies, and messages stored before batching existed).
    summary_status: Mapped[Optional[str]] = mapped_column(String(10))
    # The customer this message belongs to, as verified by the auth service from the sender's address.
    # NULL = sender not registered (or inactive): the message is stored but not processed further.
    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    # Outbound only: "sent" = delivered to Gmail, "failed" = will be retried, NULL = stored only,
    # "awaiting_approval" -> "approved" | "rejected" for escalated replies (services/reply_service.py)
    send_status: Mapped[Optional[str]] = mapped_column(String(20))
    # Outbound only: Gmail's id for the message we sent
    gmail_sent_id: Mapped[Optional[str]] = mapped_column(String(64))
    # Outbound only: what the agent said the reply is (answer / closing / holding)
    reply_kind: Mapped[Optional[str]] = mapped_column(String(20))
