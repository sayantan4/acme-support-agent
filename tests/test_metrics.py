"""The dashboard's data: every LLM call is measured once (Prometheus + the run's own list), and each
agent run's calls are stored in llm_calls. Fake OpenAI only."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from ai_analyzer.agent import run_agent
from ai_analyzer.clients import llm_client
from ai_analyzer.models import LLMCall
from ai_analyzer.models.session import init_db, session_scope
from ai_analyzer.services import review_service
from tests.test_agent import MESSAGES, FakeRetriever, FakeTools, asks_for, final_answer, tool_call


class FakeOpenAI:
    """responses.create replays scripted responses; embeddings.create returns one vector per text."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.embeddings = SimpleNamespace(create=self._embed)
        self.responses_api = SimpleNamespace(create=lambda **request: self.responses.pop(0))

    def _embed(self, model, input):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in input],
                               usage=SimpleNamespace(prompt_tokens=7 * len(input)))


@pytest.fixture
def fake_openai(monkeypatch):
    def install(*responses):
        fake = FakeOpenAI(*responses)
        monkeypatch.setattr(llm_client, "_client", SimpleNamespace(responses=fake.responses_api,
                                                                    embeddings=fake.embeddings))
        return fake
    return install


def with_cache(response, cached: int):
    response.usage.input_tokens_details = SimpleNamespace(cached_tokens=cached)
    return response


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_every_call_is_measured_with_its_purpose_and_recorded_inside_a_block(fake_openai):
    fake_openai(with_cache(final_answer(), cached=60))
    tokens_before = metric("ai_llm_tokens_total", purpose="rerank", model="m", kind="cached")

    with llm_client.recording() as calls:
        llm_client.respond("rerank", model="m", input="x")
        llm_client.embed(["a", "b"], model="emb")
    llm_client.embed(["outside the block"], model="emb")

    assert [(c.purpose, c.input_tokens, c.cached_tokens, c.output_tokens) for c in calls] == \
        [("rerank", 100, 60, 20), ("embedding", 14, 0, 0)]
    assert all(c.ok and c.seconds >= 0 for c in calls)
    assert metric("ai_llm_tokens_total", purpose="rerank", model="m", kind="cached") - tokens_before == 60
    assert metric("ai_llm_call_seconds_count", purpose="embedding", model="emb") >= 2


def test_a_failed_call_is_counted_as_an_error(fake_openai):
    fake = fake_openai()  # no scripted response: create() raises
    errors_before = metric("ai_llm_calls_total", purpose="agent", model="m", outcome="error")
    with pytest.raises(IndexError), llm_client.recording() as calls:
        llm_client.respond("agent", model="m", input="x")
    assert calls[0].ok is False and fake.responses == []
    assert metric("ai_llm_calls_total", purpose="agent", model="m", outcome="error") == errors_before + 1


def test_an_agent_run_knows_each_of_its_llm_calls_and_is_reported(fake_openai):
    fake_openai(with_cache(asks_for(tool_call("check_deposit", {"deposit_id": 401}, "c1")), cached=0),
                with_cache(final_answer(), cached=64))
    runs_before = metric("ai_agent_tool_calls_total", tool="check_deposit", outcome="ok")

    result = run_agent("", MESSAGES, 57, retriever=FakeRetriever({"PAY-01": 3}), run_tool=FakeTools())

    assert [(c.purpose, c.cached_tokens) for c in result.llm_calls] == [("agent", 0), ("agent", 64)]
    first, second = result.llm_calls
    assert first.started_at <= second.started_at  # a timeline: each call knows when it began
    assert result.cached_input_tokens == 64 and result.turns == 2
    assert metric("ai_agent_tool_calls_total", tool="check_deposit", outcome="ok") == runs_before + 1
    assert metric("ai_agent_tools_per_run_count") >= 1 and metric("ai_retrieval_seconds_count") >= 1


def test_a_runs_llm_calls_are_stored_in_order():
    init_db()
    run = {"prompt_version": "v", "model": "m", "sops_retrieved": [], "sops_followed": [], "tool_calls": [],
           "summary": "s", "reply": "r", "reply_kind": "answer", "escalate": False, "escalation_note": None,
           "input_tokens": 300, "cached_input_tokens": 64, "output_tokens": 40, "seconds": 2.0,
           "retrieval_seconds": 0.5, "loop_seconds": 1.5, "turns": 2, "tool_call_count": 1}
    t0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc)
    calls = [{"purpose": "rerank", "model": "m", "seconds": 0.4, "ok": True, "input_tokens": 90,
              "output_tokens": 5, "cached_tokens": 0, "started_at": t0},
             {"purpose": "agent", "model": "m", "seconds": 1.1, "ok": True, "input_tokens": 210,
              "output_tokens": 35, "cached_tokens": 64, "started_at": t0 + timedelta(seconds=0.5)}]
    run_id = review_service.start_review(run, mailbox="mb", thread_id="T", in_reply_to_id=1, llm_calls=calls)

    with session_scope() as session:
        stored = session.query(LLMCall).filter(LLMCall.agent_run_id == run_id).order_by(LLMCall.seq).all()
        assert [(c.seq, c.purpose, c.cached_tokens) for c in stored] == [(1, "rerank", 0), (2, "agent", 64)]
        assert (stored[1].started_at - stored[0].started_at).total_seconds() == 0.5
