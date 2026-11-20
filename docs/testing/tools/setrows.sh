#!/bin/bash
# Test tool: set the Test inputs table textarea (React-safe) from a file. Usage: setrows.sh <file>
T=$(dirname "$0"); J=$(python -c "import json,sys;print(json.dumps(open(sys.argv[1],encoding='utf-8').read()))" "$1")
node $T/cdp_eval.mjs ${CDP:-50671} "(()=>{const t=document.querySelector('textarea[id^=ti-]');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,$J);t.dispatchEvent(new Event('input',{bubbles:true}));return t.value.length})()"
