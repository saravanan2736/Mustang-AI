import { useState, useEffect, useCallback } from "react";
import { StatusBar } from "./components/StatusBar";
import { Sidebar } from "./components/Sidebar";
import { ModelSelector } from "./components/ModelSelector";
import { ChatInterface } from "./components/ChatInterface";
import { PromptInput } from "./components/PromptInput";
import { ErrorBanner } from "./components/ErrorBanner";
import { SettingsModal } from "./components/SettingsModal";
import { useHardware } from "./hooks/useHardware";
import { useChatStream } from "./hooks/useChatStream";
import {
  bootstrapAuth,
  fetchEngineStatus,
  fetchConversations,
  fetchConversation,
  createConversation,
  updateConversation,
  deleteConversation,
  fetchProfiles,
  fetchSettings,
} from "./services/api";
import {
  AppSettings,
  AssistantProfile,
  ConversationSummary,
  EngineStatus,
} from "./types";

export default function App() {
  const { telemetry } = useHardware(2000);
  const [engineStatus, setEngineStatus] = useState<EngineStatus | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Conversations & UI Navigation State
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [externalPrompt, setExternalPrompt] = useState<string | undefined>();

  // Profiles and Settings
  const [profiles, setProfiles] = useState<AssistantProfile[]>([]);
  const [activeProfile, setActiveProfile] = useState<AssistantProfile | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings>({
    temperature: 0.7,
    top_p: 0.9,
    top_k: 40,
    repeat_penalty: 1.1,
    max_tokens: 2048,
    context_size: 4096,
    default_profile: "default",
    custom_system_prompt: "",
  });

  const {
    messages,
    setMessages,
    isGenerating,
    streamError,
    tokensPerSec,
    sendMessage,
    regenerateResponse,
    editAndResendMessage,
    stopStream,
  } = useChatStream();

  const loadConversationsList = useCallback(async () => {
    try {
      const list = await fetchConversations();
      setConversations(list);
      return list;
    } catch (err) {
      console.warn("Could not load conversations:", err);
      return [];
    }
  }, []);

  // Initial Bootstrap & Polling
  useEffect(() => {
    bootstrapAuth();

    fetchEngineStatus()
      .then(setEngineStatus)
      .catch((err) => console.debug("Status check notice:", err));

    const statusInterval = setInterval(() => {
      fetchEngineStatus()
        .then(setEngineStatus)
        .catch(() => {});
    }, 2500);

    // Load initial profiles & settings
    fetchProfiles()
      .then((profs) => {
        setProfiles(profs);
        if (profs.length > 0) {
          const def = profs.find((p) => p.id === "default") || profs[0];
          setActiveProfile(def);
        }
      })
      .catch((err) => console.warn("Could not load profiles:", err));

    fetchSettings()
      .then(setAppSettings)
      .catch((err) => console.warn("Could not load settings:", err));

    loadConversationsList().then((list) => {
      if (list.length > 0 && !activeConversationId) {
        // Automatically open the most recent conversation
        handleSelectConversation(list[0].id);
      }
    });

    return () => clearInterval(statusInterval);
  }, [loadConversationsList]);

  // Refresh conversation list after generation finishes to update titles and previews
  useEffect(() => {
    if (!isGenerating && activeConversationId) {
      loadConversationsList();
    }
  }, [isGenerating, activeConversationId, loadConversationsList]);

  const handleSelectConversation = async (id: string) => {
    try {
      setActiveConversationId(id);
      const detail = await fetchConversation(id);
      setMessages(detail.messages);
      // If conversation has a custom profile or system prompt, match profile
      if (detail.profile_id) {
        const matchingProfile = profiles.find((p) => p.id === detail.profile_id);
        if (matchingProfile) {
          setActiveProfile(matchingProfile);
        }
      }
    } catch (err) {
      console.error("Error selecting conversation:", err);
    }
  };

  const handleNewConversation = async () => {
    try {
      const created = await createConversation({
        title: "New Chat",
        model: engineStatus?.model_name || "local-model",
        system_prompt: activeProfile?.system_prompt || "",
        profile_id: activeProfile?.id || "default",
      });
      setConversations((prev) => [created, ...prev]);
      setActiveConversationId(created.id);
      setMessages([]);
    } catch (err) {
      console.error("Failed to create new conversation:", err);
    }
  };

  const handleRenameConversation = async (id: string, newTitle: string) => {
    try {
      const updated = await updateConversation(id, { title: newTitle });
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title: updated.title } : c))
      );
    } catch (err) {
      console.error("Failed to rename conversation:", err);
    }
  };

  const handleDeleteConversation = async (id: string) => {
    try {
      await deleteConversation(id);
      const remaining = conversations.filter((c) => c.id !== id);
      setConversations(remaining);
      if (activeConversationId === id) {
        if (remaining.length > 0) {
          handleSelectConversation(remaining[0].id);
        } else {
          setActiveConversationId(null);
          setMessages([]);
        }
      }
    } catch (err) {
      console.error("Failed to delete conversation:", err);
    }
  };

  const handleSendPrompt = async (prompt: string) => {
    let targetConvId = activeConversationId;
    if (!targetConvId) {
      // Auto-create conversation on first send if none selected
      const created = await createConversation({
        title: prompt.length > 35 ? prompt.slice(0, 32) + "..." : prompt,
        model: engineStatus?.model_name || "local-model",
        system_prompt: activeProfile?.system_prompt || "",
        profile_id: activeProfile?.id || "default",
      });
      setConversations((prev) => [created, ...prev]);
      setActiveConversationId(created.id);
      targetConvId = created.id;
    }

    const effectiveSystemPrompt =
      appSettings.custom_system_prompt.trim() || activeProfile?.system_prompt || "";

    await sendMessage(prompt, targetConvId, effectiveSystemPrompt, appSettings);
  };

  const handleRegenerate = async () => {
    if (!activeConversationId) return;
    await regenerateResponse(activeConversationId, appSettings);
  };

  const handleEditMessage = async (messageId: string, newContent: string) => {
    if (!activeConversationId) return;
    const effectiveSystemPrompt =
      appSettings.custom_system_prompt.trim() || activeProfile?.system_prompt || "";
    await editAndResendMessage(
      activeConversationId,
      messageId,
      newContent,
      effectiveSystemPrompt,
      appSettings
    );
  };

  const handleSuggestionSelected = (prompt: string) => {
    setExternalPrompt(prompt);
  };

  const activeError = errorMessage || streamError || engineStatus?.error_message || null;
  const isReady = engineStatus?.state === "READY";

  return (
    <div className="flex flex-col h-screen bg-[#07090E] font-sans text-slate-100 overflow-hidden">
      {/* Top Navbar & Telemetry */}
      <StatusBar
        telemetry={telemetry}
        engineStatus={engineStatus}
        tokensPerSec={tokensPerSec}
        activeProfile={activeProfile}
        profiles={profiles}
        onSelectProfile={setActiveProfile}
        onOpenSettings={() => setIsSettingsOpen(true)}
      />

      {/* Main App Workspace */}
      <div className="flex-1 flex overflow-hidden">
        {/* Obsidian Collapsible Sidebar */}
        <Sidebar
          conversations={conversations}
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          onRenameConversation={handleRenameConversation}
          onDeleteConversation={handleDeleteConversation}
          isCollapsed={isSidebarCollapsed}
          onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
        />

        {/* Central Chat Interface Column */}
        <main className="flex-1 flex flex-col min-w-0 max-w-5xl mx-auto p-4 gap-3 overflow-hidden">
          {/* Model Management & Advisory Bar */}
          <ModelSelector
            engineStatus={engineStatus}
            onStatusChange={setEngineStatus}
            onError={setErrorMessage}
          />

          {/* Actionable Error Banner */}
          <ErrorBanner
            error={activeError}
            onDismiss={() => setErrorMessage(null)}
          />

          {/* Central Chat Window */}
          <div className="flex-1 flex flex-col min-h-0 bg-[#0A0D17]/60 border border-white/[0.06] rounded-2xl overflow-hidden backdrop-blur-md">
            <ChatInterface
              messages={messages}
              isGenerating={isGenerating}
              onRegenerate={handleRegenerate}
              onEditMessage={handleEditMessage}
              onSelectPromptSuggestion={handleSuggestionSelected}
              activeModelName={engineStatus?.model_name}
            />

            {/* Prompt Input Box */}
            <div className="p-3 border-t border-white/[0.06] bg-[#07090E]/80 backdrop-blur-md">
              <PromptInput
                onSend={handleSendPrompt}
                onStop={stopStream}
                isGenerating={isGenerating}
                disabled={!isReady && !isGenerating}
                placeholder={
                  isReady
                    ? `Message ${engineStatus?.model_name || "model"}...`
                    : "Load a model in the panel above to begin chatting..."
                }
                externalPrompt={externalPrompt}
                onExternalPromptConsumed={() => setExternalPrompt(undefined)}
              />
            </div>
          </div>
        </main>
      </div>

      {/* Application & Generation Settings Modal */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onSettingsSaved={(newCfg) => setAppSettings(newCfg)}
      />
    </div>
  );
}
