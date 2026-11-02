import React, { useState } from "react";
import {
  Cpu,
  HardDrive,
  Zap,
  AlertCircle,
  Sliders,
  User,
  ChevronDown,
} from "lucide-react";
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

export const StatusBar: React.FC<StatusBarProps> = ({
  telemetry,
  engineStatus,
  tokensPerSec,
  activeProfile,
  profiles = [],
  onSelectProfile,
  onOpenSettings,
}) => {
  const [showProfileDropdown, setShowProfileDropdown] = useState(false);
  const gpu = telemetry?.gpu;
  const mem = telemetry?.memory;
  const state = engineStatus?.state || "UNLOADED";

  const getStatusBadge = () => {
    switch (state) {
      case "READY":
        return (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
            Ready
          </span>
        );
      case "GENERATING":
        return (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-ping"></span>
            Generating
          </span>
        );
      case "STARTING":
        return (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"></span>
            Starting...
          </span>
        );
      case "ERROR":
        return (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <AlertCircle className="w-3.5 h-3.5" />
            Error
          </span>
        );
      default:
        return (
          <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-slate-800/60 text-slate-400 border border-slate-700/50">
            <span className="w-1.5 h-1.5 rounded-full bg-slate-500"></span>
            Unloaded
          </span>
        );
    }
  };

  const vramUsedGb = gpu?.available ? (gpu.vram_used_mb / 1024).toFixed(1) : "0.0";
  const vramTotalGb = gpu?.available ? (gpu.vram_total_mb / 1024).toFixed(1) : "0.0";
  const ramUsedGb = mem ? (mem.ram_used_mb / 1024).toFixed(1) : "0.0";
  const ramTotalGb = mem ? (mem.ram_total_mb / 1024).toFixed(1) : "0.0";

  return (
    <header className="h-14 border-b border-white/[0.08] bg-[#07090E]/95 backdrop-blur-md px-5 flex items-center justify-between select-none z-20">
      <div className="flex items-center gap-3">
        {getStatusBadge()}

        {/* Profile Selector Pill */}
        <div className="relative">
          <button
            onClick={() => setShowProfileDropdown(!showProfileDropdown)}
            className="flex items-center gap-1.5 px-3 py-1 rounded-xl bg-[#0D1220] hover:bg-[#12182B] border border-white/[0.08] text-xs text-slate-200 transition"
          >
            <User className="w-3.5 h-3.5 text-indigo-400" />
            <span className="font-medium">{activeProfile?.name || "General Assistant"}</span>
            <ChevronDown className="w-3 h-3 text-slate-400" />
          </button>

          {showProfileDropdown && (
            <div className="absolute left-0 mt-1.5 w-60 rounded-xl bg-[#0B0F1D] border border-white/10 shadow-2xl py-1 z-30 animate-fadeIn">
              <div className="px-3 py-1.5 text-[10px] uppercase font-semibold text-slate-400 tracking-wider">
                Select Persona Profile
              </div>
              {profiles.map((p) => (
                <button
                  key={p.id}
                  onClick={() => {
                    onSelectProfile?.(p);
                    setShowProfileDropdown(false);
                  }}
                  className={`w-full text-left px-3 py-2 text-xs flex flex-col transition hover:bg-white/[0.06] ${
                    activeProfile?.id === p.id
                      ? "text-indigo-400 font-semibold bg-indigo-600/10"
                      : "text-slate-300"
                  }`}
                >
                  <span>{p.name}</span>
                  <span className="text-[10px] text-slate-400 font-normal line-clamp-1">
                    {p.description}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Telemetry and Controls */}
      <div className="flex items-center gap-5 text-xs text-slate-400">
        {/* Token speed */}
        <div className="flex items-center gap-1.5">
          <Zap className="w-3.5 h-3.5 text-amber-400" />
          <span>Speed:</span>
          <span className="font-mono font-medium text-slate-200">
            {tokensPerSec > 0 ? `${tokensPerSec} tok/s` : "—"}
          </span>
        </div>

        {/* VRAM Telemetry */}
        <div className="flex items-center gap-1.5">
          <HardDrive className="w-3.5 h-3.5 text-indigo-400" />
          <span>VRAM (RTX 5050):</span>
          <span className="font-mono font-medium text-slate-200">
            {gpu?.available ? `${vramUsedGb} / ${vramTotalGb} GB` : "No GPU"}
          </span>
        </div>

        {/* DDR5 Host RAM */}
        <div className="flex items-center gap-1.5">
          <Cpu className="w-3.5 h-3.5 text-sky-400" />
          <span>RAM:</span>
          <span className="font-mono font-medium text-slate-200">
            {mem ? `${ramUsedGb} / ${ramTotalGb} GB` : "—"}
          </span>
        </div>

        {/* Settings button */}
        <button
          onClick={onOpenSettings}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
          title="Application Settings"
        >
          <Sliders className="w-4 h-4 text-slate-300" />
          <span className="text-xs font-medium">Settings</span>
        </button>
      </div>
    </header>
  );
};
