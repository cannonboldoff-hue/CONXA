"""Gap analysis G2: a command in free text becomes one of the lifecycle's allowed actions, and
nothing else. No network, no model: ``call`` is injected or ``call_llm`` is replaced."""

from __future__ import annotations

import pytest

from conxa_core.config import settings
from conxa_compile.conversation import harness, runtime_state
from conxa_compile.conversation import thread_store as ts
from conxa_compile.conversation.actions import ACTIONS, routable
from conxa_compile.conversation.intent_router import route_intent
from conxa_compile.conversation.lifecycle import derive_lifecycle


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    runtime_state.clear_all()
    yield


def _never(*_a, **_k):
    raise AssertionError("the model must not be called")


# ── the table ───────────────────────────────────────────────────────────────────────────────


def test_every_action_the_lifecycle_can_offer_has_an_entry():
    """If derive_lifecycle gains an action id, chat has to know what to do with it (even if the answer
    is "nothing, it is a card button")."""
    base = {"session_id": "s", "target_url": "https://x", "recording_status": "compiled", "skill_id": "k"}
    seen: set[str] = set()
    cases = [
        ({"target_url": "", "session_id": ""}, {}), ({"session_id": ""}, {}), ({"session_id": ""}, {"runtime": {"recording": True}}),
        ({"session_id": ""}, {"runtime": {"signing_in": True}}), ({"session_id": ""}, {"auth_missing": True}),
        ({"skill_id": "", "recording_status": "recorded"}, {}), ({"skill_id": "", "recording_status": "recorded"}, {"runtime": {"out_of_credits": True}}),
        ({"skill_id": "", "recording_status": "error"}, {}), ({}, {"runtime": {"testing": True}}), ({}, {"clarify": {"open": 1}}),
        ({"last_test_status": "passed"}, {}), ({"last_test_status": "passed", "signed_off": True}, {}),
        ({"last_test_status": "failed"}, {}), ({"compile_steps_with_warnings": 2}, {}), ({}, {}),
        ({"last_test_status": "failed"}, {"autofix": {"state": "stopped"}}),
    ]
    for wf, kw in cases:
        seen.update(derive_lifecycle({**base, **wf}, **kw)["allowed_actions"])
    assert seen and seen <= set(ACTIONS), sorted(seen - set(ACTIONS))


def test_only_actions_with_a_chat_meaning_are_routable():
    assert [a.id for a in routable(["compile_later", "add_credits", "test", "nonsense"])] == ["test"]


# ── routing ─────────────────────────────────────────────────────────────────────────────────


def test_an_exact_phrase_needs_no_model():
    r = route_intent("Test it!", ["test", "edit"], call=_never)
    assert r == {"action": "test", "slots": {}, "source": "phrase"}
    assert route_intent("approve", ["approve", "test", "edit"], call=_never)["action"] == "approve"


def test_a_phrase_for_an_action_that_is_not_allowed_right_now_is_not_routed():
    # "approve" before the test passed: the lifecycle does not allow it, so chat cannot do it either
    assert route_intent("approve", ["test", "edit"], call=_never) is None


def test_an_ambiguous_phrase_goes_to_the_model_instead_of_guessing():
    # "fix it" means both autofix and resume_autofix; with both allowed it is not a phrase hit
    calls: list = []
    r = route_intent("fix it", ["autofix", "resume_autofix"], call=lambda t, p, ms: calls.append(t) or {"action": "autofix"})
    assert calls == ["chat_route_intent"] and r["action"] == "autofix" and r["source"] == "model"


def test_plain_conversation_never_costs_a_routing_call():
    assert route_intent("why did the second step fail?", ["test", "edit"], call=_never) is None  # no command word
    assert route_intent("x " * 200 + "test it", ["test"], call=_never) is None                       # far too long to be a command


def test_the_model_can_only_choose_an_allowed_action():
    ask = lambda answer: route_intent("could you please run it now", ["test", "edit"], call=lambda *_a: answer)  # noqa: E731
    assert ask({"action": "test"})["action"] == "test"
    assert ask({"action": "approve"}) is None        # real action, but not allowed now
    assert ask({"action": "chat"}) is None
    assert ask({"action": "delete_everything"}) is None
    assert ask(None) is None and ask("nonsense") is None


def test_a_failing_model_degrades_to_conversation():
    def boom(*_a):
        raise RuntimeError("cloud down")

    assert route_intent("please run the test", ["test"], call=boom) is None


def test_slots_are_held_to_what_the_workflow_actually_has():
    answer = {"action": "test", "slots": {"inputs": {"repository": "templates", "bogus": "x"}}}
    r = route_intent("test it with repository templates", ["test"], known_inputs=lambda: ["repository"], call=lambda *_a: answer)
    assert r["slots"] == {"inputs": {"repository": "templates"}}


def test_chat_opens_a_workflow_by_name_or_by_the_models_pick_among_the_persons_own():
    wfs = [{"id": "w1", "name": "Invoice run"}, {"id": "w2", "name": "Refunds"}]
    r = route_intent("open the invoice run workflow", ["open_workflow"], workflows=wfs, call=_never)
    assert r["action"] == "open_workflow" and r["slots"]["workflow_id"] == "w1"
    assert route_intent("take me to something", ["open_workflow"], workflows=wfs,
                        call=lambda *_a: {"action": "open_workflow", "slots": {"workflow_id": "not-mine"}}) is None
    assert route_intent("go to refunds", ["open_workflow"], workflows=wfs, call=_never)["slots"]["workflow_id"] == "w2"


# ── the harness ─────────────────────────────────────────────────────────────────────────────

_LC = {"stage": "test", "allowed_actions": ["test", "edit"], "note": "", "substate": "ready", "glyph": "warn", "rows": []}


def _turn(message, executor, monkeypatch, lc=_LC):
    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", _never)
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: None)
    return harness.run_turn(thread_id="wf:w1", message=message, sink=None, lifecycle=lc, flow_executor=executor,
                            workflow={"id": "w1", "name": "W"})


def test_a_side_effect_command_posts_a_confirm_card_and_runs_nothing(monkeypatch):
    ran: list = []
    out = _turn("test it", lambda a, s: ran.append(a) or {"ok": True}, monkeypatch)
    assert ran == [] and out["routed"] == "test"
    card = next(i for i in ts.load("wf:w1")["items"] if i.get("card_type") == "confirm_action")
    assert card["state"] == "open" and card["action"] == "test" and card["actions"] == ["confirm", "cancel"]
    assert "nothing has happened yet" in out["reply"]


def test_a_command_without_a_side_effect_runs_and_reports(monkeypatch):
    out = _turn("stop", lambda a, s: {"ok": True, "message": "Stopping the test."}, monkeypatch,
                lc={**_LC, "allowed_actions": ["stop_test"]})
    assert out["reply"] == "Stopping the test." and out["routed"] == "stop_test"


def test_navigation_becomes_a_link_card_not_a_navigation(monkeypatch):
    out = _turn("open human edit", lambda a, s: {"ok": True}, monkeypatch)
    card = next(i for i in ts.load("wf:w1")["items"] if i.get("card_type") == "action_link")
    assert out["routed"] == "edit" and card["target"] == "edit" and card["actions"] == ["open"]


def test_a_blocked_action_is_just_conversation(monkeypatch):
    seen: list = []
    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", lambda task, payload, ms, *, on_delta, error_detail=None: (on_delta("ok"), seen.append(task))[1] or {})
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: None)
    out = harness.run_turn(thread_id="wf:w2", message="approve", sink=None, lifecycle=_LC, flow_executor=lambda a, s: {"ok": True},
                           workflow={"id": "w2", "name": "W"})
    assert "routed" not in out and seen == ["studio_chat_reply"]   # approve is not allowed before a passing test


def test_a_command_beats_an_open_question_only_when_it_is_not_an_answer(monkeypatch):
    ts.append_card("wf:w3", "clarify_question", "open", title="Which?", choices=[{"id": "a", "label": "Alpha"}, {"id": "b", "label": "Beta"}])
    ran: list = []
    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", _never)
    monkeypatch.setattr("conxa_compile.llm.client.call_llm", lambda *a, **k: {"choice": "unclear"})
    out = harness.run_turn(thread_id="wf:w3", message="edit", sink=None, lifecycle={**_LC, "allowed_actions": ["answer_clarify", "edit"]},
                           card_executor=lambda c, a, b: ran.append(b) or {}, flow_executor=lambda a, s: {"ok": True},
                           workflow={"id": "w3", "name": "W"})
    assert out["routed"] == "edit" and ran == []


def test_the_renderers_action_table_names_only_real_actions_and_mirrors_their_kind():
    """lib/actions.ts is a small mirror of actions.py for the strip's labels; the ids and flow/nav kind must agree."""
    import os
    import re

    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "electron", "renderer", "src", "lib", "actions.ts"), encoding="utf-8").read()
    rows = re.findall(r"^\s+(\w+): \{ label: '[^']*', kind: '(flow|nav)'", src, re.M)
    assert rows
    for action_id, kind in rows:
        assert action_id in ACTIONS and ACTIONS[action_id].kind == kind, action_id
