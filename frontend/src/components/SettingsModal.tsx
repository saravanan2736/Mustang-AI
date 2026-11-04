import React, { useState, useEffect } from "react";
import { X, RotateCcw, Plus, Trash2 } from "lucide-react";
import { isDesktop } from "../services/connection";
import { DesktopSettingsPanel } from "./DesktopSettingsPanel";
import { AppSettings, AssistantProfile } from "../types";
import {
  fetchSettings,
  updateSettings,
  fetchProfiles,
  createProfile,
  deleteProfile,
} from "../services/api";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSettingsSaved?: (settings: AppSettings) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  onSettingsSaved,
}) => {
  const [activeTab, setActiveTab] = useState<"inference" | "profiles" | "hardware" | "desktop">("inference");
  const [settings, setSettings] = useState<AppSettings>({
    temperature: 0.7,
    top_p: 0.9,
    top_k: 40,
    repeat_penalty: 1.1,
    max_tokens: 2048,
    context_size: 4096,
    default_profile: "default",
    custom_system_prompt: "",
  });
  const [profiles, setProfiles] = useState<AssistantProfile[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // New Profile Form
  const [newProfileName, setNewProfileName] = useState("");
  const [newProfileDesc, setNewProfileDesc] = useState("");
  const [newProfilePrompt, setNewProfilePrompt] = useState("");
  const [showNewProfileForm, setShowNewProfileForm] = useState(false);

  useEffect(() => {
    if (isOpen) {
      fetchSettings()
        .then(setSettings)
        .catch((err) => console.error("Error loading settings:", err));
      loadProfilesList();
    }
  }, [isOpen]);

  const loadProfilesList = () => {
    fetchProfiles()
      .then(setProfiles)
      .catch((err) => console.error("Error loading profiles:", err));
  };

  if (!isOpen) return null;

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const updated = await updateSettings(settings);
      setSettings(updated);
      onSettingsSaved?.(updated);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
    } catch (err) {
      console.error("Save settings failed:", err);
    } finally {
      setIsSaving(false);
    }
  };

  const handleResetDefaults = () => {
    setSettings({
      temperature: 0.7,
      top_p: 0.9,
      top_k: 40,
      repeat_penalty: 1.1,
      max_tokens: 2048,
      context_size: 4096,
      default_profile: "default",
      custom_system_prompt: "",
    });
  };

  const handleCreateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProfileName.trim() || !newProfilePrompt.trim()) return;
    try {
      await createProfile({
        name: newProfileName.trim(),
        description: newProfileDesc.trim(),
        system_prompt: newProfilePrompt.trim(),
      });
      setNewProfileName("");
      setNewProfileDesc("");
      setNewProfilePrompt("");
      setShowNewProfileForm(false);
      loadProfilesList();
    } catch (err) {
      console.error("Failed to create profile:", err);
    }
  };

  const handleDeleteProfile = async (id: string) => {
    if (window.confirm("Delete this custom assistant profile?")) {
      try {
        await deleteProfile(id);
        loadProfilesList();
      } catch (err) {
        console.error("Failed to delete profile:", err);
      }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70">
      <div className="w-full max-w-2xl bg-surface/95 border border-line/80 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[88vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 h-13 border-b border-line/50 bg-panel/70">
          <h2 className="text-[15px] font-semibold tracking-tight text-fg">Settings</h2>
          <button
            onClick={onClose}
            className="h-8 w-8 flex items-center justify-center rounded-lg text-muted hover:text-fg hover:bg-raised/80 transition-colors"
            title="Close"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-line/50 bg-panel/40 px-5 text-[13px] gap-2 pt-1">
          <button
            onClick={() => setActiveTab("inference")}
            className={`h-9 px-3 border-b-2 -mb-px transition-all font-medium ${
              activeTab === "inference"
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            Generation
          </button>
          <button
            onClick={() => setActiveTab("profiles")}
            className={`h-9 px-3 border-b-2 -mb-px transition-all font-medium ${
              activeTab === "profiles"
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            Personas
          </button>
          <button
            onClick={() => setActiveTab("hardware")}
            className={`h-9 px-3 border-b-2 -mb-px transition-all font-medium ${
              activeTab === "hardware"
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            Defaults
          </button>
          {isDesktop && (
            <button
              onClick={() => setActiveTab("desktop")}
              className={`h-9 px-3 border-b-2 -mb-px transition-all font-medium ${
                activeTab === "desktop"
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-fg"
              }`}
            >
              Desktop
            </button>
          )}
        </div>

        {/* Tab Contents */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* --- INFERENCE TAB --- */}
          {activeTab === "inference" && (
            <div className="space-y-5">
              {/* Temperature */}
              <div className="rounded-xl bg-panel/50 border border-line/50 p-4">
                <div className="flex justify-between text-xs mb-2">
                  <span className="text-fg font-medium">Temperature</span>
                  <span className="text-accent font-mono font-medium">{settings.temperature.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.05"
                  value={settings.temperature}
                  onChange={(e) =>
                    setSettings({ ...settings, temperature: parseFloat(e.target.value) })
                  }
                  className="w-full accent-accent cursor-pointer"
                />
                <p className="text-xs text-muted mt-2">
                  Lower values make responses more focused and deterministic; higher values increase variety.
                </p>
              </div>

              {/* Top-P */}
              <div className="rounded-xl bg-panel/50 border border-line/50 p-4">
                <div className="flex justify-between text-xs mb-2">
                  <span className="text-fg font-medium">Top-P (Nucleus Sampling)</span>
                  <span className="text-accent font-mono font-medium">{settings.top_p.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="0.05"
                  max="1"
                  step="0.05"
                  value={settings.top_p}
                  onChange={(e) =>
                    setSettings({ ...settings, top_p: parseFloat(e.target.value) })
                  }
                  className="w-full accent-accent cursor-pointer"
                />
                <p className="text-xs text-muted mt-2">
                  Limits next-token candidate pool to cumulative probability threshold.
                </p>
              </div>

              {/* Top-K */}
              <div className="rounded-xl bg-panel/50 border border-line/50 p-4">
                <div className="flex justify-between text-xs mb-2">
                  <span className="text-fg font-medium">Top-K</span>
                  <span className="text-accent font-mono font-medium">{settings.top_k}</span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="100"
                  step="1"
                  value={settings.top_k}
                  onChange={(e) =>
                    setSettings({ ...settings, top_k: parseInt(e.target.value, 10) })
                  }
                  className="w-full accent-accent cursor-pointer"
                />
              </div>

              {/* Repetition Penalty */}
              <div className="rounded-xl bg-panel/50 border border-line/50 p-4">
                <div className="flex justify-between text-xs mb-2">
                  <span className="text-fg font-medium">Repetition Penalty</span>
                  <span className="text-accent font-mono font-medium">{settings.repeat_penalty.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="1.0"
                  max="1.5"
                  step="0.05"
                  value={settings.repeat_penalty}
                  onChange={(e) =>
                    setSettings({ ...settings, repeat_penalty: parseFloat(e.target.value) })
                  }
                  className="w-full accent-accent cursor-pointer"
                />
                <p className="text-xs text-muted mt-2">
                  Penalizes repetitive vocabulary patterns. 1.0 is neutral, 1.1 - 1.15 recommended.
                </p>
              </div>

              {/* Max Tokens */}
              <div className="rounded-xl bg-panel/50 border border-line/50 p-4">
                <div className="flex justify-between text-xs mb-2">
                  <span className="text-fg font-medium">Maximum Generation Length</span>
                  <span className="text-accent font-mono font-medium">{settings.max_tokens} tokens</span>
                </div>
                <input
                  type="range"
                  min="128"
                  max="8192"
                  step="128"
                  value={settings.max_tokens}
                  onChange={(e) =>
                    setSettings({ ...settings, max_tokens: parseInt(e.target.value, 10) })
                  }
                  className="w-full accent-accent cursor-pointer"
                />
              </div>
            </div>
          )}

          {/* --- PROFILES TAB --- */}
          {activeTab === "profiles" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">
                  The default persona is used for new chats. Each persona is a system prompt.
                </span>
                <button
                  type="button"
                  onClick={() => setShowNewProfileForm(!showNewProfileForm)}
                  className="flex items-center gap-1.5 h-7 px-3 rounded-lg border border-line/70 bg-panel/60 text-xs font-medium text-fg hover:bg-raised/80 hover:border-accent/40 transition-all shadow-sm"
                >
                  <Plus className="w-3.5 h-3.5 text-accent" />
                  <span>New persona</span>
                </button>
              </div>

              {/* New Profile Form */}
              {showNewProfileForm && (
                <form
                  onSubmit={handleCreateProfile}
                  className="p-4 rounded-xl bg-panel/80 border border-accent/70 space-y-3"
                >
                  <h4 className="text-xs font-semibold text-fg">New persona</h4>
                  <div>
                    <label className="text-xs text-muted block mb-1">Name</label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. Legal Contract Analyst"
                      value={newProfileName}
                      onChange={(e) => setNewProfileName(e.target.value)}
                      className="w-full bg-canvas/80 border border-line/80 rounded-lg px-3 py-1.5 text-xs text-fg focus:outline-none focus:border-accent/80 focus:ring-1 focus:ring-accent/30 transition-all"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted block mb-1">Description</label>
                    <input
                      type="text"
                      placeholder="e.g. Reviews clause language and identifies liabilities"
                      value={newProfileDesc}
                      onChange={(e) => setNewProfileDesc(e.target.value)}
                      className="w-full bg-canvas/80 border border-line/80 rounded-lg px-3 py-1.5 text-xs text-fg focus:outline-none focus:border-accent/80 focus:ring-1 focus:ring-accent/30 transition-all"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted block mb-1">System prompt</label>
                    <textarea
                      required
                      rows={3}
                      placeholder="You are an expert contract lawyer..."
                      value={newProfilePrompt}
                      onChange={(e) => setNewProfilePrompt(e.target.value)}
                      className="w-full bg-canvas/80 border border-line/80 rounded-lg px-3 py-1.5 text-xs text-fg focus:outline-none focus:border-accent/80 focus:ring-1 focus:ring-accent/30 transition-all resize-none"
                    />
                  </div>
                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => setShowNewProfileForm(false)}
                      className="px-3 py-1 text-xs text-muted hover:text-fg"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="px-3.5 py-1 text-xs btn-violet text-white font-medium rounded-lg hover:brightness-110"
                    >
                      Create
                    </button>
                  </div>
                </form>
              )}

              {/* Profiles List */}
              <div className="space-y-2.5">
                {profiles.map((p) => {
                  const isSelected = settings.default_profile === p.id;
                  return (
                    <div
                      key={p.id}
                      onClick={() => setSettings({ ...settings, default_profile: p.id })}
                      className={`p-3.5 rounded-xl border text-xs cursor-pointer transition-all flex items-start justify-between ${
                        isSelected
                          ? "bg-accent/15 border-accent/70"
                          : "bg-panel/50 border-line/60 hover:border-line hover:bg-panel/80"
                      }`}
                    >
                      <div className="flex-1 pr-4">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[13px] font-semibold text-fg">{p.name}</span>
                          <span className="text-[11px] px-1.5 py-0.2 rounded bg-raised/80 text-subtle font-medium">
                            {p.is_builtin ? "Built-in" : "Custom"}
                          </span>
                          {isSelected && (
                            <span className="text-[11px] px-2 py-0.2 rounded-full bg-accent/20 border border-accent/40 text-link font-medium">
                              Default
                            </span>
                          )}
                        </div>
                        <p className="text-muted text-xs leading-relaxed mb-2">
                          {p.description}
                        </p>
                        <p className="text-xs text-subtle line-clamp-2">
                          {p.system_prompt}
                        </p>
                      </div>

                      {!p.is_builtin && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteProfile(p.id);
                          }}
                          className="p-1 rounded text-muted hover:text-danger hover:bg-panel transition"
                          title="Delete profile"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* --- DESKTOP TAB (desktop app only) --- */}
          {activeTab === "desktop" && isDesktop && <DesktopSettingsPanel />}

          {/* --- HARDWARE TAB --- */}
          {activeTab === "hardware" && (
            <div className="space-y-5">
              <div className="rounded-xl bg-panel/50 border border-line/50 p-4">
                <label className="text-xs font-medium text-fg block mb-2">Default context size</label>
                <div className="grid grid-cols-4 gap-2">
                  {[2048, 4096, 8192, 16384].map((ctx) => (
                    <button
                      key={ctx}
                      type="button"
                      onClick={() => setSettings({ ...settings, context_size: ctx })}
                      className={`h-9 px-3 rounded-xl text-xs font-mono font-medium border transition-all ${
                        settings.context_size === ctx
                          ? "bg-accent/20 text-fg border-accent/80"
                          : "bg-panel/70 border-line/60 text-muted hover:text-fg hover:border-line"
                      }`}
                    >
                      {ctx.toLocaleString()} tokens
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted mt-3 leading-relaxed">
                  Used when a model is loaded without choosing a size. Larger contexts need more GPU memory; the model toolbar shows the estimate for the selected model.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        {activeTab !== "desktop" && (
          <div className="flex items-center justify-between px-6 py-3 border-t border-line/50 bg-panel/70">
            <button
              onClick={handleResetDefaults}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-muted hover:text-fg hover:bg-raised/70 transition-all"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Reset to defaults</span>
            </button>

            <div className="flex items-center gap-3">
              {saveSuccess && (
                <span className="text-xs font-medium text-success">Saved</span>
              )}
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="h-8 px-4 rounded-xl btn-violet text-white font-medium text-[13px] hover:brightness-110 transition-all disabled:opacity-50"
              >
                {isSaving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
