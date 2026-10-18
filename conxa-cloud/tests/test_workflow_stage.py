"""Tests for handlers/status.py::derive_workflow_stage — the single status
derivation function replacing the three previously-inconsistent vocabularies."""

from __future__ import annotations

import pytest

from conxa_core.models.workflow import Workflow
from handlers.status import derive_workflow_stage


def _wf(**overrides) -> Workflow:
    base = dict(
        id="wf_1",
        slug="wf-1",
        name="Test workflow",
        target_url="https://example.com",
        session_id="sess_1",
        recorded_at=0.0,
    )
    base.update(overrides)
    return Workflow(**base)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        # Freshly recorded, nothing compiled yet.
        (dict(recording_status="recorded"), "ready_to_compile"),
        # Compiled but not yet signed off (and no legacy edited_at shim).
        (dict(recording_status="compiled", skill_id="skill_1"), "needs_review"),
        # Signed off via the new field, no test run yet.
        (dict(recording_status="compiled", skill_id="skill_1", signed_off=True), "needs_test"),
        # edited_at moves on every skill write (even a fresh compile), so it is NOT approval.
        (dict(recording_status="compiled", skill_id="skill_1", edited_at=123.0), "needs_review"),
        # Signed off, tested, but the test failed.
        (
            dict(recording_status="compiled", skill_id="skill_1", signed_off=True, last_test_status="failed"),
            "needs_test",
        ),
        # Signed off and passed — fully ready.
        (
            dict(recording_status="compiled", skill_id="skill_1", signed_off=True, last_test_status="passed"),
            "ready",
        ),
        # Explicit error status always wins.
        (dict(recording_status="error", skill_id="skill_1", signed_off=True, last_test_status="passed"), "error"),
    ],
)
def test_derive_workflow_stage_field_combinations(overrides, expected):
    assert derive_workflow_stage(_wf(**overrides)) == expected


def test_active_job_queued_overrides_field_state():
    wf = _wf(recording_status="recorded")
    assert derive_workflow_stage(wf, active_job={"status": "queued"}) == "queued"


def test_active_job_running_overrides_field_state():
    wf = _wf(recording_status="compiled", skill_id="skill_1", signed_off=True, last_test_status="passed")
    assert derive_workflow_stage(wf, active_job={"status": "running"}) == "compiling"
