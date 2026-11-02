"""
Mustang AI Download Manager Unit & Integration Tests
Validates queue management, HTTP Range resume (RFC 7233), rolling speed/ETA,
SHA-256 checksum verification, atomic file finalization, model registration,
cancellation cleanup, restart recovery, and strict anti-SSRF protection.
"""

import asyncio
import hashlib
import httpcore
import io
import os
from pathlib import Path
import socket
import time
import gguf
import httpx
import pytest
from httpx import AsyncClient

from app.config import settings
from app.core import download_manager as dm_module
from app.core.download_manager import (
    DownloadManager,
    is_safe_public_destination,
    can_transition,
    is_ip_allowed,
    PinningSafeNetworkBackend,
    SafeAsyncTransport,
    safe_atomic_move,
)
from app.db.database import get_db, init_db


def create_tiny_gguf_bytes(arch: str = "qwen2", blocks: int = 16) -> bytes:
    """Generates a valid lightweight GGUF binary in-memory in ~5ms."""
    bio = io.BytesIO()
    # Write to a temporary file then read bytes (gguf.GGUFWriter requires a path or file)
    temp_path = settings.RUN_DIR / f"test_tiny_{time.time_ns()}.gguf"
    w = gguf.GGUFWriter(temp_path, arch)
    w.add_block_count(blocks)
    w.add_context_length(2048)
    w.add_embedding_length(896)
    w.add_head_count(14)
    w.add_head_count_kv(2)
    w.add_file_type(2)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    content = temp_path.read_bytes()
    temp_path.unlink(missing_ok=True)
    return content


@pytest.fixture
def test_download_sandbox(tmp_path: Path):
    """Creates isolated staging and models directories for download tests."""
    staging = tmp_path / "staging_part"
    staging.mkdir(parents=True, exist_ok=True)
    return staging


@pytest.fixture
async def mock_dm_client(test_download_sandbox: Path):
    """Provides an isolated DownloadManager instance wired to a mock HTTP transport."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM downloads;")
        await db.execute("DELETE FROM models WHERE download_origin = 'huggingface';")
        await db.commit()
    finally:
        await db.close()

    test_binary = create_tiny_gguf_bytes()
    test_sha256 = hashlib.sha256(test_binary).hexdigest()
    test_size = len(test_binary)

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        headers = dict(request.headers)
        range_header = headers.get("range", "")

        # 1. SSRF target simulation
        if "evil-redirect" in url_str:
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8080/internal"})
        if "bad-loopback" in url_str:
            return httpx.Response(200, text="internal admin panel")

        # 2. Rate limit simulation
        if "rate-limit" in url_str:
            return httpx.Response(429, headers={"Retry-After": "30"}, text="Too Many Requests")

        # 3. Server error simulation
        if "server-error" in url_str:
            return httpx.Response(500, text="Internal Server Error")

        # 4. Server ignoring range request (returns 200 with full content)
        if "ignore-range" in url_str:
            return httpx.Response(
                200,
                headers={"Content-Length": str(test_size)},
                content=test_binary,
            )

        # 5. Invalid Content-Range simulation
        if "bad-range" in url_str:
            return httpx.Response(
                206,
                headers={
                    "Content-Range": "bytes 99999-100000/100000",
                    "Content-Length": "1",
                },
                content=b"X",
            )

        # 6. Standard valid download with Range support
        if range_header.startswith("bytes="):
            parts = range_header[6:].split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else test_size - 1
            sliced = test_binary[start : end + 1]
            return httpx.Response(
                206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{test_size}",
                    "Content-Length": str(len(sliced)),
                    "ETag": '"test-etag-1234"',
                },
                content=sliced,
            )

        return httpx.Response(
            200,
            headers={
                "Content-Length": str(test_size),
                "ETag": '"test-etag-1234"',
            },
            content=test_binary,
        )

    transport = httpx.MockTransport(mock_handler)
    mock_http = httpx.AsyncClient(transport=transport, base_url="https://huggingface.co")
    models_sandbox = test_download_sandbox.parent / "test_models"
    models_sandbox.mkdir(parents=True, exist_ok=True)
    dm = DownloadManager(downloads_dir=test_download_sandbox, models_dir=models_sandbox, custom_client=mock_http)

    orig_dm = dm_module.download_manager
    dm_module.set_download_manager(dm)

    try:
        yield {
            "manager": dm,
            "binary": test_binary,
            "sha256": test_sha256,
            "size": test_size,
        }
    finally:
        dm_module.set_download_manager(orig_dm)
        cleanup_db = await get_db()
        try:
            await cleanup_db.execute("DELETE FROM downloads;")
            await cleanup_db.execute("DELETE FROM models WHERE download_origin = 'huggingface';")
            await cleanup_db.commit()
        finally:
            await cleanup_db.close()


# ==================== 1. QUEUE & VALIDATION TESTS ====================

@pytest.mark.asyncio
async def test_create_download_success(async_client: AsyncClient, mock_dm_client, auth_headers):
    """Verifies that a valid download request is successfully enqueued."""
    payload = {
        "repo_id": "TestOrg/TestModel-GGUF",
        "filename": "test-model-q4_k_m.gguf",
        "revision": "main",
        "expected_sha256": mock_dm_client["sha256"],
        "total_bytes": mock_dm_client["size"],
    }
    res = await async_client.post("/api/downloads", json=payload, headers=auth_headers)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "QUEUED"
    assert data["repo_id"] == "TestOrg/TestModel-GGUF"
    assert data["filename"] == "test-model-q4_k_m.gguf"
    assert data["sha256_expected"] == mock_dm_client["sha256"]
    assert "https://huggingface.co/TestOrg/TestModel-GGUF/resolve/main/" in data["canonical_url"]


@pytest.mark.asyncio
async def test_duplicate_download_rejection(async_client: AsyncClient, mock_dm_client, auth_headers):
    """Verifies that attempting to queue an identical active/queued download returns HTTP 409."""
    payload = {
        "repo_id": "DupOrg/DupModel-GGUF",
        "filename": "dup-model-q4_k_m.gguf",
        "revision": "main",
        "total_bytes": 1000,
    }
    r1 = await async_client.post("/api/downloads", json=payload, headers=auth_headers)
    assert r1.status_code == 201

    # Second identical download
    r2 = await async_client.post("/api/downloads", json=payload, headers=auth_headers)
    assert r2.status_code == 409
    assert "already" in r2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_path_traversal_rejection(async_client: AsyncClient, mock_dm_client, auth_headers):
    """Verifies that path traversal attempts in repo_id or filename are rejected with HTTP 400."""
    bad_payload = {
        "repo_id": "EvilOrg/../../etc",
        "filename": "model.gguf",
    }
    r = await async_client.post("/api/downloads", json=bad_payload, headers=auth_headers)
    assert r.status_code == 400

    bad_file = {
        "repo_id": "GoodOrg/GoodModel-GGUF",
        "filename": "../../evil.gguf",
    }
    r2 = await async_client.post("/api/downloads", json=bad_file, headers=auth_headers)
    assert r2.status_code == 400


# ==================== 2. STREAMING, PROGRESS & INTEGRITY ====================

@pytest.mark.asyncio
async def test_successful_streaming_and_atomic_finalization(mock_dm_client):
    """Verifies chunk streaming, size accounting, sha256 verification, and atomic file move."""
    dm = mock_dm_client["manager"]
    expected_sha = mock_dm_client["sha256"]
    total_size = mock_dm_client["size"]

    # Create task
    task = await dm.create_download(
        repo_id="ValidOrg/ValidModel-GGUF",
        filename="valid-model-q4_k_m.gguf",
        expected_sha256=expected_sha,
        total_bytes=total_size,
    )

    # Process task manually through stream_and_write
    await dm._stream_and_write(task)

    # Verify task state in SQLite
    updated = await dm.get_download(task["id"])
    assert updated is not None
    assert updated["status"] == "COMPLETED"
    assert updated["downloaded_bytes"] == total_size
    assert updated["sha256_actual"] == expected_sha

    # Verify final destination file exists and staging .part file is gone
    final_file = Path(updated["destination_path"])
    staging_file = dm.downloads_dir / f"{task['id']}.part"
    assert final_file.exists()
    assert not staging_file.exists()
    assert final_file.stat().st_size == total_size

    # Clean up created file
    final_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_sha256_mismatch_fails_and_quarantines(mock_dm_client):
    """Verifies that an SHA-256 mismatch transitions task to FAILED and purges the file."""
    dm = mock_dm_client["manager"]
    total_size = mock_dm_client["size"]

    # Provide intentionally wrong SHA-256
    task = await dm.create_download(
        repo_id="CorruptOrg/CorruptModel-GGUF",
        filename="corrupt-model-q4_k_m.gguf",
        expected_sha256="deadbeef" * 8,
        total_bytes=total_size,
    )

    await dm._stream_and_write(task)

    updated = await dm.get_download(task["id"])
    assert updated["status"] == "FAILED"
    assert "sha-256 integrity check failed" in updated["error_message"].lower()

    # Staging and final files must not exist
    staging_file = dm.downloads_dir / f"{task['id']}.part"
    final_file = Path(updated["destination_path"])
    assert not staging_file.exists()
    assert not final_file.exists()


# ==================== 3. PAUSE, RESUME (HTTP 206) & CANCELLATION ====================

@pytest.mark.asyncio
async def test_pause_and_resume_with_http_206(mock_dm_client):
    """Verifies that a paused task retains partial bytes and resumes via HTTP Range 206."""
    dm = mock_dm_client["manager"]
    total_size = mock_dm_client["size"]

    task = await dm.create_download(
        repo_id="ResumeOrg/ResumeModel-GGUF",
        filename="resume-model-q4_k_m.gguf",
        expected_sha256=mock_dm_client["sha256"],
        total_bytes=total_size,
    )
    task_id = task["id"]

    # Simulate partial staging file on disk (first 100 bytes)
    staging_file = dm.downloads_dir / f"{task_id}.part"
    staging_file.write_bytes(mock_dm_client["binary"][:100])

    # Pause download
    await dm.pause_download(task_id)
    paused = await dm.get_download(task_id)
    assert paused["status"] == "PAUSED"
    assert staging_file.exists()

    # Resume download
    await dm.resume_download(task_id)
    resumed = await dm.get_download(task_id)
    assert resumed["status"] == "QUEUED"

    # Worker streaming picks it up and finishes
    await dm._stream_and_write(resumed)
    completed = await dm.get_download(task_id)
    assert completed["status"] == "COMPLETED"
    assert completed["downloaded_bytes"] == total_size

    # Clean up
    Path(completed["destination_path"]).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_cancellation_cleans_up_staging_file(mock_dm_client):
    """Verifies that cancelling a download purges the partial staging file."""
    dm = mock_dm_client["manager"]
    task = await dm.create_download(
        repo_id="CancelOrg/CancelModel-GGUF",
        filename="cancel-model-q4_k_m.gguf",
        total_bytes=5000,
    )
    task_id = task["id"]

    # Write dummy staging file
    staging_file = dm.downloads_dir / f"{task_id}.part"
    staging_file.write_bytes(b"PARTIAL_STAGING_CONTENT")
    assert staging_file.exists()

    await dm.cancel_download(task_id)
    cancelled = await dm.get_download(task_id)
    assert cancelled["status"] == "CANCELLED"
    assert not staging_file.exists()


# ==================== 4. ERROR HANDLING & SECURITY ====================

@pytest.mark.asyncio
async def test_http_429_rate_limit_handling(mock_dm_client):
    """Verifies that an HTTP 429 response sets task status to FAILED with Retry-After details."""
    dm = mock_dm_client["manager"]
    task = await dm.create_download(
        repo_id="RateOrg/RateModel-GGUF",
        filename="rate-limit-model.gguf",
        total_bytes=1000,
    )
    await dm._stream_and_write(task)
    updated = await dm.get_download(task["id"])
    assert updated["status"] == "FAILED"
    assert "rate limit" in updated["error_message"].lower()


@pytest.mark.asyncio
async def test_redirect_ssrf_rejection():
    """Verifies that redirects to loopback or private networks are blocked."""
    assert not is_safe_public_destination("http://127.0.0.1:8080/secret")
    assert not is_safe_public_destination("http://localhost:18000/api")
    assert not is_safe_public_destination("https://10.0.0.1/model.gguf")
    assert not is_safe_public_destination("https://192.168.1.1/model.gguf")
    assert not is_safe_public_destination("https://169.254.169.254/metadata")
    assert is_safe_public_destination("https://huggingface.co/model.gguf")


# ==================== 5. RESTART RECOVERY & RECONCILIATION ====================

@pytest.mark.asyncio
async def test_application_restart_reconciles_stale_tasks(mock_dm_client):
    """Verifies that interrupted DOWNLOADING tasks are safely transitioned to PAUSED on restart."""
    dm = mock_dm_client["manager"]
    db = await get_db()
    try:
        # Insert a simulated stale active task
        await db.execute(
            """
            INSERT INTO downloads (
                id, repo_id, filename, canonical_url, destination_path,
                total_bytes, downloaded_bytes, status, speed_bps, eta_seconds, created_at, updated_at
            ) VALUES ('dl-stale-001', 'StaleOrg/StaleModel-GGUF', 'stale.gguf', 'https://hf.co/model',
                      'models/stale.gguf', 5000, 2500, 'DOWNLOADING', 100, 25, 1000, 1000);
            """
        )
        await db.commit()
    finally:
        await db.close()

    # Trigger recovery
    await dm.recover_stale_tasks()

    recovered = await dm.get_download("dl-stale-001")
    assert recovered is not None
    assert recovered["status"] == "PAUSED"
    assert "interrupted by application restart" in recovered["error_message"].lower()


# ==================== 6. STATE MACHINE TRANSITION ENFORCEMENT ====================

def test_state_machine_transition_rules():
    """Verifies valid and forbidden state machine transitions."""
    assert can_transition("QUEUED", "DOWNLOADING")
    assert can_transition("QUEUED", "CANCELLED")
    assert not can_transition("QUEUED", "COMPLETED")

    assert can_transition("DOWNLOADING", "PAUSED")
    assert can_transition("DOWNLOADING", "VERIFYING")
    assert can_transition("DOWNLOADING", "FAILED")
    assert not can_transition("DOWNLOADING", "COMPLETED")  # Must go through VERIFYING

    assert can_transition("VERIFYING", "COMPLETED")
    assert can_transition("VERIFYING", "FAILED")
    assert not can_transition("VERIFYING", "QUEUED")

    assert not can_transition("COMPLETED", "DOWNLOADING")
    assert not can_transition("COMPLETED", "QUEUED")


# ==================== 7. API ENDPOINT & ACTION TESTS ====================

@pytest.mark.asyncio
async def test_unauthenticated_download_actions_rejected(anon_client: AsyncClient, mock_dm_client):
    """Verifies that POST /api/downloads endpoints require X-Mustang-Token authentication."""
    r1 = await anon_client.post("/api/downloads", json={"repo_id": "a/b", "filename": "c.gguf"})
    assert r1.status_code == 401

    r2 = await anon_client.post("/api/downloads/dl-fake/pause")
    assert r2.status_code == 401

    r3 = await anon_client.post("/api/downloads/dl-fake/resume")
    assert r3.status_code == 401

    r4 = await anon_client.post("/api/downloads/dl-fake/cancel")
    assert r4.status_code == 401


@pytest.mark.asyncio
async def test_download_action_endpoints(async_client: AsyncClient, mock_dm_client, auth_headers):
    """Verifies pause, resume, cancel, and retry endpoints via HTTP API."""
    payload = {
        "repo_id": "ActionOrg/ActionModel-GGUF",
        "filename": "action-model-q4_k_m.gguf",
        "total_bytes": 1000,
    }
    create_res = await async_client.post("/api/downloads", json=payload, headers=auth_headers)
    assert create_res.status_code == 201
    task_id = create_res.json()["id"]

    # Pause via API
    pause_res = await async_client.post(f"/api/downloads/{task_id}/pause", headers=auth_headers)
    assert pause_res.status_code == 200
    assert pause_res.json()["status"] == "PAUSED"

    # Resume via API
    resume_res = await async_client.post(f"/api/downloads/{task_id}/resume", headers=auth_headers)
    assert resume_res.status_code == 200
    assert resume_res.json()["status"] == "QUEUED"

    # Cancel via API
    cancel_res = await async_client.post(f"/api/downloads/{task_id}/cancel", headers=auth_headers)
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "CANCELLED"

    # Retry via API
    retry_res = await async_client.post(f"/api/downloads/{task_id}/retry", headers=auth_headers)
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_queue_capacity_limit(async_client: AsyncClient, mock_dm_client, auth_headers):
    """Verifies that exceeding the 10 queued download capacity returns HTTP 429."""
    dm = mock_dm_client["manager"]
    db = await get_db()
    try:
        # Pre-seed 10 queued tasks
        now = time.time()
        for i in range(10):
            await db.execute(
                """
                INSERT INTO downloads (
                    id, repo_id, filename, canonical_url, destination_path,
                    total_bytes, downloaded_bytes, status, speed_bps, eta_seconds, created_at, updated_at
                ) VALUES (?, 'Q/M', ?, 'https://hf.co', 'models/x.gguf', 100, 0, 'QUEUED', 0, 0, ?, ?);
                """,
                (f"dl-queue-{i}", f"file-{i}.gguf", now, now),
            )
        await db.commit()
    finally:
        await db.close()

    payload = {
        "repo_id": "OverloadOrg/Model-GGUF",
        "filename": "model-11.gguf",
        "total_bytes": 1000,
    }
    res = await async_client.post("/api/downloads", json=payload, headers=auth_headers)
    assert res.status_code == 429
    assert "queue is full" in res.json()["detail"].lower()


# ==================== 5. AUDIT & REMEDIATION TESTS ====================

@pytest.mark.asyncio
async def test_no_expected_checksum_sets_unverified_status(mock_dm_client, auth_headers):
    """Verifies that downloads without an upstream checksum complete with UNVERIFIED_NO_CHECKSUM."""
    dm = mock_dm_client["manager"]
    task = await dm.create_download(
        repo_id="Community/NoHash-GGUF",
        filename="nohash.gguf",
        total_bytes=mock_dm_client["size"],
        expected_sha256=None,  # No upstream hash available
    )
    assert task["sha256_expected"] is None

    # Stream and finalize
    await dm._stream_and_write(task)

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task["id"],))
        assert rows[0]["status"] == "COMPLETED"
        assert rows[0]["integrity_status"] == "UNVERIFIED_NO_CHECKSUM"
        assert rows[0]["sha256_actual"] == mock_dm_client["sha256"]

        # Verify model was registered in models table
        m_rows = await db.execute_fetchall("SELECT * FROM models WHERE filename = 'nohash.gguf';")
        assert len(m_rows) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_expected_checksum_match_sets_verified_status(mock_dm_client):
    """Verifies that downloads with matching expected hash complete with VERIFIED status."""
    dm = mock_dm_client["manager"]
    task = await dm.create_download(
        repo_id="Community/Verified-GGUF",
        filename="verified.gguf",
        total_bytes=mock_dm_client["size"],
        expected_sha256=mock_dm_client["sha256"],
    )

    await dm._stream_and_write(task)

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task["id"],))
        assert rows[0]["status"] == "COMPLETED"
        assert rows[0]["integrity_status"] == "VERIFIED"
        assert rows[0]["sha256_actual"] == mock_dm_client["sha256"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_file_size_mismatch_fails_and_quarantines(mock_dm_client):
    """Verifies that file size mismatch marks task FAILED with FAILED_SIZE_MISMATCH."""
    dm = mock_dm_client["manager"]
    task = await dm.create_download(
        repo_id="Community/BadSize-GGUF",
        filename="badsize.gguf",
        total_bytes=mock_dm_client["size"] + 5000,  # Expected size differs from served size
    )

    await dm._stream_and_write(task)

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task["id"],))
        assert rows[0]["status"] == "FAILED"
        assert rows[0]["integrity_status"] == "FAILED_SIZE_MISMATCH"
        assert "size mismatch" in rows[0]["error_message"].lower()

        # Destination model must not exist in models table
        m_rows = await db.execute_fetchall("SELECT * FROM models WHERE filename = 'badsize.gguf';")
        assert len(m_rows) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_resume_etag_mismatch_restarts_from_zero(test_download_sandbox: Path):
    """Verifies that if remote ETag changes between pause and resume, transfer restarts from 0."""
    binary = create_tiny_gguf_bytes()
    half = len(binary) // 2

    # Handler returns changed ETag
    def handler(request: httpx.Request) -> httpx.Response:
        range_h = request.headers.get("range", "")
        if range_h:
            # Resumed request with changed remote ETag
            return httpx.Response(
                206,
                headers={
                    "Content-Range": f"bytes {half}-{len(binary)-1}/{len(binary)}",
                    "Content-Length": str(len(binary) - half),
                    "ETag": '"new-modified-etag-9999"',  # Remote file modified!
                },
                content=binary[half:],
            )
        return httpx.Response(
            200,
            headers={
                "Content-Length": str(len(binary)),
                "ETag": '"new-modified-etag-9999"',
            },
            content=binary,
        )

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://huggingface.co")
    models_sandbox = test_download_sandbox.parent / "test_models"
    models_sandbox.mkdir(parents=True, exist_ok=True)
    dm = DownloadManager(downloads_dir=test_download_sandbox, models_dir=models_sandbox, custom_client=mock_http)

    task = await dm.create_download(
        repo_id="Org/EtagModel-GGUF",
        filename="etag_test.gguf",
        total_bytes=len(binary),
    )
    # Simulate partial file with old ETag
    staging = test_download_sandbox / f"{task['id']}.part"
    staging.write_bytes(binary[:half])
    await dm.update_task_state(task["id"], "DOWNLOADING", downloaded_bytes=half, etag='"old-etag-1111"')

    # Resuming should detect ETag mismatch and restart from zero
    fresh_task = await dm.get_download(task["id"])
    await dm._stream_and_write(fresh_task)

    final_task = await dm.get_download(task["id"])
    assert final_task["status"] == "COMPLETED"
    assert final_task["downloaded_bytes"] == len(binary)


@pytest.mark.asyncio
async def test_resume_server_returns_200_restarts_from_zero(test_download_sandbox: Path):
    """Verifies that if server ignores Range and returns 200, staging file is reset to 0."""
    binary = create_tiny_gguf_bytes()
    half = len(binary) // 2

    def handler(request: httpx.Request) -> httpx.Response:
        # Always return 200 OK with full content (ignoring Range)
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(binary))},
            content=binary,
        )

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://huggingface.co")
    models_sandbox = test_download_sandbox.parent / "test_models"
    models_sandbox.mkdir(parents=True, exist_ok=True)
    dm = DownloadManager(downloads_dir=test_download_sandbox, models_dir=models_sandbox, custom_client=mock_http)

    task = await dm.create_download(
        repo_id="Org/NoRangeServer-GGUF",
        filename="norange.gguf",
        total_bytes=len(binary),
    )
    staging = test_download_sandbox / f"{task['id']}.part"
    staging.write_bytes(binary[:half])

    fresh_task = await dm.get_download(task["id"])
    await dm._stream_and_write(fresh_task)

    final_task = await dm.get_download(task["id"])
    assert final_task["status"] == "COMPLETED"
    assert final_task["downloaded_bytes"] == len(binary)


@pytest.mark.asyncio
async def test_resume_malformed_content_range_restarts_from_zero(test_download_sandbox: Path):
    """Verifies that malformed or inconsistent Content-Range headers safely restart from byte 0."""
    binary = create_tiny_gguf_bytes()
    half = len(binary) // 2

    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        range_h = request.headers.get("range", "")
        if range_h and attempts["count"] == 1:
            # Send bogus Content-Range on first resume attempt
            return httpx.Response(
                206,
                headers={"Content-Range": "bytes 99999-99999/100000"},
                content=b"bad-data",
            )
        # On restart from 0, serve full content
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(binary))},
            content=binary,
        )

    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://huggingface.co")
    models_sandbox = test_download_sandbox.parent / "test_models"
    models_sandbox.mkdir(parents=True, exist_ok=True)
    dm = DownloadManager(downloads_dir=test_download_sandbox, models_dir=models_sandbox, custom_client=mock_http)

    task = await dm.create_download(
        repo_id="Org/MalformedRange-GGUF",
        filename="malformed_range.gguf",
        total_bytes=len(binary),
    )
    staging = test_download_sandbox / f"{task['id']}.part"
    staging.write_bytes(binary[:half])

    fresh_task = await dm.get_download(task["id"])
    await dm._stream_and_write(fresh_task)

    final_task = await dm.get_download(task["id"])
    assert final_task["status"] == "COMPLETED"
    assert final_task["downloaded_bytes"] == len(binary)


@pytest.mark.asyncio
async def test_resumed_download_hashes_complete_file(mock_dm_client):
    """Verifies that a resumed download computes SHA-256 over ALL bytes (both prior and resumed)."""
    dm = mock_dm_client["manager"]
    binary = mock_dm_client["binary"]
    expected_full_sha = mock_dm_client["sha256"]
    half = len(binary) // 2

    task = await dm.create_download(
        repo_id="Org/ResumedHash-GGUF",
        filename="resumed_hash.gguf",
        total_bytes=len(binary),
        expected_sha256=expected_full_sha,
    )

    # Pre-populate first half on disk
    staging = dm.downloads_dir / f"{task['id']}.part"
    staging.write_bytes(binary[:half])

    # Resume streaming
    await dm._stream_and_write(task)

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task["id"],))
        assert rows[0]["status"] == "COMPLETED"
        assert rows[0]["integrity_status"] == "VERIFIED"
        assert rows[0]["sha256_actual"] == expected_full_sha
    finally:
        await db.close()


def test_ip_safety_validation():
    """Validates public vs prohibited IP classifications for anti-SSRF."""
    # Loopback
    assert is_ip_allowed("127.0.0.1") is False
    assert is_ip_allowed("127.0.0.2") is False
    assert is_ip_allowed("::1") is False

    # RFC 1918 Private
    assert is_ip_allowed("10.0.0.1") is False
    assert is_ip_allowed("192.168.1.100") is False
    assert is_ip_allowed("172.16.5.5") is False
    assert is_ip_allowed("172.31.255.255") is False
    assert is_ip_allowed("fc00::1") is False

    # Carrier-Grade NAT (CG-NAT RFC 6598)
    assert is_ip_allowed("100.64.0.1") is False
    assert is_ip_allowed("100.127.255.254") is False

    # Link-local & Multicast
    assert is_ip_allowed("169.254.1.1") is False
    assert is_ip_allowed("fe80::1") is False
    assert is_ip_allowed("224.0.0.1") is False
    assert is_ip_allowed("ff02::1") is False

    # Documentation
    assert is_ip_allowed("192.0.2.1") is False
    assert is_ip_allowed("198.51.100.1") is False
    assert is_ip_allowed("203.0.113.1") is False

    # Public Routable
    assert is_ip_allowed("8.8.8.8") is True
    assert is_ip_allowed("1.1.1.1") is True
    assert is_ip_allowed("104.26.4.15") is True
    assert is_ip_allowed("2606:4700:4700::1111") is True


@pytest.mark.asyncio
async def test_pinning_backend_blocks_dns_rebinding_mixed_answers(monkeypatch):
    """Verifies that PinningSafeNetworkBackend blocks connections when DNS returns mixed public & private IPs."""
    backend = PinningSafeNetworkBackend()

    # Simulate DNS rebinding returning a public IP followed by a private IP
    def fake_getaddrinfo(host, port, proto=0):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(httpcore.ConnectError) as exc_info:
        await backend.connect_tcp("rebind.attacker.com", 443)

    assert "Blocked SSRF / DNS rebinding" in str(exc_info.value)


def test_safe_atomic_move_cross_drive_two_phase(tmp_path: Path, monkeypatch):
    """Verifies safe two-phase copy, flush, fsync, destination atomic rename, and source deletion."""
    src = tmp_path / "source.gguf"
    dest = tmp_path / "subfolder" / "destination.gguf"
    dest.parent.mkdir(parents=True, exist_ok=True)

    src_content = b"TEST_GGUF_DATA_SAFE_MOVE_12345"
    src.write_bytes(src_content)

    # Simulate cross-drive move (WinError 17)
    orig_replace = os.replace
    call_count = {"count": 0}

    def simulated_cross_drive_replace(s, d):
        call_count["count"] += 1
        if call_count["count"] == 1:
            err = OSError("The system cannot move the file to a different disk drive.")
            err.winerror = 17
            raise err
        # Destination atomic rename succeeds
        orig_replace(s, d)

    monkeypatch.setattr(os, "replace", simulated_cross_drive_replace)

    safe_atomic_move(src, dest)

    assert dest.exists()
    assert dest.read_bytes() == src_content
    assert not src.exists(), "Source file must be unlinked after successful destination replace"


@pytest.mark.asyncio
async def test_crash_recovery_verifying_file_moved_but_unregistered(test_download_sandbox: Path):
    """
    Simulates crash in VERIFYING state where file was moved to destination but process died
    before SQLite registration: startup reconciliation registers model idempotently.
    """
    binary = create_tiny_gguf_bytes()
    expected_sha = hashlib.sha256(binary).hexdigest()

    models_sandbox = test_download_sandbox.parent / "test_models"
    models_sandbox.mkdir(parents=True, exist_ok=True)
    dest_file = models_sandbox / "recovered_test_model.gguf"
    dest_file.write_bytes(binary)

    task_id = "dl-crash-verifying-001"
    now = time.time()
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO downloads (
                id, repo_id, filename, canonical_url, destination_path,
                total_bytes, downloaded_bytes, status, error_message, sha256_expected,
                sha256_actual, speed_bps, eta_seconds, created_at, updated_at
            ) VALUES (?, 'Org/Recovered', 'recovered_test_model.gguf', 'https://hf.co', ?, ?, ?, 'VERIFYING', NULL, ?, NULL, 0, 0, ?, ?);
            """,
            (task_id, str(dest_file), len(binary), len(binary), expected_sha, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    dm = DownloadManager(downloads_dir=test_download_sandbox, models_dir=models_sandbox)
    await dm.recover_stale_tasks()

    db = await get_db()
    try:
        task_rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task_id,))
        assert task_rows[0]["status"] == "COMPLETED"
        assert task_rows[0]["integrity_status"] == "VERIFIED"

        m_rows = await db.execute_fetchall("SELECT * FROM models WHERE filename = 'recovered_test_model.gguf';")
        assert len(m_rows) == 1
    finally:
        await db.execute("DELETE FROM downloads WHERE id = ?;", (task_id,))
        await db.execute("DELETE FROM models WHERE filename = 'recovered_test_model.gguf';")
        await db.commit()
        await db.close()
        dest_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_crash_recovery_downloading_recovers_to_paused(test_download_sandbox: Path):
    """Verifies that interrupted DOWNLOADING task recovers to PAUSED with partial bytes preserved."""
    binary = create_tiny_gguf_bytes()
    half = len(binary) // 2

    task_id = "dl-crash-downloading-001"
    staging = test_download_sandbox / f"{task_id}.part"
    staging.write_bytes(binary[:half])

    models_sandbox = test_download_sandbox.parent / "test_models"
    models_sandbox.mkdir(parents=True, exist_ok=True)

    now = time.time()
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO downloads (
                id, repo_id, filename, canonical_url, destination_path,
                total_bytes, downloaded_bytes, status, error_message, created_at, updated_at
            ) VALUES (?, 'Org/Crash', 'crash.gguf', 'https://hf.co', 'models/crash.gguf', ?, ?, 'DOWNLOADING', NULL, ?, ?);
            """,
            (task_id, len(binary), half, now, now),
        )
        await db.commit()
    finally:
        await db.close()

    dm = DownloadManager(downloads_dir=test_download_sandbox, models_dir=models_sandbox)
    await dm.recover_stale_tasks()

    db = await get_db()
    try:
        task_rows = await db.execute_fetchall("SELECT * FROM downloads WHERE id = ?;", (task_id,))
        assert task_rows[0]["status"] == "PAUSED"
        assert "application restart" in task_rows[0]["error_message"].lower()
        # Verify partial file remained intact on disk
        assert staging.exists()
        assert staging.stat().st_size == half
    finally:
        await db.execute("DELETE FROM downloads WHERE id = ?;", (task_id,))
        await db.commit()
        await db.close()
        staging.unlink(missing_ok=True)


