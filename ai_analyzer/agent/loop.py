"""The agent's tool-calling loop (OpenAI Responses API).

    1. Find the SOP sections for the new messages (retrieval.py) and the tools they allow.
    2. Send the prompt with only those tools. The LLM asks for tool calls or gives its answer.
    3. Run each tool call through ToolSvc with OUR message_id (never from the LLM), add the results
       to the conversation, and ask again. At most MAX_TURNS rounds; the last one forbids tools.
    4. The final answer is JSON in the shape of prompt.AgentAnswer: new summary, reply, escalation.

The LLM never sees a user_id or message_id it could change: tools.json has no user_id, and the
message_id is added by run_agent's caller from the stream event.
"""

import functools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ai_analyzer.agent import retrieval
from ai_analyzer.agent.prompt import (
    AGENT_PROMPT_VERSION,
    AgentAnswer,
    build_instructions,
    build_prompt,
)
from ai_analyzer.agent.retrieval import SopMatch
from ai_analyzer.clients import llm_client, metrics_client
from ai_analyzer.clients.llm_client import LLMCallRecord
from ai_analyzer.clients.toolsvc_client import run_tool as run_tool_via_toolsvc
from ai_analyzer.config import get_settings
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface
from ai_analyzer.tools import tool_definitions

MAX_TURNS = 8  # LLM rounds per thread; the last round must answer without tools


# ================================================================ results

@dataclass
class ToolCallRecord:
    turn: int
    name: str
    arguments: dict
    ok: bool
    result_preview: str  # short, for the printed report
    output: dict = field(default_factory=dict)  # what the LLM was told (evals check the reply against it)


@dataclass
class AgentResult:
    summary: str
    reply: str | None
    escalate: bool
    escalation_note: str | None
    sop_ids_used: list[str]
    reply_to_message_id: int | None
    reply_kind: str = "none"           # answer | closing | holding | small_talk | out_of_scope | none
    reply_dropped: str | None = None   # why code removed the agent's reply, if it did
    sops: list[SopMatch] = field(default_factory=list)
    offered_tools: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    model: str = ""
    prompt_version: str = AGENT_PROMPT_VERSION
    turns: int = 0
    input_tokens: int = 0              # agent loop, all turns
    cached_input_tokens: int = 0       # part of input_tokens served from OpenAI's prompt cache
    output_tokens: int = 0             # includes the model's hidden reasoning tokens
    retrieval_seconds: float = 0.0
    retrieval_input_tokens: int = 0    # the reranker's LLM call (embedding tokens not counted)
    retrieval_output_tokens: int = 0
    loop_seconds: float = 0.0
    llm_calls: list[LLMCallRecord] = field(default_factory=list)  # every LLM call of this run, in order
    scope_decision: str | None = None  # the scope gate's decision (in_scope | mixed | no_issue | out_of_scope)
    scope_reason: str | None = None


# ================================================================ the main function

def create_response(**request):
    """One call to the Responses API, measured as an "agent" call. Tests replace this with a fake."""
    return llm_client.respond("agent", **request)


def measured(run: Callable[..., "AgentResult"]) -> Callable[..., "AgentResult"]:
    """Record every LLM call made during the run on result.llm_calls, and report the run to Prometheus."""
    @functools.wraps(run)
    def run_and_measure(*args, **kwargs) -> AgentResult:
        with llm_client.recording() as llm_calls:
            result = run(*args, **kwargs)
        result.llm_calls = llm_calls
        metrics_client.observe_agent_run(result)
        return result
    return run_and_measure


@measured
def run_agent(
    summary_till_now: str,
    messages: list[dict],
    reply_to_message_id: int | None,
    *,
    previous_replies: list[str] | None = None,
    last_reply_kind: str | None = None,
    scope_note: str | None = None,
    retriever: RetrievalInterface | None = None,
    create: Callable = create_response,
    run_tool: Callable = run_tool_via_toolsvc,
) -> AgentResult:
    """Summarize the thread and draft the reply to `reply_to_message_id` (None = summary only).

    `previous_replies`: our replies the customer already received in this thread, oldest first.
    `last_reply_kind`: what the newest of them was (answer / closing / holding / small_talk / out_of_scope),
    as stored by the main app.
    `scope_note`: the scope gate's instruction for a mixed message ("decline the loan part").
    """
    
    result = AgentResult(
        summary="",
        reply=None,
        escalate=False,
        escalation_note=None,
        sop_ids_used=[],
        reply_to_message_id=reply_to_message_id,
        model=get_settings().openai_model,
    )

    # Step 1: which SOPs, and which tools they allow (no tools without a verified customer)
    result.sops = find_sops_timed(messages, retriever or retrieval.get_retriever(), result)
    if reply_to_message_id is not None:
        result.offered_tools = retrieval.allowed_tools(result.sops)

    # Steps 2 and 3: ask, run the tools it asks for, ask again
    request = build_request(result.model, result.offered_tools)
    first_message = build_prompt(summary_till_now, messages, result.sops, reply_to_message_id,
                                 previous_replies or [], scope_note)
    conversation: list = [{"role": "user", "content": first_message}]

    loop_started = time.perf_counter()
    for turn in range(1, MAX_TURNS + 1):
        result.turns = turn
        response = ask_llm(create, request, conversation, last_turn=turn == MAX_TURNS)
        add_usage(result, response.usage)

        tool_requests = [item for item in response.output if item.type == "function_call"]
        if not tool_requests:
            # Step 4: no more tools wanted, so this is the final answer
            result.loop_seconds = time.perf_counter() - loop_started
            return drop_repeated_reply(finish(result, response.output_text), last_reply_kind)

        conversation += response.output  # the model's own items (incl. reasoning) go back as-is
        for tool_request in tool_requests:
            output = execute(tool_request, reply_to_message_id, run_tool, turn, result)
            conversation.append(tool_output_item(tool_request.call_id, output))

    # Only reached if the model ignored tool_choice="none" on the last turn.
    result.loop_seconds = time.perf_counter() - loop_started
    return give_up(result, summary_till_now)


# ================================================================ steps

def find_sops_timed(messages: list[dict], retriever: RetrievalInterface,
                    result: AgentResult) -> list[SopMatch]:
    """find_sops(), recording its time and the reranker's tokens on `result`."""
    # The reranker keeps a running total, so measure the difference made by this call.
    usage = getattr(getattr(retriever, "reranker", None), "usage", None)
    input_before = usage.input_tokens if usage else 0
    output_before = usage.output_tokens if usage else 0

    started = time.perf_counter()
    sops = retrieval.find_sops(retrieval.messages_to_query(messages), retriever)
    result.retrieval_seconds = time.perf_counter() - started

    if usage:
        result.retrieval_input_tokens = usage.input_tokens - input_before
        result.retrieval_output_tokens = usage.output_tokens - output_before
    return sops


def build_request(model: str, offered_tools: list[str]) -> dict:
    """Everything sent on every turn except the conversation itself."""
    request = {
        "model": model,
        "instructions": build_instructions(retrieval.support_guidelines()),
        "text": llm_client.json_schema_format("agent_result", AgentAnswer),
    }
    tools = [
        {**definition, "strict": False}  # our tool schemas have optional parameters
        for definition in tool_definitions()
        if definition["name"] in offered_tools
    ]
    if tools:
        request["tools"] = tools
    return request


def ask_llm(create: Callable, request: dict, conversation: list, last_turn: bool):
    """One turn. On the last turn the model may not call tools, so it has to answer."""
    if last_turn and "tools" in request:
        return create(**request, input=conversation, tool_choice="none")
    return create(**request, input=conversation)


def add_usage(result: AgentResult, usage) -> None:
    result.input_tokens += usage.input_tokens
    result.output_tokens += usage.output_tokens
    details = getattr(usage, "input_tokens_details", None)
    result.cached_input_tokens += getattr(details, "cached_tokens", 0) or 0


def execute(tool_request, message_id: int | None, run_tool: Callable, turn: int,
            result: AgentResult) -> dict:
    """Run one tool call the LLM asked for, if we offered it. Returns what the LLM is told."""
    try:
        arguments = json.loads(tool_request.arguments or "{}")
    except json.JSONDecodeError:
        arguments, output = {}, {"error": "arguments were not valid JSON"}
    else:
        if tool_request.name not in result.offered_tools:
            output = {"error": f"tool {tool_request.name} is not available for this email"}
        else:
            output = run_tool(tool_request.name, arguments, message_id=message_id)

    result.tool_calls.append(ToolCallRecord(
        turn=turn,
        name=tool_request.name,
        arguments=arguments,
        ok="error" not in output,
        result_preview=preview(output),
        output=output,
    ))
    return output


def tool_output_item(call_id: str, output: dict) -> dict:
    """A tool's result, in the form the Responses API expects back in the conversation."""
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps(output, default=str),
    }


def preview(output: dict) -> str:
    """A short text of a tool's output for the report: the error, a row count, or the start."""
    if "error" in output:
        return f"error: {output['error']}"
    value = output.get("result")
    if isinstance(value, list):
        return f"{len(value)} row(s)"
    text = json.dumps(value, default=str)
    return text if len(text) <= 90 else text[:87] + "..."


def finish(result: AgentResult, final_text: str) -> AgentResult:
    """Copy the model's final JSON answer onto `result`."""
    answer = AgentAnswer.model_validate_json(final_text)  # the shape is guaranteed by structured output
    result.summary = answer.summary
    result.reply = answer.reply if result.reply_to_message_id is not None else None
    result.escalate = answer.escalate
    result.escalation_note = answer.escalation_note
    result.sop_ids_used = answer.sop_ids_used
    result.reply_kind = answer.reply_kind if result.reply else "none"
    return result


ONCE_IN_A_ROW = {"closing", "small_talk"}


def drop_repeated_reply(result: AgentResult, last_reply_kind: str | None) -> AgentResult:
    """One closing (or small-talk) message in a row, guaranteed by code rather than by the prompt.

    "Thanks" -> "Glad I could help" -> "Thanks" again: the agent may write another closing; we drop it.
    A new question still gets an answer (reply_kind "answer"), so the thread re-opens normally.
    """
    if result.reply and result.reply_kind in ONCE_IN_A_ROW and last_reply_kind == result.reply_kind:
        result.reply = None
        result.reply_dropped = f"our last reply was already {result.reply_kind.replace('_', ' ')}"
        result.reply_kind = "none"
    return result


def give_up(result: AgentResult, summary_till_now: str) -> AgentResult:
    """No final answer within MAX_TURNS: keep the old summary, send no reply, and hand it to a human."""
    result.summary = summary_till_now
    result.escalate = True
    result.escalation_note = f"agent did not finish within {MAX_TURNS} turns"
    return result
