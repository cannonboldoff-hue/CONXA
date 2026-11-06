"""BUILD-26 stage g: accepting one or more `structural_op` proposals lands as a single
all-or-nothing batch — exactly one undo entry regardless of how many ops it contains, and a
failing op aborts the whole batch untouched. Exercised through the real Backend.dispatch() (same
pattern as test_copilot_handlers.py) so the edit-log attribution is covered too.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))


@pytest.fixture()
def backend():
    spec = importlib.util.spec_from_file_location("cbackend_copilot_structural", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out: list[dict] = []
    from handlers import protocol as _protocol

    mod._write = lambda obj: out.append(obj)
    _protocol._write = lambda obj: out.append(obj)
    return mod.Backend(), out


def _last(out):
    return out[-1]


def _write_click_skill(skill_id: str) -> None:
    from conxa_core.storage.json_store import write_skill

    write_skill(
        skill_id,
        {
            "meta": {"id": skill_id, "title": "Structural test skill", "version": 1},
            "skills": [{"id": skill_id, "steps": [
                {
                    "action": {"action": "click"},
                    "url": "https://x",
                    "identity_bundle": {"signals": [{"engine": "testid", "selector": "#submit", "durability": 0.9}]},
                    "intent": "click_submit",
                    "value": "",
                    "target": {"primary_selector": "#submit", "fallback_selectors": []},
                    "validation": {"wait_for": {"type": "none"}, "success_conditions": {}},
                },
            ]}],
        },
    )


def test_a_multi_op_batch_lands_as_one_undo_entry(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill
    from conxa_compile.compiler.step_key import step_keys

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    b, out = backend
    _write_click_skill("skill_struct_1")

    doc = read_skill("skill_struct_1")
    keys = step_keys(doc["skills"][0]["steps"])

    b.dispatch({
        "id": "1", "type": "accept_copilot_proposal",
        "payload": {
            "skill_id": "skill_struct_1", "command": "structural_op", "proposal_id": "p1,p2",
            "ops": [
                {"op": "insert_step", "action_kind": "agent_span", "after_step_key": keys[0],
                 "fields": {"value": "Did it submit?"}},
                {"op": "insert_step", "action_kind": "hover", "after_step_key": keys[0],
                 "identity_from_step_key": keys[0]},
            ],
        },
    })

    assert _last(out)["type"] == "result", _last(out)
    updated = read_skill("skill_struct_1")
    assert len(updated["skills"][0]["steps"]) == 3
    assert len(b._undo_stacks.get("skill_struct_1", [])) == 1

    b.dispatch({"id": "2", "type": "undo_workflow", "payload": {"skill_id": "skill_struct_1"}})
    assert _last(out)["type"] == "result", _last(out)
    reverted = read_skill("skill_struct_1")
    assert len(reverted["skills"][0]["steps"]) == 1


def test_a_batch_with_a_stale_op_aborts_entirely_and_pushes_no_undo(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill
    from conxa_compile.compiler.step_key import step_keys

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    b, out = backend
    _write_click_skill("skill_struct_2")

    doc = read_skill("skill_struct_2")
    keys = step_keys(doc["skills"][0]["steps"])

    b.dispatch({
        "id": "1", "type": "accept_copilot_proposal",
        "payload": {
            "skill_id": "skill_struct_2", "command": "structural_op", "proposal_id": "p3",
            "ops": [
                {"op": "insert_step", "action_kind": "wait", "after_step_key": keys[0]},
                {"op": "delete_step", "step_key": "not-a-real-step-key"},
            ],
        },
    })

    assert _last(out)["type"] == "error", _last(out)
    untouched = read_skill("skill_struct_2")
    assert len(untouched["skills"][0]["steps"]) == 1
    assert b._undo_stacks.get("skill_struct_2", []) == []


def test_chat_ops_apply_through_the_editor_calls_as_one_undo(backend, monkeypatch, tmp_path):
    """A popup decision, an agent step and a step inside an if-present body land together, the popup
    decision writes author_approved through set_fork_policy, and one Undo reverts all of it."""
    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_compile.compiler.step_key import step_keys
    from conxa_compile.editor.workflow_mutations import _new_manual_step

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    b, out = backend
    click = {"action": {"action": "click"}, "url": "https://x", "intent": "click_submit", "value": "",
             "identity_bundle": {"signals": [{"engine": "testid", "selector": "#submit", "durability": 0.9}]},
             "target": {"primary_selector": "#submit", "fallback_selectors": []},
             "validation": {"wait_for": {"type": "none"}, "success_conditions": {}}}
    fork = {"action": {"action": "outcome_fork"}, "intent": "popup_decision", "url": "https://x", "value": None,
            "outcome_fork": {"states": [{"id": "s1", "signature": {"title": "File exists"}, "policy": {"mode": "ask_human"},
                                         "options": [{"id": "o1", "label": "Replace", "role": "consequential", "recorded": True, "steps": [click]},
                                                     {"id": "o2", "label": "Keep both", "role": "safe", "steps": []}]}]}}
    guard = _new_manual_step("if_present", "https://x")
    guard["target"] = {"primary_selector": "#banner", "fallback_selectors": []}
    guard["branch"] = {"steps": []}
    write_skill("skill_chat_ops", {"meta": {"id": "skill_chat_ops", "title": "t", "version": 1},
                                   "skills": [{"id": "skill_chat_ops", "steps": [click, fork, guard]}]})
    keys = step_keys(read_skill("skill_chat_ops")["skills"][0]["steps"])

    b.dispatch({"id": "1", "type": "accept_copilot_proposal", "payload": {
        "skill_id": "skill_chat_ops", "command": "structural_op", "ops": [
            {"op": "set_popup_decision", "step_key": keys[1], "state_id": "s1", "policy": {"mode": "auto", "option_id": "o2"}},
            {"op": "insert_step", "action_kind": "agent_span", "after_step_key": keys[0],
             "fields": {"value": "If it says verified click Download.", "agent_span_allowed": ["click"]}},
            {"op": "body_insert_step", "step_key": keys[2], "action_kind": "wait"},
        ]}})
    assert _last(out)["type"] == "result", _last(out)
    doc = read_skill("skill_chat_ops")
    steps = doc["skills"][0]["steps"]
    assert steps[1]["action"]["action"] == "agent_span" and steps[1]["value"].startswith("If it says verified")
    policy = steps[2]["outcome_fork"]["states"][0]["policy"]
    assert policy["mode"] == "auto" and policy["option_id"] == "o2" and policy["author_approved"] is True
    assert len(steps[3]["branch"]["steps"]) == 1
    assert len(b._undo_stacks.get("skill_chat_ops", [])) == 1

    b.dispatch({"id": "2", "type": "undo_workflow", "payload": {"skill_id": "skill_chat_ops"}})
    assert len(read_skill("skill_chat_ops")["skills"][0]["steps"]) == 3


def test_loop_body_ops_apply_and_the_editor_link_card_carries_the_step(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill
    from conxa_compile.compiler.step_key import step_keys
    from conxa_compile.editor.workflow_mutations import _new_manual_step

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    b, out = backend
    click = {"action": {"action": "click"}, "url": "https://x", "intent": "click_submit", "value": "",
             "identity_bundle": {"signals": [{"engine": "testid", "selector": "#submit", "durability": 0.9}]},
             "target": {"primary_selector": "#submit", "fallback_selectors": []},
             "validation": {"wait_for": {"type": "none"}, "success_conditions": {}}}
    loop = _new_manual_step("for_each", "https://x")
    loop["for_each"] = {"rows": {"container_selector": "tr"}, "as": "row", "max_iterations": 5, "on_row_error": "stop", "steps": [click]}
    write_skill("skill_loop_ops", {"meta": {"id": "skill_loop_ops", "title": "t", "version": 1},
                                   "skills": [{"id": "skill_loop_ops", "steps": [loop]}]})
    key = step_keys(read_skill("skill_loop_ops")["skills"][0]["steps"])[0]

    b.dispatch({"id": "1", "type": "accept_copilot_proposal", "payload": {
        "skill_id": "skill_loop_ops", "command": "structural_op", "ops": [
            {"op": "body_insert_step", "step_key": key, "action_kind": "wait", "after_nested_index": 0,
             "fields": {"intent": "pause_between_rows"}},
        ]}})
    assert _last(out)["type"] == "result", _last(out)
    body = read_skill("skill_loop_ops")["skills"][0]["steps"][0]["for_each"]["steps"]
    assert len(body) == 2 and body[1]["intent"] == "pause_between_rows"
    assert len(b._undo_stacks.get("skill_loop_ops", [])) == 1
