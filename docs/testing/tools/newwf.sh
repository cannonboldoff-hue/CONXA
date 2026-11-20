#!/bin/bash
# Test tool: create a workflow via the chat Draft card and start recording. Usage: newwf.sh "<name>" "<url>"
D="node $(dirname "$0")/studio_driver.mjs ${CDP:-50671}"
$D eval "location.hash='#/chat'" >/dev/null; sleep 2
$D eval "(()=>{const t=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'Create a workflow named $1 in the group Probe group. Start at $2');t.dispatchEvent(new Event('input',{bubbles:true}));return 1})()" >/dev/null; sleep 1
$D eval "document.querySelector('button[aria-label=Send]').click()" >/dev/null; sleep 30
$D eval "location.reload()" >/dev/null 2>&1; sleep 6
$D click "Create and start recording"; sleep 8
$D pages
