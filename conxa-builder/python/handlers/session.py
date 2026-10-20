"""Auth/deps/onboarding and recording-session command handlers."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from services import bootstrap as _bootstrap_pkg
from conxa_compile.recorder.session import registry as _recorder_registry
from conxa_core.storage.workflow_store import get_workflow as _get_workflow
from handlers.protocol import _CommandError, _emit_event, _event_sink, _safe_id


def _recording_duration_seconds(events: list[dict[str, Any]]) -> float | None:
    """Wall-clock length of a recording: last event's timestamp minus the first's.

    Same ISO-parsing idiom as conxa_compile.recorder.session's per-tab video-offset
    calculation (bridge.js emits e.g. "2025-02-15T14:32:10.123Z").
    """
    from datetime import datetime, timezone

    def _timestamp_of(event: dict[str, Any]) -> str:
        action = event.get("action")
        return str(action.get("timestamp") or "") if isinstance(action, dict) else ""

    def _parse(ts: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    first = _parse(_timestamp_of(events[0]))
    last = _parse(_timestamp_of(events[-1]))
    if first is None or last is None:
        return None
    duration = (last - first).total_seconds()
    return duration if duration > 0 else None


def _refresh_group_app_sessions(workflow_id: str) -> None:
    """After a workflow recording ends, split its merged group session back into
    per-app updates so each app's saved session picks up whatever the site did to
    it during the recording (rotated cookies, a mid-recording re-login) — see
    cmd_start_recording above, which seeds *and* autosaves this same merged file
    instead of a plain single storageState.
    """
    import json

    from conxa_core.config import settings as _settings
    from conxa_core.storage.group_store import get_group, mark_group_app_unverified
    from conxa_core.storage.storage_state import refresh_app_state
    from conxa_core.storage.workflow_store import get_workflow

    workflow = get_workflow(workflow_id)
    if workflow is None or not workflow.group_id:
        return
    merged_path = Path(_settings.data_dir) / "workflows" / workflow_id / "merged_group_state.json"
    if not merged_path.is_file():
        return
    group = get_group(workflow.group_id)
    if group is None:
        return
    current = json.loads(merged_path.read_text(encoding="utf-8"))
    for app in group.apps:
        if not app.storage_state_path or not Path(app.storage_state_path).is_file():
            continue
        state_path = Path(app.storage_state_path)
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        refreshed = refresh_app_state(previous, current)
        if not refreshed["cookies"] and not refreshed["origins"]:
            continue  # never blank a working session
        state_path.write_text(json.dumps(refreshed), encoding="utf-8")
        # Not a login: the recording may have ended signed out of this app (an expired session
        # copied straight back). Stamping it verified here hid exactly that from the next gate.
        mark_group_app_unverified(group.id, app.id)


_PROBE_TTL_S = 600


def group_apps_needing_sign_in(group: Any) -> list[Any]:
    """Every app in `group` that must be signed in before a recording may start: never
    connected, or its saved session fails a check. The whole group, not just the apps the
    workflow's start URL matches — a workflow that starts on one app routinely crosses into
    another (start on GitHub, upload to Drive), and the runtime already requires every group
    app before a run (AUTH-14), so recording holds the same rule (AUTH-16).

    Apps checked within the TTL are trusted as is, so a record click doesn't relaunch a
    headless browser per app; the rest are probed concurrently (each probe caps itself at 20s,
    see check_app_session_sync), so N apps cost one wait. Returns the apps in group order."""
    from conxa_core.storage.group_store import get_group, set_group_app_checked

    now = time.time()
    to_probe = [
        a for a in group.apps
        if a.captured_at and a.storage_state_path and (not a.checked_at or (now - a.checked_at) > _PROBE_TTL_S)
    ]
    if to_probe:
        from concurrent.futures import ThreadPoolExecutor
        from conxa_compile.recorder.session import check_app_session_sync

        with ThreadPoolExecutor(max_workers=len(to_probe)) as pool:
            verdicts = list(pool.map(check_app_session_sync, to_probe))
        for a, state in zip(to_probe, verdicts):
            set_group_app_checked(group.id, a.id, state)
        group = get_group(group.id) or group  # set_group_app_checked persisted the verdicts
    # last_error is the single source of truth for "expired now": set_group_app_checked sets it
    # on "expired" and clears it on "ready"; a failed capture sets it too.
    return [a for a in group.apps if not a.captured_at or not a.storage_state_path or a.last_error]


def _backup_previous_recording(workflow_id: str, session_id: str) -> str | None:
    """Copy a workflow's previous recording session to data/backups/recordings/
    before a re-record replaces it. Copy (not move): compiled skill packages and
    editor state may still reference blobs under the original session path, so
    the original stays in place — the backup is the safety net for "I recorded
    over the wrong thing". Returns the backup path, or None if there was nothing
    to back up (or the destination already exists)."""
    import shutil

    from conxa_core.config import settings as _settings

    src = Path(_settings.data_dir) / "sessions" / session_id
    if not src.is_dir():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(_settings.data_dir) / "backups" / "recordings" / workflow_id / f"{stamp}-{session_id}"
    if dest.exists():
        return None
    shutil.copytree(src, dest)
    return str(dest)


class SessionMixin:
    def cmd_ping(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        return {"ok": True, "pid": os.getpid()}

    def cmd_deps_status(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Fast offline check — returns which deps are already present."""
        return _bootstrap_pkg.check_status()

    def cmd_deps_check(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Fetch the cloud manifest and return which deps are outdated.

        Pass {"force": true} to bypass the 24 h TTL cache.
        Returns {"outdated": [{"dep": str, "installed": str|None, "available": str}]}
        """
        force = bool(payload.get("force", False))
        outdated = _bootstrap_pkg.check_for_updates(self._cloud_api, force=force)
        return {"outdated": outdated}

    def cmd_deps_apply(self, _payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Apply all pending dependency updates.

        Downloads, verifies, and atomically installs each outdated dep.
        Streams progress events. Returns {"ok": true} on success.
        """
        return _bootstrap_pkg.ensure_all(self._cloud_api, on_event=_event_sink(rid))

    def cmd_bootstrap(self, _payload: dict[str, Any], rid: str) -> dict[str, Any]:
        return _bootstrap_pkg.ensure_all(self._cloud_api, on_event=_event_sink(rid))

    def cmd_login(self, _payload: dict[str, Any], rid: str) -> dict[str, Any]:
        return {"identity": self._auth_service().login(on_event=_event_sink(rid))}

    def cmd_logout(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        self._auth_service().logout()
        return {"ok": True}

    def cmd_whoami(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        return {"identity": self._auth_service().current_identity()}

    def cmd_cloud_ping(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Is Conxa Cloud reachable right now? Drives the offline banner and the sidebar connection dot.

        Any HTTP answer, even an error status, means the network path works; only a failure to get one
        (no route, DNS, timeout) counts as offline. Never raises — a ping that threw would itself look
        like the engine being down.
        """
        import urllib.error
        import urllib.request

        started = time.monotonic()
        try:
            req = urllib.request.Request(f"{self._cloud_api_base()}/healthz")
            req.add_header("X-Conxa-Client", os.environ.get("CONXA_PROXY_CLIENT", "build-studio"))
            with urllib.request.urlopen(req, timeout=6):
                pass
        except urllib.error.HTTPError:
            pass
        except Exception as exc:
            return {"online": False, "detail": type(exc).__name__}
        return {"online": True, "ms": int((time.monotonic() - started) * 1000)}

    def cmd_start_recording(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import re
        from pathlib import Path
        from conxa_core.config import settings as _settings

        with self._rec_lock:
            if self._active_recording is not None:
                stale = _recorder_registry.get(self._active_recording)
                if stale is not None and stale.browser_open:
                    raise _CommandError(
                        "recording_in_progress",
                        "You already have a recording in progress. Finish or close that "
                        "browser window before starting a new one.",
                    )
                # The previous session's browser is already gone (closed, crashed, or the
                # app was reloaded before stop_recording ran) but nothing cleared the lock —
                # release it instead of permanently blocking new recordings.
                if stale is not None:
                    _recorder_registry.pop(self._active_recording)
                self._active_recording = None

            workflow_id_raw = payload.get("workflow_id")
            workflow_id = _safe_id(workflow_id_raw, "workflow_id") if workflow_id_raw else ""

            if workflow_id:
                # Workflow recording (auth is captured once at the group level —
                # see handlers/groups.py's cmd_start_group_app_auth — so this
                # path only ever records the workflow itself, seeded with the
                # group's merged session).
                from conxa_core.storage.group_store import get_group
                from conxa_core.storage.storage_state import merge_storage_states

                workflow = _get_workflow(workflow_id)
                if not workflow:
                    raise _CommandError("workflow_not_found", f"No workflow {workflow_id}")
                group = get_group(workflow.group_id) if workflow.group_id else None
                if group is None:
                    raise _CommandError("auth_required", "Authenticate every app in this workflow's group before recording.")

                # Every app in the group must be signed in before the browser opens, so a
                # missing or expired sign-in is never discovered halfway through a recording.
                needs = group_apps_needing_sign_in(group)
                if needs:
                    never = [a.name for a in needs if not a.captured_at]
                    expired = [a.name for a in needs if a.captured_at]
                    parts = []
                    if never:
                        parts.append(f"sign in to {', '.join(never)}")
                    if expired:
                        parts.append(f"sign in again to {', '.join(expired)} (the saved sign-in has expired)")
                    raise _CommandError("auth_required", f"Before recording, {' and '.join(parts)}.")

                # Seed the recording from every app in the group — all of them were just
                # confirmed signed in above.
                states = []
                for app in group.apps:
                    if app.storage_state_path and Path(app.storage_state_path).is_file():
                        import json as _json
                        states.append(_json.loads(Path(app.storage_state_path).read_text(encoding="utf-8")))
                merged = merge_storage_states(states)

                import json as _json
                workflow_dir = Path(_settings.data_dir) / "workflows" / workflow_id
                workflow_dir.mkdir(parents=True, exist_ok=True)
                merged_path = workflow_dir / "merged_group_state.json"
                merged_path.write_text(_json.dumps(merged), encoding="utf-8")
                storage_state_path = str(merged_path)
                # Autosave into the same merged file the recorder was seeded from, so
                # cmd_stop_recording/cmd_cancel_recording can read back what the site did to
                # the session (rotated cookies, a mid-recording re-login) and write each app's
                # slice back to its own saved session. Confirmed safe to enable: the recorder
                # no longer autosaves on a timer (see the pump-loop comment in
                # recorder/session.py) — it only saves once, at teardown, so this no longer
                # reintroduces the flicker that motivated temporarily disabling this line.
                storage_state_autosave = storage_state_path

                # Re-record support: if this workflow already has a recording,
                # back its session up to disk before the new one replaces it as
                # the workflow's active session (set_recording below).
                backup_path: str | None = None
                if workflow.session_id:
                    try:
                        backup_path = _backup_previous_recording(workflow_id, workflow.session_id)
                    except OSError:
                        backup_path = None  # never block a re-record on backup failure

                start_url = str((workflow.protected_url or workflow.target_url or "about:blank")).strip()
                url_variables = payload.get("url_variables")
                if isinstance(url_variables, dict) and url_variables:
                    pattern = re.compile(r"\{\{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*\}\}")
                    start_url = pattern.sub(
                        lambda m: str(url_variables.get(m.group(1)) or m.group(0)),
                        start_url,
                    )
            else:
                start_url = str(payload.get("start_url") or "about:blank")
                storage_state_path = str(payload.get("storage_state_path") or "")
                storage_state_autosave = str(payload.get("storage_state_autosave_path") or "")

            sess = _recorder_registry.create(
                start_url=start_url,
                storage_state_path=storage_state_path,
                storage_state_autosave_path=storage_state_autosave,
                capture_hover=bool(payload.get("capture_hover")),
                # False from a caller with nowhere to draw the panel's view (the older record dialog).
                in_panel=payload.get("in_panel") is not False,
            )
            # Fires from the recorder's Playwright driver thread whenever a "Choose File"
            # click would otherwise open the native OS picker — see
            # RecordingSession._on_file_chooser. No req_id: this isn't a reply to any
            # in-flight command, just a broadcast the renderer picks up via onEvent.
            sess.on_file_picker_request = lambda request_id, default_dir, multiple: _emit_event(
                None,
                action="file_picker_request",
                session_id=sess.session_id,
                request_id=request_id,
                default_dir=default_dir,
                multiple=multiple,
            )
            # Fires from the recorder's Playwright driver thread whenever a native
            # alert/confirm/prompt would otherwise open — see RecordingSession._on_dialog.
            # Same no-req_id broadcast shape as on_file_picker_request above.
            sess.on_js_dialog_request = lambda request_id, dialog_type, message, default_value: _emit_event(
                None,
                action="js_dialog_request",
                session_id=sess.session_id,
                request_id=request_id,
                dialog_type=dialog_type,
                message=message,
                default_value=default_value,
            )
            # Fires when a dialog left unanswered too long was auto-accepted by
            # _drain_js_dialog_sync's timeout fallback, so the Studio can close its modal.
            sess.on_js_dialog_cancelled = lambda request_id: _emit_event(
                None,
                action="js_dialog_cancelled",
                session_id=sess.session_id,
                request_id=request_id,
            )
            # Fires once a human-answered dialog has actually been accepted/dismissed and the
            # recording browser has been brought back to front — see RecordingSession's
            # on_js_dialog_resolved doc comment. cmd_resolve_js_dialog's own return only means
            # the answer was queued, not that this happened yet, so the Studio must wait for
            # this event (not that RPC response) before releasing its always-on-top pin.
            sess.on_js_dialog_resolved = lambda request_id: _emit_event(
                None,
                action="js_dialog_resolved",
                session_id=sess.session_id,
                request_id=request_id,
            )
            try:
                self._loop.run(sess.start())
            except RuntimeError as exc:
                _recorder_registry.pop(sess.session_id)
                raise _CommandError("recorder_launch_failed", str(exc)) from exc
            # embedded = the recording is a view in Studio's browser panel (run id = session_id).
            result: dict[str, Any] = {"session_id": sess.session_id, "start_url": start_url, "embedded": bool(getattr(sess, "embedded", False))}
            downloads_dir = getattr(sess, "_downloads_dir", None)
            if downloads_dir is not None:
                # Files downloaded during this recording land here (mirrors the runtime's
                # runs/{runId}/) — surfaced so the UI can point the user at it. It's also the
                # default folder the Studio's own file-pick dialog opens to on "Choose File"
                # (see on_file_picker_request above / _on_file_chooser in recorder/session.py).
                result["downloads_dir"] = str(downloads_dir)
            if backup_path:
                result["previous_recording_backup"] = backup_path
            if workflow_id:
                from conxa_core.storage.workflow_store import set_recording

                updated = set_recording(workflow_id, sess.session_id)
                if updated is None:
                    self._loop.run(sess.stop())
                    _recorder_registry.pop(sess.session_id)
                    raise _CommandError("workflow_not_found", f"No workflow {workflow_id}")
                result["workflow_id"] = updated.id
            self._active_recording = sess.session_id
            return result

    def cmd_resolve_file_picker(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Delivers the Studio dialog's result back to a recording session in response to a
        "file_picker_request" event. `paths` is null/omitted on cancel. A session that's
        already gone (recording ended while the dialog was open) is a benign no-op."""
        session_id = _safe_id(payload.get("session_id"), "session_id")
        request_id = str(payload.get("request_id") or "").strip()
        paths_raw = payload.get("paths")
        paths = [str(p) for p in paths_raw] if isinstance(paths_raw, list) and paths_raw else None
        sess = _recorder_registry.get(session_id)
        if sess is not None and request_id:
            sess.resolve_file_pick(request_id, paths)
        return {"ok": True}

    def cmd_resolve_js_dialog(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Delivers the Studio dialog's answer back to a recording session in response to a
        "js_dialog_request" event. A session that's already gone (recording ended while the
        dialog was open) is a benign no-op."""
        session_id = _safe_id(payload.get("session_id"), "session_id")
        request_id = str(payload.get("request_id") or "").strip()
        accepted = bool(payload.get("accepted"))
        text = str(payload.get("text") or "")
        sess = _recorder_registry.get(session_id)
        if sess is not None and request_id:
            sess.resolve_js_dialog(request_id, accepted, text)
        return {"ok": True}

    def cmd_cancel_recording(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Discard an in-progress recording the user cancelled from the UI.

        Unlike cmd_stop_recording, this never saves anything — it force-stops the
        session (even if the browser is still open) and drops any workflow
        placeholder created at start_recording. This exists because closing the
        recorder's Chromium window doesn't clear the active-recording lock right
        away: session.py debounces the close for up to ~8s (plus a shutdown drain)
        before it flips browser_open to False on its own. Without this handler, a
        user who closes the browser and hits Cancel — instead of waiting for that
        auto-save/detection — leaves the lock held, so the very next
        start_recording attempt fails with "recording_in_progress" even though the
        browser is already gone.
        """
        session_id = _safe_id(payload.get("session_id"), "session_id")
        sess = _recorder_registry.get(session_id)
        if sess is not None:
            self._loop.run(sess.stop())
            _recorder_registry.pop(session_id)
        with self._rec_lock:
            if self._active_recording == session_id:
                self._active_recording = None

        workflow_id_raw = str(payload.get("workflow_id") or "").strip()
        if workflow_id_raw:
            from conxa_core.storage.workflow_store import clear_recording

            workflow_id = _safe_id(workflow_id_raw, "workflow_id")
            _refresh_group_app_sessions(workflow_id)
            clear_recording(workflow_id)
        return {"ok": True}

    def cmd_stop_recording(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        registry = _recorder_registry

        session_id = _safe_id(payload.get("session_id"), "session_id")
        sess = registry.get(session_id)
        if sess is None:
            raise _CommandError("session_not_found", f"No session {session_id}")
        workflow_id = str(payload.get("workflow_id") or "").strip()

        # sess.stop() joins the recorder's background thread, which owns Playwright's
        # sync API on its own thread — Playwright's sync driver only allows the thread
        # that started it to touch its objects. Touching sess._context / sess._page
        # from this (RPC) thread before that join completes throws "Cannot switch to
        # a different thread". Join first; the thread's own final storage_state save
        # (session.py's forced autosave right before its teardown) already wrote
        # the file on the correct thread, so nothing else needs to touch it here.
        events = sess.snapshot_events()
        self._loop.run(sess.stop())
        with self._rec_lock:
            if self._active_recording == session_id:
                self._active_recording = None
        if workflow_id:
            from conxa_core.storage.workflow_store import clear_recording, get_workflow, save_workflow

            workflow_id = _safe_id(workflow_id, "workflow_id")
            _refresh_group_app_sessions(workflow_id)
            if len(events) == 0:
                clear_recording(workflow_id)
                raise _CommandError("empty_recording", "No workflow actions were recorded.")
            # Persist every host this recording navigated to (same extraction the
            # compiler later stores in SkillMeta.visited_hosts) so the group page can
            # show all touched platform apps before the first compile.
            from conxa_compile.recorder.session import extract_visited_hosts

            wf = get_workflow(workflow_id)
            if wf is not None:
                wf.visited_hosts = extract_visited_hosts(events)
                wf.recording_duration_seconds = _recording_duration_seconds(events)
                save_workflow(wf)
            return {
                "session_id": session_id,
                "event_count": len(events),
                "workflow_id": workflow_id,
                "status": "recorded",
                "workflow_kind": "workflow",
            }
        return {"session_id": session_id, "event_count": len(events)}

