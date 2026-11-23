#!/usr/bin/env python3
"""WF-21 phase 6: grep a build directory for the test secret values and key-shaped strings. usage: wf21_f_grep.py <dir> [<dir>...]"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__)); import wf21_ctl as C
vals = list(C.SECRETS.values()) + ["wf21-api-proxy-SECRET-88dd"]
vals = [v for v in vals if v not in ("wf21-admin-token",)] + ["wf21-admin-token"]
pats = {"sk_live/sk_test": r"sk_(live|test)_[A-Za-z0-9]{8,}", "sk-or / sk- key": r"\bsk-(or-|ant-|proj-)?[A-Za-z0-9_-]{24,}", "AWS": r"AKIA[0-9A-Z]{16}", "Google": r"AIza[0-9A-Za-z_-]{30,}",
        "PEM private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}", "Bearer literal": r"Bearer [A-Za-z0-9._-]{30,}", "postgres url": r"postgres(ql)?://[^\"' ]*:[^\"' @]+@",
        "pk_live (publishable, public by design)": r"pk_(live|test)_[A-Za-z0-9=]{10,}", "Cashfree": r"cfsk_[A-Za-z0-9_]+|TEST[0-9]{6,}"}
files = n = 0; hits = {}
for d in sys.argv[1:]:
    for r, _d, fs in os.walk(d):
        for f in fs:
            if not f.endswith((".js", ".json", ".html", ".css", ".txt", ".map", ".rsc", ".body", ".meta")): continue
            fp = os.path.join(r, f); files += 1
            try: t = open(fp, errors="replace").read()
            except Exception: continue
            n += len(t)
            for v in vals:
                if v in t: hits.setdefault("SECRET VALUE " + v, []).append(fp)
            for k, rx in pats.items():
                m = re.search(rx, t)
                if m: hits.setdefault(k + " -> " + m.group(0)[:20] + "...", []).append(fp)
print("scanned %d files, %.1f MB" % (files, n / 1e6))
for k, v in hits.items(): print("HIT", k, len(v), "files e.g.", v[0])
if not hits: print("no hits")
