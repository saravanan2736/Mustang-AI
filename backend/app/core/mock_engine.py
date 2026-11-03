"""
Mustang AI Mock Inference Engine
Deterministic test engine for unit testing, stream verification, and cancellation tests.
"""

import asyncio
import uuid
from pathlib import Path
from typing import AsyncIterator

from app.core.engine_base import (
    ChatChunk,
    ChatCompletionRequest,
    EngineConfig,
    EngineState,
    EngineStatus,
    InferenceEngine,
    ToolCall,
    ToolCallFunction,
)


class MockEngine(InferenceEngine):
    """Deterministic simulation of an inference engine for tests and offline development."""

    def __init__(self, token_delay_s: float = 0.02):
        self._state = EngineState.UNLOADED
        self._config: EngineConfig | None = None
        self._previous_config: EngineConfig | None = None
        self._cancelled = False
        self._token_delay = token_delay_s
        self._current_gen_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()
        self.fail_on_model: str | None = None
        self.rollback_occurred: bool = False
        self._error_message: str | None = None

    async def start(self, config: EngineConfig) -> bool:
        async with self._lifecycle_lock:
            # Cancel active generation if in progress
            if self._state == EngineState.GENERATING:
                await self.cancel_generation()
                await asyncio.sleep(0.05)

            previous_config = self._config if self._state == EngineState.READY else None

            # Simulate failure if target model matches fail_on_model hook
            if self.fail_on_model and self.fail_on_model in str(config.model_path):
                if previous_config:
                    self._config = previous_config
                    self._state = EngineState.READY
                    self.rollback_occurred = True
                    self._error_message = (
                        f"Failed to switch to '{config.model_path.name}'. "
                        f"Successfully rolled back to '{previous_config.model_path.name}'."
                    )
                    return False
                else:
                    self._state = EngineState.ERROR
                    self._error_message = f"Failed to start model '{config.model_path.name}'."
                    return False

            self._state = EngineState.STARTING
            self._config = config
            self._error_message = None
            await asyncio.sleep(0.05)  # simulate brief initialization
            self._state = EngineState.READY
            return True

    async def stop(self) -> bool:
        async with self._lifecycle_lock:
            self._state = EngineState.UNLOADING
            self._cancelled = True
            await asyncio.sleep(0.02)
            self._state = EngineState.UNLOADED
            self._config = None
            return True

    async def get_status(self) -> EngineStatus:
        if self._state == EngineState.UNLOADED:
            return EngineStatus(state=EngineState.UNLOADED)
        
        return EngineStatus(
            state=self._state,
            model_name=self._config.model_path.name if self._config else "mock-model",
            model_path=str(self._config.model_path) if self._config else None,
            gpu_layers_requested=self._config.n_gpu_layers if self._config else 0,
            gpu_layers_offloaded=self._config.n_gpu_layers if self._config else 0,
            context_size=self._config.n_ctx if self._config else 4096,
            vram_allocated_mb=1024.0,
            pid=99999,
            port=self._config.port if self._config else 18080,
            error_message=self._error_message,
            active_tokens_per_sec=42.5,
        )

    async def stream_chat(self, request: ChatCompletionRequest) -> AsyncIterator[ChatChunk]:
        if self._state not in (EngineState.READY, EngineState.GENERATING):
            raise RuntimeError(f"Engine is not ready (state={self._state})")

        self._state = EngineState.GENERATING
        self._cancelled = False
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model_name = self._config.model_path.name if self._config else "mock-model"

        last = request.messages[-1] if request.messages else None
        # With tools offered and a user turn last, call the first tool (deterministic).
        if request.tools and request.tool_choice != "none" and last is not None and last.role == "user":
            try:
                yield ChatChunk(
                    id=completion_id,
                    model=model_name,
                    finish_reason="tool_calls",
                    tool_calls=[ToolCall(id="call_mock_1", function=ToolCallFunction(
                        name=request.tools[0].function.name, arguments='{"mock": true}'))],
                )
            finally:
                self._state = EngineState.READY
            return

        # Deterministic response tokens based on user query
        user_msg = last.content if last is not None else "Hello"
        if last is not None and last.role == "tool":
            mock_text = f"Mock answer using the tool result: {last.content}"
        else:
            mock_text = (
                f"Mustang AI verified response to: '{user_msg}'. "
                "Local inference streaming is fully operational. "
                "System is running with strict loopback security and real-time token delivery."
            )
        words = mock_text.split(" ")

        try:
            for i, word in enumerate(words):
                if self._cancelled:
                    yield ChatChunk(
                        id=completion_id,
                        model=model_name,
                        delta_content="",
                        finish_reason="stop",
                    )
                    break

                chunk_content = word + (" " if i < len(words) - 1 else "")
                yield ChatChunk(
                    id=completion_id,
                    model=model_name,
                    delta_content=chunk_content,
                    finish_reason=None if i < len(words) - 1 else "stop",
                )
                await asyncio.sleep(self._token_delay)
        finally:
            self._state = EngineState.READY

    async def cancel_generation(self) -> bool:
        self._cancelled = True
        return True

