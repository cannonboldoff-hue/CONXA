"""Proof of Done at build time: what "done" means for a step that starts work (compiler/effects.py)."""

import copy

from conxa_compile.compiler.effects import (
    RUNTIME_PROBES,
    build_effect,
    calibrate_effect,
    classify_kind,
    has_real_proof,
    plain_sentence,
)
from conxa_compile.skill_package_builder_saved_skill import _copy_saved_common
from conxa_core.models.events import RecordedEvent
from conxa_core.models.skill_spec import Effect, ValidationBlock


def _click(text: str, **extra):
    return {"action": {"action": "click"}, "target": {"inner_text": text}, **extra}


def _watch(*words: str, kind: str = "status"):
    return {"progress_seen": True, "waited_ms": 3000, "final_status": [{"kind": kind, "words": list(words)}]}


# ── what kind of work an action starts ──────────────────────────────────────────────────────────


def test_kinds_come_from_the_action_and_the_buttons_own_words():
    assert classify_kind({"action": {"action": "upload_intent"}}) == "upload"
    assert classify_kind({"action": {"action": "download_observed"}}) == "download"
    assert classify_kind(_click("Delete project"), destructive=True, consequence="irreversible") == "delete"
    assert classify_kind(_click("Send invoice"), consequence="irreversible") == "send"
    assert classify_kind(_click("Save"), is_commit=True) == "save"
    assert classify_kind(_click("Export report"), is_commit=True) == "long_job"
    assert classify_kind(_click("Create"), is_commit=True) == "save"


def test_a_step_that_starts_no_work_gets_no_effect():
    for ev in ({"action": {"action": "type"}}, {"action": {"action": "navigate"}}, _click("Open menu"), {"action": {"action": "select"}}):
        assert classify_kind(ev) is None and build_effect(ev) is None, ev


def test_a_progress_window_after_a_plain_click_makes_it_a_long_job():
    assert classify_kind(_click("Go", post_watch=_watch("upload", "complete"))) == "long_job"


# ── the checklist ───────────────────────────────────────────────────────────────────────────────


def test_an_upload_gets_separate_witnesses_and_always_a_failure_veto_last():
    eff = build_effect({"action": {"action": "upload_intent"}, "post_watch": _watch("upload", "complete")})
    assert eff.kind == "upload"
    by_probe = {c.probe["type"]: c for c in eff.checks}
    assert by_probe["input_has_files"].witness == "target" and by_probe["input_has_files"].rung == "performed"
    assert by_probe["request_chain"].witness == "transport" and by_probe["request_chain"].rung == "completed"
    assert by_probe["new_text"].probe["words"] == ["upload", "complete"] and by_probe["new_text"].witness == "renderer"
    assert by_probe["fresh_look"].witness == "fresh_look" and by_probe["fresh_look"].rung == "result_exists"
    assert eff.checks[-1].role == "veto" and eff.checks[-1].probe["type"] == "failure_signal"
    assert [c.id for c in eff.checks] == ["c1", "c2", "c3", "c4", "c5"]


def test_the_required_rung_never_asks_for_more_than_the_runtime_can_prove():
    eff = build_effect({"action": {"action": "upload_intent"}})
    assert eff.target_rung == "result_exists", "the design wants the right item to be really there"
    assert eff.required_rung == "completed", "but the runtime of this version cannot prove that yet"
    assert build_effect(_click("Save"), is_commit=True).required_rung == "completed"
    assert all(c.probe["type"] in RUNTIME_PROBES for c in eff.checks)


def test_the_quorum_starts_at_one_so_a_working_site_never_stops_for_a_signal_it_does_not_expose():
    for ev, kw in [({"action": {"action": "upload_intent"}}, {}), (_click("Delete"), {"destructive": True}), (_click("Send"), {"consequence": "irreversible"})]:
        assert build_effect(ev, **kw).quorum.witnesses == 1


def test_words_are_taken_only_from_a_completion_message_never_from_an_alert_or_progress():
    # A status line right after a click counts only when it carries a completion word.
    saved = build_effect(_click("Save", surface_context={"items": [{"kind": "status", "words": ["changes", "saved"]}]}), is_commit=True)
    assert [c.probe["words"] for c in saved.checks if c.probe["type"] == "new_text"] == [["changes", "saved"]]
    chatter = build_effect(_click("Save", surface_context={"items": [{"kind": "status", "words": ["saving", "changes"]}]}), is_commit=True)
    assert not [c for c in chatter.checks if c.probe["type"] == "new_text"]
    alert = build_effect(_click("Save", surface_context={"items": [{"kind": "alert", "words": ["failed", "saved"]}]}), is_commit=True)
    assert not [c for c in alert.checks if c.probe["type"] == "new_text"], "an alert is a failure, not a completion"


def test_no_check_carries_a_selector_or_a_value():
    eff = build_effect({"action": {"action": "upload_intent"}, "post_watch": _watch("upload", "complete")})
    dumped = eff.model_dump_json()
    for forbidden in ("selector", "css", "xpath", "#", "http"):
        assert forbidden not in dumped, forbidden


def test_plain_sentence_reads_in_everyday_words():
    eff = build_effect({"action": {"action": "upload_intent"}, "post_watch": _watch("upload", "complete")})
    assert plain_sentence(eff) == "the app accepted the request; the app says “upload complete”; the file shows up when the page is opened again"


# ── the model ───────────────────────────────────────────────────────────────────────────────────


def test_the_effect_round_trips_through_the_skill_document_and_into_execution_json():
    eff = build_effect({"action": {"action": "upload_intent"}})
    step = {"validation": ValidationBlock(effect=eff).model_dump()}
    out = _copy_saved_common(step, {})
    assert out["validation"]["effect"]["kind"] == "upload"
    assert Effect.model_validate(out["validation"]["effect"]).required_rung == "completed"
    # no effect, no key: an older pack looks exactly as before
    assert "validation" not in _copy_saved_common({"validation": ValidationBlock().model_dump()}, {})


def test_a_recorded_post_watch_is_accepted_by_the_event_model():
    assert "post_watch" in RecordedEvent.model_fields


# ── calibration and the publish rule ────────────────────────────────────────────────────────────


def _upload_effect():
    return build_effect({"action": {"action": "upload_intent"}, "post_watch": _watch("upload", "complete")}).model_dump()


def test_calibration_marks_dead_checks_and_raises_the_quorum_to_the_witnesses_that_really_answered():
    eff = _upload_effect()
    got = calibrate_effect(eff, {"c1": "yes", "c2": "yes", "c3": "no", "c4": "no", "c5": "no"})
    flags = {c["id"]: c["calibrated"] for c in got["checks"]}
    assert flags["c1"] is True and flags["c2"] is True and flags["c4"] is False
    assert flags["c3"] is None, "the words the recording itself saw are never retired by a run; only the author can"
    assert flags["c5"] is None, "a veto cannot be proven by a run that had no failure"
    assert got["quorum"]["witnesses"] == 2, "the target and transport witnesses really answered"
    assert eff["quorum"]["witnesses"] == 1, "the input is not changed in place"
    assert all(c["calibration_basis"] == ["c2"] for c in got["checks"] if c["calibrated"] is not None), "a mark names the check that proved the run"


def test_a_run_that_rested_on_nothing_but_the_step_running_teaches_nothing():
    # The exact answers of a false pass: the step ran, nothing the app did was seen. It must not retire a single check.
    eff = _upload_effect()
    got = calibrate_effect(eff, {"c1": "yes", "c2": "cant_tell", "c3": "no", "c4": "cant_tell", "c5": "no"})
    assert got == eff


def test_could_not_tell_never_retires_a_check():
    got = calibrate_effect(_upload_effect(), {"c1": "yes", "c2": "yes", "c3": "cant_tell", "c4": "cant_tell"})
    assert {c["id"]: c["calibrated"] for c in got["checks"]}["c4"] is None


def test_a_check_the_author_agreed_to_is_never_retired_by_a_run():
    eff = _upload_effect()
    for c in eff["checks"]:
        if c["probe"]["type"] == "fresh_look":
            c["confirmed"] = True
    got = calibrate_effect(eff, {"c1": "yes", "c2": "yes", "c4": "no"})
    assert {c["id"]: c["calibrated"] for c in got["checks"]}["c4"] is None


def test_calibration_leaves_a_check_the_run_never_reached_alone_and_never_drops_the_quorum_below_one():
    got = calibrate_effect(_upload_effect(), {})
    assert [c["calibrated"] for c in got["checks"]] == [None] * 5 and got["quorum"]["witnesses"] == 1


def test_calibration_never_asks_for_more_witnesses_than_the_design_wants():
    save = build_effect(_click("Save", surface_context={"items": [{"kind": "status", "words": ["saved"]}]}), is_commit=True).model_dump()
    got = calibrate_effect(save, {"c1": "yes", "c2": "yes"})
    assert got["quorum"]["witnesses"] == 1, "a save wants one witness"


def test_real_proof_means_a_live_support_check_at_completed_or_higher():
    eff = _upload_effect()
    assert has_real_proof(eff)
    dead = copy.deepcopy(eff)
    for c in dead["checks"]:
        if c["role"] != "veto" and c["rung"] != "performed":
            c["calibrated"], c["calibration_basis"] = False, ["c9"]  # shown dead by a run that really proved the step
    assert not has_real_proof(dead), "only the 'file was chosen' check is alive, and that proves nothing finished"
    bare = copy.deepcopy(dead)
    for c in bare["checks"]:
        c["calibration_basis"] = []  # a mark no proven run wrote (it may come from a false pass) is ignored
    assert has_real_proof(bare)
    unproven = copy.deepcopy(eff)
    for c in unproven["checks"]:
        c["calibrated"] = True
    assert not has_real_proof(unproven, strict=True), "'calibrated' without a basis is not shown to work"
    assert not has_real_proof(None) and not has_real_proof({})
    only_veto = {"checks": [{"id": "c1", "role": "veto", "rung": "completed", "probe": {"type": "failure_signal"}}]}
    assert not has_real_proof(only_veto), "'no error seen' is not proof that it worked"


# ── going to look raises the requirement only once a test run has shown it works ────────────────


def test_the_fresh_look_never_raises_what_a_step_requires_until_a_clean_run_saw_it_work():
    eff = _upload_effect()
    assert eff["required_rung"] == "completed" and eff["target_rung"] == "result_exists"
    fresh = next(c for c in eff["checks"] if c["probe"]["type"] == "fresh_look")
    # a run in which the file was found on a fresh look: this site lists new files, so ask for that from now on
    seen = calibrate_effect(eff, {"c1": "yes", "c2": "yes", fresh["id"]: "yes"})
    assert seen["required_rung"] == "result_exists"
    # a run in which it was not found: the requirement stays, and the dead check stops counting
    unseen = calibrate_effect(eff, {"c1": "yes", "c2": "yes", fresh["id"]: "no"})
    assert unseen["required_rung"] == "completed"
    assert {c["id"]: c["calibrated"] for c in unseen["checks"]}[fresh["id"]] is False
    # a run that could not tell: the requirement stays, and nothing is retired
    unsure = calibrate_effect(eff, {"c1": "yes", "c2": "yes", fresh["id"]: "cant_tell"})
    assert unsure["required_rung"] == "completed"
    assert {c["id"]: c["calibrated"] for c in unsure["checks"]}[fresh["id"]] is None


def test_only_an_upload_with_a_single_named_file_gets_a_fresh_look():
    assert not [c for c in build_effect(_click("Delete project"), destructive=True, consequence="irreversible").checks if c.probe["type"] == "fresh_look"]
    assert not [c for c in build_effect(_click("Save"), is_commit=True).checks if c.probe["type"] == "fresh_look"]


def test_app_words_never_learn_a_result_as_progress_or_keep_the_persons_file_name():
    from conxa_compile.compiler.effects import app_words

    ev = {
        "action": {"action": "upload_intent", "value": '[{"name":"Maven.gitignore","size":1,"type":""}]'},
        "post_watch": {
            "progress_seen": True,
            "progress_words": ["uploading", "complete", "gitignore", "maven", "starting", "cancel"],
            "final_status": [{"kind": "status", "words": ["complete", "gitignore", "maven"]}],
        },
    }
    words = app_words(ev)
    assert "complete" not in words.get("progress", [])
    assert not ({"gitignore", "maven"} & {w for g in words.values() for w in g})
    assert words["success"] == ["complete"]


def test_a_later_step_working_never_teaches_the_checklist():
    """The later step is the weakest finish witness (it may only prove the file existed), so a run whose only
    "completed" yes is later_step changes nothing: it must not retire the checks that answered "no"."""
    eff = {
        "kind": "download", "required_rung": "completed", "target_rung": "completed", "quorum": {"witnesses": 1},
        "checks": [
            {"id": "c1", "rung": "completed", "witness": "filesystem", "role": "support", "probe": {"type": "download_file"}},
            {"id": "c2", "rung": "completed", "witness": "later_step", "role": "support", "probe": {"type": "later_step", "step": "k"}},
        ],
    }
    assert calibrate_effect(copy.deepcopy(eff), {"c1": "no", "c2": "yes"}) == eff
    taught = calibrate_effect(copy.deepcopy(eff), {"c1": "yes", "c2": "yes"})
    assert [c.get("calibrated") for c in taught["checks"]] == [True, True]
