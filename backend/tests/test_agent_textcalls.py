"""
M5 fixes from real use: tool calls written as plain text, repeated calls after a change, and
create_file in folders that don't exist yet. Found in a long agent chat where the model wrote
"<tool_call><function=list_files>..." as text (nothing ran, the tags showed in the chat), was
blocked from listing files again after creating some, and could not create "src/Config.h".
"""

import asyncio
import json

import pytest

from app.agent import changes, loop, tools
from app.agent.registry import ToolContext
from app.agent.textcalls import TagFilter, extract_text_calls
from app.agent.workspace import Workspace
from app.core.engine_base import ChatCompletionRequest

SCHEMAS = {
    "list_files": {"properties": {"path": {"type": "string"}}},
    "read_file": {"properties": {"path": {"type": "string"}}},
    "create_file": {"properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
    "count": {"properties": {"n": {"type": "integer"}, "flag": {"type": "boolean"}}},
}

XML = "<tool_call>\n<function=list_files>\n<parameter=path>\n.\n</parameter>\n</function>\n</tool_call>"


# --- parser -----------------------------------------------------------------------------------

def test_xml_call_is_parsed():
    calls, rest = extract_text_calls("Let me look.\n" + XML, SCHEMAS)
    assert rest == "Let me look."
    assert len(calls) == 1 and calls[0][1] == "list_files" and json.loads(calls[0][2]) == {"path": "."}
    assert calls[0][0].startswith("call_")


def test_multiline_parameter_keeps_inner_newlines():
    text = ("<tool_call>\n<function=create_file>\n<parameter=path>\nsrc/a.h\n</parameter>\n"
            "<parameter=content>\n#pragma once\nint x;\n</parameter>\n</function>\n</tool_call>")
    (call,) = extract_text_calls(text, SCHEMAS)[0]
    assert json.loads(call[2]) == {"path": "src/a.h", "content": "#pragma once\nint x;"}


def test_json_style_call_is_parsed():
    text = '<tool_call>{"name": "read_file", "arguments": {"path": "notes.txt"}}</tool_call>'
    (call,) = extract_text_calls(text, SCHEMAS)[0]
    assert call[1] == "read_file" and json.loads(call[2]) == {"path": "notes.txt"}


def test_numbers_and_booleans_follow_the_schema():
    text = "<tool_call><function=count><parameter=n>\n7\n</parameter><parameter=flag>true</parameter></function></tool_call>"
    (call,) = extract_text_calls(text, SCHEMAS)[0]
    assert json.loads(call[2]) == {"n": 7, "flag": True}


def test_several_calls_and_junk():
    calls, rest = extract_text_calls(XML + "\ntext\n" + XML.replace("list_files", "read_file"), SCHEMAS)
    assert [c[1] for c in calls] == ["list_files", "read_file"] and rest == "text"
    assert extract_text_calls("<tool_call>not a call</tool_call>", SCHEMAS) == ([], "")
    assert extract_text_calls("<tool_call><function=list_files><parameter=path>\n.", SCHEMAS) == ([], "")  # cut off
    assert extract_text_calls("no tags here", SCHEMAS) == ([], "no tags here")


# --- streaming filter -------------------------------------------------------------------------

@pytest.mark.parametrize("chunks", [
    [XML],
    list(XML),
    ["Sure. ", "<tool", "_call>", "\n<function=x>", "</function></tool_call>"],
])
def test_filter_hides_tags_however_they_are_split(chunks):
    f = TagFilter()
    shown = "".join(f.feed(c) for c in chunks) + f.flush()
    assert "<tool_call>" not in shown and "<function" not in shown
    assert f.saw_tag and f.held.startswith("<tool_call>")


def test_filter_passes_ordinary_text_and_lone_angle_brackets():
    f = TagFilter()
    shown = "".join(f.feed(c) for c in ["a < b ", "and <tool", "bar> is ", "fine <"]) + f.flush()
    assert shown == "a < b and <toolbar> is fine <" and not f.saw_tag


# --- loop -------------------------------------------------------------------------------------

@pytest.fixture
def ws_ctx(tmp_path):
    (tmp_path / "notes.txt").write_text("Buy milk.", encoding="utf-8")
    return ToolContext(workspace=Workspace(tmp_path), conversation_id="textcalls")


async def _run(engine, ctx, answer=None):
    events = []
    async for ev in loop.run_agent(engine, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ctx):
        events.append(ev)
        if ev.kind == "approval" and answer is not None:
            # Answer after the loop has registered its wait (it resumes right after this yield).
            asyncio.get_running_loop().call_soon(loop.resolve_approval, ev.call_id, answer)
    return events


@pytest.mark.asyncio
async def test_a_call_written_as_text_runs_and_the_tags_stay_out_of_the_chat(scripted_engine, ws_ctx):
    engine = scripted_engine([{"text": "Let me check. " + XML}, {"text": "There is one file."}])
    events = await _run(engine, ws_ctx)
    shown = "".join(e.text for e in events if e.kind == "content")
    assert "<tool_call>" not in shown and "<function" not in shown
    result = next(e for e in events if e.kind == "tool_result")
    assert result.ok and "notes.txt" in result.text
    turn = next(e for e in events if e.kind == "assistant_turn")
    assert turn.message.content == "Let me check." and turn.message.tool_calls[0].function.name == "list_files"
    assert events[-1].kind == "done" and engine.requests[1].messages[-1].role == "tool"


@pytest.mark.asyncio
async def test_a_text_call_to_an_unknown_tool_gets_an_error_result(scripted_engine, ws_ctx):
    events = await _run(scripted_engine([{"text": XML.replace("list_files", "delete_everything")}, {"text": "Sorry."}]), ws_ctx)
    result = next(e for e in events if e.kind == "tool_result")
    assert not result.ok and "unknown tool" in result.text


@pytest.mark.asyncio
async def test_malformed_tags_are_shown_not_swallowed(scripted_engine, ws_ctx):
    events = await _run(scripted_engine([{"text": "Hmm <tool_call>this is not a call</tool_call>"}]), ws_ctx)
    shown = "".join(e.text for e in events if e.kind == "content")
    assert "Hmm" in shown and "this is not a call" in shown
    assert not any(e.kind == "tool_call" for e in events)


@pytest.mark.asyncio
async def test_each_step_gets_room_for_a_whole_file(scripted_engine, ws_ctx):
    engine = scripted_engine([{"text": "hi"}])
    await _run(engine, ws_ctx)
    assert engine.requests[0].max_tokens >= loop.AGENT_MIN_TOKENS


# --- repeat guard -----------------------------------------------------------------------------

LIST = ("list_files", json.dumps({"path": "."}))
CREATE = ("create_file", json.dumps({"path": "a.txt", "content": "x"}))


@pytest.mark.asyncio
async def test_listing_again_after_a_change_is_not_a_repeat(scripted_engine, ws_ctx):
    engine = scripted_engine([{"calls": [LIST]}, {"calls": [CREATE]}, {"calls": [LIST]}, {"text": "Done."}])
    events = await _run(engine, ws_ctx, answer=True)
    lists = [e for e in events if e.kind == "tool_result" and e.name == "list_files"]
    assert [r.ok for r in lists] == [True, True] and "a.txt" in lists[1].text
    assert events[-1].reason is None


@pytest.mark.asyncio
async def test_listing_twice_with_no_change_still_stops(scripted_engine, ws_ctx):
    engine = scripted_engine([{"calls": [LIST]}] * 3 + [{"text": "ok"}])
    events = await _run(engine, ws_ctx)
    assert [e.ok for e in events if e.kind == "tool_result"] == [True, False, False]
    assert events[-1].reason == "repeated_calls"


@pytest.mark.asyncio
async def test_a_failing_call_can_be_retried_but_not_forever(scripted_engine, ws_ctx):
    bad = ("read_file", json.dumps({"path": "missing.txt"}))
    engine = scripted_engine([{"calls": [bad]}] * 6 + [{"text": "ok"}])
    events = await _run(engine, ws_ctx)
    results = [e for e in events if e.kind == "tool_result"]
    assert all("Not found" in r.text for r in results[:3]), "the real error comes back each time"
    assert events[-1].reason in ("repeated_calls", "tool_errors")


# --- folders ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_file_makes_the_folders_and_undo_removes_them(ws_ctx):
    root = ws_ctx.workspace.root
    p = tools._prepare_create("src/core/Config.h", "#pragma once\n", ws_ctx)
    assert "2 folders" in p.summary
    assert not (root / "src").exists(), "nothing is created before approval"
    assert "Created src" in await p.apply("call_dirs_1")
    assert (root / "src" / "core" / "Config.h").read_text(encoding="utf-8") == "#pragma once\n"
    assert changes.list_changes(ws_ctx.workspace)[0]["created_dirs"] == ["src", "src\\core"]

    changes.undo_change(ws_ctx.workspace, "call_dirs_1")
    assert not (root / "src").exists(), "the folders the change made are removed again"


@pytest.mark.asyncio
async def test_undo_keeps_a_folder_the_user_added_files_to(ws_ctx):
    root = ws_ctx.workspace.root
    await tools._prepare_create("src/a.h", "x", ws_ctx).apply("call_dirs_2")
    (root / "src" / "mine.txt").write_text("my own file", encoding="utf-8")
    changes.undo_change(ws_ctx.workspace, "call_dirs_2")
    assert not (root / "src" / "a.h").exists() and (root / "src" / "mine.txt").exists()


def test_create_file_refuses_a_path_through_a_file_and_too_deep_trees(ws_ctx):
    with pytest.raises(Exception, match="is a file"):
        tools._prepare_create("notes.txt/inner.txt", "x", ws_ctx)
    with pytest.raises(Exception, match="new folders"):
        tools._prepare_create("a/b/c/d/e/f/g/h.txt", "x", ws_ctx)
