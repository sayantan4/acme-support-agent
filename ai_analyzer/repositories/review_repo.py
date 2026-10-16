"""Queries on reply_reviews. Every function takes the caller's session."""

from sqlalchemy.orm import Session

from ai_analyzer.models import ReplyReview


def add_pending(session: Session, agent_run_id: int) -> None:
    session.add(ReplyReview(agent_run_id=agent_run_id, status="PENDING"))


def for_run(session: Session, agent_run_id: int) -> ReplyReview:
    return session.query(ReplyReview).filter(
        ReplyReview.agent_run_id == agent_run_id,
    ).one()
