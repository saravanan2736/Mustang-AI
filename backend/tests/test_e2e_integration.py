"""
Mustang AI Milestone 3 End-to-End Integration Test Suite
Validates the complete full-lifecycle flow across all M3 components:
1. Hugging Face Search & GGUF Repository Exploration
2. Asynchronous Download Queue, SSE Progress, SHA-256 Verification, and Local Library Auto-Registration
3. Hardware-Aware Dynamic Context Recommendations & Presets (Advisory vs Runtime)
4. Model Loading into Inference Runtime with Active Library Synchronization
5. Multi-turn Conversational Chat with Token Streaming & Regeneration
6. Safe Dynamic Model Switching & Failed Switch Rollback Protection
7. Deletion Guards for Active Models & Clean File Deletion Post-Unload
"""

import asyncio
import hashlib
import io
import json
from pathlib import Path
import time
import gguf
import httpx
import pytest
from httpx import AsyncClient

from app.config import settings
from app.core import download_manager as dm_module
from app.core.download_manager import DownloadManager
from app.core.engine_base import EngineState
from app.core.hf_client import HuggingFaceClient, set_hf_client
from app.core.mock_engine import MockEngine
from app.core import model_library
from app.db.database import get_db, init_db


def create_e2e_test_gguf(
    filename: str,
    arch: str = "qwen2",
    context_length: int = 4096,
    embedding_dim: int = 896,
    head_count: int = 14,
    head_count_kv: int = 2,
    blocks: int = 16,
) -> bytes:
    """Generates a valid, minimal GGUF model binary in-memory."""
    temp_path = settings.RUN_DIR / f"temp_{filename}_{time.time_ns()}.gguf"
    w = gguf.GGUFWriter(temp_path, arch)
    w.add_block_count(blocks)
    w.add_context_length(context_length)
    w.add_embedding_length(embedding_dim)
    w.add_head_count(head_count)
    w.add_head_count_kv(head_count_kv)
    w.add_file_type(2)  # Q4_0
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    content = temp_path.read_bytes()
    temp_path.unlink(missing_ok=True)
    return content


@pytest.fixture
async def e2e_environment(tmp_path: Path):
    """Sets up an isolated models directory, downloads directory, and mock network services."""
    models_dir = tmp_path / "models"
    downloads_dir = tmp_path / "downloads"
    models_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    # Backup original directories
    orig_models_dir = settings.MODELS_DIR
    orig_downloads_dir = settings.DOWNLOADS_DIR
    settings.MODELS_DIR = models_dir
    settings.DOWNLOADS_DIR = downloads_dir

    # Generate test GGUF models
    model1_name = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    model1_bytes = create_e2e_test_gguf(model1_name, arch="qwen2", context_length=4096)
    model1_sha256 = hashlib.sha256(model1_bytes).hexdigest()

    model2_name = "llama3-8b-instruct-q4_0.gguf"
    model2_bytes = create_e2e_test_gguf(model2_name, arch="llama", context_length=8192)
    model2_sha256 = hashlib.sha256(model2_bytes).hexdigest()

    # Pre-seed model2 directly in the models directory to test switching
    model2_path = models_dir / model2_name
    model2_path.write_bytes(model2_bytes)

    # Initialize SQLite and clean tables
    await init_db()
    db = await get_db()
    try:
        await db.execute("DELETE FROM downloads;")
        await db.execute("DELETE FROM models;")
        await db.execute("DELETE FROM messages;")
        await db.execute("DELETE FROM conversations;")
        await db.commit()
    finally:
        await db.close()

    # Scan to index pre-seeded model2
    await model_library.scan_models_directory()

    # Mock HTTP transport for HF search & downloads
    def mock_hf_and_download_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        path = request.url.path
        headers = dict(request.headers)

        # 1. HF Search API
        if path == "/api/models":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                        "author": "Qwen",
                        "description": "State-of-the-art small language model in GGUF format.",
                        "downloads": 154200,
                        "likes": 890,
                        "lastModified": "2026-10-15T12:00:00.000Z",
                        "tags": ["gguf", "nlp", "text-generation"],
                    }
                ],
            )

        # 2. HF Model Details API
        if path == "/api/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF":
            return httpx.Response(
                200,
                json={
                    "id": "Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                    "author": "Qwen",
                    "downloads": 154200,
                    "likes": 890,
                    "lastModified": "2026-10-15T12:00:00.000Z",
                    "cardData": {"license": "apache-2.0"},
                    "siblings": [
                        {
                            "rfilename": model1_name,
                            "size": len(model1_bytes),
                            "lfs": {
                                "sha256": model1_sha256,
                                "size": len(model1_bytes),
                            },
                        }
                    ],
                },
            )

        # 3. HF Model Card Readme
        if path.endswith("/raw/main/README.md"):
            return httpx.Response(
                200,
                text="# Qwen2.5 0.5B Instruct GGUF\nThis is the quantized weights for Qwen 2.5 0.5B.",
            )

        # 4. HF Raw File Download Handler
        if model1_name in path or "resolve/main" in path:
            range_header = headers.get("range", "")
            if range_header.startswith("bytes="):
                parts = range_header[6:].split("-")
                start = int(parts[0]) if parts[0] else 0
                end = int(parts[1]) if parts[1] else len(model1_bytes) - 1
                chunk = model1_bytes[start : end + 1]
                return httpx.Response(
                    206,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{len(model1_bytes)}",
                        "Content-Length": str(len(chunk)),
                        "ETag": '"test-model1-etag"',
                    },
                    content=chunk,
                )
            return httpx.Response(
                200,
                headers={
                    "Content-Length": str(len(model1_bytes)),
                    "ETag": '"test-model1-etag"',
                },
                content=model1_bytes,
            )

        return httpx.Response(404, text="Not Found")

    mock_transport = httpx.MockTransport(mock_hf_and_download_handler)
    mock_hf_http = httpx.AsyncClient(transport=mock_transport, base_url="https://huggingface.co")
    mock_hf_client = HuggingFaceClient(custom_client=mock_hf_http)
    set_hf_client(mock_hf_client)

    # Initialize download manager with mock transport
    dm = DownloadManager(
        downloads_dir=downloads_dir,
        models_dir=models_dir,
        custom_client=httpx.AsyncClient(transport=mock_transport, base_url="https://huggingface.co"),
    )
    orig_dm = dm_module.download_manager
    dm_module.set_download_manager(dm)

    yield {
        "models_dir": models_dir,
        "downloads_dir": downloads_dir,
        "model1_name": model1_name,
        "model1_bytes": model1_bytes,
        "model1_sha256": model1_sha256,
        "model2_name": model2_name,
        "model2_sha256": model2_sha256,
        "dm": dm,
    }

    # Teardown
    settings.MODELS_DIR = orig_models_dir
    settings.DOWNLOADS_DIR = orig_downloads_dir
    dm_module.set_download_manager(orig_dm)
    set_hf_client(None)


@pytest.mark.asyncio
async def test_complete_m3_end_to_end_journey(async_client: AsyncClient, auth_headers, mock_engine: MockEngine, e2e_environment):
    """
    Executes an uninterrupted end-to-end integration scenario across all 5 M3 milestones:
    Search -> Download -> Checksum & Register -> Context Presets -> Load -> Chat -> Regenerate -> Switch -> Rollback -> Guarded Delete.
    """
    env = e2e_environment
    dm: DownloadManager = env["dm"]

    # ---------------------------------------------------------
    # STEP 1: Hugging Face Search & Quantization Inspection
    # ---------------------------------------------------------
    search_res = await async_client.get("/api/hf/search?query=qwen")
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert search_data["total"] >= 1
    assert search_data["results"][0]["repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct-GGUF"

    details_res = await async_client.get(f"/api/hf/models/{search_data['results'][0]['repo_id']}")
    assert details_res.status_code == 200
    details = details_res.json()
    assert details["repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
    assert len(details["available_gguf_files"]) >= 1
    target_file = details["available_gguf_files"][0]
    assert target_file["filename"] == env["model1_name"]
    assert target_file["quantization"] in ("Q4_K_M", "Q4_K", "UNKNOWN")

    # ---------------------------------------------------------
    # STEP 2: Enqueue Download & Verify SHA-256 + Auto-Indexing
    # ---------------------------------------------------------
    enqueue_res = await async_client.post(
        "/api/downloads",
        json={
            "repo_id": details["repo_id"],
            "filename": target_file["filename"],
            "revision": "main",
            "expected_sha256": env["model1_sha256"],
            "total_bytes": len(env["model1_bytes"]),
        },
        headers=auth_headers,
    )
    assert enqueue_res.status_code == 201
    task_info = enqueue_res.json()
    task_id = task_info["id"]

    # Start the download manager worker
    await dm.start()

    # Wait for completion (max 5 seconds)
    completed = False
    for _ in range(50):
        await asyncio.sleep(0.05)
        d_res = await async_client.get("/api/downloads")
        d_list = d_res.json()
        current = next((d for d in d_list if d["id"] == task_id), None)
        if current and current["status"] == "COMPLETED":
            completed = True
            assert current["integrity_status"] == "VERIFIED"
            assert current["sha256_actual"] == env["model1_sha256"]
            break

    await dm.stop()
    assert completed, "Download task failed to reach COMPLETED status within timeout"

    # Verify model is registered in the Local Model Library
    lib_res = await async_client.get("/api/models/library")
    assert lib_res.status_code == 200
    library = lib_res.json()
    assert len(library) >= 2  # model1 + pre-seeded model2

    m1_entry = next((m for m in library if m["filename"] == env["model1_name"]), None)
    assert m1_entry is not None
    assert m1_entry["architecture"] == "qwen2"
    assert m1_entry["download_origin"] == "huggingface"
    assert m1_entry["context_length"] == 4096
    m1_id = m1_entry["id"]

    # ---------------------------------------------------------
    # STEP 3: Context Recommendations & Memory Estimation
    # ---------------------------------------------------------
    ctx_res = await async_client.get(f"/api/models/{m1_id}/context_recommendation?kv_type=f16")
    assert ctx_res.status_code == 200
    ctx_data = ctx_res.json()
    assert ctx_data["architecture"] == "qwen2"
    assert "presets" in ctx_data
    assert "recommended_context" in ctx_data
    assert "2048" in ctx_data["presets"]
    rec_preset = ctx_data["presets"]["2048"]
    assert rec_preset["context_length"] == 2048
    assert "estimated_vram_mb" in rec_preset
    assert "risk_classification" in rec_preset
    assert "disclaimer" in ctx_data

    # ---------------------------------------------------------
    # STEP 4: Dynamic Model Load
    # ---------------------------------------------------------
    load_res = await async_client.post(
        "/api/models/load",
        json={
            "model_name": m1_id,
            "n_ctx": rec_preset["context_length"],
            "n_gpu_layers": 99,
            "flash_attention": True,
        },
        headers=auth_headers,
    )
    assert load_res.status_code == 200
    engine_status = load_res.json()
    assert engine_status["state"] == "READY"
    assert engine_status["context_size"] == rec_preset["context_length"]

    # Verify library reports active status
    lib_res2 = await async_client.get("/api/models/library")
    active_entry = next((m for m in lib_res2.json() if m["id"] == m1_id), None)
    assert active_entry is not None
    assert active_entry["is_active"] is True

    # ---------------------------------------------------------
    # STEP 5: Conversational Inference & Chat Streaming
    # ---------------------------------------------------------
    chat_payload = {
        "conversation_id": "e2e-conversation-1",
        "messages": [
            {"role": "user", "content": "Hello Mustang AI, give me an overview of your capabilities."}
        ],
        "temperature": 0.7,
        "max_tokens": 100,
    }

    chat_res = await async_client.post("/api/chat/completions", json=chat_payload)
    assert chat_res.status_code == 200
    assert "text/event-stream" in chat_res.headers["content-type"]

    # Accumulate streamed tokens
    streamed_text = ""
    async for line in chat_res.aiter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            chunk_data = json.loads(line[6:])
            streamed_text += chunk_data["choices"][0]["delta"].get("content", "")

    assert len(streamed_text) > 0

    # Verify persistence in SQLite
    db = await get_db()
    try:
        messages = await db.execute_fetchall(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            ("e2e-conversation-1",),
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == streamed_text
    finally:
        await db.close()

    # Test regeneration
    regen_res = await async_client.post(
        "/api/chat/regenerate",
        json={"conversation_id": "e2e-conversation-1"},
    )
    assert regen_res.status_code == 200
    regen_text = ""
    async for line in regen_res.aiter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            chunk_data = json.loads(line[6:])
            regen_text += chunk_data["choices"][0]["delta"].get("content", "")
    assert len(regen_text) > 0

    # ---------------------------------------------------------
    # STEP 6: Dynamic Context Preset Adjustment on Active Model
    # ---------------------------------------------------------
    reload_preset_res = await async_client.post(
        "/api/models/load",
        json={
            "model_name": m1_id,
            "n_ctx": 2048,
            "n_gpu_layers": 99,
        },
        headers=auth_headers,
    )
    assert reload_preset_res.status_code == 200
    assert reload_preset_res.json()["context_size"] == 2048

    # ---------------------------------------------------------
    # STEP 7: Safe Model Switch to Model 2
    # ---------------------------------------------------------
    m2_entry = next((m for m in library if m["filename"] == env["model2_name"]), None)
    assert m2_entry is not None
    m2_id = m2_entry["id"]

    switch_res = await async_client.post(
        "/api/models/load",
        json={
            "model_name": m2_id,
            "n_ctx": 4096,
            "n_gpu_layers": 99,
        },
        headers=auth_headers,
    )
    assert switch_res.status_code == 200
    m2_status = switch_res.json()
    assert m2_status["state"] == "READY"
    assert m2_status["model_name"] == env["model2_name"]

    # Verify model2 is active and model1 is inactive
    lib_res3 = await async_client.get("/api/models/library")
    m1_state = next(m for m in lib_res3.json() if m["id"] == m1_id)
    m2_state = next(m for m in lib_res3.json() if m["id"] == m2_id)
    assert m1_state["is_active"] is False
    assert m2_state["is_active"] is True

    # ---------------------------------------------------------
    # STEP 8: Switch Failure & Safe Rollback Protection
    # ---------------------------------------------------------
    # Inject simulated failure hook on mock engine
    mock_engine.fail_on_model = "failing_model.gguf"
    fake_path = env["models_dir"] / "failing_model.gguf"
    fake_path.write_bytes(create_e2e_test_gguf("failing_model.gguf"))
    await model_library.scan_models_directory()

    failed_switch_res = await async_client.post(
        "/api/models/load",
        json={
            "model_name": "failing_model.gguf",
            "n_ctx": 2048,
        },
        headers=auth_headers,
    )
    assert failed_switch_res.status_code == 400
    assert "rolled back" in failed_switch_res.json()["detail"].lower()

    # Verify the active model is still model 2
    cur_status = await async_client.get("/api/models/status")
    assert cur_status.json()["model_name"] == env["model2_name"]
    assert cur_status.json()["state"] == "READY"

    # Reset failure hook
    mock_engine.fail_on_model = None

    # ---------------------------------------------------------
    # STEP 9: Active Model Deletion Guard & Clean Unload / Delete
    # ---------------------------------------------------------
    # Attempting to delete active model2 must be refused
    del_active_res = await async_client.delete(f"/api/models/{m2_id}", headers=auth_headers)
    assert del_active_res.status_code == 400
    assert "cannot delete actively loaded model" in del_active_res.json()["detail"].lower()

    # Deleting inactive model1 is allowed
    del_inactive_res = await async_client.delete(f"/api/models/{m1_id}", headers=auth_headers)
    assert del_inactive_res.status_code == 200
    assert not (env["models_dir"] / env["model1_name"]).exists()

    # Unload model2
    unload_res = await async_client.post("/api/models/unload", headers=auth_headers)
    assert unload_res.status_code == 200
    assert unload_res.json()["state"] == "UNLOADED"

    # Now deleting model2 is allowed
    del_m2_res = await async_client.delete(f"/api/models/{m2_id}", headers=auth_headers)
    assert del_m2_res.status_code == 200
    assert not (env["models_dir"] / env["model2_name"]).exists()


@pytest.mark.asyncio
async def test_download_sse_subscription_integration(async_client: AsyncClient, auth_headers, e2e_environment):
    """
    Verifies that client subscription to /api/downloads/progress receives real-time SSE events
    when download tasks transition between states.
    """
    env = e2e_environment
    dm: DownloadManager = env["dm"]

    # Subscribe to SSE queue directly through DM to verify event broadcasting pipeline
    queue = dm.subscribe_sse()
    try:
        # Enqueue download
        task = await dm.create_download(
            repo_id="Qwen/Qwen2.5-0.5B-Instruct-GGUF",
            filename="qwen2.5-0.5b-instruct-q4_k_m.gguf",
            expected_sha256=env["model1_sha256"],
            total_bytes=len(env["model1_bytes"]),
        )

        # Receive queued broadcast
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["id"] == task["id"]
        assert event["status"] == "QUEUED"
        assert event["filename"] == "qwen2.5-0.5b-instruct-q4_k_m.gguf"

        # Pause task
        await dm.pause_download(task["id"])
        pause_event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert pause_event["id"] == task["id"]
        assert pause_event["status"] == "PAUSED"

        # Resume task
        await dm.resume_download(task["id"])
        resume_event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert resume_event["id"] == task["id"]
        assert resume_event["status"] == "QUEUED"

        # Cancel task
        await dm.cancel_download(task["id"])
        cancel_event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert cancel_event["id"] == task["id"]
        assert cancel_event["status"] == "CANCELLED"
    finally:
        dm.unsubscribe_sse(queue)

