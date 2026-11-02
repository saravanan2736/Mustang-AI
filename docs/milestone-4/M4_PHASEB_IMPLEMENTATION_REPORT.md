# Mustang AI — M4 Phase B Implementation Report

**Phase B: Tauri 2 desktop shell and WebView2 connectivity proof. Status: complete; awaiting approval for Phase C.**

Git: baseline commit `05232d5` on `main` (pre-Phase-B checkpoint); Phase B work on branch `m4-phase-b`.

---

## 1. Completion gate summary

| Question | Answer |
|---|---|
| Does Tauri 2 launch successfully? | **Yes.** `tauri dev` (debug, Vite at 127.0.0.1:5173) and a release build both open a native 1280×800 window (min 960×640) rendering the unchanged Obsidian Dark UI. Release exe: 4.3 MB. |
| Can WebView2 communicate with a local backend? | **Yes.** 18/18 connectivity tests pass from the release WebView2 (and 18/18 in dev with the dev origin allow-listed), with CORS, CSP and Host validation enforced. |
| Which origin and networking configuration work? | Release origin **`http://tauri.localhost`** (dev: `http://127.0.0.1:5173` with Vite, `http://127.0.0.1:1430` without `devUrl`). Direct `fetch` to the literal `http://127.0.0.1:<dynamic port>`, CORS allow-list = exact origin, CSP `connect-src … http://127.0.0.1:*`. |
| Does streaming work? | **Yes.** fetch-reader streaming delivers events incrementally (20 events over 2.03 s, 88–116 ms gaps); EventSource works; client abort reaches the server; backend death mid-stream surfaces as an error within ~0.4 s. |
| Does Job Object cleanup work? | **Yes.** Hard-killing the parent removes the whole tree (child + grandchild) in the prototype and in the real Tauri app; the no-Job control reproduces the orphan. |
| Is the existing app intact? | **Yes.** No tracked file under `frontend/` or `backend/` changed; backend 118/118 pass before and after; `mustang.db` hash unchanged; `tsc` clean; frontend build output byte-identical file names. |

## 2. Pre-implementation checks

| Item | Result |
|---|---|
| Node.js / npm | v24.21.0 / 11.19.0 |
| Rust / Cargo | **1.99.1 / 1.99.1, already installed** via rustup at `%USERPROFILE%\.cargo\bin` (not on PATH). Toolchain `stable-x86_64-pc-windows-gnu` |
| C toolchain | MinGW-w64 GCC 16.2.0 (WinLibs UCRT, on PATH), provides `ld`, `windres`, `dlltool` |
| MSVC Build Tools | Initially **not installed**. **Installed after approval** (2026-11-02): Visual Studio Build Tools 2026 18.11.2 (`Microsoft.VisualStudio.Workload.VCTools` + recommended, via `winget`), Windows SDK 10.0.26100; Rust `stable-x86_64-pc-windows-msvc` 1.99.1 added via rustup and pinned by the repo-level `rust-toolchain.toml` (the machine's rustup default remains GNU) |
| WebView2 Runtime | 156.0.4314.58 (Evergreen, per-machine) |
| Tauri | crate `tauri` 2.12.5, `tauri-build` 2.7.4, `wry` 0.57.1, `webview2-com` 0.39.1, `tauri-plugin-single-instance` 2.5.5, CLI `@tauri-apps/cli` 2.12.4 |
| Git | 2.55.0. The repo was not under version control, so I ran `git init`, added a `.gitignore` excluding models, DB, token, runtime binaries, venv and node_modules, then made checkpoint commit `05232d5` and created branch `m4-phase-b`. The commit is authored with the user's address per-commit; the global git identity was not changed. |
| Frontend build (baseline) | `npm run build` OK (5.0 s; pre-existing >500 kB chunk warning) |
| Backend tests (baseline) | **118 passed** (50.8 s) |

System-wide changes: only the approved Visual Studio Build Tools 2026 install (and the user-level rustup MSVC toolchain). Everything else is project-local (`npm install` at the repo root; Cargo crates in the Cargo cache).

### 118 vs 120 test discrepancy (resolved, no tests modified)

The Phase A figure of 120 came from counting `def test_` lines with grep. Two of those are **pytest fixtures whose names start with `test_`**, not tests:

- `tests/test_download_manager.py:57` `@pytest.fixture def test_download_sandbox(...)`
- `tests/test_model_library.py:39` `@pytest.fixture def test_models_sandbox(...)`

`pytest --collect-only` collects **118** tests, which is the authoritative count. The Phase A documents' "120 tests" should read 118.

### Test isolation note

`tests/conftest.py` does not isolate `RUN_DIR`, so a plain `pytest` run writes into the real `backend/run/mustang.db` (Phase A risk R18). To honour "do not modify the database", every run in Phase B used a **copy** of `backend/run` via `MUSTANG_RUN_DIR=<scratch copy>` (an existing pydantic-settings override; no test or code change). The `mustang.db` SHA-256 was identical before and after Phase B.

## 3. What was built

### 3.1 Desktop shell — `src-tauri/`

| File | Purpose |
|---|---|
| `Cargo.toml` | crate `mustang-desktop`; `tauri 2`, `tauri-plugin-single-instance`; optional `probe` feature (pulls in the supervisor prototype + `getrandom`); release profile LTO/strip/`opt-level="s"` |
| `build.rs` | `tauri-build` with an `AppManifest` listing the app's commands (so each needs an explicit capability grant) |
| `tauri.conf.json` | productName "Mustang AI", identifier `ai.mustang.desktop`; `frontendDist ../frontend/dist`, `devUrl http://127.0.0.1:5173`, before-dev/build commands run the existing frontend scripts; window 1280×800 / min 960×640, dark theme, background `#07090E`; CSP; `capabilities: ["main"]`; bundling disabled (`active: false`, installer is Phase F) |
| `tauri.probe.conf.json` | JSON merge-patch overlay for the connectivity probe (different frontendDist/identifier, probe capability, global Tauri API) |
| `capabilities/main.json` | only `allow-desktop-runtime-info` |
| `capabilities/probe.json` | probe commands (activated only by the overlay) |
| `src/main.rs` | entry; `windows_subsystem = "windows"` in release (no console) |
| `src/lib.rs` | builder: single-instance (focus existing window), command registration; probe wiring under `cfg(feature = "probe")` |
| `src/runtime_info.rs` | `desktop_runtime_info` command (versions, WebView2 version, OS/arch, build profile, backend status) |
| `src/probe.rs` | probe only: port picking, test token, supervised mock backend, restart/stop/logs/raw-host-check/report commands |
| `icons/` | `app-icon.svg` (indigo "M" on obsidian) + generated `icon.ico`, `32x32.png`, `128x128(@2x).png`, `icon.png` |

The existing React application is used **unchanged**. It is not modified to call `desktop_runtime_info` or to change its API base URL; per scope, that happens in Phase C.

### 3.2 Root desktop tooling

| File | Purpose |
|---|---|
| `package.json` / `package-lock.json` | `@tauri-apps/cli` 2.12.4 (dev dependency, pinned) and scripts `desktop:dev`, `desktop:build`, `probe:dev`, `probe:build`. Kept at the root so `frontend/package.json` is untouched |
| `.gitignore` | new (repo had none) |

### 3.3 Prototypes — `desktop-prototypes/`

| Path | Purpose |
|---|---|
| `process-supervisor/` | Job Object supervision crate + scenario runner (see `M4_PHASEB_PROCESS_LIFECYCLE_REPORT.md`) |
| `connectivity-probe/mock_backend.py` | TEST-ONLY Starlette/uvicorn mock (see `M4_PHASEB_CONNECTIVITY_REPORT.md`) |
| `connectivity-probe/ui/` | probe page (`index.html`, `probe.js`, `probe.css`), CSP-compatible (no inline script) |
| `connectivity-probe/results/` | 4 result files: release ×2 (18/18), debug ×2 (first run: CORS origin-mismatch evidence; second run: 18/18) |

### 3.4 Documentation

`docs/milestone-4/M4_PHASEB_{IMPLEMENTATION,CONNECTIVITY,PROCESS_LIFECYCLE,SECURITY}_REPORT.md`

### 3.5 Not changed

`frontend/**` (source, config, package.json), `backend/**` (app, tests, config, DB, runtime), `models/`, the Phase A documents. `frontend/dist/` was regenerated by builds and is git-ignored.

## 4. Commands and results

All Rust commands need `%USERPROFILE%\.cargo\bin` on PATH. Results below were first obtained on the GNU toolchain and then **re-run on MSVC** (§5.1); both sets pass. On MSVC, builds go to the normal in-project `src-tauri/target/`.

Note: `desktop:build` and `probe:build` write the same `src-tauri/target/release/mustang-desktop.exe`. Rebuild the one you intend to run.

| Test | Command | Result |
|---|---|---|
| Frontend production build | `npm --prefix frontend run build` | ✅ built in 5.0 s (and again inside `tauri build`) |
| TypeScript | `npx tsc --noEmit` (frontend) | ✅ exit 0 |
| Tauri development launch | `npx tauri dev --config '{"build":{"beforeDevCommand":""}}'` | ✅ window "Mustang AI" 1296×839, responding; UI loaded from Vite and showed live data from the running backend (GPU telemetry, model list). `beforeDevCommand` was skipped only because the user's own Vite server already held port 5173 (it was not stopped). `npm run desktop:dev` is the normal entry point. |
| Tauri Windows application build | `npm run desktop:build` (`tauri build --no-bundle`) | ✅ release `mustang-desktop.exe` 4.3 MB; launches; UI renders. As expected it cannot read the production backend yet (CORS, finding F1) |
| Probe build + run (release) | `npm run probe:build`; `MUSTANG_PROBE_AUTOEXIT=1 mustang-desktop.exe` | ✅ 18/18 |
| Probe (dev) | `MUSTANG_PROBE_ALLOWED_ORIGINS=http://127.0.0.1:1430 MUSTANG_PROBE_AUTOEXIT=1 npm run probe:dev` | ✅ 18/18 |
| Localhost GET and POST | probe #3–#8 | ✅ |
| SSE / fetch streaming | probe #9–#11, #18 | ✅ |
| Connection refusal | probe #12, #15 | ✅ fails in ≤2 ms, no hang |
| Backend restart and reconnect | probe #2, #16, #17 | ✅ |
| Child-process termination | `cargo run --release --bin supervisor-poc -- all`; in-app hard-kill test | ✅ 9/9; in-app tree cleaned in <2 s |
| Backend regression | `MUSTANG_RUN_DIR=<copy> pytest -q` | ✅ **118 passed** (69.9 s); `mustang.db` unchanged |

**Not executed:** nothing from the required list was skipped. Not attempted, as out of scope: NSIS bundling (`bundle.active: false`), code signing, the updater, GPU inference through the shell.

## 5. Toolchain issues found (GNU target) and decisions

| Issue | Cause | Phase B handling |
|---|---|---|
| `windres: preprocessing failed … AI\src-tauri\…: No such file` | MinGW `windres` cannot handle the space in `S:\Mustang AI` in its include path (OUT_DIR). The S: volume has no 8.3 short names. | Build with `CARGO_TARGET_DIR` on a path without spaces (Phase B used the session scratchpad) |
| `ld: export ordinal too large` | Tauri template `crate-type = ["staticlib","cdylib","rlib"]`; the cdylib exceeds GNU ld's 65,535 export limit | `crate-type = ["rlib"]` (cdylib/staticlib are only needed for mobile) |
| Linker warning `.rsrc merge failure: multiple non-default manifests` | MinGW's default manifest plus Tauri's Common-Controls v6 manifest both get embedded | The app runs; Tauri's manifest is present in the binary; which one Windows selects is unverified |

**Decision: switch to MSVC (approved and done).**

### 5.1 MSVC re-verification (2026-11-02)

| Check | GNU result | MSVC result |
|---|---|---|
| Build from the path with spaces, no `CARGO_TARGET_DIR` | failed (`windres`) | ✅ clean, in-project `src-tauri/target/` |
| Linker warnings | `.rsrc merge failure: multiple non-default manifests` | ✅ none |
| Manifests embedded in release exe | 2 | ✅ 1 (Tauri's, with Common-Controls v6) |
| Release exe size | 4.3 MB | 4.2 MB |
| `tauri dev` launch | ✅ | ✅ window responding, live data from the running backend via the Vite origin |
| `tauri build --no-bundle` release launch | ✅ | ✅ UI renders (still CORS-blocked by the production backend, F1) |
| Release connectivity probe | 18/18 | ✅ 18/18 (`results/probe-results-release-1793612193.json`) |
| Supervisor scenarios | 9/9 | ✅ 9/9 |
| In-app hard-kill → Python tree cleaned | ✅ | ✅ 3/3 processes gone within 2 s |

All three GNU workarounds are obsolete. The only remnant is `crate-type = ["rlib"]`, kept deliberately because a desktop-only app does not need the mobile `cdylib`/`staticlib`.

The backend was not re-run after the toolchain switch: no backend or frontend file changed (verified with `git status`), and its last run (118 passed) happened after all Phase B source changes.

## 6. Known limitations

- The main UI is not yet wired to the shell (it still uses `http://127.0.0.1:18000` and bootstrap auth). That needs the Phase C backend and frontend changes.
- The release build cannot talk to the current backend until `http://tauri.localhost` is allow-listed (F1, Phase C).
- No managed backend in the default build. `desktop_runtime_info.backend.managed` is `false`.
- `dev` runs without a CSP (`devCsp: null`) because Vite injects inline scripts.
- `frontend/public/mustang-logo.svg` is still missing (pre-existing 404). The new `src-tauri/icons/app-icon.svg` can be copied there in Phase C.
- `desktop-prototypes/process-supervisor/target/` (41 MB) is a local build cache, git-ignored.

## 7. Additional work required for production integration (Phase C onward)

1. **Toolchain:** done. MSVC installed and pinned (§5.1).
2. **Backend (Phase C):**
   - accept `MUSTANG_SESSION_TOKEN` from the environment;
   - add `http://tauri.localhost` to the allow-list in desktop mode;
   - disable bootstrap in desktop mode;
   - put token auth on all routes;
   - add `POST /api/system/shutdown`;
   - add a launcher that reads the port from settings;
   - isolate tests (`conftest.py` temp `RUN_DIR`).
3. **Frontend (Phase C):**
   - resolve the base URL and token via `invoke("backend_info")` when running in Tauri, keeping the browser-dev fallback;
   - add reconnect logic, re-querying the shell after a connection failure;
   - send the token on every request;
   - move download progress from EventSource to a fetch stream.
4. **Shell (Phase C):**
   - promote `mustang-supervisor` into the app (a `backend.rs` that spawns embedded Python in the Job Object with an env allow-list, ports and token);
   - add the `backend_info` command and a `backend://state` event;
   - restart with backoff;
   - set up the data directory and import legacy data.
5. **Phase D:**
   - give llama-server an `--api-key`;
   - use `CREATE_NO_WINDOW` in Python subprocesses;
   - remove the PATH fallback;
   - verify VRAM is released after a hard kill with a real model loaded.
6. **Phase G:** re-run the connectivity probe on the WebView2 runtime current at release (LNA watch).
