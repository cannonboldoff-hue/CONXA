"""Each recorded scroll stays its own step (WF-2 Segment G, the-internet infinite scroll, 2026-11-16).

Three wheel scrolls (to 114, 514 and 914 px) became one 114 px scroll: cleanup treated consecutive scrolls on the page
as duplicates. A list that loads more as you scroll then never loaded what later steps need."""

from __future__ import annotations

from conxa_compile.compiler.step_anchors import clean_steps


def _scroll(seq: int, amount: int) -> dict:
    return {"action": {"action": "scroll"}, "target": {"tag": "html", "inner_text": "page"},
            "selectors": {}, "extras": {"sequence": seq, "scroll_amount": amount}}


def test_consecutive_scrolls_each_keep_their_distance():
    out = clean_steps([_scroll(1, 114), _scroll(2, 400), _scroll(3, 400)])
    assert [e["extras"]["scroll_amount"] for e in out] == [114, 400, 400]
