"""Run the scope gate (agent/scope.py) on its eval cases and score it.

    uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals                    # scope_cases.json (~40 cases)
    uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals --retrieval-sets   # + the 558 labelled retrieval emails
    uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals --cases id1,id2
    uv run python -m ai_analyzer.agent.evals.gate_llm.run_scope_evals --rescore results/eval_results_scope-v3__scope.json
        (no API calls: re-score saved results against the current labels, e.g. after fixing labels)

For each case:
    1. run the gate      the real small model (SCOPE_MODEL), with the case's summary, last reply and message
    2. reply decision    for no_issue / out_of_scope, the template logic decides if a reply would be sent
    3. score             decision (and no_issue kind, and reply) against the expected ones

The number that matters most is "wrongly rejected": real Acme issues the gate would have answered with a
template instead of the agent. Needs OPENAI_API_KEY. Saves results/eval_results_<scope prompt>__scope.json
and imports it for the ai-evals Grafana dashboard.
"""

import argparse
import json
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ai_analyzer.agent.scope import SCOPE_PROMPT_VERSION, check_scope
from ai_analyzer.agent.template_replies import reply_without_agent
from ai_analyzer.clients import llm_client
from ai_analyzer.services.eval_import_service import import_quietly

EVALS_DIR = Path(__file__).parent
CASES_FILE = EVALS_DIR / "scope_cases.json"
RETRIEVAL_EVALS = EVALS_DIR.parents[2] / "knowledge_retriever" / "evals"
LABEL_OVERRIDES = EVALS_DIR / "scope_label_overrides.json"  # hand-reviewed scope labels for those emails
RESULTS_DIR = EVALS_DIR / "results"
REJECTED = ("no_issue", "out_of_scope")  # decisions that skip the agent
DEFAULT_WORKERS = 4                      # small calls; well under the rate limit
MESSAGE_PREVIEW_CHARS = 300              # the newest message, saved with each result for the dashboard


# ---------------------------------------------------------------------------
# Loading cases
# ---------------------------------------------------------------------------

def load_cases(only: set[str] | None = None, with_retrieval_sets: bool = False) -> list[dict]:
    cases = json.loads(CASES_FILE.read_text())
    if with_retrieval_sets:
        cases += retrieval_set_cases()
    if only:
        cases = [case for case in cases if case["id"] in only]
    return cases


def retrieval_set_cases() -> list[dict]:
    """The labelled retrieval emails as single-message scope cases: in scope, or out of scope.

    The scope label is the email's out_of_scope flag unless scope_label_overrides.json corrects it;
    emails reviewed as ambiguous are left out.
    """
    overrides = json.loads(LABEL_OVERRIDES.read_text())["overrides"]
    cases = []
    for path in sorted(RETRIEVAL_EVALS.glob("evals*.json")):
        for item in json.loads(path.read_text()):
            case_id = f"{path.stem}/{item['id']}"
            label = overrides.get(case_id, {}).get("scope_label")
            label = label or ("out_of_scope" if item.get("out_of_scope") else "in_scope")
            if label == "ambiguous":
                continue
            email = item["email"]
            cases.append({
                "id": case_id, "category": f"set:{path.stem}", "note": "",
                "summary_till_now": "", "previous_replies": [], "last_reply_kind": None,
                "messages": [{"body": f"{email.get('subject', '')}\n{email['body']}".strip()}],
                "expect": {"decision": label},
            })
    return cases


# ---------------------------------------------------------------------------
# One case: gate, reply decision, score
# ---------------------------------------------------------------------------

def evaluate_case(case: dict) -> dict:
    expect = case["expect"]

    # 1. the gate
    started = time.perf_counter()
    with llm_client.recording() as calls:
        decision = check_scope(case["summary_till_now"], case["messages"], case["previous_replies"],
                               case["last_reply_kind"])
    seconds = time.perf_counter() - started

    # 2. would a reply be sent? (only decided without the agent for no_issue / out_of_scope)
    reply_sent = None
    if decision.decision in REJECTED:
        result = reply_without_agent(decision, case["previous_replies"], case["last_reply_kind"],
                                     reply_to_message_id=1)
        reply_sent = result.reply is not None

    # 3. score
    result = {
        "id": case["id"],
        "category": case["category"],
        "message": message_preview(case),
        "decision": decision.decision,
        "no_issue_kind": decision.no_issue_kind,
        "out_of_scope_topics": decision.out_of_scope_topics,
        "reply_sent": reply_sent,
        "reason": decision.reason,
        "gate_failed": decision.failed,
        "seconds": round(seconds, 2),
        "tokens": {"input": sum(c.input_tokens for c in calls), "cached": sum(c.cached_tokens for c in calls),
                   "output": sum(c.output_tokens for c in calls)},
    }
    return score(result, expect)


def message_preview(case: dict) -> str:
    """The newest customer message of the case, shortened: what the gate mostly decided on."""
    return (case["messages"][-1].get("body") or "")[:MESSAGE_PREVIEW_CHARS]


def score(result: dict, expect: dict) -> dict:
    """Grade one gate result against the case's expectations: adds expected, problems, passed, wrongly_rejected."""
    problems = []
    if result["decision"] != expect["decision"]:
        problems.append(f"decision {result['decision']}, expected {expect['decision']}")
    if "no_issue_kind" in expect and result["no_issue_kind"] != expect["no_issue_kind"]:
        problems.append(f"kind {result['no_issue_kind']}, expected {expect['no_issue_kind']}")
    if "reply" in expect and result["reply_sent"] is not None and result["reply_sent"] != expect["reply"]:
        problems.append("replied, expected silence" if result["reply_sent"] else "silent, expected a reply")
    return {
        **result,
        "expected": expect["decision"],
        "problems": problems,
        "passed": not problems,
        "wrongly_rejected": expect["decision"] in ("in_scope", "mixed") and result["decision"] in REJECTED,
    }


def rescore(path: Path) -> Path:
    """Re-score a saved results file against the current labels (no API calls) and save it again.

    Cases that are no longer in the eval set (e.g. reviewed as ambiguous) are dropped.
    """
    saved = json.loads(path.read_text())
    cases = {case["id"]: case for case in load_cases(with_retrieval_sets=True)}
    results = [score({**result, "message": message_preview(cases[result["id"]])}, cases[result["id"]]["expect"])
               for result in saved["cases"] if result["id"] in cases]
    summary = summarize(results)
    content = {**saved, "summary": summary, "cases": results, "rescored_with_current_labels": True}
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    print(f"  {path.name}: {summary['passed']}/{summary['cases']} passed, "
          f"wrongly rejected {summary['wrongly_rejected']} ({(summary['wrongly_rejected_rate'] or 0):.1%})")
    return path


# ---------------------------------------------------------------------------
# All cases: one summary
# ---------------------------------------------------------------------------

def summarize(results: list[dict]) -> dict:
    should_pass = [r for r in results if r["expected"] in ("in_scope", "mixed")]
    out_of_scope = [r for r in results if r["expected"] == "out_of_scope"]
    no_issue = [r for r in results if r["expected"] == "no_issue"]
    seconds = sorted(r["seconds"] for r in results)
    return {
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "pass_rate": share(sum(r["passed"] for r in results), len(results)),
        "wrongly_rejected": sum(r["wrongly_rejected"] for r in results),
        "wrongly_rejected_rate": share(sum(r["wrongly_rejected"] for r in should_pass), len(should_pass)),
        "out_of_scope_caught_rate": share(sum(r["decision"] == "out_of_scope" for r in out_of_scope),
                                          len(out_of_scope)),
        "no_issue_caught_rate": share(sum(r["decision"] == "no_issue" for r in no_issue), len(no_issue)),
        "gate_failures": sum(r["gate_failed"] for r in results),
        "pass_rate_by_category": {category: share(sum(r["passed"] for r in group), len(group))
                                  for category, group in _by_category(results).items()},
        "confusion": {f"{expected} -> {got}": n
                      for (expected, got), n in sorted(Counter((r["expected"], r["decision"]) for r in results).items())},
        "seconds_p50": statistics.median(seconds),
        "seconds_p95": seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))],
        "tokens_per_case_avg": round(statistics.mean(r["tokens"]["input"] + r["tokens"]["output"] for r in results)),
    }


def share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _by_category(results: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for result in results:
        groups.setdefault(result["category"], []).append(result)
    return groups


# ---------------------------------------------------------------------------
# Printing and saving
# ---------------------------------------------------------------------------

def print_report(results: list[dict], summary: dict) -> None:
    print(f"\nScope gate evals  prompt={SCOPE_PROMPT_VERSION}\n")
    for r in results:
        if not r["passed"] or not r["category"].startswith("set:"):  # the big sets: only failures
            flag = " !! WRONGLY REJECTED" if r["wrongly_rejected"] else ""
            print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id'][:44]:44s} {r['decision']:13s}{flag}")
            for problem in r["problems"]:
                print(f"          - {problem}  ({r['reason'][:90]})")
    s = summary
    print(f"\n  passed {s['passed']}/{s['cases']} ({s['pass_rate']:.0%})   "
          f"wrongly rejected: {s['wrongly_rejected']} ({(s['wrongly_rejected_rate'] or 0):.1%} of real issues)")
    print(f"  out of scope caught: {_pct(s['out_of_scope_caught_rate'])}   "
          f"no issue caught: {_pct(s['no_issue_caught_rate'])}   gate failures: {s['gate_failures']}")
    print("  by category: " + ", ".join(f"{k} {_pct(v)}" for k, v in s["pass_rate_by_category"].items()))
    print(f"  time per case p50 {s['seconds_p50']:.1f}s p95 {s['seconds_p95']:.1f}s   "
          f"tokens per case ~{s['tokens_per_case_avg']:,}")


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def save_results(results: list[dict], summary: dict) -> Path:
    path = RESULTS_DIR / f"eval_results_{SCOPE_PROMPT_VERSION}__scope.json"
    RESULTS_DIR.mkdir(exist_ok=True)
    content = {"scope_prompt": SCOPE_PROMPT_VERSION, "summary": summary, "cases": results}
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--retrieval-sets", action="store_true", help="also the 558 labelled retrieval emails")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--rescore", nargs="+", type=Path, help="saved results files to re-score (no API calls)")
    args = parser.parse_args()

    if args.rescore:
        for path in args.rescore:
            import_quietly(rescore(path))
        return

    only = set(args.cases.split(",")) if args.cases else None
    cases = load_cases(only, with_retrieval_sets=args.retrieval_sets)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(evaluate_case, cases))

    summary = summarize(results)
    print_report(results, summary)
    path = save_results(results, summary)
    print(f"\n  saved {path}")
    import_quietly(path)  # for the ai-evals Grafana dashboard


if __name__ == "__main__":
    main()
