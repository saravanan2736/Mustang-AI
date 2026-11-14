# Mustang AI — Milestone 5 Phase E: File changes with approval and Undo

Branch `m5-phase-e` (from `m5-phase-d`). Plan: `M5_AGENTIC_PLAN.md`, Phase E.

Status: **done.** Agent mode can create and edit text files in the workspace. **Every change is shown as a diff and written only after the user approves it.** The previous version is kept, and Undo puts it back. Tested with a real model in the UI.

## 1. What the user sees

- **Approval card.** When the model wants to change a file, the reply shows a highlighted card: "Edit notes.txt (+1 −1 lines)" or "Create groceries.md (5 lines)". It holds a colored diff and **Approve change** / **Deny**, with "Nothing is written unless you approve". The model waits for the answer.
- **After approval,** the card becomes "Edit file `notes.txt`" with an **Undo** button. Undo puts back the previous version (or removes a file the agent created), and the card then shows "(undone)".
- **After a denial,** the card shows "Create file (denied)" and the model is told the change was refused.
- **The agent bar** now says: "Reads files in this folder; asks you before every change (with Undo). Never deletes or runs anything."
- The states (applied, undone, denied) are the same after a reload.

## 2. Safety rules

| Rule | How |
|---|---|
| Nothing written without approval | Write tools return a *proposal* (summary and diff); the loop sends it as an `approval_required` event and waits. Only `POST /api/chat/approve` with `approved: true` runs it |
| No answer = no change | Deny, Stop, or no answer within 10 minutes (`APPROVAL_TIMEOUT_S`) leave the workspace untouched |
| Invalid changes never reach you | Checked before asking: path inside the workspace (all Phase C rules), the file exists or doesn't as the tool requires, the folder exists, `old_text` found exactly once, sizes |
| No program files | Never created or edited, even with approval: `.exe .com .bat .cmd .ps1 .psm1 .vbs .vbe .js .jse .wsf .wsh .msi .msp .scr .pif .lnk .url .hta .cpl .dll .sys .reg .jar .appref-ms` |
| No deleting, no overwriting | No delete tool and no whole-file overwrite: `create_file` refuses existing files; `edit_file` replaces one exact, unique piece of text |
| What you approved is what gets written | Just before writing, the file's SHA-256 is compared with the one shown. If you or another program changed it meanwhile, nothing is written |
| Safe writes | A temporary file in the same folder, then an atomic rename; Windows line endings (CRLF) and a UTF-8 BOM are kept; UTF-16 and non-UTF-8 files are refused |
| Undo never loses your edits | Undo works only while the file is still exactly what the agent wrote; otherwise it refuses and says why |
| Backups out of the agent's reach | `.mustang-backups` in the workspace (hidden attribute) holds the previous versions and `journal.json`. The agent's tools can't list, search, read or write it |
| Injection | Planted instructions in a file can at most make the model *propose* a change, which you see as a diff and can deny (tested) |

## 3. Changes

| Area | Files |
|---|---|
| Change journal: backup, atomic write, SHA-256 checks, Undo | new `backend/app/agent/changes.py` |
| `create_file`, `edit_file`: checks and diff preview | `backend/app/agent/tools.py` |
| `Proposal`, `needs_approval` | `backend/app/agent/registry.py` |
| Approval wait, Stop/timeout, apply after approval, system-prompt wording | `backend/app/agent/loop.py` |
| Backup folder hidden from tools | `backend/app/agent/workspace.py` |
| `approval_required` event; `POST /api/chat/approve`, `GET /api/chat/changes`, `POST /api/chat/undo` (token-protected like every chat route) | `backend/app/api/chat.py` |
| UI: approval card with diff, Approve/Deny, Undo, denied/undone states, the chat's change list | `frontend/src/components/ToolSteps.tsx`, `ChatInterface.tsx`, `AgentBar.tsx`, `App.tsx`, `services/agent.ts`, `services/api.ts`, `types/index.ts` |

## 4. Tests

| Suite | Result |
|---|---|
| `test_agent_changes.py` (new, 33 tests) | pass. Diff before write and nothing written early. Create refusals (exists, missing folder, 4 program types, outside, backup folder, too long). Exact single replace. CRLF+BOM kept. Edit refusals (not found, empty, same, duplicate, binary, UTF-16, Latin-1, `.cmd`). **File changed after preview is not overwritten.** Undo restores the exact bytes, refuses a second time, removes a created file, **refuses after your own edits**, and is scoped to the chat. Backup folder invisible. Loop: approve applies, deny/Stop/timeout write nothing, invalid changes never shown, unknown approval ignored, planted instructions still need approval |
| `test_agent_api.py` (+3) | pass. Approve over the API while the reply streams (`tool_call` → `approval_required` → `tool_result` → `done`), list, undo, second undo 409; deny writes nothing; edge cases (unknown approval 404, no workspace 400, unknown chat 404) |
| Backend pytest, dev venv | **321 passed, 1 skipped** (285 + 36) |
| Frontend `tsc --noEmit` | clean |
| Data guard | unchanged |

**Live in the UI** (browser dev mode, isolated test backend, Qwen3.5 4B Uncensored, scratch workspace):

| Step | Result |
|---|---|
| "In notes.txt, change 'Buy milk.' to 'Buy milk and eggs.'" | *Read file*, then an approval card "Edit notes.txt (+1 −1 lines)" with the red/green diff; the file is **still unchanged** while it waits |
| Approve | the file now says "Buy milk and eggs."; the card shows Undo; the model confirms |
| Undo | the file is back to "Buy milk."; the card reads "Edit file (undone)"; `.mustang-backups` has the hidden attribute and holds the backup and journal |
| "Create a new file groceries.md with a list of three groceries." | an approval card with the 5-line new file |
| Deny | "Create file (denied)"; the model says it wasn't created; **`groceries.md` does not exist** |
| Reload | "Edit file (undone)" and "Create file (denied)" as before |

One bug was found live and fixed: after a reload, step numbers carried on across replies ("step 3" for a reply's first step). They now restart per reply.

## 5. Not done

- **Desktop app:** not rebuilt or retested. The Rust shell didn't change in this phase; the UI and backend are the same code that ran in Phase D's desktop check.
- **Diff after reload:** a reloaded change card shows its arguments and result. The diff is kept only while the reply streams (not stored).
- **Changes from earlier workspaces:** the chat's change list and Undo cover the current workspace folder. Changes made while another folder was open stay in that folder's journal.
