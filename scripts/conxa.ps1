<#
.SYNOPSIS
  conxa — the single dev/prod switch (Windows).

.DESCRIPTION
  Exports CONXA_ENV plus the isolated path roots for the chosen lane, then
  launches the target. Dev and Prod use completely separate trees
  ($HOME\.conxa-dev vs $HOME\.conxa, %APPDATA%\Conxa-Dev vs %APPDATA%\Conxa,
  .conxa-build-studio-dev vs .conxa-build-studio) so they coexist on one machine
  with zero interference. Endpoints/keys come from the matching .env.<env> file,
  loaded automatically by CONXA_ENV.

  With no arguments, defaults to dev + studio (Build Studio in the dev lane).

.EXAMPLE
  .\scripts\conxa.ps1
  .\scripts\conxa.ps1 backend
  .\scripts\conxa.ps1 prod studio
#>
param(
  [Parameter(Position=0)][string]$Arg1,
  [Parameter(Position=1)][string]$Arg2,
  # Extra Studio beside a running one (studio target only): own data dirs, user-data dir, vite port 5174+N.
  [int]$Instance = 0
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

$ValidEnvs    = @("dev", "prod")
$ValidTargets = @("backend", "frontend", "studio", "runtime", "env")

# ── Resolve env + target from 0/1/2 positional args ──────────────────────────
if (-not $Arg1 -and -not $Arg2) {
  $Env = "dev"; $Target = "studio"
} elseif ($Arg1 -and -not $Arg2) {
  if ($ValidEnvs -contains $Arg1)    { $Env = $Arg1; $Target = "studio" }
  elseif ($ValidTargets -contains $Arg1) { $Env = "dev"; $Target = $Arg1 }
  else { throw "Unknown argument: '$Arg1' (expected dev|prod|backend|frontend|studio|runtime|env)" }
} else {
  $Env = $Arg1; $Target = $Arg2
}

if ($ValidEnvs -notcontains $Env)       { throw "Unknown env: '$Env' (expected dev|prod)" }
if ($ValidTargets -notcontains $Target) { throw "Unknown target: '$Target' (expected backend|frontend|studio|runtime|env)" }

# ── The single switch ────────────────────────────────────────────────────────
$env:CONXA_ENV = $Env

# ── Isolated path roots + dev conveniences per lane ──────────────────────────
if ($Env -eq "dev") {
  if (-not $env:CONXA_DIR)            { $env:CONXA_DIR            = "$HOME\.conxa-dev" }
  if (-not $env:CONXA_DATA_DIR)       { $env:CONXA_DATA_DIR       = "$env:APPDATA\Conxa-Dev" }
  if (-not $env:CONXA_APP_DIR)        { $env:CONXA_APP_DIR        = "$env:CONXA_DIR\conxa-app" }
  if (-not $env:CONXA_STUDIO_HOME)    { $env:CONXA_STUDIO_HOME    = "$HOME\.conxa-build-studio-dev" }
  if (-not $env:CONXA_UPDATE_CHANNEL) { $env:CONXA_UPDATE_CHANNEL = "dev" }
  # Skip Clerk login in Build Studio — dev lane only.
  if (-not $env:CONXA_DEV_SKIP_AUTH) { $env:CONXA_DEV_SKIP_AUTH = "1" }
  # No .env.dev ships in the repo, so the standalone cloud backend
  # (`conxa.ps1 dev backend`) has zero LLM provider keys by default. Without
  # this, Settings() refuses to even start ("No LLM providers enabled").
  # Studio's own Python backend already gets this from main.js when Electron
  # spawns it — set it here too so `conxa.ps1 dev backend` boots standalone
  # the same way. Dev-only: prod must have real provider keys.
  if (-not $env:SKILL_ALLOW_NO_PROVIDERS) { $env:SKILL_ALLOW_NO_PROVIDERS = "1" }
  # Vision-anchor LLM calls on/off switch. Skips the anchor_vision proxy call
  # entirely and falls straight to DOM-only anchors (existing, already-recoverable
  # fallback path in build.py) instead of waiting on it. Flip to "0" once the
  # configured providers are healthy again; leave "1" while they're down/flaky.
  if (-not $env:CONXA_DISABLE_VISION_ANCHORS) { $env:CONXA_DISABLE_VISION_ANCHORS = "1" }
  # Runtime / Test Skill: skip self-update polling in dev.
  if (-not $env:CONXA_SKIP_SELF_UPDATE) { $env:CONXA_SKIP_SELF_UPDATE = "1" }
  # Studio sandbox caps recovery at tier 2 (deterministic, no agent handoff).
  if (-not $env:CONXA_MAX_RECOVERY_TIER) { $env:CONXA_MAX_RECOVERY_TIER = "2" }
  # Test Skill always runs the LOCALLY BUILT runtime — never a download, ever, in
  # Dev. Run scripts\build-runtime-local.ps1 and scripts\build-app-local.ps1 (once
  # after cloning, then again after editing bootstrap.js/server.js/etc.) — they
  # write to <CONXA_STUDIO_HOME>\deps\, which conxa_compile/conxa_runtime.py's
  # resolve_runtime_dir() resolves for a non-frozen (unpacked) Studio checkout.
} else {
  if (-not $env:CONXA_DIR)            { $env:CONXA_DIR            = "$HOME\.conxa" }
  if (-not $env:CONXA_DATA_DIR)       { $env:CONXA_DATA_DIR       = "$env:APPDATA\Conxa" }
  if (-not $env:CONXA_APP_DIR)        { $env:CONXA_APP_DIR        = "$env:CONXA_DIR\conxa-app" }
  if (-not $env:CONXA_STUDIO_HOME)    { $env:CONXA_STUDIO_HOME    = "$HOME\.conxa-build-studio" }
  if (-not $env:CONXA_UPDATE_CHANNEL) { $env:CONXA_UPDATE_CHANNEL = "stable" }
}

if ($Instance -gt 0) {
  if ($Target -ne "studio") { throw "-Instance only applies to the studio target" }
  $sfx = "-i$Instance"
  foreach ($v in "CONXA_DIR", "CONXA_DATA_DIR", "CONXA_STUDIO_HOME") {
    $cur = [Environment]::GetEnvironmentVariable($v, "Process")
    [Environment]::SetEnvironmentVariable($v, "$cur$sfx", "Process")
  }
  $env:CONXA_APP_DIR = "$env:CONXA_DIR\conxa-app"
  $env:CONXA_USER_DATA_DIR = "$env:CONXA_DATA_DIR\electron-userdata"
  $env:CONXA_RENDERER_PORT = [string](5174 + $Instance)
}

Write-Host "-- conxa [$($env:CONXA_ENV)] -> $Target --------------------------------"
Write-Host "  CONXA_DIR            = $($env:CONXA_DIR)"
Write-Host "  CONXA_DATA_DIR       = $($env:CONXA_DATA_DIR)"
Write-Host "  CONXA_STUDIO_HOME    = $($env:CONXA_STUDIO_HOME)"
Write-Host "  CONXA_UPDATE_CHANNEL = $($env:CONXA_UPDATE_CHANNEL)"
Write-Host "  env file             = .env.$($env:CONXA_ENV)"
if ($Env -eq "dev") {
  Write-Host "  CONXA_DEV_SKIP_AUTH         = $($env:CONXA_DEV_SKIP_AUTH)"
  Write-Host "  CONXA_DISABLE_VISION_ANCHORS = $($env:CONXA_DISABLE_VISION_ANCHORS)"
}
Write-Host "-----------------------------------------------------------------"

$port = if ($env:PORT) { $env:PORT } else { "8000" }

switch ($Target) {
  "env"      { break }
  "backend"  {
    Set-Location "$Root\conxa-cloud\backend"
    if ($Env -eq "dev") { python -m uvicorn app.main:app --reload --host 127.0.0.1 --port $port }
    else                { python -m uvicorn app.main:app --host 127.0.0.1 --port $port }
  }
  "frontend" {
    Set-Location "$Root\conxa-cloud\frontend"
    if ($Env -eq "dev") { npm run dev } else { npm run start }
  }
  "studio"   {
    Set-Location "$Root\conxa-builder\electron"
    # `npm run dev` runs cleanup-dev.js, which kills every Studio under this checkout — skip it for extra instances.
    if ($Instance -gt 0) { npx concurrently "npm:dev:renderer" "npm:dev:electron" } else { npm run dev }
  }
  "runtime"  { Set-Location "$Root\runtime"; node server.js }
}
