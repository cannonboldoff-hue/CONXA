<#
.SYNOPSIS
  One-shot E2E environment: stop old stack, rebuild test runtime, start cloud backend + Studio + fixture server.
  Prints exactly one final line: CDP=<port> backend=200 fixtures=200
  NOTE: detached children inherit the caller stdout pipe; callers must redirect output to a file (> up.out), not pipe it.
#>
param([switch]$SkipRuntimeBuild)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $env:TEMP "conxa-e2e"; New-Item -ItemType Directory -Force $Log | Out-Null

# 1. stop old stack (matched by command line, scoped to this repo / known servers)
Get-CimInstance Win32_Process | Where-Object {
  $c = $_.CommandLine; if (-not $c) { return $false }
  ($_.Name -match '^(electron|node|python|py|npm|cmd|powershell)(\.exe)?$' -and $c -match [regex]::Escape("$Root\conxa-builder")) -or
  ($c -match 'uvicorn app\.main:app') -or ($c -match 'http\.server 8099') -or ($c -match 'backend\.py' -and $c -match 'conxa')
} | Where-Object { $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# 2. rebuild test runtime (dev studio home)
$env:CONXA_STUDIO_HOME = "$env:USERPROFILE\.conxa-build-studio-dev"
if (-not $SkipRuntimeBuild) { & "$Root\scripts\build-app-local.ps1" *> "$Log\runtime-build.log"; if ($LASTEXITCODE) { throw "runtime build failed, see $Log\runtime-build.log" } }

# 3. cloud backend (no --reload)
$env:CONXA_ENV = "dev"; $env:CONXA_DEV_SKIP_AUTH = "1"; $env:SKILL_ALLOW_NO_PROVIDERS = "1"
Start-Process python -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory "$Root\conxa-cloud\backend" -WindowStyle Hidden -RedirectStandardOutput "$Log\backend.out.log" -RedirectStandardError "$Log\backend.err.log"

# 4. fixture server
Start-Process python -ArgumentList "-m http.server 8099 --directory docs\testing\fixtures" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput "$Log\fixtures.out.log" -RedirectStandardError "$Log\fixtures.err.log"

# 5. Studio via conxa.ps1 (dev lane)
Start-Process powershell -ArgumentList "-NoProfile -File `"$Root\scripts\conxa.ps1`" dev studio" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput "$Log\studio.out.log" -RedirectStandardError "$Log\studio.err.log"

function Probe($url) { try { (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 0 } }

# 6. CDP port is random each start and not logged: probe loopback ports listened on by electron
$cdp = $null
for ($i = 0; $i -lt 90 -and -not $cdp; $i++) {
  Start-Sleep -Seconds 2
  $pids = (Get-Process electron -ErrorAction SilentlyContinue).Id
  if ($pids) {
    $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $pids -contains $_.OwningProcess -and $_.LocalAddress -eq '127.0.0.1' } | Select-Object -Expand LocalPort
    foreach ($p in $ports) { if ((Probe "http://127.0.0.1:$p/json/version") -eq 200) { $cdp = $p; break } }
  }
}
if (-not $cdp) { throw "Studio CDP port not found; see $Log\studio.err.log" }

$b = 0; $f = 0
for ($i = 0; $i -lt 60 -and ($b -ne 200 -or $f -ne 200); $i++) {
  if ($b -ne 200) { $b = Probe "http://127.0.0.1:8000/healthz" }
  if ($f -ne 200) { $f = Probe "http://127.0.0.1:8099/" }
  if ($b -ne 200 -or $f -ne 200) { Start-Sleep -Seconds 2 }
}
"CDP=$cdp backend=$b fixtures=$f"
