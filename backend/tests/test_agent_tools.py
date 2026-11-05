"""
M5 Phase C: the built-in read-only tools and the argument checks in front of them.
"""

import json
import time
import uuid

import pytest

from app.agent import tools
from app.agent.registry import ToolContext, ToolError, available_tools, get_tool, validate_arguments
from app.agent.workspace import Workspace
from app.db.database import get_db


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "notes.txt").write_text("Buy milk.\nCall Ravi at 6 pm.\n", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("# Guide\nHorses like hay.\n", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\x89PNG\x00\x00\x00data")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("horses in git internals", encoding="utf-8")
    return ToolContext(workspace=Workspace(root))


# --- calculator -------------------------------------------------------------------------------

@pytest.mark.parametrize("expr, want", [
    ("1234 * 5678", "7006652"), ("(17 + 25) / 3", "14"), ("15/100*240", "36"), ("2^10", "1024"),
    ("2**10", "1024"), ("sqrt(2)", "1.41421356237"), ("-3 + +4", "1"), ("7 // 2", "3"), ("7 % 4", "3"),
    ("1,000 * 3", "3000"), ("1,234,567 + 1", "1234568"), ("max(3, 9, 4)", "9"), ("round(pi, 3)", "3.142"), ("50%", "0.5"), ("10 / 4", "2.5"),
])
def test_calculator(expr, want):
    assert tools.calculate(expr) == want


@pytest.mark.parametrize("expr", [
    "__import__('os').system('calc')", "().__class__", "open('x')", "lambda: 1", "[1, 2]", "'a' * 3",
    "x + 1", "sqrt(x=2)", "9**9**9", "2**100000", "1/0", "", "1" * 201, "True + 1",
])
def test_calculator_refuses_anything_but_arithmetic(expr):
    with pytest.raises(ToolError):
        tools.calculate(expr)


# --- argument validation ----------------------------------------------------------------------

def test_arguments_are_checked_against_the_schema():
    spec = get_tool("calculator", ToolContext())
    assert validate_arguments(spec, '{"expression": "1+1"}') == {"expression": "1+1"}
    for raw, msg in [
        ("not json", "not valid JSON"), ("[1]", "JSON object"), ("{}", "Missing required"),
        ('{"expression": 5}', "must be a string"), ('{"expression": "1", "rm": true}', "Unknown argument"),
        (json.dumps({"expression": "1" * 300}), "too long"),
    ]:
        with pytest.raises(ToolError, match=msg):
            validate_arguments(spec, raw)


def test_file_tools_are_only_offered_with_a_workspace(ws):
    without = {t.name for t in available_tools(ToolContext())}
    with_ws = {t.name for t in available_tools(ws)}
    assert without == {"get_current_time", "calculator", "search_history"}
    assert with_ws == without | {"list_files", "read_file", "search_files", "create_file", "edit_file"}
    # Every tool either only reads or needs the user's approval for each call (M5 Phase E).
    assert all(t.read_only != t.needs_approval for t in available_tools(ws))
    assert {t.name for t in available_tools(ws) if t.needs_approval} == {"create_file", "edit_file"}


# --- files ------------------------------------------------------------------------------------

def test_list_files(ws):
    out = tools._list_files(".", ws)
    assert out.splitlines()[0] == "Contents of . (4 items):"
    assert "docs\\  (folder)" in out and "notes.txt" in out
    assert "guide.md" in tools._list_files("docs", ws)
    with pytest.raises(ToolError, match="Not a folder"):
        tools._list_files("notes.txt", ws)


def test_read_file(ws):
    assert tools._read_file("notes.txt", ws).startswith("Buy milk.")
    assert "Horses" in tools._read_file("docs/guide.md", ws)
    with pytest.raises(ToolError, match="binary"):
        tools._read_file("image.bin", ws)
    with pytest.raises(ToolError, match="folder"):
        tools._read_file("docs", ws)


def test_read_file_limits(ws):
    root = ws.workspace.root
    (root / "big.txt").write_text("x" * 50_000, encoding="utf-8")
    out = tools._read_file("big.txt", ws)
    assert len(out) < tools.MAX_READ_CHARS + 200 and "Lines 1-1 of 1" in out
    with open(root / "huge.txt", "wb") as f:
        f.truncate(tools.MAX_FILE_BYTES + 1)
    with pytest.raises(ToolError, match="too large"):
        tools._read_file("huge.txt", ws)


def test_utf16_file_is_read_as_text(ws):
    (ws.workspace.root / "u16.txt").write_bytes("Hello in UTF-16".encode("utf-16"))
    assert tools._read_file("u16.txt", ws) == "Hello in UTF-16"


def test_search_files(ws):
    out = tools._search_text("horses", ".", ws)
    assert "docs\\guide.md:2: Horses like hay." in out
    assert ".git" not in out, "hidden folders are skipped"
    assert "No matches" in tools._search_text("zebra", ".", ws)


def test_no_workspace_means_no_file_access():
    with pytest.raises(ToolError, match="No workspace"):
        tools._read_file("notes.txt", ToolContext())


# --- time and history -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_current_time_format():
    out = await tools._current_time({}, ToolContext())
    assert out[4] == "-" and "UTC" in out and "(" in out


@pytest.mark.asyncio
async def test_search_history_stays_in_this_conversation():
    db = await get_db()
    now = time.time()
    try:
        for conv in ("hist-a", "hist-b"):
            await db.execute("INSERT INTO conversations (id, title, model, system_prompt, created_at, updated_at) VALUES (?, 't', 'm', '', ?, ?)", (conv, now, now))
        rows = [("hist-a", "user", "Tell me about horses"), ("hist-a", "assistant", "Horses eat hay; 100% true"),
                ("hist-a", "tool", "horses in a tool result"), ("hist-b", "user", "horses in another chat")]
        for i, (conv, role, content) in enumerate(rows):
            await db.execute("INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                             (str(uuid.uuid4()), conv, role, content, now + i))
        await db.commit()
    finally:
        await db.close()
    out = await tools._search_history({"query": "horses"}, ToolContext(conversation_id="hist-a"))
    assert "2 earlier messages" in out and "another chat" not in out and "tool result" not in out
    assert "No earlier messages" in await tools._search_history({"query": "_%"}, ToolContext(conversation_id="hist-a"))
    assert "1 earlier message" in await tools._search_history({"query": "100%"}, ToolContext(conversation_id="hist-a"))
