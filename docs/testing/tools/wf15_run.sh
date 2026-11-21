#!/bin/bash
# Run the open workflow's test via Studio and time it. Usage: wf15_run.sh <label> [maxWaitSec] ; env CDP. Writes evidence/${EVD:-WF-015}/<label>.result.txt
T=$(dirname "$0"); E=$T/../evidence/${EVD:-WF-015}; P=${CDP:?}
CNT='(()=>{const t=document.querySelector("main").innerText;const s=t.slice(t.lastIndexOf("Test inputs"));return /Test passed|Test failed|Test stopped|ended the test|Test paused|Test cancel/i.test(s)?1:0})()'
bash $T/btn.sh $P "Run test" >/dev/null
S=$(date +%s); echo "$1 start $(date +%T)"
for i in $(seq 1 $(( ${2:-600} / 2 ))); do sleep 2; [ "$(node $T/cdp_eval.mjs $P "$CNT")" = 1 ] && break; done
D=$(( $(date +%s) - S )); sleep 2
node $T/cdp_eval.mjs $P "document.querySelector('main').innerText.slice(-1500)" > $E/$1.result.txt
V=$(grep -o 'Test passed[^\]*\|Test failed[^\]*' $E/$1.result.txt | tail -1)
echo "$1 end secs=$D verdict=$V"
echo "$1 secs=$D $V" >> $E/runs.log
