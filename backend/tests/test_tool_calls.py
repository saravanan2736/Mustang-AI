"""
Mustang AI M5 Phase B: tool calls through the engine and the OpenAI-compatible API (no GPU).
Schemas, the request sent to llama-server, assembly of streamed tool-call fragments, and the
/v1/chat/completions response shapes. /api/chat (the UI's endpoint) rejects tools until agent
mode exists, because conversations store text turns only.
"""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.core import llama_engine
from app.core.engine_base import ChatCompletionRequest, ChatMessage, EngineConfig, EngineState, Tool
from app.core.llama_engine import LlamaServerEngine

CALC = {"type": "function", "function": {"name": "calculator", "description": "Evaluate arithmetic.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}
FILES = {"type": "function", "function": {"name": "list_files", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}


def _ready_engine(tmp_path) -> LlamaServerEngine:
    engine = LlamaServerEngine(binary_path=tmp_path / "llama-server.exe")
    engine._config = EngineConfig(model_path=tmp_path / "m.gguf", port=1)
    engine._state = EngineState.READY
    engine._api_key = "k" * 64
    engine._http = llama_engine.httpx.AsyncClient(timeout=120.0)
    return engine


def _patch_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    class MockClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(llama_engine.httpx, "AsyncClient", MockClient)


def _sse(*deltas, finish="tool_calls", done=True):
    frames = [{"id": "x", "choices": [{"delta": d, "finish_reason": None}]} for d in deltas]
    if finish:
        frames.append({"id": "x", "choices": [{"delta": {}, "finish_reason": finish}]})
    text = "".join("data: " + json.dumps(f) + "\n\n" for f in frames)
    return text + ("data: [DONE]\n\n" if done else "")


# Two parallel calls, the first streamed in three pieces, the second without an id.
FRAGMENTS = [
    {"tool_calls": [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "calculator", "arguments": ""}}]},
    {"tool_calls": [{"index": 0, "function": {"arguments": '{"expression": '}}]},
    {"tool_calls": [{"index": 0, "function": {"arguments": '"2+2"}'}}]},
    {"tool_calls": [{"index": 1, "type": "function", "function": {"name": "list_files", "arguments": '{"path": "."}'}}]},
]


# --- schemas ---------------------------------------------------------------------------------

def test_tool_message_needs_tool_call_id():
    with pytest.raises(ValidationError):
        ChatMessage(role="tool", content="4")
    assert ChatMessage(role="tool", content="4", tool_call_id="call_a").tool_call_id == "call_a"


def test_only_assistant_messages_carry_tool_calls():
    call = {"id": "call_a", "function": {"name": "calculator", "arguments": "{}"}}
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="hi", tool_calls=[call])
    m = ChatMessage(role="assistant", content=None, tool_calls=[call])
    assert m.content == "" and m.tool_calls[0].function.name == "calculator"


@pytest.mark.parametrize("name", ["", "has space", "semi;colon", "x" * 65, "../etc"])
def test_tool_names_are_restricted(name):
    with pytest.raises(ValidationError):
        Tool(function={"name": name})


def test_plain_requests_do_not_use_tools():
    assert not ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}]).uses_tools
    assert ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}], tools=[CALC]).uses_tools


# --- engine ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_sends_tools_and_tool_turns(monkeypatch, tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=_sse({"content": "4"}, finish="stop"), headers={"content-type": "text/event-stream"})

    _patch_transport(monkeypatch, handler)
    engine = _ready_engine(tmp_path)
    req = ChatCompletionRequest(tools=[CALC], tool_choice="auto", messages=[
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_a", "function": {"name": "calculator", "arguments": '{"expression": "2+2"}'}}]},
        {"role": "tool", "tool_call_id": "call_a", "content": "4"},
    ])
    text = "".join([c.delta_content async for c in engine.stream_chat(req)])

    body = seen["body"]
    assert text == "4"
    assert body["tools"] == [CALC] and body["tool_choice"] == "auto"
    assert body["messages"][1] == {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_a", "type": "function", "function": {"name": "calculator", "arguments": '{"expression": "2+2"}'}}]}
    assert body["messages"][2] == {"role": "tool", "content": "4", "tool_call_id": "call_a"}


@pytest.mark.asyncio
async def test_plain_chat_request_is_unchanged(monkeypatch, tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=_sse({"content": "hi"}, finish="stop"), headers={"content-type": "text/event-stream"})

    _patch_transport(monkeypatch, handler)
    engine = _ready_engine(tmp_path)
    chunks = [c async for c in engine.stream_chat(ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}]))]
    assert "tools" not in seen["body"] and "tool_choice" not in seen["body"]
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert all(c.tool_calls is None for c in chunks) and chunks[-1].finish_reason == "stop"


@pytest.mark.asyncio
async def test_streamed_fragments_are_assembled_into_complete_calls(monkeypatch, tmp_path):
    _patch_transport(monkeypatch, lambda r: httpx.Response(200, text=_sse(*FRAGMENTS), headers={"content-type": "text/event-stream"}))
    engine = _ready_engine(tmp_path)
    chunks = [c async for c in engine.stream_chat(ChatCompletionRequest(messages=[{"role": "user", "content": "x"}], tools=[CALC, FILES]))]

    with_calls = [c for c in chunks if c.tool_calls]
    assert len(with_calls) == 1, "calls are delivered once, complete"
    calls = with_calls[0].tool_calls
    assert with_calls[0].finish_reason == "tool_calls"
    assert [c.function.name for c in calls] == ["calculator", "list_files"]
    assert json.loads(calls[0].function.arguments) == {"expression": "2+2"}
    assert calls[0].id == "call_a" and calls[1].id.startswith("call_")
    assert engine._state == EngineState.READY


@pytest.mark.asyncio
async def test_calls_are_delivered_when_the_stream_ends_without_finish_reason(monkeypatch, tmp_path):
    _patch_transport(monkeypatch, lambda r: httpx.Response(200, text=_sse(*FRAGMENTS[:3], finish=None), headers={"content-type": "text/event-stream"}))
    engine = _ready_engine(tmp_path)
    chunks = [c async for c in engine.stream_chat(ChatCompletionRequest(messages=[{"role": "user", "content": "x"}], tools=[CALC]))]
    assert chunks[-1].finish_reason == "tool_calls"
    assert json.loads(chunks[-1].tool_calls[0].function.arguments) == {"expression": "2+2"}


@pytest.mark.asyncio
async def test_cancelled_stream_never_delivers_a_partial_call(monkeypatch, tmp_path):
    async def slow():
        yield ("data: " + json.dumps({"choices": [{"delta": FRAGMENTS[0], "finish_reason": None}]}) + "\n\n").encode()
        yield ("data: " + json.dumps({"choices": [{"delta": {"content": "thinking"}, "finish_reason": None}]}) + "\n\n").encode()
        yield ("data: " + json.dumps({"choices": [{"delta": FRAGMENTS[1], "finish_reason": None}]}) + "\n\n").encode()

    _patch_transport(monkeypatch, lambda r: httpx.Response(200, content=slow()))
    engine = _ready_engine(tmp_path)
    chunks = []
    async for c in engine.stream_chat(ChatCompletionRequest(messages=[{"role": "user", "content": "x"}], tools=[CALC])):
        chunks.append(c)
        if c.delta_content == "thinking":
            await engine.cancel_generation()
    assert not any(c.tool_calls for c in chunks)
    assert chunks[-1].finish_reason == "stop"


# --- API -------------------------------------------------------------------------------------

async def _started(mock_engine):
    await mock_engine.start(EngineConfig(model_path=Path("S:/Mustang AI/models/mock.gguf"), n_gpu_layers=99))


@pytest.mark.asyncio
async def test_v1_non_streamed_tool_call_shape(async_client, mock_engine):
    await _started(mock_engine)
    res = await async_client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "What is 2+2?"}], "tools": [CALC], "stream": False})
    assert res.status_code == 200
    choice = res.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"] == [
        {"id": "call_mock_1", "type": "function", "function": {"name": "calculator", "arguments": '{"mock": true}'}}]


@pytest.mark.asyncio
async def test_v1_streamed_tool_call_shape(async_client, mock_engine):
    await _started(mock_engine)
    async with async_client.stream("POST", "/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "What is 2+2?"}], "tools": [CALC], "stream": True}) as res:
        assert res.status_code == 200
        events = [json.loads(line[5:]) for line in [l async for l in res.aiter_lines()]
                  if line.startswith("data:") and line[5:].strip() != "[DONE]"]
    with_calls = [e for e in events if e["choices"][0]["delta"].get("tool_calls")]
    assert len(with_calls) == 1
    assert with_calls[0]["choices"][0]["finish_reason"] == "tool_calls"
    assert with_calls[0]["choices"][0]["delta"]["tool_calls"] == [
        {"index": 0, "id": "call_mock_1", "type": "function", "function": {"name": "calculator", "arguments": '{"mock": true}'}}]


@pytest.mark.asyncio
async def test_v1_round_trip_with_tool_result(async_client, mock_engine):
    await _started(mock_engine)
    res = await async_client.post("/v1/chat/completions", json={"stream": False, "tools": [CALC], "messages": [
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_mock_1", "type": "function", "function": {"name": "calculator", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_mock_1", "content": "4"},
    ]})
    assert res.status_code == 200
    msg = res.json()["choices"][0]["message"]
    assert "tool_calls" not in msg and msg["content"].startswith("Mock answer using the tool result: 4")


@pytest.mark.asyncio
async def test_v1_rejects_malformed_tool_turns(async_client, mock_engine):
    await _started(mock_engine)
    res = await async_client.post("/v1/chat/completions", json={"stream": False, "messages": [
        {"role": "user", "content": "x"}, {"role": "tool", "content": "4"}]})
    assert res.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"tools": [CALC], "messages": [{"role": "user", "content": "What is 2+2?"}]},
    {"messages": [{"role": "user", "content": "x"}, {"role": "tool", "tool_call_id": "c", "content": "4"}]},
])
async def test_ui_chat_endpoint_rejects_tools_until_agent_mode(async_client, mock_engine, body):
    await _started(mock_engine)
    res = await async_client.post("/api/chat/completions", json={"conversation_id": "tools-test", **body})
    assert res.status_code == 400
    assert "/v1/chat/completions" in res.json()["detail"]
