"""One element, three descriptions: the recorder (bridge.js), the compiler (stable_hash.py,
identity_bundle._accessible_name) and the runtime (page_scripts.js::extractDescriptor) must say
the same thing about it. Cases live in runtime/test/fixtures/element_descriptor_cases.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conxa_compile.compiler.identity_bundle import _accessible_name
from conxa_compile.compiler.stable_hash import compute_stable_hash, stable_hash_input

from tests.test_recorder_bridge_js import _events, _install_bridge, browser, page  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[2] / "runtime"
CASES = json.loads((ROOT / "test" / "fixtures" / "element_descriptor_cases.json").read_text(encoding="utf-8"))
_SRC = (ROOT / "app" / "page_scripts.js").read_text(encoding="utf-8")
_DESCRIPTOR_JS = _SRC[_SRC.index("function extractDescriptor(el)"): _SRC.index("// RAF-stable check")]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _describe(page, selector):  # noqa: F811
    """Click the element; return (recorded event, compiled hash, compiled pre-field hash, runtime descriptor)."""
    page.click(selector)
    ev = [e for e in _events(page) if e.get("target")][-1]
    live = page.evaluate(f"(sel) => {{ {_DESCRIPTOR_JS}\n return extractDescriptor(document.querySelector(sel)); }}", selector)
    hash_input = stable_hash_input(ev["target"], ev.get("semantic"))
    legacy_input = {k: v for k, v in hash_input.items() if k != "form_field"}  # what packs built before carry
    return ev, compute_stable_hash(hash_input), compute_stable_hash(legacy_input), live


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_recorder_compiler_and_runtime_describe_the_element_alike(page, case):  # noqa: F811
    _install_bridge(page, case["html"])
    ev, recorded_hash, legacy_hash, live = _describe(page, case["selector"])

    assert _sha(live["_hashPayload"]) == recorded_hash
    # An old pack's hash is still what the runtime computes as the legacy one (or the only one, when nothing was added).
    assert _sha(live["_legacyHashPayload"] or live["_hashPayload"]) == legacy_hash

    named = _accessible_name(ev["target"])
    if named:
        assert live["name"] == named

    if case.get("twin"):
        _, twin_hash, twin_legacy, twin_live = _describe(page, case["twin"])
        assert _sha(twin_live["_hashPayload"]) == twin_hash
        assert _sha(twin_live["_legacyHashPayload"] or twin_live["_hashPayload"]) == twin_legacy
        assert (twin_hash == recorded_hash) is bool(case.get("same_as_twin"))


def test_state_class_rule_is_one_rule_in_recorder_and_compiler():
    """bridge.js::isStateClass and selector_filters._STATE_CLASS_RE must stay the same rule."""
    import re

    from conxa_compile.compiler import selector_filters as sf

    bridge = (Path(__file__).resolve().parents[1].parent / "conxa-builder" / "python" / "conxa_compile" / "recorder" / "bridge.js").read_text(encoding="utf-8")
    words = "".join(re.findall(r'"([^"]*)"', bridge[bridge.index("const _STATE_WORDS"): bridge.index("const _STATE_CLASS_RE")]))
    assert words == sf._STATE_WORDS
    tail = re.search(r'"\[-_\]\(\?:([^)]*)\)\$"', bridge).group(1)
    assert f"[-_](?:{tail})$" in sf._STATE_CLASS_RE.pattern


def test_a_saved_event_keeps_the_fields_the_compiler_reads():
    """RecordedEvent drops undeclared target fields; value_label and label_nearby must survive validation."""
    from conxa_core.models.events import TargetDom

    kept = TargetDom.model_validate({"tag": "input", "value_label": "Transfer", "label_nearby": True}).model_dump()
    assert kept["value_label"] == "Transfer" and kept["label_nearby"] is True
