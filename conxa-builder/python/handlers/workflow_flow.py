"""Sign-in and recording, driven from a workflow's conversation (BUILD-38).

Each command here wraps commands that already exist (``cmd_start_group_app_auth``,
``cmd_finish_group_app_auth``, ``cmd_start_recording``, ``cmd_stop_recording``,
``cmd_cancel_recording``) and adds only two things: the thread's cards and event rows — which are
written *after* the wrapped command has returned, so a card never claims something the backend
hasn't done — and the in-memory lifecycle flags. The conversation never infers state from prose;
the lifecycle moves because one of these commands ran.

Cards: ``sign_in`` (waiting → saved | cancelled) and ``recording``
(recording ⇄ paused → saving → saved | discarded | browser_closed).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from handlers.protocol import _CommandError, _emit_event, _event_sink, _safe_id
from handlers.session import _recorder_registry

_WATCH_INTERVAL_S = 1.0


def _thread(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class WorkflowFlowMixin:
    # ── helpers ─────────────────────────────────────────────────────────────────────────────

    def _flow_workflow(self, payload: dict[str, Any]):
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(_safe_id(payload.get("workflow_id"), "workflow_id"))
        if workflow is None:
            raise _CommandError("workflow_not_found", "No such workflow.")
        return workflow

    def _flow_missing_apps(self, workflow: Any) -> list[Any]:
        """Apps this workflow's own URLs resolve to that nobody has signed in to yet."""
        from conxa_core.storage.group_store import apps_for_workflow, get_group

        group = get_group(workflow.group_id) if workflow.group_id else None
        if group is None:
            return []
        return [a for a in apps_for_workflow(group.apps, workflow.target_url, workflow.protected_url) if not a.captured_at]

    def _flow_say(self, workflow_id: str, text: str, sink: Any) -> None:
        from conxa_compile.conversation import harness, thread_store as ts

        harness.emit_item(sink, _thread(workflow_id), ts.append(_thread(workflow_id), "assistant_message", text=text))

    def _flow_card(self, workflow_id: str, card_id: str) -> dict[str, Any]:
        from conxa_compile.conversation import thread_store as ts

        card = ts.get_card(_thread(workflow_id), card_id)
        if card is None:
            raise _CommandError("card_not_found", "That card isn’t in this conversation any more.")
        return card

    # ── entry point ─────────────────────────────────────────────────────────────────────────

    def cmd_flow_begin(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Start (or continue) the workflow's first steps: sign in if an app it uses has no saved
        sign-in, otherwise record. Safe to call twice — an active recording or sign-in is reported."""
        from conxa_compile.conversation import runtime_state

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        flags = runtime_state.get_flags(workflow.id)
        if flags.get("recording"):
            return {"next": "record", "already": True}
        if flags.get("signing_in"):
            return {"next": "sign_in", "already": True}
        missing = self._flow_missing_apps(workflow)
        if missing:
            return self._flow_open_sign_in(workflow, missing[0], rid, sink)
        return self._flow_open_recording(workflow, rid, sink)

    # ── sign-in ─────────────────────────────────────────────────────────────────────────────

    def _flow_open_sign_in(self, workflow: Any, app: Any, rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state
        from conxa_core.storage.group_store import get_group

        started = self.cmd_start_group_app_auth({"group_id": workflow.group_id, "app_id": app.id}, rid)
        group = get_group(workflow.group_id)
        group_name = group.name if group else "this group"
        runtime_state.set_flags(workflow.id, signing_in=True, recording_discarded=False)
        harness.log_event(_thread(workflow.id), f"{app.name} needs sign-in — opened its sign-in page", glyph="warn", sink=sink)
        self._flow_say(
            workflow.id,
            f"{app.name} needs you to sign in first. I’ve opened its sign-in page in a browser window — sign in as you "
            f"normally do, including any two-step code, then press Done. Nothing is recorded yet. The sign-in is saved "
            f"for all of {group_name}, so the next workflow in this group skips this step.",
            sink,
        )
        card = harness.post_card(
            _thread(workflow.id), "sign_in", "waiting", sink=sink,
            refs={"group_id": workflow.group_id, "app_id": app.id, "session_id": started["session_id"]},
            app_name=app.name, login_url=started.get("login_url") or app.login_url, group_name=group_name,
            actions=["done", "cancel"],
        )
        return {"next": "sign_in", "card_id": card["card_id"]}

    def cmd_flow_sign_in_done(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """“I’m signed in — Done.” Saves the sign-in for the whole group, then goes straight on to
        recording. If the sign-in can’t be confirmed yet the window stays open and the card says why."""
        from conxa_compile.conversation import harness, runtime_state

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        refs = card.get("refs") or {}
        result = self.cmd_finish_group_app_auth(
            {"session_id": refs.get("session_id"), "group_id": refs.get("group_id"), "app_id": refs.get("app_id"),
             "force": bool(payload.get("force"))}, rid,
        )
        if result.get("confirmed") is False:
            harness.update_card(_thread(workflow.id), card["card_id"], note=str(result.get("reason") or ""), can_force=True, sink=sink)
            return {"next": "sign_in", "confirmed": False, "reason": result.get("reason")}
        harness.update_card(_thread(workflow.id), card["card_id"], state="saved", note="", actions=[], sink=sink)
        runtime_state.set_flags(workflow.id, signing_in=False)
        harness.log_event(_thread(workflow.id), f"Signed in to {card.get('app_name') or 'the app'}", glyph="ok", sink=sink)
        from conxa_core.storage.workflow_store import get_workflow

        return self._flow_open_recording(get_workflow(workflow.id) or workflow, rid, sink)

    def cmd_flow_sign_in_cancel(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        try:
            self.cmd_cancel_group_app_auth({"session_id": (card.get("refs") or {}).get("session_id")}, rid)
        except _CommandError:
            pass  # the window is already gone
        harness.update_card(_thread(workflow.id), card["card_id"], state="cancelled", actions=[], sink=sink)
        runtime_state.set_flags(workflow.id, signing_in=False)
        harness.log_event(_thread(workflow.id), "Sign-in cancelled", glyph="none", sink=sink)
        return {"ok": True}

    # ── recording ───────────────────────────────────────────────────────────────────────────

    def _flow_open_recording(self, workflow: Any, rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state
        from conxa_compile.recorder.plain import describe_event

        try:
            started = self.cmd_start_recording({"workflow_id": workflow.id}, rid)
        except _CommandError as exc:
            if exc.code == "auth_required":
                missing = self._flow_missing_apps(workflow)
                if missing:
                    return self._flow_open_sign_in(workflow, missing[0], rid, sink)
            raise
        session_id = started["session_id"]
        sess = _recorder_registry.get(session_id)
        workflow_id = workflow.id
        counter = {"n": 0}

        def on_event(event: Any) -> None:
            row = describe_event(event.model_dump(mode="json"))
            counter["n"] = max(counter["n"], int(row.get("sequence") or 0))
            _emit_event(None, phase="recording_event", workflow_id=workflow_id, session_id=session_id,
                        row=row, count=len(sess.snapshot_events()) if sess is not None else counter["n"])

        if sess is not None:
            sess.event_hook = on_event
        runtime_state.set_flags(workflow_id, recording=True, recording_paused=False, signing_in=False,
                                recording_discarded=False)
        host = (workflow.target_url or "").split("//")[-1].split("/")[0]
        harness.log_event(_thread(workflow_id), "Recording started", glyph="run", sink=sink, subject=host or None)
        self._flow_say(workflow_id, "Do the task in the browser window; each action shows up here as it’s captured. "
                       "Mark a typed value “Changes each run” and I won’t need to ask about it later.", sink)
        card = harness.post_card(
            _thread(workflow_id), "recording", "recording", sink=sink,
            refs={"session_id": session_id}, started_at=time.time(), host=host, count=0,
            actions=["pause", "finish", "discard"], warnings=started.get("warnings") or [],
        )
        threading.Thread(target=self._flow_watch_recording, args=(workflow_id, session_id, card["card_id"]), daemon=True).start()
        return {"next": "record", "card_id": card["card_id"], "session_id": session_id}

    def _flow_watch_recording(self, workflow_id: str, session_id: str, card_id: str) -> None:
        """Notice a recording browser that was closed by hand, so the card can offer to save what
        was captured instead of leaving the conversation waiting on a window that is gone."""
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        sink = None
        while True:
            time.sleep(_WATCH_INTERVAL_S)
            card = ts.get_card(_thread(workflow_id), card_id)
            if card is None or card.get("state") not in ("recording", "paused"):
                return
            sess = _recorder_registry.get(session_id)
            if sess is None:
                return
            if sess.browser_open:
                continue
            count = len(sess.snapshot_events())
            runtime_state.set_flags(workflow_id, recording=False, recording_paused=False)
            harness.update_card(_thread(workflow_id), card_id, state="browser_closed", count=count,
                                actions=["finish", "discard"] if count else ["discard"], sink=sink)
            harness.log_event(_thread(workflow_id), "The browser closed before you finished", glyph="warn", sink=sink,
                              subject=f"{count} action{'s' if count != 1 else ''} captured")
            return

    def _flow_session(self, card: dict[str, Any]):
        sid = str((card.get("refs") or {}).get("session_id") or "")
        return sid, _recorder_registry.get(sid)

    def cmd_flow_pause_recording(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state
        from conxa_compile.recorder import hints

        workflow = self._flow_workflow(payload)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        sid, sess = self._flow_session(card)
        if sess is None or card.get("state") != "recording":
            raise _CommandError("not_recording", "There is no recording to pause.")
        sess.pause()
        hints.add_paused_span(sid, len(sess.snapshot_events()))
        runtime_state.set_flags(workflow.id, recording_paused=True)
        harness.update_card(_thread(workflow.id), card["card_id"], state="paused", actions=["resume", "finish", "discard"], sink=_event_sink(rid))
        return {"ok": True}

    def cmd_flow_resume_recording(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state

        workflow = self._flow_workflow(payload)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        _sid, sess = self._flow_session(card)
        if sess is None or card.get("state") != "paused":
            raise _CommandError("not_paused", "This recording isn’t paused.")
        sess.resume()
        runtime_state.set_flags(workflow.id, recording_paused=False)
        harness.update_card(_thread(workflow.id), card["card_id"], state="recording", actions=["pause", "finish", "discard"], sink=_event_sink(rid))
        return {"ok": True}

    def cmd_flow_finish_recording(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Finish and save. Also what “Save N actions” does after the browser was closed by hand."""
        from conxa_compile.conversation import harness, runtime_state
        from conxa_core.storage.workflow_store import get_workflow

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        sid, _sess = self._flow_session(card)
        if card.get("state") not in ("recording", "paused", "browser_closed"):
            raise _CommandError("not_recording", "This recording was already finished.")
        harness.update_card(_thread(workflow.id), card["card_id"], state="saving", actions=[], sink=sink)
        try:
            result = self.cmd_stop_recording({"session_id": sid, "workflow_id": workflow.id}, rid)
        except _CommandError as exc:
            runtime_state.set_flags(workflow.id, recording=False, recording_paused=False, recording_discarded=exc.code == "empty_recording")
            harness.update_card(_thread(workflow.id), card["card_id"], state="discarded", note=exc.message, actions=[], sink=sink)
            harness.log_event(_thread(workflow.id), "Nothing was saved", glyph="none", sink=sink, subject=exc.message)
            raise
        runtime_state.set_flags(workflow.id, recording=False, recording_paused=False)
        saved = get_workflow(workflow.id)
        seconds = int(getattr(saved, "recording_duration_seconds", 0) or 0)
        count = int(result.get("event_count") or 0)
        harness.update_card(_thread(workflow.id), card["card_id"], state="saved", count=count, duration_seconds=seconds, actions=[], sink=sink)
        pretty = f"{count} action{'s' if count != 1 else ''}" + (f" · {seconds // 60} min {seconds % 60:02d} s" if seconds else "")
        harness.log_event(_thread(workflow.id), "Recording saved", glyph="ok", sink=sink, subject=pretty)
        self._flow_say(workflow.id, f"Got it — {plural(count, 'action')}. I’m compiling them into steps that can find the right "
                       "buttons and fields again, even when the page shifts a little. I’ll ask you a few things once that’s done.", sink)
        if payload.get("auto_compile", True):
            self._flow_start_compile(workflow.id)
        return {"ok": True, "event_count": count, "session_id": sid}

    def cmd_flow_discard_recording(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        sid, _sess = self._flow_session(card)
        self.cmd_cancel_recording({"session_id": sid, "workflow_id": workflow.id}, rid)
        runtime_state.set_flags(workflow.id, recording=False, recording_paused=False, recording_discarded=True)
        harness.update_card(_thread(workflow.id), card["card_id"], state="discarded", actions=[], sink=sink)
        harness.log_event(_thread(workflow.id), "Recording discarded", glyph="none", sink=sink,
                          subject="Nothing was saved")
        return {"ok": True}

    def cmd_flow_set_varies(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """“Changes each run?” for one typed value. ``varies`` null clears the answer."""
        from conxa_compile.recorder import hints

        session_id = _safe_id(payload.get("session_id"), "session_id")
        varies = payload.get("varies")
        data = hints.set_varies(session_id, int(payload.get("sequence") or 0), None if varies is None else bool(varies))
        return {"varies": data.get("varies", {})}

    def cmd_recording_rows(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Everything captured so far, as plain-language rows — to repaint the live list after a reload."""
        from conxa_compile.recorder import hints
        from conxa_compile.recorder.plain import describe_event

        session_id = _safe_id(payload.get("session_id"), "session_id")
        sess = _recorder_registry.get(session_id)
        rows = [describe_event(e) for e in (sess.snapshot_events() if sess is not None else [])]
        return {"rows": rows, "varies": hints.load(session_id).get("varies", {}), "paused": bool(sess and sess.status().get("paused"))}
