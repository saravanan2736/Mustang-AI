"""
Found in M4 Phase G (soak): the UI requests a memory estimate on every slider change and every
model state change, and each estimate parsed the whole GGUF file (~3 s and ~460 MB of Python
heap for a 0.5B model, mostly the tokenizer arrays). Overlapping estimates piled up in the
thread pool, stalled other requests for up to 100 s and raised the backend to ~2 GB.
The header is now parsed once per file version, and concurrent requests share that parse.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gguf
import pytest

from app.core import memory_estimator


def _tiny_gguf(path: Path, blocks: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = gguf.GGUFWriter(path, "llama")
    w.add_block_count(blocks)
    w.add_context_length(2048)
    w.add_embedding_length(768)
    w.add_head_count(12)
    w.add_head_count_kv(4)
    w.add_file_type(2)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


@pytest.fixture
def counted_reader(monkeypatch):
    memory_estimator._header_cache.clear()
    calls = {"n": 0}
    real = gguf.GGUFReader
    lock = threading.Lock()

    def counting(*args, **kwargs):
        with lock:
            calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(memory_estimator.gguf, "GGUFReader", counting)
    yield calls
    memory_estimator._header_cache.clear()


def test_concurrent_estimates_parse_the_file_once(tmp_path, counted_reader):
    model = _tiny_gguf(tmp_path / "m.gguf", blocks=12)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda ctx: memory_estimator.estimate_model_memory(model, 99, ctx), [2048, 4096] * 8))
    assert counted_reader["n"] == 1
    assert all(r.estimate_available and r.total_layers == 12 for r in results)
    # The context still changes the result: only the header is cached, not the estimate.
    assert results[0].kv_cache_gpu_mb != results[1].kv_cache_gpu_mb


def test_changed_file_is_parsed_again(tmp_path, counted_reader):
    model = _tiny_gguf(tmp_path / "m.gguf", blocks=12)
    assert memory_estimator.estimate_model_memory(model).total_layers == 12
    _tiny_gguf(model, blocks=24)
    st = model.stat()
    os.utime(model, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert memory_estimator.estimate_model_memory(model).total_layers == 24
    assert counted_reader["n"] == 2


def test_unparseable_file_is_reported_not_cached(tmp_path, counted_reader):
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"not a gguf file at all")
    for _ in range(2):
        r = memory_estimator.estimate_model_memory(bad)
        assert not r.estimate_available and "Could not parse" in r.reason
    assert counted_reader["n"] == 2
    assert not memory_estimator._header_cache
