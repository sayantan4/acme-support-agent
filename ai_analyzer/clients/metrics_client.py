"""Prometheus metrics: what the AI service measures, and the /metrics endpoint Prometheus scrapes.

Aggregates only (counts, token totals, latency histograms for p50/p95/p99). The per-run detail
("run 42 made 3 LLM calls, called get_orders...") is stored in Postgres (agent_runs, llm_calls);
Grafana shows both. Prometheus config and the dashboard live in monitoring/.

    ai_llm_call_seconds{purpose, model}         latency of one LLM call (agent turn, rerank, embedding, judge)
    ai_llm_calls_total{purpose, model, outcome} LLM calls, ok or error
    ai_llm_tokens_total{purpose, model, kind}   tokens; kind = input | output | cached (cached is part of input)
    ai_retrieval_seconds                        SOP retrieval per agent run (hybrid + rerank)
    ai_agent_run_seconds                        whole agent run (retrieval + tool loop)
    ai_agent_runs_total{outcome}                reply | no_reply | escalated
    ai_agent_turns                              LLM rounds per agent run
    ai_agent_tools_per_run                      tool calls per agent run
    ai_agent_tool_calls_total{tool, outcome}    tool calls, ok or error
    ai_scope_decisions_total{decision}          the scope gate: in_scope | mixed | no_issue | out_of_scope
"""

from prometheus_client import Counter, Histogram, start_http_server

LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5, 8, 13, 20, 30, 60)

LLM_CALL_SECONDS = Histogram("ai_llm_call_seconds", "Latency of one LLM call", ["purpose", "model"],
                             buckets=LATENCY_BUCKETS)
LLM_CALLS = Counter("ai_llm_calls", "LLM calls", ["purpose", "model", "outcome"])
LLM_TOKENS = Counter("ai_llm_tokens", "LLM tokens (cached is part of input)", ["purpose", "model", "kind"])

RETRIEVAL_SECONDS = Histogram("ai_retrieval_seconds", "SOP retrieval per agent run", buckets=LATENCY_BUCKETS)
AGENT_RUN_SECONDS = Histogram("ai_agent_run_seconds", "Whole agent run (retrieval + tool loop)",
                              buckets=LATENCY_BUCKETS)
AGENT_RUNS = Counter("ai_agent_runs", "Agent runs", ["outcome"])
AGENT_TURNS = Histogram("ai_agent_turns", "LLM rounds per agent run", buckets=(1, 2, 3, 4, 5, 6, 8))
AGENT_TOOLS_PER_RUN = Histogram("ai_agent_tools_per_run", "Tool calls per agent run",
                                buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10))
AGENT_TOOL_CALLS = Counter("ai_agent_tool_calls", "Tool calls", ["tool", "outcome"])
SCOPE_DECISIONS = Counter("ai_scope_decisions", "Scope gate decisions", ["decision"])


def start_server(port: int) -> None:
    """Serve /metrics on this port, in a background thread."""
    start_http_server(port)


def observe_llm_call(purpose: str, model: str, seconds: float, ok: bool,
                     input_tokens: int = 0, output_tokens: int = 0, cached_tokens: int = 0) -> None:
    LLM_CALL_SECONDS.labels(purpose, model).observe(seconds)
    LLM_CALLS.labels(purpose, model, "ok" if ok else "error").inc()
    LLM_TOKENS.labels(purpose, model, "input").inc(input_tokens)
    LLM_TOKENS.labels(purpose, model, "output").inc(output_tokens)
    LLM_TOKENS.labels(purpose, model, "cached").inc(cached_tokens)


def observe_scope_decision(decision: str) -> None:
    SCOPE_DECISIONS.labels(decision).inc()


def observe_agent_run(result) -> None:
    """One finished agent run (ai_analyzer.agent.loop.AgentResult)."""
    RETRIEVAL_SECONDS.observe(result.retrieval_seconds)
    AGENT_RUN_SECONDS.observe(result.retrieval_seconds + result.loop_seconds)
    AGENT_TURNS.observe(result.turns)
    AGENT_TOOLS_PER_RUN.observe(len(result.tool_calls))
    for call in result.tool_calls:
        AGENT_TOOL_CALLS.labels(call.name, "ok" if call.ok else "error").inc()
    AGENT_RUNS.labels(_outcome(result)).inc()


def _outcome(result) -> str:
    if result.escalate:
        return "escalated"
    return "reply" if result.reply else "no_reply"
