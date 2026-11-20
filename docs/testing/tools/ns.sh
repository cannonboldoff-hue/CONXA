#!/bin/bash
# New workflow via dialog. Usage: ns.sh <cdp> "<name>" "<url>" (dialog "New workflow" must be open; group WF14 rt)
D="node $(dirname "$0")/studio_driver.mjs $1"
$D eval "(()=>{const s=(id,v)=>{const t=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(t,v);t.dispatchEvent(new Event('input',{bubbles:true}))};s('nw-name','$2');s('nw-url','$3');const l=[...document.querySelectorAll('label')].find(x=>x.innerText.trim()==='WF14 rt');if(l)l.click();return 1})()"
sleep 1; bash $(dirname "$0")/btn.sh $1 "Create and start recording"
