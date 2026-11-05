"""
Keep a chat inside the model's context window.

The whole conversation used to be sent on every request. Once it outgrew the window (agent chats
get there quickly: one file read can be thousands of tokens) the engine either refused the request
or silently dropped the start of it, so the model lost the thread or stopped replying. This trims
what is sent, never what is stored: old tool output is shortened first, then the oldest exchanges
are left out, and the model is told that earlier messages were left out.
"""

import json
import logging

logger = logging.getLogger("mustang.history")

CHARS_PER_TOKEN = 3  # conservative: code and non-English text pack fewer characters per token
PER_MESSAGE_TOKENS = 8
SAFETY_TOKENS = 128
TRIM_TO = 0.6  # once over the limit, keep this share of it
OLD_TOOL_RESULT_CHARS = 800  # tool output from earlier turns is cut to this
NOTE = ("Note: the earliest messages of this chat were left out to fit the context window. "
        "If something the user refers to is missing, say so and ask them to repeat it.")


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def _msg_tokens(m: dict) -> int:
    n = PER_MESSAGE_TOKENS + estimate_tokens(m.get("content") or "")
    if m.get("tool_calls"):
        n += estimate_tokens(json.dumps(m["tool_calls"]))
    return n


def _cut(text: str, limit: int) -> str:
    """Keep the start and the end of `text`: a tool result's last line often says how to continue."""
    if len(text) <= limit:
        return text
    tail = min(200, limit // 4)
    note = f"\n[... {len(text) - limit} characters left out to fit the context window ...]\n"
    return text[:limit - tail] + note + text[len(text) - tail:]


def fit_messages(messages: list[dict], n_ctx: int, max_tokens: int, tools: list | None = None) -> tuple[list[dict], int]:
    """Return (messages that fit, number of messages left out). The input list is not changed."""
    reserve = min(max_tokens, n_ctx // 3)  # room for the reply (the engine caps it at the window anyway)
    budget = n_ctx - reserve - SAFETY_TOKENS
    if tools:
        budget -= estimate_tokens(json.dumps(tools, default=str))

    msgs = [dict(m) for m in messages]
    total = lambda ms: sum(_msg_tokens(m) for m in ms)  # noqa: E731
    if total(msgs) <= budget:
        return msgs, 0

    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=0)

    # 1. Shorten tool output from earlier turns.
    for m in msgs[:last_user]:
        if m.get("role") == "tool" and m.get("content"):
            m["content"] = _cut(m["content"], OLD_TOOL_RESULT_CHARS)

    # 2. Leave out whole old exchanges (a user message and everything up to the next one), oldest
    # first, so a tool result never loses the call it answers.
    system = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]
    dropped = 0
    # Trim to well under the limit, not just under it: dropping one exchange per turn would change the
    # start of the prompt every time, so the engine could never reuse what it already read.
    target = int(budget * TRIM_TO)
    while total(system + rest) > target:
        starts = [i for i, m in enumerate(rest) if m.get("role") == "user"]
        if len(starts) < 2:
            break  # only the current turn is left
        cut = starts[1]
        dropped += cut
        rest = rest[cut:]

    # 3. The current turn alone is too big: shorten its tool output, largest first.
    if total(system + rest) > budget:
        for m in sorted((m for m in rest if m.get("role") == "tool"), key=lambda m: -len(m.get("content") or "")):
            over = total(system + rest) - budget
            if over <= 0:
                break
            keep = max(400, len(m["content"]) - over * CHARS_PER_TOKEN)
            m["content"] = _cut(m["content"], keep)

    if dropped:
        # One system message only: several chat templates (Qwen, Gemma) reject a second one.
        if system:
            first = dict(system[0])
            first["content"] = ((first.get("content") or "") + "\n\n" + NOTE).strip()
            system = [first] + system[1:]
        else:
            system = [{"role": "system", "content": NOTE}]
        logger.info("History trimmed to fit %d tokens: %d older messages left out", n_ctx, dropped)
    return system + rest, dropped
