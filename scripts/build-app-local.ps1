<#
.SYNOPSIS
  Build the real (obfuscated) runtime app layer locally and drop it exactly
  where a real download would — so Test Skill uses the identical code path as
  Production. Dev only — never touches Production.

.DESCRIPTION
  Runs the SAME pipeline as build-runtime-app.yml: file list + obfuscation
  profiles come from runtime/app-layer-files.json (the single source of truth
  both pipelines consume — they cannot drift), the check_app_layer_files.js
  guard runs first, and javascript-obfuscator gets identical flags per profile.
  version.json has the same shape (app_version, min_host, built_at, per-file
  sha256). The only differences: it writes straight into
  <CONXA_STUDIO_HOME>\deps\conxa-app\<version>\ instead of publishing a GitHub
  Release, and min_host is "0.0.0" so any host exe will load it.

  This is the SAME folder conxa_compile/conxa_runtime.py's _bootstrap_app_dir()
  scans in Production (after a real download) — Test Skill's ensure_test_sandbox()
  copies whatever's here into the sandbox and creates the `current` junction
  itself (stage_runtime_payload(), same code either way); this script does NOT
  create a `current` junction here, matching the flat shape a downloaded app
  layer has before staging.

  Any other version already under deps\conxa-app\ is deleted first, so there's
  always exactly one — deterministic, and avoids competing with a real download
  that might land there too (e.g. from clicking "Build Installer").

  Run this after editing server.js, run.js, resolver.js, recovery.js, or any
  other app-layer file listed in runtime/app-layer-files.json. For bootstrap.js
  or anything else bundled into the exe itself, use build-runtime-local.ps1
  instead — bootstrap.js runs only from inside conxa-runtime.exe; nothing on
  disk ever loads a conxa-app/ copy of it.

.EXAMPLE
  .\scripts\build-app-local.ps1
#>
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root "runtime"

$StudioHome = if ($env:CONXA_STUDIO_HOME) { $env:CONXA_STUDIO_HOME } else { "$HOME\.conxa-build-studio-dev" }
$DepsRoot = Join-Path $StudioHome "deps\conxa-app"

# Same guard CI runs before building: manifest coverage + require closure.
Write-Host "-- guard: app-layer-files.json ------------------------------------"
Push-Location $RuntimeDir
try { node check_app_layer_files.js; if ($LASTEXITCODE -ne 0) { throw "app-layer-files.json guard failed" } }
finally { Pop-Location }

$stamp = Get-Date -Format "yyyyMMddHHmmss"
$appVersion = "app-v0.0.0-local.$stamp"
$versionDest = Join-Path $DepsRoot $appVersion
New-Item -ItemType Directory -Force -Path $versionDest | Out-Null

$manifest = Get-Content (Join-Path $RuntimeDir "app-layer-files.json") -Raw | ConvertFrom-Json

Write-Host "-- obfuscating app layer -------------------------------------------"
$hashes = @{}
foreach ($entry in $manifest.files) {
  $f = $entry.name
  $src = Join-Path $RuntimeDir "app\$f"
  $out = Join-Path $versionDest $f
  # Flag sets mirror build-runtime-app.yml's "Obfuscate app JS files" step exactly;
  # per-profile rationale lives in app-layer-files.json's $comment.
  $obArgs = @($src, "--output", $out,
    "--compact", "true",
    "--identifier-names-generator", "mangled",
    "--string-array-rotate", "true",
    "--string-array-shuffle", "true",
    "--dead-code-injection", "false",
    "--debug-protection", "false")
  switch ($entry.profile) {
    "no-self-defending" {
      $obArgs += @("--self-defending", "false",
        "--string-array", "true",
        "--string-array-encoding", "rc4",
        "--string-array-threshold", "0.75")
    }
    "in-page" {
      $obArgs += @("--self-defending", "false",
        "--string-array", "false")
    }
    default {
      $obArgs += @("--self-defending", "true",
        "--string-array", "true",
        "--string-array-encoding", "rc4",
        "--string-array-threshold", "0.75")
    }
  }
  Push-Location $RuntimeDir
  try { npx --yes javascript-obfuscator @obArgs } finally { Pop-Location }
  $hashes[$f] = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
}

$versionJson = @{
  app_version = $appVersion
  min_host    = "0.0.0"
  built_at    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  capabilities = @("test_guard", "outputs", "run_if", "stop_before", "date_offset", "ask_missing", "templated_click", "loop_guard", "variant_branch")  # see build-runtime-app.yml
  files       = $hashes
} | ConvertTo-Json -Depth 5
# No-BOM write: Windows PowerShell 5.1's `Out-File -Encoding utf8` prepends a BOM, which makes
# bootstrap.js's JSON.parse of version.json fail (silently — "no usable app layer").
[System.IO.File]::WriteAllText((Join-Path $versionDest "version.json"), $versionJson, (New-Object System.Text.UTF8Encoding($false)))

# Only one version needed locally — drop any other (a stale local build, or a
# real download from Build Installer) so resolution is unambiguous.
Get-ChildItem $DepsRoot -Directory | Where-Object { $_.Name -ne $appVersion } |
  ForEach-Object { Remove-Item $_.FullName -Recurse -Force }

Write-Host "-----------------------------------------------------------------"
Write-Host "App layer rebuilt: $versionDest ($($hashes.Count) modules)"
if (-not (Test-Path (Join-Path $StudioHome "deps\conxa-runtime"))) {
  Write-Host ""
  Write-Host "No host exe staged yet — also run .\scripts\build-runtime-local.ps1"
  Write-Host "before Test Skill will work."
}
Write-Host ""
Write-Host 'Next Test Skill run will re-stage the sandbox with this build (see ensure_test_sandbox() in conxa_compile/conxa_runtime.py).'
Write-Host "-----------------------------------------------------------------"
