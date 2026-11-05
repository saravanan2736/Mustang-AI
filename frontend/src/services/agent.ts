/**
 * Agent mode (M5) on the UI side: turning stored tool rows and streamed "agent" events into
 * tool steps shown on the assistant message, and keeping tool turns out of the history the
 * UI sends (the backend accepts text turns only; it runs the tools itself).
 */
import { AgentStep, ChatMessage } from "../types";

export interface AgentEvent {
  event: "tool_call" | "approval_required" | "tool_result" | "done";
  summary?: string;
  diff?: string;
  step?: number;
  call_id?: string;
  name?: string;
  arguments?: string;
  ok?: boolean;
  content?: string;
  reason?: string | null;
  auto?: boolean;
}

/** Tools that change files: every call is shown as a diff and needs the user's approval. */
export const CHANGE_TOOLS = new Set(["create_file", "edit_file"]);

const DENIED_PREFIX = "The user denied this change";

function resultStatus(ok: boolean, text: string): AgentStep["status"] {
  if (ok) return "ok";
  return text.startsWith(DENIED_PREFIX) ? "denied" : "error";
}

/** Apply one streamed agent event to the assistant message being generated. */
export function applyAgentEvent(msg: ChatMessage, ev: AgentEvent): ChatMessage {
  const steps = msg.steps ?? [];
  if (ev.event === "tool_call" && ev.call_id) {
    // Text streamed so far belonged to this tool step ("Let me check."), not to the answer.
    const note = msg.content.trim() || undefined;
    return {
      ...msg,
      content: "",
      reasoning: undefined, // the thinking that led to this call has done its job
      steps: [...steps, { call_id: ev.call_id, step: ev.step ?? steps.length + 1, name: ev.name ?? "tool",
                          arguments: ev.arguments ?? "{}", status: "running", note }],
    };
  }
  if (ev.event === "approval_required" && ev.call_id) {
    // With "Approve all" on, the change is written at once: show its diff but never the buttons.
    const status = ev.auto ? "running" : "awaiting";
    return {
      ...msg,
      steps: steps.map((s) =>
        s.call_id === ev.call_id ? { ...s, status, summary: ev.summary, diff: ev.diff, auto: ev.auto } : s
      ),
    };
  }
  if (ev.event === "tool_result" && ev.call_id) {
    return {
      ...msg,
      steps: steps.map((s) =>
        s.call_id === ev.call_id ? { ...s, status: resultStatus(ev.ok ?? false, ev.content ?? ""), result: ev.content ?? "" } : s
      ),
    };
  }
  if (ev.event === "done") {
    return { ...msg, agent_stop_reason: ev.reason ?? null };
  }
  return msg;
}

/**
 * Stored rows -> display messages: an assistant row with tool_calls and the tool rows that
 * answer it become steps on the next assistant answer, as they looked while streaming.
 */
export function groupAgentMessages(rows: ChatMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  let pending: AgentStep[] = [];
  let step = 0;
  let firstStepRow: ChatMessage | null = null;

  const flush = (reason: string | null) => {
    if (!pending.length) return;
    out.push({
      id: `${firstStepRow?.id ?? "agent"}-steps`,
      role: "assistant",
      content: "",
      created_at: firstStepRow?.created_at,
      steps: pending,
      agent_stop_reason: reason,
    });
    pending = [];
    firstStepRow = null;
  };

  for (const row of rows) {
    if (row.role === "assistant" && row.tool_calls?.length) {
      step += 1;
      firstStepRow = firstStepRow ?? row;
      row.tool_calls.forEach((call, i) =>
        pending.push({
          call_id: call.id,
          step,
          name: call.function.name,
          arguments: call.function.arguments,
          status: "running",
          note: i === 0 ? row.content.trim() || undefined : undefined,
        })
      );
    } else if (row.role === "tool") {
      pending = pending.map((s) =>
        s.call_id === row.tool_call_id
          ? { ...s, status: resultStatus(!row.content.startsWith("Error:") && !row.content.startsWith(DENIED_PREFIX) && !row.content.startsWith("Stopped before"), row.content), result: row.content }
          : s
      );
    } else if (row.role === "assistant") {
      out.push(pending.length ? { ...row, steps: pending } : row);
      pending = [];
      firstStepRow = null;
      step = 0; // steps are numbered per reply
    } else {
      flush("stopped"); // a reply that ended during its tool steps
      out.push(row);
      step = 0;
    }
  }
  flush("stopped");
  // Steps whose result never arrived (stopped mid-call) are not "running" any more.
  return out.map((m) =>
    m.steps ? { ...m, steps: m.steps.map((s) => (s.status === "running" || s.status === "awaiting" ? { ...s, status: "error", result: s.result ?? "Stopped." } : s)) } : m
  );
}

/** History for a request: text turns only (no tool rows, no step-only placeholders). */
export function historyForRequest(messages: ChatMessage[]): { role: ChatMessage["role"]; content: string }[] {
  return messages
    .filter((m) => m.role !== "tool" && !m.tool_calls?.length && !(m.role === "assistant" && m.steps?.length && !m.content))
    .map((m) => ({ role: m.role, content: m.content }));
}

/** Parameter count in billions from a GGUF file name ("Qwen3.5-4B-..." -> 4), if it has one. */
export function modelSizeB(name: string | null | undefined): number | null {
  const m = /(?:^|[^a-z0-9.])(\d+(?:\.\d+)?)b(?:[^a-z]|$)/i.exec(name ?? "");
  return m ? parseFloat(m[1]) : null;
}

/** Phase A: models under ~3B call tools when none is needed and miss needed calls. */
export const SMALL_AGENT_MODEL_B = 3;

export const STOP_REASON_TEXT: Record<string, string> = {
  step_limit: "Stopped after the maximum number of tool steps.",
  repeated_calls: "Stopped: the model kept repeating the same tool call.",
  tool_errors: "Stopped: the tool calls kept failing.",
  stopped: "Stopped before the answer was finished.",
};
