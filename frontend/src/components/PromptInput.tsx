import React, { useState, useRef, useEffect } from "react";
import { ArrowUp, Square } from "lucide-react";

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
      className="rounded-2xl bg-panel border border-line focus-within:border-accent transition-colors"
    >
      <div className="px-4 pt-3.5">
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
          className="w-full bg-transparent text-[14px] text-fg placeholder:text-subtle focus:outline-none resize-none py-0.5 leading-relaxed disabled:opacity-50 max-h-[220px]"
        />
      </div>
      <div className="flex items-center gap-2 px-3 pb-3 pt-2">
        <div className="flex-1 min-w-0 flex flex-wrap items-center gap-1.5">{toolbar}</div>
        {isGenerating ? (
          <button
            type="button"
            onClick={onStop}
            className="h-8 px-3.5 flex items-center gap-1.5 rounded-full bg-raised border border-line text-[13px] font-medium text-fg hover:bg-line transition-colors"
            title="Stop generating"
          >
            <Square className="w-3 h-3 fill-current" />
            <span>Stop</span>
          </button>
        ) : (
          <button
            type="submit"
            disabled={disabled || !prompt.trim()}
            className="h-8 w-8 flex items-center justify-center rounded-full bg-accent text-white hover:bg-accent-hover transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
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
