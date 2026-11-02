import React, { useState, useEffect } from "react";
import {
  Layers,
  Play,
  Square,
  AlertTriangle,
  CheckCircle,
  HelpCircle,
  RefreshCw,
  HardDrive,
  ArrowRightLeft,
  Activity,
  Cpu,
} from "lucide-react";
import {
  EngineStatus,
  MemoryEstimateResult,
  ModelContextRecommendation,
  ModelInfo,
} from "../types";
import {
  fetchContextRecommendation,
  fetchModelEstimate,
  fetchModels,
  loadModel,
  unloadModel,
} from "../services/api";
import { ModelLibraryModal } from "./ModelLibraryModal";

interface ModelSelectorProps {
  engineStatus: EngineStatus | null;
  onStatusChange: (status: EngineStatus) => void;
  onError: (err: string | null) => void;
}

export const ModelSelector: React.FC<ModelSelectorProps> = ({
  engineStatus,
  onStatusChange,
  onError,
}) => {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [gpuLayers, setGpuLayers] = useState<number>(99);
  const [contextSize, setContextSize] = useState<number>(4096);
  const [estimate, setEstimate] = useState<MemoryEstimateResult | null>(null);
  const [contextRec, setContextRec] = useState<ModelContextRecommendation | null>(null);
  const [loadingModels, setLoadingModels] = useState<boolean>(false);
  const [actionInProgress, setActionInProgress] = useState<boolean>(false);
  const [isLibraryOpen, setIsLibraryOpen] = useState<boolean>(false);

  const isModelLoaded =
    engineStatus?.state === "READY" || engineStatus?.state === "GENERATING";
  const isStarting = engineStatus?.state === "STARTING";
  const isUnloading = engineStatus?.state === "UNLOADING";

  // Check if current user selection differs from active engine
  const activeModelName = engineStatus?.model_name || "";
  const isDifferentFromActive =
    isModelLoaded &&
    (selectedModel !== activeModelName || contextSize !== engineStatus?.context_size);

  const refreshModelList = async () => {
    setLoadingModels(true);
    try {
      const list = await fetchModels();
      setModels(list);
      if (list.length > 0 && !selectedModel) {
        setSelectedModel(list[0].filename);
      }
    } catch (err: unknown) {
      console.warn("Could not fetch models:", err);
    } finally {
      setLoadingModels(false);
    }
  };

  useEffect(() => {
    refreshModelList();
  }, []);

  // Update estimate & dynamic context recommendation whenever selected model or parameters change
  useEffect(() => {
    if (!selectedModel) {
      setEstimate(null);
      setContextRec(null);
      return;
    }
    let isCurrent = true;

    // Pre-flight memory estimate
    fetchModelEstimate(selectedModel, gpuLayers, contextSize)
      .then((data) => {
        if (isCurrent) setEstimate(data);
      })
      .catch((err) => {
        console.debug("Estimate fetch notice:", err);
        if (isCurrent) setEstimate(null);
      });

    // Architecture-aware dynamic context tiers
    fetchContextRecommendation(selectedModel)
      .then((rec) => {
        if (isCurrent) {
          setContextRec(rec);
          // If first time loading model and not active, suggest recommended context
          if (!isModelLoaded && rec.recommended_context) {
            setContextSize(rec.recommended_context);
          }
        }
      })
      .catch((err) => {
        console.debug("Context recommendation fetch notice:", err);
        if (isCurrent) setContextRec(null);
      });

    return () => {
      isCurrent = false;
    };
  }, [selectedModel, gpuLayers, contextSize, isModelLoaded]);

  const handleLoadOrSwitch = async () => {
    if (!selectedModel) return;
    setActionInProgress(true);
    onError(null);
    try {
      const newStatus = await loadModel(selectedModel, gpuLayers, contextSize);
      onStatusChange(newStatus);
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : "Failed to load/switch model");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleUnload = async () => {
    setActionInProgress(true);
    onError(null);
    try {
      const newStatus = await unloadModel();
      onStatusChange(newStatus);
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : "Failed to unload model");
    } finally {
      setActionInProgress(false);
    }
  };

  const getTierBadge = (tier?: string) => {
    switch (tier) {
      case "LOW_RISK":
        return { text: "LOW RISK", bg: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" };
      case "RECOMMENDED":
        return { text: "RECOMMENDED", bg: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20" };
      case "HIGH_MEMORY":
        return { text: "HIGH VRAM", bg: "bg-amber-500/10 text-amber-400 border-amber-500/20" };
      case "LIKELY_CPU_OFFLOAD":
        return { text: "CPU OFFLOAD", bg: "bg-purple-500/10 text-purple-400 border-purple-500/20" };
      case "PROBABLE_OOM":
        return { text: "OOM RISK", bg: "bg-rose-500/10 text-rose-400 border-rose-500/20" };
      default:
        return { text: "ADVISORY", bg: "bg-slate-800 text-slate-400 border-slate-700" };
    }
  };

  const standardPresets = [2048, 4096, 8192, 16384, 32768];

  return (
    <div className="glass-panel p-5 rounded-xl space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-wide text-slate-200 flex items-center gap-2">
          <Layers className="w-4 h-4 text-indigo-glow" />
          Model Management & Dynamic Context Allocation
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsLibraryOpen(true)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 text-slate-300 border border-white/10 transition"
            title="Open Model Library"
          >
            <HardDrive className="w-3.5 h-3.5 text-indigo-400" />
            <span>Model Library</span>
          </button>
          <button
            onClick={refreshModelList}
            disabled={loadingModels || actionInProgress}
            className="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-white/5 transition"
            title="Refresh model list"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loadingModels ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Model dropdown */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Target GGUF Model</span>
            {contextRec?.attention_type && (
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
                {contextRec.attention_type}
              </span>
            )}
          </div>
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            disabled={actionInProgress || isStarting}
            className="w-full bg-obsidian-900 border border-white/10 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-electric transition disabled:opacity-50"
          >
            {models.length === 0 ? (
              <option value="">No models in models/ directory</option>
            ) : (
              models.map((m) => (
                <option key={m.filename} value={m.filename}>
                  {m.name} ({m.size_gb} GB)
                </option>
              ))
            )}
          </select>
        </div>

        {/* GPU Layers Slider */}
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs text-slate-400">
            <span>GPU Offload Layers</span>
            <span className="font-mono text-indigo-glow font-medium">{gpuLayers}</span>
          </div>
          <input
            type="range"
            min={0}
            max={estimate?.total_layers ? estimate.total_layers : 99}
            value={gpuLayers}
            onChange={(e) => setGpuLayers(Number(e.target.value))}
            disabled={actionInProgress || isStarting}
            className="w-full accent-indigo-electric disabled:opacity-50"
          />
        </div>

        {/* Context Size Selection & Presets */}
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs text-slate-400">
            <span>Context Window</span>
            {contextRec?.recommendations && (
              <span
                className={`text-[10px] px-1.5 py-0.2 rounded border font-mono ${
                  getTierBadge(contextRec.recommendations[String(contextSize)]?.risk_classification).bg
                }`}
              >
                {getTierBadge(contextRec.recommendations[String(contextSize)]?.risk_classification).text}
              </span>
            )}
          </div>
          <select
            value={contextSize}
            onChange={(e) => setContextSize(Number(e.target.value))}
            disabled={actionInProgress || isStarting}
            className="w-full bg-obsidian-900 border border-white/10 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-electric transition disabled:opacity-50"
          >
            {standardPresets.map((preset) => {
              const tier = contextRec?.recommendations?.[String(preset)]?.risk_classification;
              const badge = tier ? ` [${tier.replace("_", " ")}]` : "";
              return (
                <option key={preset} value={preset}>
                  {preset.toLocaleString()} tokens{badge}
                </option>
              );
            })}
          </select>
        </div>
      </div>

      {/* Advisory Pre-flight Estimator Box */}
      {estimate && (
        <div className="bg-obsidian-900/80 border border-white/5 rounded-lg p-3 space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-medium text-slate-300 flex items-center gap-1.5">
              <Cpu className="w-3.5 h-3.5 text-indigo-400" />
              Advisory Pre-flight Memory Estimation:
            </span>
            {estimate.estimate_available ? (
              estimate.fit_status === "FULL_GPU_SAFE" ? (
                <span className="flex items-center gap-1 text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                  <CheckCircle className="w-3 h-3" /> Full GPU Safe
                </span>
              ) : estimate.fit_status === "TIGHT_MARGIN" ? (
                <span className="flex items-center gap-1 text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                  <AlertTriangle className="w-3 h-3" /> Tight VRAM Margin
                </span>
              ) : (
                <span className="flex items-center gap-1 text-rose-400 bg-rose-500/10 px-2 py-0.5 rounded border border-rose-500/20">
                  <AlertTriangle className="w-3 h-3" /> Probable CUDA OOM
                </span>
              )
            ) : (
              <span className="flex items-center gap-1 text-slate-400 bg-slate-800 px-2 py-0.5 rounded">
                <HelpCircle className="w-3 h-3" /> Advisory Unavailable
              </span>
            )}
          </div>

          {estimate.estimate_available ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] text-slate-400 pt-1">
              <div>
                Weights VRAM:{" "}
                <span className="font-mono text-slate-200">
                  {(estimate.weights_gpu_mb / 1024).toFixed(1)} GB
                </span>
              </div>
              <div>
                KV Cache:{" "}
                <span className="font-mono text-slate-200">
                  {(estimate.kv_cache_gpu_mb / 1024).toFixed(2)} GB
                </span>
              </div>
              <div>
                Est. Total VRAM:{" "}
                <span className="font-mono text-slate-200">
                  {(estimate.total_estimated_vram_mb / 1024).toFixed(1)} GB
                </span>
              </div>
              <div>
                VRAM Headroom:{" "}
                <span
                  className={`font-mono font-bold ${
                    estimate.vram_headroom_mb >= 500 ? "text-emerald-400" : "text-amber-400"
                  }`}
                >
                  {(estimate.vram_headroom_mb / 1024).toFixed(2)} GB
                </span>
              </div>
            </div>
          ) : (
            <p className="text-slate-400 text-[11px]">{estimate.reason}</p>
          )}

          <p className="text-[10px] text-slate-500 italic border-t border-white/5 pt-1.5">
            {estimate.disclaimer}
          </p>
        </div>
      )}

      {/* Authoritative Runtime Telemetry Card (When loaded) */}
      {isModelLoaded && (
        <div className="bg-[#0A1020]/90 border border-indigo-500/20 rounded-lg p-3 space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-indigo-300 flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-indigo-400 animate-pulse" />
              Authoritative Runtime Telemetry (Active Engine)
            </span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">
              PID: {engineStatus?.pid ?? "N/A"} | Port: {engineStatus?.port ?? 18080}
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] text-slate-300 pt-1">
            <div>
              Active Model:{" "}
              <span className="font-mono text-slate-100 font-medium">
                {engineStatus?.model_name || "Unknown"}
              </span>
            </div>
            <div>
              GPU Offload:{" "}
              <span className="font-mono text-indigo-glow font-bold">
                {engineStatus?.gpu_layers_offloaded} / {engineStatus?.gpu_layers_requested} layers
              </span>
            </div>
            <div>
              Authoritative VRAM:{" "}
              <span className="font-mono text-slate-100 font-medium">
                {engineStatus?.vram_allocated_mb?.toFixed(1) ?? "0.0"} MiB
              </span>
            </div>
            <div>
              Active Context:{" "}
              <span className="font-mono text-slate-100 font-medium">
                {engineStatus?.context_size?.toLocaleString()} tokens
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Action buttons: Load / Switch / Unload */}
      <div className="flex items-center justify-between gap-3 pt-2">
        <div className="text-xs text-slate-400">
          {isDifferentFromActive && (
            <span className="text-indigo-300 flex items-center gap-1 text-[11px]">
              <ArrowRightLeft className="w-3 h-3 text-indigo-400" />
              Different configuration selected. Click &quot;Switch Model&quot; to cleanly transition.
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          {isModelLoaded && (
            <button
              onClick={handleUnload}
              disabled={actionInProgress || isUnloading}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40 text-xs font-medium transition disabled:opacity-50"
            >
              <Square className="w-3.5 h-3.5" />
              {isUnloading ? "Unloading..." : "Unload Model"}
            </button>
          )}

          {(!isModelLoaded || isDifferentFromActive) && (
            <button
              onClick={handleLoadOrSwitch}
              disabled={!selectedModel || actionInProgress || isStarting}
              className={`flex items-center gap-2 px-5 py-2 rounded-lg text-white text-xs font-semibold shadow-indigo-glow transition disabled:opacity-50 ${
                isDifferentFromActive
                  ? "bg-amber-600 hover:bg-amber-500 shadow-amber-600/30"
                  : "bg-indigo-electric hover:bg-indigo-deep"
              }`}
            >
              {isDifferentFromActive ? (
                <>
                  <ArrowRightLeft className="w-3.5 h-3.5" />
                  {isStarting ? "Switching model..." : "Switch Model"}
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5 fill-current" />
                  {isStarting ? "Starting llama-server..." : "Load Model"}
                </>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Model Library Modal */}
      <ModelLibraryModal
        isOpen={isLibraryOpen}
        onClose={() => {
          setIsLibraryOpen(false);
          refreshModelList();
        }}
        engineStatus={engineStatus}
        onStatusChange={onStatusChange}
        onModelSelected={(filename) => {
          setSelectedModel(filename);
          refreshModelList();
        }}
      />
    </div>
  );
};

