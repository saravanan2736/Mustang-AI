import React from "react";
import { Download, FileText, Library, Lightbulb, ListChecks, Wrench } from "lucide-react";
import type { LibraryTab } from "./ModelSelector";

interface StartScreenProps {
  modelName?: string | null;
  agentEnabled: boolean;
  /** The prompt card, rendered between the quick actions and the feature cards. */
  composer: React.ReactNode;
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
  "h-8 px-3.5 flex items-center gap-2 rounded-xl bg-panel/70 border border-line/60 text-[13px] font-medium text-muted hover:text-fg hover:bg-raised/80 hover:border-accent/40 backdrop-blur-sm shadow-sm transition-all duration-150";

/** Empty chat: greeting, quick actions, the prompt card and what the app can do. */
export const StartScreen: React.FC<StartScreenProps> = ({
  modelName,
  agentEnabled,
  composer,
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
    <div className="flex-1 overflow-y-auto">
      <div className="min-h-full max-w-3xl mx-auto px-6 py-10 flex flex-col justify-center animate-fade-in">
        <div className="flex flex-col items-center text-center mb-8">
          <div className="orb w-16 h-16 rounded-full mb-5 transition-transform hover:scale-105 duration-300" aria-hidden />
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-fg">What are we working on today?</h1>
          <p className="mt-2.5 text-[13px] text-muted max-w-md leading-relaxed">
            {modelName ? (
              <span>Running locally on <strong className="text-fg font-medium">{modelName.replace(/\.gguf$/i, "")}</strong>.</span>
            ) : (
              "Load a model above to start. Everything stays on this PC."
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center justify-center gap-2 mb-4">
          {QUICK_ACTIONS.map(({ label, icon: Icon, prompt }) => (
            <button key={label} type="button" onClick={() => onQuickPrompt(prompt)} className={chipCls}>
              <span>{label}</span>
              <Icon className="w-3.5 h-3.5 text-accent/80" />
            </button>
          ))}
        </div>

        {composer}

        <div className="mt-5 grid grid-cols-1 sm:grid-cols-3 gap-3">
          {cards.map(({ icon: Icon, title, text, action, onClick, disabled }) => (
            <div
              key={title}
              className="rounded-2xl bg-panel/60 border border-line/60 hover:border-accent/30 hover:bg-panel/85 backdrop-blur-md p-4 transition-all duration-200 shadow-sm hover:shadow-glow-sm flex flex-col justify-between"
            >
              <div>
                <div className="flex items-start justify-between mb-3">
                  <span className="w-8 h-8 flex items-center justify-center rounded-xl bg-raised/80 border border-line/60 text-accent shadow-sm">
                    <Icon className="w-4 h-4" />
                  </span>
                  <button
                    type="button"
                    onClick={onClick}
                    disabled={disabled}
                    className={`h-6 px-3 rounded-lg text-xs font-medium transition-all ${
                      disabled
                        ? "bg-accent/15 border border-accent/30 text-link cursor-default"
                        : "bg-raised/90 border border-line/70 text-fg hover:bg-accent/20 hover:border-accent/50 hover:text-white"
                    }`}
                  >
                    {action}
                  </button>
                </div>
                <h3 className="text-[13px] font-semibold text-fg tracking-tight">{title}</h3>
                <p className="mt-1 text-xs text-muted leading-relaxed">{text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
