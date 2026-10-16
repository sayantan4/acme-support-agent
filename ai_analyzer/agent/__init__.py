"""The orchestrator: what C1 calls for each thread with new messages. One call in, one result out.

    result = handle(summary_till_now, messages, target_id=57, previous_replies=[...], last_reply_kind="answer")
    result.summary, result.reply, result.scope_decision, result.llm_calls, ...
    print(format_report(result, thread_id))

Inside the orchestrator (C1 never calls these directly):
    orchestrator.py     the entry: the gate first, then the main agent or a template
    scope.py            the scope gate: a small model decides in_scope / mixed / no_issue / out_of_scope
    template_replies.py fixed replies for no_issue / out_of_scope (no main-agent run)
    The main agent, in the order a run uses them:
    retrieval.py        find the SOP sections for the new messages (hybrid + reranker, prompt rerank-v1)
    prompt.py           the instructions, the prompt text, and the JSON shape of the final answer
    loop.py             the tool-calling loop: LLM -> tools (through ToolSvc) -> LLM ... -> final answer
    report.py           the readable block C1 prints: gate, SOPs, tools called, summary, reply
"""

from ai_analyzer.agent.loop import AgentResult, ToolCallRecord, run_agent
from ai_analyzer.agent.report import format_report
from ai_analyzer.agent.orchestrator import handle

__all__ = ["AgentResult", "ToolCallRecord", "format_report", "handle", "run_agent"]
