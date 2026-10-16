"""ToolSvc: read-only data tools for customer support, behind an HTTP API. Knows nothing about AI.

    uv run python -m toolsvc                        # API on :8002

    POST /tools/<name>  {"message_id": 57, "arguments": {...}}   -> {"result": ...} | {"error": ...}
    GET  /tools                                                   -> catalog of tools and their parameters

Every call names the email (message_id) it is made for. ToolSvc asks the main app which verified
customer sent that email and runs the tool for that user_id only: callers can never pass a user_id.

Layers: controllers/http_tools.py (routes) -> services/tool_service.py (who is the customer, run the
tool) -> tools/ (one module per area, @tool) -> models/fake_db.py (data); clients/ talks to the main app.

The names below are this package's public API (the agent evals run tools in-process with them).
"""

from toolsvc.models import fake_db
from toolsvc.tools import INJECTED, TOOLS, Param, Tool, ToolError, call_tool, catalog, tool

__all__ = ["INJECTED", "TOOLS", "Param", "Tool", "ToolError", "call_tool", "catalog", "fake_db", "tool"]
