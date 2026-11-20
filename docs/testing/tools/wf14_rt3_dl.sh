#!/bin/bash
# usage: dl3.sh <mode> [maxLoops]
T=/c/Users/kiran/Desktop/Conxa/docs/testing/tools; P=57877; E=/c/Users/kiran/Desktop/Conxa/docs/testing/evidence/WF-014/retest
curl -s -X POST "localhost:8097/mode?m=$1" >/dev/null
CNT='(document.querySelector("main").innerText.match(/Test passed|Test failed|didn.t finish/g)||[]).length'
WAIT=30 bash $T/btn.sh $P "Test it" >/dev/null; sleep 4
C0=$(node $T/cdp_eval.mjs $P "$CNT")
node $T/cdp_eval.mjs $P "(()=>{const c=[...document.querySelectorAll('main input[type=checkbox]')].find(x=>x.closest('label')?.innerText.startsWith('Ask before'));if(c&&c.checked)c.click();return 1})()" >/dev/null
bash $T/btn.sh $P "Run test"; date +%T
for i in $(seq 1 ${2:-60}); do sleep 2; v=$(node $T/cdp_eval.mjs $P "$CNT" 2>/dev/null); [ -n "$v" ] && [ "$v" -gt "${C0:-0}" ] && break; done; date +%T; sleep 2
node $T/cdp_eval.mjs $P "document.querySelector('main').innerText.slice(-1100)" | tee $E/R-dl-$1.result.txt
