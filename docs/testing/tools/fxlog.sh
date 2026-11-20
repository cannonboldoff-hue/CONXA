#!/bin/bash
# Test tool: print fixture-server __log lines (decoded) after line offset $1 (default 0). Usage: fxlog.sh [offset]
tail -n +$((${1:-0}+1)) "$TEMP/conxa-e2e/fixtures.err.log" | grep "__log" | sed 's/.*GET //; s/ HTTP.*//' | PYTHONIOENCODING=utf-8 python -c "import sys,urllib.parse;[print(urllib.parse.unquote(l.strip())) for l in sys.stdin]"
