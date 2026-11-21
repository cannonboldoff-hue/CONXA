#!/bin/bash
# Start the saucedemo test (WF4 B shop checkout r3), signing in again when asked. Returns when the test is running. env CDP
T=$(dirname "$0"); P=${CDP:?}
node $T/studio_driver.mjs $P eval "location.hash='#/w/62513831-9ded-4829-8e87-fb0461f8806e'" >/dev/null; sleep 3
bash $T/btn.sh $P "Test it" >/dev/null; sleep 2; bash $T/btn.sh $P "Run test" >/dev/null; echo "run clicked $(date +%T)"
for i in $(seq 1 30); do sleep 2
  s=$(node $T/cdp_eval.mjs $P "(()=>{const t=document.querySelector('main').innerText;return t.slice(-400).includes('Sign in and test again')?'signin':t.slice(-400).includes('Stop test')?'running':'wait'})()")
  case "$s" in *running*) echo running; exit 0;; *signin*)
    bash $T/btn.sh $P "Sign in and test again" >/dev/null; sleep 8
    node $T/studio_driver.mjs $P ext "(()=>{const s=(id,v)=>{const t=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(t,v);t.dispatchEvent(new Event('input',{bubbles:true}))};s('user-name','standard_user');s('password','secret_sauce');document.getElementById('login-button').click();return 1})()" >/dev/null; sleep 6
    bash $T/btn.sh $P "I’m signed in" >/dev/null; sleep 4; bash $T/btn.sh $P "Save anyway" >/dev/null 2>&1 ;;
  esac
done
