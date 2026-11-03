import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, Settings } from "lucide-react";
import { AssistantProfile, EngineStatus, HardwareTelemetry } from "../types";

interface StatusBarProps {
  telemetry: HardwareTelemetry | null;
  engineStatus: EngineStatus | null;
  tokensPerSec: number;
  activeProfile?: AssistantProfile | null;
  profiles?: AssistantProfile[];
  onSelectProfile?: (profile: AssistantProfile) => void;
  onOpenSettings?: () => void;
}

const gb = (mb: number) => (mb / 1024).toFixed(1);

/** Top bar: persona, live resource numbers, settings. */
export const StatusBar: React.FC<StatusBarProps> = ({
  telemetry,
  tokensPerSec,
  activeProfile,
  profiles = [],
  onSelectProfile,
  onOpenSettings,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const gpu = telemetry?.gpu;
  const mem = telemetry?.memory;

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  return (
    <header className="h-11 shrink-0 border-b border-line bg-panel px-3 flex items-center justify-between select-none">
      <div className="relative" ref={menuRef}>
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="flex items-center gap-1.5 h-7 px-2 rounded-md text-[13px] text-fg hover:bg-raised transition-colors"
          title="Persona for new messages"
        >
          <span>{activeProfile?.name || "General Assistant"}</span>
          <ChevronDown className="w-3.5 h-3.5 text-muted" />
        </button>

        {menuOpen && (
          <div className="absolute left-0 top-full mt-1 w-64 rounded-md bg-panel border border-line shadow-menu py-1 z-30">
            {profiles.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  onSelectProfile?.(p);
                  setMenuOpen(false);
                }}
                className={`w-full text-left px-3 py-1.5 hover:bg-raised transition-colors ${
                  activeProfile?.id === p.id ? "text-fg" : "text-muted"
                }`}
              >
                <div className="text-[13px] flex items-center justify-between">
                  <span>{p.name}</span>
                  {activeProfile?.id === p.id && <span className="text-xs text-subtle">current</span>}
                </div>
                {p.description && <div className="text-xs text-subtle line-clamp-1">{p.description}</div>}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center gap-4 text-xs text-muted tabular-nums">
        {tokensPerSec > 0 && <span>{tokensPerSec} tok/s</span>}
        <span title={gpu?.available ? gpu.name : "No NVIDIA GPU detected"}>
          GPU {gpu?.available ? `${gb(gpu.vram_used_mb)} / ${gb(gpu.vram_total_mb)} GB` : "none"}
        </span>
        <span>RAM {mem ? `${gb(mem.ram_used_mb)} / ${gb(mem.ram_total_mb)} GB` : "—"}</span>
        <button
          onClick={onOpenSettings}
          className="h-7 w-7 flex items-center justify-center rounded-md text-muted hover:text-fg hover:bg-raised transition-colors"
          title="Settings"
          aria-label="Settings"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
};
