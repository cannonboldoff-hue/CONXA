"""Tests for accessible-name derivation on elements that have no text of their own.

The-internet.herokuapp.com/hovers regression: a bare <img> carries no aria-label, no
name attribute and no inner text, so the compiler fell through to `label_text` — which
captureAssociatedLabel() fills from the nearest surrounding text. The avatar was named
after the page's paragraph, producing
`internal:role=img[name="Hover over the image for additional information"]`, a name
Playwright never computes. Both the 0.95 role signal and the 0.75 relational signal that
reuses it matched zero elements at replay, and the step failed as "element not found".

Covers:
- An <img> is named from its alt, not from surrounding text.
- label_text names a form control (that IS its accessible name) and nothing else.
- A role selector that matches nothing in the recorded a11y snapshot is dropped rather
  than shipped, taking its relational twin with it.
"""
from __future__ import annotations

import pytest

from conxa_compile.compiler.identity_bundle import _accessible_name, generate_deterministic_signals
from conxa_compile.compiler.selector_filters import resolves_to_nothing

HOVERS_DOM = """
<html><body><div id="content"><div class="example">
  <h3>Hovers</h3>
  <p>Hover over the image for additional information</p>
  <div class="figure"><img src="/img/avatar-blank.jpg" alt="User Avatar">
    <div class="figcaption"><h5>name: user1</h5><a href="/users/1">View profile</a></div>
  </div>
</div></div></body></html>
"""

# What Playwright's accessibility snapshot actually reports for that image — the paragraph
# is its own node, and is never the image's name. Recorder shape: {"aria_snapshot": "<yaml>"}
# (Locator.aria_snapshot()), one `- role "name":` line per node.
HOVERS_A11Y = {
    "aria_snapshot": (
        '- WebArea "The Internet":\n'
        '  - heading "Hovers"\n'
        '  - text "Hover over the image for additional information"\n'
        '  - img "User Avatar"\n'
        '  - link "View profile"'
    ),
}


def _img_event(alt: str | None = "User Avatar") -> dict:
    return {
        "target": {
            "tag": "img",
            "role": "img",
            "inner_text": "",
            "aria_label": None,
            "name": None,
            "placeholder": None,
            "alt": alt,
            "title": None,
            # What captureAssociatedLabel's last-resort "nearest surrounding text" walk finds.
            "label_text": "Hover over the image for additional information",
        },
        "semantic": {"role": "img"},
        "selectors": {
            "css": "div.figure > img",
            "xpath": "/html[1]/body[1]/div[1]/div[1]/div[3]/img[1]",
            "text_based": "",
        },
        "anchors": [{"element": "Hovers"}],
    }


# ---------------------------------------------------------------------------
# _accessible_name
# ---------------------------------------------------------------------------

def test_image_is_named_from_alt_not_surrounding_text():
    assert _accessible_name(_img_event()["target"]) == "User Avatar"


def test_nameless_image_gets_no_name_rather_than_a_borrowed_one():
    """No name is a valid answer — it falls through to structural identity, which is
    strictly better than a name that resolves to nothing."""
    assert _accessible_name(_img_event(alt=None)["target"]) == ""


def test_label_text_still_names_a_form_control():
    target = {
        "tag": "input",
        "role": "textbox",
        "inner_text": "",
        "label_text": "First Name",
    }
    assert _accessible_name(target) == "First Name"


def test_label_text_never_names_a_link():
    target = {"tag": "a", "role": "link", "inner_text": "", "label_text": "Project"}
    assert _accessible_name(target) == ""


def test_name_attribute_never_used_as_accessible_name():
    """The `name` HTML attribute is the radio/checkbox GROUP key, not any one option's
    accessible name -- `role=radio[name="gender"]` matches every sibling in the group (or
    nothing, since Playwright's role selector matches the accessible name "Male", not the
    group's shared `name`). Falls through to label_text, the option's own real accessible
    name for a form control."""
    target = {"tag": "input", "role": "radio", "inner_text": "", "name": "gender", "label_text": "Male"}
    assert _accessible_name(target) == "Male"


def test_name_attribute_alone_yields_no_name_rather_than_the_group_key():
    target = {"tag": "input", "role": "radio", "inner_text": "", "name": "gender"}
    assert _accessible_name(target) == ""


# ---------------------------------------------------------------------------
# generate_deterministic_signals
# ---------------------------------------------------------------------------

def test_image_role_signal_uses_alt():
    signals = generate_deterministic_signals(_img_event(), HOVERS_DOM, HOVERS_A11Y)
    role = [s for s in signals if s.engine == "role"]
    assert role, "an <img> with an alt should still get its high-durability role signal"
    assert role[0].selector == 'internal:role=img[name="User Avatar"]'
    assert not any("Hover over the image" in s.selector for s in signals)


def test_fabricated_role_name_is_dropped_with_its_relational_twin():
    """A role+name selector derived from the element's own attributes that matches nothing
    in the very snapshot it came from is fabricated — ship structure instead."""
    ev = _img_event(alt=None)
    ev["target"]["aria_label"] = "Hover over the image for additional information"
    signals = generate_deterministic_signals(ev, HOVERS_DOM, HOVERS_A11Y)
    engines = {s.engine for s in signals}
    assert "role" not in engines
    assert "relational" not in engines
    assert engines, "structural signals must survive so the step is still resolvable"


def test_real_role_name_survives_the_drop():
    signals = generate_deterministic_signals(_img_event(), HOVERS_DOM, HOVERS_A11Y)
    assert any(s.engine == "role" for s in signals)


def test_no_a11y_snapshot_never_drops_a_role_signal():
    """Without an a11y tree a role selector is unverifiable — allow it through rather than
    silently stripping every role signal from packs compiled without a snapshot."""
    ev = _img_event(alt=None)
    ev["target"]["aria_label"] = "Hover over the image for additional information"
    signals = generate_deterministic_signals(ev, HOVERS_DOM, None)
    assert any(s.engine == "role" for s in signals)


# ---------------------------------------------------------------------------
# demoqa.com/automation-practice-form regression: react-datepicker's year <select> has no
# id/name/aria-label/<label>. `page.accessibility.snapshot()` was removed from Playwright
# (this repo pins 1.58.0), and the session that hit this recorded no a11y evidence at all
# (a bare-except swallowed the AttributeError) — so `resolves_to_nothing` could never verify
# anything, and `_accessible_name`'s ungated `inner_text` candidate shipped the concatenated
# <option> list ("1900 1901 1902 ... 1915") as the element's "name": a
# role=combobox[name="1900 1901 ..."] selector Playwright never computes, reported at replay
# as "Element not found (resolve miss)".
# ---------------------------------------------------------------------------

YEAR_SELECT_OPTIONS = " ".join(str(y) for y in range(1900, 1917))  # the recorded option[0..16] text


def _year_select_target(inner_text: str = YEAR_SELECT_OPTIONS) -> dict:
    return {
        "tag": "select",
        "role": "combobox",
        "inner_text": inner_text,
        "aria_label": None,
        "alt": None,
        "title": None,
        "placeholder": None,
        "name": None,
        "label_text": None,
    }


def test_nameless_select_is_not_named_from_its_concatenated_option_list():
    assert _accessible_name(_year_select_target()) == ""


def test_button_is_still_named_from_its_own_inner_text():
    """Control: the inner_text gate is role-specific, not a blanket removal — a button
    genuinely IS named from its own visible text per ARIA "name from content"."""
    target = {"tag": "button", "role": "button", "inner_text": "Submit", "aria_label": None,
              "alt": None, "title": None, "placeholder": None, "label_text": None}
    assert _accessible_name(target) == "Submit"


def test_overlong_inner_text_is_dropped_not_truncated_even_for_a_name_from_content_role():
    """A name this long (81+ chars) reads as fabricated content rather than a real accessible
    name — dropped outright, never shipped as a truncated (and therefore unmatchable) selector."""
    target = {"tag": "button", "role": "button", "inner_text": "x" * 81, "aria_label": None,
              "alt": None, "title": None, "placeholder": None, "label_text": None}
    assert _accessible_name(target) == ""


def test_nameless_select_gets_no_role_or_relational_signal():
    """The fabricated name is never even a candidate — no role/relational signal exists to
    verify or drop, unlike the <img> case above which needed the resolves_to_nothing gate."""
    ev = {
        "target": _year_select_target(),
        "semantic": {"role": "combobox"},
        "selectors": {"css": "select.react-datepicker__year-select", "xpath": "", "text_based": ""},
        "anchors": [],
    }
    signals = generate_deterministic_signals(ev, "<html><body><select></select></body></html>", None)
    engines = {s.engine for s in signals}
    assert "role" not in engines
    assert "relational" not in engines
    assert any(s.engine.startswith("css") for s in signals), "must still fall through to structural identity"


def _accessible_name_cases():
    import json
    from pathlib import Path

    # One fixture, two languages: runtime/test/unit/test_shortcut_tail.js runs the same file.
    path = Path(__file__).resolve().parents[2] / "runtime" / "test" / "fixtures" / "accessible_name_cases.json"
    return json.loads(path.read_text("utf-8"))


@pytest.mark.parametrize("case", _accessible_name_cases(), ids=lambda c: c["name"])
def test_shortcut_tail_matches_shared_fixture(case):
    from conxa_compile.compiler.identity_bundle import strip_keyboard_shortcut_tail

    assert strip_keyboard_shortcut_tail(case["input"]) == case["expect"]


def test_keyboard_shortcut_tail_stripped_from_accessible_name():
    from conxa_compile.compiler.identity_bundle import strip_keyboard_shortcut_tail

    assert strip_keyboard_shortcut_tail("File upload Alt+C then U") == "File upload"
    assert strip_keyboard_shortcut_tail("Save") == "Save"
    assert strip_keyboard_shortcut_tail("Cut Ctrl+X") == "Cut"
    target = {"tag": "li", "role": "menuitem", "inner_text": "File upload Alt+C then U"}
    assert _accessible_name(target) == "File upload"


DRIVE_MENU_DOM = "<html><body><ul role='menu'><li>File upload</li><li>Folder upload</li></ul></body></html>"
DRIVE_MENU_A11Y = {
    "aria_snapshot": (
        '- WebArea "Drive":\n'
        '  - menuitem "File upload"\n'
        '  - menuitem "Folder upload"'
    ),
}


def test_drive_file_upload_keeps_role_signal_when_snapshot_omits_shortcut():
    """Recorded inner text includes Drive's accelerator; Playwright's snapshot does not.
    Exact name matching dropped the role signal; replay then missed on exact text too."""
    ev = {
        "target": {"tag": "li", "role": "menuitem", "inner_text": "File upload Alt+C then U"},
        "semantic": {"role": "menuitem"},
        "selectors": {
            "css": "ul[role=menu] > li:nth-of-type(1)",
            "text_based": 'text="File upload Alt+C then U"',
            "xpath": "/html/body/ul/li[1]",
        },
        "anchors": [],
    }
    signals = generate_deterministic_signals(ev, DRIVE_MENU_DOM, DRIVE_MENU_A11Y)
    role = next(s for s in signals if s.engine == "role")
    assert role.selector == 'internal:role=menuitem[name="File upload"]'
    text = next(s for s in signals if s.engine == "text_based")
    assert text.selector == 'internal:text="File upload"'


# ---------------------------------------------------------------------------
# _count_role against the a11y evidence shape ({"aria_snapshot": "<yaml>"} —
# Locator.aria_snapshot()'s YAML, the only shape the recorder captures).
# ---------------------------------------------------------------------------

YEAR_SELECT_ARIA_SNAPSHOT = {
    "aria_snapshot": (
        '- textbox "Date of Birth"\n'
        "- combobox:\n"
        '  - option "1900" [selected]\n'
        '  - option "1901"\n'
        "- button \"Submit\""
    ),
}


def test_resolves_to_nothing_true_for_fabricated_name_against_aria_snapshot_yaml():
    fabricated = f'internal:role=combobox[name="{YEAR_SELECT_OPTIONS}"]'
    assert resolves_to_nothing(fabricated, "<html></html>", YEAR_SELECT_ARIA_SNAPSHOT) is True


def test_resolves_to_nothing_false_for_a_real_name_against_aria_snapshot_yaml():
    real = 'internal:role=textbox[name="Date of Birth"]'
    assert resolves_to_nothing(real, "<html></html>", YEAR_SELECT_ARIA_SNAPSHOT) is False


def test_resolves_to_nothing_matches_nameless_role_node_against_aria_snapshot_yaml():
    nameless = "internal:role=combobox"
    assert resolves_to_nothing(nameless, "<html></html>", YEAR_SELECT_ARIA_SNAPSHOT) is False


# ---------------------------------------------------------------------------
# llm/selector_regeneration.py::_extract_a11y_node — the 1-click fix API's own a11y-node
# lookup (BUILD-10's other named consumer, feeds the LLM re-compile prompt). Same dual-shape
# requirement as _count_role above; this is its own separate implementation, not a shared
# helper, so it needs its own coverage.
# ---------------------------------------------------------------------------

def test_extract_a11y_node_matches_against_aria_snapshot_yaml():
    from conxa_compile.llm.selector_regeneration import _extract_a11y_node

    node = _extract_a11y_node(YEAR_SELECT_ARIA_SNAPSHOT, {"role": "textbox", "aria_label": "Date of Birth"})
    assert node == {"role": "textbox", "name": "Date of Birth"}


def test_extract_a11y_node_no_match_against_aria_snapshot_yaml():
    from conxa_compile.llm.selector_regeneration import _extract_a11y_node

    node = _extract_a11y_node(YEAR_SELECT_ARIA_SNAPSHOT, {"role": "checkbox", "aria_label": "Nothing here"})
    assert node is None
