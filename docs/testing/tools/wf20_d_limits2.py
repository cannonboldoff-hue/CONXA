"""WF-20 rows 20.10-20.13 (workflows, trial expiry, distribution/white-label, ops tiers). usage: python wf20_d_limits2.py [10 11 12 13]"""
import sys, os, json, time, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wf20_lib import *

rows = sys.argv[1:] or ["10", "11", "12", "13"]
open_transcript("wf20_d_limits2_" + "_".join(rows) + ".txt")
TAG = os.environ.get("WF20_TAG", "d" + str(int(time.time()))[-5:])
say(f"== WF-20 D rows {rows} tag={TAG} ==")


def lock_status(tok, label):
    j = cur(tok, label)
    return j["workflow_lock"] if j else None


def fill(tok, prefix, n, quiet=True):
    bad = []
    for i in range(n):
        st, js = publish(tok, f"{prefix}{i:03d}", "1.0.0")
        if st != 200: bad.append((i, st, detail(js)))
    return bad


# ------------------------------------------------------------------ 20.10
def row10():
    say("\n#### 20.10 workflows (publish ledger; cap = compile-credit number)")
    problems = []
    for plan, cap, *_ in PLANS:
        ws, tok = newws("d10" + TAG, plan)
        n = cap if cap else 30
        say(f"\n-- {plan}: cap={cap}; ledger holds {n} workflows (first {n - 2} written by the product's own ledger function in the test store because a publish costs ~0.5 s here, the last 2 through real POST /workflows/publish)")
        seed("workflows", ws, n - 2)
        bad = []
        for i in range(n - 2, n):
            st, js = publish(tok, f"s{i:03d}", "1.0.0")
            if st != 200: bad.append((i, st, detail(js)))
        if bad: problems.append(f"{plan}: publish within cap failed {bad[:2]}")
        lock_status(tok, "at cap")
        st, js = publish(tok, "s_over", "1.0.0")
        if cap:
            if not (st == 402 and detail(js) == "workflow_limit_exceeded"): problems.append(f"{plan}: cap+1 new workflow got {st} {detail(js)}")
        elif st != 200: problems.append(f"{plan}: unlimited plan refused a new workflow ({st} {detail(js)})")
        st, js = publish(tok, "s000", "1.0.1")
        if st != 200: problems.append(f"{plan}: republish of an existing workflow at the cap got {st} {detail(js)}")
        if plan == "pro":
            say("\n  -- downgrade pro -> starter (cap 200 with 500 published)")
            set_plan(ws, "starter")
            ls = lock_status(tok, "after downgrade")
            names = [w["workflow_id"] for w in ls["workflows"]]
            locked = sorted(w["workflow_id"] for w in ls["workflows"] if w["locked"])
            expect = [f"s{i:03d}" for i in range(0, 300)]
            if ls["locked"] != 300 or locked != expect: problems.append(f"pro->starter: locked={ls['locked']} oldest-first check={'OK' if locked == expect else 'WRONG SET'}")
            else: say("  locked set == the 300 OLDEST (s000..s299): yes")
            st, js = publish(tok, "s000", "1.0.2")
            say(f"  republish oldest (locked) -> {st} {detail(js)}")
            if not (st == 402 and detail(js) == "workflow_locked"): problems.append(f"downgrade: locked republish got {st} {detail(js)}")
            st, js = publish(tok, "s499", "1.0.1")
            if st != 200: problems.append(f"downgrade: newest active republish got {st} {detail(js)}")
            st, js = publish(tok, "s_new", "1.0.0")
            if not (st == 402 and detail(js) == "workflow_limit_exceeded"): problems.append(f"downgrade: new workflow got {st} {detail(js)}")
            set_plan(ws, "pro")
            ls = lock_status(tok, "after upgrade back to pro")
            st, js = publish(tok, "s000", "1.0.3")
            if ls["locked"] != 0 or st != 200: problems.append(f"upgrade did not unlock: locked={ls['locked']} republish={st} {detail(js)}")
            # Pro -> Free: locked everything but 25
            set_plan(ws, "free")
            ls = lock_status(tok, "pro->free")
            if ls["active"] != 25: problems.append(f"pro->free active={ls['active']}")
            set_plan(ws, "pro")
        if plan == "free":
            say("\n  -- free ws: upgrade to starter lets a 26th workflow in")
            set_plan(ws, "starter")
            st, js = publish(tok, "s_after_upgrade", "1.0.0")
            if st != 200: problems.append(f"free->starter upgrade: 26th workflow got {st} {detail(js)}")
    record("20.10", "PASS" if not problems else "FAIL", "" if not problems else "S1", "; ".join(problems) or "caps 25/200/500 -> 402 workflow_limit_exceeded; republish at cap OK; Pro->Starter locks the 300 oldest (workflow_locked), newest still republishes; upgrade unlocks; Enterprise unlimited")


# ------------------------------------------------------------------ 20.11
def row11():
    say("\n#### 20.11 trial expiry (Free, 30 days) - backdated via the test store; sync + tracking must keep working")
    problems = []
    ws, tok = newws("d11" + TAG, "free")
    st, pub = publish(tok, "keep_working", "1.0.0")
    trk, syn, slug = pub["tracking"]["tracking_token"], pub["sync_token"], pub["slug"]
    cur(tok, "free, day 0")
    seed("billing", ws, json.dumps({"trial_started_at": time.time() - 29 * 86400}))
    j = cur(tok, "day 29")
    if j["trial_expired"]: problems.append("trial expired at day 29")
    st, _ = reserve(tok, f"{ws}-ok29")
    if st != 200: problems.append(f"day 29 reserve got {st}")
    seed("billing", ws, json.dumps({"trial_started_at": time.time() - 31 * 86400}))
    j = cur(tok, "day 31 (expired)")
    if not j["trial_expired"]: problems.append("trial not expired at day 31")
    checks = {}
    st, js = reserve(tok, f"{ws}-x", log=True); checks["compile reserve"] = (st, detail(js))
    st, js = proxy(tok, "text", "compile", 500, log=True); checks["LLM proxy compile"] = (st, detail(js))
    st, js = proxy(tok, "text", "human_edit", 500, log=True); checks["LLM proxy human_edit"] = (st, detail(js))
    st, js = publish(tok, "after_expiry", "1.0.0"); checks["publish"] = (st, detail(js))
    st, js = call("POST", "/api/v1/workflows/installer/upload?version=1.0.0&release_notes=x", token=tok, raw_body=b"MZ-X", headers={"Content-Type": "application/octet-stream"}, label="[installer upload]"); checks["installer upload"] = (st, detail(js))
    st, js = call("POST", "/api/v1/video/analyze?duration_s=5", token=tok, raw_body=b"v", headers={**H_STUDIO, "Content-Type": "video/mp4"}, label="[video analyze]"); checks["video analyze"] = (st, detail(js))
    for k, (st, d) in checks.items():
        if not (st == 402 and d == "trial_expired"): problems.append(f"expired trial: {k} -> {st} {d} (wanted 402 trial_expired)")
    say("  -- things that must KEEP working on an expired trial --")
    st, js = call("GET", f"/api/v1/skill-packs/{slug}/delta", headers={"Authorization": f"Bearer {syn}"}, label="[runtime sync with sync token]")
    if st != 200: problems.append(f"sync after expiry -> {st} {detail(js)}")
    now_ms = int(time.time() * 1000)
    st, js = call("POST", f"/api/v1/tracking/{slug}/events", headers={"x-tracking-token": trk}, body={"rid": "run-after-expiry", "wfid": "keep_working", "wfv": "1.0.0", "evts": [{"e": "wf_start", "ts": now_ms}]}, label="[runtime telemetry]")
    if st != 202: problems.append(f"tracking after expiry -> {st} {detail(js)}")
    st, js = call("POST", f"/api/tracking/{slug}/events", headers={"x-tracking-token": trk}, body={"rid": "run-after-expiry2", "evts": [{"e": "wf_start", "ts": now_ms}]}, label="[runtime telemetry, bare /api/tracking alias]")
    if st != 202: problems.append(f"bare tracking alias after expiry -> {st}")
    st, js = call("GET", "/api/v1/workflows", token=tok, label="[dashboard list workflows]")
    st2, js2 = call("GET", "/api/v1/entitlements/current", token=tok, log=False)
    if st != 200 or st2 != 200: problems.append("dashboard reads blocked on expired trial")
    # paid plans never expire, even with an ancient trial_started_at
    for plan in ("starter", "pro", "enterprise"):
        ws2, tok2 = newws("d11p" + TAG, plan)
        call("GET", "/api/v1/entitlements/current", token=tok2, log=False)
        seed("billing", ws2, json.dumps({"trial_started_at": time.time() - 400 * 86400}))
        j2 = call("GET", "/api/v1/entitlements/current", token=tok2, log=False)[1]
        st, js = publish(tok2, "paid_ok", "1.0.0")
        say(f"  {plan} with 400-day-old trial stamp: trial_expired={j2['trial_expired']} publish={st}")
        if j2["trial_expired"] or st != 200: problems.append(f"{plan} treated as expired trial ({st})")
    # upgrade the expired free ws
    set_plan(ws, "starter")
    j = cur(tok, "expired ws upgraded to starter")
    st, js = publish(tok, "after_upgrade", "1.0.0")
    if j["trial_expired"] or st != 200: problems.append(f"upgrade did not clear the expiry ({st} {detail(js)})")
    record("20.11", "PASS" if not problems else "FAIL", "" if not problems else "S1", "; ".join(problems) or "day 29 ok; day 31: reserve/LLM(compile+human-edit)/publish/installer/video -> 402 trial_expired; sync (200), tracking (202, both URLs), dashboard reads keep working; paid plans never expire; upgrade clears it. (Backdated through the test store: a 30-day wait is not possible.)")


# ------------------------------------------------------------------ 20.12
def row12():
    say("\n#### 20.12 external distribution + white-label (installer upload gate)")
    problems = []
    expect = {"free": (402, 200, 402, 402), "starter": (200, 200, 402, 402), "pro": (200, 200, 402, 402), "enterprise": (200, 200, 200, 200)}
    # tuple: (external ok?, internal ok?, white_label internal?, white_label external?) -> statuses
    for plan, *_ in PLANS:
        ws, tok = newws("d12" + TAG, plan)
        say(f"\n-- {plan}")
        cur(tok, "before")
        def up(ver, dist, wl):
            return call("POST", f"/api/v1/workflows/installer/upload?version={ver}&release_notes=t&distribution={dist}&white_label={'true' if wl else 'false'}", token=tok,
                        raw_body=b"MZ-" + ver.encode(), headers={"Content-Type": "application/octet-stream"}, label=f"[upload {dist} wl={wl}]")
        got = (up("1.0.0", "external", False)[0], up("1.0.1", "internal", False)[0], up("1.0.2", "internal", True), up("1.0.3", "external", True))
        gs = (got[0], got[1], got[2][0], got[3][0])
        say(f"  statuses (external, internal, white-label internal, white-label external) = {gs}; wl error ids: {detail(got[2][1])}, {detail(got[3][1])}")
        want = expect[plan]
        if gs != want: problems.append(f"{plan}: got {gs} wanted {want}")
        if plan == "free":
            st, js = up("1.0.4", "external", False)
            say(f"  free external body: {js}")
            if detail(js) != "distribution_not_permitted": problems.append(f"free external error id {detail(js)}")
        for st_, js_ in (got[2], got[3]):
            if st_ == 402 and detail(js_) != "white_label_not_permitted" and not (plan == "free" and detail(js_) == "distribution_not_permitted"):
                problems.append(f"{plan}: white-label refusal id {detail(js_)}")
        # member (non-admin) cannot upload at all
        mem = mint(f"user_d12{TAG}_{plan}_mem", ws, "org:member")
        sm, jm = call("POST", "/api/v1/workflows/installer/upload?version=9.0.0&release_notes=t", token=mem, raw_body=b"MZ", headers={"Content-Type": "application/octet-stream"}, label="[member upload]")
        if sm != 403: problems.append(f"{plan}: member upload -> {sm}")
    record("20.12", "PASS" if not problems else "FAIL", "" if not problems else "S1", "; ".join(problems) or "Free internal-only (external 402 distribution_not_permitted); Starter/Pro external OK; white-label only Enterprise (402 white_label_not_permitted below). Matches pricing page; NOTE PRD.md says Starter is internal-only, pricing page + code say Starter external")


# ------------------------------------------------------------------ 20.13
def row13():
    say("\n#### 20.13 ops screens (403 ops_tier_required; basic on Starter, all on Pro+)")
    problems = []
    basic = ["/tracking/dashboard", "/tracking/activity", "/tracking/roi-assumptions", "/audit-events", "/tracking/{ws}/runs", "/tracking/workflows/{ws}/x"]
    full = ["/tracking/drift", "/tracking/{ws}/reconcile", "/app-graphs/b.example/field"]
    for plan, *_ in PLANS:
        ws, tok = newws("d13" + TAG, plan)
        say(f"\n-- {plan}")
        res = {}
        for grp, paths in (("basic", basic), ("full", full)):
            for p in paths:
                st, js = call("GET", "/api/v1" + p.replace("{ws}", ws), token=tok, log=False)
                res[p] = (st, detail(js))
                say(f"  [{grp}] GET {p.replace('{ws}', ws)} -> {st} {detail(js) if st != 200 else ''}")
        st, js = call("PUT", f"/api/v1/tracking/{ws}/policy", token=tok, body={"denied_hosts": []}, label="[full] PUT policy")
        res["PUT policy"] = (st, detail(js))
        for p, (st, d) in res.items():
            kind = "full" if (p in full or p == "PUT policy") else "basic"
            allowed = {"free": False, "starter": kind == "basic", "pro": True, "enterprise": True}[plan]
            if allowed and st == 403 and d == "ops_tier_required": problems.append(f"{plan}: {p} wrongly refused")
            if allowed and st >= 400 and not (st == 403 and d == "ops_tier_required"): problems.append(f"{plan}: {p} -> {st} {d} (allowed tier errored)")
            if not allowed and not (st == 403 and d == "ops_tier_required"): problems.append(f"{plan}: {p} -> {st} {d} (wanted 403 ops_tier_required)")
    record("20.13", "PASS" if not problems else "FAIL", "" if not problems else "S1", "; ".join(problems[:6]) or "Free: all 403 ops_tier_required; Starter: basic 200, full (drift/reconcile/policy/field) 403; Pro+Enterprise: all 200")


for r in rows:
    {"10": row10, "11": row11, "12": row12, "13": row13}[r]()
save_results("results_d_" + "_".join(rows) + ".json")
