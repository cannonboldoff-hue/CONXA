"""A click's section heading and shadow hosts reach the compiled identity (WF-2 Segment J, 2026-11-16).

Two identical "Toggle Me" widgets on Shoelace's Details page differ only in the section they sit in. The recorder now
keeps that heading and the shadow-host path (which it computed but dropped); compile puts the heading in the
fingerprint, never an email address."""

from __future__ import annotations

from conxa_compile.compiler.build import _build_element_fingerprint, _section_heading
from tests.test_phases import _minimal_click_event


def test_the_section_heading_reaches_the_fingerprint():
    ev = _minimal_click_event()
    ev["extras"] = {"section_heading": "Customizing the Summary Icon", "shadow_path": [{"host": "sl-details", "mode": "open"}]}
    assert _build_element_fingerprint(ev).section_heading == "Customizing the Summary Icon"


def test_an_email_in_a_heading_is_never_kept():
    assert _section_heading({"extras": {"section_heading": "Inbox for asha.rao@acme.io"}}) == ""
    assert _section_heading({}) == ""
