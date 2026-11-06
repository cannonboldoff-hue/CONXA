"""The one place text becomes something a person reads.

A recorded name is a machine's word for a thing ("New folder Alt+C then F", "Report.pdf, (File)"). Anything shown
to a person in the Studio goes through here first, so a page's accessibility extras, shortcut hints and web
addresses never reach a chat message. Display only: ``target`` / ``literal`` values stay raw, because matching
and replay depend on them.
"""

from __future__ import annotations

import re

from conxa_compile.compiler.identity_bundle import strip_keyboard_shortcut_tail
from conxa_compile.compiler.selector_filters import scrub_email

# macOS symbol hints ("Save ⌘S"); strip_keyboard_shortcut_tail handles "Alt+C then F".
_MAC_SHORTCUT_TAIL = re.compile(r"\s+[⌘⌥⇧⌃]\S*$")
# The kind a page appends to an accessible name ("Report.pdf, (File)"). Needs the comma: "Delete (permanent)" is a
# real qualifier a person must still see.
_KIND_SUFFIX = re.compile(r"\s*,\s*\([^)]*\)\s*$")

# Things a person should never be shown. One table: a pattern per kind of machine word.
_MACHINE_WORDS = (
    re.compile(r"://|\bwww\.", re.I),                  # a web address
    re.compile(r"\b\w+=\S"),                           # part of a query string (pli=1)
    re.compile(r"\{\{|\}\}|`"),                        # a placeholder token, a code span
    re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b"),       # snake_case name
    re.compile(r"(?:^|\s)#[a-z][\w-]*|\[[a-z-]+[=\]]", re.I),   # a selector fragment
)


def collapse(text: object) -> str:
    return " ".join(str(text or "").split())


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def clean_name(text: object) -> str:
    """A recorded name without the page's extras (type suffix, keyboard-shortcut hint), whitespace collapsed."""
    out = collapse(text)
    out = _KIND_SUFFIX.sub("", out).strip() or out
    out = _MAC_SHORTCUT_TAIL.sub("", strip_keyboard_shortcut_tail(out)).strip() or out
    return out


def display_label(text: object, limit: int = 60) -> str:
    """What a person would call an item: ``clean_name``, cut at a word, never mid-word."""
    return _cut(clean_name(text), limit)


def person_text(value: object, limit: int) -> str:
    """Model text a person will read: one line, no email address (an account label carries one)."""
    return scrub_email(collapse(value))[:limit]


def has_machine_words(text: object) -> bool:
    s = str(text or "")
    return any(p.search(s) for p in _MACHINE_WORDS)
