"""
Mustang AI Model Registry & Lifecycle Router
Handles GGUF discovery, model library management, advisory VRAM estimations,
and dynamic model load/unload.
"""

import asyncio
from pathlib import Path
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core import hardware, security
from app.core.engine_base import EngineConfig, EngineState, EngineStatus, InferenceEngine
from app.core.memory_estimator import MemoryEstimateResult, estimate_model_memory
from app.core import model_library

router = APIRouter(prefix="/api/models", tags=["models"])

# Engine singleton dependency injected by main.py
_ENGINE_INSTANCE: InferenceEngine | None = None


def set_engine_instance(engine: InferenceEngine):
    global _ENGINE_INSTANCE
    _ENGINE_INSTANCE = engine


def get_engine() -> InferenceEngine:
    if _ENGINE_INSTANCE is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Inference engine supervisor is not initialized.",
        )
    return _ENGINE_INSTANCE


class ModelInfo(BaseModel):
    name: str
    filename: str
    path: str
    size_mb: float
    size_gb: float
    modified_time: float


class ModelLibraryItem(BaseModel):
    id: str
    canonical_path: str
    filename: str
    repo_id: str
    file_size_bytes: int
    file_size_mb: float
    file_size_gb: float
    architecture: str
    quantization: str
    parameter_count: str | None = None
    context_length: int
    embedding_dim: int
    block_count: int
    total_layers: int
    head_count: int
    head_count_kv: int
    download_origin: str
    last_scanned_at: float
    created_at: float
    is_active: bool = False


class ModelLoadRequest(BaseModel):
    model_name: str
    n_gpu_layers: int = Field(default=99, ge=0, le=256)
    n_ctx: int = Field(default=4096, ge=512, le=65536)
    n_batch: int = Field(default=512, ge=128, le=4096)
    flash_attention: bool = True


# --- Model Library Endpoints (Milestone 3) ---

@router.get("/library", response_model=list[ModelLibraryItem])
async def list_model_library(engine: InferenceEngine = Depends(get_engine)):
    """
    Returns all discovered and indexed GGUF models in the local library with rich metadata.
    Marks the currently active/loaded model.
    """
    # Trigger lightweight scan if library is empty
    models = await model_library.get_all_models()
    if not models:
        await model_library.scan_models_directory()
        models = await model_library.get_all_models()

    engine_status = await engine.get_status()
    is_running = engine_status.state in (EngineState.READY, EngineState.GENERATING)
    active_name = engine_status.model_name or ""
    active_path = engine_status.model_path or ""

    items: list[ModelLibraryItem] = []
    for m in models:
        size_bytes = m["file_size_bytes"]
        is_active = is_running and (
            m["filename"] == active_name
            or m["canonical_path"] == active_path
            or Path(m["canonical_path"]).name == active_name
        )
        block_cnt = m.get("block_count", 0)
        items.append(
            ModelLibraryItem(
                id=m["id"],
                canonical_path=m["canonical_path"],
                filename=m["filename"],
                repo_id=m["repo_id"],
                file_size_bytes=size_bytes,
                file_size_mb=round(size_bytes / (1024 * 1024), 2),
                file_size_gb=round(size_bytes / (1024 * 1024 * 1024), 2),
                architecture=m["architecture"],
                quantization=m["quantization"],
                parameter_count=m["parameter_count"],
                context_length=m["context_length"],
                embedding_dim=m["embedding_dim"],
                block_count=block_cnt,
                total_layers=block_cnt + 1 if block_cnt > 0 else 0,
                head_count=m["head_count"],
                head_count_kv=m["head_count_kv"],
                download_origin=m["download_origin"],
                last_scanned_at=m["last_scanned_at"],
                created_at=m["created_at"],
                is_active=is_active,
            )
        )

    return items


@router.post("/scan")
async def scan_models():
    """
    Triggers an explicit recursive scan of MODELS_DIR, reuses cached metadata for
    unmodified files, extracts metadata for new files, and prunes deleted records.
    """
    return await model_library.scan_models_directory()


@router.get("/status", response_model=EngineStatus)
async def get_engine_status(engine: InferenceEngine = Depends(get_engine)):
    """Returns authoritative runtime state, VRAM usage, and active model status."""
    return await engine.get_status()


@router.get("/{model_id}/context_recommendation")
async def get_model_context_recommendation(
    model_id: str,
    kv_type: str = Query(default="f16", description="KV cache precision: f16, q8_0, q4_0"),
    kv_type_k: str | None = Query(default=None, description="Key cache precision: f16, q8_0, q4_0"),
    kv_type_v: str | None = Query(default=None, description="Value cache precision: f16, q8_0, q4_0"),
    flash_attention: bool = Query(default=True, description="FlashAttention enabled/disabled"),
    engine: InferenceEngine = Depends(get_engine),
):
    """
    Returns dynamic context allocation tiers (LOW_RISK, RECOMMENDED, HIGH_MEMORY, etc.)
    derived from GQA metadata and live VRAM headroom.
    """
    model = await model_library.get_model_by_id(model_id)
    if not model:
        # Check by filename fallback
        all_m = await model_library.get_all_models()
        model = next((m for m in all_m if m["filename"] == model_id), None)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{model_id}' not found in library.",
            )

    # Blocking work (nvidia-smi, GGUF parsing) runs off the event loop so chat streaming
    # and other requests are not stalled while it completes.
    hw = await asyncio.to_thread(hardware.get_system_telemetry)
    vram_free = hw.gpu.vram_free_mb if hw.gpu.available else 2000.0
    vram_used = hw.gpu.vram_used_mb if hw.gpu.available else None
    ram_avail = hw.memory.ram_available_mb

    result = await asyncio.to_thread(
        model_library.calculate_context_recommendations,
        model=model,
        available_vram_mb=vram_free,
        available_ram_mb=ram_avail,
        kv_type=kv_type,
        kv_type_k=kv_type_k or kv_type,
        kv_type_v=kv_type_v or kv_type,
        flash_attention=flash_attention,
    )

    # Cross-check against authoritative runtime engine measurements if model is actively running
    engine_status = await engine.get_status()
    is_active = (
        engine_status.state in (EngineState.READY, EngineState.GENERATING)
        and (
            engine_status.model_name in (model["filename"], model["id"])
            or engine_status.model_path == model.get("canonical_path")
        )
    )
    measured_model_vram = engine_status.vram_allocated_mb if is_active else None

    result["measured_model_vram_mb"] = measured_model_vram
    result["measured_total_gpu_vram_mb"] = vram_used

    if "presets" in result:
        for p in result["presets"].values():
            p["measured_model_vram_mb"] = measured_model_vram
            p["measured_total_gpu_vram_mb"] = vram_used

    return result


@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    engine: InferenceEngine = Depends(get_engine),
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Safely deletes a model from disk and prunes it from the database.
    Requires X-Mustang-Token authentication.
    Refuses deletion if the model is currently actively loaded.
    """
    model = await model_library.get_model_by_id(model_id)
    if not model:
        # Check by filename fallback
        all_m = await model_library.get_all_models()
        model = next((m for m in all_m if m["filename"] == model_id), None)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{model_id}' not found in library.",
            )

    engine_status = await engine.get_status()
    is_active = engine_status.state in (EngineState.READY, EngineState.GENERATING) and (
        model["filename"] == engine_status.model_name
        or model["canonical_path"] == engine_status.model_path
        or Path(model["canonical_path"]).name == engine_status.model_name
    )

    return await model_library.delete_model_file(model["id"], is_active=is_active)


@router.get("/{model_id}")
async def get_model_detail(model_id: str):
    """Retrieves complete metadata for a specific model by model ID or filename."""
    model = await model_library.get_model_by_id(model_id)
    if not model:
        all_m = await model_library.get_all_models()
        model = next((m for m in all_m if m["filename"] == model_id), None)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{model_id}' not found in library.",
            )
    return model


# --- Backwards Compatible Endpoints (Milestones 1 & 2) ---

def _listed_model_files() -> list[Path]:
    """
    The GGUF files the model menu shows: the models folder and its subfolders (downloads are
    stored per repository), except hidden folders such as .downloads. A file name is listed
    once; when two subfolders hold the same name, the first in sorted order wins.
    """
    if not settings.MODELS_DIR.exists():
        return []
    files: list[Path] = []
    seen_names: set[str] = set()
    for file_path in sorted(settings.MODELS_DIR.rglob("*.gguf")):
        folders = file_path.relative_to(settings.MODELS_DIR).parts[:-1]
        if any(part.startswith(".") for part in folders) or file_path.name in seen_names:
            continue
        seen_names.add(file_path.name)
        if file_path.is_file():
            files.append(file_path)
    return files


@router.get("", response_model=list[ModelInfo])
async def list_models():
    """Scans the designated models directory and returns discovered GGUF files."""
    models: list[ModelInfo] = []
    for file_path in _listed_model_files():
        stat = file_path.stat()
        size_mb = round(stat.st_size / (1024 * 1024), 2)
        size_gb = round(stat.st_size / (1024 * 1024 * 1024), 2)
        models.append(
            ModelInfo(
                name=file_path.stem,
                filename=file_path.name,
                path=str(file_path),
                size_mb=size_mb,
                size_gb=size_gb,
                modified_time=stat.st_mtime,
            )
        )

    return sorted(models, key=lambda m: m.name)


@router.get("/{filename}/estimate", response_model=MemoryEstimateResult)
async def get_model_memory_estimate(
    filename: str,
    n_gpu_layers: Annotated[int, Query(ge=0, le=256)] = 99,
    n_ctx: Annotated[int, Query(ge=512, le=65536)] = 4096,
    kv_type: Annotated[str, Query()] = "f16",
):
    """
    Returns pre-flight advisory VRAM estimate based on GGUF tensor metadata.
    Advisory only; runtime allocation by llama.cpp remains authoritative.
    """
    resolved_path = await resolve_model_reference(filename)
    # GGUF parsing takes seconds on real models: keep it off the event loop.
    return await asyncio.to_thread(
        estimate_model_memory,
        gguf_path=resolved_path,
        requested_gpu_layers=n_gpu_layers,
        target_ctx=n_ctx,
        kv_type=kv_type,
    )


async def resolve_model_reference(reference: str) -> Path:
    """
    Resolve a model reference from the UI: a library model id, a file name at the top of
    the models folder, or the bare file name of a library model stored in a subfolder
    (downloads are saved per repository, e.g. "Org--Repo/model.gguf").
    """
    model = await model_library.get_model_by_id(reference)
    if model:
        return Path(model["canonical_path"])
    bare_name = "/" not in reference and "\\" not in reference
    if bare_name and not (settings.MODELS_DIR / reference).exists():
        matches = [m for m in await model_library.get_all_models() if m["filename"] == reference]
        if len(matches) == 1:
            return security.validate_model_path(matches[0]["canonical_path"])
        # A file copied into a subfolder by hand is listed but not in the library: load the
        # same file the menu shows for that name.
        listed = [f for f in _listed_model_files() if f.name == reference]
        if listed:
            return security.validate_model_path(str(listed[0]))
    return security.validate_model_path(reference)


@router.post("/load", response_model=EngineStatus)
async def load_model(
    req: ModelLoadRequest,
    engine: InferenceEngine = Depends(get_engine),
    _auth: bool = Depends(security.verify_session_token),
):
    """
    Loads a model into the inference engine.
    Supports either stable model_id or filename.
    Authenticated with X-Mustang-Token.
    """
    resolved_path = await resolve_model_reference(req.model_name)

    config = EngineConfig(
        model_path=resolved_path,
        n_gpu_layers=req.n_gpu_layers,
        n_ctx=req.n_ctx,
        n_batch=req.n_batch,
        flash_attention=req.flash_attention,
        port=settings.ENGINE_PORT,
        host=settings.ENGINE_HOST,
    )

    success = await engine.start(config)
    status_obj = await engine.get_status()
    if not success:
        # If engine recovered to READY via rollback, return 400 with rollback details
        if status_obj.state == EngineState.READY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=status_obj.error_message or "Model switch failed. Previous model was safely restored.",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=status_obj.error_message or "Failed to start inference runtime.",
        )

    return status_obj


@router.post("/unload", response_model=EngineStatus)
async def unload_model(
    engine: InferenceEngine = Depends(get_engine),
    _auth: bool = Depends(security.verify_session_token),
):
    """Safely terminates the inference engine and releases VRAM/resources."""
    await engine.stop()
    return await engine.get_status()
