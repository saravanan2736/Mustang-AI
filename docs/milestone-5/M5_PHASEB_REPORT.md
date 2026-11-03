# Mustang AI — Milestone 5 Phase B: Engine and API tool-call support

Branch `m5-phase-b` (from `m5-phase-a`). Plan: `M5_AGENTIC_PLAN.md`, Phase B.

Status: **done.** A tool call now goes model → engine → API → client, streamed and not. Nothing runs tools yet; that is Phase C.

## 1. Changes

### Schemas (`backend/app/core/engine_base.py`)

| Type | Change |
|---|---|
| `Tool`, `ToolFunction` | New. OpenAI function-tool definition; the name must match `^[A-Za-z0-9_-]{1,64}$`; description up to 2,048 characters; `parameters` is a JSON Schema object |
| `ToolCall`, `ToolCallFunction` | New. `id`, `type: "function"`, name, and `arguments` as JSON text. The text is passed through as the model wrote it; whoever runs the tool validates it (Phase C) |
| `ChatMessage` | `role` adds `"tool"`; new `tool_calls` (assistant only) and `tool_call_id` (required for `tool`); `content: null` is accepted as `""`, since OpenAI clients send it on tool-call turns |
| `ChatCompletionRequest` | New `tools` (at most 64) and `tool_choice` (`auto`/`none`/`required` or an object); a `uses_tools` property |
| `ChatChunk` | New `tool_calls`: complete calls, sent once on the chunk that ends the reply, with `finish_reason: "tool_calls"` |

### Engine (`backend/app/core/llama_engine.py`)

- **Request:** sends `tools`/`tool_choice` only when set, plus assistant `tool_calls` and `tool` turns in OpenAI form (`_wire_message`). Plain chat requests are unchanged byte for byte (tested).
- **Fragment assembly:** `_ToolCallAssembler` collects streamed `delta.tool_calls` fragments by index (name, id and argument pieces) and delivers complete calls once.
  - It generates an id if the model gave none.
  - A stream that ends without a `finish_reason` still delivers its calls.
  - **A cancelled stream never delivers a partial call,** so a cut-off call can't be run later.

### API

| Endpoint | Change |
|---|---|
| `/v1/chat/completions` (OpenAI-compatible) | Passes `tools`/`tool_choice` through. Streamed replies carry `delta.tool_calls` with `index`; non-streamed replies carry `message.tool_calls` with `content: null`, as OpenAI does. A malformed tool turn returns 422 |
| `/api/chat/completions` (the UI's endpoint) | **Rejects** `tools` and tool turns with 400, pointing to `/v1`. Conversations store text turns only; the agent loop and schema v2 for tool messages arrive in Phase C |

`MockEngine` (tests) now calls the first offered tool when the last turn is from the user, and answers from a tool result when the last turn is a tool message.

### Bug fixed on the way (also affects 0.1.0)

A model copied into a **subfolder** of the models folder by hand was listed in the model menu, but loading it returned 404 "Model file not found". Loading by file name only looked at the top level and at library (downloaded) models. It now falls back to the same listing the menu uses (one shared helper, `_listed_model_files`), and picks the same file the menu shows when two subfolders hold the same name. Hidden folders (`.downloads`) stay excluded. Commit `01c0dde`, with two tests; the new one fails on the old code.

## 2. Tests

| Suite | Result |
|---|---|
| `tests/test_tool_calls.py` (new, 19 tests) | pass. Schemas (tool turn needs an id, only assistants carry calls, restricted names, `content: null`); engine request shape with tools and tool turns; plain request unchanged; two parallel calls assembled from fragments; calls delivered without `finish_reason`; **no partial call after cancel**; `/v1` streamed and non-streamed shapes; round trip; 422 for a malformed tool turn; 400 on `/api/chat` |
| `tests/test_tool_calls_gpu.py` (new, 2 tests, `-m gpu`) | pass. Real llama-server, 0.5B model with `tool_choice: "required"`: a structured call whose streamed arguments form valid JSON, then a tool result accepted; plain chat unchanged |
| `tests/test_downloaded_model_loading.py` (+2) | pass. Hand-copied model in a subfolder loads; a file only in `.downloads` doesn't |
| Backend pytest, dev venv | **186 passed** (163 + 23 new; includes the 7 GPU tests) |
| Frontend `tsc --noEmit` | clean (no frontend change) |
| Data guard | unchanged |

**End to end** (isolated source backend, scratch run folder, Qwen3.5 4B Uncensored, through `/v1/chat/completions`):

| Request | Result |
|---|---|
| "What is 1234 * 5678?" with `calculator` and `get_current_time`, non-streamed | `finish_reason: "tool_calls"`, `content: null`, `calculator` with `{"expression":"1234 * 5678"}` |
| Same, streamed | 34 events, exactly **one** carrying the complete call |
| Tool result `7006652` sent back | "The result of 1234 * 5678 is **7,006,652**." |
| "Say hello in one word." with the same tools | "Hello", no tool call |
| `/api/chat/completions` with tools | 400 |

## 3. Notes for Phase C

- **Reasoning text** (`delta.reasoning_content`) from thinking models is still dropped, as before. The UI shows only the answer. Showing the thinking can be decided in Phase D.
- **Reply limit:** the UI's default `max_tokens` is 2048. Phase A showed thinking models need ~1,000 tokens per step, so that is enough.
- **Argument validation:** tool names and arguments from the model are passed through unvalidated. The agent loop must check the name against its registry and the arguments against the tool's schema before running anything.

## 4. Not done in this phase

No desktop rebuild; the changes ship with the next `scripts/build_release.py`. The UI is unchanged.
