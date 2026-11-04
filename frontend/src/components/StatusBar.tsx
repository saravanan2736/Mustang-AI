import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, Settings } from "lucide-react";
import { AssistantProfile } from "../types";
import { pillCls } from "./ModelSelector";

interface HeaderActionsProps {
  activeProfile?: AssistantProfile | null;
  profiles?: AssistantProfile[];
  onSelectProfile?: (profile: AssistantProfile) => void;
  onOpenSettings?: () => void;
}

/** Right end of the header: persona menu and Settings. */
export const HeaderActions: React.FC<HeaderActionsProps> = ({
  activeProfile,
  profiles = [],
  onSelectProfile,
  onOpenSettings,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);

  return (
    <>
      <div className="relative" ref={menuRef}>
        <button onClick={() => setMenuOpen(!menuOpen)} className={pillCls} title="Persona for new messages">
          <span className="max-w-[80px] xl:max-w-[140px] truncate">{activeProfile?.name || "General Assistant"}</span>
          <ChevronDown className="w-4 h-4 text-muted" />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-full mt-1.5 w-64 rounded-2xl bg-panel/95 border border-line/80 shadow-menu p-1.5 z-30">
            {profiles.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  onSelectProfile?.(p);
                  setMenuOpen(false);
                }}
                className={`w-full text-left px-3 py-2 rounded-xl transition-all ${
                  activeProfile?.id === p.id
                    ? "bg-accent/15 text-fg border border-accent/30 font-medium shadow-sm"
                    : "text-muted hover:bg-raised/70 hover:text-fg border border-transparent"
                }`}
              >
                <div className="text-[13px] font-medium">{p.name}</div>
                {p.description && <div className="text-xs text-subtle line-clamp-1 mt-0.5">{p.description}</div>}
              </button>
            ))}
          </div>
        )}
      </div>
      <button onClick={onOpenSettings} className={pillCls} title="Settings">
        <span className="hidden xl:inline">Settings</span>
        <Settings className="w-4 h-4 text-muted" />
      </button>
    </>
  );
};
