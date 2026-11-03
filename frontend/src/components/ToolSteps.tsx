import React from "react";
import { CheckCircle2, ChevronRight, Loader2, XCircle } from "lucide-react";
import { AgentStep } from "../types";
import { STOP_REASON_TEXT } from "../services/agent";

const TOOL_LABELS: Record<string, string> = {
  get_current_time: "Checked the time",
  calculator: "Calculated",
  list_files: "Listed files",
  read_file: "Read file",
  search_files: "Searched files",
  search_history: "Searched this chat",
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

interface ToolStepsProps {
  steps: AgentStep[];
  stopReason?: string | null;
}

/** Tool steps of an agent-mode reply, one collapsible card per call. */
export const ToolSteps: React.FC<ToolStepsProps> = ({ steps, stopReason }) => (
  <div className="space-y-1.5 mb-3 not-prose">
    {steps.map((s) => {
      const summary = argumentSummary(s.arguments);
      return (
        <div key={s.call_id}>
          {s.note && <p className="text-xs text-slate-400 italic mb-1">{s.note}</p>}
          <details className="group/step rounded-lg border border-white/[0.07] bg-white/[0.02] open:bg-white/[0.035]">
            <summary className="flex items-center gap-2 px-2.5 py-1.5 cursor-pointer list-none text-xs text-slate-300 select-none">
              <ChevronRight className="w-3 h-3 text-slate-500 transition group-open/step:rotate-90 shrink-0" />
              {s.status === "running" ? (
                <Loader2 className="w-3.5 h-3.5 text-indigo-300 animate-spin shrink-0" />
              ) : s.status === "ok" ? (
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
              ) : (
                <XCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
              )}
              <span className="font-medium">{TOOL_LABELS[s.name] ?? s.name}</span>
              {summary && <code className="font-mono text-[11px] text-indigo-300 truncate">{summary}</code>}
              <span className="ml-auto text-[10px] text-slate-500 shrink-0">step {s.step}</span>
            </summary>
            <div className="px-3 pb-2.5 pt-1 space-y-1.5 text-[11px]">
              <div>
                <span className="text-slate-500">Tool: </span>
                <code className="font-mono text-slate-300">{s.name}</code>
                <span className="text-slate-500"> with </span>
                <code className="font-mono text-slate-300 break-all">{s.arguments || "{}"}</code>
              </div>
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
