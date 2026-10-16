"""Queries on agent_runs. Every function takes the caller's session."""

from sqlalchemy.orm import Session

from ai_analyzer.models import AgentRun


def get(session: Session, agent_run_id: int) -> AgentRun | None:
    return session.get(AgentRun, agent_run_id)


def add(session: Session, agent_run: AgentRun) -> AgentRun:
    """Add and flush, so agent_run.id is assigned."""
    session.add(agent_run)
    session.flush()
    return agent_run


def find_by_reply_id(session: Session, reply_id: int) -> AgentRun | None:
    """The run whose reply the main app stored as processed_messages.id = reply_id."""
    return session.query(AgentRun).filter(
        AgentRun.reply_id == reply_id,
    ).first()
