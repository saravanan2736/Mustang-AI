import React from "react";
import { Ban, CheckCircle2, ChevronRight, FilePen, Loader2, RotateCcw, XCircle } from "lucide-react";
import { AgentChange, AgentStep } from "../types";
import { CHANGE_TOOLS, STOP_REASON_TEXT } from "../services/agent";

const TOOL_LABELS: Record<string, string> = {
  get_current_time: "Checked the time",
  calculator: "Calculated",
  list_files: "Listed files",
  read_file: "Read file",
  search_files: "Searched files",
  search_history: "Searched this chat",
  create_file: "Create file",
  edit_file: "Edit file",
};

/** The one argument worth showing in the card's title line. */
function argumentSummary(raw: string): string {
  try {
    const args = JSON.parse(raw || "{}");
    const value = args.path ?? args.query ?? args.expression;
    return typeof value === "string" ? value : "";
  } catch {
    return raw.slice(0, 60);
  }
}

/** Unified diff with added lines green and removed lines red. */
const DiffView: React.FC<{ diff: string }> = ({ diff }) => (
  <pre className="max-h-64 overflow-auto rounded-md bg-canvas border border-line p-2 font-mono text-xs leading-snug">
    {diff.split("\n").map((line, i) => {
      const cls = line.startsWith("+++") || line.startsWith("---")
        ? "text-subtle"
        : line.startsWith("+")
        ? "text-success bg-success/10"
        : line.startsWith("-")
        ? "text-danger bg-danger/10"
        : line.startsWith("@@")
        ? "text-subtle"
        : "text-muted";
      return (
        <div key={i} className={`whitespace-pre-wrap break-words ${cls}`}>
          {line || " "}
        </div>
      );
    })}
  </pre>
);

interface ToolStepsProps {
  steps: AgentStep[];
  stopReason?: string | null;
  /** Journal state of this chat's file changes, by call id (for Undo). */
  changes?: Record<string, AgentChange>;
  onApprove?: (callId: string, approved: boolean) => void;
  onUndo?: (callId: string) => void;
}

const StatusIcon: React.FC<{ status: AgentStep["status"] }> = ({ status }) =>
  status === "running" ? (
    <Loader2 className="w-3.5 h-3.5 text-muted animate-spin shrink-0" />
  ) : status === "awaiting" ? (
    <FilePen className="w-3.5 h-3.5 text-warning shrink-0" />
  ) : status === "ok" ? (
    <CheckCircle2 className="w-3.5 h-3.5 text-success shrink-0" />
  ) : status === "denied" ? (
    <Ban className="w-3.5 h-3.5 text-subtle shrink-0" />
  ) : (
    <XCircle className="w-3.5 h-3.5 text-danger shrink-0" />
  );

/** Tool steps of an agent-mode reply, one collapsible card per call. */
export const ToolSteps: React.FC<ToolStepsProps> = ({ steps, stopReason, changes, onApprove, onUndo }) => (
  <div className="space-y-1.5 mb-3 not-prose">
    {steps.map((s) => {
      const summary = argumentSummary(s.arguments);
      const isChange = CHANGE_TOOLS.has(s.name);
      const change = changes?.[s.call_id];

      if (s.status === "awaiting") {
        // A proposed file change: always open, with the diff and the two answers.
        return (
          <div key={s.call_id} className="rounded-md border border-warning/50 bg-panel p-3 space-y-2">
            {s.note && <p className="text-[13px] text-muted">{s.note}</p>}
            <div className="flex items-center gap-2 text-[13px] text-fg">
              <StatusIcon status={s.status} />
              <span className="font-semibold">{s.summary || `${TOOL_LABELS[s.name] ?? s.name} ${summary}`}</span>
              <span className="ml-auto text-xs text-warning">waiting for you</span>
            </div>
            {s.diff && <DiffView diff={s.diff} />}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onApprove?.(s.call_id, true)}
                className="h-7 px-3 rounded-md bg-accent hover:bg-accent-hover text-white text-xs font-medium"
              >
                Approve change
              </button>
              <button
                type="button"
                onClick={() => onApprove?.(s.call_id, false)}
                className="h-7 px-3 rounded-md border border-line hover:bg-raised text-fg text-xs font-medium"
              >
                Deny
              </button>
              <span className="text-xs text-subtle">Nothing is written unless you approve.</span>
            </div>
          </div>
        );
      }

      return (
        <div key={s.call_id}>
          {s.note && <p className="text-[13px] text-muted mb-1">{s.note}</p>}
          <details className="group/step rounded-md border border-line bg-panel">
            <summary className="flex items-center gap-2 h-8 px-2.5 cursor-pointer list-none text-xs text-fg select-none">
              <ChevronRight className="w-3 h-3 text-subtle transition-transform group-open/step:rotate-90 shrink-0" />
              <StatusIcon status={s.status} />
              <span className="font-medium">
                {TOOL_LABELS[s.name] ?? s.name}
                {isChange && s.status === "denied" ? " (denied)" : ""}
                {change?.undone ? " (undone)" : ""}
              </span>
              {summary && <code className="font-mono text-xs text-muted truncate">{summary}</code>}
              {isChange && s.status === "ok" && change?.can_undo && onUndo && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault();
                    onUndo(s.call_id);
                  }}
                  className="ml-1 flex items-center gap-1 h-6 px-1.5 rounded border border-line text-xs text-fg hover:bg-raised"
                  title="Put back the previous version of this file"
                >
                  <RotateCcw className="w-3 h-3" />
                  Undo
                </button>
              )}
              <span className="ml-auto text-xs text-subtle shrink-0">step {s.step}</span>
            </summary>
            <div className="px-3 pb-2.5 pt-1 space-y-1.5 text-xs border-t border-line-soft">
              <div>
                <span className="text-subtle">Tool: </span>
                <code className="font-mono text-fg">{s.name}</code>
                {(!isChange || !s.diff) && (
                  <>
                    <span className="text-subtle"> with </span>
                    <code className="font-mono text-muted break-all">{s.arguments || "{}"}</code>
                  </>
                )}
              </div>
              {isChange && s.diff && <DiffView diff={s.diff} />}
              {s.result !== undefined && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-canvas border border-line p-2 font-mono text-xs text-muted">
                  {s.result}
                </pre>
              )}
            </div>
          </details>
        </div>
      );
    })}
    {stopReason && STOP_REASON_TEXT[stopReason] && (
      <p className="text-xs text-warning">{STOP_REASON_TEXT[stopReason]}</p>
    )}
  </div>
);
