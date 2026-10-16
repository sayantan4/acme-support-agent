"""Risk holds, support tickets, notifications, tax records, account closure."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, tool

HOLD_TYPES = ("ACCOUNT_FREEZE", "WITHDRAWAL_HOLD", "DEPOSIT_HOLD", "REWARD_HOLD", "TRADE_RESTRICTION")


@tool(
    "List a user's risk holds (account freeze, withdrawal/deposit/reward hold, trade restriction). "
    "INTERNAL ONLY: never tell the customer the reason_code or source, and for source LAW_ENFORCEMENT do not "
    "even confirm that an agency is involved (fraud_risk_sop).",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    active_only=Param("boolean", "Only holds still ACTIVE (default true)"),
    hold_type=Param("string", "Only this hold type", enum=HOLD_TYPES),
)
def get_risk_holds(user_id, active_only=None, hold_type=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(hold_type, "hold_type = :hold_type", hold_type=hold_type))
    if active_only is not False:
        where.add("status = 'ACTIVE'")
    return query(f"SELECT hold_id, hold_type, reason_code, source, status, created_at, released_at "
                 f"FROM risk_holds{where.sql()} ORDER BY created_at DESC", where.params)


@tool(
    "List a user's support tickets, newest first: channel, category, status, priority, assigned team and "
    "response times. Use to find an existing ticket before opening a new one, and for SLA questions.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    category=Param("string", "Only this category, e.g. payments"),
    open_only=Param("boolean", "Only tickets not yet RESOLVED or CLOSED"),
    days=Param("integer", "Only tickets from the last N days"),
)
def get_tickets(user_id, category=None, open_only=None, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(category, "category = :category", category=category)
             .when(days, "created_at >= :since", since=ago(days=days or 0)))
    if open_only:
        where.add("status NOT IN ('RESOLVED', 'CLOSED')")
    return query(f"SELECT ticket_id, channel, category, subcategory, status, priority, assigned_team, "
                 f"created_at, first_response_at, resolved_at "
                 f"FROM tickets{where.sql()} ORDER BY created_at DESC", where.params)


@tool(
    "List notifications sent to a user (push, SMS, email), newest first, with template and delivery status. "
    "Use when the customer says they did not get an alert.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    channel=Param("string", "Only this channel", enum=("PUSH", "SMS", "EMAIL")),
    days=Param("integer", "Only the last N days (default 7)"),
)
def get_notifications(user_id, channel=None, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .add("created_at >= :since", since=ago(days=days or 7))
             .when(channel, "channel = :channel", channel=channel))
    return query(f"SELECT channel, template, status, created_at FROM notifications_log{where.sql()} "
                 f"ORDER BY created_at DESC LIMIT 20", where.params)


@tool(
    "A user's tax records per financial year: TDS deducted, Form 16A status (NOT_AVAILABLE | GENERATED) and "
    "when the P&L statement was generated.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    financial_year=Param("string", "Only this year, e.g. FY2025-26"),
)
def get_tax_records(user_id, financial_year=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(financial_year, "financial_year = :financial_year", financial_year=financial_year))
    return query(f"SELECT financial_year, tds_deducted_total, form16a_status, pnl_statement_generated_at "
                 f"FROM tax_records{where.sql()} ORDER BY financial_year DESC", where.params)


@tool(
    "A user's account closure requests, newest first: status (REQUESTED | BLOCKED_BY_BALANCE | BLOCKED_BY_HOLD | "
    "IN_PROGRESS | COMPLETED | WITHDRAWN), reason, created and completed time.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
)
def get_closure_requests(user_id):
    return query("SELECT request_id, status, reason, created_at, completed_at FROM account_closure_requests "
                 "WHERE user_id = :user_id ORDER BY created_at DESC", {"user_id": user_id})
