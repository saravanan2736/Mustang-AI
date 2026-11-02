# Mustang AI — M4 Phase B Process Lifecycle Report

**Question:** can Rust supervise a child process tree (Python backend → llama-server) on Windows, including cleanup when the desktop app itself is killed?

**Answer: yes, with a Windows Job Object.** 9/9 prototype scenarios pass. A no-Job control run shows the exact production risk (an orphaned grandchild), and the Job Object eliminates it, both in the standalone prototype and inside the real Tauri app.

---

## 1. Prototype

`desktop-prototypes/process-supervisor/` is a standalone Rust crate (`mustang-supervisor`, ~450 LOC). It depends only on `windows-sys 0.61` and does not touch the production Python backend.

| File | Purpose |
|---|---|
| `src/lib.rs` | `Supervised` child: spawn, stdout/stderr capture (500-line ring buffer + event callback), secret redaction, exit monitoring (expected vs unexpected), graceful-then-forced shutdown, allow-listed environment |
| `src/win.rs` | `JobObject` (`KILL_ON_JOB_CLOSE` + `DIE_ON_UNHANDLED_EXCEPTION`), `spawn_in_job`, `process_alive`, `IsProcessInJob`, job accounting |
| `src/bin/supervisor_poc.rs` | Scenario runner: `supervisor-poc all` |
| `fixtures/child_fixture.py` | Stand-in for the backend: writes stdout/stderr, optionally spawns a long-lived grandchild (stand-in for `llama-server.exe`), exits on `shutdown`/EOF on stdin |

### Key design points

- **Race-free job assignment.** The child is created with `CREATE_SUSPENDED | CREATE_NO_WINDOW`, assigned to the job, then its thread is resumed. It cannot spawn a grandchild before it is inside the job. Every descendant then inherits the job automatically, and breakaway is not permitted.
- **No console windows.** `CREATE_NO_WINDOW` on the child. Grandchildren inherit the hidden console, so `nvidia-smi` / `llama-server` spawned by Python will not flash windows.
- **Minimal environment.** `env_clear()`, then an allow-list (`SystemRoot`, `TEMP`, `LOCALAPPDATA`, …). `PATH` is reduced to `%SystemRoot%\System32`. `SystemRoot` is mandatory for Python (sockets, `os.urandom`).
- **Secrets via environment, never argv** (argv is readable by any local process). Configured secret strings are replaced with `[REDACTED]` in captured output.
- **Shutdown contract:** graceful signal (stdin line, stdin close, or a custom closure such as an HTTP `POST /api/system/shutdown`) → wait up to a timeout → `TerminateJobObject` (whole tree).

## 2. Results (`cargo run --release --bin supervisor-poc -- all`)

| # | Scenario | Result | Evidence |
|---|---|---|---|
| 1 | stdout/stderr capture | PASS | 25/25 stdout + 25/25 stderr lines, 54 events |
| 2 | Secret redaction in captured logs | PASS | secret not present; `[REDACTED]` present |
| 3 | Graceful exit | PASS | `Graceful` |
| 4 | Unexpected termination detected | PASS | exit code 3, `expected=false`, exit event fired ~1.16 s after the simulated crash |
| 5 | Grandchild inherits the Job Object | PASS | `IsProcessInJob(grandchild) = true`; 5 active processes in job (venv launchers + interpreters) |
| 6 | Graceful shutdown of the whole tree | PASS | 0.36 s; grandchild gone |
| 7 | Forced termination when the child ignores shutdown | PASS | `Forced` after the 2 s grace period; child and grandchild gone |
| 8 | **Parent hard-killed (`TerminateProcess`) → whole tree cleaned** | PASS | launcher, fixture, grandchild alive before, **all gone** 1.5 s after the parent kill |
| 9 | **Control: same, without a Job Object** | PASS (orphan reproduced) | launcher and fixture died, **grandchild survived as an orphan** |

### In the real Tauri app

The release probe build runs the mock backend through this crate.
- **App hard-killed** (`Stop-Process -Force`, equivalent to Task Manager "End task"): the 3-process Python tree was alive before and **all gone within 2 s**.
- **Normal close** (`MUSTANG_PROBE_AUTOEXIT`): the mock stopped gracefully, and the only `python.exe` processes left were the user's pre-existing backend.
- **Restarts:** six mock launches per probe run (restart same port, new port, kill mid-stream, CORS swap). No leftover processes.

## 3. Findings that matter for production

1. **Without a Job Object, `llama-server` *will* be orphaned on a crash.** Scenario 9 reproduces it exactly: the grandchild (llama-server's stand-in) survives while its parent dies. In production that means 1–8 GB of VRAM and port 18080 stay held until the user kills it manually.
2. **venv `python.exe` is a launcher.** `backend\.venv\Scripts\python.exe` starts the real base interpreter as a *second* process, so PIDs from `Child::id()` are the launcher's, not the interpreter's. The embeddable CPython planned for Phase C has no launcher. Either way, the Job Object makes this irrelevant to cleanup.
3. **stdin EOF is a useful but insufficient parent-death signal.** The fixture exits on stdin EOF when its parent dies, which first masked the orphan problem in testing. uvicorn does not watch stdin, so the Job Object remains the authoritative cleanup mechanism. Stdin EOF may be added as a secondary graceful trigger.
4. **`terminate()` in Python on Windows is a hard kill** (`TerminateProcess`). Graceful backend shutdown must be an explicit request (HTTP endpoint), with job termination as the fallback.

## 4. Integration plan for the real process tree (Phase C/D)

```
Mustang AI.exe (Tauri, Rust)
 └─ JobObject [KILL_ON_JOB_CLOSE, DIE_ON_UNHANDLED_EXCEPTION]
     └─ python.exe (embedded CPython) -m mustang_launcher      ← spawn_in_job (suspended → assign → resume)
         ├─ llama-server.exe (spawned by LlamaServerEngine)    ← inherits job automatically
         └─ nvidia-smi.exe (telemetry, short-lived)            ← inherits job automatically
```

| Concern | Plan |
|---|---|
| Who owns the job | `src-tauri` `backend.rs` (Phase C) promotes `mustang-supervisor` from prototype to a workspace crate; one job per app instance, held for the app's lifetime |
| Graceful shutdown | `GracefulSignal::Custom` → authenticated `POST /api/system/shutdown` (Phase C backend change) → 10 s → `TerminateJobObject` |
| Crash detection and restart | exit monitor event → UI "backend restarting" state → respawn with backoff (max 3 in 60 s) → new port + token re-published to the UI through `backend_info` |
| llama-server only crashing | still handled by the existing Python supervisor (`_health_loop`); no Rust change |
| App crash or Task Manager kill | kernel closes the job handle → whole tree terminated (verified) |
| Logs | captured lines → rotating file in `%LOCALAPPDATA%\MustangAI\logs\`, token and engine API key redacted |
| Python-side hygiene (Phase D) | add `CREATE_NO_WINDOW` to `create_subprocess_exec` / `subprocess.run`; no PATH lookups |

## 5. Limitations

- The fixture is a Python stand-in, not the real backend or llama-server. Real-tree verification (VRAM released after a hard kill) belongs to the Phase D test plan.
- Windows logoff/shutdown (`WM_QUERYENDSESSION`) handling is not prototyped (Phase E).
- Restart-with-backoff policy is not implemented, only the detection it relies on.
