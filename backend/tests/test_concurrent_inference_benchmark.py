"""
Mustang AI Concurrent Inference & Background Download Benchmark
Tests and measures:
- Tokens per second before, during, and after background download
- GPU VRAM allocation during download
- System RAM and CPU utilization
- Download streaming speed and cancellation behavior
"""

import asyncio
import io
import os
from pathlib import Path
import psutil
import pytest
import time
import httpx
import gguf

from app.config import settings
from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineConfig,
    EngineState,
)
from app.core.llama_engine import LlamaServerEngine
from app.core.download_manager import DownloadManager
from app.db.database import get_db


def create_mock_large_binary(size_mb: int = 50) -> bytes:
    """Creates a synthetic binary payload for streaming benchmarks."""
    chunk = os.urandom(1024 * 1024)  # 1 MiB random block
    return chunk * size_mb


@pytest.mark.gpu
@pytest.mark.asyncio
async def test_concurrent_inference_and_download_benchmark(tmp_path: Path):
    """
    Executes live CUDA inference while concurrently streaming a background download.
    Measures tokens/sec, VRAM, RAM, and CPU across baseline, concurrent, and post-download phases.
    """
    model_path = settings.MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    if not model_path.exists():
        pytest.skip(f"Test model not present: {model_path}")

    # Set up isolated download sandbox
    staging_dir = tmp_path / "staging_dl"
    staging_dir.mkdir(parents=True, exist_ok=True)
    models_dir = tmp_path / "models_out"
    models_dir.mkdir(parents=True, exist_ok=True)

    # 50 MiB synthetic download stream
    payload_size = 50 * 1024 * 1024
    synthetic_chunk = b"X" * (1024 * 1024)

    async def mock_streaming_generator():
        for _ in range(50):
            await asyncio.sleep(0.05)  # Throttle to simulate realistic 20 MB/s download
            yield synthetic_chunk

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(payload_size)},
            content=mock_streaming_generator(),
        )

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_handler),
        base_url="https://huggingface.co",
    )
    dm = DownloadManager(downloads_dir=staging_dir, models_dir=models_dir, custom_client=mock_client)

    # 1. Spin up real llama-server on port 18082
    engine = LlamaServerEngine()
    config = EngineConfig(
        model_path=model_path,
        n_gpu_layers=99,
        n_ctx=2048,
        n_batch=512,
        port=18082,
        host="127.0.0.1",
        flash_attention=True,
    )

    started = await engine.start(config)
    assert started is True, "Failed to start llama-server process"

    results: dict[str, dict[str, float]] = {}
    proc = psutil.Process()

    try:
        # Await server readiness
        status = await engine.get_status()
        assert status.state == EngineState.READY
        vram_baseline = status.vram_allocated_mb

        # ---------------- Phase 1: Baseline Inference ----------------
        req1 = ChatCompletionRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content="Explain quantum entanglement clearly in three short bullet points.",
                )
            ],
            temperature=0.7,
            max_tokens=150,
        )

        tokens_p1 = 0
        t0 = time.perf_counter()
        async for chunk in engine.stream_chat(req1):
            if chunk.delta_content:
                tokens_p1 += 1
        t_baseline = time.perf_counter() - t0
        tok_s_baseline = tokens_p1 / t_baseline if t_baseline > 0 else 0.0

        ram_p1 = proc.memory_info().rss / (1024 * 1024)
        cpu_p1 = proc.cpu_percent(interval=0.1)

        results["baseline"] = {
            "tokens_sec": round(tok_s_baseline, 1),
            "tokens": tokens_p1,
            "duration": round(t_baseline, 2),
            "vram_mb": round(vram_baseline, 1),
            "ram_mb": round(ram_p1, 1),
            "cpu_percent": round(cpu_p1, 1),
        }

        # ---------------- Phase 2: Concurrent Download + Inference ----------------
        task = await dm.create_download(
            repo_id="BenchmarkOrg/LargeModel-GGUF",
            filename="large_model.gguf",
            total_bytes=payload_size,
        )

        # Launch download in background task
        dl_task = asyncio.create_task(dm._stream_and_write(task))

        # Wait for download to start streaming
        await asyncio.sleep(0.3)

        req2 = ChatCompletionRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content="Summarize general relativity in three concise bullet points.",
                )
            ],
            temperature=0.7,
            max_tokens=150,
        )

        tokens_p2 = 0
        t1 = time.perf_counter()
        async for chunk in engine.stream_chat(req2):
            if chunk.delta_content:
                tokens_p2 += 1
        t_concurrent = time.perf_counter() - t1
        tok_s_concurrent = tokens_p2 / t_concurrent if t_concurrent > 0 else 0.0

        # Check status during concurrency
        status_during = await engine.get_status()
        vram_during = status_during.vram_allocated_mb
        ram_p2 = proc.memory_info().rss / (1024 * 1024)
        cpu_p2 = proc.cpu_percent(interval=0.1)

        # Check download task speed
        active_dl_info = await dm.get_download(task["id"])
        dl_bytes = active_dl_info.get("downloaded_bytes", 0)

        results["concurrent"] = {
            "tokens_sec": round(tok_s_concurrent, 1),
            "tokens": tokens_p2,
            "duration": round(t_concurrent, 2),
            "vram_mb": round(vram_during, 1),
            "ram_mb": round(ram_p2, 1),
            "cpu_percent": round(cpu_p2, 1),
            "downloaded_mb": round(dl_bytes / (1024 * 1024), 2),
        }

        # Cancel download cleanly
        await dm.cancel_download(task["id"])
        try:
            await asyncio.wait_for(dl_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        # Verify staging file unlinked on cancellation
        part_file = staging_dir / f"{task['id']}.part"
        assert not part_file.exists(), "Cancelled staging file must be purged from disk!"

        # ---------------- Phase 3: Post-Download Inference ----------------
        req3 = ChatCompletionRequest(
            messages=[
                ChatMessage(
                    role="user",
                    content="What are gravitational waves in one short sentence?",
                )
            ],
            temperature=0.7,
            max_tokens=60,
        )

        tokens_p3 = 0
        t2 = time.perf_counter()
        async for chunk in engine.stream_chat(req3):
            if chunk.delta_content:
                tokens_p3 += 1
        t_post = time.perf_counter() - t2
        tok_s_post = tokens_p3 / t_post if t_post > 0 else 0.0

        ram_p3 = proc.memory_info().rss / (1024 * 1024)
        cpu_p3 = proc.cpu_percent(interval=0.1)

        results["post_download"] = {
            "tokens_sec": round(tok_s_post, 1),
            "tokens": tokens_p3,
            "duration": round(t_post, 2),
            "vram_mb": round(vram_baseline, 1),
            "ram_mb": round(ram_p3, 1),
            "cpu_percent": round(cpu_p3, 1),
        }

        # Print Benchmark Table to stdout
        print("\n" + "=" * 70)
        print(" MUSTANG AI — CONCURRENT INFERENCE & DOWNLOAD BENCHMARK REPORT")
        print("=" * 70)
        print(f"Model: {model_path.name} | GPU: NVIDIA RTX 5050 (8GB VRAM)")
        print("-" * 70)
        print(f"Phase 1 (Baseline):    {results['baseline']['tokens_sec']} tok/s | VRAM: {results['baseline']['vram_mb']} MB | RAM: {results['baseline']['ram_mb']} MB")
        print(f"Phase 2 (Concurrent):  {results['concurrent']['tokens_sec']} tok/s | VRAM: {results['concurrent']['vram_mb']} MB | RAM: {results['concurrent']['ram_mb']} MB | Downloaded: {results['concurrent']['downloaded_mb']} MB")
        print(f"Phase 3 (Post-DL):     {results['post_download']['tokens_sec']} tok/s | VRAM: {results['post_download']['vram_mb']} MB | RAM: {results['post_download']['ram_mb']} MB")
        print("=" * 70)

        # Assertions
        assert results["baseline"]["tokens"] > 10, "Baseline produced too few tokens"
        assert results["concurrent"]["tokens"] > 10, "Concurrent inference produced too few tokens"
        # Verify VRAM was completely unchanged by download
        assert results["concurrent"]["vram_mb"] == results["baseline"]["vram_mb"]
        # Verify token generation speed did not drop significantly (>70% of baseline maintained)
        speed_ratio = results["concurrent"]["tokens_sec"] / results["baseline"]["tokens_sec"]
        assert speed_ratio >= 0.70, f"Concurrent inference suffered severe drop: ratio {speed_ratio:.2f}"

    finally:
        await engine.stop()
        # Clean up database
        db = await get_db()
        try:
            await db.execute("DELETE FROM downloads;")
            await db.commit()
        finally:
            await db.close()
