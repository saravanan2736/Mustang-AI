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
}

export const PromptInput: React.FC<PromptInputProps> = ({
  onSend,
  onStop,
  isGenerating,
  disabled,
  placeholder = "Message",
  externalPrompt,
  onExternalPromptConsumed,
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
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
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
      className="flex items-end gap-2 bg-canvas border border-line focus-within:border-accent rounded-lg p-2 transition-colors"
    >
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
        className="flex-1 bg-transparent text-[14px] text-fg placeholder:text-subtle focus:outline-none resize-none px-1.5 py-1 leading-relaxed disabled:opacity-50 max-h-[200px]"
      />
      {isGenerating ? (
        <button
          type="button"
          onClick={onStop}
          className="h-8 px-2.5 flex items-center gap-1.5 rounded-md border border-line text-[13px] text-fg hover:bg-raised transition-colors"
          title="Stop generating"
        >
          <Square className="w-3 h-3 fill-current" />
          Stop
        </button>
      ) : (
        <button
          type="submit"
          disabled={disabled || !prompt.trim()}
          className="h-8 w-8 flex items-center justify-center rounded-md bg-accent hover:bg-accent-hover text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          title="Send (Enter)"
          aria-label="Send"
        >
          <ArrowUp className="w-4 h-4" />
        </button>
      )}
    </form>
  );
};
