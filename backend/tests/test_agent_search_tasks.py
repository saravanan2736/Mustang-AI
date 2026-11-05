"""
Project-wide search (find_files, search_files filters, list_files depth) and the task list
(update_tasks): validation, storage with the chat, the agent's instructions, and survival of the
history being trimmed.
"""

import json

import pytest

from app.agent import loop, tasks, tools
from app.agent.registry import ToolContext, ToolError, get_tool, validate_arguments
from app.agent.workspace import Workspace
from app.core.engine_base import ChatCompletionRequest


@pytest.fixture
def ctx(tmp_path):
    files = {
        "README.md": "# Project\nbuild with make\n",
        "src/main.cpp": "int main() { return run(); }\n",
        "src/util/Config.h": "#pragma once\nint run();\n",
        "src/util/config_test.cpp": "// tests for run()\n",
        "docs/guide.md": "Use run() to start.\n",
        "node_modules/dep/index.js": "run();\n",
        ".hidden/secret.txt": "run();\n",
        "src/__pycache__/x.pyc": "run",
    }
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return ToolContext(workspace=Workspace(tmp_path), conversation_id="search-tasks")


# --- find_files -------------------------------------------------------------------------------

def test_find_files_by_name_anywhere_case_insensitive(ctx):
    out = tools._find_files("config*", ".", ctx)
    assert "src/util/Config.h" in out and "src/util/config_test.cpp" in out and "main.cpp" not in out
    assert out.startswith("2 files match")


def test_find_files_with_a_path_pattern_and_double_star(ctx):
    assert "src/main.cpp" in tools._find_files("src/**/*.cpp", ".", ctx)
    assert "src/util/config_test.cpp" in tools._find_files("src/**/*.cpp", ".", ctx)
    assert "main.cpp" not in tools._find_files("src/util/*.cpp", ".", ctx), "* stays inside one folder"
    assert "README.md" in tools._find_files("*.MD", ".", ctx)


def test_find_files_skips_dependencies_hidden_folders_and_caches(ctx):
    out = tools._find_files("*", ".", ctx)
    assert "node_modules" not in out and ".hidden" not in out and "__pycache__" not in out and ".mustang-backups" not in out
    assert "src/main.cpp" in out


def test_find_files_in_a_subfolder_and_with_no_match(ctx):
    assert tools._find_files("*.h", "src", ctx).count("\n") == 1
    assert "No files match" in tools._find_files("*.rs", ".", ctx)
    with pytest.raises(ToolError, match="Not a folder"):
        tools._find_files("*", "README.md", ctx)
    with pytest.raises(ToolError, match="inside the workspace"):
        tools._find_files("*", "..", ctx)


def test_find_files_stops_at_the_limit(tmp_path):
    for i in range(tools.MAX_FIND_RESULTS + 30):
        (tmp_path / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    out = tools._find_files("*.txt", ".", ToolContext(workspace=Workspace(tmp_path)))
    assert f"Stopped at {tools.MAX_FIND_RESULTS} files" in out


# --- search_files filters ---------------------------------------------------------------------

def test_search_files_in_only_some_files(ctx):
    out = tools._search_text("run", ".", ctx, file_pattern="*.h")
    assert "src/util/Config.h:2" in out and "main.cpp" not in out and "guide.md" not in out
    out = tools._search_text("run", ".", ctx, file_pattern="src/**/*.cpp")
    assert "src/main.cpp:1" in out and "config_test.cpp:1" in out and "Config.h" not in out


def test_search_files_regex(ctx):
    out = tools._search_text(r"int\s+\w+\(", ".", ctx, use_regex=True)
    assert "src/main.cpp:1" in out and "Config.h:2" in out
    with pytest.raises(ToolError, match="valid regular expression"):
        tools._search_text("(unclosed", ".", ctx, use_regex=True)
    assert "No matches" in tools._search_text("no.such.thing", ".", ctx, use_regex=True)


def test_search_files_skips_dependencies_and_hidden_folders(ctx):
    out = tools._search_text("run", ".", ctx)
    assert "node_modules" not in out and ".hidden" not in out


def test_search_arguments_are_validated(ctx):
    spec = get_tool("search_files", ctx)
    args = validate_arguments(spec, json.dumps({"query": "x", "file_pattern": "*.py", "regex": True}))
    assert args["regex"] is True
    with pytest.raises(ToolError, match="boolean"):
        validate_arguments(spec, json.dumps({"query": "x", "regex": "yes"}))


# --- list_files depth -------------------------------------------------------------------------

def test_list_files_depth_one_is_unchanged_and_deeper_shows_a_tree(ctx):
    flat = tools._list_files(".", ctx)
    assert "src\\  (folder)" in flat and "Config.h" not in flat
    tree = tools._list_files(".", ctx, depth=3)
    assert "3 levels deep" in tree
    assert "  util\\  (folder)" in tree.replace("\n", "\n") and "    Config.h" in tree
    assert "node_modules" not in tree and "secret.txt" not in tree, "dependencies skipped, hidden folders not opened"


def test_list_files_depth_is_capped_and_validated(ctx):
    assert "4 levels deep" in tools._list_files(".", ctx, depth=99)
    spec = get_tool("list_files", ctx)
    with pytest.raises(ToolError, match="between 1 and 4"):
        validate_arguments(spec, json.dumps({"path": ".", "depth": 9}))


# --- update_tasks -----------------------------------------------------------------------------

TASKS = [{"text": "Read the code", "status": "done"}, {"text": "Write tests", "status": "in_progress"}, {"text": "Fix bugs", "status": "pending"}]


def test_task_arguments_are_validated_as_an_array_of_objects(ctx):
    spec = get_tool("update_tasks", ctx)
    assert validate_arguments(spec, json.dumps({"tasks": TASKS}))["tasks"] == TASKS
    for bad, msg in [
        ({"tasks": "do it"}, "must be a array"),
        ({"tasks": [{"text": "x"}]}, "missing 'status'"),
        ({"tasks": [{"text": "x", "status": "soon"}]}, "must be one of"),
        ({"tasks": [{"text": "x", "status": "done", "extra": 1}]}, "Unknown field"),
        ({"tasks": [{"text": 5, "status": "done"}]}, "must be a string"),
        ({"tasks": ["just text"]}, "must be a object"),
        ({"tasks": [{"text": "x", "status": "done"}] * 21}, "too many items"),
    ]:
        with pytest.raises(ToolError, match=msg):
            validate_arguments(spec, json.dumps(bad))


def test_only_one_task_in_progress_and_text_is_cleaned():
    with pytest.raises(ToolError, match="Only one task"):
        tasks.normalise([{"text": "a", "status": "in_progress"}, {"text": "b", "status": "in_progress"}])
    with pytest.raises(ToolError, match="has no text"):
        tasks.normalise([{"text": "   ", "status": "pending"}])
    out = tasks.normalise([{"text": "  two   spaces\nand a newline  ", "status": "pending"}])
    assert out == [{"text": "two spaces and a newline", "status": "pending"}]


@pytest.mark.asyncio
async def test_update_tasks_stores_the_list_with_the_chat(async_client, ctx):
    created = (await async_client.post("/api/conversations", json={"title": "t"})).json()
    ctx.conversation_id = created["id"]
    result = await tasks._update_tasks({"tasks": TASKS}, ctx)
    assert result.startswith("Task list saved: 1 of 3 done.") and "Next: Write tests" in result
    assert ctx.tasks == TASKS
    assert (await async_client.get(f"/api/conversations/{created['id']}")).json()["tasks"] == TASKS
    assert await tasks.load_tasks(created["id"]) == TASKS
    assert (await tasks._update_tasks({"tasks": []}, ctx)) == "Task list cleared."
    assert (await async_client.get(f"/api/conversations/{created['id']}")).json()["tasks"] == []


def test_the_task_list_is_offered_without_a_workspace():
    assert get_tool("update_tasks", ToolContext()) is not None


@pytest.mark.asyncio
async def test_the_task_list_is_part_of_the_agents_instructions(scripted_engine, ctx):
    ctx.tasks = TASKS
    eng = scripted_engine([{"text": "ok"}])
    async for _ in loop.run_agent(eng, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ctx):
        pass
    system = eng.requests[0].messages[0].content
    assert "[x] Read the code" in system and "[~] Write tests" in system and "[ ] Fix bugs" in system
    assert "update_tasks" in system
    quiet = scripted_engine([{"text": "ok"}])
    async for _ in loop.run_agent(quiet, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ToolContext(workspace=ctx.workspace)):
        pass
    assert "Your task list for this chat" not in quiet.requests[0].messages[0].content


@pytest.mark.asyncio
async def test_a_model_updating_its_list_in_the_loop_changes_what_it_sees_next(scripted_engine, ctx):
    call = ("update_tasks", json.dumps({"tasks": TASKS}))
    eng = scripted_engine([{"calls": [call]}, {"text": "Done."}])
    events = []
    async for ev in loop.run_agent(eng, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}]), ctx):
        events.append(ev)
    assert next(e for e in events if e.kind == "tool_result").ok
    assert "[~] Write tests" in eng.requests[1].messages[0].content, "the next step sees the new list"
