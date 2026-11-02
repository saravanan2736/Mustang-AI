# Mustang AI — Milestone 4 Risk Register

Phase A deliverable. Likelihood (L) and Impact (I) on a 1–5 scale. Score = L × I.

| ID | Risk | L | I | Score | Phase | Mitigation | Trigger / early signal | Owner |
|---|---|---|---|---|---|---|---|---|
| R1 | WebView2 blocks or prompts on fetches from `http://tauri.localhost` to `http://127.0.0.1` (Chromium Local Network Access / PNA rules) | 3 | 5 | **15** | B | Day-1 spike in Phase B; check the WebView2 runtime version; fallbacks in order: WebView2 additional browser args disabling the LNA feature for the app, then `useHttpsScheme`, then a Tauri custom-protocol proxy for REST with SSE kept via a fetch stream | Console CORS/LNA errors in spike | Desktop |
| R2 | Pruned llama runtime misses a DLL (e.g. `llama-common.dll`, `mtmd.dll`, a CPU variant), so the engine fails on some CPUs | 3 | 4 | 12 | D | Derive the closure with Process Monitor; keep all `ggml-cpu-*` variants; startup integrity manifest; test on Intel and AMD | `llama-server` exits code `0xC0000135` | Backend |
| R3 | Embedded Python misses something a venv provides (`_pth` paths, `ssl` certs for httpx, `asyncio` proactor quirks) | 3 | 4 | 12 | C | Run the full pytest suite under embedded Python in CI; use `certifi` explicitly; PyInstaller onedir as fallback | Import errors, TLS failures to huggingface.co | Backend |
| R4 | User's NVIDIA driver too old for CUDA 13.x, so a CUDA init failure looks like "app broken" | 3 | 4 | 12 | D | Driver version check at startup with an actionable message; CPU fallback | `cudaErrorInsufficientDriver` in engine log | Desktop |
| R5 | Orphaned `python.exe`/`llama-server.exe` after a crash holding VRAM/ports | 4 | 4 | **16** | C | Job Object `KILL_ON_JOB_CLOSE`; dynamic ports; graceful shutdown endpoint | VRAM not freed after quit | Desktop |
| R6 | CPU fallback does not engage automatically without a driver | 2 | 3 | 6 | D | Verify on a driver-less VM; if needed, pass `-ngl 0` when no GPU is detected by telemetry | Engine crash on CPU-only VM | Backend |
| R7 | `/slots/0?action=cancel` is not a valid b12000 action, so cancel relies only on stream close | 3 | 2 | 6 | D | Verify against the b12000 server source; rely on HTTP stream close (llama-server stops on disconnect); remove the dead call if invalid | Generation continues in log after Stop | Backend |
| R8 | User data (DB, models) lost during upgrade, uninstall or legacy import | 2 | 5 | 10 | C, F | Data root separate from install dir *and* from bundle-identifier dir; import copies, never moves; pre-migration backups; uninstall tests | Any test where the DB or models are missing after an installer action | Release |
| R9 | Windows logoff/shutdown kills the app before the DB flushes or partial output is saved | 3 | 3 | 9 | E | Handle `WM_QUERYENDSESSION`; WAL mode already crash-safe; persist partial on cancel | Missing last assistant message after reboot | Desktop |
| R10 | Tightening auth on all routes breaks a frontend call that does not send the token | 4 | 2 | 8 | C | Route-coverage test (every route 401 without token); central `request()` helper in `api.ts` | 401s in UI console | Frontend |
| R11 | WebView2 throttles timers or streams when minimized to the tray, so status polling and SSE stall | 2 | 2 | 4 | E | Polling is non-critical; chat streams finish server-side and persist anyway | Stale status after restore | Frontend |
| R12 | Rust/MSVC toolchain absent on dev machine (confirmed: `cargo` not installed) | 5 | 1 | 5 | B | Install rustup + VS Build Tools (C++ workload, Windows SDK) at Phase B start | — | Dev |
| R13 | Installer ~0.5 GB (cublasLt alone is 493 MB), so updates are slow | 5 | 2 | 10 | F | Accept for M4; post-M4 option to split the runtime into a separately versioned pack | User complaints | Release |
| R14 | SmartScreen / AV false positives on an unsigned or new-reputation binary | 4 | 3 | 12 | F | Authenticode signing (OV or Azure Trusted Signing); embedded python.exe is PSF-signed; avoid PyInstaller bootloader | Defender quarantine in VM tests | Release |
| R15 | Updater private key lost or leaked | 1 | 5 | 5 | F | Key in an offline vault plus CI secret; documented rotation; updater opt-in | — | Release |
| R16 | Two app instances fight over VRAM and DB | 3 | 3 | 9 | B | `tauri-plugin-single-instance` | Second window appears | Desktop |
| R17 | Port-race: chosen free port taken before Python binds | 2 | 2 | 4 | C | Retry with new ports (max 3) | Backend start failure | Desktop |
| R18 | Tests currently write into the real `backend/run/mustang.db` | 5 | 2 | 10 | C | Isolate `MUSTANG_RUN_DIR`/`MODELS_DIR` to `tmp_path` in `conftest.py` | Test conversations in user history | Backend |
| R19 | Sync `nvidia-smi` call blocks the event loop (≤2.5 s), causing stutter in token streams during polling | 3 | 2 | 6 | D | `asyncio.to_thread` + short cache (1 s) | Token gaps aligned to poll interval | Backend |
| R20 | Models folder on a removable or slow drive (e.g. `S:`) is unavailable at launch | 2 | 3 | 6 | C | Detect a missing root; show "models folder unavailable" instead of an empty library; never recreate silently on another drive | Empty library | Backend |
| R21 | Scope creep (RAG, cloud providers, agents) | 2 | 3 | 6 | all | Scope boundaries in proposal; reject in review | — | Lead |

## Top risks to retire first

1. **R1** — spike on day 1 of Phase B, before any other Tauri work.
2. **R5** — Job Object is the first Rust feature in Phase C.
3. **R2 / R4** — DLL closure plus the driver check early in Phase D, on a second machine.
