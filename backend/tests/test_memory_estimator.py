"""
Advisory Memory Estimator Unit Tests
Verifies mathematical models, architecture guards, and disclaimer preservation.
"""

from pathlib import Path
from app.core.memory_estimator import (
    MemoryEstimateResult,
    estimate_model_memory,
)


def test_nonexistent_model_handled():
    """Verifies that non-existent files return unavailable estimate with clear reason."""
    dummy_path = Path("S:/Mustang AI/models/nonexistent_model.gguf")
    res = estimate_model_memory(dummy_path)
    assert isinstance(res, MemoryEstimateResult)
    assert res.estimate_available is False
    assert "does not exist" in res.reason
    assert "advisory heuristics" in res.disclaimer.lower()


def test_advisory_disclaimer_preservation():
    """Guarantees that the advisory disclaimer is never omitted or altered."""
    res = MemoryEstimateResult(
        model_name="test.gguf",
        architecture="unknown",
        estimate_available=False,
    )
    assert "authoritative" in res.disclaimer.lower()
    assert "advisory" in res.disclaimer.lower()


def test_real_model_memory_estimation_and_component_consistency():
    """
    Validates estimate_model_memory on actual Qwen2.5 GGUF file:
    verifies separated K and V dimension resolution, component-to-total consistency,
    and FlashAttention scaling.
    """
    real_path = Path("S:/Mustang AI/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")
    if not real_path.exists():
        return

    # 1. FlashAttention Enabled (Default)
    res_fa_on = estimate_model_memory(
        real_path,
        target_ctx=2048,
        flash_attention=True,
    )
    assert res_fa_on.estimate_available is True
    assert res_fa_on.architecture == "qwen2"
    assert res_fa_on.dim_k == 64
    assert res_fa_on.dim_v == 64
    assert res_fa_on.kv_cache_gpu_mb == 24.0
    assert res_fa_on.compute_overhead_mb == 56.9
    assert res_fa_on.estimated_driver_reserve_mb == 250.0

    # Component-to-total consistency check
    comp_sum = round(
        res_fa_on.weights_gpu_mb
        + res_fa_on.kv_cache_gpu_mb
        + res_fa_on.compute_overhead_mb
        + res_fa_on.estimated_driver_reserve_mb,
        1,
    )
    assert abs(res_fa_on.total_estimated_vram_mb - comp_sum) < 0.1

    # 2. FlashAttention Disabled
    res_fa_off = estimate_model_memory(
        real_path,
        target_ctx=2048,
        flash_attention=False,
    )
    # Logits buffer materialized: 56.9 + (512 * 2048 * 14 * 4) / 1024^2 = 56.9 + 56.0 = 112.9 MiB
    assert res_fa_off.compute_overhead_mb == 112.9


def test_real_model_separate_kv_quantization():
    """
    Validates independent quantization precision for Key and Value cache
    on actual model GGUF (e.g. q8_0 K, q4_0 V).
    """
    real_path = Path("S:/Mustang AI/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")
    if not real_path.exists():
        return

    res_quant = estimate_model_memory(
        real_path,
        target_ctx=2048,
        kv_type_k="q8_0",
        kv_type_v="q4_0",
    )
    assert res_quant.estimate_available is True
    # 24 layers * 2 KV heads * 2048 tokens * (64 * 1.0625 + 64 * 0.5625)
    # = 48 * 2048 * (68 + 36) = 98304 * 104 = 10,223,616 bytes = 9.75 MiB -> 9.8 MiB
    assert abs(res_quant.kv_cache_gpu_mb - 9.8) < 0.1

