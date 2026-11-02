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
        return "text-emerald-400 bg-emerald-500/10 border-emerald-500/20";
      case "RECOMMENDED":
        return "text-cyan-400 bg-cyan-500/10 border-cyan-500/20";
      case "HIGH_MEMORY":
        return "text-amber-400 bg-amber-500/10 border-amber-500/20";
      case "LIKELY_CPU_OFFLOAD":
        return "text-purple-400 bg-purple-500/10 border-purple-500/20";
      case "PROBABLE_OOM":
        return "text-rose-400 bg-rose-500/10 border-rose-500/20";
      default:
        return "text-slate-400 bg-slate-800 border-slate-700";
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-4xl max-h-[88vh] flex flex-col bg-[#07090E] border border-white/[0.08] rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3.5 border-b border-white/[0.08] bg-[#0A0E1A]/80 flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 bg-black/40 p-1 rounded-xl border border-white/[0.08]">
              <button
                onClick={() => setActiveTab("LOCAL")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  activeTab === "LOCAL"
                    ? "bg-indigo-600 text-white shadow-md shadow-indigo-600/20"
                    : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
                }`}
              >
                <HardDrive className="w-3.5 h-3.5" />
                <span>Local Models</span>
                <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-white/10 font-mono">
                  {models.length}
                </span>
              </button>
              <button
                onClick={() => setActiveTab("HF")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  activeTab === "HF"
                    ? "bg-indigo-600 text-white shadow-md shadow-indigo-600/20"
                    : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
                }`}
              >
                <Globe className="w-3.5 h-3.5" />
                <span>Hugging Face Hub</span>
              </button>
              <button
                onClick={() => setActiveTab("DOWNLOADS")}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition ${
                  activeTab === "DOWNLOADS"
                    ? "bg-indigo-600 text-white shadow-md shadow-indigo-600/20"
                    : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
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
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 border border-white/10 text-slate-200 transition disabled:opacity-50"
                title="Rescan models directory"
              >
                <RefreshCw
                  className={`w-3.5 h-3.5 ${isScanning ? "animate-spin text-indigo-400" : ""}`}
                />
                <span>{isScanning ? "Scanning..." : "Rescan Directory"}</span>
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-white/5 transition"
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
          <div className="px-6 py-2 bg-indigo-950/30 border-b border-indigo-500/20 flex items-center justify-between text-xs text-indigo-200">
            <span>
              Directory synced: <strong>{scanResult.scanned}</strong> scanned •{" "}
              <strong className="text-emerald-400">+{scanResult.added}</strong> added •{" "}
              <strong className="text-amber-400">~{scanResult.updated}</strong> updated •{" "}
              <strong className="text-rose-400">-{scanResult.pruned}</strong> pruned
            </span>
            <button
              onClick={() => setScanResult(null)}
              className="text-indigo-400 hover:text-indigo-200 text-xs"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Error Banner */}
        {actionError && (
          <div className="px-6 py-2.5 bg-rose-950/40 border-b border-rose-500/30 flex items-center gap-2 text-xs text-rose-300">
            <AlertTriangle className="w-4 h-4 shrink-0 text-rose-400" />
            <span className="flex-1">{actionError}</span>
            <button
              onClick={() => setActionError(null)}
              className="text-rose-400 hover:text-rose-200 text-xs font-semibold"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Filter and Search Bar */}
        <div className="px-6 py-3 border-b border-white/[0.06] bg-[#0A0D17]/40 flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-500" />
            <input
              type="text"
              placeholder="Search by filename, architecture, or quantization..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-[#0D1220] border border-white/10 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500/50 transition"
            />
          </div>
        </div>

        {/* Models List Scroll Area */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {isLoading ? (
            <div className="py-12 flex flex-col items-center justify-center gap-2 text-slate-500 text-xs">
              <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
              <span>Loading indexed model library...</span>
            </div>
          ) : filteredModels.length === 0 ? (
            <div className="py-16 text-center border border-dashed border-white/10 rounded-2xl bg-white/[0.01] p-8">
              <HardDrive className="w-10 h-10 mx-auto text-slate-600 mb-3" />
              <h3 className="text-sm font-medium text-slate-300">No GGUF models found</h3>
              <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
                Place your .gguf model files into the <code className="text-indigo-300">models/</code> directory and click <strong>Rescan Directory</strong> above.
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
                  className={`border rounded-xl transition duration-200 ${
                    isLoaded
                      ? "border-emerald-500/40 bg-emerald-950/10 shadow-[0_0_20px_rgba(16,185,129,0.06)]"
                      : "border-white/[0.07] bg-[#0A0D17]/60 hover:border-white/[0.14]"
                  }`}
                >
                  <div className="p-4 flex flex-col gap-3">
                    {/* Top Row: Title + Badges + Actions */}
                    <div className="flex items-start justify-between gap-3">
                      <div className="space-y-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="text-sm font-semibold text-slate-100 truncate" title={model.filename}>
                            {model.filename}
                          </h3>
                          {isLoaded && (
                            <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded-full">
                              <CheckCircle2 className="w-3 h-3" /> Active / Loaded
                            </span>
                          )}
                          <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/25">
                            {model.quantization}
                          </span>
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-slate-300 border border-white/10">
                            {model.architecture}
                          </span>
                          {model.parameter_count && model.parameter_count !== "Unknown" && (
                            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-slate-300 border border-white/10">
                              {model.parameter_count}
                            </span>
                          )}
                        </div>

                        <div className="text-[11px] text-slate-500 flex items-center gap-2 truncate">
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
                            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/30 text-rose-300 transition disabled:opacity-50"
                          >
                            <Square className="w-3.5 h-3.5" />
                            <span>{isBusy ? "Unloading..." : "Unload"}</span>
                          </button>
                        ) : (
                          <button
                            onClick={() => handleLoadModel(model)}
                            disabled={isBusy}
                            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium text-white shadow-lg transition disabled:opacity-50 ${
                              engineStatus?.state === "READY"
                                ? "bg-amber-600 hover:bg-amber-500 shadow-amber-600/20"
                                : "bg-indigo-600 hover:bg-indigo-500 shadow-indigo-600/20"
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
                          className="p-2 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 transition disabled:opacity-30 disabled:pointer-events-none"
                          title={isLoaded ? "Cannot delete actively loaded model" : "Delete model from disk"}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* Metadata Grid */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-white/[0.04] text-[11px]">
                      <div className="bg-black/20 p-2 rounded-lg border border-white/[0.04]">
                        <span className="text-slate-500 block">Context Window</span>
                        <span className="font-mono text-slate-200 font-medium">
                          {model.context_length ? model.context_length.toLocaleString() : "Unknown"} tokens
                        </span>
                      </div>
                      <div className="bg-black/20 p-2 rounded-lg border border-white/[0.04]">
                        <span className="text-slate-500 block">Total Layers</span>
                        <span className="font-mono text-slate-200 font-medium">
                          {model.block_count > 0 ? `${model.block_count + 1} (${model.block_count} blocks)` : "Unknown"}
                        </span>
                      </div>
                      <div className="bg-black/20 p-2 rounded-lg border border-white/[0.04]">
                        <span className="text-slate-500 block">Attention Heads</span>
                        <span className="font-mono text-slate-200 font-medium">
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
                      <div className="bg-black/20 p-2 rounded-lg border border-white/[0.04]">
                        <span className="text-slate-500 block">Embedding Dim</span>
                        <span className="font-mono text-slate-200 font-medium">
                          {model.embedding_dim ? model.embedding_dim : "Unknown"}
                        </span>
                      </div>
                    </div>

                    {/* Context Recommendations Toggle Button */}
                    <div className="pt-1 flex items-center justify-between">
                      <button
                        onClick={() => handleToggleRecommendations(model)}
                        className="text-[11px] font-medium text-indigo-400 hover:text-indigo-300 flex items-center gap-1.5 transition"
                      >
                        <Sparkles className="w-3.5 h-3.5" />
                        <span>
                          {isRecOpen
                            ? "Hide Context Memory Tiers"
                            : "Show Advisory Memory Tiers"}
                        </span>
                        {loadingRecId === model.id && (
                          <RefreshCw className="w-3 h-3 animate-spin ml-1 text-indigo-400" />
                        )}
                      </button>

                      <span className="text-[10px] text-slate-600">ID: {model.id}</span>
                    </div>

                    {/* Expandable Advisory Memory Tiers Drawer */}
                    {isRecOpen && (
                      <div className="mt-2 p-3 bg-black/40 border border-white/[0.06] rounded-xl space-y-2.5 animate-fade-in">
                        <div className="flex items-center justify-between text-xs text-slate-300">
                          <span className="font-medium flex items-center gap-1.5 text-slate-200">
                            <Cpu className="w-3.5 h-3.5 text-indigo-400" />
                            Advisory Context Window Allocation
                          </span>
                          {rec && (
                            <span className="text-[11px] text-slate-400">
                              Available VRAM: <strong className="text-indigo-300">{Math.round(rec.available_vram_mb)} MB</strong>
                            </span>
                          )}
                        </div>

                        {rec?.estimate_status === "UNAVAILABLE" ? (
                          <div className="p-2.5 bg-slate-900 border border-slate-800 rounded-lg text-xs text-slate-400">
                            {rec.reason || "Context estimation unavailable for this architecture."}
                          </div>
                        ) : rec?.recommendations ? (
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                            {Object.entries(rec.recommendations).map(([ctx, info]) => (
                              <div
                                key={ctx}
                                className="bg-[#0A0D17] border border-white/5 rounded-lg p-2.5 space-y-1.5"
                              >
                                <div className="flex items-center justify-between">
                                  <span className="text-xs font-mono font-medium text-slate-200">
                                    {Number(ctx).toLocaleString()} ctx
                                  </span>
                                  <span
                                    className={`text-[9px] font-semibold px-1.5 py-0.5 rounded border uppercase ${getTierColor(
                                      info.risk_classification
                                    )}`}
                                  >
                                    {info.risk_classification.replace(/_/g, " ")}
                                  </span>
                                </div>
                                <div className="text-[10px] text-slate-400 space-y-0.5 font-mono">
                                  <div className="flex justify-between">
                                    <span>Weights:</span>
                                    <span className="text-slate-300">{info.estimated_weights_gpu_mb ?? info.weights_vram_mb} MB</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>KV Cache:</span>
                                    <span className="text-slate-300">{info.estimated_kv_cache_mb ?? info.kv_cache_mb} MB</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>Compute:</span>
                                    <span className="text-slate-300">{info.estimated_compute_overhead_mb ?? info.compute_buffer_mb} MB</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span>Driver:</span>
                                    <span className="text-slate-300">{info.estimated_driver_reserve_mb ?? 250} MB</span>
                                  </div>
                                  <div className="flex justify-between border-t border-white/5 pt-0.5">
                                    <span>Est. Total:</span>
                                    <span className="text-indigo-300 font-semibold">{info.estimated_vram_total_mb} MB</span>
                                  </div>
                                  {info.measured_model_vram_mb != null && (
                                    <div className="flex justify-between text-emerald-400 border-t border-emerald-500/20 pt-0.5">
                                      <span>Measured:</span>
                                      <span>{info.measured_model_vram_mb} MB</span>
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-xs text-slate-500 py-2 text-center">
                            Calculating advisory memory requirements...
                          </div>
                        )}

                        <div className="flex items-center gap-1.5 text-[10px] text-slate-500 italic pt-1">
                          <Info className="w-3 h-3 shrink-0 text-slate-600" />
                          <span>
                            {rec?.disclaimer ||
                              "Advisory pre-flight estimate based on GQA KV cache. llama.cpp runtime allocation is authoritative."}
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
        <div className="px-6 py-3 border-t border-white/[0.08] bg-[#0A0E1A]/60 flex items-center justify-between text-xs text-slate-500">
          <div className="flex items-center gap-2">
            <span>Storage Directory:</span>
            <code className="text-slate-400 font-mono text-[11px] bg-white/5 px-2 py-0.5 rounded border border-white/5">
              models/
            </code>
          </div>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 text-slate-300 transition"
          >
            Close
          </button>
        </div>
        </>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {modelToDelete && (
        <div className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-md bg-[#0D1220] border border-rose-500/30 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-400">
                <ShieldAlert className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-slate-100">
                  Delete Model File?
                </h3>
                <p className="text-xs text-slate-400">
                  This will permanently delete the file from disk.
                </p>
              </div>
            </div>

            <div className="bg-black/30 border border-white/5 rounded-xl p-3 text-xs space-y-1 font-mono text-slate-300">
              <div className="text-slate-500 text-[11px]">Filename:</div>
              <div className="text-rose-300 font-semibold truncate">{modelToDelete.filename}</div>
              <div className="text-slate-500 text-[11px] pt-1">Size:</div>
              <div>{modelToDelete.file_size_gb} GB ({modelToDelete.file_size_bytes.toLocaleString()} bytes)</div>
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setModelToDelete(null)}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-4 py-2 rounded-xl text-xs font-medium bg-rose-600 hover:bg-rose-500 text-white font-semibold shadow-lg shadow-rose-600/30 transition"
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
