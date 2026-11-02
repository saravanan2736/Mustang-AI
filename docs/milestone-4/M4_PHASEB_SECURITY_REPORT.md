# Mustang AI — M4 Phase B Security Report

Scope: the security posture of the new Tauri shell, the connectivity probe, and the supervision prototype. The production backend is unchanged in Phase B.

---

## 1. Requirement checklist

| Requirement | Status | How it is met / evidence |
|---|---|---|
| Test backend bound only to 127.0.0.1 | Met | `mock_backend.py` uses the hard-coded `BIND_HOST = "127.0.0.1"`; host is not configurable |
| Dynamically allocated port | Met | Rust binds `127.0.0.1:0`, reads the port, releases it; new ports observed on every launch and on "restart new port" |
| Never `0.0.0.0` | Met | no code path binds a wildcard address |
| No hard-coded production tokens | Met | probe token is 32 bytes from the OS RNG (`getrandom`) per launch, labelled `test_token`; the default shell build has **no** token at all |
| Browser security not disabled | Met | no WebView2 flags, no `--disable-web-security`, CORS and CSP active; CORS-negative test (#19) proves enforcement |
| No unnecessary Tauri capabilities | Met | see §2 |
| Test server separate from production backend | Met | separate file under `desktop-prototypes/`, imports nothing from `backend/app`; compiled into the shell only with `--features probe` |
| Logs do not contain credentials | Met | token passed by env (not argv); supervisor redacts it; probe test #20 and supervisor scenario 2 verify; result JSON files contain no 64-hex token strings (checked with grep) |

## 2. Tauri capabilities and IPC

- **Default build:** one capability (`capabilities/main.json`) bound to window `main`, `local: true` (remote origins cannot invoke). It grants **one** permission: `allow-desktop-runtime-info`.
- `core:default` is **not** granted. There is no `shell`, `fs`, `http`, `process`, `dialog`, `opener` or `updater` plugin.
- `build.rs` declares the app's commands in an `AppManifest`. Every command therefore needs an explicit grant, so a command missing from a capability is not callable.
- `desktop_runtime_info` returns only non-sensitive data: version, Tauri/WebView2 version, OS, build profile, and backend status (default build: `managed: false`, no URL, no credential).
- **Probe build only:** the `probe` capability adds six `allow-probe-*` grants. It is activated only by `tauri.probe.conf.json` (`app.security.capabilities: ["main","probe"]`). The commands are compiled only with `--features probe`, and the main config pins `capabilities: ["main"]`.
- `withGlobalTauri` is `false` in the main build (only the probe UI uses the global, via the overlay).
- `freezePrototype: true` in both builds.
- Single-instance plugin: a second launch focuses the existing window. It exposes no JS API.

## 3. Content Security Policy (release)

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; font-src 'self' data:;
connect-src 'self' ipc: http://ipc.localhost http://127.0.0.1:*;
object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'
```

- **Verified in practice:** in release, `fetch('http://localhost:<port>')` is blocked (0 ms failure), while `127.0.0.1` works. The same request succeeds in dev, where there is no CSP.
- The existing Mustang UI renders correctly under this CSP: the release shell screenshot shows the full Obsidian Dark interface, including KaTeX CSS and icons.
- `devCsp: null`. Vite's dev server injects inline scripts (React Refresh), so dev runs without a CSP. Release is what ships.

## 4. Findings about the production backend (not changed in Phase B)

| # | Finding | Severity | Phase |
|---|---|---|---|
| F1 | `ALLOWED_ORIGINS` lacks `http://tauri.localhost` (the measured Windows origin). The release desktop app currently cannot read any backend response (preflight 400, no ACAO). | Functional blocker | C |
| F2 | `/api/auth/bootstrap` returns the token to requests **without** an Origin header in the first 60 s, so any local process can obtain it. With desktop IPC token delivery this endpoint should be disabled. | High | C |
| F3 | Most `/api/*` and all `/v1/*` routes are unauthenticated. The probe's mock already demonstrates the target model: token on every route, with CORS headers on 401s so the UI can react. | High | C |
| F4 | llama-server listens without `--api-key` on a fixed port. | Medium | D |
| F5 | Without a Job Object, a crash orphans llama-server (reproduced by the supervisor control scenario). | Medium | C |

## 5. Evaluation: exposing backend address, token, readiness and status to the UI

| Item | Recommended mechanism | Prototype evidence |
|---|---|---|
| Backend address | Rust-owned; returned by an IPC command (`backend_info`), re-queried by the UI after any connection failure | Restart-on-new-port test (#17): the UI reconnects using the shell-provided URL |
| Session token | Generated in Rust per launch; passed to Python by **env var**; returned to the UI **only** through a capability-gated IPC command; kept in JS memory (no localStorage); never written to disk or logs | `probe_info.test_token` is delivered by IPC and redacted in logs; the default build exposes none |
| Readiness | Rust polls `/health` for the status payload; the UI independently polls `/health` with ~250 ms backoff until ready | Startup-unavailable (#2) and restart tests (#16, #17) |
| Runtime status | `desktop_runtime_info` (versions, build, backend `managed/ready/pid`); later a Tauri event (`backend://state`) pushes changes so the UI does not have to poll | Implemented as a command; event push deferred to Phase C |

Not implemented in Phase B, per scope: the final authentication architecture, and any production credential flow.

## 6. Residual risks

- **LNA (R1):** WebView2 156 reports permission state `prompt` for local-network permissions but does not gate loopback fetches. Re-test on each WebView2 major version during Phase G.
- ~~GNU toolchain binaries embed two application manifests~~ **Resolved:** builds now use MSVC; the release exe embeds exactly one manifest (Tauri's).
- The probe token is visible to the probe UI by design (test-only). The production `backend_info` will be the only path for the real token.
