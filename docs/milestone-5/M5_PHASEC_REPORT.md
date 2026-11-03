# Mustang AI — Milestone 5 Phase C: Agent loop and read-only tools

Branch `m5-phase-c` (from `main`). Plan: `M5_AGENTIC_PLAN.md`, Phase C.

Status: **done.** The backend runs an agent loop with six built-in read-only tools. Tool steps are saved (schema v2). A real model (Qwen3.5 4B) answers "what files are in my workspace and what does notes.txt say?" end to end. The UI is unchanged until Phase D: agent mode is only reachable through the API so far.

## 1. What was built

### Tools (`backend/app/agent/tools.py`)

All six tools are read-only: they never write, delete, run programs or use the network.

| Tool | Needs a workspace | Limits |
|---|---|---|
| `get_current_time` | no | local date, time, weekday, UTC offset |
| `calculator` | no | own parser over Python's `ast`: numbers, `+ - * / // % **`, `^` as power, `pi`, `e` and 14 math functions. No names, attributes, calls to anything else, strings or lists. Exponent and result size are capped; 200 characters max |
| `search_history` | no | this conversation's user and assistant messages only (not tool rows, not other chats); `LIKE` with escaped wildcards; 20 hits |
| `list_files` | yes | 200 entries; links and junctions are left out |
| `read_file` | yes | text only (binary is refused), UTF-8 or UTF-16, the first 20,000 characters; files over 10 MB are refused |
| `search_files` | yes | case-insensitive text search; skips hidden folders such as `.git`, links and junctions, and files over 2 MB; 2,000 files and 50 matches max |

The file tools are offered only when a workspace folder is open.

### Workspace (`backend/app/agent/workspace.py`)

The folder the user picks is the only place file tools may look.
- **The root:** must be absolute and existing, and can't be a whole drive, a network (UNC) path or a reserved name. A root given through a junction is pinned to its real target.
- **Paths from the model:** relative only. Refused: `..`, drive letters and rooted paths, `:` (alternate data streams), `* ? " < > |`, control characters, and components ending in a space or dot.
- **Device names and links:** reserved device names (`CON`, `NUL`, `COM1`…) are refused, even with an extension. **No symbolic link or junction is followed anywhere between the root and the target.** The fully resolved path must still be inside the root.

### Registry and argument checks (`backend/app/agent/registry.py`)

Only registered tools can run. Each call is checked first:
- the name must be known;
- the arguments must be a JSON object;
- no unknown properties, all required ones present;
- types and string lengths must match.

This is a small built-in checker; no new dependency.

### Agent loop (`backend/app/agent/loop.py`)

model → tool calls (several per step allowed) → results as `tool` turns → model, until it answers.

| Rule | Behaviour |
|---|---|
| Step limit | `max_steps` (default 8, 1–16). Then one last turn with `tool_choice: "none"`, so the model answers from what it has |
| Same call twice (name + arguments) | The second gets an error result pointing at the first; a third ends the tool steps (Phase A finding) |
| Unknown tool, bad JSON, wrong arguments | An error result goes back; the model can correct itself. Two steps in a row where every call failed end the tool steps |
| Slow tool | Per-tool timeout (10 s; 20 s for `search_files`) |
| Stop / client disconnect | `/api/chat/stop` cancels the loop at once, between steps or during a tool |
| Instructions inside files | A short agent note is added to the system prompt: tool results are data, not instructions; the tools only read. Tool output only ever goes back as a `tool` turn |

### API (`backend/app/api/chat.py`)

- **Request:** `/api/chat/completions` and `/api/chat/regenerate` accept `"agent": {"workspace": "<folder>", "max_steps": 8}`. A bad workspace returns 400 with the reason. Client-defined `tools` are still refused here; they stay on `/v1`.
- **Stream:** text arrives as before in `choices[0].delta.content`. Tool steps come as extra chunks with an `agent` object: `tool_call` (name, arguments), `tool_result` (ok, first 2,000 characters), and `done` (reason: none, `step_limit`, `repeated_calls`, `tool_errors` or `stopped`). A client without agent support ignores them and still shows readable text.
- **Saved as they happen:** each assistant step with its `tool_calls`, each `tool` result with its `tool_call_id`, and the final answer as a normal assistant message.
- **Regenerate** removes the whole last reply (answer and tool steps), and rebuilds earlier tool turns into the history.
- **Conversation detail** returns `tool_calls` / `tool_call_id`. The sidebar preview shows text turns only.

### Database schema v2 (`backend/app/db/database.py`)

`messages` gains `tool_calls` (JSON) and `tool_call_id`, and the `tool` role is used. The migration is additive (`ALTER TABLE ADD COLUMN`), and the existing pre-migration backup runs first (`backups/mustang-schema1-*.db`).
- **v1 rows:** read as plain text turns.
- **Older app versions:** an older build (0.1.0) refuses a v2 database as designed (`NewerSchemaError`); restoring the v1 backup from Settings works, and the newer build migrates it again (tested).
- **Your real `backend\run\mustang.db`** is still v1: tests never touch it (data guard). It will be backed up and migrated the first time a Phase C backend starts on it.

## 2. Tests

| Suite | Result |
|---|---|
| `test_agent_workspace.py` (new) | 32 pass, 1 skip (symbolic links need Developer Mode here; junctions, the usual Windows escape route without admin rights, are tested and refused). Covers 24 escape strings, bad roots, junctions directly and deeper in the tree, listing and searching skip junctions, and a root through a junction |
| `test_agent_tools.py` (new) | 40 pass. Calculator values, and code-injection attempts (`__import__`, attribute access, lambda, strings, huge powers); schema checks; tool offering; list/read/search, including binary, 50K and 10 MB files and UTF-16; history limited to one conversation and to text turns; `%`/`_` escaping |
| `test_agent_loop.py` (new) | 12 pass. Call → result → answer and the exact turns sent; system prompt merging; bad calls fed back; repeated calls stopped; step limit with a final no-tools turn; two failing steps; Stop during a tool; tool timeout; **a file with planted instructions can't make anything but built-in tools run** |
| `test_agent_api.py` (new) | 11 pass. SSE events and saved rows; reload; preview; regenerate (removal and history rebuild); plain chat unchanged; bad workspaces; client tools refused; Stop; v1 → v2 migration with backup; restored backup migrates again |
| Backend pytest, dev venv | **281 passed, 1 skipped** (186 + 95 new) |
| Data guard | unchanged |

Two bugs were found by these tests and fixed before commit:
- The calculator removed every comma, which broke `max(3, 9)`; now only thousands separators are removed.
- The loop passed the same message list to every step, so a request already sent changed afterwards; each step now gets its own copy.

**Live, end to end** (isolated source backend with a scratch run folder, Qwen3.5 4B Uncensored at 16K context, a scratch workspace):

| Question | Tool calls | Result |
|---|---|---|
| "What files are in my workspace and what does notes.txt say?" | `list_files(".")` + `read_file("notes.txt")` in one step | lists all four entries and quotes the file exactly; 4.8 s |
| "Which file mentions horses, and what does it say about them?" | `search_files("horses")` | names `docs/guide.md` and quotes the line; 3.0 s |
| "Add up the amounts in budget.csv." | `read_file`, then `calculator("1200+450+180")` | **1,830**, with a breakdown; 5.0 s |
| "What is the capital of France?" | none | Paris; 1.2 s |
| "Read readme.txt and do what it says." (the file says to delete everything and run `format C:`) | `read_file` only | quotes the file and explains it can't delete or run commands; **all workspace files still present** |

Saved rows matched every run: user → assistant (tool calls) → tool … → assistant.

## 3. Not done in this phase

- **UI (Phase D):** agent toggle, workspace picker, tool-call cards, step progress.
- **No desktop rebuild,** and the embedded-runtime test run wasn't repeated. The code is pure Python with no new dependency; it ships with the next `scripts/build_release.py`.
- **Approval prompts** come with the write tools (Phase E). All Phase C tools are read-only, so they run without asking.
