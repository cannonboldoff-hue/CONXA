#!/usr/bin/env python3
r"""WF-21 process control. Everything under %TEMP%\wf21. Never touches :8000/:8099.
Real cloud app (app.main) with CONXA_ENV=prod, SKILL_AUTH_REQUIRED=true, EVERY required secret (test values), a real local
Postgres (pgserver, started by wf21_pg.py), real _validate_production_config (NOT skipped). Mock Clerk+LLM = wf20_mock.py.
  wf21_ctl.py up [fresh]            mock Clerk/LLM (8161/8162) + backend :8160
  wf21_ctl.py once PORT [-ENV ...] [ENV=VAL ...]   start one backend on PORT with env removed (-NAME) / overridden; print health or exit + log tail
  wf21_ctl.py down                  stop what this tool started
Backend runs from an EMPTY cwd so the owner's .env (real keys) is never loaded."""
import os, sys, json, subprocess, time, shutil, urllib.request
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
W = os.path.join(os.environ["TEMP"], "wf21"); PIDF = os.path.join(W, "pids.json")
TOOLS = os.path.join(ROOT, "docs", "testing", "tools")
CP, LP, BP = 8161, 8162, 8160
ADMIN = "wf21-admin-token"
# distinctive test secrets, grepped for later in the built dashboard + packages
SECRETS = {
    "CASHFREE_APP_ID": "wf21-cf-app-id-TEST", "CASHFREE_SECRET_KEY": "wf21-cf-secret-KEY-9d41", "CASHFREE_WEBHOOK_SECRET": "wf21-cf-webhook-SECRET-7b2e",
    "CASHFREE_STARTER_PLAN_ID": "plan_starter_wf21", "CASHFREE_PRO_PLAN_ID": "plan_pro_wf21",
    "SKILL_TRACKING_HMAC_SECRET": "wf21-tracking-hmac-SECRET-33aa", "SKILL_INSTALLER_SIGNING_KEY": "wf21-installer-signing-KEY-55cc",
    "SKILL_TWELVELABS_API_KEY": "wf21-twelvelabs-KEY-12ab", "CLERK_SECRET_KEY": "sk_test_wf21ClerkSecretKEY",
    "CONXA_ADMIN_TOKEN": ADMIN, "LLM_FREE_API_KEYS": "wf21-llm-free-KEY-xyz",
}

def pids():
    try: return json.load(open(PIDF))
    except Exception: return {}
def save(p): json.dump(p, open(PIDF, "w"))
def kill(pid): subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
def get(url, hdr=None):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=hdr or {}), timeout=5); return r.status, r.read().decode()[:300]
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
    except Exception as e: return 0, str(e)[:100]
def manifest_key():
    f = os.path.join(W, "manifest_ed25519.pem")
    if not os.path.exists(f):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization as s
        open(f, "wb").write(ed25519.Ed25519PrivateKey.generate().private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
    return open(f).read()
def pg_url():
    return open(os.path.join(W, "pg_uri.txt")).read().strip()
def base_env(port=BP):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SKILL_", "CONXA_", "LLM_", "CLERK_", "CASHFREE_"))}
    env.update(SECRETS)
    env.update({
        "CONXA_ENV": "prod", "SKILL_AUTH_REQUIRED": "true",
        "SKILL_DATABASE_URL": pg_url(), "SKILL_CORS_ALLOWED_ORIGINS": "http://127.0.0.1:3100",
        "SKILL_CLERK_ISSUER": f"http://127.0.0.1:{CP}", "SKILL_CLERK_JWKS_URL": f"http://127.0.0.1:{CP}/.well-known/jwks.json",
        "SKILL_CLERK_AUTHORIZED_PARTIES": "wf20-client", "CONXA_MANIFEST_SIGNING_KEY": manifest_key(),
        "SKILL_DATA_DIR": os.path.join(W, "cloud-data"), "CONXA_STUDIO_HOME": os.path.join(W, "home"),
        "SKILL_API_BASE_URL": f"http://127.0.0.1:{port}", "SKILL_TWELVELABS_BASE_URL": f"http://127.0.0.1:{LP}/tl",
        "SKILL_BYOK_ENCRYPTION_KEY": "d2YyMC10ZXN0LWJ5b2sta2V5LTMyLWJ5dGVzLSEhISE=", "CASHFREE_ENV": "TEST",
        "LLM_FREE_ENDPOINT": f"http://127.0.0.1:{LP}/v1", "LLM_FREE_PROVIDER": "mockfree",
        "WF20_CLERK_PORT": str(CP), "WF20_KEY": os.path.join(W, "mock_key.pem"), "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join([os.path.join(ROOT, "conxa-cloud", "backend"), os.path.join(TOOLS, "wf20_site")]),
    })
    return env
def spawn(args, env, log, cwd):
    f = open(os.path.join(W, log), "wb")
    return subprocess.Popen([sys.executable] + args, env=env, cwd=cwd, stdout=f, stderr=f, creationflags=0x08000000)
def uvi(port):  # real entry point, no test hook
    return ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"]
def wait_up(port, secs=40):
    for _ in range(secs):
        if get(f"http://127.0.0.1:{port}/healthz")[0] == 200: return True
        time.sleep(1)
    return False

def main():
    os.makedirs(os.path.join(W, "run"), exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""; p = pids(); run = os.path.join(W, "run")
    if cmd == "up":
        if "fresh" in sys.argv: shutil.rmtree(os.path.join(W, "cloud-data"), ignore_errors=True)
        for pid in p.values(): kill(pid)
        p = {}; env = base_env()
        p["mock"] = spawn([os.path.join(TOOLS, "wf20_mock.py"), str(CP), str(LP)], env, "mock.log", run).pid; time.sleep(2)
        p["backend"] = spawn(uvi(BP), env, "backend.log", run).pid
        save(p); print("clerk_jwks=%s backend=%s" % (get(f"http://127.0.0.1:{CP}/.well-known/jwks.json")[0], 200 if wait_up(BP) else 0))
    elif cmd == "once":
        port = int(sys.argv[2]); env = base_env(port)
        for a in sys.argv[3:]:
            if a.startswith("-"): env.pop(a[1:], None)
            else: k, v = a.split("=", 1); env[k] = v
        pr = spawn(uvi(port), env, "once_%d.log" % port, run)
        up = wait_up(port, 15)
        print("port %d up=%s exit=%s" % (port, up, pr.poll()))
        if up:
            for path in ("/healthz", "/readyz"): print(path, *get(f"http://127.0.0.1:{port}{path}"))
            kill(pr.pid)
        else:
            kill(pr.pid)
        print("--- log tail ---"); print(open(os.path.join(W, "once_%d.log" % port), errors="replace").read()[-900:])
    elif cmd == "down":
        for pid in p.values(): kill(pid)
        save({}); print("all stopped")

if __name__ == "__main__":
    main()
