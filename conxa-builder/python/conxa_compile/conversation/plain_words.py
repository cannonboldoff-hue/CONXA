"""The one gate for words a model writes that a person reads (a change card's reason, a recap, a step's name).

A model sometimes writes the system's own names into a sentence ("Change entity_binding.confirmed on step 2"). Code
wording is written from structure and is safe; a model's is checked here, and when it fails the caller falls back to
code wording. Pure.
"""

from __future__ import annotations

import re

_INTERNAL = re.compile(
    r"`"                                                     # code formatting
    r"|\{\{|\}\}"                                            # an input token
    r"|\b[a-z]+_[a-z0-9_]+\b"                                # snake_case: an internal name
    r"|\b(?:entity_binding|validation|identity_bundle|handler_hints|run_if|input_binding|for_each|branch|target)\.\w+"  # a field path
    r"|\[object Object\]|\bundefined\b|\bnull\b"             # an unrendered value
    r"|““|””"                                                # doubled quotes
)


def plain_ok(text: object) -> bool:
    """True when a person can read ``text`` without meeting an internal name."""
    return not _INTERNAL.search(str(text or ""))


def plain_or(text: object, fallback: str) -> str:
    """``text`` when it reads plainly, else ``fallback`` (words written by code)."""
    t = " ".join(str(text or "").split())
    return t if t and plain_ok(t) else fallback
