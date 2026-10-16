"""DenseRet on the real SOP files, with a fake embedder so tests need no network or API key."""

import numpy as np
import pytest
from fake_embedder import BagOfWordsEmbedder

from ai_analyzer.knowledge_retriever.dense import DenseRet
from ai_analyzer.knowledge_retriever.dense.dense import normalize_rows
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface
from ai_analyzer.sops.sop_parser import SOPParser


@pytest.fixture(scope="module")
def parser():
    return SOPParser()


@pytest.fixture(scope="module")
def dense(parser):
    retriever = DenseRet(parser, embedder=BagOfWordsEmbedder())
    retriever.index_sop()
    return retriever


def test_dense_is_a_retriever_that_uses_the_parser(dense, parser):
    assert isinstance(dense, RetrievalInterface)
    assert dense.sop_parser is parser
    assert dense.get_sop_list() == parser.parse()
    assert dense.section_vectors.shape[0] == len(parser.parse())


def test_a_section_finds_itself_first(dense):
    section = next(s for s in dense.get_sop_list() if s.sop_id == "PAY-01")
    [top, *_] = dense.get_retrieve_data(section.text, 3)
    assert top.section.sop_id == "PAY-01"
    assert top.score == pytest.approx(1.0, abs=1e-5)  # identical text = cosine similarity 1


def test_results_are_ranked_and_limited(dense):
    hits = dense.get_retrieve_data("UPI deposit pending, money deducted", 5)
    assert len(hits) == 5
    assert [h.rank for h in hits] == [1, 2, 3, 4, 5]
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_returns_nothing_without_calling_the_model(parser, query):
    embedder = BagOfWordsEmbedder()
    retriever = DenseRet(parser, embedder=embedder)
    retriever.index_sop()
    calls_after_indexing = embedder.calls
    assert retriever.get_retrieve_data(query, 3) == []
    assert embedder.calls == calls_after_indexing


def test_normalize_rows_gives_unit_length_and_keeps_zero_rows():
    vectors = normalize_rows(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert np.allclose(vectors[0], [0.6, 0.8])
    assert np.allclose(vectors[1], [0.0, 0.0])
