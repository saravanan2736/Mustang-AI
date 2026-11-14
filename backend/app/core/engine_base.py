"""
Mustang AI Inference Engine Interface
Decouples application UI and API from any specific engine implementation.
"""

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


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


_TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ToolFunction(BaseModel):
    """OpenAI function-tool definition; `parameters` is a JSON Schema object."""
    name: str = Field(pattern=_TOOL_NAME_PATTERN)
    description: str | None = Field(default=None, max_length=2048)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class Tool(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolCallFunction(BaseModel):
    name: str = Field(pattern=_TOOL_NAME_PATTERN)
    arguments: str = "{}"  # JSON text as produced by the model; validated by whoever runs the tool


class ToolCall(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] | None = None  # assistant turns that called tools
    tool_call_id: str | None = None  # tool turns: which call this result answers

    @field_validator("content", mode="before")
    @classmethod
    def _null_content(cls, v):
        # OpenAI clients send content: null on assistant turns that only call tools.
        return "" if v is None else v

    @model_validator(mode="after")
    def _tool_fields_match_role(self):
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("a 'tool' message needs tool_call_id")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only 'assistant' messages can carry tool_calls")
        return self


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
    tools: list[Tool] | None = Field(default=None, max_length=64)
    tool_choice: Literal["auto", "none", "required"] | dict[str, Any] | None = None

    @property
    def uses_tools(self) -> bool:
        return bool(self.tools) or any(m.role == "tool" or m.tool_calls for m in self.messages)


class ChatChunk(BaseModel):
    id: str
    model: str
    delta_content: str = ""
    delta_reasoning: str = ""  # the model's thinking text (shown to the user, never stored or sent back)
    finish_reason: str | None = None
    # Complete tool calls, sent once with the chunk that ends the reply (finish_reason
    # "tool_calls"); the engine assembles the streamed fragments.
    tool_calls: list[ToolCall] | None = None


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
