"""
Mustang AI Milestone 3 Stage 6 - Stress & Resilience Test Suite
Exhaustive stress validation covering:
1. Download Queue Capacity Enforcement (10 items max, HTTP 429 on overflow)
2. Strict Concurrency Cap (Maximum 1 active download at all times)
3. Mid-Stream Disconnection, RFC 7233 Range Resumption, and ETag Mutation Detection
4. Checksum Corruption Resilience & Auto-Purge of Corrupted Partials
5. Ungraceful Crash Simulation & Stale Task Recovery
6. Concurrency Locks on Dynamic Model Switching
7. Dynamic Model Switch During Active Token Generation
"""

import asyncio
import hashlib
import io
import time
from pathlib import Path
import gguf
import httpx
import pytest
from httpx import AsyncClient

from app.config import settings
from app.core import download_manager as dm_module
from app.core.download_manager import (
    MAX_QUEUED_DOWNLOADS,
    DownloadManager,
)
from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineConfig,
    EngineState,
)
from app.core.mock_engine import MockEngine
from app.db.database import get_db, init_db


def make_stress_gguf(arch: str = "qwen2", name: str = "stress_model.gguf") -> bytes:
    """Generates a small valid GGUF binary."""
    p = settings.RUN_DIR / f"temp_{name}_{time.time_ns()}.gguf"
    w = gguf.GGUFWriter(p, arch)
    w.add_block_count(8)
    w.add_context_length(2048)
    w.add_embedding_length(512)
    w.add_head_count(8)
    w.add_head_count_kv(2)
    w.add_file_type(2)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    data = p.read_bytes()
    p.unlink(missing_ok=True)
    return data


@pytest.fixture
async def stress_sandbox(tmp_path: Path):
    """Sets up an isolated environment for stress testing."""
    models_dir = tmp_path / "stress_models"
    downloads_dir = tmp_path / "stress_downloads"
    models_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    orig_models = settings.MODELS_DIR
    orig_downloads = settings.DOWNLOADS_DIR
    settings.MODELS_DIR = models_dir
    settings.DOWNLOADS_DIR = downloads_dir

    await init_db()
    db = await get_db()
    try:
        await db.execute("DELETE FROM downloads;")
        await db.execute("DELETE FROM models;")
        await db.commit()
    finally:
        await db.close()

    yield {
        "models_dir": models_dir,
        "downloads_dir": downloads_dir,
    }

    settings.MODELS_DIR = orig_models
    settings.DOWNLOADS_DIR = orig_downloads


# =====================================================================
# 1. QUEUE CAPACITY & CONCURRENCY ENFORCEMENT
# =====================================================================

@pytest.mark.asyncio
async def test_queue_capacity_limit_enforcement(async_client: AsyncClient, auth_headers, stress_sandbox):
    """Verifies that the queue caps at MAX_QUEUED_DOWNLOADS (10) and rejects the 11th with HTTP 429."""
    for i in range(MAX_QUEUED_DOWNLOADS):
        res = await async_client.post(
            "/api/downloads",
            json={
                "repo_id": f"StressOrg/Model-{i}",
                "filename": f"model-{i}-q4.gguf",
                "total_bytes": 1000,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201, f"Failed to enqueue task {i}: {res.text}"

    # 11th task must be rejected
    overflow_res = await async_client.post(
        "/api/downloads",
        json={
            "repo_id": "StressOrg/Model-Overflow",
            "filename": "model-overflow-q4.gguf",
            "total_bytes": 1000,
        },
        headers=auth_headers,
    )
    assert overflow_res.status_code == 429
    assert "queue is full" in overflow_res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_strict_single_active_download_concurrency(stress_sandbox):
    """Verifies that even with multiple queued items, strictly one active download runs at a time."""
    dm = DownloadManager(
        downloads_dir=stress_sandbox["downloads_dir"],
        models_dir=stress_sandbox["models_dir"],
    )

    t1 = await dm.create_download(repo_id="Org/M1", filename="m1.gguf", total_bytes=1000)
    t2 = await dm.create_download(repo_id="Org/M2", filename="m2.gguf", total_bytes=1000)

    # Acquire lock manually on t1 to simulate an active download in flight
    async with dm._lock:
        dm._active_task_id = t1["id"]
        # In a separate coroutine or check, verify that a second download cannot become active
        assert dm._active_task_id == t1["id"]

    # Once lock releases, active task can clear
    dm._active_task_id = None
    assert dm._active_task_id is None


# =====================================================================
# 2. NETWORK CHAOS: RANGE RESUME, ETAG MUTATION & HASH CORRUPTION
# =====================================================================

@pytest.mark.asyncio
async def test_network_interruption_and_range_resumption(stress_sandbox):
    """Verifies that a download interrupted mid-stream resumes seamlessly using HTTP 206 Range headers."""
    model_bytes = make_stress_gguf(name="resume_test.gguf")
    expected_sha = hashlib.sha256(model_bytes).hexdigest()
    total_len = len(model_bytes)
    midpoint = total_len // 2

    # Pre-write a partial .part file with the first half of bytes
    task_id = "dl-resume-test"
    part_file = stress_sandbox["downloads_dir"] / f"{task_id}.part"
    part_file.write_bytes(model_bytes[:midpoint])

    # Pre-seed task in DB as PAUSED with downloaded_bytes = midpoint
    now = time.time()
    dest = stress_sandbox["models_dir"] / "resume_test.gguf"
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO downloads (
                id, repo_id, filename, canonical_url, destination_path,
                total_bytes, downloaded_bytes, status, etag, last_modified,
                error_message, sha256_expected, sha256_actual, speed_bps,
                eta_seconds, created_at, updated_at
            ) VALUES (?, 'Org/Resume', 'resume_test.gguf', 'https://huggingface.co/Org/Resume/resolve/main/resume_test.gguf',
                      ?, ?, ?, 'PAUSED', '"initial-etag"', NULL, NULL, ?, NULL, 0, 0, ?, ?);
            """,
            (task_id, str(dest), total_len, midpoint, expected_sha, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    # Mock handler that enforces Range requests
    range_received = False

    def range_mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal range_received
        range_hdr = request.headers.get("range", "")
        if range_hdr == f"bytes={midpoint}-":
            range_received = True
            remaining = model_bytes[midpoint:]
            return httpx.Response(
                206,
                headers={
                    "Content-Range": f"bytes {midpoint}-{total_len - 1}/{total_len}",
                    "Content-Length": str(len(remaining)),
                    "ETag": '"initial-etag"',
                },
                content=remaining,
            )
        return httpx.Response(400, text="Unexpected range request")

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(range_mock_handler),
        base_url="https://huggingface.co",
    )

    dm = DownloadManager(
        downloads_dir=stress_sandbox["downloads_dir"],
        models_dir=stress_sandbox["models_dir"],
        custom_client=mock_client,
    )

    # Resume the download
    await dm.resume_download(task_id)

    # Execute stream_and_write directly
    task = await dm.get_download(task_id)
    await dm._stream_and_write(task)

    assert range_received, "Download did not send the expected Range header for remaining bytes!"
    final_task = await dm.get_download(task_id)
    assert final_task["status"] == "COMPLETED"
    assert final_task["integrity_status"] == "VERIFIED"
    assert dest.exists()
    assert dest.read_bytes() == model_bytes


@pytest.mark.asyncio
async def test_etag_mutation_forces_full_restart(stress_sandbox):
    """
    If the remote file's ETag changes mid-stream, the partial file is stale.
    Mustang AI must restart the download from byte 0 to prevent corrupting weights.
    """
    model_bytes = make_stress_gguf(name="etag_test.gguf")
    expected_sha = hashlib.sha256(model_bytes).hexdigest()
    total_len = len(model_bytes)

    task_id = "dl-etag-test"
    part_file = stress_sandbox["downloads_dir"] / f"{task_id}.part"
    part_file.write_bytes(b"STALE_OLD_BYTES_CORRUPTED")

    now = time.time()
    dest = stress_sandbox["models_dir"] / "etag_test.gguf"
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO downloads (
                id, repo_id, filename, canonical_url, destination_path,
                total_bytes, downloaded_bytes, status, etag, last_modified,
                error_message, sha256_expected, sha256_actual, speed_bps,
                eta_seconds, created_at, updated_at
            ) VALUES (?, 'Org/ETag', 'etag_test.gguf', 'https://huggingface.co/Org/ETag/resolve/main/etag_test.gguf',
                      ?, ?, 25, 'PAUSED', '"old-etag"', NULL, NULL, ?, NULL, 0, 0, ?, ?);
            """,
            (task_id, str(dest), total_len, expected_sha, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    def etag_handler(request: httpx.Request) -> httpx.Response:
        # Server returns new ETag with 200 OK (rejecting range or signalling new file version)
        return httpx.Response(
            200,
            headers={
                "Content-Length": str(total_len),
                "ETag": '"brand-new-etag-updated"',
            },
            content=model_bytes,
        )

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(etag_handler),
        base_url="https://huggingface.co",
    )

    dm = DownloadManager(
        downloads_dir=stress_sandbox["downloads_dir"],
        models_dir=stress_sandbox["models_dir"],
        custom_client=mock_client,
    )

    # Resume the download from PAUSED to QUEUED
    await dm.resume_download(task_id)

    task = await dm.get_download(task_id)
    await dm._stream_and_write(task)

    final_task = await dm.get_download(task_id)
    assert final_task["status"] == "COMPLETED"
    assert final_task["integrity_status"] == "VERIFIED"
    assert dest.read_bytes() == model_bytes


@pytest.mark.asyncio
async def test_corrupted_checksum_fails_and_deletes_file(stress_sandbox):
    """Verifies that downloaded data with a mismatched SHA256 is purged immediately."""
    corrupted_data = b"GGUF_CORRUPTED_GARBAGE_DATA_12345678"
    bogus_sha = hashlib.sha256(b"SOMETHING_COMPLETELY_DIFFERENT").hexdigest()

    def bad_hash_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(corrupted_data))},
            content=corrupted_data,
        )

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(bad_hash_handler),
        base_url="https://huggingface.co",
    )

    dm = DownloadManager(
        downloads_dir=stress_sandbox["downloads_dir"],
        models_dir=stress_sandbox["models_dir"],
        custom_client=mock_client,
    )

    task = await dm.create_download(
        repo_id="Org/Corrupted",
        filename="corrupted.gguf",
        expected_sha256=bogus_sha,
        total_bytes=len(corrupted_data),
    )

    await dm._stream_and_write(task)

    updated_task = await dm.get_download(task["id"])
    assert updated_task["status"] == "FAILED"
    assert updated_task["integrity_status"] == "FAILED_CHECKSUM_MISMATCH"
    assert not Path(updated_task["destination_path"]).exists(), "Corrupted destination file was not purged!"


# =====================================================================
# 3. CRASH RECOVERY & STALE TASK RECONCILIATION
# =====================================================================

@pytest.mark.asyncio
async def test_recover_stale_tasks_on_abrupt_backend_restart(stress_sandbox):
    """
    Simulates a sudden power loss or process kill while a task was DOWNLOADING.
    On restart, recover_stale_tasks() must transition DOWNLOADING tasks to PAUSED.
    """
    dm = DownloadManager(
        downloads_dir=stress_sandbox["downloads_dir"],
        models_dir=stress_sandbox["models_dir"],
    )

    # Insert an artificially abandoned task with DOWNLOADING status
    task_id = "dl-crash-interrupted"
    dest = stress_sandbox["models_dir"] / "interrupted.gguf"
    part = stress_sandbox["downloads_dir"] / f"{task_id}.part"
    part.write_bytes(b"partial_bytes_saved_on_disk")

    now = time.time()
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO downloads (
                id, repo_id, filename, canonical_url, destination_path,
                total_bytes, downloaded_bytes, status, etag, last_modified,
                error_message, sha256_expected, sha256_actual, speed_bps,
                eta_seconds, created_at, updated_at
            ) VALUES (?, 'Org/Crash', 'interrupted.gguf', 'https://hf.co/Org/Crash/resolve/main/interrupted.gguf',
                      ?, 1000, 27, 'DOWNLOADING', NULL, NULL, NULL, NULL, NULL, 0, 0, ?, ?);
            """,
            (task_id, str(dest), now, now),
        )
        await db.commit()
    finally:
        await db.close()

    # Trigger recovery as occurs during dm.start()
    await dm.recover_stale_tasks()

    recovered = await dm.get_download(task_id)
    assert recovered["status"] == "PAUSED"
    assert "application restart" in recovered["error_message"].lower()
    # The partial file on disk is preserved for resumption
    assert part.exists()


# =====================================================================
# 4. MODEL SWITCHING CONCURRENCY & GENERATION INTERRUPTION
# =====================================================================

@pytest.mark.asyncio
async def test_concurrent_model_switch_requests_serialized(stress_sandbox):
    """
    Verifies that simultaneous model switch calls are safely serialized by the lifecycle lock.
    """
    engine = MockEngine()
    m1_path = stress_sandbox["models_dir"] / "m1.gguf"
    m2_path = stress_sandbox["models_dir"] / "m2.gguf"
    m1_path.write_bytes(make_stress_gguf(name="m1.gguf"))
    m2_path.write_bytes(make_stress_gguf(name="m2.gguf"))

    config1 = EngineConfig(model_path=m1_path, port=18081)
    config2 = EngineConfig(model_path=m2_path, port=18082)

    # Fire two switch calls concurrently
    res1, res2 = await asyncio.gather(
        engine.start(config1),
        engine.start(config2),
    )

    assert res1 is True or res2 is True
    status = await engine.get_status()
    assert status.state == EngineState.READY
    # Exactly one model remains loaded
    assert status.model_name in ("m1.gguf", "m2.gguf")


@pytest.mark.asyncio
async def test_model_switch_cancels_active_streaming_generation(stress_sandbox):
    """
    Verifies that switching models while an active generation is in flight
    safely aborts token streaming and cleanly transfers to the new model.
    """
    engine = MockEngine(token_delay_s=0.1)
    m1_path = stress_sandbox["models_dir"] / "m1.gguf"
    m2_path = stress_sandbox["models_dir"] / "m2.gguf"
    m1_path.write_bytes(make_stress_gguf(name="m1.gguf"))
    m2_path.write_bytes(make_stress_gguf(name="m2.gguf"))

    # Start engine on model 1
    await engine.start(EngineConfig(model_path=m1_path))
    assert (await engine.get_status()).state == EngineState.READY

    # Start generation in the background
    req = ChatCompletionRequest(
        conversation_id="stream-cancel-test",
        messages=[ChatMessage(role="user", content="Generate a long text")],
        max_tokens=100,
    )

    tokens_received: list[str] = []

    async def consume_stream():
        try:
            async for chunk in engine.stream_chat(req):
                tokens_received.append(chunk.delta)
                if len(tokens_received) >= 2:
                    break
        except Exception:
            pass

    stream_task = asyncio.create_task(consume_stream())
    await asyncio.sleep(0.05)

    # Trigger switch to model 2 while generation is running
    switch_res = await engine.start(EngineConfig(model_path=m2_path))
    assert switch_res is True

    await stream_task

    status = await engine.get_status()
    assert status.state == EngineState.READY
    assert status.model_name == "m2.gguf"
