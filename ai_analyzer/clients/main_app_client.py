"""Calls to the main app's internal API. The main app owns the database; we never touch it."""

import requests

from ai_analyzer.config import get_settings


def get_messages(mailbox: str, thread_id: str, after: int) -> list[dict]:
    """Inbound messages of the thread with id > after, oldest first."""
    response = requests.get(
        f"{get_settings().main_app_url}/internal/threads/{thread_id}/messages",
        params={"mailbox": mailbox, "after": after},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_sent_replies(mailbox: str, thread_id: str, limit: int = 3) -> list[dict]:
    """Our last `limit` replies the customer received in this thread, oldest first: [{id, body, kind}]."""
    response = requests.get(
        f"{get_settings().main_app_url}/internal/threads/{thread_id}/replies",
        params={"mailbox": mailbox, "limit": limit},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_message(mailbox: str, msg_id: int) -> dict:
    """One message by our processed_messages id."""
    response = requests.get(
        f"{get_settings().main_app_url}/internal/messages/{msg_id}",
        params={"mailbox": mailbox},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def store_reply(reply: dict) -> dict:
    """The main app stores the reply and returns {"id": ..., "created": true/false}."""
    response = requests.post(f"{get_settings().main_app_url}/internal/replies", json=reply, timeout=10)
    response.raise_for_status()
    return response.json()
