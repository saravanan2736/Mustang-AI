"""
Built-in tool registry. The model is offered these tools only, and every call is checked here
first: known name, arguments that are a JSON object matching the tool's schema. Nothing the
model sends is executed without passing these checks.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.agent.workspace import Workspace
from app.core.engine_base import Tool, ToolFunction


class ToolError(Exception):
    """A tool call that can't be carried out; the message goes back to the model."""


@dataclass
class ToolContext:
    workspace: Workspace | None = None
    conversation_id: str | None = None


@dataclass
class Proposal:
    """A change a tool wants to make, shown to the user before anything happens. `apply` runs
    only after the user approved it (with the tool call's id, for the change journal)."""
    summary: str
    diff: str
    apply: Callable[[str], Awaitable[str]]


Handler = Callable[[dict[str, Any], ToolContext], Awaitable["str | Proposal"]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler  # read-only tools return the result; approval tools return a Proposal
    needs_workspace: bool = False
    read_only: bool = True
    needs_approval: bool = False
    timeout_s: float = 10.0
    max_string: int = field(default=1000)

    def definition(self) -> Tool:
        return Tool(function=ToolFunction(name=self.name, description=self.description, parameters=self.parameters))


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def validate_arguments(spec: ToolSpec, raw: str) -> dict[str, Any]:
    """Parse and check a call's arguments: a JSON object, only known properties, required ones
    present, simple types matching. Raises ToolError with a message the model can act on."""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        raise ToolError(f"Arguments are not valid JSON ({e.msg}). Send a JSON object.")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("Arguments must be a JSON object.")
    props: dict[str, Any] = spec.parameters.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError(f"Unknown argument(s) for {spec.name}: {', '.join(unknown)}. Allowed: {', '.join(props) or 'none'}.")
    for name in spec.parameters.get("required", []):
        if name not in args:
            raise ToolError(f"Missing required argument '{name}' for {spec.name}.")
    for name, value in args.items():
        want = props[name].get("type")
        py = _TYPES.get(want)
        if py is None:
            continue
        # bool is an int subclass in Python; don't accept true/false for numbers.
        if not isinstance(value, py) or (want in ("integer", "number") and isinstance(value, bool)):
            raise ToolError(f"Argument '{name}' must be a {want}.")
        if isinstance(value, str) and len(value) > spec.max_string:
            raise ToolError(f"Argument '{name}' is too long (max {spec.max_string} characters).")
        if want == "integer":
            lo, hi = props[name].get("minimum"), props[name].get("maximum")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                raise ToolError(f"Argument '{name}' must be between {lo} and {hi}.")
    return args


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"Tool {spec.name} registered twice")
    Tool(function=ToolFunction(name=spec.name))  # name must satisfy the schema pattern
    _REGISTRY[spec.name] = spec
    return spec


def available_tools(ctx: ToolContext) -> list[ToolSpec]:
    """Tools offered for this context: file tools only when a workspace is open."""
    from app.agent import tools  # noqa: F401  (registers the built-in tools)

    return [t for t in _REGISTRY.values() if ctx.workspace is not None or not t.needs_workspace]


def get_tool(name: str, ctx: ToolContext) -> ToolSpec | None:
    return next((t for t in available_tools(ctx) if t.name == name), None)
