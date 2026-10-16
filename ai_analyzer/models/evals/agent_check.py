"""eval_agent_checks: every check of every agent eval case (sop, tools_called, escalation, ..., judge)."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base


class EvalAgentCheck(Base):
    __tablename__ = "eval_agent_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(40))           # sop | tools_called | escalation | ... | judge
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text)
