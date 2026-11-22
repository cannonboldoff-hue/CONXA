# WF-17: restart ONLY Build Studio (dev lane). -Mock: no skip-auth, Clerk = local mock (:8110), BROWSER = fake. Prints CDP=<port>.
param([switch]$Mock)
$Root = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$Log = Join-Path $env:TEMP "conxa-e2e"
Get-CimInstance Win32_Process | Where-Object { $c = $_.CommandLine; $c -and $_.Name -match '^(electron|node|python|py|npm|cmd|powershell)(\.exe)?$' -and $c -match [regex]::Escape("$Root\conxa-builder") -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
$env:CONXA_STUDIO_HOME = "$env:USERPROFILE\.conxa-build-studio-dev"; $env:CONXA_ENV = "dev"
if ($Mock) { $env:CONXA_DEV_SKIP_AUTH = "0"; $env:CONXA_CLERK_DOMAIN = "http://127.0.0.1:8110"; $env:PYTHONPATH = (Join-Path $Root "docs/testing/tools/wf17_site") } else { $env:CONXA_DEV_SKIP_AUTH = "1" }
Start-Process powershell -ArgumentList "-NoProfile -File `"$Root\scripts\conxa.ps1`" dev studio" -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput "$Log\studio.out.log" -RedirectStandardError "$Log\studio.err.log"
function Probe($url) { try { (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 0 } }
$cdp = $null
for ($i = 0; $i -lt 90 -and -not $cdp; $i++) { Start-Sleep 2
  $pids = (Get-Process electron -ErrorAction SilentlyContinue).Id
  if ($pids) { $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $pids -contains $_.OwningProcess -and $_.LocalAddress -eq '127.0.0.1' } | Select-Object -Expand LocalPort
    foreach ($p in $ports) { if ((Probe "http://127.0.0.1:$p/json/version") -eq 200) { $cdp = $p; break } } } }
"CDP=$cdp"
