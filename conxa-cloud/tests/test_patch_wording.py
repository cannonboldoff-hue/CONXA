"""Every patch_gate rejection code has plain wording, and no output leaks a code."""

from __future__ import annotations

import ast
import os
import re
import sys

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_compile.editor.patch_wording import _WORDING, plain_patch_error  # noqa: E402

_GATE = os.path.join(_PY_DIR, "conxa_compile", "editor", "patch_gate.py")


def _static_codes() -> set[str]:
    with open(_GATE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        if getattr(func, "id", None) != "ValueError" or not node.exc.args:
            continue
        arg = node.exc.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            codes.add(arg.value)
    return codes


def test_every_static_code_has_exact_wording():
    codes = _static_codes()
    assert len(codes) > 90
    missing = sorted(c for c in codes if c not in _WORDING)
    assert not missing, f"add plain wording to patch_wording._WORDING for: {missing}"


def test_outputs_never_leak_codes():
    samples = sorted(_static_codes()) + [
        "frame_chain_3_selector_required",
        "branch_option_2_must_be_object",
        "branch_option_1_selector_failed_quality_gates",
        "branch_candidate_4_failed_quality_gates",
        "invalid_placeholder_syntax:{{a b}}",
        "click_step_allows_only_value_and_description",
        "type_step_cannot_patch_recovery",
        "type_value_empty",
        "agent_span_something_new",
        "for_each_something_new",
        "totally_unknown_code",
        "",
    ]
    for code in samples:
        text = plain_patch_error(code)
        assert text.strip()
        assert not re.search(r"[a-z]+_[a-z]+", text), text
        if code:
            assert code not in text


def test_fallback_and_pattern_used_when_no_exact_entry():
    assert plain_patch_error("totally_unknown_code").startswith("That change doesn't fit")
    assert "frame" in plain_patch_error("frame_chain_3_selector_required")
