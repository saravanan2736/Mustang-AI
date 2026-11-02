import React, { useState, useEffect } from "react";
import { X, Sliders, User, Cpu, Save, RotateCcw, Plus, Trash2 } from "lucide-react";
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
  const [activeTab, setActiveTab] = useState<"inference" | "profiles" | "hardware">("inference");
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fadeIn">
      <div className="w-full max-w-2xl bg-[#090C15] border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[88vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08] bg-[#0D1220]">
          <div className="flex items-center gap-2.5">
            <div className="p-1.5 rounded-lg bg-indigo-600/20 text-indigo-400">
              <Sliders className="w-4 h-4" />
            </div>
            <h2 className="text-base font-semibold text-white">Application Settings</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-white/[0.06] bg-[#07090E] px-6 text-xs">
          <button
            onClick={() => setActiveTab("inference")}
            className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
              activeTab === "inference"
                ? "border-indigo-500 text-indigo-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Sliders className="w-3.5 h-3.5" />
            <span>Generation Parameters</span>
          </button>
          <button
            onClick={() => setActiveTab("profiles")}
            className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
              activeTab === "profiles"
                ? "border-indigo-500 text-indigo-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <User className="w-3.5 h-3.5" />
            <span>Profiles & Personas</span>
          </button>
          <button
            onClick={() => setActiveTab("hardware")}
            className={`flex items-center gap-2 py-3 px-3 font-medium border-b-2 transition ${
              activeTab === "hardware"
                ? "border-indigo-500 text-indigo-400"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            <Cpu className="w-3.5 h-3.5" />
            <span>Hardware Context</span>
          </button>
        </div>

        {/* Tab Contents */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* --- INFERENCE TAB --- */}
          {activeTab === "inference" && (
            <div className="space-y-5">
              {/* Temperature */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-slate-200">Temperature</span>
                  <span className="font-mono text-indigo-400">{settings.temperature.toFixed(2)}</span>
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
                  className="w-full accent-indigo-500 cursor-pointer"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  Lower values make responses more focused and deterministic; higher values increase variety.
                </p>
              </div>

              {/* Top-P */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-slate-200">Top-P (Nucleus Sampling)</span>
                  <span className="font-mono text-indigo-400">{settings.top_p.toFixed(2)}</span>
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
                  className="w-full accent-indigo-500 cursor-pointer"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  Limits next-token candidate pool to cumulative probability threshold.
                </p>
              </div>

              {/* Top-K */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-slate-200">Top-K</span>
                  <span className="font-mono text-indigo-400">{settings.top_k}</span>
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
                  className="w-full accent-indigo-500 cursor-pointer"
                />
              </div>

              {/* Repetition Penalty */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-slate-200">Repetition Penalty</span>
                  <span className="font-mono text-indigo-400">{settings.repeat_penalty.toFixed(2)}</span>
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
                  className="w-full accent-indigo-500 cursor-pointer"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  Penalizes repetitive vocabulary patterns. 1.0 is neutral, 1.1 - 1.15 recommended.
                </p>
              </div>

              {/* Max Tokens */}
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="font-medium text-slate-200">Maximum Generation Length</span>
                  <span className="font-mono text-indigo-400">{settings.max_tokens} tokens</span>
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
                  className="w-full accent-indigo-500 cursor-pointer"
                />
              </div>
            </div>
          )}

          {/* --- PROFILES TAB --- */}
          {activeTab === "profiles" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400">
                  Select, customize, or create assistant profiles with customized system prompts.
                </span>
                <button
                  type="button"
                  onClick={() => setShowNewProfileForm(!showNewProfileForm)}
                  className="flex items-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300 font-medium px-2.5 py-1 rounded-lg bg-indigo-600/10 border border-indigo-500/20"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>New Profile</span>
                </button>
              </div>

              {/* New Profile Form */}
              {showNewProfileForm && (
                <form
                  onSubmit={handleCreateProfile}
                  className="p-4 rounded-xl bg-[#0D1220] border border-indigo-500/30 space-y-3 animate-fadeIn"
                >
                  <h4 className="text-xs font-semibold text-white">Create Custom Assistant Profile</h4>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">Profile Name</label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. Legal Contract Analyst"
                      value={newProfileName}
                      onChange={(e) => setNewProfileName(e.target.value)}
                      className="w-full bg-[#07090E] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">Short Description</label>
                    <input
                      type="text"
                      placeholder="e.g. Reviews clause language and identifies liabilities"
                      value={newProfileDesc}
                      onChange={(e) => setNewProfileDesc(e.target.value)}
                      className="w-full bg-[#07090E] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">System Instructions</label>
                    <textarea
                      required
                      rows={3}
                      placeholder="You are an expert contract lawyer..."
                      value={newProfilePrompt}
                      onChange={(e) => setNewProfilePrompt(e.target.value)}
                      className="w-full bg-[#07090E] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500 resize-none"
                    />
                  </div>
                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => setShowNewProfileForm(false)}
                      className="px-3 py-1 text-xs text-slate-400 hover:text-white"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="px-3 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg"
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
                      className={`p-3.5 rounded-xl border text-xs cursor-pointer transition flex items-start justify-between ${
                        isSelected
                          ? "bg-indigo-600/15 border-indigo-500/50 shadow-subtle"
                          : "bg-[#0D1220]/60 border-white/[0.06] hover:border-white/10"
                      }`}
                    >
                      <div className="flex-1 pr-4">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-semibold text-white">{p.name}</span>
                          {p.is_builtin ? (
                            <span className="px-1.5 py-0.2 rounded text-[10px] bg-slate-800 text-slate-300 font-mono">
                              Built-in
                            </span>
                          ) : (
                            <span className="px-1.5 py-0.2 rounded text-[10px] bg-indigo-900/60 text-indigo-300 font-mono">
                              Custom
                            </span>
                          )}
                          {isSelected && (
                            <span className="text-[10px] text-indigo-400 font-medium">● Active Default</span>
                          )}
                        </div>
                        <p className="text-slate-400 text-[11px] leading-relaxed mb-2">
                          {p.description}
                        </p>
                        <p className="text-[11px] font-mono text-slate-400 line-clamp-2 bg-[#07090E]/60 p-1.5 rounded">
                          {p.system_prompt}
                        </p>
                      </div>

                      {!p.is_builtin && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteProfile(p.id);
                          }}
                          className="p-1 text-slate-400 hover:text-rose-400 transition"
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

          {/* --- HARDWARE TAB --- */}
          {activeTab === "hardware" && (
            <div className="space-y-5">
              <div>
                <label className="text-xs font-medium text-slate-200 block mb-1.5">
                  Default Context Window Size
                </label>
                <div className="grid grid-cols-4 gap-2">
                  {[2048, 4096, 8192, 16384].map((ctx) => (
                    <button
                      key={ctx}
                      type="button"
                      onClick={() => setSettings({ ...settings, context_size: ctx })}
                      className={`py-2 px-3 rounded-xl text-xs font-mono font-medium border transition ${
                        settings.context_size === ctx
                          ? "bg-indigo-600 text-white border-indigo-400 shadow-indigo-glow"
                          : "bg-[#0D1220] border-white/10 text-slate-300 hover:border-white/20"
                      }`}
                    >
                      {ctx} tokens
                    </button>
                  ))}
                </div>
                <p className="text-[11px] text-slate-400 mt-2">
                  Larger context sizes allocate more VRAM for the KV cache. 4,096 tokens fits comfortably on 8GB VRAM.
                </p>
              </div>

              <div className="p-4 rounded-xl bg-[#0D1220] border border-white/[0.08] text-xs space-y-2">
                <div className="flex items-center gap-2 font-semibold text-white">
                  <Cpu className="w-4 h-4 text-indigo-400" />
                  <span>Target GPU Configuration</span>
                </div>
                <p className="text-slate-400 leading-relaxed text-[11px]">
                  Configured for NVIDIA RTX 5050 Laptop GPU (8GB VRAM) and 24GB DDR5 System RAM. CUDA acceleration and Flash Attention are automatically activated.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-white/[0.08] bg-[#0D1220]">
          <button
            onClick={handleResetDefaults}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-400 hover:text-white transition"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Defaults</span>
          </button>

          <div className="flex items-center gap-3">
            {saveSuccess && (
              <span className="text-xs text-emerald-400 font-medium">Settings saved!</span>
            )}
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-xs shadow-indigo-glow transition disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              <span>{isSaving ? "Saving..." : "Save Settings"}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

