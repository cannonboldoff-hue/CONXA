"""Typing the recorder sent only as the page unloaded goes back before the press that unloaded it (WF-4, 2026-11-17).

Leg C: type "tshirt", click Search; the same text arrived again after the click, as the page left. Leg D: typed
"Automation", pressed Enter; the typing arrived after the Enter, so a replay pressed Enter on "Au"."""

from __future__ import annotations

from conxa_compile.compiler.step_anchors import clean_steps

_OLD, _NEW = "https://shop.test/products", "https://shop.test/products?search=tshirt"


def _ev(action: str, ref: str, *, value: str | None = None, tag: str = "input", tab_url: str = _OLD,
        bridge: str = "b1") -> dict:
    return {"action": {"action": action, "value": value}, "target": {"tag": tag, "name": ref},
            "selectors": {"css": f"#{ref}"}, "page": {"url": _OLD}, "tab": {"id": "tab_0", "url": tab_url},
            "extras": {"bridge": bridge, "element_ref": f"{bridge}:{ref}"}}


def _shape(out: list[dict]) -> list[tuple]:
    return [(e["action"]["action"], e["target"]["name"], e["action"].get("value")) for e in out]


def test_the_search_typed_again_as_the_page_left_is_one_typing_before_the_click():
    out = clean_steps([
        _ev("type", "q", value="tshirt"),
        _ev("click", "go", tag="button", tab_url=_NEW),
        _ev("type", "q", value="tshirt", tab_url=_NEW),
        {**_ev("scroll", "page", tag="html", bridge="b2"), "page": {"url": _NEW}},
    ])
    assert _shape(out)[:2] == [("type", "q", "tshirt"), ("click", "go", None)]
    assert [a for a, *_ in _shape(out)].count("type") == 1


def test_typing_that_arrived_after_its_enter_goes_back_before_it():
    out = clean_steps([
        _ev("type", "q", value="Au"),
        _ev("keyboard_shortcut", "q", value='{"key":"Enter"}', tab_url=_NEW),
        _ev("type", "q", value="Automation", tab_url=_NEW),
    ])
    assert _shape(out) == [("type", "q", "Automation"), ("keyboard_shortcut", "q", '{"key":"Enter"}')]


def test_typing_sent_while_the_page_stayed_keeps_its_place():
    # Same text again after a click, but the page never left: a real second entry (a box the page cleared).
    out = clean_steps([
        _ev("type", "q", value="milk"),
        _ev("click", "add", tag="button"),
        _ev("type", "q", value="milk"),
    ])
    assert _shape(out) == [("type", "q", "milk"), ("click", "add", None), ("type", "q", "milk")]


def test_typing_in_a_frame_keeps_its_place():
    # A frame's page address is its own, never the tab's, so it says nothing about the page leaving.
    framed = [{**e, "frame": {"chain": [{"url": "https://shop.test/demo.html"}]}} for e in (
        _ev("click", "go", tag="button"), _ev("type", "q", value="Ja"))]
    assert _shape(clean_steps(framed)) == [("click", "go", None), ("type", "q", "Ja")]
