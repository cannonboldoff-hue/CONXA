#!/bin/bash
# logs every change of the llm_usage KV with UTC time (ms). Usage: kvwatch.sh <outfile>
F=$(ls /c/Users/kiran/.conxa-build-studio-dev/data/kv/llm_usage/*.json | head -1); last=""
while true; do cur=$(cat "$F" 2>/dev/null | python -c "import sys,json;d=json.load(sys.stdin);print(d['input_tokens'],d['output_tokens'],d['requests'])" 2>/dev/null); if [ "$cur" != "$last" ] && [ -n "$cur" ]; then echo "$(date -u +%H:%M:%S.%3N) $cur" >> "$1"; last="$cur"; fi; sleep 0.5; done
