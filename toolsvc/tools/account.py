"""Identity and login tools: users, devices, auth events, OTP delivery."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, ToolError, tool

USER_COLUMNS = ("user_id, phone, email, full_name, status, kyc_status, referral_code, referred_by, "
                "created_at, last_login_at")
EVENT_TYPES = ("LOGIN_SUCCESS", "LOGIN_FAILED", "OTP_SENT", "OTP_VERIFIED", "OTP_FAILED", "PASSWORD_RESET",
               "TWO_FA_ENABLED", "TWO_FA_RESET", "PHONE_CHANGED", "EMAIL_CHANGED", "SESSION_REVOKED")


def last_10_digits(phone: str) -> str:
    """'+91 98123-45678' and '9812345678' both become '9812345678'."""
    return "".join(ch for ch in phone if ch.isdigit())[-10:]


@tool(
    "Check an email and/or phone the customer mentions (matches either) against Acme accounts. Use it for "
    "a 'duplicate account' or 'my other account' question. Returns whether it matches the customer's own "
    "account, and for any OTHER account only its status, kyc_status and dates: never its id, name or "
    "contact details, and never confirm another account's details to the customer.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    email=Param("string", "Email address, any case"),
    phone=Param("string", "Phone number in any format; matched on its last 10 digits"),
)
def find_user(user_id, email=None, phone=None):
    if not email and not phone:
        raise ToolError("find_user needs an email or a phone")
    matches = []
    if email:
        matches.append("lower(email) = lower(:email)")
    if phone:
        matches.append("substr(phone, -10) = :phone10")
    phone10 = last_10_digits(phone) if phone else None
    rows = query(f"SELECT user_id, status, kyc_status, created_at, last_login_at FROM users "
                 f"WHERE {' OR '.join(matches)}", {"email": email, "phone10": phone10})

    other_accounts = []
    for row in rows:
        if row["user_id"] != user_id:
            other_accounts.append({column: value for column, value in row.items() if column != "user_id"})
    return {
        "matches_your_account": any(row["user_id"] == user_id for row in rows),
        "other_accounts": other_accounts,
    }


@tool(
    "Get one user's profile: status (ACTIVE | BLOCKED | FROZEN | DORMANT | CLOSED), kyc_status, signup and "
    "last login time, referral code. Call after the user is identified and verified.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
)
def get_user(user_id):
    rows = query(f"SELECT {USER_COLUMNS} FROM users WHERE user_id = :user_id", {"user_id": user_id})
    if not rows:
        raise ToolError(f"user {user_id} not found")
    return rows[0]


@tool(
    "Check whether a transaction reference the customer gave belongs to their account: a UTR (deposit or "
    "payout), a blockchain tx hash (crypto deposit or withdrawal) or an order id. Returns where it was "
    "found and belongs_to_you. If it belongs to another account, share nothing about it (SUP-01).",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    utr=Param("string", "Bank UTR / RRN"),
    tx_hash=Param("string", "Blockchain transaction hash"),
    order_id=Param("integer", "Acme order id"),
)
def find_user_by_reference(user_id, utr=None, tx_hash=None, order_id=None):
    if utr is None and tx_hash is None and order_id is None:
        raise ToolError("find_user_by_reference needs a utr, tx_hash or order_id")
    found = []
    for sql, params in reference_lookups(utr, tx_hash, order_id):
        for row in query(sql, params):
            found.append({"matched_in": row["matched_in"], "belongs_to_you": row["user_id"] == user_id})
    return found


def reference_lookups(utr, tx_hash, order_id) -> list[tuple[str, dict]]:
    """One (sql, params) per table the given references could be in; each row is (user_id, matched_in)."""
    lookups = []
    if utr is not None:
        lookups.append(("SELECT user_id, 'deposit' AS matched_in FROM deposits WHERE utr = :utr",
                        {"utr": utr}))
        lookups.append(("SELECT user_id, 'withdrawal' AS matched_in FROM withdrawals WHERE payout_utr = :utr",
                        {"utr": utr}))
    if tx_hash is not None:
        lookups.append(("SELECT user_id, 'crypto_deposit' AS matched_in FROM crypto_deposits "
                        "WHERE tx_hash = :tx", {"tx": tx_hash}))
        lookups.append(("SELECT user_id, 'crypto_withdrawal' AS matched_in FROM withdrawals "
                        "WHERE tx_hash = :tx", {"tx": tx_hash}))
    if order_id is not None:
        lookups.append(("SELECT user_id, 'order' AS matched_in FROM orders WHERE order_id = :order_id",
                        {"order_id": order_id}))
    return lookups


@tool(
    "List a user's devices (platform, app version, trusted or not, first and last seen). Use for login "
    "problems, app issues and to spot a new device in an unauthorized-access case.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    platform=Param("string", "Only this platform", enum=("ANDROID", "IOS", "WEB")),
)
def get_user_devices(user_id, platform=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(platform, "platform = :platform", platform=platform))
    return query(f"SELECT device_id, platform, app_version, is_trusted, first_seen_at, last_seen_at "
                 f"FROM user_devices{where.sql()} ORDER BY last_seen_at DESC", where.params)


@tool(
    "List a user's login and security events (logins, failed logins with reason, OTPs, password and 2FA "
    "resets, phone/email changes), newest first. Use for login trouble, blocked accounts, cooldowns after a "
    "security change, and unauthorized access.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    event_types=Param("array", "Only these event types", items="string"),
    days=Param("integer", "How many days back to look (default 30)"),
    from_time=Param("string", "Only events at or after this time, ISO 8601 (instead of days)"),
    to_time=Param("string", "Only events at or before this time, ISO 8601"),
)
def get_auth_events(user_id, event_types=None, days=None, from_time=None, to_time=None):
    unknown = set(event_types or []) - set(EVENT_TYPES)
    if unknown:
        raise ToolError(f"unknown event_types {sorted(unknown)}; valid: {list(EVENT_TYPES)}")
    since = from_time or ago(days=days or 30)
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .add("created_at >= :since", since=since)
             .when(to_time, "created_at <= :to_time", to_time=to_time)
             .in_list("event_type", event_types))
    return query(f"SELECT event_type, channel, ip_address, device_id, failure_reason, created_at "
                 f"FROM auth_events{where.sql()} ORDER BY created_at DESC", where.params)


@tool(
    "List OTPs sent to a phone/email or user (channel, provider, delivery status, provider error), newest "
    "first. Use when the customer is not receiving OTPs at signup or login.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    destination=Param("string", "Phone (matched on last 10 digits) or email the OTP was sent to"),
    channel=Param("string", "Only this channel", enum=("SMS", "EMAIL", "WHATSAPP")),
    hours=Param("integer", "How many hours back to look (default 24)"),
)
def get_otp_logs(user_id, destination=None, channel=None, hours=None):
    # The customer's own OTPs, plus OTPs sent before an account existed (user_id NULL) to the
    # destination they gave, or to their own phone/email when they gave none.
    phone10, email = otp_destination(user_id, destination)
    where = (Where().add("created_at >= :since", since=ago(hours=hours or 24))
             .when(channel, "channel = :channel", channel=channel)
             .add("(user_id = :user_id OR (user_id IS NULL AND (substr(destination, -10) = :phone10 "
                  "OR lower(destination) = lower(:email))))", user_id=user_id, phone10=phone10, email=email))
    if destination is not None:
        where.add("(substr(destination, -10) = :phone10 OR lower(destination) = lower(:email))")
    return query(f"SELECT destination, channel, provider, status, provider_error, created_at "
                 f"FROM otp_logs{where.sql()} ORDER BY created_at DESC LIMIT 20", where.params)


def otp_destination(user_id: int, destination: str | None) -> tuple[str | None, str | None]:
    """(phone10, email) to match OTPs on: the destination given, else the customer's own phone and email."""
    if destination:
        if "@" in destination:
            return None, destination
        return last_10_digits(destination), None

    users = query("SELECT phone, email FROM users WHERE user_id = :user_id", {"user_id": user_id})
    contacts = users[0] if users else {}
    phone10 = last_10_digits(contacts["phone"]) if contacts.get("phone") else None
    return phone10, contacts.get("email")


@tool(
    "Check OTP delivery across ALL users for a recent window: how many OTPs failed and to how many numbers. "
    "Many failures for many numbers means a provider outage, not the customer's problem.",
    channel=Param("string", "Channel to check (default SMS)", enum=("SMS", "EMAIL", "WHATSAPP")),
    minutes=Param("integer", "Window in minutes (default 30)"),
)
def get_otp_delivery_health(channel=None, minutes=None):
    channel = channel or "SMS"
    window = minutes or 30
    [row] = query("SELECT count(*) AS sent, sum(status = 'FAILED') AS failed, "
                  "count(DISTINCT CASE WHEN status = 'FAILED' THEN destination END) AS failed_destinations "
                  "FROM otp_logs WHERE channel = :channel AND created_at >= :since",
                  {"channel": channel, "since": ago(minutes=window)})
    counts = {name: count or 0 for name, count in row.items()}  # sum() over no rows is NULL
    return {"channel": channel, "window_minutes": window, **counts}
