"""Test tool: patch a Studio workflow's saved skill. Usage: wf10_patch.py <workflow_id> navurl <url>  (sets every navigate step's url; bumps edited_at so the test copy rebuilds)"""
import json, glob, os, sys, time
H = os.path.expanduser("~/.conxa-build-studio-dev/data")
wid, op, arg = sys.argv[1:4]
w = json.load(open(f"{H}/workflows/{wid}.json", encoding="utf-8")); sid = w["skill_id"]
paths = [f"{H}/skills/{sid}.json"] + [p for p in glob.glob(f"{H}/kv/skills/*.json") if sid in open(p, encoding="utf-8").read(400)]
for p in paths:
    d = json.load(open(p, encoding="utf-8"))
    for s in d["skills"][0]["steps"]:
        if op == "navurl" and s.get("action") == "navigate": s["url"] = arg
    d["meta"]["version"] = int(d["meta"].get("version", 1)) + 1
    json.dump(d, open(p, "w", encoding="utf-8"), indent=1)
now = time.time()
for p in [f"{H}/workflows/{wid}.json"] + glob.glob(f"{H}/kv/workflows/*.json"):
    j = json.load(open(p, encoding="utf-8"))
    if j.get("id") == wid: j["edited_at"] = now; json.dump(j, open(p, "w", encoding="utf-8"), indent=1)
print("patched", paths)
