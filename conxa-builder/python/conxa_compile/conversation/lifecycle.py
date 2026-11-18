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

STAGES = ("describe", "configure", "record", "understand", "compile", "clarify", "edit", "test", "approve")

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
    pending_changes: int = 0,
) -> dict[str, Any]:
    """Return ``{stage, substate, glyph, note, allowed_actions, rows}``.

    ``clarify`` is ``{"open": n, "answered": m}`` (None = no questions generated yet).
    ``runtime`` is ``runtime_state.get_flags(...)``. ``pending_changes`` is how many proposed chat
    changes (including a fix proposed after a failed test) are still waiting for Apply / Dismiss.
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
    # Every question is a hard blocker: shown, being applied, or held back for the next batch.
    open_q = int(cl.get("unanswered") if cl.get("unanswered") is not None else cl.get("open") or 0)

    stage, substate, glyph, note = "record", "not_recorded", NONE, "Not recorded yet"
    actions: list[str] = ["record"]

    if not (_g(wf, "target_url") or "").strip() and not recorded:
        stage, substate, glyph, note, actions = "describe", "needs_details", WARN, "Add a starting page", ["edit_details"]
    elif rt.get("recording"):
        stage, glyph, actions = "record", RUN, ["finish_recording", "discard_recording"]
        substate, note = ("paused", "Recording paused") if rt.get("recording_paused") else ("recording", "Recording")
    elif rt.get("signing_in"):
        stage, substate, glyph, note, actions = "configure", "signing_in", RUN, "Waiting for sign-in", ["finish_sign_in", "cancel"]
    elif recorded and (rt.get("watching") or rt.get("video_error") or cl.get("stage") == "understand") and not (
        rt.get("compiling") or rt.get("queued") or rt.get("out_of_credits") or rt.get("compile_error")
    ):
        # Before the compile: the recording is being watched and lined up with the browser's events,
        # then one round of questions. Pegasus is a hard dependency, so a failure offers Retry, never a compile.
        stage = "understand"
        if rt.get("watching"):
            substate, glyph, note, actions = "watching", RUN, "Watching your recording", []
        elif rt.get("video_error"):
            substate, glyph, note, actions = "video_failed", FAIL, str(rt["video_error"])[:120], ["retry_video", "re_record"]
        elif open_q > 0:
            substate, glyph, actions = "open", WARN, ["answer_clarify", "re_record"]
            if cl.get("fallback"):
                actions = ["answer_clarify", "retry_video", "re_record"]
            note = f"{open_q} question{'s' if open_q != 1 else ''} need you"
        else:
            substate, glyph, note, actions = "ready", RUN, "Getting ready to build", []
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
        # A compile (including a recompile: skill_id stays set) or the understanding check is still
        # running, so the questions don't exist yet: nothing may offer a test in that gap.
        if rt.get("compiling") or rt.get("queued"):
            stage, substate, glyph, note, actions = "compile", "compiling", RUN, "Compiling", []
        elif rt.get("understanding"):
            stage, substate, glyph, note, actions = "clarify", "checking", RUN, "Checking your workflow", []
        elif rt.get("testing"):
            stage = "test"
            substate, note = "running", "Test running"
            glyph, actions = RUN, ["stop_test"]
        elif rt.get("investigating"):
            stage, substate, glyph, note, actions = "test", "investigating", RUN, "Finding why the test failed", ["stop_test"]
        elif rt.get("sign_in_expired"):
            stage, substate, glyph, note, actions = "test", "sign_in_expired", WARN, "Sign-in expired", ["sign_in", "test"]
        elif open_q > 0:
            stage, substate, glyph = "clarify", "open", WARN
            note = f"{open_q} question{'s' if open_q != 1 else ''} need you"
            # Every question that was asked is answered before any test, approval or publish.
            actions = ["answer_clarify", "edit"]
        elif pending_changes > 0:
            stage, substate, glyph, note, actions = "edit", "change_pending", WARN, "A proposed change is waiting — apply or dismiss it", ["edit"]
        elif last_test == "passed":
            if signed_off:
                stage, substate, glyph, note, actions = "approve", "approved", OK, "Approved", ["test", "edit", "publish"]
            else:
                stage, substate, glyph, note, actions = "approve", "ready", WARN, "Test passed — not yet approved", ["approve", "test", "edit"]
        elif last_test == "failed":
            stage, glyph = "test", FAIL
            substate, note, actions = "failed", "Latest test failed", ["investigate", "edit", "test"]
        elif isinstance(_g(wf, "last_test_outcome"), dict) and _g(wf, "last_test_outcome").get("outcome"):
            # A test that ended partway is not "never tested", and not a pass either.
            where = str(_g(wf, "last_test_outcome").get("title") or "").strip()
            stage, substate, glyph, actions = "test", "partial", WARN, ["test", "edit"]
            note = f"Last test ended{f' at “{where}”' if where else ' early'} — not a full pass"
        elif warnings > 0 or _g(wf, "compile_status") == "review_needed":
            stage, substate, glyph = "edit", "recommended", WARN
            note = f"{warnings} step{'s' if warnings != 1 else ''} less certain" if warnings else "Review recommended"
            actions = ["test", "edit"]
        else:
            stage, substate, glyph, note, actions = "test", "ready", WARN, "Ready to test", ["test", "edit"]

    # A compiled workflow can always be built again from its recording, unless something is running right now.
    # (An unanswered question does not stop it: the new build replaces those questions.)
    if compiled and recorded and not any(rt.get(k) for k in (
        "compiling", "queued", "understanding", "watching", "testing", "investigating", "recording", "signing_in", "replying",
    )):
        actions = actions + ["rebuild"]

    # A typed message is still being answered: it may come back as a change card, so nothing may offer
    # a test (or an approval of the version it would change) until the reply and its card are in.
    if rt.get("replying"):
        actions = [a for a in actions if a not in ("test", "approve")]
    # An unanswered question blocks test, approval and publish in every state (a sign-in that expired too).
    if open_q > 0:
        actions = [a for a in actions if a not in ("test", "approve", "publish")]

    # "Ask me": the things the harness was sure about can still be put to the person. Never blocks anything.
    if int(cl.get("assumed") or 0) > 0 and open_q == 0 and "answer_clarify" not in actions and compiled and not (
        rt.get("testing") or rt.get("compiling") or rt.get("queued") or rt.get("understanding")
    ):
        actions = actions + ["answer_clarify"]
    return {
        "stage": stage,
        "substate": substate,
        "glyph": glyph,
        "note": note,
        "allowed_actions": actions,
        "rev": lifecycle_rev(stage, substate, actions),
        "rows": _rows(wf, auth_missing, rt, cl, recorded, compiled, last_test, signed_off, warnings, open_q),
    }


def lifecycle_rev(stage: str, substate: str, actions: list[str]) -> str:
    """A short fingerprint of what the person may do right now. A card that offers an action is only good while its
    action is still allowed; when this changes, cards are checked against the new list (``expire_stale_cards``)."""
    import hashlib

    return hashlib.sha1("|".join([stage, substate, *sorted(actions)]).encode()).hexdigest()[:10]


def expire_stale_cards(workflow_id: str, lc: dict[str, Any], items: list[dict[str, Any]]) -> int:
    """Close every open Confirm card whose action the lifecycle no longer allows ("things moved on"). One place for
    this, read wherever the lifecycle is read, so no handler has to remember to close a card it posted earlier.
    Returns how many were closed. Never raises."""
    from conxa_compile.conversation import thread_store as ts

    allowed = set(lc.get("allowed_actions") or [])
    closed = 0
    for card in items:
        if card.get("kind") != "card" or card.get("card_type") != "confirm_action" or card.get("state") != "open":
            continue
        if str(card.get("action") or "") in allowed:
            continue
        try:
            ts.revise_card(f"wf:{workflow_id}", str(card.get("card_id")), state="expired",
                           note="This is no longer available — things have moved on since it was offered.")
            closed += 1
        except Exception:  # noqa: BLE001 — a card that cannot be closed must not hide the lifecycle
            pass
    return closed


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
    # The thread file is read and folded once: the screen asks for the lifecycle after every new
    # chat item, so reading it per helper made the cost grow with the length of the chat.
    items = _thread_items(workflow.id)
    lc = derive_lifecycle(
        workflow,
        auth_missing=auth_missing,
        clarify=clarify,
        runtime=runtime_state.get_flags(workflow.id),
        pending_changes=_pending_changes(items),
    )
    expire_stale_cards(workflow.id, lc, items)
    return lc


def _thread_items(workflow_id: str) -> list[dict[str, Any]]:
    from conxa_compile.conversation import thread_store as ts

    try:
        return ts.fold(ts.read_raw(f"wf:{workflow_id}"))
    except Exception:  # noqa: BLE001
        return []


def _pending_changes(items: list[dict[str, Any]]) -> int:
    """Proposed chat changes still waiting for Apply / Dismiss (thread data, like auto-fix state)."""
    return sum(1 for i in items if i.get("card_type") == "pending_change" and i.get("state") == "open")
