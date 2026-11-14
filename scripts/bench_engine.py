"""
Engine benchmark: speed, memory and tool-calling accuracy of llama-server under a given flag set.

It starts llama-server itself (own port, own key, nothing from the app), runs fixed workloads through
the OpenAI-style chat endpoint and prints one result block, so two flag sets can be compared fairly:

    backend\\.venv\\Scripts\\python.exe scripts\\bench_engine.py --model models\\Qwen3.5-4B-....gguf ^
        --label baseline --ctx 8192 -- -b 512 -v --flash-attn on

Everything after `--` goes to llama-server as is. Workloads (all with a fixed seed and temperature 0):
  decode   short prompt, 256 tokens out, thinking off: generation speed
  prefill  a ~5k-token prompt, then the same prompt again: prompt speed and prompt-cache reuse
  edit     rewrite a 150-line file with one rename: code-like decoding (speculative decoding helps here)
  tools    the app's real tool list and agent prompt, 10 requests: is the right tool called with valid
           arguments, how many prompt tokens the tool list costs, and how long a call takes
Use --think on|off to run the chat workloads with the model's reasoning on or off.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "backend" / "runtime" / "bin" / "llama-server.exe"
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def gpu_used_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        return int(out[0])
    except Exception:  # noqa: BLE001
        return None


class Server:
    def __init__(self, model: Path, ctx: int, extra: list[str], port: int = 18290):
        self.port, self.key = port, secrets.token_hex(16)
        self.cmd = [str(SERVER), "-m", str(model), "-c", str(ctx), "--host", "127.0.0.1", "--port", str(port),
                    "--no-webui", "--offline", "--no-agent", *extra]
        self.base = f"http://127.0.0.1:{port}"
        self.proc = None
        self.log = Path(os.environ.get("TEMP", ".")) / f"bench_server_{port}.log"

    def start(self) -> float:
        env = {**os.environ, "LLAMA_API_KEY": self.key}
        before = gpu_used_mb() or 0
        t0 = time.time()
        self.proc = subprocess.Popen(self.cmd, stdout=open(self.log, "w"), stderr=subprocess.STDOUT, env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with httpx.Client(timeout=2) as c:
            while time.time() - t0 < 180:
                if self.proc.poll() is not None:
                    raise RuntimeError("llama-server exited: " + self.log.read_text(errors="replace")[-600:])
                try:
                    if c.get(self.base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("llama-server did not become ready in 180 s")
        self.load_s = time.time() - t0
        time.sleep(1.0)
        self.vram_base_mb = (gpu_used_mb() or 0) - before
        return self.load_s

    def chat(self, messages, max_tokens=256, tools=None, think=None, temperature=0.0, extra=None) -> dict:
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "seed": 1, "stream": False}
        if tools:
            body["tools"] = tools
        if think is False:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if extra:
            body.update(extra)
        t0 = time.time()
        r = httpx.post(self.base + "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=600)
        r.raise_for_status()
        d = r.json()
        d["_secs"] = time.time() - t0
        return d

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            self.proc.wait(timeout=20)
        time.sleep(1.5)


def timings(d: dict) -> dict:
    t = d.get("timings") or {}
    return {"prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n"), "prefill_tps": t.get("prompt_per_second"),
            "decode_n": t.get("predicted_n"), "decode_tps": t.get("predicted_per_second"), "secs": round(d["_secs"], 2)}


# --- workloads ------------------------------------------------------------------------------------

def w_decode(s: Server, think) -> dict:
    d = s.chat([{"role": "user", "content": "Write a 200-word story about a horse who learns to sail."}], 256, think=think)
    return timings(d)


def w_prefill(s: Server, think) -> dict:
    para = ("The harbour at dawn was quiet except for the gulls and the slow knock of hulls against the pier. "
            "A fisherman mended a net while his daughter counted the crates of ice, and nobody spoke of the storm. ")
    text = " ".join(f"[{i}] {para}" for i in range(1, 120))
    msgs = [{"role": "user", "content": text + "\n\nReply with the single word OK."}]
    first = s.chat(msgs, 8, think=think)
    second = s.chat(msgs, 8, think=think)  # identical prompt: the prompt cache should make this nearly free
    a, b = timings(first), timings(second)
    return {"prompt_tokens": first["usage"]["prompt_tokens"], "first_prefill_tps": a["prefill_tps"], "first_secs": a["secs"],
            "second_secs": b["secs"], "second_cache_n": b["cache_n"], "second_prompt_n": b["prompt_n"]}


CODE = "\n".join(
    f"def handler_{i}(payload):\n    value = payload.get('item_{i}', {i})\n    return value * {i + 2}\n" for i in range(1, 38))


def w_edit(s: Server, think) -> dict:
    msgs = [{"role": "user", "content": f"Here is a Python file:\n\n```python\n{CODE}\n```\n\nReturn the same file with the "
                                        "function handler_7 renamed to process_seven. Output only the file in a code block."}]
    d = s.chat(msgs, 1600, think=think)
    text = d["choices"][0]["message"].get("content") or ""
    return {**timings(d), "renamed": "process_seven" in text and "handler_7" not in text, "out_chars": len(text)}


TOOL_CASES = [
    ("What time is it right now?", "get_current_time"),
    ("List the files in the workspace.", "list_files"),
    ("Find every .py file in the project.", "find_files"),
    ("Search the project for the word 'timeout'.", "search_files"),
    ("Show me what README.md says.", "read_file"),
    ("Create a file called hello.txt containing the text hi.", "create_file"),
    ("What is 17 * 23 + 4?", "calculator"),
    ("Run the tests with python -m unittest.", "run_command"),
    ("Make a plan with update_tasks: first read the code, then fix the bug, then run the tests.", "update_tasks"),
    ("Change 'Buy milk' to 'Buy oat milk' in notes.txt.", "edit_file"),
]


def w_tools(s: Server, think) -> dict:
    from app.agent import loop
    from app.agent.registry import ToolContext, ToolError, available_tools, validate_arguments
    from app.agent.workspace import Workspace
    import tempfile

    ws = Workspace(tempfile.mkdtemp())
    ctx = ToolContext(workspace=ws, terminal=True)
    specs = available_tools(ctx)
    tools = [t.definition().model_dump(exclude_none=True) for t in specs]
    by_name = {t.name: t for t in specs}
    system = loop._with_agent_prompt([], ctx)[0].content
    ok_tool = ok_args = 0
    secs, wrong = [], []
    prompt_tokens = None
    for prompt, expected in TOOL_CASES:
        d = s.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], 400, tools=tools, think=think)
        prompt_tokens = prompt_tokens or d["usage"]["prompt_tokens"]
        secs.append(d["_secs"])
        calls = d["choices"][0]["message"].get("tool_calls") or []
        names = [c["function"]["name"] for c in calls]
        if expected in names:
            ok_tool += 1
            call = next(c for c in calls if c["function"]["name"] == expected)
            try:
                validate_arguments(by_name[expected], call["function"]["arguments"])
                ok_args += 1
            except ToolError as e:
                wrong.append(f"{expected}: bad args ({e})")
        else:
            wrong.append(f"{expected}: got {names or 'no call'}")
    return {"tool_tokens_plus_prompt": prompt_tokens, "right_tool": f"{ok_tool}/{len(TOOL_CASES)}", "valid_args": f"{ok_args}/{len(TOOL_CASES)}",
            "avg_secs": round(sum(secs) / len(secs), 2), "total_secs": round(sum(secs), 1), "misses": wrong}


WORKLOADS = {"decode": w_decode, "prefill": w_prefill, "edit": w_edit, "tools": w_tools}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--label", default="run")
    ap.add_argument("--think", choices=["on", "off"], default="off")
    ap.add_argument("--only", default=",".join(WORKLOADS))
    ap.add_argument("--json", help="append the result as one JSON line to this file")
    ap.add_argument("extra", nargs=argparse.REMAINDER, help="after --: flags for llama-server")
    a = ap.parse_args()
    extra = a.extra[1:] if a.extra and a.extra[0] == "--" else a.extra
    think = None if a.think == "on" else False

    s = Server(Path(a.model), a.ctx, extra)
    result = {"label": a.label, "model": Path(a.model).name, "ctx": a.ctx, "think": a.think, "flags": " ".join(extra)}
    try:
        result["load_s"] = round(s.start(), 1)
        result["vram_after_load_mb"] = s.vram_base_mb
        for name in a.only.split(","):
            result[name] = WORKLOADS[name](s, think)
        result["vram_peak_mb"] = (gpu_used_mb() or 0) - (s.vram_base_mb and 0)
    finally:
        s.stop()
    print(json.dumps(result, indent=1, ensure_ascii=False))
    if a.json:
        with open(a.json, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
