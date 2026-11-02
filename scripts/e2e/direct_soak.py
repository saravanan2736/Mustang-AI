"""M4 Phase G: the soak cycle of phase_g_soak.ps1 driven directly over HTTP (no WebView/CDP).
Usage: python direct_soak.py <base_url> <session_token> <backend_pid> <minutes>. Run it only against a backend
started with scratch MUSTANG_RUN_DIR / MUSTANG_LOG_DIR and private ports (see M4_PHASEG_RELEASE_REPORT.md §7)."""
import sys, time, httpx, psutil
BASE, TOKEN, PID, MINUTES = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
c = httpx.Client(base_url=BASE, headers={"X-Mustang-Token": TOKEN}, timeout=300)
proc = psutil.Process(PID)
t0 = time.time(); cycle = 0
while time.time() - t0 < MINUTES * 60:
    cycle += 1; ts = time.time(); slow = []
    ctx = 4096 if cycle % 2 else 2048
    r = c.post("/api/models/load", json={"model_name": "qwen2.5-0.5b-instruct-q4_k_m.gguf", "n_gpu_layers": 99, "n_ctx": ctx}); assert r.status_code == 200, r.text
    for k in range(5):
        a = time.time()
        r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": f"Give one interesting fact about horses (#{cycle}-{k})."}], "max_tokens": 96, "stream": False})
        d = time.time() - a
        if d > 3: slow.append(f"chat{k} {d:.0f}s len={len(r.json()['choices'][0]['message']['content'])}")
    a = time.time()
    with c.stream("POST", "/api/chat/completions", json={"conversation_id": f"soak-{cycle}", "messages": [{"role": "user", "content": "Count from 1 to 40."}], "max_tokens": 160}) as r:
        n = sum(1 for _ in r.iter_bytes())
    if time.time() - a > 5: slow.append(f"stream {time.time()-a:.0f}s")
    if cycle % 4 == 0: c.post("/api/models/unload")
    m = proc.memory_info()
    print(f"cycle {cycle:3} {time.time()-ts:5.1f}s ws {m.rss/2**20:6.0f} MB private {m.private/2**20:6.0f} MB {' '.join(slow)}", flush=True)
