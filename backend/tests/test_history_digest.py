"""
When old messages are left out to fit the window, the model gets a short summary of them: the user's
requests (the latest detailed one in full) and the work done. Found in the installed app: a 4k window
dropped the user's task and the agent kept reading files, because only "retry" was left.
"""

import json

from app.core.history import build_digest, fit_messages


def _agent_chat():
    """A project chat: a detailed request, files written, a command run, then a string of short follow-ups."""
    msgs = [{"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Build me a small C++ program that reads a CSV file and prints the column averages."}]
    calls = [("create_file", {"path": "src/main.cpp"}, "Created src\\main.cpp (40 lines). The previous version is kept for Undo."),
             ("create_file", {"path": "Makefile"}, "Created Makefile (6 lines)."),
             ("run_command", {"command": "mingw32-make"}, "Exit code: 0\ng++ -o app src/main.cpp"),
             ("edit_file", {"path": "src/main.cpp"}, "The user denied this change. Nothing was written."),
             ("read_file", {"path": "src/main.cpp"}, "int main() {}\n" * 300)]
    for i, (name, args, result) in enumerate(calls):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": result})
    msgs.append({"role": "assistant", "content": "Done, the program is built."})
    for i in range(30):
        msgs.append({"role": "user", "content": f"continue {i}"})
        msgs.append({"role": "assistant", "content": "Working on it. " + "y" * 500})
    msgs.append({"role": "user", "content": "retry"})
    return msgs


def test_a_trimmed_chat_keeps_the_task_and_the_work_done_in_the_system_message():
    out, dropped = fit_messages(_agent_chat(), 2048, 512)
    assert dropped > 0
    system = out[0]["content"]
    assert [m["role"] for m in out].count("system") == 1
    assert "reads a CSV file and prints the column averages" in system, "the task survives although only 'retry' is left"
    assert "Created src\\main.cpp (40 lines)." in system and "Created Makefile" in system
    assert "Undo" not in system.split("Work done")[1], "the Undo remark is dropped from the summary"
    assert "ran `mingw32-make` -> Exit code: 0" in system
    assert "the user denied edit_file src/main.cpp" in system
    assert "int main()" not in system, "reads are noise and are not summarised"
    assert out[-1]["content"] == "retry"


def test_the_digest_is_short_and_has_no_empty_parts():
    assert build_digest([]) == "" and build_digest([{"role": "assistant", "content": "hi"}]) == ""
    digest = build_digest(_agent_chat())
    assert len(digest) < 2600
    assert digest.count("The user's latest detailed request") == 1


def test_short_follow_ups_are_listed_but_do_not_replace_the_detailed_request():
    msgs = [{"role": "user", "content": "Please refactor the parser to use a state machine and add tests for it."},
            {"role": "user", "content": "ok"}, {"role": "user", "content": "go on"}]
    digest = build_digest(msgs)
    assert "refactor the parser" in digest and "ok | go on" in digest


def test_only_the_last_dozen_actions_are_kept_and_commands_are_clipped():
    msgs = []
    for i in range(30):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": "run_command", "arguments": json.dumps({"command": f"echo {i} " + "z" * 300})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "Exit code: 0\nout"})
    digest = build_digest(msgs)
    assert digest.count("- ran `") == 12 and "echo 29 " in digest and "echo 0 " not in digest
    assert all(len(line) < 200 for line in digest.splitlines())


def test_the_digest_is_not_added_when_nothing_is_dropped():
    out, dropped = fit_messages(_agent_chat()[:6], 32768, 512)
    assert dropped == 0 and out[0]["content"] == "You are helpful."


def test_a_malformed_tool_call_does_not_break_the_summary():
    msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "x", "function": {"name": "create_file", "arguments": "{not json"}}]},
            {"role": "tool", "tool_call_id": "x", "content": "Error: bad arguments"},
            {"role": "tool", "tool_call_id": "nope", "content": "orphan"}]
    assert build_digest(msgs) == ""
