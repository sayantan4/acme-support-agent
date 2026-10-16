"""The block C1 prints after each agent run: which SOPs, which tools, the summary and the reply."""

import json
import textwrap

from ai_analyzer.agent.loop import AgentResult

LINE = "=" * 78


def format_report(result: AgentResult, thread_id: str) -> str:
    """Header, timing and tokens, then what the agent found and decided."""
    if result.reply_to_message_id is not None:
        reply_to = f"msg {result.reply_to_message_id}"
    else:
        reply_to = "nobody (no verified customer)"
    escalation = f"YES - {result.escalation_note}" if result.escalate else "no"
    dropped = f"  (dropped: {result.reply_dropped})" if result.reply_dropped else ""
    return (
        f"\n{LINE}\n"
        f"AGENT RESULT  thread={thread_id}  reply_to={reply_to}\n"
        f"model={result.model}  prompt={result.prompt_version}\n"
        f"{LINE}\n"
        f"{timing_and_tokens(result)}"
        f"{LINE}\n"
        f"SOPs RETRIEVED : {sops_retrieved(result)}\n"
        f"SOPs FOLLOWED  : {', '.join(result.sop_ids_used) or 'none'}\n"
        f"TOOLS OFFERED  : {', '.join(result.offered_tools) or 'none'}\n"
        f"TOOLS CALLED   :\n{tools_called(result)}\n"
        f"SCOPE GATE     : {result.scope_decision or '-'}  ({result.scope_reason or 'not run'})\n"
        f"ESCALATE       : {escalation}\n"
        f"FINAL SUMMARY  :\n{wrap(result.summary)}\n"
        f"REPLY KIND     : {result.reply_kind}{dropped}\n"
        f"FINAL REPLY    :\n{wrap(result.reply)}\n"
        f"{LINE}"
    )


def timing_and_tokens(result: AgentResult) -> str:
    """Three lines: retrieval, the agent loop, and their total."""
    total_seconds = result.retrieval_seconds + result.loop_seconds
    total_in = result.input_tokens + result.retrieval_input_tokens
    total_out = result.output_tokens + result.retrieval_output_tokens
    return (
        f"RETRIEVAL      : {result.retrieval_seconds:.2f} s  |  reranker tokens "
        f"{result.retrieval_input_tokens:,} in / {result.retrieval_output_tokens:,} out\n"
        f"AGENT LOOP     : {result.loop_seconds:.2f} s  |  {result.turns} turn(s)  |  tokens "
        f"{result.input_tokens:,} in ({result.cached_input_tokens:,} cached) / {result.output_tokens:,} out\n"
        f"TOTAL          : {total_seconds:.2f} s  |  tokens {total_in:,} in / {total_out:,} out\n"
    )


def sops_retrieved(result: AgentResult) -> str:
    sops = [f"{sop.section.sop_id} (grade {sop.grade}) {sop.section.title}" for sop in result.sops]
    return ", ".join(sops) or "none matched"


def tools_called(result: AgentResult) -> str:
    """One numbered line per tool call: turn, name(arguments) -> ok/FAILED: short result."""
    if not result.tool_calls:
        return "    (no tools called)"
    lines = []
    for number, call in enumerate(result.tool_calls, start=1):
        status = "ok" if call.ok else "FAILED"
        lines.append(f"    {number}. [turn {call.turn}] {call.name}({json.dumps(call.arguments)}) "
                     f"-> {status}: {call.result_preview}")
    return "\n".join(lines)


def wrap(text: str | None, indent: str = "    ") -> str:
    """Wrap each paragraph at 74 columns, indented; keep blank lines blank."""
    if not text:
        return f"{indent}(none)"
    lines = []
    for paragraph in text.splitlines():
        if paragraph.strip():
            lines.append(textwrap.fill(paragraph, width=74, initial_indent=indent, subsequent_indent=indent))
        else:
            lines.append("")
    return "\n".join(lines)
