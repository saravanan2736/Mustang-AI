import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import {
  Bot,
  User,
  Sparkles,
  Copy,
  Check,
  RotateCcw,
  Edit2,
  Terminal,
  Cpu,
  ShieldCheck,
  BookOpen,
} from "lucide-react";
import { AgentChange, ChatMessage } from "../types";
import { CodeBlock } from "./CodeBlock";
import { ToolSteps } from "./ToolSteps";

interface ChatInterfaceProps {
  messages: ChatMessage[];
  isGenerating: boolean;
  onRegenerate?: () => void;
  onEditMessage?: (messageId: string, newContent: string) => void;
  onSelectPromptSuggestion?: (text: string) => void;
  activeModelName?: string | null;
  agentChanges?: Record<string, AgentChange>;
  onApproveChange?: (callId: string, approved: boolean) => void;
  onUndoChange?: (callId: string) => void;
}

export const ChatInterface: React.FC<ChatInterfaceProps> = ({
  messages,
  isGenerating,
  onRegenerate,
  onEditMessage,
  onSelectPromptSuggestion,
  activeModelName,
  agentChanges,
  onApproveChange,
  onUndoChange,
}) => {
  const scrollEndRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Inline editing state for user messages
  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState<string>("");

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isGenerating]);

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleStartEdit = (msg: ChatMessage) => {
    setEditingMsgId(msg.id);
    setEditContent(msg.content);
  };

  const handleSaveEdit = (msgId: string) => {
    if (editContent.trim() && onEditMessage) {
      onEditMessage(msgId, editContent.trim());
    }
    setEditingMsgId(null);
  };

  const handleCancelEdit = () => {
    setEditingMsgId(null);
  };

  const suggestions = [
    {
      title: "CUDA Memory Architecture",
      desc: "Explain how VRAM, L2 cache, and tensor cores interact during GGUF inference.",
      icon: <Cpu className="w-4 h-4 text-indigo-400" />,
      prompt: "Explain how VRAM, L2 cache, and tensor cores interact during quantized GGUF inference on an NVIDIA RTX 50-series GPU.",
    },
    {
      title: "Async Python Task Supervisor",
      desc: "Write a type-safe async worker queue with exponential backoff and timeout.",
      icon: <Terminal className="w-4 h-4 text-emerald-400" />,
      prompt: "Write a type-safe Python async worker pool using asyncio.Queue, exponential backoff, and graceful cancellation.",
    },
    {
      title: "System Architecture Spec",
      desc: "Draft a clean hexagonal architecture document for local-first desktop apps.",
      icon: <BookOpen className="w-4 h-4 text-amber-400" />,
      prompt: "Draft an architecture specification comparing monolithic desktop apps vs decoupled IPC backend architectures.",
    },
    {
      title: "Hardware Telemetry Validation",
      desc: "Check RTX 5050 driver version, compute capability, and memory layout.",
      icon: <ShieldCheck className="w-4 h-4 text-sky-400" />,
      prompt: "How does nvmlDeviceGetMemoryInfo_v2 report dedicated VRAM versus shared system memory on Windows 11?",
    },
  ];

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 select-none overflow-y-auto">
        <div className="w-12 h-12 rounded-2xl bg-indigo-600/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400 mb-4 shadow-indigo-glow">
          <Sparkles className="w-6 h-6" />
        </div>
        <h3 className="text-lg font-semibold text-slate-100 mb-1">
          Mustang AI Local Inference Engine
        </h3>
        <p className="text-xs text-slate-400 max-w-md text-center leading-relaxed mb-8">
          Accelerated on NVIDIA RTX 5050 Laptop GPU (8GB VRAM) & 24GB DDR5 RAM. Completely local, zero cloud telemetry, low latency streaming.
        </p>

        {/* Suggestion Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl w-full">
          {suggestions.map((s, idx) => (
            <button
              key={idx}
              onClick={() => onSelectPromptSuggestion?.(s.prompt)}
              className="p-3.5 rounded-xl bg-[#0B0E17]/80 hover:bg-[#101422] border border-white/[0.06] hover:border-indigo-500/40 text-left transition group shadow-sm flex flex-col justify-between"
            >
              <div className="flex items-center gap-2 mb-1.5">
                <div className="p-1 rounded-lg bg-white/[0.04] group-hover:bg-indigo-600/20 transition">
                  {s.icon}
                </div>
                <h4 className="text-xs font-semibold text-slate-200 group-hover:text-indigo-300 transition">
                  {s.title}
                </h4>
              </div>
              <p className="text-[11px] text-slate-400 leading-normal line-clamp-2">
                {s.desc}
              </p>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 space-y-6">
      {messages.map((msg, index) => {
        const isUser = msg.role === "user";
        const isLastAssistant =
          !isUser &&
          index === messages.length - 1 &&
          !isGenerating;
        const isCurrentlyEditing = editingMsgId === msg.id;

        return (
          <div
            key={msg.id}
            className={`flex items-start gap-3.5 max-w-4xl mx-auto ${
              isUser ? "flex-row-reverse" : "flex-row"
            }`}
          >
            {/* Avatar */}
            <div
              className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 text-xs font-semibold select-none shadow-sm ${
                isUser
                  ? "bg-indigo-600 text-white shadow-indigo-glow"
                  : "bg-[#0E1322] text-indigo-400 border border-white/10"
              }`}
            >
              {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
            </div>

            {/* Bubble / Container */}
            <div
              className={`relative group rounded-2xl p-4 text-sm leading-relaxed max-w-[85%] ${
                isUser
                  ? "bg-[#1E2038]/70 border border-indigo-500/30 text-white rounded-tr-none"
                  : "bg-[#0A0D18]/90 border border-white/[0.08] text-slate-200 rounded-tl-none shadow-md"
              }`}
            >
              {/* Header on assistant messages */}
              {!isUser && (
                <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 mb-2 pb-1.5 border-b border-white/[0.04] select-none">
                  <span className="flex items-center gap-1.5 text-indigo-300 font-sans font-medium">
                    <span>{activeModelName || "Mustang LLM"}</span>
                  </span>
                  {msg.token_count ? (
                    <span>~{msg.token_count} tokens</span>
                  ) : null}
                </div>
              )}

              {/* Message Content */}
              {isUser ? (
                isCurrentlyEditing ? (
                  <div className="space-y-2">
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      rows={3}
                      className="w-full bg-[#0B0E17] border border-indigo-500/50 rounded-xl p-2.5 text-sm text-white focus:outline-none"
                    />
                    <div className="flex justify-end gap-2 text-xs">
                      <button
                        onClick={handleCancelEdit}
                        className="px-2.5 py-1 text-slate-400 hover:text-white"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => handleSaveEdit(msg.id)}
                        className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg"
                      >
                        Save & Resend
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                )
              ) : (
                <div className="markdown-content prose prose-invert prose-sm max-w-none space-y-2">
                  {msg.steps?.length ? (
                    <ToolSteps
                      steps={msg.steps}
                      stopReason={msg.agent_stop_reason}
                      changes={agentChanges}
                      onApprove={onApproveChange}
                      onUndo={onUndoChange}
                    />
                  ) : null}
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={{
                      code({ node, className, children, ...props }) {
                        const match = /language-(\w+)/.exec(className || "");
                        const isInline = !match && !String(children).includes("\n");
                        if (isInline) {
                          return (
                            <code
                              className="px-1.5 py-0.5 rounded bg-white/[0.08] text-indigo-300 font-mono text-xs"
                              {...props}
                            >
                              {children}
                            </code>
                          );
                        }
                        return (
                          <CodeBlock
                            language={match ? match[1] : "text"}
                            code={String(children).replace(/\n$/, "")}
                          />
                        );
                      },
                    }}
                  >
                    {msg.content}
                  </ReactMarkdown>

                  {/* Pulsing cursor while generating tokens */}
                  {isGenerating && index === messages.length - 1 && msg.content === "" && !msg.steps?.some((s) => s.status === "running" || s.status === "awaiting") && (
                    <span className="inline-block w-2 h-4 bg-indigo-400 animate-pulse align-middle ml-1" />
                  )}
                  {isGenerating && index === messages.length - 1 && msg.content !== "" && (
                    <span className="inline-block w-1.5 h-3.5 bg-indigo-400 animate-pulse align-middle ml-0.5" />
                  )}
                </div>
              )}

              {/* Action Buttons on Hover */}
              <div className="opacity-0 group-hover:opacity-100 transition absolute top-2 right-2 flex items-center gap-1">
                {/* Copy Button */}
                {msg.content && (
                  <button
                    onClick={() => copyToClipboard(msg.content, msg.id)}
                    className="p-1.5 rounded-lg bg-[#07090E]/90 border border-white/10 text-slate-400 hover:text-white transition"
                    title="Copy text"
                  >
                    {copiedId === msg.id ? (
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                  </button>
                )}

                {/* Edit Button for User Messages */}
                {isUser && !isGenerating && !isCurrentlyEditing && (
                  <button
                    onClick={() => handleStartEdit(msg)}
                    className="p-1.5 rounded-lg bg-[#07090E]/90 border border-white/10 text-slate-400 hover:text-white transition"
                    title="Edit prompt"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>
                )}

                {/* Regenerate Button for Assistant Messages */}
                {isLastAssistant && onRegenerate && (
                  <button
                    onClick={onRegenerate}
                    className="p-1.5 rounded-lg bg-[#07090E]/90 border border-white/10 text-slate-400 hover:text-indigo-300 transition"
                    title="Regenerate response"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          </div>
        );
      })}
      <div ref={scrollEndRef} />
    </div>
  );
};
