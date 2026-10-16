"""The orchestrator: decides who answers a thread's new messages, and returns one result to C1.

    C1 -> handle() -> scope gate (scope.py, a small model)
                       in_scope / mixed         -> the main agent (loop.py): SOPs + tools
                       no_issue / out_of_scope  -> a template reply, or none (template_replies.py)
                     no verified customer       -> the main agent, only to update the summary

The orchestrator itself is plain code, not an LLM: the gate already made the decision, so a second model
would pay twice for it. Every LLM call of the run (gate + main agent) is in result.llm_calls, tagged by
purpose (scope = the gate; agent, rerank, embedding = the main agent), so one agent_runs row and its
llm_calls rows show both parts.
"""

from ai_analyzer.agent.loop import AgentResult, run_agent
from ai_analyzer.agent.scope import check_scope, mixed_note
from ai_analyzer.agent.template_replies import reply_without_agent
from ai_analyzer.clients import llm_client, metrics_client


def handle(summary_till_now: str, messages: list[dict], target_id: int | None, previous_replies: list[str],
          last_reply_kind: str | None) -> AgentResult:
    """The scope gate first; only in-scope (and mixed) messages pay for the main agent."""
    if target_id is None:  # no verified customer to answer: the agent only updates the summary
        return run_agent(summary_till_now, messages, reply_to_message_id=None)

    with llm_client.recording() as gate_calls:
        decision = check_scope(summary_till_now, messages, previous_replies, last_reply_kind)
    metrics_client.observe_scope_decision(decision.decision)

    if decision.decision in ("in_scope", "mixed"):
        note = mixed_note(decision) if decision.decision == "mixed" else None
        # message_id for tools comes from here (our code), never from the LLM.
        result = run_agent(summary_till_now, messages, reply_to_message_id=target_id,
                           previous_replies=previous_replies, last_reply_kind=last_reply_kind, scope_note=note)
    else:
        # No agent run: nothing to add to the agent/retrieval latency metrics (the scope counter covers it).
        result = reply_without_agent(decision, previous_replies, last_reply_kind, reply_to_message_id=target_id)

    result.llm_calls = gate_calls + result.llm_calls  # the gate's call is part of this run's cost
    result.scope_decision, result.scope_reason = decision.decision, decision.reason
    return result
