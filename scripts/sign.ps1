# Mustang AI — Authenticode-sign one file (called by the Tauri bundler via signCommand).
#   MUSTANG_SIGN_CERT_THUMBPRINT  SHA-1 thumbprint of a code-signing certificate (CurrentUser\My)
#   MUSTANG_SIGN_TIMESTAMP_URL    RFC 3161 timestamp server (default http://timestamp.digicert.com)
param([Parameter(Mandatory = $true)][string]$File)
$ErrorActionPreference = "Stop"

$thumb = $env:MUSTANG_SIGN_CERT_THUMBPRINT
if (-not $thumb) { throw "MUSTANG_SIGN_CERT_THUMBPRINT is not set" }
$ts = if ($env:MUSTANG_SIGN_TIMESTAMP_URL) { $env:MUSTANG_SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }

$kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$signtool = Get-ChildItem -Path $kits -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw "signtool.exe not found under $kits (install the Windows SDK)" }

& $signtool.FullName sign /sha1 $thumb /fd SHA256 /tr $ts /td SHA256 /d "Mustang AI" $File
if ($LASTEXITCODE -ne 0) { throw "signtool failed for $File" }
& $signtool.FullName verify /pa /q $File
if ($LASTEXITCODE -ne 0) { throw "signature verification failed for $File" }
