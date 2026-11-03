# Mustang AI — Milestone 4 Phase G: Release Testing Report (0.1.0 RC)

Environment: E1 (i7-13620H, RTX 5050 Laptop 8 GB, driver 617.42, 24 GB RAM, Windows 11 Home 26300). The tests ran from inside an MSIX-packaged desktop app: writes to `%LOCALAPPDATA%` from the app and the installer were redirected to the package's private `LocalCache`, so test installs never touched the real profile. Scratch data roots (`MUSTANG_DESKTOP_DATA_ROOT`) kept every run away from real data. The model file and `backend\run\mustang.db` were hash-checked after every step: unchanged.

---

## 1. Release gates

| Gate | Status | Evidence |
|---|---|---|
| G1 Functional | **Pass on E1** after two fixes (§3) and the soak fix (§7.1) | Security, download/resume, chat, load/switch, tray, update, uninstall scenarios (this report and Phase B–F reports) |
| G2 Automated | **Pass** | §6 |
| G3 Data safety | **Pass on E1** (scenarios 2–6 in Phase F; 1 on E1 only) | Phase F test report §2–6; legacy import in Phase E |
| G4 Security | **Pass** | §2 |
| G5 Performance | **Pass for throughput**; switch latency ~1.5 s median (1.0–1.6 s) vs a 1.0 s baseline that could not be reproduced (was 1.95 s at the start of Phase G) | §4 |
| G6 Clean machine | **Not run: needs E2/E3** | §8 |
| G7 Signing | **Not met: no certificate**. Signing pipeline ready (`scripts/sign.ps1`, `MUSTANG_SIGN_CERT_THUMBPRINT`); updater signature verification tested in Phase F with a test key | §8 |

The release candidate is functionally ready on E1. The soak finding (stalls of 40–100 s and a step of the backend's memory to ~2.1 GB) was traced to piled-up memory-estimate requests, each re-parsing the whole GGUF file, and is **fixed** (§7.1): a 30-min soak on the installed RC ran 1,524 chats with no errors and no stalls. The slow memory growth found next (~1 MB per load/chat cycle) was traced to garbage in reference cycles from per-request HTTP clients and the host-check middleware, and is **fixed** (§7.2). A 30-min desktop soak now grows ~0.13 MB per cycle instead of ~1.06. A small residual remains, and the 4-h soak hasn't been run. Switches got faster too (§4). G6 and G7 need hardware and a certificate that are not available here; see §8 for what to run.

## 2. Security checklist (release build, `src-tauri\target\release`)

| Check | Result |
|---|---|
| Listeners | backend and engine on `127.0.0.1` only (dynamic ports); nothing on 0.0.0.0 / :: |
| Backend without token / wrong token | 401 on every `/api` and `/v1` route; `/health` public |
| Bootstrap endpoint in desktop mode | 404 |
| Forged `Host` header | 403 |
| CORS preflight from a foreign origin | 400, no `Access-Control-Allow-Origin` |
| Engine without API key | 401; engine web UI 404 (`--no-webui`) |
| Session token on disk | absent from process command lines, `desktop.log`, `backend.log`, the database and the data root; no token file |
| CSP | injected inline script not executed; no `unsafe-eval` in the served policy; fetch to non-loopback hosts and to `localhost` blocked |
| Tauri ACL | `plugin:shell|*` and `plugin:fs|*` refused; `open_folder` limited to the app's folders, `restore_backup` rejects path traversal, `open_external` only `https://huggingface.co/…` |
| Quit | clean: no `python.exe` / `llama-server.exe` left |

## 3. Bugs found and fixed

### 3.1 Downloaded models could not be loaded from the UI

Pre-existing since Milestone 3, found by the download test. Downloads are saved per repository (`models\Org--Repo-GGUF\file.gguf`), but:

- `GET /api/models` (the model drop-down) listed only top-level files, and
- the Library and Download Queue **Load** buttons send the bare file name, which `/api/models/load` and `/estimate` resolved only at the top level → **404 "Model file not found"**.

Fix (`backend/app/api/models.py`): `resolve_model_reference()` accepts a library id, a top-level file name, or the bare file name of a single indexed library model (ambiguous names fall back to strict validation), and always ends in `validate_model_path`, so traversal and ADS checks are unchanged. The model list walks subfolders, skips hidden folders such as `.downloads` (partial downloads) and lists a file name once. Regression tests: `backend/tests/test_downloaded_model_loading.py` (4 tests; the two behaviour tests fail without the fix).

Desktop verification: see §5.

### 3.2 Regenerate/Edit could delete the wrong messages

Found by the flaky-test hunt: `test_conversation_crud_and_messages` failed 1 run in ~8 (`assert 2 == 1` after a truncate). Cause: `time.time()` in Python 3.11 on Windows advances in ~15.6 ms steps, so messages saved within one tick share `created_at`, and `/truncate` compared only `created_at`:

- **Edit** (truncate after a message) could keep a reply that should have been removed;
- **Regenerate** (inclusive truncate from the assistant reply) could also delete the **user's prompt** saved in the same tick.

Fix (`backend/app/api/conversations.py`, `backend/app/api/chat.py`): the implicit SQLite `rowid` (insertion order) breaks ties, both in `/truncate` and in every message ordering (conversation detail, chat history, "last message"). No schema change; existing databases benefit without migration (no `VACUUM` is used, so rowids keep insertion order). Regression tests: `backend/tests/test_message_order_same_timestamp.py` (3 tests, clock frozen; the two truncate tests fail without the fix).

## 4. Performance (G5)

Reference model `qwen2.5-0.5b-instruct-q4_k_m.gguf`, `tests/run_stage6_benchmarks.py` with an isolated run directory (scratch `MUSTANG_RUN_DIR`), Phase G code:

| Tier | Stage 6 baseline | Phase G | Change |
|---|---|---|---|
| 25 tokens | 314.4 tok/s (TTFT 164 ms) | 305.2 (178) | −2.9 % |
| 50 tokens | 352.0 (141) | 344.6 (131) | −2.1 % |
| 100 tokens | 323.4 (135) | 356.1 (131) | +10.1 % |
| 200 tokens | 331.7 (134) | 354.2 (133) | +6.8 % |
| VRAM at 8K context | 373.73 MiB | 373.73 MiB | 0 |
| Unload | 76 ms | 84 ms | |
| Context switch 2K→8K | 1024 ms | 1844 ms | see below |

**Throughput: pass** (every tier within 5 % or faster; TTFT unchanged).

**Switch latency** (Phase D left this open at ~1.95 s against a 1024 ms morning baseline that could not be reproduced):

- Engine start alone (packaged b12000, spawn → `/health` 200, 0.5B model): **0.79 s** warm (2.3 s on the very first cold start), identical to the dev runtime (0.79–0.85 s). Packaging is not the cause.
- Found: on Windows, a connect to a loopback port that is not listening yet is retried for ~1.1 s instead of being refused (measured: 1115–1191 ms per probe). The backend's readiness probe used a 1 s timeout and a 0.3 s interval, so the first probe always blocked past the moment the engine came up. **Fixed**: connect timeout 50 ms, interval 0.1 s (`llama_engine.py`). A/B with `switch_timing.py`: 1963/1959/1961 ms → 1820/1828/1809 and 1836/1792/1841 ms (−140 ms).
- Found next: the fixed **0.3 s socket cooldown** before respawning cost more than its own 0.3 s. llama-server's first output line (before CUDA init completes) comes ~190 ms after spawn when the previous one exited ≤ 0.1 s earlier, but ~600 ms after an idle gap of 0.3 s or more (`gap_test.py`, measured for gaps 0, 0.1, 0.3, 0.6, 1, 3 s). After the first line, output progresses at the same pace in the engine and standalone. Ruled out by measurement: the `-v` output (~400 KB, read in ~45 ms by any reader; standalone start is the same with and without `-v`, and without it the lines the output monitor parses for GPU layers and VRAM are not printed), the asyncio pipe reader, the readiness probe pattern, `LLAMA_API_KEY` and the parent's imports. **Fixed**: no cooldown; the engine closes its HTTP connections before stopping the server, so TIME_WAIT stays on its own ephemeral ports. 115 back-to-back switches on one fixed port with chats, some made mid-generation: **0 bind failures**. `switch_timing.py`: 1683–1781 ms → **998–1586 ms** (median ~1.5 s).
- Still open: even with no gap, most spawns take ~1.4 s to ready and some ~0.8 s, with no pattern by idle versus mid-generation switch. This looks driver-level (CUDA start-up after a context teardown); not tracked further. It does not affect chat speed.
- In the desktop app (installed RC): 1.5B load 2.3 s, switches 2.3–2.6 s; with the build machine busy (NSIS compressing) 1.9–2.1 s.

## 5. Download, resume and downloaded-model loading (release build)

| Step | Result |
|---|---|
| Download `Qwen/Qwen2.5-1.5B-Instruct-GGUF` q4_k_m (1,117,320,736 bytes) | started from the app's API (`/api/downloads`) |
| Quit during download | app exited in 611 ms; 379 MiB partial kept in `.downloads` |
| Relaunch | download `PAUSED`, "Interrupted by application restart" |
| Resume | HTTP `Range` request; `COMPLETED`, `VERIFIED`, SHA-256 `6a1a2eb6…407e` matches Hugging Face; 212 s |
| Notification | "Download complete" shown |
| Chat after download | OK (0.5B model loaded in 1993 ms, reply in 430 ms) |
| Model drop-down (`GET /api/models`) after the fix | lists `qwen2.5-0.5b-instruct-q4_k_m.gguf` and the downloaded `qwen2.5-1.5b-instruct-q4_k_m.gguf`; the `.downloads` folder is not listed |
| Memory estimate for the downloaded model by file name | 200 |
| Load the downloaded model by file name (as the Library / Download Queue buttons do) | **200**, `READY` in 2388 ms, path `…\Qwen--Qwen2.5-1.5B-Instruct-GGUF\qwen2.5-1.5b-instruct-q4_k_m.gguf` (before the fix: 404) |
| Chat on it | 200: "Three popular horse breeds include: 1. Arabian Horse … 2. Thoroughbred … 3. Quarter Horse …" |
| Switch 1.5B → 0.5B → 1.5B | both 200 / `READY`; rollback path untouched |
| Quit | clean, no leftover processes |

## 6. Automated tests

| Suite | Result |
|---|---|
| Backend pytest, dev venv | **162 passed** (151 + 11 new regression tests), repeated 4×: the only intermittent failure found (1 in ~8 runs of `test_conversation_crud_and_messages`) was the real bug in §3.2, now covered deterministically |
| Backend pytest `-m gpu` (live CUDA inference, cancel, engine key, concurrency) | **5 passed** |
| Backend pytest inside the embedded release runtime (`scripts/test_desktop_runtime.py`) | **162 passed** |
| Rust unit tests (`src-tauri`, `cargo test --lib`) | **20 passed** |
| Frontend `tsc --noEmit` | clean |
| Data guard (model + `backend\run\mustang.db` SHA-256) | unchanged after every step |

## 7. Soak

`scripts/e2e/phase_g_soak.ps1`, release build, scratch data root, 20.9 min (the 4 h soak of the plan is left to the release owner, §8). Each cycle: load (context alternating 4096/2048), 5 non-streamed chats through `/v1`, 1 streamed chat through `/api/chat/completions` (saved to a new conversation), unload every 4th cycle; samples after every cycle (CSV kept in the scratchpad).

| Measure | Result |
|---|---|
| Cycles / chats / errors | 53 / 318 / **0** |
| Engine processes at once | max 1 (no orphans); VRAM back to 0 after every unload |
| Shell (`mustang-desktop.exe`) | 28 → 29 MB, handles 364 → 358 |
| WebView2 | 416 → 431 MB (+3.7 %) |
| Backend handles | 298 → 328 (+10 %), flat over the last 38 cycles (318–340) |
| Quit afterwards | clean, no leftover processes |
| **Backend memory** | step to ~2.1 GB with stalls: cause found and fixed in §7.1 |

**Open finding: backend working set steps up to ~2.1 GB.** It is not a steady leak: the backend stays between 170 and 650 MB for 14 cycles, then rises once to ~2.06 GB during a cycle that took ~117 s instead of ~9 s, and then stays flat for the remaining 38 cycles (2.03–2.40 GB). Similar ~100 s cycles recur every few minutes (cycles 15, 19, 26, 31, 38, 43, 47, 53). A 3.8-min re-run reproduced it (cycle 13: 121 s, 748 → 2070 MB). In those cycles, single non-streamed `/v1/chat/completions` calls (`max_tokens` 96) took 30 s and 80 s.

Ruled out so far:
- Steady per-request leaks in the backend code: driving a source backend (real engine, isolated ports and run folder) with 60–150 calls each of hardware, status, conversations, downloads, chat, stream and load showed ≤ 0.5 MB per load and nothing for the others.
- Idle with the UI polling and a model loaded: flat (150–157 MB working set for 2 min).
- Engine output overflowing the output monitor: the longest `-v` line is 2 KB (limit 64 KB); ~100 KB of output per chat.

**Follow-up (20:31–20:45): the backend and the bundled runtime are not the cause.** The identical soak cycle (load with alternating context, 5 non-streamed `/v1` chats, 1 streamed `/api/chat` saved to a new conversation, unload every 4th cycle) was driven directly over HTTP (`scripts/e2e/direct_soak.py`, isolated ports and run folder, real engine):

| Backend | Cycles | Cycle time | Stalls | Working set |
|---|---|---|---|---|
| Source tree, dev venv | 67 in 5 min | 3.9–5.0 s | none | 79–115 MB, garbage-collection sawtooth, no growth |
| Embedded release runtime (`build\desktop-runtime\python` + bundled `backend`) | 48+ in 4 min | 4.3–4.8 s | none | 75–111 MB, same pattern |
| Desktop app, requests issued from the WebView page via CDP (the soak above) | 13 in 3.8 min / 53 in 21 min | ~9 s, ~100 s every few minutes | yes | step to ~2.1 GB |

The backend log of the slow cycle 15 shows where the time goes: httpx logs an engine call when its response headers arrive, so the gaps of **39.6 s and 67.5 s are between consecutive chats**, not inside the engine's replies. Each reply streams in ~0.3 s; the next request arrives late. No warnings or errors are logged in the whole soak window.

**Conclusion so far:** the stall happens on the desktop request path the harness uses (a `fetch` evaluated in the WebView page through `node scripts/e2e/cdp_eval.mjs`, one Node process per call), not in the backend or engine when driven on its own. The one-time ~1.4 GB step in the backend's working set happens in those same stalled cycles and is **still unexplained**: driven directly, the backend never grows.

**Throttling ruled out (20:43–20:48).** A 5.5-min soak on the release build with `--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows` added to the WebView2 arguments behaved the same: 19 cycles, 0 errors, a ~100 s cycle at cycle 15, and the step to ~2.0 GB (670 → 2018 MB), flat afterwards.

**Pattern across all three desktop runs:** the first stall and the memory step come at the **same time after app start**, t ≈ 105–125 s (cycle 15 at t 123 s, cycle 13 at t 105 s, cycle 15 at t 117 s), whatever the number of cycles done. That suggests something **time-triggered inside the desktop app** (the shell, the UI, or a backend background task started by a request only the desktop makes) rather than the soak workload itself. Every soak run starts the app fresh, which is why it shows up within the first 2 minutes.

**Strongest lead (20:49): a request hangs.** The shell's `desktop.log` shows the backend stop on quit: in the soak data root, **5 of 9 quits were `Forced`**, including every soak run; in the download data root, whose short runs never stalled, **7 of 7 were `Graceful`**. At the forced quits, uvicorn logged `Waiting for connections to close` and the shell killed the backend after its 10 s limit, so a request was still open. During the stall the shell's keep-awake shows no generation running (e.g. 94 s off). Together: in the desktop app, a request (most likely the page's streamed `/api/chat/completions` or a non-streamed `/v1` call) **stays open for ~40–70 s or longer** while nothing is generated, and the harness waits for it. The memory step plausibly belongs to whatever that request is doing. A real user would see this as a reply that stalls, and a quit that takes 10 s instead of ~1 s.

**Last run (20:55–20:57), with connection sampling:** a 2.1-min soak on the release build, sampling every 5 s which processes hold connections to the backend port. 15 cycles, 0 errors, **no stall and no step this time**. Every connection belonged to the WebView process (3–6 at a time: UI polling, the download-progress stream, the soak's request). The backend's working set still swung between 290 and 870 MB, against ~100 MB when the same cycle is driven directly. So the stall is **intermittent**: 3 of 4 desktop runs, always after t ≈ 100 s when it happens. The larger memory swings in the app happen even without a stall.

### 7.1 Root cause found and fixed: piled-up memory estimates

A request-tracing ASGI middleware (logging start, end of body and duration of every request; added to a **scratch copy** of the release app only) reproduced the stall in a 4-min soak (cycle 16: ~92 s, 746 → 2347 MB) and showed the cause: the UI's **`GET /api/models/<file>/estimate`** requests. They took ~5 s each at first and then queued up: 5.5 s, 10.9 s, 15.7 s, 17.4 s, 68.6 s, 93.7 s, 96–101 s; up to **7 were in flight at once** (31 in that run).

- **Why so many:** `ModelSelector.tsx` fetches an estimate whenever the selected model, GPU layers or context size changes, and also whenever `isModelLoaded` changes. Every load goes STARTING → READY and every unload flips it back, and the context slider fires on each step. Results the UI has already discarded still run to completion on the backend.
- **Why each was expensive:** `estimate_model_memory` re-parsed the whole GGUF file with `gguf.GGUFReader` on every call. Measured for the 0.5B model: ~3 s per parse holding **~460 MB of Python heap** (tracemalloc: 459 MB held, 466 MB peak; mostly the tokenizer arrays), plus ~1.5 s for `nvidia-smi`.
- **Effect:** overlapping parses competed for the GIL, so each got slower. They filled the worker-thread pool that other endpoints also use, so unrelated requests (the soak's chats) waited behind them for 40–100 s. Four or five parses alive at once is ~2 GB, the observed step. Requests still open at quit made uvicorn wait, hence the `Forced` stops. A user would hit this by dragging the context slider or loading and unloading models in quick succession.
- **Why the direct soaks were clean:** they never called `/estimate`; only the open UI did.

**Fix** (`backend/app/core/memory_estimator.py`): `_read_header()` reads only the architecture and the 8 integer hyperparameters the estimate uses. It caches them per file version (resolved path, size, mtime; at most 64 entries) and serializes parses with a lock, so concurrent requests share one parse instead of each holding their own. The estimate itself is still computed per request, because context, layers and live VRAM change it. Unparseable files are reported as before and not cached. Regression tests: `backend/tests/test_estimate_header_cache.py`:
- 16 concurrent estimates → 1 parse, with results still varying by context;
- a changed file is parsed again;
- a bad file is reported and not cached.

**Verified** on the scratch copy with the fix (same 4-min soak):

| | Before fix | After fix |
|---|---|---|
| Cycles in 4.1 min | 19 (one ~92 s) | **34** (7.3 s each, none slow) |
| Backend working set | 119–746 MB, then 2297–2347 MB | **119 → 157 MB** |
| `/estimate` latency | up to 101 s, 7 in flight | **mean 0.23 s**; one 5.3 s (the first, uncached parse) |
| Requests over 3 s | 31 estimates queued | **1** |
| Backend stop on quit | `Forced` | **`Graceful`** (~1 s) |

**Longer runs with the fix:**

| Run | Cycles / chats / errors | Slow cycles | Backend working set | Quit |
|---|---|---|---|---|
| Scratch copy with tracing, 20.1 min | 163 / 978 / 0 | none (1 request > 3 s: the first parse) | 105 → 294 MB | Graceful |
| Release build (`target\release`), 8 min | 73 / 438 / 0 | none | 141 → 221 MB | Graceful |
| **Installed RC `2fc4f4d5…`, 30.1 min** | **254 / 1524 / 0** | **none** | 105 → 375 MB | Graceful |

In the installed run, shell handles (366 → 358), backend handles (299 → 299), WebView (+3 %) and engine processes (max 1) are flat.

### 7.2 Root cause found and fixed: memory growth from reference-cycle garbage

With the stalls gone, the backend's memory still rose **linearly by ~1.06 MB per soak cycle** (each cycle: one model load, 6 chats, the UI's polling), in private bytes too, with threads and handles flat. At that rate the 4-h soak would have ended near 2 GB.

**Reproduced without the WebView.** An isolated source backend (scratch run dir, its own ports), driven by a script that adds the UI's concurrent traffic to the soak cycle (status every 2.5 s, hardware every 2 s, downloads every 5 s, the download-progress SSE stream, `Origin` headers, two estimates per load), grew the same way: 594 → 676 MB private over 80 cycles. Without the extra traffic it was a sawtooth that fell back every ~40 cycles, which is why the earlier direct runs looked flat.

**Mechanism.** A wrapper around the app logged Python's gc counters: during the whole run the backend did **no full (generation-2) collection** (11, all at start-up), while memory climbed. One forced `gc.collect()` freed 8,112 objects and dropped private bytes **669 → 589 MB** in 0.07 s. So the growth was garbage in reference cycles holding native memory, which Python 3.11 frees only on its rare full collections. That's why tracemalloc (Python heap ~14 MB) and the earlier single-endpoint tests missed it: a new client with no request, or calls followed by an explicit `gc.collect()`, look flat. A census of the cycle garbage (`gc.DEBUG_SAVEALL` over 150 s) showed two sources:

| Source | In the garbage (150 s) | Fix |
|---|---|---|
| `llama_engine` created a new `httpx.AsyncClient` per readiness probe (every 0.1 s during a load), per health probe (every 2 s) and per chat; after a request each one ended up in a cycle through exception tracebacks | 324 complete clients, each with its own `SSLContext` and connection pool; 347 socket transports holding 21.7 MB of read buffers | **One client per llama-server process**, closed when the process stops (`_http`, `_close_http`) |
| `HostValidationMiddleware` was a Starlette `BaseHTTPMiddleware`, which runs every request in an anyio task group and cancels it; the `CancelledError` tracebacks kept request frames alive | ~5,000 each of `TaskGroup`, `CancelScope`, `CancelledError`, `Task`, with 37k frames and 27k tracebacks | **Plain ASGI middleware** with the same checks |

| Run (all UI traffic) | Private bytes | Per cycle |
|---|---|---|
| Isolated backend, before | 594 → 676 MB over 80 cycles | ~1.0 MB |
| Isolated backend, client fix only | 573 → 640 MB over 120 cycles | ~0.55 MB |
| Isolated backend, both fixes | 578 → 590 MB over 120 cycles | **~0.1 MB** |

With both fixes the backend does far fewer young-generation collections (~26 against ~140 per minute), a sign much less garbage is made.

**Desktop app, 30-min soak on the rebuilt release (`target\release`), same harness as §7.1:**

| | Installed RC before the fix (§7.1) | After |
|---|---|---|
| Cycles / chats / errors | 254 / 1,524 / 0 | **341 / 2,046 / 0** (cycles are faster) |
| Backend working set | 105 → 375 MB (~1.06 MB per cycle) | **103 → 147 MB (~0.13 MB per cycle)** |
| Backend private bytes | rising linearly | 572 → ~615 MB |
| Handles / threads / WebView | flat / flat / +3 % | flat / flat / +3 % |
| Quit | Graceful | Graceful, no leftovers |

The remaining ~0.13 MB per cycle is small but not zero. At the soak's rate of ~11 model loads per minute it would add ~0.35 GB over 4 h, so the scripted 4-h soak **would still likely miss the plan's 10 % criterion** for the backend (the criterion assumes no growth; in normal use, with a few loads per session, this is a few MB). It hasn't been tracked further; one candidate is the anyio task groups that Starlette's streaming responses still use. The 4-h soak (§8) has still to be run before release.

Tests: `test_one_http_client_per_process_closed_on_stop` (one client across three chats, closed on stop); the existing host-check tests cover the middleware.

## 8. Not testable here: for the release owner

| Item | Why not here | How to run |
|---|---|---|
| G6 clean machine (E2) | no clean VM | Windows 11 VM without Python/Node/VS or an NVIDIA driver: run the installer, start, load the 0.5B model, chat (CPU), check the "Running on CPU" notice |
| G6 old driver (E3) | no second GPU machine | driver < R580: the app must start on CPU and show the driver message |
| G7 signing | no code-signing certificate | set `MUSTANG_SIGN_CERT_THUMBPRINT`, run `npm run release:build`; check `Get-AuthenticodeSignature` on the installer, `mustang-desktop.exe`, `uninstall.exe` |
| Real install outside the MSIX sandbox | the test shell's AppData writes are virtualized | install from Explorer on a normal account; confirm `%LOCALAPPDATA%\Mustang AI` and `%LOCALAPPDATA%\MustangAI` |
| Real Windows logoff / shutdown during generation and download | would end the test session | log off with a download running: expect the download `PAUSED` on next start and the engine stopped gracefully (`desktop.log`: `session ending`) |
| Standard (non-admin) account (E4) | only this account | install and run as a standard user |
| Windows 10 22H2 (E5), multi-GPU, older GPUs | hardware | best effort |
| 4 h soak | time | `scripts\e2e\phase_g_soak.ps1 -Exe <installed exe> -DataRoot <scratch folder> -Repo . -Minutes 240 -Csv soak.csv` (§7) |
| Defender scan of a signed build | needs G7 | full scan of the install folder |

## 9. Release candidate artifact

| Item | Value |
|---|---|
| File | `build\release\0.1.0\Mustang AI_0.1.0_x64-setup.exe` (578,053,609 bytes) |
| SHA-256 | `7228065c9330a5204356f32e32a42b001b8952a8dd61eb24f9f41f8319ae123f` (also in `SHA256SUMS.txt`) |
| Contents | all Phase G fixes (§3.1, §3.2, the readiness probe and the cooldown in §4, the estimate cache in §7.1, the cycle-garbage fixes in §7.2); unsigned; updater not configured |
| Silent install | exit 0 in 48 s |
| Installed app, 15-min soak (§7 harness) | 181 cycles / 1,086 chats / **0 errors** (the previous RC ran ~127 cycles in 15 min); no bind failures or rollbacks in the logs; backend working set 104 → 119 MB (~0.08 MB per cycle), private 573 → 589 MB; handles flat; max 1 engine process; graceful quit, no leftovers |

Superseded: `bc2a4273…2e43e` (before all fixes), `89150d1c…b0ee` (before the estimate fix), `2fc4f4d5…1df7` (before the §7.2 and cooldown fixes; installed and verified: downloaded 1.5B model loaded by file name in 2327 ms, chat, switches 2.2–2.4 s, clean quit), and `1d0df021…91ea` (before the cooldown fix; used for the 30-min soak in §7.2 from `target\release`).
