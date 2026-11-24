"""E2E16-14 compiler side.

BUILD-110 (1): a step that sends a form names the boxes typed on its page since the last send, so the runtime checks
they still hold what was typed just before sending. BUILD-110 (2), owner rule 2026-11-21: only a recorded paste of a
whole box copied from another box makes the second box "the same value as the first box"; a secret box never."""

from __future__ import annotations

from conxa_compile.compiler.build import link_pasted_values, mark_typed_rechecks
from conxa_core.models.skill_spec import Assertion, Effect, ElementFingerprint, IdentityBundle, SkillStep, ValidationBlock

URL = "https://app.test/new"


def _typed(sid: str, binding: str | None, *, url: str = URL, role: str = "", seq: int = 0, value: str | None = None) -> SkillStep:
    return SkillStep(step_id=sid, action={"action": "type"}, intent="type", url=url,
                     value=value if value is not None else ("{{" + binding + "}}" if binding else "x"), input_binding=binding,
                     identity_bundle=IdentityBundle(fingerprint=ElementFingerprint(role=role)), source_event=seq,
                     validation=ValidationBlock(assertions=[Assertion(type="value_equals", target="#b",
                                                                      expected="{{" + binding + "}}" if binding else "x")]))


def _send(sid: str, kind: str = "send", *, url: str = URL) -> SkillStep:
    return SkillStep(step_id=sid, action={"action": "click"}, intent="send", url=url,
                     validation=ValidationBlock(effect=Effect(kind=kind)))


def _click(sid: str, role: str = "button") -> SkillStep:
    return SkillStep(step_id=sid, action={"action": "click"}, intent="click", url=URL,
                     identity_bundle=IdentityBundle(fingerprint=ElementFingerprint(role=role)))


# ─── BUILD-110 (1) ───────────────────────────────────────────────────────────────────────────

def test_a_send_lists_the_boxes_typed_on_its_page_since_the_last_send():
    steps = [_typed("a", "name"), _typed("b", "email"), _send("s1"), _typed("c", "note"), _send("s2", "save")]
    mark_typed_rechecks(steps)
    assert steps[2].handler_hints.recheck_typed == ["a", "b"]
    assert steps[4].handler_hints.recheck_typed == ["c"], "a box sent by the first send is not checked again"
    assert all(not s.handler_hints.recheck_typed for s in steps[:2] + [steps[3]])


def test_enter_straight_after_typing_is_a_send_and_a_plain_click_is_not():
    enter = SkillStep(step_id="e", action={"action": "keyboard_shortcut"}, intent="press", url=URL, value="Enter")
    steps = [_typed("a", "q"), enter, _typed("b", "x"), _click("next")]
    mark_typed_rechecks(steps)
    assert steps[1].handler_hints.recheck_typed == ["a"]
    assert steps[3].handler_hints.recheck_typed == [], "a click with nothing to prove sends nothing"


def test_a_file_pick_is_not_a_send_and_another_page_s_boxes_are_not_listed():
    steps = [_typed("old", "a", url="https://app.test/other"), _typed("a", "b"), _send("pick", "choose_files"), _send("s")]
    mark_typed_rechecks(steps)
    assert steps[2].handler_hints.recheck_typed == []
    assert steps[3].handler_hints.recheck_typed == ["a"]


def test_a_box_filled_from_a_suggestion_list_is_left_out():
    steps = [_typed("who", "person", role="combobox"), _typed("a", "name"), _click("pick", role="option"), _typed("b", "note"), _send("s")]
    mark_typed_rechecks(steps)
    assert steps[4].handler_hints.recheck_typed == ["b"], "a combobox, and a box before an option pick, are not checked"


def _on(step: SkillStep, before: str, after: str | None = None) -> SkillStep:
    """A compiled step as the compiler writes it: no url of its own, the recorded page in ``state``."""
    step.url = ""
    step.tab = {"id": "tab_0"}
    step.state = {"before": {"url": before}, "after": {"url": after or before}}
    return step


def test_build_117_only_the_send_s_own_page_is_listed_and_an_irreversible_send_gets_its_list():
    # site/live.html: two boxes, "Send order" (judged can't-be-undone after effects were built: no effect), then a
    # Comment box on the next page sent with Enter.
    p1, p2, p3 = "http://h/live.html", "http://h/live.html?stage=2", "http://h/live.html?stage=3"
    send = _on(SkillStep(step_id="send", action={"action": "click"}, intent="send_order", consequence="irreversible"), p1, p2)
    enter = _on(SkillStep(step_id="enter", action={"action": "keyboard_shortcut"}, intent="press", value="Enter"), p2, p3)
    steps = [_on(_typed("name", "customer_name"), p1), _on(_typed("label", "customer_name"), p1), send,
             _on(_typed("comment", "comment"), p2), enter]
    mark_typed_rechecks(steps)
    assert send.handler_hints.recheck_typed == ["name", "label"]
    assert enter.handler_hints.recheck_typed == ["comment"], "a box typed on the page before is never looked for again"


def test_build_117_the_walk_back_stops_at_a_step_that_left_its_page():
    # No send in between, but the first box's page was left (a link) and came back to the same address.
    p, other = "http://h/form", "http://h/elsewhere"
    steps = [_on(_typed("old", "a"), p), _on(_click("away"), p, other), _on(_click("back"), other, p),
             _on(_typed("new", "b"), p), _on(_send("s"), p)]
    mark_typed_rechecks(steps)
    assert steps[4].handler_hints.recheck_typed == ["new"]


def test_build_117_a_plain_click_that_leaves_its_page_sends_what_was_typed_and_one_that_stays_does_not():
    # site/results.html: a search box, then a reversible "Search" click with no effect that opens the results page.
    p, results = "http://h/results.html", "http://h/results.html?s=lamp"
    search = _on(_click("search"), p, results)
    stays = _on(_click("filter"), p)
    steps = [_on(_typed("q", "term"), p), stays, search]
    mark_typed_rechecks(steps)
    assert stays.handler_hints.recheck_typed == [], "a click that stayed on its page sends nothing"
    assert search.handler_hints.recheck_typed == ["q"]


# ─── BUILD-110 (2) ───────────────────────────────────────────────────────────────────────────

def _ev(seq: int, value: str, ref: str, pasted_from: str = "", input_type: str = "text") -> dict:
    extras = {"sequence": seq, "element_ref": ref, **({"pasted_from": pasted_from} if pasted_from else {})}
    return {"action": {"action": "type", "value": value}, "extras": extras, "semantic": {"input_type": input_type}}


def test_a_pasted_box_takes_the_first_box_s_input():
    steps = [_typed("a", "email", seq=1), _typed("b", "email_again", seq=2)]
    events = [_ev(1, "ada@x.io", "r1"), _ev(2, "ada@x.io", "r2", pasted_from="r1")]
    link_pasted_values(steps, events)
    assert steps[1].value == "{{email}}"
    assert steps[1].input_binding is None, "not an input of its own: it is the first box's"
    assert steps[1].validation.assertions[0].expected == "{{email}}", "its check follows the value"
    assert steps[0].value == "{{email}}" and steps[0].input_binding == "email"


def test_without_a_recorded_paste_nothing_changes():
    steps = [_typed("a", "email", seq=1), _typed("b", "email_again", seq=2)]
    link_pasted_values(steps, [_ev(1, "ada@x.io", "r1"), _ev(2, "ada@x.io", "r2")])
    assert (steps[1].value, steps[1].input_binding) == ("{{email_again}}", "email_again")


def test_a_paste_that_is_not_what_the_first_box_was_given_is_not_linked():
    steps = [_typed("a", "email", seq=1), _typed("b", "other", seq=2)]
    link_pasted_values(steps, [_ev(1, "ada@x.io", "r1"), _ev(2, "bob@x.io", "r2", pasted_from="r1")])
    assert steps[1].input_binding == "other"


def test_a_secret_box_is_never_linked():
    steps = [_typed("a", "password", seq=1), _typed("b", "password_again", seq=2)]
    events = [_ev(1, "{{REDACTED}}", "r1", input_type="password"), _ev(2, "{{REDACTED}}", "r2", pasted_from="r1", input_type="password")]
    link_pasted_values(steps, events)
    assert steps[1].input_binding == "password_again"


def test_a_first_box_with_a_fixed_value_leaves_the_second_as_recorded():
    steps = [_typed("a", None, seq=1, value="ACME"), _typed("b", "company", seq=2)]
    link_pasted_values(steps, [_ev(1, "ACME", "r1"), _ev(2, "ACME", "r2", pasted_from="r1")])
    assert steps[1].input_binding == "company"


# ─── BUILD-117 (7): empty typing ─────────────────────────────────────────────────────────────

def _typing(seq: int, value: str, ref: str, *, cleared: bool = False, url: str = URL) -> dict:
    extras = {"sequence": seq, "element_ref": ref, **({"cleared": True} if cleared else {})}
    return {"action": {"action": "type", "value": value}, "target": {"tag": "input", "aria_label": "Search"},
            "selectors": {"css": "#q"}, "page": {"url": url}, "extras": extras}


def test_build_117_empty_typing_is_a_step_only_when_the_box_held_text_before():
    from conxa_compile.compiler.build import prepare_compile_events
    from conxa_compile.policy.bundle import get_policy_bundle

    def enter(seq: int) -> dict:
        return {"action": {"action": "keyboard_shortcut", "value": "Enter"}, "target": {"tag": "input"},
                "selectors": {"css": "#q"}, "page": {"url": URL}, "extras": {"sequence": seq, "element_ref": "p1:1"}}

    events = [_typing(1, "", "p1:1"),                       # first seen empty: never typed in
              _typing(2, "desk lamp", "p1:1"), enter(3),
              _typing(4, "", "p2:1", url=URL + "?s=desk+lamp"),  # same selector, next page, never typed in
              _typing(5, "lamp", "p2:1", url=URL + "?s=desk+lamp"),
              _typing(6, "", "p2:1", url=URL + "?s=desk+lamp"),  # emptied after typing: the person cleared it
              _typing(7, "", "p2:2", cleared=True, url=URL + "?s=desk+lamp")]  # recorder saw a filled box emptied
    ledger: list[dict] = []
    kept = prepare_compile_events(events, get_policy_bundle().data, ledger)
    typed = [(e["extras"]["sequence"], e["action"]["value"]) for e in kept if e["action"]["action"] == "type"]
    assert (1, "") not in typed and (4, "") not in typed
    assert (6, "") in typed and (7, "") in typed
    assert {(d["sequence"], d["pass"]) for d in ledger if d["pass"] == "empty_typing"} == {(1, "empty_typing"), (4, "empty_typing")}
