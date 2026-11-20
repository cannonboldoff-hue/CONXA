#!/bin/bash
# Test tool: with the "note" Test inputs card already open, set value from file, uncheck guard, Run test, wait, collect evidence.
# Usage: notecase2.sh <label> <valuefile> [waitSeconds]
T=$(dirname "$0"); E=${EV:-$T/../evidence/WF-011}; P=${CDP:-49440}; F="$TEMP/conxa-e2e/fixtures.err.log"; D="node $T/studio_driver.mjs $P"
OFF=$(wc -l < $F)
J=$(python -c "import json,sys;print(json.dumps(open(sys.argv[1],encoding='utf-8',newline='').read()))" "$2")
node $T/cdp_eval.mjs $P "(()=>{const t=document.querySelector('[id^=ti-]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(t,$J);t.dispatchEvent(new Event('input',{bubbles:true}));return 'set len '+t.value.length+' now '+t.value.length})()"
$D eval "(()=>{const c=[...document.querySelectorAll('main input[type=checkbox]')].find(x=>x.closest('label')?.innerText.startsWith('Ask before'));if(c&&c.checked)c.click();return 1})()" >/dev/null
$D click "Run test" | head -c 100; echo
sleep ${3:-40}
$D eval "document.querySelector('main').innerText.slice(-600)" | tee $E/$1.result.txt
echo; tail -n +$((OFF+1)) $F | grep "__log" | sed 's/.*GET //; s/ HTTP.*//' | python -c "import sys,urllib.parse;[print(urllib.parse.unquote(l.strip())) for l in sys.stdin]" | tee $E/$1.fixture.txt
