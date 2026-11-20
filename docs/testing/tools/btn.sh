#!/bin/bash
# Click a Studio button by exact visible text, retrying up to ${WAIT:-60}s until it exists. Usage: btn.sh <cdp> "<text>"
J=$(python -I -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$2")
for i in $(seq 1 $(( ${WAIT:-60} / 2 ))); do
  r=$(node "$(dirname "$0")/cdp_eval.mjs" "$1" "(()=>{const b=[...document.querySelectorAll('button')].reverse().find(x=>x.innerText.trim()===$J&&!x.disabled);if(!b)return 'NO BUTTON';b.click();return 'clicked'})()")
  [ "$r" = '"clicked"' ] && break; sleep 2
done
echo "$r"
