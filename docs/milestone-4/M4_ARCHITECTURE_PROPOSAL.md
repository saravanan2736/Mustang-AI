# Mustang AI — Milestone 4 Architecture Proposal

Phase A deliverable: native Windows desktop architecture. Status: **proposal, awaiting approval**.
Audit date: 2026-11-02. No application source code was changed to produce this document.

---

## 1. Audit findings: the code as it exists today

### 1.1 Repository layout (verified)

```
S:\Mustang AI\
├── README.md
├── backend\                      Python 3.11.9 (.venv), FastAPI, ~5.2k LOC in app\
│   ├── app\main.py               FastAPI app + lifespan (token, DB init, engine, download worker)
│   ├── app\config.py             pydantic-settings, env prefix MUSTANG_, ALL paths derived here
│   ├── app\api\                  10 routers: system, auth, models, downloads, hf, conversations,
│   │                             profiles, settings, chat (SSE), openai (/v1)
│   ├── app\core\                 llama_engine (supervisor), download_manager, hf_client,
│   │                             model_library, gguf_parser, memory_estimator, hardware, security
│   ├── app\db\database.py        aiosqlite, WAL, additive ALTER TABLE migrations, seeding
│   ├── run_backend.py            uvicorn.run("app.main:app", 127.0.0.1:18000)
│   ├── run\                      RUNTIME DATA: mustang.db, .mustang_token, logs (next to code)
│   ├── runtime\bin\              llama.cpp b12000 CUDA 13.4 build + CUDA DLLs (732 MB)
│   ├── runtime\downloads\        cached release zips (562 MB)
│   ├── runtime\setup_runtime.py  pinned, SHA-256-verified runtime installer
│   └── tests\                    120 pytest tests (MockEngine by default, gpu marker)
├── frontend\                     React 18.3, TypeScript 5.7, Vite 6, Tailwind 3.4
│   ├── src\services\api.ts       hard-coded BACKEND_URL = http://127.0.0.1:18000
│   ├── src\hooks\useChatStream.ts fetch + ReadableStream SSE parser, AbortController
│   └── dist\                     existing production build
└── models\                       GGUF files (469 MB today) + .downloads\ partials
```

There are no `.bat`/`.ps1` launch scripts, no git repository, and no Rust toolchain (`cargo`/`rustc` not installed). Node is v24.21.0. GPU: RTX 5050 Laptop, driver 617.42, 8151 MiB.

### 1.2 Backend lifecycle

- `run_backend.py` starts uvicorn on a fixed `127.0.0.1:18000`.
- `lifespan` startup: generates a 256-bit token, writes it to `run/.mustang_token`, runs `init_db()`, creates `LlamaServerEngine` (or `MockEngine` when `MUSTANG_USE_MOCK_ENGINE=1`), starts the download worker.
- `lifespan` shutdown: stops the download manager, then `engine.stop()`. **This only runs on a graceful uvicorn exit.** A `TerminateProcess` (what `Popen.kill()` / Task Manager does on Windows) skips it.

### 1.3 llama-server supervision (`app/core/llama_engine.py`)

- Mature: lifecycle lock, generation lock, cancel-before-switch, automatic rollback to the previous model, health loop, stdout parsing for offloaded layers / VRAM / OOM.
- Spawned with `asyncio.create_subprocess_exec` (no shell, typed args). **No `creationflags`** (no `CREATE_NO_WINDOW`), **no Job Object**, so if Python dies hard, `llama-server.exe` is orphaned, holding VRAM and port 18080.
- Fixed engine port `18080`; **no `--api-key`** passed, so any local process can use the model server directly.
- `_resolve_binary()` falls back to `shutil.which("llama-server")` on PATH. That is a binary-planting risk in a packaged app.
- `terminate()` on Windows is `TerminateProcess` (not graceful); the 3 s wait/kill logic is effectively a single hard kill.
- Cancellation posts to `/slots/0?action=cancel`. That action needs verifying against b12000. The real stop mechanism is closing the HTTP stream, which the code also does.
- Side finding: `stream_chat` does not forward `top_k` to llama-server, although the request model and UI carry it.

### 1.4 CUDA runtime

- `runtime/bin` holds the official `llama-b12000-bin-win-cuda-13.4-x64` build plus `cudart-llama-bin-win-cuda-13.4-x64`: `cublasLt64_13.dll` (493 MB), `ggml-cuda.dll` (155 MB), `cublas64_13.dll` (55 MB), `cudart64_13.dll`, per-CPU `ggml-cpu-*.dll` variants, and ~20 tool executables that the app never uses (bench, quantize, tts, `ggml-rpc-server.exe`, …).
- `setup_runtime.py` pins tag and SHA-256 for both CUDA 13 and CUDA 12 variants. That logic should be reused at build time.
- The RTX 5050 is Blackwell (sm_120). Native sm_120 kernels require CUDA ≥ 12.8, so **only the CUDA 13.4 build is appropriate**; the CUDA 12.4 variant should not ship.
- The CUDA *driver* (`nvcuda.dll`) is not redistributable and comes from the NVIDIA driver. Driver 617.42 satisfies CUDA 13.x.
- Telemetry calls `nvidia-smi` resolved via PATH with a synchronous `subprocess.run` inside an `async def` endpoint. That blocks the event loop for up to 2.5 s per poll.

### 1.5 Storage

- Every path derives from `config.py`: `BASE_DIR = <source>/../..`, then `MODELS_DIR`, `DOWNLOADS_DIR`, `RUNTIME_DIR`, `RUN_DIR`. Nothing else in `app/` uses `__file__`. **Each is overridable by env var** (`MUSTANG_MODELS_DIR`, `MUSTANG_RUN_DIR`, …) through pydantic-settings. This is the key enabler for packaging without a rewrite.
- But defaults put user data *inside the source/install tree*. In a Program Files–style install this is both wrong (data gets wiped on upgrade) and unwritable.
- SQLite at `RUN_DIR/mustang.db` (WAL). Schema migrations are additive `ALTER TABLE … ADD COLUMN` inside try/except, with no schema version table.
- Model IDs are hashes of the path *relative to* `MODELS_DIR`, so relocating the models root keeps IDs stable.
- Tests (`conftest.py`) set the mock engine but **do not isolate `RUN_DIR`**, so the test suite writes to the real `mustang.db`.

### 1.6 Authentication and browser pairing

- Token header `X-Mustang-Token`. The browser obtains the token from `GET /api/auth/bootstrap` within 60 s of backend start.
- **Bootstrap accepts requests without an `Origin` header** (`if not allowed and origin:`), so any local process can fetch the token during the window.
- `ALLOWED_ORIGINS` includes `tauri://localhost` (the macOS/Linux scheme) but **not `http://tauri.localhost`**, which is the Tauri 2 origin on Windows.
- **Only 9 endpoints require the token**: model load/unload/delete and download create/pause/resume/cancel/retry, plus chat stop. Chat completions, regenerate, conversations (read, edit, delete), profiles, settings, HF search, model scan, hardware and the `/v1` OpenAI API are unauthenticated.
- `HostValidationMiddleware` rejects non-loopback Host headers (DNS-rebinding guard). This is good and should be kept.

### 1.7 SSE and cancellation

- Chat: `StreamingResponse` with OpenAI-shaped `data:` frames and `[DONE]`. The frontend reads via `fetch` + `getReader()`, so custom headers work. Cancellation uses AbortController → `request.is_disconnected()` → `engine.cancel_generation()`, with shielded persistence of partial output. This design works unchanged inside a webview.
- Download progress: `EventSource` (cannot send headers), which is a constraint once the endpoint requires auth.

### 1.8 Frontend

- No router and no SSR: a pure SPA built to `dist/`, so it is directly loadable by Tauri.
- External surface: one `target="_blank"` Hugging Face link (needs an opener in a webview); Prism output via `dangerouslySetInnerHTML` (Prism escapes input; CSP is the backstop); KaTeX CSS bundled locally; no remote fonts or scripts.
- `index.html` references `/mustang-logo.svg`, but there is no `public/` directory, so the asset is missing.

---

## 2. Options evaluated

| Criterion | **A — Tauri launches bundled Python sidecar** | B — Python backend as separately packaged process/service | C — Rust owns lifecycle, Python unchanged behind it |
|---|---|---|---|
| CUDA compatibility | Unchanged: Python spawns the same llama-server | Unchanged | Unchanged if Python still spawns llama-server; risky if Rust takes over (re-implement parsing/rollback) |
| Python packaging | Bundled inside the app's install dir | Separate installer/service; two products to version | Bundled (same as A) |
| llama-server supervision | Existing Python supervisor kept as is | Kept | Duplicated or moved to Rust (rewrite of 455 tested LOC) |
| Startup/shutdown | Rust starts Python, waits on `/health`; graceful shutdown endpoint + Job Object | Service starts at boot, wasting RAM when unused; the window can outlive or predate the backend | Same as A |
| Crash recovery | Rust watches the child, restarts with backoff; Job Object kills the whole tree if the shell dies | Service manager restarts, but the UI has no visibility | Same as A |
| API auth | Rust generates the token, passes it to Python by env and to the UI by IPC; no bootstrap race | Needs file- or pipe-based pairing between two independently started processes | Same as A |
| SSE streaming | Direct webview → 127.0.0.1 fetch; current code works | Same | Same, *unless* proxied through IPC (rewrite of the frontend network layer) |
| Port management | Rust picks free ports per launch | Fixed port, conflicts likely | Same as A |
| Windows security | No admin, no service, per-user | Service needs admin to install and runs with broader rights | Same as A |
| Installer size | ~0.5 GB (runtime-dominated) | Same plus a second installer | Same as A |
| Upgrade/rollback | One versioned bundle; atomic per install | Version skew between UI and backend | Same as A |
| Dev complexity | Low to medium | High | Medium to high |

### Recommendation: **Option A, with Rust owning the process lifecycle**

Tauri's Rust core launches the bundled Python backend as a supervised child process inside a Windows Job Object. The existing Python backend keeps doing everything it does today, including supervising `llama-server.exe`. Rust adds only the things Python cannot do for itself:

1. Choose free loopback ports for the backend and engine.
2. Generate the session token and an engine API key.
3. Pass data-directory paths and secrets to Python through environment variables, which the existing `MUSTANG_*` settings already support.
4. Own a Job Object with `KILL_ON_JOB_CLOSE`, so Python *and* llama-server die with the app even on a crash.
5. Health-check, restart with backoff, and shut down gracefully on window close or tray "Quit".
6. Hand the backend URL and token to the webview over a single narrowly scoped IPC command.

This takes the best part of Option C (Rust-owned lifecycle, which closes the orphan and token-race gaps) without its cost of re-implementing the llama-server supervisor. The frontend keeps talking HTTP/SSE to `127.0.0.1`, so `useChatStream.ts` and every API call keep their shape; only the base URL and token source change.

### Rejected

- **Option B.** A Windows service or separately installed backend adds admin rights, a second installer, version skew and a pairing problem, and it consumes resources when the app is closed. It suits enterprise deployment, which is out of scope.
- **Option C as a backend move to Rust.** It would rewrite a working, tested supervisor (rollback, OOM detection, VRAM parsing) and the download manager for no user-visible gain. This contradicts "do not introduce an unnecessary backend rewrite".
- **Proxying all API traffic through Tauri IPC** (instead of HTTP to loopback). That would rewrite the frontend network layer and SSE handling, and IPC is a poor fit for long token streams. Loopback HTTP plus per-launch token, Host validation and CORS is sufficient.
- **Electron.** It ships its own Chromium (larger, more patching), while Tauri was already anticipated in `ALLOWED_ORIGINS`.

---

## 3. Target runtime architecture

```
┌──────────────────────────── Mustang AI.exe (Tauri 2, Rust) ────────────────────────────┐
│  WebView2 window (React SPA from dist/, origin http://tauri.localhost)                  │
│     │  invoke("backend_info") → { baseUrl, token }   (only custom IPC command)          │
│     │  fetch/SSE  http://127.0.0.1:<P1>/api/*  + X-Mustang-Token                        │
│  Rust core: single-instance, tray, port picker, token gen, Job Object, supervisor       │
└─────┬───────────────────────────────────────────────────────────────────────────────────┘
      │ spawn (CREATE_NO_WINDOW, in Job Object, env: MUSTANG_PORT, MUSTANG_SESSION_TOKEN,
      │        MUSTANG_ENGINE_PORT, MUSTANG_ENGINE_API_KEY, MUSTANG_*_DIR, MUSTANG_DESKTOP=1)
┌─────▼──────────────── python.exe (embedded CPython 3.11) -m mustang_launcher ───────────┐
│  FastAPI/uvicorn 127.0.0.1:<P1>  — unchanged routers, DB, downloads, HF, telemetry      │
│  LlamaServerEngine — unchanged supervisor, + --api-key, + resolved binary only          │
└─────┬───────────────────────────────────────────────────────────────────────────────────┘
      │ spawn (inherits Job Object)
┌─────▼──────── llama-server.exe (b12000, CUDA 13.4) 127.0.0.1:<P2> --api-key <K> ────────┐
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Directory model

| Purpose | Location | Survives upgrade | Survives uninstall |
|---|---|---|---|
| Program files (shell, Python, backend code, frontend, llama runtime) | `%LOCALAPPDATA%\Programs\Mustang AI\` (per-user NSIS) | replaced | removed |
| Database, settings, logs, backups | `%LOCALAPPDATA%\MustangAI\data\` | yes | **yes** (never auto-deleted) |
| Models (default) | `%LOCALAPPDATA%\MustangAI\models\` or a user-chosen folder (e.g. `S:\Mustang AI\models`) | yes | **yes** |
| WebView2 cache | `%LOCALAPPDATA%\<bundle-identifier>\` | yes | optional checkbox |

The user data root (`MustangAI`) deliberately differs from the Tauri bundle-identifier directory. The NSIS uninstaller's "delete app data" option only targets the identifier directories, so it can never reach the database or models. See `M4_PACKAGING_STRATEGY.md`.

### 3.2 Minimal backend changes this design needs (Phases C–D, not now)

| Change | File | Size |
|---|---|---|
| Accept `MUSTANG_SESSION_TOKEN` from env; skip writing the token file in desktop mode | `app/core/security.py` | small |
| Disable `/api/auth/bootstrap` when `MUSTANG_DESKTOP=1`; reject Origin-less bootstrap in dev | `app/api/auth.py` | small |
| Add `http://tauri.localhost` to allowed origins | `app/config.py` | trivial |
| Apply token auth to all `/api/*` and `/v1/*` routes (router-level dependency) | `app/main.py`, routers | small |
| Authenticated `POST /api/system/shutdown` that triggers a graceful uvicorn exit | `app/api/system.py`, launcher | small |
| `--api-key` for llama-server; send `Authorization` from the engine; no PATH fallback when desktop | `app/core/llama_engine.py` | small |
| `CREATE_NO_WINDOW` on subprocesses; explicit `System32\nvidia-smi.exe`; telemetry via `asyncio.to_thread` | `llama_engine.py`, `hardware.py` | small |
| Launcher passes the app object to uvicorn and reads the port from settings | `run_backend.py` (or new `mustang_launcher.py`) | small |
| Forward `top_k` to llama-server (existing bug) | `llama_engine.py` | trivial |
| Schema version table plus pre-migration DB backup | `app/db/database.py` | small |

No router, data model or engine algorithm is rewritten.
