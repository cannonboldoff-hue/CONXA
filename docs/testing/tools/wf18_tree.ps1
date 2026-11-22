# descendants of runtime pids + any Chrome-for-Testing/chromium-1217 processes (command line mentions deps\chromium or user-data-dir under conxa)
param([string]$roots="")
$all=Get-CimInstance Win32_Process
$set=@{}; foreach($r in ($roots -split ',' | ? {$_})){ $set[[int]$r]=1 }
do { $n=0; foreach($p in $all){ if($set.ContainsKey([int]$p.ParentProcessId) -and -not $set.ContainsKey([int]$p.ProcessId)){ $set[[int]$p.ProcessId]=1; $n++ } } } while($n -gt 0)
$all | ? { $set.ContainsKey([int]$_.ProcessId) -or ($_.CommandLine -match 'chromium|chrome-for-testing|ms-playwright' ) } | % { "$($_.ProcessId) ppid=$($_.ParentProcessId) $($_.Name) tree=$($set.ContainsKey([int]$_.ProcessId))" }
