import React, { useState, useEffect } from "react";
import { X, Sliders, User, Cpu, Save, RotateCcw, Plus, Trash2, Monitor } from "lucide-react";
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60">
      <div className="w-full max-w-2xl bg-canvas border border-line rounded-lg shadow-menu overflow-hidden flex flex-col max-h-[88vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-line bg-panel">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-accent/10 text-link">
              <Sliders className="w-4 h-4" />
            </div>
            <h2 className="text-base font-semibold text-fg">Application Settings</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-raised transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-line bg-canvas px-6 text-xs">
          <button
            onClick={() => setActiveTab("inference")}
            className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
              activeTab === "inference"
                ? "border-accent text-link"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            <Sliders className="w-3.5 h-3.5" />
            <span>Generation Parameters</span>
          </button>
          <button
            onClick={() => setActiveTab("profiles")}
            className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
              activeTab === "profiles"
                ? "border-accent text-link"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            <User className="w-3.5 h-3.5" />
            <span>Profiles & Personas</span>
          </button>
          <button
            onClick={() => setActiveTab("hardware")}
            className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
              activeTab === "hardware"
                ? "border-accent text-link"
                : "border-transparent text-muted hover:text-fg"
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>Hardware Context</span>
          </button>
          {isDesktop && (
            <button
              onClick={() => setActiveTab("desktop")}
              className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
                activeTab === "desktop"
                  ? "border-accent text-link"
                  : "border-transparent text-muted hover:text-fg"
              }`}
            >
              <Monitor className="w-3.5 h-3.5" />
              <span>Desktop</span>
            </button>
          )}
        </div>

        {/* Tab Contents */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* --- INFERENCE TAB --- */}
          {activeTab === "inference" && (
            <div className="space-y-5">
              {/* Temperature */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-fg">Temperature</span>
                  <span className="font-mono text-link">{settings.temperature.toFixed(2)}</span>
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
                  className="w-full accent-[rgb(var(--accent))] cursor-pointer"
                />
                <p className="text-xs text-muted mt-1">
                  Lower values make responses more focused and deterministic; higher values increase variety.
                </p>
              </div>

              {/* Top-P */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-fg">Top-P (Nucleus Sampling)</span>
                  <span className="font-mono text-link">{settings.top_p.toFixed(2)}</span>
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
                  className="w-full accent-[rgb(var(--accent))] cursor-pointer"
                />
                <p className="text-xs text-muted mt-1">
                  Limits next-token candidate pool to cumulative probability threshold.
                </p>
              </div>

              {/* Top-K */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-fg">Top-K</span>
                  <span className="font-mono text-link">{settings.top_k}</span>
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
                  className="w-full accent-[rgb(var(--accent))] cursor-pointer"
                />
              </div>

              {/* Repetition Penalty */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-fg">Repetition Penalty</span>
                  <span className="font-mono text-link">{settings.repeat_penalty.toFixed(2)}</span>
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
                  className="w-full accent-[rgb(var(--accent))] cursor-pointer"
                />
                <p className="text-xs text-muted mt-1">
                  Penalizes repetitive vocabulary patterns. 1.0 is neutral, 1.1 - 1.15 recommended.
                </p>
              </div>

              {/* Max Tokens */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-fg">Maximum Generation Length</span>
                  <span className="font-mono text-link">{settings.max_tokens} tokens</span>
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
                  className="w-full accent-[rgb(var(--accent))] cursor-pointer"
                />
              </div>
            </div>
          )}

          {/* --- PROFILES TAB --- */}
          {activeTab === "profiles" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">
                  Select, customize, or create assistant profiles with customized system prompts.
                </span>
                <button
                  type="button"
                  onClick={() => setShowNewProfileForm(!showNewProfileForm)}
                  className="flex items-center gap-1.5 text-xs text-link hover:underline font-medium px-2.5 py-1 rounded-lg bg-accent/10 border border-accent"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>New Profile</span>
                </button>
              </div>

              {/* New Profile Form */}
              {showNewProfileForm && (
                <form
                  onSubmit={handleCreateProfile}
                  className="p-4 rounded-md bg-panel border border-accent space-y-3"
                >
                  <h4 className="text-xs font-semibold text-fg">Create Custom Assistant Profile</h4>
                  <div>
                    <label className="text-xs text-muted block mb-1">Profile Name</label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. Legal Contract Analyst"
                      value={newProfileName}
                      onChange={(e) => setNewProfileName(e.target.value)}
                      className="w-full bg-canvas border border-line rounded-lg px-3 py-1.5 text-xs text-fg focus:outline-none focus:border-accent"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted block mb-1">Short Description</label>
                    <input
                      type="text"
                      placeholder="e.g. Reviews clause language and identifies liabilities"
                      value={newProfileDesc}
                      onChange={(e) => setNewProfileDesc(e.target.value)}
                      className="w-full bg-canvas border border-line rounded-lg px-3 py-1.5 text-xs text-fg focus:outline-none focus:border-accent"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted block mb-1">System Instructions</label>
                    <textarea
                      required
                      rows={3}
                      placeholder="You are an expert contract lawyer..."
                      value={newProfilePrompt}
                      onChange={(e) => setNewProfilePrompt(e.target.value)}
                      className="w-full bg-canvas border border-line rounded-lg px-3 py-1.5 text-xs text-fg focus:outline-none focus:border-accent resize-none"
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
                      className="px-3 py-1 text-xs bg-accent hover:bg-accent-hover text-fg font-medium rounded-lg"
                    >
                      Create Profile
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
                      className={`p-3.5 rounded-md border text-xs cursor-pointer transition flex items-start justify-between ${
                        isSelected
                          ? "bg-accent/10 border-accent"
                          : "bg-panel border-line hover:border-line"
                      }`}
                    >
                      <div className="flex-1 pr-4">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-semibold text-fg">{p.name}</span>
                          {p.is_builtin ? (
                            <span className="px-1.5 py-0.2 rounded text-xs bg-raised text-fg font-mono">
                              Built-in
                            </span>
                          ) : (
                            <span className="px-1.5 py-0.2 rounded text-xs bg-accent/10 text-link font-mono">
                              Custom
                            </span>
                          )}
                          {isSelected && (
                            <span className="text-xs text-link font-medium">● Active Default</span>
                          )}
                        </div>
                        <p className="text-muted text-xs leading-relaxed mb-2">
                          {p.description}
                        </p>
                        <p className="text-xs font-mono text-muted line-clamp-2 bg-canvas p-1.5 rounded">
                          {p.system_prompt}
                        </p>
                      </div>

                      {!p.is_builtin && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteProfile(p.id);
                          }}
                          className="p-1 text-muted hover:text-danger transition"
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
              <div>
                <label className="text-xs font-medium text-fg block mb-1.5">
                  Default Context Window Size
                </label>
                <div className="grid grid-cols-4 gap-2">
                  {[2048, 4096, 8192, 16384].map((ctx) => (
                    <button
                      key={ctx}
                      type="button"
                      onClick={() => setSettings({ ...settings, context_size: ctx })}
                      className={`py-2 px-3 rounded-md text-xs font-mono font-medium border transition ${
                        settings.context_size === ctx
                          ? "bg-accent text-fg border-accent"
                          : "bg-panel border-line text-fg hover:border-line"
                      }`}
                    >
                      {ctx} tokens
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted mt-2">
                  Larger context sizes allocate more VRAM for the KV cache. 4,096 tokens fits comfortably on 8GB VRAM.
                </p>
              </div>

              <div className="p-4 rounded-md bg-panel border border-line text-xs space-y-2">
                <div className="flex items-center gap-2 font-semibold text-fg">
                  <Cpu className="w-4 h-4 text-link" />
                  <span>Target GPU Configuration</span>
                </div>
                <p className="text-muted leading-relaxed text-xs">
                  Configured for NVIDIA RTX 5050 Laptop GPU (8GB VRAM) and 24GB DDR5 System RAM. CUDA acceleration and Flash Attention are automatically activated.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions (generation settings; the Desktop tab applies changes immediately) */}
        {activeTab !== "desktop" && (
        <div className="flex items-center justify-between px-6 py-4 border-t border-line bg-panel">
          <button
            onClick={handleResetDefaults}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-muted hover:text-fg transition"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Defaults</span>
          </button>

          <div className="flex items-center gap-3">
            {saveSuccess && (
              <span className="text-xs text-success font-medium">Settings saved!</span>
            )}
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-md bg-accent hover:bg-accent-hover text-fg font-medium text-xs transition disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              <span>{isSaving ? "Saving..." : "Save Settings"}</span>
            </button>
          </div>
        </div>
        )}
      </div>
    </div>
  );
};

