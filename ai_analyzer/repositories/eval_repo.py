"""Queries on the eval_* tables. Every function takes the caller's session."""

from datetime import datetime

from sqlalchemy.orm import Session

from ai_analyzer.models.evals import EvalRun


def find_run(session: Session, source_file: str, run_at: datetime) -> EvalRun | None:
    return session.query(EvalRun).filter(
        EvalRun.source_file == source_file,
        EvalRun.run_at == run_at,
    ).first()


def add_run(session: Session, run: EvalRun, rows: list) -> EvalRun:
    """Add the run and its metric / case / check rows (each gets eval_run_id). Flushes for run.id."""
    session.add(run)
    session.flush()
    for row in rows:
        row.eval_run_id = run.id
        session.add(row)
    return run
