"""WF-3 Leg A (2026-11-16): a row's bin icon (<span id="delete-record-3" title="Delete" data-toggle="tooltip">) compiled
to an absolute page path alone, which can never match inside the row the step is bound to, so the Delete failed."""

from __future__ import annotations

from conxa_compile.compiler.identity_bundle import generate_deterministic_signals
from conxa_compile.compiler.named_attrs import is_identity_attr_key
from conxa_compile.conversation.clarify import _restore_literal_patch

ICON = {"action": {"action": "click"},
        "target": {"tag": "span", "title": "Delete", "attributes": {"data-toggle": "tooltip"}, "id": "delete-record-3"},
        "selectors": {"css": "#delete-record-3", "xpath": "//div[1]/table[1]/tbody[1]/tr[3]/td[7]/div[1]/span[2]"},
        "semantic": {"role": "span"}}


def test_a_titled_icon_is_found_by_its_tooltip():
    sels = [s.selector for s in generate_deterministic_signals(ICON)]
    assert 'span[title="Delete"]' in sels


def test_widget_wiring_is_not_identity():
    assert not is_identity_attr_key("data-toggle") and not is_identity_attr_key("data-bs-placement")
    assert is_identity_attr_key("data-key")


def test_restoring_a_value_also_fixes_a_completion_check_that_names_it():
    step = {"value": "{{department_5}}", "input_binding": "department_5",
            "validation": {"assertions": [], "effect": {"checks": [{"probe": {"key": "{{department_5}}"}}]}}}
    patch = _restore_literal_patch(step, "department_5", "QA")
    assert patch["value"] == "QA" and patch["validation"]["effect"]["checks"][0]["probe"]["key"] == "QA"


def test_a_seen_on_screen_check_for_a_value_fixed_later_checks_the_recorded_words():
    from conxa_compile.conversation import clarify
    q = {"kind": "done_when", "choices": [], "recorded": {"search_term_2": "Conxa"}}
    step = {"action": {"action": "type"}, "value": "Conxa", "identity_bundle": {"stable_hash": "s"},
            "target": {"primary_selector": "#q"}, "validation": {"assertions": []}}
    doc = {"skills": [{"steps": [step]}], "inputs": []}
    ops, _ = clarify.plan_answer(q, {"selected": ["input:search_term_2"]}, doc)
    targets = [a["target"] for o in ops if o["op"] == "patch" for a in o["patch"]["validation"]["assertions"]]
    assert targets == ["Conxa"]


def test_waiting_for_the_clicked_element_itself_never_becomes_a_check():
    from conxa_compile.compiler.build import _own_selectors
    own = _own_selectors(ICON, "span[title=\"Delete\"]")
    assert ICON["selectors"]["xpath"] in own and 'span[title="Delete"]' in own
