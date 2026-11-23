"""WF-20 row 20.2: company A's token against company B's data. Run after wf20_ctl.py up fresh (+ wf20_a_auth.py is independent)."""
import sys, os, json, time, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wf20_lib import *

open_transcript("wf20_b_cross.txt")
say("== WF-20 B: cross-company (A = org_a, B = org_b), both Pro so every gate is open and only identity can stop A ==")
tok_a = mint("user_a", "org_a", "org:admin", email="a@t.test")
tok_b = mint("user_b", "org_b", "org:admin", email="b@t.test")
set_plan("org_a", "pro")
set_plan("org_b", "pro")
cur(tok_a, "A")
cur(tok_b, "B")

MARK = ["B-SECRET-WF", "b_secret_skill", "run-b-secret", "user_b", "b@t.test", "org_b", "SECRET screen text", "b-original.example", "B-ORIGINAL-INSTALLER"]


def leak(js, path=""):
    # a marker that is only the caller's own request path echoed back (e.g. {"slug":"org_b","versions":[]}) is not B's data
    t = json.dumps(js) if not isinstance(js, str) else js
    return [m for m in MARK if m in t and m not in path]


say("\n-- seed B's data --")
st, pub_b = publish(tok_b, "b_secret_skill", "1.0.0")
trk_b = pub_b["tracking"]["tracking_token"]
sync_b = pub_b["sync_token"]
slug_b = pub_b["slug"]
say(f"  B slug={slug_b} (tracking+sync tokens captured, not printed)")
st, js = call("POST", "/api/v1/workflows", token=tok_b, body={"name": "B-SECRET-WF", "target_url": "https://b.example"}, label="[B create workflow]")
b_wf = js["workflow"]["id"]
now_ms = int(time.time() * 1000)
call("POST", f"/api/v1/tracking/{slug_b}/events", headers={"x-tracking-token": trk_b},
     body={"rid": "run-b-secret", "wfid": "b_secret_skill", "wfv": "1.0.0", "evts": [{"e": "wf_start", "ts": now_ms - 2000}, {"e": "wf_ok", "ts": now_ms - 1000, "dur": 500, "tot": 1, "rec": 0}]},
     label="[B telemetry]")
call("PUT", f"/api/v1/tracking/{slug_b}/policy", token=tok_b, body={"denied_hosts": ["b-original.example"]}, label="[B own policy]")
call("POST", "/api/v1/workflows/installer/upload?version=1.0.0&release_notes=orig&distribution=internal", token=tok_b, raw_body=b"MZ-B-ORIGINAL-INSTALLER",
     headers={"Content-Type": "application/octet-stream"}, label="[B own installer]")
call("PUT", "/api/v1/app-graphs/b.example/contributions/skill:b_secret_skill", token=tok_b,
     body={"id": "skill:b_secret_skill", "app_key": "b.example"}, label="[B app-graph contribution]")
st, js = call("POST", "/api/v1/video/analyze?duration_s=5", token=tok_b, raw_body=b"fakevideo", headers={"X-Conxa-Client": "build-studio", "Content-Type": "video/mp4"}, label="[B video job]")
job_b = js.get("job_id") if isinstance(js, dict) else None
time.sleep(2)
st, js = call("GET", f"/api/v1/video/analyze/{job_b}", token=tok_b, headers={"X-Conxa-Client": "build-studio"}, label="[B reads own job]")
b_job_result_visible_to_b = isinstance(js, dict) and js.get("status") == "succeeded"
say(f"  B job {job_b}: succeeded for B = {b_job_result_visible_to_b}")
st, pub_a = publish(tok_a, "a_skill", "1.0.0")
sync_a = pub_a["sync_token"]
trk_a = pub_a["tracking"]["tracking_token"]
call("PUT", "/api/v1/app-graphs/a.example/contributions/skill:a_skill", token=tok_a, body={"id": "skill:a_skill", "app_key": "a.example"}, label="[A own app-graph]")

say("\n-- A reads (every GET route; response scanned for any B marker) --")
gets = ["/workflows", "/workflows/skill-packs", "/workflows/skill-packs/current", "/workflows/generations", "/workflows/installer/versions",
        "/tracking/companies", "/tracking/dashboard?range=30d", "/tracking/activity", "/tracking/diagnostics", "/tracking/drift", "/tracking/roi-assumptions",
        "/audit-events", "/usage", "/dashboard", "/me", "/workspaces/current", "/billing/subscription", "/entitlements/machines", "/entitlements/execute-grants",
        "/entitlements/installer-domain", "/execute/contexts", "/telemetry/runtimes", "/legal/acceptances", "/legal/current", "/app-graphs",
        "/workspace/llm-key", "/subscriptions/plans", "/subscriptions/addons",
        f"/packages/bundles/{slug_b}/release", "/packages/bundles/b_secret_skill/release",
        "/workflows/v1/deployments", "/workflows/v1/groups", "/workflows/v1/releases/events?skill_slug=b_secret_skill",
        "/workflows/v1/releases/1.0.0?skill_slug=b_secret_skill", "/workflows/v1/releases/1.0.0/diff?skill_slug=b_secret_skill",
        "/workflows/v1/skill-packs/versions?skill_slug=b_secret_skill", f"/workflows/{slug_b}/installer/versions", f"/workflows/{b_wf}",
        f"/tracking/{slug_b}/runs", f"/tracking/{slug_b}/runs/run-b-secret", f"/tracking/{slug_b}/reconcile",
        f"/tracking/workflows/{slug_b}/b_secret_skill", "/tracking/workflows/org_a/b_secret_skill",
        "/app-graphs/b.example/contributions", "/app-graphs/b.example/field", "/app-graphs/a.example/contributions",
        "/jobs", f"/jobs/{job_b}", f"/jobs/{job_b}/events", f"/video/analyze/{job_b}",
        f"/tracking/{slug_b}/policy", f"/skill-packs/{slug_b}/delta", f"/workflows/v1/{slug_b}/skill-packs/delta"]
leaks = []
for g in gets:
    st, js = call("GET", "/api/v1" + g, token=tok_a, headers={"X-Conxa-Client": "build-studio"}, log=False)
    l = leak(js, g)
    say(f"  A GET {g[:92]} -> {st} {'LEAK ' + str(l) if l else 'clean'}  {str(js)[:90]}")
    if l:
        leaks.append((g, st, l))

say("\n-- A writes against B --")
st, js = call("DELETE", f"/api/v1/workflows/{b_wf}", token=tok_a, label="[A delete B's workflow]")
st, js = call("GET", f"/api/v1/workflows/{b_wf}", token=tok_b, label="[B still has workflow?]")
b_wf_alive = st == 200
st, js = call("PUT", f"/api/v1/tracking/{slug_b}/policy", token=tok_a, body={"denied_hosts": ["EVIL-A-WROTE-THIS.example"], "enforce": True}, label="[A writes B's governance policy]")
policy_write_status = st
st, pol0 = call("GET", f"/api/v1/tracking/{slug_b}/policy", headers={"x-tracking-token": trk_b}, label="[runtime-style fetch: tracking token only, no bearer]")
policy_runtime_fetch_status = st
st, pol = call("GET", f"/api/v1/tracking/{slug_b}/policy", token=tok_b, headers={"x-tracking-token": trk_b}, label="[B fetches its policy with JWT + tracking token]")
policy_overwritten = isinstance(pol, dict) and "evil-a-wrote-this.example" in json.dumps(pol)
st, js = call("POST", f"/api/v1/workflows/{slug_b}/installer/upload?version=9.9.9&release_notes=evil&distribution=internal", token=tok_a, raw_body=b"MZ-A-EVIL-INSTALLER",
              headers={"Content-Type": "application/octet-stream"}, label="[A uploads installer into B's slug via legacy route]")
inst_write_status = st
st, dl = call("GET", f"/api/v1/installers/{slug_b}", log=False)
say(f"  public download of B's installer now returns: {str(dl)[:60]!r} (status {st})")
installer_overwritten = isinstance(dl, str) and "A-EVIL-INSTALLER" in dl
st, js = call("GET", "/api/v1/workflows/installer/versions", token=tok_b, label="[B version list]")
call("PATCH", f"/api/v1/packages/bundles/{slug_b}/release", token=tok_a, body={"state": "draft"}, label="[A PATCH B bundle release]")
call("POST", "/api/v1/workflows/v1/skills/archive", token=tok_a, body={"skill_slug": "b_secret_skill"}, label="[A archive b_secret_skill (A's own slug)]")
call("POST", f"/api/v1/tracking/{slug_b}/events", headers={"x-tracking-token": trk_a}, body={"rid": "run-forged", "evts": [{"e": "wf_start", "ts": now_ms}]}, label="[A's tracking token into B's feed]")
call("GET", f"/api/v1/skill-packs/{slug_b}/delta", headers={"Authorization": f"Bearer {sync_a}"}, label="[A's sync token against B's pack]")
call("GET", f"/api/v1/skill-packs/{slug_b}/delta", label="[no sync token against B's pack]")
call("GET", f"/api/v1/skill-packs/{slug_b}/delta", headers={"Authorization": f"Bearer {sync_b}"}, label="[CONTROL B's own sync token]")
st, _ = call("POST", f"/api/v1/jobs/{job_b}/cancel", token=tok_a, label="[A cancels B's job]")
job_cancel_status = st
job_gone = {}
for jp in ("/jobs", f"/jobs/{job_b}", f"/jobs/{job_b}/events"):
    job_gone[jp] = call("GET", "/api/v1" + jp, token=tok_a, log=False)[0]
for jp in ("/jobs", f"/jobs/{job_b}", f"/jobs/{job_b}/events"):
    job_gone["B " + jp] = call("GET", "/api/v1" + jp, token=tok_b, log=False)[0]
say(f"  job routes (A and B) now: {job_gone}; cancel: {job_cancel_status}")
st, rs = call("GET", f"/api/v1/video/analyze/{job_b}", token=tok_b, headers={"X-Conxa-Client": "build-studio"}, label="[B's job after A's cancel]")
b_job_after = rs.get("status") if isinstance(rs, dict) else None
call("DELETE", "/api/v1/app-graphs/b.example/contributions/skill:b_secret_skill", token=tok_a, label="[A deletes B's app-graph (A's own namespace)]")
st, ag = call("GET", "/api/v1/app-graphs/b.example/contributions", token=tok_b, label="[B app-graph after]")
cur(tok_b, "B after attacks")
cur(tok_a, "A after attacks")

say("\n-- verdicts --")
say(f"  leaks in A's GET responses: {leaks}")
say(f"  B workflow alive after A's DELETE: {b_wf_alive}")
say(f"  A's PUT of B's policy: HTTP {policy_write_status}; B's policy now contains A's text: {policy_overwritten}")
say(f"  A's legacy installer upload into B's slug: HTTP {inst_write_status}; B's public download now A's bytes: {installer_overwritten}")
say(f"  B's own job after A's cancel call: {b_job_after}")
say(f"  runtime-style policy fetch (X-Tracking-Token only) status: {policy_runtime_fetch_status} (extra finding if 401)")
verdict_s0 = []
if policy_write_status != 403: verdict_s0.append(f"A PUT on B's policy got {policy_write_status}, want 403")
if inst_write_status != 403: verdict_s0.append(f"A upload into B's installer slot got {inst_write_status}, want 403")
if policy_runtime_fetch_status != 200: verdict_s0.append(f"runtime policy fetch with tracking token only got {policy_runtime_fetch_status}, want 200")
if job_cancel_status != 404 or any(v != 404 for v in job_gone.values()): verdict_s0.append(f"generic job routes still answer: {job_gone} cancel={job_cancel_status}")
if not b_job_result_visible_to_b: verdict_s0.append("B's own video job result no longer readable by B")
if leaks: verdict_s0.append(f"data leak via GET: {leaks}")
if not b_wf_alive: verdict_s0.append("B workflow deleted by A")
if policy_overwritten: verdict_s0.append(f"A overwrote B's governance policy (PUT /tracking/{{B}}/policy -> {policy_write_status})")
if installer_overwritten: verdict_s0.append(f"A replaced B's installer (POST /workflows/{{B slug}}/installer/upload -> {inst_write_status}; GET /installers/{{B slug}} serves A's bytes)")
if b_job_after == "canceled": verdict_s0.append("A cancelled B's job")
if verdict_s0:
    record("20.2", "FAIL", "S0", " | ".join(verdict_s0))
else:
    record("20.2", "PASS", "", "no B data in any A response; no write reached B")
save_results("results_b.json")
