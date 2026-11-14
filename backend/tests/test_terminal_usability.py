"""
Found by running realistic coding tasks on the 9B model: bash-style `&&` chains that Windows PowerShell 5.1
rejects, programs in the current folder not found by name, and the same failing command run seven times
in a row (every run needs the user's approval, so a loop is a loop of clicks).
"""

import asyncio
import json
import os

import pytest

from app.agent import loop, terminal
from app.agent.registry import ToolContext
from app.agent.workspace import Workspace
from app.core.engine_base import ChatCompletionRequest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="run_command targets Windows PowerShell")


# --- && and || ----------------------------------------------------------------------------------

@pytest.mark.parametrize("command, expected", [
    ("cd src && make", "cd src; if ($?) { make }"),
    ("a && b && c", "a; if ($?) { b; if ($?) { c } }"),
    ("a || b", "a; if (-not $?) { b }"),
    ("a || b || c", "a; if (-not $?) { b; if (-not $?) { c } }"),
    ("git add . && git commit -m 'x && y'", "git add .; if ($?) { git commit -m 'x && y' }"),
])
def test_chains_become_powershell(command, expected):
    assert terminal.powershell_form(command) == expected


@pytest.mark.parametrize("command", [
    "echo hello", "echo 'a && b'", 'echo "a || b"', "a | b", "a; b", "a && b || c", "a || b && c",
    "&& a", "a &&", "echo `\"&&`\"",
])
def test_everything_else_is_left_alone(command):
    assert terminal.powershell_form(command) == command


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "sub").mkdir()
    return tmp_path


@pytest.mark.asyncio
async def test_a_chain_runs_the_second_part_only_if_the_first_worked(ws):
    ok = await terminal.run_command("cd sub && Write-Output reached", ws, 30)
    assert ok.exit_code == 0 and "reached" in ok.output
    bad = await terminal.run_command("cd does_not_exist && Write-Output reached", ws, 30)
    assert "reached" not in bad.output and bad.exit_code != 0, "the second part must not run after a failure"
    native = await terminal.run_command("cmd /c exit 3 && Write-Output reached", ws, 30)
    assert "reached" not in native.output and native.exit_code == 3


@pytest.mark.asyncio
async def test_or_runs_the_second_part_only_after_a_failure(ws):
    rec = await terminal.run_command("cmd /c exit 1 || Write-Output recovered", ws, 30)
    assert "recovered" in rec.output
    skip = await terminal.run_command("cmd /c exit 0 || Write-Output recovered", ws, 30)
    assert "recovered" not in skip.output and skip.exit_code == 0


@pytest.mark.asyncio
async def test_a_longer_chain_stops_at_the_first_failure(ws):
    res = await terminal.run_command("Write-Output one && cmd /c exit 2 && Write-Output three", ws, 30)
    assert "one" in res.output and "three" not in res.output and res.exit_code == 2


def test_the_approval_card_shows_what_will_really_run(ws):
    ctx = ToolContext(workspace=Workspace(ws), terminal=True)
    p = terminal._prepare_run({"command": "cd sub && dir"}, ctx)
    assert "$ cd sub && dir" in p.diff and "cd sub; if ($?) { dir }" in p.diff
    plain = terminal._prepare_run({"command": "dir"}, ctx)
    assert "will run as" not in plain.diff


# --- programs in the current folder -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_program_in_the_current_folder_runs_by_name_but_never_shadows_a_real_tool(ws):
    (ws / "hello.cmd").write_text("@echo off\necho from-the-folder\n", encoding="ascii")
    res = await terminal.run_command("hello", ws, 30)
    assert res.exit_code == 0 and "from-the-folder" in res.output
    # a file named like a real tool does not win: the folder is last on the PATH
    (ws / "where.cmd").write_text("@echo off\necho HIJACKED\n", encoding="ascii")
    real = await terminal.run_command("where.exe where", ws, 30)
    assert "HIJACKED" not in real.output
    assert "hijacked" not in (await terminal.run_command("cmd /c where where", ws, 30)).output.lower()


# --- the same command again ------------------------------------------------------------------------

BAD = ("run_command", json.dumps({"command": "Write-Output same; cmd /c exit 1"}))
OTHER = ("run_command", json.dumps({"command": "Write-Output different"}))
EDIT = ("create_file", json.dumps({"path": "fix.txt", "content": "fixed"}))


async def _run(engine, ctx):
    events = []
    async for ev in loop.run_agent(engine, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ctx):
        events.append(ev)
        if ev.kind == "approval" and not ev.extra.get("auto"):
            asyncio.get_running_loop().call_soon(loop.resolve_approval, ev.call_id, True)
    return events


@pytest.fixture
def ctx(ws):
    return ToolContext(workspace=Workspace(ws), conversation_id="usability", terminal=True)


@pytest.mark.asyncio
async def test_the_same_command_with_no_change_in_between_is_refused_not_asked_again(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [BAD]}, {"calls": [BAD]}, {"calls": [BAD]}, {"text": "I give up."}])
    events = await _run(engine, ctx)
    approvals = [e for e in events if e.kind == "approval"]
    assert len(approvals) == 1, "the user is asked once, not once per repeat"
    results = [e for e in events if e.kind == "tool_result"]
    assert results[0].ok and "Exit code: 1" in results[0].text
    assert not results[1].ok and "already ran in this folder and nothing in the workspace has changed" in results[1].text
    assert events[-1].reason == "repeated_calls"


@pytest.mark.asyncio
async def test_a_different_command_or_a_change_in_between_allows_running_it_again(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [BAD]}, {"calls": [OTHER]}, {"calls": [BAD]}, {"calls": [EDIT]}, {"calls": [BAD]}, {"text": "Done."}])
    events = await _run(engine, ctx)
    ran = [e for e in events if e.kind == "tool_result" and e.name == "run_command"]
    assert [r.ok for r in ran] == [True, True, True, True], "no repeat refusals here"
    assert len([e for e in events if e.kind == "approval" and e.name == "run_command"]) == 4


@pytest.mark.asyncio
async def test_the_prompt_tells_the_model_to_check_the_readme_before_running_tests(scripted_engine, ctx):
    engine = scripted_engine([{"text": "hi"}])
    await _run(engine, ctx)
    assert "check the README" in engine.requests[0].messages[0].content
