# Mustang AI — M4 Phase E Test Report

Real runs on 2026-11-02 (i7-13620H, RTX 5050 Laptop 8 GB, Windows 11, WebView2 156), release builds from `npm run desktop:build` / `npx tauri build --config src-tauri/tauri.release.conf.json`, scratch data roots. The real `models/qwen2.5-0.5b-instruct-q4_k_m.gguf` and `backend/run/mustang.db` were SHA-256-guarded: **unchanged**. The user's dev servers (5173, 18000) were untouched, and no app or llama-server processes were left after any run.

## 1. Summary

| Area | Result |
|---|---|
| Backend suite, dev venv / embedded runtime | **150 / 150 passed** (149 + 1 new) |
| Rust unit tests | **15 passed** (new: link allow-list, app URLs, models-folder validation, argument mapping, close-to-tray setting) |
| Probe build, `tsc` | clean |
| Desktop scenarios (`phase_e_scenarios.ps1`) | all pass |
| Models-folder flow (`phase_e_folder.ps1`) | all pass |
| Final E2E on the last build | chat OK (44.3 tok/s), `--quit` graceful |

## 2. Scenario results (release app)

| Scenario | Method | Result |
|---|---|---|
| Launch | start app | window visible; tray reported active; E2E chat OK (48.5–54 tok/s) |
| Navigation guard | `location.href = 'https://example.com/'` via DevTools | page stays on `http://tauri.localhost`; `blocked navigation: https://example.com/` logged |
| Allow-listed external link | synthetic `<a target=_blank href=https://huggingface.co/…>` click | `opening external link: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF` (dry-run hook: browser not actually launched) |
| Other external link | `<a target=_blank href=https://example.com/phish>` | `blocked external link` logged; nothing opened |
| Popup | `window.open('https://example.com/popup')` | returns `null`; still one app window |
| Close to tray | `CloseMainWindow` (WM_CLOSE) | process alive, window hidden, backend not stopped; one-time hint dialog shown and dismissed; not shown again on the next close |
| Reopen | plain second launch | window visible again; still exactly one app process |
| Keep-awake | long generation | `keep-awake on (generating or downloading)` then `keep-awake off` |
| `--unload-model` | second launch with flag | backend log `llama-server stopped and unloaded`; 0 llama-server processes |
| `--quit` | second launch with flag | app exited; `stopped: Graceful`; no leftovers |
| Window state | `SetWindowPos` → 140,110 1100×720 → `--quit` → relaunch | restored exactly `140,110,1100x720`, visible |
| Quit during generation | long prompt, `--quit` after 1.5 s | exit in **2.1 s**, graceful, no leftovers; **partial reply persisted** (363 chunks / 1,855 chars) *before* the exit was triggered |
| Simulated logoff during generation | real `WM_QUERYENDSESSION` (→ `1`, allow) then `WM_ENDSESSION(TRUE)` to the main window | exit in **2.2 s**; shell log: "Windows session ending: stopping the backend" → `stopped: Graceful` → "backend stopped, exiting"; partial reply persisted (383 chunks); no leftovers |
| `--quit` with no instance running | flag on first launch | exits immediately (used throughout the harness) |

### Before / after the shutdown fix
| | Before | After |
|---|---|---|
| Quit during a long reply | uvicorn waited for the reply to finish (1,024 tokens, ~2.4 s here; would exceed the 10 s grace on slow models and then lose the reply) | generation cancelled first, partial reply saved at once |
| Exit time (`--quit` / logoff) | 3.4 s / 3.3 s | 2.1 s / 2.2 s |

## 3. Models folder (`phase_e_folder.ps1`, dialog answer via `MUSTANG_DESKTOP_TEST_PICK_FOLDER`)

| Step | Result |
|---|---|
| Change to an empty scratch folder | `models folder set to …models-alt`; engine restarted; UI reloaded (Settings closed); model list "No models in models/ directory"; `desktop.json` updated; `.downloads` created in the chosen folder; validation probe file removed |
| `C:\Windows` | refused: "C:\Windows is inside a system or application folder (C:\Windows). Choose another folder."; setting unchanged |
| `\\server\share\models` | refused: "…is a network location. Choose a folder on a local drive." |
| Use default | `models_dir: null` (→ `<root>\models`); engine restarted |
| Restore `S:\Mustang AI\models` | engine restarted; the qwen model listed again; no probe file left in the real folder |
| Native picker itself | opens with title "Choose the folder for your GGUF models" (verified, then cancelled; see the implementation report §4) |

## 4. Incident (see implementation report §4)

One automated run accepted the folder picker's default location (Documents), which created an empty `C:\Users\<user>\Documents\.downloads`. It was removed after checking it was empty and created by the run; Documents was clean on every later check.

## 5. Not tested

- A real Windows logoff, shutdown or sleep on the user's session (simulated with genuine session-end messages; plan for a clean VM in Phase G).
- `powercfg /requests` confirmation of keep-awake (requires elevation; verified through the shell's own log).
- Clicking the OS tray popup menu (its handlers share `perform()` with the tested command-line actions).
- Opening the user's real browser (dry-run hook).
- Live download interrupted by quit: no network download was started in this phase. The download manager's stop → PAUSED path runs in the same graceful shutdown (log "Download Manager worker stopped") and is covered by the backend suite.
