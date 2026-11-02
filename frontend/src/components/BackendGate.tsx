import React, { useEffect, useState } from "react";
import { AlertCircle, Cpu, Loader2, RotateCw, X } from "lucide-react";
import {
  BackendStatus,
  backendLogDir,
  getBackendStatus,
  gpuStatus,
  initBackendConnection,
  retryBackend,
  subscribeBackendStatus,
} from "../services/connection";

/**
 * Renders the app once the backend address and session token are known.
 * Start-up shows a splash; a backend restart overlays the (still mounted) app so
 * in-progress UI state survives; a failure shows the reason, log tail and Retry.
 */
export const BackendGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [status, setStatus] = useState<BackendStatus>(getBackendStatus());
  const [hasBeenReady, setHasBeenReady] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [gpuNoticeDismissed, setGpuNoticeDismissed] = useState(false);

  useEffect(() => {
    const unsubscribe = subscribeBackendStatus((s) => {
      setStatus(s);
      if (s.state === "ready") {
        setHasBeenReady(true);
        setRetrying(false);
      }
      if (s.state === "failed") {
        // The app unmounts behind the failure screen; after Retry it must mount only once
        // the new backend is ready, so its initial data loads hit the new address.
        setHasBeenReady(false);
        setRetrying(false);
      }
    });
    initBackendConnection().catch((err) =>
      setStatus({ state: "failed", reason: `Could not reach the desktop shell: ${err}`, log_tail: [] })
    );
    return unsubscribe;
  }, []);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await retryBackend();
    } catch (err) {
      setRetrying(false);
      console.error("Restart request failed:", err);
    }
  };

  if (status.state === "failed") {
    const logDir = backendLogDir();
    return (
      <div className="h-screen w-screen bg-obsidian-950 flex items-center justify-center p-8">
        <div className="max-w-2xl w-full bg-obsidian-900 border border-rose-500/30 rounded-2xl p-6 shadow-glass space-y-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="font-semibold text-rose-200">The local engine could not start</p>
              <p className="text-sm text-rose-300/90 whitespace-pre-line">{status.reason}</p>
            </div>
          </div>
          {status.log_tail.length > 0 && (
            <pre className="max-h-64 overflow-auto bg-obsidian-950 border border-obsidian-700 rounded-xl p-3 text-[11px] leading-relaxed font-mono text-slate-400 whitespace-pre-wrap">
              {status.log_tail.join("\n")}
            </pre>
          )}
          {logDir && <p className="text-xs text-slate-500 font-mono break-all">Logs: {logDir}</p>}
          <button
            onClick={handleRetry}
            disabled={retrying}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-electric hover:bg-indigo-deep disabled:opacity-60 text-white text-sm font-semibold shadow-indigo-glow transition"
          >
            <RotateCw className={`w-4 h-4 ${retrying ? "animate-spin" : ""}`} />
            {retrying ? "Retrying…" : "Retry"}
          </button>
        </div>
      </div>
    );
  }

  if (!hasBeenReady) {
    const detail = status.state === "starting" ? status.detail : "Starting the local engine";
    return (
      <div className="h-screen w-screen bg-obsidian-950 flex flex-col items-center justify-center gap-4 text-slate-300">
        <Loader2 className="w-8 h-8 text-indigo-glow animate-spin" />
        <p className="text-sm">{detail}…</p>
      </div>
    );
  }

  const reconnecting = status.state !== "ready";
  const gpu = gpuStatus();
  const showGpuNotice = !gpuNoticeDismissed && gpu !== null && gpu.mode !== "cuda" && !!gpu.message;
  return (
    <>
      {children}
      {showGpuNotice && (
        <div className="fixed top-20 right-4 z-40 max-w-md bg-obsidian-900 border border-amber-500/30 rounded-xl p-4 shadow-glass flex items-start gap-3 text-xs">
          <Cpu className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-semibold text-amber-200">Running on CPU</p>
            <p className="text-amber-100/80 leading-relaxed">{gpu!.message}</p>
          </div>
          <button
            onClick={() => setGpuNoticeDismissed(true)}
            className="text-amber-400 hover:text-amber-200 p-1 rounded-lg hover:bg-amber-500/20 transition"
            aria-label="Dismiss"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
      {reconnecting && (
        <div className="fixed inset-0 z-50 bg-obsidian-950/70 backdrop-blur-sm flex items-center justify-center">
          <div className="flex items-center gap-3 bg-obsidian-900 border border-obsidian-700 rounded-xl px-5 py-3 text-sm text-slate-200 shadow-glass">
            <Loader2 className="w-4 h-4 text-indigo-glow animate-spin" />
            {status.state === "restarting"
              ? `${status.reason} Restarting (attempt ${status.attempt})…`
              : status.state === "starting"
                ? `${status.detail}…`
                : "Reconnecting to the local engine…"}
          </div>
        </div>
      )}
    </>
  );
};
