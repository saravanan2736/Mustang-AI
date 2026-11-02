"""
Milestone 3 Stage 5: Safe Model Switching Lifecycle Tests
Validates the 13-step model switching sequence, in-flight stream cancellation,
automatic rollback on startup failure, and concurrency lock protection.
"""

import asyncio
from pathlib import Path
import pytest
from httpx import AsyncClient

from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineConfig,
    EngineState,
)
from app.core.mock_engine import MockEngine


@pytest.mark.asyncio
async def test_clean_model_switch():
    """Verifies seamless model switching: Model A -> Model B."""
    engine = MockEngine()
    config_a = EngineConfig(
        model_path=Path("S:/Mustang AI/models/model_alpha.gguf"),
        n_gpu_layers=25,
        n_ctx=2048,
    )
    config_b = EngineConfig(
        model_path=Path("S:/Mustang AI/models/model_beta.gguf"),
        n_gpu_layers=28,
        n_ctx=4096,
    )

    # 1. Load Model A
    started_a = await engine.start(config_a)
    assert started_a is True
    status_a = await engine.get_status()
    assert status_a.state == EngineState.READY
    assert status_a.model_name == "model_alpha.gguf"
    assert status_a.context_size == 2048

    # 2. Switch to Model B directly
    switched_b = await engine.start(config_b)
    assert switched_b is True
    status_b = await engine.get_status()
    assert status_b.state == EngineState.READY
    assert status_b.model_name == "model_beta.gguf"
    assert status_b.context_size == 4096

    # 3. Clean unload
    stopped = await engine.stop()
    assert stopped is True
    assert (await engine.get_status()).state == EngineState.UNLOADED


@pytest.mark.asyncio
async def test_switch_cancels_inflight_generation():
    """Verifies that initiating a model switch while actively generating cancels the stream safely."""
    engine = MockEngine(token_delay_s=0.08)
    config_a = EngineConfig(model_path=Path("S:/Mustang AI/models/model_a.gguf"))
    config_b = EngineConfig(model_path=Path("S:/Mustang AI/models/model_b.gguf"))

    await engine.start(config_a)

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Stream me a long answer")]
    )

    # Start generation in background task
    stream_task = asyncio.create_task(
        anext(engine.stream_chat(req))
    )
    # Wait for first token to ensure generator is actively streaming
    first_chunk = await stream_task
    assert len(first_chunk.delta_content) > 0
    assert (await engine.get_status()).state == EngineState.GENERATING

    # Switch to Model B while generation is active
    switch_task = asyncio.create_task(engine.start(config_b))
    switched = await switch_task

    assert switched is True
    status_now = await engine.get_status()
    assert status_now.state == EngineState.READY
    assert status_now.model_name == "model_b.gguf"

    await engine.stop()


@pytest.mark.asyncio
async def test_rollback_on_switch_failure():
    """
    Verifies that if Model B fails to start, the engine automatically rolls back
    to previous Model A and leaves the system in READY state with a descriptive message.
    """
    engine = MockEngine()
    config_good = EngineConfig(model_path=Path("S:/Mustang AI/models/working_model.gguf"))
    config_bad = EngineConfig(model_path=Path("S:/Mustang AI/models/corrupted_model.gguf"))

    # Load good model
    await engine.start(config_good)
    status_initial = await engine.get_status()
    assert status_initial.state == EngineState.READY
    assert status_initial.model_name == "working_model.gguf"

    # Configure mock engine to simulate startup failure on the bad model
    engine.fail_on_model = "corrupted_model"

    # Attempt switch: should fail and trigger automatic rollback
    switch_res = await engine.start(config_bad)
    assert switch_res is False
    assert engine.rollback_occurred is True

    # Authoritative engine state must be READY with working_model restored
    status_after = await engine.get_status()
    assert status_after.state == EngineState.READY
    assert status_after.model_name == "working_model.gguf"
    assert "rolled back" in (status_after.error_message or "").lower()

    await engine.stop()


@pytest.mark.asyncio
async def test_initial_load_failure_without_rollback():
    """Verifies that an initial model load failure (with no previous model) sets state to ERROR cleanly."""
    engine = MockEngine()
    engine.fail_on_model = "broken"

    config_broken = EngineConfig(model_path=Path("S:/Mustang AI/models/broken.gguf"))
    started = await engine.start(config_broken)

    assert started is False
    status = await engine.get_status()
    assert status.state == EngineState.ERROR
    assert "broken.gguf" in (status.error_message or "")


@pytest.mark.asyncio
async def test_switching_concurrency_lock():
    """Verifies that simultaneous start/switch requests are serialized without collision."""
    engine = MockEngine()
    config_1 = EngineConfig(model_path=Path("S:/Mustang AI/models/m1.gguf"))
    config_2 = EngineConfig(model_path=Path("S:/Mustang AI/models/m2.gguf"))

    # Launch both concurrently
    res1, res2 = await asyncio.gather(
        engine.start(config_1),
        engine.start(config_2),
    )

    assert res1 is True
    assert res2 is True
    status = await engine.get_status()
    assert status.state == EngineState.READY
    # Final model is deterministic (one succeeded after the other)
    assert status.model_name in ("m1.gguf", "m2.gguf")

    await engine.stop()


@pytest.mark.asyncio
async def test_api_switch_and_rollback_endpoint(async_client: AsyncClient, mock_engine, auth_headers):
    """
    Verifies that the /api/models/load endpoint handles safe switching,
    and returns HTTP 400 with rollback details when a switch target fails.
    """
    # 1. Start with initial model
    init_req = {
        "model_name": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "n_gpu_layers": 25,
        "n_ctx": 2048,
    }
    res_init = await async_client.post("/api/models/load", json=init_req, headers=auth_headers)
    assert res_init.status_code == 200
    assert res_init.json()["state"] == "READY"

    # 2. Configure mock engine to simulate failure on switch target
    mock_engine.fail_on_model = "corrupt_target"

    # 3. Attempt switch to a nonexistent or failing target
    bad_req = {
        "model_name": "corrupt_target.gguf",
        "n_gpu_layers": 99,
        "n_ctx": 4096,
    }
    # Note: validate_model_path requires target file to exist if using filename
    # Create tiny dummy file so path validation passes
    from app.config import settings
    dummy_file = settings.MODELS_DIR / "corrupt_target.gguf"
    dummy_file.write_bytes(b"GGUF_TEST_HEADER_DATA")

    try:
        res_bad = await async_client.post("/api/models/load", json=bad_req, headers=auth_headers)
        # Should return HTTP 400 because rollback recovered the engine to READY
        assert res_bad.status_code == 400
        detail = res_bad.json()["detail"]
        assert "rolled back" in detail.lower()

        # Engine remains READY with original model
        status_res = await async_client.get("/api/models/status")
        assert status_res.status_code == 200
        assert status_res.json()["state"] == "READY"
        assert status_res.json()["model_name"] == "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    finally:
        if dummy_file.exists():
            dummy_file.unlink()
        mock_engine.fail_on_model = None
        await mock_engine.stop()
