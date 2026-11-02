# Mustang AI — Milestone 4 Packaging Strategy

Phase A deliverable. Covers Tauri shell, Python backend packaging, the llama.cpp/CUDA runtime, data directories, installer, upgrades and uninstall.

---

## 1. Bundle layout (install directory)

```
%LOCALAPPDATA%\Programs\Mustang AI\
├── Mustang AI.exe                 Tauri 2 shell (Rust, ~10 MB)
├── resources\
│   ├── python\                    CPython 3.11.9 embeddable distribution (PSF-signed python.exe)
│   │   ├── python311._pth         isolated: python311.zip, ., Lib\site-packages, ..\backend
│   │   └── Lib\site-packages\     fastapi, uvicorn[standard], pydantic(-core), aiosqlite, httpx,
│   │                              psutil, gguf, sse-starlette … (hash-locked)
│   ├── backend\app\…              existing backend package, copied as-is (no tests, no run\)
│   ├── backend\mustang_launcher.py
│   └── llama\                     pruned llama.cpp b12000 CUDA 13.4 runtime (see §3)
│       └── runtime-manifest.json  file list + SHA-256 for startup integrity check
└── (frontend dist\ is embedded into Mustang AI.exe by Tauri)
```

## 2. Python backend packaging

### 2.1 Decision: embeddable CPython + vendored site-packages

| Approach | Verdict | Reason |
|---|---|---|
| **Embeddable CPython 3.11.9 + `pip install --target` from a hash-locked requirements file** | **Chosen** | `python.exe` is Authenticode-signed by the PSF, so it triggers far fewer AV/SmartScreen false positives than a freshly built bootloader. No freezing magic: uvicorn's dynamic imports simply work. Tracebacks are readable. It upgrades by replacing files. ~45–60 MB. |
| PyInstaller `--onedir` | Fallback | Mature, but its bootloader is a frequent AV false-positive source, and uvicorn/pydantic need hidden-import maintenance. |
| PyInstaller `--onefile` | Rejected | Extracts to `%TEMP%` on every launch (slow start, AV scanning, leftover dirs on crash). |
| Nuitka | Rejected | Long compile, C toolchain requirement, little benefit for an I/O-bound service. |
| Require a system Python | Rejected | Violates "native app, no prerequisites". |

### 2.2 Build steps (Phase C)

1. Produce `backend/requirements.lock` with `pip-compile --generate-hashes` from `pyproject.toml` (runtime deps only; no pytest).
2. Download `python-3.11.9-embed-amd64.zip` from python.org and verify a pinned SHA-256.
3. `pip install --require-hashes --only-binary=:all: --target resources/python/Lib/site-packages -r requirements.lock`.
4. Write `python311._pth` (keeps the interpreter isolated from the user's PYTHONPATH and site-packages; `import site` disabled).
5. Copy `backend/app/**` (excluding `__pycache__`), plus the launcher.
6. Pre-compile with `python -m compileall` so the read-only install dir never needs `.pyc` writes.

### 2.3 Launcher contract

`mustang_launcher.py` (new, small): reads `MUSTANG_*` env, sets `sys.dont_write_bytecode`, and calls `uvicorn.run(app, host="127.0.0.1", port=settings.PORT, log_config=<file logger>)`. It passes the app object, not the string. Logs go to `data\logs\backend.log` (rotating, 5 × 5 MB), because there is no console.

Environment set by Rust at spawn:

| Variable | Value |
|---|---|
| `MUSTANG_DESKTOP` | `1` |
| `MUSTANG_PORT` / `MUSTANG_ENGINE_PORT` | free ports chosen per launch |
| `MUSTANG_SESSION_TOKEN` / `MUSTANG_ENGINE_API_KEY` | 256-bit random, per launch |
| `MUSTANG_RUN_DIR` | `%LOCALAPPDATA%\MustangAI\data` |
| `MUSTANG_MODELS_DIR` | user setting, default `%LOCALAPPDATA%\MustangAI\models` |
| `MUSTANG_DOWNLOADS_DIR` | `<models>\.downloads` (same volume, so the final `os.replace` stays atomic) |
| `MUSTANG_RUNTIME_DIR` | `<install>\resources\llama` |
| `PYTHONNOUSERSITE`, `PYTHONUTF8` | `1` |

The child environment is built from an allowlist (SystemRoot, TEMP, USERPROFILE, LOCALAPPDATA, PATH limited to `System32`), not inherited wholesale.

## 3. llama.cpp + CUDA runtime distribution

### 3.1 Version and variant

- Ship **only** `llama-b12000-bin-win-cuda-13.4-x64` + `cudart-llama-bin-win-cuda-13.4-x64`. The pinned SHA-256 values already exist in `backend/runtime/setup_runtime.py`, and the build script reuses them.
- CUDA 13.x is required for native Blackwell (sm_120) support on the RTX 5050. The CUDA 12.4 variant is dropped.
- The CUDA *driver* is not bundled (not redistributable). Rust checks the driver version at startup via `nvidia-smi --query-gpu=driver_version` and shows a clear "update NVIDIA driver to ≥ R580" message if it is too old.

### 3.2 Pruning

Keep only what `llama-server.exe` loads:

```
llama-server.exe, llama-server-impl.dll, llama.dll, llama-common.dll, mtmd.dll,
ggml.dll, ggml-base.dll, ggml-cuda.dll, ggml-cpu-*.dll (all variants: CPU dispatch),
cudart64_13.dll, cublas64_13.dll, cublasLt64_13.dll, libomp.dll, LICENSE files
```

Remove the bench, quantize, cli, tts, mtmd/llava/gemma/qwen CLIs, `llama.exe`, and especially `ggml-rpc-server.exe` / `ggml-rpc.dll` (a network-listening tool with no place in this product). Phase D must verify the exact DLL closure: run `llama-server --version` and a real load under Process Monitor, then freeze the list in `runtime-manifest.json`.

### 3.3 DLL resolution

All runtime DLLs sit beside `llama-server.exe`; Windows searches the application directory first, so no PATH changes are needed. In desktop mode the backend **must not** fall back to `shutil.which("llama-server")`. The installer never writes to PATH or System32.

### 3.4 CPU fallback

The ggml backend loader loads `ggml-cuda.dll` dynamically. On a machine with no NVIDIA driver, CUDA backend registration should fail and llama-server should continue on the best `ggml-cpu-*` variant. Phase D verifies this on a machine without a GPU driver. The UI then shows "CPU mode" instead of failing.

### 3.5 Integrity

At startup, Rust verifies the size and SHA-256 of `llama-server.exe`, `ggml-cuda.dll` and the CUDA DLLs against `runtime-manifest.json` (cached by file mtime, so it costs nothing after the first run). On a mismatch it refuses to start the engine and offers "Repair installation".

## 4. Tauri 2 configuration (Phase B)

- `src-tauri/` placed at the repo root beside `frontend/`. `build.frontendDist = "../frontend/dist"`, `build.devUrl = "http://127.0.0.1:5173"`, `beforeBuildCommand = "npm --prefix frontend run build"`.
- Plugins: `single-instance` (two instances would contend for VRAM and the DB), `opener` (scoped to `https://huggingface.co/*`), `updater` (Phase F, opt-in), `log`. **No `shell` plugin and no `fs` plugin.** Rust spawns processes with `std::process::Command` internally, so the webview never gets spawn rights.
- WebView2: use the Evergreen runtime (preinstalled on Windows 11). The installer uses `webviewInstallMode: downloadBootstrapper`, with `embedBootstrapper` as an offline option.
- Window: 1280×800 default, min 960×640, dark title bar, remembers size and position.

## 5. Installer

| Choice | Decision |
|---|---|
| Format | **NSIS** via the Tauri bundler (`targets: ["nsis"]`). MSI is not produced in M4. |
| Install mode | `currentUser`: no admin, installs to `%LOCALAPPDATA%\Programs`. |
| Size estimate | Uncompressed ~0.78 GB (runtime ~0.71 GB, Python ~0.06 GB, shell ~0.01 GB). The NSIS installer (LZMA) is ~0.45–0.55 GB. |
| Signing | Authenticode-sign `Mustang AI.exe`, the installer, and the uninstaller (OV certificate or Azure Trusted Signing). Unsigned builds work but show SmartScreen warnings. |
| Offline | The installer is fully offline; no model, runtime or account download is required to install. |

## 6. User data and first run

1. On first launch, Rust creates `%LOCALAPPDATA%\MustangAI\{data,models,logs,backups}`.
2. **Migration from the dev layout:** if `S:\Mustang AI\backend\run\mustang.db` (or a user-pointed legacy folder) exists, offer "Import existing conversations and models". This *copies* the DB (after a SQLite `VACUUM INTO` for a consistent snapshot) and *points* `MODELS_DIR` at the existing models folder. Nothing is moved or deleted.
3. The models folder is configurable in Settings. Changing it never moves files automatically; the library rescans.

## 7. Upgrades

- Tauri updater (Phase F) with minisign-signed `latest.json`. It is **disabled by default** and checked only on user request or opt-in, consistent with "no mandatory cloud".
- An upgrade replaces only the install directory. Before the backend applies a schema change, it writes `backups\mustang-<oldver>-<timestamp>.db` (keeps the last 5).
- A `schema_version` table (added in Phase C) lets a newer DB be detected by an older app. In that case the app opens read-only with a "this data was created by a newer version" notice instead of corrupting it.
- Size concern: each update is a full ~0.5 GB installer. Acceptable for M4. Splitting the runtime into a separately versioned pack (reusing `setup_runtime.py`'s pinned-hash model) is a post-M4 option.

## 8. Rollback

- Installing an older signed installer over a newer one is supported (NSIS allows downgrade with a warning).
- DB compatibility: migrations stay additive. If a downgrade meets a newer `schema_version`, restore from `backups\` via Settings → "Restore backup".

## 9. Safe uninstallation

- The uninstaller removes only `%LOCALAPPDATA%\Programs\Mustang AI\` and shortcuts.
- The Tauri NSIS "Delete application data" checkbox only targets `%APPDATA%\<identifier>` and `%LOCALAPPDATA%\<identifier>` (WebView2 cache). User data lives in `%LOCALAPPDATA%\MustangAI\`, which is a different directory, so it is never touched.
- A custom NSIS template (`installer.nsi` hook) is used only to add the uninstall text: "Your conversations and models in … were kept."
- Uninstall first asks the running app to quit (single-instance IPC), so no file locks remain.
- The installer never deletes anything outside `$INSTDIR`. A user-chosen models folder such as `S:\Mustang AI\models` is never written to by the installer.

## 10. System tray and graceful shutdown

- Tray menu: Show/Hide, model status (read-only), Unload model, Quit.
- Closing the window minimizes to the tray by default (a setting can make it quit instead).
- Quit sequence:
  1. Rust calls `POST /api/system/shutdown` with the token.
  2. The backend cancels any generation, persists partial output, stops downloads (they become `PAUSED`), stops llama-server, and exits.
  3. Rust waits up to 10 s, then closes the Job Object (kills any survivor).
  4. The app exits.
- Windows logoff/shutdown (`WM_QUERYENDSESSION`) runs the same sequence with a 5 s budget.
