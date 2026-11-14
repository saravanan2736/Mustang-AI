"""
Engine command line for small cards (measured with scripts/bench_engine.py): one slot, --fit instead of forcing
every layer onto the GPU, n-gram speculative decoding (6-7x on code rewrites, identical output), optional KV
quantization; and a fallback if a custom runtime rejects the speculation flag.
"""

from pathlib import Path

import pytest

from app.core.engine_base import ALL_LAYERS, EngineConfig, EngineState
from app.core.llama_engine import LlamaServerEngine, build_command

EXE = Path("llama-server.exe")


def cfg(**kw) -> EngineConfig:
    return EngineConfig(model_path=Path("m.gguf"), **kw)


def pairs(cmd: list[str]) -> dict[str, str]:
    return {a: b for a, b in zip(cmd, cmd[1:]) if a.startswith("-")}


def test_the_default_command_is_built_for_one_user_on_a_small_card():
    cmd = build_command(cfg(), EXE)
    p = pairs(cmd)
    assert p["-np"] == "1" and p["-fit"] == "on" and p["--spec-type"] == "ngram-mod" and p["--flash-attn"] == "on"
    assert "-ngl" not in cmd, "no forced layer count: --fit decides"
    assert "-ctk" not in cmd and "-ctv" not in cmd, "the KV cache stays exact by default"
    assert {"--no-webui", "--offline", "--no-agent", "-v"} <= set(cmd), "the safety and logging flags are kept"
    assert p["-c"] == "4096" and p["-b"] == "512" and p["--host"] == "127.0.0.1"


def test_an_exact_layer_count_is_passed_through_and_fit_is_left_out():
    cmd = build_command(cfg(n_gpu_layers=20), EXE)
    assert pairs(cmd)["-ngl"] == "20" and "-fit" not in cmd
    assert "-ngl" not in build_command(cfg(n_gpu_layers=ALL_LAYERS), EXE)
    assert pairs(build_command(cfg(n_gpu_layers=0), EXE))["-ngl"] == "0", "0 layers (CPU only) is an exact choice too"


def test_speculation_can_be_turned_off():
    assert "--spec-type" not in build_command(cfg(speculation=False), EXE)


def test_kv_quantization_is_opt_in_and_brings_flash_attention_with_it():
    cmd = build_command(cfg(kv_cache_type="q8_0", flash_attention=False), EXE)
    p = pairs(cmd)
    assert p["-ctk"] == "q8_0" and p["-ctv"] == "q8_0"
    assert p["--flash-attn"] == "on", "a quantized V cache needs flash attention"
    assert "--flash-attn" not in build_command(cfg(flash_attention=False), EXE)


def test_no_value_can_inject_extra_arguments():
    cmd = build_command(cfg(), EXE)
    assert all(isinstance(a, str) for a in cmd) and not any(" " in a for a in cmd if a.startswith("-"))
    with pytest.raises(Exception):
        cfg(kv_cache_type="q4_0; calc.exe")


# --- fallback when the runtime doesn't know the speculation flag --------------------------------------------

@pytest.mark.asyncio
async def test_a_runtime_that_rejects_speculation_is_started_again_without_it(monkeypatch):
    e = LlamaServerEngine(binary_path=EXE)
    tried = []

    async def fake_spawn(config):
        tried.append(config.speculation)
        if config.speculation:
            e._stderr_buffer[:] = ["error: invalid argument: --spec-type"]
            return False
        return True

    async def noop():
        return None

    monkeypatch.setattr(e, "_spawn_and_verify", fake_spawn)
    monkeypatch.setattr(e, "_stop_process_internal", lambda: noop())
    monkeypatch.setattr(e, "_health_loop", lambda: noop())
    model = Path(__file__)  # any existing file passes the "model exists" check
    ok = await e.start(EngineConfig(model_path=model))
    assert ok and tried == [True, False] and e._config.speculation is False and e._state == EngineState.READY


@pytest.mark.asyncio
async def test_other_start_failures_are_not_retried(monkeypatch):
    e = LlamaServerEngine(binary_path=EXE)
    tried = []

    async def fake_spawn(config):
        tried.append(config.speculation)
        e._stderr_buffer[:] = ["CUDA error: out of memory"]
        e._error_message = "CUDA Out of Memory"
        return False

    async def noop():
        return None

    monkeypatch.setattr(e, "_spawn_and_verify", fake_spawn)
    monkeypatch.setattr(e, "_stop_process_internal", lambda: noop())
    ok = await e.start(EngineConfig(model_path=Path(__file__)))
    assert not ok and tried == [True], "an out-of-memory failure is not a flag problem"
