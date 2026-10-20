"""Phase 2: Build Studio stdio backend dispatcher + input sanitization."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))


@pytest.fixture()
def backend():
    spec = importlib.util.spec_from_file_location(
        "cbackend", os.path.join(_PY_DIR, "backend.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out: list[dict] = []
    capture = lambda obj: out.append(obj)  # capture protocol output
    # _write lives in handlers.protocol (imported into backend.py's namespace);
    # command handlers emit events via handlers.protocol._event_sink, which
    # calls handlers.protocol._write directly, so both bindings need patching.
    from handlers import protocol as _protocol

    mod._write = capture
    _protocol._write = capture
    b = mod.Backend()
    return b, out


def _last(out):
    return out[-1]


def test_ping(backend):
    b, out = backend
    b.dispatch({"id": "1", "type": "ping", "payload": {}})
    assert _last(out)["type"] == "result"
    assert _last(out)["result"]["ok"] is True


def test_unknown_command(backend):
    b, out = backend
    b.dispatch({"id": "2", "type": "frobnicate", "payload": {}})
    assert _last(out)["type"] == "error"
    assert _last(out)["code"] == "unknown_command"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "x\x00y", ""])
def test_path_traversal_rejected(backend, bad):
    b, out = backend
    b.dispatch({"id": "3", "type": "stop_recording", "payload": {"session_id": bad}})
    assert _last(out)["type"] == "error"
    assert _last(out)["code"] == "invalid_input"


def test_missing_workflow_reported(backend):
    b, out = backend
    b.dispatch({"id": "4", "type": "get_workflow", "payload": {"workflow_id": "ghost"}})
    assert _last(out)["type"] == "error"
    assert _last(out)["code"] == "workflow_not_found"


def test_validation_module():
    from services.validation import InvalidInput, safe_identifier

    assert safe_identifier("skill_abc-123", "x") == "skill_abc-123"
    for bad in ["../etc", "a/b", "a\\b", "x\x00y", "  "]:
        with pytest.raises(InvalidInput):
            safe_identifier(bad, "x")


def test_proxy_router_injection_swaps_singleton(backend, monkeypatch):
    b, _out = backend
    monkeypatch.setenv("CONXA_CLERK_DOMAIN", "https://clerk.example.com")
    monkeypatch.setenv("CONXA_CLERK_CLIENT_ID", "client_x")

    b._install_proxy_router()
    from conxa_core import llm as core_llm
    from services.llm_proxy_client import LLMProxyClient

    assert isinstance(core_llm.get_router(), LLMProxyClient)


def test_compile_updated_only_bumps_metadata(backend, monkeypatch, tmp_path):
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    def fail_compile(*_args, **_kwargs):
        raise AssertionError("compile_updated must not recompile from recording")

    monkeypatch.setattr(b, "cmd_compile", fail_compile)

    skill_id = "skill_meta_bump"
    saved_step = {
        "id": "step_1",
        "selector": "#saved-human-edit-selector",
        "signals": {"human_edit_marker": "preserve"},
    }
    write_skill(
        skill_id,
        {
            "meta": {"id": skill_id, "title": "Original title", "version": 3},
            "skills": [{"id": skill_id, "steps": [saved_step]}],
            "inputs": [{"name": "service_name"}],
        },
    )

    result = b.cmd_compile_updated(
        {"skill_id": skill_id, "skill_title": "Renamed title"},
        "rid",
    )

    updated = read_skill(skill_id)
    assert result == {"skill_id": skill_id, "ok": True}
    assert updated["meta"]["title"] == "Renamed title"
    assert updated["meta"]["version"] == 4
    assert updated["skills"][0]["steps"] == [saved_step]
    assert updated["inputs"] == [{"name": "service_name"}]


def test_patch_step_rejects_a_patch_that_drops_a_required_assertion(backend, monkeypatch, tmp_path):
    # cmd_patch_step must call validate_editor_patch (conxa_compile/editor/patch_gate.py) before
    # persisting, so a manual edit in StepEditorPanel can't silently drop the enforced
    # post-condition on a consequential step (BUILD-3's companion wiring gap).
    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill
    from handlers.protocol import _CommandError

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    b, _out = backend
    skill_id = "skill_patch_gate"
    step = {
        "action": {"action": "fill", "value": "alice@example.com"},
        "intent": "Enter email address",
        "value": "alice@example.com",
        "target": {"primary_selector": "#email", "fallback_selectors": []},
        "signals": {},
        "validation": {
            "wait_for": {"type": "none"},
            "assertions": [
                {"type": "value_equals", "target": "#email", "expected": "alice@example.com", "required": True},
            ],
        },
    }
    write_skill(
        skill_id,
        {
            "meta": {"id": skill_id, "title": "Patch gate", "version": 1},
            "skills": [{"id": skill_id, "steps": [step]}],
        },
    )

    with pytest.raises(_CommandError, match="consequential_step_requires_required_assertion"):
        b.cmd_patch_step(
            {"skill_id": skill_id, "step_index": 0, "patch": {"validation": {"assertions": []}}},
            "rid",
        )

    # Rejected patch must not be persisted.
    unchanged = read_skill(skill_id)
    assert unchanged["skills"][0]["steps"][0]["validation"]["assertions"] == step["validation"]["assertions"]
    assert unchanged["meta"]["version"] == 1


def test_patch_step_allows_an_unrelated_patch_that_keeps_the_required_assertion(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings
    from conxa_core.storage.json_store import read_skill, write_skill

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    b, _out = backend
    skill_id = "skill_patch_gate_ok"
    step = {
        "action": {"action": "fill", "value": "alice@example.com"},
        "intent": "Enter email address",
        "value": "alice@example.com",
        "target": {"primary_selector": "#email", "fallback_selectors": []},
        "signals": {},
        "validation": {
            "wait_for": {"type": "none"},
            "assertions": [
                {"type": "value_equals", "target": "#email", "expected": "alice@example.com", "required": True},
            ],
        },
    }
    write_skill(
        skill_id,
        {
            "meta": {"id": skill_id, "title": "Patch gate ok", "version": 1},
            "skills": [{"id": skill_id, "steps": [step]}],
        },
    )

    result = b.cmd_patch_step(
        {"skill_id": skill_id, "step_index": 0, "patch": {"intent": "Enter the customer email"}},
        "rid",
    )

    assert result["skill_id"] == skill_id
    updated = read_skill(skill_id)
    assert updated["skills"][0]["steps"][0]["intent"] == "Enter the customer email"
    assert updated["skills"][0]["steps"][0]["validation"]["assertions"] == step["validation"]["assertions"]


def test_installer_publish_rewrites_pack_with_cloud_tracking(backend, monkeypatch, tmp_path):
    import urllib.request

    b, _out = backend
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    b._cloud_api = "https://apis.conxa.in"
    b._auth = SimpleNamespace(get_token=lambda: "studio-token")

    packs_dir = tmp_path / "skill-packs" / "render"
    packs_dir.mkdir(parents=True)
    (packs_dir / "pack.json").write_text(
        json.dumps(
            {
                "company": "render",
                "company_display": "Render",
                "skill_pack_version": "0.1.0",
                "target_url": "https://dashboard.render.com",
                "protected_url": "https://dashboard.render.com/",
                "skills": ["delete-a-service"],
                "skill_groups": {"delete-a-service": "_default"},
                "tracking": {"tracking_url": "http://127.0.0.1:8000/api/tracking/render/events"},
            }
        ),
        encoding="utf-8",
    )
    skill_dir = packs_dir / "_default" / "delete-a-service"
    skill_dir.mkdir(parents=True)
    (skill_dir / "execution.json").write_text("[]", encoding="utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "tracking": {
                        "enabled": True,
                        "tracking_url": "https://internal.example/api/tracking/render/events",
                        "tracking_token": "cloud-token",
                        "company_id": "render",
                        "schema_version": 1,
                        "protocol_version": 1,
                    },
                    "sync_token": "sync-token",
                    "workspace_id": "wrk_123",
                    "published_at": 123.0,
                }
            ).encode("utf-8")

    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    logs: list[dict] = []

    publish_info = b._publish_skill_pack(
        company_slug="render",
        company_name="Render",
        skill_slug="delete-a-service",
        version="1.2.3",
        release_notes="Release message",
        group_id="_default",
        sink=logs.append,
    )

    rewritten = json.loads((packs_dir / "pack.json").read_text(encoding="utf-8"))
    # _installer_generation() probes GET /api/v1/workflows/generations first (via the
    # same mocked urlopen); its GET has no body, so fake_urlopen's req.data.decode()
    # raises inside itself, which _cloud_json converts to _CommandError and
    # _installer_generation() catches, falling back to "v2" — the actual publish
    # POST happens second and is what `seen` reflects by the time we assert.
    assert seen["url"] == "https://apis.conxa.in/api/v1/workflows/v2/skill-packs/upload"
    assert seen["auth"] == "Bearer studio-token"
    assert seen["body"]["skill_pack_version"] == "1.2.3"
    assert seen["body"]["release_notes"] == "Release message"
    assert rewritten["skill_pack_version"] == "1.2.3"
    assert rewritten["release_notes"] == "Release message"
    assert rewritten["installer_version"] == "v2"
    # tracking_url/sync_endpoint are now trusted verbatim from the cloud's response
    # (already correctly versioned server-side) rather than reconstructed locally.
    assert rewritten["tracking"]["tracking_url"] == "https://internal.example/api/tracking/render/events"
    assert rewritten["tracking"]["tracking_token"] == "cloud-token"
    assert rewritten["sync_endpoint"] == "https://apis.conxa.in/api/v1/workflows/v2/render/skill-packs/delta"
    assert rewritten["sync_token"] == "sync-token"
    assert publish_info["slug"] == "render"
    assert publish_info["version"] == "1.2.3"
    assert publish_info["workspace_id"] == "wrk_123"
    assert publish_info["tracking_url"] == "https://internal.example/api/tracking/render/events"
    assert publish_info["tracking_token_present"] is True
    assert publish_info["sync_endpoint"] == rewritten["sync_endpoint"]
    assert any("workspace wrk_123" in entry["message"] for entry in logs)


def test_installer_publish_skips_gracefully_when_local_cloud_unreachable(backend, monkeypatch, tmp_path):
    """A local dev cloud that isn't running should degrade to a skip, not a crash —
    but the same failure against a real, non-local cloud must still be fatal."""
    import urllib.request

    b, _out = backend
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    b._auth = SimpleNamespace(get_token=lambda: "studio-token")

    packs_dir = tmp_path / "skill-packs" / "render"
    packs_dir.mkdir(parents=True)
    (packs_dir / "pack.json").write_text(
        json.dumps({"company": "render", "skills": []}), encoding="utf-8"
    )

    def fake_urlopen_refused(req, timeout):
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_refused)

    # Local API base: skip gracefully.
    b._cloud_api = "http://127.0.0.1:8000"
    logs: list[dict] = []
    result = b._publish_skill_pack(
        company_slug="render",
        company_name="Render",
        skill_slug="delete-a-service",
        version="1.0.0",
        release_notes="",
        group_id="_default",
        sink=logs.append,
    )
    # Truthy sentinel, not {} — a caller that does `if not publish_info: raise` must
    # not mistake a graceful skip for a failure (that was the actual bug: {} is falsy).
    assert result == {"skipped": True, "slug": "render", "version": "1.0.0"}
    assert any("skipped" in entry["message"] for entry in logs)

    # Real, non-local cloud: the same unreachability must still fail the build.
    b._cloud_api = "https://apis.conxa.in"
    with pytest.raises(Exception, match="Cloud publish failed"):
        b._publish_skill_pack(
            company_slug="render",
            company_name="Render",
            skill_slug="delete-a-service",
            version="1.0.0",
            release_notes="",
            group_id="_default",
            sink=lambda _e: None,
        )


def test_cmd_build_installer_forwards_release_metadata(backend, monkeypatch, tmp_path):
    """Build Installer no longer publishes as a side effect — it requires a
    skill pack already published (sync_token present in pack.json) and just
    packages + optionally uploads it."""
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.skill_pack_store import get_or_create_skill_pack
    from conxa_core.workspace import LOCAL_WORKSPACE_ID
    from services import installer_builder

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    get_or_create_skill_pack(LOCAL_WORKSPACE_ID, display_name="Render")

    packs_dir = tmp_path / "skill-packs" / "wrk_local"
    packs_dir.mkdir(parents=True)
    (packs_dir / "pack.json").write_text(
        json.dumps({"company": "render", "skills": [], "sync_token": "sync-token"}), encoding="utf-8"
    )

    seen: dict[str, object] = {}

    def fake_build(workspace_id, **kwargs):
        from conxa_core.workspace import workspace_dir_slug
        seen["build_workspace_id"] = workspace_id
        seen["build"] = kwargs
        company_slug = workspace_dir_slug(workspace_id)
        return {
            "installer_path": str(tmp_path / "Render-Setup.exe"),
            "filename": "Render-Setup.exe",
            "company": company_slug,
            "workspace_id": workspace_id,
            "version": kwargs["version"],
            "runtime_version": "v1.0.0",
            "release_notes": kwargs["release_notes"],
        }

    def fake_upload(**kwargs):
        seen["upload"] = kwargs
        return dict(kwargs["result"], cloud_download_url="https://apis.conxa.in/api/v1/installers/render")

    monkeypatch.setattr(installer_builder, "build_installer", fake_build)
    monkeypatch.setattr(b, "_upload_installer_for_download", fake_upload)

    result = b.cmd_build_installer(
        {
            "company_slug": "render",
            "logo_path": "C:/logo.png",
            "version": "2.0.0",
            "release_notes": "Ship it",
        },
        "rid",
    )

    assert seen["build"]["version"] == "2.0.0"
    assert seen["build"]["release_notes"] == "Ship it"
    assert seen["upload"]["release_notes"] == "Ship it"
    assert result["version"] == "2.0.0"
    assert result["release_notes"] == "Ship it"


def test_cmd_build_installer_requires_published_skill_pack(backend, monkeypatch, tmp_path):
    """Build Installer is a secondary, advanced action — it must refuse to run
    until Publish Skill Package has actually shipped a release."""
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.skill_pack_store import get_or_create_skill_pack
    from conxa_core.workspace import LOCAL_WORKSPACE_ID

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    get_or_create_skill_pack(LOCAL_WORKSPACE_ID, display_name="Render")

    with pytest.raises(Exception, match="Publish a skill pack release"):
        b.cmd_build_installer(
            {"company_slug": "render", "version": "1.0.0", "release_notes": "notes"},
            "rid",
        )


def test_cmd_build_installer_non_fatal_when_upload_fails(backend, monkeypatch, tmp_path):
    """Installer upload to the cloud is optional — a failure there must not
    fail the build, only surface as a warning field on the result."""
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.skill_pack_store import get_or_create_skill_pack
    from conxa_core.workspace import LOCAL_WORKSPACE_ID
    from handlers.protocol import _CommandError
    from services import installer_builder

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    get_or_create_skill_pack(LOCAL_WORKSPACE_ID, display_name="Render")

    packs_dir = tmp_path / "skill-packs" / "wrk_local"
    packs_dir.mkdir(parents=True)
    (packs_dir / "pack.json").write_text(
        json.dumps({"company": "render", "skills": [], "sync_token": "sync-token"}), encoding="utf-8"
    )

    def fake_build(workspace_id, **kwargs):
        from conxa_core.workspace import workspace_dir_slug
        company_slug = workspace_dir_slug(workspace_id)
        return {
            "installer_path": str(tmp_path / "Render-Setup.exe"),
            "filename": "Render-Setup.exe",
            "company": company_slug,
            "workspace_id": workspace_id,
            "version": kwargs["version"],
            "runtime_version": "v1.0.0",
            "release_notes": kwargs["release_notes"],
        }

    def fake_upload_fails(**_kwargs):
        raise _CommandError("installer_upload_failed", "Installer upload failed: connection refused")

    monkeypatch.setattr(installer_builder, "build_installer", fake_build)
    monkeypatch.setattr(b, "_upload_installer_for_download", fake_upload_fails)

    result = b.cmd_build_installer(
        {"company_slug": "render", "version": "1.0.0", "release_notes": "notes"},
        "rid",
    )

    assert result["cloud_upload_error"] == "installer_upload_failed"
    assert "connection refused" in result["cloud_upload_error_message"]
    assert result["installer_path"] == str(tmp_path / "Render-Setup.exe")


def test_finish_group_app_auth_marks_group_and_workflows_ready(backend, monkeypatch, tmp_path):
    """Superseded test_auth_stop_recording_marks_workflow_ready — auth is now
    captured once per group app (cmd_finish_group_app_auth), not per workflow."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app
    from conxa_core.storage.workflow_store import create_workflow, get_workflow

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "https://example.test/dashboard")
    app_id = group.apps[0].id
    workflow = create_workflow("Example Workflow", "https://example.test", group_id=group.id)

    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    class FakeSession:
        reached_wait_url = False  # RecordingSession always has it; False = the user closed the window by hand
        browser_open = False  # by the time finish is called, the window is already gone -> no learn_auth attempt
        async def stop(self):
            # Mirrors RecordingSession.stop(): the recorder thread forces a final
            # storage_state autosave right before it tears down (session.py L1074).
            # Non-empty cookies here matter — cmd_finish_group_app_auth now rejects an
            # empty storage state (see _has_captured_session) as "closed before signing
            # in", so a genuinely captured session needs at least one cookie to exercise
            # this test's actual success path.
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "abc"}], "origins": []}),
                encoding="utf-8",
            )

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id},
        "rid",
    )

    assert result["auth"]["ready"] is True
    assert result["auth"]["apps_authenticated"] == 1
    # closed by hand, never reached success_url -> advisory (the session itself is still saved and ready)
    assert "https://example.test/dashboard" in result["auth"]["apps"][0]["detect_warning"]
    updated = get_workflow(workflow.id)
    assert updated is not None
    assert updated.status == "ready"


def test_finish_group_app_auth_has_no_warning_when_success_url_was_reached(backend, monkeypatch, tmp_path):
    """A login that self-detected success_url is exactly what a run will also detect — nothing to warn about."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "https://example.test/dashboard")
    app_id = group.apps[0].id
    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    class FakeSession:
        reached_wait_url = True
        browser_open = False  # by the time finish is called, the window is already gone -> no learn_auth attempt

        async def stop(self):
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "abc"}], "origins": []}),
                encoding="utf-8",
            )

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id},
        "rid",
    )

    assert result["auth"]["apps"][0]["detect_warning"] == ""

def test_finish_group_app_auth_fails_when_browser_closed_before_save(backend, monkeypatch, tmp_path):
    """Superseded test_auth_stop_recording_uses_autosaved_state_after_browser_close."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "https://example.test/dashboard")
    app_id = group.apps[0].id

    class FakeSession:
        reached_wait_url = False  # RecordingSession always has it; False = the user closed the window by hand
        browser_open = False  # by the time finish is called, the window is already gone -> no learn_auth attempt
        async def stop(self):
            return None  # no auth file ever written — browser closed before login completed

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-closed" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    from handlers.protocol import _CommandError

    with pytest.raises(_CommandError) as exc_info:
        b.cmd_finish_group_app_auth(
            {"session_id": "sess-closed", "group_id": group.id, "app_id": app_id},
            "rid",
        )
    assert exc_info.value.code == "auth_capture_failed"


def test_finish_group_app_auth_fails_when_saved_state_has_no_session(backend, monkeypatch, tmp_path):
    """The recorder can still write a storage-state file (an empty shell) even when the
    user closed Chromium before actually signing in — cmd_finish_group_app_auth must treat
    that the same as no file at all, not silently mark the app connected with no cookies."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "https://example.test/dashboard")
    app_id = group.apps[0].id
    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    class FakeSession:
        reached_wait_url = False  # RecordingSession always has it; False = the user closed the window by hand
        browser_open = False  # by the time finish is called, the window is already gone -> no learn_auth attempt
        async def stop(self):
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-empty" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    from handlers.protocol import _CommandError

    with pytest.raises(_CommandError) as exc_info:
        b.cmd_finish_group_app_auth(
            {"session_id": "sess-empty", "group_id": group.id, "app_id": app_id},
            "rid",
        )
    assert exc_info.value.code == "auth_capture_failed"
    assert "closed the login window" in str(exc_info.value)


def test_finish_group_app_auth_saves_a_definition_that_passes_self_test(backend, monkeypatch, tmp_path):
    """P0: while the browser is still open, finish tries to learn a definition first — one
    whose self-test passes gets saved onto the app and clears detect_warning outright."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app, get_group

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "")
    app_id = group.apps[0].id
    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    learned = {"version": 1, "probe_url": "https://example.test/login", "signed_out": {}, "signed_in": {}, "session_keys": []}

    class FakeSession:
        reached_wait_url = False
        browser_open = True

        async def learn_auth(self, login_url, existing_definition):
            assert login_url == "https://example.test/login"
            assert existing_definition is None  # this app had no prior definition (first Connect)
            return learned, ""

        async def stop(self):
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "abc"}], "origins": []}),
                encoding="utf-8",
            )

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id},
        "rid",
    )

    assert result["confirmed"] is True
    assert result["auth"]["apps"][0]["detect_warning"] == ""
    saved = get_group(group.id)
    assert saved.apps[0].auth_definition == learned


def test_finish_group_app_auth_reconnect_keeps_still_valid_definition_unchanged(backend, monkeypatch, tmp_path):
    """AUTH-15: Reconnecting to a site whose sign-in setup hasn't changed must not rewrite its
    auth_definition to a new (but semantically identical) object — that's what made
    _sign_in_setup_drift wrongly demand a rebuild after a plain session refresh."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app, set_group_app_auth_definition, get_group

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "")
    app_id = group.apps[0].id
    existing = {"version": 1, "probe_url": "https://example.test/login", "signed_out": {}, "signed_in": {}, "session_keys": []}
    set_group_app_auth_definition(group.id, app_id, existing)
    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    class FakeSession:
        reached_wait_url = False
        browser_open = True

        async def learn_auth(self, login_url, existing_definition):
            assert login_url == "https://example.test/login"
            assert existing_definition == existing
            return existing_definition, ""  # site unchanged -> same object returned

        async def stop(self):
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "def"}], "origins": []}),
                encoding="utf-8",
            )

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id},
        "rid",
    )

    assert result["confirmed"] is True
    assert result["auth"]["apps"][0]["detect_warning"] == ""
    saved = get_group(group.id)
    assert saved.apps[0].auth_definition == existing


def test_finish_group_app_auth_hand_closed_leaves_existing_definition_untouched(backend, monkeypatch, tmp_path):
    """A browser closed by hand (not Done) skips learning entirely — nothing was observed, so it
    must not wipe a previously-verified definition to None (that was the other half of AUTH-15's
    false-drift bug: any re-login that didn't go through Done also looked like a setup change)."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app, set_group_app_auth_definition, get_group

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "https://example.test/dashboard")
    app_id = group.apps[0].id
    existing = {"version": 1, "probe_url": "https://example.test/login", "signed_out": {}, "signed_in": {}, "session_keys": []}
    set_group_app_auth_definition(group.id, app_id, existing)
    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    class FakeSession:
        reached_wait_url = False
        browser_open = False  # closed by hand -> no learn_auth attempt

        async def learn_auth(self, login_url, existing_definition):
            raise AssertionError("browser_open=False must skip learning entirely")

        async def stop(self):
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "abc"}], "origins": []}),
                encoding="utf-8",
            )

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id},
        "rid",
    )

    assert result["confirmed"] is True
    saved = get_group(group.id)
    assert saved.apps[0].auth_definition == existing


def test_finish_group_app_auth_keeps_window_open_when_self_test_fails(backend, monkeypatch, tmp_path):
    """A failed self-test is NOT an error — the window stays open (stop() never runs) so the
    user can keep signing in and click Done again, per spec: only Done ends an auth session."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "")
    app_id = group.apps[0].id

    stopped = []

    class FakeSession:
        reached_wait_url = False
        browser_open = True

        async def learn_auth(self, login_url, existing_definition):
            return None, "A signed-out browser also read as signed in."

        async def stop(self):
            stopped.append(True)

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            raise AssertionError("should not pop a session that never stopped")

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id},
        "rid",
    )

    assert result == {"confirmed": False, "reason": "A signed-out browser also read as signed in."}
    assert stopped == []


def test_finish_group_app_auth_force_skips_learning_and_saves_with_no_definition(backend, monkeypatch, tmp_path):
    """force=true (the renderer's "Save anyway") skips learning entirely — the app falls back
    to the runtime's generic detection ladder, same as before this feature existed."""
    b, _out = backend
    globals_ = b.cmd_finish_group_app_auth.__globals__

    from conxa_core.config import settings
    from conxa_core.storage.group_store import create_group, add_app, get_group

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "")
    app_id = group.apps[0].id
    # Where the sign-in browser saves while it runs; finishing promotes it to {app_id}.json.
    auth_path = tmp_path / "groups" / group.id / "auth" / f"{app_id}.pending.json"

    class FakeSession:
        reached_wait_url = False
        browser_open = True

        async def learn_auth(self, login_url, existing_definition):
            raise AssertionError("force=true must skip learning entirely")

        async def stop(self):
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "abc"}], "origins": []}),
                encoding="utf-8",
            )

    class FakeRegistry:
        def get(self, session_id: str):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id: str):
            return None

    monkeypatch.setitem(globals_, "_recorder_registry", FakeRegistry())

    result = b.cmd_finish_group_app_auth(
        {"session_id": "sess-1", "group_id": group.id, "app_id": app_id, "force": True},
        "rid",
    )

    assert result["confirmed"] is True
    saved = get_group(group.id)
    assert saved.apps[0].auth_definition is None


def test_delete_workflow_removes_metadata_and_artifact_dir(monkeypatch, tmp_path):
    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, delete_workflow, get_workflow

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    workflow = create_workflow("Delete Me", "https://example.test")
    workflow_file = tmp_path / "workflows" / f"{workflow.id}.json"
    workflow_dir = tmp_path / "workflows" / workflow.id
    auth_file = workflow_dir / "auth" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    assert workflow_file.is_file()
    assert auth_file.is_file()

    assert delete_workflow(workflow.id) is True
    assert get_workflow(workflow.id) is None
    assert not workflow_file.exists()
    assert not workflow_dir.exists()
    assert delete_workflow(workflow.id) is False


def test_delete_workflow_command_is_idempotent_for_stale_renderer_rows(backend, monkeypatch, tmp_path):
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    workflow = create_workflow("Delete Me", "https://example.test")

    assert b.cmd_delete_workflow({"workflow_id": workflow.id}, "rid") == {"deleted": True}
    assert b.cmd_delete_workflow({"workflow_id": workflow.id}, "rid") == {"deleted": False}


def test_compile_derives_title_from_workflow_and_marks_compiled(
    backend, monkeypatch, tmp_path
):
    b, out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, set_recording
    import conxa_compile.compiler.build as compiler_build
    import conxa_compile.pipeline.run as pipeline_run
    import conxa_core.storage.session_events as session_events

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(b, "_install_proxy_router", lambda sink=None, usage_class="compile": None)
    monkeypatch.setattr(
        b,
        "_reserve_compile_credit",
        lambda **kwargs: {
            "reservation_id": kwargs["reservation_id"],
            "remaining_compile_credits": 99,
        },
    )
    monkeypatch.setattr(
        b,
        "_commit_compile_credit",
        lambda reservation_id: {
            "reservation_id": reservation_id,
            "remaining_compile_credits": 98,
        },
    )
    monkeypatch.setattr(b, "_release_compile_credit", lambda reservation_id: None)
    monkeypatch.setattr(session_events, "read_session_events", lambda session_id: [{"type": "click"}])
    monkeypatch.setattr(pipeline_run, "run_pipeline", lambda raw: raw)

    workflow = create_workflow("Submit Invoice", "https://example.test")
    set_recording(workflow.id, "sess-compile")

    captured: dict[str, object] = {}

    def fake_compile_skill_package(
        events,
        *,
        skill_id: str,
        source_session_id: str,
        title: str,
        version: int,
    ):
        captured.update(
            {
                "events": events,
                "skill_id": skill_id,
                "source_session_id": source_session_id,
                "title": title,
                "version": version,
            }
        )
        return SimpleNamespace(
            skills=[SimpleNamespace(steps=[{"kind": "click"}])],
            compile_report={"status": "ok", "min_confidence": 1.0, "steps_with_warnings": 0},
            model_dump=lambda mode="json": {
                "meta": {
                    "id": skill_id,
                    "source_session_id": source_session_id,
                    "title": title,
                    "version": version,
                },
                "skills": [{"steps": [{"kind": "click"}]}],
            },
        )

    monkeypatch.setattr(compiler_build, "compile_skill_package", fake_compile_skill_package)

    result = b.cmd_compile(
        {"workflow_id": workflow.id, "session_id": "sess-compile"},
        "compile-request",
    )

    assert result["skill_id"] == "skill_sess-compile"
    assert result["compile_status"] == "ok"
    assert captured["title"] == "Submit Invoice"


def test_compile_vision_anchor_failure_surfaces_the_specific_error_not_generic(
    backend, monkeypatch, tmp_path
):
    """A VisionAnchorGenerationError used to reach backend.py's dispatcher as a
    bare (non-_CommandError) exception, which its generic arm turned into
    code="internal_error" -> the renderer's "Something went wrong inside the
    app. Please try again." — even though the specific, correct message
    (errorMessages.ts, code vision_anchors_failed) already existed and the
    exception's own api_detail() already knew to ask for it. Fixed 2026-08-23:
    the handler now wraps the raise in a _CommandError using that code."""
    b, out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, set_recording
    import conxa_compile.compiler.build as compiler_build
    import conxa_compile.pipeline.run as pipeline_run
    import conxa_core.storage.session_events as session_events
    from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError
    from handlers.protocol import _CommandError

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(b, "_install_proxy_router", lambda sink=None, usage_class="compile": None)
    monkeypatch.setattr(b, "_apply_vision_fallback_entitlement", lambda: None)
    monkeypatch.setattr(
        b,
        "_reserve_compile_credit",
        lambda **kwargs: {"reservation_id": kwargs["reservation_id"], "remaining_compile_credits": 99},
    )
    monkeypatch.setattr(
        b,
        "_commit_compile_credit",
        lambda reservation_id: {"reservation_id": reservation_id, "remaining_compile_credits": 98},
    )
    monkeypatch.setattr(b, "_release_compile_credit", lambda reservation_id: None)
    refunded: list[str] = []
    monkeypatch.setattr(b, "_refund_compile_credit", lambda reservation_id: refunded.append(reservation_id))
    monkeypatch.setattr(session_events, "read_session_events", lambda session_id: [{"type": "click"}])
    monkeypatch.setattr(pipeline_run, "run_pipeline", lambda raw: raw)

    def fake_compile_skill_package(events, *, skill_id, source_session_id, title, version):
        raise VisionAnchorGenerationError("vision_llm_request_failed", step_index=1, hint="proxy HTTP 502")

    monkeypatch.setattr(compiler_build, "compile_skill_package", fake_compile_skill_package)

    workflow = create_workflow("Submit Invoice", "https://example.test")
    set_recording(workflow.id, "sess-vision-fail")

    with pytest.raises(_CommandError) as exc_info:
        b.cmd_compile({"workflow_id": workflow.id, "session_id": "sess-vision-fail"}, "compile-request")

    assert exc_info.value.code == "vision_anchors_failed"
    # The infra-class reason must trigger a refund of the already-committed credit.
    assert refunded == [b._compile_reservation_id("compile-request", workflow.id, "sess-vision-fail")]


def _write_apply_visual_fixture_skill(skill_id: str, session_id: str) -> None:
    from conxa_core.storage.json_store import write_skill

    step = {
        "action": {"action": "click"},
        "intent": "click_download",
        "target": {"primary_selector": "#x", "fallback_selectors": []},
        "signals": {},
        "validation": {"wait_for": {"type": "none"}, "assertions": []},
        "recovery": {},
    }
    write_skill(
        skill_id,
        {
            "meta": {"id": skill_id, "title": "t", "version": 1, "source_session_id": session_id},
            "skills": [{"id": skill_id, "steps": [step]}],
        },
    )


def test_apply_recording_visual_forwards_the_events_frameset_to_vision_anchors(
    backend, monkeypatch, tmp_path
):
    """apply_recording_event_visual_to_step_or_raise used to build the vision-anchor request
    from a fresh dict that only ever carried the one chosen representative screenshot, silently
    dropping the recorded event's own before_far/.../after_far frameset — so the anchor request
    always fell back to (or, before the frameset requirement existed, was stuck with) a single
    image even when a full frameset was captured on disk. Regression for forwarding the event's
    own visual.frames through to the vision-anchor call."""
    b, _out = backend

    from conxa_core.config import settings
    import conxa_core.storage.session_events as session_events

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(b, "_install_proxy_router", lambda sink=None, usage_class="human_edit": None)

    session_id = "sess_apply_visual"
    image_path = tmp_path / "sessions" / session_id / "images" / "full.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-jpeg-bytes")

    frames = {
        "before_far": "images/before_far.jpg",
        "before_near": "images/before_near.jpg",
        "at": "images/full.jpg",
        "after_near": "images/after_near.jpg",
        "after_far": "images/after_far.jpg",
    }
    monkeypatch.setattr(
        session_events,
        "read_session_events",
        lambda sid: [
            {"visual": {"full_screenshot": "images/full.jpg", "frames": frames, "bbox": {}, "viewport": "1280x720"}}
        ],
    )

    captured: dict = {}

    def fake_generate(ev, **_kwargs):
        captured["frames"] = ev["visual"].get("frames")
        return {"chosen_frame": "at", "anchor_sentence": "x", "anchor_phrases": ["x"]}

    monkeypatch.setattr(
        "conxa_compile.llm.anchor_vision_llm.generate_anchors_for_step_or_raise", fake_generate
    )

    skill_id = "skill_apply_visual"
    _write_apply_visual_fixture_skill(skill_id, session_id)

    b.cmd_apply_recording_visual({"skill_id": skill_id, "step_index": 0, "event_index": 0}, "rid")

    assert captured["frames"] == frames


def test_apply_recording_visual_vision_anchor_failure_surfaces_specific_error(
    backend, monkeypatch, tmp_path
):
    """Same class of bug as
    test_compile_vision_anchor_failure_surfaces_the_specific_error_not_generic, but for the
    Human Edit 'pick a screenshot from the recording' command: a VisionAnchorGenerationError
    (e.g. an event with no frameset at all — full_screenshot_path_missing) used to reach
    backend.py's generic except-Exception arm as code=internal_error (the renderer's unhelpful
    "Something went wrong inside the app"), instead of the specific vision_anchors_failed code
    the renderer already knows how to show a real message for."""
    b, _out = backend

    from conxa_core.config import settings
    from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError
    import conxa_core.storage.session_events as session_events
    from handlers.protocol import _CommandError

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(b, "_install_proxy_router", lambda sink=None, usage_class="human_edit": None)

    session_id = "sess_apply_visual_fail"
    image_path = tmp_path / "sessions" / session_id / "images" / "full.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake-jpeg-bytes")

    monkeypatch.setattr(
        session_events,
        "read_session_events",
        lambda sid: [{"visual": {"full_screenshot": "images/full.jpg", "bbox": {}, "viewport": "1280x720"}}],
    )

    def fake_generate(*_a, **_k):
        raise VisionAnchorGenerationError("full_screenshot_path_missing", step_index=0)

    monkeypatch.setattr(
        "conxa_compile.llm.anchor_vision_llm.generate_anchors_for_step_or_raise", fake_generate
    )

    skill_id = "skill_apply_visual_fail"
    _write_apply_visual_fixture_skill(skill_id, session_id)

    with pytest.raises(_CommandError) as exc_info:
        b.cmd_apply_recording_visual({"skill_id": skill_id, "step_index": 0, "event_index": 0}, "rid")

    assert exc_info.value.code == "vision_anchors_failed"


def test_recompile_reserves_compile_credit_not_human_edit(backend, monkeypatch, tmp_path):
    """A recompile (workflow already has a skill_id) must still spend a
    compile credit and bill LLM calls as usage_class="compile" — recompile no
    longer draws from the Human Edit pool."""
    b, out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, set_recording
    import conxa_compile.compiler.build as compiler_build
    import conxa_compile.pipeline.run as pipeline_run
    import conxa_core.storage.session_events as session_events

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    calls: dict[str, list] = {"reserve": [], "commit": [], "release": [], "proxy": []}
    monkeypatch.setattr(
        b,
        "_install_proxy_router",
        lambda sink=None, usage_class="compile": calls["proxy"].append(usage_class),
    )
    monkeypatch.setattr(
        b,
        "_reserve_compile_credit",
        lambda **kwargs: (
            calls["reserve"].append(kwargs["reservation_id"]),
            {"reservation_id": kwargs["reservation_id"], "remaining_compile_credits": 99},
        )[1],
    )
    monkeypatch.setattr(
        b,
        "_commit_compile_credit",
        lambda reservation_id: (
            calls["commit"].append(reservation_id),
            {"reservation_id": reservation_id, "remaining_compile_credits": 98},
        )[1],
    )
    monkeypatch.setattr(
        b,
        "_release_compile_credit",
        lambda reservation_id: calls["release"].append(reservation_id),
    )
    monkeypatch.setattr(session_events, "read_session_events", lambda session_id: [{"type": "click"}])
    monkeypatch.setattr(pipeline_run, "run_pipeline", lambda raw: raw)
    monkeypatch.setattr(
        compiler_build,
        "compile_skill_package",
        lambda events, *, skill_id, source_session_id, title, version: SimpleNamespace(
            skills=[SimpleNamespace(steps=[{"kind": "click"}])],
            compile_report={"status": "ok", "min_confidence": 1.0, "steps_with_warnings": 0},
            model_dump=lambda mode="json": {
                "meta": {"id": skill_id, "source_session_id": source_session_id, "title": title, "version": version},
                "skills": [{"steps": [{"kind": "click"}]}],
            },
        ),
    )

    workflow = create_workflow("Submit Invoice", "https://example.test")
    workflow = set_recording(workflow.id, "sess-recompile")
    workflow.skill_id = "skill_sess-recompile"
    from conxa_core.storage.workflow_store import save_workflow
    save_workflow(workflow)

    result = b.cmd_compile(
        {"workflow_id": workflow.id, "session_id": "sess-recompile", "mode": "recompile"},
        "recompile-request",
    )

    assert result["skill_id"] == "skill_sess-recompile"
    assert len(calls["reserve"]) == 1
    assert len(calls["commit"]) == 1
    assert calls["proxy"] == ["compile"]


def test_sign_off_auto_builds_the_signed_off_workflow_immediately(backend, monkeypatch, tmp_path):
    """Sign-off should trigger build_skill_package for THIS workflow immediately —
    it never waits on any other workflow in the workspace, without a separate
    Build Skill Package page visit."""
    b, out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow
    import conxa_compile.skill_package_builder as skill_package_builder

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    build_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        skill_package_builder,
        "build_skill_package",
        lambda workspace_id, **kwargs: build_calls.append((workspace_id, kwargs.get("only_workflow_id"))),
    )

    workflow = create_workflow("Send Invoice", "https://acme.test")
    workflow = get_workflow(workflow.id)
    workflow.skill_id = "skill_sess-1"
    workflow.recording_status = "compiled"
    save_workflow(workflow)

    result = b.cmd_sign_off_workflow({"skill_id": "skill_sess-1"}, "signoff-1")

    assert result == {"skill_id": "skill_sess-1", "signed_off": True, "built": True, "waiting_on": []}
    assert build_calls == [(workflow.workspace_id, workflow.id)]

    updated = get_workflow(workflow.id)
    assert updated.signed_off is True
    assert updated.edited_at is not None


def test_sign_off_builds_this_workflow_even_when_a_sibling_is_not_ready(backend, monkeypatch, tmp_path):
    """Approving one workflow must build it immediately, regardless of whether
    another workflow in the same workspace is compiled/edited/signed off — this
    is the fix for the bug where "Create a Lead" couldn't publish because
    "Update Opportunity" was still failing."""
    b, out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow
    import conxa_compile.skill_package_builder as skill_package_builder

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    build_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        skill_package_builder,
        "build_skill_package",
        lambda workspace_id, **kwargs: build_calls.append((workspace_id, kwargs.get("only_workflow_id"))),
    )

    wf1 = create_workflow("Send Invoice", "https://acme.test")
    create_workflow("Cancel Invoice", "https://acme.test")  # recorded but never compiled

    wf1 = get_workflow(wf1.id)
    wf1.skill_id = "skill_sess-1"
    wf1.recording_status = "compiled"
    save_workflow(wf1)

    result = b.cmd_sign_off_workflow({"skill_id": "skill_sess-1"}, "signoff-2")

    assert result == {"skill_id": "skill_sess-1", "signed_off": True, "built": True, "waiting_on": []}
    assert build_calls == [(wf1.workspace_id, wf1.id)]

    updated = get_workflow(wf1.id)
    assert updated.signed_off is True
    assert updated.edited_at is not None


def test_sign_off_surfaces_build_failure_without_failing_the_sign_off(backend, monkeypatch, tmp_path):
    """A build failure after a successful sign-off must not make sign-off itself
    look like it failed — the fields are already persisted; only `built` is false."""
    b, out = backend

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow
    import conxa_compile.skill_package_builder as skill_package_builder

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    def _boom(workspace_id, **kwargs):
        raise ValueError("disk full")

    monkeypatch.setattr(skill_package_builder, "build_skill_package", _boom)

    workflow = create_workflow("Send Invoice", "https://acme.test")
    workflow = get_workflow(workflow.id)
    workflow.skill_id = "skill_sess-1"
    workflow.recording_status = "compiled"
    save_workflow(workflow)

    result = b.cmd_sign_off_workflow({"skill_id": "skill_sess-1"}, "signoff-3")

    assert result["signed_off"] is True
    assert result["built"] is False
    assert "disk full" in result["build_error"]

    updated = get_workflow(workflow.id)
    assert updated.signed_off is True


# ---------------------------------------------------------------------------
# Release Center RPC handlers — each is a thin proxy over _cloud_json; these
# tests assert the URL/method/body built for the cloud call and that the
# cloud's response is returned unchanged, without needing a real cloud.
# ---------------------------------------------------------------------------

def _stub_generation(monkeypatch, b) -> None:
    monkeypatch.setattr(b, "_installer_generation", lambda: "v2")


def test_cmd_release_preview_reads_the_built_pack_not_the_payload(backend, monkeypatch, tmp_path):
    """The renderer has no direct access to the built pack's files — the handler
    must read them off disk itself, the same way publish would upload them."""
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.skill_pack_store import get_or_create_skill_pack
    from conxa_core.workspace import LOCAL_WORKSPACE_ID

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _stub_generation(monkeypatch, b)
    get_or_create_skill_pack(LOCAL_WORKSPACE_ID, display_name="Render")

    packs_dir = tmp_path / "skill-packs" / "wrk_local"
    packs_dir.mkdir(parents=True)
    (packs_dir / "pack.json").write_text(
        json.dumps({"company": "render", "skills": ["deploy"], "skill_groups": {"deploy": "grp"}}),
        encoding="utf-8",
    )
    (packs_dir / "grp" / "deploy").mkdir(parents=True)
    (packs_dir / "grp" / "deploy" / "execution.json").write_text("[]", encoding="utf-8")

    seen: dict[str, object] = {}

    def fake_cloud_json(path, *, method="GET", body=None):
        seen["path"] = path
        seen["method"] = method
        seen["body"] = body
        return {"diff": {"summary": "no changes"}}

    monkeypatch.setattr(b, "_cloud_json", fake_cloud_json)

    result = b.cmd_release_preview({"company_slug": "render", "skill_slug": "deploy", "version": "1.1.0"}, "rid")

    assert result == {"diff": {"summary": "no changes"}}
    assert seen["path"] == "/api/v1/workflows/v2/releases/preview"
    assert seen["method"] == "POST"
    assert seen["body"]["version"] == "1.1.0"
    assert seen["body"]["skill_slug"] == "deploy"
    assert seen["body"]["group_id"] == "grp"
    assert {f["path"] for f in seen["body"]["files"]} == {"grp/deploy/execution.json"}


def test_cmd_release_detail_and_diff_use_the_given_version(backend, monkeypatch, tmp_path):
    b, _out = backend

    from conxa_core.config import settings
    from conxa_core.storage.skill_pack_store import get_or_create_skill_pack
    from conxa_core.workspace import LOCAL_WORKSPACE_ID

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _stub_generation(monkeypatch, b)
    get_or_create_skill_pack(LOCAL_WORKSPACE_ID, display_name="Render")

    calls: list[str] = []

    def fake_cloud_json(path, *, method="GET", body=None):
        calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(b, "_cloud_json", fake_cloud_json)

    b.cmd_release_detail({"company_slug": "render", "skill_slug": "deploy", "version": "1.0.0"}, "rid")
    b.cmd_release_diff({"company_slug": "render", "skill_slug": "deploy", "version": "1.0.0"}, "rid")

    assert calls == [
        "/api/v1/workflows/v2/releases/1.0.0?skill_slug=deploy",
        "/api/v1/workflows/v2/releases/1.0.0/diff?skill_slug=deploy",
    ]


def test_create_group_puts_to_cloud(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings

    b, out = backend
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _stub_generation(monkeypatch, b)

    calls: list[tuple] = []

    def fake_cloud_json(path, *, method="GET", body=None):
        calls.append((method, path, body))
        if path.endswith("/skill-packs"):
            return {"skill_packs": []}
        return {"ok": True}

    monkeypatch.setattr(b, "_cloud_json", fake_cloud_json)
    result = b.cmd_create_group({"name": "Sales"}, "rid")
    assert result["group"]["name"] == "Sales"
    puts = [c for c in calls if c[0] == "PUT"]
    assert len(puts) == 1
    assert puts[0][2]["group_name"] == "Sales"
    assert result["group"]["id"] in puts[0][1]


def test_rename_group_puts_to_cloud(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings

    b, _out = backend
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _stub_generation(monkeypatch, b)
    calls: list[tuple] = []

    def fake_cloud_json(path, *, method="GET", body=None):
        calls.append((method, path, body))
        if path.endswith("/skill-packs"):
            return {"skill_packs": []}
        return {"ok": True}

    monkeypatch.setattr(b, "_cloud_json", fake_cloud_json)
    created = b.cmd_create_group({"name": "Sales"}, "rid")
    gid = created["group"]["id"]
    calls.clear()
    renamed = b.cmd_rename_group({"group_id": gid, "name": "Revenue"}, "rid")
    assert renamed["group"]["name"] == "Revenue"
    puts = [c for c in calls if c[0] == "PUT"]
    assert len(puts) == 1
    assert puts[0][2]["group_name"] == "Revenue"


def test_create_group_succeeds_when_cloud_raises(backend, monkeypatch, tmp_path):
    from conxa_core.config import settings

    b, _out = backend
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _stub_generation(monkeypatch, b)

    def boom(*_a, **_k):
        raise RuntimeError("cloud down")

    monkeypatch.setattr(b, "_cloud_json", boom)
    result = b.cmd_create_group({"name": "Sales"}, "rid")
    assert result["group"]["name"] == "Sales"


def _signin_fixture(b, monkeypatch, tmp_path, saved_cookie):
    """A group app with a working saved sign-in, and a sign-in browser (fake) that wrote its pending file."""
    from conxa_core.config import settings
    from conxa_core.storage.group_store import add_app, create_group, set_group_app_auth

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    group = create_group("Sales")
    group = add_app(group.id, "Example", "https://example.test/login", "https://example.test/dashboard")
    app_id = group.apps[0].id
    auth_dir = tmp_path / "groups" / group.id / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    saved, pending = auth_dir / f"{app_id}.json", auth_dir / f"{app_id}.pending.json"
    saved.write_text(json.dumps({"cookies": [{"name": "session", "value": saved_cookie}], "origins": []}), encoding="utf-8")
    set_group_app_auth(group.id, app_id, str(saved))

    class FakeSession:
        reached_wait_url = False
        browser_open = False
        storage_state_autosave_path = str(pending)

        async def stop(self):
            pending.write_text(json.dumps({"cookies": [{"name": "half", "value": "done"}], "origins": []}), encoding="utf-8")

    class FakeRegistry:
        def get(self, session_id):
            return FakeSession() if session_id == "sess-1" else None

        def pop(self, session_id):
            return None

    monkeypatch.setitem(b.cmd_finish_group_app_auth.__globals__, "_recorder_registry", FakeRegistry())
    return group, app_id, saved, pending


def test_cancelling_a_sign_in_never_touches_the_saved_one(backend, monkeypatch, tmp_path):
    """Cancel used to tear the browser down onto the app's saved file, replacing a working sign-in
    with whatever the half-finished one held."""
    b, _out = backend
    group, app_id, saved, pending = _signin_fixture(b, monkeypatch, tmp_path, "working")
    b.cmd_cancel_group_app_auth({"session_id": "sess-1"}, "rid")
    assert json.loads(saved.read_text(encoding="utf-8"))["cookies"][0]["value"] == "working"
    assert not pending.exists()


def test_finishing_a_sign_in_promotes_its_pending_file(backend, monkeypatch, tmp_path):
    b, _out = backend
    group, app_id, saved, pending = _signin_fixture(b, monkeypatch, tmp_path, "old")
    b.cmd_finish_group_app_auth({"session_id": "sess-1", "group_id": group.id, "app_id": app_id}, "rid")
    assert json.loads(saved.read_text(encoding="utf-8"))["cookies"][0]["name"] == "half"
    assert not pending.exists()