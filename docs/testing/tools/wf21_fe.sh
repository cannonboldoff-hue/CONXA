#!/bin/bash
# WF-21: start/stop the built dashboard (next start) on :3100 against the wf21 backend :8160, with a MOCK Clerk (local JWT key, networkless).
# usage: wf21_fe.sh start|stop   (build first: see handoff)
FE=/c/Users/kiran/Desktop/Conxa/conxa-cloud/frontend; TOOLS=/c/Users/kiran/Desktop/Conxa/docs/testing/tools
case "$1" in
start)
  cd $FE
  export NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="pk_test_d2YyMS1tb2NrLmNsZXJrLmFjY291bnRzLmRldiQ=" CLERK_SECRET_KEY=sk_test_wf21ClerkSecretKEY API_ORIGIN=http://127.0.0.1:8160 NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:8160 CONXA_API_PROXY_SECRET=wf21-api-proxy-SECRET-88dd CLERK_TELEMETRY_DISABLED=1
  export CLERK_API_URL=http://127.0.0.1:8163
  nohup python $TOOLS/wf21_bapi.py 8163 > "$TEMP/wf21/bapi.log" 2>&1 &
  nohup npx next start -p 3100 -H localhost > "$TEMP/wf21/fe_start.log" 2>&1 &
  sleep 5; echo started ;;
stop)
  for P in $(netstat -ano | grep LISTENING | grep -E ":8163 " | awk '{print $5}' | sort -u); do taskkill //F //T //PID $P; done
  for P in $(netstat -ano | grep "LISTENING" | grep -E ":3100 " | awk '{print $5}' | sort -u); do taskkill //F //T //PID $P; done ;;
esac
