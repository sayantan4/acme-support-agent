"""Run a tool for the customer who sent an email. The caller names the email, never the customer.

ToolSvc asks the main app which verified customer sent the email (message_id) and runs the tool for
that user_id only, so several emails being handled at once can't mix up their customers.
"""

import logging

from toolsvc.clients import main_app_client
from toolsvc.tools import call_tool, catalog

log = logging.getLogger(__name__)


class NoVerifiedCustomer(Exception):
    """The email doesn't exist, or its sender is not a verified customer."""


def list_tools() -> list[dict]:
    """Every tool and its parameters, without user_id (callers can't set it)."""
    return catalog()


def run_for_message(name: str, message_id: int, arguments: dict) -> dict:
    """{"result": ...} or {"error": ...}. Raises NoVerifiedCustomer if the email has no verified sender."""
    try:
        user_id = main_app_client.user_for_message(message_id)
    except main_app_client.UnknownMessage as unknown:
        raise NoVerifiedCustomer(message_id) from unknown

    result = call_tool(name, arguments, user_id=user_id)
    # Audit trail: which email, which customer, which tool. Arguments are logged, results are not.
    log.info("tool=%s message_id=%d user_id=%d arguments=%s outcome=%s",
             name, message_id, user_id, arguments, "error" if "error" in result else "ok")
    return result
