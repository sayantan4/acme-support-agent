"""Bank accounts, INR deposits and refunds."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, ToolError, tool

DEPOSIT_STATUSES = ("INITIATED", "PENDING", "SUCCESS", "FAILED", "ON_HOLD", "REFUND_INITIATED", "REFUNDED")
BANK_STATUSES = ("PENDING_VERIFICATION", "VERIFIED", "FAILED", "REMOVED")
DEPOSIT_COLUMNS = ("deposit_id, user_id, amount, method, status, utr, gateway, gateway_txn_id, "
                   "bank_account_id, failure_reason, created_at, updated_at")


@tool(
    "List a user's bank accounts (masked number, IFSC, bank, holder name returned by the bank, status, "
    "verification method, name_match_score 0-100, primary or not).",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    status=Param("string", "Only this status", enum=BANK_STATUSES),
    days=Param("integer", "Only accounts added in the last N days"),
)
def get_bank_accounts(user_id, status=None, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(status, "status = :status", status=status)
             .when(days, "created_at >= :since", since=ago(days=days or 0)))
    return query(f"SELECT bank_account_id, account_number_masked, ifsc, bank_name, account_holder_name, "
                 f"status, verification_method, name_match_score, is_primary, created_at "
                 f"FROM bank_accounts{where.sql()} ORDER BY created_at DESC", where.params)


@tool(
    "Check whether OTHER Acme accounts have linked the same bank account (bank_account_sop BNK-10). Returns, "
    "for each other account, only the link status and that account's status: never who it is. Never tell "
    "the customer anything about the other account.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    account_number_masked=Param("string", "Masked account number, e.g. XXXXXX4321", required=True),
    ifsc=Param("string", "IFSC code", required=True),
)
def find_bank_account_owners(user_id, account_number_masked, ifsc):
    # Compare holder names here, so the caller learns "same person or not" without seeing the name.
    return query("SELECT b.status AS link_status, u.status AS other_account_status, "
                 "upper(trim(b.account_holder_name)) = upper(trim(me.full_name)) AS same_name_as_customer "
                 "FROM bank_accounts b JOIN users u ON u.user_id = b.user_id "
                 "JOIN users me ON me.user_id = :user_id "
                 "WHERE b.account_number_masked = :masked AND b.ifsc = :ifsc AND b.user_id <> :user_id "
                 "AND b.status <> 'REMOVED'",
                 {"masked": account_number_masked, "ifsc": ifsc, "user_id": user_id})


@tool(
    "Check bank verification across ALL users: failed verifications per bank in a recent window. Many "
    "failures at one bank = that bank's penny-drop is down, not the customer's problem.",
    minutes=Param("integer", "Window in minutes (default 60)"),
)
def get_bank_verification_failures(minutes=None):
    window = minutes or 60
    failures = query("SELECT bank_name, count(*) AS failures FROM bank_accounts "
                     "WHERE status = 'FAILED' AND created_at >= :since "
                     "GROUP BY bank_name ORDER BY failures DESC", {"since": ago(minutes=window)})
    return {"window_minutes": window, "failures_by_bank": failures}


@tool(
    "List a user's INR deposits, newest first, with status, UTR, method and failure_reason. Filter by method, "
    "status, failure_reason, how many days back, or one calendar date. Then call check_deposit on the one "
    "the customer means.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    method=Param("string", "Only this method", enum=("UPI", "IMPS", "NEFT", "RTGS")),
    status=Param("string", "Only this status", enum=DEPOSIT_STATUSES),
    statuses=Param("array", "Only these statuses, e.g. INITIATED, PENDING, ON_HOLD", items="string"),
    failure_reason=Param("string", "Only this failure_reason, e.g. THIRD_PARTY_ACCOUNT"),
    days=Param("integer", "Only deposits from the last N days"),
    on_date=Param("string", "Only deposits created on this date, YYYY-MM-DD"),
    from_time=Param("string", "Created at or after, ISO 8601"),
    to_time=Param("string", "Created at or before, ISO 8601"),
)
def get_deposits(user_id, method=None, status=None, statuses=None, failure_reason=None, days=None,
                 on_date=None, from_time=None, to_time=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(from_time, "created_at >= :from_time", from_time=from_time)
             .when(to_time, "created_at <= :to_time", to_time=to_time)
             .when(method, "method = :method", method=method)
             .when(status, "status = :status", status=status)
             .in_list("status", statuses)
             .when(failure_reason, "failure_reason = :failure_reason", failure_reason=failure_reason)
             .when(days, "created_at >= :since", since=ago(days=days or 0))
             .when(on_date, "substr(created_at, 1, 10) = :on_date", on_date=on_date))
    return query(f"SELECT {DEPOSIT_COLUMNS} FROM deposits{where.sql()} ORDER BY created_at DESC LIMIT 20",
                 where.params)


@tool(
    "Deposit checker: everything about one INR deposit in one call. Returns the deposit (status, UTR, amount, "
    "failure_reason, age in minutes), its payment-gateway record (gateway_status, bank_reference, webhook and "
    "last poll time), the ledger credit(s) it produced, and any refund (status, refund_utr). "
    "ledger_credited=false on a SUCCESS deposit means status and ledger disagree: escalate.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    deposit_id=Param("integer", "deposit_id from get_deposits or find_deposits_by_reference", required=True),
)
def check_deposit(user_id, deposit_id):
    rows = query(f"SELECT {DEPOSIT_COLUMNS}, "
                 f"CAST((julianday('now') - julianday(created_at)) * 1440 AS INTEGER) AS age_minutes "
                 f"FROM deposits WHERE deposit_id = :deposit_id AND user_id = :user_id",
                 {"deposit_id": deposit_id, "user_id": user_id})
    if not rows:  # another customer's deposit looks exactly like one that doesn't exist
        raise ToolError(f"deposit {deposit_id} not found")
    params = {"deposit_id": deposit_id}
    ledger = query("SELECT entry_id, amount, entry_type, created_at FROM ledger_entries "
                   "WHERE reference_type = 'DEPOSIT' AND reference_id = :deposit_id", params)
    gateway = query("SELECT gateway, gateway_status, bank_reference, webhook_received_at, last_polled_at "
                    "FROM payment_gateway_txns WHERE deposit_id = :deposit_id", params)
    refunds = query("SELECT refund_id, amount, status, refund_utr, created_at, processed_at FROM refunds "
                    "WHERE source_type = 'DEPOSIT' AND source_id = :deposit_id", params)
    return {
        "deposit": rows[0],
        "gateway": gateway[0] if gateway else None,
        "ledger_entries": ledger,
        "ledger_credited": any(entry["amount"] > 0 for entry in ledger),
        "refunds": refunds,
    }


@tool(
    "Find the customer's deposits by a bank reference (UTR / RRN) they gave, with any gateway record for that "
    "reference. used_by_another_account=true means the UTR belongs to a different Acme account: share nothing "
    "about it and verify identity (SUP-01). No deposits and no gateway record = the payment never reached Acme.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    utr=Param("string", "UTR / RRN the customer gave", required=True),
)
def find_deposits_by_reference(user_id, utr):
    all_matches = query(f"SELECT {DEPOSIT_COLUMNS} FROM deposits WHERE utr = :utr", {"utr": utr})
    # Gateway records of the customer's own deposits, or not matched to any deposit yet.
    gateway = query("SELECT g.gateway_txn_id, g.deposit_id, g.gateway_status, g.bank_reference, "
                    "g.webhook_received_at "
                    "FROM payment_gateway_txns g LEFT JOIN deposits d ON d.deposit_id = g.deposit_id "
                    "WHERE g.bank_reference = :utr AND (d.user_id = :user_id OR g.deposit_id IS NULL)",
                    {"utr": utr, "user_id": user_id})
    return {
        "deposits": [deposit for deposit in all_matches if deposit["user_id"] == user_id],
        "gateway_records": gateway,
        "used_by_another_account": any(deposit["user_id"] != user_id for deposit in all_matches),
    }


@tool(
    "List a user's refunds (of deposits or withdrawals), newest first, with status, refund_utr, timing and, for "
    "deposit refunds, the original deposit's amount, method and status. Refunds take 5-7 working days (DD-09).",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    source_type=Param("string", "Only refunds of this kind", enum=("DEPOSIT", "WITHDRAWAL")),
)
def get_refunds(user_id, source_type=None):
    where = (Where().add("r.user_id = :user_id", user_id=user_id)
             .when(source_type, "r.source_type = :source_type", source_type=source_type))
    return query(f"SELECT r.refund_id, r.source_type, r.source_id, r.amount, r.status, r.refund_utr, "
                 f"r.created_at, r.processed_at, "
                 f"d.amount AS deposit_amount, d.method AS deposit_method, d.status AS deposit_status "
                 f"FROM refunds r "
                 f"LEFT JOIN deposits d ON r.source_type = 'DEPOSIT' AND d.deposit_id = r.source_id"
                 f"{where.sql()} ORDER BY r.created_at DESC", where.params)
