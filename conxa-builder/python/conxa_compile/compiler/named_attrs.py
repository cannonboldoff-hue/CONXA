"""Named-attribute identity: stability gate + uniqueness-tightened CSS synthesis.

Recorder stores a bounded `target.attributes` map. This module is the compiler's
strict half: it turns those attributes into at most one IdentityBundle `attr`
signal. `data-testid*` stays the testid engine. Not a per-app special case —
`data-key="19"` on a Drive menuitem is the same rule as any uniqueness-gated
named attribute.

Skip-list for state/ephemeral keys is `stable_hash._SKIP_ATTRS` so hash and emit
cannot drift.
"""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.selector_filters import _parse_soup, uniqueness_gate
from conxa_compile.compiler.stable_hash import _SKIP_ATTRS

_MAX_VALUE_LEN = 64
_TESTID_KEYS = frozenset({"data-testid", "data-test-id", "data-test", "data-cy"})
_KEY_PREFERENCE = ("data-key", "data-command", "data-action", "data-value")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LONG_HEX_RE = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)
_HASH_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,}$")


def is_stable_attr_value(value: str) -> bool:
    v = (value or "").strip()
    if not v or len(v) > _MAX_VALUE_LEN:
        return False
    if _UUID_RE.match(v) or _LONG_HEX_RE.match(v):
        return False
    if " " not in v and _HASH_TOKEN_RE.match(v) and re.search(r"[A-Za-z]", v) and re.search(r"\d", v):
        # Mixed alnum token long enough to be a generated hash, not a command enum ("19").
        if re.search(r"[A-Z]", v) and re.search(r"[a-z]", v):
            return False
    return True


# What an element is doing right now, not what it is: a mid-animation or active/selected marker is unique at the moment of
# recording and gone at replay. By name (`data-transitioning`) and by value (`active`, `true`, `open`).
_STATE_KEY_RE = re.compile(
    r"^data-(?:state|status|active|current|selected|checked|disabled|expanded|open|closed|hidden|visible|loading|busy|"
    r"focus(?:ed)?|hover(?:ed)?|pressed|highlighted|transition(?:ing)?|animating|dragging|valid|invalid)$"
)
_STATE_VALUES = frozenset({"true", "false", "active", "inactive", "open", "closed", "on", "off", "loading", "loaded",
                           "transitioning", "selected", "checked", "disabled", "enabled", "visible", "hidden", "current"})


# How a widget library wires an element up (Bootstrap's data-toggle="tooltip"), not which element it is: shared by
# every tooltip or dropdown on the page (WF-3 A: every row's bin icon carries data-toggle="tooltip").
_WIRING_KEY_RE = re.compile(r"^data-(?:bs-)?(?:toggle|placement|trigger|dismiss|container|delay|animation|html|original-title)$")


def is_identity_attr_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k or k in _SKIP_ATTRS or k in _TESTID_KEYS or _STATE_KEY_RE.match(k) or _WIRING_KEY_RE.match(k):
        return False
    if k == "role":
        return True
    return k.startswith("data-")


def filter_identity_attrs(raw: dict[str, Any] | None) -> dict[str, str]:
    """Strict map: identity-bearing keys with stable values. Role is kept for qualification."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        k = str(key or "").strip().lower()
        if not is_identity_attr_key(k):
            continue
        v = str(val or "").strip()
        if not is_stable_attr_value(v) or (k != "role" and v.lower() in _STATE_VALUES):
            continue
        out[k] = v
    return out


def _css_attr(name: str, value: str) -> str:
    esc = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'[{name}="{esc}"]'


def _data_keys(attrs: dict[str, str]) -> list[str]:
    keys = [k for k in attrs if k.startswith("data-")]
    preferred = [k for k in _KEY_PREFERENCE if k in attrs]
    rest = sorted(k for k in keys if k not in preferred)
    return preferred + rest


def _ladder(tag: str, role: str, name: str, value: str) -> list[str]:
    attr = _css_attr(name, value)
    steps = [attr]
    if role:
        steps.append(f'{_css_attr("role", role)}{attr}')
    tag_c = (tag or "").strip().lower()
    if tag_c and role:
        steps.append(f'{tag_c}{_css_attr("role", role)}{attr}')
    elif tag_c:
        steps.append(f"{tag_c}{attr}")
    return steps


def backfill_attrs_from_snapshot(
    selectors: dict[str, Any] | None, dom_html: str | None
) -> dict[str, str] | None:
    """Recover a pre-fix recording's own attributes from its saved DOM snapshot.

    Older recordings never populated `target.attributes` (bridge.js gained that
    capture later); the full page HTML was always saved regardless. Locate the
    same node via the selectors already captured for this exact event and read
    its live attributes off the snapshot, so recompiling an old session doesn't
    require re-recording it.
    """
    if not dom_html or not selectors:
        return None
    node = None
    css = str(selectors.get("css") or "").strip()
    if css:
        try:
            matches = _parse_soup(dom_html).select(css)
        except Exception:  # noqa: BLE001 — selector grammar bs4 can't parse
            matches = []
        if len(matches) == 1:
            node = matches[0]
    if node is not None:
        return {
            str(k): (v if isinstance(v, str) else " ".join(v))
            for k, v in node.attrs.items()
        }
    xpath = str(selectors.get("xpath") or "").strip()
    if not xpath:
        return None
    from lxml import etree  # type: ignore — hard pinned dependency, ImportError propagates

    try:
        matches = etree.HTML(dom_html).xpath(xpath)
    except Exception:  # noqa: BLE001 — malformed xpath / snapshot
        return None
    if len(matches) != 1:
        return None
    return {str(k): str(v) for k, v in matches[0].attrib.items()}


def synthesize_attr_selector(
    target: dict[str, Any],
    semantic: dict[str, Any] | None = None,
    dom_html: str | None = None,
) -> str | None:
    """Tightest uniqueness-gated CSS for a named data-* attribute, or None.

    Ladder: [data-key="19"] → [role="menuitem"][data-key="19"] → li[role="menuitem"][data-key="19"].
    `absent_ok=True`: a missing snapshot (portal menu not in the HTML blob) must not
    drop a real attribute.
    """
    semantic = semantic or {}
    attrs = filter_identity_attrs((target or {}).get("attributes"))
    data_keys = _data_keys(attrs)
    if not data_keys:
        return None
    tag = str((target or {}).get("tag") or "").strip().lower()
    role = str(
        attrs.get("role")
        or semantic.get("role")
        or (target or {}).get("role")
        or ""
    ).strip()
    # A selector that picks exactly one node on the recorded page is verified. One that picks NONE is not: the
    # `role` in the ladder's second and third rungs is usually the implicit role of the tag (`<a href>` is a link
    # but has no role attribute), so those rungs match nothing in the saved HTML and used to pass as "can't verify"
    # (GitHub: `[role="link"][data-discover="true"]` was kept for every tab link, then matched them all live while a
    # distinguishing `data-tab-item` sat unused). So look for a verified one across every key first. Only the plain
    # `[data-key="value"]` rung, built from an attribute the element really has, may stand on "matched nothing"
    # (the element can be missing from the snapshot, e.g. a menu mounted after it was taken).
    unverified: str | None = None
    for name in data_keys:
        for rung, sel in enumerate(_ladder(tag, role, name, attrs[name])):
            if not dom_html:
                return sel
            if uniqueness_gate(sel, dom_html, absent_ok=False):
                return sel
            if rung == 0 and unverified is None and uniqueness_gate(sel, dom_html, absent_ok=True):
                unverified = sel
    return unverified
