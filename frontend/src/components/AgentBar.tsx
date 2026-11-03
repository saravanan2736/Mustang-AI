import React from "react";
import { AlertTriangle, FolderOpen, Wrench, X } from "lucide-react";
import { SMALL_AGENT_MODEL_B, modelSizeB } from "../services/agent";

interface AgentBarProps {
  enabled: boolean;
  workspace: string | null;
  modelName?: string | null;
  disabled?: boolean;
  onToggle: (enabled: boolean) => void;
  onChooseWorkspace: () => void;
  onClearWorkspace: () => void;
}

/** Agent mode controls for the open chat: on/off, the workspace folder, and model advice. */
export const AgentBar: React.FC<AgentBarProps> = ({
  enabled,
  workspace,
  modelName,
  disabled,
  onToggle,
  onChooseWorkspace,
  onClearWorkspace,
}) => {
  const size = modelSizeB(modelName);
  const smallModel = enabled && size !== null && size < SMALL_AGENT_MODEL_B;
  const folderName = workspace ? workspace.split(/[\\/]/).filter(Boolean).pop() : null;

  return (
    <div className="flex flex-wrap items-center gap-2 mb-2 px-1 text-[11px] select-none">
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        disabled={disabled}
        onClick={() => onToggle(!enabled)}
        title={enabled ? "Agent mode is on: the model may use read-only tools" : "Turn on agent mode"}
        className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border transition disabled:opacity-40 ${
          enabled
            ? "bg-indigo-600/20 border-indigo-500/50 text-indigo-200"
            : "bg-white/[0.03] border-white/[0.08] text-slate-400 hover:text-slate-200"
        }`}
      >
        <Wrench className="w-3.5 h-3.5" />
        <span className="font-medium">Agent mode</span>
        <span className={`ml-0.5 w-6 h-3.5 rounded-full relative transition ${enabled ? "bg-indigo-500" : "bg-slate-600"}`}>
          <span className={`absolute top-0.5 w-2.5 h-2.5 rounded-full bg-white transition-all ${enabled ? "left-3" : "left-0.5"}`} />
        </span>
      </button>

      {enabled && (
        <>
          {workspace ? (
            <span
              className="flex items-center gap-1 pl-2 pr-1 py-1 rounded-lg bg-white/[0.04] border border-white/[0.08] text-slate-300 max-w-[260px]"
              title={workspace}
            >
              <FolderOpen className="w-3.5 h-3.5 text-amber-300 shrink-0" />
              <span className="truncate">{folderName}</span>
              <button
                type="button"
                disabled={disabled}
                onClick={onClearWorkspace}
                className="p-0.5 rounded hover:bg-white/10 text-slate-500 hover:text-slate-200 disabled:opacity-40"
                title="Close the workspace folder (file tools off)"
                aria-label="Close workspace folder"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ) : null}
          <button
            type="button"
            disabled={disabled}
            onClick={onChooseWorkspace}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.06] transition disabled:opacity-40"
            title="Choose the folder the agent may read"
          >
            <FolderOpen className="w-3.5 h-3.5" />
            <span>{workspace ? "Change folder" : "Choose workspace folder"}</span>
          </button>
          <span className="text-slate-500">
            {workspace ? "Can read files in this folder. Never changes, deletes or runs anything." : "No folder: time, calculator and chat search only."}
          </span>
        </>
      )}

      {smallModel && (
        <span className="flex items-center gap-1 text-amber-300/90" title="Measured in M5 Phase A">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>Small models often misuse tools; Qwen3.5 4B, Gemma 4 E4B or larger work much better.</span>
        </span>
      )}
    </div>
  );
};
