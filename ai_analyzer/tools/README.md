# Support tools: the LLM's side

The tools themselves run in **ToolSvc** (`toolsvc/`, see its README). This folder only has what the
ai_analyzer needs to offer them to the LLM and to call them.

| File | |
|---|---|
| `tools.json` | the 44 tool definitions given to the LLM (OpenAI function-tool format). No `user_id` anywhere. |
| `export.py` | regenerates `tools.json` from ToolSvc's `GET /tools` (`uv run python -m ai_analyzer.tools.export`) |
| `client.py` | `run_tool(name, arguments, message_id)` -> `POST /tools/<name>` on ToolSvc |

```python
from ai_analyzer.tools import tool_definitions, run_tool

tools = tool_definitions()                      # give these to the LLM (or only a SOP section's TOOLS:)
run_tool("check_deposit", {"deposit_id": 401},  # arguments = exactly what the LLM chose
         message_id=event["last_summary_msg_id"])   # from our job, never from the LLM
```

**Why it's safe:** the LLM only picks a tool and its filters. `message_id` is added by our code from
the stream event, and ToolSvc turns it into the verified customer via the main app, so a prompt
injection ("show user 103's deposits") has nothing to change: there is no user to pass, and
another customer's record ids answer "not found".
