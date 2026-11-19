#!/bin/bash
# Test tool: run the open workflow once and save evidence. Usage: wf10_case.sh <label> [waitSeconds]
T=$(dirname "$0"); E=$T/../evidence/WF-010; mkdir -p $E
bash $T/wf10_run.sh ${2:-60} | tail -c 450 | tee $E/$1.result
L=/c/Users/kiran/.conxa-build-studio-dev/sandbox/data/logs/runtime.log
R=$(grep -o '"run_id":"r_[a-z0-9_]*"' $L | tail -1 | cut -d'"' -f4)
grep "$R" $L | grep -E 'execute_(success|fail|error)|stopped|fault|llm|recover' | cut -c1-400 | tee $E/$1.log
python $T/wf10_tel.py $R | grep -v '"proof"' | tee $E/$1.tel
