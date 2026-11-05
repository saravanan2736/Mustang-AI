"""
Agent mode improvements from the review of a real chat: approve all changes, create_file that
replaces an existing file, and read_file in parts.
"""

import asyncio
import json

import pytest

from app.agent import changes, loop, tools
from app.agent.registry import ToolContext, ToolError, validate_arguments, get_tool
from app.agent.workspace import Workspace
from app.core.engine_base import ChatCompletionRequest


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "notes.txt").write_text("one\r\ntwo\r\nthree\r\n", encoding="utf-8", newline="")
    (tmp_path / "long.txt").write_text("".join(f"line {i:04d} " + "x" * 90 + "\n" for i in range(1, 301)), encoding="utf-8")
    return ToolContext(workspace=Workspace(tmp_path), conversation_id="improve")


# --- read_file in parts -----------------------------------------------------------------------

def test_a_long_file_comes_back_in_parts_with_a_pointer_to_the_next_part(ctx):
    first = tools._read_file("long.txt", ctx)
    assert len(first) < tools.MAX_READ_CHARS + 300
    assert first.startswith("line 0001") and "[Lines 1-" in first and "of 300" in first
    nxt = int(first.rsplit("start_line=", 1)[1].split(".")[0])
    second = tools._read_file("long.txt", ctx, start_line=nxt)
    assert second.startswith(f"line {nxt:04d}")


def test_a_line_range_returns_just_those_lines(ctx):
    out = tools._read_file("long.txt", ctx, start_line=10, end_line=12)
    assert out.splitlines()[0].startswith("line 0010") and "line 0012" in out and "line 0013" not in out
    assert "[Lines 10-12 of 300" in out


def test_bad_ranges_are_refused_and_the_whole_short_file_is_unchanged(ctx):
    with pytest.raises(ToolError, match="only 3 lines"):
        tools._read_file("notes.txt", ctx, start_line=9)
    with pytest.raises(ToolError, match="before start_line"):
        tools._read_file("notes.txt", ctx, start_line=3, end_line=2)
    assert tools._read_file("notes.txt", ctx) == "one\r\ntwo\r\nthree\r\n"


def test_the_range_arguments_are_validated_as_integers(ctx):
    spec = get_tool("read_file", ctx)
    assert validate_arguments(spec, json.dumps({"path": "long.txt", "start_line": 5}))["start_line"] == 5
    with pytest.raises(ToolError):
        validate_arguments(spec, json.dumps({"path": "long.txt", "start_line": "five"}))


# --- create_file replaces ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_on_an_existing_file_replaces_it_after_approval_and_undo_restores_it(ctx):
    root = ctx.workspace.root
    p = tools._prepare_create("notes.txt", "one\ntwo\nthree\nfour\n", ctx)
    assert p.summary == "Replace notes.txt (4 lines)" and "+four" in p.diff
    assert (root / "notes.txt").read_bytes() == b"one\r\ntwo\r\nthree\r\n", "nothing is written before approval"
    assert (await p.apply("call_replace_1")).startswith("Replaced notes.txt")
    assert (root / "notes.txt").read_bytes() == b"one\r\ntwo\r\nthree\r\nfour\r\n", "the file keeps its CRLF line endings"
    assert changes.list_changes(ctx.workspace)[0]["can_undo"]
    changes.undo_change(ctx.workspace, "call_replace_1")
    assert (root / "notes.txt").read_bytes() == b"one\r\ntwo\r\nthree\r\n"


def test_replace_refusals(ctx):
    (ctx.workspace.root / "img.dat").write_bytes(b"\x00\x01binary")
    (ctx.workspace.root / "d").mkdir()
    with pytest.raises(ToolError, match="not a UTF-8 text file"):
        tools._prepare_create("img.dat", "x", ctx)
    with pytest.raises(ToolError, match="is a folder"):
        tools._prepare_create("d", "x", ctx)
    with pytest.raises(ToolError, match="program and script"):
        tools._prepare_create("notes.exe", "x", ctx)


# --- approve all ------------------------------------------------------------------------------

CREATE_A = ("create_file", json.dumps({"path": "a.txt", "content": "a"}))
CREATE_B = ("create_file", json.dumps({"path": "b.txt", "content": "b"}))
CREATE_C = ("create_file", json.dumps({"path": "c.txt", "content": "c"}))


async def _run(engine, ctx, on_approval=None, **kw):
    events = []
    async for ev in loop.run_agent(engine, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ctx, **kw):
        events.append(ev)
        if ev.kind == "approval" and on_approval:
            asyncio.get_running_loop().call_soon(on_approval, ev.call_id)
    return events


@pytest.mark.asyncio
async def test_approve_all_answers_the_rest_of_the_reply_without_asking(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [CREATE_A]}, {"calls": [CREATE_B]}, {"calls": [CREATE_C]}, {"text": "Done."}])
    asked = []

    def answer(call_id):
        if loop.resolve_approval(call_id, True, approve_all=True):  # False for changes that were not waiting
            asked.append(call_id)

    events = await _run(engine, ctx, answer)
    root = ctx.workspace.root
    assert len(asked) == 1, "only the first change waited for the user"
    assert all((root / n).exists() for n in ("a.txt", "b.txt", "c.txt"))
    approvals = [e for e in events if e.kind == "approval"]
    assert [e.extra["auto"] for e in approvals] == [False, True, True]
    assert events[-1].kind == "done" and not loop._approve_all


@pytest.mark.asyncio
async def test_auto_approve_from_the_start_never_waits_and_still_applies_the_limits(scripted_engine, ctx):
    bad = ("create_file", json.dumps({"path": "run.bat", "content": "format C:"}))
    engine = scripted_engine([{"calls": [CREATE_A]}, {"calls": [bad]}, {"text": "Done."}])
    events = await _run(engine, ctx, auto_approve=True)
    root = ctx.workspace.root
    assert (root / "a.txt").exists() and not (root / "run.bat").exists()
    results = [e for e in events if e.kind == "tool_result"]
    assert results[0].ok and not results[1].ok and "program and script" in results[1].text
    assert not any(e.kind == "approval" and not e.extra["auto"] for e in events)


@pytest.mark.asyncio
async def test_approving_only_one_change_does_not_switch_on_approve_all(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [CREATE_A]}, {"calls": [CREATE_B]}, {"text": "Done."}])
    asked = []

    def answer(call_id):
        if loop.resolve_approval(call_id, True):
            asked.append(call_id)

    await _run(engine, ctx, answer)
    assert len(asked) == 2


# --- thinking ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_models_thinking_is_streamed_but_not_stored_or_sent_back(scripted_engine, ctx):
    engine = scripted_engine([{"thinking": "Let me check the notes.", "calls": [("list_files", json.dumps({"path": "."}))]},
                              {"thinking": "Two files.", "text": "There are two files."}])
    events = await _run(engine, ctx)
    thoughts = [e.text for e in events if e.kind == "reasoning"]
    assert thoughts == ["Let me check the notes.", "Two files."]
    turn = next(e for e in events if e.kind == "assistant_turn")
    assert "Let me check" not in turn.message.content
    assert not any("Let me check" in (m.content or "") for m in engine.requests[1].messages)


@pytest.mark.asyncio
async def test_the_engine_passes_reasoning_content_through(monkeypatch):
    import httpx
    from pathlib import Path

    from app.core.engine_base import EngineConfig, EngineState
    from app.core.llama_engine import LlamaServerEngine

    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in [
        {"id": "1", "choices": [{"delta": {"reasoning_content": "hmm "}}]},
        {"id": "1", "choices": [{"delta": {"content": "Hi"}}]},
        {"id": "1", "choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]) + "data: [DONE]\n\n"
    e = LlamaServerEngine(binary_path=Path("llama-server.exe"))
    e._config = EngineConfig(model_path=Path("m.gguf"))
    e._state = EngineState.READY
    e._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sse)))
    chunks = [c async for c in e.stream_chat(ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}]))]
    assert [c.delta_reasoning for c in chunks if c.delta_reasoning] == ["hmm "]
    assert "".join(c.delta_content for c in chunks) == "Hi"


# --- found with the 9B model: raw tags in the last turn, probing after a denial ---------------

TAG_CALL = "<tool_call>\n<function=read_file>\n<parameter=path>\nx.txt\n</parameter>\n</function>\n</tool_call>"


@pytest.mark.asyncio
async def test_tool_call_tags_in_the_last_turn_never_reach_the_user(scripted_engine, ctx):
    bad = ("read_file", json.dumps({"path": "missing.txt"}))
    engine = scripted_engine([{"calls": [bad]}, {"calls": [bad]}, {"text": TAG_CALL}])
    events = await _run(engine, ctx)
    shown = "".join(e.text for e in events if e.kind == "content")
    assert "<tool_call>" not in shown and "<function" not in shown
    assert shown.strip() == loop.NO_ANSWER_AFTER_TOOLS
    assert events[-1].reason == "tool_errors"


@pytest.mark.asyncio
async def test_text_before_tags_in_the_last_turn_is_kept(scripted_engine, ctx):
    bad = ("read_file", json.dumps({"path": "missing.txt"}))
    engine = scripted_engine([{"calls": [bad]}, {"calls": [bad]}, {"text": "I could not read it. " + TAG_CALL}])
    shown = "".join(e.text for e in await _run(engine, ctx) if e.kind == "content")
    assert shown.strip() == "I could not read it." and "<tool_call>" not in shown


@pytest.mark.asyncio
async def test_a_denied_change_ends_the_tool_steps_so_the_model_answers_at_once(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [CREATE_A]}, {"text": "Understood, I did not create it."}])
    events = await _run(engine, ctx, lambda cid: loop.resolve_approval(cid, False))
    assert [e.name for e in events if e.kind == "tool_call"] == ["create_file"], "no probing after a denial"
    assert engine.requests[1].tool_choice == "none"
    assert not (ctx.workspace.root / "a.txt").exists()
    assert events[-1].reason == "denied" and "Understood" in "".join(e.text for e in events if e.kind == "content")


@pytest.mark.asyncio
async def test_after_a_denial_a_tag_only_last_turn_becomes_a_plain_acknowledgement(scripted_engine, ctx):
    engine = scripted_engine([{"calls": [CREATE_A]}, {"text": TAG_CALL}])
    events = await _run(engine, ctx, lambda cid: loop.resolve_approval(cid, False))
    shown = "".join(e.text for e in events if e.kind == "content")
    assert shown.strip() == loop.NO_ANSWER_AFTER_DENIAL and "<tool_call>" not in shown
