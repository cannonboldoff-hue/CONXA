#!/bin/bash
# Test tool: hand-record 3 records on rows.html via OS input. Usage: rec3.sh   (recording page must be open)
T=$(dirname "$0"); R="node $T/rec.mjs ${CDP:-50671}"
one(){ $R "[{\"click\":\"#firstName\"},{\"type\":\"$1\"},{\"click\":\"#lastName\"},{\"type\":\"$2\"},{\"click\":\"#userEmail\"},{\"type\":\"$3\"},{\"click\":\"#age\"},{\"type\":\"$4\"},{\"click\":\"#salary\"},{\"type\":\"$5\"},{\"click\":\"#department\"},{\"type\":\"$6\"},{\"click\":\"#add\"},{\"wait\":700}]" >/dev/null; }
one Alice Adams alice@example.com 31 5000 Sales
one Bob Brown bob@example.com 42 6000 Support
one Cara Clark cara@example.com 28 7000 Legal
