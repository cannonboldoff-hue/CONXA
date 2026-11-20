#!/bin/bash
# Test tool: run the open "note" workflow once with a value from a file. Usage: notecase.sh <label> <valuefile> [waitSeconds] ; evidence -> evidence/WF-011/<label>.*
T=$(dirname "$0"); E=${EV:-$T/../evidence/WF-011}; P=${CDP:-49440}; F="$TEMP/conxa-e2e/fixtures.err.log"
D="node $T/studio_driver.mjs $P"
OFF=$(wc -l < $F)
$D eval "[...document.querySelectorAll('button')].filter(b=>b.innerText.trim()==='Test it').pop().click()" >/dev/null; sleep 6
J=$(python -c "import json,sys;print(json.dumps(open(sys.argv[1],encoding='utf-8',newline='').read()))" "$2")
node $T/cdp_eval.mjs $P "(()=>{const t=document.querySelector('[id^=ti-]');Object.getOwnPropertyDescriptor(t.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(t,$J);t.dispatchEvent(new Event('input',{bubbles:true}));return t.value.length})()"
$D eval "(()=>{const c=[...document.querySelectorAll('main input[type=checkbox]')].find(x=>x.closest('label')?.innerText.startsWith('Ask before'));if(c&&c.checked)c.click();return 1})()" >/dev/null
$D click "Run test" | head -c 100; echo
sleep ${3:-45}
$D eval "document.querySelector('main').innerText.slice(-700)" | tee $E/$1.result.txt
tail -n +$((OFF+1)) $F | grep "__log" | sed 's/.*GET //; s/ HTTP.*//' | PYTHONIOENCODING=utf-8 python -c "import sys,urllib.parse;[print(urllib.parse.unquote(l.strip())) for l in sys.stdin]" | tee $E/$1.fixture.txt
