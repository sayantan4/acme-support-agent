"""OpenAI client: the one place that calls the LLM, shared by the agent (C1), the reranker and the judge.

Every call is measured here, once: latency, input / output / cached tokens, and its purpose
(agent, rerank, embedding, judge). Each call goes to Prometheus (metrics_client), and, inside a
`with recording() as calls:` block, is also appended to `calls`: that is how an agent run knows
its own LLM calls (stored per run in the llm_calls table).

    respond(purpose, **request)        the Responses API (the agent's tool-calling loop)
    json_schema_format(name, Model)    structured output: the reply must match a Pydantic model
    ask(instructions, input, purpose)  one plain call, text in -> text out (reranker, judge)
    embed(texts, model)                embeddings (dense retrieval)
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from ai_analyzer.clients import metrics_client
from ai_analyzer.config import get_settings


class StructuredOutput(BaseModel):
    """Base for a model's structured answer. OpenAI's strict mode needs every field required (no defaults)
    and no extra fields, so subclasses declare fields without defaults; extra="forbid" adds the second rule."""

    model_config = ConfigDict(extra="forbid")


def json_schema_format(name: str, answer_model: type[StructuredOutput]) -> dict:
    """The Responses API's `text` parameter for structured output: the reply can only be this model's JSON.

    strict=True makes OpenAI constrain decoding to the schema: every field present, no extra fields,
    enum fields only their listed values. Read the reply back with answer_model.model_validate_json().
    """
    return {"format": {"type": "json_schema", "name": name, "schema": answer_model.model_json_schema(),
                       "strict": True}}


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass
class LLMCallRecord:
    """One LLM call, as measured here."""
    purpose: str
    model: str
    seconds: float
    ok: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # when the call began


# The SDK retries rate-limit (429) and server errors, waiting longer each time. Its default of 2
# was not enough when several requests share the org's tokens-per-minute limit.
OPENAI_MAX_RETRIES = 5

_client = None
# The calls made inside the current `with recording()` block (per thread / task), or None.
_recorded_calls: ContextVar[list[LLMCallRecord] | None] = ContextVar("recorded_llm_calls", default=None)


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=get_settings().openai_api_key, max_retries=OPENAI_MAX_RETRIES)
    return _client


def openai_client() -> OpenAI:
    """The shared client. Calls made with it directly are NOT measured; prefer respond/ask/embed."""
    return _openai()


@contextmanager
def recording() -> Iterator[list[LLMCallRecord]]:
    """Collect every LLM call made inside the block (e.g. one agent run) into the yielded list."""
    calls: list[LLMCallRecord] = []
    token = _recorded_calls.set(calls)
    try:
        yield calls
    finally:
        _recorded_calls.reset(token)


# ------------------------------------------------------------------ calls

def respond(purpose: str = "agent", **request):
    """One Responses API call (the raw response, for the agent loop)."""
    started_at, started = datetime.now(timezone.utc), time.perf_counter()
    try:
        response = _openai().responses.create(**request)
    except Exception:
        _record(purpose, request.get("model", ""), started_at, started, ok=False)
        raise
    usage = response.usage
    details = getattr(usage, "input_tokens_details", None)
    _record(purpose, request.get("model", ""), started_at, started, ok=True,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cached_tokens=getattr(details, "cached_tokens", 0) or 0)
    return response


def ask(instructions: str, user_input: str, purpose: str = "other") -> LLMResult:
    """One plain call, no tools: the model's text and the tokens it used."""
    model = get_settings().openai_model
    response = respond(purpose, model=model, instructions=instructions, input=user_input)
    return LLMResult(
        text=response.output_text.strip(),
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def embed(texts: list[str], model: str) -> list[list[float]]:
    """One embedding vector per text, in input order (one API call; the caller batches)."""
    started_at, started = datetime.now(timezone.utc), time.perf_counter()
    try:
        response = _openai().embeddings.create(model=model, input=texts)
    except Exception:
        _record("embedding", model, started_at, started, ok=False)
        raise
    _record("embedding", model, started_at, started, ok=True, input_tokens=response.usage.prompt_tokens)
    return [item.embedding for item in response.data]


def _record(purpose: str, model: str, started_at: datetime, started: float, ok: bool, **tokens) -> None:
    """started_at: wall-clock start (for the run's timeline); started: perf_counter start (for latency)."""
    call = LLMCallRecord(purpose=purpose, model=model, seconds=time.perf_counter() - started, ok=ok,
                         started_at=started_at, **tokens)
    metrics_client.observe_llm_call(call.purpose, call.model, call.seconds, call.ok, call.input_tokens,
                                    call.output_tokens, call.cached_tokens)
    calls = _recorded_calls.get()
    if calls is not None:
        calls.append(call)
