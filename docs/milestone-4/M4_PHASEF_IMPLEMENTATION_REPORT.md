# Mustang AI — M4 Phase F Implementation Report

**Phase F: installer and update mechanism. Status: complete; awaiting approval for Phase G.**
Branch `m4-phase-f` (from `m4-phase-e`). Date: 2026-11-02.

---

## 1. Outcome

| Capability | Result |
|---|---|
| **Windows installer** | NSIS, per-user (no admin), `Mustang AI_<version>_x64-setup.exe`, **551 MiB** (LZMA). Installs to `%LOCALAPPDATA%\Mustang AI\`, adds Start-menu and desktop shortcuts and an Apps & features entry (publisher, version, ~845 MB). WebView2 is installed by bootstrapper only if missing. |
| **Upgrade and downgrade** | Running a newer or older installer over an installation works. A **pre-install hook asks the running app to quit gracefully first** (`--quit`: partial reply saved, downloads paused, model unloaded), with Tauri's own running-app check as a fallback. |
| **Opt-in signed updates** | Settings → Desktop → *Check for updates* / *Install*; automatic check at start-up is **off by default**. Updates are verified against a minisign public key embedded at build time. Before installing, the app stops the backend gracefully and writes a **pre-update database backup**; the installer runs in passive mode and relaunches the new version. Builds without an update endpoint report "not configured" and contact nothing. |
| **Database backups and restore** | Settings → Desktop lists backups (pre-update, pre-migration, before-restore) with *Restore*. The current database is always kept as a backup first, so a restore can be undone. The **failure screen** offers the same list; when a newer database blocks start-up (downgrade), it explains this and shows the backups expanded. |
| **Safe uninstall** | Removes only files it installed, the shortcuts and the registry entry. A file the user put in the install folder survives. "Delete the application data" removes only `%APPDATA%\ai.mustang.desktop` and `%LOCALAPPDATA%\ai.mustang.desktop` (WebView2 cache, window state), **never `%LOCALAPPDATA%\MustangAI` or the models folder**. A closing message tells the user where their data was kept. |
| **Notifications** | Windows toasts for "Download complete" / "Download failed". They work once installed: the Start-menu shortcut carries AUMID `ai.mustang.desktop`, and a test toast appeared in that AUMID's notification history. |
| **Version single source** | `npm run version:set -- X.Y.Z` (`scripts/set_version.py`) updates all six version fields; `tests/test_version_consistency.py` fails on drift and runs at the start of every release build. |
| **Release pipeline** | `scripts/build_release.py` → `build/release/<version>/`: installer, `SHA256SUMS.txt`, and (when the updater is configured) `.sig` and `latest.json`. Authenticode signing is wired via `scripts/sign.ps1` (Windows SDK `signtool`, certificate thumbprint) and runs only when `MUSTANG_SIGN_CERT_THUMBPRINT` is set. |
| **Settings robustness** | A corrupt `desktop.json` is moved aside as `desktop.json.corrupt-<time>` (never silently overwritten) and defaults are used; this is logged. |

## 2. Findings during the phase

| Finding | Handling |
|---|---|
| **The Tauri CLI could not download its NSIS toolchain** (`timeout: global`), although `curl` fetched the same file in 0.8 s | Read the pinned URLs and SHA-1 hashes from the CLI binary, downloaded `nsis-3.13.zip` and `nsis_tauri_utils.dll` v0.5.3 with `curl`, verified both SHA-1 values (`DB14F8AF…`, `75197FEE…`) and placed them exactly where the CLI expects (`%LOCALAPPDATA%\tauri\NSIS`). Documented for CI. |
| **My shell runs inside an MSIX-packaged desktop app.** Windows redirects its writes to `AppData` into `…\AppData\Local\Packages\<package>\LocalCache\` | The test installs, the NSIS cache and earlier phases' WebView2 data landed in that virtualized location; **the user's real profile was not modified**. Installer, updater and uninstaller mechanics were exercised for real; real-profile paths and visibility in the real Start menu and Apps list must be confirmed outside this sandbox (Phase G). |
| Tauri installs per-user to `%LOCALAPPDATA%\<productName>` (not `%LOCALAPPDATA%\Programs\…` as Phase A assumed) | Accepted. The install folder (`Mustang AI`) and the data root (`MustangAI`) are separate folders, and the uninstaller only touches the former, verified with sentinels. |
| One bundling attempt failed with "file in use (os error 32)" while a test run and the build ran concurrently | Re-run succeeded; release builds should run alone (Phase G checklist). |
| The failure screen's headline said only "exit code 3" for a newer database | The shell now appends the backend's last exception line (e.g. `NewerSchemaError: Database … uses schema v2 …`), unit-tested. |

## 3. Changes

### Desktop shell
| File | Change |
|---|---|
| `tauri.conf.json` | Bundle active: NSIS, `currentUser`, LZMA, publisher/description/copyright, Start-menu folder, installer hooks, WebView2 download bootstrapper (silent) |
| `windows/installer-hooks.nsh` (new) | Graceful `--quit` of a running app before install/uninstall; post-uninstall message (interactive only) |
| `src/updater.rs` (new) | `update_settings`, `set_auto_check_updates`, `check_for_update`, `install_update` (download + signature verification → graceful backend stop → pre-update backup → install; backend restarted if any step fails), opt-in auto-check |
| `src/backups.rs` (new) | `list_backups`, `restore_backup` (strict name validation, current DB kept first), `backup_before_update`, retention of 5 per kind; 3 unit tests |
| `src/backend.rs` | `stop_for_maintenance()`; start-up failure reason includes the last exception line (`last_error_line`, unit-tested) |
| `src/paths.rs` | `auto_check_updates` setting; corrupt `desktop.json` set aside (unit-tested) |
| `src/tray.rs` | Download complete/failed notifications; auto update check once the backend is first ready; `MUSTANG_DESKTOP_TEST_NOTIFY` hook |
| `src/lib.rs` | Updater plugin registered **only** when the bundled config contains an updater section; notification plugin; new commands |
| `Cargo.toml`, `build.rs`, `capabilities/main.json` | `tauri-plugin-updater`, `tauri-plugin-notification` (Rust-side only); 6 new command permissions |

### Frontend
| File | Change |
|---|---|
| `src/components/BackupsAndUpdates.tsx` (new) | `UpdatesSection` (check, install with download progress, auto-check toggle, "not configured" state) and `BackupList` (restore with confirmation) |
| `src/components/DesktopSettingsPanel.tsx` | Updates and Database backups sections |
| `src/components/BackendGate.tsx` | Failure screen: newer-data explanation and backup restore |
| `src/services/desktop.ts` | Backup and update command wrappers |

### Build and release
| File | Purpose |
|---|---|
| `scripts/build_release.py` (new) | Version check → runtime → generated overlay (resources + optional updater + optional signing) → NSIS → `build/release/<version>/` with `SHA256SUMS.txt` (+ `.sig`, `latest.json`) |
| `scripts/sign.ps1` (new) | Authenticode via `signtool` (SHA-256, RFC 3161 timestamp, verify) |
| `scripts/set_version.py` (new), `backend/tests/test_version_consistency.py` (new) | Version single source and drift test |
| `package.json` | `release:build`, `version:set` |

## 4. How to produce a real release

```bash
npm run version:set -- 0.2.0
```
```bash
npm run release:build
```

- **Without any extra settings:** an unsigned, offline installer with no updater. This is what `build/release/0.1.0/` contains now.
- **Code signing:** install a code-signing certificate (OV or Azure Trusted Signing) and set `MUSTANG_SIGN_CERT_THUMBPRINT`. Not exercised here (no certificate).
- **Updates:**
  1. Generate a production key once with `npx tauri signer generate`. Keep the private key offline and in a CI secret; losing it means users can no longer be updated.
  2. Set `MUSTANG_UPDATE_ENDPOINT` (https), `MUSTANG_UPDATE_PUBKEY`, `MUSTANG_UPDATE_BASE_URL` and `TAURI_SIGNING_PRIVATE_KEY` (+ `_PASSWORD`).
  3. Publish the installer and `latest.json` (e.g. GitHub Releases).
  4. `MUSTANG_UPDATE_ALLOW_HTTP` exists **only** for local tests.

## 5. Known limitations

| Item | Phase |
|---|---|
| No code-signing certificate: installers are unsigned, so SmartScreen warns | user procurement, then a signed build in G |
| Real-profile install, Start-menu and Apps-list visibility not observed (MSIX virtualization of this test shell) | G (outside the sandbox or a clean VM) |
| Every update downloads the full ~551 MiB installer (no delta updates) | accepted for M4 |
| The interactive installer's downgrade prompt was not exercised (silent downgrade was) | G |
| The test updater key pair lives only in the session scratchpad and is not for production | — |
