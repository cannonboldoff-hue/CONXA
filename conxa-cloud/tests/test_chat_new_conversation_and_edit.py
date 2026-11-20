"""WF-12 12.7: "New conversation" (a divider the model reads nothing above) and "Edit and resend"
(only the person's last message, only while nothing but replies followed it). No network, no model."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from conxa_core.config import settings  # noqa: E402
from conxa_compile.conversation import harness, runtime_state  # noqa: E402
from conxa_compile.conversation import thread_store as ts  # noqa: E402

T = "wf:w127"


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    runtime_state.clear_all()


@pytest.fixture()
def calls(monkeypatch):
    """Every reply call's user text; each reply is "re: <message>"."""
    seen: list[str] = []

    def fake(task, payload, timeout_ms, *, on_delta, error_detail=None):
        seen.append(payload["user_text"])
        on_delta("re: " + payload["user_text"].rsplit("Person's message: ", 1)[1])
        return {}

    monkeypatch.setattr("conxa_compile.llm.client.stream_llm", fake)
    return seen


def _say(text: str, **kw):
    return harness.run_turn(thread_id=T, message=text, sink=None, lifecycle={"stage": "test"}, **kw)


def _shown() -> list[tuple[str, str]]:
    return [(i["kind"], i.get("text") or i.get("card_type") or "") for i in ts.load(T)["items"]]


# ── New conversation ────────────────────────────────────────────────────────────────────────


def test_a_new_conversation_hides_earlier_turns_from_the_model_but_not_from_the_person(calls):
    _say("the old topic")
    divider = ts.start_new_conversation(T)
    assert divider is not None and divider["kind"] == "new_conversation"
    _say("a fresh question")
    assert "the old topic" not in calls[-1]                      # the model starts after the divider
    assert [k for k, _ in _shown()] == ["user_message", "assistant_message", "new_conversation",
                                        "user_message", "assistant_message"]   # nothing removed
    _say("and a follow-up")
    assert "a fresh question" in calls[-1] and "the old topic" not in calls[-1]


def test_cards_above_the_divider_stay_open_and_known_to_the_model(calls):
    q = ts.append_card(T, "clarify_question", "open", title="Which account?", choices=[{"id": "a", "label": "A"}])
    ts.append(T, "user_message", text="hello")
    ts.start_new_conversation(T)
    assert [c["card_id"] for c in ts.open_cards(ts.fold(ts.read_raw(T)))] == [q["card_id"]]
    out = harness.run_turn(thread_id=T, message="what next?", sink=None, lifecycle={"stage": "test"})
    assert out["status"] == "done" and "Which account?" in calls[-1]    # the question still counts


def test_a_question_above_the_divider_can_still_be_answered():
    q = ts.append_card(T, "clarify_question", "open", title="Q", choices=[{"id": "yes", "label": "Yes"}])
    ts.append(T, "user_message", text="hi")
    ts.start_new_conversation(T)
    got: list = []
    harness.run_turn(thread_id=T, message="", sink=None, card_id=q["card_id"], choice="yes",
                     card_executor=lambda c, a, body: got.append((c["card_id"], body["choice"])) or {"ok": True})
    assert got == [(q["card_id"], "yes")]


def test_no_divider_without_anything_said_since_the_last_one():
    assert ts.start_new_conversation(T) is None                      # empty thread
    ts.append(T, "user_message", text="hi")
    assert ts.start_new_conversation(T) is not None
    assert ts.start_new_conversation(T) is None                      # nothing new since


def test_the_new_conversation_command_appends_and_tells_the_screen(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    spec = importlib.util.spec_from_file_location("cbackend_127", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from handlers import protocol

    out: list[dict] = []
    mod._write = out.append
    protocol._write = out.append
    b = mod.Backend()
    ts.append(T, "user_message", text="hi")
    item = b.cmd_thread_new_conversation({"thread_id": T}, "r1")["item"]
    assert item["kind"] == "new_conversation"
    assert b.cmd_thread_new_conversation({"thread_id": T}, "r2") == {"item": None}


# ── Edit and resend ─────────────────────────────────────────────────────────────────────────


def test_editing_the_last_message_replaces_it_and_its_reply(calls):
    _say("first")
    first = _say("wrong words")
    msg_id = next(i["id"] for i in first["items"] if i["kind"] == "user_message")
    events: list[dict] = []
    out = harness.run_turn(thread_id=T, message="right words", sink=events.append, lifecycle={"stage": "test"}, replaces=msg_id)
    assert out["status"] == "done"
    assert _shown() == [("user_message", "first"), ("assistant_message", "re: first"),
                        ("user_message", "right words"), ("assistant_message", "re: right words")]
    assert "wrong words" not in calls[-1]                            # the model never sees the replaced text
    retract = next(e["item"] for e in events if e.get("phase") == "thread_item" and e["item"]["kind"] == "retract")
    assert msg_id in retract["ids"] and len(retract["ids"]) == 2     # the screen is told to drop both
    assert any(i.get("text") == "wrong words" for i in ts.read_raw(T))   # the file keeps history


def test_only_the_last_message_can_be_edited(calls):
    a = _say("one")
    _say("two")
    old = next(i["id"] for i in a["items"] if i["kind"] == "user_message")
    with pytest.raises(ValueError, match="Only your last message"):
        _say("changed", replaces=old)
    assert [t for _, t in _shown()] == ["one", "re: one", "two", "re: two"]


@pytest.mark.parametrize("after", ["card", "card_revision", "divider", "event", "routed"])
def test_nothing_that_followed_the_message_can_be_orphaned(calls, after):
    early = ts.append_card(T, "clarify_question", "open", title="Q", choices=[{"id": "y", "label": "Y"}])
    msg = ts.append(T, "user_message", text="please change it")
    ts.append(T, "assistant_message", text="ok", **({"routed": "test"} if after == "routed" else {}))
    if after == "card":
        ts.append_card(T, "pending_change", "open", title="Change")
    elif after == "card_revision":
        ts.revise_card(T, early["card_id"], state="answered")        # folds back above the message
    elif after == "divider":
        ts.start_new_conversation(T)
    elif after == "event":
        ts.append(T, "event", text="Test started")
    assert ts.editable_message(ts.read_raw(T)) is None
    with pytest.raises(ValueError):
        _say("something else", replaces=msg["id"])
    assert any(i.get("id") == msg["id"] for i in ts.load(T)["items"])


def test_a_hidden_message_is_not_editable(calls):
    hidden = ts.append(T, "user_message", text="internal", hidden=True)
    assert ts.editable_message(ts.read_raw(T)) is None
    with pytest.raises(ValueError):
        _say("x", replaces=hidden["id"])


def test_an_edited_message_can_be_edited_again(calls):
    a = _say("v1")
    b = _say("v2", replaces=next(i["id"] for i in a["items"] if i["kind"] == "user_message"))
    _say("v3", replaces=next(i["id"] for i in b["items"] if i["kind"] == "user_message"))
    assert [t for _, t in _shown()] == ["v3", "re: v3"]


# ── honest refusals (WF-12 retest S3) ───────────────────────────────────────────────────────


def test_an_unclicked_human_edit_link_is_not_an_open_card_for_the_model(calls):
    ts.append_card(T, "action_link", "open", title="Open step 1 in Human Edit", actions=["open"])
    _say("change the page address of step 1")
    assert "No card is waiting on the person right now." in calls[-1]


def test_the_human_edit_link_does_not_claim_a_new_element():
    card = harness.post_editor_hint_card(T, None, {"step_key": "k1", "reason": "page addresses are set by a person"},
                                         {"steps": [{"step_key": "k1", "step_index": 0}]})
    assert card["title"].startswith("Open step 1 in Human Edit") and "new element" not in card["title"]
