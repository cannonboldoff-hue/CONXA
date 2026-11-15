"""Deterministic-floor IdentitySignal generator (zero-LLM).

Generates the base signal set from recorded event attributes using Playwright's
native internal: grammar. LLM enrichment only runs on gaps not covered here.
"""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.selector_filters import (
    dedup_by_orthogonality,
    is_low_quality_anchor,
    resolves_to_nothing,
    salvage_deep_css_tail,
    selector_passes_filters,
    uniqueness_gate,
)
from conxa_compile.compiler.named_attrs import (
    backfill_attrs_from_snapshot,
    synthesize_attr_selector,
)
from conxa_compile.compiler.selector_score import rank_by_durability
from conxa_compile.compiler.selector_grammar import to_playwright_grammar
from conxa_core.models.skill_spec import IdentitySignal

_EXCLUDED_ROLES = frozenset({
    "none", "presentation", "div", "span", "input", "path", "svg", "g", "generic",
})

# Engines for which we skip the selector_passes_filters check (internal: grammar passes it)
_NATIVE_ENGINES = frozenset({"testid", "role", "text_based", "relational"})

# Elements whose inner text is the concatenation of their <option> children, not visible page
# text. Playwright's text engine does not match these by that content, and the live DOM reports
# it without the whitespace the recorder's innerText read inserts — so the recorded string can
# match neither the selector engine nor the runtime's own descriptor. Mirrored by bridge.js's
# OPTION_CONTENT_TAGS and resolver.js's OPTION_CONTENT_TAGS; all three must agree.
_OPTION_CONTENT_TAGS = frozenset({"select", "datalist", "optgroup", "combobox", "listbox"})

# Google Drive (and similar) append accelerator hints to a control's accessible name /
# inner text: "File upload Alt+C then U". Playwright's accessibility snapshot names the
# same node "File upload". An exact [name="…"] / internal:text="…" built from the recorded
# string then matches nothing at compile (role signal dropped) and nothing at replay.
_SHORTCUT_TAIL_RE = re.compile(
    r"\s+"
    r"(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command|Meta|Win|Windows|Option)\s*\+\s*)+[A-Za-z0-9]"
    r"(?:\s+then\s+(?:(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command|Meta|Win|Windows|Option)\s*\+\s*)+[A-Za-z0-9]|[A-Za-z0-9]))*"
    r"\s*$",
    re.IGNORECASE,
)


def strip_keyboard_shortcut_tail(text: str) -> str:
    """Drop a trailing accelerator hint; leave ordinary names untouched."""
    return _SHORTCUT_TAIL_RE.sub("", text).strip()


def _file_input_selector(target: dict[str, Any]) -> str:
    """``input[type="file"]``, narrowed by the first of name / accept / aria-label the recording has."""
    attrs = target.get("attributes") if isinstance(target.get("attributes"), dict) else {}
    for key in ("name", "accept", "aria-label"):
        value = str(attrs.get(key) or "").strip()
        if value and '"' not in value and len(value) <= 120:
            return f'input[type="file"][{key}="{value}"]'
    return 'input[type="file"]'


# A number that changes by itself: a count in a tab label ("Pull requests 58 (58)", "Issues 1.1k", "Inbox (274)").
_COUNT_TAIL_RE = re.compile(r"(?:\s*[\(\[]?\d[\d.,]*[kKmM]?[\)\]]?)+\s*$")
_ANY_NUMBER_RE = re.compile(r"\d")
_BARE_NUMBER_RE = re.compile(r"^[\d.,\s]+[kKmM]?$")


def strip_volatile_count(name: str) -> str:
    """The name without a trailing live count. Playwright matches a role's name as a substring, so the shorter name still
    finds the element after the count changes. A name that is only a number is left alone."""
    stripped = _COUNT_TAIL_RE.sub("", name).strip()
    return stripped if stripped else name


def _link_href_selector(target: dict[str, Any], selectors: dict[str, Any], dom_html: str | None) -> str | None:
    """``a[href="/path"]`` for a link whose address is unique on the recorded page. A link's destination is the most
    stable thing about it (labels carry counts and wording that change; the route does not). Needs the saved page: the
    recorder's attribute capture does not keep ``href``, and an address that cannot be shown to be unique is not used."""
    if not dom_html:
        return None
    tag = str(target.get("tag") or "").lower()
    role = str(target.get("role") or "").lower()
    if tag != "a" and role != "link":
        return None
    href = str(target.get("href") or "").strip()
    if not href:
        node_attrs = backfill_attrs_from_snapshot(selectors, dom_html) or {}
        href = str(node_attrs.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:")) or len(href) > 160 or '"' in href:
        return None
    sel = f'a[href="{href}"]'
    return sel if uniqueness_gate(sel, dom_html, absent_ok=False) else None


def generate_deterministic_signals(
    ev: dict[str, Any],
    dom_html: str | None = None,
    a11y_tree: dict[str, Any] | None = None,
) -> list[IdentitySignal]:
    """Return durability-ranked, orthogonality-deduplicated IdentitySignal list (no LLM).

    Produces signals in Playwright's native internal: grammar where applicable.
    Falls back to raw CSS/XPath for structural signals.

    `dom_html`/`a11y_tree` are the recorded page's snapshot (see
    conxa_core.storage.snapshots) used by uniqueness_gate to mark each signal
    unique_at_compile; omit when no snapshot was captured for this event.
    """
    target = ev.get("target") or {}
    semantic = ev.get("semantic") or {}
    selectors = ev.get("selectors") or {}

    candidates: list[tuple[str, str]] = []

    # 1. testid (highest durability) — preserves the exact attribute name (data-testid or data-test-id)
    testid_attr, data_testid = _extract_testid(selectors)
    if data_testid:
        candidates.append(("testid", to_playwright_grammar("testid", f'[{testid_attr}="{data_testid}"]')))

    # 1b. named-attr (uniqueness-gated role + stable data-* — not testid, not hashed classes)
    attr_target = target
    if not target.get("attributes") and dom_html:
        backfilled = backfill_attrs_from_snapshot(selectors, dom_html)
        if backfilled:
            attr_target = {**target, "attributes": backfilled}
    attr_sel = synthesize_attr_selector(attr_target, semantic, dom_html)
    if attr_sel:
        candidates.append(("attr", attr_sel))

    # 1c. a link's own address
    href_sel = _link_href_selector(target, selectors, dom_html)
    if href_sel and not any(c[1] == href_sel for c in candidates):
        candidates.append(("attr", href_sel))

    # 2. role + name (semantic-aria)
    role = str(semantic.get("role") or target.get("role") or "").strip()
    ax_name = _accessible_name(target)
    if role and ax_name and role.lower() not in _EXCLUDED_ROLES:
        candidates.append(("role", to_playwright_grammar("role", role, strip_volatile_count(ax_name))))

    # 3. text-based (visible-text)
    # Skipped for option-content elements: a <select>'s inner text is its concatenated <option>
    # list, which Playwright's text engine never matches the <select> by. Emitting it anyway put
    # a guaranteed-miss selector in the bundle's highest-durability slot and pushed the element's
    # real identity below it. bridge.js's buildTextSelector now refuses to record these at all;
    # this gate is what heals sessions recorded before that fix, without a re-record.
    raw_text = str(selectors.get("text_based") or "").strip()
    text_val = strip_keyboard_shortcut_tail(re.sub(r'^text=["\']?|["\']?$', "", raw_text).strip())
    tag_or_role = {str(target.get("tag") or "").lower(), str(semantic.get("role") or target.get("role") or "").lower()}
    # A bare number ("3" on a cart badge) names nothing: it is a count, and it is the same on a hundred elements.
    if text_val and not (tag_or_role & _OPTION_CONTENT_TAGS) and not _BARE_NUMBER_RE.match(text_val):
        candidates.append(("text_based", to_playwright_grammar("text", text_val)))

    # 4. relational from first *stable* (non-ephemeral) anchor phrase (spatial-anchor)
    anchor_phrases = [
        str(a.get("element") or "").strip()
        for a in (ev.get("anchors") or [])
        if a.get("element") and not a.get("ephemeral")
    ]
    stable_anchors = [p for p in anchor_phrases if p and not is_low_quality_anchor(p)]
    if stable_anchors and role and ax_name and role.lower() not in _EXCLUDED_ROLES:
        anchor = stable_anchors[0]
        rel_sel = f'internal:role={role}[name="{ax_name}"] >> right-of=internal:text="{anchor}"'
        candidates.append(("relational", rel_sel))

    # 4b. A file input is found by what it is, not by where it sits: a page that adds another input would move
    # a positional path. Narrowed by the first attribute that names it, when the recording has one.
    if str(semantic.get("input_type") or target.get("input_type") or "").lower() == "file":
        file_sel = _file_input_selector(attr_target)
        if file_sel and not any(c[1] == file_sel for c in candidates):
            candidates.append(("attr", file_sel))

    # 5. CSS (structural fallback)
    css_sel = str(selectors.get("css") or "").strip()
    if css_sel:
        # An over-deep chain is rejected wholesale by selector_passes_filters below. When its
        # last segment names the element by class/id/attribute, that tail is the element's only
        # durable identity — keep it instead of losing the channel entirely.
        if not selector_passes_filters(css_sel):
            css_sel = salvage_deep_css_tail(css_sel)
    if css_sel:
        engine = "css-id" if re.search(r"#[a-zA-Z][\w-]*", css_sel) else "css-structural"
        candidates.append((engine, css_sel))

    # 6. XPath (lowest durability)
    xpath_sel = str(selectors.get("xpath") or "").strip()
    if xpath_sel:
        candidates.append(("xpath", xpath_sel))

    testid_present = bool(data_testid)
    ranked = rank_by_durability(candidates, testid_present=testid_present)

    signals: list[IdentitySignal] = []
    for dur, engine, sel, oc in ranked:
        if engine not in _NATIVE_ENGINES and not selector_passes_filters(sel):
            continue
        # A role+name selector that matches nothing in the very page it was derived from is
        # fabricated, and shipping it wastes the two highest-durability slots on signals that
        # can only ever miss at replay. The relational signal is probed by its role base — the
        # `>> right-of=` chain is not statically countable, and it inherits the same name.
        if engine in ("role", "relational"):
            probe = sel.split(">>")[0].strip() if engine == "relational" else sel
            if resolves_to_nothing(probe, dom_html, a11y_tree):
                continue
        # absent_ok=True: a 0-match verdict here means "couldn't confirm", not "not unique" — see
        # uniqueness_gate's docstring. Without it, snapshot gaps (e.g. a modal captured before it
        # mounted) wrongly stamped real, durable selectors as unverified.
        unique = uniqueness_gate(sel, dom_html, a11y_tree, absent_ok=True)
        # Known to match several elements on the recorded page (a zero count was let through above as "can't
        # verify"): it must not outrank a signal that does pick one. Text and names that carry a digit run follow
        # a number that changes by itself, so they rank below the same signal without one.
        if not unique and dur > 0.35:
            dur = 0.35
        elif engine in ("text_based", "role", "relational") and _ANY_NUMBER_RE.search(sel) and dur > 0.6:
            dur = 0.6
        signals.append(IdentitySignal(
            engine=engine,
            selector=sel,
            durability=dur,
            orthogonality_class=oc,
            unique_at_compile=unique,
            source="compiler",
        ))

    return dedup_by_orthogonality(signals)


# label_text is only a real accessible name for a form control (a <label for=…> names its
# input). For anything else it is captureAssociatedLabel's last-resort "nearest surrounding
# text" walk — which named a bare avatar <img> after the paragraph above it and produced
# `internal:role=img[name="Hover over the image for additional information"]`, a name
# Playwright never computes, so the whole 0.95 signal matched nothing at replay.
# runtime/app/resolver.js's scoreCandidate already refuses to score on label_text for the
# same reason; emitting selectors from it here was the other half of that mismatch.
# Tag OR role — a control named by its label is a form control under either name, and
# runtime/app/cascade.js's a11yRecoveryName applies the identical gate so compile-time
# naming and recovery-time naming cannot disagree.
_LABELLED_TAGS = frozenset({
    "input", "select", "textarea",
    "textbox", "searchbox", "combobox", "listbox", "spinbutton", "checkbox", "radio",
})

# ARIA "name from content" roles — the only roles the accessibility tree actually derives a
# name from an element's own inner text. Everything else (combobox, listbox, textbox,
# searchbox, spinbutton, slider, img, region, a bare <select>...) computes its name from
# aria-label/aria-labelledby/<label> only — never from concatenating its own contents, so an
# inner_text candidate for those is fabricated, not read: react-datepicker's year <select> has
# no name, and its <option> text ("1900 1901 1902 ... 1915") is not an accessible name any
# browser or Playwright would ever compute for it. Same class of bug the _LABELLED_TAGS gate
# above already fixed for label_text; this is the inner_text half of the same function.
_NAME_FROM_CONTENT_ROLES = frozenset({
    "button", "link", "heading", "cell", "gridcell", "columnheader", "rowheader",
    "checkbox", "radio", "menuitem", "menuitemcheckbox", "menuitemradio",
    "option", "tab", "treeitem", "switch", "tooltip",
})


# Visible text longer than this is a paragraph, not a name. Pinned by the "text over 80 characters" row of
# runtime/test/fixtures/fingerprint_name_cases.json; runtime/app/accessible_name.js uses the same number.
_MAX_NAME_CHARS = 80


def _accessible_name(target: dict[str, Any]) -> str:
    """Element's accessible name, or "" when it genuinely has none.

    "" is a valid answer: no name means no role signal, and the bundle falls through to
    structural identity. That is strictly better than inventing a name that resolves to
    nothing.
    """
    # `name` is deliberately NOT a candidate: it's the HTML form-field attribute, never an ARIA
    # accessible-name source. For an ordinary input it's usually just the accessible name's exact
    # duplicate by coincidence (a coincidence that stopped being harmless once selectors moved to
    # matching on it), but for a radio/checkbox GROUP it's the shared group key every sibling
    # carries -- `role=radio[name="gender"]` matches nothing (Playwright's role selector matches
    # the accessible name "Male", not the group's `name`) or every option in the group, never the
    # one that was recorded. See CLAUDE.md's multiple-choice plan.
    candidates = [
        target.get("aria_label"),
        target.get("alt"),
        target.get("title"),
        target.get("placeholder"),
    ]
    role_or_tag = {str(target.get("tag") or "").lower(), str(target.get("role") or "").lower()}
    if role_or_tag & _NAME_FROM_CONTENT_ROLES:
        inner_text = str(target.get("inner_text") or "").strip()
        # No [:80] truncation: a truncated name inside an exact-match [name="…"] selector is a
        # guaranteed miss. 80 chars is instead a plausibility ceiling on THIS candidate only —
        # inner_text this long (react-datepicker's whole option list concatenated, e.g.) reads
        # as fabricated content, not a real accessible name, so it's dropped rather than
        # shipped half-cut. aria_label/alt/title/placeholder/label_text below are author-set,
        # not concatenated content, and stay uncapped.
        if inner_text and len(inner_text) <= _MAX_NAME_CHARS:
            candidates.append(inner_text)
    if (
        str(target.get("tag") or "").lower() in _LABELLED_TAGS
        or str(target.get("role") or "").lower() in _LABELLED_TAGS
    ):
        candidates.append(target.get("label_text"))
    for value in candidates:
        name = strip_keyboard_shortcut_tail(str(value or "").strip())
        if name:
            return name
    return ""


_TESTID_RE = re.compile(r'(data-test(?:-?id)?)=["\']?([^"\'>\s\]]+)')


def _extract_testid(selectors: dict[str, Any]) -> tuple[str, str]:
    """Returns (attr_name, value) for the first testid attr found, or ('', '')."""
    for key in ("css", "aria"):
        s = str(selectors.get(key) or "")
        m = _TESTID_RE.search(s)
        if m:
            return m.group(1), m.group(2)
    return "", ""
