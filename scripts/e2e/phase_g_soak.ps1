# M4 Phase G harness. Usage: see docs/milestone-4/M4_PHASEG_RELEASE_REPORT.md. Needs a scratch -DataRoot (never your real data).
param([string]$Exe, [string]$DataRoot, [string]$Repo, [int]$Minutes = 25, [string]$Csv)
$ErrorActionPreference = "Continue"
$env:MUSTANG_DESKTOP_DATA_ROOT = $DataRoot
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=9223"
$log = Join-Path $DataRoot "logs\desktop.log"
function Cdp($expr) { Push-Location $Repo; $r = (cmd /c "node scripts/e2e/cdp_eval.mjs `"$($expr -replace '"','\"')`" 2>nul"); Pop-Location; $r }
$api = "async (method, path, body) => { const i = await window.__TAURI_INTERNALS__.invoke('backend_info'); const r = await fetch(i.base_url + path, { method, headers: { 'X-Mustang-Token': i.token, 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, body: await r.json().catch(() => null) }; }"
function Api($method, $path, $body = "null") { (Cdp "($api)('$method', '$path', $body)") | ConvertFrom-Json }
function Sample($cycle, $note) {
  $app = Get-Process mustang-desktop -ErrorAction SilentlyContinue | Select-Object -First 1
  $kids = Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','llama-server.exe' -and ($_.ParentProcessId -eq $app.Id -or (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue).ParentProcessId -eq $app.Id) }
  $py = $kids | Where-Object Name -eq 'python.exe' | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } | Sort-Object WorkingSet64 -Descending | Select-Object -First 1
  $ll = $kids | Where-Object Name -eq 'llama-server.exe' | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue } | Select-Object -First 1
  $wv = Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" | Where-Object { $_.CommandLine -match 'ai\.mustang\.desktop' } | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
  $vram = (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits) -as [int]
  [pscustomobject]@{
    cycle = $cycle; t = [int]((Get-Date) - $script:start).TotalSeconds; note = $note
    shell_mb = [math]::Round($app.WorkingSet64 / 1MB); shell_handles = $app.HandleCount
    backend_mb = [math]::Round($py.WorkingSet64 / 1MB); backend_handles = $py.HandleCount
    llama_mb = if ($ll) { [math]::Round($ll.WorkingSet64 / 1MB) } else { 0 }; llama_procs = @($kids | Where-Object Name -eq 'llama-server.exe').Count
    webview_mb = [math]::Round(($wv | Measure-Object WorkingSet64 -Sum).Sum / 1MB); vram_mb = $vram
  }
}
$app = Start-Process -FilePath $Exe -PassThru
$d = (Get-Date).AddSeconds(90); while ((Get-Date) -lt $d -and -not (Get-Content $log -ErrorAction SilentlyContinue | Select-String "ready$")) { Start-Sleep -Milliseconds 500 }
Start-Sleep 5
$script:start = Get-Date; $rows = @(); $errors = 0; $cycle = 0; $chats = 0
$rows += Sample 0 "idle"
while (((Get-Date) - $script:start).TotalMinutes -lt $Minutes) {
  $cycle++
  $ctx = if ($cycle % 2) { 4096 } else { 2048 }
  $l = Api "POST" "/api/models/load" "{ model_name: 'qwen2.5-0.5b-instruct-q4_k_m.gguf', n_gpu_layers: 99, n_ctx: $ctx }"
  if ($l.status -ne 200) { $errors++; "cycle $cycle load failed: $($l.status)" }
  for ($k = 0; $k -lt 5; $k++) {
    $c = Api "POST" "/v1/chat/completions" "{ messages: [{ role: 'user', content: 'Give one interesting fact about horses (#$cycle-$k).' }], max_tokens: 96, stream: false }"
    if ($c.status -ne 200) { $errors++; "cycle $cycle chat failed: $($c.status)" } else { $chats++ }
  }
  # One streamed chat through the UI path per cycle.
  $s = Cdp "(async () => { const i = await window.__TAURI_INTERNALS__.invoke('backend_info'); const r = await fetch(i.base_url + '/api/chat/completions', { method: 'POST', headers: { 'X-Mustang-Token': i.token, 'Content-Type': 'application/json' }, body: JSON.stringify({ conversation_id: 'soak-$cycle', messages: [{ role: 'user', content: 'Count from 1 to 40.' }], max_tokens: 160 }) }); const rd = r.body.getReader(); let n = 0; while (true) { const { done } = await rd.read(); if (done) break; n++; } return r.status + ':' + n; })()"
  if ($s -notmatch '^"200:') { $errors++; "cycle $cycle stream failed: $s" } else { $chats++ }
  if ($cycle % 4 -eq 0) { $u = Api "POST" "/api/models/unload"; if ($u.status -ne 200) { $errors++ } }
  $rows += Sample $cycle "ctx $ctx"
}
$rows | Export-Csv -NoTypeInformation -Path $Csv
$first = $rows | Select-Object -Skip 1 -First 3; $last = $rows | Select-Object -Last 3
"cycles=$cycle chats=$chats errors=$errors duration_min=$([math]::Round(((Get-Date) - $script:start).TotalMinutes, 1))"
foreach ($f in 'shell_mb','shell_handles','backend_mb','backend_handles','webview_mb') {
  $a = ($first | Measure-Object $f -Average).Average; $b = ($last | Measure-Object $f -Average).Average
  "{0,-16} early {1,8:N0}  late {2,8:N0}  change {3,6:N1}%" -f $f, $a, $b, (($b - $a) / [math]::Max($a, 1) * 100)
}
"max llama-server processes at once: " + ($rows | Measure-Object llama_procs -Maximum).Maximum
$null = Start-Process -FilePath $Exe -ArgumentList "--quit"; $app.WaitForExit(60000) | Out-Null
"quit: exited=$($app.HasExited) leftovers=" + @(Get-Process mustang-desktop, llama-server -ErrorAction SilentlyContinue).Count
