"""
Mustang AI M5 Phase B: tool calls against the real llama-server (GPU).
The small test model is not a reliable agent (M5 Phase A), so tool_choice "required" forces
a call: these tests check the protocol (a structured call comes back, streamed fragments
assemble into valid JSON, a tool result is accepted), not the model's judgement.
"""

import json
import socket

import pytest

from app.config import settings
from app.core.engine_base import ChatCompletionRequest, EngineConfig
from app.core.llama_engine import LlamaServerEngine

MODEL = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
CALC = {"type": "function", "function": {"name": "calculator", "description": "Evaluate an arithmetic expression exactly.",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}}


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
    assert await engine.start(EngineConfig(model_path=model_path, n_gpu_layers=99, n_ctx=4096, port=_free_port())), engine._error_message
    try:
        yield engine
    finally:
        await engine.stop()


@pytest.mark.gpu
@pytest.mark.asyncio
async def test_live_tool_call_round_trip(live_engine):
    question = {"role": "user", "content": "What is 1234 * 5678? Use the calculator."}
    first = ChatCompletionRequest(messages=[question], tools=[CALC], tool_choice="required", temperature=0.0, max_tokens=128)
    chunks = [c async for c in live_engine.stream_chat(first)]

    calls = [tc for c in chunks if c.tool_calls for tc in c.tool_calls]
    assert len(calls) >= 1 and calls[0].function.name == "calculator"
    assert chunks[-1].finish_reason == "tool_calls"
    args = json.loads(calls[0].function.arguments)  # streamed fragments must form valid JSON
    assert isinstance(args, dict) and "expression" in args

    follow_up = ChatCompletionRequest(tools=[CALC], temperature=0.0, max_tokens=64, messages=[
        question,
        {"role": "assistant", "content": None, "tool_calls": [calls[0].model_dump()]},
        {"role": "tool", "tool_call_id": calls[0].id, "content": "7006652"},
    ])
    reply = [c async for c in live_engine.stream_chat(follow_up)]
    text = "".join(c.delta_content for c in reply)
    assert text or any(c.tool_calls for c in reply), "llama-server accepted the tool result and answered"
    assert reply[-1].finish_reason in ("stop", "tool_calls", "length")


@pytest.mark.gpu
@pytest.mark.asyncio
async def test_live_plain_chat_unchanged_with_tools_disabled(live_engine):
    req = ChatCompletionRequest(messages=[{"role": "user", "content": "Say OK."}], temperature=0.0, max_tokens=8)
    chunks = [c async for c in live_engine.stream_chat(req)]
    assert "".join(c.delta_content for c in chunks).strip()
    assert not any(c.tool_calls for c in chunks)
