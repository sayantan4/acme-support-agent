# ToolSvc

Read-only data tools for customer support, behind an HTTP API. ToolSvc knows nothing about AI: the
ai_analyzer turns its catalog into `ai_analyzer/tools/tools.json` for the LLM and calls it over HTTP.

```
uv run python -m toolsvc            # :8002 (TOOLSVC_PORT), asks the main app at TOOLSVC_MAIN_APP_URL
```

| Endpoint | |
|---|---|
| `GET /tools` | catalog: name, description, parameters, required (never `user_id`) |
| `POST /tools/<name>` `{"message_id": 57, "arguments": {...}}` | `{"result": ...}` or `{"error": ...}`; 403 if the message has no verified customer |

## Whose data a call sees

Every call names the email it is made for (`message_id`). For each call ToolSvc asks the main app
`GET /internal/messages/<id>/user` and runs the tool with that `user_id`. So:

- **Callers can't choose the user.** `user_id` is not in the catalog, and passing it is rejected.
- **Record ids are checked.** `check_deposit`, `check_withdrawal`, `check_order`, `get_kyc_vendor_logs`
  answer "not found" for another customer's record.
- **Cross-account lookups only answer for the customer.** `find_user`, `find_user_by_reference`,
  `find_deposits_by_reference`, `find_bank_account_owners`, `find_deposit_address_owner` return
  `belongs_to_you` / `matches_your_account` / `used_by_another_account` / `same_name_as_customer`,
  never another customer's id, name or contact details.
- **Several emails at once can't mix:** nothing is remembered between calls.
- **Audit log:** every call logs tool, message_id, user_id, arguments and outcome.

## Layout

| File | |
|---|---|
| `controllers/http_tools.py` | the HTTP API above (thin: parse, call the service, respond) |
| `services/tool_service.py` | asks the main app who the email's customer is, then runs the tool for that user only; audit log |
| `clients/main_app_client.py` | `user_for_message(message_id)`, the only thing ToolSvc asks the main app |
| `tools/registry.py` | `@tool` (function + parameter descriptions), `catalog()`, `call_tool(name, args, user_id)`; `INJECTED = {"user_id"}` |
| `tools/account.py`, `kyc.py`, `banking.py`, `withdrawals.py`, `wallet.py`, `crypto.py`, `trading.py`, `rewards.py`, `support.py` | the 44 tools |
| `models/fake_db.py` | in-memory SQLite with the `ai_analyzer/sops/data_dictionary.txt` schema and seeded customers 101-106 |

Tool rules: read-only and parameterized (`fake_db.Where`), errors come back as data, "checker" tools
(`check_deposit`, `check_withdrawal`, `check_order`, `check_wallet_balance`) return everything about
one record plus a verdict, and descriptions say which fields must never reach the customer.

## Adding a tool

1. Write it in the right module with `@tool(...)`. If it reads one customer's data, give it a
   `user_id` parameter (filled in by ToolSvc) and filter on it.
2. Add an example to `EXAMPLE_CALLS` in `tests/test_toolsvc.py`.
3. With ToolSvc running, `uv run python -m ai_analyzer.tools.export` (a test fails if tools.json is stale).
4. Use it in a SOP and its `TOOLS:` line; `python scripts/check_sops.py` checks both.

## Demo customers (`fake_db.seed_rows`; `python -m auth seed` registers the same ids)

| user_id | Scenario |
|---|---|
| 101 Rahul (your Gmail in auth) | UPI deposit pending 45 min (401), INR withdrawal processing (601) and one failed + reversed (604), BTC buy in 2 fills (801), open sell (804); BTC-INR order issues: limit buy rejected PRICE_OUT_OF_BAND (805), market buy rejected SYSTEM_ERROR with 2 other users' failures nearby (806), limit buy partly filled 0.0008/0.002 with Rs 732 locked (807), market sell filled at Rs 5,90,000 during a BTC-INR price incident (808) |
| 102 Priya | KYC rejected twice (PAN name mismatch), Aadhaar vendor timeout, failed-but-debited deposit (403) with refund initiated |
| 103 Amit | FROZEN by a law-enforcement hold, INR withdrawal ON_HOLD (602), login from a new device after a password reset |
| 104 Neha | USDT deposit confirming (702), XRP deposit missing memo (703), daily ETH SIP failing on balance, pending cashback, new address in cooldown |
| 105 Vikram | DORMANT, closure blocked by a Rs 3 balance |
| 106 Sneha | new signup, SMS OTPs failing (provider issue), referral by 101 rejected (same device), tried to link 101's bank account |
