import React, { useState, useEffect, useRef } from "react";
import {
  Download,
  Pause,
  Play,
  X,
  RotateCcw,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ShieldCheck,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { DownloadTask, DownloadStatus, EngineStatus } from "../types";
import {
  fetchDownloads,
  pauseDownload,
  resumeDownload,
  cancelDownload,
  retryDownload,
  loadModel,
  subscribeDownloadProgress,
} from "../services/api";

interface DownloadQueueProps {
  onLoadModel?: (filename: string) => void;
  engineStatus?: EngineStatus | null;
  onStatusChange?: (status: EngineStatus) => void;
}

export const DownloadQueue: React.FC<DownloadQueueProps> = ({
  onLoadModel,
  engineStatus,
  onStatusChange,
}) => {
  const [downloads, setDownloads] = useState<DownloadTask[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [actionInProgressId, setActionInProgressId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [taskToCancel, setTaskToCancel] = useState<DownloadTask | null>(null);

  // Unsubscribe function for the authenticated progress stream
  const sseRef = useRef<(() => void) | null>(null);

  const loadInitialDownloads = async () => {
    try {
      const list = await fetchDownloads();
      setDownloads(list);
    } catch (err: unknown) {
      console.warn("Could not fetch downloads:", err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadInitialDownloads();

    // Establish Server-Sent Events stream for real-time progress
    let isMounted = true;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;

    const connectSSE = () => {
      if (!isMounted) return;
      if (sseRef.current) {
        sseRef.current();
        sseRef.current = null;
      }
      sseRef.current = subscribeDownloadProgress(
        (updatedTask) => {
          if (!isMounted) return;
          setDownloads((prev) => {
            const idx = prev.findIndex((t) => t.id === updatedTask.id);
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = updatedTask;
              return next;
            } else {
              return [updatedTask, ...prev];
            }
          });
        },
        (err) => {
          if (err) console.warn("Download progress stream interrupted:", err);
          if (isMounted) {
            // Reconnect after 3 seconds
            retryTimeout = setTimeout(connectSSE, 3000);
          }
        }
      );
    };

    connectSSE();

    return () => {
      isMounted = false;
      if (retryTimeout) clearTimeout(retryTimeout);
      if (sseRef.current) {
        sseRef.current();
        sseRef.current = null;
      }
    };
  }, []);

  const handlePause = async (id: string) => {
    setActionInProgressId(id);
    setErrorMessage(null);
    try {
      await pauseDownload(id);
      setDownloads((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: "PAUSED" } : d))
      );
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to pause download");
    } finally {
      setActionInProgressId(null);
    }
  };

  const handleResume = async (id: string) => {
    setActionInProgressId(id);
    setErrorMessage(null);
    try {
      await resumeDownload(id);
      setDownloads((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: "QUEUED" } : d))
      );
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to resume download");
    } finally {
      setActionInProgressId(null);
    }
  };

  const handleCancelClick = (task: DownloadTask) => {
    if (task.status === "QUEUED") {
      // 0 bytes downloaded; cancel immediately
      handleConfirmCancel(task.id);
    } else {
      // Prompt confirmation before discarding partial downloaded file
      setTaskToCancel(task);
    }
  };

  const handleConfirmCancel = async (id: string) => {
    setTaskToCancel(null);
    setActionInProgressId(id);
    setErrorMessage(null);
    try {
      await cancelDownload(id);
      setDownloads((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: "CANCELLED" } : d))
      );
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to cancel download");
    } finally {
      setActionInProgressId(null);
    }
  };

  const handleRetry = async (id: string) => {
    setActionInProgressId(id);
    setErrorMessage(null);
    try {
      await retryDownload(id);
      setDownloads((prev) =>
        prev.map((d) =>
          d.id === id
            ? { ...d, status: "QUEUED", downloaded_bytes: 0, error_message: null }
            : d
        )
      );
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to retry download");
    } finally {
      setActionInProgressId(null);
    }
  };

  const handleLoadCompletedModel = async (filename: string) => {
    if (onLoadModel) {
      onLoadModel(filename);
      return;
    }
    try {
      const newStatus = await loadModel(filename);
      if (onStatusChange) {
        onStatusChange(newStatus);
      }
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to load model");
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024 * 1024) {
      return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const formatSpeed = (bps: number) => {
    if (bps >= 1024 * 1024) {
      return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`;
    }
    if (bps >= 1024) {
      return `${(bps / 1024).toFixed(0)} KB/s`;
    }
    return `${Math.round(bps)} B/s`;
  };

  const formatETA = (sec: number) => {
    if (sec <= 0) return "--";
    if (sec < 60) return `${Math.round(sec)}s`;
    const min = Math.floor(sec / 60);
    const rem = Math.round(sec % 60);
    return `${min}m ${rem}s`;
  };

  const getIntegrityBadge = (task: DownloadTask) => {
    if (task.status === "COMPLETED") {
      if (
        task.integrity_status === "VERIFIED" ||
        (task.sha256_expected && task.sha256_actual === task.sha256_expected)
      ) {
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-medium px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">
            <ShieldCheck className="w-3 h-3 text-emerald-400" />
            <span>SHA-256 Verified</span>
          </span>
        );
      } else {
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-medium px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">
            <AlertTriangle className="w-3 h-3 text-amber-400" />
            <span>Size Verified (No Upstream Checksum)</span>
          </span>
        );
      }
    }
    if (task.status === "FAILED") {
      if (
        task.integrity_status === "FAILED_CHECKSUM_MISMATCH" ||
        task.error_message?.includes("SHA-256")
      ) {
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-medium px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30">
            <AlertTriangle className="w-3 h-3 text-rose-400" />
            <span>Checksum Mismatch</span>
          </span>
        );
      }
      if (
        task.integrity_status === "FAILED_SIZE_MISMATCH" ||
        task.error_message?.toLowerCase().includes("size mismatch")
      ) {
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-medium px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30">
            <AlertTriangle className="w-3 h-3 text-rose-400" />
            <span>Size Mismatch</span>
          </span>
        );
      }
    }
    return null;
  };

  const getStatusBadge = (status: DownloadStatus) => {
    switch (status) {
      case "QUEUED":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20">
            <Clock className="w-3 h-3" /> Queued
          </span>
        );
      case "DOWNLOADING":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
            <RefreshCw className="w-3 h-3 animate-spin text-indigo-400" /> Downloading
          </span>
        );
      case "PAUSED":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-yellow-500/10 text-yellow-300 border border-yellow-500/20">
            <Pause className="w-3 h-3" /> Paused
          </span>
        );
      case "VERIFYING":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-purple-500/15 text-purple-300 border border-purple-500/30">
            <RefreshCw className="w-3 h-3 animate-spin text-purple-400" /> Verifying SHA-256
          </span>
        );
      case "COMPLETED":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/25">
            <CheckCircle2 className="w-3 h-3" /> Completed
          </span>
        );
      case "FAILED":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30">
            <AlertTriangle className="w-3 h-3" /> Failed
          </span>
        );
      case "CANCELLED":
        return (
          <span className="flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
            Cancelled
          </span>
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden bg-transparent text-fg">
      {/* Top Banner / Error */}
      {errorMessage && (
        <div className="px-6 py-2.5 bg-rose-950/30 border-b border-rose-500/40 flex items-center gap-2 text-xs text-rose-300">
          <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
          <span className="flex-1">{errorMessage}</span>
          <button
            onClick={() => setErrorMessage(null)}
            className="text-rose-400 hover:text-rose-200 text-xs font-semibold"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Downloads List */}
      <div className="flex-1 overflow-y-auto p-6 space-y-3">
        {isLoading ? (
          <div className="py-20 flex flex-col items-center justify-center gap-2 text-subtle text-xs">
            <RefreshCw className="w-6 h-6 animate-spin text-accent" />
            <span>Loading active download queue...</span>
          </div>
        ) : downloads.length === 0 ? (
          <div className="py-20 text-center border border-dashed border-line/70 rounded-2xl bg-panel/30 p-8">
            <Download className="w-10 h-10 mx-auto text-subtle mb-3" />
            <h3 className="text-sm font-semibold text-fg">No active or past downloads</h3>
            <p className="text-xs text-subtle mt-1.5 max-w-sm mx-auto leading-relaxed">
              Switch to the <strong>Hugging Face Hub</strong> tab to discover open-weight models and initiate GGUF downloads.
            </p>
          </div>
        ) : (
          downloads.map((task) => {
            const isBusy = actionInProgressId === task.id;
            const pct =
              task.total_bytes > 0
                ? Math.min(100, Math.round((task.downloaded_bytes / task.total_bytes) * 100))
                : 0;

            const isLoadedInEngine =
              engineStatus?.model_name === task.filename ||
              engineStatus?.model_path?.includes(task.filename);

            return (
              <div
                key={task.id}
                className={`p-4 rounded-2xl border transition-all shadow-sm ${
                  task.status === "DOWNLOADING"
                    ? "bg-panel/80 border-accent/80 shadow-glow-sm"
                    : task.status === "COMPLETED"
                      ? "bg-panel/60 border-emerald-500/40"
                      : task.status === "FAILED"
                        ? "bg-rose-950/20 border-rose-500/40"
                        : "bg-panel/50 border-line/60"
                }`}
              >
                <div className="space-y-3">
                  {/* Top Row: Info + Badges + Actions */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1.5 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h4 className="text-sm font-semibold text-fg truncate tracking-tight" title={task.filename}>
                          {task.filename}
                        </h4>
                        {getStatusBadge(task.status)}
                        {getIntegrityBadge(task)}
                      </div>

                      <div className="text-[11px] text-slate-500 flex items-center gap-2 truncate">
                        <span className="font-mono text-slate-400">{task.repo_id}</span>
                        <span>•</span>
                        <span className="font-mono">{formatBytes(task.downloaded_bytes)}</span>
                        {task.total_bytes > 0 && (
                          <>
                            <span>/</span>
                            <span className="font-mono">{formatBytes(task.total_bytes)}</span>
                            <span>•</span>
                            <span className="text-accent font-semibold">{pct}%</span>
                          </>
                        )}
                      </div>
                    </div>

                    {/* Action Controls */}
                    <div className="flex items-center gap-2 shrink-0">
                      {task.status === "DOWNLOADING" && (
                        <button
                          onClick={() => handlePause(task.id)}
                          disabled={isBusy}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/30 transition-all disabled:opacity-50"
                          title="Pause download"
                        >
                          <Pause className="w-3.5 h-3.5" />
                          <span>Pause</span>
                        </button>
                      )}

                      {task.status === "PAUSED" && (
                        <button
                          onClick={() => handleResume(task.id)}
                          disabled={isBusy}
                          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs font-medium btn-violet text-white shadow-glow-sm hover:brightness-110 active:scale-95 transition-all disabled:opacity-50"
                          title="Resume download"
                        >
                          <Play className="w-3.5 h-3.5 fill-current" />
                          <span>Resume</span>
                        </button>
                      )}

                      {(task.status === "QUEUED" ||
                        task.status === "DOWNLOADING" ||
                        task.status === "PAUSED") && (
                          <button
                            onClick={() => handleCancelClick(task)}
                            disabled={isBusy}
                            className="p-2 rounded-xl text-subtle hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/30 transition-all disabled:opacity-50"
                            title="Cancel download"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        )}

                      {(task.status === "FAILED" || task.status === "CANCELLED") && (
                        <button
                          onClick={() => handleRetry(task.id)}
                          disabled={isBusy}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-raised/80 hover:bg-raised text-fg border border-line/70 transition-all disabled:opacity-50"
                          title="Retry download"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                          <span>Retry</span>
                        </button>
                      )}

                      {task.status === "COMPLETED" && (
                        <button
                          onClick={() => handleLoadCompletedModel(task.filename)}
                          disabled={isLoadedInEngine}
                          className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
                            isLoadedInEngine
                              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 cursor-default"
                              : "btn-violet text-white shadow-glow-sm hover:brightness-110 active:scale-95"
                          }`}
                        >
                          <Play className="w-3.5 h-3.5 fill-current" />
                          <span>{isLoadedInEngine ? "Active in Chat" : "Load Model"}</span>
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Progress Bar (Visible during active, queued, or paused states) */}
                  {(task.status === "DOWNLOADING" ||
                    task.status === "PAUSED" ||
                    task.status === "VERIFYING" ||
                    task.status === "QUEUED") && (
                      <div className="space-y-1.5">
                        <div className="w-full bg-canvas/90 border border-line/60 rounded-full h-2 overflow-hidden">
                          <div
                            className={`h-full transition-all duration-300 rounded-full ${
                              task.status === "VERIFYING"
                                ? "bg-gradient-to-r from-accent to-purple-400"
                                : task.status === "PAUSED"
                                  ? "bg-amber-500"
                                  : "bg-gradient-to-r from-accent to-purple-600"
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>

                        {/* Speed & ETA Metrics */}
                        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono">
                          <div>
                            {task.status === "DOWNLOADING" && task.speed_bps > 0 && (
                              <span className="text-accent font-medium">
                                Speed: <strong>{formatSpeed(task.speed_bps)}</strong>
                              </span>
                            )}
                            {task.status === "PAUSED" && <span className="text-amber-400">Download paused</span>}
                            {task.status === "QUEUED" && <span>Waiting for active slot...</span>}
                            {task.status === "VERIFYING" && (
                              <span className="text-accent">Verifying integrity...</span>
                            )}
                          </div>

                          <div>
                            {task.status === "DOWNLOADING" && task.eta_seconds > 0 && (
                              <span>
                                ETA: <strong>{formatETA(task.eta_seconds)}</strong>
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    )}

                  {/* Terminal Error Message */}
                  {task.status === "FAILED" && task.error_message && (
                    <div className="p-3 rounded-xl bg-rose-950/20 border border-rose-500/40 text-xs text-rose-300 flex items-start gap-2.5">
                      <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400 mt-0.5" />
                      <span className="break-all leading-relaxed">{task.error_message}</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Footer Status Bar */}
      <div className="px-6 py-3 border-t border-line/50 bg-panel/60 flex items-center justify-between text-xs text-subtle">
        <div className="flex items-center gap-2">
          <span>Staging Directory:</span>
          <code className="text-muted font-mono text-xs bg-raised/80 px-2 py-0.5 rounded-lg border border-line/60">
            models/.downloads/
          </code>
        </div>
        <div className="flex items-center gap-1.5">
          <span>Concurrency:</span>
          <strong className="text-fg font-mono bg-raised/80 px-2 py-0.5 rounded-lg border border-line/60">1 active / 10 queued</strong>
        </div>
      </div>

      {/* Cancel Confirmation Modal */}
      {taskToCancel && (
        <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md bg-panel/95 backdrop-blur-xl border border-rose-500/40 rounded-2xl p-6 shadow-2xl space-y-4 animate-slide-up">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-slate-100">
                  Cancel Download?
                </h3>
                <p className="text-xs text-slate-400">
                  This will cancel the task and discard partial progress.
                </p>
              </div>
            </div>

            <div className="bg-canvas/90 border border-line/60 rounded-xl p-3.5 text-xs space-y-1.5 font-mono text-fg">
              <div className="text-subtle text-[11px]">Filename:</div>
              <div className="text-rose-400 font-semibold truncate">{taskToCancel.filename}</div>
              <div className="text-subtle text-[11px] pt-1">Downloaded so far:</div>
              <div>
                {formatBytes(taskToCancel.downloaded_bytes)} of {taskToCancel.total_bytes > 0 ? formatBytes(taskToCancel.total_bytes) : "unknown size"}
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setTaskToCancel(null)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-raised/80 hover:bg-raised text-fg border border-line/70 transition-all"
              >
                Keep Downloading
              </button>
              <button
                onClick={() => handleConfirmCancel(taskToCancel.id)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-rose-600 hover:bg-rose-500 text-white font-semibold shadow-glow-sm transition-all"
              >
                Abort & Discard File
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
