"""
Stage 2 Tests: Local Model Library, Discovery, Identifiers, Metadata Caching, Context Recommendations, and Deletion Guards
"""

import gc
import os
import time
from pathlib import Path
import pytest
import gguf
from httpx import AsyncClient

from app.core import model_library
from app.core.engine_base import EngineConfig
from app.db.database import get_db, init_db
from app.config import settings

REAL_MODEL_PATH = settings.MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def create_tiny_gguf(path: Path, arch: str = "llama", blocks: int = 12, ctx: int = 2048) -> Path:
    """Helper to generate a lightweight, valid GGUF binary fixture in milliseconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w = gguf.GGUFWriter(path, arch)
    w.add_block_count(blocks)
    w.add_context_length(ctx)
    w.add_embedding_length(768)
    w.add_head_count(12)
    w.add_head_count_kv(12)
    w.add_file_type(2)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    return path


@pytest.fixture
def test_models_sandbox(tmp_path: Path):
    """Creates an isolated temporary models directory for discovery and collision tests."""
    sandbox_dir = tmp_path / "models_sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    return sandbox_dir


@pytest.mark.asyncio
async def test_scan_and_cache_real_model(async_client: AsyncClient):
    """Verifies that scanning the real models directory indexes Qwen2.5 0.5B with accurate metadata."""
    res = await async_client.post("/api/models/scan")
    assert res.status_code == 200
    scan_info = res.json()
    assert scan_info["total"] >= 1

    lib_res = await async_client.get("/api/models/library")
    assert lib_res.status_code == 200
    models = lib_res.json()
    assert len(models) >= 1

    qwen = next((m for m in models if m["filename"] == "qwen2.5-0.5b-instruct-q4_k_m.gguf"), None)
    assert qwen is not None
    assert qwen["architecture"] == "qwen2"
    assert qwen["quantization"] == "Q4_K_M"
    assert qwen["block_count"] == 24
    assert qwen["total_layers"] == 25
    assert qwen["context_length"] == 32768
    assert qwen["head_count_kv"] == 2
    assert qwen["id"].startswith("mod-")

    # Second scan: should hit metadata cache (0 added, 0 updated)
    res2 = await async_client.post("/api/models/scan")
    scan_info2 = res2.json()
    assert scan_info2["added"] == 0
    assert scan_info2["updated"] == 0


@pytest.mark.asyncio
async def test_identical_filenames_in_separate_subdirectories(test_models_sandbox: Path):
    """
    Verifies that identical filenames in different subdirectories are assigned
    different stable model IDs and co-exist in SQLite without collision.
    """
    sub_a = test_models_sandbox / "PublisherA--ModelGGUF"
    sub_b = test_models_sandbox / "PublisherB--ModelGGUF"
    file_a = sub_a / "model.gguf"
    file_b = sub_b / "model.gguf"

    create_tiny_gguf(file_a, "llama", 12)
    create_tiny_gguf(file_b, "qwen2", 24)

    result = await model_library.scan_models_directory(models_dir=test_models_sandbox)
    assert result["scanned"] == 2
    assert result["added"] >= 2

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, filename, canonical_path, repo_id, architecture FROM models WHERE filename = 'model.gguf';"
        )
        assert len(rows) == 2
        id1, id2 = rows[0]["id"], rows[1]["id"]
        assert id1 != id2
        assert id1.startswith("mod-")
        assert id2.startswith("mod-")
        assert rows[0]["repo_id"] != rows[1]["repo_id"]
        assert set(r["architecture"] for r in rows) == {"llama", "qwen2"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_gguf_files_skipped(test_models_sandbox: Path):
    """Verifies that non-GGUF or corrupted files are skipped without halting scan."""
    corrupt_file = test_models_sandbox / "corrupt.gguf"
    corrupt_file.write_bytes(b"INVALID_HEADER_DATA_12345")

    txt_file = test_models_sandbox / "notes.txt"
    txt_file.write_text("Hello", encoding="utf-8")

    result = await model_library.scan_models_directory(models_dir=test_models_sandbox)
    assert result["scanned"] == 0


@pytest.mark.asyncio
async def test_detection_of_deleted_and_moved_files(test_models_sandbox: Path):
    """Verifies that deleting a file on disk causes the next scan to prune it from SQLite."""
    temp_file = test_models_sandbox / "temp_tiny.gguf"
    create_tiny_gguf(temp_file, "phi3", 16)

    await model_library.scan_models_directory(models_dir=test_models_sandbox)
    model = await model_library.get_model_by_id(
        model_library.generate_stable_model_id(temp_file.resolve(), test_models_sandbox.resolve())
    )
    assert model is not None
    assert model["architecture"] == "phi3"

    # Delete file from disk (force gc collect so mmap is released on Windows)
    gc.collect()
    temp_file.unlink()

    # Rescan should prune the record
    res2 = await model_library.scan_models_directory(models_dir=test_models_sandbox)
    assert res2["pruned"] >= 1

    pruned = await model_library.get_model_by_id(model["id"])
    assert pruned is None


@pytest.mark.asyncio
async def test_active_model_deletion_protection(async_client: AsyncClient, mock_engine, auth_headers):
    """Verifies that attempting to delete an actively loaded model returns HTTP 400."""
    await async_client.post("/api/models/scan")
    lib = (await async_client.get("/api/models/library")).json()
    qwen = next((m for m in lib if "qwen" in m["filename"].lower()), None)
    assert qwen is not None

    # Start mock engine with this model
    config = EngineConfig(model_path=Path(qwen["canonical_path"]))
    await mock_engine.start(config)

    # Attempt delete while engine is READY
    del_resp = await async_client.delete(f"/api/models/{qwen['id']}", headers=auth_headers)
    assert del_resp.status_code == 400
    assert "actively loaded" in del_resp.json()["detail"].lower()

    # Stop engine and verify delete without auth fails with 401
    await mock_engine.stop()
    unauth_del = await async_client.delete(f"/api/models/{qwen['id']}")
    assert unauth_del.status_code == 401


@pytest.mark.asyncio
async def test_path_traversal_safety(async_client: AsyncClient, auth_headers):
    """Verifies that attempts to target files outside MODELS_DIR are blocked."""
    bad_id = "mod-../../windows/system32/cmd.exe"
    res = await async_client.delete(f"/api/models/{bad_id}", headers=auth_headers)
    assert res.status_code in (400, 404)


@pytest.mark.asyncio
async def test_context_recommendation_safety(async_client: AsyncClient):
    """Verifies advisory context estimation categorizes tiers without 'guaranteed fit' claims."""
    await async_client.post("/api/models/scan")
    lib = (await async_client.get("/api/models/library")).json()
    qwen = next((m for m in lib if "qwen" in m["filename"].lower()), None)
    assert qwen is not None

    rec_res = await async_client.get(f"/api/models/{qwen['id']}/context_recommendation")
    assert rec_res.status_code == 200
    rec = rec_res.json()
    assert rec["estimate_status"] == "AVAILABLE"
    assert rec["architecture"] == "qwen2"
    assert "2048" in rec["recommendations"]
    assert "4096" in rec["recommendations"]

    rec_4k = rec["recommendations"]["4096"]
    assert rec_4k["risk_classification"] in ("LOW_RISK", "RECOMMENDED", "HIGH_MEMORY")
    assert rec_4k["kv_cache_mb"] > 0
    assert "guaranteed" not in str(rec).lower()


@pytest.mark.asyncio
async def test_unsupported_architecture_handled_as_unavailable():
    """Verifies that an unsupported model architecture returns UNAVAILABLE without fabricating estimates."""
    fake_model = {
        "id": "mod-unsupported",
        "architecture": "custom_neural_network_unknown",
        "block_count": 0,
        "embedding_dim": 0,
        "head_count": 0,
        "head_count_kv": 0,
        "context_length": 2048,
        "file_size_bytes": 1000000,
    }
    result = model_library.calculate_context_recommendations(
        model=fake_model,
        available_vram_mb=4000.0,
        available_ram_mb=16000.0,
    )
    assert result["estimate_status"] == "UNAVAILABLE"
    assert "missing critical attention parameters" in result["reason"]
