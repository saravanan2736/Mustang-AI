import React, { useState, useRef, useEffect } from "react";
import { ArrowUp, Sparkles, Square } from "lucide-react";

interface PromptInputProps {
  onSend: (prompt: string) => void;
  onStop: () => void;
  isGenerating: boolean;
  disabled: boolean;
  placeholder?: string;
  externalPrompt?: string;
  onExternalPromptConsumed?: () => void;
  /** Controls shown along the bottom of the box (agent mode, folder). */
  toolbar?: React.ReactNode;
  /** Taller box on the start screen. */
  large?: boolean;
}

/** The prompt card: text, a toolbar row, and the send / stop button. */
export const PromptInput: React.FC<PromptInputProps> = ({
  onSend,
  onStop,
  isGenerating,
  disabled,
  placeholder = "Ask anything…",
  externalPrompt,
  onExternalPromptConsumed,
  toolbar,
  large,
}) => {
  const [prompt, setPrompt] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (externalPrompt) {
      setPrompt(externalPrompt);
      onExternalPromptConsumed?.();
      textareaRef.current?.focus();
    }
  }, [externalPrompt, onExternalPromptConsumed]);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
    }
  }, [prompt]);

  useEffect(() => {
    if (!disabled) textareaRef.current?.focus();
  }, [disabled]);

  const send = () => {
    if (!isGenerating && prompt.trim() && !disabled) {
      onSend(prompt);
      setPrompt("");
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (isGenerating) onStop();
        else send();
      }}
      className="rounded-2xl bg-panel/85 backdrop-blur-md border border-line/80 focus-within:border-accent/70 focus-within:shadow-glow-sm shadow-xl transition-all duration-200"
    >
      <div className={`flex items-start gap-3 px-4 pt-3.5 ${large ? "min-h-[76px]" : ""}`}>
        <Sparkles className="w-4 h-4 mt-1 text-accent shrink-0 transition-transform group-focus-within:scale-110" />
        <textarea
          ref={textareaRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          disabled={disabled}
          placeholder={placeholder}
          rows={1}
          className="flex-1 bg-transparent text-[14px] text-fg placeholder:text-subtle/80 focus:outline-none resize-none py-0.5 leading-relaxed disabled:opacity-50 max-h-[220px]"
        />
      </div>
      <div className="flex items-center gap-2 px-3 pb-3 pt-2 border-t border-line/30 mt-1">
        <div className="flex-1 min-w-0 flex flex-wrap items-center gap-1.5">{toolbar}</div>
        {isGenerating ? (
          <button
            type="button"
            onClick={onStop}
            className="h-8 px-3.5 flex items-center gap-1.5 rounded-full bg-raised/90 border border-line/80 text-[13px] font-medium text-fg hover:bg-rose-950/40 hover:border-rose-500/50 hover:text-rose-300 transition-all shadow-sm"
            title="Stop generating"
          >
            <Square className="w-3 h-3 fill-current text-rose-400" />
            <span>Stop</span>
          </button>
        ) : (
          <button
            type="submit"
            disabled={disabled || !prompt.trim()}
            className="h-8 w-8 flex items-center justify-center rounded-full btn-violet text-white shadow-glow-sm hover:scale-105 active:scale-95 transition-all disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:scale-100 disabled:shadow-none"
            title="Send (Enter)"
            aria-label="Send"
          >
            <ArrowUp className="w-4 h-4 stroke-[2.5]" />
          </button>
        )}
      </div>
    </form>
  );
};
