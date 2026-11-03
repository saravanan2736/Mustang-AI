import React from "react";
import { AlertCircle, X } from "lucide-react";

interface ErrorBannerProps {
  error: string | null;
  onDismiss: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({ error, onDismiss }) => {
  if (!error) return null;

  return (
    <div role="alert" className="mx-4 mt-2.5 flex items-start gap-3 rounded-2xl border border-rose-500/30 bg-rose-950/25 backdrop-blur-md px-4 py-2.5 text-[13px] shadow-lg animate-slide-up">
      <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
      <p className="flex-1 text-fg/90 whitespace-pre-line break-words leading-relaxed">{error}</p>
      <button
        onClick={onDismiss}
        className="p-1 rounded-lg text-rose-300/70 hover:text-rose-200 hover:bg-rose-900/30 transition-colors"
        title="Dismiss"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
};
