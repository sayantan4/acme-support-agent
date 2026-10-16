"""SOP parser and BM25 retriever, on the real SOP files."""

import pytest

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.interface import RetrievalInterface
from ai_analyzer.sops.sop_parser import SOPParser


@pytest.fixture(scope="module")
def parser():
    return SOPParser()


@pytest.fixture(scope="module")
def bm25(parser):
    return BM25Ret(parser)


def test_parser_skips_always_on_files(parser):
    files = {s.source_file for s in parser.parse()}
    assert "support_guidelines_sop.txt" not in files and "data_dictionary.txt" not in files
    assert "payment_sop.txt" in files


def test_parser_reads_sections_with_metadata(parser):
    by_id = {s.sop_id: s for s in parser.parse()}
    pay01 = by_id["PAY-01"]
    assert pay01.category == "payments"
    assert pay01.title == "UPI deposit is pending"
    assert pay01.text.startswith("## PAY-01:")
    assert "=====" not in pay01.text
    assert pay01.customer_may_say  # phrasings were extracted
    assert len(by_id) == len(parser.parse())  # ids are unique


def test_bm25_is_a_retriever_that_uses_the_parser(bm25, parser):
    assert isinstance(bm25, RetrievalInterface)
    assert bm25.sop_parser is parser
    assert bm25.get_sop_list() == parser.parse()


def test_exact_terms_find_the_right_section(bm25):
    [top, *_] = bm25.get_retrieve_data("Form 16A certificate not available", 3)
    assert top.section.sop_id == "TAX-04"
    assert top.rank == 1


def test_results_are_ranked_and_limited(bm25):
    hits = bm25.get_retrieve_data("UPI deposit pending, money deducted", 5)
    assert 0 < len(hits) <= 5
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:]))


@pytest.mark.parametrize("query", ["", "the and of", "zzzqqq"])
def test_no_known_words_returns_nothing(bm25, query):
    assert bm25.get_retrieve_data(query, 3) == []
