"""
Terminal access for agent mode (run_command): off by default, every command approved, never
covered by "approve all", bounded by a timeout / output cap / Stop, and a few system-level
commands refused even when approved.
"""

import asyncio
import json
import os
import time

import pytest

from app.agent import loop, terminal
from app.agent.registry import ToolContext, ToolError, available_tools, get_tool, validate_arguments
from app.agent.workspace import Workspace
from app.core.engine_base import ChatCompletionRequest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="run_command targets Windows PowerShell")


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "sub").mkdir()
    return ToolContext(workspace=Workspace(tmp_path), conversation_id="term", terminal=True)


# --- who gets the tool ------------------------------------------------------------------------

def test_run_command_is_offered_only_when_terminal_is_on_and_a_workspace_is_open(tmp_path):
    ws = Workspace(tmp_path)
    names = lambda c: {t.name for t in available_tools(c)}  # noqa: E731
    assert "run_command" not in names(ToolContext(workspace=ws))
    assert "run_command" in names(ToolContext(workspace=ws, terminal=True))
    assert "run_command" not in names(ToolContext(workspace=None, terminal=True))
    assert get_tool("run_command", ToolContext(workspace=ws)) is None


# --- refusals ---------------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "Start-Process cmd -Verb RunAs", "runas /user:Administrator cmd", "shutdown /s /t 0", "Restart-Computer -Force",
    "diskpart", "Format-Volume -DriveLetter D", "format C:", "bcdedit /set x y", "Set-ExecutionPolicy Unrestricted",
    "reg add HKLM\\Software\\X /v a /d b", "reg delete HKCU\\Software\\X", "net user bob pass /add",
    "sc delete SomeService", "schtasks /create /tn x /tr y", "netsh advfirewall set allprofiles state off",
    "Remove-Item C:\\ -Recurse -Force", "rd /s /q D:\\", "del C:", "sudo rm -rf x",
])
def test_system_level_commands_are_refused(command):
    with pytest.raises(ToolError, match="Refused"):
        terminal.check_command(command)


@pytest.mark.parametrize("command", [
    "git status", "npm test", "python -m pytest -q", "dir", "Get-ChildItem -Recurse src",
    "Remove-Item build\\out.txt", "del C:\\Users\\me\\project\\temp.txt", "rd /s /q node_modules",
    "echo 'format is fine' > notes.txt", "cargo build --release",
])
def test_ordinary_commands_pass_the_check(command):
    terminal.check_command(command)


def test_empty_and_oversized_commands_are_refused():
    with pytest.raises(ToolError, match="Give the command"):
        terminal.check_command("   ")
    with pytest.raises(ToolError, match="too long"):
        terminal.check_command("x" * (terminal.MAX_COMMAND_CHARS + 1))


def test_secrets_are_removed_from_the_environment_the_command_gets():
    env = terminal.clean_environment({"PATH": "p", "OPENAI_API_KEY": "k", "GITHUB_TOKEN": "t", "DB_PASSWORD": "x",
                                      "MUSTANG_SESSION_TOKEN": "m", "LLAMA_API_KEY": "l", "HOME": "h"})
    assert env == {"PATH": "p", "HOME": "h"}


# --- running ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_command_runs_in_the_folder_and_returns_output_and_exit_code(ctx):
    res = await terminal.run_command("Write-Output hello; (Get-Location).Path", ctx.workspace.root / "sub", 30)
    assert res.exit_code == 0 and "hello" in res.output and res.output.strip().endswith("sub")
    assert res.text(30).startswith("Exit code: 0")
    bad = await terminal.run_command("exit 3", ctx.workspace.root, 30)
    assert bad.exit_code == 3 and bad.text(30).startswith("Exit code: 3")


@pytest.mark.asyncio
async def test_non_ascii_output_survives(ctx):
    res = await terminal.run_command("Write-Output 'café ₹ 日本'", ctx.workspace.root, 30)
    assert "café ₹ 日本" in res.output


@pytest.mark.asyncio
async def test_a_command_that_runs_too_long_is_stopped(ctx):
    t0 = time.time()
    res = await terminal.run_command("Start-Sleep -Seconds 60", ctx.workspace.root, 2)
    assert res.timed_out and time.time() - t0 < 20
    assert "did not finish within 2 s" in res.text(2)
    assert not terminal._running


@pytest.mark.asyncio
async def test_a_chatty_command_keeps_only_the_start_and_the_end(ctx):
    res = await terminal.run_command("1..4000 | ForEach-Object { 'line ' + $_ + ' ' + ('x' * 40) }", ctx.workspace.root, 60)
    assert res.exit_code == 0 and res.total_chars > 100_000
    assert len(res.output) < terminal.HEAD_CHARS + terminal.TAIL_CHARS + 200
    assert res.output.startswith("line 1 ") and "characters left out" in res.output and "line 4000" in res.output


@pytest.mark.asyncio
async def test_the_command_gets_no_secrets_and_no_input(ctx):
    env = {**terminal.clean_environment(), "MY_SECRET_KEY": "hunter2"}  # the caller passes the environment through the filter
    res = await terminal.run_command("Write-Output ('[' + $env:MY_SECRET_KEY + ']'); 'in:' + [Console]::In.ReadLine()",
                                     ctx.workspace.root, 30, env=env)
    assert "hunter2" not in res.output and "[]" in res.output, "the secret did not reach the command"
    assert "in:" in res.output  # reading input returns at once (no hang) because stdin is closed


@pytest.mark.asyncio
async def test_stop_ends_a_running_command_and_its_children(ctx):
    task = asyncio.create_task(terminal.run_command("Start-Sleep -Seconds 60", ctx.workspace.root, 120))
    for _ in range(100):
        if terminal._running:
            break
        await asyncio.sleep(0.05)
    t0 = time.time()
    assert terminal.kill_all() == 1
    res = await asyncio.wait_for(task, 20)
    assert res.stopped and not res.timed_out and time.time() - t0 < 15
    assert "stopped before it finished" in res.text(120)


# --- the tool: validation and approval --------------------------------------------------------

def test_arguments_are_validated(ctx):
    spec = get_tool("run_command", ctx)
    assert validate_arguments(spec, json.dumps({"command": "dir"}))["command"] == "dir"
    with pytest.raises(ToolError):
        validate_arguments(spec, json.dumps({}))
    with pytest.raises(ToolError, match="between 1 and 300"):
        validate_arguments(spec, json.dumps({"command": "dir", "timeout_seconds": 9999}))


@pytest.mark.asyncio
async def test_the_proposal_shows_the_exact_command_and_folder_and_is_always_asked(ctx):
    p = terminal._prepare_run({"command": "git status", "folder": "sub"}, ctx)
    assert p.always_ask and p.summary.startswith("Run in sub: git status")
    assert "$ git status" in p.diff and str(ctx.workspace.root / "sub") in p.diff


def test_a_folder_outside_the_workspace_or_a_file_is_refused(ctx):
    (ctx.workspace.root / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError):
        terminal._prepare_run({"command": "dir", "folder": ".."}, ctx)
    with pytest.raises(ToolError, match="not a folder"):
        terminal._prepare_run({"command": "dir", "folder": "a.txt"}, ctx)
    with pytest.raises(ToolError, match="Refused"):
        terminal._prepare_run({"command": "shutdown /s"}, ctx)


# --- the loop: approval is never skipped ------------------------------------------------------

RUN_A = ("run_command", json.dumps({"command": "Write-Output one"}))
RUN_B = ("run_command", json.dumps({"command": "Write-Output two"}))
CREATE = ("create_file", json.dumps({"path": "f.txt", "content": "x"}))


async def _run(engine, ctx, on_approval=None, **kw):
    events = []
    async for ev in loop.run_agent(engine, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ctx, **kw):
        events.append(ev)
        if ev.kind == "approval" and on_approval and not ev.extra.get("auto"):
            asyncio.get_running_loop().call_soon(on_approval, ev.call_id)
    return events


@pytest.mark.asyncio
async def test_a_command_waits_for_approval_even_with_auto_approve_on(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [CREATE]}, {"calls": [RUN_A]}, {"text": "Done."}])
    asked = []

    def answer(cid):
        if loop.resolve_approval(cid, True):
            asked.append(cid)

    events = await _run(engine, ctx, answer, auto_approve=True)
    approvals = [e for e in events if e.kind == "approval"]
    assert [(e.name, e.extra["auto"]) for e in approvals] == [("create_file", True), ("run_command", False)]
    assert len(asked) == 1, "only the command waited for the user"
    out = next(e for e in events if e.kind == "tool_result" and e.name == "run_command")
    assert out.ok and "one" in out.text and out.text.startswith("Exit code: 0")


@pytest.mark.asyncio
async def test_approve_all_on_a_command_does_not_switch_on_auto_approval_for_later_ones(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [RUN_A]}, {"calls": [RUN_B]}, {"text": "Done."}])
    asked = []

    def answer(cid):
        if loop.resolve_approval(cid, True, approve_all=True):
            asked.append(cid)

    events = await _run(engine, ctx, answer)
    assert len(asked) == 2, "the second command asked again"
    assert [e.extra["auto"] for e in events if e.kind == "approval"] == [False, False]
    assert not loop._approve_all


@pytest.mark.asyncio
async def test_a_denied_command_does_not_run(scripted_engine, ctx):
    marker = ctx.workspace.root / "ran.txt"
    cmd = ("run_command", json.dumps({"command": "Set-Content ran.txt yes"}))
    engine = scripted_engine([{"calls": [cmd]}, {"text": "OK."}])
    await _run(engine, ctx, lambda cid: loop.resolve_approval(cid, False))
    assert not marker.exists()


@pytest.mark.asyncio
async def test_an_approved_command_really_runs_and_without_terminal_the_call_is_unknown(scripted_engine, ctx):
    cmd = ("run_command", json.dumps({"command": "Set-Content ran.txt yes"}))
    await _run(scripted_engine([{"calls": [cmd]}, {"text": "Done."}]), ctx, lambda cid: loop.resolve_approval(cid, True))
    assert (ctx.workspace.root / "ran.txt").exists()

    off = ToolContext(workspace=ctx.workspace, conversation_id="off", terminal=False)
    events = await _run(scripted_engine([{"calls": [cmd]}, {"text": "No."}]), off)
    res = next(e for e in events if e.kind == "tool_result")
    assert not res.ok and "unknown tool" in res.text


@pytest.mark.asyncio
async def test_the_prompt_tells_the_model_about_the_terminal_only_when_it_is_on(scripted_engine, ctx):
    on = scripted_engine([{"text": "hi"}])
    await _run(on, ctx)
    assert "run_command" in on.requests[0].messages[0].content and "cannot delete files or run programs" not in on.requests[0].messages[0].content
    off = scripted_engine([{"text": "hi"}])
    await _run(off, ToolContext(workspace=ctx.workspace, conversation_id="x"))
    assert "cannot delete files or run programs" in off.requests[0].messages[0].content


# --- the chat is saved with the setting; the request flag reaches the agent ---------------------

@pytest.mark.asyncio
async def test_terminal_setting_is_saved_with_the_conversation(async_client, tmp_path):
    created = (await async_client.post("/api/conversations", json={"title": "t", "agent_enabled": True, "workspace": str(tmp_path)})).json()
    detail = (await async_client.get(f"/api/conversations/{created['id']}")).json()
    assert detail["terminal_enabled"] is False
    res = await async_client.patch(f"/api/conversations/{created['id']}", json={"terminal_enabled": True})
    assert res.status_code == 200
    assert (await async_client.get(f"/api/conversations/{created['id']}")).json()["terminal_enabled"] is True
    await async_client.patch(f"/api/conversations/{created['id']}", json={"title": "renamed"})
    assert (await async_client.get(f"/api/conversations/{created['id']}")).json()["terminal_enabled"] is True, "other updates keep it"


@pytest.mark.asyncio
async def test_the_request_flag_offers_the_tool_and_the_default_does_not(async_client, scripted_engine, tmp_path):
    eng = scripted_engine([{"text": "a"}])
    await async_client.post("/api/chat/completions", json={
        "conversation_id": "t1", "messages": [{"role": "user", "content": "hi"}], "agent": {"workspace": str(tmp_path)}})
    assert "run_command" not in [t.function.name for t in eng.requests[0].tools]
    eng2 = scripted_engine([{"text": "b"}])
    await async_client.post("/api/chat/completions", json={
        "conversation_id": "t2", "messages": [{"role": "user", "content": "hi"}],
        "agent": {"workspace": str(tmp_path), "terminal": True}})
    assert "run_command" in [t.function.name for t in eng2.requests[0].tools]


@pytest.mark.asyncio
async def test_a_programs_own_exit_code_is_reported_not_a_generic_1(ctx):
    root = ctx.workspace.root
    assert (await terminal.run_command("cmd /c exit 5", root, 30)).exit_code == 5
    assert (await terminal.run_command("cmd /c exit 0", root, 30)).exit_code == 0
    assert (await terminal.run_command("Get-Item does_not_exist_zzz", root, 30)).exit_code == 1, "a failed cmdlet is a failure"
    assert (await terminal.run_command("cmd /c exit 7  # a trailing comment", root, 30)).exit_code == 7
    assert (await terminal.run_command("cmd /c exit 4; Write-Output after", root, 30)).exit_code == 4, "a program's failure is not hidden by a later cmdlet"
