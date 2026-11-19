#!/bin/bash
# Test tool: summarise the newest test run from the sandbox runtime log. Usage: wf10_log.sh [outfile]
L=/c/Users/kiran/.conxa-build-studio-dev/sandbox/data/logs/runtime.log
R=$(grep -o '"run_id":"r_[a-z0-9_]*"' $L | tail -1 | cut -d'"' -f4)
echo "run=$R"
grep "\"$R\"" $L | grep -v "tracking_\|resolve_trace" | python -c "
import sys,json,collections
c=collections.Counter()
for l in sys.stdin:
    try: j=json.loads(l)
    except: continue
    m=j.get('msg')
    if m=='test_phase': print(j['ts'][11:19], j.get('phase'), j.get('ms')); continue
    c[m]+=1
    if any(k in m for k in ('tier','recover','cascade','llm','token','branch','fault','fail','error','dismiss','fork','probe','intercept')) : print(j['ts'][11:19], l.strip()[:300])
print(dict(c))
" | tee ${1:-/dev/null}
