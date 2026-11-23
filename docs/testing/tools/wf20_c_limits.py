"""WF-20 rows 20.6-20.9 (compile credits, human-edit pool, machines, seats). usage: python wf20_c_limits.py [6 7 8 9]. Needs wf20_ctl.py up."""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wf20_lib import *

rows = sys.argv[1:] or ["6", "7", "8", "9"]
open_transcript("wf20_c_limits_" + "_".join(rows) + ".txt")
TAG = os.environ.get("WF20_TAG", "t" + str(int(time.time()))[-5:])  # fresh workspaces every run
say(f"== WF-20 C rows {rows} tag={TAG} ==")


def used(js, meter):
    m = js["meters"][meter]
    return m["used"], m["limit"], m.get("remaining")


# ------------------------------------------------------------------ 20.6
def row6():
    say("\n#### 20.6 compile credits (reserve -> commit; cap = plan limit; payment-shaped 402)")
    problems = []
    pending = []
    for plan, cap, *_ in PLANS:
        ws, tok = newws("c6" + TAG, plan)
        say(f"\n-- {plan}: cap={cap}")
        before = cur(tok, "before")
        if cap is None:
            ok = 0
            for i in range(30):
                st, js = reserve(tok, f"{ws}-e{i}")
                st2, _ = call("POST", "/api/v1/usage/compile/commit", token=tok, body={"reservation_id": f"{ws}-e{i}"}, log=False)
                ok += (st == 200 and st2 == 200)
            say(f"  30 reserve+commit on Enterprise (unlimited): {ok}/30 ok")
            if ok != 30: problems.append(f"{plan}: unlimited plan refused credits ({ok}/30)")
            cur(tok, "after")
            continue
        prefill = cap - 3
        if cap > 30:  # 0.4 s/request here: pre-fill the committed counter in the store (the exact row a real commit writes), real calls do the rest
            seed("usage", ws, prefill)
        else:
            for i in range(prefill):
                reserve(tok, f"{ws}-p{i}")
                call("POST", "/api/v1/usage/compile/commit", token=tok, body={"reservation_id": f"{ws}-p{i}"}, log=False)
        j = cur(tok, f"after {prefill} committed")
        if j["meters"]["compile_credits"]["used"] != prefill: problems.append(f"{plan}: prefill mismatch used={j['meters']['compile_credits']['used']}")
        # 3 slots left: reserve 3 (one of them twice)
        s1, j1 = reserve(tok, f"{ws}-a", log=True)
        s1b, j1b = reserve(tok, f"{ws}-a", log=True)  # same id twice
        s2, _ = reserve(tok, f"{ws}-b", log=True)
        s3, _ = reserve(tok, f"{ws}-c", log=True)
        j = cur(tok, "3 reserved (a twice)")
        res = j["meters"]["compile_credits"]
        if res["remaining"] != 0: problems.append(f"{plan}: same id twice counted more than once: remaining={res['remaining']} (expected 0)")
        s4, j4 = reserve(tok, f"{ws}-over", log=True)
        if not (s4 == 402 and detail(j4) == "compile_credit_limit_exceeded"): problems.append(f"{plan}: at cap got {s4} {detail(j4)}")
        # release returns the slot
        sr, _ = call("POST", "/api/v1/usage/compile/release", token=tok, body={"reservation_id": f"{ws}-b"}, label="[release b]")
        j = cur(tok, "after release b")
        if j["meters"]["compile_credits"]["remaining"] != 1: problems.append(f"{plan}: release did not return the credit")
        s5, _ = reserve(tok, f"{ws}-d", log=True)
        s6, j6 = reserve(tok, f"{ws}-over2", log=True)
        if s5 != 200 or s6 != 402: problems.append(f"{plan}: after release expected 200 then 402, got {s5},{s6}")
        # commit twice counts once; release after commit refused
        sc1, _ = call("POST", "/api/v1/usage/compile/commit", token=tok, body={"reservation_id": f"{ws}-a"}, label="[commit a]")
        sc2, jc2 = call("POST", "/api/v1/usage/compile/commit", token=tok, body={"reservation_id": f"{ws}-a"}, label="[commit a again]")
        sr2, jr2 = call("POST", "/api/v1/usage/compile/release", token=tok, body={"reservation_id": f"{ws}-a"}, label="[release a after commit]")
        j = cur(tok, "after commit a")
        u, l, _ = used(j, "compile_credits")
        if u != prefill + 1: problems.append(f"{plan}: commit counted wrongly used={u}")
        pending.append((plan, ws, tok, cap, prefill))
        # other pool unaffected: human-edit call still works with compile credits exhausted
        sh, jh = proxy(tok, "text", "human_edit", 2000)
        say(f"  human_edit proxy call with compile credits exhausted -> {sh} {str(jh)[:80]}")
        if sh != 200 and plan != "enterprise": problems.append(f"{plan}: compile exhaustion blocked the human-edit pool ({sh})")
    say("\n-- reservation expiry: server TTL is max(60s, setting) -> waiting 63s once for all plans (c and d reservations above were never committed) --")
    time.sleep(63)
    for plan, ws, tok, cap, prefill in pending:
        j = cur(tok, f"{plan} after TTL")
        if j["meters"]["compile_credits"]["remaining"] != cap - (prefill + 1):
            problems.append(f"{plan}: expired reservations still held: remaining={j['meters']['compile_credits']['remaining']} expected {cap - prefill - 1}")
        s7, _ = reserve(tok, f"{ws}-e1", log=True)
        s8, _ = reserve(tok, f"{ws}-e2", log=True)
        s9, j9 = reserve(tok, f"{ws}-e3", log=True)
        if not (s7 == 200 and s8 == 200 and s9 == 402): problems.append(f"{plan}: after expiry expected 200,200,402 got {s7},{s8},{s9}")
    record("20.6", "PASS" if not problems else "FAIL", "" if not problems else "S1", "; ".join(problems) or "Free 25 / Starter 200 / Pro 500 caps -> 402 compile_credit_limit_exceeded at cap+1; same id twice counts once; release returns; reservation expired after TTL; Enterprise unlimited")


# ------------------------------------------------------------------ 20.7
def row7():
    say("\n#### 20.7 human-edit token pool (402 human_edit_pool_exceeded for the legacy class, ai_credits_exceeded for the new name)")
    problems = []
    notes = []
    for plan, cap, _s, _m, pool in PLANS:
        ws, tok = newws("c7" + TAG, plan)
        say(f"\n-- {plan}: pool={pool}")
        cur(tok, "before")
        if plan == "enterprise":
            st, js = proxy(tok, "text", "human_edit", 2000, log=True)
            notes.append(f"enterprise(no overrides) first human_edit call -> {st} {detail(js)}")
            st2, js2 = proxy(tok, "text", "ai_credits", 2000, log=True)
            stc, jsc = proxy(tok, "text", "compile", 2000, log=True)
            cur(tok, "enterprise after")
            if st != 200 or st2 != 200 or stc != 200: problems.append(f"enterprise no overrides: human_edit {st}, ai_credits {st2}, compile {stc}")
            continue
        n = 0
        last = None
        # vision calls of ~7.5MB ≈ 2.1M tokens each (pre-check only looks at what is ALREADY used, so the call that crosses the cap succeeds)
        kind, chars = ("text", 900_000) if plan == "free" else ("vision", 7_500_000)
        while n < 12:
            st, js = proxy(tok, kind, "human_edit", chars)
            n += 1
            if st != 200:
                last = (st, js); break
        say(f"  {n} calls until refusal -> {last[0] if last else None} {detail(last[1]) if last else ''}")
        j = cur(tok, "pool state")
        if not last or last[0] != 402 or detail(last[1]) != "human_edit_pool_exceeded":
            problems.append(f"{plan}: pool exhausted gave {last and last[0]} {last and detail(last[1])}")
        st_new, js_new = proxy(tok, "text", "ai_credits", 2000, log=True)
        if not (st_new == 402 and detail(js_new) == "ai_credits_exceeded"): problems.append(f"{plan}: ai_credits class gave {st_new} {detail(js_new)}")
        overshoot = j["meters"]["human_edit_tokens"]["used"] - pool
        notes.append(f"{plan}: pool used {j['meters']['human_edit_tokens']['used']} of {pool} (overshoot {overshoot} tokens: one call may cross the cap)")
        # compile pool unaffected
        stc, jsc = proxy(tok, "text", "compile", 2000, log=True)
        stres, jres = reserve(tok, f"{ws}-after-pool", log=True)
        jj = cur(tok, "after compile call")
        if stres != 200: problems.append(f"{plan}: reserving a compile credit after pool exhaustion gave {stres}")
        if stc != 200:
            problems.append(f"{plan}: COMPILE-class proxy call refused ({stc} {detail(jsc)}) although only the human-edit pool is exhausted")
        # the other direction was checked in 20.6 (compile exhausted -> human_edit 200)
    for n_ in notes: say("  note: " + n_)
    record("20.7", "PASS" if not problems else "FAIL", "" if not problems else "S2", "; ".join(problems) or "pool cap -> 402 human_edit_pool_exceeded (legacy) / ai_credits_exceeded (new class); compile calls + compile credits unaffected. " + " | ".join(notes[:5]))


# ------------------------------------------------------------------ 20.8
def row8():
    say("\n#### 20.8 machines (X-Conxa-Machine header on compile reserve / LLM proxy; revoke frees a slot)")
    problems = []
    for plan, _cap, _s, mcap, _p in PLANS:
        ws, tok = newws("c8" + TAG, plan)
        say(f"\n-- {plan}: machines={mcap}")
        cur(tok, "before")
        if plan == "enterprise":
            st, js = reserve(tok, f"{ws}-m0", machine="mach-a", log=True)
            say(f"  NOTE enterprise(no overrides) machine limit is 0: first machine -> {st} {detail(js)} (judged under 20.16, not 20.8)")
            if st != 200: problems.append(f"enterprise floor: first machine {st}")
            continue
        okc = 0
        for i in range(mcap):
            st, js = reserve(tok, f"{ws}-m{i}", machine=f"mach-{i}", log=False)
            okc += st == 200
        say(f"  {okc}/{mcap} machines registered")
        j = cur(tok, "at cap")
        st, js = reserve(tok, f"{ws}-mx", machine="mach-new", log=True)
        if not (st == 402 and detail(js) == "machine_limit_exceeded"): problems.append(f"{plan}: over-cap machine got {st} {detail(js)}")
        st, js = reserve(tok, f"{ws}-mk", machine="mach-0", log=True)  # known machine again
        if st != 200: problems.append(f"{plan}: known machine refused at cap ({st})")
        # LLM proxy path also enforces
        stp, jp = call("POST", "/api/v1/llm/proxy/text", token=tok, body={"task": "t", "payload": {"m": "x"}}, headers={**H_STUDIO, "X-Conxa-Machine": "mach-new"}, label="[proxy with new machine at cap]")
        if stp != 402: problems.append(f"{plan}: proxy accepted a new machine at cap ({stp})")
        # revoke mach-0 -> frees slot
        sr, jr = call("POST", "/api/v1/entitlements/machines/revoke", token=tok, body={"machine_hash": "mach-0"}, label="[revoke mach-0]")
        j = cur(tok, "after revoke")
        if j["meters"]["machines"]["used"] != mcap - 1: problems.append(f"{plan}: revoke did not free a slot ({j['meters']['machines']})")
        st, js = reserve(tok, f"{ws}-mn", machine="mach-new", log=True)
        if st != 200: problems.append(f"{plan}: slot freed by revoke but new machine refused ({st} {detail(js)})")
        st, js = reserve(tok, f"{ws}-mr", machine="mach-0", log=True)  # revoked machine back: brand new -> at cap again
        if st != 402: problems.append(f"{plan}: a revoked machine slipped back in at cap ({st})")
        call("GET", "/api/v1/entitlements/machines", token=tok, label="[machine list]")
        # a non-admin member cannot revoke
        member = mint(f"user_c8{TAG}_{plan}_mem", ws, "org:member")
        stm, jm = call("POST", "/api/v1/entitlements/machines/revoke", token=member, body={"machine_hash": "mach-1"}, label="[member revoke]")
        if stm not in (402, 403): problems.append(f"{plan}: non-admin member revoke -> {stm}")
    record("20.8", "PASS" if not problems else "FAIL", "" if not problems else "S1", "; ".join(problems) or "Free 1/Starter 3/Pro 10 -> 402 machine_limit_exceeded at cap+1 (reserve and LLM proxy); known machine OK; revoke frees exactly one slot; revoked machine counts as new")


# ------------------------------------------------------------------ 20.9
def row9():
    say("\n#### 20.9 seats (Clerk member count from the local mock; gate is count > limit at a NEW member's first request)")
    problems = []
    ctl(api="ok")
    for plan, _cap, scap, _m, _p in PLANS:
        ws = f"org_c9{TAG}_{plan}"
        say(f"\n-- {plan}: seats={scap}")
        set_plan(ws, plan)
        owner = mint(f"user_c9{TAG}_{plan}_o", ws, "org:admin")
        ctl(count=f"{ws}:1")
        st, js = call("GET", "/api/v1/tracking/companies", token=owner, label="[first member, count=1]")
        if plan == "enterprise":
            say(f"  enterprise FIRST member (billing set before anyone signed in) -> {st} {detail(js)}")
            say(f"  NOTE (judged under 20.16): the very first member of a new Enterprise workspace (no overrides, seat limit 0) gets {st} {detail(js)}")
            # now seed the member as known by switching the plan after first sight: use a second ws
            ws2 = ws + "_late"
            owner2 = mint(f"user_c9{TAG}_ent_o2", ws2, "org:admin")
            call("GET", "/api/v1/tracking/companies", token=owner2, label="[ent2 owner first sight as free]")
            set_plan(ws2, "enterprise")
            st2, js2 = call("GET", "/api/v1/tracking/companies", token=owner2, label="[ent2 owner after upgrade]")
            ctl(count=f"{ws2}:2")
            newm = mint(f"user_c9{TAG}_ent_new", ws2, "org:member")
            st3, js3 = call("GET", "/api/v1/tracking/companies", token=newm, label="[ent2 NEW member, count=2]")
            say(f"  enterprise existing owner after upgrade -> {st2}; new member -> {st3} {detail(js3)}")
            say(f"  NOTE (judged under 20.16): a NEW member of an Enterprise workspace with no overrides gets {st3}")
            if st != 200 or st2 != 200 or st3 != 200: problems.append(f"enterprise floor: first member {st}, owner {st2}, new member {st3}")
            continue
        if st != 200: problems.append(f"{plan}: first member refused ({st} {detail(js)})")
        # fill to the limit: count == limit allows the arriving member
        ctl(count=f"{ws}:{scap}")
        mem_ok = mint(f"user_c9{TAG}_{plan}_ok", ws, "org:member")
        st_ok, js_ok = call("GET", "/api/v1/tracking/companies", token=mem_ok, label=f"[new member, count={scap} == limit]")
        if scap > 1 and st_ok != 200: problems.append(f"{plan}: member within limit refused ({st_ok})")
        # now one more than the limit
        ctl(count=f"{ws}:{scap + 1}")
        mem_over = mint(f"user_c9{TAG}_{plan}_over", ws, "org:member")
        st_o, js_o = call("GET", "/api/v1/tracking/companies", token=mem_over, label=f"[new member, count={scap + 1} > limit]")
        if not (st_o == 402 and detail(js_o) == "seat_limit_exceeded"): problems.append(f"{plan}: over-limit member got {st_o} {detail(js_o)}")
        st_oe, js_oe = call("GET", "/api/v1/entitlements/current", token=mem_over, label="[same refused member on /entitlements/current: the dashboard's plan call]")
        if st_oe != 402: problems.append(f"{plan}: over-limit member on GET /entitlements/current gets {st_oe} {detail(js_oe)} instead of 402 seat_limit_exceeded (the route wraps current_principal's 402 and re-maps it to 503)")
        # existing members keep working while the workspace is over its seat limit
        st_e, js_e = call("GET", "/api/v1/tracking/companies", token=owner, label="[existing owner while count>limit]")
        st_w, js_w = call("GET", "/api/v1/workflows", token=owner, label="[existing owner list workflows]")
        if st_e != 200 or st_w != 200: problems.append(f"{plan}: existing member blocked while over the limit ({st_e},{st_w})")
        # refused member was NOT persisted as a member
        j = call("GET", "/api/v1/entitlements/current", token=owner, log=False)[1]
        say(f"  seats meter now: {j['meters']['seats'] if isinstance(j, dict) and 'meters' in j else j}")
        # fail open when Clerk is unreachable
        for mode in ("down500", "drop"):
            ctl(api=mode)
            n = mint(f"user_c9{TAG}_{plan}_fo_{mode}", ws, "org:member")
            st_f, js_f = call("GET", "/api/v1/tracking/companies", token=n, label=f"[new member, Clerk API {mode}]")
            if st_f != 200: problems.append(f"{plan}: Clerk {mode} did not fail open ({st_f})")
        ctl(api="ok")
        # downgrade with existing members above the new limit: they keep working
        if plan == "pro":
            set_plan(ws, "starter")
            ctl(count=f"{ws}:6")
            st_d, _ = call("GET", "/api/v1/tracking/companies", token=owner, label="[existing owner after downgrade to starter with 6 members]")
            nn = mint(f"user_c9{TAG}_pro_after", ws, "org:member")
            st_n, js_n = call("GET", "/api/v1/tracking/companies", token=nn, label="[new member after downgrade]")
            if st_d != 200 or st_n != 402: problems.append(f"pro->starter downgrade: existing={st_d} new={st_n} {detail(js_n)}")
    record("20.9", "PASS" if not problems else "FAIL", "" if not problems else "S2", "; ".join(problems) or "Free 1/Starter 3/Pro 10: new member beyond cap -> 402 seat_limit_exceeded; existing members unaffected (also after downgrade); fails open when the Clerk member API is down/dropped (documented). Clerk itself is a local mock (BLOCKED against real Clerk).")


for r in rows:
    {"6": row6, "7": row7, "8": row8, "9": row9}[r]()
save_results("results_c_" + "_".join(rows) + ".json")
