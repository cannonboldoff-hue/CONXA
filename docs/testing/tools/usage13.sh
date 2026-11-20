#!/bin/bash
# prints llm_usage KV (input/output tokens, requests) + ai_usage_credits.used + proxy POST count
K=$(cat /c/Users/kiran/.conxa-build-studio-dev/data/kv/llm_usage/*.json)
U=$(curl -s -m 10 http://localhost:8000/api/v1/entitlements/current | python -c "import sys,json;print(json.load(sys.stdin)['meters']['ai_usage_credits']['used'])")
N=$(grep -c "POST /api/v1/llm/proxy" $TEMP/conxa-e2e/backend.out.log)
echo "$(date +%T) kv=$K credits_used=$U proxyPosts=$N"
