"""
Mustang AI M4 Phase D — llama-server engine hardening (no GPU required).
API key handling, process flags, binary resolution, request forwarding, cancellation,
and nvidia-smi resolution.
"""

import asyncio
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.core import hardware, llama_engine
from app.core.engine_base import ChatCompletionRequest, ChatMessage, EngineConfig, EngineState
from app.core.llama_engine import LlamaServerEngine


class _SpawnCaptured(OSError):
    """Raised by the fake spawn; _spawn_and_verify reports spawn failures by returning False."""


@pytest.mark.asyncio
async def test_spawn_passes_api_key_by_env_and_hardening_flags(monkeypatch, tmp_path):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"")
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    captured = {}

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"], captured["kwargs"] = cmd, kwargs
        raise _SpawnCaptured()

    monkeypatch.setattr(llama_engine.asyncio, "create_subprocess_exec", fake_exec)
    engine = LlamaServerEngine(binary_path=exe)
    assert await engine._spawn_and_verify(EngineConfig(model_path=model, port=1)) is False

    key = captured["kwargs"]["env"]["LLAMA_API_KEY"]
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)
    assert key not in " ".join(captured["cmd"]), "API key must not appear on the command line"
    assert "--no-webui" in captured["cmd"] and "--offline" in captured["cmd"]
    assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.asyncio
async def test_each_spawn_gets_a_new_api_key(monkeypatch, tmp_path):
    exe = tmp_path / "llama-server.exe"
    exe.write_bytes(b"")
    keys = []

    async def fake_exec(*cmd, **kwargs):
        keys.append(kwargs["env"]["LLAMA_API_KEY"])
        raise _SpawnCaptured()

    monkeypatch.setattr(llama_engine.asyncio, "create_subprocess_exec", fake_exec)
    engine = LlamaServerEngine(binary_path=exe)
    for _ in range(2):
        assert await engine._spawn_and_verify(EngineConfig(model_path=tmp_path / "m.gguf", port=1)) is False
    assert keys[0] != keys[1]


def test_desktop_mode_never_resolves_llama_server_from_path(monkeypatch, tmp_path):
    monkeypatch.setattr(llama_engine.shutil, "which", lambda name: r"C:\planted\llama-server.exe")
    engine = LlamaServerEngine(binary_path=tmp_path / "missing" / "llama-server.exe")

    monkeypatch.setattr(settings, "DESKTOP", True)
    with pytest.raises(FileNotFoundError):
        engine._resolve_binary()

    monkeypatch.setattr(settings, "DESKTOP", False)
    assert engine._resolve_binary() == Path(r"C:\planted\llama-server.exe")


def _ready_engine(tmp_path) -> LlamaServerEngine:
    engine = LlamaServerEngine(binary_path=tmp_path / "llama-server.exe")
    engine._config = EngineConfig(model_path=tmp_path / "m.gguf", port=1)
    engine._state = EngineState.READY
    engine._api_key = "k" * 64
    engine._http = llama_engine.httpx.AsyncClient(timeout=120.0)  # created per process by _spawn_and_verify
    return engine


def _patch_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    class MockClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(llama_engine.httpx, "AsyncClient", MockClient)


def _sse(*contents, done=True):
    frames = [
        "data: " + json.dumps({"id": "x", "choices": [{"delta": {"content": c}, "finish_reason": None}]}) + "\n\n"
        for c in contents
    ]
    if done:
        frames.append("data: [DONE]\n\n")
    return "".join(frames)


@pytest.mark.asyncio
async def test_stream_sends_api_key_and_top_k(monkeypatch, tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=_sse("Hel", "lo"), headers={"content-type": "text/event-stream"})

    _patch_transport(monkeypatch, handler)
    engine = _ready_engine(tmp_path)
    req = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")], top_k=17)
    text = "".join([c.delta_content async for c in engine.stream_chat(req)])

    assert text == "Hello"
    assert seen["auth"] == "Bearer " + "k" * 64
    assert seen["body"]["top_k"] == 17
    assert engine._state == EngineState.READY


@pytest.mark.asyncio
async def test_one_http_client_per_process_closed_on_stop(monkeypatch, tmp_path):
    # Found in the M4 Phase G soak: a new AsyncClient per probe and per chat ended up in
    # reference cycles holding an SSL context and socket buffers, ~1 MB native per cycle.
    created = []
    real_client = httpx.AsyncClient

    class CountingClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(200, text=_sse("ok"), headers={"content-type": "text/event-stream"}))
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(llama_engine.httpx, "AsyncClient", CountingClient)
    engine = _ready_engine(tmp_path)
    req = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
    for _ in range(3):
        assert "".join([c.delta_content async for c in engine.stream_chat(req)]) == "ok"
    assert len(created) == 1

    await engine._stop_process_internal()
    assert engine._http is None and created[0].is_closed


@pytest.mark.asyncio
async def test_cancel_closes_upstream_stream_and_ends_cleanly(monkeypatch, tmp_path):
    async def slow_tokens():
        for i in range(200):
            yield ("data: " + json.dumps({"id": "x", "choices": [{"delta": {"content": f"t{i} "}, "finish_reason": None}]}) + "\n\n").encode()
            await asyncio.sleep(0.02)

    _patch_transport(monkeypatch, lambda request: httpx.Response(200, content=slow_tokens()))
    engine = _ready_engine(tmp_path)
    req = ChatCompletionRequest(messages=[ChatMessage(role="user", content="count")])

    chunks = []
    async for chunk in engine.stream_chat(req):
        chunks.append(chunk)
        if len(chunks) == 3:
            assert await engine.cancel_generation() is True

    assert len(chunks) < 20, "stream kept running after cancel"
    assert chunks[-1].finish_reason == "stop"
    assert engine._state == EngineState.READY
    assert engine._active_response is None


@pytest.mark.asyncio
async def test_upstream_error_is_still_raised_without_cancel(monkeypatch, tmp_path):
    _patch_transport(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    engine = _ready_engine(tmp_path)
    req = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
    with pytest.raises(RuntimeError, match="llama-server error 500"):
        async for _ in engine.stream_chat(req):
            pass
    assert engine._state == EngineState.READY


def test_nvidia_smi_resolution(monkeypatch, tmp_path):
    fake_system = tmp_path / "System32" / "nvidia-smi.exe"
    monkeypatch.setattr(hardware.shutil, "which", lambda name: r"C:\planted\nvidia-smi.exe")

    monkeypatch.setattr(hardware, "_SYSTEM_NVIDIA_SMI", fake_system)
    monkeypatch.setattr(settings, "DESKTOP", True)
    assert hardware._resolve_nvidia_smi() is None  # no PATH fallback in desktop mode
    monkeypatch.setattr(settings, "DESKTOP", False)
    assert hardware._resolve_nvidia_smi() == r"C:\planted\nvidia-smi.exe"

    fake_system.parent.mkdir()
    fake_system.write_bytes(b"")
    monkeypatch.setattr(settings, "DESKTOP", True)
    assert hardware._resolve_nvidia_smi() == str(fake_system)


def test_nvidia_smi_runs_without_console_window(monkeypatch, tmp_path):
    exe = tmp_path / "nvidia-smi.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(hardware, "_SYSTEM_NVIDIA_SMI", exe)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="RTX, 8151, 100, 8051, 617.42, 3, 40\n", stderr="")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)
    gpu = hardware.get_gpu_telemetry()
    assert gpu.available and gpu.driver_version == "617.42"
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW
