# Mustang AI — M4 Phase C Test Report

All results below are from actual runs on the development machine (i7-13620H, RTX 5050 Laptop 8 GB, driver 617.42, Windows 11, WebView2 156) on 2026-11-02, branch `m4-phase-c`.

Automated desktop runs used **scratch data roots** (`MUSTANG_DESKTOP_DATA_ROOT`), so `%LOCALAPPDATA%` was not modified. The legacy import read the real `backend/run/mustang.db`. Every run after the incident (see the implementation report §2) was wrapped in a SHA-256 guard of `models/qwen2.5-0.5b-instruct-q4_k_m.gguf` and `backend/run/mustang.db`: **unchanged in every run**.

## 1. Summary

| Area | Result |
|---|---|
| Backend suite, dev venv (source) | **139 passed** (118 existing + 21 new) |
| Backend suite, embedded runtime (packaged copy, shell environment allow-list, real CUDA inference) | **139 passed** |
| Rust unit tests (`cargo test --lib`, src-tauri) | **8 passed** |
| Supervisor scenarios (`supervisor-poc all`) | **9/9** |
| Frontend `tsc --noEmit` / `vite build` | clean / built |
| Release desktop E2E (real window, real GPU chat) | **pass** |
| Lifecycle: crash ×3 → failure screen → Retry → hard kill | **pass** |
| Graceful close | **pass** (exit 0.67 s, engine unloaded, no GPU process left) |
| First-run import dialog: Import (auto) and Start fresh (real dialog) | **pass** |
| `tauri dev` (backend from source, Vite UI) | **pass** |
| Browser dev workflow (new backend + Vite) | **pass** |

## 2. Backend

```bash
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```
```bash
npm run runtime:test -- -q
```

- Both: `139 passed` (venv 53.6 s, embedded 52.9 s). The embedded run asserts that `app` is imported only from `build/desktop-runtime/backend`.
- New tests (`tests/test_desktop_integration.py`, 21):
  - every one of the >30 OpenAPI operations except `/health` and bootstrap returns 401 without a token;
  - a 401 is readable cross-origin from `http://tauri.localhost`;
  - preflight from the Tauri origin passes;
  - bootstrap rejects no-Origin and near-miss origins and is disabled in desktop mode;
  - the desktop token is not written to disk, and short tokens are rejected;
  - `HOST=0.0.0.0` is rejected;
  - the shutdown endpoint needs a token, returns 409 without the launcher and triggers the hook;
  - a legacy database is backed up then versioned; a newer schema is refused byte-identical; a fresh database gets no backup;
  - legacy import leaves the source byte-identical (WAL mode, path with spaces) and refuses an existing destination;
  - `/health` stays responsive during a 1.5 s blocking estimate: fails without the fix (1.53 s), passes with it;
  - log redaction.
- Embedded-runtime findings while bringing the suite to green: NVML requires `ProgramFiles`/`ProgramW6432` in the environment (`nvidia-smi` exit 255 "Failed to initialize NVML" otherwise). Both are now in the shell's allow-list.

## 3. Release desktop app: end-to-end (`scripts/e2e/desktop_e2e.mjs`)

The driver attaches to the real WebView2 through DevTools (enabled for test runs only via `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`). It clicks New Chat, selects the model, clicks Load Model, types a prompt, clicks Send, waits for the stream to finish and records browser resource timings.

| Run | Origin | Load → engine ready | Chat request | Status-bar speed | Persisted reply |
|---|---|---|---|---|---|
| First launch (import accepted) | `http://tauri.localhost` | ~7 s (cold) | — | 45.9 tok/s (screenshot) | "Mustangs are muscle cars…" (14 chunks) |
| Before event-loop fix | `http://tauri.localhost` | 7.5 s | **8,815 ms** | 3–4.7 tok/s | yes |
| After fix, second launch | `http://tauri.localhost` | 4.7 s | **393–635 ms** | 39–72.7 tok/s | yes |
| After crash-restart (reconnect + reload model) | `http://tauri.localhost` | 1.8 s | 496–539 ms | 38.6–44.3 tok/s | yes |
| After Retry from the failure screen | `http://tauri.localhost` | — | 543 ms | 80.8 tok/s | yes |

Process tree while chatting: `mustang-desktop.exe` → `python.exe` (bundled) → `llama-server.exe` on a dynamic port; 373.7 MiB VRAM, 25/25 layers on GPU.

## 4. Lifecycle (`lifecycle_test.ps1`, release app)

| Step | Expected | Observed |
|---|---|---|
| Second launch | no import prompt; reuse data | no prompt; ready in ~2 s |
| Kill backend Python with model loaded (crash 1) | llama-server terminated with it; restart | llama-server gone **within 0.7 s**; restarted on a new port after 1 s backoff; UI reconnected and chatted |
| Crash 2 | restart (2 s backoff) | restarted; llama-server gone within 0.7 s |
| Crash 3 within 60 s | failure screen | `FAILED: The backend stopped unexpectedly 3 times within 60 s` + log tail + Retry (screenshot) |
| Click Retry (UI) | backend ready, app reloads data | ready; E2E chat passes |
| Hard-kill the app (`Stop-Process -Force`) with model loaded | whole tree gone, VRAM free | `python.exe` and `llama-server.exe` gone; `nvidia-smi --query-compute-apps` empty |
| Close window (`CloseMainWindow`) | graceful | exited in 672 ms; backend log: shutdown requested → downloads stopped → llama-server stopped → "shutdown complete"; `desktop.log`: `stopped: Graceful` |

## 5. First run and data

| Check | Result |
|---|---|
| Import (auto-accept), fresh root | `desktop.json` `{"models_dir":"S:\\Mustang AI\\models","legacy_decision":"imported"}`; table counts identical to source (profiles 4, settings 8); source SHA-256 unchanged; on the first desktop backend start the imported copy (schema v0) was backed up to `data/backups/mustang-schema0-20261102-161123.db`, then marked v1; later starts made no further backups |
| Real dialog shown (no override) | Title "Import existing Mustang AI data", text names the database and models folder, buttons Import / Start fresh (screenshot) |
| Close app while the dialog is open | no decision recorded; next launch asks again |
| Start fresh | `legacy_decision: "declined"`; fresh DB (0 conversations, 4 personas, schema v1); default `<root>\models` created; backend ready; graceful close |
| Token leakage | no 64-hex string in `backend.log`, `desktop.log` or the app's stdout/stderr |

## 6. Development workflows

| Workflow | Result |
|---|---|
| `tauri dev` (reusing the user's Vite on 5173) | runtime source `dev-source` (venv + repo backend), ready in ~1 s, E2E chat 46 tok/s (452 ms request), graceful stop |
| Browser: new backend on 18010 + Vite on 5174 (`VITE_BACKEND_URL`) | bootstrap 200 from the allow-listed origin; all API calls 200 with the token; preflights 200. One 404: `context_recommendation` for a model not yet scanned into the copied dev DB's library (pre-existing behaviour, unrelated to auth) |
| User's own servers (5173, 18000) | untouched throughout |

## 7. Problems found by testing and fixed in this phase

1. **Real models folder reachable from tests** (caused the incident): private hard-linked models folder plus `anon_client` audit.
2. **NVML needs `ProgramFiles`**: added to the environment allow-list.
3. **`resource_dir()` returns `\\?\` paths**: stripped before being passed to Python and llama-server.
4. **Crashed backend left llama-server running until the next spawn**: now terminated immediately (job termination).
5. **Event-loop stalls of 5–8 s after model load** (pre-existing): blocking work moved to threads.
6. **UI after Retry kept stale (empty) data**: the gate unmounts the app on failure and remounts it only once ready.
7. **Raw-TCP shutdown request could be split across segments**: sent with a single `write_all`.
8. **Rust `Option` debug text in user-facing messages**: replaced with plain exit codes.

## 8. Test-harness notes

- **`cargo build --release` is not a release app.** Two early lifecycle runs used it. Without the Tauri CLI the binary runs in dev mode and loads `devUrl` (the user's Vite), so the UI steps of those runs were invalid and were repeated with `npx tauri build --config src-tauri/tauri.release.conf.json`. Process-level results from those runs (restart, failure, hard kill) were consistent with the valid runs.
- The lifecycle and dialog scripts used here are PowerShell scripts in the session scratchpad and are not committed. The reusable E2E driver is committed at `scripts/e2e/desktop_e2e.mjs`.

## 9. Not tested

- First launch against the real `%LOCALAPPDATA%\MustangAI` (deliberately left to the user).
- A machine without the repo (`backend/runtime/bin` fallback absent) or without an NVIDIA driver: Phase D.
- Windows logoff/shutdown while running: Phase E.
- Installer, upgrade and uninstall: Phase F.
