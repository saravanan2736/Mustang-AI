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
MAX_READ_CHARS = 8_000  # per call; longer files are read in parts (start_line / end_line)
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


def read_limit_for_context(n_ctx: int) -> int:
    """How much of a file one read may return: about a sixth of the context window (3 characters is
    about a token), between 1,500 and MAX_READ_CHARS. A fixed 8,000 characters left no room for
    anything else in a 4k window, and the history trimming then dropped the user's task."""
    return max(1_500, min(MAX_READ_CHARS, n_ctx // 2)) if n_ctx > 0 else MAX_READ_CHARS


def _read_file(path: str, ctx: ToolContext, start_line: int | None = None, end_line: int | None = None) -> str:
    limit = ctx.read_chars or MAX_READ_CHARS
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
    lines = _decode(data).splitlines(keepends=True)
    total = len(lines)
    partial_file = size > MAX_READ_BYTES  # only the first part of a very large file is loaded
    first = max(1, start_line or 1)
    if first > total:
        raise ToolError(f"{path!r} has only {total} line{'s' if total != 1 else ''}.")
    last = min(end_line or total, total)
    if last < first:
        raise ToolError("end_line is before start_line.")
    out, used, n, cut_line = [], 0, first, None
    while n <= last:
        line = lines[n - 1]
        if out and used + len(line) > limit:
            break
        if len(line) > limit:
            cut_line, line = n, line[:limit]
        out.append(line)
        used += len(line)
        n += 1
    shown_last = n - 1
    body = "".join(out)
    if first == 1 and shown_last == total and not partial_file and cut_line is None:
        return body  # the whole file, unchanged
    tail = ""
    if cut_line is not None:
        tail = f" Line {cut_line} is longer than {limit} characters and was cut."
    if shown_last < total:
        tail += f" To read more, call read_file again with start_line={shown_last + 1}."
    of = f"{total}+" if partial_file else str(total)
    return f"{body}\n\n[Lines {first}-{shown_last} of {of} ({_size(size)}).{tail}]"


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
    description=("Read a text file from the user's workspace. A long file comes back in parts of about 8,000 "
                 "characters with a note saying where to continue; pass start_line and end_line to read a "
                 "specific range."),
    parameters=_obj({
        "path": {"type": "string", "description": "File path relative to the workspace, e.g. notes.txt"},
        "start_line": {"type": "integer", "description": "First line to read (1 = the top). Optional."},
        "end_line": {"type": "integer", "description": "Last line to read. Optional."},
    }, ["path"]),
    handler=lambda a, c: _in_thread(_read_file, a["path"], c, a.get("start_line"), a.get("end_line")),
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


# --- workspace changes (need the user's approval every time) ----------------------------------

# Never written by the agent, even with approval: files Windows would run or open as programs.
EXECUTABLE_SUFFIXES = {
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".msi", ".msp", ".scr", ".pif", ".lnk", ".url", ".hta", ".cpl", ".dll", ".sys", ".reg", ".jar", ".appref-ms",
}
MAX_WRITE_CHARS = 200_000
MAX_NEW_FOLDERS = 6
MAX_EDIT_FILE_BYTES = 1024 * 1024
MAX_DIFF_CHARS = 20_000
_UTF8_BOM = b"\xef\xbb\xbf"


def _check_writable_name(target: Path, path: str) -> None:
    if target.suffix.lower() in EXECUTABLE_SUFFIXES:
        raise ToolError(f"{path!r}: agent mode doesn't create or change program and script files ({target.suffix}).")
    if target.name.lower().endswith(".mustang-tmp"):
        raise ToolError("That name is reserved.")


def _diff(before: str, after: str, path: str, new_file: bool) -> str:
    import difflib

    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="(new file)" if new_file else f"a/{path}", tofile=f"b/{path}", n=2,
    )
    text = "".join(l if l.endswith("\n") else l + "\n" for l in lines)
    return text if len(text) <= MAX_DIFF_CHARS else text[:MAX_DIFF_CHARS] + "\n[diff cut off]\n"


def _prepare_create(path: str, content: str, ctx: ToolContext):
    from app.agent.changes import apply_change
    from app.agent.registry import Proposal

    ws = _workspace(ctx)
    try:
        target = ws.resolve(path, must_exist=False)
    except WorkspaceError as e:
        raise ToolError(str(e))
    _check_writable_name(target, path)
    if len(content) > MAX_WRITE_CHARS:
        raise ToolError(f"Content is too long (max {MAX_WRITE_CHARS} characters).")
    if target.exists():
        return _prepare_replace(ws, target, path, content, ctx)
    # Missing folders on the way are created together with the file (the approval shows them).
    new_dirs: list[Path] = []
    probe = target.parent
    while not probe.exists() and probe != ws.root:
        new_dirs.append(probe)
        probe = probe.parent
    if not probe.is_dir():
        raise ToolError(f"{ws.relative(probe)!r} is a file, so {path!r} can't be created inside it.")
    if len(new_dirs) > MAX_NEW_FOLDERS:
        raise ToolError(f"{path!r} would create {len(new_dirs)} new folders (the limit is {MAX_NEW_FOLDERS}).")
    new_dirs.reverse()
    data = content.encode("utf-8")
    rel = ws.relative(target)
    n = len(content.splitlines())

    folders = f" and the folder {ws.relative(new_dirs[0])}" if len(new_dirs) == 1 else \
        f" and {len(new_dirs)} folders ({ws.relative(new_dirs[0])} ...)" if new_dirs else ""

    async def apply(call_id: str) -> str:
        await asyncio.to_thread(apply_change, ws, target, data, kind="create", call_id=call_id,
                                conversation_id=ctx.conversation_id, expected_sha256=None, make_dirs=new_dirs)
        return f"Created {rel}{folders} ({n} line{'s' if n != 1 else ''})."

    return Proposal(summary=f"Create {rel}{folders} ({n} line{'s' if n != 1 else ''})",
                    diff=_diff("", content, rel, True), apply=apply)


def _prepare_replace(ws, target: Path, path: str, content: str, ctx: ToolContext):
    """create_file on a file that exists: replace its whole content. Shown as a diff, needs approval,
    and the old version is kept for Undo (same journal as an edit)."""
    from app.agent.changes import apply_change, sha256_bytes
    from app.agent.registry import Proposal

    if target.is_dir():
        raise ToolError(f"{path!r} is a folder.")
    raw = target.read_bytes()
    if len(raw) > MAX_EDIT_FILE_BYTES:
        raise ToolError(f"{path!r} is too large to replace ({_size(len(raw))}; the limit is {_size(MAX_EDIT_FILE_BYTES)}).")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or _looks_binary(raw[:8192]):
        raise ToolError(f"{path!r} is not a UTF-8 text file; it can't be replaced here.")
    bom = raw.startswith(_UTF8_BOM)
    try:
        old = raw[len(_UTF8_BOM) if bom else 0:].decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{path!r} is not a UTF-8 text file; it can't be replaced here.")
    crlf = "\r\n" in old
    old_norm = old.replace("\r\n", "\n")
    new_norm = content.replace("\r\n", "\n")
    if new_norm == old_norm:
        raise ToolError(f"{path!r} already has exactly this content; nothing would change.")
    out = new_norm.replace("\n", "\r\n") if crlf else new_norm  # keep the file's own line endings
    data = (_UTF8_BOM if bom else b"") + out.encode("utf-8")
    before_sha = sha256_bytes(raw)
    rel = ws.relative(target)
    n = len(new_norm.splitlines())

    async def apply(call_id: str) -> str:
        await asyncio.to_thread(apply_change, ws, target, data, kind="edit", call_id=call_id,
                                conversation_id=ctx.conversation_id, expected_sha256=before_sha)
        return f"Replaced {rel} ({n} line{'s' if n != 1 else ''}). The previous version is kept for Undo."

    return Proposal(summary=f"Replace {rel} ({n} line{'s' if n != 1 else ''})",
                    diff=_diff(old_norm, new_norm, rel, False), apply=apply)


def _prepare_edit(path: str, old_text: str, new_text: str, ctx: ToolContext):
    from app.agent.changes import apply_change, sha256_bytes
    from app.agent.registry import Proposal

    ws = _workspace(ctx)
    target = _resolve(ctx, path)
    if target.is_dir():
        raise ToolError(f"{path!r} is a folder.")
    _check_writable_name(target, path)
    raw = target.read_bytes()
    if len(raw) > MAX_EDIT_FILE_BYTES:
        raise ToolError(f"{path!r} is too large to edit ({_size(len(raw))}; the limit is {_size(MAX_EDIT_FILE_BYTES)}).")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or _looks_binary(raw[:8192]):
        raise ToolError(f"{path!r} is not a UTF-8 text file; it can't be edited here.")
    bom = raw.startswith(_UTF8_BOM)
    try:
        text = raw[len(_UTF8_BOM) if bom else 0:].decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError(f"{path!r} is not a UTF-8 text file; it can't be edited here.")
    crlf = "\r\n" in text
    norm = text.replace("\r\n", "\n")
    old = old_text.replace("\r\n", "\n")
    if not old:
        raise ToolError("old_text is empty. Give the exact text to replace (read the file first).")
    count = norm.count(old)
    if count == 0:
        raise ToolError(f"old_text was not found in {path!r}. Read the file and copy the exact text to replace.")
    if count > 1:
        raise ToolError(f"old_text appears {count} times in {path!r}; include more surrounding text so it is unique.")
    if len(new_text) > MAX_WRITE_CHARS:
        raise ToolError(f"new_text is too long (max {MAX_WRITE_CHARS} characters).")
    updated = norm.replace(old, new_text.replace("\r\n", "\n"), 1)
    if updated == norm:
        raise ToolError("new_text is the same as old_text; nothing would change.")
    out = updated.replace("\n", "\r\n") if crlf else updated
    data = (_UTF8_BOM if bom else b"") + out.encode("utf-8")
    before_sha = sha256_bytes(raw)
    rel = ws.relative(target)
    diff = _diff(norm, updated, rel, False)
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))

    async def apply(call_id: str) -> str:
        await asyncio.to_thread(apply_change, ws, target, data, kind="edit", call_id=call_id,
                                conversation_id=ctx.conversation_id, expected_sha256=before_sha)
        return f"Edited {rel} (+{added} −{removed} lines). The previous version is kept for Undo."

    return Proposal(summary=f"Edit {rel} (+{added} −{removed} lines)", diff=diff, apply=apply)


register(ToolSpec(
    name="create_file",
    description=("Create a text file in the user's workspace (missing folders are created). If the file already "
                 "exists its whole content is replaced; for a small change use edit_file. The user sees the "
                 "content or diff and must approve it."),
    parameters=_obj({
        "path": {"type": "string", "description": "New file path relative to the workspace, e.g. todo.md"},
        "content": {"type": "string", "description": "The full text of the file"},
    }, ["path", "content"]),
    handler=lambda a, c: _in_thread(_prepare_create, a["path"], a["content"], c),
    needs_workspace=True,
    read_only=False,
    needs_approval=True,
    max_string=MAX_WRITE_CHARS,
))

register(ToolSpec(
    name="edit_file",
    description=("Change a text file in the user's workspace by replacing one exact piece of text (old_text, "
                 "which must appear exactly once) with new_text. Read the file first. The user sees the "
                 "change and must approve it."),
    parameters=_obj({
        "path": {"type": "string", "description": "File path relative to the workspace"},
        "old_text": {"type": "string", "description": "Exact text to replace, copied from the file"},
        "new_text": {"type": "string", "description": "Replacement text (may be empty to remove old_text)"},
    }, ["path", "old_text", "new_text"]),
    handler=lambda a, c: _in_thread(_prepare_edit, a["path"], a["old_text"], a["new_text"], c),
    needs_workspace=True,
    read_only=False,
    needs_approval=True,
    max_string=MAX_WRITE_CHARS,
))
