"""Retrieval evals, shared by every retriever.

Every retriever built on RetrievalInterface gets `retriever.run_evals()`, which calls
`run_retrieval_evals` below.

How it works, in order:
    1. Send every eval email to the retriever and note where the expected SOP section landed.
    2. Turn those positions into metrics, overall / per eval file / per category.
    3. For files with out-of-scope emails, check if the top score alone can flag them.
    4. Print a summary and, optionally, save every case to a JSON file.

Metrics (only for in-scope emails whose expected section is searchable):
    hit@1           % of emails where the expected section is the 1st result
    hit@3           % of emails where it is in the first 3 results
    MRR             average of 1/position: 1st = 1, 2nd = 0.5, 3rd = 0.33, not found = 0
    lenient hit@3   like hit@3, but an "acceptable" section also counts
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for type hints: interface.py imports this module
    from ai_analyzer.knowledge_retriever.interface import RetrievalInterface

EVALS_DIR = Path(__file__).resolve().parent / "evals"  # ai_analyzer/knowledge_retriever/evals
DEFAULT_EVAL_FILES = ("evals.json", "evals_long.json", "evals_banking77.json", "evals_cfpb.json")
DEFAULT_TOP_K = 20        # how many sections we ask the retriever for
DEFAULT_MAX_WORKERS = 1   # emails evaluated at once; 1 = one after another
MISSES_TO_PRINT = 10


# ================================================================ data we produce

@dataclass
class CaseResult:
    """What the retriever returned for one eval email."""

    case_id: str
    eval_file: str
    category: str
    is_out_of_scope: bool
    expected_ids: list[str]    # expected sections that the retriever can actually return
    expected_rank: int | None  # position of the first expected section (1 = first); None = not found
    lenient_rank: int | None   # same, but an acceptable section also counts
    top_5_ids: list[str]       # what the retriever returned, best first
    top_score: float           # score of the 1st result (0 when nothing was returned)
    latency_ms: float
    query: str

    @property
    def is_scored(self) -> bool:
        """Only in-scope emails with a searchable expected section get hit@k / MRR.

        Some emails expect only support-guideline (SUP-*) sections. Those are always put in the
        prompt and never searched, so no retriever can return them: we skip such emails.
        """
        return not self.is_out_of_scope and len(self.expected_ids) > 0


@dataclass
class LatencySummary:
    """Time per retrieval call, in milliseconds. p95 = 95% of queries were at least this fast."""

    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass
class RetrievalMetrics:
    case_count: int
    hit_at_1: float
    hit_at_3: float
    mrr: float
    lenient_hit_at_3: float
    latency: LatencySummary  # over the same emails as the metrics above


@dataclass
class ScoreCutoff:
    """The best rule "top score below `threshold` = no SOP applies" for one eval file.

    Computed per file because scores grow with query length, so a one-line question and a
    long email can't share a threshold. It is picked and measured on the same emails, so
    treat it as an optimistic first look, not a production setting.
    """

    threshold: float
    accuracy: float
    in_scope_count: int
    in_scope_kept: int             # in-scope emails at or above the threshold (correct)
    out_of_scope_count: int
    out_of_scope_caught: int       # out-of-scope emails below the threshold (correct)
    median_top_score_in_scope: float
    median_top_score_out_of_scope: float


@dataclass
class EvalReport:
    retriever_name: str
    top_k: int
    overall: RetrievalMetrics
    by_file: dict[str, RetrievalMetrics]
    by_category: dict[str, RetrievalMetrics]   # largest category first
    score_cutoffs: dict[str, ScoreCutoff]      # only for files that have out-of-scope emails
    case_results: list[CaseResult]

    @property
    def scored_results(self) -> list[CaseResult]:
        return [result for result in self.case_results if result.is_scored]

    @property
    def misses(self) -> list[CaseResult]:
        """Scored emails whose expected section is not in the first 3 results."""
        return [result for result in self.scored_results if not found_within(result.expected_rank, 3)]


# ================================================================ the main function

def run_retrieval_evals(
    retriever: RetrievalInterface,
    eval_files: tuple[str, ...] = DEFAULT_EVAL_FILES,
    top_k: int = DEFAULT_TOP_K,
    results_file: Path | None = None,
    evals_dir: Path = EVALS_DIR,
    show_report: bool = True,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> EvalReport:
    """Run every email in `eval_files` through `retriever` and summarise how it did.

    `max_workers` > 1 sends that many emails at once. Use it for retrievers that wait on the
    network (Dense, the reranker); keep 1 for local ones and when stepping through with a debugger.
    """
    # Step 1: retrieve for every email
    searchable_ids = {section.sop_id for section in retriever.sop_parser.parse()}
    jobs = load_jobs(evals_dir, eval_files)
    case_results = evaluate_all(retriever, jobs, searchable_ids, top_k, max_workers)

    # Steps 2 and 3: metrics and out-of-scope cut-offs
    report = build_report(type(retriever).__name__, top_k, eval_files, case_results)

    # Step 4: print and save
    if show_report:
        print_report(report)
    if results_file is not None:
        save_report(report, results_file)
        if show_report:
            print(f"\nAll cases saved to {results_file}")
    return report


# ================================================================ step 1: one email at a time

def load_jobs(evals_dir: Path, eval_files: tuple[str, ...]) -> list[tuple[str, dict]]:
    """Every eval case as an (eval_file, case) pair, file by file, in file order."""
    jobs = []
    for eval_file in eval_files:
        for case in load_eval_cases(evals_dir / eval_file):
            jobs.append((eval_file, case))
    return jobs


def load_eval_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def evaluate_all(
    retriever: RetrievalInterface,
    jobs: list[tuple[str, dict]],
    searchable_ids: set[str],
    top_k: int,
    max_workers: int,
) -> list[CaseResult]:
    """Evaluate every (eval_file, case) job. Results come back in the same order as `jobs`.

    In parallel, each email is still timed on its own, so latency stays per query. Total run
    time drops because we no longer wait for one network reply before sending the next email.
    """
    def evaluate(job: tuple[str, dict]) -> CaseResult:
        eval_file, case = job
        return evaluate_case(retriever, case, eval_file, searchable_ids, top_k)

    if max_workers <= 1 or len(jobs) <= 1:
        return [evaluate(job) for job in jobs]

    # The first email runs alone: a retriever that builds its index lazily does it here, once.
    first_result = evaluate(jobs[0])
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        other_results = list(pool.map(evaluate, jobs[1:]))  # map() keeps the input order
    return [first_result, *other_results]


def email_to_query(email: dict) -> str:
    """The retriever searches with the subject and body together."""
    return f"{email.get('subject', '')}\n{email['body']}".strip()


def evaluate_case(
    retriever: RetrievalInterface, case: dict, eval_file: str, searchable_ids: set[str], top_k: int
) -> CaseResult:
    query = email_to_query(case["email"])
    print("Inside ----- ")
    start = time.perf_counter()
    retrieved = retriever.get_retrieve_data(query, top_k)
    latency_ms = (time.perf_counter() - start) * 1000

    returned_ids = [item.section.sop_id for item in retrieved]  # best first
    expected_ids = set(case["expected_sop_ids"]) & searchable_ids
    acceptable_ids = set(case.get("acceptable_sop_ids", [])) & searchable_ids

    return CaseResult(
        case_id=case["id"],
        eval_file=eval_file,
        category=case.get("expected_category") or "out_of_scope",
        is_out_of_scope=case.get("out_of_scope", False),
        expected_ids=sorted(expected_ids),
        expected_rank=position_of_first_match(returned_ids, expected_ids),
        lenient_rank=position_of_first_match(returned_ids, expected_ids | acceptable_ids),
        top_5_ids=returned_ids[:5],
        top_score=retrieved[0].score if retrieved else 0.0,
        latency_ms=round(latency_ms, 2),
        query=query,
    )


def position_of_first_match(returned_ids: list[str], wanted_ids: set[str]) -> int | None:
    """Position (1 = first) of the first returned section that is wanted, or None."""
    for position, sop_id in enumerate(returned_ids, start=1):
        if sop_id in wanted_ids:
            return position
    return None


# ================================================================ step 2: metrics

def found_within(rank: int | None, n: int) -> bool:
    """True if the section was found in the first n results."""
    return rank is not None and rank <= n


def reciprocal_rank(rank: int | None) -> float:
    """1 for 1st place, 1/2 for 2nd, 1/3 for 3rd ... and 0 when not found."""
    return 1 / rank if rank else 0.0


def compute_metrics(results: list[CaseResult]) -> RetrievalMetrics:
    if not results:
        return RetrievalMetrics(
            case_count=0, hit_at_1=0.0, hit_at_3=0.0, mrr=0.0, lenient_hit_at_3=0.0,
            latency=summarize_latency([]),
        )

    def share_of(values) -> float:  # True counts as 1, so this is also a percentage
        return sum(values) / len(results)

    return RetrievalMetrics(
        case_count=len(results),
        hit_at_1=share_of(found_within(result.expected_rank, 1) for result in results),
        hit_at_3=share_of(found_within(result.expected_rank, 3) for result in results),
        mrr=share_of(reciprocal_rank(result.expected_rank) for result in results),
        lenient_hit_at_3=share_of(found_within(result.lenient_rank, 3) for result in results),
        latency=summarize_latency([result.latency_ms for result in results]),
    )


def group_by(results: list[CaseResult], key: Callable[[CaseResult], str]) -> dict[str, list[CaseResult]]:
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        groups[key(result)].append(result)
    return groups


# ================================================================ step 3: out-of-scope cut-off

def find_best_score_cutoff(in_scope: list[CaseResult], out_of_scope: list[CaseResult]) -> ScoreCutoff | None:
    """Try every top score we saw as the threshold and keep the one that is right most often.

    "Right" means: an in-scope email scores at or above the threshold (kept), and an
    out-of-scope email scores below it (caught).
    """
    if not in_scope or not out_of_scope:
        return None

    all_emails = in_scope + out_of_scope
    best_threshold, best_kept, best_caught = 0.0, -1, -1
    for threshold in sorted({email.top_score for email in all_emails}):
        kept = sum(email.top_score >= threshold for email in in_scope)
        caught = sum(email.top_score < threshold for email in out_of_scope)
        if kept + caught > best_kept + best_caught:
            best_threshold, best_kept, best_caught = threshold, kept, caught

    return ScoreCutoff(
        threshold=best_threshold,
        accuracy=(best_kept + best_caught) / len(all_emails),
        in_scope_count=len(in_scope),
        in_scope_kept=best_kept,
        out_of_scope_count=len(out_of_scope),
        out_of_scope_caught=best_caught,
        median_top_score_in_scope=statistics.median(email.top_score for email in in_scope),
        median_top_score_out_of_scope=statistics.median(email.top_score for email in out_of_scope),
    )


# ================================================================ latency

def percentile(values: list[float], p: float) -> float:
    """The value that p% of `values` are at or below (nearest-rank: always a real measurement)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))  # 1-based position in the sorted list
    return ordered[rank - 1]


def summarize_latency(latencies_ms: list[float]) -> LatencySummary:
    if not latencies_ms:
        return LatencySummary(p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, max_ms=0.0)
    return LatencySummary(
        p50_ms=percentile(latencies_ms, 50),
        p95_ms=percentile(latencies_ms, 95),
        p99_ms=percentile(latencies_ms, 99),
        max_ms=max(latencies_ms),
    )


# ================================================================ steps 2 + 3 together

def build_report(
    retriever_name: str, top_k: int, eval_files: tuple[str, ...], case_results: list[CaseResult]
) -> EvalReport:
    scored = [result for result in case_results if result.is_scored]
    out_of_scope = [result for result in case_results if result.is_out_of_scope]

    scored_by_file = group_by(scored, key=lambda result: result.eval_file)
    scored_by_category = group_by(scored, key=lambda result: result.category)
    out_of_scope_by_file = group_by(out_of_scope, key=lambda result: result.eval_file)

    by_file = {}
    for eval_file in eval_files:
        by_file[eval_file] = compute_metrics(scored_by_file.get(eval_file, []))

    by_category = {}
    for category in sorted(scored_by_category, key=lambda name: -len(scored_by_category[name])):
        by_category[category] = compute_metrics(scored_by_category[category])

    return EvalReport(
        retriever_name=retriever_name,
        top_k=top_k,
        overall=compute_metrics(scored),
        by_file=by_file,
        by_category=by_category,
        score_cutoffs=find_score_cutoffs_per_file(eval_files, scored_by_file, out_of_scope_by_file),
        case_results=case_results,
    )


def find_score_cutoffs_per_file(
    eval_files: tuple[str, ...],
    scored_by_file: dict[str, list[CaseResult]],
    out_of_scope_by_file: dict[str, list[CaseResult]],
) -> dict[str, ScoreCutoff]:
    """A cut-off for every file that has both in-scope and out-of-scope emails."""
    # Out-of-scope emails are compared only with in-scope emails from the same file,
    # so both sides have the same writing style and length.
    score_cutoffs = {}
    for eval_file in eval_files:
        in_scope = scored_by_file.get(eval_file, [])
        out_of_scope = out_of_scope_by_file.get(eval_file, [])
        cutoff = find_best_score_cutoff(in_scope, out_of_scope)
        if cutoff is not None:
            score_cutoffs[eval_file] = cutoff
    return score_cutoffs


# ================================================================ step 4: print and save

def print_report(report: EvalReport) -> None:
    case_count = len(report.case_results)
    print(f"\n{report.retriever_name} on {case_count} eval cases (top {report.top_k} retrieved)\n")
    print_metrics_table("By eval file", {**report.by_file, "ALL": report.overall})
    print()
    print_metrics_table("By category", report.by_category)
    print_score_cutoffs(report.score_cutoffs)
    print_latency_and_skipped(report.overall.latency, report.case_results)
    print_misses(report)


def print_metrics_table(title: str, rows: dict[str, RetrievalMetrics]) -> None:
    print(f"{title:24s} {'n':>4s}   {'hit@1':>6s} {'hit@3':>6s} {'MRR':>6s}   {'lenient':>7s}"
          f"   {'p50 ms':>8s} {'p95 ms':>8s} {'p99 ms':>8s} {'max ms':>8s}")
    for name, metrics in rows.items():
        latency = metrics.latency
        print(f"  {name:22s} {metrics.case_count:4d}   {metrics.hit_at_1:6.1%} {metrics.hit_at_3:6.1%} "
              f"{metrics.mrr:6.3f}   {metrics.lenient_hit_at_3:6.1%}"
              f"   {latency.p50_ms:8.2f} {latency.p95_ms:8.2f} {latency.p99_ms:8.2f} {latency.max_ms:8.2f}")


def print_score_cutoffs(score_cutoffs: dict[str, ScoreCutoff]) -> None:
    for eval_file, cutoff in score_cutoffs.items():
        print(f"\nOut of scope ({eval_file}: "
              f"{cutoff.in_scope_count} in scope vs {cutoff.out_of_scope_count} out of scope)")
        print(f"  median top score: in scope {cutoff.median_top_score_in_scope:.2f}, "
              f"out of scope {cutoff.median_top_score_out_of_scope:.2f}")
        print(f"  best cut-off {cutoff.threshold:.2f}: accuracy {cutoff.accuracy:.1%} "
              f"({cutoff.in_scope_kept}/{cutoff.in_scope_count} in scope kept, "
              f"{cutoff.out_of_scope_caught}/{cutoff.out_of_scope_count} out of scope caught)")


def print_latency_and_skipped(latency: LatencySummary, case_results: list[CaseResult]) -> None:
    print(f"\nLatency per query (scored emails): p50 {latency.p50_ms:.2f} ms, p95 {latency.p95_ms:.2f} ms, "
          f"p99 {latency.p99_ms:.2f} ms, max {latency.max_ms:.2f} ms")

    guideline_only = [
        result for result in case_results if not result.is_out_of_scope and not result.expected_ids
    ]
    print(f"Skipped {len(guideline_only)} cases that expect only support-guideline sections (never indexed)")


def print_misses(report: EvalReport) -> None:
    misses = report.misses
    print(f"\nMisses (expected section not in top 3): {len(misses)} of {len(report.scored_results)}. "
          f"First {MISSES_TO_PRINT}:")
    for miss in misses[:MISSES_TO_PRINT]:
        print(f"  {miss.case_id:18s} expected {','.join(miss.expected_ids):14s} got {miss.top_5_ids[:3]}  "
              f"| {miss.query[:70]!r}")


def save_report(report: EvalReport, path: Path) -> None:
    path.write_text(json.dumps(asdict(report), indent=2) + "\n")
