#!/usr/bin/env python3
r"""WF-21 phase 3 (LOCAL, NOT a clean VM): publish one existing skill package to the wf21 backend as org_a admin, release it, then run the runtime's own
`bootstrap.js sync` (the command the installer runs) in an isolated CONXA_DIR that has only pack.json staged, and compare every synced file's SHA-256 with the
source package. Also greps the published/synced package for credentials. Needs wf21_ctl.py up and wf21_e_update.py run once (stages %TEMP%\wf21\upd).
usage: wf21_c_sync.py"""
import os, sys, json, base64, hashlib, shutil, subprocess, urllib.request, re
sys.path.insert(0, os.path.dirname(__file__)); import wf21_ctl as C, wf21_tok as T
API = f"http://127.0.0.1:{C.BP}"; U = os.path.join(C.W, "upd"); RT = os.path.join(U, "rt"); out = []
SRC = os.path.expanduser("~/.conxa-build-studio-dev/data/skill-packs/wrk_local/e2e-2026-11-16-19291d9d/wf1-a-wikipedia-control-r4-71600c18")
VER = sys.argv[1] if len(sys.argv) > 1 else "0.1.1"; SLUG = "wf21-sync-" + VER.replace(".", "-")


def p(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)


def call(method, path, body=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    rq = urllib.request.Request(API + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        r = urllib.request.urlopen(rq, timeout=30); return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


sha = lambda b: hashlib.sha256(b).hexdigest()
files = {f: open(os.path.join(SRC, f), "rb").read() for f in os.listdir(SRC) if os.path.isfile(os.path.join(SRC, f))}
p("source package files:", {f: sha(b)[:12] for f, b in files.items()})
# credential grep on the package BEFORE publishing (spec: packages hold no credentials / auth.json)
hits = [(f, w) for f, b in files.items() for w in ("secret_sauce", "SuperSecretPassword!", "admin123", "storageState", "auth.json", "cookies") if w.encode() in b]
p("credential grep on package files:", hits or "no hits")
tok = T.mint("user_owner", "org_a", "org:admin")
st, r = call("POST", "/api/v1/workflows/v2/skill-packs/upload", {"skill_slug": SLUG, "skill_pack_version": VER, "group_id": "wf21grp", "group_name": "", "workflow_name": "wf21 sync probe",
             "target_url": "http://127.0.0.1:8099/", "files": [{"path": f"wf21grp/{SLUG}/{f}", "content_base64": base64.b64encode(b).decode()} for f, b in files.items()]}, tok)
p("publish ->", st, {k: r.get(k) for k in ("slug", "version", "files_written", "sync_url")} if isinstance(r, dict) else r)
if st != 200:
    sys.exit(1)
st2, r2 = call("POST", f"/api/v1/workflows/v2/releases/{VER}/release?skill_slug={SLUG}", {}, tok); p("release ->", st2, str(r2)[:150])
# staged pack.json = exactly what the installer stages (workspace id, sync endpoint, token). No skills.
data = os.path.join(U, "data_sync"); cd = os.path.join(U, "conxa")
shutil.rmtree(data, ignore_errors=True); shutil.rmtree(os.path.join(cd, "skill-packs"), ignore_errors=True)
ws = r["slug"]; pdir = os.path.join(cd, "skill-packs", ws); os.makedirs(pdir)
json.dump({"workspace_id": ws, "display_name": "WF21", "sync_endpoint": API + r["sync_url"], "sync_token": r["sync_token"], "skills": [], "tracking": r["tracking"]}, open(os.path.join(pdir, "pack.json"), "w"))
p("staged ONLY pack.json at", pdir, "->", os.listdir(pdir))
env = {k: v for k, v in os.environ.items() if not k.startswith(("CONXA_", "SKILL_"))}
env.update({"CONXA_ENV": "prod", "CONXA_DIR": cd, "CONXA_DATA_DIR": data, "CONXA_APP_DIR": cd + "/conxa-app", "CONXA_API_URL": API, "CONXA_UPDATE_CHANNEL": "stable", "CONXA_SKIP_SELF_UPDATE": "1"})
pr = subprocess.run(["node", os.path.join(RT, "host", "bootstrap.js"), "sync"], env=env, cwd=U, capture_output=True, text=True, timeout=120)
p("bootstrap sync exit", pr.returncode, "| stdout:", pr.stdout.strip()[:300].replace("\n", " / "), "| stderr:", pr.stderr.strip()[:300].replace("\n", " / "))
# find synced files
found = {}
for root, _d, fs in os.walk(cd):
    for f in fs:
        if root.replace("\\", "/").find(SLUG) >= 0 and f in files:
            found[f] = os.path.join(root, f)
p("synced files on disk:", {f: os.path.relpath(pth, cd) for f, pth in found.items()})
ok = bool(found) and all(f in found and sha(open(found[f], "rb").read()) == sha(files[f]) for f in files)
p("SHA-256 of every synced file matches the published source:", ok)
pk = json.load(open(os.path.join(pdir, "pack.json"))); p("pack.json after sync: skills =", pk.get("skills"), "last_synced set:", bool(pk.get("last_synced")))
# tamper check: ask delta with a wrong token -> must refuse
st3, r3 = call("GET", r["sync_url"] + "?since={}", None, "wrong-token"); p("delta with a wrong token ->", st3)
leak = [root for root, _d, fs in os.walk(cd) for f in fs if f in ("auth.json",) or f.endswith("storageState.json")]
p("auth.json / storageState anywhere under the install dir:", leak or "none")
open(os.path.join(os.path.dirname(__file__), "..", "evidence", "WF-021", "wf21_c_sync.txt"), "w").write("\n".join(out) + "\n")
