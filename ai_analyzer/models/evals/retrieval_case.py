"""eval_retrieval_cases: every retrieval eval case: the expected SOP, the rank it was found at, the top 5."""

from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base


class EvalRetrievalCase(Base):
    __tablename__ = "eval_retrieval_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(60))
    eval_file: Mapped[str] = mapped_column(String(60))
    category: Mapped[Optional[str]] = mapped_column(String(40))
    is_out_of_scope: Mapped[bool] = mapped_column(Boolean)
    expected_ids: Mapped[list] = mapped_column(JSON)
    expected_rank: Mapped[Optional[int]] = mapped_column(Integer)  # rank of the first right SOP; NULL = not in top k
    lenient_rank: Mapped[Optional[int]] = mapped_column(Integer)
    top_5_ids: Mapped[list] = mapped_column(JSON)
    top_score: Mapped[Optional[float]] = mapped_column(Float)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    query: Mapped[str] = mapped_column(Text)
