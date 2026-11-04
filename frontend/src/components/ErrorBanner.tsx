import React from "react";
import { AlertCircle, X } from "lucide-react";

interface ErrorBannerProps {
  error: string | null;
  onDismiss: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({ error, onDismiss }) => {
  if (!error) return null;

  return (
    <div role="alert" className="mx-4 mt-2.5 flex items-start gap-3 rounded-2xl border border-danger/30 bg-danger/10 px-4 py-2.5 text-[13px]">
      <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
      <p className="flex-1 text-fg/90 whitespace-pre-line break-words leading-relaxed">{error}</p>
      <button
        onClick={onDismiss}
        className="p-1 rounded-lg text-danger/70 hover:opacity-80 hover:bg-danger/20 transition-colors"
        title="Dismiss"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
};
