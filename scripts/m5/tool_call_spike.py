"""
M5 Phase A: how reliably does a local GGUF model make OpenAI-format tool calls through llama-server?

Starts the engine with LlamaServerEngine (same flags as the app, including --no-agent), sends a fixed
set of prompts with five tool definitions, non-streamed and streamed, and scores every reply:
right tool, valid JSON arguments, sensible arguments, no tool when none is needed. Also checks one
round trip per tool-using case (tool result sent back -> final answer uses it) and the prompt cost
of the tool definitions. Nothing is executed: tool results are canned.

usage: python scripts/m5/tool_call_spike.py <model.gguf> [repeats=3] [--json out.json]
"""

import argparse
import asyncio
import json
import logging
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.engine_base import EngineConfig  # noqa: E402
from app.core.llama_engine import LlamaServerEngine  # noqa: E402

logging.disable(logging.CRITICAL)


def _fn(name, description, props, required):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props, "required": required}}}


TOOLS = [
    _fn("get_current_time", "Get the current local date and time.", {}, []),
    _fn("calculator", "Evaluate an arithmetic expression and return the exact result.",
        {"expression": {"type": "string", "description": "Arithmetic expression, e.g. (2+3)*4"}}, ["expression"]),
    _fn("list_files", "List the files in a folder of the user's workspace.",
        {"path": {"type": "string", "description": "Folder path relative to the workspace; '.' for the top"}}, ["path"]),
    _fn("read_file", "Read a text file from the user's workspace.",
        {"path": {"type": "string", "description": "File path relative to the workspace"}}, ["path"]),
    _fn("search_history", "Search the user's earlier chat messages.",
        {"query": {"type": "string", "description": "Words to search for"}}, ["query"]),
]

SYSTEM = "You are a helpful assistant on the user's computer. Use a tool when it is needed to answer; otherwise answer directly."


def _has(*words):
    return lambda a: all(w in json.dumps(a).lower() for w in words)


# (prompt, expected tool or None, argument check, canned tool result, word the final answer must contain)
CASES = [
    ("What time is it right now?", "get_current_time", lambda a: True, "2026-11-03 14:05:09", "14:05"),
    ("What is 1234 * 5678?", "calculator", _has("1234", "5678"), "7006652", "7006652"),
    ("What is (17 + 25) / 3?", "calculator", _has("17", "25", "3"), "14", "14"),
    ("Calculate 15% of 240.", "calculator", _has("240"), "36", "36"),
    ("List the files in my workspace folder.", "list_files", lambda a: True, "notes.txt\ntodo.md\nbudget.csv", "todo.md"),
    ("Show me what files are in the docs folder.", "list_files", _has("docs"), "guide.md\nfaq.md", "faq"),
    ("What's inside notes.txt?", "read_file", _has("notes.txt"), "Buy milk. Call Ravi at 6 pm.", "ravi"),
    ("Read todo.md and tell me the first item.", "read_file", _has("todo.md"), "1. Renew passport\n2. Pay rent", "passport"),
    ("Did we talk about horses earlier? Search my chat history.", "search_history", _has("horse"), "2026-10-30: 'One fact about horses'", "horse"),
    ("Hi! How are you today?", None, None, None, None),
    ("Write a two-line poem about the sea.", None, None, None, None),
    ("What is the capital of France?", None, None, None, None),
]


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Spike:
    def __init__(self, engine: LlamaServerEngine, port: int):
        self.e, self.url = engine, f"http://127.0.0.1:{port}/v1/chat/completions"

    async def ask(self, messages, stream, tools=TOOLS, max_tokens=256):
        body = {"messages": messages, "tools": tools, "temperature": 0.7, "max_tokens": max_tokens, "stream": stream}
        if not tools:
            body.pop("tools")
        t0 = time.perf_counter()
        if not stream:
            r = await self.e._http.post(self.url, json=body, headers=self.e._auth_headers(), timeout=120)
            r.raise_for_status()
            j = r.json()
            msg = j["choices"][0]["message"]
            return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or [],
                    "finish": j["choices"][0].get("finish_reason"), "usage": j.get("usage", {}), "s": time.perf_counter() - t0}
        content, calls, finish = "", {}, None
        async with self.e._http.stream("POST", self.url, json=body, headers=self.e._auth_headers(), timeout=120) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                ch = json.loads(line[5:])["choices"]
                if not ch:
                    continue
                d = ch[0].get("delta", {})
                content += d.get("content") or ""
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": None, "function": {"name": "", "arguments": ""}})
                    slot["id"] = tc.get("id") or slot["id"]
                    f = tc.get("function") or {}
                    slot["function"]["name"] += f.get("name") or ""
                    slot["function"]["arguments"] += f.get("arguments") or ""
                finish = ch[0].get("finish_reason") or finish
        return {"content": content, "tool_calls": [calls[k] for k in sorted(calls)], "finish": finish, "usage": {}, "s": time.perf_counter() - t0}


def score(case, reply):
    prompt, want, check, *_ = case
    calls = reply["tool_calls"]
    leaked = any(t in reply["content"] for t in ("<tool_call>", '"name":', "<|python_tag|>", "[TOOL_CALLS]"))
    if want is None:
        kind = "false_tool_call" if calls else "leaked_as_text" if leaked else "ok"
        return {"ok": kind == "ok", "kind": kind, "leaked": leaked}
    if not calls:
        return {"ok": False, "kind": "leaked_as_text" if leaked else "no_tool_call", "leaked": leaked}
    f = calls[0]["function"]
    try:
        args = json.loads(f["arguments"] or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "kind": "bad_json_args", "leaked": leaked}
    if f["name"] != want:
        return {"ok": False, "kind": f"wrong_tool:{f['name']}", "leaked": leaked}
    if not isinstance(args, dict) or not check(args):
        return {"ok": False, "kind": "wrong_args", "args": args, "leaked": leaked}
    return {"ok": True, "kind": "ok", "args": args, "leaked": leaked}


async def round_trip(spike, case, stream):
    """Model calls the tool, gets the canned result, and must use it in its answer."""
    prompt, want, _, result, must = case
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    first = await spike.ask(msgs, stream)
    if not first["tool_calls"] or first["tool_calls"][0]["function"]["name"] != want:
        return {"ok": False, "kind": "no_correct_first_call"}
    call = first["tool_calls"][0]
    call_id = call.get("id") or "call_0"
    msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": call["function"]}]})
    msgs.append({"role": "tool", "tool_call_id": call_id, "content": result})
    second = await spike.ask(msgs, stream)
    if second["tool_calls"]:
        return {"ok": False, "kind": "called_again", "answer": second["content"][:160]}
    used = must in second["content"].lower().replace(",", "")  # "7,006,652" counts as 7006652
    return {"ok": used, "kind": "ok" if used else "ignored_result", "answer": second["content"][:160]}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("repeats", nargs="?", type=int, default=3)
    ap.add_argument("--json")
    a = ap.parse_args()
    model = Path(a.model)
    engine, port = LlamaServerEngine(), _port()
    t0 = time.perf_counter()
    assert await engine.start(EngineConfig(model_path=model, n_ctx=8192, n_gpu_layers=99, port=port)), (await engine.get_status()).error_message
    load_s = time.perf_counter() - t0
    spike = Spike(engine, port)
    props = (await engine._http.get(f"http://127.0.0.1:{port}/props", headers=engine._auth_headers())).json()
    caps = props.get("chat_template_caps", {})
    out = {"model": model.name, "load_s": round(load_s, 2), "template_caps": caps, "vram_mb": (await engine.get_status()).vram_allocated_mb, "modes": {}}

    base = await spike.ask([{"role": "user", "content": "Hi"}], False, tools=None, max_tokens=8)
    withtools = await spike.ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Hi"}], False, max_tokens=8)
    out["prompt_tokens"] = {"no_tools": base["usage"].get("prompt_tokens"), "with_5_tools": withtools["usage"].get("prompt_tokens")}

    for stream in (False, True):
        mode = "stream" if stream else "non-stream"
        rows = []
        for case in CASES:
            for _ in range(a.repeats):
                reply = await spike.ask([{"role": "system", "content": SYSTEM}, {"role": "user", "content": case[0]}], stream)
                s = score(case, reply)
                rows.append({"prompt": case[0], "want": case[1], **s, "finish": reply["finish"], "s": round(reply["s"], 2),
                             "got": [c["function"] for c in reply["tool_calls"]][:2], "content": reply["content"][:120]})
        trips = [{"prompt": c[0], **(await round_trip(spike, c, stream))} for c in CASES if c[1]]
        tool_rows = [r for r in rows if r["want"]]
        plain_rows = [r for r in rows if not r["want"]]
        out["modes"][mode] = {
            "tool_cases_correct": f"{sum(r['ok'] for r in tool_rows)}/{len(tool_rows)}",
            "no_tool_cases_correct": f"{sum(r['ok'] for r in plain_rows)}/{len(plain_rows)}",
            "round_trips_correct": f"{sum(t['ok'] for t in trips)}/{len(trips)}",
            "failure_kinds": sorted({r["kind"] for r in rows if not r["ok"]}),
            "leaked_tool_syntax_in_text": sum(r["leaked"] for r in rows),
            "median_reply_s": sorted(r["s"] for r in rows)[len(rows) // 2],
            "rows": rows, "round_trips": trips,
        }
    await engine.stop()

    print(f"\n== {out['model']}  load {out['load_s']} s  VRAM {out['vram_mb']} MiB  caps {caps}")
    print(f"   prompt tokens: {out['prompt_tokens']}")
    for mode, m in out["modes"].items():
        print(f"   {mode:10} tool cases {m['tool_cases_correct']:7} no-tool cases {m['no_tool_cases_correct']:6} "
              f"round trips {m['round_trips_correct']:5} leaked {m['leaked_tool_syntax_in_text']}  median {m['median_reply_s']} s")
        print(f"              failures: {m['failure_kinds']}")
        for r in m["rows"]:
            if not r["ok"]:
                print(f"              x {r['prompt'][:44]:44} {r['kind']:22} got={json.dumps(r['got'])[:110]} text={r['content'][:60]!r}")
        for t in m["round_trips"]:
            if not t["ok"]:
                print(f"              x round trip {t['prompt'][:40]:40} {t['kind']} {t.get('answer', '')[:80]!r}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
