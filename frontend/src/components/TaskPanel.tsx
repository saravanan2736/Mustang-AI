import React, { useState } from "react";
import { CheckCircle2, ChevronRight, Circle, Loader2 } from "lucide-react";
import { TaskItem } from "../types";

interface TaskPanelProps {
  tasks: TaskItem[];
  /** The agent is working right now (the in-progress item shows a spinner). */
  working?: boolean;
}

/** The agent's task list, above the prompt box: what it plans to do, what it is doing, what is done. */
export const TaskPanel: React.FC<TaskPanelProps> = ({ tasks, working }) => {
  const [open, setOpen] = useState(true);
  if (!tasks.length) return null;
  const done = tasks.filter((t) => t.status === "done").length;

  return (
    <div className="mb-2 rounded-xl border border-line bg-panel" aria-label="Task list">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 h-8 px-3 text-xs text-fg hover:bg-raised rounded-xl transition-colors"
      >
        <ChevronRight className={`w-3 h-3 text-subtle transition-transform ${open ? "rotate-90" : ""}`} />
        <span className="font-medium">Tasks</span>
        <span className="text-subtle tabular-nums">
          {done} of {tasks.length} done
        </span>
        {done === tasks.length && <CheckCircle2 className="w-3.5 h-3.5 text-success ml-auto" />}
      </button>
      {open && (
        <ul className="px-3 pb-2.5 pt-0.5 space-y-1 max-h-40 overflow-y-auto">
          {tasks.map((t, i) => (
            <li key={i} className="flex items-start gap-2 text-[13px] leading-snug">
              {t.status === "done" ? (
                <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 text-success shrink-0" />
              ) : t.status === "in_progress" ? (
                working ? (
                  <Loader2 className="w-3.5 h-3.5 mt-0.5 text-accent animate-spin shrink-0" />
                ) : (
                  <Circle className="w-3.5 h-3.5 mt-0.5 text-accent shrink-0" />
                )
              ) : (
                <Circle className="w-3.5 h-3.5 mt-0.5 text-subtle shrink-0" />
              )}
              <span className={t.status === "done" ? "text-subtle line-through" : t.status === "in_progress" ? "text-fg font-medium" : "text-muted"}>
                {t.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};
