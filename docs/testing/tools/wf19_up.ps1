# WF-19: start the MOCK stack: mock Clerk :8110, mock LLM :8130, mock-auth cloud backend :8120 (auth ON, own data dir, mock provider key).
# usage: wf19_up.ps1 [-Down]. Work dir: %TEMP%\wf19. Never touches the normal backend on :8000.
param([switch]$Down)
$Root = (Resolve-Path "$PSScriptRoot\..\..\..").Path; $W = "$env:TEMP\wf19"; New-Item -ItemType Directory -Force $W | Out-Null
Get-CimInstance Win32_Process | Where-Object { $c = $_.CommandLine; $c -and ($c -match 'wf19_mock_clerk|wf19_mock_llm|wf19_backend') -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($Down) { return }
$env:WF19_KEY = "$W\mock_key.pem"
Start-Process python -ArgumentList "`"$Root\docs\testing\tools\wf19_mock_clerk.py`" 8110" -WindowStyle Hidden -RedirectStandardOutput "$W\clerk.log" -RedirectStandardError "$W\clerk.err"
Start-Process python -ArgumentList "`"$Root\docs\testing\tools\wf19_mock_llm.py`" 8130" -WindowStyle Hidden -RedirectStandardOutput "$W\llm.log" -RedirectStandardError "$W\llm.err"
Start-Sleep 2
$env:CONXA_ENV = "dev"; $env:CONXA_DEV_SKIP_AUTH = "0"; $env:SKILL_AUTH_REQUIRED = "true"
$env:SKILL_CLERK_ISSUER = "http://127.0.0.1:8110"; $env:SKILL_CLERK_JWKS_URL = "http://127.0.0.1:8110/.well-known/jwks.json"; $env:CLERK_SECRET_KEY = "mock-secret"
$env:SKILL_DATA_DIR = "$W\cloud-data"; $env:CONXA_STUDIO_HOME = "$W\cloud-home"; $env:CONXA_ADMIN_TOKEN = "wf19-admin"
$env:LLM_FREE_API_KEYS = "mock-key"; $env:LLM_FREE_ENDPOINT = "http://127.0.0.1:8130/v1"; $env:LLM_FREE_PROVIDER = "mockllm"
$env:PYTHONPATH = "$Root\docs\testing\tools\wf19_site"
Start-Process python -ArgumentList "`"$Root\docs\testing\tools\wf19_backend.py`" 8120" -WorkingDirectory "$Root\conxa-cloud\backend" -WindowStyle Hidden -RedirectStandardOutput "$W\backend.log" -RedirectStandardError "$W\backend.err"
function Probe($u) { try { (Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 3).StatusCode } catch { 0 } }
$b = 0; for ($i = 0; $i -lt 40 -and $b -ne 200; $i++) { Start-Sleep 2; $b = Probe "http://127.0.0.1:8120/healthz" }
"clerk=$(Probe 'http://127.0.0.1:8110/.well-known/jwks.json') llm_port=8130 backend=$b"
