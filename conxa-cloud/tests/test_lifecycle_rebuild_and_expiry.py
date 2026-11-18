"""W5: a compiled workflow can always be built again; a Confirm card is only good while its action is allowed."""

from __future__ import annotations

from conxa_compile.conversation import thread_store as ts
from conxa_compile.conversation.actions import ACTIONS
from conxa_compile.conversation.lifecycle import derive_lifecycle, expire_stale_cards, lifecycle_rev

COMPILED = {"id": "w1", "session_id": "s", "skill_id": "k", "target_url": "https://x.test", "last_test_status": "passed", "signed_off": True}


def test_a_compiled_workflow_can_be_built_again_in_every_idle_state():
    for wf in (COMPILED, {**COMPILED, "last_test_status": "failed"}, {**COMPILED, "last_test_status": "never", "signed_off": False}):
        assert "rebuild" in derive_lifecycle(wf)["allowed_actions"]
    # ...even while a question is open (the new build replaces the questions)
    assert "rebuild" in derive_lifecycle(COMPILED, clarify={"open": 2, "unanswered": 2})["allowed_actions"]


def test_nothing_offers_a_rebuild_while_something_is_running():
    for flag in ("compiling", "testing", "understanding", "recording", "replying"):
        assert "rebuild" not in derive_lifecycle(COMPILED, runtime={flag: True})["allowed_actions"], flag
    assert "rebuild" not in derive_lifecycle({"id": "w2", "session_id": "s", "target_url": "https://x.test"})["allowed_actions"]


def test_rebuild_is_a_confirmed_action_with_a_plain_warning():
    spec = ACTIONS["rebuild"]
    assert spec.side_effect and "test it again" in spec.describe.lower() or "new test" in spec.describe


def test_the_revision_changes_when_what_the_person_may_do_changes():
    a = derive_lifecycle(COMPILED)
    b = derive_lifecycle({**COMPILED, "last_test_status": "failed"})
    assert a["rev"] != b["rev"] and a["rev"] == lifecycle_rev(a["stage"], a["substate"], a["allowed_actions"])


def test_a_confirm_card_whose_action_is_no_longer_allowed_is_closed(tmp_path, monkeypatch):
    revised: list[tuple] = []
    monkeypatch.setattr(ts, "revise_card", lambda thread, card_id, **kw: revised.append((thread, card_id, kw)))
    items = [
        {"kind": "card", "card_type": "confirm_action", "state": "open", "action": "approve", "card_id": "c1"},
        {"kind": "card", "card_type": "confirm_action", "state": "open", "action": "test", "card_id": "c2"},
        {"kind": "card", "card_type": "confirm_action", "state": "done", "action": "approve", "card_id": "c3"},
        {"kind": "card", "card_type": "compile", "state": "open", "action": "approve", "card_id": "c4"},
    ]
    lc = {"allowed_actions": ["test", "edit"]}
    assert expire_stale_cards("w1", lc, items) == 1
    assert revised[0][1] == "c1" and revised[0][2]["state"] == "expired"
