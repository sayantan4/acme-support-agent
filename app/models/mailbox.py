"""mailboxes: one row per connected Gmail account (OAuth tokens + history cursor)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class Mailbox(Base):
    """A connected Gmail account."""

    __tablename__ = "mailboxes"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    # Encrypted google.oauth2.credentials.Credentials JSON
    credentials_enc: Mapped[str] = mapped_column(Text)
    # Cursor: everything up to this history record has been processed
    last_history_id: Mapped[Optional[str]] = mapped_column(String(32))
    watch_expiration: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | disconnected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
