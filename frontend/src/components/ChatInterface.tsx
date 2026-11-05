import React, { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { ArrowDown, Check, ChevronRight, Copy, Pencil, RotateCcw } from "lucide-react";
import { AgentChange, ChatMessage } from "../types";
import { CodeBlock } from "./CodeBlock";
import { ToolSteps } from "./ToolSteps";
import { stripEmoji } from "../services/text";

interface ChatInterfaceProps {
  messages: ChatMessage[];
  isGenerating: boolean;
  onRegenerate?: () => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  activeModelName?: string | null;
  agentChanges?: Record<string, AgentChange>;
  onApproveChange?: (callId: string, approved: boolean, approveAll?: boolean) => void;
  onUndoChange?: (callId: string) => void;
}

/** How close to the bottom (px) still counts as "following" the reply. */
const FOLLOW_THRESHOLD = 80;

const actionCls =
  "h-7 px-2 flex items-center gap-1 rounded-md text-xs text-subtle hover:text-fg hover:bg-raised transition-colors";

const markdownComponents = {
  code({ className, children, ...props }: React.ComponentProps<"code">) {
    const match = /language-(\w+)/.exec(className || "");
    const isInline = !match && !String(children).includes("\n");
    if (isInline) {
      return (
        <code className="px-1.5 py-0.5 rounded bg-raised font-mono text-[0.85em]" {...props}>
          {children}
        </code>
      );
    }
    return <CodeBlock language={match ? match[1] : "text"} code={String(children).replace(/\n$/, "")} />;
  },
};

function useCopy() {
  const [copied, setCopied] = useState(false);
  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, []);
  return { copied, copy };
}

/** The model's thinking: open while the reply is still empty, a collapsed line afterwards. */
const Thinking = memo(function Thinking({ text, waiting }: { text: string; waiting: boolean }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? waiting;
  return (
    <div className="mb-3 not-prose">
      <button
        type="button"
        onClick={() => setUserOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-subtle hover:text-fg transition-colors"
        aria-expanded={open}
      >
        <ChevronRight className={`w-3 h-3 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className={waiting ? "animate-pulse" : ""}>{waiting ? "Thinking…" : "Show thinking"}</span>
      </button>
      {open && (
        <pre className="mt-1.5 max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-panel border border-line px-3 py-2 text-xs leading-relaxed text-muted font-sans">
          {text}
        </pre>
      )}
    </div>
  );
});

interface UserMessageProps {
  msg: ChatMessage;
  canEdit: boolean;
  onEdit: (id: string, text: string) => void;
}

const UserMessage = memo(function UserMessage({ msg, canEdit, onEdit }: UserMessageProps) {
  const { copied, copy } = useCopy();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(msg.content);

  if (editing) {
    return (
      <div className="flex flex-col items-end">
        <div className="w-full max-w-[85%] space-y-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            autoFocus
            className="w-full bg-panel border border-accent rounded-xl p-3 text-[14px] text-fg focus:outline-none"
          />
          <div className="flex justify-end gap-2">
            <button onClick={() => setEditing(false)} className="h-7 px-3 rounded-md text-xs text-muted hover:text-fg hover:bg-raised">
              Cancel
            </button>
            <button
              onClick={() => {
                if (text.trim()) onEdit(msg.id, text.trim());
                setEditing(false);
              }}
              className="h-7 px-3 rounded-md text-xs font-medium bg-accent text-white hover:bg-accent-hover transition-colors"
            >
              Save and send
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex flex-col items-end">
      <div className="max-w-[85%] rounded-2xl bg-raised px-4 py-2.5 text-[14px] leading-relaxed whitespace-pre-wrap break-words text-fg">
        {msg.content}
      </div>
      <div className="h-7 mt-0.5 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <button onClick={() => copy(msg.content)} className={actionCls} title="Copy">
          {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
        {canEdit && (
          <button
            onClick={() => {
              setText(msg.content);
              setEditing(true);
            }}
            className={actionCls}
            title="Edit and send again"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
});

interface AssistantMessageProps {
  msg: ChatMessage;
  streaming: boolean;
  isLast: boolean;
  changes?: Record<string, AgentChange>;
  onApprove: (callId: string, approved: boolean, approveAll?: boolean) => void;
  onUndo: (callId: string) => void;
  onRegenerate: () => void;
}

const AssistantMessage = memo(function AssistantMessage({
  msg,
  streaming,
  isLast,
  changes,
  onApprove,
  onUndo,
  onRegenerate,
}: AssistantMessageProps) {
  const { copied, copy } = useCopy();
  const toolRunning = msg.steps?.some((s) => s.status === "running" || s.status === "awaiting");
  const text = stripEmoji(msg.content);

  return (
    <div className="group">
      {msg.steps?.length ? (
        <ToolSteps steps={msg.steps} stopReason={msg.agent_stop_reason} changes={changes} onApprove={onApprove} onUndo={onUndo} />
      ) : null}
      {msg.reasoning ? <Thinking text={stripEmoji(msg.reasoning)} waiting={streaming && !text && !toolRunning} /> : null}
      {!text && !msg.steps?.length && !msg.reasoning && (
        <p className={`text-[13px] text-subtle ${streaming ? "animate-pulse" : ""}`}>
          {streaming
            ? "Thinking…"
            : "No answer came back. The model may have used its whole token limit while thinking. Try again, or raise Max tokens in Settings."}
        </p>
      )}
      <div className="markdown-content text-[14px] text-fg break-words">
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={markdownComponents}>
          {text}
        </ReactMarkdown>
        {streaming && !toolRunning && (
          <span className="inline-block w-1.5 h-4 bg-muted align-text-bottom animate-pulse rounded-sm" aria-hidden />
        )}
      </div>
      {!streaming && msg.content && (
        <div className="h-7 mt-1 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button onClick={() => copy(text)} className={actionCls} title="Copy">
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
          {isLast && (
            <button onClick={onRegenerate} className={actionCls} title="Regenerate">
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          )}
          {msg.token_count ? <span className="ml-1 text-xs text-subtle tabular-nums">{msg.token_count} tokens</span> : null}
        </div>
      )}
    </div>
  );
});

export const ChatInterface: React.FC<ChatInterfaceProps> = ({
  messages,
  isGenerating,
  onRegenerate,
  onEditMessage,
  agentChanges,
  onApproveChange,
  onUndoChange,
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true); // true while the reader is at the bottom
  const [showLatest, setShowLatest] = useState(false);

  // Callbacks stay the same function between renders, so memoised messages don't re-render per token.
  const handlers = useRef({ onRegenerate, onEditMessage, onApproveChange, onUndoChange });
  handlers.current = { onRegenerate, onEditMessage, onApproveChange, onUndoChange };
  const regenerate = useCallback(() => handlers.current.onRegenerate?.(), []);
  const edit = useCallback((id: string, text: string) => handlers.current.onEditMessage?.(id, text), []);
  const approve = useCallback((id: string, ok: boolean, all?: boolean) => handlers.current.onApproveChange?.(id, ok, all), []);
  const undo = useCallback((id: string) => handlers.current.onUndoChange?.(id), []);

  // Scroll only this container (scrollIntoView also moves the clipped panels around it).
  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  const lastTop = useRef(0);
  const lastHeight = useRef(0);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD;
    // Content growing under us (a burst of tokens) must not count as the reader leaving the
    // bottom; only moving up does.
    // A shrinking page (a card collapsing) also lowers scrollTop; that isn't the reader either.
    const movedUp = el.scrollTop < lastTop.current - 1 && el.scrollHeight >= lastHeight.current;
    lastTop.current = el.scrollTop;
    lastHeight.current = el.scrollHeight;
    if (atBottom) following.current = true;
    else if (movedUp) following.current = false;
    setShowLatest(!following.current && !atBottom);
  };

  // A different conversation opens at its end.
  const firstId = messages[0]?.id;
  useLayoutEffect(() => {
    following.current = true;
    setShowLatest(false);
    scrollToBottom();
  }, [firstId, scrollToBottom]);

  // Sending a message always brings you to it.
  const last = messages[messages.length - 1];
  const lastIsNewUserMessage = last?.role === "user";
  useLayoutEffect(() => {
    if (lastIsNewUserMessage) {
      following.current = true;
      setShowLatest(false);
      scrollToBottom();
    }
  }, [messages.length, lastIsNewUserMessage, scrollToBottom]);

  // While the reply grows (text, tool cards, code blocks), stay at the bottom, but only if the
  // reader hasn't scrolled up.
  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (following.current) scrollToBottom();
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, [scrollToBottom]);

  return (
    <div className="relative flex-1 min-h-0 flex flex-col">
      <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto overscroll-contain" style={{ overflowAnchor: "none" }}>
        <div ref={contentRef} className="max-w-3xl mx-auto px-6 py-6 space-y-6">
          {messages.map((msg, index) => {
            const isLast = index === messages.length - 1;
            return msg.role === "user" ? (
              <UserMessage key={msg.id} msg={msg} canEdit={!isGenerating} onEdit={edit} />
            ) : (
              <AssistantMessage
                key={msg.id}
                msg={msg}
                streaming={isGenerating && isLast}
                isLast={isLast}
                changes={agentChanges}
                onApprove={approve}
                onUndo={undo}
                onRegenerate={regenerate}
              />
            );
          })}
        </div>
      </div>
      {showLatest && (
        <button
          onClick={() => {
            following.current = true;
            scrollToBottom(true);
          }}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 h-8 px-3 flex items-center gap-1.5 rounded-full bg-panel border border-line text-xs text-fg hover:bg-raised transition-colors"
        >
          <ArrowDown className="w-3.5 h-3.5" />
          Latest
        </button>
      )}
    </div>
  );
};
