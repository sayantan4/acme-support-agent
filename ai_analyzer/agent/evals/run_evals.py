"""Run the support agent on the eval cases and score every reply.

    uv run python -m ai_analyzer.agent.evals.run_evals                   # all cases
    uv run python -m ai_analyzer.agent.evals.run_evals --suite thread    # only thread_cases.json
    uv run python -m ai_analyzer.agent.evals.run_evals --cases id1,id2   # only these cases
    uv run python -m ai_analyzer.agent.evals.run_evals --no-judge        # skip the LLM judge (cheaper)

For each case:
    1. run the agent    real retrieval + real LLM; tools run in-process on ToolSvc's fake DB
    2. check            deterministic checks (checks.py)
    3. judge            LLM grades the reply against the case's rubric (judge.py)
    4. record           one dict per case, saved to results/

Needs OPENAI_API_KEY. Auth, ToolSvc and the main app do not need to be running.
"""

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from ai_analyzer.agent.evals.checks import run_checks
from ai_analyzer.agent.evals.judge import JUDGE_PROMPT_VERSION, judge_reply
from ai_analyzer.agent.loop import AgentResult, run_agent
from ai_analyzer.agent.prompt import AGENT_PROMPT_VERSION
from ai_analyzer.services.eval_import_service import import_quietly
from toolsvc import call_tool, fake_db

EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"
SUITES = {
    "single": EVALS_DIR / "cases.json",         # one customer email (or a short thread of them)
    "thread": EVALS_DIR / "thread_cases.json",  # a follow-up after we already replied
}
CHECK_NAMES = ["sop", "tools_called", "tools_not_called", "escalation", "reply_decision",
               "no_internal_terms", "must_contain_any", "max_words", "not_a_repeat"]

# One case at a time: a case sends ~15-25k tokens in ~10 s; the org limit is 200k tokens/minute.
DEFAULT_WORKERS = 1


# ---------------------------------------------------------------------------
# Loading cases
# ---------------------------------------------------------------------------

def load_cases(only: set[str] | None = None, suite: str = "all") -> list[dict]:
    files = list(SUITES.values()) if suite == "all" else [SUITES[suite]]

    cases = []
    for file in files:
        if file.exists():
            cases += json.loads(file.read_text())

    if only:
        cases = [case for case in cases if case["id"] in only]
    return cases


# ---------------------------------------------------------------------------
# One case: run the agent, check, judge, record
# ---------------------------------------------------------------------------

def evaluate_case(case: dict, use_judge: bool) -> dict:
    expect = case["expect"]
    previous_replies = case.get("previous_replies", [])

    # 1. run the agent
    started = time.perf_counter()
    result = run_case_agent(case, previous_replies)
    seconds = time.perf_counter() - started

    # 2. deterministic checks
    checks = run_checks(expect, result, previous_replies)

    # 3. LLM judge (only when there is a reply and a rubric to grade it against)
    verdict = None
    has_rubric = bool(expect.get("reply_must") or expect.get("reply_must_not"))
    if use_judge and result.reply and has_rubric:
        verdict = judge_reply(case, result)

    # 4. record
    checks_passed = all(check.passed for check in checks)
    judge_passed = verdict is None or verdict.passed
    return {
        "id": case["id"],
        "passed": checks_passed and judge_passed,
        "checks": [asdict(check) for check in checks],
        "judge": asdict(verdict) if verdict else None,
        "agent": describe_agent_run(result),
        "seconds": round(seconds, 2),
        "tokens": count_tokens(result, verdict),
    }


def run_case_agent(case: dict, previous_replies: list[str]) -> AgentResult:
    messages = as_messages(case)
    return run_agent(
        case.get("summary_till_now", ""),
        messages,
        reply_to_message_id=messages[-1]["id"],
        previous_replies=previous_replies,
        last_reply_kind=case.get("last_reply_kind"),
        run_tool=tools_for(case["user_id"]),
    )


def as_messages(case: dict) -> list[dict]:
    """The case's messages in the shape the main app returns them: ids 1, 2, ... oldest first."""
    messages = []
    for number, message in enumerate(case["messages"], start=1):
        messages.append({
            "id": number,
            "from": message["from"],
            "subject": message["subject"],
            "date": message.get("date", ""),
            "body": message["body"],
            "is_automated": False,
        })
    return messages


def tools_for(user_id: int):
    """The agent's run_tool, but in-process: the case's customer stands in for ToolSvc's lookup."""
    def run_tool(name: str, arguments: dict, message_id: int) -> dict:
        return call_tool(name, arguments, user_id=user_id)
    return run_tool


def describe_agent_run(result: AgentResult) -> dict:
    """What the agent did, for reading the results file."""
    return {
        "sops_retrieved": [sop.section.sop_id for sop in result.sops],
        "sops_followed": result.sop_ids_used,
        "tools_called": [{"name": call.name, "arguments": call.arguments, "ok": call.ok}
                         for call in result.tool_calls],
        "escalate": result.escalate,
        "escalation_note": result.escalation_note,
        "summary": result.summary,
        "reply": result.reply,
        "reply_kind": result.reply_kind,
        "reply_dropped": result.reply_dropped,
    }


def count_tokens(result: AgentResult, verdict) -> dict:
    """Agent + reranker tokens, and the judge's separately."""
    return {
        "input": result.input_tokens + result.retrieval_input_tokens,
        "cached": result.cached_input_tokens,
        "output": result.output_tokens + result.retrieval_output_tokens,
        "judge": verdict.input_tokens + verdict.output_tokens if verdict else 0,
    }


# ---------------------------------------------------------------------------
# All cases: one summary
# ---------------------------------------------------------------------------

def summarize(results: list[dict]) -> dict:
    all_checks = [check for r in results for check in r["checks"]]
    readable_verdicts = [r["judge"] for r in results if r["judge"] and not r["judge"]["error"]]
    rubric_items = [item for verdict in readable_verdicts for item in verdict["items"]]
    seconds = sorted(r["seconds"] for r in results)
    tokens_per_case = [r["tokens"]["input"] + r["tokens"]["output"] for r in results]
    passed = sum(r["passed"] for r in results)

    return {
        "cases": len(results),
        "passed": passed,
        "pass_rate": share(passed, len(results)),
        "check_pass_rates": {name: check_pass_rate(all_checks, name) for name in CHECK_NAMES},
        "missed_escalations": count_detail(all_checks, "MISSED escalation"),
        "unneeded_escalations": count_detail(all_checks, "unneeded escalation"),
        "judge_rubric_item_pass_rate": share(sum(item["passed"] for item in rubric_items), len(rubric_items)),
        "judge_grounded_rate": share(sum(v["grounded"] for v in readable_verdicts), len(readable_verdicts)),
        "judge_unreadable": sum(1 for r in results if r["judge"] and r["judge"]["error"]),
        "seconds_p50": statistics.median(seconds),
        "seconds_p95": seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))],
        "tokens_per_case_avg": round(statistics.mean(tokens_per_case)),
        "cached_share": share(sum(r["tokens"]["cached"] for r in results),
                              max(1, sum(r["tokens"]["input"] for r in results))),
    }


def share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def check_pass_rate(checks: list[dict], name: str) -> float | None:
    """None when no case ran this check (e.g. not_a_repeat only runs on thread cases)."""
    results = [check["passed"] for check in checks if check["name"] == name]
    return share(sum(results), len(results))


def count_detail(checks: list[dict], detail: str) -> int:
    return sum(1 for check in checks if check["detail"] == detail)


# ---------------------------------------------------------------------------
# Printing and saving
# ---------------------------------------------------------------------------

def print_report(results: list[dict], summary: dict) -> None:
    print(f"\nAgent evals  agent={AGENT_PROMPT_VERSION}  judge={JUDGE_PROMPT_VERSION}\n")
    for r in results:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id']:34s} {r['seconds']:5.1f}s")
        for reason in failure_reasons(r):
            print(f"          - {reason[:150]}")
    print_summary(summary)


def failure_reasons(result: dict) -> list[str]:
    reasons = [f"{check['name']}: {check['detail']}" for check in result["checks"] if not check["passed"]]

    verdict = result["judge"]
    if verdict:
        for item in verdict["items"]:
            if not item["passed"]:
                reasons.append(f"judge {item['kind']}: {item['item']} ({item['reason']})")
        if not verdict["grounded"]:
            reasons.append(f"judge: not grounded ({verdict['grounded_reason']})")
        if verdict["error"]:
            reasons.append(verdict["error"])
    return reasons


def print_summary(s: dict) -> None:
    print(f"\n  passed {s['passed']}/{s['cases']} ({s['pass_rate']:.0%})   "
          f"missed escalations: {s['missed_escalations']}   unneeded: {s['unneeded_escalations']}")

    rates = [f"{name} {rate:.0%}" for name, rate in s["check_pass_rates"].items() if rate is not None]
    print("  check pass rates: " + ", ".join(rates))

    if s["judge_rubric_item_pass_rate"] is not None:
        print(f"  judge: rubric items {s['judge_rubric_item_pass_rate']:.0%}, "
              f"grounded {s['judge_grounded_rate']:.0%}, unreadable {s['judge_unreadable']}")

    print(f"  time per case p50 {s['seconds_p50']:.1f}s p95 {s['seconds_p95']:.1f}s   "
          f"tokens per case ~{s['tokens_per_case_avg']:,} ({s['cached_share']:.0%} of input cached)")


def save_results(results: list[dict], summary: dict, suite: str) -> Path:
    suffix = "" if suite == "all" else f"__{suite}"
    path = RESULTS_DIR / f"eval_results_{AGENT_PROMPT_VERSION}__{JUDGE_PROMPT_VERSION}{suffix}.json"
    RESULTS_DIR.mkdir(exist_ok=True)
    content = {
        "agent_prompt": AGENT_PROMPT_VERSION,
        "judge_prompt": JUDGE_PROMPT_VERSION,
        "summary": summary,
        "cases": results,
    }
    path.write_text(json.dumps(content, indent=2, default=str) + "\n")
    return path


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["all", *SUITES], default="all")
    parser.add_argument("--cases", help="comma-separated case ids (default: all)")
    parser.add_argument("--no-judge", action="store_true", help="deterministic checks only")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()

    only = set(args.cases.split(",")) if args.cases else None
    cases = load_cases(only, args.suite)
    use_judge = not args.no_judge

    fake_db.reset()  # tools only read, so one fresh database serves the whole run
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda case: evaluate_case(case, use_judge), cases))

    summary = summarize(results)
    print_report(results, summary)
    path = save_results(results, summary, args.suite)
    print(f"\n  saved {path}")
    import_quietly(path)  # for the ai-evals Grafana dashboard


if __name__ == "__main__":
    main()
