"""Compute a dynamic-class-stripped SHA-256 stable hash for element identity."""

from __future__ import annotations

import hashlib
from typing import Any

from conxa_compile.compiler.selector_filters import is_dynamic_id

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
    # Opt-in (BUILD-104 (1)): only a caller that passes `form_field` gets it, so every other hash stays byte-identical.
    field = form_field_part(tag, element_data.get("form_field"))
    if field:
        payload += f"|field|{field}"
    return hashlib.sha256(payload.encode()).hexdigest()


_FORM_FIELD_TAGS = frozenset({"input", "textarea", "select"})


def form_field_part(tag: str, field: dict[str, Any] | None) -> str:
    """A form field's own markup, so two plain boxes differ: its id (unless handed out per render), form-field name,
    placeholder and, for an input, type. Never what was typed. Mirrors runtime/app/page_scripts.js::extractDescriptor;
    runtime/test/fixtures/element_descriptor_cases.json pins both."""
    if tag not in _FORM_FIELD_TAGS or not isinstance(field, dict):
        return ""
    el_id = str(field.get("id") or "").strip()
    parts = [
        ("id", "" if is_dynamic_id(f"#{el_id}") else el_id),
        ("name", str(field.get("name") or "").strip()),
        ("placeholder", str(field.get("placeholder") or "").strip()),
        ("type", (str(field.get("type") or "").strip().lower() or "text") if tag == "input" else ""),
    ]
    return "&".join(f"{k}={v}" for k, v in parts if v)


def stable_hash_input(target: dict[str, Any], semantic: dict[str, Any] | None = None) -> dict[str, Any]:
    """compute_stable_hash's input for one recorded event (bridge.js target + semantic)."""
    return {
        "tag": str(target.get("tag") or ""),
        "parent_tag": "",
        "aria_label": str(target.get("aria_label") or ""),
        "name": str(target.get("name") or ""),
        "inner_text": str(target.get("inner_text") or "")[:80],
        "value_label": str(target.get("value_label") or ""),
        "attributes": target.get("attributes") or {},
        "form_field": {
            "id": target.get("id"), "name": target.get("name"), "placeholder": target.get("placeholder"),
            "type": (semantic or {}).get("input_type"),
        },
    }
