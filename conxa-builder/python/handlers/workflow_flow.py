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

import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from handlers.protocol import _CommandError, _emit_event, _event_sink, _safe_id
from handlers.session import _recorder_registry

_WATCH_INTERVAL_S = 1.0
_SIGN_IN_WATCH_INTERVAL_S = 1.5
_sign_in_finish_lock = threading.Lock()
# A login-shaped path: the runtime's LOGIN_PATH_RE / AUTH_FAILURE_URL_RE plus the single-sign-on
# hops a sign-in passes through. Being on one means the person is still signing in.
_LOGIN_PATH_RE = re.compile(r"/(login|log-in|signin|sign-in|sign_in|servicelogin|auth|oauth2?|sso|logout|session-expired)(/|$)", re.I)


def _looks_like_login_page(url: str) -> bool:
    return bool(_LOGIN_PATH_RE.search(urlsplit(url).path or "/"))


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
        """Apps in the workflow's group that must be signed in before recording — never connected,
        or a saved sign-in that has expired. The same check the record command gates on."""
        from conxa_core.storage.group_store import get_group
        from handlers.session import group_apps_needing_sign_in

        group = get_group(workflow.group_id) if workflow.group_id else None
        if group is None:
            return []
        return group_apps_needing_sign_in(group)

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
        expired = bool(app.captured_at)
        harness.log_event(_thread(workflow.id),
                          f"{app.name} {'sign-in expired' if expired else 'needs sign-in'} — opened its sign-in page",
                          glyph="warn", sink=sink)
        embedded = bool(started.get("embedded"))
        where = "in the browser on the right" if embedded else "in a browser window"
        why = "your saved sign-in has expired, so sign in again" if expired else "you need to sign in first"
        self._flow_say(
            workflow.id,
            f"Before recording: {app.name} — {why}. I’ve opened its sign-in page {where} — sign in as you "
            f"normally do, including any two-step code. I’ll notice when you’re in and carry on by myself "
            f"(or press “I’m signed in”). Nothing is recorded yet. The sign-in "
            f"is saved for all of {group_name}, so the next workflow in this group skips this step.",
            sink,
        )
        card = harness.post_card(
            _thread(workflow.id), "sign_in", "waiting", sink=sink,
            refs={"group_id": workflow.group_id, "app_id": app.id, "session_id": started["session_id"],
                  "embedded": embedded},
            app_name=app.name, login_url=started.get("login_url") or app.login_url, group_name=group_name,
            actions=["done", "cancel"],
        )
        threading.Thread(target=self._flow_watch_sign_in, args=(workflow.id, card["card_id"], started["session_id"]),
                         daemon=True).start()
        return {"next": "sign_in", "card_id": card["card_id"]}

    def cmd_flow_sign_in_done(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """“I’m signed in — Done.” Saves the sign-in for the whole group, then goes straight on to
        recording. If the sign-in can’t be confirmed yet the window stays open and the card says why.
        Usually not needed: _flow_watch_sign_in does this by itself once the person signs in."""
        workflow = self._flow_workflow(payload)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        return self._flow_finish_sign_in(workflow, card["card_id"], rid, force=bool(payload.get("force")), auto=False)

    def _flow_finish_sign_in(self, workflow: Any, card_id: str, rid: str | None, *, force: bool, auto: bool) -> dict[str, Any]:
        """Confirm and save the sign-in, then open the next app's sign-in or the recording. Shared by
        the button and the watcher; one at a time, and a no-op once the card is no longer waiting,
        so the two can never both finish the same sign-in."""
        from conxa_compile.conversation import harness, runtime_state
        from conxa_core.storage.workflow_store import get_workflow

        sink = _event_sink(rid)
        with _sign_in_finish_lock:
            card = self._flow_card(workflow.id, card_id)
            if card.get("state") != "waiting":
                return {"next": "sign_in", "already": True}
            refs = card.get("refs") or {}
            result = self.cmd_finish_group_app_auth(
                {"session_id": refs.get("session_id"), "group_id": refs.get("group_id"), "app_id": refs.get("app_id"),
                 "force": force}, rid or "",
            )
            if result.get("confirmed") is False:
                # An automatic check that came too early stays quiet — the person is still signing in.
                if not auto:
                    harness.update_card(_thread(workflow.id), card_id, note=str(result.get("reason") or ""), can_force=True, sink=sink)
                return {"next": "sign_in", "confirmed": False, "reason": result.get("reason")}
            harness.update_card(_thread(workflow.id), card_id, state="saved", note="", actions=[], sink=sink)
            runtime_state.set_flags(workflow.id, signing_in=False)
        harness.log_event(_thread(workflow.id), f"Signed in to {card.get('app_name') or 'the app'}"
                          + (" — detected automatically" if auto else ""), glyph="ok", sink=sink)
        fresh = get_workflow(workflow.id) or workflow
        if runtime_state.get_flags(workflow.id).get("retest_after_sign_in"):
            # The sign-in was asked for by a test that paused on an expired one: carry on testing, not recording.
            missing = self._flow_missing_apps(fresh)
            if missing:
                return self._flow_open_sign_in(fresh, missing[0], rid or "", sink)
            return self._flow_retest_after_sign_in(fresh, sink)
        return self._flow_open_recording(fresh, rid or "", sink)

    def cmd_flow_sign_in_then_test(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """A test paused because a saved sign-in expired. Sign in again, then run the same test again with the
        same inputs (the guard still pauses before anything that can't be undone). Never starts a recording."""
        from conxa_compile.conversation import runtime_state

        workflow = self._flow_workflow(payload)
        if not workflow.skill_id:
            raise _CommandError("workflow_not_compiled", "Compile this workflow before testing it.")
        runtime_state.set_flags(workflow.id, retest_after_sign_in=True)
        missing = self._flow_missing_apps(workflow)
        if missing:
            return self._flow_open_sign_in(workflow, missing[0], rid, _event_sink(rid))
        return self._flow_retest_after_sign_in(workflow, _event_sink(rid))

    def _flow_retest_after_sign_in(self, workflow: Any, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state

        runtime_state.set_flags(workflow.id, retest_after_sign_in=False, sign_in_expired=False)
        harness.log_event(_thread(workflow.id), "Testing again with the same inputs", glyph="run", sink=sink)
        threading.Thread(target=self._tf_background, args=(workflow.id, dict(workflow.last_test_inputs or {}), True, True),
                         daemon=True).start()
        return {"next": "test"}

    def _flow_watch_sign_in(self, workflow_id: str, card_id: str, session_id: str) -> None:
        """Finish the sign-in by itself once the person has signed in, so nobody has to press
        “I’m signed in”. Signed in = the sign-in tab reached the app's success page, or it was on a
        login page and has since left it for a page that isn't one. Each such page is checked once
        with the same confirmation the button runs (it compares the live session against a signed-out
        one, so a page that only looks done is not taken as signed in); a check that comes too early
        is silent and the next page change tries again. The button stays as the fallback."""
        from conxa_compile.conversation import thread_store as ts
        from conxa_compile.recorder.frame_utils import is_blank_url

        seen_login = False
        tried: set[str] = set()
        while True:
            time.sleep(_SIGN_IN_WATCH_INTERVAL_S)
            card = ts.get_card(_thread(workflow_id), card_id)
            if card is None or card.get("state") != "waiting":
                return
            sess = _recorder_registry.get(session_id)
            if sess is None or not sess.browser_open:
                return
            url = str(getattr(sess, "current_url", "") or "")
            if not url.startswith(("http://", "https://")) or is_blank_url(url):
                continue
            if _looks_like_login_page(url):
                seen_login = True
                continue
            page = url.split("#")[0].split("?")[0]
            if not (seen_login or getattr(sess, "reached_wait_url", False)) or page in tried:
                continue
            tried.add(page)
            try:
                from conxa_core.storage.workflow_store import get_workflow

                workflow = get_workflow(workflow_id)
                if workflow is None:
                    return
                result = self._flow_finish_sign_in(workflow, card_id, None, force=False, auto=True)
            except Exception:  # noqa: BLE001 — never crash the watcher; the button still works
                continue
            if result.get("confirmed") is not False:
                return

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
        runtime_state.set_flags(workflow.id, signing_in=False, retest_after_sign_in=False)
        return {"ok": True}

    # ── recording ───────────────────────────────────────────────────────────────────────────

    def _flow_post_record_plan(self, workflow: Any, sink: Any) -> None:
        """A checklist from the person's own description, posted a moment after recording opens (a
        model call must not delay the browser). Nothing is posted without a description or a plan."""
        task = (workflow.task_description or "").strip()
        if not task:
            return
        workflow_id, target = workflow.id, workflow.target_url or ""

        def work() -> None:
            try:
                from conxa_compile.conversation import harness, record_plan

                steps = record_plan.generate(task, target)
                if steps:
                    harness.post_card(_thread(workflow_id), "record_plan", "open", sink=sink, steps=steps, actions=[])
            except Exception:  # noqa: BLE001 -- advisory only
                pass

        threading.Thread(target=work, daemon=True, name="record-plan").start()

    def _flow_open_recording(self, workflow: Any, rid: str, sink: Any, variant_of: str = "",
                             segment_from: str = "", adopt_run_id: str = "", segment_mode: str = "replace") -> dict[str, Any]:
        """Open the recording browser. ``variant_of`` (an outcome fork's step key) makes it a
        VARIANT take: a second recording of the same task showing a different outcome, which never
        replaces the workflow's own recording (see cmd_start_recording / cmd_apply_variant)."""
        from conxa_compile.conversation import harness, runtime_state
        from conxa_compile.recorder.plain import describe_event

        try:
            # A segment (re-record from a step) is stored beside the workflow's own recording exactly like a
            # variant take: it never replaces it. The "segment:" prefix keeps its key apart from a fork's.
            kind_key = f"segment:{segment_from}" if segment_from else variant_of
            started = self.cmd_start_recording({
                "workflow_id": workflow.id,
                **({"variant_of": kind_key} if kind_key else {}),
                **({"adopt_run_id": adopt_run_id} if adopt_run_id else {}),
            }, rid)
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

        hinter = None
        if not variant_of and not segment_from:
            try:
                from conxa_compile.app_graph.hints import RecordingHinter
                from conxa_core.workspace import LOCAL_WORKSPACE_ID

                hinter = RecordingHinter(workflow.workspace_id or LOCAL_WORKSPACE_ID)
            except Exception:  # noqa: BLE001 -- a hint, never worth failing a recording
                hinter = None

        def on_event(event: Any) -> None:
            data = event.model_dump(mode="json")
            row = describe_event(data)
            counter["n"] = max(counter["n"], int(row.get("sequence") or 0))
            _emit_event(None, phase="recording_event", workflow_id=workflow_id, session_id=session_id,
                        row=row, count=len(sess.snapshot_events()) if sess is not None else counter["n"])
            if hinter is not None:
                try:
                    hint = hinter(data)
                    if hint:
                        harness.log_event(_thread(workflow_id), hint, glyph="warn", sink=_event_sink(None))
                except Exception:  # noqa: BLE001 -- advisory only
                    pass

        if sess is not None:
            sess.event_hook = on_event
        runtime_state.set_flags(workflow_id, recording=True, recording_paused=False, signing_in=False,
                                recording_discarded=False)
        host = (workflow.target_url or "").split("//")[-1].split("/")[0]
        harness.log_event(_thread(workflow_id), "Recording started", glyph="run", sink=sink, subject=host or None)
        embedded = bool(started.get("embedded"))
        where = "the browser on the right" if embedded else "the browser window"
        if segment_from:
            what = ("is kept as a second way the workflow can go from that step on" if segment_mode == "branch"
                    else "replaces that step and the ones after it")
            self._flow_say(workflow_id, f"The browser on the right is at the point just before the step you chose — everything "
                           f"before it is done. Carry on from there; when you press Finish, what you do {what}. "
                           "The steps before it stay exactly as they are.", sink)
        elif variant_of:
            self._flow_say(workflow_id, f"Record it once more in {where}, this time so the popup does NOT appear "
                           "(for example, upload a file that isn’t there yet). Then press Finish — I’ll compare it with your "
                           "first recording; your first recording isn’t touched.", sink)
        else:
            self._flow_say(workflow_id, f"Do the task in {where}; each action shows up here as it’s captured. "
                           "Mark a typed value “Changes each run” and I won’t need to ask about it later.", sink)
            self._flow_post_record_plan(workflow, sink)
        card = harness.post_card(
            _thread(workflow_id), "recording", "recording", sink=sink,
            refs={"session_id": session_id, "embedded": embedded, **({"variant_of": variant_of} if variant_of else {}),
                  **({"segment_from": segment_from, "segment_mode": segment_mode} if segment_from else {})},
            started_at=time.time(), host=host, count=0,
            actions=["pause", "finish", "discard"], warnings=started.get("warnings") or [],
        )
        threading.Thread(target=self._flow_watch_recording, args=(workflow_id, session_id, card["card_id"]), daemon=True).start()
        return {"next": "record", "card_id": card["card_id"], "session_id": session_id}

    def _flow_watch_recording(self, workflow_id: str, session_id: str, card_id: str) -> None:
        """Notice a recording browser that was closed by hand, so the card can offer to save what
        was captured instead of leaving the conversation waiting on a window that is gone."""
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        sink = _event_sink(None)  # no request to answer: a broadcast the renderer applies to the thread
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
            return

    def _flow_session(self, card: dict[str, Any]):
        sid = str((card.get("refs") or {}).get("session_id") or "")
        return sid, _recorder_registry.get(sid)

    def cmd_flow_capture_value(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """"Read this": the next click in the recording browser reads that element's value into an
        output the finished workflow returns, instead of clicking it. ``on: false`` cancels."""
        workflow = self._flow_workflow(payload)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        _sid, sess = self._flow_session(card)
        if sess is None or card.get("state") != "recording":
            raise _CommandError("not_recording", "Capture a value while you are recording.")
        on = payload.get("on", True) is not False
        sess.request_capture(on)
        return {"ok": True, "capturing": on}

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
        variant_of = str((card.get("refs") or {}).get("variant_of") or "")
        if variant_of:
            return self._flow_finish_variant(workflow, card, sid, variant_of, rid, sink)
        segment_from = str((card.get("refs") or {}).get("segment_from") or "")
        if segment_from:
            return self._flow_finish_segment(workflow, card, sid, segment_from, rid, sink,
                                             str((card.get("refs") or {}).get("segment_mode") or "replace"))
        harness.update_card(_thread(workflow.id), card["card_id"], state="saving", actions=[], sink=sink)
        try:
            result = self.cmd_stop_recording({"session_id": sid, "workflow_id": workflow.id}, rid)
        except _CommandError as exc:
            runtime_state.set_flags(workflow.id, recording=False, recording_paused=False, recording_discarded=exc.code == "empty_recording")
            harness.update_card(_thread(workflow.id), card["card_id"], state="discarded", note=exc.message, actions=[], sink=sink)
            raise
        runtime_state.set_flags(workflow.id, recording=False, recording_paused=False)
        saved = get_workflow(workflow.id)
        seconds = int(getattr(saved, "recording_duration_seconds", 0) or 0)
        count = int(result.get("event_count") or 0)
        harness.update_card(_thread(workflow.id), card["card_id"], state="saved", count=count, duration_seconds=seconds, actions=[], sink=sink)
        self._flow_say(workflow.id, f"Got it — {plural(count, 'action')}. I’m compiling them into steps that can find the right "
                       "buttons and fields again, even when the page shifts a little. I’ll ask you a few things once that’s done.", sink)
        if payload.get("auto_compile", True):
            self._flow_start_compile(workflow.id)
        return {"ok": True, "event_count": count, "session_id": sid}

    # ── re-record from a step ───────────────────────────────────────────────────────────────

    def cmd_flow_rerecord_from(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """"Step 7 is wrong, let me show you": replay the workflow up to the step before it, hand that live page
        to the recorder, and (when the person finishes) compile what they did and put it in place of that step
        and everything after. Returns at once; the replay and the recording run on the conversation."""
        from conxa_compile.compiler.step_key import step_keys
        from conxa_compile.conversation import runtime_state
        from conxa_compile.skill_package_builder_saved_skill import runtime_step_index
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow, list_workflows

        if payload.get("workflow_id"):
            workflow = self._flow_workflow(payload)
        else:
            skill_id = _safe_id(payload.get("skill_id"), "skill_id")
            workflow = next((w for w in list_workflows() if w.skill_id == skill_id), None)
            if workflow is None:
                raise _CommandError("workflow_not_found", "That skill doesn\u2019t belong to a workflow.")
        workflow = get_workflow(workflow.id)
        doc = read_skill(workflow.skill_id) if workflow.skill_id else None
        if doc is None:
            raise _CommandError("not_compiled", "Compile this workflow before re-recording part of it.")
        steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        keys = step_keys(steps)
        step_key = str(payload.get("step_key") or "")
        if not step_key and payload.get("step_index") is not None:
            index = int(payload["step_index"])
            step_key = keys[index] if 0 <= index < len(keys) else ""
        if step_key not in keys:
            raise _CommandError("step_not_found", "That step isn\u2019t in the workflow any more.")
        if keys.index(step_key) == 0:
            raise _CommandError("first_step", "That is the first step \u2014 record the whole workflow again instead.")
        runtime_index = runtime_step_index(doc, step_key)
        if runtime_index is None:
            raise _CommandError("step_not_runnable", "That step isn\u2019t part of what runs, so there is nothing to replay up to.")
        flags = runtime_state.get_flags(workflow.id)
        if flags.get("recording") or flags.get("testing"):
            raise _CommandError("busy", "Finish the recording or test that is in progress first.")
        inputs = {str(i.get("id")): i.get("default") for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("id")}
        missing = [str(i.get("id")) for i in doc.get("inputs") or []
                   if isinstance(i, dict) and i.get("id") and not i.get("optional") and str(i.get("default") or "").strip() == ""]
        if missing:
            raise _CommandError("inputs_required", "Fill in " + ", ".join(missing) + " first \u2014 the replay needs a value for each.")
        mode = "branch" if payload.get("mode") == "branch" else "replace"
        runtime_state.set_flags(workflow.id, testing=True)
        threading.Thread(target=self._rerecord_safe, args=(workflow.id, step_key, runtime_index, inputs, mode), daemon=True).start()
        return {"ok": True, "step_key": step_key, "runtime_index": runtime_index, "mode": mode}

    def _rerecord_safe(self, workflow_id: str, step_key: str, runtime_index: int, inputs: dict[str, Any], mode: str = "replace") -> None:
        from conxa_compile.conversation import harness, runtime_state

        sink = _event_sink(f"rerecord_{workflow_id}")
        try:
            self._rerecord(workflow_id, step_key, runtime_index, inputs, sink, mode)
        except _CommandError as exc:
            runtime_state.set_flags(workflow_id, testing=False)
            harness.log_event(_thread(workflow_id), "Couldn\u2019t continue from that step", glyph="fail", sink=sink, subject=exc.message[:160])
            self._flow_say(workflow_id, f"I couldn\u2019t get the browser to that point: {exc.message}", sink)
        except Exception as exc:  # noqa: BLE001 -- a background replay must never take the engine down
            import traceback

            traceback.print_exc()
            runtime_state.set_flags(workflow_id, testing=False)
            self._flow_say(workflow_id, f"I couldn\u2019t get the browser to that point: {exc}", sink)

    def _rerecord(self, workflow_id: str, step_key: str, runtime_index: int, inputs: dict[str, Any], sink: Any, mode: str = "replace") -> None:
        from conxa_compile.conversation import harness, runtime_state
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        harness.log_event(_thread(workflow_id), "Replaying up to the step you chose", glyph="run", sink=sink)
        self._flow_say(workflow_id, "Replaying the workflow up to just before that step \u2014 the browser on the right will do it by "
                       "itself. When it stops, it\u2019s your turn.", sink)
        self._tf_ensure_built(workflow, f"rerecord_{workflow_id}")
        result = self.cmd_test_workflow({
            "workflow_id": workflow_id, "inputs": inputs, "headless": False, "guard": False,
            "stop_before_step": runtime_index, "handoff_park": True,
        }, f"rerecord_{workflow_id}")
        runtime_state.set_flags(workflow_id, testing=False)
        run_id = result.get("handed_off_run_id")
        if result.get("status") != "stopped_at_step" or not run_id:
            raise _CommandError("replay_failed", "The replay didn\u2019t reach that step \u2014 test the workflow to see where it stops.")
        self._flow_open_recording(get_workflow(workflow_id), f"rerecord_{workflow_id}", sink, segment_from=step_key, adopt_run_id=run_id,
                                  segment_mode=mode)

    def _flow_finish_segment(self, workflow: Any, card: dict[str, Any], sid: str, step_key: str, rid: str, sink: Any,
                             mode: str = "replace") -> dict[str, Any]:
        """Finish a re-recorded part: stop, then compile it and splice it in over the chosen step and
        everything after, in the background. The workflow's own recording is never touched."""
        from conxa_compile.conversation import harness, runtime_state

        thread = _thread(workflow.id)
        harness.update_card(thread, card["card_id"], state="saving", actions=[], sink=sink)
        try:
            stopped = self.cmd_stop_recording({"session_id": sid, "workflow_id": workflow.id}, rid)
        except _CommandError as exc:
            runtime_state.set_flags(workflow.id, recording=False, recording_paused=False)
            harness.update_card(thread, card["card_id"], state="discarded", note=exc.message, actions=[], sink=sink)
            raise
        runtime_state.set_flags(workflow.id, recording=False, recording_paused=False)
        count = int(stopped.get("event_count") or 0)
        harness.update_card(thread, card["card_id"], state="saved", count=count, duration_seconds=0, actions=[], sink=sink)
        where = "adding them as a second way from that step on" if mode == "branch" else "putting them in place of that step and the ones after it"
        self._flow_say(workflow.id, f"Got it \u2014 {plural(count, 'action')}. I\u2019m compiling them and {where}.", sink)
        threading.Thread(target=self._splice_segment_safe, args=(workflow.id, sid, step_key, mode), daemon=True).start()
        return {"ok": True, "event_count": count, "session_id": sid}

    def _splice_segment_safe(self, workflow_id: str, sid: str, step_key: str, mode: str = "replace") -> None:
        from conxa_compile.conversation import harness
        from conxa_core.storage.json_store import delete_skill
        from conxa_core.storage.workflow_store import get_workflow, remove_variant

        rid = f"segment_{uuid.uuid4().hex[:10]}"
        sink = _event_sink(rid)
        workflow = get_workflow(workflow_id)
        try:
            compiled = self.cmd_compile({"session_id": sid, "workflow_id": workflow_id, "skill_title": f"{workflow.name} (part)", "segment": True}, rid)
            put_in = self.cmd_branch_segment if mode == "branch" else self.cmd_splice_segment
            spliced = put_in({"skill_id": workflow.skill_id, "segment_skill_id": compiled["skill_id"], "from_step_key": step_key}, rid)
        except _CommandError as exc:
            harness.log_event(_thread(workflow_id), "Couldn\u2019t put the new part in", glyph="fail", sink=sink, subject=exc.message[:160])
            self._flow_say(workflow_id, f"I couldn\u2019t put the new part in: {exc.message} Your workflow is unchanged.", sink)
            return
        except Exception as exc:  # noqa: BLE001
            self._flow_say(workflow_id, f"I couldn\u2019t put the new part in: {exc} Your workflow is unchanged.", sink)
            return
        finally:
            try:
                remove_variant(workflow_id, sid)
            except Exception:  # noqa: BLE001
                pass
        try:
            delete_skill(compiled["skill_id"])  # the part only existed to be spliced in
        except Exception:  # noqa: BLE001
            pass
        added = int(spliced.get("added") or 0)
        stale = int(spliced.get("stale_answers") or 0)
        from_step = int(spliced.get("branched_from", spliced.get("replaced_from")) or 0) + 1
        harness.log_event(_thread(workflow_id), "New part added", glyph="ok", sink=sink, subject=f"{plural(added, 'step')} from step {from_step}")
        self._flow_say(workflow_id, (f"Done \u2014 from step {from_step} on, the workflow now takes whichever way the page offers: the one it "
                                     f"had, or your new {plural(added, 'step')}. You can see both in Human Edit."
                                     if mode == "branch" else
                                     f"Done \u2014 the workflow now has your new {plural(added, 'step')} from step {from_step} on.")
                       + (f" {plural(stale, 'earlier answer')} about the old steps no longer apply." if stale else "")
                       + " Test it to check the whole thing still works.", sink)
        self._flow_post_summary(workflow_id, sink)

    def cmd_apply_variant(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Learn from a variant recording ("the case where the popup does NOT appear") and fold it
        into the workflow's outcome fork. No compile, no credit, no model call: the two takes are
        compared by rule (compiler/variant_align.py) and only what the fork explains is applied.
        Everything else is returned as ``unexplained`` for the author to read, never merged."""
        import copy
        from conxa_compile.compiler.variant_align import compare_variant, normalize_events
        from conxa_compile.editor.copilot_proposals import ProposalError, resolve_step_index
        from conxa_compile.editor.workflow_mutations import apply_variant_observation
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_core.storage.session_events import read_session_events
        from conxa_core.storage.workflow_store import remove_variant

        workflow = self._flow_workflow(payload)
        session_id = _safe_id(payload.get("session_id"), "session_id")
        fork_step_key = str(payload.get("fork_step_key") or "")
        if not workflow.skill_id or not workflow.session_id:
            raise _CommandError("not_compiled", "Compile this workflow before recording another way it can go.")
        doc = read_skill(workflow.skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {workflow.skill_id}")
        try:
            step_index = resolve_step_index(doc, fork_step_key)
        except ProposalError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        step = ((doc.get("skills") or [{}])[0].get("steps") or [])[step_index]
        states = [s for s in ((step.get("outcome_fork") or {}).get("states") or []) if isinstance(s, dict)]
        known = [((s.get("signature") or {}).get("container_selector") or "", (s.get("signature") or {}).get("title") or "") for s in states]

        def load(sid: str) -> list[dict[str, Any]]:
            raw = [e if isinstance(e, dict) else e.model_dump(mode="json") for e in read_session_events(sid)]
            try:
                return normalize_events(raw)
            except Exception:  # noqa: BLE001 -- compare the raw take rather than fail the whole feature
                return raw

        observation = compare_variant(load(workflow.session_id), load(session_id), known)
        applied = bool(observation["clean_observed"] or observation["new_dialogs"])
        if applied:
            self._push_undo(workflow.skill_id, copy.deepcopy(doc))
            try:
                doc = apply_variant_observation(doc, step_index, session_id, observation)
            except ValueError as exc:
                raise _CommandError(str(exc), f"Could not apply the new recording: {exc}") from exc
            write_skill(workflow.skill_id, doc)
        remove_variant(workflow.id, session_id)
        return {"ok": True, "applied": applied, "clean_observed": observation["clean_observed"],
                "new_popups": len(observation["new_dialogs"]), "unexplained": observation["unexplained"]}

    def _flow_finish_variant(self, workflow: Any, card: dict[str, Any], sid: str, variant_of: str, rid: str, sink: Any) -> dict[str, Any]:
        """Finish a variant take: compare it with the first recording and fold what it shows into the
        outcome fork. No compile, no credit, and the first recording is never touched."""
        from conxa_compile.conversation import harness, runtime_state

        thread = _thread(workflow.id)
        harness.update_card(thread, card["card_id"], state="saving", actions=[], sink=sink)
        try:
            stopped = self.cmd_stop_recording({"session_id": sid, "workflow_id": workflow.id}, rid)
            learned = self.cmd_apply_variant({"workflow_id": workflow.id, "session_id": sid, "fork_step_key": variant_of}, rid)
        except _CommandError as exc:
            runtime_state.set_flags(workflow.id, recording=False, recording_paused=False)
            harness.update_card(thread, card["card_id"], state="discarded", note=exc.message, actions=[], sink=sink)
            raise
        runtime_state.set_flags(workflow.id, recording=False, recording_paused=False)
        count = int(stopped.get("event_count") or 0)
        harness.update_card(thread, card["card_id"], state="saved", count=count, duration_seconds=0, actions=[], sink=sink)
        lines = []
        if learned["clean_observed"]:
            lines.append("Now I’ve seen it go through with no popup, so that case is covered too.")
        if learned["new_popups"]:
            lines.append(f"It also showed {learned['new_popups']} other popup{'s' if learned['new_popups'] != 1 else ''}, which I’ve added to the workflow — check what each should do in Human Edit.")
        if not learned["applied"]:
            lines.append("Nothing in it changed the workflow.")
        if learned["unexplained"]:
            lines.append("These differences are NOT added, because they aren’t about the popup: " + "; ".join(learned["unexplained"][:3]))
        harness.log_event(thread, "Compared with your first recording", glyph="ok", sink=sink,
                          subject=f"{count} action{'s' if count != 1 else ''}")
        self._flow_say(workflow.id, " ".join(lines) + " Your first recording and your answers are unchanged.", sink)
        return {"ok": True, "event_count": count, "session_id": sid, **{k: learned[k] for k in ("applied", "clean_observed", "new_popups", "unexplained")}}

    def cmd_flow_discard_recording(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = self._flow_card(workflow.id, str(payload.get("card_id") or ""))
        sid, _sess = self._flow_session(card)
        is_variant = bool((card.get("refs") or {}).get("variant_of") or (card.get("refs") or {}).get("segment_from"))
        self.cmd_cancel_recording({"session_id": sid, "workflow_id": workflow.id}, rid)
        runtime_state.set_flags(workflow.id, recording=False, recording_paused=False, recording_discarded=not is_variant)
        harness.update_card(_thread(workflow.id), card["card_id"], state="discarded", actions=[], sink=sink)
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
