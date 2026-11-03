# Mustang AI — Milestone 5 Phase A: Tool-calling spike report

Branch `m5-phase-a` (from `m5-phase-0`). Plan: `M5_AGENTIC_PLAN.md`, Phase A.

Status: **done.** Six models tested: the two local Qwen2.5 models and four uncensored models downloaded with the owner's OK (§6). **Go** for Phases B–F, with recommended models (§4).

## 1. Method

The harness is `scripts/m5/tool_call_spike.py` and can be rerun on any GGUF file:
```
python scripts/m5/tool_call_spike.py <model.gguf> [repeats] [--max-tokens N] [--json out.json]
```
- **Engine:** started through `LlamaServerEngine`, with the app's flags (including Phase 0's `--no-agent`), 8K context, all layers on the GPU.
- **Tools:** five OpenAI-format tools: `get_current_time`, `calculator`, `list_files`, `read_file`, `search_history`. A short system prompt says to use a tool only when needed.
- **Prompts:** 12 fixed prompts. 9 need a specific tool with specific arguments; 3 need no tool (greeting, poem, general knowledge).
- **Runs:** temperature 0.7 (the app default), 5 repeats, non-streamed and streamed. Streamed `delta.tool_calls` fragments are assembled the way Phase B would.
- **Scoring:** right tool, valid JSON arguments, sensible arguments (e.g. the file name), and no tool or fake tool-call text when none is needed.
- **Round trip:** the model's call gets a canned result and the final answer must use it. Up to 3 tool calls are allowed; other tools get "(no results)".
- **Reply limit:** reasoning models (Qwen3.5, Gemma 4) think before they answer or call a tool, so they ran with `--max-tokens 1024`; the Qwen2.5 models used 256.

Nothing is executed: all tool results are canned.

## 2. Results

Tool prompts: 45 per mode. No-tool prompts: 15 per mode. Round trips: 9 per mode. Each pair is non-stream / stream.

| Model (Q4_K_M) | Size | VRAM (8K ctx) | Tool prompts right | No-tool prompts right | Round trips | Median step |
|---|---|---|---|---|---|---|
| Qwen2.5 0.5B Instruct | 0.49 GB | 374 MiB | 39 / 36 | 6 / 7 | 8 / 8 | 0.09 s |
| Qwen2.5 1.5B Instruct | 1.12 GB | 935 MiB | 40 / 41 | 11 / 12 | 8 / 8 | 0.13 s |
| **Qwen3.5 4B Uncensored (HauhauCS)** | 2.71 GB | 2,572 MiB | **45 / 45** | **15 / 15** | **9 / 9** | 1.0 s |
| **Gemma 4 E4B Uncensored (HauhauCS)** | 5.34 GB | 2,868 MiB | **45 / 45** | **15 / 15** | **9 / 9** | 1.9–2.2 s |
| **Qwen3.5 9B Uncensored (HauhauCS)** | 5.63 GB | 4,810 MiB | **45 / 45** | **15 / 15** | 9 / 8 | 1.4 s |
| Huihui Qwen3.5 9B abliterated | 5.63 GB | ~4,810 MiB | 45 / 45 | 13 / 11 | 9 / 8 | 1.5 s |

Other measurements:
- **Invalid JSON arguments:** 0 in all 720 scored replies.
- **Tool call printed as text instead of a structured call:** 1 each for the two Qwen2.5 models, 0 for the others.
- **Template caps (`/props`):** `supports_tools` and `supports_tool_calls` for all six. Prompt cost of the five tool definitions: +429 tokens (Qwen2.5), +568 (Qwen3.5), +300 (Gemma 4).

The Qwen3.5 rows are from the rerun with multi-step round trips. In the first run, 3 "failed" round trips were the model calling a second tool, and 2 were scoring artifacts ("7,006,652" with commas; "2:05 PM" instead of "14:05").

Failures, by model:
- **Qwen2.5 0.5B / 1.5B:** they don't read a file by name ("I can't access your files", 8–9 of 10). They call a tool for the poem (6–8 of 10). The 0.5B calls the clock for "capital of France" (7 of 10) and lists `"."` instead of `"docs"` (6 of 10).
- **Huihui 9B:** calls a tool for a greeting or a general question (6 of 30 no-tool tries: the clock, the calculator, a history search). One streamed round trip looped `search_history` → `read_file` → `read_file`.
- **Qwen3.5 9B (HauhauCS):** one streamed round trip called `search_history` three times in a row and hit the step limit.

## 3. Findings

1. **The plumbing works.** llama-server b12000, with its default `--jinja`, turns tool-call output into structured OpenAI `tool_calls`, streamed and not, with `finish_reason: "tool_calls"`. That holds for three model families (Qwen2.5, Qwen3.5, Gemma 4). Reasoning output is kept out of `content` (`reasoning_content`), and tool calls come after the thinking. Phase B can rely on this.
2. **`--jinja` needs no change.** It was already the default throughout Milestone 4, so plain chat is unaffected.
3. **Model size and generation decide reliability.** The 0.5B and 1.5B Qwen2.5 models are no-go. The 4B–9B Qwen3.5 and Gemma 4 E4B models were right in every scored tool and no-tool case.
4. **Uncensored doesn't hurt tool use here.** The HauhauCS models score like a perfect instruct model. The Huihui abliteration of the same 9B base calls tools a little too eagerly.
5. **Thinking costs latency:** ~1–2 s per step before a tool call or answer, against ~0.1 s for Qwen2.5. A three-step task therefore takes a few seconds. The UI should show progress per step (Phase D).
6. **Loops happen.** Two 9B runs repeated tool calls until the step limit. Phase C needs the planned step limit, plus a stop when the same call repeats with the same arguments and gets the same result.
7. **Tool definitions cost 300–570 tokens** for five tools. Keep schemas short; it matters more at small context sizes.
8. **Uncensored models don't refuse,** so the safety rules in the plan (§2: approval for anything that changes something, workspace folder limit, no shell or network tools) are what stands between a bad instruction and an action. Phase F's prompt-injection tests matter even more.

## 4. Decision

- **Go for Phases B–F.**
- **Recommended for agent mode (8 GB GPU):**
  1. **Qwen3.5 4B Uncensored:** the best balance; 2.6 GB VRAM, ~1 s per step, perfect scores.
  2. **Gemma 4 E4B Uncensored:** perfect scores; slower (~2 s per step).
  3. **Qwen3.5 9B Uncensored:** perfect scores; needs ~4.8 GB VRAM at 8K context, so less room for long contexts on 8 GB.
- **Not recommended:** Qwen2.5 0.5B / 1.5B (agent mode should warn when loaded); Huihui 9B (too many unneeded tool calls).
- **Phase C additions:** a repeated-call guard, and a reply limit of at least ~1,000 tokens per step for reasoning models.

## 5. Still open

- **Long multi-step tasks** (5+ tools) and longer contexts aren't tested; they come with the real tools in Phase C.
- **The agent loop's own system prompt** will be tuned in Phase C. The 1.5B's "I have no file access" replies suggest wording matters for weaker models.

## 6. Downloaded test models

All four are in `build\test-models\m5`, which is git-ignored and is not the user's models folder. Each was verified against Hugging Face's SHA-256 before use:

| File | Source | SHA-256 |
|---|---|---|
| `Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive | `79e28eca…2741` |
| `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive | `d0027dd3…6e96` |
| `Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive | `2ca636d9…a4f5` |
| `Huihui-Qwen3.5-9B-abliterated.Q4_K_M.gguf` | mradermacher/Huihui-Qwen3.5-9B-abliterated-GGUF | `ea1858ef…b4e3` |

Licenses: Apache-2.0 (Qwen3.5 variants) and the Gemma terms (Gemma 4).

## 7. Files

- `scripts/m5/tool_call_spike.py`: the harness. This report adds the `--max-tokens` option, multi-step round trips, and number/time-format tolerant checks.
- This report.
- Raw JSON results stay in the session's scratch folder; they aren't committed.

Data guard (model + `backend\run\mustang.db` SHA-256): unchanged after every run.
