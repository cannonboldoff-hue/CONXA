# Restart MY Studio (dev lane). Usage: restart15.ps1 [CONXA_ACTION_TIMEOUT_MS value]. Kills only the process tree rooted at the conxa.ps1 launcher of the current Studio main window. Prints new electron pid.
param($Timeout)
$all=Get-CimInstance Win32_Process
$e=Get-Process electron -EA 0 | ? MainWindowTitle -eq 'Conxa Build Studio' | select -First 1
if($e){ $id=$e.Id; while($id){ $p=$all|?{$_.ProcessId -eq $id}; if(-not $p){break}; if($p.Name -eq 'powershell.exe' -and $p.CommandLine -like '*conxa.ps1*'){ taskkill /T /F /PID $p.ProcessId | Out-Null; break }; $id=$p.ParentProcessId } ; Start-Sleep 4 }
Remove-Item Env:CONXA_ACTION_TIMEOUT_MS -EA 0
if($Timeout){ $env:CONXA_ACTION_TIMEOUT_MS=$Timeout }
Set-Location C:\Users\kiran\Desktop\Conxa
Start-Process powershell -ArgumentList '-NoProfile','-File','scripts\conxa.ps1','dev','studio' -WindowStyle Minimized
for($i=0;$i -lt 40;$i++){ Start-Sleep 2; $n=Get-Process electron -EA 0 | ? MainWindowTitle -eq 'Conxa Build Studio' | select -First 1; if($n){ Start-Sleep 6; $n.Id; break } }
