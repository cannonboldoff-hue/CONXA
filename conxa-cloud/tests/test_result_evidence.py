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


def test_a_button_or_link_that_appeared_is_never_an_it_is_there_check():
    """W-E (owner decision 2026-11-21): an "Edit" button appears after Save whether or not the save worked. Its
    data-testid is never a selector check that could prove the click; its label is still offered, unticked. A panel
    with a data-testid that appeared is a result and stays."""
    ev = _ev(dom_diff={"added": ["button|edit-btn||Edit", "a|view-link||View", "div|save-banner||Draft saved"]})
    merged = merge_dom_diff_evidence(ev, {"text_change": [], "new_elements": [], "removed_elements": []})
    assert merged["new_elements"] == ['[data-testid="save-banner"]']
    assert {"Edit", "View"} <= set(merged["control_text"])


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


# ── E2E16-5: which page a step's evidence is from ─────────────────────────────────────────────────────────────────────
from conxa_compile.compiler.state_validation import document_after, mark_document_changes  # noqa: E402
from conxa_compile.compiler.step_key import step_keys  # noqa: E402
from conxa_compile.conversation.clarify import ALREADY_SHOWN, step_where_words_appear  # noqa: E402

_EMPTY = {"text_change": [], "new_elements": [], "removed_elements": []}
_FINISH = {"before": "https://shop.test/checkout-step-two", "after": "https://shop.test/checkout-complete"}


def _rec(seq, action, doc="", *, tab="tab_0", frame=False, **kw):
    e = {"action": {"action": action}, "doc_id": doc, "tab": tab if isinstance(tab, dict) else {"id": tab}, "frame": {"chain": [{"url": "f"}] if frame else []},
         "extras": {"sequence": seq}, "page": {"url": "https://shop.test/a"}, "snapshot": {"dom_hash": ""}}
    e.update(kw)
    return e


def _nav(seq, cause):
    e = _rec(seq, "manual_navigate")
    e["action"]["value"] = '{"cause": "%s"}' % cause
    return e


def test_the_next_step_on_the_same_tab_tells_whether_the_document_stayed():
    events = [_rec(1, "click", "A"), _nav(2, "action"), _rec(3, "click", "Z", tab="tab_1"), _rec(4, "click", "B", frame=True),
              _rec(5, "click", "A"), _rec(6, "click", "A"), _rec(7, "click", "C"), _rec(8, "click", "C"), _nav(9, "user"),
              _rec(10, "click", "C"), _rec(11, "click", "")]
    # Its own navigation, another tab and a frame are skipped; the person's own navigation ends the search.
    assert [document_after(events, i) for i in (0, 4, 5, 7, 9, 10)] == ["same", "same", "left", "", "", ""]
    marked = mark_document_changes([dict(e) for e in events])
    assert marked[5]["post_condition"] == {"document": "left"} and "post_condition" not in events[5]


def test_a_route_change_inside_one_page_keeps_its_results():
    """E2E16-1 hand-on: saucedemo's Finish changes the address without leaving the page, so its words are real results."""
    ev = _ev(result_diff={"added": ["Thank you for your order!"]}, dom_diff={"added": ["button|||Generate PDF order"]})
    ev["post_condition"] = {"url_delta": _FINISH, "document": "same"}
    merged = merge_dom_diff_evidence(ev, dict(_EMPTY))
    assert merged["text_change"] == ["Thank you for your order!", "Generate PDF order"]
    assert merged["control_text"] == ["Generate PDF order"]


def test_a_page_left_gives_only_the_words_its_next_page_first_showed():
    ev = _ev(result_diff={"added": ["Thank you for your order!"], "next_page": True},
             dom_diff={"added": ["a|sugg|Ada Lovelace|Ada Lovelace"]})
    ev["post_condition"] = {"url_delta": _FINISH}
    merged = merge_dom_diff_evidence(ev, dict(_EMPTY))
    assert merged["text_change"] == ["Thank you for your order!"] and merged["new_elements"] == []
    assert merged["control_text"] == []


def test_a_document_left_at_the_same_address_takes_nothing_from_it():
    """BUILD-101 (1) residue: a form sent back to its own address is another document; the diff is the old one's."""
    ev = _ev(dom_diff={"added": ["a||Ada Lovelace|Ada Lovelace"]}, result_diff={"added": ["Suggestions"]})
    ev["post_condition"] = {"document": "left"}
    assert merge_dom_diff_evidence(ev, dict(_EMPTY)) == _EMPTY


def _where(monkeypatch, events, pages, steps):
    import conxa_core.storage.session_events as se
    import conxa_core.storage.snapshots as sn

    reads: list[str] = []
    monkeypatch.setattr(se, "read_session_events", lambda _s: events)
    monkeypatch.setattr(sn, "read_dom_snapshot", lambda _s, h: (reads.append(h), pages.get(h))[1])
    doc = {"meta": {"version": 3, "source_session_id": "s1"}, "skills": [{"steps": steps}], "inputs": []}
    return doc, reads


def _step(action, seq):
    return {"action": {"action": action}, "intent": f"s{seq}", "validation": {"assertions": []}, "source_event": seq}


def test_words_only_the_last_step_recorded_are_placed_on_it(monkeypatch):
    """BUILD-111 (9): a practice form recorded up to Submit; no saved copy holds the thank-you box, Submit's result does."""
    events = [_rec(1, "type", "A", snapshot={"dom_hash": "form"}),
              _rec(2, "click", "A", snapshot={"dom_hash": "form"},
                   state_change={"result_diff": {"added": ["Thanks for submitting the form", "Sana Khan"]}})]
    steps = [_step("type", 1), _step("click", 2)]
    doc, _ = _where(monkeypatch, events, {"form": "<label>First Name</label><input value='Sana'>"}, steps)
    assert step_where_words_appear(doc, "Sana") == step_keys(steps)[1]
    assert step_where_words_appear(doc, "Nobody") == ""


def test_recorded_words_of_a_page_the_step_left_place_nothing(monkeypatch):
    events = [_rec(1, "click", "A", post_condition={"url_delta": _FINISH},
                   state_change={"result_diff": {"added": ["Suggestions"]}}), _rec(2, "click", "B")]
    doc, _ = _where(monkeypatch, events, {}, [_step("click", 1), _step("click", 2)])
    assert step_where_words_appear(doc, "Suggestions") is None


def test_a_dropdowns_choices_are_not_words_on_the_screen(monkeypatch):
    """BUILD-99 (5): ParaBank's account type list made "CHECKING" look shown on the form, so it was offered there."""
    events = [_rec(1, "click", "A", snapshot={"dom_hash": "form"}), _rec(2, "click", "A", snapshot={"dom_hash": "details"})]
    pages = {"form": "<select><option>CHECKING</option><option>SAVINGS</option></select>",
             "details": "<td>Account Type:</td><td>CHECKING</td>"}
    steps = [_step("click", 1), _step("click", 2)]
    doc, _ = _where(monkeypatch, events, pages, steps)
    assert step_where_words_appear(doc, "CHECKING") == step_keys(steps)[1]


def test_words_already_on_the_page_around_a_frame_are_not_a_check(monkeypatch):
    """BUILD-110 (8) / WF-8 8.13: a press inside a frame is described by the frame's text; the heading outside it was
    already on the screen. The frame's own address is not a new page either."""
    events = [_rec(1, "type", "A", snapshot={"dom_hash": "demo"}),
              _rec(2, "click", "F", frame=True, snapshot={"dom_hash": "demo"}, page={"url": "https://g.test/anchor"},
                   tab={"id": "tab_0", "url": "https://shop.test/a"})]
    doc, _ = _where(monkeypatch, events, {"demo": "<h1>reCAPTCHA demo</h1>"}, [_step("type", 1), _step("click", 2)])
    assert step_where_words_appear(doc, "reCAPTCHA demo") == ALREADY_SHOWN


def test_the_pages_are_read_once_for_every_input(monkeypatch):
    """BUILD-111 (7): the done-when question asks once per input; one memo reads the recording and each page once."""
    events = [_rec(1, "click", "A", snapshot={"dom_hash": "p1"}), _rec(2, "click", "A", snapshot={"dom_hash": "p2"})]
    doc, reads = _where(monkeypatch, events, {"p1": "<p>start</p>", "p2": "<p>Asha Verma asha@x.test</p>"},
                        [_step("click", 1), _step("click", 2)])
    memo: dict = {}
    for words in ("Asha", "asha@x.test", "Verma"):
        step_where_words_appear(doc, words, memo)
    assert sorted(reads) == ["p1", "p2"]
