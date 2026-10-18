"""Single source of truth for a workflow's user-facing lifecycle stage.

Replaces the three separate status vocabularies previously scattered across
the renderer (the workflow's recording_status, last_test_status, and an
inferred sign-off state) with one derived value: recording, ready_to_compile,
queued, compiling, needs_review, needs_test, ready, or error.
"""

from __future__ import annotations

from typing import Any, Literal

WorkflowStage = Literal[
    "recording",
    "ready_to_compile",
    "queued",
    "compiling",
    "needs_review",
    "needs_test",
    "ready",
    "error",
]


def derive_workflow_stage(wf: Any, active_job: dict[str, Any] | None = None) -> WorkflowStage:
    """Compute the single lifecycle stage for a workflow.

    `active_job` is an optional `{"status": "queued" | "running"}` dict describing
    an in-flight compile job for this workflow (wired up once the Phase 2 job
    manager exists); it always overrides the field-derived stage.
    """
    if active_job is not None:
        if active_job.get("status") == "queued":
            return "queued"
        if active_job.get("status") == "running":
            return "compiling"

    if wf.recording_status == "error":
        return "error"

    if not wf.skill_id:
        return "ready_to_compile"

    # `signed_off` is the only approval truth (BUILD-37): `edited_at` moves on every skill write,
    # including a fresh compile, so reading it as "approved" made any edit look like an approval.
    approved = bool(wf.signed_off)

    if not approved:
        return "needs_review"

    if wf.last_test_status != "passed":
        return "needs_test"

    return "ready"
