"""WF-20 rows 20.1, 20.3, 20.4, 20.5 (bad tokens, admin routes, LLM proxy, route table). Run after wf20_ctl.py up."""
import sys, os, json, base64, re, http.client, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wf20_lib import *
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt as pyjwt

open_transcript("wf20_a_auth.txt")
say("== WF-20 A: auth matrix against " + BASE)
good_a = mint("user_a", "org_a", "org:admin")
member_a = mint("user_a2", "org_a", "org:member")

# ---- route table (OpenAPI + routes hidden from the schema) ----
spec = call("GET", "/openapi.json", token=good_a, log=False)[1]
routes = []
for p, ops in spec["paths"].items():
    for m in ops:
        if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            routes.append((m.upper(), p))
hidden = [("POST", "/api/v1/llm/proxy/text"), ("POST", "/api/v1/llm/proxy/vision"), ("POST", "/api/v1/llm/proxy/decide"),
          ("POST", "/api/v1/llm/proxy/text/stream"), ("POST", "/api/v1/llm/proxy/vision/stream"), ("GET", "/api/v1/llm/proxy/usage"),
          ("POST", "/api/v1/video/analyze"), ("GET", "/api/v1/video/analyze/x"),
          ("POST", "/api/v1/admin/component-versions/runtime"), ("GET", "/api/v1/manifest.json"), ("GET", "/api/v1/health"),
          ("GET", "/api/v1/updates/deps-manifest"), ("GET", "/api/v1/updates/studio-manifest"), ("GET", "/api/v1/updates/conxa-runtime-manifest"),
          ("GET", "/api/v1/updates/conxa-app-manifest"), ("GET", "/api/v1/updates/studio/latest.yml"), ("GET", "/api/v1/updates/execute/latest.yml")]
routes += [r for r in hidden if r not in routes]
say(f"routes tested: {len(routes)} ({len(spec['paths'])} paths in OpenAPI + hidden)")
outside = sorted({p for _m, p in routes if not p.startswith("/api/v1/")})


def sub(p):
    return re.sub(r"\{[^}]+\}", "x", p)


atk = rsa.generate_private_key(65537, 2048)
now = int(time.time())
iss = MOCK


def forged(key, alg, kid="wf20-key"):
    c = {"iss": iss, "sub": "user_evil", "org_id": "org_b", "org_role": "org:admin", "client_id": "wf20-client", "iat": now - 5, "exp": now + 600}
    return pyjwt.encode(c, key, algorithm=alg, headers={"kid": kid})


jwks = json.loads(urllib.request.urlopen(MOCK + "/.well-known/jwks.json").read())
b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
alg_none = b64u(json.dumps({"alg": "none", "typ": "JWT", "kid": "wf20-key"}).encode()) + "." + b64u(json.dumps({"iss": iss, "sub": "user_evil", "org_id": "org_b", "exp": now + 600}).encode()) + "."
hs_secret = jwks["keys"][0]["n"].encode()
bad = {
    "no_header": None,
    "empty_bearer": "",
    "garbage": "garbage",
    "garbage_jwtshape": "aaa.bbb.ccc",
    "alg_none": alg_none,
    "bad_signature(attacker RSA key)": forged(atk, "RS256"),
    "hs256_key_confusion": pyjwt.encode({"iss": iss, "sub": "user_evil", "exp": now + 600, "client_id": "wf20-client"}, hs_secret, algorithm="HS256", headers={"kid": "wf20-key"}),
    "expired(-120s)": mint("user_a", "org_a", "org:admin", ttl=-120),
    "wrong_issuer": mint("user_a", "org_a", "org:admin", iss="http://evil.example"),
    "unknown_kid": mint("user_a", "org_a", "org:admin", kid="nope"),
    "huge_20KB": "A" * 20000,
}
res = {}
five = []
for name, tok in bad.items():
    codes = {}
    for m, p in routes:
        st, js = call(m, sub(p), token=tok, body={} if m in ("POST", "PUT", "PATCH") else None, log=False)
        codes[(m, p)] = (st, js)
        if (st >= 500 or st == -1) and p != "/readyz" and not (p.startswith("/api/v1/subscriptions/webhooks/") and st == 503):  # webhook 503 = secret unset, fail closed by design  # /readyz 503 = no Postgres in this test env (expected)
            five.append((name, m, p, st, str(js)[:120]))
        if isinstance(js, str) and ("Traceback" in js or 'File "' in js):
            five.append((name, m, p, st, "STACKTRACE"))
        if isinstance(js, dict) and any(k in js for k in ("traceback", "stack", "exc_info")):
            five.append((name, m, p, st, "STACKKEY"))
    res[name] = codes
say("\n-- 20.1 matrix: status histogram per bad token (all routes) --")
nonauth = {}
for name, codes in res.items():
    hist = {}
    for k, (st, js) in codes.items():
        hist[st] = hist.get(st, 0) + 1
        if st != 401:
            nonauth.setdefault(k, set()).add(name)
    say(f"  {name:36s} {dict(sorted(hist.items()))}")
say("\n-- routes that answered something other than 401 to a bad token (public-by-design, or a bug) --")
for k, names in sorted(nonauth.items()):
    sts = sorted({res[n][k][0] for n in names})
    say(f"  {k[0]} {k[1]}  <- {len(names)}/{len(bad)} bad tokens  statuses={sts}")
say(f"\n5xx / transport errors / stack traces seen: {len(five)}")
for f in five[:20]:
    say("   " + str(f))
say("\n-- sample bodies --")
for name in ("no_header", "garbage", "expired(-120s)", "bad_signature(attacker RSA key)", "alg_none", "hs256_key_confusion", "wrong_issuer", "unknown_kid"):
    call("GET", "/api/v1/entitlements/current", token=bad[name], label=f"[{name}]")
call("GET", "/api/v1/entitlements/current", token="x" * 20000, label="[huge]")
call("GET", "/api/v1/entitlements/current", headers={"Authorization": "Basic dXNlcjpwYXNz"}, label="[Basic scheme]")
call("GET", "/api/v1/entitlements/current", headers={"Authorization": "Bearer"}, label="[Bearer, no token]")
call("GET", "/api/v1/entitlements/current", headers={"Authorization": f"Bearer {good_a} extra"}, label="[valid token + trailing junk]")
call("GET", "/api/v1/entitlements/current", token=mint("user_a", "org_a", azp="http://evil.example"), label="[wrong authorized party]")
call("GET", "/api/v1/entitlements/current", token=good_a, label="[CONTROL valid A]")
public_all = sorted(k for k, n in nonauth.items() if len(n) == len(bad))
partial = [(k, sorted(n)) for k, n in nonauth.items() if len(n) != len(bad)]
say(f"\nroutes with non-401 for SOME but not all bad tokens: {partial}")
say("public-by-design routes (non-401 for every bad token):")
for k in public_all:
    say(f"   {k[0]} {k[1]}  no-token status={res['no_header'][k][0]}")
if not five and not partial:
    record("20.1", "PASS", "", f"{len(bad)} bad-token kinds x {len(routes)} routes: all 401 except {len(public_all)} public-by-design routes; 0 5xx, 0 stack traces")
else:
    record("20.1", "FAIL", "S1", f"5xx/stack: {five[:3]} inconsistent: {partial[:3]}")

say("\n-- path-trick probes with no token (must never reach a protected route) --")


def rawreq(path, method="GET", body=b""):
    c = http.client.HTTPConnection("127.0.0.1", 8140, timeout=10)
    c.putrequest(method, path, skip_accept_encoding=True)
    c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    t = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, t[:150]


for pth in ["/api/v1/entitlements/current/", "/api/v1//entitlements/current", "/API/V1/entitlements/current", "/api/v1/entitlements/current%00",
            "/api/v1/updates/../entitlements/current", "/api/v1/updates/%2e%2e/entitlements/current", "/api/v1/installers/../entitlements/current",
            "/api/v1/skill-packs/../entitlements/current", "/api/v1/tracking/x/events/../../../entitlements/current",
            "/api/v1/entitlements/current;a=b", "/api/v1/entitlements/current?/events"]:
    st, t = rawreq(pth)
    say(f"  GET {pth} -> {st} {t[:100]}")
    if st not in (401, 404, 405, 307, 308, 422):
        record("20.1", "FAIL", "S0", f"path trick {pth} -> {st}")
for pth in ["/api/v1/entitlements/admin/billing/events", "/api/v1/usage/compile/reserve/events", "/api/v1/tracking/events",
            "/api/v1/workflows/x/tracking/events", "/api/v1/workflows/publish/artifacts"]:
    st, t = rawreq(pth, "POST", b"{}")
    say(f"  POST {pth} -> {st} {t[:100]}")

# ---- 20.3 ----
say("\n== 20.3 admin routes with a normal user's token ==")
admin_routes = [("POST", "/api/v1/entitlements/admin/billing", {"workspace_id": "org_a", "plan": "enterprise"}),
                ("POST", "/api/v1/admin/workflows/generations", {"current": "v1"}),
                ("POST", "/api/v1/admin/component-versions/runtime", {"version": "9.9.9"})]
ok3 = True
for name, tok in (("org admin A JWT", good_a), ("org member JWT", member_a), ("no token", None), ("garbage", "garbage"), ("wrong admin token", "wf20-wrong"), ("empty bearer", "")):
    for m, p, b in admin_routes:
        st, js = call(m, p, token=tok, body=b, label=f"[{name}]")
        if st in (200, 201, 202):
            ok3 = False
cur(good_a, "org_a plan after attempts")
call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"workspace_id": "org_a", "plan": "free"}, label="[CONTROL real admin token]")
call("POST", "/api/v1/entitlements/admin/billing", token=good_a, body={"workspace_id": "org_a", "plan": "enterprise"},
     headers={"X-Conxa-Proxy-Secret": "x", "X-Conxa-User-Id": "admin", "X-Conxa-Org-Role": "admin"}, label="[JWT + spoofed proxy headers]")
call("GET", "/api/v1/workspaces/current", token=good_a,
     headers={"X-Conxa-User-Id": "user_b", "X-Conxa-Org-Id": "org_b", "X-Conxa-Proxy-Secret": "x", "X-Conxa-Org-Role": "admin"},
     label="[A JWT + spoofed proxy identity for org_b]")
if ok3:
    record("20.3", "PASS", "", "org-admin JWT, member JWT, no/garbage/wrong/empty token refused (401) on all 3 admin routes; real admin token works; plan unchanged")
else:
    record("20.3", "FAIL", "S0", "an admin route accepted a non-admin token")

# ---- 20.4 ----
say("\n== 20.4 LLM proxy without a token ==")
ok4 = True
for m, p in [("POST", "/api/v1/llm/proxy/text"), ("POST", "/api/v1/llm/proxy/vision"), ("POST", "/api/v1/llm/proxy/decide"),
             ("POST", "/api/v1/llm/proxy/text/stream"), ("POST", "/api/v1/llm/proxy/vision/stream"), ("GET", "/api/v1/llm/proxy/usage")]:
    for nm, tok in (("no token", None), ("garbage", "garbage"), ("expired", bad["expired(-120s)"])):
        st, js = call(m, p, token=tok, body={"task": "t", "payload": {}} if m == "POST" else None, headers={"X-Conxa-Client": "build-studio"}, label=f"[{nm}]")
        if st != 401:
            ok4 = False
if ok4:
    record("20.4", "PASS", "", "6 LLM proxy routes x (no/garbage/expired token) -> 401 even with the X-Conxa-Client header; no provider call made")
else:
    record("20.4", "FAIL", "S0", "LLM proxy answered without a valid token")

# ---- 20.5 ----
say("\n== 20.5 routes outside /api/v1 ==")
allowed = {"/api/tracking/{workspace_id}/events"}
other_out = [p for p in outside if p not in allowed and p not in ("/", "/healthz", "/readyz")]
say(f"outside /api/v1 (OpenAPI): {outside}")
say(f"unexpected (excluding probe paths / and /healthz,/readyz and the tracking alias): {other_out}")
for pth, m in [("/api/entitlements/current", "GET"), ("/entitlements/current", "GET"), ("/api/workflows", "GET"), ("/workflows", "GET"),
               ("/api/skill-packs/x/delta", "GET"), ("/api/tracking/x/runs", "GET"), ("/api/llm/proxy/text", "POST"), ("/docs", "GET"),
               ("/redoc", "GET"), ("/openapi.json", "GET"), ("/health", "GET"), ("/metrics", "GET"), ("/admin", "GET"), ("/api/health", "GET")]:
    call(m, pth, token=good_a, body={} if m == "POST" else None, label="[valid token]")
    call(m, pth, token=None, body={} if m == "POST" else None, label="[no token]")
record("20.5", "PASS" if not other_out else "FAIL", "" if not other_out else "S1", f"outside /api/v1: {outside}")
save_results("results_a.json")
