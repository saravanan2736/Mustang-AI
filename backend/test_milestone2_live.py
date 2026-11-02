"""
Milestone 2 Verification Script: Live CUDA End-to-End Chat Experience Test
"""

import asyncio
import json
import time
from pathlib import Path
import httpx

BACKEND = "http://127.0.0.1:18000"


async def main():
    token_file = Path("S:/Mustang AI/backend/run/.mustang_token")
    if token_file.exists():
        token = token_file.read_text("utf-8").strip()
    else:
        async with httpx.AsyncClient(base_url=BACKEND) as client:
            auth_resp = await client.get("/api/auth/bootstrap")
            token = auth_resp.json()["token"]
    auth_headers = {"X-Mustang-Token": token}
    print(f"[1] Using session token: {token[:12]}...")

    async with httpx.AsyncClient(base_url=BACKEND, timeout=90.0) as client:

        print("\n[2] Checking hardware telemetry...")
        hw = (await client.get("/api/system/hardware")).json()
        print(f"    GPU: {hw['gpu']['name']} (Available: {hw['gpu']['available']})")
        print(f"    VRAM: {hw['gpu']['vram_used_mb']:.1f} / {hw['gpu']['vram_total_mb']:.1f} MB")
        print(f"    RAM: {hw['memory']['ram_used_mb']:.1f} / {hw['memory']['ram_total_mb']:.1f} MB")

        print("\n[3] Loading model qwen2.5-0.5b-instruct-q4_k_m.gguf onto RTX 5050...")
        load_res = await client.post(
            "/api/models/load",
            json={"model_name": "qwen2.5-0.5b-instruct-q4_k_m.gguf", "n_gpu_layers": 99, "n_ctx": 4096},
            headers=auth_headers,
        )
        assert load_res.status_code == 200, f"Load failed: {load_res.text}"
        status = load_res.json()
        print(f"    Engine State: {status['state']}")
        print(f"    Offloaded Layers: {status['gpu_layers_offloaded']}/{status['gpu_layers_requested']}")
        print(f"    Authoritative VRAM: {status['vram_allocated_mb']:.2f} MiB")

        print("\n[4] Creating conversation thread...")
        conv_res = await client.post(
            "/api/conversations",
            json={
                "title": "New Chat",
                "model": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
                "system_prompt": "You are Mustang AI, an elite software architect.",
                "profile_id": "coder",
            },
        )
        assert conv_res.status_code == 201
        conv = conv_res.json()
        conv_id = conv["id"]
        print(f"    Conversation ID: {conv_id}")

        print("\n[5] Streaming prompt with live CUDA token generation...")
        prompt = "Write a Python function with type hints to calculate factorial using memoization. Return code only."
        start_time = time.perf_counter()
        token_count = 0
        received_text = []

        async with client.stream(
            "POST",
            "/api/chat/completions",
            json={
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "top_p": 0.9,
                "max_tokens": 512,
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                clean = line.strip()
                if not clean or not clean.startswith("data:"):
                    continue
                data_str = clean[5:].strip()
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    token_count += 1
                    received_text.append(delta)

        elapsed = time.perf_counter() - start_time
        speed = token_count / elapsed if elapsed > 0 else 0
        full_output = "".join(received_text)
        print(f"    Generated {token_count} tokens in {elapsed:.2f}s ({speed:.1f} tok/s)")
        print(f"    Output Preview:\n{full_output[:180]}...")

        # Small pause for generator finally: DB commit to settle
        await asyncio.sleep(0.5)

        print("\n[6] Verifying SQLite persistence and auto-titling...")
        conv_detail = (await client.get(f"/api/conversations/{conv_id}")).json()
        print(f"    Updated Title: '{conv_detail['title']}'")
        print(f"    Message count: {len(conv_detail['messages'])}")
        assert len(conv_detail["messages"]) == 2
        assert conv_detail["messages"][0]["role"] == "user"
        assert conv_detail["messages"][1]["role"] == "assistant"

        print("\n[7] Testing Regenerate response...")
        regen_tokens = 0
        async with client.stream(
            "POST",
            "/api/chat/regenerate",
            json={"conversation_id": conv_id, "temperature": 0.8},
        ) as regen_resp:
            assert regen_resp.status_code == 200
            async for line in regen_resp.aiter_lines():
                clean = line.strip()
                if clean.startswith("data:") and "[DONE]" not in clean:
                    chunk = json.loads(clean[5:].strip())
                    if chunk.get("choices", [{}])[0].get("delta", {}).get("content"):
                        regen_tokens += 1

        print(f"    Regenerated {regen_tokens} tokens successfully.")
        await asyncio.sleep(0.5)
        conv_after_regen = (await client.get(f"/api/conversations/{conv_id}")).json()
        assert len(conv_after_regen["messages"]) == 2

        print("\n[8] Unloading model...")
        unload_res = await client.post("/api/models/unload", headers=auth_headers)
        assert unload_res.status_code == 200
        print(f"    Final Engine State: {unload_res.json()['state']}")

        print("\n>>> ALL MILESTONE 2 LIVE INTEGRATION CHECKS PASSED SUCCESSFULLY! <<<")


if __name__ == "__main__":
    asyncio.run(main())
