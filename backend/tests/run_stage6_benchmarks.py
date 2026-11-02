"""
Mustang AI Milestone 3 Stage 6: Automated End-to-End Performance Benchmarking Suite
Executes live measurements across all M3 subsystems:
1. Local Model Library Discovery Latency (Cold Scan vs Warm Cached Scan)
2. Hugging Face Search & Quantization Metadata Extraction Latency
3. Checksum SHA-256 Streaming Verification Throughput (MB/s)
4. Real Model Switch Latency (Graceful shutdown, parameter configuration, CUDA server boot)
5. Inference Engine Time to First Token (TTFT) and Generation Tokens Per Second (TPS)
   across prompt token tiers (25, 50, 100, 200 tokens)
6. Authentic NVIDIA CUDA VRAM Telemetry vs Pre-Flight Advisory Estimates
"""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure we use the real engine for live hardware benchmarks
if "MUSTANG_USE_MOCK_ENGINE" in os.environ:
    del os.environ["MUSTANG_USE_MOCK_ENGINE"]

from app.config import settings
from app.core import hardware, model_library
from app.core.engine_base import (
    ChatCompletionRequest,
    ChatMessage,
    EngineConfig,
    EngineState,
)
from app.core.hf_client import get_hf_client
from app.core.llama_engine import LlamaServerEngine


async def run_stage6_benchmarks():
    results = {}
    print("\n" + "=" * 78)
    print(" MUSTANG AI — MILESTONE 3 STAGE 6 AUTOMATED BENCHMARKS")
    print("=" * 78)

    hw = hardware.get_system_telemetry()
    print(f"Cores: {hw.cpu_logical_cores} | Host RAM: {hw.memory.ram_total_mb:.1f} MB (Free: {hw.memory.ram_available_mb:.1f} MB)")
    if hw.gpu.available:
        print(f"GPU: {hw.gpu.name} | VRAM: {hw.gpu.vram_total_mb:.1f} MB (Free: {hw.gpu.vram_free_mb:.1f} MB)")
    else:
        print("GPU: CPU-only / No discrete CUDA device detected.")
    print("-" * 78 + "\n")

    # ---------------------------------------------------------
    # 1. MODEL LIBRARY SCAN LATENCY
    # ---------------------------------------------------------
    print(">>> 1. Benchmarking Model Library Scan Latency...")
    # Cold scan
    t0 = time.perf_counter()
    cold_res = await model_library.scan_models_directory()
    cold_dur_ms = (time.perf_counter() - t0) * 1000

    # Warm cached scan
    t0 = time.perf_counter()
    warm_res = await model_library.scan_models_directory()
    warm_dur_ms = (time.perf_counter() - t0) * 1000

    print(f"  - Cold Scan: {cold_dur_ms:.2f} ms ({cold_res['scanned']} files scanned, {cold_res['added']} added)")
    print(f"  - Warm Scan (Cached): {warm_dur_ms:.2f} ms ({warm_res['scanned']} files verified, {warm_res['updated']} updated)")

    results["scan_latency"] = {
        "cold_ms": round(cold_dur_ms, 2),
        "warm_ms": round(warm_dur_ms, 2),
        "files_scanned": cold_res["scanned"],
    }

    # ---------------------------------------------------------
    # 2. HUGGING FACE SEARCH & METADATA LATENCY
    # ---------------------------------------------------------
    print("\n>>> 2. Benchmarking Hugging Face Hub API Latency...")
    hf_client = get_hf_client()

    t0 = time.perf_counter()
    search_data = await hf_client.search_repositories(query="qwen2.5", limit=10)
    search_dur_ms = (time.perf_counter() - t0) * 1000
    print(f"  - HF Search ('qwen2.5'): {search_dur_ms:.2f} ms ({search_data['total']} repositories found)")

    t0 = time.perf_counter()
    repo_data = await hf_client.get_repository_details(repo_id="Qwen/Qwen2.5-0.5B-Instruct-GGUF")
    details_dur_ms = (time.perf_counter() - t0) * 1000
    print(f"  - Tree & Quantization Extraction: {details_dur_ms:.2f} ms ({len(repo_data['available_gguf_files'])} GGUF quantizations identified)")

    results["hf_latency"] = {
        "search_ms": round(search_dur_ms, 2),
        "details_ms": round(details_dur_ms, 2),
        "quantizations_found": len(repo_data["available_gguf_files"]),
    }

    # ---------------------------------------------------------
    # 3. STREAMING CHECKSUM HASHING THROUGHPUT
    # ---------------------------------------------------------
    print("\n>>> 3. Benchmarking SHA-256 Hashing Throughput...")
    test_sizes_mb = [10, 50, 100]
    hash_benchmarks = {}

    for size_mb in test_sizes_mb:
        payload = os.urandom(size_mb * 1024 * 1024)
        t0 = time.perf_counter()
        hasher = hashlib.sha256()
        chunk_size = 1024 * 1024  # 1MB buffer as in download_manager
        for i in range(0, len(payload), chunk_size):
            hasher.update(payload[i : i + chunk_size])
        digest = hasher.hexdigest()
        elapsed = time.perf_counter() - t0
        mb_per_sec = size_mb / elapsed
        print(f"  - {size_mb} MB Payload: {elapsed * 1000:.2f} ms ({mb_per_sec:.2f} MB/s throughput)")
        hash_benchmarks[f"{size_mb}MB"] = {
            "elapsed_ms": round(elapsed * 1000, 2),
            "throughput_mb_s": round(mb_per_sec, 2),
        }

    results["hashing_throughput"] = hash_benchmarks

    # ---------------------------------------------------------
    # 4. REAL INFERENCE RUNTIME, SWITCHING & GENERATION
    # ---------------------------------------------------------
    real_model_path = settings.MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    if not real_model_path.exists():
        print(f"\n[SKIP] Real model not found at {real_model_path}. Skipping live inference benchmark.")
        return results

    print("\n>>> 4. Benchmarking Live Model Load & CUDA Context Initialization...")
    engine = LlamaServerEngine()

    config_2k = EngineConfig(
        model_path=real_model_path,
        n_ctx=2048,
        n_gpu_layers=99,
        flash_attention=True,
        port=18085,
    )

    t0 = time.perf_counter()
    started = await engine.start(config_2k)
    init_dur_ms = (time.perf_counter() - t0) * 1000
    assert started, "Engine failed to boot on model 1!"
    status = await engine.get_status()
    print(f"  - Model Boot (2048 ctx): {init_dur_ms:.2f} ms (State: {status.state}, VRAM: {status.vram_allocated_mb} MB)")

    # ---------------------------------------------------------
    # 5. TTFT & GENERATION TOKENS PER SECOND (TPS) TIERS
    # ---------------------------------------------------------
    print("\n>>> 5. Benchmarking Inference Latency (TTFT & TPS across generation tiers)...")
    token_tiers = [25, 50, 100, 200]
    inference_results = {}

    for max_toks in token_tiers:
        req = ChatCompletionRequest(
            conversation_id=f"bench-tier-{max_toks}",
            messages=[
                ChatMessage(
                    role="user",
                    content="Describe the principles of high-performance operating system kernel design in detail.",
                )
            ],
            temperature=0.0,
            max_tokens=max_toks,
        )

        t_start = time.perf_counter()
        t_first = None
        token_count = 0

        async for chunk in engine.stream_chat(req):
            if t_first is None and chunk.delta_content:
                t_first = time.perf_counter()
            if chunk.delta_content:
                token_count += 1

        t_end = time.perf_counter()
        ttft_ms = ((t_first or t_end) - t_start) * 1000
        gen_time = t_end - (t_first or t_start)
        tps = (token_count / gen_time) if gen_time > 0 else 0.0

        print(f"  - Tier {max_toks} tokens: TTFT = {ttft_ms:.2f} ms | Generated {token_count} tokens in {gen_time:.2f}s | Speed = {tps:.2f} tok/s")
        inference_results[f"{max_toks}_tokens"] = {
            "requested_tokens": max_toks,
            "actual_tokens": token_count,
            "ttft_ms": round(ttft_ms, 2),
            "generation_seconds": round(gen_time, 2),
            "tps": round(tps, 2),
        }

    results["inference_performance"] = inference_results

    # ---------------------------------------------------------
    # 6. MODEL SWITCHING & CONTEXT PRESET ADJUSTMENT LATENCY
    # ---------------------------------------------------------
    print("\n>>> 6. Benchmarking Model Switch & Context Resize Latency...")
    # Resize context from 2048 to 8192
    config_8k = EngineConfig(
        model_path=real_model_path,
        n_ctx=8192,
        n_gpu_layers=99,
        flash_attention=True,
        port=18085,
    )
    t0 = time.perf_counter()
    switched = await engine.start(config_8k)
    switch_dur_ms = (time.perf_counter() - t0) * 1000
    assert switched
    status_8k = await engine.get_status()
    print(f"  - Dynamic Switch (2K -> 8K ctx): {switch_dur_ms:.2f} ms (Context: {status_8k.context_size}, VRAM: {status_8k.vram_allocated_mb} MB)")

    # Unload
    t0 = time.perf_counter()
    await engine.stop()
    unload_dur_ms = (time.perf_counter() - t0) * 1000
    print(f"  - Engine Unload & VRAM Release: {unload_dur_ms:.2f} ms")

    results["switch_latency"] = {
        "switch_ms": round(switch_dur_ms, 2),
        "unload_ms": round(unload_dur_ms, 2),
        "vram_at_8k_mb": status_8k.vram_allocated_mb,
    }

    # ---------------------------------------------------------
    # 7. MEMORY ACCURACY COMPARISON (Advisory vs Runtime)
    # ---------------------------------------------------------
    all_models = await model_library.get_all_models()
    real_meta = next((m for m in all_models if m["filename"] == real_model_path.name), None)
    if real_meta:
        est = model_library.calculate_context_recommendations(
            model=real_meta,
            available_vram_mb=hw.gpu.vram_free_mb if hw.gpu.available else 2000.0,
            available_ram_mb=hw.memory.ram_available_mb,
            kv_type="f16",
        )
        p2k = est["presets"]["2048"]
        p8k = est["presets"]["8192"]
        print("\n>>> 7. Memory Estimation Accuracy Comparison:")
        print(f"  - 2K Context: Estimated VRAM = {p2k['estimated_vram_mb']:.1f} MB | Runtime Measured = {status.vram_allocated_mb} MB")
        print(f"  - 8K Context: Estimated VRAM = {p8k['estimated_vram_mb']:.1f} MB | Runtime Measured = {status_8k.vram_allocated_mb} MB")

        results["memory_accuracy"] = {
            "2k_estimated_vram_mb": p2k["estimated_vram_mb"],
            "2k_runtime_vram_mb": status.vram_allocated_mb,
            "8k_estimated_vram_mb": p8k["estimated_vram_mb"],
            "8k_runtime_vram_mb": status_8k.vram_allocated_mb,
        }

    print("\n" + "=" * 78)
    print(" ALL BENCHMARKS COMPLETED SUCCESSFULLY")
    print("=" * 78 + "\n")

    # Save benchmark results to JSON
    out_file = settings.RUN_DIR / "stage6_benchmark_results.json"
    out_file.write_text(json.dumps(results, indent=2))
    print(f"Benchmark results written to: {out_file}")
    return results


if __name__ == "__main__":
    asyncio.run(run_stage6_benchmarks())
