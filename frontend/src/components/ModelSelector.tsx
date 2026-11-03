import React, { useState, useEffect } from "react";
import { ChevronDown, Library, Loader2, RefreshCw } from "lucide-react";
import { EngineStatus, MemoryEstimateResult, ModelContextRecommendation, ModelInfo } from "../types";
import { fetchContextRecommendation, fetchModelEstimate, fetchModels, loadModel, unloadModel } from "../services/api";
import { ModelLibraryModal } from "./ModelLibraryModal";

interface ModelSelectorProps {
  engineStatus: EngineStatus | null;
  onStatusChange: (status: EngineStatus) => void;
  onError: (err: string | null) => void;
}

const CONTEXT_PRESETS = [2048, 4096, 8192, 16384, 32768];

/** Context-size risk tiers from the backend, in plain words. */
const TIER_TEXT: Record<string, string> = {
  LOW_RISK: "safe",
  RECOMMENDED: "recommended",
  HIGH_MEMORY: "high memory",
  LIKELY_CPU_OFFLOAD: "may spill to CPU",
  PROBABLE_OOM: "likely too large",
};

const FIT_TEXT: Record<string, { text: string; cls: string }> = {
  FULL_GPU_SAFE: { text: "fits on the GPU", cls: "text-success" },
  TIGHT_MARGIN: { text: "tight fit", cls: "text-warning" },
  PROBABLE_OOM: { text: "probably too large for the GPU", cls: "text-danger" },
};

const gb = (mb: number, digits = 1) => `${(mb / 1024).toFixed(digits)} GB`;

const selectCls =
  "h-8 bg-canvas border border-line rounded-md px-2 text-[13px] text-fg focus:outline-none focus:border-accent disabled:opacity-50";
const buttonCls =
  "h-8 px-3 rounded-md text-[13px] font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed";

/** Model toolbar: pick, load, switch or unload a model; details on demand. */
export const ModelSelector: React.FC<ModelSelectorProps> = ({ engineStatus, onStatusChange, onError }) => {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [gpuLayers, setGpuLayers] = useState<number>(99);
  const [contextSize, setContextSize] = useState<number>(4096);
  const [estimate, setEstimate] = useState<MemoryEstimateResult | null>(null);
  const [contextRec, setContextRec] = useState<ModelContextRecommendation | null>(null);
  const [loadingModels, setLoadingModels] = useState<boolean>(false);
  const [actionInProgress, setActionInProgress] = useState<boolean>(false);
  const [isLibraryOpen, setIsLibraryOpen] = useState<boolean>(false);
  const [showDetails, setShowDetails] = useState<boolean>(false);

  const state = engineStatus?.state;
  const isModelLoaded = state === "READY" || state === "GENERATING";
  const isStarting = state === "STARTING";
  const isUnloading = state === "UNLOADING";
  const activeModelName = engineStatus?.model_name || "";
  const isDifferentFromActive =
    isModelLoaded && (selectedModel !== activeModelName || contextSize !== engineStatus?.context_size);
  const busy = actionInProgress || isStarting || isUnloading;

  const refreshModelList = async () => {
    setLoadingModels(true);
    try {
      const list = await fetchModels();
      setModels(list);
      // Keep the current choice; otherwise the running model, otherwise the first file.
      if (list.length > 0) setSelectedModel((prev) => prev || activeModelName || list[0].filename);
    } catch (err: unknown) {
      console.warn("Could not fetch models:", err);
    } finally {
      setLoadingModels(false);
    }
  };

  useEffect(() => {
    refreshModelList();
  }, []);

  // Show the model that is actually running whenever it changes (not just the first file).
  useEffect(() => {
    if (isModelLoaded && activeModelName) {
      setSelectedModel(activeModelName);
      if (engineStatus?.context_size) setContextSize(engineStatus.context_size);
    }
  }, [isModelLoaded, activeModelName, engineStatus?.context_size]);

  useEffect(() => {
    if (!selectedModel) {
      setEstimate(null);
      setContextRec(null);
      return;
    }
    let isCurrent = true;
    fetchModelEstimate(selectedModel, gpuLayers, contextSize)
      .then((data) => isCurrent && setEstimate(data))
      .catch(() => isCurrent && setEstimate(null));
    fetchContextRecommendation(selectedModel)
      .then((rec) => {
        if (!isCurrent) return;
        setContextRec(rec);
        if (!isModelLoaded && rec.recommended_context) setContextSize(rec.recommended_context);
      })
      .catch(() => isCurrent && setContextRec(null));
    return () => {
      isCurrent = false;
    };
  }, [selectedModel, gpuLayers, contextSize, isModelLoaded]);

  const handleLoadOrSwitch = async () => {
    if (!selectedModel) return;
    setActionInProgress(true);
    onError(null);
    try {
      onStatusChange(await loadModel(selectedModel, gpuLayers, contextSize));
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : "Failed to load the model");
    } finally {
      setActionInProgress(false);
    }
  };

  const handleUnload = async () => {
    setActionInProgress(true);
    onError(null);
    try {
      onStatusChange(await unloadModel());
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : "Failed to unload the model");
    } finally {
      setActionInProgress(false);
    }
  };

  const statusText = isStarting
    ? "Loading…"
    : isUnloading
    ? "Unloading…"
    : state === "ERROR"
    ? "Engine error"
    : isModelLoaded
    ? `Loaded · ${gb(engineStatus?.vram_allocated_mb ?? 0)} on GPU`
    : "No model loaded";

  const fit = estimate?.estimate_available ? FIT_TEXT[estimate.fit_status] : undefined;

  return (
    <section className="border-b border-line bg-canvas">
      <div className="h-12 px-4 flex items-center gap-2">
        <select
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          disabled={busy}
          className={`${selectCls} min-w-0 flex-1 max-w-[420px]`}
          aria-label="Model"
        >
          {models.length === 0 ? (
            <option value="">No models in the models folder</option>
          ) : (
            models.map((m) => (
              <option key={m.filename} value={m.filename}>
                {m.name} · {m.size_gb} GB
              </option>
            ))
          )}
        </select>

        <select
          value={contextSize}
          onChange={(e) => setContextSize(Number(e.target.value))}
          disabled={busy}
          className={`${selectCls} w-48`}
          aria-label="Context size"
          title="Context window (tokens)"
        >
          {CONTEXT_PRESETS.map((preset) => {
            const tier = contextRec?.recommendations?.[String(preset)]?.risk_classification;
            return (
              <option key={preset} value={preset}>
                {preset.toLocaleString()} tokens{tier && TIER_TEXT[tier] ? ` · ${TIER_TEXT[tier]}` : ""}
              </option>
            );
          })}
        </select>

        {(!isModelLoaded || isDifferentFromActive) && (
          <button
            onClick={handleLoadOrSwitch}
            disabled={!selectedModel || busy}
            className={`${buttonCls} bg-accent hover:bg-accent-hover text-white flex items-center gap-1.5`}
          >
            {(actionInProgress || isStarting) && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {isDifferentFromActive ? "Switch" : "Load"}
          </button>
        )}
        {isModelLoaded && (
          <button
            onClick={handleUnload}
            disabled={busy}
            className={`${buttonCls} border border-line text-fg hover:bg-raised`}
          >
            Unload
          </button>
        )}

        <span className={`text-xs truncate ${state === "ERROR" ? "text-danger" : "text-muted"}`}>{statusText}</span>

        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={refreshModelList}
            disabled={loadingModels || busy}
            className="h-8 w-8 flex items-center justify-center rounded-md text-muted hover:text-fg hover:bg-raised transition-colors disabled:opacity-50"
            title="Refresh the model list"
            aria-label="Refresh the model list"
          >
            <RefreshCw className={`w-4 h-4 ${loadingModels ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={() => setIsLibraryOpen(true)}
            className={`${buttonCls} text-muted hover:text-fg hover:bg-raised flex items-center gap-1.5`}
          >
            <Library className="w-4 h-4" />
            Library
          </button>
          <button
            onClick={() => setShowDetails(!showDetails)}
            aria-expanded={showDetails}
            className={`${buttonCls} text-muted hover:text-fg hover:bg-raised flex items-center gap-1`}
          >
            Details
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showDetails ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>

      {showDetails && (
        <div className="px-4 pb-3 grid grid-cols-1 md:grid-cols-[260px_1fr] gap-x-8 gap-y-3 text-xs">
          <div>
            <label htmlFor="gpu-layers" className="flex justify-between text-muted mb-1">
              <span>Layers on GPU</span>
              <span className="text-fg tabular-nums">
                {gpuLayers >= (estimate?.total_layers ?? 99) ? "all" : gpuLayers}
              </span>
            </label>
            <input
              id="gpu-layers"
              type="range"
              min={0}
              max={estimate?.total_layers || 99}
              value={gpuLayers}
              onChange={(e) => setGpuLayers(Number(e.target.value))}
              disabled={busy}
              className="w-full accent-[rgb(var(--accent))] disabled:opacity-50"
            />
          </div>

          <div className="space-y-1 text-muted">
            {estimate?.estimate_available ? (
              <p>
                Estimated GPU memory{" "}
                <span className="text-fg tabular-nums">{gb(estimate.total_estimated_vram_mb)}</span> (model{" "}
                {gb(estimate.weights_gpu_mb)}, context {gb(estimate.kv_cache_gpu_mb, 2)}), {gb(estimate.vram_headroom_mb, 2)} left
                {fit && (
                  <>
                    {" — "}
                    <span className={fit.cls}>{fit.text}</span>
                  </>
                )}
                .
              </p>
            ) : (
              <p>{estimate?.reason || "No memory estimate for this model."}</p>
            )}
            {isModelLoaded && (
              <p>
                Running <span className="text-fg">{activeModelName}</span> ·{" "}
                <span className="tabular-nums">
                  {engineStatus?.gpu_layers_offloaded} of {engineStatus?.gpu_layers_requested} layers on GPU ·{" "}
                  {(engineStatus?.vram_allocated_mb ?? 0).toFixed(0)} MiB · {engineStatus?.context_size?.toLocaleString()} tokens
                </span>
              </p>
            )}
            {estimate?.disclaimer && <p className="text-subtle">{estimate.disclaimer}</p>}
          </div>
        </div>
      )}

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
    </section>
  );
};
