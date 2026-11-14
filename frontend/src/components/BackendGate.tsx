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
  isDesktop,
} from "../services/connection";
import { BackupList } from "./BackupsAndUpdates";

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
  const [showBackups, setShowBackups] = useState(false);

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
      <div className="h-screen w-screen bg-canvas flex items-center justify-center p-8">
        <div className="max-w-2xl w-full bg-panel border border-danger/40 rounded-lg p-6 space-y-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-danger shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="font-semibold text-danger">The local engine could not start</p>
              <p className="text-sm text-danger/90 whitespace-pre-line">{status.reason}</p>
            </div>
          </div>
          {status.log_tail.length > 0 && (
            <pre className="max-h-64 overflow-auto bg-canvas border border-line rounded-md p-3 text-xs leading-relaxed font-mono text-muted whitespace-pre-wrap">
              {status.log_tail.join("\n")}
            </pre>
          )}
          {logDir && <p className="text-xs text-subtle font-mono break-all">Logs: {logDir}</p>}
          {isDesktop && (() => {
            const newerData = status.log_tail.some((l) => l.includes("newer Mustang AI"));
            return (
              <div className="space-y-2">
                {newerData && (
                  <p className="text-xs text-warning">
                    Your data was saved by a newer version of Mustang AI. Install that version again, or restore a backup made before it.
                  </p>
                )}
                {showBackups || newerData ? (
                  <BackupList compact />
                ) : (
                  <button onClick={() => setShowBackups(true)} className="text-xs text-link hover:underline underline">
                    Restore a database backup…
                  </button>
                )}
              </div>
            );
          })()}
          <button
            onClick={handleRetry}
            disabled={retrying}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-accent hover:bg-accent-hover disabled:opacity-60 text-fg text-sm font-semibold transition"
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
      <div className="h-screen w-screen bg-canvas flex flex-col items-center justify-center gap-4 text-fg">
        <Loader2 className="w-8 h-8 text-link animate-spin" />
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
        <div className="fixed top-20 right-4 z-40 max-w-md bg-panel border border-warning/40 rounded-md p-4 flex items-start gap-3 text-xs">
          <Cpu className="w-4 h-4 text-warning shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-semibold text-warning">Running on CPU</p>
            <p className="text-warning/80 leading-relaxed">{gpu!.message}</p>
          </div>
          <button
            onClick={() => setGpuNoticeDismissed(true)}
            className="text-warning hover:text-warning p-1 rounded-lg hover:bg-warning/10 transition"
            aria-label="Dismiss"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
      {reconnecting && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center">
          <div className="flex items-center gap-3 bg-panel border border-line rounded-md px-5 py-3 text-sm text-fg">
            <Loader2 className="w-4 h-4 text-link animate-spin" />
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
