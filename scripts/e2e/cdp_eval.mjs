// Evaluate one JavaScript expression in the running desktop app's WebView2 (test runs only:
// start the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<CDP_PORT>).
//
//   node scripts/e2e/cdp_eval.mjs "<expression>"     -> prints the JSON-encoded result
//
// Promises are awaited. Exit code 1 if the expression throws.

const CDP_PORT = Number(process.env.CDP_PORT ?? 9223);
const expression = process.argv[2];

const targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
const page = targets.find((t) => t.type === "page" && /^(http:\/\/tauri\.localhost|http:\/\/127\.0\.0\.1:5173)/.test(t.url));
if (!page) {
  console.log(JSON.stringify({ error: "no app page target" }));
  process.exit(1);
}
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve) => (ws.onopen = resolve));
ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression, awaitPromise: true, returnByValue: true } }));
const reply = await new Promise((resolve) => (ws.onmessage = (m) => resolve(JSON.parse(m.data))));
ws.close();
const result = reply.result ?? {};
if (result.exceptionDetails) {
  console.log(JSON.stringify({ error: result.exceptionDetails.exception?.description ?? "evaluation failed" }));
  process.exit(1);
}
console.log(JSON.stringify(result.result?.value ?? null));
