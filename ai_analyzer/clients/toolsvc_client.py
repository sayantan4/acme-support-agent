"""ToolSvc client: run a tool the LLM asked for (POST /tools/<name>), and read the catalog (GET /tools).
The tools themselves live in toolsvc/."""

import requests

from ai_analyzer.config import get_settings


def run_tool(name: str, arguments: dict, message_id: int) -> dict:
    """{"result": ...} or {"error": ...}, ready to hand back to the LLM.

    `message_id` is the email this job is handling. It comes from our code (the stream event),
    not from the LLM, and ToolSvc maps it to the verified customer.
    """
    response = requests.post(f"{get_settings().toolsvc_url}/tools/{name}",
                             json={"message_id": message_id, "arguments": arguments}, timeout=15)
    if response.status_code in (400, 403):
        return response.json()  # e.g. "message 57 has no verified customer": the LLM should stop, not retry
    response.raise_for_status()
    return response.json()


def catalog() -> list[dict]:
    """Every tool ToolSvc offers (GET /tools), without user_id: what tools.json is generated from."""
    response = requests.get(f"{get_settings().toolsvc_url}/tools", timeout=10)
    response.raise_for_status()
    return response.json()
