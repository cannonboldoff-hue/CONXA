"""Deterministic selector portfolio scoring (stability, uniqueness heuristics, policy weights)."""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.selector_filters import is_dynamic_id, selector_passes_filters

# Lower index = higher reliability; used as tie-breaker when scores are equal.
_KIND_PRIORITY: dict[str, int] = {
    "aria": 0,
    "label": 1,
    "name": 2,
    "text_based": 3,
    "css": 4,
    "role": 5,
    "xpath": 6,
}


def score_selector_row(kind: str, value: str, policy: dict[str, Any]) -> float:
    if not (value or "").strip():
        return -1.0
    v = value.strip()
    if not selector_passes_filters(v):
        return -1.0
    sel_policy = policy.get("selectors") if isinstance(policy.get("selectors"), dict) else {}
    base_map = sel_policy.get("kind_base_scores") or {}
    base = float(base_map.get(kind, 50))
    score = base
    if kind == "xpath":
        pen = float(sel_policy.get("xpath_length_penalty_per_50", 3))
        score -= (max(0, len(v) - 50) // 50) * pen
    if kind == "css" and is_dynamic_id(v):
        score -= 12.0
    if len(v) > 200:
        score -= 5.0
    return round(score, 4)


def rank_selectors_scored(selectors: dict[str, Any], policy: dict[str, Any]) -> list[tuple[float, str, str]]:
    """Return sorted list of (score, kind, value) descending."""
    rows: list[tuple[float, str, str]] = []
    mapping = [
        ("aria", str(selectors.get("aria") or "").strip()),
        ("name", str(selectors.get("name") or "").strip()),
        ("text_based", str(selectors.get("text_based") or "").strip()),
        ("role", str(selectors.get("role") or "").strip()),
        ("css", str(selectors.get("css") or "").strip()),
        ("xpath", str(selectors.get("xpath") or "").strip()),
    ]
    seen: set[str] = set()
    for kind, val in mapping:
        if not val:
            continue
        s = score_selector_row(kind, val, policy)
        if s < 0 or val in seen:
            continue
        seen.add(val)
        rows.append((s, kind, val))
    rows.sort(key=lambda r: (r[0], -_KIND_PRIORITY.get(r[1], 99)), reverse=True)
    return rows


def ordered_selector_strings(selectors: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    return [v for _, _, v in rank_selectors_scored(selectors, policy)]


def rank_labeled_selector_candidates(
    candidates: list[tuple[str, str]], policy: dict[str, Any]
) -> list[str]:
    """Score and order arbitrary selector strings (e.g. stable-synthesis rows) by policy weights."""
    rows: list[tuple[float, str, str]] = []
    seen: set[str] = set()
    for kind, val in candidates:
        v = (val or "").strip()
        if not v:
            continue
        s = score_selector_row(kind, v, policy)
        if s < 0.0 or v in seen:
            continue
        seen.add(v)
        rows.append((s, kind, v))
    rows.sort(key=lambda r: (r[0], -_KIND_PRIORITY.get(r[1], 99)), reverse=True)
    return [r[2] for r in rows]


# ---------------------------------------------------------------------------
# Durability scoring (Phase 1 — Final Selector Architecture)
# ---------------------------------------------------------------------------

# Inverted+normalized Playwright cost model: higher = more durable across app changes.
_ENGINE_BASE_DURABILITY: dict[str, float] = {
    "testid": 0.99,
    "attr": 0.96,
    "role": 0.95,
    "aria": 0.95,
    "label": 0.95,
    "name": 0.95,
    "text": 0.85,
    "text_based": 0.85,
    "relational": 0.75,
    "css-id": 0.45,
    "css": 0.30,
    "css-structural": 0.30,
    "xpath": 0.10,
}

# Orthogonality class per engine — signals in different classes are independent axes of identity.
_ENGINE_ORTHOGONALITY: dict[str, str] = {
    "testid": "test-contract",
    "attr": "named-attr",
    "role": "semantic-aria",
    "aria": "semantic-aria",
    "label": "semantic-aria",
    "name": "semantic-aria",
    "text": "visible-text",
    "text_based": "visible-text",
    "relational": "spatial-anchor",
    "css-id": "structural",
    "css": "structural",
    "css-structural": "structural",
    "xpath": "structural",
}

_POSITIONAL_RE = re.compile(r":nth-child\(|:nth-of-type\(|\[\d+\]")


def durability_score(
    engine: str,
    selector: str,
    survival_prior: float = 1.0,
    testid_present: bool = False,
) -> float:
    """Compute durability score in [0, 1] for a selector signal.

    Formula: base_durability(engine) × survival_prior × stability_adjustments
    """
    base = _ENGINE_BASE_DURABILITY.get(engine, 0.30)
    adj = 1.0
    if is_dynamic_id(selector):
        adj *= 0.1
    if _POSITIONAL_RE.search(selector):
        adj *= 0.1
    # When testid is present on the element, non-testid signals are slightly downgraded
    # because the testid signal should be preferred.
    if testid_present and engine != "testid":
        adj *= 0.95
    return round(base * survival_prior * adj, 4)


def tag_orthogonality_class(engine: str) -> str:
    """Return the orthogonality class for an engine type."""
    return _ENGINE_ORTHOGONALITY.get(engine, "structural")


def confidence_from_signal_rows(signals: list[dict[str, Any]]) -> float:
    """Dict-based twin of ``compiler.build._confidence_from_identity_bundle``.

    Same formula (strongest signal's durability, discounted when signals don't span >=2
    orthogonality classes or when none matched uniquely at compile time), but works off the
    persisted/serialized signal dicts the editor DTOs carry instead of the in-memory
    IdentityBundle/IdentitySignal pydantic objects used during compile.
    """
    rows = [s for s in signals or [] if isinstance(s, dict)]
    if not rows:
        return 0.0
    # What counts is whether the element can be found uniquely: the best signal that did match
    # exactly one element on the recorded page. Only with none of those is the strongest-looking
    # signal discounted.
    unique = [float(s.get("durability") or 0.0) for s in rows if s.get("unique_at_compile")]
    base = max(unique) if unique else max(float(s.get("durability") or 0.0) for s in rows)
    unique_factor = 1.0 if unique else 0.6
    classes = {str(s.get("orthogonality_class") or "") for s in rows}
    classes.discard("")
    # One kind of signal is a penalty only when that signal is weak; a readable id that matched
    # one element (css-id, 0.45) is a strong signal on its own.
    ortho_factor = 1.0 if len(classes) >= 2 or base >= _STRONG_SINGLE_SIGNAL else 0.7
    return round(min(1.0, base * ortho_factor * unique_factor), 3)


# A signal below this durability is a dynamic id, a positional path or a bare xpath: it may match
# one element today but says nothing lasting about which one.
USABLE_SIGNAL_MIN_DURABILITY = 0.25
_STRONG_SINGLE_SIGNAL = 0.45


def has_usable_unique_signal(signals: list[dict[str, Any]]) -> bool:
    """True when at least one signal matched exactly one element on the recorded page and is not
    a dynamic, positional or bare-xpath address. A step without one cannot be trusted to find its
    element again."""
    return any(
        isinstance(s, dict) and s.get("unique_at_compile")
        and float(s.get("durability") or 0.0) >= USABLE_SIGNAL_MIN_DURABILITY
        for s in signals or []
    )


def rank_by_durability(
    candidates: list[tuple[str, str]],
    survival_prior: float = 1.0,
    testid_present: bool = False,
) -> list[tuple[float, str, str, str]]:
    """Rank (engine, selector) pairs by durability score descending.

    Returns list of (durability, engine, selector, orthogonality_class).
    Deduplicates by selector string.
    """
    rows: list[tuple[float, str, str, str]] = []
    seen: set[str] = set()
    for engine, sel in candidates:
        sel = (sel or "").strip()
        if not sel or sel in seen:
            continue
        seen.add(sel)
        d = durability_score(engine, sel, survival_prior=survival_prior, testid_present=testid_present)
        oc = tag_orthogonality_class(engine)
        rows.append((d, engine, sel, oc))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows
