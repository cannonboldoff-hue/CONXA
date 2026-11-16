"""A frame identified only by a relative src stays in the chain, and a recording that starts inside a frame opens the
top page, not the frame's own document (WF-2 Segment D, jqueryui.com demos, 2026-11-16)."""

from __future__ import annotations

from unittest.mock import patch

from conxa_core.config import settings
from conxa_compile.recorder.frame_utils import _iframe_fingerprint_from_attrs
from tests.test_redirect_navigation import _event
from tests.test_date_format import _compile_steps

FRAME = "https://jqueryui.com/resources/demos/tabs/default.html"
TOP = "https://jqueryui.com/tabs/"


def test_a_relative_src_is_still_a_frame_signal():
    fp = _iframe_fingerprint_from_attrs({"src": "/resources/demos/tabs/default.html"}, FRAME)
    assert [s["selector"] for s in fp["signals"]] == ['iframe[src="/resources/demos/tabs/default.html"]']


@patch.object(settings, "compile_keep_every_action", False)
def test_a_recording_that_starts_inside_a_frame_opens_the_top_page():
    click = _event("click", "2026-01-01T00:00:00Z", FRAME, "Aenean lacinia", "ui-id-3")
    click["tab"] = {"id": "tab_0", "url": TOP}
    click["frame"] = {"chain": [{"url": FRAME, "url_pattern": "", "fingerprint": _iframe_fingerprint_from_attrs(
        {"src": "/resources/demos/tabs/default.html"}, FRAME)}]}
    steps = _compile_steps([click], "sess-frame").skills[0].steps
    assert steps[0].action == "navigate" and steps[0].url == TOP
    assert len(steps[1].identity_bundle.frame_chain) == 1
