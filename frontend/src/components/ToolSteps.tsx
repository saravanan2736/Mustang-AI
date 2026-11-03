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
  <pre className="max-h-64 overflow-auto rounded-md bg-black/40 border border-white/[0.06] p-2 font-mono text-[11px] leading-snug">
    {diff.split("\n").map((line, i) => {
      const cls = line.startsWith("+++") || line.startsWith("---")
        ? "text-slate-500"
        : line.startsWith("+")
        ? "text-emerald-300 bg-emerald-500/10"
        : line.startsWith("-")
        ? "text-rose-300 bg-rose-500/10"
        : line.startsWith("@@")
        ? "text-sky-300/80"
        : "text-slate-400";
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
    <Loader2 className="w-3.5 h-3.5 text-indigo-300 animate-spin shrink-0" />
  ) : status === "awaiting" ? (
    <FilePen className="w-3.5 h-3.5 text-amber-300 shrink-0" />
  ) : status === "ok" ? (
    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
  ) : status === "denied" ? (
    <Ban className="w-3.5 h-3.5 text-slate-400 shrink-0" />
  ) : (
    <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
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
          <div key={s.call_id} className="rounded-lg border border-amber-400/40 bg-amber-500/[0.06] p-2.5 space-y-2">
            {s.note && <p className="text-xs text-slate-400 italic">{s.note}</p>}
            <div className="flex items-center gap-2 text-xs text-amber-100">
              <StatusIcon status={s.status} />
              <span className="font-semibold">{s.summary || `${TOOL_LABELS[s.name] ?? s.name} ${summary}`}</span>
              <span className="ml-auto text-[10px] text-amber-200/70">waiting for you</span>
            </div>
            {s.diff && <DiffView diff={s.diff} />}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onApprove?.(s.call_id, true)}
                className="px-3 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
              >
                Approve change
              </button>
              <button
                type="button"
                onClick={() => onApprove?.(s.call_id, false)}
                className="px-3 py-1 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] text-slate-200 text-xs font-semibold border border-white/10"
              >
                Deny
              </button>
              <span className="text-[10px] text-slate-500">Nothing is written unless you approve.</span>
            </div>
          </div>
        );
      }

      return (
        <div key={s.call_id}>
          {s.note && <p className="text-xs text-slate-400 italic mb-1">{s.note}</p>}
          <details className="group/step rounded-lg border border-white/[0.07] bg-white/[0.02] open:bg-white/[0.035]">
            <summary className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer list-none text-xs text-slate-300 select-none">
              <ChevronRight className="w-3 h-3 text-slate-500 transition group-open/step:rotate-90 shrink-0" />
              <StatusIcon status={s.status} />
              <span className="font-medium">
                {TOOL_LABELS[s.name] ?? s.name}
                {isChange && s.status === "denied" ? " (denied)" : ""}
                {change?.undone ? " (undone)" : ""}
              </span>
              {summary && <code className="font-mono text-[11px] text-indigo-300 truncate">{summary}</code>}
              {isChange && s.status === "ok" && change?.can_undo && onUndo && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.preventDefault();
                    onUndo(s.call_id);
                  }}
                  className="ml-1 flex items-center gap-1 px-1.5 py-0.5 rounded border border-white/10 text-[10px] text-slate-300 hover:text-white hover:bg-white/[0.08]"
                  title="Put back the previous version of this file"
                >
                  <RotateCcw className="w-3 h-3" />
                  Undo
                </button>
              )}
              <span className="ml-auto text-[10px] text-slate-500 shrink-0">step {s.step}</span>
            </summary>
            <div className="px-3 pb-2.5 pt-1 space-y-1.5 text-[11px]">
              <div>
                <span className="text-slate-500">Tool: </span>
                <code className="font-mono text-slate-300">{s.name}</code>
                {(!isChange || !s.diff) && (
                  <>
                    <span className="text-slate-500"> with </span>
                    <code className="font-mono text-slate-300 break-all">{s.arguments || "{}"}</code>
                  </>
                )}
              </div>
              {isChange && s.diff && <DiffView diff={s.diff} />}
              {s.result !== undefined && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/30 border border-white/[0.05] p-2 font-mono text-[11px] text-slate-300">
                  {s.result}
                </pre>
              )}
            </div>
          </details>
        </div>
      );
    })}
    {stopReason && STOP_REASON_TEXT[stopReason] && (
      <p className="text-[11px] text-amber-300/90">{STOP_REASON_TEXT[stopReason]}</p>
    )}
  </div>
);
