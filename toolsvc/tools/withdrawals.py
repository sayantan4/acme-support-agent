"""INR and crypto withdrawals."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, ToolError, tool

WITHDRAWAL_STATUSES = ("REQUESTED", "PENDING_APPROVAL", "PROCESSING", "SUCCESS", "FAILED", "CANCELLED",
                       "ON_HOLD")
WITHDRAWAL_COLUMNS = ("withdrawal_id, user_id, asset, amount, fee, status, bank_account_id, "
                      "destination_address, network, memo_tag, payout_utr, tx_hash, hold_reason, "
                      "created_at, updated_at")


@tool(
    "List a user's withdrawals (INR or crypto), newest first: amount, fee, status, payout_utr or tx_hash, "
    "hold_reason. Filter by asset, status, a bank account, or how many days back. Then call check_withdrawal.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    asset=Param("string", "INR or a crypto symbol, e.g. USDT"),
    status=Param("string", "Only this status", enum=WITHDRAWAL_STATUSES),
    statuses=Param("array", "Only these statuses, e.g. the still-open ones", items="string"),
    bank_account_id=Param("integer", "Only withdrawals to this bank account"),
    days=Param("integer", "Only withdrawals from the last N days"),
)
def get_withdrawals(user_id, asset=None, status=None, statuses=None, bank_account_id=None, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(asset, "asset = :asset", asset=asset)
             .when(status, "status = :status", status=status)
             .when(bank_account_id, "bank_account_id = :bank_account_id", bank_account_id=bank_account_id)
             .when(days, "created_at >= :since", since=ago(days=days or 0))
             .in_list("status", statuses))
    return query(f"SELECT {WITHDRAWAL_COLUMNS} FROM withdrawals{where.sql()} "
                 f"ORDER BY created_at DESC LIMIT 20", where.params)


@tool(
    "Withdrawal checker: everything about one withdrawal in one call. Returns the withdrawal (status, amount, "
    "fee, payout_utr / tx_hash, hold_reason, hours since request), the destination bank account (INR), its "
    "ledger entries (WITHDRAWAL, FEE, and REVERSAL if the payout failed and money returned) and any refund. "
    "hold_reason is internal: never quote it to the customer.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    withdrawal_id=Param("integer", "withdrawal_id from get_withdrawals", required=True),
)
def check_withdrawal(user_id, withdrawal_id):
    rows = query(f"SELECT {WITHDRAWAL_COLUMNS}, "
                 f"CAST((julianday('now') - julianday(created_at)) * 24 AS INTEGER) AS age_hours "
                 f"FROM withdrawals WHERE withdrawal_id = :withdrawal_id AND user_id = :user_id",
                 {"withdrawal_id": withdrawal_id, "user_id": user_id})
    if not rows:  # another customer's withdrawal looks exactly like one that doesn't exist
        raise ToolError(f"withdrawal {withdrawal_id} not found")
    withdrawal = rows[0]
    bank = query("SELECT bank_name, account_number_masked, ifsc, status FROM bank_accounts "
                 "WHERE bank_account_id = :id", {"id": withdrawal["bank_account_id"]})
    ledger = query("SELECT entry_type, amount, asset, created_at FROM ledger_entries "
                   "WHERE reference_type = 'WITHDRAWAL' AND reference_id = :id ORDER BY created_at",
                   {"id": withdrawal_id})
    refunds = query("SELECT refund_id, amount, status, refund_utr, processed_at FROM refunds "
                    "WHERE source_type = 'WITHDRAWAL' AND source_id = :id", {"id": withdrawal_id})
    return {
        "withdrawal": withdrawal,
        "bank_account": bank[0] if bank else None,
        "ledger_entries": ledger,
        "reversed": any(entry["entry_type"] == "REVERSAL" for entry in ledger),
        "refunds": refunds,
    }
