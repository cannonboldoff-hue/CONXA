"""Test tool: author a recorded decision popup (real compiler build_outcome_forks) into the WF10 test skill, or apply set_fork_policy. Usage: wf10_fork.py build | policy | show"""
import sys, json, copy, time, os
sys.path.insert(0, r"C:\Users\kiran\Desktop\Conxa\conxa-builder\python"); sys.path.insert(0, r"C:\Users\kiran\Desktop\Conxa\packages\conxa-core")
from conxa_core.models.skill_spec import SkillStep
from conxa_compile.compiler.outcome_fork import build_outcome_forks
from conxa_compile.editor.workflow_mutations import set_fork_policy
sys.argv_ = sys.argv
HERE = os.path.dirname(os.path.abspath(__file__))
SK = r"C:\Users\kiran\.conxa-build-studio-dev\data\skills\skill_f02fa97d-b837-410d-9e47-97f136b73230.json"
KV = r"C:\Users\kiran\.conxa-build-studio-dev\data\kv\skills\09c94e805e0ef00b2be2f22ffffbaf1eec425fe7317f313edcb0346b3b15204a.json"
H = os.path.expanduser("~/.conxa-build-studio-dev/data")
WFS = (H + "/kv/workflows/fd750cba1c22d473e46a7acc0eb8a0eacca2f6aca6cf16cca3ab267e9128479e.json", H + "/workflows/16be31f3-df2c-4279-8b36-5f138a5b88b2.json")
op = sys.argv[1]
def save(d):
    d["meta"]["version"] = int(d["meta"].get("version", 1)) + 7
    for p in (SK, KV): json.dump(d, open(p, "w", encoding="utf-8"), indent=1)
    for wp in WFS:
        w = json.load(open(wp, encoding="utf-8")); w["edited_at"] = time.time(); json.dump(w, open(wp, "w", encoding="utf-8"), indent=1)
if op == "build":
    d = json.load(open(os.path.join(HERE, "../evidence/WF-010/r3-skill-original-bk.json"), encoding="utf-8"))
    st = d["skills"][0]["steps"]
    nav = copy.deepcopy(st[0]); nav["url"] = "http://127.0.0.1:8099/decision.html?ask=1"
    trig = copy.deepcopy(st[4]); trig["intent"] = "click_show_box"; trig["target"] = {"primary_selector": "#trigger", "fallback_selectors": []}
    ok = copy.deepcopy(st[4]); ok["intent"] = "click_ok"; ok["target"] = {"primary_selector": "#ask-ok", "fallback_selectors": []}
    ok["consequence"] = "irreversible"; ok["identity_bundle"] = {"stable_hash": "h-ok"}; ok["compiled_selectors"] = []
    ok["signals"] = {"dialog_context": {"kind": "dom", "container_signal": "#ask", "role": "dialog", "title": "Delete all items?", "message": "Delete all items?", "has_fields": False,
        "buttons": [{"tag": "button", "role": "button", "name": n, "text": n, "chosen": n == "OK"} for n in ("OK", "Cancel")]}}
    steps = [SkillStep.model_validate(s) for s in (nav, ok)]
    out = [json.loads(s.model_dump_json()) for s in build_outcome_forks(steps, "s1")]
    d["skills"][0]["steps"] = out
    save(d); op = "show"
d = json.load(open(KV, encoding="utf-8"))
if op == "policy":
    fk = d["skills"][0]["steps"][1]["outcome_fork"]["states"][0]
    sid = fk.get("id") or fk.get("state_id")
    opt = sys.argv[2]
    d = set_fork_policy(d, 1, sid, {"mode": "auto", "option_id": opt}); save(d)
for i, s in enumerate(d["skills"][0]["steps"]):
    f = s.get("outcome_fork") or {}
    print(i, s["action"], s.get("intent"), json.dumps(f)[:900] if f else "")
