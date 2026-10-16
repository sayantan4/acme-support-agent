# Support SOPs (knowledge base for the support agent)

Standard operating procedures for **Acme Invest**, a fictional Indian crypto investing app used
for this demo. The `*.txt` files here are what gets loaded into the vector database. This README
is not loaded.

All tables, statuses, limits, fees and SLAs are sample values defined in `data_dictionary.txt`.
The SOPs get data through the support tools in ToolSvc (`toolsvc/`, a fake database for now); the
LLM sees them as `ai_analyzer/tools/tools.json`.
Replace them with real policy before any real use.

## Files

| File | Covers |
|---|---|
| `data_dictionary.txt` | Tables, statuses, platform policies, SLAs, teams. Every SOP refers to it |
| `support_guidelines_sop.txt` | Identity checks, tone, what never to reveal, reply rules |
| `onboarding_sop.txt` | Signup, OTP, login, account blocked, duplicate accounts |
| `kyc_sop.txt` | PAN, Aadhaar, selfie/liveness, rejections, re-KYC |
| `bank_account_sop.txt` | Adding/changing bank accounts, penny drop, name mismatch |
| `payment_sop.txt` | INR deposits: pending, failed-but-debited, refunds, third-party deposits |
| `withdrawal_sop.txt` | INR withdrawals: pending, failed, on hold, cooldowns |
| `crypto_transfers_sop.txt` | Crypto deposits/withdrawals, wrong network, missing memo, whitelisting |
| `trade_sop.txt` | Orders, rejections, partial fills, slippage, SIPs, portfolio mismatches |
| `fees_charges_sop.txt` | Trading fee, GST, withdrawal and network fees, fee disputes |
| `tax_statements_sop.txt` | TDS, P&L and tax statements, Form 16A |
| `rewards_sop.txt` | Referrals, cashback, coupons, expiry, reward holds |
| `account_security_sop.txt` | Unauthorized access, 2FA reset, phone/email change, phishing |
| `fraud_risk_sop.txt` | Account freezes, risk holds, law-enforcement holds, scams |
| `account_closure_sop.txt` | Closing an account, dormant accounts, data deletion |
| `app_technical_sop.txt` | App crashes, login errors, prices not loading, notifications |
| `complaints_escalation_sop.txt` | Grievances, SLA breaches, legal/regulatory threats, social media |

## Format (every SOP follows this, so each issue becomes one retrieval chunk)

```
SOP_ID: PAY
TITLE: ...
CATEGORY: payments
VERSION / LAST_REVIEWED / OWNER / NOTE (fictional sample)

======================================================================
## PAY-00: Scope and quick triage
======================================================================
...

======================================================================
## PAY-01: <one customer problem>
======================================================================
CUSTOMER MAY SAY:
  - "..."            <- real phrasings; these help retrieval match incoming emails
INFO NEEDED FROM CUSTOMER:
  - ...
TOOLS: get_deposits, check_deposit   <- the tools this section calls (ai_analyzer/tools)
DEBUGGING STEPS:
  1. Identify the user (see SUP-02) ...
     Call get_deposits(days=7).
  2. ...
     - If <condition>: <what it means> -> <next action>
     - Else if ...
RESOLUTION:
  ...
CUSTOMER REPLY TEMPLATE:
  ...
ESCALATE WHEN:
  - ... -> <team>, priority P2, include: ...
DO NOT:
  - ...
```

Rules for writing SOPs:
- One `## <ID>-NN:` section per customer problem. IDs are unique across all files.
- Data is fetched with tools from `ai_analyzer/tools/tools.json`, never SQL. Never pass `user_id`:
  ToolSvc fills in the verified customer of the email. Write calls as
  `Call tool_name(param=<value>, param2="LITERAL").` with the tool's real parameter names. Need data no
  tool returns? Add a tool (see `toolsvc/README.md`), don't write a query. A tool only sees the
  customer whose email is being handled, so a step can't look into a second account.
- Every branch ends in an action: resolve, ask the customer, wait (with how long), or escalate.
- Reply templates are plain language: no table names, status codes, vendor or reviewer names.

## Example issue

```
======================================================================
## PAY-01: UPI deposit is pending
======================================================================
CUSTOMER MAY SAY:
  - "I added money through UPI but it's not showing in my wallet"
  - "Deposit pending since morning, money deducted from bank"
INFO NEEDED FROM CUSTOMER:
  - Registered phone or email (if not the sender address), amount, date/time, UTR if available
TOOLS: find_user, get_deposits, check_deposit
DEBUGGING STEPS:
  1. Find the user:
     Call find_user(email=<sender_email>, phone=<phone>).
  2. Find recent deposits:
     Call get_deposits(method="UPI", days=7).
  3. Check the matching deposit: Call check_deposit(deposit_id=<deposit_id>). Then by deposit.status:
     - SUCCESS: confirm ledger_credited.
       -> true: tell the customer the balance is updated. false: escalate (P1).
     - PENDING and age_minutes < 30: normal; ask the customer to wait 30 minutes.
     - PENDING and older than 30 minutes: check the gateway (step 4).
     - FAILED: go to PAY-02 (failed but money debited).
  4. Use the gateway record from check_deposit (gateway.gateway_status, webhook_received_at):
     - CAPTURED but deposit still PENDING: webhook missed -> escalate to Payments Ops (P2).
     - PENDING at gateway: bank has not confirmed; auto-reconciliation runs every 15 minutes
       for 72 hours -> tell the customer it will update automatically.
RESOLUTION: ...
CUSTOMER REPLY TEMPLATE: ...
ESCALATE WHEN: ...
DO NOT: ...
```

## Checking the SOPs

After editing, run `python scripts/check_sops.py`. It checks that section IDs are unique,
cross-references (`fraud_risk_sop RSK-01`) point to real sections, no SQL is left, every tool call
names a real tool and real arguments, each `TOOLS:` line lists exactly the tools its section calls,
reply templates contain no internal codes, and files are plain ASCII.
(Words like "orders" or "withdrawals" in reply templates are reported but are plain English.)

## Backlog: data the SOPs would like but the dictionary doesn't have yet

Found while writing the SOPs. The SOPs work around these today.

- **Market data:** a current price / ticker table (slippage checks, valuing holdings in INR).
- **Order history:** an order events log (cancel requested at), `orders.cancelled_by`, time-in-force / expiry.
- **Money movement:** `withdrawals.failure_reason`; `payment_gateway_txns.amount`;
  `bank_accounts.failure_reason`, `verified_at` and a full account-number hash;
  a penny-drop attempts table; an unmatched bank credits table; network fee history.
- **Identity and security:** `users.two_fa_enabled`, `blocked_until`, `dormant_since`, residency;
  old/new values and city on `auth_events`; `provider_error` values for `otp_logs`;
  a profile change requests table; masked PAN on KYC tables.
- **Risk and support:** a document requests table; ticket messages / notes; nominee and deceased fields;
  a link from withdrawal holds to `risk_holds`.
- **Platform:** an incidents / status table (beyond `price_incidents`); an API keys table;
  notification template names; device model and OS on `user_devices`.
- **Tax and rewards:** per-quarter TDS / Form 16A records; GST invoices; a link from rewards to referrals.
- **Policy values:** many SOPs mark values "(sample value)" (OTP validity, re-KYC every 2 years,
  bank account limits, withdrawal daily cap, risk review SLA, ...). Promote the ones you keep into DD-09.
