"""Tests for ElementFingerprint compilation and Assertion building."""

from __future__ import annotations

from conxa_compile.compiler.build import (
    _build_assertions,
    _build_element_fingerprint,
    _build_structural_fingerprint,
    _fingerprint_is_weak,
)
from conxa_compile.compiler.selector_filters import (
    dedup_by_orthogonality,
    pii_bind,
    uniqueness_gate,
    xpath_shadow_guard,
)
from conxa_compile.compiler.selector_score import (
    durability_score,
    rank_by_durability,
    tag_orthogonality_class,
)
from conxa_compile.compiler.identity_bundle import generate_deterministic_signals
from conxa_compile.compiler.selector_grammar import to_playwright_grammar
from conxa_compile.compiler.stable_hash import compute_stable_hash
from conxa_core.models.skill_spec import (
    Assertion,
    ElementFingerprint,
    FrameFingerprint,
    IdentityBundle,
    IdentitySignal,
    ShadowHost,
    SkillStep,
    ValidationBlock,
)


# ─── ElementFingerprint ──────────────────────────────────────────────────────

def _make_ev(
    *,
    tag="button",
    inner_text="Sign In",
    aria_label="",
    name="",
    data_testid="",
    role="button",
    input_type="",
    classes=None,
    css="",
    x=100, y=400, vw=1280, vh=800,
    anchors=None,
):
    return {
        "target": {
            "tag": tag,
            "inner_text": inner_text,
            "aria_label": aria_label or None,
            "name": name or None,
            "classes": classes or [],
        },
        "semantic": {
            "role": role,
            "input_type": input_type or None,
            "normalized_text": inner_text.lower(),
        },
        "selectors": {
            "css": css or f"{tag}.btn",
            "aria": f'[role="{role}"][name="{inner_text}"]' if not data_testid else f'[data-testid="{data_testid}"]',
            "text_based": f'text="{inner_text}"',
            "xpath": f"//{tag}",
        },
        "visual": {
            "bbox": {"x": x, "y": y, "w": 80, "h": 30, "vw": vw, "vh": vh},
            "viewport": f"{vw}x{vh}",
        },
        "anchors": anchors or [{"element": "Login form", "relation": "inside"}],
        "action": {"action": "click", "value": None},
    }


def test_fingerprint_extracts_data_testid_from_css():
    ev = _make_ev(css='button[data-testid="signin-btn"]')
    fp = _build_element_fingerprint(ev)
    assert isinstance(fp, ElementFingerprint)
    assert fp.data_testid == "signin-btn"


def test_fingerprint_extracts_data_testid_from_aria():
    ev = _make_ev(css="button.btn")
    ev["selectors"]["aria"] = '[data-testid="submit-order"]'
    fp = _build_element_fingerprint(ev)
    assert fp.data_testid == "submit-order"


def test_fingerprint_extracts_basic_fields():
    ev = _make_ev(
        tag="button",
        inner_text="Submit",
        aria_label="Submit order",
        role="button",
        input_type="",
    )
    fp = _build_element_fingerprint(ev)
    assert fp.tag == "button"
    assert fp.inner_text == "Submit"
    assert fp.aria_label == "Submit order"
    assert fp.role == "button"


def test_fingerprint_trims_inner_text_to_120():
    long_text = "A" * 200
    ev = _make_ev(inner_text=long_text)
    fp = _build_element_fingerprint(ev)
    assert len(fp.inner_text) == 120


def test_fingerprint_filters_hash_like_classes():
    ev = _make_ev(classes=["btn", "primary", "a3f9b2c1", "abc123456789"])
    fp = _build_element_fingerprint(ev)
    # Hash-like classes should be filtered; stable tokens kept
    assert "btn" in fp.css_class_tokens
    assert "primary" in fp.css_class_tokens
    # Hash-like class with 4+ consecutive digits should be dropped
    assert not any("a3f9b2c1" in t for t in fp.css_class_tokens)


def test_fingerprint_position_hint_normalized():
    ev = _make_ev(x=640, y=400, vw=1280, vh=800)
    fp = _build_element_fingerprint(ev)
    assert fp.position_hint["x_pct"] == 0.5
    assert fp.position_hint["y_pct"] == 0.5


def test_fingerprint_anchor_phrases():
    anchors = [
        {"element": "Login form", "relation": "inside"},
        {"element": "Password field", "relation": "above"},
    ]
    ev = _make_ev(anchors=anchors)
    fp = _build_element_fingerprint(ev)
    assert "Login form" in fp.anchor_phrases
    assert "Password field" in fp.anchor_phrases


def test_fingerprint_is_weak_with_no_strong_signal():
    # No data-testid, no aria_label/name, no inner_text — nothing for the runtime
    # resolver to score the candidate against.
    ev = _make_ev(inner_text="", aria_label="", name="", css="button.btn")
    fp = _build_element_fingerprint(ev)
    assert _fingerprint_is_weak(fp)


def test_fingerprint_is_weak_false_with_testid():
    ev = _make_ev(inner_text="", aria_label="", name="", css='button[data-testid="signin-btn"]')
    fp = _build_element_fingerprint(ev)
    assert not _fingerprint_is_weak(fp)


def test_fingerprint_is_weak_false_with_inner_text():
    ev = _make_ev(inner_text="Sign In", aria_label="", name="", css="button.btn")
    fp = _build_element_fingerprint(ev)
    assert not _fingerprint_is_weak(fp)


def test_fingerprint_is_weak_false_with_aria_label():
    ev = _make_ev(inner_text="", aria_label="Sign in", name="", css="button.btn")
    fp = _build_element_fingerprint(ev)
    assert not _fingerprint_is_weak(fp)


# ─── Assertion building ──────────────────────────────────────────────────────

def _make_validation(wait_for=None, success_conditions=None):
    return ValidationBlock(
        wait_for=wait_for or {},
        success_conditions=success_conditions or {},
    )


def test_assertions_url_changed_from_wait_for():
    ev = _make_ev()
    ev["action"]["action"] = "click"
    ev["page"] = {"url": "https://example.com/login"}
    validation = _make_validation(wait_for={"type": "url_change", "timeout": 8000})
    assertions = _build_assertions(ev, validation, {}, {})
    assertion = next(a for a in assertions if a.type == "url_changed")
    assert assertion.target == ev["page"]["url"]
    assert assertion.required is True


def test_assertions_element_appear_from_wait_for():
    ev = _make_ev()
    ev["action"]["action"] = "click"
    validation = _make_validation(
        wait_for={"type": "element_appear", "target": ".success-banner", "timeout": 5000},
    )
    assertions = _build_assertions(ev, validation, {}, {})
    assert any(a.type == "selector_present" and a.target == ".success-banner" and a.required for a in assertions)


def test_assertions_empty_for_fill_without_target():
    # No resolvable primary_selector — nothing to assert the value landed in.
    ev = _make_ev()
    ev["action"]["action"] = "fill"
    ev["target"]["tag"] = "input"
    assertions = _build_assertions(ev, _make_validation(), {}, {})
    assert assertions == []


def test_assertions_value_equals_required_for_fill():
    ev = _make_ev()
    ev["action"]["action"] = "fill"
    ev["action"]["value"] = "alice@example.com"
    ev["target"]["tag"] = "input"
    target = {"primary_selector": "#email"}
    assertions = _build_assertions(ev, _make_validation(), {}, target, "alice@example.com")
    assertion = next(a for a in assertions if a.type == "value_equals")
    assert assertion.required is True
    assert assertion.target == "#email"
    assert assertion.expected == "alice@example.com"


def test_assertions_value_equals_skipped_for_keyboard_event():
    # Keyboard-shortcut steps serialize their value as a JSON blob (e.g. '{"key":"Enter"}') —
    # that's not field content, so no value_equals should be synthesized for it.
    ev = _make_ev()
    ev["action"]["action"] = "fill"
    ev["action"]["value"] = '{"key":"Enter"}'
    target = {"primary_selector": "#email"}
    assertions = _build_assertions(ev, _make_validation(), {}, target, '{"key":"Enter"}')
    assert not any(a.type == "value_equals" for a in assertions)


def test_assertions_dialog_opened_from_post_condition():
    # Recorder classified the click's effect as a dialog opening (Priority 1 distillation) — the
    # enforced assertion should target the recorded dialog_signal, not a generic state_changed.
    ev = _make_ev(inner_text="Add to cart")
    ev["action"]["action"] = "click"
    ev["post_condition"] = {"classified_effect": "dialog_opened", "dialog_signal": '[role="dialog"]'}
    validation = _make_validation(wait_for={"type": "none"})
    assertions = _build_assertions(ev, validation, {}, {})
    assert len(assertions) == 1
    assertion = assertions[0]
    assert assertion.type == "selector_present"
    assert assertion.target == '[role="dialog"]'
    assert assertion.required is True


def test_assertions_value_equals_prefers_readback_over_recorded_value():
    # The committed value read back from the DOM (post-normalization by the page's own JS) wins
    # over the recorded intent value when both are present.
    ev = _make_ev()
    ev["action"]["action"] = "fill"
    ev["action"]["value"] = "alice"
    ev["post_condition"] = {"classified_effect": "value_set", "value_readback": "alice@example.com"}
    target = {"primary_selector": "#email"}
    assertions = _build_assertions(ev, _make_validation(), {}, target, "alice")
    assertion = next(a for a in assertions if a.type == "value_equals")
    assert assertion.expected == "alice@example.com"


def test_assertions_value_equals_ignores_redacted_readback():
    # A password field's readback is always "{{REDACTED}}" — never trust it as the expected
    # value. In practice the recorded value is redacted the same way, which the existing
    # keyboard-event guard already catches (both forms start with "{"), so no value_equals is
    # synthesized for a redacted field at all — asserting against a literal "{{REDACTED}}" string
    # would never verify anything real. This is the redaction guarantee: no readback leak either
    # way.
    ev = _make_ev()
    ev["action"]["action"] = "fill"
    ev["action"]["value"] = "{{REDACTED}}"
    ev["post_condition"] = {"classified_effect": "value_set", "value_readback": "{{REDACTED}}"}
    target = {"primary_selector": "#password"}
    assertions = _build_assertions(ev, _make_validation(), {}, target, "{{REDACTED}}")
    assert not any(a.type == "value_equals" for a in assertions)


def test_assertions_advisory_from_success_conditions():
    # A generic (non-commit, non-destructive) click with incidental DOM evidence — success
    # conditions stay advisory since the click isn't classified as consequential.
    ev = _make_ev()
    ev["action"]["action"] = "click"
    validation = _make_validation(
        wait_for={"type": "intent_outcome", "timeout": 5000},
        success_conditions={
            "required_elements": [".order-confirmation"],
            "expected_text_tokens": ["success"],
        },
    )
    assertions = _build_assertions(ev, validation, {}, {})
    advisory = [a for a in assertions if not a.required]
    assert any(a.type == "selector_present" for a in advisory)
    assert any(a.type == "text_present" for a in advisory)
    assert not any(a.required for a in assertions)


def test_assertions_required_elements_promoted_for_commit_click():
    # A submit-labeled click with no url_change/element_appear evidence but a recorded DOM
    # diff: the first required_element is promoted to the enforced post-condition.
    ev = _make_ev(inner_text="Submit")
    ev["action"]["action"] = "click"
    ev["target"]["type"] = "submit"
    validation = _make_validation(
        wait_for={"type": "intent_outcome", "timeout": 5000},
        success_conditions={"required_elements": [".order-confirmation"]},
    )
    assertions = _build_assertions(ev, validation, {}, {})
    promoted = next(a for a in assertions if a.type == "selector_present")
    assert promoted.required is True
    assert promoted.target == ".order-confirmation"


def test_assertions_state_changed_for_evidence_less_commit():
    # A submit click with no wait_for evidence and no success_conditions at all still gets a
    # synthesized, required state_changed check — the "no silent no-op" guarantee.
    ev = _make_ev(inner_text="Save")
    ev["action"]["action"] = "click"
    ev["target"]["type"] = "submit"
    validation = _make_validation(wait_for={"type": "none"})
    assertions = _build_assertions(ev, validation, {}, {})
    assert len(assertions) == 1
    assert assertions[0].type == "state_changed"
    assert assertions[0].required is True


def test_assertions_no_state_changed_for_non_consequential_click():
    # A plain click with no submit/destructive signal and no evidence gets nothing — we don't
    # fabricate a required check where there's genuinely no consequential-action classification.
    ev = _make_ev(inner_text="Expand row")
    ev["action"]["action"] = "click"
    validation = _make_validation(wait_for={"type": "none"})
    assertions = _build_assertions(ev, validation, {}, {})
    assert assertions == []


def test_assertions_state_changed_for_upload_with_no_evidence():
    # A file input rarely moves the URL or visible DOM on its own — most sites don't react
    # until a later, separate submit — so without the upload fallback this compiled to zero
    # assertions. Mirrors test_assertions_state_changed_for_evidence_less_commit.
    ev = _make_ev(inner_text="")
    ev["action"]["action"] = "upload"
    validation = _make_validation(wait_for={"type": "none"})
    assertions = _build_assertions(ev, validation, {}, {})
    assert len(assertions) == 1
    assert assertions[0].type == "state_changed"
    assert assertions[0].required is True


def test_assertions_state_changed_for_upload_intent_with_no_evidence():
    # Same fallback for the raw recorder-emitted action name, in case a step is ever built
    # directly from it rather than the post-compile "upload" name.
    ev = _make_ev(inner_text="")
    ev["action"]["action"] = "upload_intent"
    validation = _make_validation(wait_for={"type": "none"})
    assertions = _build_assertions(ev, validation, {}, {})
    assert len(assertions) == 1
    assert assertions[0].type == "state_changed"
    assert assertions[0].required is True


def test_assertions_upload_prefers_stronger_evidence():
    # When the recording did show a real reaction (e.g. a redirect after upload), that stronger,
    # more specific assertion wins — the state_changed fallback must not also claim the slot.
    ev = _make_ev(inner_text="")
    ev["action"]["action"] = "upload"
    ev["page"] = {"url": "https://example.com/upload"}
    validation = _make_validation(wait_for={"type": "url_change", "timeout": 8000})
    assertions = _build_assertions(ev, validation, {}, {})
    assert any(a.type == "url_changed" and a.required for a in assertions)
    assert not any(a.type == "state_changed" for a in assertions)


# ─── Structural fingerprint ──────────────────────────────────────────────────

def _make_step(intent="click_button", primary_selector="button.submit", data_testid="", inner_text="Submit", tag="button"):
    fp = ElementFingerprint(
        tag=tag, inner_text=inner_text, data_testid=data_testid, role="button"
    )
    return SkillStep(
        action="click",
        intent=intent,
        target={"primary_selector": primary_selector},
        identity_bundle=IdentityBundle(fingerprint=fp),
    )


def test_structural_fingerprint_collects_landmarks():
    steps = [
        _make_step("navigate_to_login", "", "", "", ""),
        _make_step("fill_email", "input[type=email]", "", "Email", "input"),
        _make_step("click_submit", "button.submit", "signin-btn", "Sign In", "button"),
        _make_step("click_cancel", "button.cancel", "", "Cancel", "button"),
    ]
    # First step is navigate (action="navigate") — let's make it explicit
    steps[0] = SkillStep(
        action={"action": "navigate", "url": "https://example.com"},
        intent="navigate_to_start_url",
        target={},
    )
    fp = _build_structural_fingerprint(steps)
    assert fp["landmark_count"] >= 1
    assert any(lm.get("data_testid") == "signin-btn" for lm in fp["landmarks"])


def test_structural_fingerprint_skips_navigate_and_scroll():
    nav = SkillStep(action={"action": "navigate"}, intent="navigate_to_start_url", target={})
    scroll = SkillStep(action={"action": "scroll"}, intent="scroll_viewport", target={})
    click = _make_step("click_submit", "button.go", "go-btn", "Go", "button")
    fp = _build_structural_fingerprint([nav, scroll, click])
    assert fp["landmark_count"] == 1


def test_structural_fingerprint_only_covers_the_page_the_run_starts_on():
    def on(step, url, tab_id="tab_0"):
        step.tab = {"id": tab_id, "url": url}
        return step

    start = on(_make_step("click_first", "a.first", "first-link", "First", "a"), "https://a.test/home")
    moved = on(_make_step("click_second", "a.second", "second-link", "Second", "a"), "https://a.test/file")
    later = on(_make_step("click_third", "button.third", "third-btn", "Third", "button"), "https://a.test/file")
    other_tab = on(_make_step("click_other", "button.o", "other-btn", "Other", "button"), "https://b.test/", "tab_1")
    fp = _build_structural_fingerprint([start, moved, later])
    # The click that moved the page acted on the start page; nothing after it did.
    assert [lm["data_testid"] for lm in fp["landmarks"]] == ["first-link", "second-link"]
    fp = _build_structural_fingerprint([start, other_tab])
    assert [lm["data_testid"] for lm in fp["landmarks"]] == ["first-link"]


def test_structural_fingerprint_counts_one_element_once():
    # BUILD-90: choose, press a key and type in one sort box were three landmarks of one element, so that one
    # element breaking read as "3/3 landmarks missing" (the app was redesigned).
    box = [_make_step(i, "[data-test=sort]", "sort", "Sort", "select") for i in ("pick", "press", "type")]
    other = _make_step("add", "[data-test=add]", "add", "Add to cart", "button")
    fp = _build_structural_fingerprint([*box, other])
    assert [lm["data_testid"] for lm in fp["landmarks"]] == ["sort", "add"]


# ─── Phase 1: Selector durability scoring ────────────────────────────────────

def test_durability_testid_beats_xpath():
    d_testid = durability_score("testid", '[data-testid="signin-btn"]')
    d_xpath = durability_score("xpath", "//div[3]/button[1]")
    assert d_testid > d_xpath


def test_durability_role_beats_css_structural():
    d_role = durability_score("role", 'role=button[name="Submit"]')
    d_css = durability_score("css-structural", "div.container > ul > li:nth-child(3) > button")
    assert d_role > d_css


def test_durability_guid_like_penalty():
    d_normal = durability_score("css-id", "#submit-btn")
    d_guid = durability_score("css-id", "#a3b4c5d6-e7f8-9012-abcd-ef1234567890")
    assert d_guid < d_normal * 0.5


def test_durability_positional_penalty():
    d_plain = durability_score("css", "button.submit")
    d_positional = durability_score("css", "ul > li:nth-child(3) > button")
    assert d_positional < d_plain


def test_durability_testid_present_downgrade():
    d_without = durability_score("role", 'role=button[name="X"]', testid_present=False)
    d_with = durability_score("role", 'role=button[name="X"]', testid_present=True)
    assert d_with < d_without


def test_tag_orthogonality_class():
    assert tag_orthogonality_class("testid") == "test-contract"
    assert tag_orthogonality_class("role") == "semantic-aria"
    assert tag_orthogonality_class("aria") == "semantic-aria"
    assert tag_orthogonality_class("text_based") == "visible-text"
    assert tag_orthogonality_class("relational") == "spatial-anchor"
    assert tag_orthogonality_class("css") == "structural"
    assert tag_orthogonality_class("xpath") == "structural"


def test_rank_by_durability_order():
    candidates = [
        ("xpath", "//div/button"),
        ("testid", '[data-testid="btn"]'),
        ("text_based", 'text="Submit"'),
    ]
    ranked = rank_by_durability(candidates)
    engines = [r[1] for r in ranked]
    assert engines.index("testid") < engines.index("text_based")
    assert engines.index("text_based") < engines.index("xpath")


def test_rank_by_durability_deduplicates():
    candidates = [
        ("css", "button.submit"),
        ("css", "button.submit"),  # duplicate
        ("xpath", "//button"),
    ]
    ranked = rank_by_durability(candidates)
    assert len(ranked) == 2


def test_identity_signal_model():
    sig = IdentitySignal(
        engine="testid",
        selector='[data-testid="submit"]',
        durability=0.99,
        orthogonality_class="test-contract",
        unique_at_compile=True,
        source="compiler",
    )
    assert sig.engine == "testid"
    assert sig.durability == 0.99
    assert sig.orthogonality_class == "test-contract"
    assert sig.unique_at_compile is True


# ─── Phase 2: Selector filtering gates ───────────────────────────────────────
# uniqueness_gate takes the recorded page's raw HTML (as read from
# conxa_core.storage.snapshots.read_dom_snapshot) — not a hand-built DOM tree —
# since that's the only form the real compile pipeline has available.

def test_uniqueness_gate_passes_single_match():
    html = '<div data-testid="submit-btn"></div>'
    assert uniqueness_gate('internal:testid=[data-testid="submit-btn"]', html) is True


def test_uniqueness_gate_rejects_no_match():
    html = '<div data-testid="other-btn"></div>'
    assert uniqueness_gate('internal:testid=[data-testid="submit-btn"]', html) is False


def test_uniqueness_gate_rejects_multi_match():
    html = '<button data-testid="btn"></button><button data-testid="btn"></button>'
    assert uniqueness_gate('internal:testid=[data-testid="btn"]', html) is False


def test_uniqueness_gate_passes_without_snapshot():
    assert uniqueness_gate("button.submit", None) is True


def test_uniqueness_gate_text_selector_multi_match():
    html = "<button>Edit</button><button>Edit</button>"
    assert uniqueness_gate('internal:text="Edit"', html) is False


def test_uniqueness_gate_absent_ok_allows_zero_matches():
    """A 0-match verdict can mean 'genuinely not on this page' or 'the recorded snapshot didn't
    contain it' (e.g. captured before a portal-mounted dialog existed) — absent_ok=True treats
    both the same as the no-snapshot case (can't verify, allow through). Used at compile time
    (identity_bundle.py) so a snapshot gap doesn't stamp a real, durable selector as not-unique."""
    html = '<div data-testid="other-btn"></div>'
    assert uniqueness_gate('internal:testid=[data-testid="submit-btn"]', html, absent_ok=True) is True


def test_uniqueness_gate_absent_ok_still_rejects_multi_match():
    """absent_ok only rescues the 0-match case — a genuine duplicate is still rejected."""
    html = '<button data-testid="btn"></button><button data-testid="btn"></button>'
    assert uniqueness_gate('internal:testid=[data-testid="btn"]', html, absent_ok=True) is False


def test_uniqueness_gate_text_selector_single_match():
    html = "<button>Edit</button><button>Delete</button>"
    assert uniqueness_gate('internal:text="Edit"', html) is True


def test_uniqueness_gate_role_without_a11y_tree_allows_through():
    # ARIA role can't be derived from raw HTML alone — without the a11y snapshot
    # the gate can't verify a role selector, so it allows it through.
    html = "<button>Edit</button><button>Edit</button>"
    assert uniqueness_gate('internal:role=button[name="Edit"]', html) is True


def test_uniqueness_gate_role_with_a11y_tree_multi_match():
    html = "<button>Edit</button><button>Edit</button>"
    a11y_tree = {
        "role": "WebArea",
        "children": [
            {"role": "button", "name": "Edit"},
            {"role": "button", "name": "Edit"},
        ],
    }
    assert uniqueness_gate('internal:role=button[name="Edit"]', html, a11y_tree) is False


def test_uniqueness_gate_role_with_a11y_tree_single_match():
    html = "<button>Edit</button><button>Delete</button>"
    a11y_tree = {
        "aria_snapshot": '- WebArea:\n  - button "Edit"\n  - button "Delete"',
    }
    assert uniqueness_gate('internal:role=button[name="Edit"]', html, a11y_tree) is True


def test_uniqueness_gate_relational_selector_assumed_unique():
    # Relational selectors are spatially disambiguated by the runtime resolver,
    # not counted here.
    html = "<button>Edit</button><button>Edit</button>"
    assert uniqueness_gate(
        'internal:role=button[name="Edit"] >> right-of=internal:text="Acme"', html
    ) is True


def test_dedup_by_orthogonality_keeps_best():
    s1 = IdentitySignal(engine="role", selector='role=button[name="X"]', durability=0.95, orthogonality_class="semantic-aria")
    s2 = IdentitySignal(engine="aria", selector='[aria-label="X"]', durability=0.90, orthogonality_class="semantic-aria")
    s3 = IdentitySignal(engine="testid", selector='[data-testid="x"]', durability=0.99, orthogonality_class="test-contract")
    result = dedup_by_orthogonality([s1, s2, s3])
    oc_engines = {s.orthogonality_class: s.engine for s in result}
    assert oc_engines["semantic-aria"] == "role"  # higher durability kept
    assert oc_engines["test-contract"] == "testid"
    assert len(result) == 2


def test_dedup_by_orthogonality_single_per_class():
    s1 = IdentitySignal(engine="css", selector="button.a", durability=0.30, orthogonality_class="structural")
    s2 = IdentitySignal(engine="xpath", selector="//button", durability=0.10, orthogonality_class="structural")
    result = dedup_by_orthogonality([s1, s2])
    assert len(result) == 1
    assert result[0].engine == "css"  # higher durability kept


def test_pii_bind_replaces_email():
    sel, bound = pii_bind('[aria-label="Send to user@example.com"]')
    assert bound is True
    assert "{{email}}" in sel
    assert "user@example.com" not in sel


def test_pii_bind_replaces_input_value():
    sel, bound = pii_bind('text="John Smith"', inputs={"full_name": "John Smith"})
    assert bound is True
    assert "{{full_name}}" in sel


def test_pii_bind_passes_clean_selector():
    sel, bound = pii_bind('[data-testid="submit-btn"]')
    assert bound is False
    assert sel == '[data-testid="submit-btn"]'


def test_xpath_shadow_guard_blocks_xpath_in_shadow():
    assert xpath_shadow_guard("xpath", [{"host": "#shadow-root", "mode": "open"}]) is False


def test_xpath_shadow_guard_allows_css_in_shadow():
    assert xpath_shadow_guard("css", [{"host": "#shadow-root", "mode": "open"}]) is True


def test_xpath_shadow_guard_allows_xpath_no_shadow():
    assert xpath_shadow_guard("xpath", []) is True
    assert xpath_shadow_guard("xpath", None) is True


# ─── Phase 3: IdentityBundle, stable_hash, FrameFingerprint, ShadowHost ──────

def test_stable_hash_deterministic():
    element = {
        "tag": "button",
        "parent_tag": "div",
        "aria_label": "Submit",
        "name": "",
        "inner_text": "Submit",
        "attributes": {"data-testid": "submit-btn", "type": "submit"},
    }
    h1 = compute_stable_hash(element)
    h2 = compute_stable_hash(element)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex


def test_stable_hash_strips_dynamic_classes():
    base = {
        "tag": "button", "parent_tag": "",
        "aria_label": "X", "name": "", "inner_text": "",
        "attributes": {"class": "btn primary focus active"},
    }
    stripped = {
        "tag": "button", "parent_tag": "",
        "aria_label": "X", "name": "", "inner_text": "",
        "attributes": {"class": "btn primary"},
    }
    assert compute_stable_hash(base) == compute_stable_hash(stripped)


def test_stable_hash_changes_on_tag_change():
    e1 = {"tag": "button", "parent_tag": "", "aria_label": "X", "name": "", "inner_text": "", "attributes": {}}
    e2 = {"tag": "a", "parent_tag": "", "aria_label": "X", "name": "", "inner_text": "", "attributes": {}}
    assert compute_stable_hash(e1) != compute_stable_hash(e2)


def test_identity_bundle_model():
    sig = IdentitySignal(engine="testid", selector='[data-testid="x"]', durability=0.99, orthogonality_class="test-contract")
    bundle = IdentityBundle(
        signals=[sig],
        stable_hash="abc123",
        frame_chain=[],
        shadow_path=[],
        compat_fingerprint="",
        guid_like_attrs=[],
        destructive=False,
    )
    assert bundle.signals[0].engine == "testid"
    assert bundle.stable_hash == "abc123"
    assert bundle.destructive is False


def test_frame_fingerprint_model():
    sig = IdentitySignal(engine="text_based", selector='text="reports"', durability=0.85, orthogonality_class="visible-text")
    fp = FrameFingerprint(signals=[sig], url="https://app.example.com", url_pattern="*/reports*")
    assert len(fp.signals) == 1
    assert fp.url_pattern == "*/reports*"


def test_shadow_host_model():
    sh = ShadowHost(host="my-widget", mode="open")
    assert sh.host == "my-widget"
    assert sh.mode == "open"


def test_skill_step_has_identity_bundle():
    sig = IdentitySignal(engine="testid", selector='[data-testid="go"]', durability=0.99, orthogonality_class="test-contract")
    bundle = IdentityBundle(signals=[sig], stable_hash="deadbeef")
    step = SkillStep(action="click", identity_bundle=bundle)
    assert step.identity_bundle is not None
    assert step.identity_bundle.stable_hash == "deadbeef"


def test_build_identity_bundle_from_ev():
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(
        tag="button",
        inner_text="Submit",
        role="button",
        aria_label="Submit order",
        css='button[data-testid="submit-order"]',
    )
    bundle = _build_identity_bundle(ev)
    assert isinstance(bundle, IdentityBundle)
    assert bundle.stable_hash != ""
    engines = [s.engine for s in bundle.signals]
    assert "testid" in engines
    # At least 1 signal present
    assert len(bundle.signals) >= 1
    # Cutover: the bundle is the single identity object — it carries the scoring fingerprint.
    assert isinstance(bundle.fingerprint, ElementFingerprint)
    assert bundle.fingerprint.role == "button"
    assert bundle.fingerprint.data_testid == "submit-order"


# ─── Phase 4: Playwright grammar + deterministic-floor generator ──────────────

def test_playwright_grammar_testid():
    result = to_playwright_grammar("testid", '[data-testid="signin-btn"]')
    assert result == 'internal:testid=[data-testid="signin-btn"]'


def test_playwright_grammar_testid_bare_value():
    result = to_playwright_grammar("testid", "signin-btn")
    assert result == 'internal:testid=[data-testid="signin-btn"]'


def test_playwright_grammar_role_with_name():
    result = to_playwright_grammar("role", "button", "New Incident")
    assert result == 'internal:role=button[name="New Incident"]'


def test_playwright_grammar_role_without_name():
    result = to_playwright_grammar("role", "button")
    assert result == "internal:role=button"


def test_playwright_grammar_text():
    result = to_playwright_grammar("text_based", 'text="Submit order"')
    assert result == 'internal:text="Submit order"'


def test_playwright_grammar_frame():
    result = to_playwright_grammar("frame", "")
    assert result == "internal:control=enter-frame"


def test_playwright_grammar_css_passthrough():
    result = to_playwright_grammar("css", "button.submit")
    assert result == "button.submit"


def test_deterministic_signals_testid_present():
    ev = _make_ev(
        tag="button",
        inner_text="Submit",
        role="button",
        aria_label="Submit",
        css='button[data-testid="submit-btn"]',
    )
    signals = generate_deterministic_signals(ev)
    engines = [s.engine for s in signals]
    assert "testid" in engines
    testid_sig = next(s for s in signals if s.engine == "testid")
    assert testid_sig.selector.startswith("internal:testid=")


def test_deterministic_signals_role_grammar():
    ev = _make_ev(
        tag="button",
        inner_text="New Incident",
        role="button",
        aria_label="New Incident",
        css="button.new-incident",
    )
    signals = generate_deterministic_signals(ev)
    role_sig = next((s for s in signals if s.engine == "role"), None)
    assert role_sig is not None
    assert role_sig.selector.startswith("internal:role=")


def test_deterministic_signals_durability_descending():
    ev = _make_ev(
        tag="button",
        inner_text="Submit",
        role="button",
        aria_label="Submit",
        css='button[data-testid="submit"]',
    )
    signals = generate_deterministic_signals(ev)
    durs = [s.durability for s in signals]
    assert durs == sorted(durs, reverse=True)


# ─── Phase 5: Frame context ───────────────────────────────────────────────────

def test_iframe_fingerprint_from_attrs():
    from conxa_compile.recorder.frame_utils import _iframe_fingerprint_from_attrs
    attrs = {
        "data-test-id": "reports-frame",
        "name": "reports",
        "title": "Reports iframe",
        "src": "https://app.example.com/reports",
    }
    fp = _iframe_fingerprint_from_attrs(attrs, "https://app.example.com/reports")
    assert len(fp["signals"]) >= 2
    engines = [s["engine"] for s in fp["signals"]]
    assert "testid" in engines
    # signals must be durability-ordered
    durs = [s["durability"] for s in fp["signals"]]
    assert durs == sorted(durs, reverse=True)


def test_iframe_fingerprint_has_url_pattern():
    from conxa_compile.recorder.frame_utils import _iframe_fingerprint_from_attrs
    attrs = {"name": "reports"}
    fp = _iframe_fingerprint_from_attrs(attrs, "https://app.example.com/reports/overview")
    assert fp["url_pattern"] != ""


def test_build_identity_bundle_populates_frame_chain():
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(tag="button", inner_text="Submit", role="button", aria_label="Submit")
    ev["frame"] = {
        "chain": [{
            "selector": 'iframe[name="reports"]',
            "fallback_selectors": [],
            "url": "https://app.example.com/reports",
            "url_pattern": "^https://app\\.example\\.com/reports$",
            "fingerprint": {
                "signals": [
                    {"engine": "name", "selector": 'iframe[name="reports"]', "durability": 0.95,
                     "orthogonality_class": "semantic-aria", "unique_at_compile": False, "source": "compiler"},
                ],
                "url": "https://app.example.com/reports",
                "url_pattern": "^https://app\\.example\\.com/reports$",
            },
        }]
    }
    bundle = _build_identity_bundle(ev)
    assert len(bundle.frame_chain) == 1
    assert len(bundle.frame_chain[0].signals) == 1
    assert bundle.frame_chain[0].signals[0].engine == "name"


def test_build_identity_bundle_frame_chain_requires_fingerprint():
    """Cutover: a legacy chain entry without a fingerprint is ignored (no signal synthesis)."""
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(tag="button", inner_text="Submit", role="button", aria_label="Submit")
    ev["frame"] = {
        "chain": [{"selector": 'iframe[id="main"]', "fallback_selectors": []}]
    }
    bundle = _build_identity_bundle(ev)
    assert bundle.frame_chain == []


# ─── Phase 6: Shadow DOM ──────────────────────────────────────────────────────

def test_shadow_path_populated_from_event():
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(tag="button", inner_text="Submit", role="button", aria_label="Submit")
    ev["shadow_path"] = [{"host": "my-widget", "mode": "open"}]
    bundle = _build_identity_bundle(ev)
    assert len(bundle.shadow_path) == 1
    assert bundle.shadow_path[0].host == "my-widget"
    assert bundle.shadow_path[0].mode == "open"


def test_shadow_path_detected_from_css_part():
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(
        tag="button",
        inner_text="Submit",
        role="button",
        aria_label="Submit",
        css="my-widget::part(submit-btn)",
    )
    bundle = _build_identity_bundle(ev)
    assert len(bundle.shadow_path) >= 1


def test_xpath_dropped_when_shadow_path_present():
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(
        tag="button",
        inner_text="Submit",
        role="button",
        aria_label="Submit",
        css="button.submit",
    )
    ev["shadow_path"] = [{"host": "my-widget", "mode": "open"}]
    bundle = _build_identity_bundle(ev)
    xpath_signals = [s for s in bundle.signals if s.engine == "xpath"]
    assert len(xpath_signals) == 0


def test_css_structural_signal_not_trusted_unique_when_shadow_sourced():
    # uniqueness_gate's dom_html-based count is blind to shadow-root internals (a standard HTML
    # serialization never contains them), so a css-structural selector for a shadow-sourced
    # element always counts 0 matches there and gets absent_ok=True-masked into
    # unique_at_compile=True regardless of how many real matches exist on the live page — see
    # FIX.md's WF-2-S-J investigation. The stamp must be corrected once shadow_path is known.
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(tag="button", inner_text="", aria_label="", css="button.button.button--primary")
    ev["shadow_path"] = [{"host": "sl-button", "mode": "open"}]
    bundle = _build_identity_bundle(ev)
    css_signals = [s for s in bundle.signals if s.engine in ("css-structural", "css-id")]
    assert css_signals
    assert all(not s.unique_at_compile for s in css_signals)


def test_no_shadow_allows_xpath():
    from conxa_compile.compiler.build import _build_identity_bundle
    ev = _make_ev(tag="button", inner_text="Submit", role="button", aria_label="Submit")
    # No shadow_path → xpath signal allowed through
    bundle = _build_identity_bundle(ev)
    # Should not crash; xpath may or may not be present depending on test input
    # (it's only present if selector_passes_filters passes)
    assert bundle.shadow_path == []


# ─── Phase 7: Hover support ───────────────────────────────────────────────────

def test_detect_hover_precondition_true():
    from conxa_compile.compiler.action_semantics import detect_hover_precondition
    prev = {"action": {"action": "hover"}, "target": {"inner_text": "Menu"}}
    cur = {"action": {"action": "click"}, "target": {"inner_text": "Settings"}}
    assert detect_hover_precondition(cur, prev) is True


def test_detect_hover_precondition_same_element():
    from conxa_compile.compiler.action_semantics import detect_hover_precondition
    prev = {"action": {"action": "hover"}, "target": {"inner_text": "Menu"}}
    cur = {"action": {"action": "click"}, "target": {"inner_text": "Menu"}}
    # Same element → not a reveal sequence
    assert detect_hover_precondition(cur, prev) is False


def test_detect_hover_precondition_no_prev():
    from conxa_compile.compiler.action_semantics import detect_hover_precondition
    cur = {"action": {"action": "click"}, "target": {"inner_text": "Settings"}}
    assert detect_hover_precondition(cur, None) is False


def test_detect_hover_precondition_prev_not_hover():
    from conxa_compile.compiler.action_semantics import detect_hover_precondition
    prev = {"action": {"action": "click"}, "target": {"inner_text": "Menu"}}
    cur = {"action": {"action": "click"}, "target": {"inner_text": "Settings"}}
    assert detect_hover_precondition(cur, prev) is False


def test_populate_hover_chains():
    from conxa_compile.compiler.build import _populate_hover_chains
    from conxa_core.models.skill_spec import SkillStep
    hover_ev = _make_ev(tag="div", inner_text="Account Menu", role="button", aria_label="Account Menu")
    hover_ev["action"]["action"] = "hover"
    click_ev = _make_ev(tag="a", inner_text="Settings", role="link", aria_label="Settings")
    click_ev["action"]["action"] = "click"
    steps = [SkillStep(action="hover"), SkillStep(action="click")]
    _populate_hover_chains(steps, [hover_ev, click_ev])
    assert len(steps[1].handler_hints.hover_chain) >= 1


def test_handler_hints_model():
    from conxa_core.models.skill_spec import HandlerHints, IdentitySignal
    sig = IdentitySignal(engine="role", selector='internal:role=button[name="Menu"]', durability=0.95, orthogonality_class="semantic-aria")
    hints = HandlerHints(hover_chain=[sig], virtualized_container="", allow_forced_action=False)
    assert len(hints.hover_chain) == 1
    assert hints.hover_chain[0].engine == "role"


def test_account_text_never_reaches_a_selector_or_fingerprint():
    from conxa_compile.compiler.selector_filters import drop_personal_target_text

    chip = "Google AccountCannon Boldcannonboldoff@gmail.com"
    ev = {"action": {"action": "upload_intent"}, "target": {"tag": "input", "label_text": chip, "aria_label": "Upload", "inner_text": ""}}
    out = drop_personal_target_text([ev])[0]
    assert out["target"]["label_text"] == "" and out["target"]["aria_label"] == "Upload"
    assert ev["target"]["label_text"] == chip, "the recording itself is not changed"
    long_label = drop_personal_target_text([{"target": {"label_text": "x" * 61}}])[0]
    assert long_label["target"]["label_text"] == ""
    assert drop_personal_target_text([{"target": {"label_text": "First name"}}])[0]["target"]["label_text"] == "First name"


def test_account_text_is_also_taken_out_of_the_recorders_own_selectors_and_surroundings():
    """The recorder writes the element's name into `selectors` and `context` as well as `target`: all three are cleaned,
    so the build-time check can never find what the compile-time scrub left in."""
    from conxa_compile.compiler.selector_filters import drop_personal_target_text

    ev = {"target": {"aria_label": "Account me@acme.io"},
          "selectors": {"aria": '[role="button"][name="Account me@acme.io"]', "css": "#acct"},
          "context": {"siblings": ["Settings", "me@acme.io"], "parent": "header"}}
    out = drop_personal_target_text([ev])[0]
    assert out["target"]["aria_label"] == "" and out["selectors"] == {"aria": "", "css": "#acct"}
    assert out["context"] == {"siblings": ["Settings"], "parent": "header"}


def test_a_sample_address_on_a_reserved_domain_is_page_text_not_an_account():
    """A placeholder like "name@example.com" (RFC 2606/6761 reserved domains) can belong to nobody: it stays a signal."""
    from conxa_compile.compiler.selector_filters import drop_personal_target_text, has_personal_email

    for sample in ("name@example.com", "you@mail.example.org", "a@b.test", "x@host.invalid"):
        assert not has_personal_email(sample), sample
    for real in ("cannon@gmail.com", "jo@example.community", "me@acme.io"):
        assert has_personal_email(real), real
    ev = {"target": {"placeholder": "name@example.com"}, "selectors": {"aria": '[role="textbox"][name="name@example.com"]'}}
    assert drop_personal_target_text([ev])[0] == ev
