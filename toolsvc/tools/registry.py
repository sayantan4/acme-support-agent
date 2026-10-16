"""Tool registry: every tool is a plain Python function plus a description of its parameters.

    @tool("List a user's deposits.", user_id=Param("integer", "...", required=True), days=Param(...))
    def get_deposits(user_id, days=None): ...

user_id is special (INJECTED): callers never pass it. call_tool() fills it in with the verified
customer of the email being handled, and catalog() leaves it out, so whoever builds a request
cannot even name it. Every other value comes from the caller.

call_tool() never raises: problems come back as {"error": ...}, so the caller can show or act on them.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass

INJECTED = {"user_id"}  # filled in by call_tool() from the verified customer, never by the caller


@dataclass(frozen=True)
class Param:
    type: str                          # JSON schema type: string | integer | number | boolean | array
    description: str
    required: bool = False
    enum: tuple[str, ...] | None = None
    items: str | None = None           # arrays only: the type of each item

    def schema(self) -> dict:
        schema = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.type == "array":
            schema["items"] = {"type": self.items or "string"}
        return schema


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: dict[str, Param]
    function: Callable

    @property
    def caller_params(self) -> dict[str, Param]:
        """The parameters a caller may set (everything except INJECTED)."""
        return {name: param for name, param in self.params.items() if name not in INJECTED}

    def spec(self) -> dict:
        """What GET /tools returns for this tool: its name, description and caller parameters."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {name: param.schema() for name, param in self.caller_params.items()},
            "required": [name for name, param in self.caller_params.items() if param.required],
        }


class ToolError(Exception):
    """A problem the caller should hear about, e.g. "deposit 999 not found"."""


TOOLS: dict[str, Tool] = {}


def tool(description: str, **params: Param):
    """Register a function as a tool. Its arguments must match the declared params exactly."""
    def register(function: Callable) -> Callable:
        name = function.__name__
        arguments = set(inspect.signature(function).parameters)
        if arguments != set(params):
            raise TypeError(f"{name}: params {sorted(params)} != arguments {sorted(arguments)}")
        if name in TOOLS:
            raise ValueError(f"tool {name} registered twice")
        TOOLS[name] = Tool(name, description, params, function)
        return function
    return register


def catalog() -> list[dict]:
    """Every registered tool's spec, as GET /tools returns them."""
    return [registered.spec() for registered in TOOLS.values()]


def call_tool(name: str, arguments: dict, user_id: int) -> dict:
    """Run a tool for the verified customer `user_id`. Returns {"result": ...} or {"error": ...}.

    A user_id inside `arguments` is rejected like any unknown argument: it can only come from the
    verified customer.
    """
    registered = TOOLS.get(name)
    if registered is None:
        return {"error": f"unknown tool {name!r}"}

    problem = argument_problem(registered, arguments)
    if problem:
        return {"error": problem}

    if "user_id" in registered.params:
        arguments = {**arguments, "user_id": user_id}
    try:
        return {"result": registered.function(**arguments)}
    except ToolError as error:
        return {"error": str(error)}


def argument_problem(registered: Tool, arguments: dict) -> str | None:
    """Why `arguments` can't be passed to the tool (unknown or missing required ones), or None."""
    allowed = registered.caller_params
    unknown = sorted(set(arguments) - set(allowed))
    missing = sorted(name for name, param in allowed.items()
                     if param.required and arguments.get(name) is None)
    if not unknown and not missing:
        return None
    return f"{registered.name}: unknown arguments {unknown}, missing required {missing}"
