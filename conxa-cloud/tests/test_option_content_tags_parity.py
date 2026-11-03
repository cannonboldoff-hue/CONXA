"""The three "this element's text is its option list" sets must stay identical.

identity_bundle.py (compile), bridge.js (recording) and resolver.js (replay) each carry a copy. A
copy that drifts makes the recorded text, the compiled selector and the live element disagree.
"""
from __future__ import annotations

import re
from pathlib import Path

from conxa_compile.compiler.identity_bundle import _OPTION_CONTENT_TAGS

ROOT = Path(__file__).resolve().parents[2]


def _words(source: Path, pattern: str) -> set[str]:
    match = re.search(pattern, source.read_text("utf-8"))
    assert match, f"OPTION_CONTENT_TAGS not found in {source.name}"
    return set(re.findall(r"[a-z]+", match.group(1)))


def test_bridge_js_matches_identity_bundle() -> None:
    bridge = _words(ROOT / "conxa-builder/python/conxa_compile/recorder/bridge.js",
                    r"OPTION_CONTENT_TAGS\s*=\s*\{([^}]*)\}")
    assert bridge - {"1"} == set(_OPTION_CONTENT_TAGS)


def test_resolver_js_matches_identity_bundle() -> None:
    resolver = _words(ROOT / "runtime/app/resolver.js", r"OPTION_CONTENT_TAGS\s*=\s*new Set\(\[([^\]]*)\]")
    assert resolver == set(_OPTION_CONTENT_TAGS)
