# Mustang AI — Milestone 4 Security Design

Phase A deliverable. Threat model: a single-user Windows desktop app. Adversaries are (1) web pages in the user's browser, (2) other local processes or users on the same machine, (3) tampered downloads or updates, and (4) content rendered from model output. Content filtering of the user's own chats is explicitly **not** a goal.

---

## 1. Current-state gaps (from audit)

| # | Gap | Where | Severity |
|---|---|---|---|
| S1 | Most `/api/*` and all `/v1/*` endpoints are unauthenticated (chat, conversations read/delete, profiles, settings, HF search, scan, hardware) | routers | High |
| S2 | `/api/auth/bootstrap` hands out the token to requests **without** an Origin header during the first 60 s | `app/api/auth.py` | High |
| S3 | llama-server listens on a fixed port with no `--api-key`, so any local process can drive the model | `llama_engine.py` | Medium |
| S4 | llama-server resolved from PATH as a fallback (binary planting) | `llama_engine._resolve_binary` | Medium |
| S5 | Token written to `run/.mustang_token`; `os.chmod(0o600)` is a no-op on Windows | `security.py` | Low–Medium |
| S6 | Fixed ports 18000/18080 allow squatting by another process before launch | `config.py` | Medium |
| S7 | No Windows origin `http://tauri.localhost` in CORS list | `config.py` | Functional blocker |
| S8 | Orphaned llama-server after a hard crash keeps the port and VRAM | lifecycle | Medium |

Existing strengths to keep: loopback-only bind, Host-header DNS-rebinding guard, `secrets.compare_digest`, `shell=False` subprocesses, path validation (ADS, reserved names, traversal), SHA-256-pinned runtime and download verification.

## 2. Trust boundaries

```
[WebView2 renderer]  --(Tauri IPC: 1 command)-->  [Rust core]  --(spawn+env)-->  [Python backend]  --(spawn, api-key)-->  [llama-server]
        |                                                                               ^
        +----------------------- HTTP 127.0.0.1:<P1> + X-Mustang-Token ----------------+
```

## 3. Tauri capabilities and IPC

- **Single capability file** `src-tauri/capabilities/main.json` bound to window `main`, with only local content (`remote` URLs are not allowed to access IPC).
- Permissions granted:
  - `core:default` (window/event basics), `core:window:allow-*` limited to minimize/maximize/close/start-dragging if a custom title bar is used.
  - `opener:allow-open-url`, scoped to `https://huggingface.co/**`.
  - `updater:default` (Phase F, only when the user opts in).
  - Custom `backend_info` (returns `{ baseUrl, token }`), `backend_restart`, `app_quit`, `pick_models_folder` (native dialog; Rust validates and persists the folder, so the webview never receives fs rights).
- **Not granted:** `shell:*`, `fs:*`, `http:*` (the plugin), `process:*`. The webview cannot spawn processes or read arbitrary files.
- Custom commands validate every argument in Rust; `pick_models_folder` rejects UNC/network paths and system directories (`C:\Windows`, `Program Files`).
- Enable the Tauri isolation pattern only if third-party scripts are ever introduced. Not needed now, because all JS is first-party and bundled.

## 4. Backend authentication

1. Rust generates a 32-byte random session token per launch and passes it as `MUSTANG_SESSION_TOKEN`. Nothing is written to disk in desktop mode.
2. Webview gets it via `invoke("backend_info")`. It is held in memory only (no localStorage).
3. **All** `/api/*` and `/v1/*` routes require `X-Mustang-Token` (router-level dependency). Only `/health` stays open, returning no sensitive data.
4. `/api/auth/bootstrap` is disabled when `MUSTANG_DESKTOP=1`. In browser dev mode it is kept but requires an allowed Origin (fixes S2).
5. SSE endpoints: chat already uses `fetch` (headers work). Download progress moves from `EventSource` to a `fetch` stream reader so it can send the header. Query-string tokens are avoided because they leak into logs.
6. `/v1/*` OpenAI-compatible API: token-protected by default. A future "Allow other local apps" toggle could expose it with a user-visible API key, but that is out of M4 scope.

## 5. Localhost exposure and port allocation

- The backend binds `127.0.0.1` only (never `0.0.0.0`/`::`); a startup assertion refuses any other host.
- Rust picks ports by binding `127.0.0.1:0`, reading the assigned port, and releasing it, for both P1 (backend) and P2 (engine). A narrow race remains, so the backend retries and Rust re-picks on bind failure (max 3).
- llama-server receives `--api-key <random>`; the engine adds `Authorization: Bearer` to its health, completions and cancel calls.
- The Host-validation middleware stays. CORS allow-list in desktop mode is `http://tauri.localhost` (plus `http://127.0.0.1:5173` only in dev builds).
- No Windows Firewall rule is created; loopback traffic does not need one, and no inbound prompt appears.

## 6. Filesystem access

- Writes happen only in `%LOCALAPPDATA%\MustangAI\` and the user-chosen models folder; the install directory is read-only at runtime (bytecode precompiled).
- `validate_model_path` currently allows *any* existing absolute `.gguf` path. Desktop mode tightens this to the models root only. Importing an external file becomes an explicit copy or "add folder" action.
- Model deletion is restricted to files inside the models root, as the current `validate_canonical_path_safety` already does.
- Logs never contain the token or API key (redact in the spawn command log line).

## 7. Shell and subprocess

- Allowed child processes: `python.exe` (from the install dir, spawned by Rust), `llama-server.exe` (from `resources\llama`, spawned by Python), and `%SystemRoot%\System32\nvidia-smi.exe` (absolute path).
- All spawns: argument vectors (no shell), `CREATE_NO_WINDOW`, allowlisted environment, inside the Job Object (`KILL_ON_JOB_CLOSE`, `DIE_ON_UNHANDLED_EXCEPTION`).
- No PATH lookups in desktop mode.

## 8. Content Security Policy

Configured in `tauri.conf.json` (`app.security.csp`). Tauri adds nonces/hashes for its own scripts.

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:;
font-src 'self' data:;
connect-src 'self' ipc: http://ipc.localhost http://127.0.0.1:*;
object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'none'
```

- `http://127.0.0.1:*` is required because the port is chosen per launch. The token plus llama-server's API key mean that reaching other loopback ports gains nothing.
- `'unsafe-inline'` for styles is needed for KaTeX/inline style attributes and does not enable script execution.
- Markdown rendering: `react-markdown` without `rehype-raw` (raw HTML in model output is not rendered). Keep it that way. Prism's `dangerouslySetInnerHTML` gets only Prism-escaped tokens, and `script-src 'self'` is the backstop.
- External links open in the system browser via `opener`, never inside the webview. Navigation away from the app origin is blocked (`on_navigation` handler).

## 9. Safe application updates

- Tauri updater with a minisign key pair. The public key is embedded in the app and the private key stays offline/CI-secret.
- The update manifest is served over HTTPS. Updates are only applied if the signature verifies, and the installer itself is Authenticode-signed.
- Updates are user-initiated or opt-in only; there is no silent auto-install.
- llama runtime and Python are updated only as part of a signed app update, never fetched separately at runtime in M4.

## 10. Residual risks (accepted)

- A malicious process running as the same Windows user can read process memory or the WebView2 profile. That is out of scope for any user-mode app.
- Model output can contain anything the user asks for. Rendering is sandboxed (no raw HTML, CSP), and there is intentionally no content filter.
