import React from "react";
import { AlertTriangle, Folder, X } from "lucide-react";
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
    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs select-none">
      <label className={`flex items-center gap-2 text-muted ${disabled ? "opacity-50" : "cursor-pointer hover:text-fg"}`}>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          disabled={disabled}
          onClick={() => onToggle(!enabled)}
          title={enabled ? "Agent mode is on: the model may use tools" : "Turn on agent mode"}
          className={`relative w-7 h-4 rounded-full transition-colors ${enabled ? "bg-accent" : "bg-line"}`}
        >
          <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-[left] ${enabled ? "left-3.5" : "left-0.5"}`} />
        </button>
        <span className={enabled ? "text-fg" : ""}>Agent mode</span>
      </label>

      {enabled && (
        <>
          {workspace ? (
            <span className="flex items-center gap-1 h-6 pl-1.5 pr-0.5 rounded-md border border-line text-fg max-w-[260px]" title={workspace}>
              <Folder className="w-3.5 h-3.5 text-muted shrink-0" />
              <span className="truncate">{folderName}</span>
              <button
                type="button"
                disabled={disabled}
                onClick={onClearWorkspace}
                className="p-0.5 rounded text-subtle hover:text-fg disabled:opacity-50"
                title="Close the folder (file tools off)"
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
            className="h-6 px-1.5 rounded-md text-link hover:bg-raised disabled:opacity-50"
          >
            {workspace ? "Change folder" : "Choose folder"}
          </button>
          <span className="text-subtle">
            {workspace
              ? "Reads files here; asks before every change. Never deletes or runs anything."
              : "No folder: time, calculator and chat search only."}
          </span>
        </>
      )}

      {smallModel && (
        <span className="flex items-center gap-1 text-warning" title="Measured in M5 Phase A">
          <AlertTriangle className="w-3.5 h-3.5" />
          Small models often misuse tools; Qwen3.5 4B or Gemma 4 E4B work much better.
        </span>
      )}
    </div>
  );
};
