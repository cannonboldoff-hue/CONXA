"""Regression tests for the compile↔edit boundary fixes (COMPILE_PIPELINE_AUDIT_REPORT.md).

Covers the derived-field synchronization the live editor path used to skip (C-2/M-1/M-2),
the value↔inputs reconciliation (M-3), intent-graph index remapping (L-1), dedup token
rewrite (L-2), find-&-replace placeholder protection (L-3), and select-default validation
(L-5). All target pure helpers — no compile, no LLM, no browser.
"""

from __future__ import annotations

import pytest

from conxa_compile.compiler.build import _deduplicate_input_bindings
from conxa_compile.compiler.intent_access import get_effective_intent_from_skill_step
from conxa_compile.compiler.patch import sync_derived_step_fields
from conxa_compile.editor.workflow_mutations import (
    _replace_outside_placeholders,
    _validate_skill_inputs,
    delete_step_at,
    insert_step_after,
    reconcile_inputs_with_step_values,
    reorder_steps,
)


# --- C-2: intent rename now reaches the effective-intent field + recovery ------------------

def test_intent_edit_syncs_final_intent_and_recovery() -> None:
    step = {
        "action": {"action": "click"},
        "intent": "old_intent",
        "signals": {"semantic": {"final_intent": "old_intent", "llm_intent": "old_intent"}},
        "recovery": {"intent": "old_intent", "final_intent": "old_intent", "strategies": []},
        "validation": {"wait_for": {"type": "none", "timeout": 5000}},
    }
    out = sync_derived_step_fields(step, {"intent": "new_intent"})
    assert out["intent"] == "new_intent"
    assert out["signals"]["semantic"]["final_intent"] == "new_intent"
    assert get_effective_intent_from_skill_step(out) == "new_intent"
    assert out["recovery"]["intent"] == "new_intent"
    assert out["recovery"]["final_intent"] == "new_intent"


# --- M-1: value edit keeps input_binding in lockstep --------------------------------------

def test_value_edit_syncs_input_binding() -> None:
    step = {
        "action": {"action": "fill"},
        "intent": "fill_x",
        "value": "{{old_name}}",
        "input_binding": "old_name",
        "signals": {},
        "validation": {"wait_for": {"type": "none", "timeout": 5000}},
    }
    renamed = sync_derived_step_fields(step, {"value": "{{new_name}}"})
    assert renamed["input_binding"] == "new_name"

    literal = sync_derived_step_fields(step, {"value": "just text"})
    assert literal["input_binding"] is None


# --- Branch-body leaf steps never get a real recovery block synced (Key Invariant) ---------

def test_branch_body_leaf_skips_recovery_sync() -> None:
    step = {"action": {"action": "click"}, "intent": "x", "recovery": {"kind": "no_recovery_block"}}
    out = sync_derived_step_fields(step, {"intent": "y"}, sync_recovery=False)
    # recovery block is not rewritten from intent (only anchors normalized)
    assert out["recovery"].get("kind") == "no_recovery_block"
    assert "strategies" not in out["recovery"]


# --- M-3: a {{var}} used in a value but undeclared gets auto-declared ----------------------

def test_reconcile_auto_declares_missing_inputs() -> None:
    doc = {
        "skills": [{"steps": [{"value": "{{a}}"}, {"value": "hello {{b}} world"}]}],
        "inputs": [{"id": "a", "type": "text"}],
    }
    out, added = reconcile_inputs_with_step_values(doc)
    assert added is True
    assert {i["id"] for i in out["inputs"]} == {"a", "b"}


def test_reconcile_noop_when_all_declared() -> None:
    doc = {"skills": [{"steps": [{"value": "{{a}}"}]}], "inputs": [{"id": "A", "type": "text"}]}
    out, added = reconcile_inputs_with_step_values(doc)  # case-insensitive: A covers {{a}}
    assert added is False
    assert out is doc


# --- for_each loop variables and the downloaded_file* family never leak into declared inputs --
# (EXEC-38 items source generalization). Two runtime-populated placeholder families must never
# surface as a "please supply a value" input: a for_each loop's own {{<as>_id}}/{{<as>_index}}
# (run.js sets them from the loop's row/items list) and {{downloaded_file*}}/
# {{downloaded_files_dir}} (run.js's download_observed handler binds them from an earlier
# download in the same run). Two editor RPCs (handlers/workflow_editor.py's patch-value and
# replace-literal paths) call reconcile_inputs_with_step_values directly with no downstream
# filter_runtime_only_inputs() call, so the exclusion has to live here, not only in the compile
# handler.

def test_reconcile_excludes_for_each_loop_variables() -> None:
    # {{other_var}}, unrelated to the loop, must still auto-declare alongside the exclusion —
    # this isn't a blanket "spotted nothing" outcome, only the loop's own {{<as>_id}}/
    # {{<as>_index}} names are held back.
    doc = {
        "skills": [{"steps": [
            {
                "action": {"action": "for_each"},
                "for_each": {
                    "items": "files",
                    "as": "file",
                    "max_iterations": 50,
                    "steps": [{"value": "{{file_id}}"}],
                },
            },
            {"value": "{{other_var}}"},
        ]}],
        "inputs": [],
    }
    out, added = reconcile_inputs_with_step_values(doc)
    ids = {i["id"] for i in out["inputs"]}
    assert added is True
    assert "other_var" in ids  # an unrelated placeholder still auto-declares
    assert "file_id" not in ids
    assert "file_index" not in ids


def test_reconcile_excludes_downloaded_file_family() -> None:
    doc = {
        "skills": [{"steps": [{"value": "{{downloaded_file}}"}, {"value": "{{downloaded_files_dir}}"}]}],
        "inputs": [],
    }
    out, added = reconcile_inputs_with_step_values(doc)
    assert added is False
    assert out is doc
    assert out["inputs"] == []


# --- L-1: intent_graph indices track structural edits -------------------------------------

def _doc_with_graph() -> dict:
    return {
        "skills": [{"steps": [{"n": 0}, {"n": 1}, {"n": 2}]}],
        "intent_graph": {
            "steps": [
                {"index": 0, "intent": "i0"},
                {"index": 1, "intent": "i1"},
                {"index": 2, "intent": "i2"},
            ]
        },
    }


def _graph_pairs(doc: dict) -> list[tuple[int, str]]:
    return [(e["index"], e["intent"]) for e in doc["intent_graph"]["steps"]]


def test_intent_graph_remap_on_reorder() -> None:
    out = reorder_steps(_doc_with_graph(), [2, 0, 1])
    assert _graph_pairs(out) == [(0, "i2"), (1, "i0"), (2, "i1")]


def test_intent_graph_remap_on_delete() -> None:
    out = delete_step_at(_doc_with_graph(), 1)
    assert _graph_pairs(out) == [(0, "i0"), (1, "i2")]


def test_intent_graph_remap_on_insert() -> None:
    out = insert_step_after(_doc_with_graph(), "click", 0)
    assert _graph_pairs(out) == [(0, "i0"), (2, "i1"), (3, "i2")]


# --- compile_report goes stale on any step-shape-changing edit (mega-workflow investigation) -
#
# compile_report is built once at compile time and indexed by top-level step position
# (compiler/build.py::_build_compile_report). A reorder/delete/insert shifts every later
# index out from under it, so the Human Edit compile-health banner
# (editor/workflow_dto.py::_compile_health) was pointing an approver at the wrong step's
# confidence/warnings after any edit. _invalidate_compile_report must fire on every mutation
# that changes step count/order.

def _doc_with_report() -> dict:
    doc = _doc_with_graph()
    doc["compile_report"] = {
        "status": "ok",
        "steps_total": 3,
        "min_confidence": 0.95,
        "steps_with_warnings": 0,
        "steps": [{"index": 0, "intent": "i0", "warnings": []}],
        "llm_router_stats": {},
    }
    return doc


def test_compile_report_marked_stale_on_reorder() -> None:
    out = reorder_steps(_doc_with_report(), [2, 0, 1])
    assert out["compile_report"]["status"] == "stale"
    assert out["compile_report"]["steps"] == []


def test_compile_report_marked_stale_on_delete() -> None:
    out = delete_step_at(_doc_with_report(), 1)
    assert out["compile_report"]["status"] == "stale"
    assert out["compile_report"]["steps"] == []


def test_compile_report_marked_stale_on_insert() -> None:
    out = insert_step_after(_doc_with_report(), "click", 0)
    assert out["compile_report"]["status"] == "stale"
    assert out["compile_report"]["steps"] == []


def test_compile_report_absent_is_a_noop() -> None:
    # No compile_report yet (never compiled) — must not raise or synthesize one.
    out = reorder_steps(_doc_with_graph(), [2, 0, 1])
    assert "compile_report" not in out


# --- L-2: dedup rewrites the {{name}} token inside a mixed value ---------------------------

def test_dedup_rewrites_mixed_value_token() -> None:
    class _Step:
        def __init__(self, binding: str, value: str) -> None:
            self.input_binding = binding
            self.value = value

    steps = [_Step("name", "{{name}}"), _Step("name", "prefix {{name}}")]
    _deduplicate_input_bindings(steps)  # type: ignore[arg-type]
    assert steps[1].input_binding == "name_2"
    assert steps[1].value == "prefix {{name_2}}"


# --- L-3: find & replace never nests braces into an existing placeholder -------------------

def test_replace_protects_existing_placeholder() -> None:
    new, count = _replace_outside_placeholders("{{db}}/extra", "db", "{{db_name}}")
    assert new == "{{db}}/extra"  # 'db' only occurs inside the placeholder → untouched
    assert count == 0
    assert "{{{{" not in new


def test_replace_hits_literal_outside_placeholder() -> None:
    new, count = _replace_outside_placeholders("prefix db {{db}}", "db", "X")
    assert new == "prefix X {{db}}"
    assert count == 1


# --- L-5: server-side select-default + case-insensitive uniqueness validation --------------

def test_select_default_must_be_in_options() -> None:
    with pytest.raises(ValueError):
        _validate_skill_inputs([{"id": "color", "type": "select", "options": ["red"], "default": "blue"}])
    _validate_skill_inputs([{"id": "color", "type": "select", "options": ["red"], "default": "red"}])


def test_case_insensitive_duplicate_input_ids_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_skill_inputs([{"id": "Email", "type": "text"}, {"id": "email", "type": "text"}])


# --- H-4 (PIPELINE_HANDOFF_AUDIT_REPORT.md): identity_bundle.signals is only rebuilt on a
# real selector edit, not on every save that happens to round-trip the displayed target -----

def _selector_step() -> dict:
    return {
        "action": {"action": "click"},
        "intent": "click_btn",
        "target": {"primary_selector": "#submit-btn", "fallback_selectors": ["#legacy-fallback"]},
        "identity_bundle": {
            "signals": [
                {
                    "engine": "css", "selector": "#submit-btn", "durability": 0.9,
                    "orthogonality_class": "id", "unique_at_compile": True, "source": "compiler",
                },
            ],
        },
        "signals": {},
        "recovery": {},
        "validation": {"wait_for": {"type": "none", "timeout": 5000}},
    }


def test_unrelated_edit_does_not_rebuild_identity_bundle() -> None:
    from handlers.workflow_editor import WorkflowEditorMixin

    step = _selector_step()
    # Editor round-trips target unchanged (matches compute_merged_display_target's merge,
    # "#legacy-fallback" is a recovery extra not covered by any signal) — only intent changed.
    patch = {
        "intent": "new_intent",
        "target": {"primary_selector": "#submit-btn", "fallback_selectors": ["#legacy-fallback"]},
    }
    merged = WorkflowEditorMixin()._apply_step_patch(step, patch)
    assert merged["identity_bundle"]["signals"] == step["identity_bundle"]["signals"]


def test_real_selector_edit_rebuilds_identity_bundle() -> None:
    from handlers.workflow_editor import WorkflowEditorMixin

    step = _selector_step()
    patch = {"target": {"primary_selector": "#new-selector", "fallback_selectors": []}}
    merged = WorkflowEditorMixin()._apply_step_patch(step, patch)
    signals = merged["identity_bundle"]["signals"]
    assert [s["selector"] for s in signals] == ["#new-selector"]
    assert signals[0]["source"] == "user"


# --- BUILD-37: consequence edits must reach the runtime's destructive flag --------------------

def test_consequence_edit_syncs_identity_bundle_destructive() -> None:
    step = {
        "action": {"action": "click"},
        "consequence": "irreversible",
        "identity_bundle": {"destructive": False, "signals": []},
        "validation": {"wait_for": {"type": "none", "timeout": 5000}},
    }
    marked = sync_derived_step_fields(step, {"consequence": "irreversible"})
    assert marked["identity_bundle"]["destructive"] is True
    assert marked["identity_bundle"]["signals"] == []  # nothing else in the bundle moves

    cleared = sync_derived_step_fields({**marked, "consequence": "reversible"}, {"consequence": "reversible"})
    assert cleared["identity_bundle"]["destructive"] is False


def test_unrelated_edit_leaves_destructive_alone() -> None:
    step = {
        "action": {"action": "click"},
        "consequence": "reversible",
        "identity_bundle": {"destructive": True, "signals": []},  # e.g. set by the compiler
        "validation": {"wait_for": {"type": "none", "timeout": 5000}},
    }
    out = sync_derived_step_fields(step, {"intent": "renamed"})
    assert out["identity_bundle"]["destructive"] is True
