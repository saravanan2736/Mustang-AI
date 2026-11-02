# M4 Phase G harness. Usage: see docs/milestone-4/M4_PHASEG_RELEASE_REPORT.md. Needs a scratch -DataRoot (never your real data).
param([string]$Exe, [string]$DataRoot, [string]$Repo)
$ErrorActionPreference = "Continue"
$env:MUSTANG_DESKTOP_DATA_ROOT = $DataRoot
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=9223"
$log = Join-Path $DataRoot "logs\desktop.log"
$mark = (Get-Content $log -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
function Cdp($expr) { Push-Location $Repo; $r = (cmd /c "node scripts/e2e/cdp_eval.mjs `"$($expr -replace '"','\"')`" 2>nul"); Pop-Location; $r }
$api = "async (method, path, body) => { const i = await window.__TAURI_INTERNALS__.invoke('backend_info'); const t0 = performance.now(); const r = await fetch(i.base_url + path, { method, headers: { 'X-Mustang-Token': i.token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }); const b = await r.json().catch(() => null); return { status: r.status, ms: Math.round(performance.now() - t0), body: b }; }"
function Api($method, $path, $body = "null") { (Cdp "($api)('$method', '$path', $body)") | ConvertFrom-Json }
$app = Start-Process -FilePath $Exe -PassThru
$d = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $d -and -not (Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Skip $mark | Select-String "ready$")) { Start-Sleep -Milliseconds 500 }
Start-Sleep 4
$list = Api "GET" "/api/models"
"models listed: " + (($list.body | ForEach-Object filename) -join ", ")
$big = "qwen2.5-1.5b-instruct-q4_k_m.gguf"; $small = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
$e = Api "GET" "/api/models/$big/estimate?n_ctx=4096"
"estimate $big -> $($e.status)"
$l = Api "POST" "/api/models/load" "{ model_name: '$big', n_gpu_layers: 99, n_ctx: 4096 }"
"load $big by file name -> $($l.status) in $($l.ms) ms; path=$($l.body.model_path); state=$($l.body.state)"
$c = Api "POST" "/v1/chat/completions" "{ messages: [{ role: 'user', content: 'Name three horse breeds.' }], max_tokens: 64, stream: false }"
"chat on $big -> $($c.status) in $($c.ms) ms: $($c.body.choices[0].message.content -replace '\s+',' ')"
$s = Api "POST" "/api/models/load" "{ model_name: '$small', n_gpu_layers: 99, n_ctx: 4096 }"
"switch to $small -> $($s.status) in $($s.ms) ms; state=$($s.body.state)"
$b = Api "POST" "/api/models/load" "{ model_name: '$big', n_gpu_layers: 99, n_ctx: 4096 }"
"switch back to $big -> $($b.status) in $($b.ms) ms"
$null = Start-Process -FilePath $Exe -ArgumentList "--quit"; $app.WaitForExit(60000) | Out-Null
"quit: exited=$($app.HasExited) leftovers=" + @(Get-Process mustang-desktop, llama-server -ErrorAction SilentlyContinue).Count
