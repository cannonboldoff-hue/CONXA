#!/bin/bash
# usage: wf18_cmd.sh <workdir> <id> <tool> '<argsjson>'   (appends a command for the daemon)  | wf18_cmd.sh <workdir> quit
d=$1; if [ "$2" = quit ]; then echo '{"quit":true}' >> $d/cmds.jsonl; else echo "{\"id\":\"$2\",\"tool\":\"$3\",\"args\":${4:-{\}}}" >> $d/cmds.jsonl; fi
