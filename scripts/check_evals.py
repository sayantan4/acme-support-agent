"""Sanity checks for ai_analyzer/knowledge_retriever/evals/*.json against the SOP knowledge base.

    python scripts/check_evals.py

Prints a summary of the eval set (categories, SOP coverage, email length), then every problem
found in the cases: unknown or duplicate ids, wrong categories, inconsistent routing, and
must_include phrases the expected SOP text does not support.
"""

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

EVALS = Path(sys.argv[1] if len(sys.argv) > 1 else "ai_analyzer/knowledge_retriever/evals/evals.json")
SOPS = Path("ai_analyzer/sops")

CATEGORY_OF_FILE = {  # from support_guidelines_sop SUP-08
    "onboarding_sop": "onboarding", "kyc_sop": "kyc", "bank_account_sop": "bank_account",
    "payment_sop": "payments", "withdrawal_sop": "withdrawals", "crypto_transfers_sop": "crypto",
    "trade_sop": "trading", "fees_charges_sop": "fees", "tax_statements_sop": "tax",
    "rewards_sop": "rewards", "account_security_sop": "security", "fraud_risk_sop": "risk",
    "account_closure_sop": "closure", "app_technical_sop": "technical",
    "complaints_escalation_sop": "complaint", "support_guidelines_sop": "general",
}
VALID_CATEGORIES = set(CATEGORY_OF_FILE.values())
NEVER_AUTO_REPLY = {"CMP-04", "CMP-06", "CMP-07", "SUP-05"}  # holding reply / human only
HINGLISH = re.compile(
    r"\b(hai|nahi|kar|mera|mujhe|maine|kya|raha|rahe|gaya|diya|kyu|paisa|abhi|bhai|yaar)\b", re.I
)


def main() -> None:
    sections = load_sections()
    cases = json.loads(EVALS.read_text())
    problems = find_problems(cases, sections)

    print_summary(cases, sections)
    print(f"\nproblems: {len(problems)}")
    for case_id, message in problems:
        print(f"   {case_id:12s} {message}")


def load_sections() -> dict[str, tuple[str, str, str]]:
    """Section id -> (file stem, section title, full section text)."""
    sections = {}
    for path in SOPS.glob("*.txt"):
        parts = re.split(r"^(?=## [A-Z]{2,4}-\d{2}:)", path.read_text(), flags=re.M)
        for part in parts:
            heading = re.match(r"## ([A-Z]{2,4}-\d{2}):\s*(.*)", part)
            if heading:
                sections[heading.group(1)] = (path.stem, heading.group(2).strip(), part)
    return sections


# ================================================================ problems

def find_problems(cases: list[dict], sections: dict) -> list[tuple[str, str]]:
    """(case id, message) for everything wrong in the eval set, duplicates first."""
    problems = find_duplicates(cases)
    data_dictionary = (SOPS / "data_dictionary.txt").read_text().lower()
    for case in cases:
        for message in check_case(case, sections, data_dictionary):
            problems.append((case["id"], message))
    return problems


def find_duplicates(cases: list[dict]) -> list[tuple[str, str]]:
    problems = []
    for case_id, count in Counter(case["id"] for case in cases).items():
        if count > 1:
            problems.append((case_id, f"duplicate id ({count}x)"))
    bodies = Counter(case["email"]["body"].strip().lower() for case in cases)
    for body, count in bodies.items():
        if count > 1:
            problems.append(("-", f"duplicate email body ({count}x): {body[:60]}"))
    return problems


def check_case(case: dict, sections: dict, data_dictionary: str) -> list[str]:
    expected, acceptable = case["expected_sop_ids"], case.get("acceptable_sop_ids", [])
    problems = check_sop_ids(case, expected, acceptable, sections)
    if case.get("out_of_scope"):
        return problems  # no SOP applies; nothing else to check

    problems += check_category(case, expected, sections)
    if "should_auto_reply" not in case:
        return problems  # retrieval-only case: no routing or reply checks

    problems += check_routing(case, expected)
    problems += check_must_include(case, expected + acceptable, sections, data_dictionary)
    return problems


def check_sop_ids(case: dict, expected: list[str], acceptable: list[str], sections: dict) -> list[str]:
    problems = []
    if not expected and not case.get("out_of_scope"):
        problems.append("no expected_sop_ids")
    for sop_id in expected + acceptable:
        if sop_id not in sections:
            problems.append(f"unknown SOP id {sop_id}")
    if set(expected) & set(acceptable):
        problems.append(f"same id in expected and acceptable: {set(expected) & set(acceptable)}")
    return problems


def check_category(case: dict, expected: list[str], sections: dict) -> list[str]:
    """The case's category must be valid and match the file of its first expected section."""
    category = case["expected_category"]
    if category not in VALID_CATEGORIES:
        return [f"unknown category {category!r}"]
    if expected and expected[0] in sections:
        file_category = CATEGORY_OF_FILE[sections[expected[0]][0]]
        if file_category not in (category, "general"):
            return [f"category {category!r} but {expected[0]} is in {file_category!r}"]
    return []


def check_routing(case: dict, expected: list[str]) -> list[str]:
    problems = []
    human_only = set(expected) & NEVER_AUTO_REPLY
    if human_only and case["should_auto_reply"]:
        problems.append(f"{human_only} means human/holding reply only, but should_auto_reply=true")
    if not case["should_auto_reply"] and not case["should_escalate"]:
        problems.append("no auto reply AND no escalation: nobody handles this case")
    return problems


def check_must_include(case: dict, sop_ids: list[str], sections: dict, data_dictionary: str) -> list[str]:
    """Every must_include phrase should be supported by the expected sections' text."""
    problems = []
    sop_text = " ".join(sections[sop_id][2].lower() for sop_id in sop_ids if sop_id in sections)
    sop_text += " " + data_dictionary
    for phrase in case["reply_must_include"]:
        words = [word for word in re.findall(r"[a-z0-9,]+", phrase.lower()) if len(word) > 2]
        missing = [word for word in words if word not in sop_text]
        if missing:
            problems.append(f"must_include {phrase!r}: not found in SOP text ({missing})")
    for phrase in case["reply_must_include"]:
        if phrase in case["reply_must_not_include"]:
            problems.append(f"{phrase!r} in both must_include and must_not_include")
    return problems


# ================================================================ summary

def print_summary(cases: list[dict], sections: dict) -> None:
    print(f"cases: {len(cases)}")
    categories = Counter(case["expected_category"] or "OUT_OF_SCOPE" for case in cases)
    print("by category:", dict(categories.most_common()))
    if all("should_auto_reply" in case for case in cases):
        print(f"auto_reply=false: {sum(not case['should_auto_reply'] for case in cases)}, "
              f"escalate=true: {sum(case['should_escalate'] for case in cases)}")
    print_coverage(cases, sections)

    word_counts = [len(case["email"]["body"].split()) for case in cases]
    hinglish_count = sum(bool(HINGLISH.search(case["email"]["body"])) for case in cases)
    print(f"email words: min {min(word_counts)}, median {statistics.median(word_counts)}, "
          f"max {max(word_counts)}; Hinglish: {hinglish_count}")

    leakages = [title_leakage(case, sections) for case in cases]
    print(f"section-title words found in the email: median {statistics.median(leakages):.0%}, "
          f">=75%: {sum(leakage >= 0.75 for leakage in leakages)} cases")


def print_coverage(cases: list[dict], sections: dict) -> None:
    """How many sections of each SOP file are some case's expected answer."""
    covered = {sop_id for case in cases for sop_id in case["expected_sop_ids"]}
    covered_by_file = Counter(sections[sop_id][0] for sop_id in covered if sop_id in sections)
    total_by_file = Counter(stem for stem, _title, _text in sections.values())
    print(f"SOP sections covered as expected answer: {len(covered)} of {len(sections)}")
    for stem in sorted(total_by_file):
        print(f"   {stem:28s} {covered_by_file.get(stem, 0):2d}/{total_by_file[stem]}")


def title_leakage(case: dict, sections: dict) -> float:
    """Share of the expected section's title words that appear in the email: high = too easy."""
    sop_id = case["expected_sop_ids"][0] if case["expected_sop_ids"] else None
    if sop_id not in sections:
        return 0
    title_words = set(re.findall(r"[a-z]{4,}", sections[sop_id][1].lower()))
    body_words = set(re.findall(r"[a-z]{4,}", case["email"]["body"].lower()))
    return len(title_words & body_words) / max(1, len(title_words))


if __name__ == "__main__":
    main()
