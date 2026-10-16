"""Every agent run gets a review a human fills in later. This tracks what happened to each run's reply.

    C1  start_review()      after the agent runs, BEFORE publishing the reply: run + PENDING review
    C3  reply_stored()      the main app stored the reply: fill in reply_id
        reply_not_sent()    the reply was dropped (a newer message arrived): review SKIPPED

agent_runs.outcome:  no_reply | published (C1 sent it to C3) | stored (main app has it) | dropped
A run without a reply is reviewed too (was staying silent right?); a dropped reply is SKIPPED.
"""

from ai_analyzer.models import AgentRun
from ai_analyzer.models.session import session_scope
from ai_analyzer.repositories import agent_run_repo, llm_call_repo, review_repo


def start_review(run: dict, *, mailbox: str, thread_id: str, in_reply_to_id: int | None,
                 llm_calls: list[dict] = ()) -> int:
    """C1: save the run, its LLM calls and a PENDING review, before the reply is published.
    Returns agent_runs.id."""
    with session_scope() as session:
        outcome = "published" if run["reply"] else "no_reply"
        agent_run = agent_run_repo.add(session, AgentRun(
            mailbox=mailbox, thread_id=thread_id, in_reply_to_id=in_reply_to_id, outcome=outcome, **run))
        llm_call_repo.add_all(session, agent_run.id, list(llm_calls))
        review_repo.add_pending(session, agent_run.id)
        return agent_run.id


def reply_stored(agent_run_id: int, reply_id: int) -> None:
    """C3: the main app stored this run's reply as processed_messages.id = reply_id.

    The same inbound message only ever gets one reply. If another run already owns it (C1 handled a
    redelivered request and ran the agent twice), this run's reply was never used: skip its review.
    """
    with session_scope() as session:
        agent_run = agent_run_repo.get(session, agent_run_id)
        if agent_run is None or agent_run.reply_id == reply_id:
            return  # unknown run, or already recorded (C3 handled a redelivered event)
        if agent_run_repo.find_by_reply_id(session, reply_id) is not None:
            _skip(session, agent_run)
            return
        agent_run.reply_id = reply_id
        agent_run.outcome = "stored"


def reply_not_sent(agent_run_id: int) -> None:
    """C3 dropped the reply (a newer message arrived), so there is nothing to review."""
    with session_scope() as session:
        agent_run = agent_run_repo.get(session, agent_run_id)
        if agent_run is not None:
            _skip(session, agent_run)


def _skip(session, agent_run: AgentRun) -> None:
    agent_run.outcome = "dropped"
    review_repo.for_run(session, agent_run.id).status = "SKIPPED"
