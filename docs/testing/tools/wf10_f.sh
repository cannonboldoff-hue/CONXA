#!/bin/bash
# Test tool: build a variant, run it once, show fixture hits since start. Usage: wf10_f.sh <variant> <label> [wait]
T=$(dirname "$0"); export CDP=${CDP:-61364}
python $T/wf10_build.py $1 >/dev/null || exit 1
F="$TEMP/conxa-e2e/fixtures.err.log"; N=$(wc -l < "$F")
bash $T/wf10_case.sh $2 ${3:-60}
tail -n +$((N+1)) "$F" | grep __log | tee $T/../evidence/WF-010/$2.fixture
