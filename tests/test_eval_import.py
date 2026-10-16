"""Eval results files -> the eval_* tables (ai-evals dashboard). Small hand-made results files only."""

import json

from ai_analyzer.models.evals import EvalAgentCase, EvalAgentCheck, EvalRetrievalCase, EvalRetrievalMetric, EvalRun
from ai_analyzer.models.session import init_db, session_scope
from ai_analyzer.services import eval_import_service

METRICS = {"case_count": 1, "hit_at_1": 1.0, "hit_at_3": 1.0, "mrr": 1.0, "lenient_hit_at_3": 1.0,
           "latency": {"p50_ms": 1.5, "p95_ms": 2.0, "p99_ms": 2.0, "max_ms": 2.0}}
RETRIEVAL = {
    "retriever_name": "HybridRetRanker", "top_k": 20, "overall": METRICS,
    "by_file": {"evals.json": METRICS}, "by_category": {"payments": METRICS}, "score_cutoffs": {},
    "case_results": [
        {"case_id": "pay-001", "eval_file": "evals.json", "category": "payments", "is_out_of_scope": False,
         "expected_ids": ["PAY-01"], "expected_rank": 1, "lenient_rank": 1, "top_5_ids": ["PAY-01", "PAY-02"],
         "top_score": 3.0, "latency_ms": 1.5, "query": "UPI deposit pending"},
        {"case_id": "oos-001", "eval_file": "evals.json", "category": None, "is_out_of_scope": True,
         "expected_ids": [], "expected_rank": None, "lenient_rank": None, "top_5_ids": ["PAY-11"],
         "top_score": 1.0, "latency_ms": 1.2, "query": "Do you offer car loans?"},
    ],
}
AGENT = {
    "agent_prompt": "agent-v2", "judge_prompt": "judge-v1", "summary": {"cases": 1, "passed": 0},
    "cases": [{
        "id": "thanks-again", "passed": False, "seconds": 3.2,
        "checks": [{"name": "reply_decision", "passed": False, "detail": "replied when no reply was needed"},
                   {"name": "escalation", "passed": True, "detail": ""}],
        "judge": {"items": [{"item": "short", "kind": "must", "passed": True, "reason": "ok"}],
                  "grounded": True, "grounded_reason": "", "error": None},
        "agent": {"escalate": False, "reply_kind": "closing", "reply": "Glad I could help.",
                  "tools_called": [{"name": "get_deposits", "arguments": {}, "ok": True}], "sops_followed": []},
        "tokens": {"input": 5000, "cached": 1000, "output": 100, "judge": 800},
    }],
}


def write(folder, name, data):
    path = folder / name
    path.write_text(json.dumps(data))
    return path


def counts():
    with session_scope() as session:
        return {model.__tablename__: session.query(model).count()
                for model in (EvalRun, EvalRetrievalMetric, EvalRetrievalCase, EvalAgentCase, EvalAgentCheck)}


def test_a_retrieval_and_an_agent_results_file_are_imported_once(tmp_path):
    init_db()
    before = counts()
    retrieval = write(tmp_path, "eval_results_rerank-v1.json", RETRIEVAL)
    agent = write(tmp_path, "eval_results_agent-v2__judge-v1__thread.json", AGENT)

    assert eval_import_service.import_file(retrieval) is True
    assert eval_import_service.import_file(agent) is True
    assert eval_import_service.import_file(retrieval) is False  # same file, same write time: nothing new

    added = {table: n - before[table] for table, n in counts().items()}
    assert added == {"eval_runs": 2, "eval_retrieval_metrics": 3, "eval_retrieval_cases": 2,
                     "eval_agent_cases": 1, "eval_agent_checks": 3}  # 2 checks + the judge

    with session_scope() as session:
        runs = {run.kind: run for run in session.query(EvalRun).filter(EvalRun.source_file.like(f"{tmp_path}%"))}
        assert (runs["retrieval"].system, runs["retrieval"].version) == ("Hybrid + rerank-v1", "rerank-v1")
        assert (runs["agent"].version, runs["agent"].judge_version, runs["agent"].suite) == \
            ("agent-v2", "judge-v1", "thread")
        case = session.query(EvalAgentCase).filter(EvalAgentCase.eval_run_id == runs["agent"].id).one()
        assert case.failed == ["reply_decision"] and case.tools_called == ["get_deposits"]


def test_a_rerun_rewrites_the_file_and_becomes_a_new_run(tmp_path):
    import os
    init_db()
    path = write(tmp_path, "eval_results.json", {**RETRIEVAL, "retriever_name": "BM25Ret"})
    assert eval_import_service.import_file(path) is True
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 60))  # the eval ran again a minute later
    assert eval_import_service.import_file(path) is True
    with session_scope() as session:
        assert session.query(EvalRun).filter(EvalRun.source_file == str(path.resolve())).count() == 2


GATE = {
    "scope_prompt": "scope-v4", "summary": {"cases": 2, "passed": 1},
    "cases": [
        {"id": "hi-only", "category": "small_talk", "expected": "no_issue", "decision": "no_issue", "passed": True,
         "problems": [], "no_issue_kind": "greeting", "out_of_scope_topics": [], "reply_sent": True,
         "reason": "greeting only", "gate_failed": False, "wrongly_rejected": False, "seconds": 1.1,
         "tokens": {"input": 1400, "cached": 0, "output": 90}},
        {"id": "evals_banking77/b77-061", "category": "set:evals_banking77", "expected": "in_scope",
         "decision": "out_of_scope", "passed": False, "problems": ["decision out_of_scope, expected in_scope"],
         "no_issue_kind": None, "out_of_scope_topics": ["cards"], "reply_sent": True, "reason": "asks about cards",
         "gate_failed": False, "wrongly_rejected": True, "seconds": 1.8,
         "tokens": {"input": 1500, "cached": 0, "output": 110}},
    ],
}


def test_a_gate_results_file_is_imported_with_one_row_per_case(tmp_path):
    from ai_analyzer.models.evals import EvalGateCase
    init_db()
    path = write(tmp_path, "eval_results_scope-v4__scope.json", GATE)
    assert eval_import_service.import_file(path) is True

    with session_scope() as session:
        run = session.query(EvalRun).filter(EvalRun.source_file == str(path.resolve())).one()
        assert (run.kind, run.system, run.version, run.suite, run.case_count) == \
            ("gate", "scope gate", "scope-v4", "all", 2)
        cases = session.query(EvalGateCase).filter(EvalGateCase.eval_run_id == run.id).order_by(EvalGateCase.id).all()
        assert [(c.case_id, c.expected, c.decision, c.wrongly_rejected) for c in cases] == [
            ("hi-only", "no_issue", "no_issue", False),
            ("evals_banking77/b77-061", "in_scope", "out_of_scope", True),
        ]
