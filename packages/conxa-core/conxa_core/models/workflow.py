"""Pydantic models for the Workflow entity and the workspace-level SkillPack.

A Workflow is one recorded automation: one login session, one recording, one
compiled skill. Many workflows in a workspace compile together into a single
shared SkillPack (one skill package, one installer) — see SkillPack below.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GroupApp(BaseModel):
    """One authenticated application inside a WorkflowGroup — a login URL, the
    URL that signals successful login, and (once captured) its session."""

    id: str
    name: str
    login_url: str
    success_url: str = ""
    captured_at: float | None = None
    storage_state_path: str = ""
    last_error: str = ""
    # When this app's session was last actually probed (headless navigation to
    # success_url) rather than merely captured. None means "never verified since
    # capture" — group_auth_status treats that as unverified even if captured_at
    # is set. Set by check_app_session_sync callers (recording gate, "Check now").
    checked_at: float | None = None
    # Set when the last sign-in was saved without the recorder ever reaching success_url (or with no
    # success_url set) — i.e. the login was closed by hand. The runtime detects sign-in by watching for
    # that same URL, so a run may never see this app as signed in. Advisory only: the session itself
    # is still valid, so it never affects `state`. Cleared on the next sign-in that self-detects.
    detect_warning: str = ""
    # Learned at Connect time (P0: Application Authentication Recording) from a contrast between
    # a signed-in and a genuine signed-out observation of the same probe url, then proven by a
    # three-way self-test before being saved — see conxa_compile.auth_learning's module docstring.
    # None means "not learned" (an app connected before this feature, or whose self-test never
    # passed): the runtime falls back to its generic detection ladder (login_signals.js) exactly
    # as it did before this field existed. Shipped into pack.json — see skill_package_builder.py —
    # but never carries cookie values, response bodies, or any query string/fragment.
    auth_definition: dict[str, Any] | None = None


class WorkflowGroup(BaseModel):
    """A business-domain folder (e.g. "Sales") that owns both a set of
    workflows and the authenticated applications those workflows need. Auth is
    captured once per app, at the group level, and shared by every workflow in
    the group — see docs/TRD.md's Workflow Groups section."""

    id: str
    slug: str
    name: str
    workspace_id: str = ""
    apps: list["GroupApp"] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


class Workflow(BaseModel):
    id: str
    slug: str
    name: str
    owner_user_id: str = "local"
    workspace_id: str = ""
    group_id: str = ""
    target_url: str
    protected_url: str = ""
    protected_url_marker_text: str = ""
    # One optional sentence from the New workflow form — what the task does. Context for
    # Clarify and the chat; never used to compile.
    task_description: str = ""
    status: Literal["needs_auth", "ready", "error"] = "needs_auth"
    created_at: float = 0.0
    updated_at: float = 0.0
    # The single recording this workflow holds (inlined — no inner list).
    session_id: str | None = None
    recorded_at: float | None = None
    # Extra recordings of the SAME task made to show a different outcome (outcome forks: "record the
    # case where the popup does not appear"). Each is {session_id, fork_step_key, recorded_at}.
    # Never the workflow's own recording -- `session_id` above stays the primary -- and discarded
    # whenever the primary is re-recorded (a variant only means something against the take it was
    # recorded to complement).
    variant_session_ids: list[dict[str, Any]] = Field(default_factory=list)
    recording_status: Literal["recorded", "compiled", "error"] | None = None
    # How long the human took to perform the workflow once, live (last event
    # timestamp minus first), computed at stop_recording time. Feeds the Cloud
    # ROI baseline (tracking_analytics.seed_recorded_baseline) as a measured
    # figure instead of an admin's guess. None for recordings made before this
    # field existed, or if the diff couldn't be computed.
    recording_duration_seconds: float | None = None
    # Every hostname the saved recording navigated to (extracted from session
    # events at stop_recording time, before any compile). Lets the group page
    # show all touched platform apps pre-compile and feed the same host
    # matching — see conxa_compile.recorder.session.extract_visited_hosts.
    visited_hosts: list[str] = Field(default_factory=list)
    skill_id: str | None = None
    edited_at: float | None = None
    last_test_at: float | None = None
    last_test_status: Literal["passed", "failed", "never"] = "never"
    last_test_error: str | None = None
    last_test_inputs: dict[str, Any] = Field(default_factory=dict)
    # BUILD-26 stage (a2): the run id parsed from the test run's own test_phase log lines
    # (handlers/workflows.py::cmd_test_workflow). Not read from in-memory `_active_test_runs` —
    # that dict doesn't survive a backend restart — so the copilot's evidence bundle
    # (conxa_compile/editor/evidence.py) has a stable pointer to
    # runs/{last_test_run_id}/_evidence/ even after a fresh Studio launch.
    last_test_run_id: str | None = None
    signed_off: bool = False
    # Confidence summary from the most recent compile's compile_report (see
    # conxa_compile/compiler/build.py::_build_compile_report). None until compiled.
    compile_status: Literal["ok", "review_needed", "failed"] | None = None
    compile_min_confidence: float | None = None
    compile_steps_with_warnings: int | None = None


class SkillPackBuild(BaseModel):
    last_built_at: float
    output_path: str
    version: str = "0.1.0"


class SkillPackInstaller(BaseModel):
    built_at: float
    installer_path: str
    filename: str
    version: str
    runtime_version: str
    release_notes: str = ""


class SkillPack(BaseModel):
    """One per workspace, forever — the shared package every workflow in that
    workspace compiles into. Keyed by workspace_id, not by any single workflow.
    display_name is cosmetic only (installer filename, UI label) and carries
    no uniqueness guarantee."""

    workspace_id: str
    display_name: str = ""
    status: Literal["idle", "building", "error"] = "idle"
    build: SkillPackBuild | None = None
    installer: SkillPackInstaller | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
