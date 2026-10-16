"""A fake Acme Invest database for the support tools: in-memory SQLite, rebuilt on every start.

Tables and columns follow ai_analyzer/sops/data_dictionary.txt (DD-02 to DD-08). All data is per
customer (user_id 101-106; `python -m auth seed` registers the same ids), each set up to exercise
SOP branches (a UPI deposit pending for 45 minutes, a failed-but-debited deposit with a refund, a
rejected KYC, a frozen account, a crypto deposit missing its memo, ...). Times are relative to
"now", so "last 7 days" filters keep working.

Only the tool modules call query(); the LLM never sees SQL. The seed data is at the bottom
(seed_rows), grouped by area; the "users" rows say what each customer is there to exercise.
"""

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

# table -> columns, in data-dictionary order
SCHEMA: dict[str, list[str]] = {
    "users": ["user_id", "phone", "email", "full_name", "status", "kyc_status", "referral_code",
              "referred_by", "created_at", "last_login_at"],
    "user_devices": ["user_id", "device_id", "platform", "app_version", "is_trusted", "first_seen_at",
                     "last_seen_at"],
    "auth_events": ["event_id", "user_id", "event_type", "channel", "ip_address", "device_id",
                    "failure_reason", "created_at"],
    "otp_logs": ["otp_id", "user_id", "destination", "channel", "provider", "status", "provider_error",
                 "created_at"],
    "kyc_applications": ["kyc_id", "user_id", "status", "submitted_at", "reviewed_at",
                         "rejection_reason_code", "review_queue", "attempt_number"],
    "kyc_documents": ["doc_id", "kyc_id", "doc_type", "vendor_ref", "verification_status", "failure_reason",
                      "name_on_doc", "dob_on_doc", "uploaded_at"],
    "kyc_vendor_logs": ["log_id", "kyc_id", "check_type", "response_code", "response_message", "latency_ms",
                        "created_at"],
    "bank_accounts": ["bank_account_id", "user_id", "account_number_masked", "ifsc", "bank_name",
                      "account_holder_name", "status", "verification_method", "name_match_score",
                      "is_primary", "created_at"],
    "deposits": ["deposit_id", "user_id", "amount", "method", "status", "utr", "gateway", "gateway_txn_id",
                 "bank_account_id", "failure_reason", "created_at", "updated_at"],
    "payment_gateway_txns": ["gateway_txn_id", "gateway", "deposit_id", "gateway_status", "bank_reference",
                             "webhook_received_at", "last_polled_at"],
    "refunds": ["refund_id", "source_type", "source_id", "user_id", "amount", "status", "refund_utr",
                "created_at", "processed_at"],
    "withdrawals": ["withdrawal_id", "user_id", "asset", "amount", "fee", "status", "bank_account_id",
                    "destination_address", "network", "memo_tag", "payout_utr", "tx_hash", "hold_reason",
                    "created_at", "updated_at"],
    "wallet_balances": ["user_id", "asset", "available", "locked", "updated_at"],
    "ledger_entries": ["entry_id", "user_id", "asset", "amount", "entry_type", "reference_type",
                       "reference_id", "created_at"],
    "crypto_deposits": ["crypto_deposit_id", "user_id", "asset", "network", "amount", "tx_hash",
                        "from_address", "to_address", "memo_tag", "confirmations", "required_confirmations",
                        "status", "created_at", "credited_at"],
    "deposit_addresses": ["user_id", "asset", "network", "address", "memo_tag", "created_at"],
    "supported_networks": ["asset", "network", "deposits_enabled", "withdrawals_enabled",
                           "required_confirmations", "min_withdrawal", "withdrawal_fee", "maintenance_note"],
    "address_whitelist": ["user_id", "asset", "network", "address", "label", "status", "added_at",
                          "active_from"],
    "instruments": ["symbol", "status", "min_order_value", "tick_size", "lot_size", "max_order_value"],
    "orders": ["order_id", "user_id", "symbol", "side", "order_type", "quantity", "price", "trigger_price",
               "filled_quantity", "avg_fill_price", "status", "reject_reason", "source", "created_at",
               "updated_at"],
    "trades": ["trade_id", "order_id", "user_id", "symbol", "side", "price", "quantity", "fee", "gst", "tds",
               "executed_at"],
    "sip_plans": ["sip_id", "user_id", "symbol", "amount", "frequency", "status", "next_run_at",
                  "created_at"],
    "sip_runs": ["run_id", "sip_id", "order_id", "status", "failure_reason", "run_at"],
    "price_incidents": ["incident_id", "symbol", "started_at", "ended_at", "description",
                        "compensation_policy"],
    "campaigns": ["campaign_id", "name", "reward_type", "rules_summary", "start_at", "end_at", "status"],
    "referrals": ["referral_id", "referrer_user_id", "referee_user_id", "referral_code_used", "status",
                  "rejection_reason", "created_at", "qualified_at"],
    "rewards_ledger": ["reward_id", "user_id", "campaign_id", "reward_type", "asset", "amount", "status",
                       "eligibility_reason", "credited_at", "expires_at"],
    "coupons": ["coupon_code", "campaign_id", "user_id", "max_redemptions", "redemptions", "status",
                "valid_until"],
    "risk_holds": ["hold_id", "user_id", "hold_type", "reason_code", "source", "status", "created_at",
                   "released_at"],
    "tax_records": ["user_id", "financial_year", "tds_deducted_total", "form16a_status",
                    "pnl_statement_generated_at"],
    "account_closure_requests": ["request_id", "user_id", "status", "reason", "created_at", "completed_at"],
    "tickets": ["ticket_id", "user_id", "channel", "category", "subcategory", "status", "priority",
                "assigned_team", "created_at", "first_response_at", "resolved_at"],
    "notifications_log": ["user_id", "channel", "template", "status", "created_at"],
}

BOOLEAN_COLUMNS = {"is_trusted", "is_primary", "deposits_enabled", "withdrawals_enabled"}  # stored as 0/1


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    """Every timestamp is stored in this one format, so comparing strings compares times."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def ago(days: float = 0, hours: float = 0, minutes: float = 0) -> str:
    """That long before now, as a stored timestamp. Negative values give a time in the future."""
    return iso(now() - timedelta(days=days, hours=hours, minutes=minutes))


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------

class Where:
    """Builds a WHERE clause from optional filters. Values always go in as bound parameters.

        where = Where().add("user_id = :user_id", user_id=101).when(status, "status = :status", status=status)
        query(f"SELECT * FROM deposits{where.sql()}", where.params)
    """

    def __init__(self):
        self.clauses: list[str] = []
        self.params: dict = {}

    def add(self, clause: str, **params) -> "Where":
        self.clauses.append(clause)
        self.params.update(params)
        return self

    def when(self, value, clause: str, **params) -> "Where":
        """Add the clause only if the filter was given (value is not None)."""
        return self.add(clause, **params) if value is not None else self

    def in_list(self, column: str, values: list | None) -> "Where":
        """column IN (...) with one bound parameter per value; skipped when values is empty or None."""
        if not values:
            return self
        prefix = column.replace(".", "_")  # "r.status" -> "r_status_0", "r_status_1", ...
        params = {f"{prefix}_{index}": value for index, value in enumerate(values)}
        placeholders = ", ".join(f":{name}" for name in params)
        return self.add(f"{column} IN ({placeholders})", **params)

    def sql(self) -> str:
        return f" WHERE {' AND '.join(self.clauses)}" if self.clauses else ""


_lock = threading.Lock()  # sqlite3 connections are not safe to share between threads without one
_connection: sqlite3.Connection | None = None


def query(sql: str, params: dict | None = None) -> list[dict]:
    """Run one read-only, parameterized query (":name" placeholders) and return plain dicts."""
    global _connection
    with _lock:
        if _connection is None:
            _connection = build_database()
        rows = [dict(row) for row in _connection.execute(sql, params or {}).fetchall()]
    for row in rows:
        for column in BOOLEAN_COLUMNS & row.keys():
            if row[column] is not None:
                row[column] = bool(row[column])
    return rows


def reset() -> None:
    """Rebuild from the seed data (tests use this so "now" is fresh)."""
    global _connection
    with _lock:
        _connection = build_database()


def build_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    for table, columns in SCHEMA.items():
        connection.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
    for table, rows in seed_rows().items():
        placeholders = ", ".join("?" for _ in SCHEMA[table])
        connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
    connection.commit()
    return connection


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def seed_rows() -> dict[str, list[tuple]]:
    """Sample customers. user_id 101-106; each comment says which SOP branches the rows exercise.

    Rows keep the order they are inserted in (some queries have no ORDER BY), so find a customer's
    rows by their user_id column rather than by position.
    """
    return {
        # ---- Account and login --------------------------------------------------------------------
        "users": [
            # 101 Rahul: active, verified; pending UPI deposit, working withdrawal, BTC trades
            (101, "+919812345678", "rahul.mehta@example.com", "Rahul Mehta", "ACTIVE", "VERIFIED", "RAHUL100",
             None, ago(days=400), ago(hours=2)),
            # 102 Priya: KYC rejected twice; failed-but-debited deposit being refunded
            (102, "+919811111111", "priya.shah@example.com", "Priya Shah", "ACTIVE", "REJECTED", "PRIYA200",
             None, ago(days=20), ago(hours=5)),
            # 103 Amit: frozen by a law-enforcement hold; withdrawal on hold; suspicious login
            (103, "+919822222222", "amit.k@example.com", "Amit Kumar", "FROZEN", "VERIFIED", "AMIT300",
             None, ago(days=700), ago(days=1)),
            # 104 Neha: crypto user; confirming USDT deposit, XRP deposit without memo, SIP failure, cashback
            (104, "+919833333333", "neha.r@example.com", "Neha Rao", "ACTIVE", "VERIFIED", "NEHA400",
             None, ago(days=300), ago(minutes=30)),
            # 105 Vikram: dormant; closure blocked by a small balance
            (105, "+919844444444", "vikram.s@example.com", "Vikram Singh", "DORMANT", "VERIFIED", "VIK500",
             None, ago(days=1200), ago(days=800)),
            # 106 Sneha: new signup, OTPs failing, referred by Rahul but rejected (same device)
            (106, "+919855555555", "sneha.p@example.com", "Sneha Patil", "ACTIVE", "NOT_STARTED", "SNEHA600",
             101, ago(days=2), ago(days=2)),
        ],
        "user_devices": [
            (101, "dev-r1", "ANDROID", "5.2.0", 1, ago(days=400), ago(hours=2)),
            (103, "dev-a1", "IOS", "5.2.0", 1, ago(days=700), ago(days=3)),
            (103, "dev-x9", "WEB", None, 0, ago(days=1), ago(days=1)),           # new untrusted device
            (104, "dev-n1", "ANDROID", "5.1.3", 1, ago(days=300), ago(minutes=30)),
            (106, "dev-r1", "ANDROID", "5.2.0", 0, ago(days=2), ago(days=2)),    # same device as Rahul
        ],
        "auth_events": [
            (1, 101, "LOGIN_SUCCESS", "APP", "49.36.10.1", "dev-r1", None, ago(hours=2)),
            (2, 103, "LOGIN_FAILED", "WEB", "185.220.1.7", "dev-x9", "WRONG_PASSWORD",
             ago(days=1, minutes=10)),
            (3, 103, "PASSWORD_RESET", "WEB", "185.220.1.7", "dev-x9", None, ago(days=1, minutes=5)),
            (4, 103, "LOGIN_SUCCESS", "WEB", "185.220.1.7", "dev-x9", None, ago(days=1)),
            (5, 104, "TWO_FA_ENABLED", "APP", "49.36.20.4", "dev-n1", None, ago(days=100)),
            (6, 106, "OTP_FAILED", "APP", "49.36.30.9", "dev-r1", "OTP_EXPIRED", ago(days=2)),
        ],
        "otp_logs": [
            (1, 106, "+919855555555", "SMS", "msg91", "FAILED", "DLT_TEMPLATE_REJECTED", ago(minutes=20)),
            (2, 106, "+919855555555", "SMS", "msg91", "FAILED", "DLT_TEMPLATE_REJECTED", ago(minutes=15)),
            (3, None, "+919866666666", "SMS", "msg91", "FAILED", "DLT_TEMPLATE_REJECTED", ago(minutes=10)),
            (4, 101, "+919812345678", "SMS", "msg91", "VERIFIED", None, ago(hours=2)),
        ],

        # ---- KYC (kyc_id 201-206: 102 has two rejected attempts) ------------------------------------
        "kyc_applications": [
            (201, 102, "REJECTED", ago(days=19), ago(days=18), "DOC_UNREADABLE", "AUTO", 1),
            (202, 102, "REJECTED", ago(days=3), ago(days=2), "PAN_NAME_MISMATCH", "MANUAL", 2),
            (203, 101, "VERIFIED", ago(days=399), ago(days=399), None, "AUTO", 1),
            (204, 104, "VERIFIED", ago(days=299), ago(days=299), None, "AUTO", 1),
            (205, 103, "VERIFIED", ago(days=699), ago(days=699), None, "AUTO", 1),
            (206, 105, "VERIFIED", ago(days=1199), ago(days=1199), None, "MANUAL", 1),
        ],
        "kyc_documents": [
            (1, 201, "PAN", "v-1", "FAILED", "DOC_UNREADABLE", None, None, ago(days=19)),
            (2, 202, "PAN", "v-2", "PASSED", None, "PRIYA SHAH", "1996-04-11", ago(days=3)),
            (3, 202, "AADHAAR", "v-3", "FAILED", "PAN_NAME_MISMATCH", "PRIYANKA SHAH", "1996-04-11",
             ago(days=3)),
            (4, 202, "SELFIE", "v-4", "PASSED", None, None, None, ago(days=3)),
            (5, 203, "PAN", "v-5", "PASSED", None, "RAHUL MEHTA", "1990-01-05", ago(days=399)),
            (6, 204, "PAN", "v-6", "PASSED", None, "NEHA RAO", "1993-08-21", ago(days=299)),
            (7, 205, "PAN", "v-7", "PASSED", None, "AMIT KUMAR", "1988-02-14", ago(days=699)),
            (8, 206, "PAN", "v-8", "PASSED", None, "VIKRAM SINGH", "1985-11-30", ago(days=1199)),
        ],
        "kyc_vendor_logs": [
            (1, 202, "PAN_VERIFY", "200", "OK", 420, ago(days=3)),
            (2, 202, "AADHAAR_OTP", "TIMEOUT", "vendor timeout", 30000, ago(days=3)),
            (3, 202, "AADHAAR_OTP", "200", "OK", 900, ago(days=3)),
            (4, 202, "FACE_MATCH", "200", "match 0.91", 650, ago(days=3)),
            (5, 203, "AADHAAR_OTP", "503", "service unavailable", 120, ago(minutes=20)),
            (6, 204, "AADHAAR_OTP", "503", "service unavailable", 110, ago(minutes=10)),
        ],

        # ---- INR money: bank accounts 30x, deposits 40x, refunds 50x, withdrawals 60x ---------------
        "bank_accounts": [
            (301, 101, "XXXXXX4321", "HDFC0001234", "HDFC Bank", "RAHUL MEHTA", "VERIFIED", "PENNY_DROP",
             98, 1, ago(days=398)),
            (302, 102, "XXXXXX5555", "ICIC0004321", "ICICI Bank", "PRIYA SHAH", "VERIFIED", "PENNY_DROP",
             95, 1, ago(days=18)),
            (303, 103, "XXXXXX7777", "SBIN0000123", "SBI", "AMIT KUMAR", "VERIFIED", "PENNY_DROP", 99, 1,
             ago(days=690)),
            (304, 104, "XXXXXX8888", "UTIB0000456", "Axis Bank", "NEHA RAO", "VERIFIED", "REVERSE_PENNY_DROP",
             97, 1, ago(days=295)),
            (305, 106, "XXXXXX4321", "HDFC0001234", "HDFC Bank", "RAHUL MEHTA", "FAILED", "PENNY_DROP", 40, 0,
             ago(days=1)),                                      # Sneha tried Rahul's account: name mismatch
            (306, 105, "XXXXXX9999", "PUNB0012300", "Punjab National Bank", "VIKRAM SINGH", "VERIFIED",
             "PENNY_DROP", 96, 1, ago(days=1190)),
        ],
        "deposits": [
            (401, 101, 5000.0, "UPI", "PENDING", "512345678901", "razorpay", "gw-401", 301, None,
             ago(minutes=45), ago(minutes=45)),                 # PAY-01: pending > 30 min
            (402, 101, 20000.0, "IMPS", "SUCCESS", "612345678902", "razorpay", "gw-402", 301, None,
             ago(days=10), ago(days=10)),
            (403, 102, 3000.0, "UPI", "FAILED", "712345678903", "razorpay", "gw-403", 302, "TIMEOUT",
             ago(days=4), ago(days=4)),                         # PAY-02: failed but debited, refund running
            (404, 104, 10000.0, "NEFT", "SUCCESS", "812345678904", "razorpay", "gw-404", 304, None,
             ago(days=30), ago(days=30)),
            (405, 103, 50010.0, "RTGS", "SUCCESS", "912345678905", "razorpay", "gw-405", 303, None,
             ago(days=600), ago(days=600)),
        ],
        "payment_gateway_txns": [
            ("gw-401", "razorpay", 401, "PENDING", "512345678901", None, ago(minutes=5)),
            ("gw-402", "razorpay", 402, "CAPTURED", "612345678902", ago(days=10), ago(days=10)),
            ("gw-403", "razorpay", 403, "CAPTURED", "712345678903", ago(days=4), ago(days=4)),
            ("gw-404", "razorpay", 404, "CAPTURED", "812345678904", ago(days=30), ago(days=30)),
            ("gw-405", "razorpay", 405, "CAPTURED", "912345678905", ago(days=600), ago(days=600)),
        ],
        "refunds": [
            (501, "DEPOSIT", 403, 102, 3000.0, "INITIATED", None, ago(days=4), None),
        ],
        "withdrawals": [
            (601, 101, "INR", 2000.0, 10.0, "PROCESSING", 301, None, None, None, None, None, None,
             ago(hours=3), ago(hours=2)),
            (602, 103, "INR", 50000.0, 10.0, "ON_HOLD", 303, None, None, None, None, None, "RISK_REVIEW",
             ago(days=1), ago(days=1)),
            (603, 104, "USDT", 50.0, 1.0, "SUCCESS", None, "TXyz1234abcd", "TRC20", None, None, "0xusdtsend1",
             None, ago(days=5), ago(days=5)),
            (604, 101, "INR", 1500.0, 10.0, "FAILED", 301, None, None, None, None, None, None,
             ago(days=7), ago(days=7)),
        ],

        # ---- Wallet and ledger ----------------------------------------------------------------------
        "wallet_balances": [
            (101, "INR", 15857.36, 732.0, ago(hours=2)),        # 732 locked by the open part of order 807
            (101, "BTC", 0.0013, 0.001, ago(hours=5)),          # 0.001 locked by the open sell order 804
            (102, "INR", 0.0, 0.0, ago(days=4)),
            (103, "INR", 0.0, 50010.0, ago(days=1)),
            (104, "INR", 8997.64, 0.0, ago(days=6)),
            (104, "ETH", 0.004, 0.0, ago(days=6)),
            (104, "USDT", 69.0, 0.0, ago(days=5)),             # 120 deposited, 50 withdrawn + 1 network fee
            (105, "INR", 3.0, 0.0, ago(days=800)),
        ],
        # Not in entry_id order: rows are grouped by what produced them.
        "ledger_entries": [
            # 101: deposit 402; withdrawal 601; withdrawal 604 failed and was reversed
            (1, 101, "INR", 20000.0, "DEPOSIT", "DEPOSIT", 402, ago(days=10)),
            (2, 101, "INR", -2000.0, "WITHDRAWAL", "WITHDRAWAL", 601, ago(hours=2)),
            (3, 101, "INR", -10.0, "FEE", "WITHDRAWAL", 601, ago(hours=2)),
            (4, 101, "INR", -1500.0, "WITHDRAWAL", "WITHDRAWAL", 604, ago(days=7)),
            (5, 101, "INR", -10.0, "FEE", "WITHDRAWAL", 604, ago(days=7)),
            (6, 101, "INR", 1510.0, "REVERSAL", "WITHDRAWAL", 604, ago(days=7)),
            # DD-10: one set of rows per fill, reference_id = trade_id; FEE = fee + GST
            # 101: order 801's two fills (trades 901, 902)
            (7, 101, "INR", -600.0, "TRADE_BUY", "TRADE", 901, ago(days=6)),
            (8, 101, "BTC", 0.001, "TRADE_BUY", "TRADE", 901, ago(days=6)),
            (9, 101, "INR", -1.42, "FEE", "TRADE", 901, ago(days=6)),
            (15, 101, "INR", -600.0, "TRADE_BUY", "TRADE", 902, ago(days=6)),
            (16, 101, "BTC", 0.001, "TRADE_BUY", "TRADE", 902, ago(days=6)),
            (17, 101, "INR", -1.41, "FEE", "TRADE", 902, ago(days=6)),
            # 104: deposit 404; SIP order 802's fill (trade 903); USDT deposit 701 and withdrawal 603
            (10, 104, "INR", 10000.0, "DEPOSIT", "DEPOSIT", 404, ago(days=30)),
            (11, 104, "INR", -1000.0, "TRADE_BUY", "TRADE", 903, ago(days=6)),
            (18, 104, "ETH", 0.004, "TRADE_BUY", "TRADE", 903, ago(days=6)),
            (19, 104, "INR", -2.36, "FEE", "TRADE", 903, ago(days=6)),
            (12, 104, "USDT", 120.0, "DEPOSIT", "CRYPTO_DEPOSIT", 701, ago(days=8)),
            (20, 104, "USDT", -50.0, "WITHDRAWAL", "WITHDRAWAL", 603, ago(days=5)),
            (21, 104, "USDT", -1.0, "FEE", "WITHDRAWAL", 603, ago(days=5)),
            # 101: order 807's fill (trade 904) and order 808's sell (trade 905, with TDS)
            (22, 101, "INR", -488.0, "TRADE_BUY", "TRADE", 904, ago(hours=19)),
            (23, 101, "BTC", 0.0008, "TRADE_BUY", "TRADE", 904, ago(hours=19)),
            (24, 101, "INR", -1.16, "FEE", "TRADE", 904, ago(hours=19)),
            (25, 101, "BTC", -0.0005, "TRADE_SELL", "TRADE", 905, ago(hours=5)),
            (26, 101, "INR", 295.0, "TRADE_SELL", "TRADE", 905, ago(hours=5)),
            (27, 101, "INR", -0.70, "FEE", "TRADE", 905, ago(hours=5)),
            (28, 101, "INR", -2.95, "TDS", "TRADE", 905, ago(hours=5)),
            # 103: deposit 405; 105: referral reward 3
            (13, 103, "INR", 50010.0, "DEPOSIT", "DEPOSIT", 405, ago(days=600)),
            (14, 105, "INR", 3.0, "REWARD", "REWARD", 3, ago(days=900)),
        ],

        # ---- Crypto (all 104 Neha's, plus one BTC deposit address of 101's) -------------------------
        "crypto_deposits": [
            (701, 104, "USDT", "TRC20", 120.0, "0xusdtin1", "TFrom111", "TNeha111", None, 20, 20, "CREDITED",
             ago(days=8), ago(days=8)),
            (702, 104, "USDT", "TRC20", 75.0, "0xusdtin2", "TFrom222", "TNeha111", None, 6, 20, "CONFIRMING",
             ago(minutes=12), None),                             # CRY-01: still confirming
            (703, 104, "XRP", "XRP", 300.0, "0xxrpin3", "rFrom333", "rAcmeShared", None, 1, 1, "MISSING_MEMO",
             ago(days=2), None),                                 # CRY-03: memo/tag missing
        ],
        "deposit_addresses": [
            (104, "USDT", "TRC20", "TNeha111", None, ago(days=290)),
            (104, "XRP", "XRP", "rAcmeShared", "104001", ago(days=200)),
            (101, "BTC", "BTC", "bc1qrahul111", None, ago(days=390)),
        ],
        "supported_networks": [
            ("BTC", "BTC", 1, 1, 2, 0.0005, 0.0002, None),
            ("USDT", "TRC20", 1, 1, 20, 10.0, 1.0, None),
            ("USDT", "ERC20", 1, 0, 12, 20.0, 5.0, "ERC20 withdrawals paused for node upgrade"),
            ("ETH", "ERC20", 1, 1, 12, 0.01, 0.002, None),
            ("XRP", "XRP", 1, 1, 1, 20.0, 0.25, "memo/tag required for deposits"),
        ],
        "address_whitelist": [
            (104, "USDT", "TRC20", "TXyz1234abcd", "my ledger", "ACTIVE", ago(days=40), ago(days=39)),
            (104, "BTC", "BTC", "bc1qnewaddr999", "new cold wallet", "PENDING_COOLDOWN", ago(hours=3),
             ago(hours=-21)),
        ],

        # ---- Trading: orders 80x, trades 90x, SIP 1001 (104's) --------------------------------------
        "instruments": [
            ("BTC-INR", "TRADING", 100.0, 1.0, 0.000001, 5000000.0),
            ("ETH-INR", "TRADING", 100.0, 0.1, 0.00001, 5000000.0),
            ("XYZ-INR", "HALTED", 100.0, 0.01, 1.0, 1000000.0),
        ],
        "orders": [
            (801, 101, "BTC-INR", "BUY", "LIMIT", 0.002, 600000.0, None, 0.002, 600000.0, "FILLED", None,
             "APP", ago(days=6), ago(days=6)),
            (802, 104, "ETH-INR", "BUY", "MARKET", 0.004, None, None, 0.004, 250000.0, "FILLED", None, "SIP",
             ago(days=6), ago(days=6)),
            (803, 104, "ETH-INR", "BUY", "MARKET", 0.004, None, None, 0.0, None, "REJECTED",
             "INSUFFICIENT_BALANCE", "SIP", ago(days=1), ago(days=1)),
            (804, 101, "BTC-INR", "SELL", "LIMIT", 0.001, 700000.0, None, 0.0, None, "OPEN", None, "APP",
             ago(days=2), ago(days=2)),
            # 101's BTC-INR order issues (trade_sop):
            # TRD-01: limit buy far above the market -> rejected, nothing debited
            (805, 101, "BTC-INR", "BUY", "LIMIT", 0.001, 650000.0, None, 0.0, None, "REJECTED",
             "PRICE_OUT_OF_BAND", "APP", ago(days=1), ago(days=1)),
            # TRD-01 / TRD-05: market buy rejected by a platform error (others hit it too, see 809/810)
            (806, 101, "BTC-INR", "BUY", "MARKET", 0.002, None, None, 0.0, None, "REJECTED", "SYSTEM_ERROR",
             "APP", ago(hours=3), ago(hours=3)),
            # TRD-03: limit buy only partly filled; the open 0.0012 BTC keeps Rs 732 locked
            (807, 101, "BTC-INR", "BUY", "LIMIT", 0.002, 610000.0, None, 0.0008, 610000.0, "PARTIALLY_FILLED",
             None, "APP", ago(hours=20), ago(hours=19)),
            # TRD-04: market sell filled at Rs 5,90,000 during the BTC-INR price incident
            (808, 101, "BTC-INR", "SELL", "MARKET", 0.0005, None, None, 0.0005, 590000.0, "FILLED", None,
             "APP", ago(hours=5), ago(hours=5)),
            # other customers' orders failing with SYSTEM_ERROR around 806's time (get_order_error_count)
            (809, 104, "BTC-INR", "BUY", "MARKET", 0.001, None, None, 0.0, None, "REJECTED", "SYSTEM_ERROR",
             "APP", ago(hours=3, minutes=4), ago(hours=3, minutes=4)),
            (810, 104, "ETH-INR", "BUY", "MARKET", 0.01, None, None, 0.0, None, "REJECTED", "SYSTEM_ERROR",
             "APP", ago(hours=2, minutes=55), ago(hours=2, minutes=55)),
        ],
        "trades": [
            (901, 801, 101, "BTC-INR", "BUY", 600000.0, 0.001, 1.20, 0.22, 0.0, ago(days=6)),
            (902, 801, 101, "BTC-INR", "BUY", 600000.0, 0.001, 1.20, 0.21, 0.0, ago(days=6)),
            (903, 802, 104, "ETH-INR", "BUY", 250000.0, 0.004, 2.00, 0.36, 0.0, ago(days=6)),
            # 807's one fill: 0.0008 x 6,10,000 = 488.00; fee 0.2% = 0.98; GST 18% of fee = 0.18
            (904, 807, 101, "BTC-INR", "BUY", 610000.0, 0.0008, 0.98, 0.18, 0.0, ago(hours=19)),
            # 808's fill: 0.0005 x 5,90,000 = 295.00; fee 0.59; GST 0.11; TDS 1% of sale = 2.95
            (905, 808, 101, "BTC-INR", "SELL", 590000.0, 0.0005, 0.59, 0.11, 2.95, ago(hours=5)),
        ],
        "sip_plans": [
            (1001, 104, "ETH-INR", 1000.0, "DAILY", "ACTIVE", ago(hours=-20), ago(days=60)),
        ],
        "sip_runs": [
            (1, 1001, 802, "SUCCESS", None, ago(days=6)),
            (2, 1001, 803, "FAILED", "INSUFFICIENT_BALANCE", ago(days=1)),
        ],
        "price_incidents": [
            (1, "ETH-INR", ago(days=6, minutes=30), ago(days=5, hours=23), "ETH-INR price feed lag of ~2%",
             "Refund the price difference for market orders placed in the window"),
            (2, "BTC-INR", ago(hours=5, minutes=10), ago(hours=4, minutes=50),
             "BTC-INR order book thin after a liquidity provider outage; market orders filled up to 1.7% off",
             "Trading Ops reviews market orders in the window case by case"),
        ],

        # ---- Rewards --------------------------------------------------------------------------------
        "campaigns": [
            (1, "Refer and earn", "REFERRAL",
             "Rs 100 in BTC after referee KYC + first trade >= Rs 500 in 30 days",
             ago(days=365), ago(days=-365), "ACTIVE"),
            (2, "Diwali cashback", "CASHBACK", "1% cashback on first ETH trade, max Rs 200", ago(days=40),
             ago(days=-10), "ACTIVE"),
            (3, "Welcome bonus", "SIGNUP_BONUS", "Rs 50 in BTC on KYC within 7 days of signup", ago(days=500),
             ago(days=-100), "ACTIVE"),
        ],
        "referrals": [
            (1, 101, 106, "RAHUL100", "REJECTED", "SAME_DEVICE", ago(days=2), None),
        ],
        "rewards_ledger": [
            (1, 104, 2, "CASHBACK", "INR", 10.0, "PENDING", "awaiting 72h credit window", None,
             ago(days=-90)),
            (2, 101, 3, "SIGNUP_BONUS", "BTC", 0.00001, "CREDITED", "KYC done", ago(days=395), ago(days=305)),
            (3, 105, 1, "REFERRAL", "INR", 3.0, "CREDITED", "referee completed first trade", ago(days=900),
             None),
        ],
        "coupons": [
            ("ACME500", 2, None, 1000, 1000, "EXHAUSTED", ago(days=-5)),
            ("NEHA10", 2, 104, 1, 0, "ACTIVE", ago(days=-5)),
        ],

        # ---- Risk, tax, closure, support ------------------------------------------------------------
        "risk_holds": [
            (1, 103, "ACCOUNT_FREEZE", "LE_REQUEST_CYBER_CELL", "LAW_ENFORCEMENT", "ACTIVE", ago(days=1),
             None),
            (2, 103, "WITHDRAWAL_HOLD", "VELOCITY_CHECK", "RISK_ENGINE", "ACTIVE", ago(days=1), None),
            (3, 101, "DEPOSIT_HOLD", "THIRD_PARTY_SUSPECT", "RISK_ENGINE", "RELEASED", ago(days=200),
             ago(days=199)),
        ],
        "tax_records": [
            (101, "FY2025-26", 0.0, "NOT_AVAILABLE", None),
            (104, "FY2025-26", 12.5, "GENERATED", ago(days=20)),
            (105, "FY2024-25", 0.0, "GENERATED", ago(days=300)),
        ],
        "account_closure_requests": [
            (1, 105, "BLOCKED_BY_BALANCE", "not using the app", ago(days=3), None),
        ],
        "tickets": [
            (1, 103, "EMAIL", "risk", "account_frozen", "OPEN", "P2", "Risk & Compliance", ago(days=1),
             ago(hours=20), None),
            (2, 102, "CHAT", "payments", "failed_debited", "PENDING_INTERNAL", "P2", "Payments Ops",
             ago(days=4), ago(days=4), None),
        ],
        "notifications_log": [
            (101, "PUSH", "deposit_pending", "SENT", ago(minutes=44)),
            (104, "SMS", "sip_failed", "FAILED", ago(days=1)),
        ],
    }
