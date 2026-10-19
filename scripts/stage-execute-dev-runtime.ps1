<#
.SYNOPSIS
  Make Conxa Execute (dev) find the locally built runtime. Dev only.

.DESCRIPTION
  build-runtime-local.ps1 / build-app-local.ps1 write the host exe and app layer
  into <CONXA_STUDIO_HOME>\deps\ (Build Studio's flat layout). Execute's
  runtime_path.js instead looks for the installed layout a customer's NSIS
  installer produces:
    <CONXA_DIR>\conxa-runtime\current\conxa-runtime.exe
    <CONXA_DIR>\conxa-app\current\server.js
  CONXA_DIR defaults to ~\.conxa-dev (one of runtime_path.js's lookup roots, and
  the dev lane's CONXA_DIR in conxa.ps1).

  This points both `current` junctions at the newest build under deps\ — no
  copy. Re-run it after every build-runtime-local.ps1 / build-app-local.ps1,
  which delete the previous timestamped folder and so leave the junction dangling.

.EXAMPLE
  .\scripts\stage-execute-dev-runtime.ps1
#>
param()

$ErrorActionPreference = "Stop"

$StudioHome = if ($env:CONXA_STUDIO_HOME) { $env:CONXA_STUDIO_HOME } else { "$HOME\.conxa-build-studio-dev" }
$ConxaDir   = if ($env:CONXA_DIR) { $env:CONXA_DIR } else { "$HOME\.conxa-dev" }

function Set-Junction($Link, $Target) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Link) | Out-Null
  if (Test-Path $Link) { (Get-Item $Link -Force).Delete() }   # removes the junction only, never its target's contents
  New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null
}

function Get-Newest($Dir, $What) {
  $d = if (Test-Path $Dir) { Get-ChildItem $Dir -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 }
  if (-not $d) { throw "No $What build under $Dir - run .\scripts\$What-local.ps1 first." }
  $d.FullName
}

$runtime = Get-Newest (Join-Path $StudioHome "deps\conxa-runtime") "build-runtime"
$app     = Get-Newest (Join-Path $StudioHome "deps\conxa-app") "build-app"

Set-Junction (Join-Path $ConxaDir "conxa-runtime\current") $runtime
Set-Junction (Join-Path $ConxaDir "conxa-app\current") $app

Write-Host "Execute dev runtime staged in $ConxaDir"
Write-Host "  conxa-runtime\current -> $runtime"
Write-Host "  conxa-app\current     -> $app"
