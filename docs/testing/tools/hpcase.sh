#!/bin/bash
# Test tool: on the open loop workflow, open Test it, set table rows from a file, Run test, auto "Run the last N for real" if paused, wait, collect evidence.
# Usage: hpcase.sh <label> <rowsfile> [maxSeconds]   env: CDP, EV (evidence dir)
T=$(dirname "$0"); P=${CDP:-49177}; EV=${EV:-$T/../evidence/WF-011/retest}; F="$TEMP/conxa-e2e/fixtures.err.log"; D="node $T/studio_driver.mjs $P"
OFF=$(wc -l < $F); S=$(date +%s)
$D eval "[...document.querySelectorAll('button')].filter(b=>b.innerText.trim()==='Test it').pop().click()" >/dev/null; sleep 6
J=$(python -c "import json,sys;print(json.dumps(open(sys.argv[1],encoding='utf-8',newline='').read()))" "$2")
node $T/cdp_eval.mjs $P "(()=>{const t=document.querySelector('textarea[id^=ti-]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,$J);t.dispatchEvent(new Event('input',{bubbles:true}));return t.value.length})()"
$D eval "(()=>{const c=[...document.querySelectorAll('main input[type=checkbox]')].find(x=>x.closest('label')?.innerText.startsWith('Ask before'));if(c&&c.checked)c.click();return 1})()" >/dev/null
$D click "Run test" | head -c 80; echo
for i in $(seq 1 $(( ${3:-120} / 5 ))); do sleep 5; t=$($D eval "document.querySelector('main').innerText.slice(-700)")
  echo "$t" | grep -q "Run the last" && { $D eval "[...document.querySelectorAll('button')].find(b=>/^Run the last/.test(b.innerText.trim())).click()" >/dev/null; continue; }
  echo "$t" | grep -q "Test running" || break; done
echo "elapsed $(( $(date +%s) - S ))s" | tee $EV/$1.time.txt
echo "$t" | python -c "import sys,json;print(json.loads(sys.stdin.read()))" | tail -25 | tee $EV/$1.result.txt
bash $T/fxlog.sh $OFF | tee $EV/$1.fixture.txt | cut -c1-260
