import React, { useState, useEffect } from "react";
import { ChevronDown, Library, Loader2, RefreshCw, SlidersHorizontal } from "lucide-react";
import { EngineStatus, MemoryEstimateResult, ModelContextRecommendation, ModelInfo } from "../types";
import { fetchContextRecommendation, fetchModelEstimate, fetchModels, loadModel, unloadModel } from "../services/api";
import { ModelLibraryModal } from "./ModelLibraryModal";

export type LibraryTab = "LOCAL" | "HF" | "DOWNLOADS";

interface ModelSelectorProps {
  engineStatus: EngineStatus | null;
  onStatusChange: (status: EngineStatus) => void;
  onError: (err: string | null) => void;
  /** Which library tab is open (null = closed); owned by the app so other places can open it. */
  libraryTab: LibraryTab | null;
  onLibraryTabChange: (tab: LibraryTab | null) => void;
  /** Extra controls at the right end of the header row. */
  trailing?: React.ReactNode;
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

export const pillCls =
  "h-9 px-3 flex items-center gap-1.5 rounded-xl bg-panel/70 border border-line/60 text-[13px] font-medium text-fg hover:bg-raised/80 hover:border-accent/40 shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed";
const selectCls =
  "h-9 rounded-xl bg-panel/80 border border-line/60 px-3 text-[13px] font-medium text-fg focus:outline-none focus:border-accent/70 focus:ring-1 focus:ring-accent/30 shadow-sm transition-all disabled:opacity-50";

/** Header row of the main panel: pick, load, switch or unload a model; details on demand. */
export const ModelSelector: React.FC<ModelSelectorProps> = ({
  engineStatus,
  onStatusChange,
  onError,
  libraryTab,
  onLibraryTabChange,
  trailing,
}) => {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [gpuLayers, setGpuLayers] = useState<number>(99);
  const [contextSize, setContextSize] = useState<number>(4096);
  const [estimate, setEstimate] = useState<MemoryEstimateResult | null>(null);
  const [contextRec, setContextRec] = useState<ModelContextRecommendation | null>(null);
  const [loadingModels, setLoadingModels] = useState<boolean>(false);
  const [actionInProgress, setActionInProgress] = useState<boolean>(false);
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
    ? `${gb(engineStatus?.vram_allocated_mb ?? 0)} on GPU`
    : "No model loaded";

  const fit = estimate?.estimate_available ? FIT_TEXT[estimate.fit_status] : undefined;

  return (
    <section className="shrink-0">
      <div className="h-14 px-4 flex items-center gap-1.5 xl:gap-2">
        <select
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          disabled={busy}
          className={`${selectCls} min-w-[150px] w-[320px] shrink`}
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
          className={`${selectCls} w-[180px] shrink-0`}
          aria-label="Context size"
          title="Context window (tokens)"
        >
          {CONTEXT_PRESETS.map((preset) => {
            const tier = contextRec?.recommendations?.[String(preset)]?.risk_classification;
            return (
              <option key={preset} value={preset}>
                {preset.toLocaleString()} context{tier && TIER_TEXT[tier] ? ` · ${TIER_TEXT[tier]}` : ""}
              </option>
            );
          })}
        </select>

        {(!isModelLoaded || isDifferentFromActive) && (
          <button
            onClick={handleLoadOrSwitch}
            disabled={!selectedModel || busy}
            className="h-9 px-4 flex items-center gap-1.5 rounded-xl btn-violet text-white text-[13px] font-medium hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {(actionInProgress || isStarting) && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {isDifferentFromActive ? "Switch" : "Load"}
          </button>
        )}
        {isModelLoaded && (
          <button onClick={handleUnload} disabled={busy} className={pillCls}>
            Unload
          </button>
        )}

        <span className={`hidden 2xl:inline text-xs font-medium truncate ${state === "ERROR" ? "text-danger" : "text-muted"}`}>
          {statusText}
        </span>

        <div className="ml-auto flex items-center gap-1.5 xl:gap-2">
          <button
            onClick={refreshModelList}
            disabled={loadingModels || busy}
            className="h-9 w-9 flex items-center justify-center rounded-xl bg-panel/70 border border-line/60 text-muted hover:text-fg hover:bg-raised/80 hover:border-accent/40 transition-all disabled:opacity-50 shadow-sm"
            title="Refresh the model list"
            aria-label="Refresh the model list"
          >
            <RefreshCw className={`w-4 h-4 ${loadingModels ? "animate-spin text-accent" : ""}`} />
          </button>
          <button onClick={() => onLibraryTabChange("LOCAL")} className={pillCls} title="Model library">
            <span className="hidden xl:inline">Library</span>
            <Library className="w-4 h-4 text-accent/80" />
          </button>
          <button onClick={() => setShowDetails(!showDetails)} aria-expanded={showDetails} className={pillCls} title="Model details">
            <span className="hidden xl:inline">Details</span>
            {showDetails ? <ChevronDown className="w-4 h-4 text-accent/80 rotate-180 transition-transform" /> : <SlidersHorizontal className="w-4 h-4 text-muted" />}
          </button>
          {trailing}
        </div>
      </div>

      {showDetails && (
        <div className="mx-4 mb-2 p-4 rounded-2xl bg-panel/70 border border-line/60 shadow-lg grid grid-cols-1 md:grid-cols-[240px_1fr] gap-x-8 gap-y-3 text-xs">
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
              className="w-full"
            />
          </div>

          <div className="space-y-1 text-muted">
            {estimate?.estimate_available ? (
              <p>
                Estimated GPU memory <span className="text-fg tabular-nums">{gb(estimate.total_estimated_vram_mb)}</span>{" "}
                (model {gb(estimate.weights_gpu_mb)}, context {gb(estimate.kv_cache_gpu_mb, 2)}),{" "}
                {gb(estimate.vram_headroom_mb, 2)} left
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
        isOpen={libraryTab !== null}
        initialTab={libraryTab ?? "LOCAL"}
        onClose={() => {
          onLibraryTabChange(null);
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
