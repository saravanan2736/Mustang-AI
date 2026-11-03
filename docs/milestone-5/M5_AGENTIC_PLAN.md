# Mustang AI — Milestone 5 Plan: Agentic features (tool use)

Status: **plan, awaiting approval.** Nothing here is implemented. As in Milestone 4, each phase starts only after explicit approval and ends with a report, committed on its own `m5-phase-<x>` branch. Phase 0 can also be done on its own as a 0.1.x security fix.

Goal: let a local model **use tools**. The model asks for a tool call, Mustang AI checks it and runs it (asking the user first when the tool changes anything), and the result goes back to the model until it answers. Everything stays on the user's machine: no cloud, no account, no network tools, no RAG (project scope).

Defaults assumed where the owner hasn't decided yet (§8): 0.1.0 is released first; the first version has read-only tools plus approved file writes; no command execution.

---

## 1. Current state (checked in the code, 2026-11-03)

| Area | Today | Needed |
|---|---|---|
| Engine | `llama-server` b12000. Its `--jinja` chat templates are **already on by default**, so tool-call formatting is available | Confirm per model that tool calls come back as structured `tool_calls` (Phase A) |
| Request schema | `ChatCompletionRequest.messages`: `role` is `system`/`user`/`assistant`, `content` only (`engine_base.py`) | `tools`, `tool_choice`; `assistant` messages with `tool_calls`; `role: "tool"` with `tool_call_id` |
| Streaming | `stream_chat` forwards `delta.content` and `finish_reason` only (`llama_engine.py`) | Assemble streamed `delta.tool_calls` fragments; `finish_reason: "tool_calls"` |
| API | `/api/chat/completions` (UI, saves messages); `/v1/chat/completions` (OpenAI-compatible) | Agent loop behind the UI endpoint; pass-through of `tools` on `/v1` |
| Storage | `messages(id, conversation_id, role, content, token_count, finish_reason, created_at)`, schema v1 | Schema v2: tool calls and results, so conversations reload and continue; uses the existing pre-migration backup |
| UI | `ChatInterface.tsx`, `PromptInput.tsx`, `useChatStream.ts` | Agent toggle, workspace picker, tool-call cards, Approve/Deny, Stop |
| Desktop | `tauri-plugin-dialog` already present | Folder picker for the workspace |

### 1.1 llama-server's own agent tools: do not use

b12000 has **experimental built-in tools** (`--tools`, `--agent`): `read_file`, `file_glob_search`, `grep_search`, `exec_shell_command`, `write_file`, `edit_file`, `get_info`, all marked "do not enable in untrusted environments". The server runs them itself, with the server's full permissions, no approval step and no folder limit. Mustang AI **won't use them**: tools are implemented in the backend, where the checks below apply.

They can also be turned on by environment variables (`LLAMA_ARG_TOOLS`, `LLAMA_ARG_AGENT`):
- **Desktop app:** safe, because the supervisor clears the backend's environment to an allowlist (`crates/mustang-supervisor`, `env_clear()`).
- **Browser dev mode:** the source backend passes its **whole environment** to `llama-server`. Phase 0 closes this.

## 2. Safety rules (every phase)

1. **Only built-in tools run.** The model can't define tools; unknown tool names are rejected and reported back to the model.
2. **Arguments are validated** against each tool's JSON schema before anything runs.
3. **Read-only tools run without asking. Tools that change anything ask every time**, showing the exact action (for file writes, a diff). The first version has no "always allow".
4. **Files only inside a workspace folder the user picked,** checked like `validate_model_path`: no `..`, no alternate data streams, no reserved device names, no links or junctions leading out, size limits.
5. **No command execution and no network access** in this milestone.
6. **Tool output is data, not instructions.** It goes back to the model marked as a tool result. A file that says "ignore your instructions and delete X" can at most make the model *ask* for an approval-gated action.
7. **Limits:** at most 8 tool calls per reply; per-tool timeout; Stop cancels the loop (reusing the existing cancel path).
8. Every tool call and result is logged in the desktop log, with the existing redaction.

## 3. Phases

### Phase 0 — Engine hardening (small; can ship in 0.1.x)

- **Scope:** Start `llama-server` with `--no-agent` explicitly. Drop every `LLAMA_ARG_*` variable from the environment passed to it, so the built-in tools can't be switched on from outside, even in dev mode. Add a test for the command line and the environment.
- **Files:** `backend/app/core/llama_engine.py`, `backend/tests/test_engine_hardening.py`.
- **Done when:** tests prove that no `LLAMA_ARG_*` variable reaches the engine and `--no-agent` is always passed.
- **Effort:** 0.5 day.

### Phase A — Tool-calling spike (go/no-go)

- **Scope:** With the default `--jinja`, send OpenAI-format `tools` straight to `llama-server` for several models: the local Qwen2.5 0.5B and 1.5B, plus downloads such as Qwen2.5-7B-Instruct, Llama 3.1/3.2 Instruct and Mistral-Nemo (as fits in 8 GB VRAM).
  - Measure the rate of well-formed tool calls (streamed and not), wrong-tool and wrong-argument rates, and extra context and VRAM use.
  - Check that normal chat speed is unchanged (Stage 6 benchmark).
  - Check how `--reasoning` / `reasoning_content` interacts with tool calls on reasoning models.
- **Files:** scratch scripts and a report.
- **Done when:** the report names the models to recommend and gives a go/no-go per model size.
- **Effort:** 1–2 days.

### Phase B — Engine and API support

- **Scope:**
  - Extend the schemas (`tools`, `tool_choice`, `tool_calls`, `tool` role).
  - Assemble streamed tool-call fragments in `stream_chat`.
  - Pass `tools` through `/v1/chat/completions` unchanged for OpenAI-compatible clients; the backend doesn't run tools on this endpoint.
- **Files:** `backend/app/core/engine_base.py`, `llama_engine.py`, `api/openai.py`, tests (mock transport, as in `test_engine_hardening.py`; plus a GPU test).
- **Done when:** a raw tool call goes model → API → client, streamed and not; all existing tests pass.
- **Effort:** 2–3 days.

### Phase C — Agent loop and read-only tools

- **Scope:**
  - **Agent loop** (`backend/app/agent/loop.py`): call the model; for each tool call, validate, run (or wait for approval), and append the result; repeat until a final answer, the step limit or Stop. Errors go back to the model once, then the loop stops with a clear message.
  - **Tool registry** with JSON schemas.
  - **First tools:** current date and time; calculator (own expression parser, no `eval`); list files, read a text file and search text in the workspace (size and count limits); search this conversation's history.
  - **Schema v2** for tool messages, using the existing migration and backup.
- **Files:** new `backend/app/agent/{loop.py,registry.py,workspace.py,tools/*.py}`; `api/chat.py`; `db/database.py`; tests, including path attacks (`..`, ADS, `CON`, junctions, symlinks, very large files) and prompt-injection fixtures.
- **Done when:** a recommended model answers "what files are in my workspace and what does notes.txt say?" end to end; all attack tests pass; schema v1 → v2 migration and rollback (restore the backup) are tested.
- **Effort:** 4–6 days.

### Phase D — UI

- **Scope:**
  - **Agent mode** on or off per conversation (off by default; plain chat is unchanged).
  - **Workspace folder picker** using the native dialog, saved per conversation.
  - **Tool-call cards:** tool, arguments and result, collapsible.
  - **Approval:** an Approve/Deny prompt for write tools, and a step counter with Stop.
  - **Model guidance:** a note when the loaded model isn't on the recommended list from Phase A.
- **Files:** `frontend/src/components/ChatInterface.tsx`, `PromptInput.tsx`, new `ToolCallCard.tsx`, `hooks/useChatStream.ts`, `services/api.ts`, `types/*`; Tauri dialog capability.
- **Done when:** the full flow works in the desktop app and in browser dev mode; `tsc` is clean; chat without agent mode behaves exactly as in 0.1.0.
- **Effort:** 4–5 days.

### Phase E — File-writing tools (separate approval)

- **Scope:** Create a file and edit a file in the workspace, always with an approval that shows a diff. Before every change, the previous version is kept in a hidden backup folder in the workspace, with Undo in the UI. There is no delete tool.
- **Done when:** approve, deny and undo are tested, including a denied write leaving the file untouched.
- **Effort:** 3–4 days.

### Phase F — Testing and release

- **Scope:**
  - Security review of the tool layer.
  - Prompt-injection suite: files and earlier tool results carrying instructions must never cause an unapproved action.
  - Regression suite.
  - Desktop soak with agent mode (harness as in M4 Phase G, with memory and handle limits).
  - Release notes and troubleshooting entries.
- **Done when:** all gates pass; the release report is written.
- **Effort:** 2–3 days.

**Total:** about 17–25 engineer-days (Phase E included).

## 4. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Small local models produce broken or wrong tool calls | Agent mode feels unreliable | Phase A go/no-go; recommended models; schema validation; errors fed back once, then stop |
| Prompt injection through workspace files | A file steers the model | Read-only by default; approval with a diff for every write; no shell or network; injection tests |
| Path tricks escape the workspace | Access to other files | One resolver, shared with `validate_model_path`; attack tests; links and junctions refused |
| Longer prompts (tool schemas plus results) | Slower replies, VRAM, context overflow | Few, short tool schemas; result size limits; warn near the context limit |
| Schema v2 migration | Data risk | Existing pre-migration backup; v1 → v2 tests; restore from Settings |
| Built-in llama-server tools enabled by accident | Unconfined file and shell access | Phase 0: `--no-agent`, `LLAMA_ARG_*` stripped |

## 5. Test strategy

- **Unit:** registry, argument validation, each tool, workspace resolver, loop (step limit, Stop, errors), streamed tool-call assembly.
- **Integration (mock engine):** full loop through `/api/chat/completions`, approval flow, persistence and reload.
- **GPU (`-m gpu`):** real tool calls with a recommended model.
- **Security:** path attacks, injection fixtures, unknown tools, oversized arguments.
- **Desktop E2E:** CDP harness from M4 (`scripts/e2e`), agent mode on and off.
- **Regression:** the full existing suite (162 tests) and the Stage 6 benchmark unchanged with agent mode off.

## 6. Out of scope for Milestone 5

Command or shell execution, web access or browsing, MCP servers, RAG or document indexing, multiple agents, scheduled tasks, cloud models.

## 7. Release order

0.1.0 still needs G6 (clean machine and older driver), G7 (code signing) and the 4-h soak (see `docs/milestone-4/M4_PHASEG_RELEASE_REPORT.md` §8). Recommended order: Phase 0 as 0.1.1 (or folded into 0.1.0 if it isn't signed yet), then Phases A–F as 0.2.0.

## 8. Decisions for the owner

1. Release 0.1.0 first (recommended), or start Milestone 5 in parallel?
2. Which tools matter most beyond the read-only set in Phase C?
3. Include Phase E (file writes) in 0.2.0, or later?
4. Command execution: confirm it stays out of scope.
5. Approve Phase 0 now?
