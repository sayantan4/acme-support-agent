"""Wallet balances, the ledger, and fee totals."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, tool

ENTRY_TYPES = ("DEPOSIT", "WITHDRAWAL", "TRADE_BUY", "TRADE_SELL", "FEE", "TDS", "REWARD", "REFUND",
               "ADJUSTMENT", "REVERSAL")
REFERENCE_TYPES = ("DEPOSIT", "CRYPTO_DEPOSIT", "WITHDRAWAL", "TRADE", "REWARD", "REFUND", "ADJUSTMENT")


@tool(
    "Wallet checker: a user's balances per asset (available, locked = reserved by open orders or pending "
    "withdrawals) next to the sum of their ledger entries. matches_ledger=false means available + locked is "
    "not what the ledger says (DD-10): do not guess, escalate to Payments Ops (INR) or Crypto Ops, P1.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    asset=Param("string", "Only this asset, e.g. INR or BTC"),
)
def check_wallet_balance(user_id, asset=None):
    where = Where().add("user_id = :user_id", user_id=user_id).when(asset, "asset = :asset", asset=asset)
    balances = query(f"SELECT asset, available, locked, updated_at FROM wallet_balances{where.sql()} "
                     f"ORDER BY asset", where.params)
    for balance in balances:
        total = ledger_total(user_id, balance["asset"])
        balance["ledger_total"] = round(total, 8)
        balance["matches_ledger"] = abs(balance["available"] + balance["locked"] - total) < 1e-6
    return balances


def ledger_total(user_id: int, asset: str) -> float:
    """The sum of every ledger entry of one asset: what the balance should be."""
    [row] = query("SELECT coalesce(sum(amount), 0) AS total FROM ledger_entries "
                  "WHERE user_id = :user_id AND asset = :asset", {"user_id": user_id, "asset": asset})
    return row["total"]


@tool(
    "List a user's ledger entries (every balance change: +credit / -debit), newest first. Filter by asset, "
    "entry types, a reference (reference_type + reference_id, e.g. TRADE 901), all fills of an order "
    "(order_id), a time range or how many days back. Use to explain a balance or an unexpected deduction.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    asset=Param("string", "Only this asset"),
    entry_type=Param("string", "Only this entry type", enum=ENTRY_TYPES),
    entry_types=Param("array", "Only these entry types, e.g. REWARD, DEPOSIT", items="string"),
    reference_type=Param("string", "Only entries for this kind of record", enum=REFERENCE_TYPES),
    reference_id=Param("integer", "Only entries for this record id (use with reference_type)"),
    order_id=Param("integer", "Only the TRADE entries of this order's fills (an order with no fills has none)"),
    from_time=Param("string", "Created at or after, ISO 8601"),
    to_time=Param("string", "Created at or before, ISO 8601"),
    days=Param("integer", "Only entries from the last N days"),
)
def get_ledger_entries(user_id, asset=None, entry_type=None, entry_types=None, reference_type=None,
                       reference_id=None, order_id=None, from_time=None, to_time=None, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(asset, "asset = :asset", asset=asset)
             .when(entry_type, "entry_type = :entry_type", entry_type=entry_type)
             .in_list("entry_type", entry_types)
             .when(reference_type, "reference_type = :reference_type", reference_type=reference_type)
             .when(reference_id, "reference_id = :reference_id", reference_id=reference_id)
             .when(order_id, "reference_type = 'TRADE' AND reference_id IN "
                             "(SELECT trade_id FROM trades WHERE order_id = :order_id)", order_id=order_id)
             .when(from_time, "created_at >= :from_time", from_time=from_time)
             .when(to_time, "created_at <= :to_time", to_time=to_time)
             .when(days, "created_at >= :since", since=ago(days=days or 0)))
    return query(f"SELECT entry_id, asset, amount, entry_type, reference_type, reference_id, created_at "
                 f"FROM ledger_entries{where.sql()} ORDER BY created_at DESC LIMIT 50", where.params)


@tool(
    "Totals a user paid between two times: trading fee, GST and TDS from all trades (no row limit), and INR "
    "withdrawal fees (fees_charges_sop FEE-11, tax_statements_sop TDS questions). Times are ISO 8601, "
    "e.g. 2026-04-01T00:00:00+00:00.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    from_time=Param("string", "Start, ISO 8601", required=True),
    to_time=Param("string", "End, ISO 8601", required=True),
)
def get_fee_summary(user_id, from_time, to_time):
    params = {"user_id": user_id, "from_time": from_time, "to_time": to_time}
    [trades] = query("SELECT coalesce(sum(fee), 0) AS trading_fee, coalesce(sum(gst), 0) AS gst, "
                     "coalesce(sum(tds), 0) AS tds, coalesce(sum(price * quantity), 0) AS trade_value, "
                     "count(*) AS fills "
                     "FROM trades WHERE user_id = :user_id AND executed_at BETWEEN :from_time AND :to_time",
                     params)
    [withdrawals] = query("SELECT coalesce(sum(fee), 0) AS withdrawal_fees, count(*) AS withdrawals "
                          "FROM withdrawals WHERE user_id = :user_id AND asset = 'INR' "
                          "AND status IN ('PROCESSING', 'SUCCESS') "
                          "AND created_at BETWEEN :from_time AND :to_time", params)
    return {**trades, **withdrawals}
