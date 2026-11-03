import React, { useState, useEffect, useCallback } from "react";
import {
  X,
  RefreshCw,
  Trash2,
  Play,
  Square,
  AlertTriangle,
  CheckCircle2,
  Info,
  Search,
  HardDrive,
  Cpu,
  Sparkles,
  ShieldAlert,
  Globe,
  Download,
  ArrowRightLeft,
} from "lucide-react";
import {
  LibraryModel,
  ModelContextRecommendation,
  ScanResult,
  EngineStatus,
} from "../types";
import {
  fetchModelLibrary,
  rescanModels,
  fetchContextRecommendation,
  deleteModel,
  loadModel,
  unloadModel,
} from "../services/api";
import { HuggingFaceDiscovery } from "./HuggingFaceDiscovery";
import { DownloadQueue } from "./DownloadQueue";

interface ModelLibraryModalProps {
  isOpen: boolean;
  onClose: () => void;
  engineStatus: EngineStatus | null;
  onStatusChange: (status: EngineStatus) => void;
  onModelSelected?: (filename: string) => void;
}

export const ModelLibraryModal: React.FC<ModelLibraryModalProps> = ({
  isOpen,
  onClose,
  engineStatus,
  onStatusChange,
  onModelSelected,
}) => {
  const [activeTab, setActiveTab] = useState<"LOCAL" | "HF" | "DOWNLOADS">("LOCAL");
  const [models, setModels] = useState<LibraryModel[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionInProgressId, setActionInProgressId] = useState<string | null>(null);

  // Recommendations state: map of model_id -> ModelContextRecommendation
  const [recommendations, setRecommendations] = useState<
    Record<string, ModelContextRecommendation>
  >({});
  const [loadingRecId, setLoadingRecId] = useState<string | null>(null);
  const [expandedRecId, setExpandedRecId] = useState<string | null>(null);

  // Delete confirmation modal state
  const [modelToDelete, setModelToDelete] = useState<LibraryModel | null>(null);

  const loadLibrary = useCallback(async () => {
    setIsLoading(true);
    setActionError(null);
    try {
      const list = await fetchModelLibrary();
      setModels(list);
    } catch (err: unknown) {
      setActionError(
        err instanceof Error ? err.message : "Failed to load model library"
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      loadLibrary();
    }
  }, [isOpen, loadLibrary]);

  const handleRescan = async () => {
    setIsScanning(true);
    setActionError(null);
    try {
      const res = await rescanModels();
      setScanResult(res);
      await loadLibrary();
    } catch (err: unknown) {
      setActionError(
        err instanceof Error ? err.message : "Failed to scan models directory"
      );
    } finally {
      setIsScanning(false);
    }
  };

  const handleToggleRecommendations = async (model: LibraryModel) => {
    if (expandedRecId === model.id) {
      setExpandedRecId(null);
      return;
    }

    setExpandedRecId(model.id);

    // Fetch if not already cached
    if (!recommendations[model.id]) {
      setLoadingRecId(model.id);
      try {
        const rec = await fetchContextRecommendation(model.id);
        setRecommendations((prev) => ({ ...prev, [model.id]: rec }));
      } catch (err: unknown) {
        console.warn("Could not fetch recommendation:", err);
      } finally {
        setLoadingRecId(null);
      }
    }
  };

  const handleLoadModel = async (model: LibraryModel) => {
    setActionInProgressId(model.id);
    setActionError(null);
    try {
      const newStatus = await loadModel(model.filename);
      onStatusChange(newStatus);
      if (onModelSelected) {
        onModelSelected(model.filename);
      }
      await loadLibrary();
    } catch (err: unknown) {
      setActionError(
        err instanceof Error ? err.message : "Failed to load model"
      );
    } finally {
      setActionInProgressId(null);
    }
  };

  const handleUnloadModel = async (model: LibraryModel) => {
    setActionInProgressId(model.id);
    setActionError(null);
    try {
      const newStatus = await unloadModel();
      onStatusChange(newStatus);
      await loadLibrary();
    } catch (err: unknown) {
      setActionError(
        err instanceof Error ? err.message : "Failed to unload model"
      );
    } finally {
      setActionInProgressId(null);
    }
  };

  const handleConfirmDelete = async () => {
    if (!modelToDelete) return;
    const target = modelToDelete;
    setModelToDelete(null);
    setActionInProgressId(target.id);
    setActionError(null);

    try {
      await deleteModel(target.id);
      setModels((prev) => prev.filter((m) => m.id !== target.id));
    } catch (err: unknown) {
      setActionError(
        err instanceof Error ? err.message : "Failed to delete model"
      );
    } finally {
      setActionInProgressId(null);
    }
  };

  if (!isOpen) return null;

  const filteredModels = models.filter((m) => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return true;
    return (
      m.filename.toLowerCase().includes(q) ||
      m.architecture.toLowerCase().includes(q) ||
      m.quantization.toLowerCase().includes(q) ||
      m.repo_id.toLowerCase().includes(q)
    );
  });

  const getTierColor = (tier: string) => {
    switch (tier) {
      case "LOW_RISK":
        return "text-success bg-success/10 border-success/40";
      case "RECOMMENDED":
        return "text-link bg-accent/10 border-accent";
      case "HIGH_MEMORY":
        return "text-warning bg-warning/10 border-warning/40";
      case "LIKELY_CPU_OFFLOAD":
        return "text-link bg-accent/10 border-accent";
      case "PROBABLE_OOM":
        return "text-danger bg-danger/10 border-danger/40";
      default:
        return "text-muted bg-raised border-line";
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60">
      <div className="relative w-full max-w-4xl max-h-[88vh] flex flex-col bg-canvas border border-line rounded-lg shadow-menu overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3.5 border-b border-line bg-canvas flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 bg-canvas p-1 rounded-md border border-line">
              <button
                onClick={() => setActiveTab("LOCAL")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  activeTab === "LOCAL"
                    ? "bg-accent text-fg"
                    : "text-muted hover:text-fg hover:bg-raised"
                }`}
              >
                <HardDrive className="w-3.5 h-3.5" />
                <span>Local Models</span>
                <span className="text-xs px-1.5 py-0.2 rounded-full bg-raised font-mono">
                  {models.length}
                </span>
              </button>
              <button
                onClick={() => setActiveTab("HF")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  activeTab === "HF"
                    ? "bg-accent text-fg"
                    : "text-muted hover:text-fg hover:bg-raised"
                }`}
              >
                <Globe className="w-3.5 h-3.5" />
                <span>Hugging Face Hub</span>
              </button>
              <button
                onClick={() => setActiveTab("DOWNLOADS")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  activeTab === "DOWNLOADS"
                    ? "bg-accent text-fg"
                    : "text-muted hover:text-fg hover:bg-raised"
                }`}
              >
                <Download className="w-3.5 h-3.5" />
                <span>Downloads</span>
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {activeTab === "LOCAL" && (
              <button
                onClick={handleRescan}
                disabled={isScanning || !!actionInProgressId}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-raised hover:bg-raised border border-line text-fg transition disabled:opacity-50"
                title="Rescan models directory"
              >
                <RefreshCw
                  className={`w-3.5 h-3.5 ${isScanning ? "animate-spin text-link" : ""}`}
                />
                <span>{isScanning ? "Scanning..." : "Rescan Directory"}</span>
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-raised transition"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {activeTab === "HF" ? (
          <HuggingFaceDiscovery
            onDownloadStarted={() => {
              setActiveTab("DOWNLOADS");
            }}
          />
        ) : activeTab === "DOWNLOADS" ? (
          <DownloadQueue
            onLoadModel={(filename) => {
              onModelSelected?.(filename);
              loadLibrary();
              setActiveTab("LOCAL");
            }}
            engineStatus={engineStatus}
            onStatusChange={onStatusChange}
          />
        ) : (
          <>
            {/* Scan Status Banner */}
        {scanResult && (
          <div className="px-6 py-2 bg-accent/10 border-b border-accent flex items-center justify-between text-xs text-link">
            <span>
              Directory synced: <strong>{scanResult.scanned}</strong> scanned •{" "}
              <strong className="text-success">+{scanResult.added}</strong> added •{" "}
              <strong className="text-warning">~{scanResult.updated}</strong> updated •{" "}
              <strong className="text-danger">-{scanResult.pruned}</strong> pruned
            </span>
            <button
              onClick={() => setScanResult(null)}
              className="text-link hover:underline text-xs"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Error Banner */}
        {actionError && (
          <div className="px-6 py-2.5 bg-danger/10 border-b border-danger/40 flex items-center gap-2 text-xs text-danger">
            <AlertTriangle className="w-4 h-4 shrink-0 text-danger" />
            <span className="flex-1">{actionError}</span>
            <button
              onClick={() => setActionError(null)}
              className="text-danger hover:text-danger text-xs font-semibold"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Filter and Search Bar */}
        <div className="px-6 py-3 border-b border-line bg-canvas flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-subtle" />
            <input
              type="text"
              placeholder="Search by filename, architecture, or quantization..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-panel border border-line rounded-lg pl-9 pr-3 py-1.5 text-xs text-fg placeholder:text-subtle focus:outline-none focus:border-accent transition"
            />
          </div>
        </div>

        {/* Models List Scroll Area */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {isLoading ? (
            <div className="py-12 flex flex-col items-center justify-center gap-2 text-subtle text-xs">
              <RefreshCw className="w-6 h-6 animate-spin text-link" />
              <span>Loading indexed model library...</span>
            </div>
          ) : filteredModels.length === 0 ? (
            <div className="py-16 text-center border border-dashed border-line rounded-lg bg-raised p-8">
              <HardDrive className="w-10 h-10 mx-auto text-subtle mb-3" />
              <h3 className="text-sm font-medium text-fg">No GGUF models found</h3>
              <p className="text-xs text-subtle mt-1 max-w-sm mx-auto">
                Place your .gguf model files into the <code className="text-link">models/</code> directory and click <strong>Rescan Directory</strong> above.
              </p>
            </div>
          ) : (
            filteredModels.map((model) => {
              const isLoaded =
                model.is_active ||
                engineStatus?.model_name === model.filename ||
                engineStatus?.model_path?.includes(model.filename);
              const isBusy = actionInProgressId === model.id;
              const rec = recommendations[model.id];
              const isRecOpen = expandedRecId === model.id;

              return (
                <div
                  key={model.id}
                  className={`border rounded-md transition ${
                    isLoaded
                      ? "border-success/40 bg-success/10"
                      : "border-line bg-canvas hover:border-line"
                  }`}
                >
                  <div className="p-4 flex flex-col gap-3">
                    {/* Top Row: Title + Badges + Actions */}
                    <div className="flex items-start justify-between gap-3">
                      <div className="space-y-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="text-sm font-semibold text-fg truncate" title={model.filename}>
                            {model.filename}
                          </h3>
                          {isLoaded && (
                            <span className="flex items-center gap-1 text-xs font-medium text-success bg-success/10 border border-success/40 px-2 py-0.5 rounded-full">
                              <CheckCircle2 className="w-3 h-3" /> Active / Loaded
                            </span>
                          )}
                          <span className="text-xs font-mono px-2 py-0.5 rounded bg-accent/10 text-link border border-accent">
                            {model.quantization}
                          </span>
                          <span className="text-xs font-mono px-2 py-0.5 rounded bg-raised text-fg border border-line">
                            {model.architecture}
                          </span>
                          {model.parameter_count && model.parameter_count !== "Unknown" && (
                            <span className="text-xs font-mono px-2 py-0.5 rounded bg-raised text-fg border border-line">
                              {model.parameter_count}
                            </span>
                          )}
                        </div>

                        <div className="text-xs text-subtle flex items-center gap-2 truncate">
                          <span>Origin: {model.download_origin === "huggingface" ? `HF: ${model.repo_id}` : "Local File"}</span>
                          <span>•</span>
                          <span className="font-mono">{model.file_size_gb} GB</span>
                          <span>•</span>
                          <span className="font-mono truncate" title={model.canonical_path}>
                            {model.canonical_path}
                          </span>
                        </div>
                      </div>

                      {/* Main Actions */}
                      <div className="flex items-center gap-2 shrink-0">
                        {isLoaded ? (
                          <button
                            onClick={() => handleUnloadModel(model)}
                            disabled={isBusy}
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-danger/10 hover:bg-danger/10 border border-danger/40 text-danger transition disabled:opacity-50"
                          >
                            <Square className="w-3.5 h-3.5" />
                            <span>{isBusy ? "Unloading..." : "Unload"}</span>
                          </button>
                        ) : (
                          <button
                            onClick={() => handleLoadModel(model)}
                            disabled={isBusy}
                            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-fg transition disabled:opacity-50 ${
                              engineStatus?.state === "READY"
                                ? "bg-warning hover:bg-warning"
                                : "bg-accent hover:bg-accent-hover"
                            }`}
                          >
                            {engineStatus?.state === "READY" ? (
                              <>
                                <ArrowRightLeft className="w-3.5 h-3.5" />
                                <span>{isBusy ? "Switching..." : "Switch to Model"}</span>
                              </>
                            ) : (
                              <>
                                <Play className="w-3.5 h-3.5 fill-current" />
                                <span>{isBusy ? "Loading..." : "Load Model"}</span>
                              </>
                            )}
                          </button>
                        )}

                        <button
                          onClick={() => setModelToDelete(model)}
                          disabled={isLoaded || isBusy}
                          className="p-2 rounded-lg text-subtle hover:text-danger hover:bg-danger/10 border border-transparent hover:border-danger/40 transition disabled:opacity-30 disabled:pointer-events-none"
                          title={isLoaded ? "Cannot delete actively loaded model" : "Delete model from disk"}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* Metadata Grid */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-line text-xs">
                      <div className="bg-canvas p-2 rounded-lg border border-line">
                        <span className="text-subtle block">Context Window</span>
                        <span className="font-mono text-fg font-medium">
                          {model.context_length ? model.context_length.toLocaleString() : "Unknown"} tokens
                        </span>
                      </div>
                      <div className="bg-canvas p-2 rounded-lg border border-line">
                        <span className="text-subtle block">Total Layers</span>
                        <span className="font-mono text-fg font-medium">
                          {model.block_count > 0 ? `${model.block_count + 1} (${model.block_count} blocks)` : "Unknown"}
                        </span>
                      </div>
                      <div className="bg-canvas p-2 rounded-lg border border-line">
                        <span className="text-subtle block">Attention Heads</span>
                        <span className="font-mono text-fg font-medium">
                          {model.head_count > 0 ? (
                            model.head_count_kv && model.head_count_kv !== model.head_count ? (
                              `${model.head_count} / ${model.head_count_kv} (GQA)`
                            ) : (
                              `${model.head_count} (MHA)`
                            )
                          ) : (
                            "Unknown"
                          )}
                        </span>
                      </div>
                      <div className="bg-canvas p-2 rounded-lg border border-line">
                        <span className="text-subtle block">Embedding Dim</span>
                        <span className="font-mono text-fg font-medium">
                          {model.embedding_dim ? model.embedding_dim : "Unknown"}
                        </span>
                      </div>
                    </div>

                    {/* Context Recommendations Toggle Button */}
                    <div className="pt-1 flex items-center justify-between">
                      <button
                        onClick={() => handleToggleRecommendations(model)}
                        className="text-xs font-medium text-link hover:underline flex items-center gap-1.5 transition"
                      >
                        <Sparkles className="w-3.5 h-3.5" />
                        <span>
                          {isRecOpen
                            ? "Hide memory by context size"
                            : "Memory by context size"}
                        </span>
                        {loadingRecId === model.id && (
                          <RefreshCw className="w-3 h-3 animate-spin ml-1 text-link" />
                        )}
                      </button>

                      <span className="text-xs text-subtle">ID: {model.id}</span>
                    </div>

                    {/* Memory needed per context size */}
                    {isRecOpen && (
                      <div className="mt-2 p-3 bg-canvas border border-line rounded-md space-y-2.5">
                        <div className="flex items-center justify-between text-xs text-fg">
                          <span className="font-medium flex items-center gap-1.5 text-fg">
                            <Cpu className="w-3.5 h-3.5 text-link" />
                            GPU memory needed per context size
                          </span>
                          {rec && (
                            <span className="text-xs text-muted">
                              Free GPU memory: <strong className="text-fg">{Math.round(rec.available_vram_mb)} MB</strong>
                            </span>
                          )}
                        </div>

                        {rec?.estimate_status === "UNAVAILABLE" ? (
                          <div className="p-2.5 bg-panel border border-line rounded-lg text-xs text-muted">
                            {rec.reason || "Context estimation unavailable for this architecture."}
                          </div>
                        ) : rec?.recommendations ? (
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                            {Object.entries(rec.recommendations).map(([ctx, info]) => (
                              <div
                                key={ctx}
                                className="bg-canvas border border-line rounded-lg p-2.5 space-y-1.5"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="text-xs font-mono font-medium text-fg">
                                    {Number(ctx).toLocaleString()} ctx
                                  </span>
                                  <span
                                    className={`text-xs font-semibold px-1.5 py-0.5 rounded border ${getTierColor(
                                      info.risk_classification
                                    )}`}
                                  >
                                    {info.risk_classification.replace(/_/g, " ")}
                                  </span>
                                </div>
                                <div className="text-xs text-muted space-y-0.5 font-mono">
                                  <div className="flex justify-between">
                                    <span>Weights:</span>
                                    <span className="text-fg">{info.estimated_weights_gpu_mb ?? info.weights_vram_mb} MB</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>KV Cache:</span>
                                    <span className="text-fg">{info.estimated_kv_cache_mb ?? info.kv_cache_mb} MB</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>Compute:</span>
                                    <span className="text-fg">{info.estimated_compute_overhead_mb ?? info.compute_buffer_mb} MB</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>Driver:</span>
                                    <span className="text-fg">{info.estimated_driver_reserve_mb ?? 250} MB</span>
                                  </div>
                                  <div className="flex justify-between border-t border-line pt-0.5">
                                    <span>Est. Total:</span>
                                    <span className="text-link font-semibold">{info.estimated_vram_total_mb} MB</span>
                                  </div>
                                  {info.measured_model_vram_mb != null && (
                                    <div className="flex justify-between text-success border-t border-success/40 pt-0.5">
                                      <span>Measured:</span>
                                      <span>{info.measured_model_vram_mb} MB</span>
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-xs text-subtle py-2 text-center">
                            Estimating…
                          </div>
                        )}

                        <div className="flex items-center gap-1.5 text-xs text-subtle italic pt-1">
                          <Info className="w-3 h-3 shrink-0 text-subtle" />
                          <span>
                            {rec?.disclaimer ||
                              "An estimate; the real use is shown after the model loads."}
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-line bg-canvas flex items-center justify-between text-xs text-subtle">
          <div className="flex items-center gap-2">
            <span>Storage Directory:</span>
            <code className="text-muted font-mono text-xs bg-raised px-2 py-0.5 rounded border border-line">
              models/
            </code>
          </div>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs font-medium bg-raised hover:bg-raised text-fg transition"
          >
            Close
          </button>
        </div>
        </>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {modelToDelete && (
        <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/60">
          <div className="w-full max-w-md bg-panel border border-danger/40 rounded-lg p-6 shadow-menu space-y-4">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-md bg-danger/10 border border-danger/40 text-danger">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-fg">
                  Delete Model File?
                </h3>
                <p className="text-xs text-muted">
                  This will permanently delete the file from disk.
                </p>
              </div>
            </div>

            <div className="bg-canvas border border-line rounded-md p-3 text-xs space-y-1 font-mono text-fg">
              <div className="text-subtle text-xs">Filename:</div>
              <div className="text-danger font-semibold truncate">{modelToDelete.filename}</div>
              <div className="text-subtle text-xs pt-1">Size:</div>
              <div>{modelToDelete.file_size_gb} GB ({modelToDelete.file_size_bytes.toLocaleString()} bytes)</div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setModelToDelete(null)}
                className="px-4 py-2 rounded-md text-xs font-medium bg-raised hover:bg-raised text-fg border border-line transition"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-4 py-2 rounded-md text-xs font-medium bg-danger hover:bg-danger text-fg font-semibold transition"
              >
                Permanently Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
