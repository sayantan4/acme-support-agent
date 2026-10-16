"""HybridRet and Reciprocal Rank Fusion, with the fake embedder so tests need no network."""

import pytest
from fake_embedder import BagOfWordsEmbedder

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.hybrid import HybridRet, reciprocal_rank_fusion
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface, RetrievedSOP
from ai_analyzer.sops.sop_parser import SOPParser, SOPSection


def ranked(*sop_ids: str) -> list[RetrievedSOP]:
    """A ranked result list with dummy sections, best first."""
    return [
        RetrievedSOP(section=SOPSection(sop_id=sop_id, title="", category="", source_file="", text=""),
                     score=0.0, rank=rank)
        for rank, sop_id in enumerate(sop_ids, start=1)
    ]


def ids(results: list[RetrievedSOP]) -> list[str]:
    return [item.section.sop_id for item in results]


def test_rrf_rewards_sections_found_by_both_lists():
    # B is 2nd in both lists; A and C are 1st in only one list each.
    fused = reciprocal_rank_fusion([ranked("A", "B"), ranked("C", "B")], number_of_items=3, rrf_k=60)
    assert ids(fused) == ["B", "A", "C"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 62)
    assert [item.rank for item in fused] == [1, 2, 3]


def test_rrf_with_one_empty_list_keeps_the_other_order():
    assert ids(reciprocal_rank_fusion([[], ranked("X", "Y", "Z")], number_of_items=2)) == ["X", "Y"]


def test_rrf_ties_follow_the_first_list():
    # A and C are both 1st in one list: equal scores, the first list (BM25 in HybridRet) wins.
    assert ids(reciprocal_rank_fusion([ranked("A"), ranked("C")], number_of_items=2)) == ["A", "C"]


@pytest.fixture(scope="module")
def parser():
    return SOPParser()


@pytest.fixture(scope="module")
def hybrid(parser):
    retriever = HybridRet(dense=DenseRet(parser, embedder=BagOfWordsEmbedder()), bm25_ret=BM25Ret(parser))
    retriever.index_sop()
    return retriever


def test_hybrid_is_a_retriever_that_reuses_both_halves(hybrid, parser):
    assert isinstance(hybrid, RetrievalInterface)
    assert hybrid.sop_parser is parser
    assert hybrid.bm25_ret.sop_parser is hybrid.dense.sop_parser is parser


def test_hybrid_results_are_ranked_and_limited(hybrid):
    hits = hybrid.get_retrieve_data("Form 16A certificate not available", 5)
    assert len(hits) == 5
    assert [h.rank for h in hits] == [1, 2, 3, 4, 5]
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))
    assert hits[0].section.sop_id == "TAX-04"  # BM25's exact-term hit survives the merge


def test_halves_must_share_a_parser():
    with pytest.raises(ValueError):
        HybridRet(dense=DenseRet(SOPParser(), embedder=BagOfWordsEmbedder()), bm25_ret=BM25Ret(SOPParser()))
