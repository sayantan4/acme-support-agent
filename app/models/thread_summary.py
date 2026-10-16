"""thread_summary_info: the running AI summary per thread (written only by C2)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class ThreadSummaryInfo(Base):
    """Running AI summary per thread. Only the summary writer (C2) changes this table."""

    __tablename__ = "thread_summary_info"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # processed_messages.id of the newest message included in `summary`
    last_summary_msg_id: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
