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
  BACKEND_URL,
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

  // SSE event source reference
  const sseRef = useRef<EventSource | null>(null);

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
        sseRef.current.close();
        sseRef.current = null;
      }
      try {
        const es = new EventSource(`${BACKEND_URL}/api/downloads/progress`);
        sseRef.current = es;

        es.addEventListener("progress", (event) => {
          if (!isMounted) return;
          try {
            const updatedTask: DownloadTask = JSON.parse(event.data);
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
          } catch (e) {
            console.error("Malformed SSE progress message:", e);
          }
        });

        es.onerror = () => {
          es.close();
          if (isMounted) {
            // Reconnect after 3 seconds
            retryTimeout = setTimeout(connectSSE, 3000);
          }
        };
      } catch (err) {
        console.warn("Could not open SSE download connection:", err);
      }
    };

    connectSSE();

    return () => {
      isMounted = false;
      if (retryTimeout) clearTimeout(retryTimeout);
      if (sseRef.current) {
        sseRef.current.close();
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
    <div className="flex flex-col h-full overflow-hidden bg-[#07090E] text-slate-200">
      {/* Top Banner / Error */}
      {errorMessage && (
        <div className="px-6 py-2.5 bg-rose-950/40 border-b border-rose-500/30 flex items-center gap-2 text-xs text-rose-300">
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
          <div className="py-20 flex flex-col items-center justify-center gap-2 text-slate-500 text-xs">
            <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
            <span>Loading active download queue...</span>
          </div>
        ) : downloads.length === 0 ? (
          <div className="py-20 text-center border border-dashed border-white/10 rounded-2xl bg-white/[0.01] p-8">
            <Download className="w-10 h-10 mx-auto text-slate-600 mb-3" />
            <h3 className="text-sm font-medium text-slate-300">No active or past downloads</h3>
            <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
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
                className={`p-4 rounded-xl border transition ${task.status === "DOWNLOADING"
                    ? "bg-[#0A0D17] border-indigo-500/40 shadow-[0_0_20px_rgba(99,102,241,0.06)]"
                    : task.status === "COMPLETED"
                      ? "bg-[#0A0D17]/80 border-emerald-500/25"
                      : task.status === "FAILED"
                        ? "bg-rose-950/10 border-rose-500/30"
                        : "bg-[#0A0D17]/60 border-white/[0.07]"
                  }`}
              >
                <div className="space-y-3">
                  {/* Top Row: Info + Badges + Actions */}
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h4 className="text-sm font-semibold text-slate-100 truncate" title={task.filename}>
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
                            <span className="text-indigo-400 font-semibold">{pct}%</span>
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
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-yellow-500/10 hover:bg-yellow-500/20 text-yellow-300 border border-yellow-500/30 transition disabled:opacity-50"
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
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-600/20 transition disabled:opacity-50"
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
                            className="p-1.5 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 border border-white/5 transition disabled:opacity-50"
                            title="Cancel download"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        )}

                      {(task.status === "FAILED" || task.status === "CANCELLED") && (
                        <button
                          onClick={() => handleRetry(task.id)}
                          disabled={isBusy}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10 transition disabled:opacity-50"
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
                          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${isLoadedInEngine
                              ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 cursor-default"
                              : "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/20"
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
                        <div className="w-full bg-black/40 border border-white/5 rounded-full h-2 overflow-hidden">
                          <div
                            className={`h-full transition-all duration-300 rounded-full ${task.status === "VERIFYING"
                                ? "bg-purple-500 animate-pulse"
                                : task.status === "PAUSED"
                                  ? "bg-yellow-500/60"
                                  : "bg-gradient-to-r from-indigo-500 to-indigo-400"
                              }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>

                        {/* Speed & ETA Metrics */}
                        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono">
                          <div>
                            {task.status === "DOWNLOADING" && task.speed_bps > 0 && (
                              <span className="text-indigo-300">
                                Speed: <strong>{formatSpeed(task.speed_bps)}</strong>
                              </span>
                            )}
                            {task.status === "PAUSED" && <span>Download paused</span>}
                            {task.status === "QUEUED" && <span>Waiting for active slot...</span>}
                            {task.status === "VERIFYING" && (
                              <span className="text-purple-300">Verifying integrity...</span>
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
                    <div className="p-2.5 rounded-lg bg-rose-950/20 border border-rose-500/30 text-xs text-rose-300 flex items-start gap-2">
                      <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400 mt-0.5" />
                      <span className="break-all">{task.error_message}</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Footer Status Bar */}
      <div className="px-6 py-3 border-t border-white/[0.08] bg-[#0A0E1A]/60 flex items-center justify-between text-xs text-slate-500">
        <div className="flex items-center gap-2">
          <span>Staging Directory:</span>
          <code className="text-slate-400 font-mono text-[11px] bg-white/5 px-2 py-0.5 rounded border border-white/5">
            models/.downloads/
          </code>
        </div>
        <div className="flex items-center gap-1">
          <span>Concurrency:</span>
          <strong className="text-slate-300 font-mono">1 active / 10 queued</strong>
        </div>
      </div>

      {/* Cancel Confirmation Modal */}
      {taskToCancel && (
        <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md bg-[#0D1220] border border-rose-500/30 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-400">
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

            <div className="bg-black/30 border border-white/5 rounded-xl p-3 text-xs space-y-1 font-mono text-slate-300">
              <div className="text-slate-500 text-[11px]">Filename:</div>
              <div className="text-rose-300 font-semibold truncate">{taskToCancel.filename}</div>
              <div className="text-slate-500 text-[11px] pt-1">Downloaded so far:</div>
              <div>
                {formatBytes(taskToCancel.downloaded_bytes)} of {taskToCancel.total_bytes > 0 ? formatBytes(taskToCancel.total_bytes) : "unknown size"}
              </div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setTaskToCancel(null)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10 transition"
              >
                Keep Downloading
              </button>
              <button
                onClick={() => handleConfirmCancel(taskToCancel.id)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-rose-600 hover:bg-rose-500 text-white font-semibold shadow-lg shadow-rose-600/30 transition"
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
