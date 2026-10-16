"""Deterministic checks of one agent run against a case's expectations. No LLM involved.

Each check returns a CheckResult(name, passed, detail). A case passes only if every check passes.
"""

import re
from difflib import SequenceMatcher
from dataclasses import dataclass

from ai_analyzer.agent.loop import AgentResult
from ai_analyzer.tools import tool_definitions

# Things that must never reach a customer: internal codes like PRICE_OUT_OF_BAND, our tool names,
# and field names that only exist in tool output.
INTERNAL_CODE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")
INTERNAL_WORDS = {
    "user_id", "reason_code", "hold_reason", "ledger_credited", "matches_ledger", "gateway_status",
}
TOOL_NAMES = {definition["name"] for definition in tool_definitions()}
# A reply this similar to one we already sent in the thread is a repeat, not an answer.
REPEAT_SIMILARITY = 0.85


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def run_checks(expect: dict, result: AgentResult, previous_replies: list[str] = ()) -> list[CheckResult]:
    called = [call.name for call in result.tool_calls]
    retrieved = {sop.section.sop_id for sop in result.sops}
    followed = set(result.sop_ids_used)
    checks = [
        check_sop(expect.get("sop_ids", []), retrieved, followed),
        check_tools_called(expect.get("tools_called", []), called),
        check_tools_not_called(expect.get("tools_not_called", []), called),
        check_escalation(expect["escalate"], result.escalate),
        check_reply_decision(expect.get("reply", True), result.reply),
    ]
    if result.reply:
        checks.append(check_no_internal_terms(result.reply))
        if expect.get("must_contain_any"):
            checks.append(check_contains_any(expect["must_contain_any"], result.reply))
        if expect.get("max_words"):
            checks.append(check_max_words(expect["max_words"], result.reply))
        if previous_replies:
            checks.append(check_not_a_repeat(result.reply, previous_replies))
    return checks


def check_sop(expected: list[str], retrieved: set[str], followed: set[str]) -> CheckResult:
    if not expected:  # out-of-scope case: nothing to find
        return CheckResult("sop", True, "no SOP expected")
    hit = [sop_id for sop_id in expected if sop_id in retrieved and sop_id in followed]
    return CheckResult("sop", bool(hit), f"expected one of {expected}; retrieved {sorted(retrieved)}, "
                                          f"followed {sorted(followed)}")


def check_tools_called(expected: list[str], called: list[str]) -> CheckResult:
    missing = [name for name in expected if name not in called]
    return CheckResult("tools_called", not missing, f"missing {missing}" if missing else f"called {called}")


def check_tools_not_called(forbidden: list[str], called: list[str]) -> CheckResult:
    wrong = [name for name in forbidden if name in called]
    return CheckResult("tools_not_called", not wrong, f"should not call {wrong}" if wrong else "")


def check_escalation(expected: bool, actual: bool) -> CheckResult:
    if expected == actual:
        return CheckResult("escalation", True)
    # A missed escalation is the dangerous direction: a case that needed a human got an auto-answer.
    kind = "MISSED escalation" if expected else "unneeded escalation"
    return CheckResult("escalation", False, kind)


def check_reply_decision(expected_reply: bool, reply: str | None) -> CheckResult:
    if expected_reply == bool(reply):
        return CheckResult("reply_decision", True)
    detail = "replied when no reply was needed" if reply else "no reply written"
    return CheckResult("reply_decision", False, detail)


def check_no_internal_terms(reply: str) -> CheckResult:
    leaks = set(INTERNAL_CODE.findall(reply))
    leaks |= {word for word in INTERNAL_WORDS | TOOL_NAMES if word in reply}
    return CheckResult("no_internal_terms", not leaks, f"leaked {sorted(leaks)}" if leaks else "")


def check_contains_any(options: list[str], reply: str) -> CheckResult:
    found = [option for option in options if option.lower() in reply.lower()]
    return CheckResult("must_contain_any", bool(found), f"none of {options} in reply" if not found else "")


def check_max_words(limit: int, reply: str) -> CheckResult:
    words = len(reply.split())
    return CheckResult("max_words", words <= limit, f"{words} words (limit {limit})")


def check_not_a_repeat(reply: str, previous_replies: list[str]) -> CheckResult:
    """Fails when the reply is (nearly) one we already sent in this thread."""
    reply = _normalise(reply)
    similarity = max(SequenceMatcher(None, reply, _normalise(old)).ratio() for old in previous_replies)
    return CheckResult("not_a_repeat", similarity < REPEAT_SIMILARITY,
                       f"similarity to a previous reply {similarity:.2f}")


def _normalise(text: str) -> str:
    """Lower case, whitespace collapsed: a re-wrapped copy of a reply is still a repeat."""
    return " ".join(text.lower().split())
