"""
Milestone 3 Stage 5: Dynamic Context Allocation Tests
Validates MHA, GQA, and MQA attention modeling, KV cache calculations,
risk tier classifications, context presets, and unsupported architecture handling.
"""

import pytest
from app.core import model_library


def test_mha_context_allocation():
    """Verifies standard Multi-Head Attention (head_count == head_count_kv)."""
    mha_model = {
        "id": "mod-mha-test",
        "filename": "mha_model.gguf",
        "architecture": "llama",
        "block_count": 32,
        "embedding_dim": 4096,
        "head_count": 32,
        "head_count_kv": 32,
        "context_length": 4096,
        "file_size_bytes": 4 * 1024 * 1024 * 1024,  # 4 GB
    }
    rec = model_library.calculate_context_recommendations(
        model=mha_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )

    assert rec["estimate_status"] == "AVAILABLE"
    assert rec["attention_type"] == "MHA"
    assert rec["head_dim"] == 128
    assert "2048" in rec["recommendations"]
    assert "4096" in rec["recommendations"]
    assert rec["disclaimer"] != ""
    assert "authoritative" in rec["disclaimer"].lower()

    # KV Cache bytes: 2 * 32 * 32 * 128 * 2048 * 2 (f16) = 1,073,741,824 bytes = 1024 MiB
    kv_2k = rec["recommendations"]["2048"]["kv_cache_mb"]
    assert abs(kv_2k - 1024.0) < 1.0


def test_gqa_context_allocation():
    """Verifies Grouped-Query Attention (head_count > head_count_kv > 1)."""
    # E.g. Qwen2.5 or LLaMA-3: 32 query heads, 8 KV heads
    gqa_model = {
        "id": "mod-gqa-test",
        "filename": "gqa_model.gguf",
        "architecture": "qwen2",
        "block_count": 28,
        "embedding_dim": 3584,
        "head_count": 28,
        "head_count_kv": 4,  # 4 KV heads
        "context_length": 32768,
        "file_size_bytes": 3 * 1024 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=gqa_model,
        available_vram_mb=7500.0,
        available_ram_mb=24000.0,
    )

    assert rec["estimate_status"] == "AVAILABLE"
    assert rec["attention_type"] == "GQA"
    assert rec["head_count_kv"] == 4
    # GQA significantly reduces KV cache footprint compared to MHA
    kv_4k = rec["recommendations"]["4096"]["kv_cache_mb"]
    # 2 * 28 * 4 * 128 * 4096 * 2 bytes = 234,881,024 bytes = 224 MiB
    assert abs(kv_4k - 224.0) < 1.0
    assert "32768" in rec["presets"]


def test_mqa_context_allocation():
    """Verifies Multi-Query Attention (head_count_kv == 1)."""
    mqa_model = {
        "id": "mod-mqa-test",
        "filename": "mqa_model.gguf",
        "architecture": "llama",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 1,  # Single KV head
        "context_length": 8192,
        "file_size_bytes": 1500 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=mqa_model,
        available_vram_mb=6000.0,
        available_ram_mb=16000.0,
    )

    assert rec["estimate_status"] == "AVAILABLE"
    assert rec["attention_type"] == "MQA"
    assert rec["head_count_kv"] == 1
    # 2 * 24 * 1 * 128 * 4096 * 2 bytes = 50,331,648 bytes = 48 MiB
    kv_4k = rec["recommendations"]["4096"]["kv_cache_mb"]
    assert abs(kv_4k - 48.0) < 1.0


def test_risk_classification_tiers():
    """Verifies risk tiers (LOW_RISK, RECOMMENDED, HIGH_MEMORY, LIKELY_CPU_OFFLOAD, PROBABLE_OOM)."""
    small_model = {
        "id": "mod-small",
        "filename": "small.gguf",
        "architecture": "qwen2",
        "block_count": 24,
        "embedding_dim": 896,
        "head_count": 14,
        "head_count_kv": 2,
        "context_length": 32768,
        "file_size_bytes": 350 * 1024 * 1024,  # ~350 MB weights
    }

    # Case A: Generous VRAM (8GB) -> LOW_RISK for 2K
    rec_generous = model_library.calculate_context_recommendations(
        model=small_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_generous["recommendations"]["2048"]["risk_classification"] == "LOW_RISK"

    # Case B: Constrained VRAM (500 MB) -> LIKELY_CPU_OFFLOAD or PROBABLE_OOM
    rec_tight = model_library.calculate_context_recommendations(
        model=small_model,
        available_vram_mb=500.0,
        available_ram_mb=16000.0,
    )
    assert rec_tight["recommendations"]["4096"]["risk_classification"] in ("LIKELY_CPU_OFFLOAD", "PROBABLE_OOM")

    # Case C: Exceeds both VRAM and RAM
    rec_oom = model_library.calculate_context_recommendations(
        model=small_model,
        available_vram_mb=200.0,
        available_ram_mb=300.0,
    )
    assert rec_oom["recommendations"]["32768"]["risk_classification"] == "PROBABLE_OOM"


def test_unsupported_and_missing_metadata_handled_safely():
    """Verifies that non-transformer architectures and missing metadata return UNAVAILABLE."""
    # Missing parameters
    empty_model = {
        "id": "mod-empty",
        "architecture": "llama",
        "block_count": 0,
        "embedding_dim": 0,
        "head_count": 0,
    }
    rec_empty = model_library.calculate_context_recommendations(
        model=empty_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_empty["estimate_status"] == "UNAVAILABLE"
    assert "missing critical attention parameters" in rec_empty["reason"]

    # Unsupported architecture (e.g. State-space model or RNN)
    mamba_model = {
        "id": "mod-mamba",
        "architecture": "mamba_ssm_v2",
        "block_count": 32,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 16,
    }
    rec_mamba = model_library.calculate_context_recommendations(
        model=mamba_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_mamba["estimate_status"] == "UNAVAILABLE"
    assert "does not support standard linear transformer KV cache estimation" in rec_mamba["reason"]


def test_separate_key_and_value_dimensions():
    """
    Validates explicit key and value dimensions when D_K != D_V.
    General formulation:
    KV cache bytes = layers * KV heads * context tokens * (D_K * b_K + D_V * b_V)
    """
    asym_model = {
        "id": "mod-asym",
        "filename": "asym.gguf",
        "architecture": "llama",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 128,    # D_K = 128
        "value_length": 64,   # D_V = 64
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=asym_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
        kv_type_k="f16",  # 2.0 bytes
        kv_type_v="f16",  # 2.0 bytes
    )

    assert rec["estimate_status"] == "AVAILABLE"
    assert rec["dim_k"] == 128
    assert rec["dim_v"] == 64
    assert rec["head_dim_derived"] is False

    # Arithmetic check at 2048 tokens:
    # 24 layers * 2 KV heads * 2048 tokens * (128 * 2.0 + 64 * 2.0 bytes)
    # = 48 * 2048 * (256 + 128) = 98304 * 384 = 37,748,736 bytes = 36.0 MiB
    kv_2k = rec["recommendations"]["2048"]["kv_cache_mb"]
    assert abs(kv_2k - 36.0) < 0.1


def test_separate_k_and_v_quantization_types():
    """
    Validates independent quantization precision for Key and Value cache (e.g. q8_0 K, q4_0 V).
    q8_0 = 1.0625 bytes, q4_0 = 0.5625 bytes.
    """
    quant_model = {
        "id": "mod-quant",
        "filename": "quant.gguf",
        "architecture": "qwen2",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 128,
        "value_length": 128,
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=quant_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
        kv_type_k="q8_0",  # 1.0625 bytes
        kv_type_v="q4_0",  # 0.5625 bytes
    )

    assert rec["estimate_status"] == "AVAILABLE"
    # Arithmetic check at 2048 tokens:
    # 24 layers * 2 KV heads * 2048 tokens * (128 * 1.0625 + 128 * 0.5625)
    # = 48 * 2048 * (136.0 + 72.0) = 98304 * 208 = 20,447,232 bytes = 19.5 MiB
    kv_2k = rec["recommendations"]["2048"]["kv_cache_mb"]
    assert abs(kv_2k - 19.5) < 0.1


def test_deepseek2_and_mla_classified_as_unavailable():
    """
    Validates that DeepSeek2/MLA architectures are strictly classified as UNAVAILABLE
    due to low-rank compressed KV projection (kv_lora_rank).
    """
    ds2_model = {
        "id": "mod-ds2",
        "filename": "deepseek2.gguf",
        "architecture": "deepseek2",
        "block_count": 60,
        "embedding_dim": 5120,
        "head_count": 128,
        "head_count_kv": 128,
        "context_length": 32768,
        "file_size_bytes": 15 * 1024 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=ds2_model,
        available_vram_mb=12000.0,
        available_ram_mb=32000.0,
    )

    assert rec["estimate_status"] == "UNAVAILABLE"
    assert rec["attention_type"] == "MLA"
    assert "kv_lora_rank" in rec["reason"]
    assert "authoritative" in rec["reason"].lower()


def test_sliding_window_attention_notes():
    """
    Validates that models employing sliding-window attention (Gemma 2, StarCoder2)
    provide a conservative upper bound with explicit explanatory notes when ctx > sliding_window.
    """
    gemma2_model = {
        "id": "mod-gemma2",
        "filename": "gemma2.gguf",
        "architecture": "gemma2",
        "block_count": 26,
        "embedding_dim": 2304,
        "head_count": 8,
        "head_count_kv": 4,
        "sliding_window": 4096,
        "context_length": 8192,
        "file_size_bytes": 2 * 1024 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=gemma2_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )

    assert rec["estimate_status"] == "AVAILABLE"
    # At 2048 tokens: <= 4096 sliding window -> no sliding window note
    assert "sliding-window" not in rec["recommendations"]["2048"]["notes"].lower()
    # At 8192 tokens: > 4096 sliding window -> explicit conservative upper bound note
    assert "sliding-window attention of 4096" in rec["recommendations"]["8192"]["notes"]
    assert "conservative upper bound" in rec["recommendations"]["8192"]["notes"]


def test_compute_buffer_arithmetic_all_five_presets():
    """
    Asserts exact arithmetic across all 5 context sizes (2K, 4K, 8K, 16K, 32K) for:
    - Estimated GPU weights
    - Estimated KV cache
    - Estimated compute overhead
    - Driver reserve
    - Total estimated VRAM
    """
    qwen_model = {
        "id": "mod-qwen-test",
        "filename": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "architecture": "qwen2",
        "block_count": 24,
        "embedding_dim": 896,
        "head_count": 14,
        "head_count_kv": 2,
        "context_length": 32768,
        "file_size_bytes": 416922944,  # 397.615 MiB
    }
    rec = model_library.calculate_context_recommendations(
        model=qwen_model,
        available_vram_mb=6144.0,
        available_ram_mb=16384.0,
        flash_attention=True,
    )

    assert rec["estimate_status"] == "AVAILABLE"
    presets = rec["recommendations"]

    # Expected values for Qwen2.5 0.5B with FlashAttention:
    # Weights GPU = 397.615 * 0.94 = 373.7 MiB
    # Compute base = 35.0 + (896/1024) * 25.0 = 35.0 + 21.875 = 56.9 MiB
    # Driver reserve = 250.0 MiB
    expected_matrix = {
        2048:  {"kv": 24.0,  "compute": 56.9, "total": 704.6},
        4096:  {"kv": 48.0,  "compute": 56.9, "total": 728.6},
        8192:  {"kv": 96.0,  "compute": 56.9, "total": 776.6},
        16384: {"kv": 192.0, "compute": 56.9, "total": 872.6},
        32768: {"kv": 384.0, "compute": 56.9, "total": 1064.6},
    }

    for ctx, exp in expected_matrix.items():
        entry = presets[str(ctx)]
        assert abs(entry["estimated_weights_gpu_mb"] - 373.7) < 0.2
        assert abs(entry["estimated_kv_cache_mb"] - exp["kv"]) < 0.2
        assert abs(entry["estimated_compute_overhead_mb"] - exp["compute"]) < 0.2
        assert abs(entry["estimated_driver_reserve_mb"] - 250.0) < 0.1
        assert abs(entry["estimated_vram_total_mb"] - exp["total"]) < 0.3


def test_component_estimates_consistency_with_total():
    """
    Verifies that for every preset, the sum of individual components
    identically matches the reported total estimated VRAM.
    """
    test_model = {
        "id": "mod-consistency",
        "filename": "consistency.gguf",
        "architecture": "llama",
        "block_count": 32,
        "embedding_dim": 4096,
        "head_count": 32,
        "head_count_kv": 8,
        "context_length": 32768,
        "file_size_bytes": 2000 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=test_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )

    for ctx_str, p in rec["recommendations"].items():
        component_sum = round(
            p["estimated_weights_gpu_mb"]
            + p["estimated_kv_cache_mb"]
            + p["estimated_compute_overhead_mb"]
            + p["estimated_driver_reserve_mb"],
            1,
        )
        assert abs(p["estimated_vram_total_mb"] - component_sum) < 0.1


def test_flash_attention_enabled_vs_disabled_scaling():
    """
    Verifies FlashAttention on vs off scaling:
    - FlashAttention ON: working memory is bounded by micro-batch (512 tokens), constant across N.
    - FlashAttention OFF: attention logits tensor (B * N * H * 4 bytes) scales linearly with N.
    """
    test_model = {
        "id": "mod-fa-test",
        "filename": "fa_model.gguf",
        "architecture": "qwen2",
        "block_count": 24,
        "embedding_dim": 896,
        "head_count": 14,
        "head_count_kv": 2,
        "context_length": 32768,
        "file_size_bytes": 400 * 1024 * 1024,
    }

    rec_fa_on = model_library.calculate_context_recommendations(
        model=test_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
        flash_attention=True,
    )
    rec_fa_off = model_library.calculate_context_recommendations(
        model=test_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
        flash_attention=False,
    )

    # FlashAttention ON: compute overhead is constant (56.9 MiB)
    assert rec_fa_on["recommendations"]["2048"]["estimated_compute_overhead_mb"] == 56.9
    assert rec_fa_on["recommendations"]["32768"]["estimated_compute_overhead_mb"] == 56.9

    # FlashAttention OFF: compute overhead scales with context:
    # 56.9 + (512 * ctx * 14 * 4) / 1024^2
    # At 2048 tokens: 56.9 + 56.0 = 112.9 MiB
    # At 4096 tokens: 56.9 + 112.0 = 168.9 MiB
    # At 8192 tokens: 56.9 + 224.0 = 280.9 MiB
    assert rec_fa_off["recommendations"]["2048"]["estimated_compute_overhead_mb"] == 112.9
    assert rec_fa_off["recommendations"]["4096"]["estimated_compute_overhead_mb"] == 168.9
    assert rec_fa_off["recommendations"]["8192"]["estimated_compute_overhead_mb"] == 280.9


def test_fallback_missing_k_metadata():
    """
    Verifies dimension fallback rules when key_length is missing:
    - Verified symmetric architecture (e.g. llama): safely infers D_K = D_V = value_length.
    - Unvalidated architecture (e.g. custom_unverified): refuses to guess and returns UNAVAILABLE.
    """
    # 1. Verified symmetric architecture
    sym_model = {
        "id": "mod-k-sym",
        "filename": "k_sym.gguf",
        "architecture": "llama",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 0,       # Missing K metadata
        "value_length": 128,   # Explicit V metadata
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec_sym = model_library.calculate_context_recommendations(
        model=sym_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_sym["estimate_status"] == "AVAILABLE"
    assert rec_sym["dim_k"] == 128
    assert rec_sym["dim_v"] == 128
    assert "Validated symmetric fallback" in rec_sym["recommendations"]["2048"]["notes"]

    # 2. Unvalidated architecture
    unval_model = {
        "id": "mod-k-unval",
        "filename": "k_unval.gguf",
        "architecture": "custom_unverified",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 0,       # Missing K metadata
        "value_length": 128,   # Explicit V metadata
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec_unval = model_library.calculate_context_recommendations(
        model=unval_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_unval["estimate_status"] == "UNAVAILABLE"
    assert "missing key_length" in rec_unval["reason"]
    assert "not validated as symmetric" in rec_unval["reason"]


def test_fallback_missing_v_metadata():
    """
    Verifies dimension fallback rules when value_length is missing:
    - Verified symmetric architecture (e.g. qwen2): safely infers D_V = D_K = key_length.
    - Unvalidated architecture (e.g. custom_unverified): refuses to guess and returns UNAVAILABLE.
    """
    # 1. Verified symmetric architecture
    sym_model = {
        "id": "mod-v-sym",
        "filename": "v_sym.gguf",
        "architecture": "qwen2",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 128,     # Explicit K metadata
        "value_length": 0,     # Missing V metadata
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec_sym = model_library.calculate_context_recommendations(
        model=sym_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_sym["estimate_status"] == "AVAILABLE"
    assert rec_sym["dim_k"] == 128
    assert rec_sym["dim_v"] == 128
    assert "Validated symmetric fallback" in rec_sym["recommendations"]["2048"]["notes"]

    # 2. Unvalidated architecture
    unval_model = {
        "id": "mod-v-unval",
        "filename": "v_unval.gguf",
        "architecture": "custom_unverified",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 128,     # Explicit K metadata
        "value_length": 0,     # Missing V metadata
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec_unval = model_library.calculate_context_recommendations(
        model=unval_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_unval["estimate_status"] == "UNAVAILABLE"
    assert "missing value_length" in rec_unval["reason"]
    assert "not validated as symmetric" in rec_unval["reason"]


def test_fallback_validated_shared_dimensions():
    """
    Verifies that explicit head_dim and derived embedding_dim // head_count
    are only applied when validated by the architecture metadata:
    - Verified symmetric (mistral / gemma): accepted with validated status.
    - Unvalidated architecture (custom_model): rejected as UNAVAILABLE.
    """
    # 1. Verified symmetric with explicit head_dim
    mistral_model = {
        "id": "mod-shared-mistral",
        "filename": "mistral.gguf",
        "architecture": "mistral",
        "block_count": 32,
        "embedding_dim": 4096,
        "head_count": 32,
        "head_count_kv": 8,
        "head_dim": 128,       # Explicit head_dim
        "context_length": 8192,
        "file_size_bytes": 2000 * 1024 * 1024,
    }
    rec_m = model_library.calculate_context_recommendations(
        model=mistral_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_m["estimate_status"] == "AVAILABLE"
    assert rec_m["dim_k"] == 128
    assert rec_m["dim_v"] == 128
    assert rec_m["head_dim_derived"] is False
    assert "Validated symmetric shared head_dim" in rec_m["recommendations"]["2048"]["notes"]

    # 2. Verified symmetric with derived embedding_dim // head_count
    gemma_model = {
        "id": "mod-derived-gemma",
        "filename": "gemma.gguf",
        "architecture": "gemma",
        "block_count": 28,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 16,
        "context_length": 8192,
        "file_size_bytes": 1500 * 1024 * 1024,
    }
    rec_g = model_library.calculate_context_recommendations(
        model=gemma_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_g["estimate_status"] == "AVAILABLE"
    assert rec_g["dim_k"] == 128
    assert rec_g["dim_v"] == 128
    assert rec_g["head_dim_derived"] is True

    # 3. Unvalidated architecture with explicit head_dim
    unval_head_dim = {
        "id": "mod-unval-head-dim",
        "filename": "unval_head_dim.gguf",
        "architecture": "custom_transformer",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "head_dim": 128,
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec_unval_hd = model_library.calculate_context_recommendations(
        model=unval_head_dim,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_unval_hd["estimate_status"] == "UNAVAILABLE"
    assert "not validated as symmetric" in rec_unval_hd["reason"]

    # 4. Unvalidated architecture with derived embedding_dim // head_count
    unval_derived = {
        "id": "mod-unval-derived",
        "filename": "unval_derived.gguf",
        "architecture": "custom_transformer",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec_unval_der = model_library.calculate_context_recommendations(
        model=unval_derived,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec_unval_der["estimate_status"] == "UNAVAILABLE"
    assert "requires verified symmetric architecture validation" in rec_unval_der["reason"]


def test_unsupported_asymmetric_dimensions():
    """
    Verifies that asymmetric Key and Value dimensions on unsupported or non-linear
    architectures (e.g. unsupported_asym, deepseek2) are strictly classified as UNAVAILABLE.
    """
    unsupported_asym_model = {
        "id": "mod-unsupp-asym",
        "filename": "unsupp_asym.gguf",
        "architecture": "unsupported_asym",
        "block_count": 24,
        "embedding_dim": 2048,
        "head_count": 16,
        "head_count_kv": 2,
        "key_length": 128,
        "value_length": 64,
        "context_length": 4096,
        "file_size_bytes": 1000 * 1024 * 1024,
    }
    rec = model_library.calculate_context_recommendations(
        model=unsupported_asym_model,
        available_vram_mb=8000.0,
        available_ram_mb=16000.0,
    )
    assert rec["estimate_status"] == "UNAVAILABLE"
    assert "asymmetric Key/Value dimensions" in rec["reason"]
    assert "not supported" in rec["reason"].lower()

