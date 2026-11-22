# WF-19: start (or restart) ONLY Conxa Execute in an isolated profile against the MOCK stack. Prints CDP=<port>.
# usage: wf19_exec_up.ps1 [-Skip] [-Fresh] [-Dsf 0.5]   -Dsf: force a device scale factor (0.5 makes the window ~3200 css px wide so the run pane can be >=1280x720 css)   -Skip: dev skip-auth against :8000 (no mock sign-in); -Fresh: wipe the isolated profile first.
param([switch]$Skip, [switch]$Fresh, [string]$Dsf = "", [string]$Backend = "http://127.0.0.1:8120")
$Root = (Resolve-Path "$PSScriptRoot\..\..\..").Path; $W = "$env:TEMP\wf19"; $App = "$Root\conxa-execute\app"
Get-CimInstance Win32_Process | Where-Object { $c = $_.CommandLine; $c -and $c -match [regex]::Escape("$Root\conxa-execute") -and $_.Name -match '^(electron|node)(\.exe)?$' -and $_.CommandLine -notmatch 'vite' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
if ($Fresh) { Remove-Item -Recurse -Force "$W\exec-profile" -ErrorAction SilentlyContinue }
function Probe($u) { try { (Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 0 } }
if ((Probe "http://localhost:5175/") -ne 200) {
  Start-Process node -ArgumentList "`"$App\node_modules\vite\bin\vite.js`" --config renderer/vite.config.mts" -WorkingDirectory $App -WindowStyle Hidden -RedirectStandardOutput "$W\vite.log" -RedirectStandardError "$W\vite.err"
  for ($i = 0; $i -lt 30 -and (Probe "http://localhost:5175/") -ne 200; $i++) { Start-Sleep 1 }
}
$env:CONXA_RENDERER_URL = "http://localhost:5175"; Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$env:CONXA_EXECUTE_API_BASE_URL = $Backend
if ($Skip) { $env:CONXA_DEV_SKIP_AUTH = "1" } else {
  $env:CONXA_DEV_SKIP_AUTH = "0"; $env:CONXA_EXECUTE_CLERK_DOMAIN = "http://127.0.0.1:8110"; $env:CONXA_EXECUTE_CLERK_CLIENT_ID = "wf19-exec-client"
  $env:NODE_OPTIONS = "--require `"$($PSScriptRoot -replace '\\','/')/wf19_openext.js`""; $env:WF19_OPENEXT_LOG = "$W\openext.log"
}
$rt = "$W\rt"; $env:CONXA_EXECUTE_RUNTIME = "$rt\conxa\conxa-runtime.exe"; $env:CONXA_ENV = "dev"; $env:CONXA_DIR = "$rt\conxa"; $env:CONXA_DATA_DIR = "$rt\data"
$env:CONXA_APP_DIR = "$rt\conxa\conxa-app"; $env:CONXA_SKIP_SELF_UPDATE = "1"
Start-Process "$App\node_modules\electron\dist\electron.exe" -ArgumentList (@(".", "--user-data-dir=$W\exec-profile") + $(if ($Dsf) { @("--force-device-scale-factor=$Dsf") } else { @() })) -WorkingDirectory $App -RedirectStandardOutput "$W\exec.out" -RedirectStandardError "$W\exec.err"
$cdp = $null
for ($i = 0; $i -lt 60 -and -not $cdp; $i++) { Start-Sleep 2
  $pids = (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'electron.exe' -and $_.CommandLine -match 'conxa-execute' -and $_.CommandLine -notmatch '--type=' }).ProcessId
  if ($pids) { $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $pids -contains $_.OwningProcess -and $_.LocalAddress -eq '127.0.0.1' } | Select-Object -Expand LocalPort
    foreach ($p in $ports) { if ((Probe "http://127.0.0.1:$p/json/version") -eq 200) { $cdp = $p; break } } } }
"CDP=$cdp"
