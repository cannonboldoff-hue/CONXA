"""An element whose words are the data is never found by those words (BUILD-101 (7), plan W-D).

A captured value ("Rs. 400", the first result's price) is read off an element whose text IS the value. Left in the
element's identity (its fingerprint text, a `text=` or role-name address, the hash name), the words that change on every
run decide whether the element is found: a different search and the step cannot find its own element. Here those words
are taken out of the identity, so the element is found by where it sits and by what is around it (its label, its
section's heading, its address in the page, its role); the words are only read afterwards.

Pure, no model. Never leaves a step with no way to be found: an address that does not name the words is kept, and when
every address names them the step keeps them all (a recording whose only handle is its text is better than none).
"""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.stable_hash import compute_stable_hash


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def is_value_text(text: Any) -> bool:
    """Text with a digit in it is a value ("Rs. 400"); one without is a label ("Order total") and a label is a fine way to
    find an element: it is the same line build.py::_extract_label draws between naming a capture and not naming it."""
    words = _norm(text)
    return len(words) >= 2 and bool(re.search(r"\d", words))


def strip_value_from_identity(step: Any, target: dict[str, Any]) -> bool:
    """Remove the element's own text (``target["inner_text"]``, as recorded) from ``step``'s identity bundle: its
    fingerprint words, every address that names it, and the hash name. Returns True when anything changed."""
    words = _norm(target.get("inner_text"))
    bundle = getattr(step, "identity_bundle", None)
    if bundle is None or not is_value_text(words):
        return False
    changed = False
    fp = bundle.fingerprint
    for field in ("inner_text", "name", "title", "alt", "value_label"):
        if words in _norm(getattr(fp, field, "")):
            setattr(fp, field, "")
            changed = True
    kept = [sig for sig in bundle.signals if words not in _norm(sig.selector)]
    if kept and len(kept) != len(bundle.signals):
        bundle.signals = kept
        changed = True
    if changed:
        # The same input the recorder's hash had, minus the words (compiler/build.py::_build_identity_bundle).
        bundle.stable_hash = compute_stable_hash({
            "tag": str(target.get("tag") or ""), "parent_tag": "", "aria_label": str(target.get("aria_label") or ""),
            "name": str(target.get("name") or ""), "inner_text": "", "value_label": str(target.get("value_label") or ""),
            "attributes": target.get("attributes") or {},
        })
    return changed
