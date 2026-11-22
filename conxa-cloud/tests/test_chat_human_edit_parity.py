"""E2E16-6b: chat and Human Edit reach the same result and refuse the same things. No network; the model is stubbed.

BUILD-96 (4) a chat "add a check" never makes a check step with no element; BUILD-98 (3) Studio's hidden follow-up
never reads as the person's message; BUILD-104 (6) the reply model is given the current state, not the transcript's;
BUILD-105 (2) the same value edit writes the same step data on both paths; BUILD-105 (4) a refusal on one path is a
refusal on the other."""

from __future__ import annotations

import copy
import importlib.util
import os
import sys

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_compile.compiler.step_key import step_keys  # noqa: E402
from conxa_compile.conversation import harness, runtime_state  # noqa: E402
from conxa_compile.conversation import thread_store as ts  # noqa: E402
from conxa_compile.editor.copilot_proposals import gate_all, gate_proposals, gate_structural_proposals, proposal_summary  # noqa: E402

HOME = "https://a.test/home"


def _type(value="quarterly report", action=None):
    return {"action": action or "type", "url": HOME, "intent": "enter_note", "value": value,
            "identity_bundle": {"signals": [{"engine": "testid", "selector": "#note", "durability": 0.9}]},
            "target": {"primary_selector": "#note", "fallback_selectors": []}}


def _click(text="Submit"):
    return {"action": "click", "url": HOME, "intent": "click_submit", "value": "",
            "identity_bundle": {"signals": [{"engine": "testid", "selector": "#s", "durability": 0.9}]},
            "target": {"primary_selector": "#s", "fallback_selectors": []},
            "validation": {"assertions": [{"type": "url_changed", "required": False}]}, "semantic_description": text}


def _doc(steps):
    return {"meta": {"id": "s", "title": "t", "version": 1}, "skills": [{"id": "s", "steps": steps}]}


def _keys(doc):
    return step_keys(doc["skills"][0]["steps"])


# ── BUILD-96 (4): "add a check after step N" ────────────────────────────────────────────────


def test_a_text_check_step_becomes_a_check_on_the_step_it_follows():
    doc = _doc([_type(), _click()])
    k = _keys(doc)
    raw = {"op": "insert_step", "action_kind": "assert", "after_step_key": k[1],
           "fields": {"check_kind": "text", "check_text": "Thank you for your order"}, "why": "asked", "evidence_refs": [k[1]]}
    out = gate_all(doc, [raw])
    assert len(out) == 1 and out[0]["command"] == "patch_step" and out[0]["step_key"] == k[1]
    assert out[0]["patch"] == {"validation": {"assertions": [
        {"type": "url_changed", "required": False},                       # the step's own checks are kept
        {"type": "text_present", "target": "Thank you for your order", "required": True}]}}
    assert proposal_summary(out[0]) == "After step 2, also check that the page shows “Thank you for your order”"
    # At the end: the last step carries it.
    assert gate_all(doc, [{**raw, "after_step_key": None}])[0]["step_key"] == k[1]


@pytest.mark.parametrize("fields", [
    {"check_kind": "selector"}, {"check_kind": "visible"}, {"check_kind": "text"}, {"check_kind": "text", "check_text": "  "},
])
def test_a_check_step_that_needs_an_element_gets_no_card(fields):
    doc = _doc([_type(), _click()])
    k = _keys(doc)
    for kind in ("assert", "check"):
        raw = {"op": "insert_step", "action_kind": kind, "after_step_key": k[0], "fields": fields, "evidence_refs": [k[0]]}
        assert gate_all(doc, [raw]) == []


def test_an_address_check_step_is_still_a_step():
    doc = _doc([_type(), _click()])
    k = _keys(doc)
    out = gate_structural_proposals(doc, [{"op": "insert_step", "action_kind": "check", "after_step_key": k[1],
                                           "fields": {"check_kind": "url", "check_pattern": "/done"}, "evidence_refs": [k[1]]}])
    assert len(out) == 1 and out[0]["op"] == "insert_step"


def test_a_text_check_step_inside_a_loop_body_gets_no_card():
    from conxa_compile.editor.workflow_mutations import _new_manual_step

    loop = _new_manual_step("for_each", HOME)
    loop["for_each"]["steps"] = [_click()]
    doc = _doc([loop])
    k = _keys(doc)
    raw = {"op": "body_insert_step", "step_key": k[0], "action_kind": "assert", "after_nested_index": 0,
           "fields": {"check_kind": "text", "check_text": "Saved"}, "evidence_refs": [k[0]]}
    assert gate_structural_proposals(doc, [raw]) == []


# ── BUILD-98 (3) and BUILD-104 (6): what the reply model is told ────────────────────────────

T = "wf:w_parity"


@pytest.fixture()
def calls(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    runtime_state.clear_all()
    seen: list[str] = []

    def fake(task, payload, timeout_ms, *, on_delta, error_detail=None):
        seen.append(payload["user_text"])
        on_delta("About the delivery note: nothing to change.")
        return {}

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", fake)
    return seen


def test_the_hidden_follow_up_is_never_the_persons_message(calls):
    ts.append(T, "user_message", text="please keep it short")
    out = harness.run_turn(thread_id=T, message="In your answers you asked me to keep in mind: “ship by Friday”.",
                           sink=None, lifecycle={"stage": "ready"}, hidden=True)
    text = calls[-1]
    assert "Person's message: In your answers" not in text
    assert "Studio (not the person; they did not write or see this): In your answers" in text
    assert out["reply"].startswith(harness.FOLLOW_UP_LEAD)       # the visible reply says it starts on its own
    # The next turn's model sees it as Studio's, not the person's.
    harness.run_turn(thread_id=T, message="thanks", sink=None, lifecycle={"stage": "ready"})
    history = calls[-1].split("Recent conversation:", 1)[1]
    assert "user: please keep it short" in history
    assert "user: In your answers" not in history and "Studio (not the person" in history


def test_the_understanding_check_is_not_told_the_hidden_follow_up_as_the_persons_chat(calls):
    from handlers.understand_flow import recording_chat

    ts.append("wf:w_rc", "user_message", text="only weekdays")
    ts.append("wf:w_rc", "user_message", text="In your answers you asked me to keep in mind", hidden=True)
    assert recording_chat("w_rc") == "only weekdays"


def test_the_hidden_follow_up_never_answers_a_card_or_starts_an_action(calls):
    ts.append_card(T, "clarify_question", "open", title="Which account?", choices=[{"id": "yes", "label": "Yes"}])
    picked: list = []
    routed: list = []
    harness.run_turn(thread_id=T, message="yes", sink=None, lifecycle={"stage": "ready", "allowed_actions": ["test"]},
                     hidden=True, card_executor=lambda c, a, body: picked.append(body) or {"ok": True},
                     flow_executor=lambda action, slots: routed.append(action) or {"ok": True})
    assert picked == [] and routed == []
    assert calls, "the follow-up is answered by the reply model, not by the card"


def test_the_reply_model_reads_the_current_state_first_and_no_closed_card(calls):
    """BUILD-104 (6), as far as a stub can prove it: the words the model is given put the current lifecycle above an
    older "already approved" reply and list no card that was cancelled. What a live model then says is not checked."""
    ts.append(T, "user_message", text="approve it")
    ts.append(T, "assistant_message", text="It is already approved.")
    card = ts.append_card(T, "confirm_action", "open", action="test", label="Test it", actions=["confirm", "cancel"])
    ts.revise_card(T, card["card_id"], state="dismissed")
    harness.run_turn(thread_id=T, message="is it approved?", sink=None,
                     lifecycle={"stage": "test", "note": "Edited since it was approved", "allowed_actions": ["test"]})
    text = calls[-1]
    state_at, history_at = text.index("current, and it overrides anything earlier"), text.index("It is already approved.")
    assert state_at < history_at and '"stage": "test"' in text and "Edited since it was approved" in text
    assert "No card is waiting on the person right now." in text and "Open cards waiting" not in text


# ── BUILD-105 (2) and (4): the same change, and the same refusal, on both paths ─────────────


@pytest.fixture()
def backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    spec = importlib.util.spec_from_file_location("cbackend_parity", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out: list[dict] = []
    from handlers import protocol as _protocol

    mod._write = out.append
    _protocol._write = out.append
    b = mod.Backend()

    def call(kind, payload):
        b.dispatch({"id": str(len(out)), "type": kind, "payload": payload})
        return out[-1]

    return call


def _twins(steps):
    from conxa_core.storage.json_store import read_skill, write_skill

    write_skill("chat_twin", _doc(copy.deepcopy(steps)))
    write_skill("edit_twin", _doc(copy.deepcopy(steps)))
    return read_skill


def _by_chat(call, step_index, patch):
    from conxa_core.storage.json_store import read_skill

    key = _keys(read_skill("chat_twin"))[step_index]
    return call("accept_copilot_proposal", {"skill_id": "chat_twin", "step_key": key, "patch": patch, "proposal_id": "p"})


def _by_human_edit(call, step_index, patch):
    # What the Human Edit form now sends: only the fields that were edited (StepConfigForm.persistStepValues).
    return call("patch_step", {"skill_id": "edit_twin", "step_index": step_index, "patch": patch})


@pytest.mark.parametrize("action", ["type", {"action": "type", "value": "quarterly report"}])
@pytest.mark.parametrize("patch", [{"value": "{{note_text}}"}, {"semantic_description": "Type the note"}])
def test_the_same_edit_writes_the_same_step_on_both_paths(backend, action, patch):
    read_skill = _twins([_type(action=copy.deepcopy(action)), _click()])
    assert _by_chat(backend, 0, patch)["type"] == "result"
    assert _by_human_edit(backend, 0, patch)["type"] == "result"
    chat, edit = read_skill("chat_twin"), read_skill("edit_twin")
    assert chat["skills"] == edit["skills"] and chat.get("inputs") == edit.get("inputs")
    step = chat["skills"][0]["steps"][0]
    if "value" in patch and isinstance(step["action"], dict):
        assert step["action"]["value"] == "{{note_text}}"     # the packager reads action.value first


REFUSED_BY_THE_GATE = [
    ("value", "{{bad-name}}"),
    ("run_if", {"input": "note", "op": "sometimes"}),
    ("run_if", "always"),
    ("on_missing", "skip"),                                  # no bound record to be missing
]


@pytest.mark.parametrize("field,value", REFUSED_BY_THE_GATE, ids=repr)
def test_what_human_edit_refuses_chat_refuses(backend, field, value):
    read_skill = _twins([_type(), _click()])
    before = read_skill("chat_twin")["skills"]
    patch = {field: value}
    assert _by_human_edit(backend, 0, patch)["type"] == "error"
    doc = read_skill("chat_twin")
    assert gate_proposals(doc, [{"step_key": _keys(doc)[0], "field": field, "patch": value, "evidence_refs": ["x"]}]) == []
    assert _by_chat(backend, 0, patch)["type"] == "error"
    assert read_skill("chat_twin")["skills"] == read_skill("edit_twin")["skills"] == before


def _password_box():
    step = _type(value="")
    step["identity_bundle"]["fingerprint"] = {"input_type": "password"}
    return step


def test_chat_refuses_a_password_typed_into_a_step():
    doc = _doc([_password_box(), _click()])
    assert gate_proposals(doc, [{"step_key": _keys(doc)[0], "field": "value", "patch": "secret123", "evidence_refs": ["x"]}]) == []


def test_human_edit_refuses_a_password_typed_into_a_step(backend):
    read_skill = _twins([_password_box(), _click()])
    before = read_skill("edit_twin")["skills"]
    out = _by_human_edit(backend, 0, {"value": "secret123"})
    assert out["type"] == "error" and out["code"] == "sign_in_not_in_workflow" and "Signing in" in out["message"]
    assert read_skill("edit_twin")["skills"] == before


def _open(url):
    return {"action": {"action": "navigate", "url": url}, "url": url, "intent": "navigate", "value": ""}


def test_human_edit_refuses_a_step_that_opens_a_sign_in_page_but_saves_one_already_there(backend):
    _twins([_open(HOME), _open("https://a.test/auth/login"), _click()])
    to_login = {"url": "https://a.test/signin", "action": {"action": "navigate", "url": "https://a.test/signin"}}
    assert _by_human_edit(backend, 0, to_login)["code"] == "sign_in_not_in_workflow"
    # The form re-sends an unchanged address with every save: renaming such a step still works.
    same = {"semantic_description": "Open the page", "url": "https://a.test/auth/login",
            "action": {"action": "navigate", "url": "https://a.test/auth/login"}}
    assert _by_human_edit(backend, 1, same)["type"] == "result"
