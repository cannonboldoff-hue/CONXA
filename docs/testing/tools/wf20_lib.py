"""WF-20 shared helpers: HTTP calls against the isolated test backend, token minting from the local mock, transcript + result log.
Backend :8140, mock Clerk :8141 (started by wf20_ctl.py). Nothing here talks to the internet or to :8000."""
import json, os, sys, time, urllib.request, urllib.error, base64, hashlib, secrets, re, subprocess

BASE = os.environ.get("WF20_BASE", "http://127.0.0.1:8140")
MOCK = "http://127.0.0.1:8141"
ADMIN = "wf20-admin-token"
EVD = os.environ.get("WF20_EVD") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evidence", "WF-020")
os.makedirs(EVD, exist_ok=True)
_T = {"f": None}
RESULTS = {}
TRANSPORT_RETRIES = [0]

def open_transcript(name):
    if _T["f"]: _T["f"].close()
    _T["f"] = open(os.path.join(EVD, name), "w", encoding="utf-8")
def say(s=""):
    print(s)
    if _T["f"]: _T["f"].write(s + "\n"); _T["f"].flush()

def _raw(method, url, headers, data):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        try: body = e.read()
        except Exception: body = b""  # server reset after sending the status line (Windows, body unread); status is still valid
        return e.code, body, dict(e.headers)
    except Exception as e: return -1, repr(e).encode(), {}

def call(method, path, token=None, body=None, headers=None, base=None, raw_body=None, log=True, label=""):
    """Returns (status, parsed-json-or-text). Logs one line to the transcript."""
    h = dict(headers or {})
    if token is not None: h["Authorization"] = f"Bearer {token}"
    data = raw_body
    if body is not None:
        data = json.dumps(body).encode(); h.setdefault("Content-Type", "application/json")
    # urllib on Windows sometimes sees a reset when the server answers 401 before reading the body; curl never does (60/60 clean). Retry, count them.
    for _try in range(3):
        st, b, rh = _raw(method, (base or BASE) + path, h, data)
        if st != -1: break
        TRANSPORT_RETRIES[0] += 1
    txt = b.decode("utf-8", "replace")
    try: js = json.loads(txt)
    except Exception: js = txt
    if log:
        short = txt if len(txt) < 260 else txt[:260] + "..."
        say(f"  {label + ' ' if label else ''}{method} {path[:110]} -> {st} {short}")
    return st, js

def mint(sub, org=None, role="org:admin", ttl=3600, email=None, **kw):
    q = f"sub={sub}&ttl={ttl}&email={email or sub + '@t.test'}"
    if org: q += f"&org={org}&role={role}"
    for k, v in kw.items(): q += f"&{k}={v}"
    return json.loads(urllib.request.urlopen(f"{MOCK}/_mint?{q}", timeout=5).read())["token"]

def ctl(**kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    return json.loads(urllib.request.urlopen(f"{MOCK}/_ctl?{q}", timeout=5).read())

def set_plan(ws, plan, days=None, base=None):
    body = {"workspace_id": ws, "plan": plan}
    if days: body["duration_days"] = days
    return call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body=body, base=base, label=f"[admin->{plan}]")

def cur(token, label="current"):
    st, js = call("GET", "/api/v1/entitlements/current", token=token, log=False)
    if st != 200:
        say(f"  {label}: GET /entitlements/current -> {st} {str(js)[:200]}"); return None
    m = js["meters"]
    say(f"  {label}: plan={js['plan']} seats={m['seats']['used']}/{m['seats']['limit']} machines={m['machines']['used']}/{m['machines']['limit']} "
        f"compile={m['compile_credits']['used']}+{m['compile_credits']['reserved'] if 'reserved' in m['compile_credits'] else 0}res/{m['compile_credits']['limit']} "
        f"pool={m['human_edit_tokens']['used']}/{m['human_edit_tokens']['limit']} wallet={js['wallet']['compile_credits']}/{js['wallet']['human_edit_tokens']} "
        f"caps={js['capabilities']['distribution']},wl={js['capabilities']['white_label']},ops={js['capabilities']['ops_tier']},pool={js['capabilities']['compile_pool']},byok={js['capabilities']['byok']} "
        f"wf_lock={js['workflow_lock']['active']}act/{js['workflow_lock']['locked']}lock/limit={js['workflow_lock']['limit']} trial_expired={js['trial_expired']}")
    return js

def detail(js):
    return js.get("detail") if isinstance(js, dict) else str(js)[:80]

def record(row, result, sev, note):
    RESULTS.setdefault(row, []).append({"result": result, "sev": sev, "note": note})
    say(f"  >>> RESULT {row}: {result} {sev or ''} - {note}")

def save_results(name):
    p = os.path.join(EVD, name)
    old = {}
    if os.path.exists(p):
        try: old = json.load(open(p))
        except Exception: pass
    old.update(RESULTS); json.dump(old, open(p, "w"), indent=1)

def publish(token, skill, version, content=None, base=None, path="/api/v1/workflows/publish"):
    c = content if content is not None else f"{skill}-{version}-{secrets.token_hex(4)}"
    files = [{"path": f"{skill}/skill.json", "content_base64": base64.b64encode(json.dumps({"id": skill, "v": version, "c": c}).encode()).decode()}]
    body = {"skill_slug": skill, "skill_pack_version": version, "release_notes": "wf20 test", "files": files}
    return call("POST", path, token=token, body=body, base=base, label=f"[publish {skill}@{version}]")


def seed(*args):
    """Seed the isolated test store (see wf20_seed.py)."""
    here = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "wf20_seed.py")] + [str(a) for a in args], capture_output=True, text=True)
    say(f"  [seed {' '.join(str(a) for a in args)[:90]}] -> {(r.stdout or r.stderr).strip()[:150]}")
    return r.returncode == 0

PLANS = [("free", 25, 1, 1, 500_000), ("starter", 200, 3, 3, 2_500_000), ("pro", 500, 10, 10, 10_000_000), ("enterprise", None, 0, 0, 0)]
# plan, compile cap (also workflow cap), seats, machines, human-edit pool (tokens); enterprise numbers are PLAN_LIMITS floor with no overrides

def newws(tag, plan, user="owner", set_billing=True):
    ws = f"org_{tag}_{plan}"
    tok = mint(f"user_{tag}_{plan}_{user}", ws, "org:admin")
    if plan == "enterprise" and set_billing:
        # Enterprise with no overrides has seats=0, so a brand-new workspace's FIRST member is refused (see 20.16). Let the owner be seen as Free first.
        call("GET", "/api/v1/workspaces/current", token=tok, log=False)
    if set_billing: set_plan(ws, plan)
    return ws, tok

def reserve(tok, rid, machine=None, log=False):
    h = {"X-Conxa-Machine": machine} if machine else None
    return call("POST", "/api/v1/usage/compile/reserve", token=tok, body={"reservation_id": rid}, headers=h, log=log)

H_STUDIO = {"X-Conxa-Client": "build-studio"}

def proxy(tok, kind="text", usage_class="compile", chars=1000, log=False):
    ep = "/api/v1/llm/proxy/vision" if kind == "vision" else "/api/v1/llm/proxy/text"
    return call("POST", ep, token=tok, body={"task": "copilot_reply" if kind == "text" else "anchor_vision", "payload": {"messages": [{"role": "user", "content": "word " * (chars // 5)}]}, "usage_class": usage_class}, headers=H_STUDIO, log=log)
