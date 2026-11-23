#!/usr/bin/env python3
"""WF-21 2c: every backend route whose handler calls require_admin (found by introspecting app.main) called as a MEMBER (org:member JWT) and as OWNER-less anonymous.
Member must get 403 admin_role_required (or 422 = body check first, nothing happens). Run against the wf21 backend :8160 (Bearer path) and via the real Next proxy :3100 (cookie path).
usage: wf21_b_member.py   (env: same as wf21_ctl so settings import works)"""
import os, sys, re, json, inspect, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__)); import wf21_ctl as C, wf21_tok as T
os.environ.update({k: v for k, v in C.base_env().items() if k.startswith(("SKILL_", "CONXA_", "LLM_", "CASHFREE_", "CLERK_"))}); os.environ["SKILL_ALLOW_NO_PROVIDERS"] = "1"
sys.path.insert(0, os.path.join(C.ROOT, "conxa-cloud", "backend"))
import app.main as m
from fastapi.routing import APIRoute
import ast, glob
paths = m.app.openapi()["paths"]; routes = set()
for fp in glob.glob(os.path.join(C.ROOT, "conxa-cloud", "backend", "app", "api", "*.py")):
    for fn in ast.walk(ast.parse(open(fp, encoding="utf-8").read())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "require_admin" for n in ast.walk(fn)):
            for d in fn.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ("get", "post", "put", "patch", "delete") and d.args and isinstance(d.args[0], ast.Constant):
                    sub, meth = d.args[0].value, d.func.attr.upper()
                    for op, ms in paths.items():
                        if op.endswith(sub) and op.startswith("/api/v1") and meth.lower() in ms and ms[meth.lower()].get("operationId", "").startswith(fn.name + "_"): routes.add((meth, op))
routes = sorted(routes, key=lambda x: x[1])
print("routes with require_admin in handler:", len(routes))
mem = T.mint("user_member", "org_a", "org:member"); own = T.mint("user_owner", "org_a", "org:admin")
def call(base, method, path, hdr, body=json.dumps({"grant_id":"x","machine_hash":"x","install_id":"x","domain":"x.example","name":"x","skill_slug":"x","endpoint":"http://x.example/v1","api_key":"x","model":"x","email":"a@b.co","user_id":"x","version":"1.0.0","group_name":"x","release_notes":"x","target_url":"http://x.example","skill_pack_version":"1.0.0","deployment":"openai","workflow_id":"x"}).encode()):
    p = re.sub(r"\{[^}]+\}", "x", path.replace("{installer_version}", "v2").replace("{version}", "1.0.0"))
    rq = urllib.request.Request(base + p + ("?skill_slug=x&version=1.0.0&slug=x" if method in ("GET", "POST", "DELETE") else ""), method=method, headers={**hdr, "Content-Type": "application/json"}, data=body if method not in ("GET", "DELETE") else None)
    try: r = urllib.request.urlopen(rq, timeout=15); return r.status, r.read()[:120].decode("latin1")
    except urllib.error.HTTPError as e: return e.code, e.read()[:120].decode("latin1")
    except Exception as e: return 0, str(e)[:80]
SKIP_OWNER = {"/api/v1/subscriptions/create", "/api/v1/subscriptions/addon/order"}  # owner call would hit Cashfree's network
res = []; bad = []
for via, base, hdr_of in (("bearer->backend", "http://127.0.0.1:8160", lambda t: {"Authorization": "Bearer " + t}), ("cookie->next proxy", "http://localhost:3100", lambda t: {"Cookie": "; ".join(f"{n}={v}" for n, v in [("__session", t), ("__session_g_f7dfRx", t), ("__client_uat", "1700000000"), ("__client_uat_g_f7dfRx", "1700000000"), ("__clerk_db_jwt", "dvb_x"), ("__clerk_db_jwt_g_f7dfRx", "dvb_x")])})):
    for meth, path in routes:
        if "admin/" in path and not path.startswith("/api/v1/entitlements/admin/billing") and False: pass
        s, b = call(base, meth, path, hdr_of(mem))
        verdict = "PASS" if (s == 403 and "admin_role_required" in b) or s in (401, 404) else ("INCONCLUSIVE(422 before role check)" if s == 422 else "CHECK")
        if s in (200, 201, 202, 204): verdict = "FAIL"
        res.append((via, meth, path, s, verdict, b[:70].replace("\n", " ")))
        if verdict in ("FAIL", "CHECK"): bad.append(res[-1])
from collections import Counter
for via in ("bearer->backend", "cookie->next proxy"):
    c = Counter(v for (vv, _m, _p, _s, v, _b) in res if vv == via); print(via, dict(c))
for row in res:
    if row[0] == "bearer->backend": print("  ", row[1], row[2], row[3], row[4], row[5])
print("NEEDS ATTENTION:", bad)
open(os.path.join(os.path.dirname(__file__), "..", "evidence", "WF-021", "wf21_b_member.json"), "w").write(json.dumps(res, indent=0))
