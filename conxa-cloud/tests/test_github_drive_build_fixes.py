"""Fixes found by investigating the "Github to Drive" dev workflow (2026-11-14): a build pass that crashed on a
reply of the wrong type, steps left without a purpose, junk proof words, a fake "you typed this" question."""
import pytest

from conxa_compile.compiler.build import _record_drops
from conxa_compile.compiler.effects import _final_words
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import _template_assertion_targets
from conxa_compile.llm import openapi_client
from conxa_compile.policy.intent_ontology import normalize_compiler_intent
from handlers.compile_flow import CompileFlowMixin


# ── A: a reply field of the wrong type must not crash the review ─────────────────────────────────────────────

def test_review_reply_with_wrong_types_does_not_raise(monkeypatch):
    reply = {"goal": "g", "steps": "a sentence", "decision_points": None, "expected_end_state": "the file is uploaded",
             "suggestions": {"not": "a list"}, "understanding": "text", "loop_copy": "x"}
    monkeypatch.setattr(openapi_client, "_call_llm_or_none", lambda *a, **k: dict(reply))
    out = openapi_client.infer_workflow_review(steps=[], page_urls=[], sibling_bindings={})
    assert out["goal"] == "g"
    assert out["steps"] == [] and out["suggestions"] == [] and out["loop_copy"] == []
    assert out["expected_end_state"] == {} and out["understanding"] is None


def test_review_reply_with_right_types_is_kept(monkeypatch):
    reply = {"goal": "g", "steps": [{"index": 0}], "expected_end_state": {"kind": "file"}}
    monkeypatch.setattr(openapi_client, "_call_llm_or_none", lambda *a, **k: dict(reply))
    out = openapi_client.infer_workflow_review(steps=[], page_urls=[], sibling_bindings={})
    assert out["steps"] == [{"index": 0}] and out["expected_end_state"] == {"kind": "file"}


# ── A0: a crashed build pass is reported, not swallowed ──────────────────────────────────────────────────────

def test_degraded_passes_are_listed(monkeypatch):
    doc = {"compile_report": {"degraded": [{"pass": "second_opinion", "reason": "llm_call_failed", "detail": "ValueError: x"}]}}
    monkeypatch.setattr("conxa_core.storage.json_store.read_skill", lambda _sid: doc)
    assert CompileFlowMixin._flow_degraded_passes("skill_x") == ["second_opinion: llm_call_failed: ValueError: x"]


def test_clean_build_lists_nothing(monkeypatch):
    monkeypatch.setattr("conxa_core.storage.json_store.read_skill", lambda _sid: {"compile_report": {"status": "ok"}})
    assert CompileFlowMixin._flow_degraded_passes("skill_x") == []
    assert CompileFlowMixin._flow_degraded_passes(None) == []


# ── B: a purpose for steps with no text of their own; a step without one stays editable ──────────────────────

_POLICY = {"intent": {}}


def _ev(action, **target):
    return {"action": {"action": action}, "target": target, "semantic": {}}


def test_icon_button_is_named_by_its_label():
    ev = _ev("click", tag="button", inner_text="", label_text="Download raw file", data_testid="download-raw-button")
    assert "download_raw_file" in normalize_compiler_intent(ev, "", _POLICY)


def test_menu_entry_is_named_without_its_shortcut_hint():
    ev = _ev("click", tag="li", role="menuitem", inner_text="File upload Alt+C then U")
    intent = normalize_compiler_intent(ev, "", _POLICY)
    assert "file_upload" in intent and "alt" not in intent


def test_upload_step_gets_a_purpose():
    assert normalize_compiler_intent(_ev("upload_intent", tag="input"), "", _POLICY) == "upload_intent"


def test_step_with_no_purpose_can_still_take_other_edits():
    step = {"action": {"action": "upload"}, "intent": ""}
    validate_editor_patch(step, {"validation": {"effect": {"kind": "upload"}}}, {})


def test_purpose_cannot_be_blanked():
    step = {"action": {"action": "click"}, "intent": "open_menu"}
    with pytest.raises(ValueError):
        validate_editor_patch(step, {"intent": ""}, {})
    with pytest.raises(ValueError):
        validate_editor_patch({"action": {"action": "click"}, "intent": ""}, {"intent": ""}, {})


# ── D: the phrase that says the work finished beats chatter ──────────────────────────────────────────────────

def test_completion_phrase_wins_over_window_and_tooltip_text():
    ev = {"action": {"action": "upload_intent"}, "post_watch": {"progress_seen": True, "final_status": [
        {"kind": "status", "words": ["maximized"]},
        {"kind": "status", "words": ["more", "actions", "alt"]},
        {"kind": "status", "words": ["upload", "complete", "kicad", "gitignore"]},
    ]}}
    assert _final_words(ev) == [["upload", "complete", "kicad", "gitignore"]]


def test_other_language_phrases_are_kept_when_none_has_a_known_word():
    ev = {"action": {"action": "upload_intent"}, "post_watch": {"progress_seen": True, "final_status": [
        {"kind": "status", "words": ["upload", "abgeschlossen"]}]}}
    assert _final_words(ev) == [["upload", "abgeschlossen"]]


# ── F: only what a person typed is asked about; old values leave no stale check ──────────────────────────────

def _drop_event(action, value, seq):
    return {"action": {"action": action, "value": value}, "target": {}, "extras": {"sequence": seq}}


def test_page_changes_are_not_reported_as_typed_values():
    before = [_drop_event("manual_navigate", '{"from_url": "https://a", "to_url": "https://b"}', 3), _drop_event("fill", "hello", 4)]
    ledger: list = []
    _record_drops(ledger, before, [], "test")
    by_seq = {e["sequence"]: e for e in ledger}
    assert "value" not in by_seq[3]
    assert by_seq[4]["value"] == "hello"


def test_optional_text_checks_on_the_old_value_are_dropped():
    steps = [{"validation": {"assertions": [
        {"type": "text_present", "target": "kicad", "required": False},
        {"type": "text_present", "target": "gitignore", "required": False},
        {"type": "text_present", "target": "kicad", "required": True},
        {"type": "url_pattern", "target": "^https://x/KiCad.gitignore", "required": False},
    ]}}]
    _template_assertion_targets(steps, "KiCad.gitignore", "{{t}}")
    kept = steps[0]["validation"]["assertions"]
    assert [a["type"] for a in kept] == ["text_present", "url_pattern"]
    assert kept[0]["required"] is True and kept[1]["target"] == "^https://x/{{t}}"


# ── round 2: general rules ───────────────────────────────────────────────────────────────────────────────────

from conxa_compile.compiler.validation_planner import _drop_echoes, infer_success_conditions  # noqa: E402
from conxa_compile.editor.action_registry import VALUE_ACTIONS  # noqa: E402


def test_text_that_only_repeats_what_was_chosen_is_not_a_check():
    chosen = {"action": {"action": "click"}, "target": {"inner_text": "Quarterly Report.pdf"}}
    kept = _drop_echoes(["Quarterly Report", "pdf", "Saved to your library"], chosen)
    assert kept == ["Saved to your library"]


def test_echo_filter_reaches_the_planned_checks():
    chosen = {"action": {"action": "click"}, "target": {"inner_text": "Quarterly Report.pdf"}}
    out = infer_success_conditions({"type": "none"}, {"text_change": ["Quarterly Report", "Saved to your library"]}, "https://x/",
                                   {}, source_step=chosen)
    assert out["expected_text_tokens"] == ["Saved to your library"]


def test_nothing_chosen_means_nothing_filtered():
    assert _drop_echoes(["anything"], None) == ["anything"]


@pytest.mark.parametrize("action", sorted(VALUE_ACTIONS | {"manual_navigate", "download_observed", "upload_intent", "click", "scroll", "brand_new"}))
def test_only_typing_actions_keep_a_value(action):
    ledger: list = []
    _record_drops(ledger, [_drop_event(action, "a blob", 1)], [], "test")
    typed = {"type", "fill", "select", "select_option", "date_pick", "set_checkbox", "set_radio"}
    assert ("value" in ledger[0]) == (action in typed)


# ── §5: a file input is found by what it is ──────────────────────────────────────────────────────────────────

from conxa_compile.compiler.identity_bundle import generate_deterministic_signals  # noqa: E402


def _file_event(attrs=None):
    return {"action": {"action": "upload_intent"}, "target": {"tag": "input", "role": "button", "label_text": "", "attributes": attrs or {}},
            "semantic": {"input_type": "file", "role": "button"},
            "selectors": {"css": "html > body > input:nth-of-type(2)"}}


def test_file_input_gets_a_signal_that_is_not_its_position():
    html = '<html><body><input type="file" name="avatar"><input type="file"></body></html>'
    sigs = generate_deterministic_signals(_file_event(), html)
    top = max(sigs, key=lambda s: s.durability)
    assert "nth-of-type" not in top.selector and 'input[type="file"]' in top.selector


def test_file_input_is_narrowed_by_its_name_when_it_has_one():
    html = '<html><body><input type="file" name="avatar"><input type="file" name="doc"></body></html>'
    sigs = generate_deterministic_signals(_file_event({"name": "avatar"}), html)
    top = max(sigs, key=lambda s: s.durability)
    assert top.selector == 'input[type="file"][name="avatar"]' and top.unique_at_compile is True


# ── §7: sign-in status follows the group, not a stored copy ──────────────────────────────────────────────────

from types import SimpleNamespace  # noqa: E402

from handlers.workflows import _live_sign_in_status  # noqa: E402


def _wf(status="needs_auth", group_id="g1"):
    return SimpleNamespace(status=status, group_id=group_id)


def test_status_turns_ready_once_every_app_is_signed_in(monkeypatch):
    group = SimpleNamespace(apps=[SimpleNamespace(captured_at=1.0), SimpleNamespace(captured_at=2.0)])
    monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _g: group)
    assert _live_sign_in_status(_wf()) == "ready"


def test_status_needs_sign_in_while_an_app_is_missing(monkeypatch):
    group = SimpleNamespace(apps=[SimpleNamespace(captured_at=1.0), SimpleNamespace(captured_at=None)])
    monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _g: group)
    assert _live_sign_in_status(_wf(status="ready")) == "needs_auth"


def test_error_and_ungrouped_workflows_keep_their_stored_status(monkeypatch):
    assert _live_sign_in_status(_wf(status="error")) == "error"
    assert _live_sign_in_status(_wf(group_id="")) == "needs_auth"
    monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _g: None)
    assert _live_sign_in_status(_wf()) == "needs_auth"


# ── §8: the count says what the person did ───────────────────────────────────────────────────────────────────

from handlers.session import _action_count  # noqa: E402


def test_actions_the_page_made_on_its_own_are_not_counted():
    events = [{"action": "click"}, {"action": "click", "origin": "script"}, {"action": "scroll"}]
    assert _action_count(events) == 2
    assert _action_count([]) == 0
