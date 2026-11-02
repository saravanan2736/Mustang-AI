// Mustang AI — Phase B connectivity probe (runs inside the real Tauri WebView2).
// Every request is a normal fetch/EventSource subject to CORS, CSP and any
// Local Network Access rules; nothing here bypasses browser security.
"use strict";

const invoke = window.__TAURI__.core.invoke;
const results = [];
let info = null; // from probe_info; contains the TEST-ONLY token (never reported)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => performance.now();

function record(name, status, detail) {
  results.push({ name, status, detail });
  const tr = document.createElement("tr");
  const cells = [String(results.length), name, status, typeof detail === "string" ? detail : JSON.stringify(detail)];
  cells.forEach((text, i) => {
    const td = document.createElement("td");
    td.textContent = text;
    if (i === 2) td.className = `r ${status}`;
    if (i === 3) td.className = "d";
    tr.appendChild(td);
  });
  document.getElementById("results").appendChild(tr);
}

// fetch with a hard timeout so a blocked/pending request (e.g. a permission prompt) is visible as a hang.
async function timedFetch(url, opts = {}, timeoutMs = 8000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort("timeout"), timeoutMs);
  const t0 = now();
  try {
    const res = await fetch(url, { ...opts, signal: ctl.signal });
    return { ok: true, res, ms: Math.round(now() - t0) };
  } catch (err) {
    return { ok: false, error: `${err.name}: ${err.message}`, timedOut: ctl.signal.aborted, ms: Math.round(now() - t0) };
  } finally {
    clearTimeout(t);
  }
}

const auth = () => ({ "X-Mustang-Token": info.test_token });

async function waitHealthy(base, { noCors = false, timeoutMs = 20000 } = {}) {
  const t0 = now();
  let attempts = 0;
  let firstError = null;
  while (now() - t0 < timeoutMs) {
    attempts++;
    const r = await timedFetch(`${base}/health`, noCors ? { mode: "no-cors" } : {}, 2000);
    if (r.ok && (noCors || r.res.ok)) {
      const body = noCors ? null : await r.res.json();
      return { ready: true, attempts, ms: Math.round(now() - t0), firstError, bootId: body && body.boot_id };
    }
    if (!r.ok && !firstError) firstError = r.error;
    await sleep(250);
  }
  return { ready: false, attempts, ms: Math.round(now() - t0), firstError };
}

async function readStream(url, { headers = {}, abortAfter = 0, onEvent } = {}) {
  const ctl = new AbortController();
  const res = await fetch(url, { headers, signal: ctl.signal });
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  const arrivals = [];
  let buf = "";
  let done = false;
  let error = null;
  try {
    while (true) {
      const { value, done: d } = await reader.read();
      if (d) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (!frame.startsWith("data:")) continue;
        const data = frame.slice(5).trim();
        if (data === "[DONE]") { done = true; continue; }
        arrivals.push(now());
        if (onEvent) await onEvent(arrivals.length);
        if (abortAfter && arrivals.length === abortAfter) ctl.abort();
      }
    }
  } catch (err) {
    error = `${err.name}: ${err.message}`;
  }
  return { status: res.status, events: arrivals.length, done, error, arrivals };
}

async function permissionState(name) {
  try {
    const p = await navigator.permissions.query({ name });
    return p.state;
  } catch (err) {
    return `unsupported (${err.name})`;
  }
}

async function run() {
  info = await invoke("probe_info");
  const runtime = await invoke("desktop_runtime_info");
  const env = {
    location_origin: location.origin,
    location_href: location.href,
    is_secure_context: window.isSecureContext,
    user_agent: navigator.userAgent,
    runtime,
    backend_base_url: info.base_url,
    allowed_origins: info.allowed_origins,
    lna_permission_local_network_access: await permissionState("local-network-access"),
    lna_permission_loopback_network: await permissionState("loopback-network"),
    lna_permission_local_network: await permissionState("local-network"),
  };
  document.getElementById("env").textContent = JSON.stringify(env, null, 2);
  record("Tauri WebView2 origin", "INFO", { origin: location.origin, secureContext: window.isSecureContext });

  // 1. Backend unavailable during initial startup (shell starts it with a 2.5 s delay).
  {
    const h = await waitHealthy(info.base_url);
    record("Backend unavailable at startup → retry until ready", h.ready && !!h.firstError ? "PASS" : "FAIL", h);
  }

  // 2. GET JSON + actual origin seen by the server (token header ⇒ CORS preflight).
  {
    const r = await timedFetch(`${info.base_url}/api/echo`, { headers: auth() });
    if (r.ok && r.res.ok) {
      const body = await r.res.json();
      record("HTTP GET (JSON, token header, preflighted)", "PASS", { ms: r.ms, server_saw_origin: body.origin, server_saw_host: body.host });
      record("Server-observed Origin matches location.origin", body.origin === location.origin ? "PASS" : "FAIL", { server: body.origin, page: location.origin });
    } else {
      record("HTTP GET (JSON, token header, preflighted)", "FAIL", r.ok ? { status: r.res.status } : r);
    }
  }

  // 3. Simple GET (no preflight).
  {
    const r = await timedFetch(`${info.base_url}/api/public-echo`);
    record("HTTP GET simple request (no preflight)", r.ok && r.res.ok ? "PASS" : "FAIL", r.ok ? { status: r.res.status, ms: r.ms } : r);
  }

  // 4. POST JSON round-trip.
  {
    const payload = { hello: "mustang", n: 42, nested: { list: [1, 2, 3] }, unicode: "ñ✓" };
    const r = await timedFetch(`${info.base_url}/api/echo-json`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth() },
      body: JSON.stringify(payload),
    });
    if (r.ok && r.res.ok) {
      const body = await r.res.json();
      const same = JSON.stringify(body.received) === JSON.stringify(payload);
      record("HTTP POST JSON request/response", same ? "PASS" : "FAIL", { ms: r.ms, echoed_equal: same });
    } else {
      record("HTTP POST JSON request/response", "FAIL", r.ok ? { status: r.res.status } : r);
    }
  }

  // 5. Missing token → readable 401 (CORS headers present on error responses).
  {
    const r = await timedFetch(`${info.base_url}/api/echo`);
    record("Missing token → 401 visible to app", r.ok && r.res.status === 401 ? "PASS" : "FAIL", r.ok ? { status: r.res.status } : r);
  }

  // 6. Preflight actually happened.
  {
    const r = await timedFetch(`${info.base_url}/api/stats`, { headers: auth() });
    const s = r.ok && r.res.ok ? await r.res.json() : null;
    record("CORS preflight (OPTIONS) observed by server", s && s.options > 0 ? "PASS" : "FAIL", s ? { options_requests: s.options, origins: s.origins } : r);
  }

  // 7. Streaming via fetch reader (the path useChatStream.ts uses).
  {
    const s = await readStream(`${info.base_url}/api/stream?n=20&interval_ms=100`, { headers: auth() });
    const spread = s.arrivals.length > 1 ? Math.round(s.arrivals[s.arrivals.length - 1] - s.arrivals[0]) : 0;
    const gaps = s.arrivals.slice(1).map((t, i) => t - s.arrivals[i]);
    const incremental = s.events === 20 && spread > 1500;
    record("fetch streaming (SSE frames) arrives incrementally", incremental && s.done ? "PASS" : "FAIL", {
      events: s.events, done: s.done, first_to_last_ms: spread,
      max_gap_ms: Math.round(Math.max(...gaps)), min_gap_ms: Math.round(Math.min(...gaps)), error: s.error,
    });
  }

  // 8. EventSource (download-progress path today).
  {
    const res = await new Promise((resolve) => {
      let n = 0;
      const es = new EventSource(`${info.base_url}/api/stream-es?n=5&interval_ms=100`);
      const t = setTimeout(() => { es.close(); resolve({ events: n, done: false, timedOut: true }); }, 8000);
      es.onmessage = () => { n++; };
      es.addEventListener("done", () => { clearTimeout(t); es.close(); resolve({ events: n, done: true }); });
      es.onerror = () => { if (n === 0) { clearTimeout(t); es.close(); resolve({ events: n, done: false, error: "EventSource error" }); } };
    });
    record("EventSource cross-origin stream", res.done && res.events === 5 ? "PASS" : "FAIL", res);
  }

  // 9. Client abort mid-stream propagates to the server (Stop button path).
  {
    const before = await (await fetch(`${info.base_url}/api/stats`, { headers: auth() })).json();
    const s = await readStream(`${info.base_url}/api/stream?n=50&interval_ms=100`, { headers: auth(), abortAfter: 5 });
    await sleep(1200);
    const after = await (await fetch(`${info.base_url}/api/stats`, { headers: auth() })).json();
    const detected = after.aborted_streams > before.aborted_streams;
    record("Client abort mid-stream detected server-side", detected ? "PASS" : "FAIL", {
      events_before_abort: s.events, client_error: s.error, server_aborted_before: before.aborted_streams, server_aborted_after: after.aborted_streams,
    });
  }

  // 10. Connection refused on a closed port.
  {
    const r = await timedFetch(`http://127.0.0.1:${info.closed_port}/health`);
    record("Connection refused (closed port) fails fast", !r.ok && !r.timedOut ? "PASS" : "FAIL", r);
  }

  // 11. localhost name instead of 127.0.0.1.
  {
    const r = await timedFetch(`http://localhost:${info.port}/api/public-echo`);
    const body = r.ok && r.res.ok ? await r.res.json() : null;
    record("http://localhost:<port> also reachable", body ? "INFO" : "INFO", body ? { host: body.host, ms: r.ms } : r);
  }

  // 12. Host-header validation (raw request from Rust; browsers cannot forge Host).
  {
    const evil = await invoke("probe_raw_host_check", { host: "evil.example" });
    const rebind = await invoke("probe_raw_host_check", { host: `attacker.test:${info.port}` });
    const good = await invoke("probe_raw_host_check", { host: `127.0.0.1:${info.port}` });
    record("Host-header validation (DNS-rebinding guard)", evil === 403 && rebind === 403 && good === 200 ? "PASS" : "FAIL", {
      "evil.example": evil, "attacker.test:port": rebind, "127.0.0.1:port": good,
    });
  }

  // 13. Backend stopped → requests fail cleanly (no hang).
  {
    const outcome = await invoke("probe_stop_backend", { graceful: true });
    const r = await timedFetch(`${info.base_url}/health`, {}, 5000);
    record("Backend down → fetch fails fast", !r.ok && !r.timedOut ? "PASS" : "FAIL", { stop: outcome, fetch: r });
  }

  // 14. Restart on the same port → reconnect, new instance.
  {
    const newInfo = await invoke("probe_restart_backend", { samePort: true, startupDelayMs: 1000, allowedOrigins: null });
    const h = await waitHealthy(newInfo.base_url);
    info = newInfo;
    record("Restart (same port) → reconnect", h.ready && !!h.firstError ? "PASS" : "FAIL", { ...h, launches: newInfo.launches });
  }

  // 15. Restart on a new port → app re-queries shell for the address.
  {
    const oldBase = info.base_url;
    const newInfo = await invoke("probe_restart_backend", { samePort: false, startupDelayMs: 0, allowedOrigins: null });
    const h = await waitHealthy(newInfo.base_url);
    const old = await timedFetch(`${oldBase}/health`, {}, 3000);
    info = newInfo;
    record("Restart (new port) → reconnect via shell-provided URL", h.ready && !old.ok ? "PASS" : "FAIL", {
      old: oldBase, new: newInfo.base_url, ready_ms: h.ms, old_port_now: old.ok ? "still answering" : old.error,
    });
  }

  // 16. Backend killed mid-stream → stream errors out instead of hanging.
  {
    let killed = false;
    const t0 = now();
    const s = await Promise.race([
      readStream(`${info.base_url}/api/stream?n=100&interval_ms=100`, {
        headers: auth(),
        onEvent: async (n) => { if (n === 5 && !killed) { killed = true; invoke("probe_stop_backend", { graceful: false }); } },
      }),
      sleep(15000).then(() => ({ hung: true })),
    ]);
    const ok = !s.hung && !s.done && s.events < 100;
    record("Backend crash mid-stream → client detects termination", ok ? "PASS" : "FAIL", {
      events: s.events, done_marker: s.done, client_error: s.error, hung: !!s.hung, ms: Math.round(now() - t0),
    });
    const back = await invoke("probe_restart_backend", { samePort: true, startupDelayMs: 0, allowedOrigins: null });
    info = back;
    await waitHealthy(info.base_url);
  }

  // 17. CORS enforced: backend whose allow-list excludes this origin.
  {
    const originalOrigins = info.allowed_origins;
    const wrong = await invoke("probe_restart_backend", { samePort: true, startupDelayMs: 0, allowedOrigins: "http://example.invalid" });
    info = wrong;
    const reach = await waitHealthy(info.base_url, { noCors: true });
    const simple = await timedFetch(`${info.base_url}/api/public-echo`);
    const pre = await timedFetch(`${info.base_url}/api/echo`, { headers: auth() });
    const logs = await invoke("probe_backend_logs");
    const serverSawSimple = logs.some((l) => l.includes("GET /api/public-echo"));
    record("CORS enforced when origin not allow-listed", reach.ready && !simple.ok && !pre.ok ? "PASS" : "FAIL", {
      server_reachable_no_cors: reach.ready, simple_get: simple.ok ? simple.res.status : simple.error,
      preflighted_get: pre.ok ? pre.res.status : pre.error, server_received_simple_get: serverSawSimple,
    });
    info = await invoke("probe_restart_backend", { samePort: true, startupDelayMs: 0, allowedOrigins: originalOrigins });
    await waitHealthy(info.base_url);
  }

  // 18. Captured backend logs never contain the token.
  {
    const logs = await invoke("probe_backend_logs");
    const leaked = logs.some((l) => l.includes(info.test_token));
    record("Backend logs captured; token absent", !leaked && logs.length > 0 ? "PASS" : "FAIL", { lines: logs.length, leaked });
  }

  const failed = results.filter((r) => r.status === "FAIL").length;
  const passed = results.filter((r) => r.status === "PASS").length;
  const summary = document.getElementById("summary");
  summary.textContent = `${passed} passed, ${failed} failed`;
  summary.className = `summary ${failed ? "fail" : "pass"}`;

  const report = { generated_at: new Date().toISOString(), environment: env, passed, failed, results };
  const path = await invoke("probe_report", { results: report });
  console.log("probe report:", path);
}

run().catch(async (err) => {
  record("probe runner", "FAIL", `${err && err.stack ? err.stack : err}`);
  try { await invoke("probe_report", { results: { fatal: String(err), results } }); } catch (_) {}
});
