"""Rewards: a user's rewards, campaigns, referrals, coupons."""

from toolsvc.models.fake_db import Where, query
from toolsvc.tools.registry import Param, ToolError, tool

REWARD_TYPES = ("REFERRAL", "CASHBACK", "SIGNUP_BONUS", "COUPON", "STREAK")
REWARD_STATUSES = ("PENDING", "ELIGIBLE", "CREDITED", "EXPIRED", "REVOKED", "ON_HOLD")


@tool(
    "List a user's rewards: type, campaign, asset, amount, status (PENDING | ELIGIBLE | CREDITED | EXPIRED | "
    "REVOKED | ON_HOLD), eligibility_reason, credit and expiry time.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    reward_type=Param("string", "Only this type", enum=REWARD_TYPES),
    status=Param("string", "Only this status", enum=REWARD_STATUSES),
    campaign_id=Param("integer", "Only rewards from this campaign"),
    reward_id=Param("integer", "Only this one reward"),
)
def get_rewards(user_id, reward_type=None, status=None, campaign_id=None, reward_id=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(reward_id, "reward_id = :reward_id", reward_id=reward_id)
             .when(reward_type, "reward_type = :reward_type", reward_type=reward_type)
             .when(status, "status = :status", status=status)
             .when(campaign_id, "campaign_id = :campaign_id", campaign_id=campaign_id))
    return query(f"SELECT reward_id, campaign_id, reward_type, asset, amount, status, eligibility_reason, "
                 f"credited_at, expires_at FROM rewards_ledger{where.sql()} ORDER BY reward_id DESC",
                 where.params)


@tool(
    "Look up reward campaigns and their rules: by id, by type and status, by part of the name the customer "
    "used, or the ones running at a given time (e.g. the user's signup time).",
    campaign_id=Param("integer", "One campaign"),
    reward_type=Param("string", "Only this type", enum=REWARD_TYPES),
    status=Param("string", "Only this status", enum=("ACTIVE", "ENDED", "PAUSED")),
    name_contains=Param("string", "Part of the offer name, any case, e.g. 'diwali'"),
    active_at=Param("string", "Only campaigns running at this time, ISO 8601"),
)
def get_campaigns(campaign_id=None, reward_type=None, status=None, name_contains=None, active_at=None):
    where = (Where().when(campaign_id, "campaign_id = :campaign_id", campaign_id=campaign_id)
             .when(reward_type, "reward_type = :reward_type", reward_type=reward_type)
             .when(status, "status = :status", status=status)
             .when(name_contains, "lower(name) LIKE lower(:name)", name=f"%{name_contains}%")
             .when(active_at, "start_at <= :active_at AND end_at >= :active_at", active_at=active_at))
    return query(f"SELECT campaign_id, name, reward_type, rules_summary, start_at, end_at, status "
                 f"FROM campaigns{where.sql()} ORDER BY start_at DESC", where.params)


@tool(
    "A user's referrals in both directions: as_referee (the code they used at signup, its status and "
    "rejection_reason) and as_referrer (people they invited). rejection_reason is e.g. SAME_DEVICE, "
    "CODE_APPLIED_LATE, QUALIFYING_TRADE_NOT_MET.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
)
def get_referrals(user_id):
    # The other person's user_id is left out: the customer only needs the status of each referral.
    columns = "referral_id, referral_code_used, status, rejection_reason, created_at, qualified_at"
    as_referee = query(f"SELECT {columns} FROM referrals WHERE referee_user_id = :id", {"id": user_id})
    as_referrer = query(f"SELECT {columns} FROM referrals WHERE referrer_user_id = :id "
                        f"ORDER BY created_at DESC", {"id": user_id})
    return {"as_referee": as_referee, "as_referrer": as_referrer}


@tool(
    "Look up a coupon code (any case): campaign, redemptions used out of the maximum, status ACTIVE | "
    "EXPIRED | EXHAUSTED, valid_until, and whether it is a personal code valid for this customer.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    coupon_code=Param("string", "The code the customer entered", required=True),
)
def get_coupon(user_id, coupon_code):
    rows = query("SELECT coupon_code, campaign_id, user_id, max_redemptions, redemptions, status, "
                 "valid_until FROM coupons WHERE upper(coupon_code) = upper(:code)", {"code": coupon_code})
    if not rows:
        raise ToolError(f"coupon {coupon_code!r} does not exist")
    coupon = rows[0]
    owner = coupon.pop("user_id")  # a personal code's owner; never shown to the caller
    coupon["personal_code"] = owner is not None
    coupon["valid_for_this_customer"] = owner is None or owner == user_id
    return coupon
