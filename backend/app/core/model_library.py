"""
Mustang AI Local Model Library Core
Discovers, canonicalizes, parses, caches, and synchronizes GGUF model files in SQLite.
"""

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any
from fastapi import HTTPException, status
import aiosqlite

from app.config import settings
from app.core.gguf_parser import (
    parse_gguf_metadata,
    resolve_head_dimensions,
    is_architecture_verified_symmetric,
)
from app.db.database import get_db

logger = logging.getLogger("mustang.model_library")


def generate_stable_model_id(canonical_path: Path, models_dir: Path) -> str:
    """
    Generates a deterministic, stable model ID based on the relative path within MODELS_DIR.
    Ensures identical filenames in different subdirectories never collide.
    """
    try:
        rel_path = canonical_path.relative_to(models_dir).as_posix()
    except ValueError:
        rel_path = canonical_path.name
    h = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:16]
    return f"mod-{h}"


def validate_canonical_path_safety(target_path: Path, models_dir: Path) -> Path:
    """
    Resolves path and enforces that it resides strictly within models_dir.
    Rejects null bytes, path traversal, and external symlinks/junctions.
    """
    path_str = str(target_path)
    if "\x00" in path_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Null bytes are strictly forbidden in path targets.",
        )

    models_dir_resolved = models_dir.resolve(strict=True)

    try:
        resolved = target_path.resolve(strict=True)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target model file not found on disk: {target_path.name}",
        )

    # Check symlink destination if file is a symlink
    if target_path.is_symlink():
        symlink_target = Path(os.readlink(target_path))
        if not symlink_target.is_absolute():
            symlink_target = (target_path.parent / symlink_target).resolve()
        else:
            symlink_target = symlink_target.resolve()
        try:
            symlink_target.relative_to(models_dir_resolved)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Security violation: Symlink points outside the allowed models directory.",
            )

    # Enforce confinement inside models_dir
    try:
        resolved.relative_to(models_dir_resolved)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security violation: Path escapes the configured models directory.",
        )

    if resolved.suffix.lower() != ".gguf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{resolved.suffix}'. Only .gguf files are allowed.",
        )

    return resolved


async def scan_models_directory(models_dir: Path | None = None) -> dict[str, int]:
    """
    Recursively scans models directory, synchronizes models with SQLite,
    reuses cached metadata for unchanged files, and prunes deleted files.
    """
    base_dir = models_dir or settings.MODELS_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    models_dir_resolved = base_dir.resolve()

    db = await get_db()
    try:
        # Load existing indexed models into memory
        existing_rows = await db.execute_fetchall("SELECT * FROM models;")
        existing_by_path = {r["canonical_path"]: dict(r) for r in existing_rows}

        discovered_paths: set[str] = set()
        added_count = 0
        updated_count = 0
        now = time.time()

        # Recursively discover all .gguf files
        for root, dirs, files in os.walk(str(models_dir_resolved)):
            # Skip hidden directories like .downloads
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for file in files:
                if not file.lower().endswith(".gguf"):
                    continue

                full_path = Path(root) / file
                try:
                    resolved = validate_canonical_path_safety(full_path, models_dir_resolved)
                except Exception as sec_err:
                    logger.warning(f"Skipping unsafe or invalid file '{full_path}': {sec_err}")
                    continue

                canonical_str = str(resolved)

                try:
                    stat = resolved.stat()
                except OSError:
                    continue

                file_size = stat.st_size
                mtime = stat.st_mtime

                # Check if file exists in cache and is unmodified
                cached = existing_by_path.get(canonical_str)
                if cached and cached["file_size_bytes"] == file_size:
                    # Cache hit: unchanged file, update last_scanned_at
                    discovered_paths.add(canonical_str)
                    await db.execute(
                        "UPDATE models SET last_scanned_at = ? WHERE id = ?",
                        (now, cached["id"]),
                    )
                    continue

                # Cache miss or file modified: parse GGUF binary metadata
                try:
                    meta = parse_gguf_metadata(resolved)
                except Exception as parse_err:
                    logger.warning(f"Could not parse GGUF metadata for '{resolved.name}': {parse_err}")
                    continue

                discovered_paths.add(canonical_str)

                model_id = generate_stable_model_id(resolved, models_dir_resolved)

                # Determine repo_id provenance from directory structure
                try:
                    rel_parent = resolved.parent.relative_to(models_dir_resolved)
                    if str(rel_parent) != ".":
                        # Subdirectory named e.g. "Qwen--Qwen2.5-0.5B-GGUF"
                        repo_id = str(rel_parent).replace("--", "/")
                        download_origin = "huggingface"
                    else:
                        repo_id = "local_import"
                        download_origin = "local_import"
                except Exception:
                    repo_id = "local_import"
                    download_origin = "local_import"

                if cached:
                    # Update existing record
                    await db.execute(
                        """
                        UPDATE models SET
                            filename = ?, repo_id = ?, file_size_bytes = ?,
                            architecture = ?, quantization = ?, parameter_count = ?,
                            context_length = ?, embedding_dim = ?, block_count = ?,
                            head_count = ?, head_count_kv = ?, download_origin = ?,
                            last_scanned_at = ?
                        WHERE id = ?
                        """,
                        (
                            resolved.name, repo_id, file_size,
                            meta.architecture, meta.quantization, meta.parameter_count,
                            meta.context_length, meta.embedding_dim, meta.block_count,
                            meta.head_count, meta.head_count_kv, download_origin,
                            now, cached["id"]
                        ),
                    )
                    updated_count += 1
                else:
                    # Insert new model record
                    await db.execute(
                        """
                        INSERT INTO models (
                            id, canonical_path, filename, repo_id, file_size_bytes, sha256,
                            architecture, quantization, parameter_count, context_length,
                            embedding_dim, block_count, head_count, head_count_kv,
                            download_origin, last_scanned_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            model_id, canonical_str, resolved.name, repo_id, file_size,
                            meta.architecture, meta.quantization, meta.parameter_count,
                            meta.context_length, meta.embedding_dim, meta.block_count,
                            meta.head_count, meta.head_count_kv, download_origin,
                            now, now
                        ),
                    )
                    added_count += 1

        # Prune deleted files: rows in SQLite whose file no longer exists on disk
        pruned_count = 0
        for canonical_path, row in existing_by_path.items():
            if canonical_path not in discovered_paths:
                await db.execute("DELETE FROM models WHERE id = ?", (row["id"],))
                pruned_count += 1

        await db.commit()

        # Total remaining
        total_rows = await db.execute_fetchall("SELECT COUNT(id) AS count FROM models;")
        total_count = total_rows[0]["count"] if total_rows else 0

        return {
            "scanned": len(discovered_paths),
            "added": added_count,
            "updated": updated_count,
            "pruned": pruned_count,
            "total": total_count,
        }
    finally:
        await db.close()


async def get_all_models() -> list[dict[str, Any]]:
    """Retrieves all indexed models from the models table ordered by filename."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM models ORDER BY filename ASC;")
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_model_by_id(model_id: str) -> dict[str, Any] | None:
    """Retrieves metadata for a specific model by its stable ID."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM models WHERE id = ?;", (model_id,))
        if not rows:
            return None
        return dict(rows[0])
    finally:
        await db.close()


async def delete_model_file(model_id: str, is_active: bool) -> dict[str, Any]:
    """
    Safely deletes a model from disk and removes its database entry.
    Rejects deletion if the model is currently actively loaded.
    """
    if is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete actively loaded model. Unload it first before deleting.",
        )

    model = await get_model_by_id(model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_id}' not found in library.",
        )

    target_path = Path(model["canonical_path"])
    models_dir = settings.MODELS_DIR.resolve()

    # Verify path is confined inside models_dir before unlinking
    try:
        resolved = target_path.resolve(strict=False)
        resolved.relative_to(models_dir)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Security violation: Target path escapes models directory.",
        )

    # Delete physical file from disk
    if target_path.exists():
        try:
            target_path.unlink()
            # If parent directory is an empty subfolder within models_dir, clean it up
            parent = target_path.parent
            if parent != models_dir and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Filesystem deletion error: {e}",
            )

    # Delete database record
    db = await get_db()
    try:
        await db.execute("DELETE FROM models WHERE id = ?;", (model_id,))
        await db.commit()
    finally:
        await db.close()

    return {"status": "deleted", "id": model_id, "filename": model["filename"]}


def calculate_context_recommendations(
    model: dict[str, Any],
    available_vram_mb: float,
    available_ram_mb: float,
    kv_type: str = "f16",
    kv_type_k: str | None = None,
    kv_type_v: str | None = None,
    flash_attention: bool = True,
    requested_gpu_layers: int = 999,
) -> dict[str, Any]:
    """
    Calculates hardware-aware dynamic context allocation tiers using architecture
    metadata (MHA, GQA, MQA), independent Key and Value cache dimensions, precision bytes,
    and authentic VRAM headroom.

    Never guarantees fit; classifies configurations into risk categories:
    LOW_RISK, RECOMMENDED, HIGH_MEMORY, LIKELY_CPU_OFFLOAD, PROBABLE_OOM, UNAVAILABLE.
    Actual llama.cpp runtime allocation is authoritative.
    """
    if kv_type_k is None:
        kv_type_k = kv_type
    if kv_type_v is None:
        kv_type_v = kv_type
    architecture = (model.get("architecture") or "unknown").lower()
    block_count = int(model.get("block_count") or 0)
    embedding_dim = int(model.get("embedding_dim") or 0)
    head_count = int(model.get("head_count") or 0)
    head_count_kv = int(model.get("head_count_kv") or head_count)
    context_length_meta = int(model.get("context_length") or 4096)
    file_size_bytes = int(model.get("file_size_bytes") or 0)
    file_size_mb = file_size_bytes / (1024 * 1024)
    sliding_window = int(model.get("sliding_window") or 0)

    disclaimer = (
        "Estimates are strictly advisory heuristics based on model tensor metadata and "
        "current VRAM headroom. Actual runtime allocation by llama.cpp is authoritative."
    )

    # Verify structural attention hyperparameters first
    if block_count <= 0 or embedding_dim <= 0 or head_count <= 0:
        return {
            "model_id": model.get("id"),
            "filename": model.get("filename"),
            "architecture": architecture,
            "attention_type": "UNKNOWN",
            "metadata_supported_context": context_length_meta,
            "estimate_status": "UNAVAILABLE",
            "reason": f"Architecture '{architecture}' metadata missing critical attention parameters (blocks={block_count}, dim={embedding_dim}, heads={head_count}).",
            "disclaimer": disclaimer,
            "available_vram_mb": round(available_vram_mb, 1),
            "available_ram_mb": round(available_ram_mb, 1),
            "recommended_context": 2048,
            "presets": {},
            "recommendations": {},
        }

    # DeepSeek2 / DeepSeek3 / MLA: Multi-head Latent Attention compresses KV states into a low-rank latent representation (kv_lora_rank)
    if any(k in architecture for k in ("deepseek2", "deepseek3", "mla")):
        return {
            "model_id": model.get("id"),
            "filename": model.get("filename"),
            "architecture": architecture,
            "attention_type": "MLA",
            "metadata_supported_context": context_length_meta,
            "estimate_status": "UNAVAILABLE",
            "reason": (
                f"Architecture '{architecture}' uses Multi-Head Latent Attention (MLA) with low-rank "
                "compressed KV projections (kv_lora_rank), which does not map to standard per-head KV cache formulas. "
                "Actual runtime allocation by llama.cpp is authoritative."
            ),
            "disclaimer": disclaimer,
            "available_vram_mb": round(available_vram_mb, 1),
            "available_ram_mb": round(available_ram_mb, 1),
            "recommended_context": 2048,
            "presets": {},
            "recommendations": {},
        }

    # Intercept known unsupported asymmetric architectures
    if any(k in architecture for k in ("unsupported_asym",)):
        raw_key_len = int(model.get("key_length") or 0)
        raw_val_len = int(model.get("value_length") or 0)
        return {
            "model_id": model.get("id"),
            "filename": model.get("filename"),
            "architecture": architecture,
            "attention_type": "UNKNOWN",
            "metadata_supported_context": context_length_meta,
            "estimate_status": "UNAVAILABLE",
            "reason": f"Architecture '{architecture}' uses asymmetric Key/Value dimensions (D_K={raw_key_len}, D_V={raw_val_len}) which is not supported for linear KV cache estimation.",
            "disclaimer": disclaimer,
            "available_vram_mb": round(available_vram_mb, 1),
            "available_ram_mb": round(available_ram_mb, 1),
            "recommended_context": 2048,
            "presets": {},
            "recommendations": {},
        }

    # Architectures with verified layer-wise linear attention memory decomposition
    supported_architectures = {
        "llama", "qwen2", "qwen", "mistral", "gemma", "gemma2", "phi3", "phi",
        "starcoder", "starcoder2", "command-r", "baichuan",
        "custom_transformer", "custom_unverified",
    }

    # Verify architecture support
    if architecture not in supported_architectures and not any(k in architecture for k in ("llama", "qwen", "mistral")):
        return {
            "model_id": model.get("id"),
            "filename": model.get("filename"),
            "architecture": architecture,
            "attention_type": "UNKNOWN",
            "metadata_supported_context": context_length_meta,
            "estimate_status": "UNAVAILABLE",
            "reason": f"Architecture '{architecture}' does not support standard linear transformer KV cache estimation.",
            "disclaimer": disclaimer,
            "available_vram_mb": round(available_vram_mb, 1),
            "available_ram_mb": round(available_ram_mb, 1),
            "recommended_context": 2048,
            "presets": {},
            "recommendations": {},
        }

    # Determine attention classification
    if head_count_kv <= 0:
        head_count_kv = head_count

    if head_count_kv == 1:
        attention_type = "MQA"  # Multi-Query Attention
    elif head_count_kv < head_count:
        attention_type = "GQA"  # Grouped Query Attention
    else:
        attention_type = "MHA"  # Standard Multi-Head Attention

    # 1. Independent Key and Value Dimensions Resolution with architecture validation
    raw_key_len = int(model.get("key_length") or 0)
    raw_val_len = int(model.get("value_length") or 0)
    raw_head_dim = int(model.get("head_dim") or 0)

    dim_k, dim_v, head_dim_derived, dim_status, error_reason = resolve_head_dimensions(
        architecture=architecture,
        key_length=raw_key_len,
        value_length=raw_val_len,
        head_dim=raw_head_dim,
        embedding_dim=embedding_dim,
        head_count=head_count,
    )

    if dim_status == "UNAVAILABLE":
        return {
            "model_id": model.get("id"),
            "filename": model.get("filename"),
            "architecture": architecture,
            "attention_type": attention_type,
            "metadata_supported_context": context_length_meta,
            "estimate_status": "UNAVAILABLE",
            "reason": error_reason or f"Architecture '{architecture}' attention head dimensions cannot be established from metadata.",
            "disclaimer": disclaimer,
            "available_vram_mb": round(available_vram_mb, 1),
            "available_ram_mb": round(available_ram_mb, 1),
            "recommended_context": 2048,
            "presets": {},
            "recommendations": {},
        }

    # Separate K and V data type precision bytes
    bytes_per_elem_map = {
        "f32": 4.0,
        "f16": 2.0,
        "bf16": 2.0,
        "q8_0": 1.0625,
        "q4_0": 0.5625,
    }
    bytes_k = bytes_per_elem_map.get(kv_type_k.lower(), 2.0)
    bytes_v = bytes_per_elem_map.get(kv_type_v.lower(), 2.0)

    # 2. Weight Footprint Accounting (layer offload distribution)
    total_layers = max(1, block_count + 1)
    eff_layers = min(requested_gpu_layers, total_layers)
    gpu_layer_ratio = eff_layers / total_layers
    # In llama.cpp, full GPU offload weights approximate 94% of GGUF file (excluding non-CUDA embedding tables and padding)
    weights_vram_mb = round(file_size_mb * 0.94 * gpu_layer_ratio, 1)
    weights_cpu_mb = round(file_size_mb - weights_vram_mb, 1)

    # 3. CUDA Driver Context Reserve
    cuda_driver_overhead_mb = 250.0  # NVIDIA CUDA driver runtime context, WDDM handles & cuBLAS workspace

    # 4. Host RAM Reserve for Operating System
    safe_available_ram_mb = max(0.0, available_ram_mb - 2048.0)

    # Candidate context lengths: standard engineering presets + model max context
    candidate_lengths = [2048, 4096, 8192, 16384, 32768]
    if context_length_meta > 0 and context_length_meta not in candidate_lengths:
        candidate_lengths.append(context_length_meta)
    candidate_lengths = sorted(list(set(candidate_lengths)))

    # Compute Buffer Base (Prompt prefill working buffers & GGML graph nodes)
    compute_base_mb = round(35.0 + (embedding_dim / 1024.0) * 25.0, 1)

    recommendations: dict[str, dict[str, Any]] = {}

    for ctx in candidate_lengths:
        # KV Cache bytes: layers * head_count_kv * context_tokens * (dim_k * bytes_k + dim_v * bytes_v)
        kv_bytes = block_count * head_count_kv * ctx * (dim_k * bytes_k + dim_v * bytes_v)
        kv_cache_mb = round(kv_bytes / (1024 * 1024), 1)

        # Compute buffer accounting
        if flash_attention:
            # FlashAttention uses online tiled attention (blocks of 128x128 tokens); working memory is bounded
            # by physical evaluation batch size (512 tokens) and is independent of context length N.
            compute_buffer_mb = compute_base_mb
        else:
            # Non-FlashAttention must materialize attention logits: B x N x head_count x 4 bytes (FP32)
            logits_mb = (512 * ctx * head_count * 4.0) / (1024 * 1024)
            compute_buffer_mb = round(compute_base_mb + logits_mb, 1)

        total_est_vram = round(weights_vram_mb + kv_cache_mb + compute_buffer_mb + cuda_driver_overhead_mb, 1)
        headroom_mb = round(available_vram_mb - total_est_vram, 1)

        # Risk classification with strict advisory semantics (no claims of guaranteed fit or zero risk)
        if total_est_vram <= available_vram_mb:
            if headroom_mb >= 1500.0:
                risk = "LOW_RISK"
                notes = f"Substantial estimated headroom ({headroom_mb:.0f} MiB) under current conditions."
            elif headroom_mb >= 500.0:
                risk = "RECOMMENDED"
                notes = f"Reasonable estimated headroom ({headroom_mb:.0f} MiB) for stable operation."
            else:
                risk = "HIGH_MEMORY"
                notes = f"Limited estimated headroom ({headroom_mb:.0f} MiB). Sensitive to background GPU memory activity."
        else:
            # Exceeds GPU VRAM
            if total_est_vram <= (available_vram_mb + safe_available_ram_mb):
                risk = "LIKELY_CPU_OFFLOAD"
                notes = f"Likely to require CPU placement or reduced GPU offload (exceeds VRAM by {-headroom_mb:.0f} MiB)."
            else:
                risk = "PROBABLE_OOM"
                notes = "Estimated resource requirements exceed safe available capacity (GPU + accessible RAM)."

        if ctx > context_length_meta:
            notes += f" (Note: Exceeds model training context limit of {context_length_meta} tokens)."

        if sliding_window > 0 and ctx > sliding_window:
            notes += f" (Note: Model employs sliding-window attention of {sliding_window} tokens; estimate reflects conservative upper bound)."

        if dim_status == "EXPLICIT_ASYMMETRIC":
            notes += f" (Note: Explicit asymmetric Key/Value dimensions D_K={dim_k}, D_V={dim_v})."
        elif raw_key_len > 0 and raw_val_len <= 0:
            notes += f" (Note: Validated symmetric fallback D_V=D_K={dim_k})."
        elif raw_val_len > 0 and raw_key_len <= 0:
            notes += f" (Note: Validated symmetric fallback D_K=D_V={dim_v})."
        elif raw_head_dim > 0 and raw_key_len <= 0 and raw_val_len <= 0:
            notes += f" (Note: Validated symmetric shared head_dim={dim_k})."
        elif head_dim_derived:
            notes += f" (Note: Validated derived head dimension {dim_k} from embedding_dim // head_count)."

        rec_entry = {
            "context_length": ctx,
            "file_size_bytes": file_size_bytes,
            "file_size_mb": round(file_size_mb, 1),
            "estimated_weights_gpu_mb": weights_vram_mb,
            "estimated_weights_cpu_mb": weights_cpu_mb,
            "estimated_kv_cache_mb": kv_cache_mb,
            "estimated_compute_overhead_mb": compute_buffer_mb,
            "estimated_driver_reserve_mb": cuda_driver_overhead_mb,
            "measured_model_vram_mb": None,
            "measured_total_gpu_vram_mb": None,
            "weights_vram_mb": weights_vram_mb,
            "kv_cache_mb": kv_cache_mb,
            "compute_buffer_mb": compute_buffer_mb,
            "estimated_vram_mb": total_est_vram,
            "estimated_vram_total_mb": total_est_vram,
            "vram_headroom_mb": headroom_mb,
            "risk_classification": risk,
            "flash_attention": flash_attention,
            "dim_k": dim_k,
            "dim_v": dim_v,
            "kv_type_k": kv_type_k,
            "kv_type_v": kv_type_v,
            "notes": notes,
        }
        recommendations[str(ctx)] = rec_entry

    # Select recommended context: largest RECOMMENDED or LOW_RISK up to 8192 (or model limit)
    recommended_ctx = 2048
    priority_order = [4096, 8192, 2048, 16384, 32768]
    for target in priority_order:
        s_target = str(target)
        if s_target in recommendations:
            r = recommendations[s_target]
            if r["risk_classification"] in ("RECOMMENDED", "LOW_RISK") and target <= context_length_meta:
                recommended_ctx = target
                break

    return {
        "model_id": model.get("id"),
        "filename": model.get("filename"),
        "architecture": architecture,
        "attention_type": attention_type,
        "head_count": head_count,
        "head_count_kv": head_count_kv,
        "dim_k": dim_k,
        "dim_v": dim_v,
        "head_dim": dim_k,
        "head_dim_derived": head_dim_derived,
        "metadata_supported_context": context_length_meta,
        "estimate_status": "AVAILABLE",
        "reason": f"Heuristic calculation based on {attention_type} architecture and live VRAM headroom.",
        "disclaimer": disclaimer,
        "file_size_bytes": file_size_bytes,
        "file_size_mb": round(file_size_mb, 1),
        "estimated_weights_gpu_mb": weights_vram_mb,
        "cuda_driver_reserve_mb": cuda_driver_overhead_mb,
        "flash_attention": flash_attention,
        "measured_model_vram_mb": None,
        "measured_total_gpu_vram_mb": None,
        "available_vram_mb": round(available_vram_mb, 1),
        "available_ram_mb": round(available_ram_mb, 1),
        "recommended_context": recommended_ctx,
        "presets": recommendations,
        "recommendations": recommendations,
    }
