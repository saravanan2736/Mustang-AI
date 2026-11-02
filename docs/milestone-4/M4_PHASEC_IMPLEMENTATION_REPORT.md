# Mustang AI — M4 Phase C Implementation Report

**Phase C: Python backend packaging and Rust-owned lifecycle. Status: complete; awaiting approval for Phase D.**
Branch `m4-phase-c` (from `m4-phase-b`). Date: 2026-11-02.

---

## 1. Outcome

Double-clicking the release build (`src-tauri/target/release/mustang-desktop.exe`) now:

1. Prepares `%LOCALAPPDATA%\MustangAI\` (data, logs, default models folder).
2. On first run, finds the existing installation and **asks** whether to import it. The import copies the database read-only and points at the existing models folder, which is not moved.
3. Starts the **bundled** CPython 3.11.9 backend inside a Job Object. It uses free loopback ports and a per-launch session token, and the window shows no console.
4. Shows a start-up splash, then the unchanged Mustang UI. All API calls are authenticated, and chat streams from llama-server on the GPU.
5. Detects backend crashes and restarts the backend, up to 3 times in 60 s. After that it shows a failure screen with the log tail and a **Retry** button.
6. On window close, shuts down gracefully. The backend stops downloads and the engine, persisting partial output. If the app is killed instead, the Job Object terminates the whole tree, including llama-server.

Browser development (`run_backend.py` + `npm run dev`) and `tauri dev` (backend from source) keep working.

## 2. Incident during Phase C: a model file was deleted by the test suite

| | |
|---|---|
| What | `S:\Mustang AI\models\qwen2.5-0.5b-instruct-q4_k_m.gguf` (491,400,032 bytes) was permanently deleted (`unlink`, not Recycle Bin) during a test run at 15:16. |
| Root cause | To require the token on every route, I changed the shared test fixture `async_client` to send the token by default. `tests/test_model_library.py::test_active_model_deletion_protection` uses that client for a DELETE it expects to be **rejected for lacking a token**. With the token attached, the DELETE was authorised and removed the file. The test suite operated on the real `models/` folder (pre-existing exposure). I had only audited two test files for this pattern before running the suite. |
| Recovery | With the user's approval, re-downloaded from Hugging Face `Qwen/Qwen2.5-0.5B-Instruct-GGUF` and verified it: size identical, SHA-256 `74a4da8c…a7a9db` identical. |
| Guardrails added | (1) Every test file was audited with an AST scan; intended-unauthenticated calls now use an explicit `anon_client`. (2) **Tests can no longer reach the real models folder**: `conftest.py` creates a private per-run models folder whose entries are hard links to `models/*.gguf`, so deleting one only removes the link. (3) Tests also use a private `RUN_DIR`, so the real `mustang.db` and token file are never touched. (4) Every subsequent suite run in this phase was wrapped in a SHA-256 guard of the real model and database; all were unchanged. |

## 3. What was built

### 3.1 Backend (Python): small, additive changes; no router or engine rewrite

| File | Change |
|---|---|
| `app/config.py` | `DESKTOP`, `SESSION_TOKEN` (`SecretStr`), `LOG_DIR` settings; `http://tauri.localhost` allowed origin; loopback-only validator for `HOST`/`ENGINE_HOST`; bootstrap time window off by default (`0` = none). |
| `app/core/security.py` | Desktop mode uses the shell-supplied token (≥ 32 chars) and never writes `.mustang_token`. |
| `app/api/auth.py` | Bootstrap requires an **exact** allow-listed Origin (or Referer origin); requests without one are refused (fixes S2). Returns 404 in desktop mode. |
| `app/main.py` | `X-Mustang-Token` required on **every** API and `/v1` router (fixes S1). Only `/health` and the origin-checked bootstrap are public. |
| `app/api/system.py` | Authenticated `POST /api/system/shutdown` (202, then the launcher stops uvicorn); hardware telemetry made token-protected and off the event loop. |
| `app/api/models.py` | GGUF estimate, context recommendation and telemetry run via `asyncio.to_thread` (see §5). |
| `app/db/database.py` | Schema version in `PRAGMA user_version` (v1 = M3 schema). A legacy (v0) database is backed up with the SQLite backup API to `backups/` (newest 5 kept) before migrating. A newer schema is refused untouched (`NewerSchemaError`). |
| `mustang_launcher.py` (new) | Desktop entry point: uvicorn with the app object, rotating `logs/backend.log` with credential redaction, shutdown hook, stdin-EOF secondary exit signal, removes the token from the environment children inherit. `--import-db SRC DST` mode performs the one-time read-only database copy with an integrity check. |
| `requirements.lock` (new) | 33 runtime packages, pinned to the exact versions in `backend/.venv` (the tested set), with wheel SHA-256 hashes. |
| `tests/conftest.py` | Private `RUN_DIR`; private hard-linked `MODELS_DIR`; `async_client` is now the authenticated UI-like client; new `anon_client`. |
| `tests/test_security.py`, `tests/test_stage6_security.py`, `tests/test_download_manager.py`, `tests/test_model_library.py` | The four tests that assert "rejected without a token" now use `anon_client`. Assertions unchanged. |
| `tests/test_desktop_integration.py` (new) | 21 tests: every route needs a token, bootstrap rules, desktop token, loopback validation, Tauri-origin CORS (including readable 401s), shutdown endpoint, schema backup/refusal, legacy import (source byte-identical, WAL source, path with spaces, refuse existing destination), event-loop responsiveness during a slow estimate, log redaction. |

### 3.2 Embedded Python runtime

| File | Purpose |
|---|---|
| `scripts/build_desktop_runtime.py` | Assembles `build/desktop-runtime/`. It downloads `python-3.11.9-embed-amd64.zip` from python.org (SHA-256 pinned `009d6bf7…`), **verifies the Authenticode signature** of `python.exe` / `python311.dll` (Python Software Foundation), writes an isolated `python311._pth`, installs the lock with `--require-hashes --only-binary`, copies `app/` and the launcher, byte-compiles with the embedded interpreter, smoke-imports the app under a minimal environment, and writes `runtime-manifest.json`. `--relock` regenerates the lock. Result: 3,313 files, 108.5 MB. |
| `scripts/test_desktop_runtime.py` | Runs the whole backend suite **inside the embedded interpreter, against the packaged copy**, with the shell's environment allow-list. It asserts that `app` resolves only to the packaged directory. |
| `src-tauri/tauri.release.conf.json` | Bundles `python/`, `backend/` and `runtime-manifest.json` as Tauri resources (release builds only; dev needs no runtime build). |

### 3.3 Desktop shell (Rust)

| File | Purpose |
|---|---|
| `crates/mustang-supervisor/` | Phase B prototype promoted to a real crate (moved from `desktop-prototypes/process-supervisor`). Its environment allow-list gained `ProgramFiles`/`ProgramW6432`/`CommonProgramFiles`, because NVIDIA NVML (`nvidia-smi`) fails without them (found in this phase). |
| `src-tauri/src/paths.rs` (new) | Data root `%LOCALAPPDATA%\MustangAI` (override `MUSTANG_DESKTOP_DATA_ROOT`), `desktop.json` shell settings (models folder, import decision), runtime resolution (env → bundled resources → debug-only repo venv), llama.cpp directory (bundled → repo `backend/runtime/bin` until Phase D), legacy-install detection, `\\?\` verbatim-path stripping. |
| `src-tauri/src/backend.rs` (new) | `BackendManager` state machine (`starting / ready / restarting / failed / stopping / stopped`). Handles preparation and legacy import (native dialog, Rust-side only; `MUSTANG_DESKTOP_LEGACY_IMPORT=accept\|decline` for automation), spawn with ports, token, allow-listed environment and data paths, `/health` readiness (60 s), crash handling (immediate Job termination of the crashed generation's leftovers, backoff 1 s/2 s, failure after 3 crashes in 60 s), manual restart, graceful shutdown (HTTP → 10 s → Job termination), `logs/desktop.log`. Commands `backend_info` (token only while ready) and `backend_restart`; event `backend://state`. |
| `src-tauri/src/lib.rs`, `runtime_info.rs` | Wiring; graceful shutdown on `RunEvent::Exit`; `desktop_runtime_info` reports real backend status. |
| `src-tauri/capabilities/main.json` | Adds `allow-backend-info`, `allow-backend-restart`, `core:event:allow-listen/unlisten`. Still no shell, fs, http or process permissions. |
| `src-tauri/Cargo.toml` | `tauri-plugin-dialog` (Rust-side message box only; no JS permission granted), supervisor crate as a normal dependency. |
| `package.json` (root) | `runtime:build`, `runtime:test`; `desktop:build` = runtime build + `tauri build --no-bundle --config src-tauri/tauri.release.conf.json`. |

### 3.4 Frontend

| File | Change |
|---|---|
| `src/services/connection.ts` (new) | Desktop: subscribes to `backend://state`, reads address and token via `backend_info`, re-reads after every restart. Browser: bootstrap as before (`VITE_BACKEND_URL` optional, default `http://127.0.0.1:18000`). |
| `src/services/api.ts` | All 30 calls go through `apiFetch(path)`, which adds the current base URL and token. `subscribeDownloadProgress()` replaces `EventSource` with an authenticated fetch-stream SSE parser. Existing exported functions and signatures unchanged. |
| `src/hooks/useChatStream.ts`, `src/components/DownloadQueue.tsx` | Use `apiFetch` / `subscribeDownloadProgress`; logic otherwise unchanged. |
| `src/components/BackendGate.tsx` (new), `src/main.tsx` | Start-up splash, reconnect overlay during restarts (app stays mounted), failure screen with reason, log tail, log folder and Retry (app unmounts so it reloads its data against the new backend). |
| `package.json` | `@tauri-apps/api` 2.12.4 (exact). |
| `src/vite-env.d.ts` (new) | Vite env typing. |

## 4. Runtime configuration contract (shell → backend)

| Variable | Value |
|---|---|
| `MUSTANG_DESKTOP` | `1` |
| `MUSTANG_PORT`, `MUSTANG_ENGINE_PORT` | free loopback ports, new for every (re)start |
| `MUSTANG_SESSION_TOKEN` | 256-bit random, per app launch; environment only (never argv), removed from the environment inherited by llama-server |
| `MUSTANG_ALLOWED_ORIGINS` | release `["http://tauri.localhost"]`; debug adds `http://127.0.0.1:5173` |
| `MUSTANG_RUN_DIR` / `MUSTANG_LOG_DIR` | `<root>\data` / `<root>\logs` |
| `MUSTANG_MODELS_DIR` / `MUSTANG_DOWNLOADS_DIR` | from `desktop.json` (imported: `S:\Mustang AI\models`) / `<models>\.downloads` (same volume, so the atomic rename still works) |
| `MUSTANG_RUNTIME_DIR` | llama.cpp directory (Phase D packages it) |
| Inherited | allow-list only (`SystemRoot`, `TEMP`, `LOCALAPPDATA`, `ProgramFiles`, …); `PATH` = `%SystemRoot%\System32` |

## 5. Additional finding fixed: event-loop stalls froze chat for up to 8 s

The first desktop E2E chat looked slow (3–5 tok/s in the status bar). Browser resource timings showed every request stalling together for 5–8 s right after a model load. The cause was existing backend code: `/api/models/{m}/estimate` and `/context_recommendation` parse the GGUF file **synchronously inside async handlers**, about 5.4–6.9 s each on this model in both the dev venv and the embedded runtime. That blocked the whole event loop, including the chat request queued behind them. This affected browser mode too.

Fix: those calls and `nvidia-smi` telemetry now run in `asyncio.to_thread` (three call sites; no algorithm change). Chat request time went from **8.8 s to 0.39–0.64 s** and the status bar now shows 39–81 tok/s. A regression test fails without the fix (`/health` delayed 1.53 s behind a 1.5 s estimate) and passes with it. The estimate itself is still slow; caching parsed GGUF metadata is a recommended follow-up.

## 6. Deviations and decisions

- **Phase A said "bootstrap disabled in desktop mode, kept for dev with an origin check".** Done, plus the 60-second window is removed by default in browser mode: a page reload after 60 s would otherwise leave the UI unable to call any endpoint now that all of them need the token. The Origin check, not the window, is the real boundary against web pages. A local process can read `run/.mustang_token` in dev mode anyway. The window is still available via `MUSTANG_BOOTSTRAP_WINDOW_SECONDS`.
- **Legacy import uses Python's SQLite backup API through the launcher** instead of adding a Rust SQLite dependency. It is the same consistent-copy guarantee, and the source is opened `mode=ro`.
- **The native import dialog runs on a worker thread** (blocking dialogs on the main thread can deadlock). The window shows the splash meanwhile. Closing the app without answering records nothing, so the next launch asks again (verified).
- **No 8.3 short-path or `CARGO_TARGET_DIR` workarounds**: MSVC builds in place.
- **Plain `cargo build --release` is not a release app.** Without the Tauri CLI, Tauri builds in dev mode and loads `devUrl`. Always use `npm run desktop:build`. This was found during testing; see the test report.

## 7. Known limitations (carried to later phases)

| Item | Phase |
|---|---|
| llama.cpp runtime is not yet bundled; release builds fall back to the repo's `backend/runtime/bin` (path compiled in), which only exists on this machine | D |
| llama-server still has no `--api-key`; `CREATE_NO_WINDOW` not yet set on Python-side subprocesses (no console flashes observed: they inherit the hidden console) | D |
| GGUF estimate still takes about 6 s (now non-blocking); metadata caching recommended | D (or follow-up) |
| Desktop app and the browser dev backend can run against the same models folder simultaneously; concurrent downloads to the same file are not coordinated | E (document / lock file) |
| No tray, close-to-tray or logoff handling; window close = quit | E |
| No installer or code signing; the release exe expects `python/` and `backend/` beside it (`tauri build --no-bundle`) | F |
| Newer-schema database: the backend refuses to start (no read-only mode yet); the failure screen shows the reason | F |
| UI to change the models folder (only `desktop.json` today) | E |
| First real launch on `%LOCALAPPDATA%\MustangAI` not yet done: all automated runs used scratch data roots so the user's profile was not modified | user, at their convenience |

## 8. Effect on the running developer backend

The browser backend already running on port 18000 still runs the pre-Phase-C code until it is restarted. After a restart it will:
- require the token on every request (the updated frontend sends it);
- refuse bootstrap requests without an allow-listed Origin;
- on its first start, back up `backend/run/mustang.db` to `backend/run/backups/` and mark it schema v1.
