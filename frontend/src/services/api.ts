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

export const BACKEND_URL = "http://127.0.0.1:18000";

let sessionToken: string | null = null;

export async function bootstrapAuth(): Promise<string | null> {
  if (sessionToken) return sessionToken;
  try {
    const res = await fetch(`${BACKEND_URL}/api/auth/bootstrap`);
    if (res.ok) {
      const data = await res.json();
      sessionToken = data.token;
      return sessionToken;
    }
  } catch (err) {
    console.warn("Bootstrap auth call skipped or unavailable:", err);
  }
  return null;
}

export function getSessionToken(): string | null {
  return sessionToken;
}

function getHeaders(authenticated = false): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (authenticated && sessionToken) {
    headers["X-Mustang-Token"] = sessionToken;
  }
  return headers;
}

// --- System & Hardware Telemetry ---

export async function fetchHardware(): Promise<HardwareTelemetry> {
  const res = await fetch(`${BACKEND_URL}/api/system/hardware`);
  if (!res.ok) throw new Error(`Failed to fetch hardware: ${res.statusText}`);
  return res.json();
}

// --- Models & Inference Engine ---

export async function fetchModels(): Promise<ModelInfo[]> {
  const res = await fetch(`${BACKEND_URL}/api/models`);
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.statusText}`);
  return res.json();
}

export async function fetchModelLibrary(): Promise<LibraryModel[]> {
  const res = await fetch(`${BACKEND_URL}/api/models/library`);
  if (!res.ok) throw new Error(`Failed to fetch model library: ${res.statusText}`);
  return res.json();
}

export async function rescanModels(): Promise<ScanResult> {
  const res = await fetch(`${BACKEND_URL}/api/models/scan`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to scan models: ${res.statusText}`);
  return res.json();
}

export async function fetchContextRecommendation(
  modelId: string,
  kvType = "f16"
): Promise<ModelContextRecommendation> {
  const res = await fetch(
    `${BACKEND_URL}/api/models/${encodeURIComponent(modelId)}/context_recommendation?kv_type=${encodeURIComponent(kvType)}`
  );
  if (!res.ok) throw new Error(`Failed to fetch context recommendation: ${res.statusText}`);
  return res.json();
}

export async function deleteModel(modelId: string): Promise<{ status: string; deleted: string }> {
  await bootstrapAuth();
  const res = await fetch(`${BACKEND_URL}/api/models/${encodeURIComponent(modelId)}`, {
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
  const url = `${BACKEND_URL}/api/hf/search?query=${encodeURIComponent(query)}&limit=${limit}&sort=${sort}`;
  const res = await fetch(url);
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
  const url = `${BACKEND_URL}/api/hf/models/${encodeURIComponent(repoId)}?fetch_readme=${fetchReadme}`;
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to fetch repository details");
  }
  return res.json();
}

// --- Asynchronous Download Manager ---

export async function fetchDownloads(): Promise<DownloadTask[]> {
  const res = await fetch(`${BACKEND_URL}/api/downloads`);
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
  const res = await fetch(`${BACKEND_URL}/api/downloads`, {
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
  const res = await fetch(`${BACKEND_URL}/api/downloads/${encodeURIComponent(id)}/pause`, {
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
  const res = await fetch(`${BACKEND_URL}/api/downloads/${encodeURIComponent(id)}/resume`, {
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
  const res = await fetch(`${BACKEND_URL}/api/downloads/${encodeURIComponent(id)}/cancel`, {
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
  const res = await fetch(`${BACKEND_URL}/api/downloads/${encodeURIComponent(id)}/retry`, {
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
  const url = `${BACKEND_URL}/api/models/${encodeURIComponent(filename)}/estimate?n_gpu_layers=${gpuLayers}&n_ctx=${ctx}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch estimate: ${res.statusText}`);
  return res.json();
}

export async function fetchEngineStatus(): Promise<EngineStatus> {
  const res = await fetch(`${BACKEND_URL}/api/models/status`);
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
  const res = await fetch(`${BACKEND_URL}/api/models/load`, {
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
  const res = await fetch(`${BACKEND_URL}/api/models/unload`, {
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
  const res = await fetch(`${BACKEND_URL}/api/chat/stop`, {
    method: "POST",
    headers: getHeaders(true),
  });
  if (!res.ok) throw new Error("Failed to stop generation");
  return res.json();
}

// --- Conversations Persistence ---

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${BACKEND_URL}/api/conversations`);
  if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.statusText}`);
  return res.json();
}

export async function createConversation(data: {
  title?: string;
  model?: string;
  system_prompt?: string;
  profile_id?: string;
}): Promise<ConversationSummary> {
  const res = await fetch(`${BACKEND_URL}/api/conversations`, {
    method: "POST",
    headers: getHeaders(false),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create conversation");
  return res.json();
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  const res = await fetch(`${BACKEND_URL}/api/conversations/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Failed to load conversation: ${res.statusText}`);
  return res.json();
}

export async function updateConversation(
  id: string,
  data: { title?: string; system_prompt?: string; profile_id?: string; model?: string }
): Promise<ConversationSummary> {
  const res = await fetch(`${BACKEND_URL}/api/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: getHeaders(false),
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update conversation");
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${BACKEND_URL}/api/conversations/${encodeURIComponent(id)}`, {
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
  const res = await fetch(
    `${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId)}/truncate`,
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
  const res = await fetch(`${BACKEND_URL}/api/profiles`);
  if (!res.ok) throw new Error("Failed to fetch profiles");
  return res.json();
}

export async function createProfile(data: {
  name: string;
  description: string;
  system_prompt: string;
}): Promise<AssistantProfile> {
  const res = await fetch(`${BACKEND_URL}/api/profiles`, {
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
  const res = await fetch(`${BACKEND_URL}/api/profiles/${encodeURIComponent(id)}`, {
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
  const res = await fetch(`${BACKEND_URL}/api/settings`);
  if (!res.ok) throw new Error("Failed to fetch settings");
  return res.json();
}

export async function updateSettings(settings: AppSettings): Promise<AppSettings> {
  const res = await fetch(`${BACKEND_URL}/api/settings`, {
    method: "PUT",
    headers: getHeaders(false),
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error("Failed to update settings");
  return res.json();
}
