"""
Stage 1 Tests: GGUF Metadata Parser and Database Migrations
"""

import pytest
import aiosqlite
from pathlib import Path
from app.core.gguf_parser import parse_gguf_metadata, clean_quantization_name
from app.db.database import get_db, init_db, DB_PATH
from app.config import settings

TEST_MODEL_PATH = settings.MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def test_gguf_parser_on_real_model():
    """Validates exact metadata extraction on actual Qwen2.5 0.5B GGUF file."""
    assert TEST_MODEL_PATH.exists(), f"Test model missing: {TEST_MODEL_PATH}"

    meta = parse_gguf_metadata(TEST_MODEL_PATH)
    assert meta.architecture == "qwen2"
    assert meta.block_count == 24
    assert meta.total_layers == 25
    assert meta.context_length == 32768
    assert meta.embedding_dim == 896
    assert meta.head_count == 14
    assert meta.head_count_kv == 2
    assert meta.attention_type == "GQA"
    assert meta.quantization == "Q4_K_M"
    assert meta.file_type_id == 15
    assert meta.tensor_count == 291
    assert meta.file_size_bytes == 491400032
    assert meta.parameter_count is not None
    assert "0.6" in meta.parameter_count or "0.5" in meta.parameter_count or "630M" in meta.parameter_count


def test_gguf_parser_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        parse_gguf_metadata(settings.MODELS_DIR / "non_existent_model.gguf")


def test_gguf_parser_invalid_magic_bytes(tmp_path: Path):
    corrupt_file = tmp_path / "corrupt.gguf"
    corrupt_file.write_bytes(b"NOT_GGUF_MAGIC_HEADER_BYTES_123456789")
    with pytest.raises(ValueError, match="expected magic b'GGUF'"):
        parse_gguf_metadata(corrupt_file)


def test_quantization_mapping_fallback():
    assert clean_quantization_name(15) == "Q4_K_M"
    assert clean_quantization_name(7) == "Q8_0"
    assert clean_quantization_name(2) == "Q4_0"
    assert clean_quantization_name(999, "my-model-q5_k_m.gguf") == "Q5_K_M"


@pytest.mark.asyncio
async def test_database_migrations_and_restart_recovery():
    """Validates that models and downloads tables exist and restart recovery transitions stale tasks."""
    await init_db()

    db = await get_db()
    try:
        # 1. Verify models table exists and has expected columns
        m_cols = await db.execute_fetchall("PRAGMA table_info(models);")
        col_names = [c["name"] for c in m_cols]
        assert "id" in col_names
        assert "canonical_path" in col_names
        assert "architecture" in col_names
        assert "head_count_kv" in col_names

        # 2. Verify downloads table exists and has expected columns
        d_cols = await db.execute_fetchall("PRAGMA table_info(downloads);")
        d_col_names = [c["name"] for c in d_cols]
        assert "id" in d_col_names
        assert "status" in d_col_names
        assert "etag" in d_col_names

        # 3. Test restart recovery: insert a task in DOWNLOADING state
        test_task_id = "test-task-recovery"
        await db.execute(
            """
            INSERT OR REPLACE INTO downloads (id, repo_id, filename, canonical_url, destination_path, total_bytes, downloaded_bytes, status, created_at, updated_at)
            VALUES (?, 'test/repo', 'test.gguf', 'https://hf.co/test', 'S:/test.part', 1000, 500, 'DOWNLOADING', 1.0, 1.0)
            """,
            (test_task_id,),
        )
        await db.commit()

        # Simulate app restart by running init_db()
        await init_db()

        # Check that test_task_id was transitioned to PAUSED
        row = await db.execute_fetchall("SELECT status, error_message FROM downloads WHERE id = ?", (test_task_id,))
        assert row[0]["status"] == "PAUSED"
        assert "restart" in row[0]["error_message"].lower()

        # Clean up test task
        await db.execute("DELETE FROM downloads WHERE id = ?", (test_task_id,))
        await db.commit()

    finally:
        await db.close()
