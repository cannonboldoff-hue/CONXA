#!/bin/bash
# Endurance loop: wf15_loop.sh <fromN> <toN> ; env CDP. Stops on first non-pass. Logs per-run stats to evidence/WF-015/loop.csv
T=$(dirname "$0"); E=$T/../evidence/WF-015; H=$HOME/.conxa-build-studio-dev/sandbox/data
for n in $(seq $1 $2); do
  L=$(printf run%02d $n)
  bash $T/btn.sh $CDP "Test it" >/dev/null; sleep 3
  bash $T/wf15_run.sh $L 900 | tail -1
  P=$(powershell -NoProfile -c "\$e=Get-Process electron -EA 0;\$n=Get-Process node -EA 0;\$c=Get-Process chrome -EA 0;'{0},{1},{2},{3},{4}' -f [int]((\$e|measure WorkingSet64 -sum).Sum/1MB),[int]((\$n|measure WorkingSet64 -sum).Sum/1MB),@(\$e).Count,@(\$n).Count,@(\$c).Count")
  echo "$L,$(date +%T),$(grep -o "secs=[0-9]*" $E/runs.log | tail -1),$(stat -c %s $H/logs/runtime.log),$(ls $H/runs | wc -l),$(du -sm $H/runs | cut -f1),$P" >> $E/loop.csv
  grep -q "Test passed" $E/$L.result.txt || { echo "STOP $L not passed"; break; }
done
