#!/bin/bash
# loop wf14_run over folders: wf14_sizes.sh <scratchDirWinPath> dir1 dir2 ...  (prints last run verdict + server lines)
T=$(dirname "$0"); export CDP=64542; B="$1"; shift
BS=$(printf '\')
for d in "$@"; do
  WAIT=120 bash $T/btn.sh 64542 "Test it" >/dev/null; sleep 4
  bash $T/wf14_run.sh 14.8s-$d "${B}${BS}${d}" 60 >/dev/null
  echo "$d: $(grep -h '"msg":"execute_\(failed\|success\)"' ~/.conxa-build-studio-dev-i2/sandbox/data/logs/runtime.log | tail -1 | cut -c20-60,100-260)"
  cat $T/../evidence/WF-014/14.8s-$d.server.txt | cut -c1-110
done
