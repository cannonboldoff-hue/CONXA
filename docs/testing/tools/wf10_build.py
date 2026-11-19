"""Test tool: rewrite WF-010 skill file with branch steps. Usage: wf10_build.py <variant>. Reads the original backup, writes the live skill file."""
import json, copy, sys
SK = r"C:\Users\kiran\.conxa-build-studio-dev\data\skills\skill_f02fa97d-b837-410d-9e47-97f136b73230.json"
BK = r"C:\Users\kiran\Desktop\Conxa\docs\testing\evidence\WF-010\r3-skill-original-bk.json"
d = json.load(open(BK, encoding="utf-8"))
st = d["skills"][0]["steps"]
nav1, close, nav2, click = st[0], st[2], st[3], st[4]
CLOSE_SEL = '#modal .modal-footer p'

def mk(tpl, action, intent, selector="", **kw):
    s = copy.deepcopy(tpl)
    s["action"] = action; s["intent"] = intent
    s["target"] = {"primary_selector": selector, "fallback_selectors": []} if selector else {}
    s["identity_bundle"] = {}; s["compiled_selectors"] = {}
    s["validation"] = {"wait_for": {"type": "none", "target": "", "timeout": 5000}, "success_conditions": {}, "assertions": [], "effect": None}
    s["recovery"] = {"intent": intent, "final_intent": intent, "anchors": [], "strategies": [], "confidence_threshold": 0.85, "max_attempts": 0, "require_diverse_attempts": False}
    s["optional_hint"] = None; s["outcome_fork"] = None
    s.update(kw); return s

def body_click(sel):
    return mk(close, "click", "click_target", sel)

def if_present(sel, body_sel):
    return mk(close, "if_present", "dismiss_if_present", sel, branch={"steps": [body_click(body_sel)], "timeout_ms": 3000})

def try_dismiss(cands, esc=True):
    return mk(close, "try_dismiss", "try_dismiss", "", branch={"candidates": cands, "timeout_ms": 3000, "fallback_escape": esc})

def wfoo(options, required=False, timeout=5000):
    return mk(close, "wait_for_one_of", "wait_for_one_of", "", branch={"options": options, "timeout_ms": timeout, "required": required})

def opt(sel, steps=()):
    return {"selector": sel, "steps": list(steps)}

v = sys.argv[1]
logout_like = body_click('text="Click here"')  # harmless success-arm click placeholder
def nav_to(u): return mk(nav1, "navigate", "navigate_to_page", url=u, target={})
good = wfoo([opt('body'), opt('text=unsuccesful', [nav_to("https://the-internet.herokuapp.com/abtest")])])  # final URL shows the arm
def forged(sel):
    s = copy.deepcopy(close); s["intent"] = "click_target"; s["target"] = {"primary_selector": sel, "fallback_selectors": [], "css": sel}
    b = s["identity_bundle"]
    for g in b.get("signals", []): g["engine"] = "css"; g["selector"] = sel
    s["compiled_selectors"] = {}; s["recovery"]["max_attempts"] = 0
    return s
V = {
 "full":   [nav1, if_present("#modal", CLOSE_SEL), try_dismiss([CLOSE_SEL]), nav2, click, good],           # 10.1 10.2 10.4
 "blank":  [nav1, try_dismiss([""]), nav2, click],                                                          # 10.5
 "bogus":  [nav1, if_present("#modal", "#does-not-exist"), try_dismiss([CLOSE_SEL]), nav2, click],         # 10.6
 "req":    [nav1, nav2, click, wfoo([opt("#nothing-here"), opt("#nor-this")], required=True, timeout=2000)],  # 10.7
 "runif":  [nav1, dict(copy.deepcopy(close), run_if={"input": "flag", "op": "equals", "value": "go"})],        # 10.11 skipped: input absent
 "runifbad": [nav1, dict(copy.deepcopy(close), run_if={"input": "flag", "op": "bogus"})],                   # 10.11 malformed: step must run
 "ask":    [nav_to("http://127.0.0.1:8099/decision.html?ask=1"), forged("#real")],                                   # 10.9
 "unrec":  [nav1, click],                                       # 10.13
}

LOG = lambda n: nav_to("http://127.0.0.1:8099/__log?" + n)
S = lambda k: wfoo([opt('#flash:has-text("Action successful")', [LOG("arm-success-" + k)]), opt('#flash:has-text("unsucces")', [LOG("arm-error-" + k)])])
V.update({
 "n4":    [nav2, click, S("a"), nav2, click, S("b"), nav2, click, S("c"), nav2, click, S("d")],                                  # 10.4 arms differ
 "n4body": [nav2, wfoo([opt('body', [LOG("arm-body")])], timeout=5000)],                                    # 10.4 always-present: immediate
 "p1":    [nav1, wfoo([opt('#modal:visible', [LOG("modal-visible-before")])], timeout=6000)],               # 10.1 presence proof
 "full3": [nav1, if_present("#modal:visible", CLOSE_SEL), wfoo([opt("#nothing-here")], timeout=2000), wfoo([opt('#modal:visible', [LOG("still-open-after-2s")]), opt('body', [LOG("modal-closed-after-2s")])]), nav2, click, S("y")],
 "h_a": [nav_to("http://127.0.0.1:8099/decision.html?hidden=1"), dict(forged("#maybe"), on_missing="skip")],
 "h_b": [nav_to("http://127.0.0.1:8099/decision.html?hidden=1"), dict(forged("#maybe"), on_missing="skip", entity_binding={"container_selector": "#maybe", "identifier": "Maybe (hidden)"})],
 "h_c": [nav_to("http://127.0.0.1:8099/decision.html?hidden=1"), dict(forged("#maybe"), on_missing="skip", entity_binding={"container_selector": "#maybe", "identifier": "Nope"})],
 "full2": [nav1, if_present("#modal:visible", CLOSE_SEL), wfoo([opt('#modal:visible', [LOG("still-open")]), opt('body', [LOG("modal-closed")])]), nav2, click, S("x")],  # 10.1
})
import os
d["meta"]["version"] = int(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wf10_ver")).read()) + 1 if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wf10_ver")) else 100
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wf10_ver"), "w").write(str(d["meta"]["version"]))  # test copy is cached per skill version, so every rebuild needs a new one
d["skills"][0]["steps"] = V[v]
KV = r"C:\Users\kiran\.conxa-build-studio-dev\data\kv\skills\09c94e805e0ef00b2be2f22ffffbaf1eec425fe7317f313edcb0346b3b15204a.json"  # the Studio store; skills/ file is only a copy
for p in (SK, KV): json.dump(d, open(p, "w", encoding="utf-8"), indent=1)
print("wrote", v, len(V[v]))

# the Studio rebuilds the test copy only when the workflow's edited_at is newer than the last build
import time
H = os.path.expanduser("~/.conxa-build-studio-dev/data")
for wp in (H + "/kv/workflows/fd750cba1c22d473e46a7acc0eb8a0eacca2f6aca6cf16cca3ab267e9128479e.json", H + "/workflows/16be31f3-df2c-4279-8b36-5f138a5b88b2.json"):
    w = json.load(open(wp, encoding="utf-8")); w["edited_at"] = time.time(); json.dump(w, open(wp, "w", encoding="utf-8"), indent=1)
