import React from "react";
import { AlertCircle, X } from "lucide-react";

interface ErrorBannerProps {
  error: string | null;
  onDismiss: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({ error, onDismiss }) => {
  if (!error) return null;

  return (
    <div role="alert" className="mx-4 mt-3 flex items-start gap-2.5 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[13px]">
      <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
      <p className="flex-1 text-fg whitespace-pre-line break-words">{error}</p>
      <button
        onClick={onDismiss}
        className="p-0.5 rounded text-muted hover:text-fg"
        title="Dismiss"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
};
