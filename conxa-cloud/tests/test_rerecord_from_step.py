"""BUILD-52: re-record from a step -- replay to just before it, hand the live page to the recorder,
compile what the person does and splice it in. Everything that doesn't need a real Electron window."""

from __future__ import annotations

import pytest

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation import runtime_state
from handlers.protocol import _CommandError
from tests.test_compile_flow import _doc, _step, env  # noqa: F401  (env is a fixture)


def _saved(wf):
    from conxa_core.storage.json_store import write_skill
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    doc = _doc("skill_rr")
    # a drag-and-drop can't be replayed, so the packager drops it: runtime numbering skips it
    doc["skills"][0]["steps"][0]["url"] = "https://billing.example.test/new"   # a navigate with no address isn't runnable either
    doc["skills"][0]["steps"].insert(1, _step("drag_drop", "drag_card"))
    write_skill("skill_rr", doc)
    w = get_workflow(wf.id)
    w.skill_id, w.recording_status = "skill_rr", "compiled"
    save_workflow(w)
    return doc, step_keys(doc["skills"][0]["steps"])


def test_the_runtime_numbers_steps_after_the_packager_dropped_some(env):
    from conxa_compile.skill_package_builder_saved_skill import runtime_step_index

    _b, wf, _out = env
    doc, keys = _saved(wf)
    assert [runtime_step_index(doc, k) for k in keys] == [0, None, 1, 2, 3]   # keys[1] is the dropped drag_drop


def test_rerecord_refuses_what_cannot_be_replayed(env):
    b, wf, _out = env
    _doc_, keys = _saved(wf)
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_rerecord_from({"workflow_id": wf.id, "step_key": keys[0]}, "r")
    assert e.value.code == "first_step"
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_rerecord_from({"workflow_id": wf.id, "step_key": "nope"}, "r")
    assert e.value.code == "step_not_found"
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_rerecord_from({"workflow_id": wf.id, "step_key": keys[1]}, "r")      # the dropped step
    assert e.value.code == "step_not_runnable"
    runtime_state.set_flags(wf.id, recording=True)
    with pytest.raises(_CommandError) as e:
        b.cmd_flow_rerecord_from({"workflow_id": wf.id, "step_key": keys[3]}, "r")
    assert e.value.code == "busy"


def test_rerecord_replays_to_the_runtime_index_then_records_on_the_handed_over_run(env, monkeypatch):
    b, wf, _out = env
    _doc_, keys = _saved(wf)
    monkeypatch.setattr(b, "_tf_ensure_built", lambda workflow, rid: None)
    seen: dict = {}

    def fake_test(payload, rid):
        seen["test"] = payload
        return {"status": "stopped_at_step", "handed_off_run_id": "run-abc"}

    monkeypatch.setattr(b, "cmd_test_workflow", fake_test)
    monkeypatch.setattr(b, "_flow_open_recording", lambda workflow, rid, sink, **kw: seen.setdefault("open", kw))
    b._rerecord(wf.id, keys[3], 2, {"po_number": "PO-1"}, lambda *_: None)
    assert seen["test"]["stop_before_step"] == 2 and seen["test"]["handoff_park"] is True and seen["test"]["guard"] is False
    assert seen["open"] == {"segment_from": keys[3], "adopt_run_id": "run-abc", "segment_mode": "replace"}
    assert not runtime_state.get_flags(wf.id).get("testing")

    monkeypatch.setattr(b, "cmd_test_workflow", lambda p, r: {"status": "failed"})
    with pytest.raises(_CommandError) as e:
        b._rerecord(wf.id, keys[3], 2, {}, lambda *_: None)
    assert e.value.code == "replay_failed"


def test_the_new_part_is_compiled_as_a_segment_spliced_in_and_cleaned_up(env, monkeypatch):
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_core.storage.workflow_store import add_variant, get_workflow

    b, wf, _out = env
    _doc_, keys = _saved(wf)
    add_variant(wf.id, "seg-sess", f"segment:{keys[3]}")
    calls: dict = {}

    def fake_compile(payload, rid):
        calls["compile"] = payload
        write_skill("skill_seg-sess", _doc("skill_seg-sess"))
        return {"skill_id": "skill_seg-sess"}

    def fake_splice(payload, rid):
        calls["splice"] = payload
        return {"added": 2, "replaced_from": 3, "stale_answers": 1}

    monkeypatch.setattr(b, "cmd_compile", fake_compile)
    monkeypatch.setattr(b, "cmd_splice_segment", fake_splice)
    monkeypatch.setattr(b, "_flow_post_summary", lambda *a, **k: calls.setdefault("summary", True))
    b._splice_segment_safe(wf.id, "seg-sess", keys[3])
    assert calls["compile"]["segment"] is True and calls["compile"]["session_id"] == "seg-sess"
    assert calls["splice"] == {"skill_id": "skill_rr", "segment_skill_id": "skill_seg-sess", "from_step_key": keys[3]}
    assert read_skill("skill_seg-sess") is None                       # the part only existed to be spliced in
    assert not [v for v in get_workflow(wf.id).variant_session_ids if v["session_id"] == "seg-sess"]   # no longer held beside the recording
    assert calls["summary"] is True


def test_a_failed_compile_leaves_the_workflow_alone_and_says_so(env, monkeypatch):
    b, wf, _out = env
    _doc_, keys = _saved(wf)

    def boom(payload, rid):
        raise _CommandError("no_events", "No recorded events for this session.")

    monkeypatch.setattr(b, "cmd_compile", boom)
    monkeypatch.setattr(b, "cmd_splice_segment", lambda p, r: pytest.fail("must not splice after a failed compile"))
    b._splice_segment_safe(wf.id, "seg-sess", keys[3])


# ── the recorder's side ─────────────────────────────────────────────────────────────────────


def test_adopt_attaches_to_the_page_by_its_target_id_and_never_destroys_the_run(monkeypatch):
    from conxa_compile.recorder import host_view

    class Page:
        def __init__(self, tid): self.tid = tid

    pages = [Page("studio"), Page("parked")]
    closed: list[str] = []

    class Browser:
        contexts = [type("C", (), {"pages": pages})()]
        def close(self): closed.append("browser")

    monkeypatch.setattr(host_view, "control", lambda op, **kw: {"tabId": "t1", "targetId": "parked"})
    monkeypatch.setattr(host_view, "cdp_endpoint", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(host_view, "target_id", lambda ctx, page: page.tid)
    pw = type("PW", (), {"chromium": type("Ch", (), {"connect_over_cdp": staticmethod(lambda url: Browser())})()})()
    browser, _ctx, page, tab = host_view.adopt(pw, "run-abc", viewport=host_view.RECORD_VIEWPORT)
    assert page.tid == "parked" and tab == "t1"

    monkeypatch.setattr(host_view, "target_id", lambda ctx, page: "other")
    released: list[str] = []
    monkeypatch.setattr(host_view, "release", lambda run_id: released.append(run_id))
    with pytest.raises(RuntimeError, match="could not find"):
        host_view.adopt(pw, "run-abc")
    assert closed == ["browser"] and released == []                    # disconnected, but the run is not closed


def test_a_session_that_adopts_a_run_takes_that_runs_id():
    from conxa_compile.recorder.session import SessionRegistry

    reg = SessionRegistry()
    sess = reg.create(adopt_run_id="run-abc")
    assert sess.session_id == "run-abc" and sess.adopt is True
    assert reg.create().adopt is False


def test_the_handed_off_run_id_comes_from_meta_only():
    from handlers.workflows import _extract_handed_off

    assert _extract_handed_off({"_meta": {"conxa/handed_off": {"run_id": "run-abc"}}}) == "run-abc"
    assert _extract_handed_off({"content": [{"type": "text", "text": "Handed ... run-abc"}]}) is None


# ── record another way from here (a branch instead of a replacement) ────────────────────────


def _click(intent, selector, stable_hash):
    return _step("click", intent, identity_bundle={
        "stable_hash": stable_hash, "fingerprint": {"inner_text": intent},
        "signals": [{"engine": "role", "selector": f'internal:role=button[name="{selector}"]', "durability": 0.9}]})


def _branchable():
    return {"meta": {"version": 1}, "inputs": [], "skills": [{"steps": [
        _click("open_form", "Open", "h-open"), _click("pay_by_card", "Pay by card", "h-card"), _click("confirm", "Confirm", "h-confirm"),
    ]}]}


def test_two_ways_from_a_step_become_one_branch_found_by_each_ways_first_element():
    from conxa_compile.editor.workflow_mutations import wrap_variant_branch

    doc = _branchable()
    other = [_click("pay_by_invoice", "Pay by invoice", "h-inv"), _click("send_invoice", "Send", "h-send")]
    out = wrap_variant_branch(doc, 1, other)
    steps = out["skills"][0]["steps"]
    assert len(steps) == 2 and steps[0]["intent"] == "open_form"                    # what came before is untouched
    branch = steps[1]["branch"]
    assert steps[1]["action"] == "wait_for_one_of" and branch["flow"] is True and branch["required"] is True
    first, second = branch["options"]
    assert first["selector"] == 'role=button[name="Pay by card"]' and [s["intent"] for s in first["steps"]] == ["pay_by_card", "confirm"]
    assert second["selector"] == 'role=button[name="Pay by invoice"]' and [s["intent"] for s in second["steps"]] == ["pay_by_invoice", "send_invoice"]
    assert first["identity_bundle"]["stable_hash"] == "h-card"                      # each way is found like a click target


def test_a_branch_is_refused_when_the_page_could_not_tell_the_two_ways_apart():
    from conxa_compile.editor.workflow_mutations import wrap_variant_branch

    doc = _branchable()
    with pytest.raises(ValueError, match="both_start_the_same_way"):
        wrap_variant_branch(doc, 1, [_click("pay_by_card", "Pay by card", "h-card"), _click("other", "Other", "h-o")])
    with pytest.raises(ValueError, match="no_way_to_tell_them_apart"):
        wrap_variant_branch(doc, 1, [_step("click", "no_bundle")])
    with pytest.raises(ValueError, match="empty_segment"):
        wrap_variant_branch(doc, 1, [])
    with pytest.raises(ValueError, match="step_range_out_of_bounds"):
        wrap_variant_branch(doc, 9, [_click("x", "X", "h-x")])
    once = wrap_variant_branch(doc, 1, [_click("pay_by_invoice", "Pay by invoice", "h-inv")])
    with pytest.raises(ValueError, match="branch_inside_branch"):
        wrap_variant_branch(once, 1, [_click("pay_cash", "Pay cash", "h-cash")])


def test_the_packager_keeps_a_flow_branch_and_probes_each_way_by_its_identity_bundle():
    import json

    from conxa_compile.editor.workflow_mutations import wrap_variant_branch
    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill

    doc = _branchable()
    for s in doc["skills"][0]["steps"]:
        s["target"] = {"primary_selector": f"#{s['intent']}"}
    other = _click("pay_by_invoice", "Pay by invoice", "h-inv")
    other["target"] = {"primary_selector": "#pay_by_invoice"}
    out = wrap_variant_branch(doc, 1, [other])
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        _build_workflow_from_saved_skill(bundle_root=Path(tmp), workflow_slug="wf", saved_skill=out)
        execution = json.loads((Path(tmp) / "skills" / "wf" / "execution.json").read_text())
    branch = next(s for s in execution if s["type"] == "wait_for_one_of")
    assert branch["flow"] is True and len(branch["options"]) == 2
    assert all(o["identity_bundle"]["signals"] and o["steps"] for o in branch["options"])


def test_branch_mode_puts_the_new_part_in_as_a_second_way_not_a_replacement(env, monkeypatch):
    from conxa_core.storage.json_store import write_skill
    from conxa_core.storage.workflow_store import add_variant

    b, wf, _out = env
    _doc_, keys = _saved(wf)
    add_variant(wf.id, "seg-sess", f"segment:{keys[3]}")
    calls: dict = {}

    def fake_compile(payload, rid):
        write_skill("skill_seg-sess", _doc("skill_seg-sess"))
        return {"skill_id": "skill_seg-sess"}

    monkeypatch.setattr(b, "cmd_compile", fake_compile)
    monkeypatch.setattr(b, "cmd_splice_segment", lambda p, r: pytest.fail("a branch must not replace"))
    monkeypatch.setattr(b, "cmd_branch_segment", lambda p, r: calls.setdefault("branch", p) and {"added": 2, "branched_from": 3, "stale_answers": 0})
    monkeypatch.setattr(b, "_flow_post_summary", lambda *a, **k: None)
    b._splice_segment_safe(wf.id, "seg-sess", keys[3], "branch")
    assert calls["branch"]["from_step_key"] == keys[3]


def test_capabilities_name_the_variant_branch():
    from handlers.workflows import capabilities_needed

    step = {"action": {"action": "wait_for_one_of"}, "branch": {"flow": True, "options": []}}
    assert capabilities_needed([step]) == {"variant_branch"}
    assert capabilities_needed([{"action": {"action": "wait_for_one_of"}, "branch": {"options": []}}]) == set()


def test_a_pack_lists_the_engine_features_it_uses_in_its_manifest():
    from conxa_compile.skill_package_builder_output import execution_capabilities

    steps = [
        {"type": "date_pick", "handler_hints": {"date_picker": {"date_offset_days": 2}}},
        {"type": "click", "identity_bundle": {"signals": [{"selector": 'role=link[name="{{f}}"]'}], "fingerprint": {}}},
        {"type": "wait_for_one_of", "flow": True, "options": [{"steps": [{"type": "click", "on_missing": "ask"}]}]},
    ]
    assert execution_capabilities(steps) == ["ask_missing", "date_offset", "templated_click", "variant_branch"]
    assert execution_capabilities([{"type": "click"}]) == []
