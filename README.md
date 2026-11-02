# Mustang AI

**Mustang AI** is a Windows-first, local-first conversational AI desktop application engineered to run open-weight language models locally on NVIDIA RTX hardware with zero cloud dependencies and legitimate unrestricted conversational capabilities.

---

## Hardware Target & Specifications
- **Operating System:** Windows 11 (64-bit)
- **Target GPU:** NVIDIA GeForce RTX 5050 Laptop GPU (8GB GDDR6 VRAM, CUDA 13.4 / 12.4)
- **Target Host RAM:** 24GB DDR5 Memory (Hybrid CPU Offload)
- **Inference Engine:** `llama.cpp` (`llama-server.exe`) pinned release build b12000 with CUDA acceleration

---

## Project Structure
```
S:\Mustang AI\
├── backend\
│   ├── app\
│   │   ├── main.py                  # FastAPI application with lifespan management
│   │   ├── config.py                # Loopback networking and security settings
│   │   ├── api\                     # System, Auth, Models, Chat, OpenAI endpoints
│   │   ├── core\                    # LlamaServer supervisor, Hardware telemetry, Memory estimator
│   │   └── db\                      # SQLite async persistence (aiosqlite + WAL mode)
│   ├── runtime\                     # Verified llama-server binaries and CUDA DLLs
│   │   ├── bin\                     # llama-server.exe, cublas64_*.dll, cudart64_*.dll
│   │   └── setup_runtime.py         # Automated SHA-256 verifier and installer
│   ├── tests\                       # Comprehensive pytest suite (unit, integration, GPU)
│   └── run_backend.py               # Backend service launcher
├── frontend\                        # Obsidian Dark Vite + React 18 + Tailwind CSS
│   ├── src\
│   │   ├── components\              # StatusBar, ModelSelector, ChatInterface, PromptInput
│   │   ├── hooks\                   # useChatStream, useHardware
│   │   └── services\                # Typed REST client with X-Mustang-Token auth
└── models\                          # Local GGUF models directory
```

---

## Quickstart

### 1. Start the Backend API Service
```powershell
cd "S:\Mustang AI\backend"
.\.venv\Scripts\python.exe run_backend.py
```
Backend binds strictly to loopback: `http://127.0.0.1:18000`

### 2. Start the Frontend Development Interface
```powershell
cd "S:\Mustang AI\frontend"
npm run dev
```
Frontend serves at: `http://127.0.0.1:5173`

### 3. Run Automated Tests
```powershell
cd "S:\Mustang AI\backend"
.\.venv\Scripts\pytest.exe -v
```
Tests run against a private run directory and a private, hard-linked copy of `models\`; they never modify `backend\run\mustang.db` or delete your models.

---

## Desktop App (Milestone 4, in progress)

Requires Rust (MSVC toolchain, pinned by `rust-toolchain.toml`) and Visual Studio Build Tools (C++).

```powershell
cd "S:\Mustang AI"
npm run desktop:dev        # native window; backend from source (backend\.venv), UI from Vite on 5173
npm run desktop:build      # embedded Python runtime + release app: src-tauri\target\release\mustang-desktop.exe
npm run runtime:test       # backend test suite inside the embedded runtime
npm run release:build      # Windows installer: buildelease\<version>\Mustang AI_<version>_x64-setup.exe
npm run version:set -- 0.2.0   # bump the version in every file that records it
```

Signing and opt-in updates are configured through environment variables; see `scripts/build_release.py` and `docs\milestone-4\M4_PHASEF_IMPLEMENTATION_REPORT.md`.

- User data lives in `%LOCALAPPDATA%\MustangAI\` (`data\mustang.db`, `logs\`, `desktop.json`). On first launch the app offers to import an existing `backend\run\mustang.db` (copied read-only) and to keep using the existing `models\` folder in place.
- The shell starts the backend on free loopback ports with a per-launch session token and stops it gracefully on exit.
- Release builds bundle a pruned llama.cpp b12000 CUDA 13.4 runtime (`build\desktop-runtime\llama`, verified against `runtime-manifest.json` at every start). Without an NVIDIA GPU, or with a driver older than R580, inference falls back to the CPU and the app says so.
- The app keeps running in the system tray when its window is closed (Settings → Desktop to change), stops the engine gracefully on Quit or Windows logoff, and accepts `mustang-desktop.exe --unload-model` / `--quit` from a second launch.
- Build release binaries with `npm run desktop:build` only. A plain `cargo build --release` produces a dev-mode binary that loads the Vite dev server.
- Design and status: `docs\milestone-4\`.

