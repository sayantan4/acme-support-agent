"""eval_runs: one row per eval results file per run. A re-run rewrites its file and becomes a new row,
so history is kept."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base, utcnow


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (UniqueConstraint("source_file", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10))           # retrieval | agent | gate
    system: Mapped[str] = mapped_column(String(40))         # BM25 | Dense | Hybrid (RRF) | Hybrid + rerank-v1 | agent | scope gate
    version: Mapped[str] = mapped_column(String(40))        # reranker / agent / scope gate prompt version ("" for none)
    judge_version: Mapped[str] = mapped_column(String(40), default="")
    suite: Mapped[str] = mapped_column(String(40))          # agent: thread | single | all; gate: handwritten | all; retrieval: all
    source_file: Mapped[str] = mapped_column(String(200))   # relative to the repo
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # when the results file was written
    case_count: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON)             # the file's own summary (overall, score cutoffs, ...)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
