#!/usr/bin/env python3
r"""WF-20 process control. Everything lives under %TEMP%\wf20 (own data dir, own pid file). Never touches :8000/:8099.
  wf20_ctl.py up [fresh]                      start mock Clerk+LLM (8141/8142) and the test backend (8140); 'fresh' wipes cloud-data first
  wf20_ctl.py extra <port> [ENV=VAL ...]      start one more backend on <port> sharing the same data dir, with extra env (kill-switch rows)
  wf20_ctl.py stop-extra                      stop the extra backends
  wf20_ctl.py down                            stop everything this tool started
Backend runs from an EMPTY cwd so the owner's conxa-cloud/backend/.env (real keys) is never loaded."""
import os, sys, json, subprocess, time, shutil, urllib.request
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
W = os.path.join(os.environ["TEMP"], "wf20"); PIDF = os.path.join(W, "pids.json")
TOOLS = os.path.join(ROOT, "docs", "testing", "tools")
CP, LP, BP = 8141, 8142, 8140
ADMIN = "wf20-admin-token"

def pids():
    try: return json.load(open(PIDF))
    except Exception: return {}
def save(p): json.dump(p, open(PIDF, "w"))
def kill(pid):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
def probe(url):
    try: return urllib.request.urlopen(url, timeout=3).status
    except Exception: return 0
def base_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SKILL_", "CONXA_", "LLM_", "CLERK_"))}
    env.update({
        "CONXA_ENV": "dev", "SKILL_AUTH_REQUIRED": "true", "SKILL_ALLOW_NO_PROVIDERS": "1",
        "SKILL_CLERK_ISSUER": f"http://127.0.0.1:{CP}", "SKILL_CLERK_JWKS_URL": f"http://127.0.0.1:{CP}/.well-known/jwks.json",
        "SKILL_CLERK_AUTHORIZED_PARTIES": "wf20-client", "CLERK_SECRET_KEY": "mock-secret",
        "CONXA_ADMIN_TOKEN": ADMIN, "SKILL_DATA_DIR": os.path.join(W, "cloud-data"), "CONXA_STUDIO_HOME": os.path.join(W, "home"),
        "SKILL_API_BASE_URL": f"http://127.0.0.1:{BP}", "SKILL_ENTITLEMENTS_RESERVATION_TTL_SECS": "60",
        "WF20_CLERK_PORT": str(CP), "SKILL_BYOK_ENCRYPTION_KEY": "d2YyMC10ZXN0LWJ5b2sta2V5LTMyLWJ5dGVzLSEhISE=", "SKILL_TWELVELABS_API_KEY": "mock-tl-key", "SKILL_TWELVELABS_BASE_URL": f"http://127.0.0.1:{LP}/tl", "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join([os.path.join(ROOT, "conxa-cloud", "backend"), os.path.join(TOOLS, "wf20_site")]),
    })
    for tier, key in (("FREE", "key-free"), ("STARTER", "key-starter"), ("PRO", "key-pro")):
        env.update({f"LLM_{tier}_API_KEYS": key, f"LLM_{tier}_ENDPOINT": f"http://127.0.0.1:{LP}/v1", f"LLM_{tier}_PROVIDER": f"mock{tier.lower()}"})
    return env
def spawn(args, env, log, cwd):
    f = open(os.path.join(W, log), "ab")
    return subprocess.Popen([sys.executable] + args, env=env, cwd=cwd, stdout=f, stderr=f, creationflags=0x08000000).pid
def wait_up(port):
    for _ in range(40):
        if probe(f"http://127.0.0.1:{port}/healthz") == 200: return True
        time.sleep(1)
    return False

def main():
    global p
    os.makedirs(os.path.join(W, "run"), exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    p = pids()
    if cmd == "up":
        if "fresh" in sys.argv: shutil.rmtree(os.path.join(W, "cloud-data"), ignore_errors=True)
        for pid in p.values(): kill(pid)
        p = {}
        env = base_env(); env["WF20_KEY"] = os.path.join(W, "mock_key.pem")
        p["mock"] = spawn([os.path.join(TOOLS, "wf20_mock.py"), str(CP), str(LP)], env, "mock.log", os.path.join(W, "run"))
        time.sleep(2)
        p["backend"] = spawn([os.path.join(TOOLS, "wf20_backend.py"), str(BP)], env, "backend.log", os.path.join(W, "run"))
        save(p); print("clerk_jwks=%s backend=%s" % (probe(f"http://127.0.0.1:{CP}/.well-known/jwks.json"), 200 if wait_up(BP) else 0))
    elif cmd == "extra":
        port = int(sys.argv[2]); env = base_env()
        for kv in sys.argv[3:]:
            k, v = kv.split("=", 1); env[k] = v
        p["extra_%d" % port] = spawn([os.path.join(TOOLS, "wf20_backend.py"), str(port)], env, "extra_%d.log" % port, os.path.join(W, "run"))
        save(p); print("extra %d up=%s" % (port, wait_up(port)))
    elif cmd == "stop-extra":
        for k in [k for k in p if k.startswith("extra_")]: kill(p.pop(k))
        save(p); print("extras stopped")
    elif cmd == "down":
        for pid in p.values(): kill(pid)
        save({}); print("all stopped")

if __name__ == '__main__':
    main()
