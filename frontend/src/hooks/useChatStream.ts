import { useState, useRef, useCallback } from "react";
import { AppSettings, ChatMessage } from "../types";
import { apiFetch, stopGeneration, truncateConversation } from "../services/api";
import { AgentEvent, applyAgentEvent, historyForRequest } from "../services/agent";

/** Agent mode for one request: the backend may run its built-in read-only tools. */
export interface AgentRequestOptions {
  workspace?: string | null;
}

const agentBody = (agent?: AgentRequestOptions | null) =>
  agent ? { agent: { workspace: agent.workspace || null } } : {};

export function useChatStream() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isGenerating, setIsGenerating] = useState<boolean>(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [tokensPerSec, setTokensPerSec] = useState<number>(0);

  const abortControllerRef = useRef<AbortController | null>(null);

  const stopStream = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    try {
      await stopGeneration();
    } catch (err) {
      console.warn("Stop generation notice:", err);
    }
    setIsGenerating(false);
  }, []);

  const streamFromEndpoint = async (
    endpoint: string,
    bodyPayload: object,
    assistantMessageId: string
  ) => {
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    const startTime = performance.now();
    let tokenCount = 0;

    try {
      const res = await apiFetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bodyPayload),
        signal: abortController.signal,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errData.detail || "Generation failed to start");
      }

      const reader = res.body?.getReader();
      if (!reader) throw new Error("No readable response body");

      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const clean = line.trim();
          if (!clean || !clean.startsWith("data:")) continue;

          const payloadStr = clean.slice(5).trim();
          if (payloadStr === "[DONE]") {
            setIsGenerating(false);
            return;
          }

          try {
            const data = JSON.parse(payloadStr);
            if (data.error) {
              setStreamError(data.error);
              break;
            }

            if (data.agent) {
              const ev = data.agent as AgentEvent;
              setMessages((prev) =>
                prev.map((m) => (m.id === assistantMessageId ? applyAgentEvent(m, ev) : m))
              );
            }

            const delta = data.choices?.[0]?.delta?.content;
            if (delta) {
              tokenCount += 1;
              const elapsedSec = (performance.now() - startTime) / 1000;
              if (elapsedSec > 0.1) {
                setTokensPerSec(Math.round((tokenCount / elapsedSec) * 10) / 10);
              }

              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMessageId
                    ? { ...m, content: m.content + delta, token_count: tokenCount }
                    : m
                )
              );
            }
          } catch {
            // Ignore partial JSON parsing chunk
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") {
        console.log("Generation aborted by user.");
      } else {
        const errMsg = err instanceof Error ? err.message : "Stream failed";
        setStreamError(errMsg);
      }
    } finally {
      setIsGenerating(false);
      abortControllerRef.current = null;
    }
  };

  const sendMessage = useCallback(
    async (
      prompt: string,
      conversationId: string | null,
      systemPrompt = "",
      settings?: Partial<AppSettings>,
      agent?: AgentRequestOptions | null
    ) => {
      if (!prompt.trim() || isGenerating) return;

      setStreamError(null);
      setIsGenerating(true);
      setTokensPerSec(0);

      const userMessageId = `user-${Date.now()}`;
      const assistantMessageId = `asst-${Date.now()}`;

      const userMsg: ChatMessage = {
        id: userMessageId,
        role: "user",
        content: prompt.trim(),
        created_at: Date.now() / 1000,
      };

      const asstPlaceholder: ChatMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        created_at: Date.now() / 1000,
      };

      setMessages((prev) => [...prev, userMsg, asstPlaceholder]);

      const conversationHistory = historyForRequest([...messages, userMsg]);

      if (systemPrompt.trim()) {
        conversationHistory.unshift({
          role: "system",
          content: systemPrompt.trim(),
        });
      }

      await streamFromEndpoint(
        "/api/chat/completions",
        {
          conversation_id: conversationId,
          messages: conversationHistory,
          temperature: settings?.temperature ?? 0.7,
          top_p: settings?.top_p ?? 0.9,
          top_k: settings?.top_k ?? 40,
          repeat_penalty: settings?.repeat_penalty ?? 1.1,
          max_tokens: settings?.max_tokens ?? 2048,
          stream: true,
          ...agentBody(agent),
        },
        assistantMessageId
      );
    },
    [isGenerating, messages]
  );

  const regenerateResponse = useCallback(
    async (
      conversationId: string,
      settings?: Partial<AppSettings>,
      agent?: AgentRequestOptions | null
    ) => {
      if (!conversationId || isGenerating) return;

      setStreamError(null);
      setIsGenerating(true);
      setTokensPerSec(0);

      // Remove last assistant message from UI if present
      const assistantMessageId = `asst-${Date.now()}`;
      setMessages((prev) => {
        const withoutLast =
          prev.length > 0 && prev[prev.length - 1].role === "assistant"
            ? prev.slice(0, -1)
            : prev;
        return [
          ...withoutLast,
          {
            id: assistantMessageId,
            role: "assistant",
            content: "",
            created_at: Date.now() / 1000,
          },
        ];
      });

      await streamFromEndpoint(
        "/api/chat/regenerate",
        {
          conversation_id: conversationId,
          temperature: settings?.temperature ?? 0.7,
          top_p: settings?.top_p ?? 0.9,
          top_k: settings?.top_k ?? 40,
          repeat_penalty: settings?.repeat_penalty ?? 1.1,
          max_tokens: settings?.max_tokens ?? 2048,
          ...agentBody(agent),
        },
        assistantMessageId
      );
    },
    [isGenerating]
  );

  const editAndResendMessage = useCallback(
    async (
      conversationId: string,
      messageId: string,
      newPrompt: string,
      systemPrompt = "",
      settings?: Partial<AppSettings>,
      agent?: AgentRequestOptions | null
    ) => {
      if (!newPrompt.trim() || isGenerating) return;

      try {
        // Truncate SQLite database messages after messageId
        await truncateConversation(conversationId, messageId, false);
      } catch (err) {
        console.warn("Truncate note:", err);
      }

      // Rollback UI messages up to messageId, update message content, and trigger generation
      const targetIndex = messages.findIndex((m) => m.id === messageId);
      if (targetIndex === -1) return;

      const updatedHistory = messages.slice(0, targetIndex + 1);
      updatedHistory[targetIndex] = {
        ...updatedHistory[targetIndex],
        content: newPrompt.trim(),
      };

      setMessages(updatedHistory);

      // Now stream assistant response
      setStreamError(null);
      setIsGenerating(true);
      setTokensPerSec(0);

      const assistantMessageId = `asst-${Date.now()}`;
      setMessages([...updatedHistory, {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        created_at: Date.now() / 1000,
      }]);

      const promptHistory = historyForRequest(updatedHistory);

      if (systemPrompt.trim()) {
        promptHistory.unshift({
          role: "system",
          content: systemPrompt.trim(),
        });
      }

      await streamFromEndpoint(
        "/api/chat/completions",
        {
          conversation_id: conversationId,
          messages: promptHistory,
          temperature: settings?.temperature ?? 0.7,
          top_p: settings?.top_p ?? 0.9,
          top_k: settings?.top_k ?? 40,
          repeat_penalty: settings?.repeat_penalty ?? 1.1,
          max_tokens: settings?.max_tokens ?? 2048,
          stream: true,
          ...agentBody(agent),
        },
        assistantMessageId
      );
    },
    [isGenerating, messages]
  );

  return {
    messages,
    setMessages,
    isGenerating,
    streamError,
    tokensPerSec,
    sendMessage,
    regenerateResponse,
    editAndResendMessage,
    stopStream,
  };
}
