"""One element, three descriptions: the recorder (bridge.js), the compiler (stable_hash.py,
identity_bundle._accessible_name) and the runtime (page_scripts.js::extractDescriptor) must say
the same thing about it. Cases live in runtime/test/fixtures/element_descriptor_cases.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conxa_compile.compiler.identity_bundle import _accessible_name
from conxa_compile.compiler.stable_hash import compute_stable_hash

from tests.test_recorder_bridge_js import _events, _install_bridge, browser, page  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[2] / "runtime"
CASES = json.loads((ROOT / "test" / "fixtures" / "element_descriptor_cases.json").read_text(encoding="utf-8"))
_SRC = (ROOT / "app" / "page_scripts.js").read_text(encoding="utf-8")
_DESCRIPTOR_JS = _SRC[_SRC.index("function extractDescriptor(el)"): _SRC.index("// RAF-stable check")]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_recorder_compiler_and_runtime_describe_the_element_alike(page, case):  # noqa: F811
    _install_bridge(page, case["html"])
    page.click(case["selector"])
    target = next(e["target"] for e in _events(page) if e.get("target"))
    live = page.evaluate(f"(sel) => {{ {_DESCRIPTOR_JS}\n return extractDescriptor(document.querySelector(sel)); }}", case["selector"])

    recorded_hash = compute_stable_hash({
        "tag": target.get("tag") or "",
        "parent_tag": "",
        "aria_label": target.get("aria_label") or "",
        "name": target.get("name") or "",
        "inner_text": (target.get("inner_text") or "")[:80],
        "value_label": target.get("value_label") or "",
        "attributes": target.get("attributes") or {},
    })
    assert hashlib.sha256(live["_hashPayload"].encode()).hexdigest() == recorded_hash

    named = _accessible_name(target)
    if named:
        assert live["name"] == named


def test_state_class_rule_is_one_rule_in_recorder_and_compiler():
    """bridge.js::isStateClass and selector_filters._STATE_CLASS_RE must stay the same rule."""
    import re

    from conxa_compile.compiler import selector_filters as sf

    bridge = (Path(__file__).resolve().parents[1].parent / "conxa-builder" / "python" / "conxa_compile" / "recorder" / "bridge.js").read_text(encoding="utf-8")
    words = "".join(re.findall(r'"([^"]*)"', bridge[bridge.index("const _STATE_WORDS"): bridge.index("const _STATE_CLASS_RE")]))
    assert words == sf._STATE_WORDS
    tail = re.search(r'"\[-_\]\(\?:([^)]*)\)\$"', bridge).group(1)
    assert f"[-_](?:{tail})$" in sf._STATE_CLASS_RE.pattern
