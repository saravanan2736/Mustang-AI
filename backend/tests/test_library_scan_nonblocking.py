"""
Found when copying multi-GB models into the models folder: the library scan parsed each new
GGUF file (the whole tokenizer, seconds and hundreds of MB per file) directly on the event loop,
so the backend stopped answering every request, /health included, until the scan finished.
The parse now runs in a thread.
"""

import asyncio
import time
from pathlib import Path

import gguf
import pytest

from app.core import model_library


def _tiny_gguf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = gguf.GGUFWriter(path, "llama")
    w.add_block_count(12)
    w.add_context_length(2048)
    w.add_embedding_length(768)
    w.add_head_count(12)
    w.add_head_count_kv(12)
    w.add_file_type(2)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


@pytest.mark.asyncio
async def test_scan_keeps_the_event_loop_responsive(monkeypatch, tmp_path):
    models = tmp_path / "models"
    for name in ("a.gguf", "b.gguf", "c.gguf"):
        _tiny_gguf(models / name)

    real_parse = model_library.parse_gguf_metadata

    def slow_parse(path):
        time.sleep(0.4)  # stands in for a multi-GB tokenizer parse
        return real_parse(path)

    monkeypatch.setattr(model_library, "parse_gguf_metadata", slow_parse)

    longest_gap = 0.0
    stop = asyncio.Event()

    async def ticker():
        nonlocal longest_gap
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.perf_counter()
            longest_gap = max(longest_gap, now - last)
            last = now

    tick = asyncio.create_task(ticker())
    result = await model_library.scan_models_directory(models_dir=models)
    stop.set()
    await tick

    assert result["added"] == 3
    # Blocking on the loop would show as a gap of 0.4 s or more for each file.
    assert longest_gap < 0.25, f"event loop was blocked for {longest_gap:.2f} s"
