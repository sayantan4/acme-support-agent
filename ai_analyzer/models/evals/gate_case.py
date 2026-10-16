"""eval_gate_cases: every scope gate eval case: expected vs actual decision, the gate's reason, tokens."""

from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base


class EvalGateCase(Base):
    __tablename__ = "eval_gate_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(40))       # small_talk | ... | set:evals_cfpb (a retrieval eval set)
    message: Mapped[Optional[str]] = mapped_column(Text)    # the newest customer message, shortened
    expected: Mapped[str] = mapped_column(String(20))       # in_scope | mixed | no_issue | out_of_scope
    decision: Mapped[str] = mapped_column(String(20))       # what the gate decided
    passed: Mapped[bool] = mapped_column(Boolean)
    wrongly_rejected: Mapped[bool] = mapped_column(Boolean) # a real issue the gate sent to a template
    problems: Mapped[list] = mapped_column(JSON)            # "decision out_of_scope, expected in_scope", ...
    no_issue_kind: Mapped[Optional[str]] = mapped_column(String(20))
    reply_sent: Mapped[Optional[bool]] = mapped_column(Boolean)  # template cases: would a reply be sent
    reason: Mapped[str] = mapped_column(Text)               # the gate's one-sentence reason
    gate_failed: Mapped[bool] = mapped_column(Boolean)      # the gate errored (fail open)
    seconds: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
