"""Main app client: ToolSvc asks which verified customer an email belongs to. Nothing else."""

import requests

from toolsvc.config import get_settings


class UnknownMessage(Exception):
    """The message doesn't exist, or its sender is not a verified customer."""


def user_for_message(message_id: int) -> int:
    """The verified customer's user_id for this message; raises UnknownMessage if there is none."""
    url = f"{get_settings().main_app_url}/internal/messages/{message_id}/user"
    response = requests.get(url, timeout=5)
    if response.status_code == 404:
        raise UnknownMessage(message_id)
    response.raise_for_status()
    return response.json()["user_id"]
