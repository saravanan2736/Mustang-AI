import React from "react";
import { AlertTriangle, Folder, Wrench, X } from "lucide-react";
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

const toolCls =
  "h-8 px-2.5 flex items-center gap-1.5 rounded-xl text-[13px] font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed";

/** Agent mode controls for the prompt card's toolbar: on/off, the workspace folder, model advice. */
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
    <>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        disabled={disabled}
        onClick={() => onToggle(!enabled)}
        title={
          enabled
            ? "Agent mode is on: the model may use tools. It asks before every file change and never deletes or runs anything."
            : "Turn on agent mode: let the model use tools"
        }
        className={`${toolCls} ${
          enabled
            ? "bg-accent/15 border border-accent/40 text-link shadow-glow-sm font-semibold"
            : "text-muted hover:text-fg hover:bg-raised/70 border border-transparent"
        }`}
      >
        <Wrench className={`w-3.5 h-3.5 ${enabled ? "text-accent" : "text-subtle"}`} />
        Agent{enabled ? " on" : ""}
      </button>

      {enabled && (
        <>
          <span className="w-px h-4 bg-line/60 mx-1" aria-hidden />
          {workspace ? (
            <span className="flex items-center bg-raised/50 border border-line/60 rounded-xl px-1" title={workspace}>
              <button type="button" disabled={disabled} onClick={onChooseWorkspace} className={`${toolCls} text-fg max-w-[200px]`}>
                <Folder className="w-3.5 h-3.5 text-accent shrink-0" />
                <span className="truncate">{folderName}</span>
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={onClearWorkspace}
                className="h-6 w-6 flex items-center justify-center rounded-lg text-subtle hover:text-fg hover:bg-panel disabled:opacity-50"
                title="Close the folder (file tools off)"
                aria-label="Close workspace folder"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </span>
          ) : (
            <button
              type="button"
              disabled={disabled}
              onClick={onChooseWorkspace}
              className={`${toolCls} text-muted hover:text-fg hover:bg-raised/70 border border-transparent`}
              title="Choose the folder the agent may read (and change with your approval)"
            >
              <Folder className="w-3.5 h-3.5 text-subtle" />
              Choose folder
            </button>
          )}
        </>
      )}

      {smallModel && (
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-warning/10 border border-warning/30 text-[11px] text-warning" title="Measured in M5 Phase A">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          Small models misuse tools; try Qwen3.5 4B or Gemma 4 E4B.
        </span>
      )}
    </>
  );
};
