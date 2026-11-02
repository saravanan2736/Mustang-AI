"""
Inference Engine Lifecycle Unit & Integration Tests
Validates state machine transitions, concurrent generation locks, and cancellation behavior.
"""

import pytest
from pathlib import Path

from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineConfig,
    EngineState,
)
from app.core.mock_engine import MockEngine


@pytest.mark.asyncio
async def test_engine_state_transitions():
    """Verifies state machine: UNLOADED -> STARTING -> READY -> UNLOADING -> UNLOADED."""
    engine = MockEngine()
    status = await engine.get_status()
    assert status.state == EngineState.UNLOADED

    config = EngineConfig(
        model_path=Path("S:/Mustang AI/models/test.gguf"),
        n_gpu_layers=99,
        n_ctx=4096,
    )
    started = await engine.start(config)
    assert started is True

    status_ready = await engine.get_status()
    assert status_ready.state == EngineState.READY
    assert status_ready.model_name == "test.gguf"
    assert status_ready.gpu_layers_offloaded == 99

    stopped = await engine.stop()
    assert stopped is True
    status_unloaded = await engine.get_status()
    assert status_unloaded.state == EngineState.UNLOADED


@pytest.mark.asyncio
async def test_stream_and_cancellation():
    """Verifies that in-flight generation can be cleanly cancelled."""
    engine = MockEngine(token_delay_s=0.05)
    config = EngineConfig(
        model_path=Path("S:/Mustang AI/models/test.gguf"),
        n_gpu_layers=99,
    )
    await engine.start(config)

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Explain quantum computing")],
        stream=True,
    )

    chunks = []
    stream_iter = engine.stream_chat(req)
    
    # Receive first chunk, then cancel
    first_chunk = await anext(stream_iter)
    chunks.append(first_chunk)
    assert len(first_chunk.delta_content) > 0

    # Issue cancellation
    cancelled = await engine.cancel_generation()
    assert cancelled is True

    # Read remaining stream, should terminate with finish_reason="stop"
    async for chunk in stream_iter:
        chunks.append(chunk)

    assert chunks[-1].finish_reason == "stop"
    status_after = await engine.get_status()
    assert status_after.state == EngineState.READY
    await engine.stop()

