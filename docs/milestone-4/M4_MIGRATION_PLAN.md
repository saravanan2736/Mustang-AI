# Mustang AI — Milestone 4 Migration Plan

Phase A deliverable. An incremental path from the current browser + dev-server setup to a native Windows desktop app. Each phase leaves the app working, and the browser dev workflow (`run_backend.py` + `npm run dev`) keeps working throughout.

---

## 1. Feature-preservation matrix

| Feature | Current implementation | Desktop impact | Preservation strategy | Verified in |
|---|---|---|---|---|
| AI chat & SSE streaming | `POST /api/chat/completions` → `StreamingResponse`; `useChatStream.ts` fetch reader | Base URL becomes dynamic; header token from IPC | Replace the `BACKEND_URL` constant with `getBackendUrl()` resolved at startup (IPC in Tauri, `127.0.0.1:18000` in browser dev). Stream code unchanged. | B, C, G |
| Stop / cancel generation | AbortController → `is_disconnected` → `cancel_generation`; `POST /api/chat/stop` | None | Unchanged; verify the `/slots/0?action=cancel` behaviour on b12000 and add the API key | D |
| Partial-response persistence | shielded `_save_assistant_message` | Also needed on app quit | Shutdown endpoint cancels the stream first so the shield path persists it | C, G |
| Conversation history | SQLite `conversations`/`messages`, WAL | DB moves to `%LOCALAPPDATA%\MustangAI\data` | `MUSTANG_RUN_DIR` env; first-run import copies the existing `mustang.db` | C |
| Regenerate / truncate / edit | `/api/chat/regenerate`, `/conversations/{id}/truncate` | Auth added | Add the token header in `api.ts` (all calls via `getHeaders(true)`) | C |
| Model discovery (Local Library) | `model_library.py` scan of `MODELS_DIR`, GGUF parser | Models root configurable | `MUSTANG_MODELS_DIR`; IDs relative to root stay stable; rescan on change | C |
| Hugging Face search | `hf_client.py` → huggingface.co | Outbound HTTPS from Python only | Unchanged; CSP does not affect the backend. "Open on HF" link via `opener` | B, C |
| Model downloads | `download_manager.py`, async worker, atomic `os.replace` | Downloads dir on the models volume | `MUSTANG_DOWNLOADS_DIR = <models>\.downloads` (same volume, so the rename stays atomic) | C |
| Download resume | HTTP Range/If-Range, PAUSED recovery in `init_db` | App quit mid-download | Graceful shutdown marks `PAUSED`; crash path already recovered by `init_db` | C, G |
| SHA-256 verification | download manager + HF LFS hashes | None | Unchanged | C |
| Download progress SSE | `EventSource` `/api/downloads/progress` | Needs auth header | Switch to fetch-stream reader (small change in `DownloadQueue.tsx`) | C |
| Model load / unload | `/api/models/load` → `LlamaServerEngine.start` | Engine port dynamic, API key | `MUSTANG_ENGINE_PORT`, `MUSTANG_ENGINE_API_KEY`; supervisor unchanged | D |
| Model switching + rollback | `_execute_safe_transition` | None | Unchanged; covered by the existing `test_model_switching.py` | D |
| Dynamic context recommendations | `memory_estimator.py`, `/models/{id}/context_recommendation` | None | Unchanged | D |
| CUDA hardware telemetry | `nvidia-smi` subprocess, psutil | Console flash risk; event-loop block | Absolute `System32` path, `CREATE_NO_WINDOW`, `asyncio.to_thread` | D |
| Chat settings | `app_settings` table, `/api/settings` | Auth added | Token header | C |
| Personas / profiles | `profiles` table with built-ins incl. "Direct & Unrestricted" | None | Unchanged; seeded via `INSERT OR IGNORE` | C |
| OpenAI-compatible `/v1` | `openai.py` | Auth added | Token-protected; documented as internal in M4 | C |
| Obsidian Dark UI | Tailwind theme, `index.html` | Native title bar | Dark window theme; add missing `public/mustang-logo.svg`; app icon from same artwork | B |

Every feature is preserved through the same code path. The changes are limited to configuration injection, auth headers and two transport tweaks (dynamic base URL, EventSource → fetch).

---

## 2. Phases

### Phase B — Tauri desktop shell

- **Scope:** Install the Rust toolchain (MSVC) and Tauri CLI. Create `src-tauri/` and a native window that loads `frontend/dist` (prod) or Vite (dev). Add single-instance, the dark theme and the app icon. Add the `backend_info` IPC command, temporarily returning `http://127.0.0.1:18000` while the backend is still started by hand. Add a frontend `getBackendUrl()` abstraction. Run a **WebView2 → 127.0.0.1 fetch/SSE spike** (risk R1) first.
- **Files:** new `src-tauri/{Cargo.toml,tauri.conf.json,build.rs,src/main.rs,src/lib.rs,capabilities/main.json,icons/*}`; `frontend/src/services/api.ts`, `frontend/src/hooks/useChatStream.ts`, `frontend/src/components/DownloadQueue.tsx` (base URL only); `frontend/package.json` (`@tauri-apps/api`, `@tauri-apps/cli` dev dep); `frontend/public/mustang-logo.svg`; `backend/app/config.py` (add `http://tauri.localhost`).
- **Dependencies:** Rust stable + MSVC Build Tools + Windows SDK; Phase A approval.
- **Risks:** R1 (WebView2 local-network access), R12 (toolchain setup).
- **Testing:** `npm run build` and `tsc` clean; `tauri dev` loads the UI; chat streams, stop works and downloads show progress against a manually started backend; browser dev mode still works.
- **Done when:** A native window runs the full existing UI against the existing backend with no feature regression, and the CSP is enabled.
- **Effort:** 4–6 engineer-days.

### Phase C — Python backend packaging and lifecycle

- **Scope:** Embeddable Python build script; `mustang_launcher.py`; Rust supervisor (port picking, token generation, env allowlist, Job Object, spawn with `CREATE_NO_WINDOW`, `/health` wait with a splash/loading state, crash restart with backoff ×3, graceful shutdown endpoint). Backend: env token, desktop-mode bootstrap disable, router-level auth, shutdown endpoint, schema_version + pre-migration backup, file logging. Data dir creation and first-run legacy import. Test isolation (`conftest.py` uses a temp `MUSTANG_RUN_DIR`/`MODELS_DIR`).
- **Files:** new `scripts/build_python_runtime.ps1`, `backend/requirements.lock`, `backend/mustang_launcher.py`, `src-tauri/src/{backend.rs,job.rs,paths.rs,migration.rs}`; changed `backend/app/core/security.py`, `app/api/auth.py`, `app/api/system.py`, `app/main.py`, `app/db/database.py`, `app/config.py`, `backend/tests/conftest.py`, new auth/shutdown tests; `frontend/src/services/api.ts` (token on every call), `DownloadQueue.tsx` (fetch stream).
- **Dependencies:** Phase B.
- **Risks:** R3 (embedded Python import gaps), R5 (orphans), R8 (data migration), R10 (auth tightening breaks a call).
- **Testing:** the full existing pytest suite (120 tests) passes against both venv and embedded Python; new tests check that every route except `/health` returns 401 without a token. Kill-tests: kill `Mustang AI.exe` via Task Manager → no `python.exe`/`llama-server.exe` survives. Port-collision test: start with 18000 occupied → app still starts.
- **Done when:** Double-clicking `tauri build` output starts everything with no console windows; quit leaves no processes; data lives in `%LOCALAPPDATA%\MustangAI`; the legacy DB is imported intact.
- **Effort:** 7–10 engineer-days.

### Phase D — llama.cpp and CUDA integration

- **Scope:** Build script fetches the pinned CUDA 13.4 zips (reusing hashes from `setup_runtime.py`), prunes to the DLL closure, and writes `runtime-manifest.json`. Engine: `--api-key`, Authorization headers, no PATH fallback in desktop mode, `CREATE_NO_WINDOW`, `top_k` forwarding fix, verified cancel endpoint. Telemetry: absolute `nvidia-smi` path, `to_thread`. Rust: startup driver-version check and runtime integrity check; CPU-fallback messaging.
- **Files:** `scripts/build_llama_runtime.ps1` (or extend `setup_runtime.py` with a `--prune --manifest` mode), `backend/app/core/llama_engine.py`, `backend/app/core/hardware.py`, `backend/app/config.py`, `src-tauri/src/runtime_check.rs`, `src-tauri/tauri.conf.json` (resources).
- **Dependencies:** Phase C.
- **Risks:** R2 (DLL closure), R4 (driver mismatch), R6 (CPU fallback), R7 (cancellation semantics).
- **Testing:** `test_live_inference`, `test_model_switching`, `test_engine_lifecycle`, `test_context_allocation` with `-m gpu` against the *packaged* runtime; tokens/s within 5 % of the Stage 6 benchmark (`run/stage6_benchmark_results.json`); a direct request to the engine port without the API key gets 401; a VM without an NVIDIA driver loads a small model on CPU.
- **Done when:** The packaged app loads, switches and unloads GGUF models on CUDA with parity to dev performance, and fails clearly on a bad driver.
- **Effort:** 4–6 engineer-days.

### Phase E — Native Windows integration

- **Scope:** System tray (show/hide, status, unload, quit); close-to-tray setting; graceful handling of logoff/shutdown; window state persistence; native folder picker for the models dir; external links via opener; app notifications for download completion (optional, local only); optionally prevent sleep while generating or downloading.
- **Files:** `src-tauri/src/{tray.rs,lifecycle.rs,commands.rs}`, `src-tauri/capabilities/main.json`, `frontend/src/components/SettingsModal.tsx` (models folder, close behaviour), `HuggingFaceDiscovery.tsx` (opener).
- **Dependencies:** Phase C (supervisor), Phase D.
- **Risks:** R9 (shutdown on logoff), R11 (WebView2 suspension in tray).
- **Testing:** Manual matrix — close/minimize/quit/logoff during generation and during download; verify a PAUSED download resumes after restart and partial chat output is persisted.
- **Done when:** The app behaves like a native Windows app; no data loss in any quit path.
- **Effort:** 4–6 engineer-days.

### Phase F — Installer and update mechanism

- **Scope:** NSIS per-user installer; icons, license, start menu and desktop shortcuts; custom uninstall messaging; code signing pipeline; updater with minisign keys, opt-in UI and a manifest host; DB backup/restore UI; version single-sourcing (`config.py`, `system.py`, `package.json`, `tauri.conf.json`).
- **Files:** `src-tauri/tauri.conf.json` (bundle), `src-tauri/windows/installer-hooks.nsh`, `scripts/build_release.ps1`, `scripts/sign.ps1`, `frontend/src/components/SettingsModal.tsx` (updates, backups), `backend/app/api/system.py` (version from settings).
- **Dependencies:** Phases B–E; signing certificate; update hosting (e.g. GitHub Releases).
- **Risks:** R13 (installer size/time), R14 (SmartScreen/AV), R15 (updater key loss), R8 (data deletion).
- **Testing:** clean install on fresh Windows 11 VM (no Python, no Node, no VS); upgrade N-1 → N with data intact; uninstall with and without the "delete app data" box → models and DB still present; downgrade path; tampered update rejected.
- **Done when:** A signed installer installs, upgrades and uninstalls without touching user data.
- **Effort:** 5–8 engineer-days (plus certificate procurement lead time).

### Phase G — Full desktop integration and release testing

- **Scope:** Execute `M4_TEST_AND_RELEASE_PLAN.md` end-to-end; soak tests; performance parity; security verification; documentation (README desktop section, troubleshooting); release candidate.
- **Files:** `README.md`, `docs/milestone-4/*` (results), test scripts under `backend/tests/` and `scripts/`.
- **Dependencies:** Phases B–F.
- **Risks:** late regressions; driver variance.
- **Testing:** see the test plan.
- **Done when:** All release gates in the test plan are green.
- **Effort:** 5–8 engineer-days.

## 3. Effort summary

| Phase | Engineer-days |
|---|---|
| B — Tauri shell | 4–6 |
| C — Python packaging & lifecycle | 7–10 |
| D — llama.cpp & CUDA | 4–6 |
| E — Native Windows integration | 4–6 |
| F — Installer & updates | 5–8 |
| G — Integration & release testing | 5–8 |
| **Total** | **29–44 engineer-days (~6–9 weeks for one engineer)** |

Code-signing certificate procurement (1–10 days of external lead time) runs in parallel and is not included.
