import React, { useState } from "react";
import { AlertTriangle, Folder, SquareTerminal, Wrench, X } from "lucide-react";
import { AGENT_CONTEXT, AGENT_MIN_CONTEXT, SMALL_AGENT_MODEL_B, modelSizeB } from "../services/agent";

interface AgentBarProps {
  enabled: boolean;
  workspace: string | null;
  modelName?: string | null;
  disabled?: boolean;
  /** "Approve all changes" is on for this chat. */
  autoApprove?: boolean;
  onTurnOffAutoApprove?: () => void;
  /** Terminal access is on for this chat. */
  terminal?: boolean;
  onToggleTerminal?: (enabled: boolean) => void;
  /** Context window of the loaded model; agent work needs at least AGENT_MIN_CONTEXT. */
  contextSize?: number;
  onUseBiggerContext?: () => void;
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
  autoApprove,
  onTurnOffAutoApprove,
  terminal,
  onToggleTerminal,
  contextSize,
  onUseBiggerContext,
  onToggle,
  onChooseWorkspace,
  onClearWorkspace,
}) => {
  const size = modelSizeB(modelName);
  const smallModel = enabled && size !== null && size < SMALL_AGENT_MODEL_B;
  const [asking, setAsking] = useState(false); // the "allow terminal access?" question is open
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
            ? "bg-accent/15 border border-accent/40 text-link font-semibold"
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

      {enabled && workspace && (
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(terminal)}
          disabled={disabled}
          onClick={() => (terminal ? onToggleTerminal?.(false) : setAsking(true))}
          title={
            terminal
              ? "Terminal is on: the model may propose PowerShell commands in the workspace. You approve every command before it runs. Click to turn off."
              : "Let the model propose terminal commands (builds, tests, git). Off by default; you approve every command."
          }
          className={`${toolCls} ${
            terminal
              ? "bg-warning/10 border border-warning/30 text-warning font-semibold"
              : "text-muted hover:text-fg hover:bg-raised/70 border border-transparent"
          }`}
        >
          <SquareTerminal className="w-3.5 h-3.5" />
          Terminal{terminal ? " on" : ""}
        </button>
      )}

      {enabled && workspace && asking && !terminal && (
        <span
          role="alertdialog"
          aria-label="Allow terminal access"
          className="flex flex-wrap items-center gap-2 px-2.5 py-1 rounded-xl bg-warning/10 border border-warning/30 text-[12px] text-fg"
        >
          <span className="max-w-[360px]">
            Let the model propose PowerShell commands in this folder? You see and approve every command first; they run with
            your own permissions.
          </span>
          <button
            type="button"
            onClick={() => {
              setAsking(false);
              onToggleTerminal?.(true);
            }}
            className="h-6 px-2.5 rounded-md bg-accent text-white text-xs font-medium hover:bg-accent-hover transition-colors"
          >
            Allow
          </button>
          <button
            type="button"
            onClick={() => setAsking(false)}
            className="h-6 px-2.5 rounded-md border border-line text-xs text-fg hover:bg-raised transition-colors"
          >
            Cancel
          </button>
        </span>
      )}

      {enabled && autoApprove && (
        <button
          type="button"
          onClick={onTurnOffAutoApprove}
          className={`${toolCls} bg-warning/10 border border-warning/30 text-warning`}
          title="File changes in this chat are written without asking (each can be undone). Click to ask again."
        >
          Auto-approve on
          <X className="w-3 h-3" />
        </button>
      )}

      {enabled && contextSize !== undefined && contextSize > 0 && contextSize < AGENT_MIN_CONTEXT && (
        <span className="flex flex-wrap items-center gap-2 px-2.5 py-1 rounded-xl bg-warning/10 border border-warning/30 text-[12px] text-warning">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>
            The context is {contextSize.toLocaleString()}: too small for agent work (the model forgets the task).
          </span>
          {onUseBiggerContext && (
            <button
              type="button"
              disabled={disabled}
              onClick={onUseBiggerContext}
              className="h-6 px-2.5 rounded-md bg-accent text-white text-xs font-medium hover:bg-accent-hover transition-colors disabled:opacity-50"
            >
              Reload with {AGENT_CONTEXT.toLocaleString()}
            </button>
          )}
        </span>
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
