"""cmd_start_recording gates on EVERY app in the workflow's group being connected and not
expired (AUTH-16 — the runtime's rule; a workflow that starts on one app routinely crosses
into another), and every workflow recording writes each app's refreshed session back to its
own saved file (see FIX.md 2026-08-16) without vouching for it — the next gate re-checks."""

from __future__ import annotations

import json
import os
import sys
import threading

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_core.config import settings  # noqa: E402
from conxa_core.storage.group_store import add_app, create_group, get_group, set_group_app_auth  # noqa: E402
from conxa_core.storage.workflow_store import create_workflow  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402
from handlers.session import SessionMixin, _refresh_group_app_sessions  # noqa: E402
import handlers.session as session_module  # noqa: E402


class _Harness(SessionMixin):
    def __init__(self):
        self._rec_lock = threading.Lock()
        self._active_recording = None
        self._loop = type("L", (), {"run": staticmethod(lambda _coro: None)})()


class _FakeSess:
    session_id = "sess1"

    def start(self):
        return None

    def stop(self):
        return None

    def snapshot_events(self):
        return [{"action": "click"}]


class _FakeSessWithTimestamps:
    """A recording whose events carry the real bridge.js action shape (nested
    ``action.timestamp``), unlike ``_FakeSess`` above — for tests that need a
    real recording_duration_seconds to come out of cmd_stop_recording."""

    session_id = "sess2"

    def stop(self):
        return None

    def snapshot_events(self):
        return [
            {"action": {"action": "click", "timestamp": "2026-01-01T00:00:00.000Z"}},
            {"action": {"action": "click", "timestamp": "2026-01-01T00:02:30.000Z"}},
        ]


@pytest.fixture()
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _captured_app(isolated_data_dir, group_id, name, login_url, success_url, state_body):
    group = add_app(group_id, name, login_url, success_url)
    app = next(a for a in group.apps if a.name == name)
    state_path = isolated_data_dir / f"{app.id}.json"
    state_path.write_text(state_body, encoding="utf-8")
    return set_group_app_auth(group_id, app.id, str(state_path)), app.id


def _age_checked_at(group_id, app_id, seconds_ago):
    """set_group_app_auth stamps checked_at fresh on capture (a just-completed login
    is inherently verified), so a test that wants the recording gate to actually
    re-probe an app must first push checked_at outside the gate's TTL — otherwise
    the fresh capture itself makes the probe a no-op."""
    from conxa_core.storage import group_store as _gs

    group = _gs.get_group(group_id)
    app = next(a for a in group.apps if a.id == app_id)
    app.checked_at -= seconds_ago
    return _gs.save_group(group)


def test_unconnected_app_the_start_url_does_not_match_still_gates_recording(isolated_data_dir, monkeypatch):
    """The reported bug: a workflow that starts on GitHub and uploads to Drive. Only GitHub
    matched the start URL, so recording opened with Drive signed out."""
    group = create_group("Sales", workspace_id="wrk_local")
    group = add_app(group.id, "Slack", "https://slack.test/login", "https://slack.test/")
    group, render_id = _captured_app(
        isolated_data_dir, group.id, "Render", "https://render.test/login", "https://render.test/", '{"cookies": [], "origins": []}'
    )
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    created = []
    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", lambda _app: "ready")
    monkeypatch.setattr(session_module._recorder_registry, "create", lambda **_kw: created.append(1) or _FakeSess())

    h = _Harness()
    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert exc_info.value.code == "auth_required"
    assert "Slack" in exc_info.value.message
    assert created == []  # the browser never opened


def test_recording_starts_once_every_group_app_is_signed_in(isolated_data_dir, monkeypatch):
    group = create_group("Sales", workspace_id="wrk_local")
    group, _ = _captured_app(
        isolated_data_dir, group.id, "Slack", "https://slack.test/login", "https://slack.test/", '{"cookies": [], "origins": []}'
    )
    group, _ = _captured_app(
        isolated_data_dir, group.id, "Render", "https://render.test/login", "https://render.test/", '{"cookies": [], "origins": []}'
    )
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    monkeypatch.setattr(session_module._recorder_registry, "create", lambda **_kw: _FakeSess())
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    h = _Harness()
    result = h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert result["session_id"] == "sess1"
    assert "warnings" not in result


def test_missing_required_app_still_gates_recording(isolated_data_dir, monkeypatch):
    group = create_group("Sales", workspace_id="wrk_local")
    group = add_app(group.id, "Render", "https://render.test/login", "https://render.test/")
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    h = _Harness()
    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert exc_info.value.code == "auth_required"
    assert "Render" in exc_info.value.message


def test_workflow_on_an_unmatched_site_is_still_gated(isolated_data_dir, monkeypatch):
    group = create_group("Sales", workspace_id="wrk_local")
    add_app(group.id, "Render", "https://render.test/login", "https://render.test/")
    workflow = create_workflow("Scrape a public page", "https://public.test/page", group_id=group.id)

    h = _Harness()
    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert exc_info.value.code == "auth_required"
    assert "Render" in exc_info.value.message


def test_refresh_writes_rotated_session_back_and_drops_foreign_domain(isolated_data_dir):
    group = create_group("Sales", workspace_id="wrk_local")
    group, app_id = _captured_app(
        isolated_data_dir,
        group.id,
        "Render",
        "https://render.test/login",
        "https://render.test/",
        json.dumps({"cookies": [{"name": "session", "domain": "render.test", "path": "/", "value": "old"}], "origins": []}),
    )
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    merged_dir = isolated_data_dir / "workflows" / workflow.id
    merged_dir.mkdir(parents=True)
    (merged_dir / "merged_group_state.json").write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "session", "domain": "render.test", "path": "/", "value": "new"},
                    {"name": "other", "domain": "other.test", "path": "/", "value": "foreign"},
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    before = get_group(group.id).apps[0].captured_at
    _refresh_group_app_sessions(workflow.id)
    after_group = get_group(group.id)
    after = after_group.apps[0]
    assert after.captured_at is not None and after.captured_at >= before
    assert after.last_error == ""
    assert after.checked_at is None  # written back, not verified — the next gate re-checks

    saved = json.loads((isolated_data_dir / f"{app_id}.json").read_text(encoding="utf-8"))
    assert saved["cookies"] == [{"name": "session", "domain": "render.test", "path": "/", "value": "new"}]


def test_refresh_is_a_noop_when_no_recording_ever_happened(isolated_data_dir):
    group = create_group("Sales", workspace_id="wrk_local")
    group, app_id = _captured_app(
        isolated_data_dir, group.id, "Render", "https://render.test/login", "https://render.test/", '{"cookies": [], "origins": []}'
    )
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    # No merged_group_state.json on disk for this workflow — must not raise.
    _refresh_group_app_sessions(workflow.id)

    saved = json.loads((isolated_data_dir / f"{app_id}.json").read_text(encoding="utf-8"))
    assert saved == {"cookies": [], "origins": []}


def test_expired_required_app_blocks_recording(isolated_data_dir, monkeypatch):
    group = create_group("Sales", workspace_id="wrk_local")
    group, render_id = _captured_app(
        isolated_data_dir, group.id, "Render", "https://render.test/login", "https://render.test/", '{"cookies": [], "origins": []}'
    )
    _age_checked_at(group.id, render_id, 700)  # past the gate's probe TTL, so it re-checks
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", lambda _app: "expired")

    h = _Harness()
    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert exc_info.value.code == "auth_required"
    assert "Render" in exc_info.value.message
    assert "expired" in exc_info.value.message


def test_expired_sibling_app_blocks_recording(isolated_data_dir, monkeypatch):
    """An expired app the start URL doesn't match is just as fatal mid-recording as one it
    does — it used to only warn, and the recording opened signed out of it."""
    group = create_group("Sales", workspace_id="wrk_local")
    group, render_id = _captured_app(
        isolated_data_dir, group.id, "Render", "https://render.test/login", "https://render.test/", '{"cookies": [], "origins": []}'
    )
    group, slack_id = _captured_app(
        isolated_data_dir, group.id, "Slack", "https://slack.test/login", "https://slack.test/", '{"cookies": [], "origins": []}'
    )
    _age_checked_at(group.id, render_id, 700)
    _age_checked_at(group.id, slack_id, 700)
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    def _probe(app):
        return "expired" if app.name == "Slack" else "ready"

    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", _probe)

    h = _Harness()
    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert exc_info.value.code == "auth_required"
    assert "Slack" in exc_info.value.message
    assert "Render" not in exc_info.value.message


def test_a_discarded_recording_does_not_clear_an_expired_verdict(isolated_data_dir, monkeypatch):
    """Ending a recording copies the browser's cookies back. That used to stamp every app
    verified, so the next record click skipped the check and opened signed out."""
    group = create_group("Sales", workspace_id="wrk_local")
    group, slack_id = _captured_app(
        isolated_data_dir, group.id, "Slack", "https://slack.test/login", "https://slack.test/",
        json.dumps({"cookies": [{"name": "s", "domain": "slack.test", "path": "/", "value": "dead"}], "origins": []}),
    )
    _age_checked_at(group.id, slack_id, 700)
    workflow = create_workflow("Create a lead", "https://slack.test/app", group_id=group.id)
    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", lambda _app: "expired")

    h = _Harness()
    with pytest.raises(_CommandError):
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")

    merged_dir = isolated_data_dir / "workflows" / workflow.id
    merged_dir.mkdir(parents=True, exist_ok=True)
    (merged_dir / "merged_group_state.json").write_text(
        json.dumps({"cookies": [{"name": "s", "domain": "slack.test", "path": "/", "value": "still-dead"}], "origins": []}),
        encoding="utf-8",
    )
    _refresh_group_app_sessions(workflow.id)

    with pytest.raises(_CommandError) as exc_info:
        h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert "Slack" in exc_info.value.message


def test_recently_checked_app_is_not_reprobed(isolated_data_dir, monkeypatch):
    """checked_at within the TTL must skip the probe entirely — otherwise every
    record click launches a headless Chromium per captured app, which is exactly
    the cost the old always-probe design was removed for."""
    from conxa_core.storage.group_store import set_group_app_checked

    group = create_group("Sales", workspace_id="wrk_local")
    group, render_id = _captured_app(
        isolated_data_dir, group.id, "Render", "https://render.test/login", "https://render.test/", '{"cookies": [], "origins": []}'
    )
    set_group_app_checked(group.id, render_id, "ready")

    calls = []

    def _probe(app):
        calls.append(app.id)
        return "ready"

    monkeypatch.setattr("conxa_compile.recorder.session.check_app_session_sync", _probe)
    monkeypatch.setattr(session_module._recorder_registry, "create", lambda **_kw: _FakeSess())
    monkeypatch.setattr(session_module._recorder_registry, "pop", lambda _sid: None)

    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)
    h = _Harness()
    h.cmd_start_recording({"workflow_id": workflow.id}, "rid")
    assert calls == []  # freshly checked — no probe needed


def test_stop_recording_writes_back_session_before_returning(isolated_data_dir, monkeypatch):
    group = create_group("Sales", workspace_id="wrk_local")
    group, app_id = _captured_app(
        isolated_data_dir,
        group.id,
        "Render",
        "https://render.test/login",
        "https://render.test/",
        json.dumps({"cookies": [{"name": "session", "domain": "render.test", "path": "/", "value": "old"}], "origins": []}),
    )
    workflow = create_workflow("Create a lead", "https://render.test/app", group_id=group.id)

    merged_dir = isolated_data_dir / "workflows" / workflow.id
    merged_dir.mkdir(parents=True)
    (merged_dir / "merged_group_state.json").write_text(
        json.dumps({"cookies": [{"name": "session", "domain": "render.test", "path": "/", "value": "new"}], "origins": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(session_module._recorder_registry, "get", lambda _sid: _FakeSess())

    h = _Harness()
    h.cmd_stop_recording({"session_id": "sess1", "workflow_id": workflow.id}, "rid")

    saved = json.loads((isolated_data_dir / f"{app_id}.json").read_text(encoding="utf-8"))
    assert saved["cookies"][0]["value"] == "new"


def test_recording_duration_seconds_diffs_first_and_last_event_timestamp():
    from handlers.session import _recording_duration_seconds

    events = [
        {"action": {"action": "click", "timestamp": "2026-01-01T00:00:00.000Z"}},
        {"action": {"action": "click", "timestamp": "2026-01-01T00:00:05.500Z"}},
        {"action": {"action": "click", "timestamp": "2026-01-01T00:01:00.000Z"}},
    ]
    assert _recording_duration_seconds(events) == 60.0


def test_recording_duration_seconds_tolerates_malformed_or_missing_timestamps():
    from handlers.session import _recording_duration_seconds

    # events shaped like _FakeSess above — "action" a bare string, not a nested dict.
    assert _recording_duration_seconds([{"action": "click"}]) is None
    assert _recording_duration_seconds([{"action": {"timestamp": "not-a-date"}}]) is None
    assert _recording_duration_seconds([{}]) is None


def test_stop_recording_computes_and_saves_recording_duration(isolated_data_dir, monkeypatch):
    from conxa_core.storage.workflow_store import get_workflow

    workflow = create_workflow("Create a lead", "https://render.test/app")
    monkeypatch.setattr(session_module._recorder_registry, "get", lambda _sid: _FakeSessWithTimestamps())

    h = _Harness()
    h.cmd_stop_recording({"session_id": "sess2", "workflow_id": workflow.id}, "rid")

    saved = get_workflow(workflow.id)
    assert saved.recording_duration_seconds == 150.0  # 2:30 between the two events
