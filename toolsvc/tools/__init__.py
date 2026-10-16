"""The support tools, one module per area. Each registers its tools with @tool when imported, so
importing this package loads them all. registry.py holds @tool, Param and call_tool().
"""

from toolsvc.tools import (  # noqa: F401  (imported for their @tool registrations)
    account,
    banking,
    crypto,
    kyc,
    rewards,
    support,
    trading,
    wallet,
    withdrawals,
)
from toolsvc.tools.registry import INJECTED, TOOLS, Param, Tool, ToolError, call_tool, catalog, tool

__all__ = ["INJECTED", "TOOLS", "Param", "Tool", "ToolError", "call_tool", "catalog", "tool"]
