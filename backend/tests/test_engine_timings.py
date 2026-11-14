"""llama-server times every request (its final streamed chunk carries `timings`); the engine logs and exposes it."""

import json
import logging
from pathlib import Path

import httpx
import pytest

from app.core.engine_base import ChatCompletionRequest, EngineConfig, EngineState
from app.core.llama_engine import LlamaServerEngine

TIMINGS = {"cache_n": 5976, "prompt_n": 4, "prompt_per_second": 812.5, "predicted_n": 33, "predicted_per_second": 49.7}


def _engine(chunks: list[dict]) -> LlamaServerEngine:
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    e = LlamaServerEngine(binary_path=Path("llama-server.exe"))
    e._config = EngineConfig(model_path=Path("m.gguf"))
    e._state = EngineState.READY
    e._http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sse)))
    return e


async def _ask(e):
    return [c async for c in e.stream_chat(ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}]))]


@pytest.mark.asyncio
async def test_the_last_request_is_kept_logged_and_shown_in_the_status(caplog):
    e = _engine([{"id": "1", "choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
                 {"id": "1", "choices": [{"delta": {}, "finish_reason": "stop"}], "timings": TIMINGS}])
    with caplog.at_level(logging.INFO, logger="mustang.engine.llama"):
        await _ask(e)
    st = await e.get_status()
    assert (st.last_prompt_tokens, st.last_cached_tokens) == (4, 5976)
    assert st.last_prompt_tps == pytest.approx(812.5) and st.last_decode_tps == pytest.approx(49.7)
    assert "4 prompt tokens read (5976 reused from the cache)" in caplog.text and "33 tokens generated at 49.7 tok/s" in caplog.text


@pytest.mark.asyncio
async def test_a_timings_chunk_without_choices_is_fine():
    e = _engine([{"id": "1", "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}, {"id": "1", "choices": [], "timings": TIMINGS}])
    chunks = await _ask(e)
    assert "".join(c.delta_content for c in chunks) == "hi"
    assert (await e.get_status()).last_cached_tokens == 5976


@pytest.mark.asyncio
async def test_no_timings_leaves_the_status_at_zero():
    e = _engine([{"id": "1", "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}])
    await _ask(e)
    st = await e.get_status()
    assert (st.last_prompt_tokens, st.last_cached_tokens, st.last_decode_tps) == (0, 0, 0.0)
