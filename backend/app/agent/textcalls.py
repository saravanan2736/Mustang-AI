"""
Tool calls a model wrote as plain text instead of the structured tool_calls field.

llama-server turns a model's tool-call output into structured calls, but small models sometimes
produce the format slightly off (or after other text) and it stays in the reply as text:

    <tool_call>
    <function=list_files>
    <parameter=path>
    .
    </parameter>
    </function>
    </tool_call>

or  <tool_call>{"name": "list_files", "arguments": {"path": "."}}</tool_call>

The agent loop runs those calls anyway (through the same name and argument checks as any call)
and keeps the raw tags out of the chat. TagFilter hides them while the reply streams.
"""

import json
import re
import uuid
from typing import Any

OPEN = "<tool_call>"
_BLOCK = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FUNCTION = re.compile(r"<function=([A-Za-z0-9_-]{1,64})>(.*?)(?:</function>|$)", re.DOTALL)
_PARAM = re.compile(r"<parameter=([A-Za-z0-9_-]{1,64})>(.*?)</parameter>", re.DOTALL)


def _strip_one_newline(value: str) -> str:
    """The XML format puts values on their own lines: drop one leading and one trailing newline."""
    if value.startswith("\r\n"):
        value = value[2:]
    elif value.startswith("\n"):
        value = value[1:]
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    return value


def _typed(value: str, schema: dict[str, Any] | None) -> Any:
    kind = (schema or {}).get("type")
    if kind in ("integer", "number", "boolean"):
        try:
            return json.loads(value.strip())
        except ValueError:
            return value
    return value


def _from_block(body: str, schemas: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    body = body.strip()
    if body.startswith("{"):
        try:
            obj = json.loads(body)
        except ValueError:
            return None
        name = obj.get("name") if isinstance(obj, dict) else None
        args = obj.get("arguments", obj.get("parameters", {})) if isinstance(obj, dict) else None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return None
        if isinstance(name, str) and isinstance(args, dict):
            return name, args
        return None
    m = _FUNCTION.search(body)
    if not m:
        return None
    name, inner = m.group(1), m.group(2)
    props = (schemas.get(name) or {}).get("properties", {})
    args = {k: _typed(_strip_one_newline(v), props.get(k)) for k, v in _PARAM.findall(inner)}
    return name, args


def extract_text_calls(text: str, schemas: dict[str, dict[str, Any]]) -> tuple[list[tuple[str, str, str]], str]:
    """
    Find complete <tool_call> blocks in `text`. `schemas` maps tool name -> its parameters schema
    (used to type numbers and booleans). Returns ([(call_id, name, arguments_json)], text without
    the blocks). A block that can't be understood stays out of the calls but is removed from the
    text too, since it is protocol noise, not something to show the user.
    """
    calls: list[tuple[str, str, str]] = []
    for m in _BLOCK.finditer(text):
        parsed = _from_block(m.group(1), schemas)
        if parsed:
            name, args = parsed
            calls.append((f"call_{uuid.uuid4().hex[:24]}", name, json.dumps(args)))
    # An unterminated block (the reply was cut off mid-call) is dropped as well.
    cleaned = _BLOCK.sub("", text)
    cut = cleaned.find(OPEN)
    if cut != -1:
        cleaned = cleaned[:cut]
    return calls, cleaned.strip()


class TagFilter:
    """
    Streaming filter: passes text through, but holds back anything that could be the start of a
    "<tool_call>" tag, and swallows everything from the tag on. `held` is what was swallowed, so
    the caller can show it if it turns out not to be a tool call.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False
        self.held = ""

    def feed(self, chunk: str) -> str:
        if self._inside:
            self.held += chunk
            return ""
        self._buffer += chunk
        i = self._buffer.find(OPEN)
        if i != -1:
            out, rest = self._buffer[:i], self._buffer[i:]
            self._buffer, self._inside, self.held = "", True, rest
            return out
        # Keep the longest tail that is a prefix of the tag ("<tool_" ...) until the next chunk.
        keep = 0
        for n in range(min(len(OPEN) - 1, len(self._buffer)), 0, -1):
            if OPEN.startswith(self._buffer[-n:]):
                keep = n
                break
        out = self._buffer[: len(self._buffer) - keep]
        self._buffer = self._buffer[len(self._buffer) - keep:]
        return out

    def flush(self) -> str:
        """Text still held at the end of the stream that was not a tag (a dangling '<tool')."""
        out, self._buffer = self._buffer, ""
        return out

    @property
    def saw_tag(self) -> bool:
        return self._inside
