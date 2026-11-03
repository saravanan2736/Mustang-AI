export type EngineState =
  | "UNLOADED"
  | "STARTING"
  | "READY"
  | "GENERATING"
  | "STOPPING"
  | "UNLOADING"
  | "ERROR";

export interface GPUMetrics {
  available: boolean;
  name: string;
  driver_version: string;
  vram_total_mb: number;
  vram_used_mb: number;
  vram_free_mb: number;
  gpu_utilization_pct: number;
  temperature_c: number;
}

export interface SystemMemoryMetrics {
  ram_total_mb: number;
  ram_available_mb: number;
  ram_used_mb: number;
  ram_used_pct: number;
}

export interface HardwareTelemetry {
  gpu: GPUMetrics;
  memory: SystemMemoryMetrics;
  cpu_utilization_pct: number;
  cpu_logical_cores: number;
}

export interface ModelInfo {
  name: string;
  filename: string;
  path: string;
  size_mb: number;
  size_gb: number;
  modified_time: number;
}

export type FitStatus = "FULL_GPU_SAFE" | "TIGHT_MARGIN" | "PROBABLE_OOM" | "UNAVAILABLE";

export interface MemoryEstimateResult {
  model_name: string;
  architecture: string;
  estimate_available: boolean;
  reason: string;
  disclaimer: string;
  total_layers: number;
  requested_gpu_layers: number;
  context_length: number;
  weights_gpu_mb: number;
  weights_cpu_mb: number;
  kv_cache_gpu_mb: number;
  compute_overhead_mb: number;
  cuda_driver_reserve_mb: number;
  total_estimated_vram_mb: number;
  available_vram_mb: number;
  vram_headroom_mb: number;
  fit_status: FitStatus;
  recommended_gpu_layers: number;
}

export interface EngineStatus {
  state: EngineState;
  model_name?: string | null;
  model_path?: string | null;
  gpu_layers_requested: number;
  gpu_layers_offloaded: number;
  context_size: number;
  vram_allocated_mb: number;
  pid?: number | null;
  port?: number | null;
  error_message?: string | null;
  active_tokens_per_sec: number;
}

export interface ToolCallInfo {
  id: string;
  type?: "function";
  function: { name: string; arguments: string };
}

/** One tool call of an agent-mode reply, as shown in the chat (live or reloaded). */
export interface AgentStep {
  call_id: string;
  step: number;
  name: string;
  arguments: string;
  status: "running" | "ok" | "error";
  result?: string;
  /** Text the model wrote before making this call ("Let me check the file."). */
  note?: string;
}

export interface ChatMessage {
  id: string;
  conversation_id?: string;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  token_count?: number;
  finish_reason?: string | null;
  created_at?: number;
  timestamp?: number;
  /** Stored agent-mode rows (schema v2): calls made by an assistant step, or the call a tool row answers. */
  tool_calls?: ToolCallInfo[] | null;
  tool_call_id?: string | null;
  /** UI only: the tool steps that led to this assistant answer. */
  steps?: AgentStep[];
  /** UI only: why an agent reply ended early (step_limit, repeated_calls, tool_errors, stopped). */
  agent_stop_reason?: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  model: string;
  system_prompt: string;
  profile_id: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  last_message?: string | null;
}

export interface ConversationDetail {
  id: string;
  title: string;
  model: string;
  system_prompt: string;
  profile_id: string;
  created_at: number;
  updated_at: number;
  messages: ChatMessage[];
  agent_enabled?: boolean;
  workspace?: string | null;
}

export interface AssistantProfile {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  is_builtin: boolean;
  created_at: number;
}

export interface AppSettings {
  temperature: number;
  top_p: number;
  top_k: number;
  repeat_penalty: number;
  max_tokens: number;
  context_size: number;
  default_profile: string;
  custom_system_prompt: string;
}

export interface LibraryModel {
  id: string;
  canonical_path: string;
  filename: string;
  repo_id: string;
  file_size_bytes: number;
  file_size_gb: number;
  architecture: string;
  quantization: string;
  parameter_count: string;
  context_length: number;
  embedding_dim: number;
  block_count: number;
  head_count: number;
  head_count_kv: number;
  download_origin: "local_import" | "huggingface";
  is_active: boolean;
  last_scanned_at: number;
  created_at: number;
}

export type ContextRiskTier =
  | "LOW_RISK"
  | "RECOMMENDED"
  | "HIGH_MEMORY"
  | "LIKELY_CPU_OFFLOAD"
  | "PROBABLE_OOM"
  | "UNAVAILABLE";

export interface ContextTierInfo {
  context_length?: number;
  file_size_bytes?: number;
  file_size_mb?: number;
  estimated_weights_gpu_mb?: number;
  estimated_kv_cache_mb?: number;
  estimated_compute_overhead_mb?: number;
  estimated_driver_reserve_mb?: number;
  measured_model_vram_mb?: number | null;
  measured_total_gpu_vram_mb?: number | null;
  kv_cache_mb: number;
  compute_buffer_mb?: number;
  weights_vram_mb?: number;
  estimated_vram_mb?: number;
  estimated_vram_total_mb: number;
  vram_headroom_mb: number;
  risk_classification: ContextRiskTier;
  notes: string;
}

export interface ModelContextRecommendation {
  model_id: string;
  filename?: string;
  architecture: string;
  attention_type?: string;
  head_count?: number;
  head_count_kv?: number;
  head_dim?: number;
  head_dim_derived?: boolean;
  metadata_supported_context?: number;
  estimate_status: "AVAILABLE" | "UNAVAILABLE";
  reason: string;
  disclaimer: string;
  file_size_bytes?: number;
  file_size_mb?: number;
  estimated_weights_gpu_mb?: number;
  cuda_driver_reserve_mb?: number;
  measured_model_vram_mb?: number | null;
  measured_total_gpu_vram_mb?: number | null;
  available_vram_mb: number;
  available_ram_mb?: number;
  recommended_context?: number;
  presets?: Record<string, ContextTierInfo>;
  recommendations: Record<string, ContextTierInfo>;
}

export interface ScanResult {
  scanned: number;
  added: number;
  updated: number;
  pruned: number;
  total: number;
}

export interface HFSearchResultItem {
  repo_id: string;
  author: string;
  repo_name: string;
  description: string;
  likes: number;
  downloads: number;
  last_modified?: string | null;
  license?: string | null;
  is_gguf: boolean;
  pipeline_tag?: string | null;
  repo_url: string;
}

export interface HFSearchResponse {
  query: string;
  limit: number;
  total: number;
  results: HFSearchResultItem[];
}

export interface HFGGUFFileItem {
  filename: string;
  path: string;
  size_bytes?: number | null;
  size_gb?: number | null;
  sha256?: string | null;
  quantization: string;
  download_url: string;
}

export interface HFRepositoryDetailResponse {
  repo_id: string;
  author: string;
  repo_name: string;
  description: string;
  model_card: string;
  license?: string | null;
  likes: number;
  downloads: number;
  last_modified?: string | null;
  revision: string;
  total_files: number;
  gguf_files_count: number;
  available_gguf_files: HFGGUFFileItem[];
  repo_url: string;
}

export interface PreparedDownloadPreview {
  repo_id: string;
  filename: string;
  size_gb?: number | null;
  size_bytes?: number | null;
  quantization: string;
  download_url: string;
  destination_path: string;
  sha256?: string | null;
}

export type DownloadStatus =
  | "QUEUED"
  | "DOWNLOADING"
  | "PAUSED"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface DownloadTask {
  id: string;
  repo_id: string;
  filename: string;
  canonical_url: string;
  destination_path: string;
  total_bytes: number;
  downloaded_bytes: number;
  status: DownloadStatus;
  etag?: string | null;
  last_modified?: string | null;
  error_message?: string | null;
  sha256_expected?: string | null;
  sha256_actual?: string | null;
  integrity_status?: "VERIFIED" | "UNVERIFIED_NO_CHECKSUM" | "FAILED_CHECKSUM_MISMATCH" | "FAILED_SIZE_MISMATCH" | null;
  speed_bps: number;
  eta_seconds: number;
  created_at: number;
  updated_at: number;
}

