"""cmd_start_recording must fail fast (not hang) when a group app's saved
session has expired, and must record why — see FIX.md 2026-08-15: the same
probe used to run unbounded and could wedge the whole backend."""

from __future__ import annotations

import os
import sys
import threading
import time

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_core.config import settings  # noqa: E402
from conxa_core.storage.group_store import add_app, create_group, get_group, set_group_app_auth  # noqa: E402
from conxa_core.storage.workflow_store import create_workflow  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402
from handlers.session import SessionMixin  # noqa: E402
import handlers.session as session_module  # noqa: E402


class _Harness(SessionMixin):
    def __init__(self):
        self._rec_lock = threading.Lock()
        self._active_recording = None
        self._loop = type("L", (), {"run": staticmethod(lambda _coro: None)})()


@pytest.fixture()
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _group_with_captured_app(isolated_data_dir, state_body: str):
    group = create_group("Sales", workspace_id="wrk_local")
    group = add_app(group.id, "Render", "https://x.test/login", "https://x.test/")
    app = group.apps[0]
    state_path = isolated_data_dir / "state.json"
    state_path.write_text(state_body, encoding="utf-8")
    return set_group_app_auth(group.id, app.id, str(state_path))


def _age_checked_at(group_id, app_id, seconds_ago):
    """set_group_app_auth stamps checked_at fresh on capture (a just-completed login
    is inherently verified), so a test that wants the recording gate to actually
    re-probe an app must push checked_at outside the gate's TTL first."""
    group = get_group(group_id)
    app = next(a for a in group.apps if a.id == app_id)
    app.checked_at -= seconds_ago
    from conxa_core.storage.group_store import save_group

    return save_group(group)


def test_expired_app_session_fails_fast_and_records_why(isolated_data_dir, monkeypatch):
    group = _group_with_captured_app(isolated_data_dir, "{}")
    _age_checked_at(group.id, group.apps[0].id, 700)
    workflow = create_workflow("Create a lead", "https://x.test/app", group_id=group.id)

    monkeypatch.setattr("conxa_compile.recorder.session.check_group_sessions_sync", lambda targets, _apps, **_kw: {a.id: "expired" for a in targets})

    h = _Harness()
    started = time.monotonic()
    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    elapsed = time.monotonic() - started

    assert exc_info.value.code == "auth_required"
    assert "Render" in exc_info.value.message
    assert elapsed < 5, f"gate should fail fast, took {elapsed:.2f}s"

    reloaded = get_group(group.id)
    assert reloaded.apps[0].last_error == "Session expired — sign in again."


def test_ready_app_session_proceeds_past_the_gate(isolated_data_dir, monkeypatch):
    """A session the probe reports as still valid must not be blocked or
    have its last_error touched — only the expired branch writes it."""
    group = _group_with_captured_app(isolated_data_dir, '{"cookies": [], "origins": []}')
    workflow = create_workflow("Create a lead", "https://x.test/app", group_id=group.id)

    monkeypatch.setattr("conxa_compile.recorder.session.check_group_sessions_sync", lambda targets, _apps, **_kw: {a.id: "ready" for a in targets})

    class _FakeSess:
        session_id = "sess1"

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(session_module._recorder_registry, "create", lambda **_kw: _FakeSess())
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    h = _Harness()
    result = h.cmd_start_recording({"workflow_id": workflow.id}, "rid")

    assert result["session_id"] == "sess1"
    reloaded = get_group(group.id)
    assert reloaded.apps[0].last_error == ""
