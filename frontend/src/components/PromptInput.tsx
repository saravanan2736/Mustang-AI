import React, { useState, useRef, useEffect } from "react";
import { Send, Square, CornerDownLeft } from "lucide-react";

interface PromptInputProps {
  onSend: (prompt: string) => void;
  onStop: () => void;
  isGenerating: boolean;
  disabled: boolean;
  placeholder?: string;
  externalPrompt?: string;
  onExternalPromptConsumed?: () => void;
}

export const PromptInput: React.FC<PromptInputProps> = ({
  onSend,
  onStop,
  isGenerating,
  disabled,
  placeholder = "Message your local model...",
  externalPrompt,
  onExternalPromptConsumed,
}) => {
  const [prompt, setPrompt] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Consume external prompt if populated (e.g. from suggestion card)
  useEffect(() => {
    if (externalPrompt) {
      setPrompt(externalPrompt);
      onExternalPromptConsumed?.();
      if (textareaRef.current) {
        textareaRef.current.focus();
      }
    }
  }, [externalPrompt, onExternalPromptConsumed]);

  // Auto-resize textarea height
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [prompt]);

  useEffect(() => {
    if (!disabled && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [disabled]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!isGenerating && prompt.trim() && !disabled) {
        onSend(prompt);
        setPrompt("");
      }
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isGenerating) {
      onStop();
    } else if (prompt.trim() && !disabled) {
      onSend(prompt);
      setPrompt("");
    }
  };

  return (
    <form onSubmit={handleSubmit} className="relative bg-[#0C101C] border border-white/[0.08] focus-within:border-indigo-500/50 rounded-2xl p-2.5 shadow-xl transition">
      <textarea
        ref={textareaRef}
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder={disabled ? "Load a model in the header above to begin chatting..." : placeholder}
        rows={1}
        className="w-full bg-transparent text-sm text-slate-100 placeholder-slate-500 focus:outline-none resize-none px-2 py-1 pr-16 leading-relaxed disabled:opacity-40 max-h-[180px]"
      />

      {/* Bottom Bar inside input container */}
      <div className="flex items-center justify-between pt-1 px-2 select-none border-t border-white/[0.04]">
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span className="flex items-center gap-1">
            <CornerDownLeft className="w-3 h-3" />
            <span>Enter to send</span>
          </span>
          <span>•</span>
          <span>Shift+Enter for newline</span>
        </div>

        <div>
          {isGenerating ? (
            <button
              type="button"
              onClick={onStop}
              className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold shadow-md transition"
              title="Stop generation"
            >
              <Square className="w-3 h-3 fill-current" />
              <span>Stop</span>
            </button>
          ) : (
            <button
              type="submit"
              disabled={disabled || !prompt.trim()}
              className="flex items-center gap-1.5 px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-indigo-glow transition disabled:opacity-25 disabled:hover:bg-indigo-600"
              title="Send prompt"
            >
              <span>Send</span>
              <Send className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </form>
  );
};
