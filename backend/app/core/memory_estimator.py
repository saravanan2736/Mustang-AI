"""
Mustang AI Advisory Memory Estimator
Inspects GGUF metadata to estimate VRAM requirements before loading.
NOTE: Estimates are non-binding and advisory. Actual llama.cpp allocation is authoritative.
"""

import threading
from pathlib import Path
from typing import Literal
from pydantic import BaseModel
import gguf

from app.core.hardware import get_gpu_telemetry
from app.core.gguf_parser import (
    resolve_head_dimensions,
    is_architecture_verified_symmetric,
    VERIFIED_SYMMETRIC_ARCHITECTURES,
    UNSUPPORTED_ASYMMETRIC_ARCHITECTURES,
)

# Architectures with verified layer-wise linear attention memory decomposition
SUPPORTED_ARCHITECTURES = {
    "llama",
    "qwen2",
    "qwen",
    "mistral",
    "gemma",
    "gemma2",
    "phi3",
    "phi",
    "starcoder",
    "starcoder2",
    "command-r",
    "baichuan",
}

# Quantization formats bytes per weight element approximation
QUANT_BYTES_PER_ELEM: dict[str, float] = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 1.0625,
    "q6_k": 0.825,
    "q5_k": 0.70,
    "q5_0": 0.6875,
    "q4_k": 0.5625,
    "q4_0": 0.5625,
    "q3_k": 0.4375,
    "q2_k": 0.3125,
}


class MemoryEstimateResult(BaseModel):
    model_name: str
    architecture: str
    estimate_available: bool
    reason: str = ""
    disclaimer: str = (
        "Estimates are strictly advisory heuristics based on model tensor metadata and "
        "current VRAM headroom. The actual allocation by llama.cpp upon startup is authoritative."
    )
    
    total_layers: int = 0
    requested_gpu_layers: int = 0
    context_length: int = 4096
    
    # 1. GGUF File Size
    file_size_bytes: int = 0
    file_size_mb: float = 0.0

    # 2. Estimated GPU Weight Allocation
    weights_gpu_mb: float = 0.0
    weights_cpu_mb: float = 0.0
    estimated_weights_gpu_mb: float = 0.0

    # 3. Estimated KV Cache Allocation
    kv_cache_gpu_mb: float = 0.0
    estimated_kv_cache_mb: float = 0.0

    # 4. Estimated Compute & Runtime Overhead
    compute_overhead_mb: float = 0.0
    estimated_compute_overhead_mb: float = 0.0
    cuda_driver_reserve_mb: float = 250.0
    estimated_driver_reserve_mb: float = 250.0

    # 5. Measured Model Allocation (Authoritative from llama-server if running)
    measured_model_vram_mb: float | None = None

    # 6. Measured Total GPU Memory Usage (Authentic NVML measurement)
    measured_total_gpu_vram_mb: float | None = None

    total_estimated_vram_mb: float = 0.0
    available_vram_mb: float = 0.0
    vram_headroom_mb: float = 0.0

    fit_status: Literal["FULL_GPU_SAFE", "TIGHT_MARGIN", "PROBABLE_OOM", "UNAVAILABLE"] = "UNAVAILABLE"
    risk_classification: str = "UNAVAILABLE"
    recommended_gpu_layers: int = 0

    # Architecture metadata inspection
    head_dim: int = 0
    dim_k: int = 0
    dim_v: int = 0
    head_dim_derived: bool = False
    kv_type_k: str = "f16"
    kv_type_v: str = "f16"
    flash_attention: bool = True
    notes: str = ""


# Integer hyperparameters ("<arch>.<key>") the estimate reads from the GGUF header.
_HEADER_KEYS = (
    "block_count",
    "embedding_length",
    "attention.head_count",
    "attention.head_count_kv",
    "attention.sliding_window",
    "attention.key_length",
    "attention.value_length",
    "attention.head_dim",
)
_HEADER_CACHE_MAX = 64
_header_cache: dict[tuple[str, int, int], tuple[str | None, dict[str, int]]] = {}
_header_lock = threading.Lock()


def _read_header(gguf_path: Path) -> tuple[str | None, dict[str, int]]:
    """
    Architecture and the integer hyperparameters of a GGUF file.

    GGUFReader takes seconds and holds hundreds of MB (the tokenizer arrays) per parse, and the
    UI asks for an estimate on every slider change and model state change. Each file version
    (path, size, mtime) is parsed once, and parses are serialized so concurrent requests wait
    for the one in progress instead of each holding their own copy.
    """
    stat = gguf_path.stat()
    key = (str(gguf_path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _header_lock:
        cached = _header_cache.get(key)
        if cached is not None:
            return cached

        reader = gguf.GGUFReader(str(gguf_path))
        arch_field = reader.get_field("general.architecture")
        arch: str | None = None
        values: dict[str, int] = {}
        if arch_field:
            arch = str(arch_field.parts[arch_field.data[0]], "utf-8") if isinstance(arch_field.data[0], int) else str(arch_field.parts[0])
            for suffix in _HEADER_KEYS:
                field = reader.get_field(f"{arch}.{suffix}")
                if field is not None and len(field.parts) > 0:
                    try:
                        values[suffix] = int(field.parts[-1][0]) if hasattr(field.parts[-1], '__getitem__') else int(field.parts[-1])
                    except Exception:
                        pass
        del reader

        if len(_header_cache) >= _HEADER_CACHE_MAX:
            _header_cache.pop(next(iter(_header_cache)))
        _header_cache[key] = (arch, values)
        return arch, values


def estimate_model_memory(
    gguf_path: Path,
    requested_gpu_layers: int = 999,
    target_ctx: int = 4096,
    kv_type: str = "f16",
    kv_type_k: str | None = None,
    kv_type_v: str | None = None,
    flash_attention: bool = True,
) -> MemoryEstimateResult:
    """
    Parses GGUF header and computes advisory VRAM budget for RTX 5050 (or current GPU).
    If architecture is unsupported (e.g. MoE / MLA / RWKV), safely returns estimate_available=False.
    """
    if kv_type_k is None:
        kv_type_k = kv_type
    if kv_type_v is None:
        kv_type_v = kv_type

    model_name = gguf_path.name

    if not gguf_path.exists():
        return MemoryEstimateResult(
            model_name=model_name,
            architecture="unknown",
            estimate_available=False,
            reason=f"Model file does not exist: {gguf_path}",
        )

    try:
        arch, header_values = _read_header(gguf_path)
    except Exception as e:
        return MemoryEstimateResult(
            model_name=model_name,
            architecture="unknown",
            estimate_available=False,
            reason=f"Could not parse GGUF header: {e}",
        )

    # 1. Detect Architecture
    if not arch:
        return MemoryEstimateResult(
            model_name=model_name,
            architecture="unknown",
            estimate_available=False,
            reason="Missing general.architecture in GGUF header.",
        )

    arch_lower = arch.lower()

    # MLA Architecture check (DeepSeek2 / DeepSeek3 / MLA)
    if any(k in arch_lower for k in ("deepseek2", "deepseek3", "mla")):
        return MemoryEstimateResult(
            model_name=model_name,
            architecture=arch,
            estimate_available=False,
            fit_status="UNAVAILABLE",
            risk_classification="UNAVAILABLE",
            reason=(
                f"Architecture '{arch}' uses Multi-Head Latent Attention (MLA) with low-rank "
                "compressed KV projections (kv_lora_rank), which does not map to standard per-head KV cache formulas. "
                "Actual runtime allocation by llama.cpp is authoritative."
            ),
        )

    if arch_lower not in SUPPORTED_ARCHITECTURES and not any(k in arch_lower for k in ("llama", "qwen", "mistral")):
        return MemoryEstimateResult(
            model_name=model_name,
            architecture=arch,
            estimate_available=False,
            fit_status="UNAVAILABLE",
            risk_classification="UNAVAILABLE",
            reason=(
                f"Architecture '{arch}' (e.g. MoE/specialized) does not support static linear "
                "layer estimation. Actual startup allocation by llama.cpp will be authoritative."
            ),
        )

    # 2. Extract Structural Hyperparameters
    def get_val(key_suffix: str, default: int) -> int:
        return header_values.get(key_suffix, default)

    block_count = get_val("block_count", 32)
    embedding_length = get_val("embedding_length", 4096)
    head_count = get_val("attention.head_count", 32)
    head_count_kv = get_val("attention.head_count_kv", head_count)  # GQA support
    sliding_window = get_val("attention.sliding_window", 0)

    # 1. Independent Key and Value Dimensions Resolution with architecture validation
    raw_key_len = get_val("attention.key_length", 0)
    raw_val_len = get_val("attention.value_length", 0)
    raw_head_dim = get_val("attention.head_dim", 0)

    dim_k, dim_v, head_dim_derived, dim_status, error_reason = resolve_head_dimensions(
        architecture=arch,
        key_length=raw_key_len,
        value_length=raw_val_len,
        head_dim=raw_head_dim,
        embedding_dim=embedding_length,
        head_count=head_count,
    )

    if dim_status == "UNAVAILABLE":
        return MemoryEstimateResult(
            model_name=model_name,
            architecture=arch,
            estimate_available=False,
            fit_status="UNAVAILABLE",
            risk_classification="UNAVAILABLE",
            reason=error_reason or f"Architecture '{arch}' attention head dimensions cannot be established from metadata.",
        )

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

    # Clamp requested layers to total block count
    eff_gpu_layers = min(requested_gpu_layers, block_count)

    # 3. Calculate Weight Memory Distribution
    file_stat = gguf_path.stat()
    file_size_bytes = file_stat.st_size
    total_file_size_mb = file_size_bytes / (1024 * 1024)

    total_layers = max(1, block_count + 1)
    eff_layers = min(eff_gpu_layers, total_layers)
    gpu_layer_ratio = eff_layers / total_layers
    weights_gpu_mb = round(total_file_size_mb * 0.94 * gpu_layer_ratio, 1)
    weights_cpu_mb = round(total_file_size_mb - weights_gpu_mb, 1)

    # 4. Calculate KV Cache Size
    # Formula: layers_on_gpu * head_count_kv * target_ctx * (dim_k * bytes_k + dim_v * bytes_v)
    kv_bytes = eff_gpu_layers * head_count_kv * target_ctx * (dim_k * bytes_k + dim_v * bytes_v)
    kv_gpu_mb = round(kv_bytes / (1024 * 1024), 1)

    # 5. Compute Buffer & Driver Overhead
    compute_base_mb = round(35.0 + (embedding_length / 1024.0) * 25.0, 1)
    if flash_attention:
        compute_overhead_mb = compute_base_mb
    else:
        logits_mb = (512 * target_ctx * head_count * 4.0) / (1024 * 1024)
        compute_overhead_mb = round(compute_base_mb + logits_mb, 1)

    driver_reserve_mb = 250.0

    total_est_vram_mb = round(weights_gpu_mb + kv_gpu_mb + compute_overhead_mb + driver_reserve_mb, 1)

    # 6. Compare with Live Telemetry
    telemetry = get_gpu_telemetry()
    avail_vram_mb = telemetry.vram_free_mb if telemetry.available else 0.0
    measured_used_vram_mb = telemetry.vram_used_mb if telemetry.available else None
    headroom_mb = round(avail_vram_mb - total_est_vram_mb, 1)

    # Fit Assessment & Risk Tier
    if headroom_mb >= 1500.0:
        fit_status = "FULL_GPU_SAFE"
        risk_tier = "LOW_RISK"
    elif headroom_mb >= 500.0:
        fit_status = "FULL_GPU_SAFE"
        risk_tier = "RECOMMENDED"
    elif headroom_mb >= 0.0:
        fit_status = "TIGHT_MARGIN"
        risk_tier = "HIGH_MEMORY"
    else:
        fit_status = "PROBABLE_OOM"
        risk_tier = "PROBABLE_OOM"

    notes = "Heuristic estimate; actual llama.cpp startup allocation is authoritative."
    if sliding_window > 0 and target_ctx > sliding_window:
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

    # Compute Recommended Layers fitting into available VRAM
    budget_for_layers = max(0.0, avail_vram_mb - compute_overhead_mb - driver_reserve_mb)
    layer_weight_mb = (total_file_size_mb * 0.94) / total_layers
    kv_per_layer_mb = (head_count_kv * target_ctx * (dim_k * bytes_k + dim_v * bytes_v)) / (1024 * 1024)
    layer_cost_with_kv = layer_weight_mb + kv_per_layer_mb
    rec_layers = int(budget_for_layers // max(0.1, layer_cost_with_kv))
    rec_layers = max(0, min(block_count, rec_layers))

    return MemoryEstimateResult(
        model_name=model_name,
        architecture=arch,
        estimate_available=True,
        reason="Heuristic estimation calculated from GGUF metadata and live VRAM headroom.",
        total_layers=block_count,
        requested_gpu_layers=eff_gpu_layers,
        context_length=target_ctx,
        file_size_bytes=file_size_bytes,
        file_size_mb=round(total_file_size_mb, 1),
        weights_gpu_mb=weights_gpu_mb,
        weights_cpu_mb=weights_cpu_mb,
        estimated_weights_gpu_mb=weights_gpu_mb,
        kv_cache_gpu_mb=kv_gpu_mb,
        estimated_kv_cache_mb=kv_gpu_mb,
        compute_overhead_mb=compute_overhead_mb,
        estimated_compute_overhead_mb=compute_overhead_mb,
        cuda_driver_reserve_mb=driver_reserve_mb,
        estimated_driver_reserve_mb=driver_reserve_mb,
        measured_model_vram_mb=None,
        measured_total_gpu_vram_mb=measured_used_vram_mb,
        total_estimated_vram_mb=total_est_vram_mb,
        available_vram_mb=round(avail_vram_mb, 1),
        vram_headroom_mb=headroom_mb,
        fit_status=fit_status,
        risk_classification=risk_tier,
        recommended_gpu_layers=rec_layers,
        head_dim=dim_k,
        dim_k=dim_k,
        dim_v=dim_v,
        head_dim_derived=head_dim_derived,
        kv_type_k=kv_type_k,
        kv_type_v=kv_type_v,
        flash_attention=flash_attention,
        notes=notes,
    )

