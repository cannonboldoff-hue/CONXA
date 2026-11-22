# WF-19 process census: Execute electron (+children), runtimes, Playwright/portable Chromium (not the owner's Chrome), node/python. usage: wf19_procs.ps1 [-Detail]
param([switch]$Detail)
$all = Get-CimInstance Win32_Process
$ex = $all | Where-Object { $_.Name -match '^electron' -and $_.CommandLine -match 'conxa-execute' }
$rt = $all | Where-Object { $_.Name -match '^conxa-runtime' }
$pw = $all | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -match 'chromium' -and $_.ExecutablePath -notmatch 'Google\Chrome' -and $_.Name -match 'chrom' }
$pwTop = $pw | Where-Object { $_.CommandLine -notmatch '--type=' }
"execute_electron_procs=$(@($ex).Count) runtimes=$(@($rt).Count) pw_chromium_procs=$(@($pw).Count) pw_chromium_browsers=$(@($pwTop).Count) node=$(@($all | ? Name -eq 'node.exe').Count) python=$(@($all | ? Name -eq 'python.exe').Count) electron_all=$(@($all | ? Name -match '^electron').Count)"
if ($Detail) { $rt | % { "RT $($_.ProcessId) parent=$($_.ParentProcessId) $($_.ExecutablePath)" }; $pwTop | % { "PWBROWSER $($_.ProcessId) parent=$($_.ParentProcessId) $($_.ExecutablePath) :: $($_.CommandLine.Substring(0,[Math]::Min(200,$_.CommandLine.Length)))" } }
