# Mustang AI — M4 Phase D Test Report

Real runs on 2026-11-02 (i7-13620H, RTX 5050 Laptop 8 GB, driver 617.42, Windows 11, WebView2 156, AC power, Balanced power plan), branch `m4-phase-d`. Desktop runs used scratch data roots. Suite runs were guarded by SHA-256 of `models/qwen2.5-0.5b-instruct-q4_k_m.gguf` and `backend/run/mustang.db`: **unchanged**.

## 1. Summary

| Area | Result |
|---|---|
| Backend suite, dev venv | **149 passed** (139 + 10 new), 10 of 11 runs; one run had 1 unidentified failure (§6) |
| Backend suite, embedded runtime + **packaged llama.cpp** (real CUDA) | **149 passed** |
| GPU tests (`-m gpu`) against the packaged runtime | **5 passed** |
| Rust unit tests | **11 passed** |
| Runtime build: closure verification, signatures, smoke test | pass |
| Pruned runtime: GPU inference / CPU fallback | 402 tok/s / 72 tok/s |
| Performance parity, packaged vs original runtime | equal within run-to-run noise |
| Release desktop E2E on the bundled engine | pass (3 launches) |
| Integrity: first launch / cached / tampered / restored | 521 ms / 1 ms / blocked with message / recovered |
| CPU-mode notice | shown (test hook) |
| `tauri dev` (debug fallback runtime) | pass |

## 2. llama-server behaviour probes (b12000)

| Probe | Result |
|---|---|
| `/health` without key | 200 (stays public for readiness) |
| `/v1/models`, `/slots`, completions without or with a wrong key | 401 |
| `/v1/models` with key | 200 |
| `/` with `--no-webui` | 404 |
| `POST /slots/0?action=cancel` | **501**: "This server does not support slots action" (the old engine's cancel was a no-op) |
| Close the stream after 20 chunks | slot idle after 0.13–0.15 s; server log `cancel task` |
| Generation / prompt throughput, full runtime | 379 / 1288 tok/s |
| Same, pruned runtime | 402 / 1057 tok/s |
| Pruned runtime with `CUDA_VISIBLE_DEVICES=-1` | "failed to initialize CUDA: no CUDA-capable device" → "no usable GPU found, --gpu-layers ignored"; 72 / 362 tok/s on the CPU |

## 3. Runtime build

```bash
npm run runtime:build
```

- Pinned zips reused from `backend/runtime/downloads` (SHA-256 match), no re-download.
- 31 files in `llama/`. `dumpbin` closure check: "every non-OS DLL import is present locally". It first failed on `libomp.dll → PSAPI.DLL`, which is a Windows DLL and was added to the OS list.
- Authenticode: `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` are valid, Microsoft Corporation. `python.exe`/`python311.dll` are valid, Python Software Foundation.
- `llama-server --version` under the minimal environment reports `version: 0.8.0-dev (build 12000, commit 68d20b884)`.

## 4. Performance (`tests/run_stage6_benchmarks.py`, isolated run directory)

Generation tok/s, with TTFT in ms in parentheses:

| Tier | Stage 6 baseline (morning) | Packaged run 1 | Original full runtime | Packaged run 2 |
|---|---|---|---|---|
| 25 tokens | 314.4 (164) | 303.0 (175) | 302.8 (172) | 274.7 (189) |
| 50 tokens | 352.0 (141) | 343.3 (134) | 350.4 (133) | 354.7 (142) |
| 100 tokens | 323.4 (135) | 323.7 (134) | 328.6 (134) | 346.2 (138) |
| 200 tokens | 331.7 (134) | 323.0 (137) | 341.1 (139) | 348.1 (135) |
| VRAM at 8K context | 373.73 MiB | 373.73 | 373.73 | 373.73 |
| Unload | 76 ms | 88 | 86 | 86 |
| Context switch 2K→8K | **1024 ms** | 1971 | 1967 | 1960 |

- **Packaged vs original runtime:** the mean of the two packaged runs against the original run is −4.6 % (25 tok), −0.4 % (50), +1.9 % (100) and −1.6 % (200). Packaged run 1 and run 2 differ by up to 10 % on the 25-token tier, so the runtimes are equal within noise. TTFT and VRAM are identical.
- **Switch latency:** ~1.95 s for every variant today. A direct A/B (`switch_timing.py`: three switches each) gave **1972/1911/1953 ms with the Phase D engine and 1969/1980/1942 ms with the Phase D engine edits stashed**, both on the packaged runtime. The original runtime also shows 1967 ms. Neither packaging nor Phase D code explains the difference from the morning baseline, which could not be reproduced. Re-measure in Phase G.

## 5. Desktop (release build, bundled engine)

| Check | Result |
|---|---|
| First launch (fresh root, import accepted) | `llama runtime verified: 31 files, 31 hashed, 521 ms`; GPU `Cuda`, RTX 5050 Laptop GPU, driver 617.42; E2E chat OK, 484 ms request, 66 tok/s |
| Second launch | `31 files, 0 hashed, 1 ms`; E2E chat OK, 439 ms |
| Third launch (final build) | E2E chat OK, 358 ms, 47.6 tok/s |
| llama-server image path | `…\target\release\llama\llama-server.exe` (bundled, not repo) |
| API key on the llama-server command line | **absent** |
| Visible windows owned by child processes (python, llama-server, conhost) | **0** |
| 64-hex secrets in `backend.log` / `desktop.log` | 0 / 0 |
| Release binary contains the repo runtime path | no (debug-only fallback) |
| Tamper: one byte changed in `llama\THIRD-PARTY-NOTICES.txt` (same size) | start-up blocked before any backend process: "The local inference engine files are damaged or were modified (THIRD-PARTY-NOTICES.txt: checksum mismatch). Reinstall Mustang AI to repair them." Shown on the failure screen and logged in `desktop.log` (logging fixed during testing) |
| Restore the original bytes | next launch re-hashes 1 file (2 ms) and starts normally; file SHA-256 identical to the build |
| `MUSTANG_DESKTOP_MIN_DRIVER=999.0` | `GpuStatus { mode: CpuOldDriver … }`; "Running on CPU" notice displayed, dismissible (moved from bottom-right, where it covered Send, to top-right) |
| `tauri dev` | runtime `dev-source`, "manifest check skipped" (development location), GPU `Cuda`; E2E chat OK, 411 ms, 76.8 tok/s |
| User's servers on 5173 / 18000 | untouched; no `llama-server` or app processes left afterwards |

## 6. Intermittent failure (open)

The first venv run after the runtime rebuild reported `1 failed, 148 passed`. The test name was not captured. Ten further full runs (5 with `-rf`) and 8 stress runs of the timing-sensitive tests (`test_slow_memory_estimate…`, engine GPU and hardening tests, live inference, model switching) all passed. Tracked for Phase G; candidates are tests with wall-clock thresholds.

## 7. Not tested

- A physical PC without an NVIDIA GPU, or with a pre-R580 driver (simulated via `CUDA_VISIBLE_DEVICES` and the minimum-driver hook).
- A PC without the VC++ Redistributable installed (the closure check and app-local copies cover it; Phase G clean-VM test).
- Installer / NSIS size (Phase F; LZMA proxy measured).
