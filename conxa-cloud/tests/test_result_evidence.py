"""W4: a "how we know it worked" option comes from words the page showed after the action, on the same page. A control's
label is offered but never pre-ticked, and a step that left the page derives nothing from the page it left."""

from __future__ import annotations

from conxa_compile.compiler.state_validation import merge_dom_diff_evidence
from conxa_compile.conversation.clarify import _done_when_choices


def _ev(**state_change):
    return {"state_change": state_change}


def test_words_the_page_showed_come_first_and_control_labels_are_marked():
    ev = _ev(dom_diff={"added": ["button||Close|Close", "a||View receipt|View receipt"]},
             result_diff={"added": ["Thank you for your order!"], "removed": []})
    merged = merge_dom_diff_evidence(ev, {"text_change": [], "new_elements": [], "removed_elements": []})
    assert merged["text_change"][0] == "Thank you for your order!"
    assert merged["control_text"] == ["Close", "View receipt"]


def test_a_step_that_left_the_page_takes_nothing_from_the_page_it_left():
    ev = _ev(dom_diff={"added": ["a||Ada Lovelace|Ada Lovelace"]}, result_diff={"added": ["Suggestions"]})
    ev["post_condition"] = {"url_delta": {"before": "https://w.test/search", "after": "https://w.test/wiki/Ada"}}
    state = {"text_change": [], "new_elements": [], "removed_elements": []}
    assert merge_dom_diff_evidence(ev, state) == state


def test_a_control_label_check_is_offered_unticked_and_a_result_is_ticked():
    def step(*checks):
        return {"action": {"action": "click"}, "intent": "x", "validation": {"assertions": list(checks)},
                "identity_bundle": {"fingerprint": {}}}

    doc = {"skills": [{"steps": [step(
        {"type": "text_present", "target": "Thank you", "required": False, "source": "result"},
        {"type": "text_present", "target": "Close", "required": False, "source": "control"})]}], "inputs": []}
    choices, _ = _done_when_choices(doc)
    ticked = {c["label"]: c["selected"] for c in choices}
    assert ticked["You see the words “Thank you” on the screen"] is True
    assert ticked["You see the words “Close” on the screen"] is False
