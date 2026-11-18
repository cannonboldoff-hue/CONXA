"""W8: a runtime-reported sign-in expiry is written onto the group's apps, so "Sign in and test again" opens the window."""

from __future__ import annotations

from types import SimpleNamespace

from handlers.test_flow import TestFlowMixin


def _app(app_id: str, login: str, connected: bool = True):
    return SimpleNamespace(id=app_id, login_url=login, success_url="", captured_at=1 if connected else 0,
                           storage_state_path="x.json" if connected else "")


def test_the_app_the_workflow_starts_on_is_marked_expired(monkeypatch):
    marked: list[tuple[str, str, str]] = []
    group = SimpleNamespace(id="g", apps=[_app("a", "https://github.com/login"), _app("b", "https://drive.google.com/login")])
    monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _id: group)
    monkeypatch.setattr("conxa_core.storage.group_store.set_group_app_checked", lambda g, a, s: marked.append((g, a, s)))
    TestFlowMixin._tf_mark_session_expired(SimpleNamespace(group_id="g", target_url="https://github.com/x"))
    assert marked == [("g", "a", "expired")]


def test_with_no_matching_app_every_connected_one_is_marked(monkeypatch):
    marked: list[tuple[str, str, str]] = []
    group = SimpleNamespace(id="g", apps=[_app("a", "https://a.test/"), _app("b", "https://b.test/"), _app("c", "https://c.test/", connected=False)])
    monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _id: group)
    monkeypatch.setattr("conxa_core.storage.group_store.set_group_app_checked", lambda g, a, s: marked.append((g, a, s)))
    TestFlowMixin._tf_mark_session_expired(SimpleNamespace(group_id="g", target_url="https://elsewhere.test/"))
    assert [m[1] for m in marked] == ["a", "b"]


def test_no_group_is_not_an_error():
    TestFlowMixin._tf_mark_session_expired(SimpleNamespace(group_id="", target_url=""))
