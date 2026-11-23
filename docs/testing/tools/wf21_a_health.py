#!/usr/bin/env python3
"""WF-21 phase 1: healthz x50 latency, readyz, log scan, missing-secret / bad-DB / filesystem behaviours. Needs wf21_ctl.py up."""
import os, sys, time, statistics, json, subprocess, re, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
import wf21_ctl as C
B = f"http://127.0.0.1:{C.BP}"
out = []
def p(*a): s = " ".join(str(x) for x in a); print(s); out.append(s)
lat = []; codes = []
for _ in range(50):
    t = time.perf_counter(); c, _b = C.get(B + "/healthz"); lat.append((time.perf_counter() - t) * 1000); codes.append(c)
lat.sort()
p("healthz x50: all200=%s p50=%.1fms p95=%.1fms max=%.1fms" % (all(c == 200 for c in codes), statistics.median(lat), lat[int(.95 * 50) - 1], lat[-1]))
rl = []; rc = []
for _ in range(10):
    t = time.perf_counter(); c, b = C.get(B + "/readyz"); rl.append((time.perf_counter() - t) * 1000); rc.append(c)
p("readyz x10:", set(rc), b, "max=%.1fms" % max(rl))
log = open(os.path.join(C.W, "backend.log"), errors="replace").read()
bad = [l for l in log.splitlines() if re.search(r"filesystem|fallback|Traceback|ERROR|WARNING", l, re.I)]
p("backend log: %d lines, suspicious lines: %d" % (len(log.splitlines()), len(bad))); [p("  ", l[:200]) for l in bad[:10]]
# auth is ON: an anonymous protected call must be refused
p("anonymous GET /api/v1/entitlements/current ->", C.get(B + "/api/v1/entitlements/current")[0])
# kv_store really lives in Postgres
import psycopg2
cx = psycopg2.connect(C.pg_url()); cur = cx.cursor(); cur.execute("select count(*) from kv_store"); p("postgres kv_store rows:", cur.fetchone()[0])
cur.execute("select count(*) from information_schema.tables where table_name='kv_store'"); p("kv_store table exists:", cur.fetchone()[0])
fs = os.path.join(C.W, "cloud-data", "kv"); p("filesystem kv dir present:", os.path.isdir(fs))
# missing-secret matrix: backend must refuse to boot (so /readyz can never say ready)
p("\n== every required secret removed one at a time (prod, auth on, real startup check) ==")
miss = ["SKILL_DATABASE_URL", "SKILL_CLERK_ISSUER", "SKILL_CLERK_JWKS_URL", "SKILL_CORS_ALLOWED_ORIGINS", "CASHFREE_APP_ID", "CASHFREE_SECRET_KEY", "CASHFREE_WEBHOOK_SECRET",
        "CASHFREE_STARTER_PLAN_ID", "CASHFREE_PRO_PLAN_ID", "SKILL_API_BASE_URL", "SKILL_TRACKING_HMAC_SECRET", "SKILL_INSTALLER_SIGNING_KEY", "CONXA_MANIFEST_SIGNING_KEY", "SKILL_TWELVELABS_API_KEY", "LLM_FREE_API_KEYS", "CONXA_ADMIN_TOKEN", "SKILL_CLERK_AUTHORIZED_PARTIES", "CLERK_SECRET_KEY"]
port = 8169
for name in miss:
    env = C.base_env(port); env.pop(name, None)
    f = open(os.path.join(C.W, "miss.log"), "wb")
    pr = subprocess.Popen([sys.executable] + C.uvi(port), env=env, cwd=os.path.join(C.W, "run"), stdout=f, stderr=f, creationflags=0x08000000)
    up = False
    for _ in range(14):
        if C.get(f"http://127.0.0.1:{port}/healthz")[0] == 200: up = True; break
        if pr.poll() is not None: break
        time.sleep(1)
    rd = C.get(f"http://127.0.0.1:{port}/readyz") if up else None
    ex = pr.poll(); C.kill(pr.pid); f.close()
    tail = [l for l in open(os.path.join(C.W, "miss.log"), errors="replace").read().splitlines() if "Refusing" in l or "ValidationError" in l or "No LLM" in l]
    p("  missing %-32s booted=%s readyz=%s exit=%s %s" % (name, up, rd and rd[0], ex, (tail[0][:150] if tail else "")))
# bad DB: DB unreachable -> readyz must be 503 (backend may boot or not)
env = C.base_env(port); env["SKILL_DATABASE_URL"] = "postgresql://postgres:@127.0.0.1:1/postgres"
f = open(os.path.join(C.W, "miss.log"), "wb"); pr = subprocess.Popen([sys.executable] + C.uvi(port), env=env, cwd=os.path.join(C.W, "run"), stdout=f, stderr=f, creationflags=0x08000000)
up = False
for _ in range(14):
    if C.get(f"http://127.0.0.1:{port}/healthz")[0] == 200: up = True; break
    if pr.poll() is not None: break
    time.sleep(1)
p("  DB URL unreachable: booted=%s readyz=%s exit=%s" % (up, C.get(f"http://127.0.0.1:{port}/readyz")[:2] if up else None, pr.poll())); C.kill(pr.pid)
# filesystem fallback + auth on, startup check bypassed via test hook (wf20 style): readyz must still be 503
env = C.base_env(port); env.pop("SKILL_DATABASE_URL")
f = open(os.path.join(C.W, "miss.log"), "wb")
pr = subprocess.Popen([sys.executable, os.path.join(C.TOOLS, "wf20_backend.py"), str(port)], env=env, cwd=os.path.join(C.W, "run"), stdout=f, stderr=f, creationflags=0x08000000)
up = False
for _ in range(14):
    if C.get(f"http://127.0.0.1:{port}/healthz")[0] == 200: up = True; break
    if pr.poll() is not None: break
    time.sleep(1)
p("  no DB + startup check bypassed (test hook): booted=%s readyz=%s" % (up, C.get(f"http://127.0.0.1:{port}/readyz") if up else None)); C.kill(pr.pid)
open(os.path.join(os.path.dirname(__file__), "..", "evidence", "WF-021", "wf21_a_health.txt"), "w").write("\n".join(out) + "\n")
