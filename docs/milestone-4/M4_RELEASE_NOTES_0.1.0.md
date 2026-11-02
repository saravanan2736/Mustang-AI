# Mustang AI 0.1.0 — Windows desktop (release candidate)

First native Windows release of Mustang AI: a local-first, private AI chat app that runs open-weight GGUF models on your own NVIDIA GPU. No account, no cloud service, no API key.

## Highlights

- **Native desktop app** (Tauri 2 / WebView2) with the familiar Obsidian Dark interface.
- **Everything bundled:** the Python engine host and llama.cpp b12000 with CUDA 13.4. Nothing else to install besides a current NVIDIA driver. Without a suitable GPU it runs on the CPU and tells you why.
- **Chat with streaming replies,** stop at any time; partial replies are saved.
- **Model management:** local library, Hugging Face search, resumable downloads with SHA-256 verification, context-size recommendations, live GPU telemetry, model switching with automatic rollback.
- **Personas and generation settings,** conversation history, regenerate and edit.
- **System tray:** keep the model loaded in the background; unload or quit from the tray.
- **Safe by default:** the engine only listens on your own machine and is locked with per-launch secret keys; downloaded engine files are verified at every start.
- **Your data stays yours:** conversations and models live outside the program folder. Updates and uninstalls never delete them; automatic backups are made before updates and database upgrades and can be restored from Settings.
- **First-run import:** existing conversations from the development version can be imported (copied, the original is untouched), and an existing models folder is used in place.

## Requirements

- Windows 10 22H2 or Windows 11, 64-bit
- NVIDIA GPU with driver R580 or newer for GPU acceleration (tested: RTX 5050 Laptop, 8 GB, driver 617.42); CPU-only works too
- About 1 GB of disk space for the app, plus your models

## Install

Run `Mustang AI_0.1.0_x64-setup.exe` (no administrator rights needed). This build is **not code-signed**, so Windows SmartScreen may warn; verify the SHA-256 against `SHA256SUMS.txt`.

## Known limitations

- Not code-signed yet.
- Automatic updates are built in but not configured for this build (no update server); install new versions with the installer.
- Each update downloads the full installer (~550 MB).
- Tested on one machine (RTX 5050 Laptop); clean-machine, multi-GPU and older-GPU testing is still pending. See `M4_PHASEG_RELEASE_REPORT.md`.
- Under investigation before final release: in stress tests of the desktop app, some requests stalled for 40-80 s, the engine host's memory rose once to about 2 GB, and quitting afterwards took about 10 s. Short sessions were not affected in testing.

## Fixed since the first release candidate

- Models downloaded from Hugging Face are now listed in the model menu and can be loaded from the Library and the Download Queue (they previously failed with "Model file not found").
- Regenerate and Edit no longer remove the wrong messages when two messages were saved within the same few milliseconds.
- Switching models is slightly faster (about 0.15 s).

See `docs/TROUBLESHOOTING.md` for help.
