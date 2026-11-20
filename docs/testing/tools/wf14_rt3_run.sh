#!/bin/bash
# Fill the open Test inputs card, uncheck "Ask before", Run test, wait, print result. Usage: wf14_run.sh <label> '<value>' [waitSec]  (CDP env, default 64542)
T=/c/Users/kiran/Desktop/Conxa/docs/testing/tools; E=/c/Users/kiran/Desktop/Conxa/docs/testing/evidence/WF-014/retest; P=${CDP:-57877}; L=$E/filesrv.log
N0=$(wc -l < $L)
J=$(python -I -c "import json,sys;print(json.dumps(sys.argv[1]))" "$2")
node /c/Users/kiran/Desktop/Conxa/docs/testing/tools/cdp_eval.mjs $P "(()=>{const t=document.querySelector('[id^=ti-]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(t,$J);t.dispatchEvent(new Event('input',{bubbles:true}));const c=[...document.querySelectorAll('main input[type=checkbox]')].find(x=>x.closest('label')?.innerText.startsWith('Ask before'));if(c&&c.checked)c.click();return t.value})()"
CNT='(document.querySelector("main").innerText.match(/Test passed|Test failed/g)||[]).length'
C0=$(node /c/Users/kiran/Desktop/Conxa/docs/testing/tools/cdp_eval.mjs $P "$CNT")
bash /c/Users/kiran/Desktop/Conxa/docs/testing/tools/btn.sh $P "Run test"
for i in $(seq 1 ${3:-90}); do sleep 2; [ "$(node /c/Users/kiran/Desktop/Conxa/docs/testing/tools/cdp_eval.mjs $P "$CNT" 2>/dev/null)" -gt "${C0:-0}" ] && break; done
sleep 3
node /c/Users/kiran/Desktop/Conxa/docs/testing/tools/cdp_eval.mjs $P "document.querySelector('main').innerText.slice(-900)" | tee $E/$1.result.txt
echo "--- server log (new lines)"; tail -n +$((N0+1)) $L | tee $E/$1.server.txt
