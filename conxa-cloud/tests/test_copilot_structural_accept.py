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
