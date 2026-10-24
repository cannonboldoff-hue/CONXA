"""Workflow Group command handlers.

A WorkflowGroup is a business-domain folder ("Sales") that owns both a set of
workflows and the authenticated applications those workflows need — auth is
captured once per app, at the group level, and shared by every workflow in
the group. See conxa_core.models.workflow.WorkflowGroup and
conxa_core.storage.group_store.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from conxa_compile.recorder.session import registry as _recorder_registry
from conxa_core.storage.group_store import DEFAULT_GROUP_NAME
from conxa_core.workspace import LOCAL_WORKSPACE_ID, workspace_dir_slug
from handlers.protocol import _CommandError, _safe_id

# How many workflow names a group summary carries, so a group card can show
# what's inside it. Three plus a "+N more" line is what fits a folder card
# without clipping; the full list lives on the group page.
WORKFLOW_PREVIEW_LIMIT = 3

# How long a probe verdict (checked_at) is trusted before an app reads as
# "unverified" again. Matches handlers/session.py's recording-gate TTL — the
# two exist for different reasons (this one drives the badge, that one bounds
# probe cost) but there's no reason for them to disagree on what "recently
# checked" means.
_VERIFIED_TTL_S = 600


def _window_rect(raw: Any) -> dict[str, int] | None:
    """The screen rect the renderer wants the login window docked at, or None for the browser's
    default placement. Anything malformed or implausibly small is dropped rather than raised —
    a bad rect must never block signing in."""
    if not isinstance(raw, dict):
        return None
    try:
        rect = {k: int(raw[k]) for k in ("x", "y", "width", "height")}
    except (KeyError, TypeError, ValueError):
        return None
    return rect if rect["width"] >= 400 and rect["height"] >= 300 else None


_PENDING_SUFFIX = ".pending.json"


def _pending_auth_path(group_id: str, app_id: str) -> Path:
    """Where an in-progress sign-in saves, beside the app's saved sign-in `<app_id>.json`."""
    from conxa_core.storage.group_store import group_auth_dir

    return group_auth_dir(group_id) / f"{app_id}{_PENDING_SUFFIX}"


def _has_captured_session(auth_path: Path) -> bool:
    """Cheap validity check for a just-finished auth recording: does the saved
    storage state actually contain a session, not just an empty shell? Distinct
    from cmd_check_group_app_auth's live probe — this only guards against the
    "closed the login window before signing in" case, where Playwright still
    writes a storage-state file but it has no cookies or localStorage."""
    if not auth_path.is_file():
        return False
    try:
        import json as _json

        data = _json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("cookies"):
        return True
    for origin in data.get("origins") or []:
        if isinstance(origin, dict) and origin.get("localStorage"):
            return True
    return False


def _detect_warning(success_url: str, reached: bool, has_definition: bool) -> str:
    """Why this sign-in may not be detected confidently at run time, or "" if it will be.

    When a learned auth definition exists (has_definition — see conxa_compile.auth_learning),
    detection no longer depends on success_url or reached at all: the runtime evaluates the
    learned definition directly (runtime/app/login_signals.js::evaluateAuthDefinition), so
    there's nothing to warn about here regardless of how success_url is configured.

    Without one (a re-connect that was force-saved past a failed self-test, or a login window
    closed by hand — see cmd_finish_group_app_auth), the runtime falls back to its generic
    detection ladder, which itself doesn't require success_url either — but a missing success_url
    also means Build Studio's own "Check now" probe (check_app_session_sync) has no target to
    verify against beyond the login page itself, so it's still worth flagging.
    """
    if has_definition:
        return ""
    if not success_url:
        return "No success URL is set, so \"Check now\" has nothing but the login page to verify against. Set it to the page you land on once signed in."
    if not reached:
        return (f"Sign-in was saved, but the login never reached {success_url} on its own — you closed it by hand, "
                "and no sign-in signals were learned from it. \"Check now\" will fall back to a generic check.")
    return ""


def group_auth_status(group) -> dict[str, Any]:
    """Per-app readiness for a group, plus the id of the first app still
    needing attention (used by both the setup wizard and the run gate).

    "state" (missing/expired/ready) is trusted from captured_at/last_error
    alone — cheap, used on every page load; it is NOT itself a live probe
    (that used to launch a headless browser on every group-page open and
    could wedge the whole backend — see FIX.md). "verified" is the honest
    signal: true only when checked_at is set and within _VERIFIED_TTL_S,
    i.e. an actual headless probe confirmed the session recently — via the
    recording gate (handlers/session.py) or the user-initiated "Check now"
    action (cmd_check_group_app_auth below). A "ready" app with verified
    false has a session on disk that has simply never been (recently)
    tested — the UI should read that differently from a freshly-confirmed one."""
    now = time.time()
    apps = []
    first_missing = None
    for app in group.apps:
        if not app.captured_at:
            state = "missing"
        elif app.last_error:
            state = "expired"
        else:
            state = "ready"
        verified = bool(app.checked_at) and (now - app.checked_at) <= _VERIFIED_TTL_S
        apps.append({
            "id": app.id,
            "name": app.name,
            "login_url": app.login_url,
            "success_url": app.success_url,
            "state": state,
            "captured_at": app.captured_at,
            "checked_at": app.checked_at,
            "verified": verified,
            "last_error": app.last_error,
            "check_note": app.check_note,
            "detect_warning": app.detect_warning,
            # Boolean only — never the definition itself (which is diagnostics-adjacent, not a
            # secret, but has no reason to round-trip through the group-status polling response).
            "has_auth_definition": app.auth_definition is not None,
        })
        if state != "ready" and first_missing is None:
            first_missing = app.id
    return {
        "group_id": group.id,
        "apps": apps,
        "apps_total": len(group.apps),
        "apps_authenticated": sum(1 for a in apps if a["state"] == "ready"),
        # Stricter than apps_authenticated: ready AND recently confirmed by a live probe. Only this
        # may be shown as "signed in" — a session file on disk is not evidence of a session.
        "apps_verified": sum(1 for a in apps if a["state"] == "ready" and a["verified"]),
        "ready": first_missing is None,
        "first_missing_app_id": first_missing,
    }


class GroupsMixin:
    def _sync_group_to_cloud(self, group, workspace_id: str = "") -> bool:
        """Best-effort upsert of this group onto Cloud's Skill Packages index.
        Returns True if Cloud accepted the write. Local CRUD must never fail
        because Cloud is down or the user is signed out."""
        from urllib.parse import quote

        from conxa_core.storage.skill_pack_store import get_skill_pack

        ws = workspace_id or getattr(group, "workspace_id", "") or LOCAL_WORKSPACE_ID
        slug = workspace_dir_slug(ws)
        display_name = ""

        pack = get_skill_pack(ws)
        if pack is not None:
            display_name = pack.display_name

        if not display_name:
            display_name = "Local workspace"

        try:
            generation = self._installer_generation()
            self._cloud_json(
                f"/api/v1/workflows/{generation}/{quote(slug)}/groups/{quote(group.id)}",
                method="PUT",
                body={
                    "group_name": group.name,
                    "display_name": display_name,
                },
            )
        except Exception:
            return False
        return True

    def cmd_list_groups(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import list_groups
        from conxa_core.storage.workflow_store import list_workflows
        from handlers.status import derive_workflow_stage

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        groups = list_groups(workspace_id)
        for g in groups:
            if g.name == DEFAULT_GROUP_NAME:
                continue
            if not self._sync_group_to_cloud(g, workspace_id):
                break
        workflows = list_workflows(workspace_id)
        counts: dict[str, int] = {}
        # Per-group lifecycle breakdown and a short preview of the workflows inside,
        # so a group card can show its contents without a second round-trip.
        # Free — these workflows are already loaded.
        stages: dict[str, dict[str, int]] = {}
        previews: dict[str, list[dict[str, str]]] = {}
        for wf in workflows:
            counts[wf.group_id] = counts.get(wf.group_id, 0) + 1
            bucket = stages.setdefault(wf.group_id, {})
            stage = derive_workflow_stage(wf)
            bucket[stage] = bucket.get(stage, 0) + 1
            preview = previews.setdefault(wf.group_id, [])
            if len(preview) < WORKFLOW_PREVIEW_LIMIT:
                preview.append({"id": wf.id, "name": wf.name, "stage": stage})

        result = []
        for g in groups:
            # The Default group exists only as a landing spot for workflows with no
            # group of their own (migrations, reassignment on delete). Empty, it is
            # noise — hide it rather than making every workspace start with a folder
            # nobody asked for.
            if g.name == DEFAULT_GROUP_NAME and not counts.get(g.id):
                continue
            status = group_auth_status(g)
            result.append({
                "id": g.id,
                "slug": g.slug,
                "name": g.name,
                "workflow_count": counts.get(g.id, 0),
                "stages": stages.get(g.id, {}),
                "workflow_preview": previews.get(g.id, []),
                "apps_total": status["apps_total"],
                "apps_authenticated": status["apps_authenticated"],
                "ready": status["ready"],
                "created_at": g.created_at,
                "updated_at": g.updated_at,
            })
        return {"groups": result}

    def cmd_get_group(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import get_group
        from conxa_core.storage.workflow_store import list_workflows
        from handlers.status import derive_workflow_stage

        group_id = _safe_id(payload.get("group_id"), "group_id")
        group = get_group(group_id)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")
        workflows = []
        for wf in list_workflows():
            if wf.group_id != group_id:
                continue
            data = wf.model_dump(mode="json")
            data["stage"] = derive_workflow_stage(wf)
            # Which of the group's apps this workflow actually touches — same
            # matcher (and same inputs: start URLs + recorded hosts) as build-time
            # the recording gate, so the card's platform chips can never disagree
            # with it (runtime gating is the whole group). visited_hosts is captured at stop_recording, so
            # chips appear before the first compile.
            from conxa_core.storage.group_store import apps_for_workflow, unclaimed_hosts

            touched = apps_for_workflow(group.apps, wf.target_url, wf.protected_url, *wf.visited_hosts)
            data["used_apps"] = [{"id": a.id, "name": a.name} for a in touched]
            # AUTH-1: hosts it visited that no app covers — the runtime warns about the same list.
            data["unclaimed_hosts"] = unclaimed_hosts(group.apps, wf.target_url, wf.protected_url, *wf.visited_hosts)
            workflows.append(data)
        return {"group": group.model_dump(mode="json"), "auth": group_auth_status(group), "workflows": workflows}

    def cmd_create_group(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import create_group

        name = str(payload.get("name") or "").strip()
        if not name:
            raise _CommandError("invalid_input", "name is required")
        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        group = create_group(name, workspace_id=workspace_id)
        self._sync_group_to_cloud(group, workspace_id)
        return {"group": group.model_dump(mode="json")}

    def cmd_rename_group(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import get_group, save_group

        group_id = _safe_id(payload.get("group_id"), "group_id")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise _CommandError("invalid_input", "name is required")
        group = get_group(group_id)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")
        group.name = name
        group = save_group(group)
        self._sync_group_to_cloud(group, group.workspace_id)
        return {"group": group.model_dump(mode="json")}

    def cmd_delete_group(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Delete a group, reassigning its workflows to Default rather than
        deleting them — a group is an organizing folder, not the workflow's
        owner in the destructive sense."""
        from conxa_core.storage.group_store import delete_group, ensure_default_group, get_group
        from conxa_core.storage.workflow_store import list_workflows, save_workflow

        group_id = _safe_id(payload.get("group_id"), "group_id")
        group = get_group(group_id)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")
        if group.name == "Default":
            raise _CommandError("cannot_delete_default", "The Default group cannot be deleted.")

        default_group = ensure_default_group(group.workspace_id)
        for wf in list_workflows(group.workspace_id):
            if wf.group_id == group_id:
                wf.group_id = default_group.id
                wf.status = "needs_auth" if any(not a.captured_at for a in default_group.apps) else "ready"
                save_workflow(wf)

        deleted = delete_group(group_id)
        return {"deleted": deleted, "reassigned_to": default_group.id}

    def cmd_add_group_app(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import add_app

        group_id = _safe_id(payload.get("group_id"), "group_id")
        name = str(payload.get("name") or "").strip()
        login_url = str(payload.get("login_url") or "").strip()
        success_url = str(payload.get("success_url") or "").strip()
        if not name or not login_url:
            raise _CommandError("invalid_input", "name and login_url are required")
        group = add_app(group_id, name, login_url, success_url)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")
        return {"group": group.model_dump(mode="json")}

    def cmd_update_group_app(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import get_group, set_group_app_auth_definition, update_app

        group_id = _safe_id(payload.get("group_id"), "group_id")
        app_id = _safe_id(payload.get("app_id"), "app_id")
        new_login_url = payload.get("login_url")

        # A learned definition is a probe against a SPECIFIC login_url — see
        # auth_learning.py's module docstring. Changing that url makes any saved definition
        # stale (it was learned from a different page), so drop it rather than let the runtime
        # keep evaluating a probe against a page nothing points at any more.
        existing = get_group(group_id)
        existing_app = next((a for a in existing.apps if a.id == app_id), None) if existing else None
        login_url_changed = (
            existing_app is not None and new_login_url is not None and new_login_url != existing_app.login_url
        )

        group = update_app(
            group_id,
            app_id,
            name=payload.get("name"),
            login_url=new_login_url,
            success_url=payload.get("success_url"),
        )
        if group is None:
            raise _CommandError("group_or_app_not_found", "No such group or app")
        if login_url_changed:
            group = set_group_app_auth_definition(group_id, app_id, None) or group
        return {"group": group.model_dump(mode="json")}

    def cmd_remove_group_app(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import remove_app

        group_id = _safe_id(payload.get("group_id"), "group_id")
        app_id = _safe_id(payload.get("app_id"), "app_id")
        group = remove_app(group_id, app_id)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")
        return {"group": group.model_dump(mode="json")}

    def cmd_get_group_auth_status(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import get_group

        group_id = _safe_id(payload.get("group_id"), "group_id")
        group = get_group(group_id)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")
        return group_auth_status(group)

    def cmd_start_group_app_auth(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Open a login browser for one app in a group. Mirrors
        cmd_start_recording's auth_mode path (handlers/session.py) but is
        scoped to a group+app instead of a workflow, and passes wait_for_url
        so the recorder can self-detect the login and the renderer can poll
        get_recording_status for reached_wait_url instead of asking the user
        to close the browser manually."""
        from conxa_core.storage.group_store import get_group

        with self._rec_lock:
            if self._active_recording is not None:
                stale = _recorder_registry.get(self._active_recording)
                if stale is not None and stale.browser_open:
                    raise _CommandError(
                        "recording_in_progress",
                        "Finish or close the current login window before starting another.",
                    )
                if stale is not None:
                    _recorder_registry.pop(self._active_recording)
                self._active_recording = None

            group_id = _safe_id(payload.get("group_id"), "group_id")
            app_id = _safe_id(payload.get("app_id"), "app_id")
            group = get_group(group_id)
            if group is None:
                raise _CommandError("group_not_found", f"No group {group_id}")
            app = next((a for a in group.apps if a.id == app_id), None)
            if app is None:
                raise _CommandError("app_not_found", f"No app {app_id} in group {group_id}")

            # The sign-in saves into a pending file, promoted over the app's saved sign-in only when
            # it finishes (cmd_finish_group_app_auth). Saving straight into the real file let a
            # cancelled or half-done sign-in overwrite a working one.
            pending_path = _pending_auth_path(group_id, app_id)
            pending_path.unlink(missing_ok=True)
            sess = _recorder_registry.create(
                start_url=app.login_url,
                storage_state_autosave_path=str(pending_path),
                wait_for_url=app.success_url,
                auth_mode=True,
                # Only the separate-window fallback uses a screen rect; in the panel the view
                # fills whatever region the renderer reports.
                window_rect=_window_rect(payload.get("window_rect")),
                label=app.name,
                # False from a caller with nowhere to draw the panel's view (a dialog).
                in_panel=payload.get("in_panel") is not False,
            )
            try:
                self._loop.run(sess.start())
            except RuntimeError as exc:
                _recorder_registry.pop(sess.session_id)
                raise _CommandError("recorder_launch_failed", str(exc)) from exc
            self._active_recording = sess.session_id
            return {
                "session_id": sess.session_id,
                "group_id": group_id,
                "app_id": app_id,
                "login_url": app.login_url,
                # True = the sign-in is a view in Studio's browser panel (run id = session_id).
                "embedded": bool(getattr(sess, "embedded", False)),
            }

    def cmd_finish_group_app_auth(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Stop a group-app login session and persist the captured state — the user's Done click
        (or a browser closed by hand). Widens workflow status for every workflow in the group
        once this app (and all its siblings) are authenticated.

        P0 (Application Authentication Recording): while the browser is still open, this first
        tries to LEARN a structured auth definition from the just-completed sign-in (see
        conxa_compile.auth_learning's module docstring) and self-tests it, passing the app's
        currently-saved definition through so a Reconnect that re-verifies as still valid keeps
        it byte-identical instead of overwriting it with a freshly re-learned one (AUTH-15 — a
        pure session refresh must never look like the site's sign-in setup changed). A definition
        that fails the self-test does NOT end the session — the login window stays open and the
        renderer shows why, so the user can keep signing in and click Done again. A page change alone
        never ends a sign-in; this confirmation does, whether the user's Done asked for it or the
        conversation's watcher did once the person left the login page (workflow_flow.py's
        _flow_watch_sign_in). Passing `force: true`
        (the renderer's "Save anyway" action) skips straight to saving the session with no
        definition, falling back to the runtime's generic detection ladder for this app.
        A browser closed by hand (no learning possible — nothing left to observe) saves the
        session and leaves any existing definition exactly as it was.
        """
        from conxa_core.storage.group_store import (
            get_group,
            group_auth_dir,
            mark_group_app_unverified,
            set_group_app_auth,
            set_group_app_auth_definition,
            set_group_app_error,
            update_app,
        )
        from conxa_core.storage.workflow_store import list_workflows, set_workflow_status_from_group_auth

        session_id = _safe_id(payload.get("session_id"), "session_id")
        group_id = _safe_id(payload.get("group_id"), "group_id")
        app_id = _safe_id(payload.get("app_id"), "app_id")
        force = bool(payload.get("force"))

        sess = _recorder_registry.get(session_id)
        if sess is None:
            raise _CommandError("session_not_found", f"No session {session_id}")

        group_before = get_group(group_id)
        app_before = next((a for a in group_before.apps if a.id == app_id), None) if group_before else None

        # `definition` is only set when THIS Done actually decided something — a fresh learn, or
        # the existing definition re-verified as still valid (see learn_auth's `existing` param
        # and _perform_learn_auth_sync's docstring, AUTH-15). A hand-closed browser (not force)
        # observed nothing, so it falls through with definition=None and — below — leaves
        # app_before's definition untouched rather than wiping a verified one to None.
        definition: dict[str, Any] | None = None
        decided = force
        learn_reason = ""
        if sess.browser_open and app_before is not None and not force:
            definition, learn_reason = self._loop.run(
                sess.learn_auth(app_before.login_url, app_before.auth_definition)
            )
            if definition is None:
                # Keep the window open — this is NOT a failure, it's "not yet". The renderer
                # polls status normally and offers "Save anyway" (force=true) as the escape
                # hatch for a site this generic learning can't confidently characterize.
                return {"confirmed": False, "reason": learn_reason or "Couldn't confirm sign-in yet. Finish signing in, then click Done again."}
            decided = True

        reached_success_url = bool(sess.reached_wait_url)  # read before stop() tears the session down
        self._loop.run(sess.stop())
        with self._rec_lock:
            if self._active_recording == session_id:
                self._active_recording = None
        _recorder_registry.pop(session_id)

        auth_path = group_auth_dir(group_id) / f"{app_id}.json"
        pending_path = _pending_auth_path(group_id, app_id)
        if not _has_captured_session(pending_path):
            pending_path.unlink(missing_ok=True)
            message = "You closed the login window before signing in — nothing was saved."
            set_group_app_error(group_id, app_id, message)
            raise _CommandError("auth_capture_failed", message)
        os.replace(pending_path, auth_path)

        group = set_group_app_auth(group_id, app_id, str(auth_path))
        if group is None:
            raise _CommandError("group_or_app_not_found", "No such group or app")
        if definition is None:
            # Saved without the self-test proving it (Save anyway, or the window closed by hand):
            # the session is on disk but nothing has confirmed it works, so it must not read as
            # verified. The next probe (Check all / the recording gate) decides.
            group = mark_group_app_unverified(group_id, app_id) or group

        app = next((a for a in group.apps if a.id == app_id), None)
        has_definition = definition is not None if decided else app_before is not None and app_before.auth_definition is not None
        warning = _detect_warning(app.success_url if app else "", reached_success_url, has_definition)
        group = update_app(group_id, app_id, detect_warning=warning) or group
        if decided:
            group = set_group_app_auth_definition(group_id, app_id, definition) or group

        status = group_auth_status(group)
        for wf in list_workflows(group.workspace_id):
            if wf.group_id == group_id:
                set_workflow_status_from_group_auth(wf.id, status["ready"])
        return {"confirmed": True, "group": group.model_dump(mode="json"), "auth": status}

    def cmd_cancel_group_app_auth(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        session_id = _safe_id(payload.get("session_id"), "session_id")
        sess = _recorder_registry.get(session_id)
        if sess is not None:
            self._loop.run(sess.stop())
            _recorder_registry.pop(session_id)
            # A cancelled sign-in saves nothing: drop its pending file, never touching the saved one.
            pending = str(getattr(sess, "storage_state_autosave_path", "") or "")
            if pending.endswith(_PENDING_SUFFIX):
                Path(pending).unlink(missing_ok=True)
        with self._rec_lock:
            if self._active_recording == session_id:
                self._active_recording = None
        return {"ok": True}

    def cmd_check_group_app_auth(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """User-initiated bounded probe: is this app's (or every captured app's)
        saved session actually still valid, right now? This is the only place
        besides the recording gate that spends a real headless navigation on a
        session check — group_auth_status's "ready" badge is otherwise cheap
        and can be stale (see its docstring). Pass app_id to check one app;
        omit it to check every captured app in the group concurrently."""
        from concurrent.futures import ThreadPoolExecutor
        from conxa_compile.recorder.session import check_app_session_sync
        from conxa_core.storage.group_store import get_group, set_group_app_checked

        group_id = _safe_id(payload.get("group_id"), "group_id")
        app_id_raw = payload.get("app_id")
        group = get_group(group_id)
        if group is None:
            raise _CommandError("group_not_found", f"No group {group_id}")

        if app_id_raw:
            app_id = _safe_id(app_id_raw, "app_id")
            targets = [a for a in group.apps if a.id == app_id and a.captured_at and a.storage_state_path]
            if not targets:
                raise _CommandError("app_not_found", f"No captured app {app_id} in group {group_id}")
        else:
            targets = [a for a in group.apps if a.captured_at and a.storage_state_path]

        if targets:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                results = list(zip(targets, pool.map(check_app_session_sync, targets)))
            for app, state in results:
                group = set_group_app_checked(group_id, app.id, state)

        return {"group": group.model_dump(mode="json"), "auth": group_auth_status(group)}
