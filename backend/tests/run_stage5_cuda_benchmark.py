"""
Milestone 3 Stage 5: Live CUDA Dynamic Context & Model Switching Benchmark
Executes live inference on NVIDIA GeForce RTX 5050 across context sizes (2K, 4K, 8K)
measuring transition time, authoritative layer offload, VRAM footprint, and tok/sec.
"""

import asyncio
import json
import time
from pathlib import Path

from app.config import settings
from app.core.engine_base import ChatCompletionRequest, ChatMessage, EngineConfig, EngineState
from app.core.hardware import get_gpu_telemetry
from app.core.llama_engine import LlamaServerEngine
from app.core.model_library import calculate_context_recommendations


async def run_benchmark():
    model_path = settings.MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"
    if not model_path.exists():
        print(f"Error: Model not found at {model_path}")
        return

    print("==================================================================")
    print(" MUSTANG AI — MILESTONE 3 STAGE 5 LIVE CUDA BENCHMARK")
    print(f" Model: {model_path.name}")
    print("==================================================================\n")

    # Initial hardware telemetry
    initial_gpu = get_gpu_telemetry()
    print(f"GPU Detected: {initial_gpu.name}")
    print(f"Driver Version: {initial_gpu.driver_version}")
    print(f"Total VRAM: {initial_gpu.vram_total_mb:.1f} MiB")
    print(f"Free VRAM: {initial_gpu.vram_free_mb:.1f} MiB\n")

    # Advisory recommendation check
    fake_meta = {
        "id": "qwen2.5-0.5b",
        "filename": model_path.name,
        "architecture": "qwen2",
        "block_count": 24,
        "embedding_dim": 896,
        "head_count": 14,
        "head_count_kv": 2,
        "context_length": 32768,
        "file_size_bytes": model_path.stat().st_size,
    }
    advisory_rec = calculate_context_recommendations(
        model=fake_meta,
        available_vram_mb=initial_gpu.vram_free_mb,
        available_ram_mb=16000.0,
    )
    print("Advisory Recommendations Summary:")
    for ctx_str, p in advisory_rec["presets"].items():
        print(f"  - Context {ctx_str:>5} tokens: {p['risk_classification']:<15} | Est VRAM: {p['estimated_vram_mb']:>6.1f} MiB | KV Cache: {p['kv_cache_mb']:>5.1f} MiB")
    print()

    contexts_to_test = [2048, 4096, 8192]
    results = []

    engine = LlamaServerEngine()

    try:
        for idx, ctx in enumerate(contexts_to_test):
            print(f"--- Benchmark Run {idx+1}/{len(contexts_to_test)}: Context Size = {ctx} tokens ---")
            config = EngineConfig(
                model_path=model_path,
                n_gpu_layers=99,
                n_ctx=ctx,
                n_batch=512,
                port=18080,
                host="127.0.0.1",
                flash_attention=True,
            )

            # Measure transition / startup time
            t_start = time.perf_counter()
            started = await engine.start(config)
            t_switch = time.perf_counter() - t_start

            if not started:
                print(f"FAILED to start engine for context {ctx}!")
                continue

            # Query authoritative metrics
            status = await engine.get_status()
            gpu_tele = get_gpu_telemetry()

            # Warm-up + Stream 64 tokens
            prompt = "In three bullet points, explain the primary benefits of dynamic context allocation in local LLM runtimes."
            req = ChatCompletionRequest(
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.7,
                max_tokens=64,
                stream=True,
            )

            t_gen_start = time.perf_counter()
            tokens_generated = 0
            async for chunk in engine.stream_chat(req):
                if chunk.delta_content:
                    tokens_generated += 1
            t_gen = max(0.01, time.perf_counter() - t_gen_start)
            tok_per_sec = round(tokens_generated / t_gen, 1)

            result_row = {
                "context_size": ctx,
                "advisory_est_vram_mb": advisory_rec["presets"][str(ctx)]["estimated_vram_mb"],
                "advisory_risk": advisory_rec["presets"][str(ctx)]["risk_classification"],
                "switch_time_sec": round(t_switch, 2),
                "layers_offloaded": f"{status.gpu_layers_offloaded}/{status.gpu_layers_requested}",
                "authoritative_vram_mb": round(status.vram_allocated_mb, 1),
                "nvml_used_vram_mb": round(gpu_tele.vram_used_mb, 1),
                "tokens_generated": tokens_generated,
                "generation_time_sec": round(t_gen, 2),
                "tok_per_sec": tok_per_sec,
            }
            results.append(result_row)
            print(f"  Switch Time: {t_switch:.2f}s | Layers: {result_row['layers_offloaded']} | Authoritative VRAM: {result_row['authoritative_vram_mb']} MiB | Speed: {tok_per_sec} tok/s\n")

    finally:
        await engine.stop()
        print("Engine cleanly stopped.")

    print("\n======================= FINAL BENCHMARK MATRIX =======================")
    print("| Context | Advisory Risk | Est VRAM | Switch Time | Offload Layers | Authoritative VRAM | NVML Used VRAM | Speed |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in results:
        print(
            f"| {r['context_size']} tokens | {r['advisory_risk']} | {r['advisory_est_vram_mb']} MiB | {r['switch_time_sec']}s | "
            f"{r['layers_offloaded']} | {r['authoritative_vram_mb']} MiB | {r['nvml_used_vram_mb']} MiB | **{r['tok_per_sec']} tok/s** |"
        )
    print("======================================================================\n")

    # Save benchmark results as JSON for artifact generation
    out_path = Path("stage5_benchmark_results.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Saved results to {out_path.resolve()}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())

