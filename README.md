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
│   │   ├── agent\                   # Agent mode: tool loop, file tools, change journal, terminal, task list
│   │   ├── core\                    # LlamaServer supervisor, Hardware telemetry, Memory estimator, history fitting
│   │   └── db\                      # SQLite async persistence (aiosqlite + WAL mode)
│   ├── runtime\                     # Verified llama-server binaries and CUDA DLLs
│   │   ├── bin\                     # llama-server.exe, cublas64_*.dll, cudart64_*.dll
│   │   └── setup_runtime.py         # Automated SHA-256 verifier and installer
│   ├── tests\                       # Comprehensive pytest suite (unit, integration, GPU)
│   └── run_backend.py               # Backend service launcher
├── frontend\                        # Obsidian Dark Vite + React 18 + Tailwind CSS
│   ├── src\
│   │   ├── components\              # StatusBar, ModelSelector, ChatInterface, PromptInput, AgentBar, ToolSteps, TaskPanel
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

## Agent Mode (Milestone 5)

Turn on **Agent** in the prompt bar, choose a **workspace folder**, and the model can work in that folder like a small local coding assistant. Everything runs on your own machine through the same local engine; nothing is sent anywhere.

| Tool | What it does |
|---|---|
| `list_files`, `find_files`, `search_files`, `read_file` | Explore the workspace: folder trees, files by name (glob), text or regex inside files (optionally only some files), files read in parts (`start_line`/`end_line`) |
| `create_file`, `edit_file` | Create a file (missing folders included), replace one, or change one exact piece of text. Each change is shown as a diff and needs your approval |
| `run_command` | Run one Windows PowerShell command in the workspace. **Off by default**; see below |
| `update_tasks` | A short task list the agent keeps current, shown in a **Tasks** panel above the prompt box and saved with the chat |
| `get_current_time`, `calculator`, `search_history` | Small helpers |

**Safety model**
- File tools only see the workspace folder. Absolute paths, `..`, drive prefixes, reserved device names, links and junctions, and the app's own backup folder are refused; searches skip hidden folders and dependency folders such as `node_modules`. The agent never creates program or script files (`.exe`, `.bat`, `.ps1`, `.js`, ...).
- Every file change needs your approval, can be undone (the previous version is kept in a hidden `.mustang-backups` folder inside the workspace), and **Approve all in this chat** covers file changes only.
- **Terminal** is a separate switch per chat, asks before it turns on, and shows the exact command and folder for every run. Commands are never covered by "Approve all", stop after a timeout (60 s by default) and on **Stop** (the whole process tree), keep only the start and end of long output, get an environment without keys or tokens, and a short list of system-level commands (administrator, shutdown, disk formatting, registry, services, ...) is refused even when approved. **It is not a sandbox**: an approved command runs with your own permissions. Details: `docs\milestone-5\TERMINAL_ACCESS.md`.
- The agent stops on its own after 24 tool steps per reply (a Continue button resumes), when it repeats the same call, or when its calls keep failing.

**Using it well**
- Load the model with a context of **16,384 or more**. Agent instructions and tool definitions alone take about 1,500 tokens; at 4,096 the model loses the task. The app warns about this and offers a one-click reload.
- Long chats are fitted to the model's context window: old tool output is shortened and the oldest messages are left out, replaced by a short summary of the user's requests and the work done. Stored chats are never changed.
- Tested with Qwen3.5 4B and a Qwen3.5 9B fine-tune. Models under about 3B misuse tools. A local model is much less reliable than a large cloud model: expect to review its work, and a short fresh chat per task works better than one very long chat.
- Design, test reports and findings: `docs\milestone-5\`.

**Performance:** the engine runs with n-gram speculative decoding (code rewrites 6 to 7 times faster, identical output), `--fit` (layers that do not fit in video memory go to the CPU instead of failing), one slot, a cap on thinking tokens, and no thinking while the agent works (Settings, Thinking). On the four test coding tasks this took the 9B from about 100 s to about 68 s. Measurements, what did not help, and what was not measured: `docs\PERFORMANCE.md`; benchmark: `scripts\bench_engine.py`.

**Status:** M5 phases 0 to E are done (tools, loop, UI, file changes with approval and Undo, terminal, task list, search). Phase F (security review and release testing) is not done, and the installer is unsigned.

---

## Desktop App (Milestone 4, release candidate 0.1.0)

Requires Rust (MSVC toolchain, pinned by `rust-toolchain.toml`) and Visual Studio Build Tools (C++).

```powershell
cd "S:\Mustang AI"
npm run desktop:dev        # native window; backend from source (backend\.venv), UI from Vite on 5173
npm run desktop:build      # embedded Python runtime + release app: src-tauri\target\release\mustang-desktop.exe
npm run runtime:test       # backend test suite inside the embedded runtime
npm run release:build      # Windows installer: build\release\<version>\Mustang AI_<version>_x64-setup.exe
npm run version:set -- 0.2.0   # bump the version in every file that records it
```

Signing and opt-in updates are configured through environment variables; see `scripts/build_release.py` and `docs\milestone-4\M4_PHASEF_IMPLEMENTATION_REPORT.md`.

- User data lives in `%LOCALAPPDATA%\MustangAI\` (`data\mustang.db`, `logs\`, `desktop.json`). On first launch the app offers to import an existing `backend\run\mustang.db` (copied read-only) and to keep using the existing `models\` folder in place.
- The shell starts the backend on free loopback ports with a per-launch session token and stops it gracefully on exit.
- Release builds bundle a pruned llama.cpp b12000 CUDA 13.4 runtime (`build\desktop-runtime\llama`, verified against `runtime-manifest.json` at every start). Without an NVIDIA GPU, or with a driver older than R580, inference falls back to the CPU and the app says so.
- The app keeps running in the system tray when its window is closed (Settings → Desktop to change), stops the engine gracefully on Quit or Windows logoff, and accepts `mustang-desktop.exe --unload-model` / `--quit` from a second launch.
- Build release binaries with `npm run desktop:build` only. A plain `cargo build --release` produces a dev-mode binary that loads the Vite dev server.
- Downloaded models are stored per repository in subfolders of the models folder; they are listed and loaded by file name like top-level models.
- Users: install with `Mustang AI_<version>_x64-setup.exe` (per user, no admin). Help: `docs\TROUBLESHOOTING.md`. Release notes and test status: `docs\milestone-4\M4_RELEASE_NOTES_0.1.0.md`, `docs\milestone-4\M4_PHASEG_RELEASE_REPORT.md`.
- Design and status: `docs\milestone-4\`.


## License

Mustang AI is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution. The name and logo are not covered by the license.

The Windows installer bundles third-party components under their own licenses: llama.cpp (MIT), LLVM OpenMP, the NVIDIA CUDA runtime, the Microsoft Visual C++ runtime and the Python runtime. Their notices are installed with the app.
