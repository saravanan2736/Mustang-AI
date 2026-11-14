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
    terminal: bool = False  # the user turned Terminal on for this chat (run_command is offered)
    read_chars: int = 8_000  # most characters one read_file call returns (smaller for a small context window)
    tasks: list = field(default_factory=list)  # the agent's task list for this chat (see app.agent.tasks)


@dataclass
class Proposal:
    """A change a tool wants to make, shown to the user before anything happens. `apply` runs
    only after the user approved it (with the tool call's id, for the change journal)."""
    summary: str
    diff: str
    apply: Callable[[str], Awaitable[str]]
    always_ask: bool = False  # never covered by "approve all" (terminal commands)


Handler = Callable[[dict[str, Any], ToolContext], Awaitable["str | Proposal"]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler  # read-only tools return the result; approval tools return a Proposal
    needs_workspace: bool = False
    needs_terminal: bool = False  # offered only when the user turned Terminal on for the chat
    read_only: bool = True
    needs_approval: bool = False
    timeout_s: float = 10.0
    max_string: int = field(default=1000)

    def definition(self) -> Tool:
        return Tool(function=ToolFunction(name=self.name, description=self.description, parameters=self.parameters))


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def _check_value(schema: dict, value: Any, label: str, max_string: int) -> None:
    """Check one value against a small JSON-schema subset: type, enum, string length, and for arrays
    maxItems and the items' schema; for objects the properties, required and no unknown keys."""
    want = schema.get("type")
    py = _TYPES.get(want)
    if py is not None and (not isinstance(value, py) or (want in ("integer", "number") and isinstance(value, bool))):
        raise ToolError(f"'{label}' must be a {want}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"'{label}' must be one of: {', '.join(map(str, schema['enum']))}.")
    if isinstance(value, str) and len(value) > max_string:
        raise ToolError(f"'{label}' is too long (max {max_string} characters).")
    if want == "array":
        if len(value) > schema.get("maxItems", 1000):
            raise ToolError(f"'{label}' has too many items (max {schema.get('maxItems', 1000)}).")
        for i, item in enumerate(value):
            _check_value(schema.get("items", {}), item, f"{label}[{i + 1}]", max_string)
    if want == "object":
        props = schema.get("properties", {})
        unknown = sorted(set(value) - set(props))
        if unknown:
            raise ToolError(f"Unknown field(s) in '{label}': {', '.join(unknown)}. Allowed: {', '.join(props) or 'none'}.")
        for name in schema.get("required", []):
            if name not in value:
                raise ToolError(f"'{label}' is missing '{name}'.")
        for name, sub in value.items():
            _check_value(props[name], sub, f"{label}.{name}", max_string)


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
        if want in ("array", "object"):
            _check_value(props[name], value, name, spec.max_string)
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
    from app.agent import tasks, terminal, tools  # noqa: F401  (registers the built-in tools)

    return [t for t in _REGISTRY.values()
            if (ctx.workspace is not None or not t.needs_workspace) and (ctx.terminal or not t.needs_terminal)]


def get_tool(name: str, ctx: ToolContext) -> ToolSpec | None:
    return next((t for t in available_tools(ctx) if t.name == name), None)
