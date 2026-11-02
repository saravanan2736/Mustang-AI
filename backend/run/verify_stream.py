import httpx
import json
import time

payload = {
    "messages": [
        {"role": "user", "content": "Write a 3-bullet point summary of why running AI locally is powerful."}
    ],
    "temperature": 0.7,
    "max_tokens": 128,
    "stream": True,
}

start = time.time()
tokens = []
print("--- STREAMING RESPONSE FROM MUSTANG AI ---")
with httpx.stream("POST", "http://127.0.0.1:18000/api/chat/completions", json=payload, timeout=30.0) as resp:
    for line in resp.iter_lines():
        if line.startswith("data:"):
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            chunk = json.loads(data_str)
            content = chunk["choices"][0]["delta"].get("content", "")
            if content:
                tokens.append(content)
                print(content, end="", flush=True)

elapsed = time.time() - start
print("\n--- STREAM COMPLETED ---")
print(f"Total tokens generated: {len(tokens)}")
print(f"Elapsed time: {elapsed:.2f}s")
print(f"Generation performance: {len(tokens) / elapsed:.1f} tokens/second")

