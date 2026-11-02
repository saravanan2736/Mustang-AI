# Mustang AI — Troubleshooting (Windows desktop app)

## Where things are

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\Mustang AI\` (installed per user, no admin needed) |
| Conversations, settings, backups | `%LOCALAPPDATA%\MustangAI\data\` (`mustang.db`, `backups\`) |
| Logs | `%LOCALAPPDATA%\MustangAI\logs\` (`desktop.log` = app shell, `backend.log` = local engine) |
| Desktop settings | `%LOCALAPPDATA%\MustangAI\desktop.json` |
| Models | your models folder: Settings → Desktop shows it; default `%LOCALAPPDATA%\MustangAI\models\` |

Uninstalling never deletes the `MustangAI` data folder or your models folder; delete them yourself if you no longer need them.

## The window says "The local engine could not start"

1. Read the reason on the screen and the last lines shown under it. **Open logs** shows `desktop.log` and `backend.log`.
2. Press **Retry**.
3. Common causes:

| Message | Fix |
|---|---|
| "The local inference engine files are damaged or were modified (…)" | An engine file in the program folder was changed or removed, for example by antivirus quarantine. Reinstall Mustang AI; your data is kept. |
| "Your data was saved by a newer version of Mustang AI" / `NewerSchemaError` | You installed an older version over a newer one. Install the newer version again, **or** restore a backup listed on the screen (the current database is kept as a backup first). |
| "The models folder … is not available (drive disconnected?)" | Reconnect the drive that holds your models, or change the folder in Settings → Desktop. |
| "The backend stopped unexpectedly 3 times within 60 s" | Check `backend.log` for the error. Restart the PC if a GPU driver update is pending. |

## "Running on CPU" notice

- **No NVIDIA GPU detected:** models run on the CPU (slower but working).
- **Driver too old:** GPU acceleration needs an NVIDIA driver R580 or newer (CUDA 13). Update the driver from nvidia.com or the NVIDIA app, then restart Mustang AI.

## A model fails to load or stops with "out of memory"

- Lower the **Context Window** or **GPU Offload Layers** in the Model Management panel; the memory estimate shows what fits.
- Close other GPU-heavy apps (games, video editors).

## Windows SmartScreen warns about the installer

Current builds are not code-signed yet. Choose **More info → Run anyway** only if you obtained the installer from the Mustang AI release you trust; compare its SHA-256 with `SHA256SUMS.txt`:

```powershell
Get-FileHash "Mustang AI_0.1.0_x64-setup.exe" -Algorithm SHA256
```

## The app keeps running after I close the window

By design: it stays in the system tray so the model stays loaded. Use the tray icon → **Quit Mustang AI**, or turn off "Keep running in the system tray" in Settings → Desktop. From a terminal: `"%LOCALAPPDATA%\Mustang AI\mustang-desktop.exe" --quit`.

## Downloads

- Downloads pause when you quit or log off and can be resumed from the Download Queue; they continue from where they stopped.
- A download that fails its SHA-256 check is deleted and marked failed; retry it.

## Restoring a backup

Settings → Desktop → Database backups → **Restore**. A backup is made automatically before every update and database upgrade; your current data is kept as a backup before any restore.

## Collecting information for a bug report

Zip `%LOCALAPPDATA%\MustangAI\logs\`. The logs never contain session keys; they may contain model file names and folder paths.
