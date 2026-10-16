"""ToolSvc: tools run only for the verified customer, its API, tools.json, and the SOPs' tool calls."""

import json
import re
from pathlib import Path

import pytest

from ai_analyzer.tools import TOOLS_JSON
from ai_analyzer.tools.export import render
from toolsvc import TOOLS, call_tool, catalog, fake_db
from toolsvc.clients import main_app_client
from toolsvc.controllers.http_tools import create_app
from toolsvc.models.fake_db import ago

SOPS_DIR = Path(__file__).resolve().parents[1] / "ai_analyzer" / "sops"
TOOL_CALL = re.compile(r"\b((?:get|check|find)_[a-z0-9_]+)\(([^()]*)\)")
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}

# One working call per tool: (verified customer, arguments). Seeded data: toolsvc/models/fake_db.seed_rows.
EXAMPLE_CALLS = {
    "find_user": (106, {"email": "rahul.mehta@example.com"}),
    "get_user": (101, {}),
    "find_user_by_reference": (104, {"tx_hash": "0xusdtin2"}),
    "get_user_devices": (103, {}),
    "get_auth_events": (103, {}),
    "get_otp_logs": (106, {}),
    "get_otp_delivery_health": (106, {"minutes": 60}),
    "get_kyc_applications": (102, {}),
    "get_kyc_vendor_logs": (102, {"kyc_id": 202, "check_types": ["AADHAAR_OTP"]}),
    "get_kyc_vendor_health": (102, {}),
    "get_bank_accounts": (101, {}),
    "find_bank_account_owners": (106, {"account_number_masked": "XXXXXX4321", "ifsc": "HDFC0001234"}),
    "get_bank_verification_failures": (106, {"minutes": 2880}),
    "get_deposits": (101, {"method": "UPI", "days": 7}),
    "check_deposit": (101, {"deposit_id": 401}),
    "find_deposits_by_reference": (102, {"utr": "712345678903"}),
    "get_refunds": (102, {}),
    "get_withdrawals": (101, {"asset": "INR"}),
    "check_withdrawal": (101, {"withdrawal_id": 604}),
    "check_wallet_balance": (101, {}),
    "get_ledger_entries": (101, {"reference_type": "WITHDRAWAL", "reference_id": 604}),
    "get_fee_summary": (101, {"from_time": ago(days=30), "to_time": ago()}),
    "get_supported_networks": (104, {"asset": "USDT"}),
    "get_crypto_deposits": (104, {}),
    "get_deposit_addresses": (104, {}),
    "find_deposit_address_owner": (104, {"address": "rAcmeShared", "memo_tag": "104001"}),
    "get_address_whitelist": (104, {}),
    "get_orders": (104, {"source": "SIP"}),
    "check_order": (101, {"order_id": 801}),
    "get_trades": (101, {}),
    "get_instrument": (101, {"symbol": "XYZ-INR"}),
    "get_market_price_range": (101, {"symbol": "BTC-INR", "from_time": ago(days=7)}),
    "get_price_incidents": (104, {"symbol": "ETH-INR", "at_time": ago(days=6, minutes=10)}),
    "get_order_error_count": (101, {"around_time": ago(days=1)}),
    "get_sip_plans": (104, {}),
    "get_rewards": (104, {}),
    "get_campaigns": (104, {"name_contains": "diwali"}),
    "get_referrals": (106, {}),
    "get_coupon": (104, {"coupon_code": "acme500"}),
    "get_risk_holds": (103, {}),
    "get_tickets": (103, {}),
    "get_notifications": (104, {}),
    "get_tax_records": (104, {}),
    "get_closure_requests": (105, {}),
}


@pytest.fixture(autouse=True)
def fresh_db():
    fake_db.reset()


def run(name, user_id, **arguments):
    return call_tool(name, arguments, user_id=user_id)


# ---------- every tool works, for one customer at a time ----------

@pytest.mark.parametrize("name", sorted(TOOLS))
def test_every_tool_runs_for_a_verified_customer(name):
    assert name in EXAMPLE_CALLS, f"add an example call for {name}"
    user_id, arguments = EXAMPLE_CALLS[name]
    result = call_tool(name, arguments, user_id=user_id)
    assert "result" in result, result
    json.dumps(result)  # what the caller gets back must be JSON


def test_callers_cannot_pass_user_id():
    assert "unknown arguments ['user_id']" in call_tool("get_deposits", {"user_id": 102}, user_id=101)["error"]
    assert all("user_id" not in spec["parameters"] for spec in catalog())


def test_list_tools_only_return_the_customers_own_rows():
    assert {d["user_id"] for d in run("get_deposits", 101)["result"]} == {101}
    assert run("get_deposits", 106)["result"] == []           # 106 has no deposits


def test_another_customers_record_looks_like_it_does_not_exist():
    assert run("check_deposit", 101, deposit_id=401)["result"]["deposit"]["user_id"] == 101
    assert run("check_deposit", 102, deposit_id=401) == {"error": "deposit 401 not found"}
    assert run("check_withdrawal", 104, withdrawal_id=604) == {"error": "withdrawal 604 not found"}
    assert run("check_order", 104, order_id=801) == {"error": "order 801 not found"}
    assert run("get_kyc_vendor_logs", 101, kyc_id=202) == {"error": "KYC application 202 not found"}


def test_cross_account_lookups_never_say_who_the_other_customer_is():
    other = run("find_user", 106, email="rahul.mehta@example.com")["result"]
    assert other["matches_your_account"] is False
    assert other["other_accounts"] and all("user_id" not in a and "email" not in a for a in other["other_accounts"])

    assert run("find_user_by_reference", 102, utr="512345678901")["result"] == [
        {"matched_in": "deposit", "belongs_to_you": False}]
    reference = run("find_deposits_by_reference", 102, utr="512345678901")["result"]
    assert reference["deposits"] == [] and reference["used_by_another_account"] is True

    owners = run("find_bank_account_owners", 106, account_number_masked="XXXXXX4321", ifsc="HDFC0001234")["result"]
    assert owners == [{"link_status": "VERIFIED", "other_account_status": "ACTIVE", "same_name_as_customer": 0}]

    address = run("find_deposit_address_owner", 101, address="rAcmeShared")["result"]
    assert address == {"is_acme_address": True, "belongs_to_you": False, "asset_networks": ["XRP/XRP"]}
    assert run("get_referrals", 101)["result"]["as_referrer"][0].keys().isdisjoint({"referee_user_id", "referrer_user_id"})


def test_personal_coupon_of_another_customer_is_not_valid():
    assert run("get_coupon", 104, coupon_code="neha10")["result"]["valid_for_this_customer"] is True
    assert run("get_coupon", 101, coupon_code="neha10")["result"]["valid_for_this_customer"] is False


def test_values_are_bound_parameters_not_sql():
    assert run("find_user", 101, email="' OR 1=1 --")["result"] == {"matches_your_account": False, "other_accounts": []}
    assert run("check_deposit", 101, deposit_id="401 OR 1=1") == {"error": "deposit 401 OR 1=1 not found"}


# ---------- checker tools on the seeded scenarios ----------

def test_check_deposit_pending_upi_deposit():
    result = run("check_deposit", 101, deposit_id=401)["result"]
    assert result["deposit"]["status"] == "PENDING" and result["deposit"]["age_minutes"] >= 44
    assert result["gateway"]["gateway_status"] == "PENDING" and result["ledger_credited"] is False


def test_check_deposit_failed_but_debited_has_a_refund():
    result = run("check_deposit", 102, deposit_id=403)["result"]
    assert result["gateway"]["gateway_status"] == "CAPTURED"
    assert [r["status"] for r in result["refunds"]] == ["INITIATED"]


def test_every_customers_wallet_matches_their_ledger():
    for user_id in (101, 102, 103, 104, 105):
        assert all(b["matches_ledger"] for b in run("check_wallet_balance", user_id)["result"])


def test_check_order_compares_fees_with_the_expected_rate():
    result = run("check_order", 101, order_id=801)["result"]
    assert result["totals"] == {"fills": 2, "value": 1200.0, "fee": 2.4, "gst": 0.43, "tds": 0.0}
    assert result["fee_charged_twice"] == []


# ---------- HTTP API: the user comes from the message, never from the request ----------

@pytest.fixture
def api(monkeypatch):
    owners = {57: 101, 58: 102}  # message_id -> verified customer, as the main app would answer

    def user_for_message(message_id):
        if message_id not in owners:
            raise main_app_client.UnknownMessage(message_id)
        return owners[message_id]

    monkeypatch.setattr(main_app_client, "user_for_message", user_for_message)
    return create_app().test_client()


def test_two_emails_at_once_each_get_their_own_customer(api):
    rahul = api.post("/tools/get_deposits", json={"message_id": 57, "arguments": {}}, environ_base=LOCAL).get_json()
    priya = api.post("/tools/get_deposits", json={"message_id": 58, "arguments": {}}, environ_base=LOCAL).get_json()
    assert {d["user_id"] for d in rahul["result"]} == {101}
    assert {d["user_id"] for d in priya["result"]} == {102}


def test_api_rejects_messages_without_a_verified_customer(api):
    response = api.post("/tools/get_deposits", json={"message_id": 99, "arguments": {}}, environ_base=LOCAL)
    assert response.status_code == 403
    assert api.post("/tools/get_deposits", json={"arguments": {}}, environ_base=LOCAL).status_code == 400


def test_api_only_answers_this_machine(api):
    assert api.get("/tools", environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403


# ---------- tools.json (what the LLM is offered) and the SOPs ----------

def test_tools_json_matches_the_toolsvc_catalog(api):
    served = api.get("/tools", environ_base=LOCAL).get_json()
    assert TOOLS_JSON.read_text() == render(served), \
        "tools.json is out of date: start toolsvc and run python -m ai_analyzer.tools.export"


def split_arguments(args: str) -> list[str]:
    """Split 'a=1, b=["X", "Y"]' on top-level commas only (same as scripts/check_sops.py)."""
    parts, depth, current = [], 0, ""
    for ch in args:
        depth += (ch == "[") - (ch == "]")
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [part.strip() for part in parts + [current] if part.strip()]


def sop_texts():
    return {path.name: path.read_text() for path in sorted(SOPS_DIR.glob("*.txt")) if path.name != "data_dictionary.txt"}


def test_sops_contain_no_sql():
    for name, text in sop_texts().items():
        assert not re.search(r"\bSELECT\b", text), f"{name} still has SQL"
        assert not re.search(r"^TABLES:", text, re.M), f"{name} still has a TABLES: line"


def test_sop_tool_calls_only_use_what_the_llm_is_offered():
    offered = {t["name"]: set(t["parameters"]["properties"]) for t in json.loads(TOOLS_JSON.read_text())}
    problems = []
    for name, text in sop_texts().items():
        for tool_name, args in TOOL_CALL.findall(text):
            if tool_name not in offered:
                problems.append(f"{name}: unknown tool {tool_name}")
                continue
            for arg in split_arguments(args):
                arg_name = arg.split("=", 1)[0].strip()
                if arg_name not in offered[tool_name]:
                    problems.append(f"{name}: {tool_name} has no argument {arg_name!r}")
    assert problems == []


def test_a_coin_works_where_a_market_is_expected():
    """The live agent asked get_orders(symbol="BTC") and told the customer they had no BTC orders."""
    fake_db.reset()
    by_market = call_tool("get_orders", {"symbol": "BTC-INR"}, user_id=101)["result"]
    assert by_market and call_tool("get_orders", {"symbol": "btc"}, user_id=101)["result"] == by_market
    assert call_tool("get_trades", {"symbol": "BTC"}, user_id=101)["result"]
    assert call_tool("get_instrument", {"symbol": "eth/inr"}, user_id=101)["result"]["symbol"] == "ETH-INR"
