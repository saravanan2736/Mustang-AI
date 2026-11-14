import React from "react";
import { Download, FileText, Library, Lightbulb, ListChecks, Wrench } from "lucide-react";
import type { LibraryTab } from "./ModelSelector";

interface StartScreenProps {
  modelName?: string | null;
  agentEnabled: boolean;
  onQuickPrompt: (text: string) => void;
  onEnableAgent: () => void;
  onOpenLibrary: (tab: LibraryTab) => void;
}

const QUICK_ACTIONS = [
  { label: "Summarize a file", icon: FileText, prompt: "Summarize the files in my workspace folder." },
  { label: "Brainstorm", icon: Lightbulb, prompt: "Help me brainstorm ideas for " },
  { label: "Make a plan", icon: ListChecks, prompt: "Make a step-by-step plan for " },
];

const chipCls =
  "h-8 px-3 flex items-center gap-2 rounded-lg bg-panel border border-line text-[13px] text-muted hover:text-fg hover:bg-raised transition-colors";

/** Empty chat: greeting, quick actions and what the app can do. The prompt box sits below, always. */
export const StartScreen: React.FC<StartScreenProps> = ({
  modelName,
  agentEnabled,
  onQuickPrompt,
  onEnableAgent,
  onOpenLibrary,
}) => {
  const cards = [
    {
      icon: Wrench,
      title: "Agent mode",
      text: "Let the model read a folder you choose and propose file changes you approve.",
      action: agentEnabled ? "Active" : "Turn on",
      onClick: onEnableAgent,
      disabled: agentEnabled,
    },
    {
      icon: Library,
      title: "Model library",
      text: "Your local models, their size and how much GPU memory they need.",
      action: "Open",
      onClick: () => onOpenLibrary("LOCAL"),
    },
    {
      icon: Download,
      title: "Get models",
      text: "Search Hugging Face for GGUF models and download them here.",
      action: "Browse",
      onClick: () => onOpenLibrary("HF"),
    },
  ];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="min-h-full max-w-3xl mx-auto px-6 py-10 flex flex-col justify-center">
        <div className="flex flex-col items-center text-center mb-6">
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-fg">What are we working on today?</h1>
          <p className="mt-2.5 text-[13px] text-muted max-w-md leading-relaxed">
            {modelName ? (
              <span>
                Running locally on <strong className="text-fg font-medium">{modelName.replace(/\.gguf$/i, "")}</strong>.
              </span>
            ) : (
              "Load a model above to start. Everything stays on this PC."
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-center gap-2 mb-8">
          {QUICK_ACTIONS.map(({ label, icon: Icon, prompt }) => (
            <button key={label} type="button" onClick={() => onQuickPrompt(prompt)} className={chipCls}>
              <Icon className="w-3.5 h-3.5 text-subtle" />
              <span>{label}</span>
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {cards.map(({ icon: Icon, title, text, action, onClick, disabled }) => (
            <div key={title} className="rounded-xl bg-panel border border-line p-4 flex flex-col justify-between">
              <div>
                <div className="flex items-start justify-between mb-3">
                  <span className="w-8 h-8 flex items-center justify-center rounded-lg bg-raised text-muted">
                    <Icon className="w-4 h-4" />
                  </span>
                  <button
                    type="button"
                    onClick={onClick}
                    disabled={disabled}
                    className={`h-6 px-3 rounded-md text-xs font-medium transition-colors ${
                      disabled ? "text-link cursor-default" : "bg-raised border border-line text-fg hover:bg-line"
                    }`}
                  >
                    {action}
                  </button>
                </div>
                <h3 className="text-[13px] font-semibold text-fg">{title}</h3>
                <p className="mt-1 text-xs text-muted leading-relaxed">{text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
