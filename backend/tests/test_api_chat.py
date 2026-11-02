"""
Chat & Streaming API Integration Tests
Tests SSE token streaming, client disconnection handling, and OpenAI endpoints.
"""

import json
import pytest
from httpx import AsyncClient
from app.core.engine_base import EngineConfig
from pathlib import Path


@pytest.mark.asyncio
async def test_chat_rejected_when_unloaded(async_client: AsyncClient):
    """Verifies that generation is rejected if no model is loaded."""
    res = await async_client.post(
        "/api/chat/completions",
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert res.status_code == 400
    assert "UNLOADED" in res.json()["detail"]


@pytest.mark.asyncio
async def test_chat_completions_sse_stream(async_client: AsyncClient, mock_engine):
    """Verifies end-to-end SSE token streaming."""
    config = EngineConfig(
        model_path=Path("S:/Mustang AI/models/mock.gguf"),
        n_gpu_layers=99,
    )
    await mock_engine.start(config)

    async with async_client.stream(
        "POST",
        "/api/chat/completions",
        json={"messages": [{"role": "user", "content": "Hello Mustang"}]},
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        tokens_received = []
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            data = json.loads(payload)
            delta = data["choices"][0]["delta"].get("content", "")
            if delta:
                tokens_received.append(delta)

        assert len(tokens_received) > 0
        full_text = "".join(tokens_received)
        assert "Mustang AI" in full_text


@pytest.mark.asyncio
async def test_openai_compatibility_endpoints(async_client: AsyncClient, mock_engine):
    """Verifies /v1/models and non-streaming /v1/chat/completions."""
    # Test GET /v1/models
    res_models = await async_client.get("/v1/models")
    assert res_models.status_code == 200
    assert res_models.json()["object"] == "list"

    # Start engine and test POST /v1/chat/completions non-streaming
    config = EngineConfig(
        model_path=Path("S:/Mustang AI/models/mock.gguf"),
        n_gpu_layers=99,
    )
    await mock_engine.start(config)

    res_chat = await async_client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": "Hello OpenAI compatible API"}],
            "stream": False,
        },
    )
    assert res_chat.status_code == 200
    data = res_chat.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert len(data["choices"][0]["message"]["content"]) > 0

