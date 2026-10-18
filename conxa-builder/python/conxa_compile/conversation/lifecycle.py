"""Deterministic per-workflow lifecycle (BUILD-37).

``describe → configure (sign-in) → record → compile → clarify → edit (optional) → test → approve``

Derived entirely from data that already exists — the workflow, its compile report, the clarify
answers, the sign-off flag — plus the in-memory flags in ``runtime_state.py`` for what is
happening right now. Nothing here is a second source of truth, and nothing here is ever set by a
model: the conversation shows a card only because a backend command moved the lifecycle.

``derive_lifecycle`` is pure (no I/O) so it is unit-testable; ``lifecycle_for_workflow`` does the
reads and calls it.
"""

from __future__ import annotations

from typing import Any

STAGES = ("describe", "configure", "record", "compile", "clarify", "edit", "test", "approve")
MAX_AUTOFIX_ATTEMPTS = 3

# Glyph kinds — identical to the renderer's lib/status.ts vocabulary.
OK, WARN, FAIL, RUN, NONE = "ok", "warn", "fail", "run", "none"


def _g(wf: Any, name: str, default: Any = None) -> Any:
    if isinstance(wf, dict):
        return wf.get(name, default)
    return getattr(wf, name, default)


def derive_lifecycle(
    wf: Any,
    *,
    auth_missing: bool = False,
    clarify: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    autofix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{stage, substate, glyph, note, allowed_actions, rows}``.

    ``clarify`` is ``{"open": n, "answered": m}`` (None = no questions generated yet).
    ``runtime`` is ``runtime_state.get_flags(...)``. ``autofix`` is the latest auto-fix summary
    ``{"state": "running"|"stopped"|"passed", "attempt": n}`` or None.
    """
    rt = runtime or {}
    cl = clarify or {}
    session_id = _g(wf, "session_id")
    recording_status = _g(wf, "recording_status")
    skill_id = _g(wf, "skill_id")
    # A recording that failed to compile (recording_status == "error") is still a recording.
    recorded = bool(session_id)
    compiled = bool(skill_id)
    last_test = _g(wf, "last_test_status") or "never"
    signed_off = bool(_g(wf, "signed_off"))
    warnings = int(_g(wf, "compile_steps_with_warnings") or 0)
    open_q = int(cl.get("open") or 0)

    stage, substate, glyph, note = "record", "not_recorded", NONE, "Not recorded yet"
    actions: list[str] = ["record"]

    if not (_g(wf, "target_url") or "").strip() and not recorded:
        stage, substate, glyph, note, actions = "describe", "needs_details", WARN, "Add a starting page", ["edit_details"]
    elif rt.get("recording"):
        stage, glyph, actions = "record", RUN, ["finish_recording", "discard_recording"]
        substate, note = ("paused", "Recording paused") if rt.get("recording_paused") else ("recording", "Recording")
    elif rt.get("signing_in"):
        stage, substate, glyph, note, actions = "configure", "signing_in", RUN, "Waiting for sign-in", ["finish_sign_in", "cancel"]
    elif not recorded:
        if auth_missing:
            stage, substate, glyph, note, actions = "configure", "sign_in_needed", WARN, "Sign-in needed", ["sign_in"]
        elif rt.get("sign_in_expired"):
            stage, substate, glyph, note, actions = "configure", "sign_in_expired", WARN, "Sign-in expired", ["sign_in"]
        elif rt.get("recording_discarded"):
            stage, substate, glyph, note, actions = "record", "discarded", NONE, "Recording discarded", ["record"]
    elif not compiled:
        stage = "compile"
        if rt.get("compiling"):
            substate, glyph, note, actions = "compiling", RUN, "Compiling", []
        elif rt.get("queued"):
            substate, glyph, note, actions = "queued", RUN, f"Next in line ({rt['queued']})", []
        elif rt.get("out_of_credits"):
            substate, glyph, note, actions = "out_of_credits", WARN, "Waiting for compile credits", ["add_credits", "compile_later"]
        elif rt.get("compile_error") or recording_status == "error":
            substate, glyph, note, actions = "failed", FAIL, "Compile didn’t finish", ["compile", "show_details"]
        else:
            substate, glyph, note, actions = "ready", WARN, "Recorded · ready to compile", ["compile", "re_record"]
    else:
        if rt.get("testing"):
            stage = "test"
            attempt = int(rt.get("autofix_attempt") or 0)
            if attempt:
                substate, note = "autofix", f"Fixing and re-testing · attempt {attempt} of {MAX_AUTOFIX_ATTEMPTS}"
            else:
                substate, note = "running", "Test running"
            glyph, actions = RUN, ["stop_test"]
        elif rt.get("sign_in_expired"):
            stage, substate, glyph, note, actions = "test", "sign_in_expired", WARN, "Sign-in expired", ["sign_in", "test"]
        elif open_q > 0:
            stage, substate, glyph = "clarify", "open", WARN
            note = f"{open_q} question{'s' if open_q != 1 else ''} need you"
            actions = ["answer_clarify", "test", "edit"]
        elif last_test == "passed":
            if signed_off:
                stage, substate, glyph, note, actions = "approve", "approved", OK, "Approved", ["test", "edit", "publish"]
            else:
                stage, substate, glyph, note, actions = "approve", "ready", WARN, "Test passed — not yet approved", ["approve", "test", "edit"]
        elif last_test == "failed":
            stage, glyph = "test", FAIL
            if autofix and autofix.get("state") == "stopped":
                substate, note, actions = "autofix_stopped", "Auto-fix stopped", ["resume_autofix", "edit", "test"]
            else:
                substate, note, actions = "failed", "Latest test failed", ["autofix", "edit", "test"]
        elif warnings > 0 or _g(wf, "compile_status") == "review_needed":
            stage, substate, glyph = "edit", "recommended", WARN
            note = f"{warnings} step{'s' if warnings != 1 else ''} less certain" if warnings else "Review recommended"
            actions = ["test", "edit"]
        else:
            stage, substate, glyph, note, actions = "test", "ready", WARN, "Ready to test", ["test", "edit"]

    return {
        "stage": stage,
        "substate": substate,
        "glyph": glyph,
        "note": note,
        "allowed_actions": actions,
        "rows": _rows(wf, auth_missing, rt, cl, recorded, compiled, last_test, signed_off, warnings, open_q),
    }


def _rows(wf: Any, auth_missing: bool, rt: dict[str, Any], cl: dict[str, Any], recorded: bool,
          compiled: bool, last_test: str, signed_off: bool, warnings: int, open_q: int) -> list[dict[str, str]]:
    """The inspector's lifecycle list. A row only exists once something real can move it."""
    sign_in = (
        {"label": "Sign in", "kind": RUN, "note": "Waiting for sign-in"} if rt.get("signing_in")
        else {"label": "Sign in", "kind": WARN, "note": "Sign-in needed"} if auth_missing and not recorded
        else {"label": "Sign in", "kind": OK, "note": ""}
    )
    if rt.get("recording"):
        record = {"label": "Record", "kind": RUN, "note": "Paused" if rt.get("recording_paused") else "Recording"}
    else:
        record = {"label": "Record", "kind": OK if recorded else NONE, "note": "" if recorded else "Not recorded yet"}
    if compiled:
        compile_row = {"label": "Compile", "kind": OK, "note": ""}
    elif rt.get("compiling") or rt.get("queued"):
        compile_row = {"label": "Compile", "kind": RUN, "note": "Compiling" if rt.get("compiling") else "Waiting"}
    elif rt.get("out_of_credits"):
        compile_row = {"label": "Compile", "kind": WARN, "note": "Waiting for credits"}
    elif rt.get("compile_error") or _g(wf, "recording_status") == "error":
        compile_row = {"label": "Compile", "kind": FAIL, "note": "Didn’t finish"}
    else:
        compile_row = {"label": "Compile", "kind": NONE, "note": "Ready to compile" if recorded else ""}
    clarify_row = (
        {"label": "Clarify", "kind": WARN, "note": f"{open_q} to answer"} if compiled and open_q
        else {"label": "Clarify", "kind": OK if compiled and cl else NONE, "note": "" if compiled and cl else ""}
    )
    edit_row = {
        "label": "Review",
        "kind": WARN if compiled and warnings else (OK if compiled else NONE),
        "note": f"{warnings} less certain" if compiled and warnings else "",
    }
    if rt.get("testing"):
        test_row = {"label": "Test", "kind": RUN, "note": "Running"}
    else:
        test_row = {
            "label": "Test",
            "kind": OK if last_test == "passed" else FAIL if last_test == "failed" else NONE,
            "note": {"passed": "Passed", "failed": "Failed"}.get(last_test, "Not tested yet"),
        }
    approve = {"label": "Approve", "kind": OK if signed_off else NONE, "note": "Approved" if signed_off else "Not approved"}
    return [sign_in, record, compile_row, clarify_row, edit_row, test_row, approve]


# ── reads ───────────────────────────────────────────────────────────────────────────────────


def _auth_missing(workflow: Any) -> bool:
    from conxa_core.storage.group_store import apps_for_workflow, get_group

    group = get_group(workflow.group_id) if getattr(workflow, "group_id", None) else None
    if group is None:
        return False
    required = apps_for_workflow(group.apps, workflow.target_url, workflow.protected_url)
    return any(not a.captured_at for a in required)


def lifecycle_for_workflow(workflow: Any) -> dict[str, Any]:
    """Derive the lifecycle for a stored workflow, reading the clarify answers and in-memory
    runtime flags. Never raises: a read that fails degrades to a missing optional input."""
    from conxa_compile.conversation import clarify_store, runtime_state

    try:
        auth_missing = _auth_missing(workflow)
    except Exception:  # noqa: BLE001 — a group/auth read failing must not hide the workflow
        auth_missing = False
    try:
        clarify = clarify_store.summary(workflow.id)
    except Exception:  # noqa: BLE001
        clarify = None
    return derive_lifecycle(
        workflow,
        auth_missing=auth_missing,
        clarify=clarify,
        runtime=runtime_state.get_flags(workflow.id),
        autofix=_latest_autofix(workflow.id),
    )


def _latest_autofix(workflow_id: str) -> dict[str, Any] | None:
    """The newest auto-fix card's state, read from the thread (auto-fix state is thread data, not a
    second source of truth: the lifecycle only uses it to tell "failed" from "auto-fix stopped")."""
    from conxa_compile.conversation import thread_store as ts

    try:
        cards = [i for i in ts.fold(ts.read_raw(f"wf:{workflow_id}")) if i.get("card_type") == "autofix"]
    except Exception:  # noqa: BLE001
        return None
    return {"state": cards[-1].get("state")} if cards else None
