"""BUILD-37: the conversation command handlers wired to a real Backend (no network, no model)."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_compile.conversation import runtime_state, thread_store as ts  # noqa: E402


@pytest.fixture()
def b(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    runtime_state.clear_all()
    spec = importlib.util.spec_from_file_location("cbackend_conv", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol

    out: list[dict] = []
    mod._write = out.append
    protocol._write = out.append
    backend = mod.Backend()
    monkeypatch.setattr(backend, "_install_proxy_router", lambda *a, **k: None)
    backend._events = out
    return backend


def _group_and_workflow(name="Create invoice", url="https://billing.example.test/new"):
    from conxa_core.storage.group_store import create_group

    g = create_group("Finance Ops")
    return g, g.id


def test_chat_commands_round_trip(b):
    chat = b.cmd_chat_create({"title": "Planning"}, "r1")["chat"]
    assert [c["title"] for c in b.cmd_chat_list({}, "r2")["chats"]] == ["Planning"]
    b.cmd_chat_rename({"chat_id": chat["id"], "title": "Refunds"}, "r3")
    page = b.cmd_thread_load({"thread_id": f"chat:{chat['id']}"}, "r4")
    assert page["chat"]["title"] == "Refunds" and page["items"] == []
    assert b.cmd_chat_delete({"chat_id": chat["id"]}, "r5") == {"deleted": True}


def test_creating_a_workflow_starts_its_thread_and_lifecycle(b):
    g, gid = _group_and_workflow()
    wf = b.cmd_create_workflow({"name": "Create invoice", "group_id": gid, "target_url": "https://billing.example.test/new", "task_description": "send one"}, "r1")["workflow"]
    page = b.cmd_thread_load({"thread_id": f"wf:{wf['id']}"}, "r2")
    assert [i["text"] for i in page["items"]] == ["Workflow created"]
    assert page["workflow"]["group_name"] == "Finance Ops"
    assert page["lifecycle"]["stage"] in ("record", "configure")
    assert wf["task_description"] == "send one"
    lcs = b.cmd_list_lifecycles({}, "r3")["lifecycles"]
    assert wf["id"] in lcs and lcs[wf["id"]]["glyph"] in ("none", "warn")


def test_unknown_thread_and_workflow_are_clean_errors(b):
    from handlers.protocol import _CommandError

    with pytest.raises(_CommandError) as e1:
        b.cmd_thread_load({"thread_id": "wf:../etc"}, "r")
    assert e1.value.code == "invalid_input"
    with pytest.raises(_CommandError) as e2:
        b.cmd_thread_load({"thread_id": "wf:nope"}, "r")
    assert e2.value.code == "workflow_not_found"


def test_draft_card_creates_nothing_until_confirmed_and_needs_a_group_and_url(b):
    _g, gid = _group_and_workflow()
    chat = b.cmd_chat_create({}, "r")["chat"]
    tid = f"chat:{chat['id']}"
    card = ts.append_card(tid, "draft_workflow", "open", draft={"name": "Issue a refund", "group": "Finance Ops", "starting_page": "", "task": "t"})
    from conxa_core.storage.workflow_store import list_workflows

    assert list_workflows() == []
    incomplete = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "create_and_record"}, "r")
    assert incomplete["ok"] is False and incomplete["needs"] == ["starting_page"]
    assert list_workflows() == []  # still nothing created

    done = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "create_and_record",
                              "payload": {"starting_page": "https://billing.example.test/orders"}}, "r")
    assert done["ok"] and done["start_recording"] is True
    assert [w.name for w in list_workflows()] == ["Issue a refund"] and list_workflows()[0].group_id == gid
    assert ts.get_card(tid, card["card_id"])["state"] == "created"
    # a draft can only be used once
    again = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "create_and_record"}, "r")
    assert again["ok"] is False


def test_pending_change_apply_then_undo_goes_through_the_existing_commands(b, monkeypatch):
    from conxa_core.storage import json_store

    _g, gid = _group_and_workflow()
    wf = b.cmd_create_workflow({"name": "W", "group_id": gid, "target_url": "https://a.example.test"}, "r")["workflow"]
    doc = {"meta": {"version": 1}, "skills": [{"steps": []}]}
    state = {"doc": doc}
    monkeypatch.setattr(json_store, "read_skill", lambda sid: state["doc"])
    applied: list[dict] = []

    def fake_accept(payload, rid):
        applied.append(payload)
        b._push_undo("skill_1", dict(state["doc"]))
        state["doc"] = {"meta": {"version": 2}, "skills": [{"steps": []}]}
        return {}

    undone: list[int] = []
    monkeypatch.setattr(b, "cmd_accept_copilot_proposal", fake_accept)
    monkeypatch.setattr(b, "cmd_undo_workflow", lambda p, r: undone.append(1) or state.update(doc={"meta": {"version": 3}, "skills": [{"steps": []}]}) or {})
    tid = f"wf:{wf['id']}"
    proposal = {"id": "p1", "command": "patch_step", "step_key": "s1", "patch": {"value": "x"}}
    card = ts.append_card(tid, "pending_change", "open", refs={"skill_id": "skill_1"}, title="Change “value”", proposal=proposal)

    r = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "apply"}, "r")
    assert r["ok"] and applied[0]["skill_id"] == "skill_1" and applied[0]["command"] == "patch_step"
    after = ts.get_card(tid, card["card_id"])
    assert after["state"] == "applied" and after["applied_version"] == 2 and after["undo_steps"] == 1

    # applying twice is refused; undo works while nothing else changed
    assert b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "apply"}, "r")["ok"] is False
    assert b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "undo"}, "r")["ok"] is True
    assert undone == [1] and ts.get_card(tid, card["card_id"])["state"] == "undone"


def _change_set_env(b):
    from conxa_core.storage.json_store import write_skill

    _g, gid = _group_and_workflow()
    wf = b.cmd_create_workflow({"name": "W", "group_id": gid, "target_url": "https://a.example.test"}, "r")["workflow"]
    write_skill("skill_cs", {
        "meta": {"version": 1}, "inputs": [],
        "skills": [{"id": "skill_cs", "steps": [{
            "action": {"action": "navigate"}, "url": "https://github.com/o/r/blob/main/Agda.gitignore",
            "intent": "navigate", "value": "", "validation": {"assertions": []},
        }]}],
    })
    parameterize = {"id": "p1", "command": "structural_op", "op": "parameterize", "literal": "Agda.gitignore",
                    "name": "file_name", "kinds": ["value"], "changed": 1}
    return f"wf:{wf['id']}", parameterize


def test_one_replys_changes_apply_together_with_one_undo_entry(b):
    from conxa_core.storage.json_store import read_skill

    tid, parameterize = _change_set_env(b)
    card = ts.append_card(tid, "pending_change", "open", refs={"skill_id": "skill_cs"}, title="t", proposals=[parameterize])
    r = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "apply"}, "r")
    assert r["ok"], r
    doc = read_skill("skill_cs")
    assert doc["skills"][0]["steps"][0]["url"].endswith("{{file_name}}")
    assert [i["id"] for i in doc["inputs"]] == ["file_name"] and doc["inputs"][0]["default"] == "Agda.gitignore"
    assert ts.get_card(tid, card["card_id"])["undo_steps"] == 1


def test_a_change_set_with_one_failing_part_changes_nothing(b):
    from conxa_core.storage.json_store import read_skill

    tid, parameterize = _change_set_env(b)
    stale = {"id": "p2", "command": "patch_step", "step_key": "no-such-step", "patch": {"value": "x"}}
    before = read_skill("skill_cs")
    depth = len(b._undo_stacks.get("skill_cs", []))
    card = ts.append_card(tid, "pending_change", "open", refs={"skill_id": "skill_cs"}, title="t", proposals=[parameterize, stale])
    r = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "apply"}, "r")
    assert r["ok"] is False
    assert read_skill("skill_cs") == before  # the first part was rolled back with the second
    assert len(b._undo_stacks.get("skill_cs", [])) == depth
    assert ts.get_card(tid, card["card_id"])["state"] == "failed"


def test_undo_is_refused_when_the_workflow_changed_since(b, monkeypatch):
    from conxa_core.storage import json_store

    _g, gid = _group_and_workflow()
    wf = b.cmd_create_workflow({"name": "W", "group_id": gid, "target_url": "https://a.example.test"}, "r")["workflow"]
    monkeypatch.setattr(json_store, "read_skill", lambda sid: {"meta": {"version": 9}})
    tid = f"wf:{wf['id']}"
    card = ts.append_card(tid, "pending_change", "applied", refs={"skill_id": "skill_1"}, title="t", proposal={}, applied_version=2, undo_steps=1)
    monkeypatch.setattr(b, "cmd_undo_workflow", lambda p, r: pytest.fail("must not undo blindly"))
    out = b.cmd_card_action({"thread_id": tid, "card_id": card["card_id"], "action": "undo"}, "r")
    assert out["ok"] is False and out["code"] == "changed_since"


def test_an_llm_error_becomes_an_error_card_in_the_thread(b, monkeypatch):
    from handlers.protocol import _CommandError
    from services.llm_proxy_client import EntitlementBlocked

    chat = b.cmd_chat_create({}, "r")["chat"]

    def boom(*a, **k):
        raise EntitlementBlocked("ai_credits_exceeded")

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", boom)
    with pytest.raises(_CommandError) as exc:
        b.cmd_thread_turn({"thread_id": f"chat:{chat['id']}", "message": "hello"}, "r")
    assert exc.value.code == "ai_credits_exceeded"
    kinds = [(i["kind"], i.get("card_type")) for i in ts.load(f"chat:{chat['id']}")["items"]]
    assert kinds == [("user_message", None), ("card", "error")]  # the person's words are kept; Try again is safe


def test_approval_is_revoked_by_a_skill_write(b):
    from conxa_core.storage.json_store import write_skill
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    _g, gid = _group_and_workflow()
    wf = b.cmd_create_workflow({"name": "W", "group_id": gid, "target_url": "https://a.example.test"}, "r")["workflow"]
    w = get_workflow(wf["id"])
    w.session_id, w.recording_status = "s", "compiled"
    w.skill_id, w.signed_off, w.last_test_status = "skill_x", True, "passed"
    save_workflow(w)
    write_skill("skill_x", {"meta": {"version": 1}, "skills": [{"steps": []}]})  # create
    assert get_workflow(wf["id"]).signed_off is True  # a first write is not an edit
    write_skill("skill_x", {"meta": {"version": 2}, "skills": [{"steps": []}]})  # update
    after = get_workflow(wf["id"])
    assert after.signed_off is False and after.last_test_status == "never"
    assert b.cmd_get_lifecycle({"workflow_id": wf["id"]}, "r")["lifecycle"]["stage"] in ("test", "edit")
