# Mustang AI — M4 Phase F Test Report

Real runs on 2026-11-02 (Windows 11, i7-13620H, RTX 5050 Laptop). The app's data root was a scratch folder in every run (`MUSTANG_DESKTOP_DATA_ROOT`). The real `models/qwen2.5-0.5b-instruct-q4_k_m.gguf` and `backend/run/mustang.db` were SHA-256-guarded: **unchanged**.

> **Environment note.** The test shell runs inside an MSIX-packaged desktop app. Windows redirected the installers' writes to `%LOCALAPPDATA%` / `%APPDATA%` into `C:\Users\<user>\AppData\Local\Packages\<package>\LocalCache\`; the installed app's real image path was `…\LocalCache\Local\Mustang AI\mustang-desktop.exe`. Install, update, downgrade and uninstall behaviour was exercised for real inside that view; the user's real profile was not modified.

## 1. Summary

| Area | Result |
|---|---|
| Backend suite (dev venv) | **151 passed** (+1 version-consistency test) |
| Rust unit tests | **20 passed** (+5: backup names, backup/restore round trip, no-DB case, corrupt settings, last-error extraction) |
| Installer build | 551 MiB, ~6.5–7.7 min (LZMA) |
| Silent install | pass |
| Installed app: integrity, chat, updater state, notification | pass |
| In-app update 0.1.0 → 0.1.1 | **pass** (signature verified, backup, relaunch, data intact) |
| Tampered update | **rejected** before any change |
| Downgrade 0.1.1 → 0.1.0 over the running app | pass (graceful quit by hook) |
| Newer-schema database after downgrade → restore from failure screen | pass |
| Interactive uninstall with "Delete the application data" | pass; user data sentinels survived |

## 2. Install (0.1.0, `/S`)

| Check | Result |
|---|---|
| Exit code / time | 0 / 67 s |
| Install location | `%LOCALAPPDATA%\Mustang AI` with `mustang-desktop.exe`, `python\`, `backend\`, `llama\`, `runtime-manifest.json`, `uninstall.exe` |
| Uninstall entry (HKCU) | DisplayName "Mustang AI", DisplayVersion 0.1.0, Publisher "Mustang AI", EstimatedSize 845,339 KB |
| Shortcuts | Start menu `Mustang AI\Mustang AI.lnk` (AUMID `ai.mustang.desktop`), desktop `Mustang AI.lnk` |
| App launched by silent install | no |
| Installed app | engine integrity verified from the install folder (31 files, 528 ms); E2E chat OK; version 0.1.0 / release; updater `configured: true`, `auto_check: false` |
| Notification | test toast "shown"; Windows `ToastNotificationManager.History` for `ai.mustang.desktop`: 1 entry |

## 3. Update via the app UI (0.1.0 → 0.1.1, served from `http://127.0.0.1:18765`, test key)

| Step | Result |
|---|---|
| Check for updates | "Version 0.1.1 is available" with release notes |
| Install | `downloading update 0.1.0 -> 0.1.1` → `update 0.1.1 downloaded and verified (578042537 bytes)` → `backend … stopped: Graceful` → `pre-update backup written: …mustang-pre-update-0.1.0-to-0.1.1-1793625602.db` → `installing update 0.1.1` |
| Installer | passive mode; old process exited; app **relaunched by the installer** (new pid), backend ready ~80 s later |
| After | app version **0.1.1**; uninstall entry 0.1.1; data identical (conversations 1, messages 2, profiles 4, settings 8) |
| Server requests | `latest.json` ×3, installer ×1 |

## 4. Tampered update

Manifest advertised "0.1.2" with the **0.1.0 installer's signature** for a different file.

| Check | Result |
|---|---|
| Offered | yes (manifest is not trusted by itself) |
| Install | refused: "Update download or signature verification failed: The signature verification failed" |
| Side effects | none: same process, still 0.1.1, backend stayed ready, **no backend stop, no backup, no installer run** (verification happens before anything is changed) |

## 5. Downgrade and newer data

| Step | Result |
|---|---|
| 0.1.0 installer `/S` over the **running** 0.1.1 | exit 0 in 50 s; hook: `action: quit` → `stopped: Graceful` before files were replaced; version 0.1.0 |
| Database marked schema v2 (simulating data from a newer release) | 0.1.0 refuses to start; failure screen shows "Your data was saved by a newer version of Mustang AI…" and both backups (screenshot) |
| Restore "Before update" backup from the failure screen | app usable again; database schema v1 with all data; the v2 database kept as `mustang-before-restore-1793625830.db` |

## 6. Uninstall (interactive, "Delete the application data" ticked)

Sentinels created first: `%LOCALAPPDATA%\MustangAI\data\sentinel-conversations.txt`, `%LOCALAPPDATA%\MustangAI\models\sentinel-model.gguf`, and a user file `my-notes.txt` in the install folder. The NSIS controls were operated with targeted `BM_CLICK` messages to their window handles (no keyboard input).

| Check | Result |
|---|---|
| Checkbox | checked |
| Post-uninstall message | "Mustang AI was removed. / Your conversations, settings and logs were kept in: / C:\Users\<user>\AppData\Local\MustangAI / Your model files were not touched…" |
| Uninstall entry, Start menu folder, desktop shortcut | removed |
| Install folder | only the user's `my-notes.txt` remained (no recursive delete of the install folder) |
| `%LOCALAPPDATA%\ai.mustang.desktop`, `%APPDATA%\ai.mustang.desktop` | removed (the only "app data" targets, confirmed in the generated `installer.nsi`) |
| **User data sentinels** | **both survived** |
| Scratch data root | intact |
| Cleanup | sentinels, the test data root folder and the leftover user file removed afterwards |

## 7. Not tested

- Authenticode signing (no certificate available; `sign.ps1` is untested).
- A production https update endpoint (a local http endpoint was used with a test-only flag and a test key).
- The interactive installer's downgrade prompt.
- Installation outside the MSIX-virtualized shell, and on a clean VM.
