import React, { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { ArchiveRestore, Download, RefreshCw } from "lucide-react";
import {
  BackupEntry,
  UpdateInfo,
  UpdateSettings,
  checkForUpdate,
  getUpdateSettings,
  installUpdate,
  listBackups,
  restoreBackup,
  setAutoCheckUpdates,
} from "../services/desktop";

const KIND_LABEL: Record<BackupEntry["kind"], string> = {
  "pre-update": "Before update",
  "before-restore": "Before a restore",
  "schema-migration": "Before database upgrade",
  other: "Backup",
};

const formatTime = (secs: number) => new Date(secs * 1000).toLocaleString();
const formatSize = (bytes: number) => `${(bytes / 1024).toFixed(0)} KB`;

/** Database backups with restore. Used in Settings → Desktop and on the failure screen. */
export const BackupList: React.FC<{ compact?: boolean }> = ({ compact }) => {
  const [backups, setBackups] = useState<BackupEntry[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = () => listBackups().then(setBackups).catch((e) => setMessage(String(e)));
  useEffect(() => {
    refresh();
  }, []);

  const restore = async (b: BackupEntry) => {
    const ok = window.confirm(
      `Restore the database from ${formatTime(b.modified)}?\n\nYour current conversations and settings are kept as a separate backup first, so this can be undone.`
    );
    if (!ok) return;
    setBusy(b.name);
    setMessage(null);
    try {
      await restoreBackup(b.name);
      setMessage("Restored. The engine is restarting with the restored data…");
      setTimeout(() => window.location.reload(), 1500);
    } catch (e) {
      setMessage(String(e));
      setBusy(null);
    }
  };

  if (backups === null) return <p className="text-[11px] text-slate-400">Loading backups…</p>;
  if (backups.length === 0) return <p className="text-[11px] text-slate-400">No backups yet. One is made automatically before every update and database upgrade.</p>;

  return (
    <div className="space-y-2">
      <ul className="space-y-1.5">
        {backups.slice(0, compact ? 5 : 15).map((b) => (
          <li key={b.name} className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-[#0D1220] border border-white/[0.08] text-[11px]">
            <div className="min-w-0">
              <p className="text-slate-200">{KIND_LABEL[b.kind]} · {formatTime(b.modified)}</p>
              <p className="text-slate-500 font-mono truncate">{b.name} · {formatSize(b.size)}</p>
            </div>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => restore(b)}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#141A29] border border-white/10 hover:border-white/20 text-slate-200 disabled:opacity-50 shrink-0 transition"
            >
              <ArchiveRestore className="w-3.5 h-3.5" />
              {busy === b.name ? "Restoring…" : "Restore"}
            </button>
          </li>
        ))}
      </ul>
      {message && <p className="text-xs text-amber-300">{message}</p>}
    </div>
  );
};

/** Opt-in updates. Hidden behind "not configured" in builds without an update endpoint. */
export const UpdatesSection: React.FC = () => {
  const [settings, setSettings] = useState<UpdateSettings | null>(null);
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [state, setState] = useState<"idle" | "checking" | "installing">("idle");
  const [progress, setProgress] = useState<{ downloaded: number; total: number | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getUpdateSettings().then(setSettings).catch((e) => setError(String(e)));
    const unlisten = listen<{ downloaded: number; total: number | null }>("update://progress", (e) => setProgress(e.payload));
    return () => {
      unlisten.then((u) => u());
    };
  }, []);

  if (!settings) return null;
  if (!settings.configured) {
    return <p className="text-[11px] text-slate-400">Version {settings.current_version}. Automatic updates are not configured for this build; install new versions with the installer.</p>;
  }

  const check = async () => {
    setState("checking");
    setError(null);
    try {
      setInfo(await checkForUpdate());
    } catch (e) {
      setError(String(e));
    } finally {
      setState("idle");
    }
  };

  const install = async () => {
    setState("installing");
    setError(null);
    try {
      await installUpdate(); // the app closes and the installer relaunches it
    } catch (e) {
      setError(String(e));
      setState("idle");
    }
  };

  const pct = progress?.total ? Math.round((progress.downloaded / progress.total) * 100) : null;

  return (
    <div className="space-y-2.5 text-[11px]">
      <div className="flex items-center justify-between gap-3">
        <span className="text-slate-300">Installed version {settings.current_version}</span>
        <button
          type="button"
          disabled={state !== "idle"}
          onClick={check}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#0D1220] border border-white/10 hover:border-white/20 text-slate-200 text-xs disabled:opacity-50 transition"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${state === "checking" ? "animate-spin" : ""}`} />
          Check for updates
        </button>
      </div>
      {info && !info.available && <p className="text-emerald-400">Mustang AI is up to date.</p>}
      {info?.available && (
        <div className="p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/30 space-y-2">
          <p className="text-indigo-200 font-medium">Version {info.version} is available.</p>
          {info.notes && <p className="text-slate-300 whitespace-pre-line">{info.notes}</p>}
          <p className="text-slate-400">Your conversations are backed up first. Mustang AI closes, updates and reopens.</p>
          <button
            type="button"
            disabled={state !== "idle"}
            onClick={install}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium disabled:opacity-60 transition"
          >
            <Download className="w-3.5 h-3.5" />
            {state === "installing" ? (pct !== null ? `Downloading… ${pct}%` : "Installing…") : `Install ${info.version}`}
          </button>
        </div>
      )}
      <label className="flex items-center gap-2 text-slate-300">
        <input
          type="checkbox"
          className="h-3.5 w-3.5 accent-indigo-500"
          checked={settings.auto_check}
          onChange={async (e) => {
            const enabled = e.target.checked;
            await setAutoCheckUpdates(enabled).catch((err) => setError(String(err)));
            setSettings({ ...settings, auto_check: enabled });
          }}
        />
        Check for updates automatically when Mustang AI starts
      </label>
      {error && <p className="text-rose-400">{error}</p>}
    </div>
  );
};
