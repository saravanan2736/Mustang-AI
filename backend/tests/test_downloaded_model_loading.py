"""
Downloaded models live in per-repository subfolders (e.g. models/Org--Repo/model.gguf), while
the UI's Library and Download Queue "Load" buttons send the bare file name. Found in M4 Phase G:
such a load returned 404 and the model drop-down did not list downloaded models.
"""

from pathlib import Path

import gguf
import pytest
from httpx import AsyncClient

from app.config import settings
from app.core import model_library


def create_tiny_gguf(path: Path) -> Path:
    """A minimal valid GGUF file (header and metadata only)."""
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


@pytest.fixture
def models_dir(tmp_path: Path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setattr(settings, "MODELS_DIR", d)
    return d


@pytest.mark.asyncio
async def test_model_in_repo_subfolder_loads_by_bare_file_name(async_client: AsyncClient, mock_engine, models_dir):
    create_tiny_gguf(models_dir / "Org--Repo-GGUF" / "downloaded-q4.gguf")
    await model_library.scan_models_directory(models_dir=models_dir)

    res = await async_client.post("/api/models/load", json={"model_name": "downloaded-q4.gguf"})
    assert res.status_code == 200, res.text
    assert res.json()["model_path"].endswith(str(Path("Org--Repo-GGUF") / "downloaded-q4.gguf"))

    est = await async_client.get("/api/models/downloaded-q4.gguf/estimate")
    assert est.status_code == 200, est.text


@pytest.mark.asyncio
async def test_model_list_includes_subfolders_but_not_partial_downloads(async_client: AsyncClient, models_dir):
    create_tiny_gguf(models_dir / "top.gguf")
    create_tiny_gguf(models_dir / "Org--Repo-GGUF" / "nested.gguf")
    create_tiny_gguf(models_dir / ".downloads" / "partial.gguf")
    names = {m["filename"] for m in (await async_client.get("/api/models")).json()}
    assert names == {"top.gguf", "nested.gguf"}


@pytest.mark.asyncio
async def test_unknown_bare_name_still_404(async_client: AsyncClient, models_dir):
    res = await async_client.post("/api/models/load", json={"model_name": "missing.gguf"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_traversal_still_refused(async_client: AsyncClient, models_dir):
    res = await async_client.post("/api/models/load", json={"model_name": "../../Windows/win.ini"})
    assert res.status_code in (400, 404)


@pytest.mark.asyncio
async def test_listed_model_loads_even_when_not_in_the_library(async_client: AsyncClient, mock_engine, models_dir):
    # Found in M5 Phase B: a model copied into a subfolder by hand (never scanned into the
    # library) appeared in the drop-down, but loading it returned 404.
    create_tiny_gguf(models_dir / "my-models" / "hand-copied.gguf")
    assert "hand-copied.gguf" in {m["filename"] for m in (await async_client.get("/api/models")).json()}

    res = await async_client.post("/api/models/load", json={"model_name": "hand-copied.gguf"})
    assert res.status_code == 200, res.text
    assert res.json()["model_path"].endswith(str(Path("my-models") / "hand-copied.gguf"))


@pytest.mark.asyncio
async def test_hidden_folders_stay_unloadable_by_bare_name(async_client: AsyncClient, mock_engine, models_dir):
    create_tiny_gguf(models_dir / ".downloads" / "partial-only.gguf")
    res = await async_client.post("/api/models/load", json={"model_name": "partial-only.gguf"})
    assert res.status_code == 404
