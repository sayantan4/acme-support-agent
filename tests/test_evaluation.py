"""The shared retrieval eval runner, on a tiny eval file and the real BM25 retriever."""

import json

import pytest

from ai_analyzer.knowledge_retriever.bm25_ret import BM25Ret
from ai_analyzer.knowledge_retriever.evaluation import (
    percentile,
    position_of_first_match,
    run_retrieval_evals,
    summarize_latency,
)
from ai_analyzer.sops.sop_parser import SOPParser

CASES = [
    {"id": "hit", "email": {"subject": "", "body": "Form 16A certificate not available"},
     "expected_category": "tax", "expected_sop_ids": ["TAX-04"], "acceptable_sop_ids": []},
    {"id": "guideline-only", "email": {"subject": "", "body": "tell me my balance"},
     "expected_category": "general", "expected_sop_ids": ["SUP-01"], "acceptable_sop_ids": []},
    {"id": "oos", "email": {"subject": "", "body": "my credit card is lost"},
     "expected_category": None, "expected_sop_ids": [], "acceptable_sop_ids": [], "out_of_scope": True},
]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    evals_dir = tmp_path_factory.mktemp("evals")
    (evals_dir / "tiny.json").write_text(json.dumps(CASES))
    return run_retrieval_evals(BM25Ret(SOPParser()), eval_files=("tiny.json",), evals_dir=evals_dir, show_report=False)


def test_position_of_first_match():
    assert position_of_first_match(["A", "B", "C"], {"C", "B"}) == 2
    assert position_of_first_match(["A"], {"Z"}) is None


def test_percentile_picks_a_real_measurement():
    latencies = [float(ms) for ms in range(1, 101)]  # 1, 2, ..., 100 ms
    assert percentile(latencies, 50) == 50.0
    assert percentile(latencies, 95) == 95.0
    assert percentile(latencies, 99) == 99.0
    assert percentile([7.0], 99) == 7.0  # one value: every percentile is that value


def test_latency_summary_is_in_the_report(report):
    assert report.overall.latency.p50_ms <= report.overall.latency.p95_ms <= report.overall.latency.p99_ms <= report.overall.latency.max_ms
    assert summarize_latency([]).max_ms == 0.0


def test_parallel_run_gives_the_same_results_in_the_same_order(tmp_path):
    (tmp_path / "tiny.json").write_text(json.dumps(CASES * 4))  # 12 emails
    retriever = BM25Ret(SOPParser())
    one_by_one = run_retrieval_evals(retriever, eval_files=("tiny.json",), evals_dir=tmp_path, show_report=False)
    parallel = run_retrieval_evals(retriever, eval_files=("tiny.json",), evals_dir=tmp_path, show_report=False,
                                   max_workers=4)
    assert [(r.case_id, r.top_5_ids) for r in parallel.case_results] == \
           [(r.case_id, r.top_5_ids) for r in one_by_one.case_results]
    assert parallel.overall.hit_at_3 == one_by_one.overall.hit_at_3


def test_every_case_gets_a_result(report):
    assert [r.case_id for r in report.case_results] == ["hit", "guideline-only", "oos"]
    assert report.retriever_name == "BM25Ret"


def test_only_in_scope_cases_with_indexed_sections_are_scored(report):
    assert [r.case_id for r in report.scored_results] == ["hit"]
    assert report.overall.case_count == 1
    assert report.overall.hit_at_1 == 1.0 and report.overall.mrr == 1.0


def test_out_of_scope_cases_feed_the_score_cutoff(report):
    assert list(report.score_cutoffs) == ["tiny.json"]
    assert report.score_cutoffs["tiny.json"].out_of_scope_count == 1


def test_results_file_is_written(tmp_path):
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    (evals_dir / "tiny.json").write_text(json.dumps(CASES[:1]))
    results_file = tmp_path / "results.json"
    run_retrieval_evals(BM25Ret(SOPParser()), eval_files=("tiny.json",), evals_dir=evals_dir,
                        results_file=results_file, show_report=False)
    saved = json.loads(results_file.read_text())
    assert saved["overall"]["hit_at_1"] == 1.0
    assert saved["case_results"][0]["case_id"] == "hit"
