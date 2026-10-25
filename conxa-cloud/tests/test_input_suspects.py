"""Blame a changed/invalid test input before editing the workflow (pure, no runtime, no model)."""

from __future__ import annotations

import os
import sys

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_compile.editor.input_suspects import find_input_suspects, preflight_problems  # noqa: E402

DEFS = [
    {"id": "gitignore_file", "type": "text", "default": "Ada.gitignore"},
    {"id": "region", "type": "select", "options": ["eu", "us"], "default": "eu"},
    {"id": "ticket", "type": "text", "pattern": r"^T-\d+$", "default": "T-1"},
    {"id": "password", "type": "text", "sensitive": True, "default": "{{REDACTED}}"},
]


def _ev(kind="ambiguous", *ids):
    return {"failure_kind": kind, "input_refs": [{"id": i, "empty": False} for i in ids]}


def test_value_changed_from_recorded_is_blamed():
    out = find_input_suspects(_ev("ambiguous", "gitignore_file"), {"gitignore_file": "gitignore"}, DEFS)
    assert out == [{"id": "gitignore_file", "reason": "differs_from_recorded"}]


def test_recorded_value_is_never_blamed_so_the_workflow_is():
    assert find_input_suspects(_ev("ambiguous", "gitignore_file"), {"gitignore_file": "Ada.gitignore"}, DEFS) == []


def test_only_inputs_the_failing_step_read_are_considered():
    assert find_input_suspects(_ev("not_found", "region"), {"gitignore_file": "other", "region": "eu"}, DEFS) == []


def test_every_find_failure_kind_counts_and_other_kinds_do_not():
    for kind in ("ambiguous", "not_found", "entity_not_found"):
        assert find_input_suspects(_ev(kind, "gitignore_file"), {"gitignore_file": "x"}, DEFS)
    for kind in ("verify_fail", "frame_not_found", "other", None):
        assert find_input_suspects(_ev(kind, "gitignore_file"), {"gitignore_file": "x"}, DEFS) == []


def test_old_runtime_evidence_without_the_new_keys_means_unknown():
    assert find_input_suspects({"message": "x"}, {"gitignore_file": "x"}, DEFS) == []
    assert find_input_suspects(None, {}, DEFS) == []


def test_definition_violations_are_named():
    out = find_input_suspects(_ev("not_found", "region", "ticket"), {"region": "asia", "ticket": "abc"}, DEFS)
    assert {(s["id"], s["reason"]) for s in out} == {("region", "not_an_option"), ("ticket", "pattern_mismatch")}


def test_empty_value_is_blamed():
    out = find_input_suspects(_ev("not_found", "gitignore_file"), {"gitignore_file": "  "}, DEFS)
    assert out == [{"id": "gitignore_file", "reason": "empty"}]


def test_sensitive_input_is_only_ever_flagged_when_empty():
    assert find_input_suspects(_ev("not_found", "password"), {"password": "hunter2"}, DEFS) == []
    assert find_input_suspects(_ev("not_found", "password"), {"password": ""}, DEFS) == [{"id": "password", "reason": "empty"}]


def test_a_broken_pattern_in_the_definition_is_ignored():
    defs = [{"id": "x", "type": "text", "pattern": "([", "default": "a"}]
    assert find_input_suspects(_ev("not_found", "x"), {"x": "a"}, defs) == []


def test_unknown_input_ids_are_skipped():
    assert find_input_suspects(_ev("not_found", "ghost"), {"ghost": "x"}, DEFS) == []


def test_preflight_flags_definition_violations_but_not_a_different_value():
    out = preflight_problems(DEFS, {"gitignore_file": "anything", "region": "asia", "ticket": "T-9"})
    assert out == [{"id": "region", "reason": "not_an_option"}]
