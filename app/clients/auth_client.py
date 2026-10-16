"""Calls to the auth service: which customer (user_id) does this sender's email belong to?"""

from email.utils import parseaddr

import requests

from app.config import get_settings


def sender_address(from_header: str | None) -> str:
    """'Rahul Mehta <Rahul.Mehta@Example.com>' -> 'rahul.mehta@example.com' ('' if there is none)."""
    return parseaddr(from_header or "")[1].strip().lower()


def verified_user_id(from_header: str | None) -> int | None:
    """The sender's user_id if their address is registered and active, else None.

    Raises if the auth service can't be reached, so the caller can retry later instead of
    treating every sender as unknown during an outage.
    """
    email = sender_address(from_header)
    if not email:
        return None
    response = requests.post(f"{get_settings().auth_url}/verify/user", json={"email_id": email}, timeout=5)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    user = response.json()
    return user["user_id"] if user["is_active"] else None
