# Sample memory + process counts every N s to a csv. Usage: powershell -File wf15_mem.ps1 <out.csv> [sec]
param($Out,$Sec=15)
"time,electronMB,nodeMB,chromeMB,electronN,nodeN,chromeN,logsMB" | Out-File $Out -Encoding ascii
while($true){
 $e=Get-Process electron -EA 0;$n=Get-Process node -EA 0;$c=Get-Process chrome -EA 0
 $lm=0; foreach($d in "$HOME\.conxa-dev\logs","$HOME\.conxa\logs","$HOME\.conxa-build-studio-dev\ogs"){ if(Test-Path $d){$lm+=(Get-ChildItem $d -Recurse -File -EA 0|Measure-Object Length -Sum).Sum} }
 "{0},{1},{2},{3},{4},{5},{6},{7}" -f (Get-Date -f HH:mm:ss),[int](($e|Measure WorkingSet64 -Sum).Sum/1MB),[int](($n|Measure WorkingSet64 -Sum).Sum/1MB),[int](($c|Measure WorkingSet64 -Sum).Sum/1MB),@($e).Count,@($n).Count,@($c).Count,[int]($lm/1MB) | Add-Content $Out
 Start-Sleep $Sec }
