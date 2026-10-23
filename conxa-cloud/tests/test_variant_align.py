"""Variant recordings: comparing a second take (the popup does NOT appear) with the workflow's own
recording around an outcome fork (compiler/variant_align.py), folding the result into the fork
without a recompile, and keeping the primary recording safe while a variant is recorded."""

from __future__ import annotations

import json

from conxa_core.models.skill_spec import IdentityBundle, SkillStep

from conxa_compile.compiler.outcome_fork import build_outcome_forks
from conxa_compile.compiler.variant_align import compare_variant, event_signature
from conxa_compile.editor.workflow_mutations import apply_variant_observation

CONTAINER = '[role="dialog"]'
KNOWN = [(CONTAINER, "Upload options")]


def _ctx(chosen="Replace", names=("Replace", "Keep both", "Cancel"), title="Upload options", container=CONTAINER):
    return {
        "kind": "dom", "container_signal": container, "role": "dialog", "title": title,
        "message": "report.pdf already exists.", "has_fields": False,
        "buttons": [{"tag": "button", "role": "button", "name": n, "text": n, "chosen": n == chosen} for n in names],
    }


def _ev(action, label="", *, role="button", value=None, url="https://drive.test/folder", dialog=None):
    ev = {
        "action": {"action": action, "value": value, "url": url},
        "target": {"role": role, "inner_text": label},
        "page": {"url": url},
    }
    if dialog is not None:
        ev["dialog_context"] = dialog
    return ev


def _primary():
    return [
        _ev("navigate", url="https://drive.test/folder"),
        _ev("click", "New"),
        _ev("upload", "File upload", value="/tmp/report.pdf"),
        _ev("click", "Replace", dialog=_ctx("Replace")),
        _ev("click", "Done"),
    ]


def test_signatures_ignore_typed_values_and_query_strings():
    a = _ev("upload", "File upload", value="/tmp/a.pdf")
    b = _ev("upload", "File upload", value="/other/b.pdf")
    assert event_signature(a) == event_signature(b)
    assert event_signature(_ev("navigate", url="https://x.test/p?a=1")) == event_signature(_ev("navigate", url="https://x.test/p?a=2"))


def test_a_variant_with_no_popup_proves_the_clean_path():
    variant = [_ev("navigate"), _ev("click", "New"), _ev("upload", "File upload", value="/tmp/brand-new.pdf"), _ev("click", "Done")]
    out = compare_variant(_primary(), variant, KNOWN)
    assert out["clean_observed"] is True and out["unexplained"] == [] and out["new_dialogs"] == []


def test_an_identical_take_proves_nothing():
    out = compare_variant(_primary(), _primary(), KNOWN)
    assert out["clean_observed"] is False and out["unexplained"] == []


def test_a_variant_that_hit_the_known_popup_again_does_not_count_as_clean():
    variant = _primary()
    variant[3] = _ev("click", "Keep both", dialog=_ctx("Keep both"))
    out = compare_variant(_primary(), variant, KNOWN)
    assert out["clean_observed"] is False


def test_a_different_popup_only_the_variant_saw_becomes_a_new_dialog():
    other = _ctx("Discard", names=("Discard", "Keep editing"), title="Unsaved changes", container='[role="alertdialog"]')
    variant = [_ev("navigate"), _ev("click", "New"), _ev("upload", "File upload"), _ev("click", "Discard", dialog=other), _ev("click", "Done")]
    out = compare_variant(_primary(), variant, KNOWN)
    assert [d["title"] for d in out["new_dialogs"]] == ["Unsaved changes"]
    assert out["clean_observed"] is False  # the variant had a popup of its own, so it says nothing about "no popup"


def test_any_other_difference_is_reported_and_never_treated_as_clean():
    variant = [_ev("navigate"), _ev("click", "New"), _ev("upload", "File upload"), _ev("click", "Share"), _ev("click", "Done")]
    out = compare_variant(_primary(), variant, KNOWN)
    assert out["clean_observed"] is False
    assert any("Share" in line for line in out["unexplained"])


# -- folding the result into the skill ----------------------------------------------------------


def _doc():
    def click(name, ctx=None):
        return SkillStep(
            action="click", intent=f"click_{name.lower()}", identity_bundle=IdentityBundle(stable_hash=f"h-{name}"),
            target={"primary_selector": f'internal:role=button[name="{name}"i]'}, signals={"dialog_context": ctx} if ctx else {},
        )

    steps = build_outcome_forks([SkillStep(action="click", intent="up", target={"primary_selector": "#u"}), click("Replace", _ctx("Replace"))], "s1")
    return {"meta": {"id": "skill_x", "version": 3}, "skills": [{"steps": [json.loads(s.model_dump_json()) for s in steps]}], "inputs": []}


def test_a_clean_observation_flips_the_forks_clean_flag_and_bumps_the_version():
    out = apply_variant_observation(_doc(), 1, "variant1", {"clean_observed": True, "new_dialogs": [], "unexplained": []})
    fork = out["skills"][0]["steps"][1]["outcome_fork"]
    assert fork["clean"] == {"observed": True, "observed_in": ["variant1"]}
    assert out["meta"]["version"] == 4


def test_applying_the_same_variant_twice_does_not_duplicate_it():
    once = apply_variant_observation(_doc(), 1, "variant1", {"clean_observed": True, "new_dialogs": []})
    twice = apply_variant_observation(once, 1, "variant1", {"clean_observed": True, "new_dialogs": []})
    assert twice["skills"][0]["steps"][1]["outcome_fork"]["clean"]["observed_in"] == ["variant1"]


def test_a_new_popup_becomes_a_new_state_and_leaves_the_existing_one_alone():
    before = _doc()
    other = _ctx("Keep editing", names=("Discard", "Keep editing"), title="Unsaved changes", container='[role="alertdialog"]')
    out = apply_variant_observation(before, 1, "variant1", {"clean_observed": False, "new_dialogs": [other]})
    states = out["skills"][0]["steps"][1]["outcome_fork"]["states"]
    assert [s["id"] for s in states] == ["dialog_upload_options", "dialog_unsaved_changes"]
    assert states[0] == before["skills"][0]["steps"][1]["outcome_fork"]["states"][0]
    new = states[1]
    assert [o["label"] for o in new["options"]] == ["Discard", "Keep editing"]
    assert [o["recorded"] for o in new["options"]] == [False, True]
    assert new["policy"] == {"mode": "auto", "option_id": "keep_editing", "author_approved": False}
    discard_step = new["options"][0]["steps"][0]
    assert discard_step["identity_bundle"]["destructive"] is True
    assert all(sig["selector"].startswith('[role="alertdialog"] >> ') for sig in discard_step["identity_bundle"]["signals"])


def test_unexplained_differences_are_never_applied():
    out = apply_variant_observation(_doc(), 1, "variant1", {"clean_observed": False, "new_dialogs": [], "unexplained": ["Only in the new recording: Share"]})
    fork = out["skills"][0]["steps"][1]["outcome_fork"]
    assert fork["clean"]["observed"] is False and len(fork["states"]) == 1


# -- the primary recording stays safe -----------------------------------------------------------


def test_variants_never_replace_the_primary_recording(tmp_path, monkeypatch):
    from conxa_core.config import settings
    from conxa_core.models.workflow import Workflow
    from conxa_core.storage import workflow_store as ws

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    wf = Workflow(id="wf1", slug="upload", name="Upload", group_id="g", target_url="https://x.test", session_id="primary", recording_status="compiled", skill_id="skill_x")
    ws.save_workflow(wf)

    ws.add_variant("wf1", "v1", "keyA")
    after = ws.get_workflow("wf1")
    assert after.session_id == "primary" and after.skill_id == "skill_x" and after.recording_status == "compiled"
    assert [v["session_id"] for v in after.variant_session_ids] == ["v1"]

    ws.remove_variant("wf1", "v1")
    assert ws.get_workflow("wf1").session_id == "primary" and ws.get_workflow("wf1").variant_session_ids == []

    ws.add_variant("wf1", "v2", "keyA")
    ws.set_recording("wf1", "new_primary")  # a fresh primary take makes old variants meaningless
    assert ws.get_workflow("wf1").variant_session_ids == []
