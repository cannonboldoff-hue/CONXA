#!/bin/bash
# the "person" in the runtime's Chrome window: wf18_person.sh tick|done|both   (finds window by title, checks foreground first)
T=$(dirname "$0"); L=$(powershell -NoProfile -File $T/wf18_wins.ps1 "Prove you are human" | head -1); PID=${L%%|*}
[ -z "$PID" ] && { echo "no window"; exit 1; }
powershell -NoProfile -File $T/wf18_fg.ps1 focus $PID
case $1 in tick|both) powershell -NoProfile -File $T/wf18_fg.ps1 click $PID 83 297; sleep 1;; esac
case $1 in done|both) powershell -NoProfile -File $T/wf18_fg.ps1 click $PID 1481 159;; esac
