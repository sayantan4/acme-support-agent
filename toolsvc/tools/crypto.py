"""Crypto transfers: networks, deposits, deposit addresses, the withdrawal address whitelist."""

from toolsvc.models.fake_db import Where, ago, query
from toolsvc.tools.registry import Param, ToolError, tool

CRYPTO_DEPOSIT_STATUSES = ("DETECTED", "CONFIRMING", "CREDITED", "FAILED", "UNSUPPORTED_NETWORK",
                           "MISSING_MEMO", "MANUAL_REVIEW")


@tool(
    "Get the networks Acme supports for an asset: deposits_enabled, withdrawals_enabled, required_confirmations, "
    "min_withdrawal, withdrawal_fee and any maintenance_note. An asset/network missing here is not supported.",
    asset=Param("string", "Crypto symbol, e.g. USDT", required=True),
    network=Param("string", "Only this network, e.g. TRC20"),
)
def get_supported_networks(asset, network=None):
    where = Where().add("asset = :asset", asset=asset).when(network, "network = :network", network=network)
    return query(f"SELECT asset, network, deposits_enabled, withdrawals_enabled, required_confirmations, "
                 f"min_withdrawal, withdrawal_fee, maintenance_note FROM supported_networks{where.sql()}",
                 where.params)


@tool(
    "List the customer's crypto deposits, or find one of theirs by tx hash (a hash on another account is not "
    "returned: use find_user_by_reference to tell 'not found' from 'another account'). Returns asset, network, amount, to_address, memo_tag, confirmations vs "
    "required_confirmations, status, and whether the ledger was credited.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    tx_hash=Param("string", "Blockchain transaction hash the customer gave"),
    status=Param("string", "Only this status", enum=CRYPTO_DEPOSIT_STATUSES),
    statuses=Param("array", "Only these statuses, e.g. DETECTED, CONFIRMING, MANUAL_REVIEW", items="string"),
)
def get_crypto_deposits(user_id, tx_hash=None, status=None, statuses=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(tx_hash, "tx_hash = :tx_hash", tx_hash=tx_hash)
             .when(status, "status = :status", status=status)
             .in_list("status", statuses))
    deposits = query(f"SELECT crypto_deposit_id, user_id, asset, network, amount, tx_hash, from_address, "
                     f"to_address, memo_tag, confirmations, required_confirmations, status, created_at, "
                     f"credited_at FROM crypto_deposits{where.sql()} ORDER BY created_at DESC LIMIT 20",
                     where.params)
    for deposit in deposits:
        ledger_rows = query("SELECT 1 FROM ledger_entries "
                            "WHERE reference_type = 'CRYPTO_DEPOSIT' AND reference_id = :id",
                            {"id": deposit["crypto_deposit_id"]})
        deposit["ledger_credited"] = bool(ledger_rows)
    return deposits


@tool(
    "List a user's own deposit addresses (asset, network, address, memo_tag). Use to check the customer sent "
    "to the right address, network and memo.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    asset=Param("string", "Only this asset"),
    network=Param("string", "Only this network"),
)
def get_deposit_addresses(user_id, asset=None, network=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(asset, "asset = :asset", asset=asset)
             .when(network, "network = :network", network=network))
    return query(f"SELECT asset, network, address, memo_tag, created_at FROM deposit_addresses{where.sql()}",
                 where.params)


@tool(
    "Check a deposit address (and memo/tag for shared-address coins like XRP): is it an Acme address, and is "
    "it the customer's own? Use for missing-memo recovery or when a customer sent to another Acme address. "
    "Never says whose address it is.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    address=Param("string", "Deposit address", required=True),
    memo_tag=Param("string", "Memo / destination tag, if the coin uses one"),
)
def find_deposit_address_owner(user_id, address, memo_tag=None):
    where = (Where().add("address = :address", address=address)
             .when(memo_tag, "memo_tag = :memo_tag", memo_tag=memo_tag))
    rows = query(f"SELECT user_id, asset, network FROM deposit_addresses{where.sql()}", where.params)
    return {
        "is_acme_address": bool(rows),
        "belongs_to_you": any(row["user_id"] == user_id for row in rows),
        "asset_networks": sorted({f"{row['asset']}/{row['network']}" for row in rows}),
    }


@tool(
    "List a user's whitelisted withdrawal addresses: status PENDING_COOLDOWN (new address, 24-hour cooldown "
    "until active_from) | ACTIVE | REMOVED.",
    user_id=Param("integer", "The verified customer (filled in by the system)", required=True),
    days=Param("integer", "Only addresses added in the last N days"),
)
def get_address_whitelist(user_id, days=None):
    where = (Where().add("user_id = :user_id", user_id=user_id)
             .when(days, "added_at >= :since", since=ago(days=days or 0)))
    return query(f"SELECT asset, network, address, label, status, added_at, active_from "
                 f"FROM address_whitelist{where.sql()} ORDER BY added_at DESC", where.params)
