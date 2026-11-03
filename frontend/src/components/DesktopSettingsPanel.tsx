import React, { useEffect, useState } from "react";
import { ArchiveRestore, FolderOpen, FolderSearch, HardDrive, MonitorDown, RefreshCw, RotateCcw, ScrollText } from "lucide-react";
import { BackupList, UpdatesSection } from "./BackupsAndUpdates";
import {
  DesktopSettings,
  chooseModelsFolder,
  getDesktopSettings,
  openFolder,
  resetModelsFolder,
  setCloseToTray,
} from "../services/desktop";
import { getBackendStatus, subscribeBackendStatus } from "../services/connection";

/** Reload the UI once the restarted backend is ready, so model lists reflect the new folder. */
function reloadWhenBackendReady() {
  let sawRestart = getBackendStatus().state !== "ready";
  const unsubscribe = subscribeBackendStatus((s) => {
    if (s.state !== "ready") sawRestart = true;
    else if (sawRestart) {
      unsubscribe();
      window.location.reload();
    }
  });
}

/** Settings → Desktop: models folder and window behaviour. Changes apply immediately. */
export const DesktopSettingsPanel: React.FC = () => {
  const [settings, setSettings] = useState<DesktopSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const refresh = () =>
    getDesktopSettings()
      .then(setSettings)
      .catch((err) => setMessage({ kind: "error", text: String(err) }));

  useEffect(() => {
    refresh();
  }, []);

  const run = async (action: () => Promise<string | null | void>, success: (r: string) => string) => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await action();
      if (typeof result === "string") setMessage({ kind: "ok", text: success(result) });
      await refresh();
      return result;
    } catch (err) {
      setMessage({ kind: "error", text: String(err) });
    } finally {
      setBusy(false);
    }
  };

  if (!settings) {
    return <p className="text-xs text-muted">Loading desktop settings…</p>;
  }

  return (
    <div className="space-y-5">
      <div>
        <label className="text-xs font-medium text-fg flex items-center gap-1.5 mb-1.5">
          <HardDrive className="w-3.5 h-3.5 text-link" />
          Models folder
        </label>
        <div className="px-3 py-2 rounded-md bg-panel border border-line text-xs font-mono text-fg break-all">
          {settings.models_dir ?? "—"}
          {settings.models_dir_is_default && <span className="ml-2 text-subtle font-sans">(default)</span>}
        </div>
        <div className="flex flex-wrap gap-2 mt-2">
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              const changed = await run(chooseModelsFolder, (p) => `Models folder changed to ${p}. Restarting the engine…`);
              if (typeof changed === "string") reloadWhenBackendReady();
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent hover:bg-accent-hover disabled:opacity-60 text-fg text-xs font-medium transition"
          >
            <FolderSearch className="w-3.5 h-3.5" />
            Change…
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => openFolder("models"), () => "")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel border border-line hover:border-line text-fg text-xs transition"
          >
            <FolderOpen className="w-3.5 h-3.5" />
            Open
          </button>
          {!settings.models_dir_is_default && (
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                const changed = await run(resetModelsFolder, (p) => `Models folder reset to ${p}. Restarting the engine…`);
                if (typeof changed === "string") reloadWhenBackendReady();
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel border border-line hover:border-line text-fg text-xs transition"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Use default
            </button>
          )}
        </div>
        <p className="text-xs text-muted mt-2">
          Existing files are never moved. Changing the folder restarts the local engine; a loaded model is unloaded.
        </p>
      </div>

      <div className="flex items-start justify-between gap-4 p-4 rounded-md bg-panel border border-line">
        <div className="space-y-1">
          <p className="text-xs font-medium text-fg flex items-center gap-1.5">
            <MonitorDown className="w-3.5 h-3.5 text-link" />
            Keep running in the system tray when the window is closed
          </p>
          <p className="text-xs text-muted">
            Keeps your model loaded for instant replies. Quit from the tray icon to stop the engine and free GPU memory.
          </p>
        </div>
        <input
          type="checkbox"
          aria-label="Keep running in the system tray"
          className="mt-1 h-4 w-4 accent-[rgb(var(--accent))]"
          checked={settings.close_to_tray}
          disabled={busy}
          onChange={(e) => run(() => setCloseToTray(e.target.checked), () => "")}
        />
      </div>

      <div className="flex items-center justify-between text-xs text-muted">
        <span className="font-mono break-all">{settings.log_dir}</span>
        <button
          type="button"
          onClick={() => run(() => openFolder("logs"), () => "")}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel border border-line hover:border-line text-fg text-xs transition shrink-0 ml-3"
        >
          <ScrollText className="w-3.5 h-3.5" />
          Open logs
        </button>
      </div>

      <div>
        <p className="text-xs font-medium text-fg flex items-center gap-1.5 mb-1.5">
          <RefreshCw className="w-3.5 h-3.5 text-link" />
          Updates
        </p>
        <UpdatesSection />
      </div>

      <div>
        <p className="text-xs font-medium text-fg flex items-center gap-1.5 mb-1.5">
          <ArchiveRestore className="w-3.5 h-3.5 text-link" />
          Database backups
        </p>
        <BackupList />
      </div>

      {message && message.text && (
        <p className={`text-xs ${message.kind === "ok" ? "text-success" : "text-danger"}`}>{message.text}</p>
      )}
    </div>
  );
};
