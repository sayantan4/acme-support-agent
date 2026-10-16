"""Build a retrieval eval set from Banking77 (real customer banking queries, not written by us).

    python scripts/import_banking77.py    # writes ai_analyzer/knowledge_retriever/evals/evals_banking77.json

Source: PolyAI Banking77 test split, https://github.com/PolyAI-LDN/task-specific-datasets
License: CC BY 4.0 (attribution: Casanueva et al., 2020, "Efficient Intent Detection with
Dual Sentence Encoders"). Queries are used unchanged.

Banking77 is a UK card/neobank dataset, so "top up" means adding money and "transfer" usually
means sending money out. INTENT_TO_SOP maps its intents onto our SOP sections: a human should
review this table, because it decides what "correct retrieval" means. Card, ATM and cash
intents have no Acme equivalent and become out-of-scope cases (the agent should not pretend
an SOP applies).
"""

import csv
import io
import json
import random
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/test.csv"
OUT = Path("ai_analyzer/knowledge_retriever/evals/evals_banking77.json")
PER_INTENT = 3          # mapped intents
OUT_OF_SCOPE_TOTAL = 20  # sampled across all card/ATM/cash intents
SEED = 77

# intent: (category, expected section(s), acceptable section(s))
INTENT_TO_SOP = {
    # adding money ("top up") -> INR deposits
    "pending_top_up":                          ("payments", ["PAY-01"], ["PAY-03"]),
    "top_up_failed":                           ("payments", ["PAY-02"], ["PAY-10", "PAY-12"]),
    "top_up_reverted":                         ("payments", ["PAY-05"], ["PAY-02"]),
    "balance_not_updated_after_bank_transfer": ("payments", ["PAY-03"], ["PAY-01"]),
    "transfer_into_account":                   ("payments", ["PAY-11"], ["PAY-03"]),
    "top_up_limits":                           ("payments", ["PAY-06"], ["PAY-11"]),
    "transaction_charged_twice":               ("payments", ["PAY-08"], []),
    "Refund_not_showing_up":                   ("payments", ["PAY-05"], []),
    "supported_cards_and_currencies":          ("payments", ["PAY-11"], []),
    "topping_up_by_card":                      ("payments", ["PAY-11"], []),
    # sending money out ("transfer", "withdrawal") -> INR withdrawals
    "pending_transfer":                        ("withdrawals", ["WDR-01"], ["WDR-02"]),
    "pending_cash_withdrawal":                 ("withdrawals", ["WDR-01"], ["WDR-02"]),
    "failed_transfer":                         ("withdrawals", ["WDR-03"], ["WDR-07", "WDR-08"]),
    "declined_transfer":                       ("withdrawals", ["WDR-03"], ["WDR-05", "WDR-07", "WDR-08"]),
    "transfer_not_received_by_recipient":      ("withdrawals", ["WDR-02"], ["WDR-01"]),
    "cancel_transfer":                         ("withdrawals", ["WDR-11"], []),
    "transfer_timing":                         ("withdrawals", ["WDR-12"], ["WDR-01"]),
    "beneficiary_not_allowed":                 ("withdrawals", ["WDR-07"], ["BNK-01"]),
    # fees
    "transfer_fee_charged":                    ("fees", ["FEE-03"], ["WDR-12"]),
    "top_up_by_bank_transfer_charge":          ("fees", ["FEE-10"], ["PAY-11"]),
    "top_up_by_card_charge":                   ("fees", ["FEE-10"], ["PAY-11"]),
    "extra_charge_on_statement":               ("fees", ["FEE-07"], []),
    # identity / KYC
    "verify_my_identity":                      ("kyc", ["KYC-03"], ["KYC-00", "KYC-01"]),
    "unable_to_verify_identity":               ("kyc", ["KYC-00"], ["KYC-02", "KYC-14", "KYC-15"]),
    "why_verify_identity":                     ("kyc", ["KYC-00"], ["ONB-11"]),
    "edit_personal_details":                   ("kyc", ["KYC-18"], ["KYC-17", "SEC-04", "SEC-05"]),
    "verify_source_of_funds":                  ("risk", ["RSK-03"], []),
    # onboarding, security, closure
    "age_limit":                               ("onboarding", ["ONB-10"], []),
    "country_support":                         ("onboarding", ["ONB-09"], []),
    "passcode_forgotten":                      ("onboarding", ["ONB-04"], ["SEC-10"]),
    "lost_or_stolen_phone":                    ("security", ["SEC-03"], ["SEC-09"]),
    "terminate_account":                       ("closure", ["CLS-01"], []),
}

# Products Acme doesn't have: cards, ATMs, cash, FX, salary accounts, merchant refunds.
OUT_OF_SCOPE_INTENTS = [
    "activate_my_card", "apple_pay_or_google_pay", "atm_support", "automatic_top_up",
    "balance_not_updated_after_cheque_or_cash_deposit", "card_about_to_expire", "card_acceptance",
    "card_arrival", "card_delivery_estimate", "card_linking", "card_not_working",
    "card_payment_fee_charged", "card_payment_not_recognised", "card_payment_wrong_exchange_rate",
    "card_swallowed", "cash_withdrawal_charge", "cash_withdrawal_not_recognised", "change_pin",
    "compromised_card", "contactless_not_working", "declined_card_payment",
    "declined_cash_withdrawal", "direct_debit_payment_not_recognised", "disposable_card_limits",
    "exchange_charge", "exchange_rate", "exchange_via_app", "fiat_currency_support",
    "get_disposable_virtual_card", "get_physical_card", "getting_spare_card", "getting_virtual_card",
    "lost_or_stolen_card", "order_physical_card", "pending_card_payment", "pin_blocked",
    "receiving_money", "request_refund", "reverted_card_payment?", "top_up_by_cash_or_cheque",
    "verify_top_up", "virtual_card_not_working", "visa_or_mastercard",
    "wrong_amount_of_cash_received", "wrong_exchange_rate_for_cash_withdrawal",
]


def load_rows() -> list[dict]:
    with urllib.request.urlopen(URL, timeout=60) as response:
        return list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))


def make_case(n: int, row: dict, category, expected, acceptable, out_of_scope=False) -> dict:
    """One eval case in our format, numbered b77-001, b77-002, ..."""
    return {
        "id": f"b77-{n:03d}",
        "email": {"from": "customer@example.com", "subject": "", "body": row["text"].strip()},
        "expected_category": category,
        "expected_sop_ids": expected,
        "acceptable_sop_ids": acceptable,
        "out_of_scope": out_of_scope,
        "eval_scope": "retrieval",
        "language": "english",
        "source": "banking77",
        "source_intent": row["category"],
    }


def main() -> None:
    rows = load_rows()
    known = set(INTENT_TO_SOP) | set(OUT_OF_SCOPE_INTENTS)
    unmapped = {row["category"] for row in rows} - known
    assert not unmapped, f"intents missing from both lists: {unmapped}"

    rng = random.Random(SEED)
    cases = []
    for intent, (category, expected, acceptable) in INTENT_TO_SOP.items():
        intent_rows = [row for row in rows if row["category"] == intent]
        for row in rng.sample(intent_rows, PER_INTENT):
            cases.append(make_case(len(cases) + 1, row, category, expected, acceptable))

    out_of_scope_rows = [row for row in rows if row["category"] in OUT_OF_SCOPE_INTENTS]
    for row in rng.sample(out_of_scope_rows, OUT_OF_SCOPE_TOTAL):
        cases.append(make_case(len(cases) + 1, row, None, [], [], out_of_scope=True))

    OUT.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(cases)} cases to {OUT} "
          f"({len(INTENT_TO_SOP) * PER_INTENT} mapped, {OUT_OF_SCOPE_TOTAL} out of scope)")


if __name__ == "__main__":
    main()
