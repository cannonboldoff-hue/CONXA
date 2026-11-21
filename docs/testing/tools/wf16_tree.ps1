# list all descendants of a process id (arg 1): used to see what one runtime process left running
param([int]$Root)
$all = Get-CimInstance Win32_Process
$seen = @($Root); $q = @($Root)
while ($q.Count) { $p = $q[0]; $q = $q[1..($q.Count)] | Where-Object { $_ }; foreach ($c in ($all | Where-Object { $_.ParentProcessId -eq $p })) { $seen += $c.ProcessId; $q += $c.ProcessId } }
$all | Where-Object { $seen -contains $_.ProcessId -and $_.ProcessId -ne $Root } | ForEach-Object { "$($_.ProcessId) $($_.Name)" }
