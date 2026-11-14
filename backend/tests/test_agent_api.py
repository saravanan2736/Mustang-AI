"""
M5 Phase C: agent mode through /api/chat/completions (the UI's endpoint), persistence of tool
steps (schema v2), reload, regenerate, and the v1 -> v2 migration with its backup.
"""

import asyncio
import json
import sqlite3

import aiosqlite
import pytest

from app.db import database


def _events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip() != "[DONE]"]


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes.txt").write_text("Buy milk. Call Ravi at 6 pm.", encoding="utf-8")
    return str(tmp_path)


async def _ask(client, conv_id, workspace, text="What does notes.txt say?", **extra):
    res = await client.post("/api/chat/completions", json={
        "conversation_id": conv_id, "messages": [{"role": "user", "content": text}],
        "agent": {"workspace": workspace}, **extra})
    return res


@pytest.mark.asyncio
async def test_agent_reply_streams_tool_steps_and_saves_them(async_client, scripted_engine, workspace):
    scripted_engine([{"text": "Let me look. ", "calls": [("read_file", '{"path": "notes.txt"}')]},
                     {"text": "It says to buy milk and call Ravi."}])
    res = await _ask(async_client, "agent-1", workspace)
    assert res.status_code == 200
    events = _events(res.text)

    agent = [e["agent"] for e in events if "agent" in e]
    assert [a["event"] for a in agent] == ["tool_call", "tool_result", "done"]
    assert agent[0]["name"] == "read_file" and json.loads(agent[0]["arguments"]) == {"path": "notes.txt"}
    assert agent[1]["ok"] and "Buy milk" in agent[1]["content"]
    assert agent[2]["reason"] is None
    text = "".join(e["choices"][0]["delta"].get("content") or "" for e in events)
    assert text == "Let me look. It says to buy milk and call Ravi."  # old clients still see readable text

    conv = (await async_client.get("/api/conversations/agent-1")).json()
    rows = [(m["role"], m["content"], bool(m["tool_calls"]), m["tool_call_id"]) for m in conv["messages"]]
    assert [r[0] for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1][1] == "Let me look. " and rows[1][2] is True
    assert rows[2][3] == conv["messages"][1]["tool_calls"][0]["id"] and "Buy milk" in rows[2][1]
    assert rows[3][1] == "It says to buy milk and call Ravi."

    summary = next(c for c in (await async_client.get("/api/conversations")).json() if c["id"] == "agent-1")
    assert summary["last_message"] == "It says to buy milk and call Ravi."


@pytest.mark.asyncio
async def test_regenerate_removes_the_whole_last_reply(async_client, scripted_engine, workspace):
    scripted_engine([{"calls": [("read_file", '{"path": "notes.txt"}')]}, {"text": "First answer."}])
    await _ask(async_client, "agent-regen", workspace)
    engine = scripted_engine([{"text": "Second answer."}])
    res = await async_client.post("/api/chat/regenerate", json={"conversation_id": "agent-regen", "agent": {"workspace": workspace}})
    assert res.status_code == 200
    roles = [m["role"] for m in (await async_client.get("/api/conversations/agent-regen")).json()["messages"]]
    assert roles == ["user", "assistant"]
    assert [m.role for m in engine.requests[0].messages] == ["system", "user"]


@pytest.mark.asyncio
async def test_regenerate_rebuilds_tool_turns_from_history(async_client, scripted_engine, workspace):
    scripted_engine([{"calls": [("calculator", '{"expression": "6*7"}')]}, {"text": "42."}])
    await _ask(async_client, "agent-hist", workspace, text="6*7?")
    scripted_engine([{"text": "Thanks!"}])
    await async_client.post("/api/chat/completions", json={"conversation_id": "agent-hist",
                            "messages": [{"role": "user", "content": "thanks"}], "agent": {}})
    engine = scripted_engine([{"text": "You're welcome."}])
    await async_client.post("/api/chat/regenerate", json={"conversation_id": "agent-hist", "agent": {}})
    sent = engine.requests[0].messages
    assert [m.role for m in sent] == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert sent[2].tool_calls[0].function.name == "calculator" and sent[3].tool_call_id == sent[2].tool_calls[0].id


@pytest.mark.asyncio
async def test_without_agent_the_endpoint_behaves_as_before(async_client, scripted_engine):
    engine = scripted_engine([{"text": "Plain reply."}])
    res = await async_client.post("/api/chat/completions", json={"conversation_id": "plain-1", "messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 200 and not any("agent" in e for e in _events(res.text))
    assert engine.requests[0].tools is None and [m.role for m in engine.requests[0].messages] == ["user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace, detail", [("relative\\path", "absolute"), ("C:\\", "whole drive"),
                                               ("C:\\does\\not\\exist\\anywhere", "does not exist")])
async def test_bad_workspace_is_refused(async_client, scripted_engine, workspace, detail):
    scripted_engine([])
    res = await _ask(async_client, "agent-bad", workspace)
    assert res.status_code == 400 and detail in res.json()["detail"]


@pytest.mark.asyncio
async def test_client_defined_tools_are_still_refused(async_client, scripted_engine, workspace):
    scripted_engine([])
    res = await _ask(async_client, "agent-tools", workspace, tools=[{"type": "function", "function": {"name": "run_command"}}])
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_stop_ends_a_running_agent_reply(async_client, scripted_engine, workspace, monkeypatch):
    from app.agent.registry import ToolContext, get_tool

    async def stop_while_running(args, ctx):
        await async_client.post("/api/chat/stop")
        return "42"

    monkeypatch.setattr(get_tool("calculator", ToolContext()), "handler", stop_while_running)
    engine = scripted_engine([{"calls": [("calculator", '{"expression": "6*7"}')]}, {"text": "never sent"}])
    res = await _ask(async_client, "agent-stop", workspace)
    done = [e["agent"] for e in _events(res.text) if "agent" in e][-1]
    assert done["event"] == "done" and done["reason"] == "stopped"
    assert len(engine.requests) == 1 and engine.cancelled >= 1


# --- schema v2 migration ----------------------------------------------------------------------

V1_MESSAGES = """
CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, profile_id TEXT DEFAULT 'default');
CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
    token_count INTEGER DEFAULT 0, finish_reason TEXT, created_at REAL NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE);
INSERT INTO conversations VALUES ('c1', 'old chat', 'm', '', 1, 1, 'default');
INSERT INTO messages VALUES ('m1', 'c1', 'user', 'hello from v1', 0, 'stop', 1);
PRAGMA user_version = 1;
"""


@pytest.mark.asyncio
async def test_v1_database_is_backed_up_and_migrated(monkeypatch, tmp_path):
    db_path = tmp_path / "mustang.db"
    with sqlite3.connect(db_path) as con:
        con.executescript(V1_MESSAGES)
    monkeypatch.setattr(database, "DB_PATH", db_path)

    await database.init_db()

    backups = list((tmp_path / "backups").glob("mustang-schema1-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "tool_calls" not in [r[1] for r in con.execute("PRAGMA table_info(messages)")]
    async with aiosqlite.connect(db_path) as db:
        assert (await (await db.execute("PRAGMA user_version")).fetchone())[0] == 2
        cols = [r[1] for r in await (await db.execute("PRAGMA table_info(messages)")).fetchall()]
        assert "tool_calls" in cols and "tool_call_id" in cols
        row = await (await db.execute("SELECT content, tool_calls, tool_call_id FROM messages WHERE id='m1'")).fetchone()
        assert row == ("hello from v1", None, None)


@pytest.mark.asyncio
async def test_restored_v1_backup_migrates_again(monkeypatch, tmp_path):
    db_path = tmp_path / "mustang.db"
    with sqlite3.connect(db_path) as con:
        con.executescript(V1_MESSAGES)
    monkeypatch.setattr(database, "DB_PATH", db_path)
    await database.init_db()
    backup = next((tmp_path / "backups").glob("mustang-schema1-*.db"))

    # "Restore" (Settings -> Backups does a file copy while the backend is stopped).
    db_path.write_bytes(backup.read_bytes())
    for side in ("-wal", "-shm"):
        (tmp_path / f"mustang.db{side}").unlink(missing_ok=True)
    await database.init_db()
    with sqlite3.connect(db_path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert con.execute("SELECT content FROM messages WHERE id='m1'").fetchone()[0] == "hello from v1"


# --- per-conversation agent settings (Phase D) ------------------------------------------------

@pytest.mark.asyncio
async def test_agent_mode_and_workspace_are_saved_per_conversation(async_client, workspace):
    created = (await async_client.post("/api/conversations", json={"title": "a", "agent_enabled": True, "workspace": workspace})).json()
    detail = (await async_client.get(f"/api/conversations/{created['id']}")).json()
    assert detail["agent_enabled"] is True and detail["workspace"].lower() == workspace.lower()

    res = await async_client.patch(f"/api/conversations/{created['id']}", json={"agent_enabled": False, "workspace": ""})
    assert res.status_code == 200
    detail = (await async_client.get(f"/api/conversations/{created['id']}")).json()
    assert detail["agent_enabled"] is False and detail["workspace"] is None

    plain = (await async_client.post("/api/conversations", json={"title": "b"})).json()
    detail = (await async_client.get(f"/api/conversations/{plain['id']}")).json()
    assert detail["agent_enabled"] is False and detail["workspace"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [r"relative\path", "C:\\", r"C:\does\not\exist\anywhere"])
async def test_bad_workspace_is_not_saved(async_client, bad):
    created = (await async_client.post("/api/conversations", json={"title": "c"})).json()
    res = await async_client.patch(f"/api/conversations/{created['id']}", json={"workspace": bad})
    assert res.status_code == 400 and res.json()["detail"].startswith("Workspace:")
    assert (await async_client.post("/api/conversations", json={"title": "d", "workspace": bad})).status_code == 400


# --- file changes over the API (Phase E) ------------------------------------------------------

async def _wait_for_pending(timeout=5.0):
    from app.agent import loop as agent_loop
    for _ in range(int(timeout / 0.02)):
        pending = agent_loop.pending_approvals()
        if pending:
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no change waited for approval")


@pytest.mark.asyncio
async def test_change_approve_list_and_undo_over_the_api(async_client, scripted_engine, workspace):
    import pathlib
    conv = (await async_client.post("/api/conversations", json={"title": "e", "agent_enabled": True, "workspace": workspace})).json()["id"]
    edit = json.dumps({"path": "notes.txt", "old_text": "Buy milk.", "new_text": "Buy eggs."})
    scripted_engine([{"calls": [("edit_file", edit)]}, {"text": "Changed it."}])

    task = asyncio.create_task(_ask(async_client, conv, workspace, text="Change milk to eggs"))
    call_id = await _wait_for_pending()
    assert pathlib.Path(workspace, "notes.txt").read_text(encoding="utf-8").startswith("Buy milk."), "not written yet"
    assert (await async_client.post("/api/chat/approve", json={"call_id": call_id, "approved": True})).status_code == 200
    res = await task
    agent = [e["agent"] for e in _events(res.text) if "agent" in e]
    assert [a["event"] for a in agent] == ["tool_call", "approval_required", "tool_result", "done"]
    assert "+Buy eggs." in agent[1]["diff"] and agent[2]["ok"]
    assert pathlib.Path(workspace, "notes.txt").read_text(encoding="utf-8").startswith("Buy eggs.")

    listed = (await async_client.get(f"/api/chat/changes?conversation_id={conv}")).json()
    assert [(c["call_id"], c["can_undo"]) for c in listed] == [(call_id, True)]
    assert (await async_client.post("/api/chat/undo", json={"conversation_id": conv, "call_id": call_id})).status_code == 200
    assert pathlib.Path(workspace, "notes.txt").read_text(encoding="utf-8").startswith("Buy milk.")
    again = await async_client.post("/api/chat/undo", json={"conversation_id": conv, "call_id": call_id})
    assert again.status_code == 409 and "already undone" in again.json()["detail"]


@pytest.mark.asyncio
async def test_denied_change_over_the_api(async_client, scripted_engine, workspace):
    import pathlib
    conv = (await async_client.post("/api/conversations", json={"title": "e2", "agent_enabled": True, "workspace": workspace})).json()["id"]
    scripted_engine([{"calls": [("create_file", json.dumps({"path": "new.txt", "content": "hi"}))]}, {"text": "OK."}])
    task = asyncio.create_task(_ask(async_client, conv, workspace, text="make a file"))
    call_id = await _wait_for_pending()
    await async_client.post("/api/chat/approve", json={"call_id": call_id, "approved": False})
    await task
    assert not pathlib.Path(workspace, "new.txt").exists()
    assert (await async_client.get(f"/api/chat/changes?conversation_id={conv}")).json() == []


@pytest.mark.asyncio
async def test_approve_and_undo_edge_cases(async_client):
    assert (await async_client.post("/api/chat/approve", json={"call_id": "call_none", "approved": True})).status_code == 404
    conv = (await async_client.post("/api/conversations", json={"title": "no ws"})).json()["id"]
    assert (await async_client.get(f"/api/chat/changes?conversation_id={conv}")).json() == []
    assert (await async_client.post("/api/chat/undo", json={"conversation_id": conv, "call_id": "x"})).status_code == 400
    assert (await async_client.get("/api/chat/changes?conversation_id=missing")).status_code == 404
