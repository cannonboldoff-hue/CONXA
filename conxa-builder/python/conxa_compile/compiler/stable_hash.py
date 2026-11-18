"""Compute a dynamic-class-stripped SHA-256 stable hash for element identity."""

from __future__ import annotations

import hashlib
from typing import Any

_DYNAMIC_CLASS_TOKENS = frozenset({
    "focus", "hover", "active", "focus-visible", "focus-within",
    "loading", "animating", "transitioning", "selected", "disabled",
    "expanded", "collapsed", "open", "closed", "checked", "pressed",
    "dragging", "dragged", "dropping",
})
_DYNAMIC_CLASS_PREFIXES = ("is-", "has-", "js-", "animate-", "transition-", "state-")
_SKIP_ATTRS = frozenset({
    "class", "style", "tabindex",
    "aria-expanded", "aria-selected", "aria-checked", "aria-disabled",
    "aria-pressed", "aria-current", "aria-busy",
    "data-state", "data-active", "data-focus", "data-open",
})
# Identity emit uses this same skip set (compiler/named_attrs.py). Add a key here
# only if it is transient state, never if it is durable identity (data-key, data-command).


def strip_dynamic_classes(classes: list[str]) -> list[str]:
    stable = []
    for c in classes:
        lc = c.lower()
        if lc in _DYNAMIC_CLASS_TOKENS:
            continue
        if any(lc.startswith(p) for p in _DYNAMIC_CLASS_PREFIXES):
            continue
        stable.append(c)
    return stable


def compute_stable_hash(element_data: dict[str, Any]) -> str:
    """Return SHA256 of stable element identity: tag_path + sorted_static_attrs + AX_name.

    Dynamic CSS classes (focus/hover/active/animation/transition/loading/is-*) are stripped
    before hashing so the hash survives transient DOM state changes.
    """
    tag = str(element_data.get("tag") or "").lower().strip()
    parent_tag = str(element_data.get("parent_tag") or "").lower().strip()
    tag_path = f"{parent_tag} > {tag}" if parent_tag else tag

    raw_attrs = element_data.get("attributes") or {}
    static_attrs: dict[str, str] = {}
    if isinstance(raw_attrs, dict):
        for k, v in raw_attrs.items():
            k_lower = k.lower()
            if k_lower in _SKIP_ATTRS:
                continue
            if k_lower == "class":
                classes = str(v or "").split()
                stable = strip_dynamic_classes(classes)
                if stable:
                    static_attrs["class"] = " ".join(sorted(stable))
            else:
                static_attrs[k_lower] = str(v or "")
    sorted_attrs = "&".join(f"{k}={v}" for k, v in sorted(static_attrs.items()))

    ax_name = (
        str(element_data.get("aria_label") or "")
        or str(element_data.get("name") or "")
        or str(element_data.get("value_label") or "")
        or str(element_data.get("inner_text") or "")[:80]
    ).strip()

    payload = f"{tag_path}|{sorted_attrs}|{ax_name}"
    return hashlib.sha256(payload.encode()).hexdigest()
