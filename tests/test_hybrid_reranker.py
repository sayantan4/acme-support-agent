"""HybridRetRanker with a fake LLM and the fake embedder, so tests need no network."""

import pytest
from fake_embedder import BagOfWordsEmbedder

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.hybrid import HybridRet
from ai_analyzer.knowledge_retriever.hybrid_reranker import (
    LATEST_PROMPT,
    PROMPTS,
    RERANK_V1,
    RERANK_V2,
    HybridRetRanker,
    LLMReranker,
)
from ai_analyzer.clients.llm_client import LLMResult
from ai_analyzer.knowledge_retriever.hybrid_reranker.hybrid_reranker import describe_candidate, parse_grades
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface
from ai_analyzer.sops.sop_parser import SOPParser


class FakeLLM:
    """Returns a fixed reply and remembers what it was asked."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []
        self.instructions: list[str] = []

    def __call__(self, instructions: str, user_input: str) -> LLMResult:
        self.prompts.append(user_input)
        self.instructions.append(instructions)
        return LLMResult(text=self.reply, model="fake", input_tokens=100, output_tokens=10)


@pytest.fixture(scope="module")
def hybrid():
    parser = SOPParser()
    retriever = HybridRet(dense=DenseRet(parser, embedder=BagOfWordsEmbedder()), bm25_ret=BM25Ret(parser))
    retriever.index_sop()
    return retriever


def test_parse_grades():
    allowed = {"PAY-01", "PAY-02"}
    assert parse_grades('{"PAY-01": 3, "PAY-02": 1}', allowed) == {"PAY-01": 3, "PAY-02": 1}
    assert parse_grades('```json\n{"PAY-01": 2}\n```', allowed) == {"PAY-01": 2}   # code fence
    assert parse_grades('{"PAY-01": 9, "XYZ-99": 3}', allowed) == {"PAY-01": 3}     # clamped, unknown id dropped
    assert parse_grades("{}", allowed) == {}                                         # nothing fits
    assert parse_grades("sorry, I cannot", allowed) is None                          # not JSON
    assert parse_grades("[1, 2]", allowed) is None                                   # not an object


def test_reranker_moves_the_best_graded_section_to_first(hybrid):
    candidates = hybrid.get_retrieve_data("Form 16A certificate not available", 20)
    last = candidates[-1].section.sop_id
    reranker = HybridRetRanker(hybrid, LLMReranker(ask_llm=FakeLLM(f'{{"{last}": 3}}')))

    hits = reranker.get_retrieve_data("Form 16A certificate not available", 20)
    assert isinstance(reranker, RetrievalInterface)
    assert hits[0].section.sop_id == last and hits[0].score == 3.0
    # everything else keeps the hybrid's order
    assert [h.section.sop_id for h in hits[1:]] == [c.section.sop_id for c in candidates if c.section.sop_id != last]
    assert [h.rank for h in hits] == list(range(1, 21))


def test_unusable_reply_keeps_hybrid_order_and_is_counted(hybrid):
    fake = FakeLLM("not json")
    reranker = HybridRetRanker(hybrid, LLMReranker(ask_llm=fake))
    query = "UPI deposit pending"
    hits = reranker.get_retrieve_data(query, 5)
    assert [h.section.sop_id for h in hits] == [c.section.sop_id for c in hybrid.get_retrieve_data(query, 5)]
    assert all(h.score == 0.0 for h in hits)
    assert reranker.reranker.usage.unusable_replies == 1
    assert reranker.reranker.usage.calls == 1 and reranker.reranker.usage.input_tokens == 100


def test_prompt_contains_the_email_and_short_candidate_cards(hybrid):
    fake = FakeLLM("{}")
    HybridRetRanker(hybrid, LLMReranker(ask_llm=fake)).get_retrieve_data("UPI deposit pending", 3)
    [prompt] = fake.prompts
    assert prompt.startswith("Customer email:\nUPI deposit pending")
    assert prompt.count("\n- ") == 20  # always grades CANDIDATES_TO_RERANK candidates
    assert "SELECT" not in prompt     # no SQL from the full section text


def test_describe_candidate_uses_title_and_phrases():
    section = next(s for s in SOPParser().parse() if s.sop_id == "PAY-01")
    card = describe_candidate(section)
    assert card.startswith("- PAY-01: UPI deposit is pending")
    assert "customers say:" in card


def test_empty_query_skips_the_llm(hybrid):
    fake = FakeLLM("{}")
    assert HybridRetRanker(hybrid, LLMReranker(ask_llm=fake)).get_retrieve_data("", 5) == []
    assert fake.prompts == []


def test_both_prompt_versions_are_kept():
    assert set(PROMPTS) == {"rerank-v1", "rerank-v2"}
    assert LATEST_PROMPT is RERANK_V2
    assert "does not offer" in RERANK_V2.instructions and "does not offer:" not in RERANK_V1.instructions


@pytest.mark.parametrize("prompt", [RERANK_V1, RERANK_V2], ids=lambda prompt: prompt.version)
def test_reranker_sends_the_chosen_prompt_version(hybrid, prompt):
    fake = FakeLLM("{}")
    HybridRetRanker(hybrid, LLMReranker(prompt=prompt, ask_llm=fake)).get_retrieve_data("UPI deposit pending", 3)
    assert fake.instructions == [prompt.instructions]


def test_latest_prompt_is_the_default():
    assert LLMReranker(ask_llm=FakeLLM("{}")).prompt is LATEST_PROMPT
