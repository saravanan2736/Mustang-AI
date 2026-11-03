# Mustang AI — Milestone 5 Phase A: Tool-calling spike report

Branch `m5-phase-a` (from `m5-phase-0`). Plan: `M5_AGENTIC_PLAN.md`, Phase A.

Status: **partly done.** The two models on this machine are tested. Larger models need downloads, which wait for the owner's OK (§6).

## 1. Method

The harness is `scripts/m5/tool_call_spike.py` and can be rerun on any GGUF file:
```
python scripts/m5/tool_call_spike.py <model.gguf> [repeats] [--json out.json]
```
- **Engine:** started through `LlamaServerEngine`, with the app's flags (including Phase 0's `--no-agent`), 8K context, all layers on the GPU.
- **Tools:** five OpenAI-format tools: `get_current_time`, `calculator`, `list_files`, `read_file`, `search_history`. A short system prompt says to use a tool only when needed.
- **Prompts:** 12 fixed prompts. 9 need a specific tool with specific arguments; 3 need no tool (greeting, poem, general knowledge).
- **Runs:** temperature 0.7 (the app default), 5 repeats, non-streamed and streamed. Streamed `delta.tool_calls` fragments are assembled the way Phase B would.
- **Scoring:** right tool, valid JSON arguments, sensible arguments (e.g. the file name), and no tool or fake tool-call text when none is needed.
- **Round trip:** for each tool prompt, the model's call gets a canned result and the final answer must use it.

Nothing is executed: all tool results are canned.

## 2. Results

| | Qwen2.5 0.5B Instruct Q4_K_M | Qwen2.5 1.5B Instruct Q4_K_M |
|---|---|---|
| Template caps (`/props`) | `supports_tools`, `supports_tool_calls`, parallel calls: yes | same |
| Tool prompts right, non-stream / stream | 39/45 (87 %) / 36/45 (80 %) | 40/45 (89 %) / **41/45 (91 %)** |
| No-tool prompts right | 6/15 (40 %) / 7/15 (47 %) | 11/15 (73 %) / **12/15 (80 %)** |
| Round trips (result used in the answer) | 8/9 / 8/9 | 8/9 / 8/9 |
| Invalid JSON arguments | **0** | **0** |
| Tool call printed as text instead of a structured call | 1 | 1 |
| Median reply (first step) | 0.09 s | 0.12–0.14 s |
| VRAM (model) | 374 MiB | 935 MiB |
| Prompt cost of the 5 tool definitions | 30 → 459 tokens (+429) | same |

Failures by prompt (both modes, 10 tries each):

| Prompt | 0.5B | 1.5B |
|---|---|---|
| "What's inside notes.txt?" → no tool call ("I can't access your files") | 8 | 9 |
| "Write a two-line poem about the sea." → calls a tool anyway (calculator, clock, history) | 8 | 6 (+1 as text) |
| "What is the capital of France?" → calls `get_current_time` | 7 (+1 as text) | 0 |
| "Show me what files are in the docs folder." → `list_files` with `"."` instead of `"docs"` | 6 | 0 |
| Others | 3 | 0 |

## 3. Findings

1. **The plumbing works.** llama-server b12000 with its default `--jinja` turns Qwen2.5's tool-call output into structured OpenAI `tool_calls`, streamed and not, with `finish_reason: "tool_calls"`. In 240 replies there was no invalid JSON, and streamed fragments assembled cleanly. Phase B can rely on this.
2. **`--jinja` needs no change.** It was already the default throughout Milestone 4, so the Stage 6 benchmark numbers already include it. Plain chat isn't affected.
3. **Small models are the limit, not the engine.** Even the 1.5B calls a tool for 1 in 5 prompts that need none, and almost always refuses to read a file by name. That is too unreliable for agent mode; it would mean asking the user to approve calls that make no sense.
4. **Tool definitions cost ~85 tokens per tool.** Five tools add ~430 prompt tokens per step. That's fine at 8K context, but tool schemas should stay short and few.
5. **Round trips work.** When the call was right, the model used the result in its answer 16 of 18 times (the misses were runs whose first call was already wrong).
6. **Both models are non-reasoning,** so the `--reasoning` / `reasoning_content` interaction is not tested yet; it needs a reasoning model (§6).

## 4. Decision so far

- **Go for the engine and API work (Phase B):** the format is reliable.
- **No-go for 0.5B and 1.5B as agent models.** Agent mode should warn when such a model is loaded (Phase D).
- **Which models to recommend** depends on testing 3B–8B models, which fit the 8 GB GPU. That needs downloads (§6).

## 5. Ideas to test with the larger models

- A system prompt that says the tools *are* the user's files (the 1.5B kept saying it has no file access).
- `tool_choice: "none"` for turns that clearly need no tool is not an option: the app can't know that in advance. So the no-tool score decides.

## 6. Pending: larger models (needs the owner's OK to download)

Candidates that fit 8 GB VRAM at Q4_K_M, from Hugging Face. Sizes are approximate:

| Model | Why | Size |
|---|---|---|
| Qwen2.5-3B-Instruct | same family, next size up | ~2.0 GB |
| Qwen2.5-7B-Instruct | likely the best tool use that fits 8 GB | ~4.7 GB |
| Llama-3.2-3B-Instruct | a second family with native tool calling | ~2.0 GB |
| A small reasoning model (e.g. Qwen3-4B) | covers the reasoning-plus-tools case from §3.6 | ~2.5 GB |

Downloads would go to the scratch test folder `build\test-models`, not the user's models folder, and be verified by SHA-256 as the app does.

## 7. Files

- `scripts/m5/tool_call_spike.py`: the harness.
- This report.
- Raw JSON results stay in the session's scratch folder; they aren't committed.

Data guard (model + `backend\run\mustang.db` SHA-256): unchanged after every run.
