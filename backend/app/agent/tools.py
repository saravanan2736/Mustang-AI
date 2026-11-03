"""
The built-in tools for M5 Phase C. All are read-only: they never write, delete, run programs
or use the network. File tools only see the workspace folder (app.agent.workspace).
"""

import ast
import asyncio
import datetime
import math
import operator
import os
import re
from pathlib import Path

from app.agent.registry import ToolContext, ToolError, ToolSpec, register
from app.agent.workspace import WorkspaceError

MAX_READ_BYTES = 256 * 1024
MAX_READ_CHARS = 20_000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_LIST_ENTRIES = 200
MAX_SEARCH_FILES = 2_000
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_SEARCH_MATCHES = 50


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _looks_binary(sample: bytes) -> bool:
    return b"\x00" in sample


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


# --- date and time ----------------------------------------------------------------------------

async def _current_time(args: dict, ctx: ToolContext) -> str:
    now = datetime.datetime.now().astimezone()
    offset = now.strftime("%z")
    return f"{now:%Y-%m-%d %H:%M:%S} ({now:%A}), UTC{offset[:3]}:{offset[3:]}"


register(ToolSpec(
    name="get_current_time",
    description="Get the current local date, time and weekday on the user's computer.",
    parameters=_obj({}),
    handler=_current_time,
))


# --- calculator -------------------------------------------------------------------------------

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.BitXor: operator.pow,  # "2^10"
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "min": min, "max": max,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_MAX_INT_BITS = 4096


def _check_size(value):
    if isinstance(value, int) and value.bit_length() > _MAX_INT_BITS:
        raise ToolError("Result is too large.")
    if isinstance(value, float) and math.isinf(value):
        raise ToolError("Result is too large.")
    return value


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval(node.left), _eval(node.right)
        if type(node.op) in (ast.Pow, ast.BitXor):
            if abs(right) > 10_000 or (abs(left) > 1 and abs(right) * math.log2(abs(left)) > _MAX_INT_BITS):
                raise ToolError("Exponent is too large.")
        try:
            return _check_size(_BINOPS[type(node.op)](left, right))
        except ZeroDivisionError:
            raise ToolError("Division by zero.")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        try:
            return _check_size(_FUNCS[node.func.id](*[_eval(a) for a in node.args]))
        except (ValueError, TypeError) as e:
            raise ToolError(f"{node.func.id}: {e}")
    raise ToolError("Only numbers, + - * / // % ** ( ), pi, e and sqrt, abs, round, floor, ceil, log, log10, log2, exp, sin, cos, tan, min, max are allowed.")


def calculate(expression: str) -> str:
    # "1,000,000" -> 1000000, but keep the commas between function arguments: max(3, 9).
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expression.strip().replace("×", "*").replace("÷", "/"))
    if not text or len(text) > 200:
        raise ToolError("Give an expression of 1 to 200 characters.")
    if text.endswith("%"):
        text = f"({text[:-1]})/100"
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ToolError("Not a valid arithmetic expression.")
    value = _eval(tree)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.12g}"
    return str(value)


async def _calculator(args: dict, ctx: ToolContext) -> str:
    return calculate(args["expression"])


register(ToolSpec(
    name="calculator",
    description="Evaluate an arithmetic expression exactly, e.g. (17+25)/3, 15/100*240, sqrt(2), 2**10.",
    parameters=_obj({"expression": {"type": "string", "description": "The arithmetic expression"}}, ["expression"]),
    handler=_calculator,
    max_string=200,
))


# --- workspace files --------------------------------------------------------------------------

def _workspace(ctx: ToolContext):
    if ctx.workspace is None:
        raise ToolError("No workspace folder is open.")
    return ctx.workspace


def _resolve(ctx: ToolContext, path: str) -> Path:
    try:
        return _workspace(ctx).resolve(path)
    except WorkspaceError as e:
        raise ToolError(str(e))


def _list_files(path: str, ctx: ToolContext) -> str:
    ws = _workspace(ctx)
    target = _resolve(ctx, path)
    if not target.is_dir():
        raise ToolError(f"Not a folder: {path!r}. Use read_file for files.")
    entries = []
    with os.scandir(target) as it:
        for e in it:
            p = Path(e.path)
            if not ws.is_followable(p):
                continue
            if e.is_dir(follow_symlinks=False):
                entries.append((0, e.name.lower(), f"{e.name}\\  (folder)"))
            else:
                entries.append((1, e.name.lower(), f"{e.name}  ({_size(e.stat(follow_symlinks=False).st_size)})"))
    entries.sort()
    shown = [line for _, _, line in entries[:MAX_LIST_ENTRIES]]
    header = f"Contents of {ws.relative(target)} ({len(entries)} item{'s' if len(entries) != 1 else ''}):"
    if not entries:
        return header + "\n(empty)"
    more = f"\n... and {len(entries) - MAX_LIST_ENTRIES} more" if len(entries) > MAX_LIST_ENTRIES else ""
    return header + "\n" + "\n".join(shown) + more


def _read_file(path: str, ctx: ToolContext) -> str:
    target = _resolve(ctx, path)
    if target.is_dir():
        raise ToolError(f"{path!r} is a folder. Use list_files.")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError(f"{path!r} is too large to read ({_size(size)}; the limit is {_size(MAX_FILE_BYTES)}).")
    with open(target, "rb") as f:
        data = f.read(MAX_READ_BYTES)
    if _looks_binary(data[:8192]) and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ToolError(f"{path!r} is a binary file, not text.")
    text = _decode(data)
    truncated = size > MAX_READ_BYTES or len(text) > MAX_READ_CHARS
    text = text[:MAX_READ_CHARS]
    note = f"\n\n[Showing the first {len(text)} characters of {_size(size)}.]" if truncated else ""
    return text + note


def _search_text(query: str, path: str, ctx: ToolContext) -> str:
    ws = _workspace(ctx)
    start = _resolve(ctx, path)
    if not start.is_dir():
        raise ToolError(f"Not a folder: {path!r}.")
    needle = query.lower()
    matches, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and ws.is_followable(here / d))
        for name in sorted(filenames):
            p = here / name
            if scanned >= MAX_SEARCH_FILES or len(matches) >= MAX_SEARCH_MATCHES:
                break
            if not ws.is_followable(p):
                continue
            try:
                if p.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            scanned += 1
            if _looks_binary(data[:8192]):
                continue
            for n, line in enumerate(_decode(data).splitlines(), 1):
                if needle in line.lower():
                    matches.append(f"{ws.relative(p)}:{n}: {line.strip()[:200]}")
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        break
    if not matches:
        return f"No matches for {query!r} ({scanned} text file{'s' if scanned != 1 else ''} searched)."
    more = f"\n[Stopped at {MAX_SEARCH_MATCHES} matches.]" if len(matches) >= MAX_SEARCH_MATCHES else ""
    return f"{len(matches)} match{'es' if len(matches) != 1 else ''} for {query!r}:\n" + "\n".join(matches) + more


async def _in_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)


register(ToolSpec(
    name="list_files",
    description="List the files and folders in a folder of the user's workspace.",
    parameters=_obj({"path": {"type": "string", "description": "Folder relative to the workspace; '.' for the top"}}, ["path"]),
    handler=lambda a, c: _in_thread(_list_files, a["path"], c),
    needs_workspace=True,
))

register(ToolSpec(
    name="read_file",
    description="Read a text file from the user's workspace (up to 20,000 characters).",
    parameters=_obj({"path": {"type": "string", "description": "File path relative to the workspace, e.g. notes.txt"}}, ["path"]),
    handler=lambda a, c: _in_thread(_read_file, a["path"], c),
    needs_workspace=True,
))

register(ToolSpec(
    name="search_files",
    description="Search the text files in the user's workspace for a word or phrase (case-insensitive).",
    parameters=_obj({
        "query": {"type": "string", "description": "Text to look for"},
        "path": {"type": "string", "description": "Folder to search, relative to the workspace; '.' for all"},
    }, ["query"]),
    handler=lambda a, c: _in_thread(_search_text, a["query"], a.get("path", "."), c),
    needs_workspace=True,
    timeout_s=20.0,
    max_string=200,
))


# --- conversation history ---------------------------------------------------------------------

async def _search_history(args: dict, ctx: ToolContext) -> str:
    from app.db.database import get_db

    query = args["query"].strip()
    if not query:
        raise ToolError("Give some words to search for.")
    if not ctx.conversation_id:
        raise ToolError("This chat has no saved history yet.")
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? "
            "AND role IN ('user', 'assistant') AND content LIKE ? ESCAPE '\\' "
            "ORDER BY created_at ASC, rowid ASC LIMIT 20",
            (ctx.conversation_id, pattern),
        )
    finally:
        await db.close()
    if not rows:
        return f"No earlier messages in this chat mention {query!r}."
    lines = []
    for r in rows:
        text = r["content"]
        i = text.lower().find(query.lower())
        snippet = text[max(0, i - 80): i + len(query) + 120].replace("\n", " ")
        when = datetime.datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{when}] {r['role']}: ...{snippet}...")
    return f"{len(rows)} earlier message{'s' if len(rows) != 1 else ''} in this chat mention {query!r}:\n" + "\n".join(lines)


register(ToolSpec(
    name="search_history",
    description="Search the earlier messages of this chat for a word or phrase.",
    parameters=_obj({"query": {"type": "string", "description": "Words to search for"}}, ["query"]),
    handler=_search_history,
    max_string=200,
))
