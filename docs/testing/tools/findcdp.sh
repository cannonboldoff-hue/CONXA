#!/bin/bash
# print CDP port of the Studio electron main process (pid arg)
for p in $(powershell -NoProfile -c "Get-NetTCPConnection -State Listen -OwningProcess $1 | % LocalPort"); do curl -s -m1 localhost:$p/json/version | grep -q Browser && echo $p; done
