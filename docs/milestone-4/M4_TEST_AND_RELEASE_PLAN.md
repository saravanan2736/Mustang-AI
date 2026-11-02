# Mustang AI — Milestone 4 Test and Release Plan

Phase A deliverable.

---

## 1. Test environments

| Env | Description | Purpose |
|---|---|---|
| E1 | Dev machine: i7-13620H, RTX 5050 8 GB (driver 617.42), 24 GB RAM, Windows 11 | Primary functional + GPU performance |
| E2 | Clean Windows 11 VM: no Python/Node/VS, no NVIDIA driver | Installer, CPU fallback, prerequisites |
| E3 | Windows 11 with older NVIDIA driver (< CUDA 13 requirement) if available | Driver-check messaging |
| E4 | Standard (non-admin) Windows account | Per-user install, permissions |
| E5 | Windows 10 22H2 (optional, best effort) | WebView2 Evergreen compatibility |

## 2. Automated tests

| Suite | Existing / new | Runs in |
|---|---|---|
| Backend pytest (120 tests, MockEngine) | existing; isolated to temp dirs in Phase C | every change |
| Backend pytest `-m gpu` (live inference, switching, context, lifecycle) | existing | Phase D+, E1 |
| Route-auth coverage: every route except `/health` returns 401 without token | new (C) | every change |
| Desktop-mode config: env paths honoured, bootstrap disabled, origins | new (C) | every change |
| Engine API key: direct engine call without key gets 401 | new (D) | E1 |
| Embedded-Python run of the full suite | new (C) | release builds |
| Frontend `tsc` + `vite build` | existing | every change |
| Rust unit tests: port picker, env allowlist, path validation, manifest check | new (B–D) | every change |

## 3. Manual / scripted desktop scenarios

**Lifecycle**
1. Cold start to usable UI in ≤ 5 s (excluding model load) on E1.
2. Quit from tray during generation → partial message saved; no `python.exe`/`llama-server.exe` left (`tasklist`).
3. Kill `Mustang AI.exe` in Task Manager → all children gone within 2 s; VRAM released (`nvidia-smi`).
4. Kill `python.exe` → the supervisor restarts the backend, the UI reconnects, and after 3 failures within 60 s an error screen offers logs.
5. Kill `llama-server.exe` → engine state `ERROR` shown; reload works.
6. Port 18000 and 18080 pre-occupied → app starts normally.
7. Second launch → focuses the existing window.
8. Windows logoff during download → download `PAUSED` on next launch and resumes with a Range request.

**Feature parity** (every row of the matrix in `M4_MIGRATION_PLAN.md`)
- Chat streaming, stop, regenerate, edit/truncate, conversation create/rename/delete/search.
- Model library scan, HF search, repo detail, download, pause, resume, cancel, retry, SHA-256 verified status.
- Load, switch (including the failed-switch rollback), unload; context recommendation; telemetry updates.
- Settings and personas persist across restart.

**Performance**
- Tokens/s on the reference model (`qwen2.5-0.5b-instruct-q4_k_m.gguf`) and one 7–8B Q4 model within 5 % of `run/stage6_benchmark_results.json` / `stage5_benchmark_results.json`.
- Idle memory of shell + backend ≤ 300 MB (excluding model).
- No visible stutter in streams during telemetry polling.

**Security**
- No console windows ever flash.
- `netstat -ano`: all listeners on `127.0.0.1` only.
- Requests with a forged Host header get 403; requests without a token get 401; the bootstrap endpoint is unavailable in desktop mode.
- CSP active (attempted inline script in markdown is not executed; raw HTML not rendered).
- External HF link opens in the default browser, not in the app.
- Token absent from logs and disk (`findstr` across the data dir).

**Installer / data safety**
1. Fresh install on E2/E4 without admin.
2. Upgrade N-1 → N: conversations, settings, profiles, models and in-progress downloads intact; backup created.
3. Uninstall (box unchecked and checked): `%LOCALAPPDATA%\MustangAI\` and a custom models folder still fully present.
4. Reinstall picks up the existing data automatically.
5. Downgrade N → N-1: newer-schema notice, no corruption.
6. Legacy import from `S:\Mustang AI\backend\run\mustang.db`: counts of conversations/messages/models match the source; source unchanged (hash before and after).
7. Tampered update manifest or installer → rejected.
8. Defender full scan of the install dir → no detections (signed build).

**Soak**
- 4 h scripted loop: load → 50 chats → switch model → download/cancel. No handle/memory growth > 10 %, no orphan processes, no DB errors.

## 4. Release gates

| Gate | Criterion |
|---|---|
| G1 Functional | All feature-parity scenarios pass on E1 |
| G2 Automated | 100 % of pytest (incl. `-m gpu` on E1), tsc, and Rust tests green |
| G3 Data safety | Installer/data-safety scenarios 1–6 pass; zero data-loss findings |
| G4 Security | All security checks pass; no `shell`/`fs` permissions in capabilities |
| G5 Performance | Within 5 % of the Stage 5/6 baselines |
| G6 Clean machine | E2 install + CPU fallback chat works; E3 shows the driver message |
| G7 Signing | Installer and executables Authenticode-signed; updater signature verified |

## 5. Release process

1. Bump the version in a single source (propagated to `tauri.conf.json`, `package.json`, backend settings).
2. `scripts/build_release.ps1`: frontend build → embedded Python + vendored deps (hash-locked) → pruned runtime + manifest → `tauri build` → sign → generate updater signature + `latest.json`.
3. Record SHA-256 for every release artifact in release notes.
4. Run gates G1–G7 on the exact signed artifact (not a dev build).
5. Publish the installer; publish the updater manifest last (users who opted in see the update only after the artifacts are live).
6. Keep the N-1 installer available for rollback.
