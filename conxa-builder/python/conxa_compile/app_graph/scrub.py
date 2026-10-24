"""The only door text goes through on its way into an app map.

The map keeps the SHAPE of an app (its control labels, page titles as a few words, routes), never
what a person typed or what a page showed them. Everything here errs toward dropping: a label that
is missing from the map costs a little polish, one that leaks a file name or an email address is a
privacy bug. Mirrors the intent of ``outcome_fork.message_tokens`` (quoted text and digits out).
"""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.outcome_fork import message_tokens

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+")
# "double", “curly”, ‘curly’ and 'single' quotes -- single only when it is a real quote, not the
# apostrophe in "Don't save".
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”|‘[^’]*’|(?<!\w)\'[^\']*\'(?!\w)')
# report.pdf, example.com, v1.2 -- a word with a dot-extension is a file or a domain, not a label.
_FILEISH = re.compile(r"\S+\.[A-Za-z0-9]{1,5}\b")
_HAS_DIGIT = re.compile(r"\d")

MAX_LABEL_WORDS = 5


def scrub_text(text: Any, max_len: int = 60) -> str:
    """Words of `text` with URLs, emails, quoted spans, file-like words and any word containing a
    digit removed. Whitespace collapsed, capped at `max_len`."""
    s = str(text or "")
    for rx in (_URL, _EMAIL, _QUOTED, _FILEISH):
        s = rx.sub(" ", s)
    words = [w for w in s.split() if not _HAS_DIGIT.search(w)]
    return " ".join(words)[:max_len].strip()


def scrub_label(text: Any) -> str | None:
    """A control label ("Upload", "Keep both"). None when nothing is left, when what is left is
    long enough to be content rather than a label, or when scrubbing had to remove anything at all:
    a label that carried a file name, a number or a quoted name is content, and what remains of it
    ("MB)") is worth nothing."""
    s = scrub_text(text, 40)
    if not s or len(s.split()) > MAX_LABEL_WORDS or s != " ".join(str(text or "").split()):
        return None
    return s


def scrub_tokens(text: Any, limit: int = 8) -> list[str]:
    """A page title as a handful of lowercase words."""
    return message_tokens(scrub_text(text, 120))[:limit]


def safe_element_name(step: dict[str, Any]) -> str | None:
    """The label of a step's element, if it can be kept without carrying data.

    Dropped entirely when the step is bound to a table row (the row text IS the data), and when
    the element is bound to an input only the field's own labels may be used -- its visible text
    can be the very value the person chose. A name that contains the step's literal value is
    dropped too."""
    if step.get("entity_binding"):
        return None
    fp = ((step.get("identity_bundle") or {}).get("fingerprint")) or {}
    sources = [fp.get("aria_label"), fp.get("label_text")]
    if not step.get("input_binding"):
        sources.append(fp.get("inner_text"))
    sources += [fp.get("placeholder"), fp.get("title"), fp.get("alt"), fp.get("name")]
    value = step.get("value")
    literal = value.strip().lower() if isinstance(value, str) and "{{" not in value else ""
    for raw in sources:
        name = scrub_label(raw)
        if not name:
            continue
        if literal and literal in name.lower():
            return None
        return name
    return None
