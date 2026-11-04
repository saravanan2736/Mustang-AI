"""
Found in the installed app: an agent chat of ~43,000 tokens was sent whole to a model with a
4k-32k window, so the model lost the thread or stopped answering. fit_messages keeps what is
sent inside the window.
"""

from app.core.history import NOTE, estimate_tokens, fit_messages


def _chat(turns, tool_chars=0):
    msgs = [{"role": "system", "content": "You are helpful."}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * 200})
        if tool_chars:
            msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "T" * tool_chars})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * 600})
    return msgs


def _tokens(msgs):
    return sum(estimate_tokens(m.get("content") or "") + 8 for m in msgs)


def test_a_short_chat_is_sent_unchanged():
    msgs = _chat(3)
    out, dropped = fit_messages(msgs, 4096, 512)
    assert out == msgs and dropped == 0


def test_a_long_chat_is_trimmed_to_the_window_and_keeps_the_latest_turn():
    msgs = _chat(60, tool_chars=4000)
    out, dropped = fit_messages(msgs, 4096, 1024)
    assert dropped > 0 and _tokens(out) <= 4096 - 1024
    assert out[0]["content"].startswith("You are helpful.") and NOTE in out[0]["content"]
    assert out[-1] == msgs[-1] and out[-4]["content"].startswith("question 59")


def test_the_chat_never_starts_with_an_orphaned_tool_result():
    out, _ = fit_messages(_chat(40, tool_chars=3000), 4096, 1024)
    first = next(m for m in out if m["role"] not in ("system",))
    assert first["role"] == "user"
    ids = {tc["id"] for m in out for tc in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in ids for m in out if m["role"] == "tool")


def test_old_tool_output_is_shortened_before_anything_is_dropped():
    msgs = _chat(6, tool_chars=6000)
    out, dropped = fit_messages(msgs, 12288, 1024)
    assert dropped == 0
    old = [m for m in out[:-4] if m["role"] == "tool"]
    assert old and all(len(m["content"]) < 1200 for m in old)
    assert len(next(m for m in reversed(out) if m["role"] == "tool")["content"]) == 6000


def test_a_current_turn_that_alone_is_too_big_is_shortened_not_refused():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "read it"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "Z" * 60000}]
    out, _ = fit_messages(msgs, 4096, 1024)
    assert _tokens(out) <= 4096 and out[-1]["content"].startswith("Z") and "left out" in out[-1]["content"]


def test_tool_definitions_and_reply_room_are_counted():
    tools = [{"type": "function", "function": {"name": "t", "description": "d" * 6000, "parameters": {}}}]
    msgs = _chat(12)
    without, _ = fit_messages(msgs, 4096, 1024)
    with_tools, _ = fit_messages(msgs, 4096, 1024, tools)
    assert len(with_tools) < len(without)


def test_the_input_is_not_modified():
    msgs = _chat(30, tool_chars=3000)
    snapshot = [dict(m) for m in msgs]
    fit_messages(msgs, 4096, 1024)
    assert msgs == snapshot


def test_a_trimmed_chat_still_has_a_single_system_message_at_the_start():
    """Qwen and Gemma templates reject a second system message ("System message must be at the beginning")."""
    for with_system in (True, False):
        msgs = _chat(60, tool_chars=4000)
        if not with_system:
            msgs = msgs[1:]
        out, dropped = fit_messages(msgs, 4096, 1024)
        assert dropped > 0
        assert [i for i, m in enumerate(out) if m["role"] == "system"] == [0]
        assert NOTE in out[0]["content"]
        if with_system:
            assert out[0]["content"].startswith("You are helpful.")
