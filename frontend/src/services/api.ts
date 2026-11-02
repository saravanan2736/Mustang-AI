import {
  AppSettings,
  AssistantProfile,
  ConversationDetail,
  ConversationSummary,
  DownloadTask,
  EngineStatus,
  HardwareTelemetry,
  HFRepositoryDetailResponse,
  HFSearchResponse,
  LibraryModel,
  MemoryEstimateResult,
  ModelContextRecommendation,
  ModelInfo,
  ScanResult,
} from "../types";

import { backendUrl, sessionToken, TOKEN_HEADER } from "./connection";

/** Fetch an API path on the current backend; always sends the session token when known. */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = sessionToken();
  if (token) headers.set(TOKEN_HEADER, token);
  return fetch(`${backendUrl()}${path}`, { ...init, headers });
}

/** Kept for existing callers; the token is resolved by initBackendConnection() at startup. */
export async function bootstrapAuth(): Promise<string | null> {
  return sessionToken();
}

export function getSessionToken(): string | null {
  return sessionToken();
}

function getHeaders(_authenticated = false): HeadersInit {
  // The session token is added by apiFetch on every request.
  return { "Content-Type": "application/json" };
}

// --- System & Hardware Telemetry ---

export async function fetchHardware(): Promise<HardwareTelemetry> {
  const res = await apiFetch(`/api/system/hardware`);
  if (!res.ok) throw new Error(`Failed to fetch hardware: ${res.statusText}`);
  return res.json();
}

// --- Models & Inference Engine ---

export async function fetchModels(): Promise<ModelInfo[]> {
  const res = await apiFetch(`/api/models`);
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.statusText}`);
  return res.json();
}

export async function fetchModelLibrary(): Promise<LibraryModel[]> {
  const res = await apiFetch(`/api/models/library`);
  if (!res.ok) throw new Error(`Failed to fetch model library: ${res.statusText}`);
  return res.json();
}

export async function rescanModels(): Promise<ScanResult> {
  const res = await apiFetch(`/api/models/scan`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to scan models: ${res.statusText}`);
  return res.json();
}

export async function fetchContextRecommendation(
  modelId: string,
  kvType = "f16"
): Promise<ModelContextRecommendation> {
  const res = await apiFetch(
    `/api/models/${encodeURIComponent(modelId)}/context_recommendation?kv_type=${encodeURIComponent(kvType)}`
  );
  if (!res.ok) throw new Error(`Failed to fetch context recommendation: ${res.statusText}`);
  return res.json();
}

export async function deleteModel(modelId: string): Promise<{ status: string; deleted: string }> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/models/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
    headers: getHeaders(true),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to delete model");
  }
  return res.json();
}

// --- Hugging Face Hub Discovery ---

export async function searchHFModels(
  query: string,
  limit = 20,
  sort = "downloads"
): Promise<HFSearchResponse> {
  const url = `/api/hf/search?query=${encodeURIComponent(query)}&limit=${limit}&sort=${sort}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to search Hugging Face models");
  }
  return res.json();
}

export async function fetchHFRepositoryDetails(
  repoId: string,
  fetchReadme = true
): Promise<HFRepositoryDetailResponse> {
  const url = `/api/hf/models/${encodeURIComponent(repoId)}?fetch_readme=${fetchReadme}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to fetch repository details");
  }
  return res.json();
}

// --- Asynchronous Download Manager ---

export async function fetchDownloads(): Promise<DownloadTask[]> {
  const res = await apiFetch(`/api/downloads`);
  if (!res.ok) throw new Error(`Failed to fetch downloads: ${res.statusText}`);
  return res.json();
}

export async function createDownload(data: {
  repo_id: string;
  filename: string;
  revision?: string;
  expected_sha256?: string;
  total_bytes?: number;
}): Promise<DownloadTask> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/downloads`, {
    method: "POST",
    headers: getHeaders(true),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to enqueue download");
  }
  return res.json();
}

export async function pauseDownload(id: string): Promise<{ status: string; id: string }> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/downloads/${encodeURIComponent(id)}/pause`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to pause download");
  }
  return res.json();
}

export async function resumeDownload(id: string): Promise<{ status: string; id: string }> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/downloads/${encodeURIComponent(id)}/resume`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to resume download");
  }
  return res.json();
}

export async function cancelDownload(id: string): Promise<{ status: string; id: string }> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/downloads/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to cancel download");
  }
  return res.json();
}

export async function retryDownload(id: string): Promise<{ status: string; id: string }> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/downloads/${encodeURIComponent(id)}/retry`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to retry download");
  }
  return res.json();
}

export async function fetchModelEstimate(
  filename: string,
  gpuLayers = 99,
  ctx = 4096
): Promise<MemoryEstimateResult> {
  const url = `/api/models/${encodeURIComponent(filename)}/estimate?n_gpu_layers=${gpuLayers}&n_ctx=${ctx}`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Failed to fetch estimate: ${res.statusText}`);
  return res.json();
}

export async function fetchEngineStatus(): Promise<EngineStatus> {
  const res = await apiFetch(`/api/models/status`);
  if (!res.ok) throw new Error(`Failed to fetch status: ${res.statusText}`);
  return res.json();
}

export async function loadModel(
  modelName: string,
  n_gpu_layers = 99,
  n_ctx = 4096,
  n_batch = 512
): Promise<EngineStatus> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/models/load`, {
    method: "POST",
    headers: getHeaders(true),
    body: JSON.stringify({
      model_name: modelName,
      n_gpu_layers,
      n_ctx,
      n_batch,
      flash_attention: true,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to load model");
  }
  return res.json();
}

export async function unloadModel(): Promise<EngineStatus> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/models/unload`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to unload model");
  }
  return res.json();
}

export async function stopGeneration(): Promise<{ status: string }> {
  await bootstrapAuth();
  const res = await apiFetch(`/api/chat/stop`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) throw new Error("Failed to stop generation");
  return res.json();
}

// --- Conversations Persistence ---

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const res = await apiFetch(`/api/conversations`);
  if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.statusText}`);
  return res.json();
}

export async function createConversation(data: {
  title?: string;
  model?: string;
  system_prompt?: string;
  profile_id?: string;
}): Promise<ConversationSummary> {
  const res = await apiFetch(`/api/conversations`, {
    method: "POST",
    headers: getHeaders(false),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create conversation");
  return res.json();
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  const res = await apiFetch(`/api/conversations/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Failed to load conversation: ${res.statusText}`);
  return res.json();
}

export async function updateConversation(
  id: string,
  data: { title?: string; system_prompt?: string; profile_id?: string; model?: string }
): Promise<ConversationSummary> {
  const res = await apiFetch(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: getHeaders(false),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update conversation");
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await apiFetch(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: getHeaders(false),
  });
  if (!res.ok) throw new Error("Failed to delete conversation");
}

export async function truncateConversation(
  conversationId: string,
  messageId: string,
  inclusive = false
): Promise<void> {
  const res = await apiFetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/truncate`,
    {
      method: "POST",
      headers: getHeaders(false),
      body: JSON.stringify({ message_id: messageId, inclusive }),
    }
  );
  if (!res.ok) throw new Error("Failed to truncate conversation");
}

// --- Assistant Profiles ---

export async function fetchProfiles(): Promise<AssistantProfile[]> {
  const res = await apiFetch(`/api/profiles`);
  if (!res.ok) throw new Error("Failed to fetch profiles");
  return res.json();
}

export async function createProfile(data: {
  name: string;
  description: string;
  system_prompt: string;
}): Promise<AssistantProfile> {
  const res = await apiFetch(`/api/profiles`, {
    method: "POST",
    headers: getHeaders(false),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to create profile");
  }
  return res.json();
}

export async function deleteProfile(id: string): Promise<void> {
  const res = await apiFetch(`/api/profiles/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: getHeaders(false),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to delete profile");
  }
}

// --- Application & Generation Settings ---

export async function fetchSettings(): Promise<AppSettings> {
  const res = await apiFetch(`/api/settings`);
  if (!res.ok) throw new Error("Failed to fetch settings");
  return res.json();
}

export async function updateSettings(settings: AppSettings): Promise<AppSettings> {
  const res = await apiFetch(`/api/settings`, {
    method: "PUT",
    headers: getHeaders(false),
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error("Failed to update settings");
  return res.json();
}

// --- Download progress stream ---

/**
 * Subscribe to download progress (SSE frames over an authenticated fetch stream;
 * EventSource cannot send the session token header). `onClosed` fires when the stream
 * ends or fails, so the caller can reconnect. Returns an unsubscribe function.
 */
export function subscribeDownloadProgress(
  onProgress: (task: DownloadTask) => void,
  onClosed: (error?: unknown) => void
): () => void {
  const controller = new AbortController();
  (async () => {
    try {
      const res = await apiFetch(`/api/downloads/progress`, {
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`Progress stream refused: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          let event = "message";
          const data: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
          }
          if (event !== "progress" || data.length === 0) continue; // comments / keepalive pings
          try {
            onProgress(JSON.parse(data.join("\n")) as DownloadTask);
          } catch (e) {
            console.error("Malformed download progress frame:", e);
          }
        }
      }
      if (!controller.signal.aborted) onClosed();
    } catch (err) {
      if (!controller.signal.aborted) onClosed(err);
    }
  })();
  return () => controller.abort();
}
