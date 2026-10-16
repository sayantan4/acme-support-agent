"""Write tools.json, the tool definitions we give the LLM, from ToolSvc's catalog.

    uv run python -m toolsvc &                    # ToolSvc must be running
    uv run python -m ai_analyzer.tools.export

ToolSvc's catalog (GET /tools) already leaves out user_id, so the LLM is never offered it. This
script only turns the catalog into OpenAI's function-tool format. A test fails if tools.json is out
of date, so re-run it after adding or changing a tool in ToolSvc.
"""

import json

from ai_analyzer.clients import toolsvc_client
from ai_analyzer.tools import TOOLS_JSON


def to_llm_definitions(catalog: list[dict]) -> list[dict]:
    """ToolSvc catalog -> OpenAI Responses API function tools."""
    return [
        {
            "type": "function",
            "name": entry["name"],
            "description": entry["description"],
            "parameters": {
                "type": "object",
                "properties": entry["parameters"],
                "required": entry["required"],
                "additionalProperties": False,
            },
        }
        for entry in catalog
    ]


def render(catalog: list[dict]) -> str:
    return json.dumps(to_llm_definitions(catalog), indent=2) + "\n"


def main() -> None:
    catalog = toolsvc_client.catalog()
    TOOLS_JSON.write_text(render(catalog))
    print(f"wrote {len(catalog)} tools to {TOOLS_JSON}")


if __name__ == "__main__":
    main()
