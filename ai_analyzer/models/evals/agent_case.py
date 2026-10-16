"""eval_agent_cases: every agent eval case: passed, failed checks, tokens, seconds, the reply."""

from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base


class EvalAgentCase(Base):
    __tablename__ = "eval_agent_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(80))
    passed: Mapped[bool] = mapped_column(Boolean)
    failed: Mapped[list] = mapped_column(JSON)              # names of failed checks, plus "judge" if the judge failed
    seconds: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    judge_tokens: Mapped[int] = mapped_column(Integer)
    escalate: Mapped[Optional[bool]] = mapped_column(Boolean)
    reply_kind: Mapped[Optional[str]] = mapped_column(String(20))
    reply: Mapped[Optional[str]] = mapped_column(Text)
    tools_called: Mapped[list] = mapped_column(JSON)
    sops_followed: Mapped[list] = mapped_column(JSON)
    judge: Mapped[Optional[dict]] = mapped_column(JSON)     # rubric items, grounded, reasons
