"""Recording a VARIANT take (the case where an outcome fork's popup does not appear) must never touch
the workflow's own recording: not on start, not on stop, not on cancel, and not when it is empty."""

from __future__ import annotations

import json
import os
import sys
import threading

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_core.config import settings  # noqa: E402
from conxa_core.storage.group_store import add_app, create_group, set_group_app_auth, set_group_app_checked  # noqa: E402
from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow, set_recording  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402
from handlers.session import SessionMixin  # noqa: E402
import handlers.session as session_module  # noqa: E402


class _Harness(SessionMixin):
    def __init__(self):
        self._rec_lock = threading.Lock()
        self._active_recording = None
        self._loop = type("L", (), {"run": staticmethod(lambda _coro: None)})()


class _FakeSess:
    def __init__(self, sid="variant1", events=None, variant_of=None):
        self.session_id = sid
        self._events = events if events is not None else [{"action": {"action": "click", "timestamp": "2026-01-01T00:00:00.000Z"}}]
        if variant_of:
            self.variant_of = variant_of

    def start(self):
        return None

    def stop(self):
        return None

    def snapshot_events(self):
        return self._events


@pytest.fixture()
def wf(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    group = create_group("Sales", workspace_id="wrk_local")
    group = add_app(group.id, "Drive", "https://drive.test/login", "https://drive.test/")
    app = group.apps[0]
    state = tmp_path / "drive.json"
    state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    group = set_group_app_auth(group.id, app.id, str(state))
    set_group_app_checked(group.id, app.id, "ready")
    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", lambda _app: "ready")
    workflow = create_workflow("Upload a file", "https://drive.test/app", group_id=group.id)
    set_recording(workflow.id, "primary")
    workflow = get_workflow(workflow.id)
    workflow.visited_hosts = ["drive.test"]
    workflow.recording_duration_seconds = 42.0
    workflow.skill_id = "skill_primary"
    workflow.recording_status = "compiled"
    save_workflow(workflow)
    return workflow


def _primary_intact(workflow_id):
    now = get_workflow(workflow_id)
    assert now.session_id == "primary"
    assert now.skill_id == "skill_primary" and now.recording_status == "compiled"
    assert now.visited_hosts == ["drive.test"] and now.recording_duration_seconds == 42.0


def test_starting_a_variant_registers_it_without_replacing_the_primary(wf, monkeypatch):
    created = []
    monkeypatch.setattr(session_module._recorder_registry, "create", lambda **kw: created.append(kw) or _FakeSess("variant1"))
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    result = _Harness().cmd_start_recording({"workflow_id": wf.id, "variant_of": "keyA"}, "rid")

    assert result["variant_of"] == "keyA" and result["session_id"] == "variant1"
    _primary_intact(wf.id)
    assert [v["session_id"] for v in get_workflow(wf.id).variant_session_ids] == ["variant1"]
    assert "previous_recording_backup" not in result  # nothing was replaced, so nothing to back up


def test_a_normal_start_still_replaces_the_primary_and_forgets_old_variants(wf, monkeypatch):
    from conxa_core.storage.workflow_store import add_variant

    add_variant(wf.id, "stale", "keyA")
    monkeypatch.setattr(session_module._recorder_registry, "create", lambda **kw: _FakeSess("fresh"))
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    _Harness().cmd_start_recording({"workflow_id": wf.id}, "rid")

    now = get_workflow(wf.id)
    assert now.session_id == "fresh" and now.variant_session_ids == []


def test_stopping_a_variant_leaves_the_primarys_hosts_duration_and_compile_alone(wf, monkeypatch):
    from conxa_core.storage.workflow_store import add_variant

    add_variant(wf.id, "variant1", "keyA")
    sess = _FakeSess("variant1", variant_of="keyA", events=[
        {"action": {"action": "navigate", "timestamp": "2026-01-01T00:00:00.000Z", "url": "https://elsewhere.test/"}},
        {"action": {"action": "click", "timestamp": "2026-01-01T00:09:00.000Z"}},
    ])
    monkeypatch.setattr(session_module._recorder_registry, "get", lambda _sid: sess)

    result = _Harness().cmd_stop_recording({"session_id": "variant1", "workflow_id": wf.id}, "rid")

    assert result["status"] == "variant_recorded" and result["event_count"] == 2
    _primary_intact(wf.id)


def test_an_empty_variant_is_dropped_and_the_primary_survives(wf, monkeypatch):
    from conxa_core.storage.workflow_store import add_variant

    add_variant(wf.id, "variant1", "keyA")
    sess = _FakeSess("variant1", variant_of="keyA", events=[])
    monkeypatch.setattr(session_module._recorder_registry, "get", lambda _sid: sess)

    with pytest.raises(_CommandError) as exc:
        _Harness().cmd_stop_recording({"session_id": "variant1", "workflow_id": wf.id}, "rid")

    assert exc.value.code == "empty_recording"
    _primary_intact(wf.id)
    assert get_workflow(wf.id).variant_session_ids == []


def test_cancelling_a_variant_never_clears_the_primary_recording(wf, monkeypatch):
    from conxa_core.storage.workflow_store import add_variant

    add_variant(wf.id, "variant1", "keyA")
    sess = _FakeSess("variant1", variant_of="keyA")
    monkeypatch.setattr(session_module._recorder_registry, "get", lambda _sid: sess)
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    _Harness().cmd_cancel_recording({"session_id": "variant1", "workflow_id": wf.id}, "rid")

    _primary_intact(wf.id)
    assert get_workflow(wf.id).variant_session_ids == []


def test_cancelling_a_normal_recording_still_clears_it(wf, monkeypatch):
    sess = _FakeSess("primary")
    monkeypatch.setattr(session_module._recorder_registry, "get", lambda _sid: sess)
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    _Harness().cmd_cancel_recording({"session_id": "primary", "workflow_id": wf.id}, "rid")

    assert get_workflow(wf.id).session_id is None
