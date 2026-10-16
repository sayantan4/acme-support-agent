"""eval_retrieval_metrics: hit@1 / hit@3 / MRR / latency of a retrieval eval run: overall, per eval file,
per category."""

from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base


class EvalRetrievalMetric(Base):
    __tablename__ = "eval_retrieval_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    scope: Mapped[str] = mapped_column(String(10))          # overall | file | category
    name: Mapped[str] = mapped_column(String(60))           # "all", "evals_cfpb.json", "payments", ...
    case_count: Mapped[int] = mapped_column(Integer)        # in-scope cases (out-of-scope have no right answer)
    hit_at_1: Mapped[float] = mapped_column(Float)
    hit_at_3: Mapped[float] = mapped_column(Float)
    mrr: Mapped[float] = mapped_column(Float)
    lenient_hit_at_3: Mapped[float] = mapped_column(Float)
    p50_ms: Mapped[Optional[float]] = mapped_column(Float)
    p95_ms: Mapped[Optional[float]] = mapped_column(Float)
    p99_ms: Mapped[Optional[float]] = mapped_column(Float)
    max_ms: Mapped[Optional[float]] = mapped_column(Float)
