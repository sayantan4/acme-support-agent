"""reply_reviews: one per agent run, created PENDING; a human sets REVIEWED and agent_was_wrong."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base, utcnow


class ReplyReview(Base):
    """agent_was_wrong is NULL until a human has reviewed the run.

    A run without a reply is reviewed too: was staying silent right? A dropped reply is SKIPPED
    (never sent, so there is nothing to judge).
    """

    __tablename__ = "reply_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")  # PENDING | REVIEWED | SKIPPED
    agent_was_wrong: Mapped[Optional[bool]] = mapped_column(Boolean)
    failure: Mapped[Optional[str]] = mapped_column(String(32))   # e.g. wrong_sop, wrong_tool_input
    corrected_reply: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
