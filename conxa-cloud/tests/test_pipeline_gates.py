"""Tests for the gated production pipeline:
Record → Compile → Human Edit → Build Skill Package → Test Skill → Build Installer.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from conxa_core.models.workflow import SkillPack, SkillPackBuild, Workflow
from conxa_core.storage.workflow_store import (
    invalidate_workflow_test_by_skill,
    set_workflow_test_error,
    set_workflow_test_result,
)


# ─── helpers ───────────────────────────────────────────────────────────────────

def _make_workflow(
    *,
    skill_id: str | None = "skill-abc",
    edited_at: float | None = 1.0,
    signed_off: bool | None = None,
    last_test_status: str = "never",
    last_test_error: str | None = None,
    name: str = "Test Workflow",
) -> Workflow:
    return Workflow(
        id=str(uuid.uuid4()),
        slug="test-wf",
        name=name,
        workspace_id="ws-1",
        target_url="https://example.com",
        status="ready",
        created_at=0.0,
        updated_at=0.0,
        session_id="sess-1",
        recorded_at=0.0,
        skill_id=skill_id,
        edited_at=edited_at,
        # Approval is its own flag now; the old tests' "edited" workflows stand for approved ones.
        signed_off=edited_at is not None if signed_off is None else signed_off,
        last_test_status=last_test_status,  # type: ignore[arg-type]
        last_test_error=last_test_error,
    )


def _make_pack(build_at: float | None = None) -> SkillPack:
    build = SkillPackBuild(last_built_at=build_at or time.time(), output_path="/tmp/out", version="0.1.0") if build_at is not None else None
    return SkillPack(
        workspace_id="ws-1",
        company_slug="test-company",
        company_name="Test Company",
        created_at=0.0,
        updated_at=0.0,
        build=build,
    )


# ─── Build Skill Package gates ─────────────────────────────────────────────────

class TestBuildSkillPackageGates:
    """build_skill_package must refuse to run when workflows are uncompiled or unedited."""

    def _run_build(self, workflows: list[Workflow], tmp_path: Path) -> None:
        from conxa_compile.skill_package_builder import build_skill_package
        pack = _make_pack()
        with patch("conxa_compile.skill_package_builder.get_or_create_skill_pack", return_value=pack), \
             patch("conxa_compile.skill_package_builder.list_workflows", return_value=workflows), \
             patch("conxa_compile.skill_package_builder.set_build", return_value=pack), \
             patch("conxa_compile.skill_package_builder._build_workflow_from_saved_skill"):
            build_skill_package("ws-1", company_name="Test Company")

    def test_raises_when_no_workflows(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="No workflows recorded"):
            self._run_build([], tmp_path)

    def test_raises_when_workflow_not_compiled(self, tmp_path: Path) -> None:
        wf = _make_workflow(skill_id=None, edited_at=None)
        with pytest.raises(ValueError, match="Compile these workflows"):
            self._run_build([wf], tmp_path)

    def test_raises_when_workflow_compiled_but_not_edited(self, tmp_path: Path) -> None:
        wf = _make_workflow(skill_id="skill-abc", edited_at=None)
        with pytest.raises(ValueError, match="sign off"):
            self._run_build([wf], tmp_path)

    def test_edited_but_not_signed_off_is_refused(self, tmp_path: Path) -> None:
        """edited_at moves on every skill write (even a fresh compile) — it is not approval."""
        wf = _make_workflow(skill_id="skill-abc", edited_at=5.0, signed_off=False)
        with pytest.raises(ValueError, match="sign off"):
            self._run_build([wf], tmp_path)

    def test_test_build_does_not_need_approval(self, tmp_path: Path) -> None:
        """A workflow has to be testable before it can be approved."""
        from conxa_compile.skill_package_builder import build_skill_package
        wf = _make_workflow(skill_id="skill-abc", edited_at=5.0, signed_off=False)
        pack = _make_pack()
        with patch("conxa_compile.skill_package_builder.get_or_create_skill_pack", return_value=pack),              patch("conxa_compile.skill_package_builder.list_workflows", return_value=[wf]),              patch("conxa_compile.skill_package_builder.set_build", return_value=pack),              patch("conxa_compile.skill_package_builder._clean_stale_artifacts", side_effect=RuntimeError("past the gate")):
            # Getting as far as the first build step proves the approval gate let it through.
            with pytest.raises(RuntimeError, match="past the gate"):
                build_skill_package("ws-1", company_name="Test Company", require_signed_off=False)

    def test_raises_partial_unedited(self, tmp_path: Path) -> None:
        """Mix of edited and unedited — should still raise."""
        wf_ok = _make_workflow(skill_id="skill-1", edited_at=1.0)
        wf_bad = _make_workflow(skill_id="skill-2", edited_at=None, name="Unedited Workflow")
        with pytest.raises(ValueError, match="Unedited Workflow"):
            self._run_build([wf_ok, wf_bad], tmp_path)


# ─── invalidate_workflow_test_by_skill ─────────────────────────────────────────

class TestInvalidateWorkflowTest:
    """Invalidation must reset test fields and bump edited_at."""

    def test_resets_test_fields_and_bumps_edited_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wf = _make_workflow(skill_id="sk-123", last_test_status="passed", edited_at=1.0)

        saved: list[Workflow] = []

        monkeypatch.setattr("conxa_core.storage.workflow_store.list_workflows", lambda: [wf])
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        before = time.time()
        invalidate_workflow_test_by_skill("sk-123")
        after = time.time()

        assert saved, "save_workflow should have been called"
        updated_wf = saved[0]
        assert updated_wf.last_test_status == "never"
        assert updated_wf.last_test_error is None
        assert updated_wf.last_test_at is None
        assert updated_wf.edited_at is not None
        assert before <= updated_wf.edited_at <= after

    def test_any_skill_write_revokes_approval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """signed_off is the one approval truth: a later edit — by a person or an automatic
        fix — means the workflow must be tested and approved again."""
        wf = _make_workflow(skill_id="sk-1", edited_at=1.0, signed_off=True)
        saved: list[Workflow] = []
        monkeypatch.setattr("conxa_core.storage.workflow_store.list_workflows", lambda: [wf])
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        invalidate_workflow_test_by_skill("sk-1")

        assert saved[0].signed_off is False

    def test_does_not_touch_unrelated_workflows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wf_target = _make_workflow(skill_id="sk-target", last_test_status="passed")
        wf_other = _make_workflow(skill_id="sk-other", last_test_status="passed")

        saved: list[Workflow] = []
        monkeypatch.setattr("conxa_core.storage.workflow_store.list_workflows", lambda: [wf_target, wf_other])
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        invalidate_workflow_test_by_skill("sk-target")

        target = next(w for w in saved if w.skill_id == "sk-target")
        assert target.last_test_status == "never"
        assert not any(w.skill_id == "sk-other" for w in saved)

    def test_no_save_when_skill_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wf = _make_workflow(skill_id="sk-abc", last_test_status="passed")

        saved: list[Workflow] = []
        monkeypatch.setattr("conxa_core.storage.workflow_store.list_workflows", lambda: [wf])
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        invalidate_workflow_test_by_skill("sk-does-not-exist")

        assert not saved, "save_workflow should NOT be called when no workflow matches"


# ─── json_store.write_skill invalidation hook ──────────────────────────────────

class TestWriteSkillInvalidationHook:
    """write_skill must invalidate on UPDATE but not on initial CREATE."""

    def _call_write_skill(self, skill_id: str, doc: dict[str, Any], existing: dict | None) -> None:
        from conxa_core.storage import json_store
        with patch.object(json_store, "read_skill", return_value=existing), \
             patch("conxa_core.db.db_set"), \
             patch.object(json_store, "skills_dir", return_value=MagicMock(
                 __truediv__=lambda self, other: MagicMock(
                     write_text=lambda *a, **kw: None
                 )
             )):
            json_store.write_skill(skill_id, doc)

    def test_invalidates_on_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        invalidated: list[str] = []

        def fake_invalidate(skill_id: str) -> None:
            invalidated.append(skill_id)

        monkeypatch.setattr(
            "conxa_core.storage.json_store.invalidate_workflow_test_by_skill",
            fake_invalidate,
            raising=False,
        )

        # Patch at the module level where it's imported lazily
        with patch("conxa_core.storage.json_store.read_skill", return_value={"existing": True}), \
             patch("conxa_core.db.db_set"), \
             patch("pathlib.Path.write_text"):
            from conxa_core.storage import json_store
            # Temporarily inject the mock so the deferred import path hits it
            import conxa_core.storage.workflow_store as ws
            original = getattr(ws, "invalidate_workflow_test_by_skill")
            ws.invalidate_workflow_test_by_skill = fake_invalidate  # type: ignore[assignment]
            try:
                json_store.write_skill("sk-update", {"new": True})
            finally:
                ws.invalidate_workflow_test_by_skill = original  # type: ignore[assignment]

    def test_no_invalidation_on_first_create(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First write (existing=None) must not trigger invalidation."""
        invalidated: list[str] = []

        import conxa_core.storage.workflow_store as ws
        original = getattr(ws, "invalidate_workflow_test_by_skill")

        def fake_invalidate(skill_id: str) -> None:
            invalidated.append(skill_id)

        ws.invalidate_workflow_test_by_skill = fake_invalidate  # type: ignore[assignment]
        try:
            with patch("conxa_core.storage.json_store.read_skill", return_value=None), \
                 patch("conxa_core.db.db_set"), \
                 patch("pathlib.Path.write_text"):
                from conxa_core.storage import json_store
                json_store.write_skill("sk-brand-new", {"new": True})
        finally:
            ws.invalidate_workflow_test_by_skill = original  # type: ignore[assignment]

        assert not invalidated, "No invalidation on first create"


# ─── set_workflow_test_result / set_workflow_test_error ────────────────────────

class TestSetWorkflowTestPersistence:
    def test_set_result_marks_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wf = _make_workflow(last_test_status="never")

        saved: list[Workflow] = []
        monkeypatch.setattr("conxa_core.storage.workflow_store.get_workflow", lambda wid, **kw: wf)
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        set_workflow_test_result(wf.id, status="passed", inputs={"url": "https://x.com"})

        updated_wf = saved[0]
        assert updated_wf.last_test_status == "passed"
        assert updated_wf.last_test_inputs == {"url": "https://x.com"}
        assert updated_wf.last_test_error is None
        assert updated_wf.last_test_at is not None

    def test_set_error_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wf = _make_workflow(last_test_status="never")

        saved: list[Workflow] = []
        monkeypatch.setattr("conxa_core.storage.workflow_store.get_workflow", lambda wid, **kw: wf)
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        set_workflow_test_error(wf.id, "Selector not found")

        updated_wf = saved[0]
        assert updated_wf.last_test_status == "failed"
        assert "Selector not found" in (updated_wf.last_test_error or "")

    def test_set_error_truncates_long_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wf = _make_workflow()

        saved: list[Workflow] = []
        monkeypatch.setattr("conxa_core.storage.workflow_store.get_workflow", lambda wid, **kw: wf)
        monkeypatch.setattr("conxa_core.storage.workflow_store.save_workflow", lambda w: saved.append(w) or w)

        long_error = "x" * 5000
        set_workflow_test_error(wf.id, long_error)

        updated_wf = saved[0]
        assert len(updated_wf.last_test_error or "") <= 2000


# ─── Stale-build gate (test endpoint) ─────────────────────────────────────────

class TestStaleBuildGate:
    """Test endpoint must reject runs where edited_at > pack.build.last_built_at."""

    def _make_stale(self) -> tuple[SkillPack, Workflow]:
        build_time = 1000.0
        edit_time = 2000.0  # edited AFTER build
        wf = _make_workflow(skill_id="sk-abc", edited_at=edit_time)
        pack = _make_pack(build_at=build_time)
        return pack, wf

    def test_stale_workflow_is_detected(self) -> None:
        pack, wf = self._make_stale()
        assert wf.edited_at is not None
        assert pack.build is not None
        assert wf.edited_at > pack.build.last_built_at

    def test_non_stale_workflow_passes(self) -> None:
        build_time = 2000.0
        edit_time = 1000.0  # edited BEFORE build
        wf = _make_workflow(skill_id="sk-abc", edited_at=edit_time)
        pack = _make_pack(build_at=build_time)
        assert wf.edited_at is not None
        assert pack.build is not None
        assert wf.edited_at <= pack.build.last_built_at


# ─── Backend enforcement of the stale-build gate (cmd_test_workflow) ──────────

class TestBackendStaleGateEnforcement:
    """cmd_test_workflow must refuse to run a workflow edited after the last build,
    independent of the UI's isStaleTest check (which a race or non-UI caller can bypass)."""

    def _handler(self) -> Any:
        from handlers.workflows import WorkflowsMixin
        return WorkflowsMixin()

    def test_raises_when_edited_after_build(self) -> None:
        wf = _make_workflow(skill_id="sk-1", edited_at=2000.0)
        pack = _make_pack(build_at=1000.0)
        with patch("conxa_core.storage.workflow_store.get_workflow", return_value=wf), \
             patch("conxa_core.storage.skill_pack_store.get_skill_pack", return_value=pack):
            from handlers.protocol import _CommandError
            with pytest.raises(_CommandError) as excinfo:
                self._handler().cmd_test_workflow(
                    {"workspace_id": "ws-1", "workflow_id": wf.id}, "rid-1"
                )
            assert excinfo.value.code == "workflow_stale"

    def test_does_not_raise_when_not_stale(self) -> None:
        wf = _make_workflow(skill_id="sk-1", edited_at=500.0)
        pack = _make_pack(build_at=1000.0)
        with patch("conxa_core.storage.workflow_store.get_workflow", return_value=wf), \
             patch("conxa_core.storage.skill_pack_store.get_skill_pack", return_value=pack):
            from handlers.protocol import _CommandError
            try:
                self._handler().cmd_test_workflow(
                    {"workspace_id": "ws-1", "workflow_id": wf.id}, "rid-1"
                )
            except _CommandError as exc:
                assert exc.code != "workflow_stale"


# ─── Sign-in setup drift gate (cmd_test_workflow) ─────────────────────────────

class TestSignInSetupDrift:
    """Run Test executes the BUILT pack, but stages the LIVE group's saved sessions under the live
    app ids. If the group changed after the build (an app re-added under a new id, a sign-in
    definition learned), the runtime reads sessions by the pack's ids and silently picks up a stale
    orphan — so Run Test must refuse until the pack is rebuilt."""

    GROUP_ID = "grp-1"

    @staticmethod
    def _app(app_id: str, name: str, auth_definition: dict | None = None) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(
            id=app_id, name=name, login_url=f"https://{app_id}.test/login", success_url="",
            auth_definition=auth_definition,
        )

    def _built(self, *apps: Any) -> dict:
        return {"groups": [{"id": self.GROUP_ID, "name": "Enterprise", "apps": [
            {"id": a.id, "name": a.name, "login_url": a.login_url, "success_url": a.success_url,
             "auth_definition": a.auth_definition} for a in apps
        ]}]}

    def _drift(self, live_apps: list, pack_json: dict, group_id: str = GROUP_ID) -> str:
        from types import SimpleNamespace
        from handlers.protocol import _sign_in_setup_drift

        wf = SimpleNamespace(group_id=group_id)
        group = SimpleNamespace(id=self.GROUP_ID, name="Enterprise", apps=live_apps) if group_id else None
        with patch("conxa_core.storage.group_store.get_group", return_value=group):
            return _sign_in_setup_drift(wf, pack_json)

    def test_in_sync_is_empty(self) -> None:
        apps = [self._app("github", "GitHub", {"v": 1}), self._app("google", "Google")]
        assert self._drift(apps, self._built(*apps)) == ""

    def test_app_readded_under_a_new_id_is_drift(self) -> None:
        # The real case: pack built with `google-drive`, group later re-connected as `google`.
        built = self._built(self._app("github", "GitHub"), self._app("google-drive", "Google"))
        live = [self._app("github", "GitHub"), self._app("google", "Google")]
        msg = self._drift(live, built)
        assert "Enterprise" in msg and "Google" in msg and "google-drive" in msg
        assert "Rebuild the skill package" in msg

    def test_newly_learned_definition_is_drift(self) -> None:
        built = self._built(self._app("github", "GitHub"))
        live = [self._app("github", "GitHub", {"version": 1})]
        assert "GitHub" in self._drift(live, built)

    def test_group_missing_from_the_built_pack_is_drift(self) -> None:
        live = [self._app("github", "GitHub")]
        assert "GitHub" in self._drift(live, {"groups": []})

    def test_workflow_without_a_group_is_never_drift(self) -> None:
        assert self._drift([], {"groups": []}, group_id="") == ""


# ─── H-5: sensitive-flagged test inputs never reach persisted test history ────

class TestRedactSensitiveTestInputs:
    def test_masks_values_declared_sensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.workflows import _redact_sensitive_test_inputs

        doc = {"inputs": [
            {"id": "password", "type": "text", "sensitive": True},
            {"id": "username", "type": "text"},
        ]}
        monkeypatch.setattr("conxa_core.storage.json_store.read_skill", lambda skill_id: doc)

        out = _redact_sensitive_test_inputs("sk-1", {"password": "hunter2", "username": "bob"})
        assert out == {"password": "", "username": "bob"}

    def test_noop_when_nothing_declared_sensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from handlers.workflows import _redact_sensitive_test_inputs

        doc = {"inputs": [{"id": "username", "type": "text"}]}
        monkeypatch.setattr("conxa_core.storage.json_store.read_skill", lambda skill_id: doc)

        inputs = {"username": "bob"}
        out = _redact_sensitive_test_inputs("sk-1", inputs)
        assert out is inputs
