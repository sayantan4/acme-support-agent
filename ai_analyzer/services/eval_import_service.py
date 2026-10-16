"""Import eval results (the runners' JSON files) into Postgres, for the ai-evals Grafana dashboard.

    retrieval: knowledge_retriever/<retriever>/eval_results*.json   (BM25, Dense, Hybrid, Hybrid + reranker)
    agent:     agent/evals/results/eval_results_<agent>__<judge>[__<suite>].json
    gate:      agent/evals/gate_llm/results/eval_results_<scope prompt>__scope.json

A file is identified by its path and the time it was written, so importing twice changes nothing and
re-running an eval (which rewrites its file) adds a new run: history is kept.

    python -m ai_analyzer evals-import
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ai_analyzer.models.evals import (
    EvalAgentCase, EvalAgentCheck, EvalGateCase, EvalRetrievalCase, EvalRetrievalMetric, EvalRun,
)
from ai_analyzer.models.session import init_db, session_scope
from ai_analyzer.repositories import eval_repo

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).resolve().parent.parent          # ai_analyzer/
REPO = PACKAGE.parent
RETRIEVAL_RESULTS = "knowledge_retriever/*/eval_results*.json"
AGENT_RESULTS = "agent/evals/results/eval_results_*.json"
GATE_RESULTS = "agent/evals/gate_llm/results/eval_results_*.json"

RETRIEVER_LABELS = {"BM25Ret": "BM25", "DenseRet": "Dense", "HybridRet": "Hybrid (RRF)",
                    "HybridRetRanker": "Hybrid + rerank"}


def import_all() -> dict:
    """Import every results file that isn't in the database yet. Returns {"imported": n, "skipped": n}."""
    counts = {"imported": 0, "skipped": 0}
    files = (sorted(PACKAGE.glob(RETRIEVAL_RESULTS)) + sorted(PACKAGE.glob(AGENT_RESULTS))
             + sorted(PACKAGE.glob(GATE_RESULTS)))
    for path in files:
        counts["imported" if import_file(path) else "skipped"] += 1
    return counts


def import_file(path: Path) -> bool:
    """Import one results file. False if this exact file version was imported before."""
    resolved = path.resolve()
    source_file = str(resolved.relative_to(REPO)) if resolved.is_relative_to(REPO) else str(resolved)
    run_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    data = json.loads(path.read_text())

    with session_scope() as session:
        if eval_repo.find_run(session, source_file, run_at) is not None:
            return False
        if "case_results" in data:
            run, rows = _retrieval_run(data, path), _retrieval_rows(data)
        elif "scope_prompt" in data:
            run, rows = _gate_run(data), _gate_rows(data)
        else:
            run, rows = _agent_run(data, path), _agent_rows(data)
        run.source_file, run.run_at = source_file, run_at
        eval_repo.add_run(session, run, rows)
    log.info("imported %s (%s %s, %d cases)", source_file, run.kind, run.system, run.case_count)
    return True


def import_quietly(path: Path) -> None:
    """For the eval runners: import right after saving, but never fail the eval run over it."""
    try:
        init_db()  # the eval tables may not exist yet on a fresh database
        import_file(path)
    except Exception as problem:  # noqa: BLE001  the results file is saved either way
        log.warning("could not import %s into the evals dashboard (%s); "
                    "run `python -m ai_analyzer evals-import` later", path, problem)


# ------------------------------------------------------------------ retrieval

def _retrieval_run(data: dict, path: Path) -> EvalRun:
    system = RETRIEVER_LABELS.get(data["retriever_name"], data["retriever_name"])
    version = ""
    if data["retriever_name"] == "HybridRetRanker":  # only the reranker has versions (its prompt)
        version = path.stem.removeprefix("eval_results").lstrip("_")   # "rerank-v1"
        system = f"{system}-{version.removeprefix('rerank-')}" if version else system
    return EvalRun(kind="retrieval", system=system, version=version, suite="all",
                   case_count=len(data["case_results"]),
                   summary={"top_k": data["top_k"], "overall": data["overall"],
                            "score_cutoffs": data.get("score_cutoffs", {})})


def _retrieval_rows(data: dict) -> list:
    rows = [_metric("overall", "all", data["overall"])]
    rows += [_metric("file", name, metrics) for name, metrics in data["by_file"].items()]
    rows += [_metric("category", name, metrics) for name, metrics in data["by_category"].items()]
    rows += [EvalRetrievalCase(
        case_id=case["case_id"], eval_file=case["eval_file"], category=case.get("category"),
        is_out_of_scope=case["is_out_of_scope"], expected_ids=case["expected_ids"],
        expected_rank=case["expected_rank"], lenient_rank=case.get("lenient_rank"),
        top_5_ids=case["top_5_ids"], top_score=case.get("top_score"), latency_ms=case.get("latency_ms"),
        query=case["query"],
    ) for case in data["case_results"]]
    return rows


def _metric(scope: str, name: str, metrics: dict) -> EvalRetrievalMetric:
    latency = metrics.get("latency") or {}
    return EvalRetrievalMetric(
        scope=scope, name=name, case_count=metrics["case_count"], hit_at_1=metrics["hit_at_1"],
        hit_at_3=metrics["hit_at_3"], mrr=metrics["mrr"], lenient_hit_at_3=metrics["lenient_hit_at_3"],
        p50_ms=latency.get("p50_ms"), p95_ms=latency.get("p95_ms"), p99_ms=latency.get("p99_ms"),
        max_ms=latency.get("max_ms"),
    )


# ------------------------------------------------------------------ agent

def _agent_run(data: dict, path: Path) -> EvalRun:
    parts = path.stem.removeprefix("eval_results_").split("__")    # agent-v2 / judge-v1 / thread
    suite = parts[2] if len(parts) > 2 else "all"
    return EvalRun(kind="agent", system="agent", version=data["agent_prompt"],
                   judge_version=data["judge_prompt"], suite=suite, case_count=len(data["cases"]),
                   summary=data["summary"])


def _agent_rows(data: dict) -> list:
    rows = []
    for case in data["cases"]:
        judge = case.get("judge")
        judge_passed = judge is None or (not judge.get("error") and judge.get("grounded", True)
                                         and all(item["passed"] for item in judge.get("items", [])))
        failed = [check["name"] for check in case["checks"] if not check["passed"]]
        if not judge_passed:
            failed.append("judge")
        agent, tokens = case.get("agent", {}), case.get("tokens", {})
        rows.append(EvalAgentCase(
            case_id=case["id"], passed=case["passed"], failed=failed, seconds=case["seconds"],
            input_tokens=tokens.get("input", 0), cached_tokens=tokens.get("cached", 0),
            output_tokens=tokens.get("output", 0), judge_tokens=tokens.get("judge", 0),
            escalate=agent.get("escalate"), reply_kind=agent.get("reply_kind"), reply=agent.get("reply"),
            tools_called=[call["name"] for call in agent.get("tools_called", [])],
            sops_followed=agent.get("sops_followed", []), judge=judge,
        ))
        rows += [EvalAgentCheck(case_id=case["id"], name=check["name"], passed=check["passed"],
                                detail=check.get("detail") or "") for check in case["checks"]]
        if judge is not None:
            rows.append(EvalAgentCheck(case_id=case["id"], name="judge", passed=judge_passed,
                                       detail=judge.get("grounded_reason") or ""))
    return rows


def _gate_run(data: dict) -> EvalRun:
    with_retrieval_sets = any(case["category"].startswith("set:") for case in data["cases"])
    return EvalRun(kind="gate", system="scope gate", version=data["scope_prompt"], judge_version="",
                   suite="all" if with_retrieval_sets else "handwritten", case_count=len(data["cases"]),
                   summary=data["summary"])


def _gate_rows(data: dict) -> list:
    return [
        EvalGateCase(
            case_id=case["id"], category=case["category"], message=case.get("message"),
            expected=case["expected"], decision=case["decision"],
            passed=case["passed"], wrongly_rejected=case["wrongly_rejected"], problems=case["problems"],
            no_issue_kind=case.get("no_issue_kind"), reply_sent=case.get("reply_sent"),
            reason=case.get("reason") or "", gate_failed=case.get("gate_failed", False), seconds=case["seconds"],
            input_tokens=case["tokens"]["input"], cached_tokens=case["tokens"]["cached"],
            output_tokens=case["tokens"]["output"],
        )
        for case in data["cases"]
    ]
