"""
Mustang AI M4 Phase D — engine hardening against the real llama-server (GPU).
The engine port must reject requests without the per-process API key, and cancelling a
generation must stop work inside llama-server, not only on our side of the socket.
"""

import asyncio
import socket

import httpx
import pytest

from app.config import settings
from app.core.engine_base import ChatCompletionRequest, ChatMessage, EngineConfig, EngineState
from app.core.llama_engine import LlamaServerEngine

MODEL = "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def live_engine():
    model_path = settings.MODELS_DIR / MODEL
    if not model_path.exists():
        pytest.skip(f"Test model not present: {model_path}")
    engine = LlamaServerEngine()
    config = EngineConfig(model_path=model_path, n_gpu_layers=99, n_ctx=2048, port=_free_port())
    assert await engine.start(config) is True, engine._error_message
    try:
        yield engine
    finally:
        await engine.stop()


@pytest.mark.gpu
@pytest.mark.asyncio
async def test_engine_port_requires_api_key(live_engine):
    base = f"http://127.0.0.1:{live_engine._config.port}"
    async with httpx.AsyncClient(timeout=5) as client:
        assert (await client.get(f"{base}/health")).status_code == 200  # readiness probe stays public
        assert (await client.get(f"{base}/v1/models")).status_code == 401
        assert (await client.get(f"{base}/slots")).status_code == 401
        assert (await client.post(f"{base}/v1/chat/completions", json={"messages": []})).status_code == 401
        wrong = {"Authorization": "Bearer " + "0" * 64}
        assert (await client.get(f"{base}/v1/models", headers=wrong)).status_code == 401
        assert (await client.get(f"{base}/v1/models", headers=live_engine._auth_headers())).status_code == 200
        assert (await client.get(f"{base}/")).status_code == 404  # --no-webui


@pytest.mark.gpu
@pytest.mark.asyncio
async def test_cancel_stops_generation_inside_llama_server(live_engine):
    base = f"http://127.0.0.1:{live_engine._config.port}"
    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Count from 1 to 3000, one number per line.")],
        max_tokens=3000,
    )
    chunks = []
    async for chunk in live_engine.stream_chat(req):
        chunks.append(chunk)
        if len(chunks) == 15:
            await live_engine.cancel_generation()

    assert chunks[-1].finish_reason == "stop"
    assert len(chunks) < 100, f"stream continued after cancel ({len(chunks)} chunks)"

    async with httpx.AsyncClient(timeout=5, headers=live_engine._auth_headers()) as client:
        for _ in range(20):
            slots = (await client.get(f"{base}/slots")).json()
            if not any(s.get("is_processing") for s in slots):
                break
            await asyncio.sleep(0.05)
        assert not any(s.get("is_processing") for s in slots), "llama-server kept generating after cancel"

    status = await live_engine.get_status()
    assert status.state == EngineState.READY

    # The engine is immediately usable again.
    again = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Say hi.")], max_tokens=8)
    text = "".join([c.delta_content async for c in live_engine.stream_chat(again)])
    assert text.strip()
