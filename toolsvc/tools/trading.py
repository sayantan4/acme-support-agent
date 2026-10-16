"""Trading: orders, fills, instruments, market prices, price incidents, SIPs."""

from datetime import datetime, timedelta

from toolsvc.models.fake_db import Where, ago, iso, query
from toolsvc.tools.registry import Param, ToolError, tool

ORDER_STATUSES = ("NEW", "OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "EXPIRED")
TRADING_FEE_RATE = 0.002  # 0.2% of trade value (DD-09)
GST_RATE = 0.18           # 18% GST on the fee
ORDER_COLUMNS = ("order_id, user_id, symbol, side, order_type, quantity, price, trigger_price, "
                 "filled_quantity, avg_fill_price, status, reject_reason, source, created_at, updated_at")


def market(symbol: str | None) -> str | None:
    """"BTC", "btc", "BTC/INR" -> "BTC-INR".

    LLMs often pass the coin; every market on Acme is quoted in INR.
    """
    if symbol is None:
        return None
    symbol = symbol.strip().upper().replace("/", "-")
    return symbol if "-" in symbol else f"{symbol}-INR"


def shift(moment: str, minutes: float) -> str:
    """An ISO 8601 time moved by `minutes` (negative = earlier)."""
    return iso(datetime.fromisoformat(moment) + timedelta(minutes=minutes))


@tool(
    "List a user's orders, newest first: symbol, side, type, quantity, price, filled quantity, average fill "
    "price, status, reject_reason, source (APP | WEB | API | SIP). Filter by symbol, status, source, a time "
    "range (ISO 8601) or how many days back. Then call check_order.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    symbol=Param("string", "Only this market, e.g. BTC-INR (a coin like BTC also works)"),
    side=Param("string", "Only buys or sells", enum=("BUY", "SELL")),
    status=Param("string", "Only this status", enum=ORDER_STATUSES),
    statuses=Param("array", "Only these statuses, e.g. the open ones: NEW, OPEN, PARTIALLY_FILLED", items="string"),
    source=Param("string", "Only orders from this source", enum=("APP", "WEB", "API", "SIP")),
    from_time=Param("string", "Created at or after, ISO 8601"),
    to_time=Param("string", "Created at or before, ISO 8601"),
    days=Param("integer", "Only orders from the last N days"),
)
def get_orders(user_id, symbol=None, side=None, status=None, statuses=None, source=None, from_time=None,
               to_time=None, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(symbol, "symbol = :symbol", symbol=market(symbol))
             .when(side, "side = :side", side=side)
             .when(status, "status = :status", status=status)
             .in_list("status", statuses)
             .when(source, "source = :source", source=source)
             .when(from_time, "created_at >= :from_time", from_time=from_time)
             .when(to_time, "created_at <= :to_time", to_time=to_time)
             .when(days, "created_at >= :since", since=ago(days=days or 0)))
    return query(f"SELECT {ORDER_COLUMNS} FROM orders{where.sql()} ORDER BY created_at DESC LIMIT 20",
                 where.params)


@tool(
    "Order checker: everything about one order in one call. Returns the order, every fill (trade) with the "
    "fee and GST charged next to what they should be (0.2% fee + 18% GST, DD-09), totals across fills, "
    "trade ids charged a fee more than once (fee_charged_twice), and the SIP run that placed it, if any.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    order_id=Param("integer", "order_id from get_orders or the customer", required=True),
)
def check_order(user_id, order_id):
    orders = query(f"SELECT {ORDER_COLUMNS} FROM orders WHERE order_id = :order_id AND user_id = :user_id",
                   {"order_id": order_id, "user_id": user_id})
    if not orders:
        raise ToolError(f"order {order_id} not found")
    params = {"order_id": order_id}
    fills = query("SELECT trade_id, price, quantity, fee, gst, tds, executed_at FROM trades "
                  "WHERE order_id = :order_id ORDER BY executed_at", params)
    for fill in fills:
        add_expected_charges(fill)
    duplicate_fees = query("SELECT reference_id AS trade_id, count(*) AS fee_entries FROM ledger_entries "
                           "WHERE entry_type = 'FEE' AND reference_type = 'TRADE' AND reference_id IN "
                           "(SELECT trade_id FROM trades WHERE order_id = :order_id) "
                           "GROUP BY reference_id HAVING count(*) > 1", params)
    sip_run = query("SELECT run_id, sip_id, status, failure_reason, run_at FROM sip_runs "
                    "WHERE order_id = :order_id", params)
    return {
        "order": orders[0],
        "fills": fills,
        "totals": fill_totals(fills),
        "fee_charged_twice": duplicate_fees,
        "sip_run": sip_run[0] if sip_run else None,
    }


def add_expected_charges(fill: dict) -> None:
    """Add the fill's trade value and the fee and GST it should have been charged (DD-09)."""
    value = fill["price"] * fill["quantity"]
    fill["trade_value"] = round(value, 2)
    fill["expected_fee"] = round(value * TRADING_FEE_RATE, 2)
    fill["expected_gst"] = round(value * TRADING_FEE_RATE * GST_RATE, 2)


def fill_totals(fills: list[dict]) -> dict:
    return {
        "fills": len(fills),
        "value": round(sum(fill["trade_value"] for fill in fills), 2),
        "fee": round(sum(fill["fee"] for fill in fills), 2),
        "gst": round(sum(fill["gst"] for fill in fills), 2),
        "tds": round(sum(fill["tds"] for fill in fills), 2),
    }


@tool(
    "List a user's trades (fills), newest first: symbol, side, price, quantity, fee, gst, tds. Filter by "
    "symbol, side or a time range (ISO 8601).",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    symbol=Param("string", "Only this market, e.g. BTC-INR (a coin like BTC also works)"),
    side=Param("string", "Only buys or sells", enum=("BUY", "SELL")),
    trade_id=Param("integer", "Only this one trade"),
    from_time=Param("string", "Executed at or after, ISO 8601"),
    to_time=Param("string", "Executed at or before, ISO 8601"),
)
def get_trades(user_id, symbol=None, side=None, trade_id=None, from_time=None, to_time=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(trade_id, "trade_id = :trade_id", trade_id=trade_id)
             .when(symbol, "symbol = :symbol", symbol=market(symbol))
             .when(side, "side = :side", side=side)
             .when(from_time, "executed_at >= :from_time", from_time=from_time)
             .when(to_time, "executed_at <= :to_time", to_time=to_time))
    return query(f"SELECT trade_id, order_id, symbol, side, price, quantity, fee, gst, tds, executed_at "
                 f"FROM trades{where.sql()} ORDER BY executed_at DESC LIMIT 50", where.params)


@tool(
    "Get a market's trading rules and status: TRADING | HALTED | DELISTED | POST_ONLY, min and max order value, "
    "tick size, lot size. Use when an order was rejected or a coin cannot be bought.",
    symbol=Param("string", "Market, e.g. BTC-INR", required=True),
)
def get_instrument(symbol):
    rows = query("SELECT symbol, status, min_order_value, max_order_value, tick_size, lot_size "
                 "FROM instruments WHERE symbol = :symbol", {"symbol": market(symbol)})
    if not rows:
        raise ToolError(f"instrument {symbol} not found (not listed on Acme)")
    return rows[0]


@tool(
    "Lowest and highest traded price of a market (ALL users) in a time window. Use to check a limit or stop "
    "price was ever reached, or whether a fill price was in line with the market at that moment.",
    symbol=Param("string", "Market, e.g. BTC-INR", required=True),
    from_time=Param("string", "Window start, ISO 8601", required=True),
    to_time=Param("string", "Window end, ISO 8601 (default: now)"),
)
def get_market_price_range(symbol, from_time, to_time=None):
    where = (Where().add("symbol = :symbol", symbol=market(symbol))
             .add("executed_at >= :from_time", from_time=from_time)
             .when(to_time, "executed_at <= :to_time", to_time=to_time))
    [row] = query(f"SELECT min(price) AS low, max(price) AS high, count(*) AS trades "
                  f"FROM trades{where.sql()}", where.params)
    return {"symbol": symbol, "from_time": from_time, "to_time": to_time, **row}


@tool(
    "Price or platform incidents (wrong price feed, outage) with any compensation_policy. Give at_time to get "
    "incidents active at that moment; give only a symbol to get its latest incident.",
    symbol=Param("string", "Only incidents for this market"),
    at_time=Param("string", "Only incidents active at this time, ISO 8601"),
)
def get_price_incidents(symbol=None, at_time=None):
    where = (Where().when(symbol, "symbol = :symbol", symbol=market(symbol))
             .when(at_time, "started_at <= :at_time AND (ended_at IS NULL OR ended_at >= :at_time)",
                   at_time=at_time))
    limit = "" if at_time else " LIMIT 1"  # without a time, only the latest incident
    return query(f"SELECT incident_id, symbol, started_at, ended_at, description, compensation_policy "
                 f"FROM price_incidents{where.sql()} ORDER BY started_at DESC{limit}", where.params)


@tool(
    "Count orders rejected with SYSTEM_ERROR across ALL users around a moment. Several = a platform issue "
    "(trade_sop TRD-01), not the customer's mistake.",
    around_time=Param("string", "The customer's order time, ISO 8601", required=True),
    minutes=Param("integer", "Look this many minutes before and after (default 15)"),
)
def get_order_error_count(around_time, minutes=None):
    window = minutes or 15
    [row] = query("SELECT count(*) AS system_errors FROM orders WHERE reject_reason = 'SYSTEM_ERROR' "
                  "AND created_at BETWEEN :start AND :end",
                  {"start": shift(around_time, -window), "end": shift(around_time, window)})
    return {"around_time": around_time, "window_minutes": window, **row}


@tool(
    "List a user's SIP plans (symbol, amount, frequency, status ACTIVE | PAUSED | CANCELLED, next run), each "
    "with its last 10 runs (status, failure_reason, order_id).",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    status=Param("string", "Only plans with this status", enum=("ACTIVE", "PAUSED", "CANCELLED")),
)
def get_sip_plans(user_id, status=None):
    where = Where().add("user_id = :user_id", user_id=user_id).when(status, "status = :status", status=status)
    plans = query(f"SELECT sip_id, symbol, amount, frequency, status, next_run_at, created_at "
                  f"FROM sip_plans{where.sql()} ORDER BY created_at DESC", where.params)
    for plan in plans:
        plan["recent_runs"] = query("SELECT run_id, order_id, status, failure_reason, run_at FROM sip_runs "
                                    "WHERE sip_id = :sip_id ORDER BY run_at DESC LIMIT 10",
                                    {"sip_id": plan["sip_id"]})
    return plans
