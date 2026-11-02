"""
Mustang AI Inference Engine Interface
Decouples application UI and API from any specific engine implementation.
"""

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Literal
from pydantic import BaseModel, Field


class EngineState(str, Enum):
    UNLOADED = "UNLOADED"
    STARTING = "STARTING"
    READY = "READY"
    GENERATING = "GENERATING"
    STOPPING = "STOPPING"
    UNLOADING = "UNLOADING"
    ERROR = "ERROR"


class EngineConfig(BaseModel):
    model_path: Path
    n_gpu_layers: int = Field(default=99, ge=0)
    n_ctx: int = Field(default=4096, ge=512, le=65536)
    n_batch: int = Field(default=512, ge=128, le=4096)
    flash_attention: bool = True
    port: int = 18080
    host: str = "127.0.0.1"


class EngineStatus(BaseModel):
    state: EngineState = EngineState.UNLOADED
    model_name: str | None = None
    model_path: str | None = None
    gpu_layers_requested: int = 0
    gpu_layers_offloaded: int = 0
    context_size: int = 0
    vram_allocated_mb: float = 0.0
    pid: int | None = None
    port: int | None = None
    error_message: str | None = None
    active_tokens_per_sec: float = 0.0


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    conversation_id: str | None = None
    model: str | None = None
    messages: list[ChatMessage]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    top_k: int = Field(default=40, ge=1, le=200)
    max_tokens: int = Field(default=2048, ge=1, le=16384)
    repeat_penalty: float = Field(default=1.1, ge=0.0, le=2.0)
    stream: bool = True


class ChatChunk(BaseModel):
    id: str
    model: str
    delta_content: str = ""
    finish_reason: str | None = None


class InferenceEngine(ABC):
    """Abstract protocol for local LLM inference engines."""

    @abstractmethod
    async def start(self, config: EngineConfig) -> bool:
        """Starts the inference engine process and waits until ready."""
        pass

    @abstractmethod
    async def stop(self) -> bool:
        """Gracefully terminates the inference engine and releases VRAM/resources."""
        pass

    @abstractmethod
    async def get_status(self) -> EngineStatus:
        """Returns the authoritative runtime health and telemetry of the engine."""
        pass

    @abstractmethod
    async def stream_chat(self, request: ChatCompletionRequest) -> AsyncIterator[ChatChunk]:
        """Streams completion tokens from the engine as they are generated."""
        pass

    @abstractmethod
    async def cancel_generation(self) -> bool:
        """Aborts active generation in-flight."""
        pass
