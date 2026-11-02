# Mustang AI — M4 Phase D Implementation Report

**Phase D: llama.cpp and CUDA integration. Status: complete; awaiting approval for Phase E.**
Branch `m4-phase-d` (from `m4-phase-c`). Date: 2026-11-02.

---

## 1. Outcome

The release app now carries its own inference engine and no longer depends on anything in the repository:

- **Bundled, pruned llama.cpp runtime.** b12000 CUDA 13.4 is reduced from 53 files to the 31 that `llama-server` actually loads. That set includes the app-local Visual C++ runtime the binaries import and the license notices. It is 715 MiB.
- **Integrity check at every start.** Every bundled engine file is verified against the build manifest. A full SHA-256 pass took 521 ms on first launch; later launches take about 1 ms using a size+mtime cache. A modified file blocks start-up with a "reinstall to repair" message.
- **GPU and driver detection.** If there is no NVIDIA GPU, or the driver is older than R580, llama.cpp falls back to the CPU and the UI shows a dismissible "Running on CPU" notice explaining why.
- **Engine hardening.**
  - Each llama-server process gets its own API key through the `LLAMA_API_KEY` environment variable, never argv, so other local programs can't use the engine port.
  - llama-server's built-in web UI is turned off (`--no-webui`) and it is blocked from network access (`--offline`).
  - No console windows.
  - No `PATH` lookup for `llama-server`/`nvidia-smi` in desktop mode.
  - `top_k` is now forwarded to the model.
  - **Cancel actually stops generation** inside llama-server.

## 2. Findings that shaped the work

| Finding | Evidence | Consequence |
|---|---|---|
| The old engine's cancel call never worked | `POST /slots/0?action=cancel` returns **501**, "This server does not support slots action" (risk R7) | Cancel now closes the upstream HTTP stream. llama-server logs `cancel task` and the slot is idle within 0.13–0.15 s |
| llama.cpp binaries need the **Visual C++ runtime** (`VCRUNTIME140.dll`, `VCRUNTIME140_1.dll`, `MSVCP140.dll`), which is not in the release zips | `dumpbin /dependents` over the closure | These three DLLs are bundled app-locally from the Build Tools redistributable (Authenticode: Microsoft Corporation). Without them a PC lacking the VC++ Redistributable could not start the engine. The shell exe needs no VC runtime (static CRT); embedded Python ships its own |
| `cudart64_13.dll` is not needed | `ggml-cuda.dll` imports only `cublas64_13.dll`; GPU inference from the pruned folder runs at 402 tok/s | Excluded (−0.5 MB) |
| `libomp.dll` imports `PSAPI.DLL` | found by the build-time closure verifier | Recognised as a Windows DLL; the verifier fails the build on any unresolved non-OS import |
| The llama.cpp MIT license is not in the release zips | zip listing | The build fetches `LICENSE` at the pinned tag and writes `THIRD-PARTY-NOTICES.txt` (llama.cpp MIT, LLVM OpenMP Apache-2.0 with LLVM exception, NVIDIA cuBLAS under the CUDA EULA, Microsoft VC++ runtime) |
| b12000 reads `LLAMA_API_KEY` from the environment and has `--no-webui` and `--offline` | `llama-server --help` | Key kept off the command line (verified: not present in the process's command line) |
| CPU fallback works without code changes | `CUDA_VISIBLE_DEVICES=-1`: "no CUDA-capable device … no usable GPU found, --gpu-layers ignored", generation 72 tok/s | No forced `-ngl 0` needed; the shell only informs the user |

## 3. Changes

### 3.1 Runtime build (`scripts/build_desktop_runtime.py`)
- New `llama/` step. It reuses the pinned tag, URLs and SHA-256 values from `backend/runtime/setup_runtime.py` (single source of truth) and the already-downloaded zips when their hashes match.
- It extracts **only** the allow-listed files (`LLAMA_FILES`): `llama-server.exe` and its DLLs, `ggml-cuda`, all 14 CPU-dispatch backends, cuBLAS/cuBLASLt and libomp. Excluded: the 20+ tool executables, `ggml-rpc` (network backend) and `cudart64_13.dll`.
- It copies and signature-checks the VC++ runtime DLLs and fetches and writes the license notices.
- **Closure verification:** every `.exe`/`.dll` in `llama/` must import only local files or Windows DLLs (`dumpbin`), otherwise the build fails.
- Smoke test: `llama-server --version` under a minimal environment must report `build 12000`.
- `runtime-manifest.json` gains a `llama` section with name, size and SHA-256 for each file.

### 3.2 Backend
| File | Change |
|---|---|
| `app/core/llama_engine.py` | Per-spawn 256-bit API key via `LLAMA_API_KEY`; `Authorization: Bearer` on completion requests; `--no-webui --offline`; `CREATE_NO_WINDOW`; PATH fallback only outside desktop mode; `top_k` forwarded; cancel closes the upstream stream (plus a per-line cancel check) and ends with `finish_reason="stop"`, the same contract as `MockEngine`; the dead `/slots/0?action=cancel` call removed |
| `app/core/hardware.py` | `nvidia-smi` from `%SystemRoot%\System32` (PATH fallback only outside desktop mode); `CREATE_NO_WINDOW` |
| `tests/test_engine_hardening.py` (new, 8 tests) | API key in env not argv, fresh key per spawn, flags, no console, no PATH in desktop mode, auth header and `top_k` sent, cancel ends cleanly, upstream errors still raised, `nvidia-smi` resolution and no console |
| `tests/test_engine_gpu_hardening.py` (new, 2 GPU tests) | Real llama-server: 401 without/with a wrong key on `/v1/models`, `/slots` and completions; `/health` public; web UI 404. Cancel stops the slot inside llama-server and the engine is usable again |

### 3.3 Desktop shell
| File | Change |
|---|---|
| `src-tauri/src/runtime_check.rs` (new) | `verify_llama()`: manifest check with a SHA-256 cache in `<data>\runtime-verified.json`, keyed by release tag and per-file size and mtime. `detect_gpu()`: `System32\nvidia-smi.exe` without a console window and with an 8 s timeout, classified as `cuda` / `cpu_no_gpu` / `cpu_old_driver` (minimum R580; test hook `MUSTANG_DESKTOP_MIN_DRIVER`). 3 unit tests |
| `src-tauri/src/paths.rs` | llama.cpp from the bundled `llama/` resource; the repo `backend/runtime/bin` fallback is now **debug builds only** (absent from the release binary, verified with `strings`); `runtime_manifest()` |
| `src-tauri/src/backend.rs` | Preparation runs the integrity check (splash: "Verifying the inference engine") and GPU detection; `backend_info` includes `gpu`; data directories recorded as soon as resolved, so preparation failures are logged and the failure screen shows the log folder |
| `src-tauri/tauri.release.conf.json` | Bundles `llama/` |
| `src-tauri/Cargo.toml` | `sha2` |

### 3.4 Frontend
- `services/connection.ts`: `GpuStatus` taken from `backend_info`.
- `components/BackendGate.tsx`: dismissible "Running on CPU" notice (top-right) with the shell's explanation.

### 3.5 Test tooling
`scripts/test_desktop_runtime.py` now points GPU tests at the packaged `build/desktop-runtime/llama`.

## 4. Size

| Part | Uncompressed |
|---|---|
| llama.cpp runtime (31 files; cuBLASLt alone is 470 MiB) | 715 MiB |
| Embedded Python + dependencies | 110 MiB |
| Backend code | 0.7 MiB |
| Shell exe | 4.2 MiB |
| **Total payload** | **823 MiB**; **549 MiB** compressed with LZMA (`xz -6`), a proxy for the NSIS installer |

Phase A estimated 0.45–0.55 GB for the installer. The measured ~0.55–0.58 GB is at the top of that range, because Python and numpy (needed by `gguf`) take 110 MiB rather than the estimated 60.

## 5. Known limitations and items for later phases

| Item | Phase |
|---|---|
| **Model-switch latency is ~1.95 s today**, against 1.02 s in this morning's Stage 6 baseline. A/B runs show the same ~1.95 s with the original full runtime and with the pre-Phase-D engine code, so neither packaging nor this phase's changes caused it. It is environmental and still unexplained; re-measure in Phase G | G |
| One unidentified intermittent failure in 1 of 11 full backend-suite runs (not reproduced in 10 further full runs or 8 stress runs of the timing-sensitive tests) | G (track) |
| CPU fallback verified at the llama-server level (`CUDA_VISIBLE_DEVICES=-1`) and via the UI notice hook; not yet on a physical machine without an NVIDIA GPU | G |
| First `nvidia-smi` call adds about 2 s to a cold start (it could run in parallel with the backend spawn) | E/G |
| The CPU notice overlaps the context-size selector until dismissed | E (UI polish) |
| The integrity check covers `llama/` only (the native GPU code); `python/` and `backend/` are protected by install-directory permissions and code signing in Phase F | F |
| Code signing of the shell and installer; installer, upgrade and uninstall | F |
