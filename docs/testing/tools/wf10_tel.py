"""Test tool: per-step telemetry of the newest (or given) run from the Studio's local tracking store. Usage: wf10_tel.py [run_id]"""
import json, glob, sys, os
D = r"C:\Users\kiran\.conxa-build-studio-dev\data\kv\tracking\wrk_local"
files = sorted(glob.glob(D + r"\*.json"), key=os.path.getmtime)
rid = sys.argv[1] if len(sys.argv) > 1 else None
if not rid:
    for f in reversed(files):
        t = open(f, encoding="utf-8").read()
        if '"run_id"' in t: rid = json.loads(t)[-1]["run_id"]; break
ev = []
for f in files[-40:]:
    for b in json.load(open(f, encoding="utf-8")):
        if b.get("run_id") == rid: ev += b["events"]
print("run", rid)
for e in sorted(ev, key=lambda e: e["ts"]):
    k = {a: b for a, b in e.items() if a not in ("ts", "virtualScroll")}
    print(json.dumps(k)[:260])
