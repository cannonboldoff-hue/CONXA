"""Outcome forks: a recorded decision dialog ("file already exists: Replace / Keep both / Cancel")
compiles into one ``outcome_fork`` step instead of a replayed click or a ``try_dismiss``
(conxa_compile/compiler/outcome_fork.py), and exports to the shape runtime/app/fork.js consumes."""

from __future__ import annotations

import json

from conxa_core.models.skill_spec import Assertion, IdentityBundle, IdentitySignal, SkillStep, ValidationBlock

from conxa_compile.compiler.outcome_fork import (
    CONSENT,
    DECISION,
    DISMISSABLE,
    FORM,
    build_outcome_forks,
    classify_dialog,
    message_tokens,
)

CONTAINER = '[role="dialog"]'


def _btn(name, chosen=False):
    return {"tag": "button", "role": "button", "name": name, "text": name, "chosen": chosen}


def _ctx(chosen="Replace", names=("Replace", "Keep both", "Cancel"), **over):
    base = {
        "kind": "dom",
        "container_signal": CONTAINER,
        "role": "dialog",
        "title": "Upload options",
        "message": 'report.pdf already exists in this location.',
        "has_fields": False,
        "buttons": [_btn(n, n == chosen) for n in names],
    }
    base.update(over)
    return base


def _click(name="Replace", ctx=None, **kw) -> SkillStep:
    bundle = IdentityBundle(
        signals=[IdentitySignal(engine="role", selector=f'internal:role=button[name="{name}"i]', durability=0.9,
                                orthogonality_class="semantic")],
        stable_hash=f"hash-{name}",
    )
    base = dict(
        action="click",
        intent=f"click_{name.lower()}",
        identity_bundle=bundle,
        target={"primary_selector": f'internal:role=button[name="{name}"i]'},
        signals={"dialog_context": ctx} if ctx is not None else {},
    )
    base.update(kw)
    return SkillStep(**base)


def _upload(**kw) -> SkillStep:
    return SkillStep(action="click", intent="click_upload", url="", target={"primary_selector": "#up"}, **kw)


def _fork_of(steps):
    out = build_outcome_forks(steps, "sess1")
    return out, [s for s in out if s.action == "outcome_fork"]


# ── classification ─────────────────────────────────────────────────────────────────────────


def test_classifies_a_replace_keep_cancel_dialog_as_a_decision():
    assert classify_dialog(_ctx()) == DECISION


def test_consent_banners_stay_on_the_old_path():
    ctx = _ctx(title="We use cookies", names=("Accept all", "Reject all"))
    assert classify_dialog(ctx) == CONSENT


def test_dialogs_with_fields_are_forms_not_decisions():
    assert classify_dialog(_ctx(has_fields=True, names=("Save", "Cancel"))) == FORM


def test_a_dialog_of_only_close_buttons_is_dismissable():
    assert classify_dialog(_ctx(names=("Close", "Cancel"))) == DISMISSABLE
    assert classify_dialog(_ctx(names=("Got it",))) == DISMISSABLE


def test_native_dialogs_are_left_to_their_own_steps():
    assert classify_dialog(_ctx(kind="native")) == DISMISSABLE


def test_missing_context_is_dismissable():
    assert classify_dialog(None) == DISMISSABLE


def test_message_tokens_drop_file_names_and_numbers():
    assert message_tokens('"q3.xlsx" already exists (3 copies)') == message_tokens('"other.pdf" already exists (9 copies)')
    assert "q3" not in message_tokens('"q3.xlsx" already exists')


# ── fork construction ──────────────────────────────────────────────────────────────────────


def test_a_decision_click_becomes_one_fork_with_every_button_as_an_option():
    out, forks = _fork_of([_upload(), _click("Replace", _ctx("Replace"))])
    assert len(out) == 2 and len(forks) == 1
    fork = forks[0].outcome_fork
    state = fork["states"][0]
    assert [o["label"] for o in state["options"]] == ["Replace", "Keep both", "Cancel"]
    assert [o["recorded"] for o in state["options"]] == [True, False, False]
    assert state["signature"]["button_labels"] == ["Replace", "Keep both", "Cancel"]
    assert state["observed_in"] == ["sess1"]
    assert fork["clean"] == {"observed": False, "observed_in": []}


def test_a_consequential_recorded_choice_asks_a_person_by_default():
    _, forks = _fork_of([_click("Replace", _ctx("Replace"))])
    policy = forks[0].outcome_fork["states"][0]["policy"]
    assert policy == {"mode": "ask_human", "suggested": "replace"}


def test_a_harmless_recorded_choice_runs_automatically_but_unapproved():
    _, forks = _fork_of([_click("Keep both", _ctx("Keep both"))])
    policy = forks[0].outcome_fork["states"][0]["policy"]
    assert policy == {"mode": "auto", "option_id": "keep_both", "author_approved": False}


def test_consequential_options_are_marked_destructive_and_irreversible():
    _, forks = _fork_of([_click("Keep both", _ctx("Keep both"))])
    options = {o["id"]: o for o in forks[0].outcome_fork["states"][0]["options"]}
    assert options["replace"]["role"] == "consequential"
    assert options["replace"]["steps"][0]["identity_bundle"]["destructive"] is True
    assert options["replace"]["steps"][0]["consequence"] == "irreversible"
    assert options["keep_both"]["role"] == "neutral"
    assert options["cancel"]["role"] == "dismiss"


def test_unrecorded_options_get_a_deterministic_selector_scoped_to_the_dialog():
    _, forks = _fork_of([_click("Replace", _ctx("Replace"))])
    options = {o["id"]: o for o in forks[0].outcome_fork["states"][0]["options"]}
    cancel_step = options["cancel"]["steps"][0]
    selectors = [s["selector"] for s in cancel_step["identity_bundle"]["signals"]]
    assert selectors and all(sel.startswith(f"{CONTAINER} >> ") for sel in selectors)
    assert cancel_step["target"]["primary_selector"].startswith(f"{CONTAINER} >> ")


def test_the_recorded_option_reuses_the_real_compiled_step():
    _, forks = _fork_of([_click("Replace", _ctx("Replace"))])
    recorded = forks[0].outcome_fork["states"][0]["options"][0]["steps"][0]
    assert recorded["identity_bundle"]["stable_hash"] == "hash-Replace"
    assert "dialog_context" not in recorded["signals"]


def test_a_multi_step_run_inside_one_dialog_collapses_into_a_single_fork():
    tick = _click("Apply to all", _ctx(chosen=""), intent="tick_apply_all")
    out, forks = _fork_of([_upload(), tick, _click("Replace", _ctx("Replace")), _upload()])
    assert [s.action for s in out] == ["click", "outcome_fork", "click"]
    recorded = forks[0].outcome_fork["states"][0]["options"][0]
    assert len(recorded["steps"]) == 2 and recorded["recorded"] is True


def test_the_trigger_steps_dialog_assertion_no_longer_fails_the_clean_path():
    trigger = _upload(validation=ValidationBlock(assertions=[
        Assertion(type="selector_present", target=CONTAINER, timeout_ms=5000, required=True),
        Assertion(type="url_changed", required=True),
    ]))
    build_outcome_forks([trigger, _click("Replace", _ctx("Replace"))], "s")
    by_type = {a.type: a.required for a in trigger.validation.assertions}
    assert by_type == {"selector_present": False, "url_changed": True}


def test_the_popup_message_never_survives_compilation():
    out, forks = _fork_of([_upload(), _click("Replace", _ctx("Replace"))])
    assert all("dialog_context" not in s.signals for s in out)
    assert "report.pdf" not in json.dumps(forks[0].outcome_fork)


def test_a_dialog_where_no_button_was_recorded_is_left_alone():
    steps = [_upload(), _click("Replace", _ctx(chosen=""))]
    out, forks = _fork_of(steps)
    assert forks == [] and len(out) == 2


def test_non_decision_dialogs_and_legacy_steps_are_untouched():
    steps = [_upload(), _click("Accept all", _ctx("Accept all", names=("Accept all", "Reject all"), title="Cookies")), _click("Go")]
    out, forks = _fork_of(steps)
    assert forks == [] and [s.intent for s in out] == [s.intent for s in steps]


def test_two_forks_in_one_workflow_get_distinct_step_keys():
    from conxa_compile.compiler.step_key import step_keys

    other = _ctx("Keep both", title="Second prompt", container_signal='[role="alertdialog"]')
    out, forks = _fork_of([_click("Replace", _ctx("Replace")), _upload(), _click("Keep both", other)])
    assert len(forks) == 2
    assert len(set(step_keys(out))) == len(out)


# ── export to execution.json ───────────────────────────────────────────────────────────────


def _export(tmp_path, steps):
    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill

    doc = {"meta": {"id": "skill_x", "version": 1}, "skills": [{"steps": [json.loads(s.model_dump_json()) for s in steps]}], "inputs": []}
    warnings: list[str] = []
    _build_workflow_from_saved_skill(bundle_root=tmp_path, workflow_slug="fork", saved_skill=doc, on_warning=warnings.append)
    return json.loads((tmp_path / "skills" / "fork" / "execution.json").read_text()), warnings


def test_exports_a_runnable_fork_with_option_bodies(tmp_path):
    steps, _ = _fork_of([_click("Replace", _ctx("Replace"))])
    execution, _ = _export(tmp_path, steps)
    fork = next(s for s in execution if s["type"] == "outcome_fork")
    state = fork["states"][0]
    assert fork["unknown_policy"] == "ask_human" and fork["timeout_ms"] == 4000
    assert state["policy"] == {"mode": "ask_human", "suggested": "replace"}
    assert [o["id"] for o in state["options"]] == ["replace", "keep_both", "cancel"]
    assert all(o["steps"] and o["steps"][0]["type"] == "click" for o in state["options"])
    replace_step = state["options"][0]["steps"][0]
    assert replace_step["destructive"] is True
    assert "report.pdf" not in json.dumps(fork)


def test_export_falls_back_to_ask_human_when_the_auto_option_cannot_run(tmp_path):
    steps, forks = _fork_of([_click("Keep both", _ctx("Keep both"))])
    forks[0].outcome_fork["states"][0]["policy"]["option_id"] = "does_not_exist"
    execution, _ = _export(tmp_path, steps)
    fork = next(s for s in execution if s["type"] == "outcome_fork")
    assert fork["states"][0]["policy"]["mode"] == "ask_human"


def test_a_fork_with_no_runnable_option_is_dropped_from_the_pack(tmp_path):
    steps, forks = _fork_of([_upload(), _click("Replace", _ctx("Replace"))])
    for opt in forks[0].outcome_fork["states"][0]["options"]:
        opt["steps"] = []
    execution, warnings = _export(tmp_path, steps)
    assert all(s["type"] != "outcome_fork" for s in execution)
    assert any("no runnable choices" in w for w in warnings)


# ── editor guard ───────────────────────────────────────────────────────────────────────────


def test_a_generic_patch_cannot_touch_a_forks_policy():
    import pytest

    from conxa_compile.editor.patch_gate import validate_editor_patch

    _, forks = _fork_of([_click("Replace", _ctx("Replace"))])
    step = json.loads(forks[0].model_dump_json())
    with pytest.raises(ValueError, match="outcome_fork_step_allows_only"):
        validate_editor_patch(step, {"outcome_fork": {"states": []}}, {})


def test_second_opinion_never_archives_a_fork():
    from conxa_compile.compiler.second_opinion import archive_flagged_steps
    from conxa_compile.compiler.step_key import step_keys

    out, _ = _fork_of([_click("Replace", _ctx("Replace"))])
    key = step_keys(out)[0]
    kept, archived = archive_flagged_steps(out, [{"kind": "flag_noise", "step_key": key, "proposed": "noise", "why": "x"}])
    assert archived == [] and len(kept) == 1
