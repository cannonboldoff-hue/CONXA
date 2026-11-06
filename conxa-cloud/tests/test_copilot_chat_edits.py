"""Chat can edit what Human Edit can edit: fields come from the patch gate (never an address), a field
edit may name a step inside a body, and the new structural ops each dry-run the real mutation. Also the
plain-words agent step ("if it says verified click Download ...") and the trigger words that reach it."""

from __future__ import annotations

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation.harness import wants_proposals
from conxa_compile.editor.capability_manifest import editable_fields
from conxa_compile.editor.copilot_proposals import gate_proposals, gate_structural_proposals, proposal_summary
from conxa_compile.editor.workflow_mutations import _new_manual_step


def _doc(steps):
    return {"meta": {"version": 1, "title": "t"}, "skills": [{"steps": steps}]}


def _click(**kw):
    step = {
        "action": {"action": "click"}, "url": "https://x", "intent": "click_submit", "value": "",
        "identity_bundle": {"signals": [{"engine": "testid", "selector": "[data-testid=submit]", "durability": 0.9}]},
        "target": {"primary_selector": "[data-testid=submit]"},
    }
    step.update(kw)
    return step


def _navigate(url="https://example.com/doc"):
    return {"action": {"action": "navigate"}, "url": url, "intent": "navigate", "value": ""}


def _fork():
    def opt(oid, label, role):
        return {"id": oid, "label": label, "role": role, "recorded": oid == "o1", "steps": [_click(intent="click_" + oid)]}

    return {
        "action": {"action": "outcome_fork"}, "intent": "popup_decision", "value": None,
        "outcome_fork": {"states": [{
            "id": "s1", "signature": {"title": "File exists"}, "policy": {"mode": "ask_human"},
            "options": [opt("o1", "Replace", "consequential"), opt("o2", "Keep both", "safe")],
        }]},
    }


def _ev(key):
    return {"why": "asked", "evidence_refs": [key]}


def test_editable_fields_follow_the_gate_and_never_include_an_address():
    click = editable_fields("click")
    assert {"run_if", "value", "intent", "input_binding"} <= click
    assert not click & {"target", "identity_bundle", "compiled_selectors", "check_selector", "recovery"}
    assert "run_if" not in editable_fields("agent_span")  # an input must never switch off a span
    assert "agent_span_budget" in editable_fields("agent_span")
    assert "url" in editable_fields("navigate")
    assert "value" not in editable_fields("drag_drop")  # holds CSS


def test_run_if_edit_passes_on_a_click_and_a_selector_field_is_dropped():
    doc = _doc([_click()])
    key = step_keys(doc["skills"][0]["steps"])[0]
    cond = {"step_key": key, "field": "run_if", "patch": {"input": "send_email", "op": "equals", "value": "yes"}, **_ev(key)}
    out = gate_proposals(doc, [cond, {"step_key": key, "field": "target", "patch": {"primary_selector": "#a"}, **_ev(key)}])
    assert [p["field"] for p in out] == ["run_if"]
    assert proposal_summary(out[0]) == "Run step 1 only when the input send_email is “yes”"


def test_a_field_edit_can_name_a_step_inside_a_branch_body():
    parent = _new_manual_step("if_present", "https://x")
    parent["branch"] = {"steps": [_click()]}
    doc = _doc([parent])
    key = step_keys(doc["skills"][0]["steps"])[0]
    ok = gate_proposals(doc, [{"step_key": key, "field": "intent", "patch": "dismiss_banner", "path": "branch.steps[0]", **_ev(key)}])
    assert len(ok) == 1 and ok[0]["path"] == "branch.steps[0]"
    assert gate_proposals(doc, [{"step_key": key, "field": "intent", "patch": "x_y", "path": "branch.steps[5]", **_ev(key)}]) == []
    assert gate_proposals(doc, [{"step_key": key, "field": "intent", "patch": "x_y", "path": "nonsense", **_ev(key)}]) == []


def test_agent_step_from_plain_words_has_a_goal_a_step_number_and_a_schedule_warning():
    doc = _doc([_navigate(), _click()])
    keys = step_keys(doc["skills"][0]["steps"])
    goal = "1. Read the whole page. 2. If it says Verified, click Download. 3. If it says Not verified, click Cancel."
    raw = {"op": "insert_step", "action_kind": "agent_span", "after_step_key": keys[0],
           "fields": {"value": goal, "agent_span_allowed": ["click", "scroll"]}, **_ev(keys[0])}
    out = gate_structural_proposals(doc, [raw])
    assert len(out) == 1
    summary = proposal_summary(out[0])
    assert "after step 1" in summary and "click Download" in summary and "can't run on a schedule" in summary
    assert gate_structural_proposals(doc, [{**raw, "fields": {"value": "  "}}]) == []


def test_popup_decision_is_worded_from_the_fork_not_the_model():
    doc = _doc([_fork()])
    key = step_keys(doc["skills"][0]["steps"])[0]
    raw = {"op": "set_popup_decision", "step_key": key, "state_id": "s1",
           "policy": {"mode": "auto", "option_id": "o2"}, **_ev(key)}
    out = gate_structural_proposals(doc, [raw])
    assert len(out) == 1
    assert "always press “Keep both”" in proposal_summary(out[0])
    risky = gate_structural_proposals(doc, [{**raw, "policy": {"mode": "auto", "option_id": "o1"}}])
    assert "pressing Apply is your approval" in proposal_summary(risky[0])
    # unknown state / option, or a step that is not a fork, never becomes a card
    assert gate_structural_proposals(doc, [{**raw, "state_id": "nope"}]) == []
    assert gate_structural_proposals(doc, [{**raw, "policy": {"mode": "auto", "option_id": "zzz"}}]) == []
    plain = _doc([_click()])
    assert gate_structural_proposals(plain, [{**raw, "step_key": step_keys(plain["skills"][0]["steps"])[0]}]) == []


def test_body_ops_and_date_offset_and_retarget_and_rename():
    parent = _new_manual_step("if_present", "https://x")
    parent["branch"] = {"steps": [_click(), _click(intent="click_other")]}
    date = {"action": {"action": "date_pick"}, "url": "https://x", "intent": "pick_date", "value": "2026-01-01"}
    doc = _doc([parent, _click(), date])
    k = step_keys(doc["skills"][0]["steps"])
    ops = [
        {"op": "body_delete_step", "step_key": k[0], "nested_index": 1},
        {"op": "body_move_step", "step_key": k[0], "new_order": [1, 0]},
        {"op": "body_insert_step", "step_key": k[0], "action_kind": "wait", "after_nested_index": 0},
        {"op": "set_date_offset", "step_key": k[2], "days": 3},
        {"op": "retarget_from_step", "step_key": k[1], "identity_from_step_key": k[0]},
        {"op": "rename_workflow", "name": "Invoices"},
    ]
    out = gate_structural_proposals(doc, [{**o, **_ev(k[0])} for o in ops])
    kept = [p["op"] for p in out]
    # the retarget source (an if_present) has no root identity, so that one is refused
    assert kept == ["body_delete_step", "body_move_step", "body_insert_step", "set_date_offset", "rename_workflow"]
    bad = [
        {"op": "body_delete_step", "step_key": k[0], "nested_index": 9},
        {"op": "body_move_step", "step_key": k[0], "new_order": [0, 0]},
        {"op": "body_insert_step", "step_key": k[0], "action_kind": "agent_span"},
        {"op": "set_date_offset", "step_key": k[1], "days": 3},
    ]
    assert gate_structural_proposals(doc, [{**o, **_ev(k[0])} for o in bad]) == []


def test_retarget_copies_an_existing_steps_identity():
    doc = _doc([_click(), _click(intent="click_other", identity_bundle={"signals": [{"engine": "testid", "selector": "[data-testid=cancel]", "durability": 0.9}]})])
    k = step_keys(doc["skills"][0]["steps"])
    out = gate_structural_proposals(doc, [{"op": "retarget_from_step", "step_key": k[1], "identity_from_step_key": k[0], **_ev(k[0])}])
    assert len(out) == 1 and proposal_summary(out[0]) == "Point step 2 at the same element as step 1"
    assert gate_structural_proposals(doc, [{"op": "retarget_from_step", "step_key": k[0], "identity_from_step_key": k[0], **_ev(k[0])}]) == []


def test_action_words_reach_the_proposal_call_but_a_question_does_not():
    ask = lambda m: wants_proposals(m, has_skill=True, anchor=None, last_test_failed=False)  # noqa: E731
    assert ask("after I navigate to this website I want to see the whole page, if it says verified click download")
    assert ask("only run step 4 when send_email is yes")
    assert ask("always press Keep both on that popup")
    assert not ask("what does step 3 do?")


def _loop(steps):
    step = _new_manual_step("for_each", "https://x")
    step["for_each"] = {"rows": {"container_selector": "tr"}, "as": "row", "max_iterations": 5, "on_row_error": "stop", "steps": steps}
    return step


def test_loop_body_ops_pass_the_gate_and_branch_only_kinds_stay_out():
    doc = _doc([_loop([_click(), _click(intent="click_other")])])
    k = step_keys(doc["skills"][0]["steps"])
    ops = [
        {"op": "body_insert_step", "step_key": k[0], "action_kind": "wait", "after_nested_index": 0},
        {"op": "body_delete_step", "step_key": k[0], "nested_index": 1},
        {"op": "body_move_step", "step_key": k[0], "new_order": [1, 0]},
    ]
    out = gate_structural_proposals(doc, [{**o, **_ev(k[0])} for o in ops])
    assert [p["op"] for p in out] == ["body_insert_step", "body_delete_step", "body_move_step"]
    assert gate_structural_proposals(doc, [{"op": "body_insert_step", "step_key": k[0], "action_kind": "agent_span", **_ev(k[0])}]) == []
    plain = _doc([_click()])
    assert gate_structural_proposals(plain, [{"op": "body_delete_step", "step_key": step_keys(plain["skills"][0]["steps"])[0], "nested_index": 0, **_ev("x")}]) == []
