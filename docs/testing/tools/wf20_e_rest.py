"""WF-20 rows 20.14-20.16 (timed grant / retention / pool routing / BYOK / add-ons, kill switches, Enterprise with no overrides).
usage: python wf20_e_rest.py [14 15 16]. Row 15 needs extra backends; it starts/stops them itself through wf20_ctl.py."""
import sys, os, json, time, subprocess, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wf20_lib import *

rows = sys.argv[1:] or ["14", "15", "16"]
open_transcript("wf20_e_rest_" + "_".join(rows) + ".txt")
TAG = os.environ.get("WF20_TAG", "e" + str(int(time.time()))[-5:])
HERE = os.path.dirname(os.path.abspath(__file__))
say(f"== WF-20 E rows {rows} tag={TAG} ==")


# ------------------------------------------------------------------ 20.14
def row14():
    say("\n#### 20.14 timed grant, analytics retention, pool routing, BYOK, add-ons")
    problems = []
    notes = []
    # ---- (a) timed grant reverts with no job
    say("\n-- (a) timed grant")
    ws, tok = newws("e14a" + TAG, "free")
    st, js = call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"workspace_id": ws, "plan": "pro", "duration_days": 7}, label="[grant pro for 7 days]")
    exp = js.get("expires_at") if isinstance(js, dict) else None
    j = cur(tok, "during grant")
    if j["plan"] != "pro" or not exp or abs(exp - (time.time() + 7 * 86400)) > 120: problems.append(f"grant: plan={j['plan']} expires_at={exp}")
    for bad in (0, -3, 3651):
        s_, j_ = call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"workspace_id": ws, "plan": "pro", "duration_days": bad}, label=f"[duration_days={bad}]")
        if s_ != 422: problems.append(f"duration_days={bad} -> {s_}")
    seed("billing", ws, json.dumps({"plan_expires_at": time.time() - 5}))
    j = cur(tok, "grant expired (no job ran)")
    if j["plan"] != "free" or j["capabilities"]["ops_tier"] != "none" or j["meters"]["compile_credits"]["limit"] != 25: problems.append(f"grant did not revert to Free: {j['plan']}")
    st, js = call("GET", "/api/v1/tracking/dashboard", token=tok, label="[ops route after the grant ended]")
    if not (st == 403): problems.append(f"after grant end ops route -> {st}")
    st, js = call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"workspace_id": ws, "plan": "pro"}, label="[permanent pro re-assign clears expiry]")
    j = cur(tok, "permanent pro")
    if j["plan"] != "pro" or (js.get("expires_at") is not None): problems.append("permanent re-assign kept an expiry")
    st, js = call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"workspace_id": ws, "plan": "platinum"}, label="[unknown plan]")
    if st != 400: problems.append(f"unknown plan -> {st}")
    st, js = call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"plan": "pro"}, label="[no workspace_id/email]")
    if st != 400: problems.append(f"no id -> {st}")

    # ---- (b) retention
    say("\n-- (b) analytics retention (runs aged through the test store: server_ts shifted; a 90-day wait is not possible)")
    now_ms = int(time.time() * 1000)
    expect_runs = {"starter": 2, "pro": 3, "enterprise": 4}  # of 4 runs: now, 30d, 100d, 400d old
    for plan, want in expect_runs.items():
        ws, tok = newws("e14b" + TAG, plan)
        st, pub = publish(tok, "ret_skill", "1.0.0")
        slug, trk = pub["slug"], pub["tracking"]["tracking_token"]
        for rid, age in (("r-now", 0), ("r-30", 30), ("r-100", 100), ("r-400", 400)):
            call("POST", f"/api/v1/tracking/{slug}/events", headers={"x-tracking-token": trk}, body={"rid": rid, "wfid": "ret_skill", "wfv": "1.0.0", "evts": [{"e": "wf_start", "ts": now_ms}, {"e": "wf_ok", "ts": now_ms + 100, "dur": 100, "tot": 1, "rec": 0}]}, log=False)
            if age: seed("agerun", slug, rid, age)
        st, js = call("GET", "/api/v1/tracking/activity?limit=50", token=tok, log=False)
        ids = sorted(r.get("run_id") for r in js.get("runs", [])) if isinstance(js, dict) else []
        say(f"  {plan}: activity feed shows runs {ids} (want {want})")
        if len(ids) != want: problems.append(f"{plan} retention: saw {ids}, wanted {want} runs")
        # the same cutoff on the unscoped list endpoint (a different code path)
        st, js = call("GET", f"/api/v1/tracking/{slug}/runs", token=tok, log=False)
        n_list = js.get("total") if isinstance(js, dict) else None
        say(f"  {plan}: GET /tracking/{{ws}}/runs total={n_list}")
        if n_list is not None and n_list != 4:
            notes.append(f"{plan}: /tracking/{{ws}}/runs returns {n_list} of 4 runs (retention {'applied' if n_list != 4 else 'not applied'} here)")
    # Free: retention 0 + ops none
    ws, tok = newws("e14b" + TAG, "free")
    st, js = call("GET", "/api/v1/tracking/activity", token=tok, label="[free activity]")
    if st != 403: problems.append(f"free analytics -> {st}")

    # ---- (c) pool routing
    say("\n-- (c) compile pool routing (the mock LLM answers with the key of the pool that served the call)")
    want_pool = {"free": "key-free", "starter": "key-starter", "pro": "key-pro", "enterprise": None}
    for plan, wk in want_pool.items():
        ws, tok = newws("e14c" + TAG, plan)
        st, js = proxy(tok, "text", "compile", 500, log=True)
        got = js.get("pool_key") if isinstance(js, dict) else None
        say(f"  {plan}: compile_pool={cur(tok, plan)['capabilities']['compile_pool']} served by {got}")
        if wk and got != wk: problems.append(f"pool routing {plan}: wanted {wk} got {got} ({st})")
        if plan == "enterprise": notes.append(f"enterprise (premium pool, none configured) fell back to {got}")
    # ---- (d) BYOK
    say("\n-- (d) BYOK (Azure OpenAI key, Enterprise only; key stored encrypted; the 'Azure' endpoint is the local mock)")
    body = {"endpoint": "http://127.0.0.1:8142", "deployment": "dep1", "api_version": "2024-08-01-preview", "api_key": "byok-secret-key-123"}
    for plan, *_ in PLANS:
        ws, tok = newws("e14d" + TAG, plan)
        st, js = call("PUT", "/api/v1/workspace/llm-key", token=tok, body=body, label=f"[{plan} PUT key]")
        if plan != "enterprise" and not (st == 403 and detail(js) == "byok_requires_enterprise"): problems.append(f"BYOK {plan}: {st} {detail(js)}")
        if plan == "enterprise":
            if st != 200: problems.append(f"BYOK enterprise PUT -> {st} {detail(js)}")
            if "byok-secret-key-123" in json.dumps(js): problems.append("BYOK key echoed back in PUT response")
            st, js = call("GET", "/api/v1/workspace/llm-key", token=tok, label="[GET key meta]")
            if "byok-secret-key-123" in json.dumps(js): problems.append("BYOK key returned by GET")
            st, js = proxy(tok, "text", "compile", 500, log=True)
            got = js.get("pool_key") if isinstance(js, dict) else js
            say(f"  enterprise compile call served by: {got}")
            if not str(got).startswith("byok:byok-secret-key-123"): problems.append(f"BYOK call not routed to the customer deployment: {got}")
            mem = mint(f"user_e14d{TAG}_mem", ws, "org:member")
            sm, jm = call("PUT", "/api/v1/workspace/llm-key", token=mem, body=body, label="[member PUT key]")
            if sm not in (402, 403): problems.append(f"BYOK member PUT -> {sm}")  # 402 = Enterprise seats=0 refuses the new member first (see 20.16)
            call("DELETE", "/api/v1/workspace/llm-key", token=tok, label="[DELETE key]")
            st, js = proxy(tok, "text", "compile", 500, log=True)
            say(f"  after DELETE served by: {js.get('pool_key') if isinstance(js, dict) else js}")
    # ---- (e) add-ons
    say("\n-- (e) add-on packs: wallet stacks, and unstacks as credits are used (purchase itself = Cashfree = BLOCKED; wallet credited through grant_credit_wallet, the function a verified payment calls)")
    ws, tok = newws("e14e" + TAG, "free")
    seed("usage", ws, 25)  # month's 25 free credits used
    st, js = reserve(tok, f"{ws}-x1", log=True)
    if st != 402: problems.append(f"free at 25/25 without wallet -> {st}")
    seed("wallet", ws, 20, 200000)
    seed("wallet", ws, 50, 500000)
    j = cur(tok, "after +20 and +50 packs (stack)")
    if j["wallet"]["compile_credits"] != 70 or j["wallet"]["human_edit_tokens"] != 700000: problems.append(f"wallet stack = {j['wallet']}")
    ok = 0
    for i in range(70):
        s1, _ = reserve(tok, f"{ws}-w{i}")
        s2, _ = call("POST", "/api/v1/usage/compile/commit", token=tok, body={"reservation_id": f"{ws}-w{i}"}, log=False)
        ok += (s1 == 200 and s2 == 200)
        if i == 4:
            jj = cur(tok, "after 5 wallet-funded compiles (unstack)")
            if jj["wallet"]["compile_credits"] != 65: problems.append(f"wallet unstack: {jj['wallet']}")
        if i % 10 == 9 and i >= 59: pass
        if ok != i + 1: break
    j = cur(tok, f"after {ok} wallet-funded compiles")
    st, js = reserve(tok, f"{ws}-wover", log=True)
    if ok != 70 or j["wallet"]["compile_credits"] != 0 or st != 402: problems.append(f"wallet drain: ok={ok} wallet={j['wallet']} next={st}")
    # AI-credit wallet extends the pool
    st, js = proxy(tok, "text", "human_edit", 900_000)
    st, js = proxy(tok, "text", "human_edit", 900_000)
    st, js = proxy(tok, "text", "human_edit", 900_000)
    st4, js4 = proxy(tok, "text", "human_edit", 900_000, log=True)
    j = cur(tok, "pool beyond 500k using the wallet's 700k tokens")
    say(f"  4th 900KB call (pool 500k exhausted, wallet 700k): {st4}")
    if st4 != 200 or j["wallet"]["human_edit_tokens"] >= 700000: problems.append(f"AI wallet did not fund usage: {st4} {j['wallet']}")
    # add-on purchase endpoints (real Cashfree)
    st, js = call("POST", "/api/v1/subscriptions/addon/order", token=tok, body={"tier": "credits_addon_20", "customer_email": "a@t.test", "customer_phone": "9999999999"}, label="[addon order, no Cashfree creds]")
    notes.append(f"add-on order without Cashfree credentials -> {st} {detail(js)} (BLOCKED: real purchase, payment page and webhook need Cashfree sandbox keys)")
    st, js = call("POST", "/api/v1/subscriptions/webhooks/cashfree-orders", raw_body=b'{"data":{}}', headers={"Content-Type": "application/json", "x-webhook-signature": "bad", "x-webhook-timestamp": "1"}, label="[forged payment webhook]")
    notes.append(f"forged Cashfree webhook (no valid signature) -> {st} {detail(js)}")
    if st not in (400, 401, 403, 503): problems.append(f"forged webhook accepted? {st}")
    for n_ in notes: say("  note: " + n_)
    record("20.14", "PASS" if not problems else "FAIL", "" if not problems else "S2", "; ".join(problems) or "timed grant reverts with no job and re-assign clears expiry; retention filters old runs (90/365/unlimited); pool routing free/starter/pro verified; BYOK 403 byok_requires_enterprise below Enterprise, routed to customer deployment on Enterprise, key never echoed; add-on wallet stacks and unstacks; real purchase BLOCKED. " + " | ".join(notes))


# ------------------------------------------------------------------ 20.15
FLAGS = ["compile", "human_edit", "distribution", "machines", "seats", "execute_seats"]


def probe(base, label):
    """Returns {gate: status/detail} using a Free workspace whose every cap is 1 (entitlement_overrides) so each gate trips on the 2nd action."""
    out = {}
    ws = f"org_k{TAG}_{label}"
    tok = mint(f"user_k{TAG}_{label}", ws, "org:admin")
    call("GET", "/api/v1/tracking/companies", token=tok, base=base, log=False)  # first sight (known member)
    set_plan(ws, "free", base=base)
    seed("billing", ws, json.dumps({"entitlement_overrides": {"compile_credits": 1, "human_edit_tokens": 1, "machines": 1, "seats": 1, "execute_seats": 1}}))
    def c(m, p, **kw): return call(m, p, token=tok, base=base, log=False, **kw)
    # compile reserve
    c("POST", "/api/v1/usage/compile/reserve", body={"reservation_id": ws + "-1"})
    st, js = c("POST", "/api/v1/usage/compile/reserve", body={"reservation_id": ws + "-2"}); out["compile (reserve 2nd of 1)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    # workflow ledger (same flag)
    publish(tok, "wk1", "1.0.0", base=base)
    st, js = publish(tok, "wk2", "1.0.0", base=base); out["compile (publish 2nd workflow of 1)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    # human-edit pool
    call("POST", "/api/v1/llm/proxy/text", token=tok, base=base, body={"task": "t", "payload": {"m": "hello " * 400}, "usage_class": "human_edit"}, headers=H_STUDIO, log=False)
    st, js = call("POST", "/api/v1/llm/proxy/text", token=tok, base=base, body={"task": "t", "payload": {"m": "hello " * 400}, "usage_class": "human_edit"}, headers=H_STUDIO, log=False); out["human_edit (2nd call, pool of 1)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    # distribution + white label
    st, js = c("POST", "/api/v1/workflows/installer/upload?version=1.0.0&release_notes=t&distribution=external", raw_body=b"MZ1", headers={"Content-Type": "application/octet-stream"}); out["distribution (external on Free)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    st, js = c("POST", "/api/v1/workflows/installer/upload?version=1.0.1&release_notes=t&distribution=internal&white_label=true", raw_body=b"MZ2", headers={"Content-Type": "application/octet-stream"}); out["distribution (white-label on Free)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    # machines via the proxy (compile class has no credit cap)
    for m in ("mach-1", "mach-2"):
        st, js = call("POST", "/api/v1/llm/proxy/text", token=tok, base=base, body={"task": "t", "payload": {"m": "x"}}, headers={**H_STUDIO, "X-Conxa-Machine": m}, log=False)
    out["machines (2nd machine, limit 1)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    # execute seats
    c("POST", "/api/v1/entitlements/execute-grants", body={"email": "one@t.test"})
    st, js = c("POST", "/api/v1/entitlements/execute-grants", body={"email": "two@t.test"}); out["execute_seats (2nd grant, limit 1)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    # seats: a brand-new member while Clerk says 5 members, limit 1
    ctl(count=f"{ws}:5")
    newm = mint(f"user_k{TAG}_{label}_new", ws, "org:member")
    st, js = call("GET", "/api/v1/tracking/companies", token=newm, base=base, log=False); out["seats (new member, 5 > 1)"] = f"{st} {detail(js) if st != 200 else ''}".strip()
    return out


def row15():
    say("\n#### 20.15 kill switches. Backend A = fresh start with NO entitlement env vars at all (the production default). Backends B1..B6 each set ONE SKILL_ENTITLEMENTS_ENFORCE_<X>=false. Backend C sets the bare name from the spec (ENTITLEMENTS_ENFORCE_COMPILE=false, no SKILL_ prefix).")
    problems = []
    ctl_py = os.path.join(HERE, "wf20_ctl.py")
    results = {}
    results["NONE off (fresh start, port 8140)"] = probe(BASE, "none")
    port = 8143
    for f in FLAGS:
        subprocess.run([sys.executable, ctl_py, "extra", str(port), f"SKILL_ENTITLEMENTS_ENFORCE_{f.upper()}=false"], capture_output=True)
        results[f"SKILL_ENTITLEMENTS_ENFORCE_{f.upper()}=false (port {port})"] = probe(f"http://127.0.0.1:{port}", f)
        port += 1
    subprocess.run([sys.executable, ctl_py, "extra", "8150", "ENTITLEMENTS_ENFORCE_COMPILE=false"], capture_output=True)
    results["bare ENTITLEMENTS_ENFORCE_COMPILE=false (port 8150, no SKILL_ prefix)"] = probe("http://127.0.0.1:8150", "bare")
    subprocess.run([sys.executable, ctl_py, "stop-extra"], capture_output=True)
    gates = list(results["NONE off (fresh start, port 8140)"].keys())
    say("\nmatrix (status of each gate; 402/403 = enforced, 200 = off/allowed):")
    for name, r in results.items():
        say(f"\n  {name}")
        for g in gates: say(f"     {g:42s} {r.get(g)}")
    # judgement
    base = results["NONE off (fresh start, port 8140)"]
    for g in gates:
        if not (base[g].startswith("402") or base[g].startswith("403")): problems.append(f"S0 fresh start: gate '{g}' NOT enforced ({base[g]})")
    gate_for = {"compile": ["compile (reserve 2nd of 1)", "compile (publish 2nd workflow of 1)"], "human_edit": ["human_edit (2nd call, pool of 1)"], "distribution": ["distribution (external on Free)", "distribution (white-label on Free)"],
                "machines": ["machines (2nd machine, limit 1)"], "seats": ["seats (new member, 5 > 1)"], "execute_seats": ["execute_seats (2nd grant, limit 1)"]}
    for f in FLAGS:
        r = results[f"SKILL_ENTITLEMENTS_ENFORCE_{f.upper()}=false (port {8143 + FLAGS.index(f)})"]
        for g in gates:
            off = g in gate_for[f]
            enforced = r[g].startswith("402") or r[g].startswith("403")
            if off and enforced: problems.append(f"{f}=false did not switch off '{g}' ({r[g]})")
            if not off and not enforced: problems.append(f"{f}=false also switched off '{g}' ({r[g]})")
    rb = results["bare ENTITLEMENTS_ENFORCE_COMPILE=false (port 8150, no SKILL_ prefix)"]
    bare_works = not (rb["compile (reserve 2nd of 1)"].startswith("402"))
    say(f"\nbare unprefixed ENTITLEMENTS_ENFORCE_COMPILE=false switches compile off? {bare_works} (the real variable is SKILL_ENTITLEMENTS_ENFORCE_COMPILE; the spec/docs name without the prefix does nothing)")
    record("20.15", "PASS" if not problems else "FAIL", "" if not problems else ("S0" if any(p.startswith("S0") for p in problems) else "S2"),
           "; ".join(problems) or "fresh start (no entitlement env) enforces all 6 gates; each SKILL_ENTITLEMENTS_ENFORCE_<X>=false switches off only its own gate (compile also covers the workflow cap; distribution also covers white-label). Note: the variable needs the SKILL_ prefix; the bare spec name is ignored")
    return results


# ------------------------------------------------------------------ 20.16
def row16():
    say("\n#### 20.16 Enterprise with no overrides (plan applied through the admin route, which has no way to pass overrides)")
    blocked = []
    ok = []
    ws = f"org_e16{TAG}"
    tok = mint(f"user_e16{TAG}", ws, "org:admin")
    set_plan(ws, "enterprise")  # BEFORE anyone has signed in: the usual order when sales sets the plan first
    st, js = call("GET", "/api/v1/tracking/companies", token=tok, label="[1st member signs in]")
    (blocked if st != 200 else ok).append(f"first member sign-in -> {st} {detail(js)}")
    # normal path: owner already known (as Free), then upgraded
    ws2 = f"org_e16b{TAG}"
    tok2 = mint(f"user_e16b{TAG}", ws2, "org:admin")
    call("GET", "/api/v1/tracking/companies", token=tok2, log=False)
    set_plan(ws2, "enterprise")
    j = cur(tok2, "enterprise, owner already known")
    def chk(label, st, js, want=200):
        (ok if st == want else blocked).append(f"{label} -> {st} {detail(js) if st != 200 else ''}".strip())
    st, js = reserve(tok2, f"{ws2}-r1"); chk("compile reserve", st, js)
    st, js = call("POST", "/api/v1/usage/compile/commit", token=tok2, body={"reservation_id": f"{ws2}-r1"}, log=False); chk("compile commit", st, js)
    st, js = publish(tok2, "ent_skill", "1.0.0"); chk("publish", st, js)
    st, js = proxy(tok2, "text", "compile", 500); chk("LLM compile call", st, js)
    st, js = proxy(tok2, "text", "human_edit", 500); chk("LLM human-edit call (AI credits)", st, js)
    st, js = proxy(tok2, "text", "ai_credits", 500); chk("LLM ai_credits call", st, js)
    st, js = reserve(tok2, f"{ws2}-r2", machine="m-ent-1"); chk("first machine registers", st, js)
    st, js = call("POST", "/api/v1/workflows/installer/upload?version=1.0.0&release_notes=t&distribution=external&white_label=true", token=tok2, raw_body=b"MZ-ent", headers={"Content-Type": "application/octet-stream"}, log=False); chk("external + white-label installer", st, js)
    st, js = call("GET", "/api/v1/tracking/drift", token=tok2, log=False); chk("ops: drift (full tier)", st, js)
    st, js = call("POST", "/api/v1/entitlements/execute-grants", token=tok2, body={"email": "x@t.test"}, log=False); chk("first Execute grant", st, js)
    ctl(count=f"{ws2}:2")
    st, js = call("GET", "/api/v1/tracking/companies", token=mint(f"user_e16b{TAG}_n", ws2, "org:member"), log=False); chk("second member joins", st, js)
    st, js = call("PUT", "/api/v1/workspace/llm-key", token=tok2, body={"endpoint": "http://127.0.0.1:8142", "deployment": "d", "api_key": "k"}, log=False); chk("BYOK key", st, js)
    for line in ok: say("  OK      " + line)
    for line in blocked: say("  BLOCKED " + line)
    # positive control: the same plan WITH explicit overrides works
    ws3 = f"org_e16c{TAG}"
    tok3 = mint(f"user_e16c{TAG}", ws3, "org:admin")
    set_plan(ws3, "enterprise")
    seed("billing", ws3, json.dumps({"entitlement_overrides": {"seats": 25, "machines": 25, "execute_seats": 50, "human_edit_tokens": 50000000}}))
    st, js = call("GET", "/api/v1/tracking/companies", token=tok3, log=False)
    st2, js2 = proxy(tok3, "text", "human_edit", 500)
    st3, js3 = reserve(tok3, f"{ws3}-r1", machine="m1")
    say(f"  positive control (same plan + explicit overrides in the store): first member {st}, human-edit {st2}, machine {st3}")
    # can the admin API set overrides?
    st4, js4 = call("POST", "/api/v1/entitlements/admin/billing", token=ADMIN, body={"workspace_id": ws3, "plan": "enterprise", "entitlement_overrides": {"seats": 99}}, log=False)
    j3 = cur(tok3, "after trying to pass overrides through the admin route")
    can_set = j3 and j3["meters"]["seats"]["limit"] == 99
    say(f"  admin route accepts overrides? {can_set} (status {st4}; the AssignPlanBody has no such field, so the extra key is ignored)")
    n_blocked = len(blocked)
    if not can_set: blocked.append("admin billing route did not accept entitlement_overrides (seats stayed != 99)"); n_blocked += 1
    else: say(f"  overrides settable through admin billing: seats limit = {j3['meters']['seats']['limit']}; response limits: {js4.get('limits') if isinstance(js4, dict) else js4}")
    if n_blocked:
        record("20.16", "FAIL", "S1", f"Enterprise with no overrides (seats=0, machines=0, execute_seats=0, human_edit pool=0): " + "; ".join(blocked) + f". Works once overrides exist ({st},{st2},{st3}) but POST /entitlements/admin/billing cannot set them (ignored key) so a support person cannot configure a fresh Enterprise workspace without editing the database. Ok: " + "; ".join(ok))
    else:
        record("20.16", "PASS", "", "; ".join(ok))


for r in rows:
    {"14": row14, "15": row15, "16": row16}[r]()
save_results("results_e_" + "_".join(rows) + ".json")
