"""Queries on llm_calls. Every function takes the caller's session."""

from sqlalchemy.orm import Session

from ai_analyzer.models import LLMCall


def add_all(session: Session, agent_run_id: int, calls: list[dict]) -> None:
    """Store a run's LLM calls in order (calls: purpose, model, seconds, ok, *_tokens)."""
    for seq, call in enumerate(calls, start=1):
        session.add(LLMCall(agent_run_id=agent_run_id, seq=seq, **call))


def for_run(session: Session, agent_run_id: int) -> list[LLMCall]:
    return session.query(LLMCall).filter(
        LLMCall.agent_run_id == agent_run_id,
    ).order_by(
        LLMCall.seq
    ).all()
