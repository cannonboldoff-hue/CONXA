#!/usr/bin/env python3
"""WF-21 phase 6: classify saved sign-in session files as encrypted / plaintext WITHOUT printing contents (only a class, size, first-8-bytes hex of non-JSON). usage: wf21_f_sessions.py <dir>..."""
import os, sys, json, glob
res = []
for d in sys.argv[1:]:
    for f in sorted(glob.glob(os.path.join(os.path.expanduser(d), "**", "*"), recursive=True)):
        if not os.path.isfile(f) or not (f.endswith((".json", ".bak", ".sealed", ".enc")) and ("auth" in f.replace("\\", "/").split("/")[-2:-1][0] or "sessions" in f or "state" in os.path.basename(f))): continue
        b = open(f, "rb").read()
        kind = "?"
        try:
            j = json.loads(b.decode("utf-8"))
            if isinstance(j, dict) and ("cookies" in j or "origins" in j): kind = "PLAINTEXT storageState (cookies/origins readable)"
            elif isinstance(j, dict) and any(k in j for k in ("iv", "ciphertext", "ct", "tag", "enc", "data", "v")) and not ("cookies" in j): kind = "JSON envelope (keys: %s)" % sorted(j)[:6]
            else: kind = "JSON other (keys: %s)" % (sorted(j)[:6] if isinstance(j, dict) else type(j).__name__)
        except Exception:
            kind = "BINARY/non-JSON (head %s) -> encrypted/sealed" % b[:8].hex()
        res.append((f.replace(os.path.expanduser("~"), "~"), len(b), kind))
from collections import Counter
for r in res: print(r[2][:60].ljust(62), r[1], r[0][-90:])
print(Counter(r[2].split(" (")[0] for r in res))
