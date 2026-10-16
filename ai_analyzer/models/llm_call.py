"""llm_calls: every LLM call of an agent run, in order (rerank, embedding, each agent turn)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base, utcnow


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)              # 1, 2, ... in the order they were made
    purpose: Mapped[str] = mapped_column(String(16))       # agent | rerank | embedding
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer)    # part of input_tokens served from the prompt cache
    output_tokens: Mapped[int] = mapped_column(Integer)
    seconds: Mapped[float] = mapped_column(Float)
    # When the call began; with seq and seconds it gives the run's timeline. NULL for rows saved before
    # this column existed.
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)  # when the row was saved
