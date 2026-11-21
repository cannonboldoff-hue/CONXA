#!/bin/bash
# open a workflow in the sidebar by exact first line. Usage: openwf.sh <cdp> "<name>"
J=$(python -I -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$2")
node "$(dirname "$0")/cdp_eval.mjs" "$1" "(()=>{const e=[...document.querySelectorAll('aside *,nav *')].filter(x=>x.children.length<4&&x.innerText&&x.innerText.split('\n')[0].trim()===$J).sort((a,b)=>a.innerText.length-b.innerText.length)[0];if(!e)return 'none';e.click();return location.hash})()"
