#!/bin/bash
# Set the stand-in's export mode, run the download workflow's test, print verdict. Usage: wf14_dl.sh <mode> [waitSec]
T=$(dirname "$0"); P=64542; E=$T/../evidence/WF-014
curl -s -X POST "localhost:8097/mode?m=$1" >/dev/null
CNT='(document.querySelector("main").innerText.match(/Test passed|Test failed|didn.t finish/g)||[]).length'
WAIT=120 bash $T/btn.sh $P "Test it" >/dev/null; sleep 4
C0=$(node $T/cdp_eval.mjs $P "$CNT")
node $T/cdp_eval.mjs $P "(()=>{const c=[...document.querySelectorAll('main input[type=checkbox]')].find(x=>x.closest('label')?.innerText.startsWith('Ask before'));if(c&&c.checked)c.click();return 1})()" >/dev/null
bash $T/btn.sh $P "Run test"; date +%T
for i in $(seq 1 ${2:-60}); do sleep 2; [ "$(node $T/cdp_eval.mjs $P "$CNT")" -gt "$C0" ] && break; done; date +%T; sleep 2
node $T/cdp_eval.mjs $P "document.querySelector('main').innerText.slice(-900)" | tee $E/14.8d-$1.result.txt
