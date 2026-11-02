import React from "react";
import { AlertCircle, X } from "lucide-react";

interface ErrorBannerProps {
  error: string | null;
  onDismiss: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({ error, onDismiss }) => {
  if (!error) return null;

  return (
    <div className="bg-rose-500/10 border border-rose-500/30 rounded-xl p-4 flex items-start justify-between gap-3 text-rose-300 text-xs shadow-lg animate-in fade-in slide-in-from-top duration-200">
      <div className="flex items-start gap-2.5">
        <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <p className="font-semibold text-rose-200">Runtime Issue Encountered</p>
          <p className="text-rose-300/90 whitespace-pre-line font-mono text-[11px] leading-relaxed">
            {error}
          </p>
        </div>
      </div>
      <button
        onClick={onDismiss}
        className="text-rose-400 hover:text-rose-200 p-1 rounded-lg hover:bg-rose-500/20 transition"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
};

