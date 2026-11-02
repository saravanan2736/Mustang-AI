# Mustang AI — M4 Phase E Implementation Report

**Phase E: native Windows integration. Status: complete; awaiting approval for Phase F.**
Branch `m4-phase-e` (from `m4-phase-d`). Date: 2026-11-02.

---

## 1. Outcome

The app now behaves like a native Windows app:

| Capability | Behaviour |
|---|---|
| **System tray** | Icon with menu: *Show Mustang AI*, a live status line (e.g. "Ready: qwen2.5…", "Generating: …", "No model loaded", "· 1 download"), *Unload model* (enabled only when a model is loaded) and *Quit Mustang AI*. Left-click shows the window. The tooltip carries the same status. |
| **Close to tray** | Closing the window hides it; the backend and the loaded model keep running for instant replies. A one-time dialog explains where the app went. Configurable in Settings → Desktop (default on). When the engine has failed, closing quits instead. |
| **Single instance and command line** | A second launch shows the existing window. `mustang-desktop.exe --unload-model` and `--quit` act on the running instance; `--quit` with nothing running exits immediately. |
| **Graceful quit** | Tray *Quit*, `--quit` or closing with tray mode off: the backend **cancels an in-flight generation first** (the partial reply is saved), pauses downloads, unloads the model and exits; no processes remain. |
| **Windows logoff / shutdown / restart** | The main window handles `WM_QUERYENDSESSION` (never vetoes) and `WM_ENDSESSION`: it registers a shutdown-block reason ("Mustang AI is saving your conversations and stopping the local engine…"), saves the window state, stops the backend gracefully (8 s budget) and exits. |
| **Window state** | Size, position and maximized state persist across launches. Never "hidden", so quitting from the tray doesn't make the next launch invisible. |
| **Keep-awake** | While a model is generating or a download is running, the app asks Windows not to sleep (`SetThreadExecutionState`, system only, not the display) and releases the request afterwards. |
| **Models folder** | Settings → Desktop → *Change…* opens the native folder picker. Rust validates the choice (must exist, be local and writable, and not be inside Windows, Program Files, ProgramData, the install folder or the app's own data/log folders), saves it, restarts the engine and reloads the UI. *Use default* and *Open* buttons. Files are never moved. |
| **External links** | Links that leave the app (`target="_blank"` or another origin) open in the default browser only if they are `https` to `huggingface.co` or a subdomain (no credentials or custom port). Everything else is refused and logged. In-app navigation away from the app is blocked by a navigation guard; `window.open` popups return `null`. |
| **Folders** | *Open* (models) and *Open logs* open the app's own folders in Explorer. |

## 2. Design notes

- **No new webview privileges.** Six app commands were added, each Rust-validated: `desktop_settings`, `set_close_to_tray`, `choose_models_folder`, `reset_models_folder`, `open_folder`, `open_external`.
  - The window-state, opener and dialog plugins are used from Rust only; no `fs`, `shell`, `dialog`, `opener` or `http` JS permission is granted.
  - `open_folder` accepts only `"models"` or `"logs"`. `open_external` re-checks the allow-list in Rust.
- **Session end runs inside the window procedure.** `RunEvent::Exit` does not run when Windows ends the session, so `session.rs` subclasses the main window (`SetWindowSubclass`). Graceful shutdown happens inside `WM_ENDSESSION`, wrapped in `ShutdownBlockReasonCreate/Destroy`.
- **Shutdown cancels generation first** (backend `POST /api/system/shutdown`). Found while testing:
  - Before the fix, uvicorn's graceful shutdown waited for an in-flight streaming reply to *finish*.
  - With a long reply on a slower model, that would exceed the shell's 10 s grace period. The Job Object would then force-kill the backend, **losing the partial reply**.
  - Now `cancel_generation()` runs before the exit is triggered. The partial reply is saved immediately and exit time dropped from ~3.4 s to ~2.2 s in tests.
- **GPU detection runs in parallel with start-up** (Phase D carry-over). The first `nvidia-smi` call no longer delays readiness; the UI re-reads the GPU status when it arrives.
- **Tray monitor thread.** Polls the backend every 2 s for engine state and downloads to update the menu and tooltip. Keep-awake is set from the same long-lived thread, as `SetThreadExecutionState` requires.

## 3. Changes

### Desktop shell (`src-tauri`)
| File | Change |
|---|---|
| `src/tray.rs` (new) | Tray icon and menu, `perform()` actions (show/unload/quit), `action_for_args()` for second launches, monitor thread (status, unload enablement, tooltip, keep-awake) |
| `src/session.rs` (new) | `WM_QUERYENDSESSION` / `WM_ENDSESSION` window subclass with shutdown-block reason |
| `src/desktop.rs` (new) | Desktop settings commands, `validate_models_folder()`, `is_allowed_external()`, `is_app_url()`, `open_folder`, `open_external`, one-time tray hint, `close_to_tray_enabled()` |
| `src/lib.rs` | Plugins (window-state without VISIBLE, opener, navigation guard), single-instance args → actions, `--quit` without an instance, tray creation, session handler installation, close-to-tray on `CloseRequested`, new commands |
| `src/backend.rs` | GPU detection in a parallel thread (status re-announced), `shutdown_with(grace)`, `api()`, `data_dirs()`, `models_dir()`, `set_models_dir()` (persist, restart), `http_json()` helper |
| `src/paths.rs` | `DesktopSettings.close_to_tray` (default true) |
| `Cargo.toml` | `tauri` feature `tray-icon`; `tauri-plugin-window-state`, `tauri-plugin-opener`; `windows-sys` (Shell, WindowsAndMessaging, Power, Shutdown) |
| `build.rs`, `capabilities/main.json` | Six new command permissions (and nothing else) |

### Backend
| File | Change |
|---|---|
| `app/api/system.py` | Shutdown cancels any in-flight generation before triggering exit |
| `tests/test_desktop_integration.py` | `test_shutdown_cancels_in_flight_generation_first` |

### Frontend
| File | Change |
|---|---|
| `src/services/desktop.ts` (new) | Typed wrappers for the desktop commands; `installExternalLinkHandler()` (desktop only) |
| `src/components/DesktopSettingsPanel.tsx` (new) | Models folder (Change…, Open, Use default), close-to-tray toggle, logs folder; reloads the UI once the restarted engine is ready |
| `src/components/SettingsModal.tsx` | "Desktop" tab (desktop app only); generation-settings footer hidden on that tab |
| `src/main.tsx` | Installs the external-link handler |

### Test tooling
`scripts/e2e/cdp_eval.mjs` (new): evaluate one expression in the running app's WebView2 for scripted desktop scenarios.

### Test hooks (environment variables, set only by the test harness)
`MUSTANG_DESKTOP_DRY_RUN_OPEN=1` logs instead of launching the browser. `MUSTANG_DESKTOP_TEST_PICK_FOLDER=<path>` supplies the folder-picker answer. These follow Phase C/D's existing hooks (`…_LEGACY_IMPORT`, `…_MIN_DRIVER`, `…_DATA_ROOT`).

## 4. Incident during testing: a test wrote into the user's Documents folder

| | |
|---|---|
| What | While automating the native folder picker, the script failed to type the path (the dialog's Folder field and buttons are not exposed through the UI Automation view used), and the dialog was accepted with its default location, `C:\Users\<user>\Documents`. That test run used Documents as its models folder, so the backend created an **empty `.downloads` folder there** (17:55:31). The folder-validation probe file was created and deleted as designed. |
| Impact | One empty folder. No files read beyond a `.gguf` scan, nothing modified or deleted, and only the scratch data root's config changed. |
| Cleanup | The empty folder was removed after checking that it was empty and created during this run. Documents was verified clean after every later run. |
| Change to approach | Native-dialog automation was abandoned: keyboard injection could reach whichever window the user has focused. The folder flow is now tested through a hook that supplies the dialog's answer. The dialog itself was only opened and then cancelled. |

## 5. Known limitations

| Item | Phase |
|---|---|
| Toast notifications (e.g. "download finished") are not implemented: Windows toasts need an AppUserModelID registered by an installer shortcut | F (with installer) |
| A corrupted `desktop.json` silently falls back to defaults (models folder reverts to the default location; files untouched) | F (validate and back up on load) |
| Desktop app and the browser dev backend can still run concurrently on the same models folder; concurrent downloads of the same file are not coordinated | G (document) / later |
| The native folder picker was verified to open (title, controls present) but not driven end to end by automation (see §4) | manual check in G |
| A real logoff, shutdown or sleep was not performed on the user's session; session end was simulated with real `WM_QUERYENDSESSION`/`WM_ENDSESSION` messages, and keep-awake was verified via the shell's log, because `powercfg /requests` needs elevation | G (clean VM) |
| The CPU-mode notice overlaps the context selector until dismissed (Phase D) | polish |
| The tray menu itself (OS popup) was not clicked by automation; its actions share `perform()` with the tested command-line path | G (manual) |
