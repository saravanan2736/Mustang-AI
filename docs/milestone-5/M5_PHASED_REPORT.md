# Mustang AI — Milestone 5 Phase D: Agent mode in the UI

Branch `m5-phase-d` (from `m5-phase-c`). Plan: `M5_AGENTIC_PLAN.md`, Phase D.

Status: **done.** Agent mode can be switched on per chat, with a workspace folder. Tool steps show as cards while the model works and after a reload. Stop works, and plain chat is unchanged when agent mode is off. Tested in browser dev mode with a real model, and in the desktop app (§3).

## 1. What the user sees

- **Agent mode switch** above the prompt box, per chat. Off by default; a new chat starts with it off.
- **Workspace folder:**
  - In the desktop app, *Choose workspace folder* opens the native folder picker (new Rust command `choose_workspace_folder`, granted only to the main window like the other commands).
  - In browser dev mode, the app asks for a path.
  - The chip shows the folder name (full path on hover), with × to close the folder (file tools off).
  - A line says what agent mode can do: "Can read files in this folder. Never changes, deletes or runs anything", or "No folder: time, calculator and chat search only".
- **Tool-step cards** inside the assistant's reply, one per call:
  - The header shows a status icon (running, done, failed), a plain label ("Read file", "Searched files", "Calculated", …), the key argument (path, query or expression) and the step number.
  - Clicking a card shows the exact tool name, its arguments and the result (first 2,000 characters).
  - Text the model wrote before a call ("Let me check.") appears above that card. The answer follows the cards.
  - If the loop ended early (step limit, repeated calls, failing tools, stopped), a short note says why.
- **Model guidance:** with agent mode on and a model under ~3B parameters loaded (from the file name), a note recommends Qwen3.5 4B, Gemma 4 E4B or larger (M5 Phase A).
- **Stop** works as before and also ends the agent loop.

## 2. Changes

| Area | Files | Change |
|---|---|---|
| Backend | `app/db/database.py`, `app/api/conversations.py` | Schema v2 also adds `conversations.agent_enabled` and `workspace` (same additive migration; v2 isn't released yet). Create/PATCH/detail accept and return them; the workspace is validated with the agent's own `Workspace` rules and stored resolved; `""` clears it; a bad folder returns 400 `Workspace: …` |
| Desktop shell | `src-tauri/src/desktop.rs`, `lib.rs`, `build.rs`, `capabilities/main.json` | `choose_workspace_folder`: native picker, returns the folder or `null`. The backend validates it again on every use. Same test hook as the models-folder picker (`MUSTANG_DESKTOP_TEST_PICK_FOLDER`) |
| UI state | `App.tsx` | Agent mode and workspace load with each chat, are saved on change, are given to a chat created by the first send, and are passed with send, regenerate and edit |
| Streaming | `hooks/useChatStream.ts`, new `services/agent.ts` | `agent` events update the live message (`applyAgentEvent`). History sent to the backend is text turns only (`historyForRequest`), since the backend refuses tool turns from clients |
| Reload | `services/agent.ts` (`groupAgentMessages`) | Saved rows (assistant with `tool_calls`, `tool` results) turn back into cards on the answer, so a reloaded chat looks like it did live. A reply that ended during its tool steps shows its cards and a "stopped" note |
| Components | new `AgentBar.tsx`, `ToolSteps.tsx`; `ChatInterface.tsx` | The bar, the cards, and no typing cursor on a card that is still running |
| Types and API | `types/index.ts`, `services/api.ts`, `services/desktop.ts` | Tool fields on messages, agent fields on conversations; `updateConversation` now shows the backend's error text |

## 3. Tests

| Check | Result |
|---|---|
| Frontend `tsc --noEmit` | clean |
| Rust `cargo check` | clean |
| Backend pytest | **285 passed, 1 skipped** (+4 tests: agent settings saved per chat, cleared with `""`, a plain chat stays off, bad folders refused on create and PATCH) |
| Data guard | unchanged |

**Browser dev mode, real model.** Setup: an isolated source backend (scratch data folder, port 18555), a second Vite on port 5174 pointed at it, Qwen3.5 4B Uncensored at 16K context, and the scratch workspace from Phase C. The in-app browser drove the real UI:

| Step | Result |
|---|---|
| Switch agent mode on, choose the workspace | the chip shows `agent-workspace`, with the "can read files" line |
| "What files are in my workspace and what does notes.txt say?" | cards **Listed files `.`** (step 1) and **Read file `notes.txt`** (step 2) with green checks, then the correct answer; expanding a card shows `read_file` with `{"path":"notes.txt"}` and the file text |
| Reload the page | same cards on the same answer; agent mode and workspace restored (backend: `agent_enabled: true`, workspace saved) |
| Follow-up "Add up the amounts in budget.csv." in the same chat | **Read file `budget.csv`**, **Calculated `1200+450+180`**, then "**1830**": the saved tool history doesn't break the request |
| Long task, then Stop | the reply ends at once, mid-sentence; the engine is back to READY |
| Agent mode off, "What does notes.txt say?" | plain reply with no cards and no tool calls; the request had no `agent` field |
| Browser console | no errors from the new code (see §4 for 4 unrelated 404s) |

**Desktop app:** see §3.1.

### 3.1 Desktop app

Setup: a release build (`npm run desktop:build`, which rebuilds the bundled runtime with this backend), started with a scratch data root whose models folder is `build\test-models`. The WebView was driven over CDP (`scripts/e2e/cdp_eval.mjs`). The native dialog itself can't be clicked by a script, so the command ran through its test hook (`MUSTANG_DESKTOP_TEST_PICK_FOLDER`, as for the models-folder picker).

| Step | Result |
|---|---|
| `invoke('choose_workspace_folder')` from the WebView | returns the folder: the command is registered and granted to the main window |
| Load Qwen3.5 4B through the shell's backend; create a chat with agent mode and that folder; reload | the UI opens it with the switch on (`aria-checked="true"`) and the workspace chip |
| Type "What files are in my workspace and what does notes.txt say?" in the real prompt box and press Enter | cards **Listed files `.`** and **Read file `notes.txt`** (both in one step), then the correct answer |
| Reload | the same cards on the same answer |
| Error text in the UI | none |
| Quit (`--quit`) | app exited; backend stop **Graceful** |
| Workspace afterwards | all files present (`docs`, `budget.csv`, `notes.txt`, `readme.txt`) |

## 4. Notes

- **Context recommendation 404 for unscanned models.** The model selector's context recommendation returned 404 for a model in a subfolder that wasn't in the model library yet. The library is only filled when it is empty and the Library is opened, or on Rescan. This existed before Phase D (it isn't agent code); the selector shows no recommendation in that case. Worth a small fix later: fall back to reading the file's header, as the estimate does.
- **The status pill** can say "Generating" for up to 2.5 s after Stop (it polls); unchanged from before.
- **Approve/Deny for actions that change files** comes with the write tools in Phase E; all current tools are read-only, so nothing needs approval yet.
