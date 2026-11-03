import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { Copy, Check, RotateCcw, Pencil } from "lucide-react";
import { AgentChange, ChatMessage } from "../types";
import { CodeBlock } from "./CodeBlock";
import { ToolSteps } from "./ToolSteps";

interface ChatInterfaceProps {
  messages: ChatMessage[];
  isGenerating: boolean;
  onRegenerate?: () => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  activeModelName?: string | null;
  agentChanges?: Record<string, AgentChange>;
  onApproveChange?: (callId: string, approved: boolean) => void;
  onUndoChange?: (callId: string) => void;
}

const actionCls =
  "h-7 px-2 flex items-center gap-1 rounded-lg text-xs text-subtle hover:text-fg hover:bg-raised/80 hover:border-line/60 border border-transparent transition-all";

const markdownComponents = {
  code({ className, children, ...props }: React.ComponentProps<"code">) {
    const match = /language-(\w+)/.exec(className || "");
    const isInline = !match && !String(children).includes("\n");
    if (isInline) {
      return (
        <code className="px-1.5 py-0.5 rounded-md bg-raised/80 border border-line/50 font-mono text-[0.85em] text-link" {...props}>
          {children}
        </code>
      );
    }
    return <CodeBlock language={match ? match[1] : "text"} code={String(children).replace(/\n$/, "")} />;
  },
};

export const ChatInterface: React.FC<ChatInterfaceProps> = ({
  messages,
  isGenerating,
  onRegenerate,
  onEditMessage,
  activeModelName,
  agentChanges,
  onApproveChange,
  onUndoChange,
}) => {
  const scrollEndRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState<string>("");

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages, isGenerating]);

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="text-center">
          <p className="text-[15px] text-fg">No messages yet</p>
          <p className="mt-1 text-[13px] text-muted">
            {activeModelName ? "Type a message below to start." : "Load a model above, then type a message below."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
        {messages.map((msg, index) => {
          const isUser = msg.role === "user";
          const isLast = index === messages.length - 1;
          const isStreaming = isGenerating && isLast && !isUser;
          const toolRunning = msg.steps?.some((s) => s.status === "running" || s.status === "awaiting");

          if (isUser) {
            const editing = editingMsgId === msg.id;
            return (
              <div key={msg.id} className="group flex flex-col items-end animate-fade-in">
                {editing ? (
                  <div className="w-full max-w-[85%] space-y-2">
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      rows={3}
                      autoFocus
                      className="w-full bg-panel/95 border border-accent/80 rounded-xl p-3 text-[14px] text-fg focus:outline-none shadow-glow-sm"
                    />
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => setEditingMsgId(null)}
                        className="h-7 px-3 rounded-lg text-xs font-medium text-muted hover:text-fg hover:bg-raised"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => {
                          if (editContent.trim() && onEditMessage) onEditMessage(msg.id, editContent.trim());
                          setEditingMsgId(null);
                        }}
                        className="h-7 px-3 rounded-lg text-xs font-medium btn-violet text-white shadow-glow-sm hover:brightness-110"
                      >
                        Save and send
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="max-w-[85%] rounded-2xl bg-panel/80 border border-line/70 px-4 py-2.5 text-[14px] leading-relaxed whitespace-pre-wrap break-words shadow-sm text-fg">
                      {msg.content}
                    </div>
                    <div className="h-7 mt-0.5 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button onClick={() => copyToClipboard(msg.content, msg.id)} className={actionCls} title="Copy">
                        {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                      {!isGenerating && onEditMessage && (
                        <button
                          onClick={() => {
                            setEditingMsgId(msg.id);
                            setEditContent(msg.content);
                          }}
                          className={actionCls}
                          title="Edit and send again"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          }

          return (
            <div key={msg.id} className="group animate-fade-in">
              {msg.steps?.length ? (
                <ToolSteps
                  steps={msg.steps}
                  stopReason={msg.agent_stop_reason}
                  changes={agentChanges}
                  onApprove={onApproveChange}
                  onUndo={onUndoChange}
                />
              ) : null}
              <div className="markdown-content text-[14px] text-fg break-words">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                  components={markdownComponents}
                >
                  {msg.content}
                </ReactMarkdown>
                {isStreaming && !toolRunning && (
                  <span className="inline-block w-1.5 h-4 bg-accent align-text-bottom animate-pulse shadow-glow-sm ml-0.5 rounded-sm" aria-hidden />
                )}
              </div>
              {!isStreaming && msg.content && (
                <div className="h-7 mt-1.5 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button onClick={() => copyToClipboard(msg.content, msg.id)} className={actionCls} title="Copy">
                    {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                  {isLast && onRegenerate && (
                    <button onClick={onRegenerate} className={actionCls} title="Regenerate">
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {msg.token_count ? (
                    <span className="ml-1 text-[11px] font-mono text-subtle px-1.5 py-0.5 rounded bg-raised/50 border border-line/40">
                      {msg.token_count} tokens
                    </span>
                  ) : null}
                </div>
              )}
            </div>
          );
        })}
        <div ref={scrollEndRef} />
      </div>
    </div>
  );
};
