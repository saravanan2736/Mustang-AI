// Mustang AI — desktop end-to-end smoke test (M4 Phase C).
//
// Drives the real desktop window through the WebView2 DevTools protocol, exactly as a
// user would: wait for the app, load the model, send a prompt, wait for the streamed
// reply. Start the app with
//   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<CDP_PORT>
// (test runs only), then: node scripts/e2e/desktop_e2e.mjs
//
// Prints one JSON summary line; exit code 0 on success.

const CDP_PORT = Number(process.env.CDP_PORT ?? 9223);
const MODEL_MATCH = process.env.E2E_MODEL ?? "qwen2.5-0.5b";
const PROMPT = process.env.E2E_PROMPT ?? "In one short sentence, what is a mustang?";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const t0 = Date.now();
const steps = [];
const step = (name, extra = {}) => {
  steps.push({ name, at_ms: Date.now() - t0, ...extra });
  console.error(`[e2e +${((Date.now() - t0) / 1000).toFixed(1)}s] ${name}`);
};

async function findPage(timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      const page = targets.find((t) => t.type === "page" && /^(http:\/\/tauri\.localhost|http:\/\/127\.0\.0\.1:5173)/.test(t.url));
      if (page) return page;
    } catch {}
    await sleep(500);
  }
  throw new Error("no WebView2 page target found on the DevTools port");
}

class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && this.pending.has(msg.id)) {
        this.pending.get(msg.id)(msg);
        this.pending.delete(msg.id);
      }
    };
  }
  open() {
    return new Promise((res, rej) => {
      this.ws.onopen = res;
      this.ws.onerror = rej;
    });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res) => this.pending.set(id, res));
  }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? "evaluation failed");
    return r.result?.result?.value;
  }
  async waitFor(description, expression, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        if (await this.eval(expression)) return;
      } catch {}
      await sleep(250);
    }
    throw new Error(`timed out waiting for: ${description}`);
  }
}

const buttonByText = (text) =>
  `[...document.querySelectorAll('button')].find(b => b.innerText.trim() === ${JSON.stringify(text)} && !b.disabled)`;

async function main() {
  const page = await findPage();
  const cdp = new Cdp(page.webSocketDebuggerUrl);
  await cdp.open();
  step("attached", { url: page.url });

  // Past the start-up gate and no reconnect overlay (BackendGate).
  await cdp.waitFor(
    "app rendered past the start-up gate",
    "document.body.innerText.includes('New Chat') && !document.querySelector('.fixed.inset-0.z-50')",
    90000
  );
  const origin = await cdp.eval("location.origin");
  step("app ready", { origin });

  await cdp.eval(`${buttonByText("New Chat")}.click()`);
  step("new chat");

  await cdp.waitFor(
    "model listed",
    `[...document.querySelectorAll('option')].some(o => o.textContent.includes(${JSON.stringify(MODEL_MATCH)}))`,
    30000
  );
  await cdp.eval(`(() => {
    const opt = [...document.querySelectorAll('option')].find(o => o.textContent.includes(${JSON.stringify(MODEL_MATCH)}));
    const sel = opt.closest('select');
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(sel, opt.value);
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  step("model selected");

  await cdp.waitFor("Load Model enabled", `!!${buttonByText("Load Model")}`, 20000);
  await cdp.eval(`${buttonByText("Load Model")}.click()`);
  step("load clicked");
  await cdp.waitFor("prompt enabled (engine READY)", "(() => { const t = document.querySelector('textarea'); return t && !t.disabled; })()", 120000);
  step("engine ready");

  await cdp.eval(`(() => {
    const ta = document.querySelector('textarea');
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(ta, ${JSON.stringify(PROMPT)});
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await cdp.waitFor("Send enabled", `!!${buttonByText("Send")}`, 10000);
  const sentAt = await cdp.eval(`(() => { const t = performance.now(); ${buttonByText("Send")}.click(); return t; })()`);
  step("prompt sent");

  // Generation finished when Send is back, stable for 1 s.
  await sleep(1000);
  await cdp.waitFor("generation finished", `!![...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Send')`, 180000);
  step("generation finished");
  // The status bar's speed comes from engine-status polling (2.5 s interval).
  const speedExpr = "(() => { const m = document.body.innerText.match(/Speed:\\s*([\\d.]+\\s*tok\\/s)/); return m ? m[1] : null; })()";
  await cdp.waitFor("speed reported", speedExpr, 10000).catch(() => {});
  const speed = await cdp.eval(speedExpr);
  step("speed read", { speed });

  // Every API request the UI made after Send, with start offset and duration (ms).
  const requests = await cdp.eval(`performance.getEntriesByType('resource')
    .filter(e => e.startTime >= ${sentAt} && e.name.includes('/api/'))
    .map(e => ({ path: new URL(e.name).pathname, start: Math.round(e.startTime - ${sentAt}), ms: Math.round(e.duration) }))`);

  console.log(JSON.stringify({ ok: true, origin, speed, steps, requests }));
  cdp.ws.close();
}

main().catch((err) => {
  console.log(JSON.stringify({ ok: false, error: String(err), steps }));
  process.exit(1);
});
