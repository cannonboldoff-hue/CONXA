"""Tests for the durability-ordered fallback_selectors and ephemeral-anchor filter.

Covers:
- XPath lands last in the fallback list (never before CSS/structural signals).
- Near-duplicate structural selectors (label:...+button / ~button) collapse to one.
- Non-unique selectors are dropped when a DOM snapshot marks them as multi-match.
- is_ephemeral_anchor() classifies cookie/consent/banner phrases correctly.
- is_low_quality_anchor() additionally rejects bare HTML tag tokens ("div", "svg:", etc.),
  concatenated container text (>6 tokens or >48 chars), and stray single-letter prefixes
  (avatar/icon initials prepended to nearby text, e.g. "M My Workspace").
- A step whose anchors are only bare-tag or chrome-header tokens emits no relational signal.
- IdentityBundle top signal (role) is the basis for the primary selector display string.
"""
from __future__ import annotations

import pytest

from conxa_compile.compiler.selector_filters import (
    is_ephemeral_anchor,
    is_low_quality_anchor,
    uniqueness_gate,
)
from conxa_compile.compiler.identity_bundle import generate_deterministic_signals
from conxa_compile.compiler.selector_score import durability_score, tag_orthogonality_class
from conxa_compile.compiler.selector_grammar import display_to_signal, signal_to_display


# ---------------------------------------------------------------------------
# is_ephemeral_anchor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    ("Cookie Consent Banner", True),
    ("cookie", True),
    ("GDPR consent dialog", True),
    ("Accept all cookies", True),
    ("Manage preferences", True),
    ("Newsletter signup", True),
    ("We use cookies", True),
    ("Popup offer", True),
    ("pop-up modal", True),
    ("Notification bar", True),
    ("Cookies policy", True),
    ("Close banner", True),
    (".toast-notification", True),
    ("Snackbar message", True),
    # Not ephemeral
    ("Notification settings", False),
    ("Subscribe to updates", False),
    ("Subscriber list", False),
    ("Bannerman Road", False),
    ("Incidents", False),
    ("New Incident", False),
    ("Projects Search CTRL + K K", False),
    ("Submit form", False),
    ("", False),
])
def test_is_ephemeral_anchor(phrase: str, expected: bool) -> None:
    assert is_ephemeral_anchor(phrase) is expected


# ---------------------------------------------------------------------------
# XPath durability is lower than CSS structural (xpath must land last)
# ---------------------------------------------------------------------------

def test_xpath_durability_below_css() -> None:
    assert durability_score("xpath", "/html[1]/body[1]/div[1]/button[1]") < durability_score("css", "header > button")


def test_xpath_orthogonality_class_is_structural() -> None:
    assert tag_orthogonality_class("xpath") == "structural"
    assert tag_orthogonality_class("css-structural") == "structural"


def test_attr_engine_outlasts_role_and_is_its_own_class() -> None:
    assert durability_score("testid", '[data-testid="x"]') > durability_score("attr", '[data-key="19"]')
    assert durability_score("attr", '[data-key="19"]') > durability_score("role", 'internal:role=menuitem[name="File upload"]')
    assert tag_orthogonality_class("attr") == "named-attr"


# ---------------------------------------------------------------------------
# display_to_signal engine inference (used by the fallback ranking path)
# ---------------------------------------------------------------------------

def test_display_to_signal_xpath() -> None:
    engine, _ = display_to_signal("/html[1]/body[1]/div[1]/button[1]")
    assert engine == "xpath"


def test_display_to_signal_css_structural() -> None:
    engine, _ = display_to_signal("label:has-text('Projects Search') + button")
    assert engine == "css-structural"


def test_display_to_signal_relational() -> None:
    engine, _ = display_to_signal('role=button[name="New"] >> right-of=text="Incidents"')
    assert engine == "relational"


# ---------------------------------------------------------------------------
# Durability ordering invariant: xpath (0.10) < any other engine
# ---------------------------------------------------------------------------

def test_durability_ordering_xpath_last() -> None:
    """Given a mixed list, the durability-sorted order must place xpath after structural CSS."""
    candidates = [
        ("xpath", "/html[1]/body[1]/div[2]/div[1]/header[1]/div[3]/div[1]/button[1]"),
        ("css-structural", "label:has-text('Projects Search CTRL + K K') + button"),
        ("css-structural", "label:has-text('Projects Search CTRL + K K') ~ button"),
        ("text_based", 'text="New"'),
        ("role", 'internal:role=button[name="New"]'),
    ]
    from conxa_compile.compiler.selector_score import rank_by_durability
    ranked = rank_by_durability(candidates)
    engines_in_order = [r[1] for r in ranked]
    assert engines_in_order[-1] == "xpath", f"xpath must be last; got {engines_in_order}"
    # role and text must precede xpath
    assert engines_in_order.index("role") < engines_in_order.index("xpath")
    assert engines_in_order.index("text_based") < engines_in_order.index("xpath")


# ---------------------------------------------------------------------------
# uniqueness_gate: non-unique selector dropped; no snapshot → allow
# ---------------------------------------------------------------------------

def test_uniqueness_gate_no_snapshot() -> None:
    # When snapshot is absent, allow through (preserve snapshot-less recompile behavior).
    assert uniqueness_gate("input[type='button']", None) is True


def test_uniqueness_gate_multi_match() -> None:
    # Raw CSS selectors are counted directly against the recorded HTML via BeautifulSoup.
    html = "<input type='button'><input type='button'>"
    assert uniqueness_gate("input[type='button']", html) is False


def test_uniqueness_gate_testid_unique() -> None:
    html = '<div data-testid="btn-new"></div>'
    assert uniqueness_gate('internal:testid=[data-testid="btn-new"]', html) is True


def test_uniqueness_gate_testid_multi_match() -> None:
    html = '<button data-testid="btn-new"></button><button data-testid="btn-new"></button>'
    assert uniqueness_gate('internal:testid=[data-testid="btn-new"]', html) is False


# ---------------------------------------------------------------------------
# generate_deterministic_signals: no relational signal when only ephemeral anchors
# ---------------------------------------------------------------------------

def test_no_relational_signal_for_ephemeral_only_anchors() -> None:
    """A step with a cookie-banner as its only anchor must NOT emit a relational signal."""
    ev = {
        "target": {"tag": "button", "role": "button", "inner_text": "New", "aria_label": "New"},
        "semantic": {"role": "button"},
        "selectors": {"css": "button.primary", "text_based": "New"},
        "anchors": [
            {"element": "Cookie Consent Banner", "relation": "right-of"},
        ],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    engines = [s.engine for s in signals]
    assert "relational" not in engines, (
        f"Expected no relational signal for ephemeral anchor; got signals: {engines}"
    )


def test_relational_signal_skips_ephemeral_uses_stable() -> None:
    """When a stable anchor exists alongside an ephemeral one, the stable one is used."""
    ev = {
        "target": {"tag": "button", "role": "button", "inner_text": "New", "aria_label": "New"},
        "semantic": {"role": "button"},
        "selectors": {"css": "button.primary", "text_based": "New"},
        "anchors": [
            {"element": "Cookie Consent Banner", "relation": "right-of"},
            {"element": "Incidents", "relation": "right-of"},
        ],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    relational = [s for s in signals if s.engine == "relational"]
    assert relational, "Expected a relational signal anchored to 'Incidents'"
    assert "Cookie Consent Banner" not in relational[0].selector
    assert "Incidents" in relational[0].selector


# ---------------------------------------------------------------------------
# is_low_quality_anchor — bare HTML tag token rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase,expected", [
    # Bare tag tokens (the actual regression: recorder leaks container tag names)
    ("div", True),
    ("Div", True),          # case-insensitive
    ("div:", True),         # with trailing colon (format used by some recorder versions)
    ("svg:", True),
    ("button:", True),
    ("span", True),
    ("a", True),
    ("p", True),
    ("ul", True),
    ("li", True),
    ("img", True),
    ("input", True),
    ("label", True),
    ("nav", True),
    ("header", True),
    ("footer", True),
    # Too short
    ("", True),
    ("x", True),
    # Ephemeral overlays are also low-quality (delegates to is_ephemeral_anchor)
    ("Cookie Consent Banner", True),
    ("accept all", True),
    # Concatenated container text (>6 tokens OR >48 chars) → filtered
    ("M My Workspace Projects Search CTRL + K K New Upgrade K", True),   # 12 tokens / 54 chars
    ("M My Workspace New Blueprint Search CTRL + K K New Upgrade K", True),  # 11 tokens / 60 chars
    # Stray single-letter prefix with ≥3 tokens → filtered
    ("M My Workspace", True),   # first token "M" single-alpha, 3 tokens
    ("K New Upgrade", True),    # first token "K" single-alpha, 3 tokens
    # Legitimate stable landmarks — must NOT be filtered
    ("Incidents", False),
    ("New Incident", False),
    # "My Workspace" is 2 tokens, first token "My" not single-char → pass
    ("My Workspace", False),
    # 6 tokens exactly (≤6) and 26 chars (≤48) → pass
    ("Projects Search CTRL + K K", False),
    ("Blueprint Name", False),
    # 5 tokens, 34 chars, first token "cannonboldoff-hue" not single-char → pass
    ("cannonboldoff-hue / SEARCH_ENGINE", False),
    ("Submit form", False),
    ("Search repositories", False),
    # 2-token phrase starting with single letter (e.g. DNS record type) → pass (< 3 tokens)
    ("A Records", False),
])
def test_is_low_quality_anchor(phrase: str, expected: bool) -> None:
    assert is_low_quality_anchor(phrase) is expected, (
        f"is_low_quality_anchor({phrase!r}) should be {expected}"
    )


def test_no_relational_signal_for_bare_tag_anchors() -> None:
    """Anchors that are only bare HTML tag names must not produce a relational signal.

    This is the actual regression: anchor_phrases = ["div", "M My Workspace …", "M My Workspace"]
    where "div" leaked in as the first anchor and produced
    internal:role=button[name="New"] >> right-of=internal:text="div".
    """
    ev = {
        "target": {"tag": "button", "role": "button", "inner_text": "New", "aria_label": "New"},
        "semantic": {"role": "button"},
        "selectors": {"css": "button.primary", "text_based": "New"},
        "anchors": [
            {"element": "div", "relation": "right-of"},
            {"element": "svg:", "relation": "right-of"},
        ],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    engines = [s.engine for s in signals]
    assert "relational" not in engines, (
        f"Expected no relational signal for bare-tag anchors; got signals: "
        f"{[(s.engine, s.selector) for s in signals]}"
    )


def test_relational_signal_skips_div_uses_real_anchor() -> None:
    """When 'div' comes first in anchors but a real text landmark follows, use the real one."""
    ev = {
        "target": {"tag": "button", "role": "button", "inner_text": "New", "aria_label": "New"},
        "semantic": {"role": "button"},
        "selectors": {"css": "button.primary", "text_based": "New"},
        "anchors": [
            {"element": "div", "relation": "right-of"},
            {"element": "Incidents", "relation": "right-of"},
        ],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    relational = [s for s in signals if s.engine == "relational"]
    assert relational, "Expected a relational signal anchored to 'Incidents', not 'div'"
    assert "div" not in relational[0].selector, (
        f"Relational anchor must not be 'div'; got: {relational[0].selector}"
    )
    assert "Incidents" in relational[0].selector


def test_no_relational_signal_for_chrome_header_anchors() -> None:
    """Anchors that are only header-navigation blobs must not produce a relational signal.

    This is the regression that survived after the bare-tag fix: the recorder captures
    the entire nav as a concatenated string ("M My Workspace Projects Search CTRL + K K
    New Upgrade K") which has 12 tokens / 54 chars — caught by the >6-token / >48-char
    rule — and "M My Workspace" which has a stray single-letter prefix "M" with ≥3 tokens.
    Neither is a useful spatial landmark; emitting them is worse than emitting nothing.
    """
    ev = {
        "target": {"tag": "button", "role": "button", "inner_text": "New", "aria_label": "New"},
        "semantic": {"role": "button"},
        "selectors": {"css": "button.primary", "text_based": "New"},
        "anchors": [
            {"element": "div", "relation": "right-of"},
            {"element": "M My Workspace Projects Search CTRL + K K New Upgrade K", "relation": "right-of"},
            {"element": "M My Workspace", "relation": "right-of"},
        ],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    engines = [s.engine for s in signals]
    assert "relational" not in engines, (
        f"Expected no relational signal for header-chrome anchors; got: "
        f"{[(s.engine, s.selector) for s in signals]}"
    )


# ---------------------------------------------------------------------------
# Primary selector promotion: IdentityBundle top signal → display string
# ---------------------------------------------------------------------------

def test_signal_to_display_role_roundtrip() -> None:
    """signal_to_display('role', internal:role=...) produces the public role= form."""
    stored = 'internal:role=button[name="New"]'
    display = signal_to_display("role", stored)
    assert display == 'role=button[name="New"]', f"Unexpected display: {display!r}"


def test_signal_to_display_text_roundtrip() -> None:
    stored = 'internal:text="Blueprint"'
    display = signal_to_display("text_based", stored)
    assert display == 'text="Blueprint"', f"Unexpected display: {display!r}"


def test_signal_to_display_testid_roundtrip() -> None:
    stored = 'internal:testid=[data-testid="select-git-repo-url-input"]'
    display = signal_to_display("testid", stored)
    assert display == '[data-testid="select-git-repo-url-input"]', (
        f"Unexpected display: {display!r}"
    )


def test_role_signal_falls_back_to_placeholder_name() -> None:
    """No aria_label/name/inner_text but a placeholder must still yield a role signal,
    ranked above the xpath fallback — instead of degrading to positional xpath only.

    Regression: a placeholder-only search input (hashed CSS classes, no aria-label/
    name/label, e.g. HubSpot's contact search combobox) previously produced only a
    durability-0.01 xpath since ax_name required aria_label/name/inner_text.
    """
    ev = {
        "target": {"tag": "input", "role": "combobox", "placeholder": "Search"},
        "semantic": {"role": "combobox"},
        "selectors": {
            "css": "",
            "xpath": "/html[1]/body[1]/div[5]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/input[1]",
        },
        "anchors": [],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    role_signals = [s for s in signals if s.engine == "role"]
    assert role_signals, f"Expected a role signal from placeholder fallback; got {[s.engine for s in signals]}"
    assert role_signals[0].selector == 'internal:role=combobox[name="Search"]'
    xpath_signals = [s for s in signals if s.engine == "xpath"]
    assert xpath_signals, "Expected xpath signal to still be present as a fallback"
    assert signals.index(role_signals[0]) < signals.index(xpath_signals[0]), (
        "role (placeholder-derived) must rank above xpath"
    )


def test_role_signal_falls_back_to_label_text_name() -> None:
    """No aria_label/name/inner_text/placeholder but a label_text must still yield a role
    signal, ranked above the xpath fallback.

    Regression: a phone-number input inside a popover (label association via a sibling
    <label>, not aria-labelledby) previously produced only a durability-0.01 xpath since
    ax_name didn't consider label_text at all. label_text must stay the LAST fallback
    (after placeholder) since, unlike the element's own attributes, it can mis-capture a
    sibling's text — see runtime/run.js's a11yRecoveryName, which documents the same risk.
    """
    ev = {
        "target": {"tag": "input", "role": "textbox", "label_text": "Edit phone number"},
        "semantic": {"role": "textbox"},
        "selectors": {
            "css": "",
            "xpath": "/html[1]/body[1]/div[5]/div[1]/div[1]/div[1]/div[1]/div[1]/div[1]/input[1]",
        },
        "anchors": [],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    role_signals = [s for s in signals if s.engine == "role"]
    assert role_signals, f"Expected a role signal from label_text fallback; got {[s.engine for s in signals]}"
    assert role_signals[0].selector == 'internal:role=textbox[name="Edit phone number"]'
    xpath_signals = [s for s in signals if s.engine == "xpath"]
    assert xpath_signals, "Expected xpath signal to still be present as a fallback"
    assert signals.index(role_signals[0]) < signals.index(xpath_signals[0]), (
        "role (label_text-derived) must rank above xpath"
    )


def test_placeholder_outranks_label_text_in_ax_name() -> None:
    """When both placeholder and label_text are present, placeholder wins — it's the
    element's own attribute, whereas label_text is external and can mis-capture a
    sibling's text (same reasoning as runtime's a11yRecoveryName ordering)."""
    ev = {
        "target": {"tag": "input", "role": "textbox", "placeholder": "Search", "label_text": "wrong"},
        "semantic": {"role": "textbox"},
        "selectors": {"css": "", "xpath": "/html[1]/body[1]/input[1]"},
        "anchors": [],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    role_signals = [s for s in signals if s.engine == "role"]
    assert role_signals and role_signals[0].selector == 'internal:role=textbox[name="Search"]'


def test_bundle_top_signal_is_role_for_named_button() -> None:
    """generate_deterministic_signals puts the role signal first for a named button."""
    ev = {
        "target": {"tag": "button", "role": "button", "inner_text": "New", "aria_label": "New"},
        "semantic": {"role": "button"},
        "selectors": {
            "css": "label:has-text('Projects Search CTRL + K K') + button",
            "text_based": "New",
            "xpath": "/html[1]/body[1]/div[2]/div[1]/header[1]/div[3]/div[1]/button[1]",
        },
        "anchors": [],
        "snapshot": {},
    }
    signals = generate_deterministic_signals(ev)
    assert signals, "Expected at least one signal"
    assert signals[0].engine == "role", (
        f"Expected top signal to be 'role'; got {signals[0].engine!r}"
    )
    top_display = signal_to_display(signals[0].engine, signals[0].selector)
    assert top_display == 'role=button[name="New"]', (
        f"Expected role display string; got {top_display!r}"
    )
