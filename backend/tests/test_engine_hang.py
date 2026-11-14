"""
Found in the installed app: llama-server accepted a request, then answered nothing for 18 minutes
(health check failing, GPU and CPU idle). The chat sat on "thinking" forever. The health loop now
treats a long silence as a hang: it kills the process and reloads the model.
"""

import asyncio
import time
from pathlib import Path

import pytest

from app.config import settings
from app.core.engine_base import EngineConfig, EngineState
from app.core.llama_engine import LlamaServerEngine


class _FakeProcess:
    returncode = None
    pid = 1234

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


class _SilentHttp:
    async def get(self, *a, **k):
        raise TimeoutError()


class _AnsweringHttp:
    async def get(self, *a, **k):
        class R:
            status_code = 200
        return R()


def _engine(http, monkeypatch):
    monkeypatch.setattr(settings, "ENGINE_HEALTH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(settings, "ENGINE_HUNG_SECONDS", 0.1)
    e = LlamaServerEngine(binary_path=Path("llama-server.exe"))
    e._state = EngineState.READY
    e._config = EngineConfig(model_path=Path("m.gguf"))
    e._process = _FakeProcess()
    e._http = http
    e._last_activity = time.monotonic() - 1000  # nothing written for a long time
    return e


@pytest.mark.asyncio
async def test_a_silent_engine_is_killed_and_reloaded(monkeypatch):
    e = _engine(_SilentHttp(), monkeypatch)
    restarted = []

    async def fake_start(config):
        restarted.append(config)
        return True

    monkeypatch.setattr(e, "start", fake_start)
    await asyncio.wait_for(e._health_loop(), 5)
    await asyncio.sleep(0.05)  # let the recovery task run
    assert e._process.killed and e._hung is False and restarted == [e._config]


@pytest.mark.asyncio
async def test_an_engine_that_answers_is_left_alone(monkeypatch):
    e = _engine(_AnsweringHttp(), monkeypatch)
    task = asyncio.create_task(e._health_loop())
    await asyncio.sleep(0.4)  # several times the hang limit
    e._state = EngineState.UNLOADED
    await asyncio.wait_for(task, 1)
    assert not e._process.killed


@pytest.mark.asyncio
async def test_a_busy_engine_that_cannot_answer_health_is_not_killed(monkeypatch):
    """A long prompt keeps llama-server too busy for /health, but it keeps writing output."""
    e = _engine(_SilentHttp(), monkeypatch)

    async def keep_writing():
        for _ in range(40):
            e._last_activity = time.monotonic()
            await asyncio.sleep(0.01)
        e._state = EngineState.UNLOADED

    writer = asyncio.create_task(keep_writing())
    await asyncio.wait_for(e._health_loop(), 5)
    await writer
    assert not e._process.killed
