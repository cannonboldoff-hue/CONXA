"""WF-20 retest: the S0 fixes (installer upload, policy PUT/GET, subscription verify, webhooks, jobs gone) and the OpenAPI sweep
(every route that takes an id, A's token against B's ids). Run after wf20_ctl.py up fresh. Webhook rows need an extra backend with
CASHFREE_WEBHOOK_SECRET set (started here through wf20_ctl.py extra 8143)."""
import sys, os, json, time, hmac, hashlib, base64, subprocess, re, secrets
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wf20_lib import *

open_transcript("wf20_f_retest.txt")
say("== WF-20 F retest: fixes + OpenAPI sweep (A = org_a, B = org_b, C = org_c, all Pro) ==")
HERE = os.path.dirname(os.path.abspath(__file__))
tok_a = mint("user_a", "org_a", "org:admin", email="a@t.test")
tok_a_mem = mint("user_a_member", "org_a", "org:member")
tok_b = mint("user_b", "org_b", "org:admin", email="b@t.test")
tok_b_mem = mint("user_b_member", "org_b", "org:member")
tok_c = mint("user_c", "org_c", "org:admin")
for ws in ("org_a", "org_b", "org_c"): set_plan(ws, "pro")
# make the members known as seats (Pro allows 10)
for t in (tok_a_mem, tok_b_mem): call("GET", "/api/v1/workspaces/current", token=t, log=False)
fails = []
def check(row, label, cond, extra=""):
    say(f"  [{'ok ' if cond else 'FAIL'}] {row} {label} {extra}")
    if not cond: fails.append(f"{row}: {label} {extra}")
    return cond

def plan_of(t):
    j = call("GET", "/api/v1/entitlements/current", token=t, log=False)[1]
    return j.get("plan") if isinstance(j, dict) else str(j)[:40]
H_OCT = {"Content-Type": "application/octet-stream"}
def inst(tok, path, payload, ver):
    return call("POST", path + ("&" if "?" in path else "?") + f"version={ver}&release_notes=x&distribution=internal", token=tok, raw_body=payload, headers=H_OCT, label="[installer upload]")

say("\n-- seed B --")
st, pub_b = publish(tok_b, "b_secret_skill", "1.0.0"); trk_b = pub_b["tracking"]["tracking_token"]; sync_b = pub_b["sync_token"]; slug_b = pub_b["slug"]
st, pub_a = publish(tok_a, "a_skill", "1.0.0"); trk_a = pub_a["tracking"]["tracking_token"]; slug_a = pub_a["slug"]
st, js = call("POST", "/api/v1/workflows", token=tok_b, body={"name": "B-SECRET-WF", "target_url": "https://b.example"}, label="[B create workflow]"); b_wf = js["workflow"]["id"]
call("PUT", "/api/v1/app-graphs/b.example/contributions/skill:b_secret_skill", token=tok_b, body={"id": "skill:b_secret_skill", "app_key": "b.example"}, label="[B app-graph]")
st, js = call("PUT", f"/api/v1/tracking/{slug_b}/policy", token=tok_b, body={"denied_hosts": ["b-original.example"]}, label="[B own policy]")
check("20.2", "B can write its own policy by slug", st == 200)
pol_b0 = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_b}, log=False)[1]
inst(tok_b, "/api/v1/workflows/installer/upload", b"MZ-B-ORIGINAL-INSTALLER", "1.0.0")
now_ms = int(time.time() * 1000)
call("POST", f"/api/v1/tracking/{slug_b}/events", headers={"x-tracking-token": trk_b}, body={"rid": "run-b-secret", "wfid": "b_secret_skill", "wfv": "1.0.0", "evts": [{"e": "wf_start", "ts": now_ms - 2000}, {"e": "wf_ok", "ts": now_ms - 1000, "dur": 500, "tot": 1, "rec": 0}]}, label="[B telemetry]")
st, js = call("POST", "/api/v1/video/analyze?duration_s=5", token=tok_b, raw_body=b"fakevideo", headers={"X-Conxa-Client": "build-studio", "Content-Type": "video/mp4"}, label="[B video job]"); job_b = js.get("job_id")


def snap_b():
    s = {}
    s["installer_bytes"] = call("GET", f"/api/v1/installers/{slug_b}", log=False)[1]
    s["installer_versions"] = [(v.get("version"), v.get("sha256")) for v in (call("GET", "/api/v1/workflows/installer/versions", token=tok_b, log=False)[1] or {}).get("versions", [])] if True else None
    s["policy"] = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_b}, log=False)[1]
    s["workflows"] = call("GET", "/api/v1/workflows", token=tok_b, log=False)[1]
    s["wf"] = call("GET", f"/api/v1/workflows/{b_wf}", token=tok_b, log=False)[0]
    s["packs"] = call("GET", "/api/v1/workflows/skill-packs", token=tok_b, log=False)[1]
    s["bundle"] = call("GET", f"/api/v1/packages/bundles/{slug_b}/release", token=tok_b, log=False)[1]
    s["appgraph"] = call("GET", "/api/v1/app-graphs/b.example/contributions", token=tok_b, log=False)[1]
    s["runs"] = call("GET", f"/api/v1/tracking/{slug_b}/runs", token=tok_b, log=False)[1]
    j = call("GET", "/api/v1/entitlements/current", token=tok_b, log=False)[1]
    s["plan"] = (j.get("plan"), j["meters"]["compile_credits"]["limit"], j["workflow_lock"]["locked"]) if isinstance(j, dict) and "meters" in j else j
    s["job"] = (call("GET", f"/api/v1/video/analyze/{job_b}", token=tok_b, headers={"X-Conxa-Client": "build-studio"}, log=False)[1] or {}).get("status")
    return s


time.sleep(2)
S0 = snap_b()
say(f"  B snapshot taken: installer={str(S0['installer_bytes'])[:30]!r} policy_version={S0['policy'].get('policy_version') if isinstance(S0['policy'], dict) else S0['policy']} job={S0['job']}")

say("\n==== 20.2 repro 1: generic jobs routes are gone ====")
for who, t in (("A", tok_a), ("B", tok_b)):
    for m, p in (("GET", "/api/v1/jobs"), ("GET", f"/api/v1/jobs/{job_b}"), ("GET", f"/api/v1/jobs/{job_b}/events"), ("POST", f"/api/v1/jobs/{job_b}/cancel")):
        st, js = call(m, p, token=t, label=f"[{who}]")
        check("20.2", f"{who} {m} {p.split(job_b)[0]}.. gone", st in (404, 405))
st, js = call("GET", f"/api/v1/video/analyze/{job_b}", token=tok_b, headers={"X-Conxa-Client": "build-studio"}, label="[B own job]")
check("20.2", "B still reads own video-analysis job", st == 200 and js.get("status") == "succeeded")
st, js = call("GET", f"/api/v1/video/analyze/{job_b}", token=tok_a, headers={"X-Conxa-Client": "build-studio"}, label="[A reading B's video job via owner-checked route]")
check("20.2", "A cannot read B's video job via the owner-checked route", st in (403, 404) and "SECRET" not in json.dumps(js))

say("\n==== 20.2 repro 2: installer ====")
st, js = inst(tok_a, f"/api/v1/workflows/{slug_b}/installer/upload", b"MZ-A-EVIL-INSTALLER", "9.9.9")
check("20.2", "A -> POST /workflows/org_b/installer/upload", st == 403, f"({st} {detail(js)})")
st, js = inst(tok_a, "/api/v1/workflows/ORG_B/installer/upload", b"MZ-A-EVIL-INSTALLER", "9.9.8")
check("20.2", "A -> POST /workflows/ORG_B/... (case variant)", st in (403, 400, 404), f"({st} {detail(js)})")
st, js = call("GET", f"/api/v1/installers/{slug_b}", log=False)
check("20.2", "B public download unchanged", st == 200 and js == "MZ-B-ORIGINAL-INSTALLER", f"({st} {str(js)[:40]!r})")
st, js = call("GET", f"/api/v1/workflows/{slug_b}/installer/versions", token=tok_a, label="[A legacy versions for org_b]")
check("20.2", "A -> GET /workflows/org_b/installer/versions", st == 403, f"({st})")
st, js = call("GET", f"/api/v1/workflows/{slug_a}/installer/versions", token=tok_a, label="[A legacy versions own slug]")
check("20.2", "A own legacy versions list works", st == 200, f"({st})")
st, js = call("GET", "/api/v1/workflows/v2/installer/versions", token=tok_a, label="[A legacy versions v2 = own]")
check("20.2", "A GET /workflows/v2/installer/versions means own slug", st == 200, f"({st})")
# member (non-admin)
for label, path in (("v2 (Studio legacy path)", "/api/v1/workflows/v2/installer/upload"), ("own slug legacy", f"/api/v1/workflows/{slug_a}/installer/upload"), ("versioned /workflows/installer/upload", "/api/v1/workflows/installer/upload")):
    st, js = inst(tok_a_mem, path, b"MZ-MEMBER", "7.0.0")
    check("20.2", f"member (non-admin) upload via {label}", st == 403, f"({st} {detail(js)})")
st, js = call("GET", "/api/v1/workflows/v2/installer/versions", token=tok_a_mem, label="[member versions]")
check("20.2", "member cannot list installer versions", st == 403, f"({st})")
# Studio legacy v2 by own admin
st, js = inst(tok_a, "/api/v1/workflows/v2/installer/upload", b"MZ-A-V2-OWN", "2.0.0")
check("20.2", "A admin legacy /workflows/v2/installer/upload works", st == 200 and js.get("slug") == slug_a, f"({st} slug={js.get('slug') if isinstance(js, dict) else js})")
st, js = call("GET", f"/api/v1/installers/{slug_a}", log=False)
check("20.2", "A's bytes in A's own slot", st == 200 and js == "MZ-A-V2-OWN", f"({st} {str(js)[:30]!r})")
st, js = call("GET", "/api/v1/installers/v2", label="[public download of the old shared v2 slot]")
check("20.2", "no shared v2 slot (GET /installers/v2 404)", st == 404, f"({st})")
st, js = inst(tok_c, "/api/v1/workflows/v2/installer/upload", b"MZ-C-V2-OWN", "2.0.0")
check("20.2", "C's legacy v2 upload lands in C's slot (not shared, same version number not a 409)", st == 200 and js.get("slug") == "org_c", f"({st} {js.get('slug') if isinstance(js, dict) else js})")
st, js = call("GET", f"/api/v1/installers/{slug_a}", log=False)
check("20.2", "A's installer unchanged after C's upload", js == "MZ-A-V2-OWN")
st, js = call("GET", "/api/v1/installers/org_c", log=False)
check("20.2", "C's slot holds C's bytes", js == "MZ-C-V2-OWN")
time.sleep(3)  # Windows only: the download handle just above may still be open; replacing an open file is refused (see handoff note)
st, js = inst(tok_a, "/api/v1/workflows/v1/installer/upload", b"MZ-A-V1", "2.0.1")
check("20.2", "v1 means own slug too", st == 200 and js.get("slug") == slug_a)

say("\n==== 20.2 repro 3: governance policy ====")
st, js = call("PUT", f"/api/v1/tracking/{slug_b}/policy", token=tok_a, body={"denied_hosts": ["EVIL-A-WROTE-THIS.example"], "enforce": True}, label="[A PUT B's policy]")
check("20.2", "A PUT /tracking/org_b/policy", st == 403, f"({st} {detail(js)})")
st, js = call("PUT", "/api/v1/tracking/ORG_B/policy", token=tok_a, body={"denied_hosts": ["EVIL.example"]}, label="[A PUT ORG_B policy]")
check("20.2", "A PUT /tracking/ORG_B/policy", st in (403, 404), f"({st})")
pol_b1 = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_b}, log=False)[1]
check("20.2", "B's policy unchanged", pol_b1 == pol_b0 and "EVIL" not in json.dumps(pol_b1), f"(version {pol_b1.get('policy_version') if isinstance(pol_b1, dict) else pol_b1})")
st, js = call("PUT", f"/api/v1/tracking/{slug_b}/policy", token=tok_b_mem, body={"denied_hosts": ["m.example"]}, label="[B member PUT own policy]")
check("20.2", "member (non-admin) cannot PUT policy", st == 403, f"({st})")
# mixed-case own id stored under slug
tok_m = mint("user_mix", "Org_Mix", "org:admin"); set_plan("Org_Mix", "pro")
st, pm = publish(tok_m, "mix_skill", "1.0.0"); slug_m = pm["slug"]; trk_m = pm["tracking"]["tracking_token"]
st, js = call("PUT", "/api/v1/tracking/Org_Mix/policy", token=tok_m, body={"denied_hosts": ["mix.example"]}, label="[mixed-case own id PUT]")
st2, js2 = call("GET", f"/api/v1/tracking/{slug_m}/policy", headers={"x-tracking-token": trk_m}, label="[runtime fetch by slug]")
check("20.2", "mixed-case own id stored under slug, readable by runtime", st == 200 and st2 == 200 and "mix.example" in json.dumps(js2), f"(slug={slug_m} {st} {st2})")
say("\n-- runtime policy fetch (X-Tracking-Token only, no sign-in) --")
st, js = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_b}, label="[token only, right token]")
check("20.2/f", "policy GET token-only 200", st == 200)
st, js = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": "wrong"}, label="[wrong token]")
check("20.2/f", "wrong token 401 invalid_tracking_token", st == 401 and detail(js) == "invalid_tracking_token", f"({st} {detail(js)})")
st, js = call("GET", f"/api/v1/tracking/{slug_b}/policy", label="[no token, no bearer]")
check("20.2/f", "no token 401", st == 401, f"({st} {detail(js)})")
st, js = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_a}, label="[A's tracking token on B's policy]")
check("20.2/f", "A's tracking token cannot read B's policy", st == 401, f"({st} {detail(js)})")
st, js = call("GET", f"/api/v1/tracking/{slug_b}/policy", token=tok_a, label="[A bearer only, B's policy]")
check("20.2/f", "A's bearer alone cannot read B's policy", st == 401, f"({st} {detail(js)})")
st, js = call("PUT", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_b}, body={"denied_hosts": ["x.example"]}, label="[PUT with tracking token only (PUT must stay behind sign-in)]")
check("20.2/f", "policy PUT with tracking token only is 401", st == 401, f"({st})")

say(f"  A plan before verify phase: {plan_of(tok_a)}")
say("\n==== subscription verify cross-company ====")
seed("kv", "cashfree_sub_workspace", "subref_b", json.dumps({"workspace_id": "org_b", "tier": "starter"}))
seed("kv", "cashfree_sub_workspace", "subref_a", json.dumps({"workspace_id": "org_a", "tier": "starter"}))
plan_a0 = call("GET", "/api/v1/entitlements/current", token=tok_a, log=False)[1]["plan"]
st, js = call("POST", "/api/v1/subscriptions/verify", token=tok_a, body={"subscription_id": "subref_b"}, label="[A verifies B's subscription id]")
check("20.2", "A verify B's subscription -> 403 subscription_forbidden", st == 403 and detail(js) == "subscription_forbidden", f"({st} {detail(js)})")
st, js = call("POST", "/api/v1/subscriptions/verify", token=tok_a, body={"subscription_id": "never-seen"}, label="[A verifies unknown id]")
check("20.2", "unknown subscription id -> 403", st == 403, f"({st} {detail(js)})")
st, js = call("POST", "/api/v1/subscriptions/verify", token=tok_a, body={"subscription_id": "subref_a"}, label="[A verifies own mapped id, no Cashfree creds in test env]")
say(f"  own mapped id with no Cashfree credentials -> {st} {detail(js)} (ACTIVE-only logic needs Cashfree: covered by unit test, BLOCKED here)")
check("20.2", "own id passes the owner check (fails later on missing Cashfree creds, never upgrades)", st in (500, 400, 502), f"({st})")
plan_a1 = call("GET", "/api/v1/entitlements/current", token=tok_a, log=False)[1]["plan"]
check("20.2", "A's plan unchanged by verify calls", plan_a0 == plan_a1, f"({plan_a0}->{plan_a1})")
st, js = call("POST", "/api/v1/subscriptions/verify", body={"subscription_id": "subref_b"}, label="[no token]")
check("20.1", "verify without token 401", st == 401)

say("\n==== webhooks (no bearer) ====")
for path in ("/api/v1/subscriptions/webhooks/cashfree", "/api/v1/subscriptions/webhooks/cashfree-orders"):
    st, js = call("POST", path, body={"cf_event": "SUBSCRIPTION_STATUS_CHANGE"}, label="[no secret configured, no bearer]")
    check("webhook", f"{path.split('/')[-1]} no secret -> 503 webhook_not_configured (not 401 by the gate)", st == 503 and detail(js) == "webhook_not_configured", f"({st} {detail(js)})")
    st, js = call("GET", path, label="[GET]")
    check("webhook", f"{path.split('/')[-1]} GET not served", st in (401, 405), f"({st})")
subprocess.run([sys.executable, os.path.join(HERE, "wf20_ctl.py"), "extra", "8143", "CASHFREE_WEBHOOK_SECRET=wf20-whsec"], capture_output=True)
X = "http://127.0.0.1:8143"
SECRET = "wf20-whsec"
sub = "/api/v1/subscriptions/webhooks/cashfree"; ordp = "/api/v1/subscriptions/webhooks/cashfree-orders"
st, js = call("POST", sub, body={"cf_event": "SUBSCRIPTION_STATUS_CHANGE", "cf_subReferenceId": "subref_b", "cf_status": "ACTIVE"}, base=X, label="[secret set, unsigned]")
check("webhook", "subscription webhook unsigned -> 400 invalid_signature", st == 400 and detail(js) == "invalid_signature", f"({st} {detail(js)})")
st, js = call("POST", sub, body={"cf_event": "SUBSCRIPTION_STATUS_CHANGE", "cf_subReferenceId": "subref_b", "cf_status": "ACTIVE", "signature": "AAAA"}, base=X, label="[secret set, wrong signature]")
check("webhook", "subscription webhook wrong signature -> 400", st == 400 and detail(js) == "invalid_signature", f"({st} {detail(js)})")
payload = {"cf_event": "SUBSCRIPTION_STATUS_CHANGE", "cf_subReferenceId": "subref_w", "cf_subscriptionId": "x", "cf_status": "ACTIVE", "cf_planId": ""}
seed("kv", "cashfree_sub_workspace", "subref_w", json.dumps({"workspace_id": "org_w", "tier": "starter"}))
msg = "".join(f"{k}{payload[k]}" for k in sorted(k for k in payload if k.startswith("cf_")))
payload["signature"] = base64.b64encode(hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).digest()).decode()
st, js = call("POST", sub, body=payload, base=X, label="[secret set, correctly signed]")
check("webhook", "subscription webhook signed -> 200", st == 200, f"({st} {detail(js)})")
tok_w = mint("user_w", "org_w", "org:admin")
pw = call("GET", "/api/v1/entitlements/current", token=tok_w, log=False)[1]
check("webhook", "signed webhook upgraded the mapped workspace only", isinstance(pw, dict) and pw.get("plan") == "starter", f"(org_w plan={pw.get('plan') if isinstance(pw, dict) else pw})")
raw = json.dumps({"type": "SOMETHING_IGNORED", "data": {}}).encode(); ts = str(int(time.time()))
st, js = call("POST", ordp, raw_body=raw, headers={"Content-Type": "application/json"}, base=X, label="[orders, unsigned]")
check("webhook", "orders webhook unsigned -> 400 invalid_signature", st == 400 and detail(js) == "invalid_signature", f"({st} {detail(js)})")
st, js = call("POST", ordp, raw_body=raw, headers={"Content-Type": "application/json", "x-webhook-timestamp": ts, "x-webhook-signature": "AAAA"}, base=X, label="[orders, wrong sig]")
check("webhook", "orders webhook wrong signature -> 400", st == 400 and detail(js) == "invalid_signature", f"({st} {detail(js)})")
sig = base64.b64encode(hmac.new(SECRET.encode(), ts.encode() + raw, hashlib.sha256).digest()).decode()
st, js = call("POST", ordp, raw_body=raw, headers={"Content-Type": "application/json", "x-webhook-timestamp": ts, "x-webhook-signature": sig}, base=X, label="[orders, correctly signed, ignored event]")
check("webhook", "orders webhook signed -> 200", st == 200, f"({st} {detail(js)})")
say(f"  A plan after webhook phase: {plan_of(tok_a)}")
subprocess.run([sys.executable, os.path.join(HERE, "wf20_ctl.py"), "stop-extra"], capture_output=True)

say(f"  A plan before sweep: {plan_of(tok_a)}; B plan: {plan_of(tok_b)}")
plan_flip = []
say("\n==== OpenAPI sweep: every route taking an id, A's token against B's ids ====")
spec = call("GET", "/openapi.json", token=tok_a, log=False)[1]
check("sweep", "OpenAPI has no /jobs routes", not any(p.startswith("/api/v1/jobs") for p in spec["paths"]))
PMAP = {"workspace_id": slug_b, "company": slug_b, "slug": slug_b, "installer_version": "v1", "bundle_slug": slug_b, "workflow_id": b_wf,
        "run_id": "run-b-secret", "app_key": "b.example", "contribution_id": "skill:b_secret_skill", "group_id": "x", "version": "1.0.0"}
IDS_BODY = {"workspace_id": "org_b", "company": "org_b", "company_id": "org_b", "slug": "org_b", "skill_slug": "b_secret_skill", "skill_id": "b_secret_skill",
            "workflow_id": b_wf, "job_id": job_b, "resource_id": "org_b", "subscription_id": "subref_b", "order_id": "b-order", "machine_id": "b-machine",
            "reservation_id": "b-res", "run_id": "run-b-secret", "id": b_wf, "app_key": "b.example", "email": "b@t.test", "grant_id": "b-grant", "installer_version": "v1"}
IDS_Q = "workspace_id=org_b&company=org_b&slug=org_b&skill_slug=b_secret_skill&job_id=" + str(job_b) + "&workflow_id=" + b_wf + "&version=1.0.0"
MARK = ["B-SECRET-WF", "b_secret_skill", "run-b-secret", "user_b", "b@t.test", "SECRET screen text", "b-original.example", "B-ORIGINAL-INSTALLER", "b.example", "subref_b"]
sweep, leaks, twoxx = [], [], []
n = 0
for p, ops in sorted(spec["paths"].items()):
    for m in ops:
        M = m.upper()
        if M not in ("GET", "POST", "PUT", "PATCH", "DELETE"): continue
        variants = [PMAP]
        if "{installer_version}" in p: variants = [PMAP, {**PMAP, "installer_version": slug_b}]
        if "{bundle_slug}" in p: variants = [PMAP, {**PMAP, "bundle_slug": "b_secret_skill"}]
        if "{company}" in p and "{slug}" in p: variants = [{**PMAP, "slug": "b_secret_skill"}]
        for v in variants:
            url = re.sub(r"\{([^}]+)\}", lambda mo: v.get(mo.group(1), "x"), p)
            url += ("&" if "?" in url else "?") + IDS_Q
            st, js = call(M, url, token=tok_a, body=IDS_BODY if M in ("POST", "PUT", "PATCH", "DELETE") else None, headers={"X-Conxa-Client": "build-studio"}, log=False)
            n += 1
            if "entitlements/current" not in url and plan_of(tok_a) != "pro" and not plan_flip:
                plan_flip.append((M, url.split("?")[0], st)); say(f"  !! A's plan stopped being pro right after {M} {url.split('?')[0]} -> {st}")
            txt = json.dumps(js) if not isinstance(js, str) else js
            # a marker that is only A's own request echoed back (path/query) is not B's data
            sent = url + json.dumps(IDS_BODY if M != "GET" else "")
            # public installer download is public by design (anyone may fetch any slug); a marker that is only the caller's own request echoed back is not B's data
            body_marks = [k for k in MARK if k in txt and k not in sent and "/installers/" not in url]
            sweep.append((M, p, url.split("?")[0], st, body_marks))
            if body_marks and st < 400: leaks.append((M, url.split("?")[0], st, body_marks))
            if 200 <= st < 300: twoxx.append((M, url.split("?")[0], st, txt[:140]))
            if st >= 500 or st == -1: say(f"  5xx: {M} {url.split('?')[0]} -> {st} {txt[:100]}")
say(f"  plan flips during sweep: {plan_flip}; A plan after: {plan_of(tok_a)}")
say(f"  {n} calls over {len(spec['paths'])} OpenAPI paths")
hist = {}
for _m, _p, _u, st, _ in sweep: hist[st] = hist.get(st, 0) + 1
say(f"  status histogram: {dict(sorted(hist.items()))}")
say("  every 2xx (A's token, B's ids) listed for review:")
for r in twoxx: say(f"    {r[0]} {r[1]} -> {r[2]} {r[3]}")
check("sweep", "no 2xx response carries B's data", not leaks, str(leaks[:5]))
S1 = snap_b()
strip = lambda o: json.loads(re.sub(r'"request_id": "[0-9a-f]+"', '"request_id": ""', json.dumps(o, sort_keys=True).replace("'", '"'))) if not isinstance(o, str) else o
diffs = [k for k in S0 if strip(S0[k]) != strip(S1[k])]
check("sweep", "B's state identical before/after the sweep", not diffs, f"(changed: {diffs})")
for k in diffs: say(f"    {k}: before={str(S0[k])[:200]} after={str(S1[k])[:200]}")
# explicit cancel-nothing check for sync / tracking tokens
st, js = call("GET", f"/api/v1/skill-packs/{slug_b}/delta", headers={"Authorization": f"Bearer {sync_b}"}, label="[CONTROL B's own sync token]")
check("sweep", "control: B's own sync token still works", st == 200)

say(f"\nFAILS: {fails}")
if any(f.startswith(("20.2", "sweep", "webhook")) for f in fails):
    record("20.2", "FAIL", "S0", "; ".join(fails)[:600])
else:
    record("20.2", "PASS", "", "jobs routes gone; installer/policy/verify cross-company refused; B unchanged; sweep clean")
save_results("results_f.json")
