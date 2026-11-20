#!/bin/bash
# Click a file input on the recording page until the native Open dialog shows, then fill it. Usage: pick.sh <cdp> <studioPid> <cssSel> <folderOrPath> [names|pipe]
T=$(dirname "$0")
PIDV=$2
has(){ powershell -NoProfile -Command "Add-Type -AssemblyName UIAutomationClient; \$r=[Windows.Automation.AutomationElement]::RootElement; foreach(\$w in \$r.FindAll('Children',[Windows.Automation.Condition]::TrueCondition)){ if(\$w.Current.ProcessId -eq $PIDV){ foreach(\$c in \$w.FindAll('Children',[Windows.Automation.Condition]::TrueCondition)){ if(\$c.Current.ClassName -eq '#32770'){'Y'} } } }"; }
for i in 1 2 3 4 5; do
  node $T/studio_driver.mjs $1 act "[{\"js\":\"document.querySelector('$3').focus();1\"},{\"press\":\"Space\"}]" | cut -c1-120
  sleep 3; [ "$(has)" = "Y" ] && break
done
powershell -NoProfile -File $T/filedlg.ps1 $2 "$4" "$5"
