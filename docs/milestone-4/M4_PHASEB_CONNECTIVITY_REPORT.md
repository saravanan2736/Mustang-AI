# Mustang AI — M4 Phase B Connectivity Report

**Question:** can the real Tauri 2 WebView2 frontend talk to a local HTTP backend on `http://127.0.0.1:<dynamic-port>` on Windows, without weakening browser security?

**Answer: yes.** 18/18 automated tests pass from inside the release Tauri app, with CORS, CSP and Host validation all enforced. No browser flags, no `--disable-web-security`, no proxying through Rust.

Date: 2026-11-02 · WebView2 Runtime 156.0.4314.58 (Chromium 156) · Tauri 2.12.5 · Windows 11 (10.0.26300)

---

## 1. Method

- **Real application, real WebView2.** The probe is the same `src-tauri` crate built with `--features probe` and the config overlay `src-tauri/tauri.probe.conf.json`. Only the static UI (`desktop-prototypes/connectivity-probe/ui/`) and two capability grants differ from the main shell. Same window, same CSP, same `http://tauri.localhost` scheme.
- **Test-only mock backend** (`desktop-prototypes/connectivity-probe/mock_backend.py`). It is separate from the production backend and imports nothing from `backend/app`. It reproduces the production posture with the same middleware types the production app uses:
  - Starlette `CORSMiddleware` with an explicit allow-list;
  - a Host-header guard with the same rule as `HostValidationMiddleware`;
  - a token header (`X-Mustang-Token`);
  - SSE-style `StreamingResponse`;
  - a bind to `127.0.0.1` only.
- **Supervised by Rust.** The shell picks a free loopback port (`127.0.0.1:0`), generates a 256-bit **test** token, passes it to the mock by environment variable (not argv), and runs it inside a Job Object (see the process lifecycle report). The first launch is deliberately delayed by 2.5 s.
- **Probe page** (`ui/probe.js`) runs 18 checks with plain `fetch`/`EventSource` (8 s timeout each, so a blocked or pending request shows up as a hang) and writes JSON results via a Rust command.

Run commands (from the repo root, with `%USERPROFILE%\.cargo\bin` on PATH; the MSVC toolchain is selected by `rust-toolchain.toml`). Results were first obtained with a GNU build and then repeated with an MSVC build (`results/probe-results-release-1793612193.json`, 18/18).

```bash
npm run probe:build
```
```bash
MUSTANG_PROBE_AUTOEXIT=1 <target>/release/mustang-desktop.exe
```
```bash
MUSTANG_PROBE_ALLOWED_ORIGINS=http://127.0.0.1:1430 MUSTANG_PROBE_AUTOEXIT=1 npm run probe:dev
```

## 2. Origins observed (measured, not assumed)

| Mode | `location.origin` | Origin header seen by server | Secure context |
|---|---|---|---|
| **Release build** (`tauri build`, frontendDist embedded) | **`http://tauri.localhost`** | `http://tauri.localhost` | yes |
| `tauri dev` with no `devUrl` (probe) | `http://127.0.0.1:1430` (Tauri built-in dev server) | `http://127.0.0.1:1430` | yes |
| `tauri dev` with `devUrl` (main shell) | `http://127.0.0.1:5173` (Vite) | `http://127.0.0.1:5173` | yes |

- `tauri://localhost` (macOS/Linux) is **not** the Windows origin. The production backend currently allows `tauri://localhost` but **not** `http://tauri.localhost`.
- Dev and release origins differ. The first dev-mode run, with only `http://tauri.localhost` allow-listed, failed every request with `TypeError: Failed to fetch` (CORS). Evidence: `results/probe-results-debug-1793609975.json`.

## 3. Results — release build (`results/probe-results-release-1793610329.json`)

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | WebView2 origin | INFO | `http://tauri.localhost`, secure context |
| 2 | Backend unavailable during startup → retry until ready | PASS | first error `TypeError: Failed to fetch`; ready after ~1.1 s / 5 attempts (polling began ~1.4 s into the 2.5 s delay) |
| 3 | HTTP GET, JSON, token header (preflighted) | PASS | 3 ms; server saw `Origin: http://tauri.localhost`, `Host: 127.0.0.1:<port>` |
| 4 | Server-observed Origin = `location.origin` | PASS | both `http://tauri.localhost` |
| 5 | Simple GET (no preflight) | PASS | 200, 2 ms |
| 6 | POST JSON request/response (incl. Unicode) | PASS | echoed body identical, 4 ms |
| 7 | Missing token → 401 readable by app | PASS | 401 (CORS headers present on error responses) |
| 8 | CORS preflight observed server-side | PASS | 3 OPTIONS requests |
| 9 | Streaming via fetch reader (the `useChatStream.ts` path) | PASS | 20/20 events, `[DONE]` received, spread 2032 ms, gaps 88–116 ms: **delivered incrementally, not buffered** |
| 10 | EventSource cross-origin (the download-progress path) | PASS | 5 events + `done` |
| 11 | Client abort mid-stream → server notices (Stop button path) | PASS | client `AbortError` after 5 events; server `aborted_streams` 0 → 1 |
| 12 | Connection refused (closed port) | PASS | `TypeError: Failed to fetch` in 2 ms, no hang |
| 13 | `http://localhost:<port>` | INFO | **blocked by CSP** in release (`connect-src` allows only `127.0.0.1:*`); reachable in dev (no CSP). The app must always use `127.0.0.1`. |
| 14 | Host-header validation (DNS-rebinding guard) | PASS | `evil.example` → 403, `attacker.test:<port>` → 403, `127.0.0.1:<port>` → 200 (raw request from Rust, because browsers cannot forge Host) |
| 15 | Backend stopped → fetch fails fast | PASS | graceful stop; fetch fails in 2 ms |
| 16 | Restart, same port → reconnect | PASS | refused while restarting, ready after 1.6 s, new `boot_id` |
| 17 | Restart, new port → reconnect via shell-provided URL | PASS | new port ready in ~0.55 s; old port refuses |
| 18 | Backend killed mid-stream | PASS | reader rejects with `TypeError: network error` after 5 events in ~0.43 s: **no hang**, no false `[DONE]` |
| 19 | CORS enforced when origin not allow-listed | PASS | server reachable (no-cors probe) and received the GET, but the app could not read it: simple and preflighted requests both fail |
| 20 | Backend logs captured; token absent | PASS | logs captured via the supervisor; token never present |

Debug/dev mode with its origin allow-listed (`results/probe-results-debug-1793610009.json`): **18/18 PASS**, identical behaviour.

## 4. Local Network Access (risk R1)

- `navigator.permissions.query` reports `prompt` for `local-network-access`, `local-network` and `loopback-network`.
- Despite that, **no prompt was shown and no request was blocked or delayed**: every loopback request completed in 2–4 ms in automated, unattended runs (a pending permission prompt would have tripped the 8 s timeout).
- Conclusion for WebView2 156: loopback fetches from `http://tauri.localhost` are **not** gated by LNA today. **R1 is retired for this runtime but stays on watch:**
  - Phase G must re-run the probe on the WebView2 version current at release.
  - If a future runtime starts gating loopback, the supported fallbacks are (a) Tauri custom-protocol / IPC-channel transport for API traffic, or (b) a WebView2 environment option set by the app. Neither weakens web security for other origins.

## 5. Production backend check (passive, no changes made)

The release main shell (default build) was launched against the existing backend on `127.0.0.1:18000`. The UI renders, but shows "No models" and "No GPU". Passive `curl` checks explain why:

| Origin | `GET /api/models` | Preflight `POST /api/models/load` |
|---|---|---|
| `http://tauri.localhost` | 200 **without** `Access-Control-Allow-Origin`, so the browser blocks it | **400 "Disallowed CORS origin"** |
| `http://127.0.0.1:5173` (dev) | 200 with ACAO | 200 with ACAO |

**Required in Phase C** (not done here, per scope):
1. Add `http://tauri.localhost` to `ALLOWED_ORIGINS` (desktop mode).
2. The bootstrap endpoint's origin check must also accept it, or bootstrap must be replaced by IPC token delivery.

## 6. Recommended networking configuration

| Item | Setting |
|---|---|
| Transport | Direct `fetch` from WebView2 to `http://127.0.0.1:<port>` (no IPC proxy needed) |
| Address | Always the literal `127.0.0.1` (CSP blocks `localhost`; also avoids `::1` ambiguity) |
| Port | Chosen per launch by Rust; UI obtains it via IPC; reconnect logic re-queries on failure |
| CORS allow-list (desktop) | exactly `http://tauri.localhost`; dev builds add `http://127.0.0.1:5173` |
| CSP `connect-src` | `'self' ipc: http://ipc.localhost http://127.0.0.1:*` |
| Streaming | `fetch` + `ReadableStream` (works incrementally); EventSource also works but cannot carry the token header |
| Failure semantics | refused/killed backend → immediate `TypeError`; UI should poll `/health` with backoff (≈250 ms) and re-query the shell for the URL |
