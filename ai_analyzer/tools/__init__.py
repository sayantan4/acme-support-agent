"""The LLM's side of the support tools: tools.json, the catalog the LLM is offered. The tools run in
ToolSvc (toolsvc/), called through ai_analyzer/clients/toolsvc_client.py.

    from ai_analyzer.tools import tool_definitions, run_tool

    tool_definitions()                                     # what we give the LLM (tools.json)
    run_tool("check_deposit", {"deposit_id": 401}, message_id=57)

tools.json never contains user_id: ToolSvc fills it in from the email being handled. message_id is
passed by our code from the job we're working on, never taken from the LLM's output.
"""

import json
from pathlib import Path

from ai_analyzer.clients.toolsvc_client import run_tool

TOOLS_JSON = Path(__file__).parent / "tools.json"


def tool_definitions() -> list[dict]:
    return json.loads(TOOLS_JSON.read_text())


__all__ = ["TOOLS_JSON", "run_tool", "tool_definitions"]
