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

