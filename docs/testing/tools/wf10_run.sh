#!/bin/bash
# Test tool: reload Studio, press Test it + Run test on the open workflow, wait, print the last result text. Usage: wf10_run.sh [waitSeconds]
D="node $(dirname "$0")/studio_driver.mjs ${CDP:-61364}"
$D eval "location.reload()" >/dev/null 2>&1; sleep 6
$D eval "(()=>{const b=[...document.querySelectorAll('button')].filter(b=>b.innerText.trim()==='Test it');b[b.length-1].click()})()" >/dev/null; sleep 3
$D click "Run test" >/dev/null; sleep ${1:-45}
$D eval "document.querySelector('main').innerText.slice(-700)"
