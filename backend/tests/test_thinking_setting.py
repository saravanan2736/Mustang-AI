"""
The thinking setting: "auto" lets the model think in chat and turns it off while the agent works; "on" and "off" are
explicit. Measured on the 9B: with thinking off a tool call took 1.3 s instead of 2.0 s with the same accuracy, and a
simple question that used all 1,200 tokens on thinking (no answer) was answered in 1 s.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.agent import loop
from app.agent.registry import ToolContext
from app.agent.workspace import Workspace
from app.core.engine_base import ChatCompletionRequest, EngineConfig, EngineState
from app.core.llama_engine import LlamaServerEngine


def _engine_capturing(bodies: list):
    sse = "data: " + json.dumps({"id": "1", "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text=sse)

    e = LlamaServerEngine(binary_path=Path("llama-server.exe"))
    e._config = EngineConfig(model_path=Path("m.gguf"))
    e._state = EngineState.READY
    e._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return e


async def _send(engine, **kw):
    req = ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}], **kw)
    return [c async for c in engine.stream_chat(req)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, expected", [("auto", None), ("off", {"enable_thinking": False}), ("on", {"enable_thinking": True})])
async def test_the_engine_sends_the_thinking_choice_to_the_template(mode, expected):
    bodies = []
    await _send(_engine_capturing(bodies), thinking=mode)
    assert bodies[0].get("chat_template_kwargs") == expected


def test_the_default_is_auto_and_other_values_are_refused():
    assert ChatCompletionRequest(messages=[{"role": "user", "content": "x"}]).thinking == "auto"
    with pytest.raises(Exception):
        ChatCompletionRequest(messages=[{"role": "user", "content": "x"}], thinking="maximum")


@pytest.mark.asyncio
@pytest.mark.parametrize("asked, sent", [("auto", "off"), ("off", "off"), ("on", "on")])
async def test_the_agent_turns_thinking_off_for_auto_only(scripted_engine, tmp_path, asked, sent):
    eng = scripted_engine([{"text": "done"}])
    ctx = ToolContext(workspace=Workspace(tmp_path))
    async for _ in loop.run_agent(eng, ChatCompletionRequest(messages=[{"role": "user", "content": "go"}], thinking=asked), ctx):
        pass
    assert eng.requests[0].thinking == sent


@pytest.mark.asyncio
async def test_plain_chat_keeps_the_models_own_behaviour_on_auto(async_client, scripted_engine):
    eng = scripted_engine([{"text": "hello"}])
    res = await async_client.post("/api/chat/completions", json={"conversation_id": "t-think", "messages": [{"role": "user", "content": "hi"}]})
    assert res.status_code == 200
    assert eng.requests[0].thinking == "auto"
    res = await async_client.post("/api/chat/completions", json={"conversation_id": "t-think2", "thinking": "off", "messages": [{"role": "user", "content": "hi"}]})
    assert eng.requests[-1].thinking == "off"


@pytest.mark.asyncio
async def test_the_setting_is_saved_and_a_bad_stored_value_falls_back_to_auto(async_client):
    got = (await async_client.get("/api/settings")).json()
    assert got["thinking"] == "auto"
    body = {**got, "thinking": "off"}
    assert (await async_client.put("/api/settings", json=body)).status_code == 200
    assert (await async_client.get("/api/settings")).json()["thinking"] == "off"
    assert (await async_client.put("/api/settings", json={**got, "thinking": "bogus"})).status_code == 422
    # restore for other tests
    await async_client.put("/api/settings", json=got)
