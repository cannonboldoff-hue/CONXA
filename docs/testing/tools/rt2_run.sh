#!/bin/bash
# WF-014 retest 2 (instance 2 only). Fill Test inputs card, Run test, wait, print result + server log + elapsed.
# Usage: rt2_run.sh <label> '<value>' [waitSec]   (value "-" = leave the field as is; card must be open)
T=$(dirname "$0"); E=$T/../evidence/WF-014/retest; P=${CDP:-52610}; L=$E/filesrv.log
N0=$(wc -l < $L)
if [ "$2" != "-" ]; then
J=$(python -I -c "import json,sys;print(json.dumps(sys.argv[1]))" "$2")
node $T/cdp_eval.mjs $P "(()=>{const t=document.querySelector('[id^=ti-]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(t,$J);t.dispatchEvent(new Event('input',{bubbles:true}));return t.value})()"
fi
CNT='(document.querySelector("main").innerText.match(/Test passed|Test failed|didn.t finish/g)||[]).length'
C0=$(node $T/cdp_eval.mjs $P "$CNT")
S=$(date +%s); date +%T
bash $T/btn.sh $P "Run test"
for i in $(seq 1 $(( ${3:-90} / 2 ))); do sleep 2; [ "$(node $T/cdp_eval.mjs $P "$CNT")" -gt "$C0" ] && break; done
echo "elapsed $(( $(date +%s) - S ))s"; sleep 3
{ echo "elapsed_s=$(( $(date +%s) - S - 3 ))"; node $T/cdp_eval.mjs $P "document.querySelector('main').innerText.slice(-1500)"; } | tee $E/$1.result.txt
echo "--- server log (new lines)"; tail -n +$((N0+1)) $L | cut -c1-230 | tee $E/$1.server.txt
