"""Test, auto-fix and approval, driven from a workflow's conversation (BUILD-40).

``cmd_flow_test_prepare`` posts the inputs card; ``cmd_flow_test_run`` builds a *test* package
(approval is not needed to test — testing comes first), runs ``cmd_test_workflow`` with the RT-9
guard and mirrors the runtime's own step events onto one ``test_run`` card. A test **never**
crosses a step that can't be undone without a person: the guard pauses before it ("Run the last N
steps for real" / "End the test here"), a confirm-only review is answered by the person, and an
engine that predates the guard is previewed (dry run) instead of run for real.

When a test fails and auto-fix is on, ``_tf_autofix`` makes **one gated change at a time** — the
same proposals the copilot makes, through the same gates, applied by the same command a reviewer's
Apply click uses, edit-logged ``source="auto_fix"``, listed with View change / Undo — and re-tests
with the guard always on, up to three attempts. Approval stays a separate human action.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from handlers.protocol import _CommandError, _event_sink, _safe_id

MAX_AUTOFIX_ATTEMPTS = 3
_DECISION_TIMEOUT_S = 170  # just under the runtime's 180 s park window

_waiters: dict[str, dict[str, Any]] = {}
_waiters_lock = threading.Lock()
_autofix_stop: dict[str, threading.Event] = {}


def _thread(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


def _humanize(intent: str) -> str:
    text = str(intent or "").replace("_", " ").strip()
    return (text[:1].upper() + text[1:]) if text else "A step"


class TestFlowMixin:
    __test__ = False  # not a pytest class, despite the name

    # ── inputs card ─────────────────────────────────────────────────────────────────────────

    def cmd_flow_test_prepare(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import clarify, clarify_store, harness, thread_store as ts
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.skill_id:
            raise _CommandError("workflow_not_compiled", "Compile this workflow before testing it.")
        doc = read_skill(workflow.skill_id) or {}
        sink = _event_sink(rid)
        existing = next((c for c in reversed(ts.fold(ts.read_raw(_thread(workflow_id))))
                         if c.get("card_type") == "test_inputs" and c.get("state") == "open"), None)
        if existing is not None:
            return {"ok": True, "card_id": existing["card_id"]}

        steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        recorded = {}
        for q in (clarify_store.load(workflow_id) or {}).get("questions") or []:
            if q.get("kind") in ("values", "chosen_values"):
                recorded = {**recorded, **(q.get("recorded") or {})}
        last = workflow.last_test_inputs or {}
        inputs = []
        for row in doc.get("inputs") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            rid_ = str(row["id"])
            value = last.get(rid_, recorded.get(rid_, row.get("default") or ""))
            inputs.append({
                "id": rid_, "label": _humanize(rid_), "type": row.get("type") or "text",
                "options": row.get("options") or [], "required": bool(row.get("required", True)),
                "value": value if isinstance(value, (str, int, float, bool)) else "",
            })
        irreversible = [clarify.step_name(s) for s in steps if isinstance(s, dict) and s.get("consequence") == "irreversible"]
        if not irreversible:
            # A step can also be destructive without a consequence label (older compiles).
            irreversible = [clarify.step_name(s) for s in steps if isinstance(s, dict)
                            and isinstance(s.get("identity_bundle"), dict) and s["identity_bundle"].get("destructive")]
        harness.emit_item(sink, _thread(workflow_id), ts.append(
            _thread(workflow_id), "assistant_message",
            text="Let’s test it. Fill in the inputs the way a real request would — I’ve put your recorded values in to start."))
        card = harness.post_card(
            _thread(workflow_id), "test_inputs", "open", sink=sink, inputs=inputs, irreversible=irreversible[-3:],
            irreversible_count=len(irreversible), guard=True, show_browser=True,
            version=int((doc.get("meta") or {}).get("version") or 1), step_count=len(steps),
            actions=["run"],
        )
        return {"ok": True, "card_id": card["card_id"]}

    def cmd_flow_test_run(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = ts.get_card(_thread(workflow.id), str(payload.get("card_id") or ""))
        if card is None or card.get("state") != "open":
            raise _CommandError("card_not_open", "This test was already started.")
        values = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        missing = [i["label"] for i in card.get("inputs") or []
                   if i.get("required") and str(values.get(i["id"], "")).strip() == ""]
        if missing:
            raise _CommandError("inputs_required", f"Fill in: {', '.join(missing)}.")
        if runtime_state.get_flags(workflow.id).get("testing"):
            raise _CommandError("test_in_progress", "A test of this workflow is already running.")
        guard = bool(payload.get("guard", True))
        show_browser = bool(payload.get("show_browser", True))
        harness.update_card(_thread(workflow.id), card["card_id"], sink=sink, state="submitted", actions=[],
                            guard=guard, show_browser=show_browser)
        threading.Thread(
            target=self._tf_background, args=(workflow.id, values, guard, show_browser), daemon=True,
        ).start()
        return {"ok": True}

    def _tf_background(self, workflow_id: str, inputs: dict[str, Any], guard: bool, show_browser: bool) -> None:
        try:
            outcome = self._tf_run_test(workflow_id, inputs, guard=guard, show_browser=show_browser, attempt=1, auto=False)
            if outcome == "failed":
                from conxa_compile.conversation import studio_settings

                if studio_settings.load().get("autofix_enabled"):
                    self._tf_autofix(workflow_id, inputs, show_browser)
        except Exception:  # noqa: BLE001 — never take the engine down from a background test
            import traceback

            traceback.print_exc()

    # ── one test run ────────────────────────────────────────────────────────────────────────

    def _tf_ensure_built(self, workflow: Any, rid: str) -> None:
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.workspace import LOCAL_WORKSPACE_ID

        pack = get_skill_pack(LOCAL_WORKSPACE_ID)
        stale = pack is None or pack.build is None or (
            workflow.edited_at is not None and workflow.edited_at > pack.build.last_built_at)
        if stale:
            self.cmd_build_skill_package({"skill_slug": workflow.slug, "for_test": True}, rid)

    def _tf_run_test(self, workflow_id: str, inputs: dict[str, Any], *, guard: bool, show_browser: bool,
                     attempt: int, auto: bool) -> str:
        """Run one test and mirror it onto a card. Returns passed | failed | stopped | ended | previewed."""
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts
        from conxa_core.storage.workflow_store import get_workflow, list_workflows  # noqa: F401

        workflow = get_workflow(workflow_id)
        thread = _thread(workflow_id)
        rid = f"test_{uuid.uuid4().hex[:10]}"
        sink = _event_sink(rid)
        rows: list[dict[str, Any]] = []
        started = time.time()
        card = harness.post_card(thread, "test_run", "running", sink=sink, rows=rows, attempt=attempt, auto=auto,
                                 guard=guard, started_at=started, actions=["stop"])
        card_id = card["card_id"]
        runtime_state.set_flags(workflow_id, testing=True, autofix_attempt=attempt if auto else 0, recording_discarded=False,
                                sign_in_expired=False)
        harness.log_event(thread, "Test started", glyph="run", sink=sink, subject=f"attempt {attempt}" if auto else None)
        released = {"v": False}

        def push(**extra: Any) -> None:
            harness.update_card(thread, card_id, sink=sink, rows=[dict(r) for r in rows], **extra)

        def phase_hook(entry: dict[str, Any]) -> None:
            if entry.get("step") is None or not str(entry.get("phase", "")).endswith("_start"):
                return
            for r in rows:
                if r["status"] == "running":
                    r["status"] = "done"
            parts = str(entry.get("phase", "")).split("_")  # step_<i>_<type>_start
            kind = parts[2] if len(parts) >= 4 else "step"
            rows.append({"index": int(entry["step"]), "name": _humanize(entry.get("intent") or kind),
                         "status": "running", "destructive": bool(entry.get("destructive")),
                         "step_key": entry.get("source_step_key") or ""})
            push(step_total=entry.get("step_total"))

        def wait_decision(kind: str) -> str:
            ev = threading.Event()
            with _waiters_lock:
                _waiters[workflow_id] = {"event": ev, "decision": None, "kind": kind}
            ev.wait(_DECISION_TIMEOUT_S)
            with _waiters_lock:
                decision = (_waiters.pop(workflow_id, {}) or {}).get("decision")
            return decision or "end_here"

        def settle() -> None:
            """A step is only known to have finished when the next one starts or the run pauses."""
            for r in rows:
                if r["status"] == "running":
                    r["status"] = "done"

        def guard_hook(info: dict[str, Any]) -> str:
            settle()
            ran = sum(1 for r in rows if r["status"] == "done")
            push(state="waiting_guard", paused_before=_humanize(info.get("step_intent")), remaining=info.get("remaining"),
                 ran=ran, actions=["run_rest", "end_here"])
            harness.log_event(thread, "Paused before a step that can’t be undone", glyph="warn", sink=sink,
                              subject=_humanize(info.get("step_intent")))
            decision = wait_decision("guard")
            if decision == "run_rest":
                released["v"] = True
                push(state="running", released=True, actions=["stop"])
            return decision

        def review_hook(review: dict[str, Any]) -> bool:
            settle()
            push(state="waiting_confirm", prompt=str(review.get("prompt") or ""), actions=["yes", "end_here"])
            decision = wait_decision("confirm")
            if decision in ("yes", "run_rest"):
                released["v"] = True
                push(state="running", released=True, actions=["stop"])
                return True
            return False

        def fork_hook(fork: dict[str, Any]) -> str:
            """A popup that needs a decision: show the choices and wait for the person. Never answered
            by a model. Returns a choice id, or "__stop__" when they end the test."""
            settle()
            push(state="waiting_fork", fork={"title": fork.get("title"), "message": fork.get("message"),
                                             "choices": fork.get("choices"), "suggested": fork.get("suggested"),
                                             "unknown": fork.get("unknown")},
                 actions=["fork_choose", "end_here"])
            harness.log_event(thread, "Paused — a popup needs your decision", glyph="warn", sink=sink,
                              subject=str(fork.get("title") or "a popup"))
            decision = wait_decision("fork")
            if decision.startswith("fork:"):
                push(state="running", fork=None, actions=["stop"])
                return decision[len("fork:"):]
            return "__stop__"

        try:
            self._tf_ensure_built(workflow, rid)
            result = self.cmd_test_workflow(
                {"workflow_id": workflow_id, "inputs": inputs, "headless": not show_browser, "guard": guard,
                 "_phase_hook": phase_hook, "_guard_hook": guard_hook, "_review_hook": review_hook,
                 "_fork_hook": fork_hook}, rid)
        except _CommandError as exc:
            return self._tf_failed(workflow_id, card_id, rows, exc.message or exc.code, sink, started, inputs)
        except Exception as exc:  # noqa: BLE001
            return self._tf_failed(workflow_id, card_id, rows, str(exc), sink, started, inputs)
        finally:
            runtime_state.set_flags(workflow_id, testing=False, autofix_attempt=0)

        status = result.get("status")
        for r in rows:
            if r["status"] == "running":
                r["status"] = "done"
        elapsed = int(time.time() - started)
        if status == "passed":
            ran_destructive = any(r.get("destructive") and r["status"] == "done" for r in rows)
            push(state="passed", seconds=elapsed, steps_ran=len(rows), irreversible_ran=ran_destructive,
                 outputs=result.get("outputs") or {}, actions=["approve", "test_again"])
            harness.log_event(thread, "Test passed", glyph="ok", sink=sink,
                              subject=f"{len(rows)} steps · {elapsed // 60} min {elapsed % 60:02d} s")
            harness.emit_item(sink, thread, ts.append(
                thread, "assistant_message",
                text="It works. Approve it when you’re happy — that adds it to the next skill package release. "
                     "Approving doesn’t publish anything on its own."))
            return "passed"
        if status == "needs_sign_in":
            runtime_state.set_flags(workflow_id, sign_in_expired=True)
            push(state="needs_sign_in", steps_ran=len(rows), seconds=elapsed, detail=str(result.get("message") or "")[:300],
                 actions=["test_again"])
            harness.log_event(thread, "Test paused — sign-in expired", glyph="warn", sink=sink,
                              subject="Nothing failed in the workflow")
            return "needs_sign_in"
        if status == "previewed":
            push(state="previewed", seconds=elapsed, actions=["test_again"])
            harness.log_event(thread, "Preview finished", glyph="warn", sink=sink,
                              subject="Steps that can’t be undone were checked, not run")
            return "previewed"
        if status == "stopped_at_guard":
            ran = [r["name"] for r in rows if r["status"] == "done"]
            push(state="ended_before_irreversible", stopped_before=_humanize(result.get("stopped_intent") or ""),
                 declined=bool(result.get("declined")), steps_ran=len(rows), actions=["test_again"])
            harness.log_event(thread, "Test ended before a step that can’t be undone", glyph="none", sink=sink,
                              subject="Nothing was sent")
            return "ended"
        # cancelled by the person
        push(state="stopped", steps_ran=len(rows), actions=["test_again"], last_step=rows[-1]["name"] if rows else "")
        harness.log_event(thread, "Test stopped by you", glyph="none", sink=sink)
        return "stopped"

    def _tf_failure_image(self, workflow_id: str) -> str | None:
        """``conxa-asset://`` address of the failure screenshot the runtime kept for the latest test, or
        None. Only the run id and file name leave the sandbox path; the renderer cannot ask for anything else."""
        try:
            from pathlib import Path

            from conxa_compile.editor.evidence import build_evidence_bundle
            from conxa_core.storage.workflow_store import get_workflow

            wf = get_workflow(workflow_id)
            if wf is None or not wf.skill_id or not wf.last_test_run_id:
                return None
            shot = (build_evidence_bundle(wf.skill_id, run_id=wf.last_test_run_id, detail="digest").get("runtime_evidence") or {}).get("failure_screenshot_path")
            parts = Path(str(shot or "")).parts
            if "_evidence" not in parts:
                return None
            i = parts.index("_evidence")
            return f"conxa-asset://local/sandbox-evidence/{parts[i - 1]}/_evidence/{parts[-1]}" if i >= 1 else None
        except Exception:  # noqa: BLE001 -- a picture is a bonus; a failed test must still report
            return None

    def _tf_followup(self, workflow_id: str, step_key: str, inputs: dict[str, Any], sink: Any) -> None:
        """A failed test where the person's test value differs from the recorded one becomes a
        Clarify question. Never raises: a missing question must not hide the failure."""
        try:
            from conxa_compile.conversation import clarify, clarify_store
            from conxa_core.storage.json_store import read_skill
            from conxa_core.storage.workflow_store import get_workflow

            workflow = get_workflow(workflow_id)
            doc = read_skill(workflow.skill_id) if workflow and workflow.skill_id else None
            q = clarify.test_followup_question(doc, step_key, inputs) if doc else None
            if q is None:
                return
            data = clarify_store.load(workflow_id) or {"skill_id": workflow.skill_id, "questions": []}
            if any(x.get("id") == q["id"] for x in data.get("questions") or []):
                return  # asked (or answered) already
            data["questions"] = [*(data.get("questions") or []), q]
            clarify_store.save(workflow_id, data)
            self._flow_post_next_question(workflow_id, sink)
        except Exception:  # noqa: BLE001
            pass

    def _tf_failed(self, workflow_id: str, card_id: str, rows: list[dict[str, Any]], message: str, sink: Any, started: float,
                   inputs: dict[str, Any] | None = None) -> str:
        from conxa_compile.conversation import harness

        failing = next((r for r in reversed(rows) if r["status"] == "running"), None)
        if failing is not None:
            failing["status"] = "failed"
        # Only claim "nothing was sent" when no step that can't be undone even started: a failure
        # AT such a step may have half-happened.
        nothing_sent = not any(r.get("destructive") for r in rows)
        harness.update_card(
            _thread(workflow_id), card_id, sink=sink, state="failed", rows=[dict(r) for r in rows], error=message[:600],
            failed_step=failing["name"] if failing else "", nothing_sent=nothing_sent, seconds=int(time.time() - started),
            failure_image=self._tf_failure_image(workflow_id), actions=[],
        )
        harness.log_event(_thread(workflow_id), "Test failed", glyph="fail", sink=sink,
                          subject=(failing["name"] if failing else message[:80]))
        if failing is not None and failing.get("step_key"):
            self._tf_followup(workflow_id, failing["step_key"], inputs or {}, sink)
        return "failed"

    # ── decisions, stop, approve ────────────────────────────────────────────────────────────

    def cmd_flow_test_decision(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """The person's answer while a test is paused: run_rest / yes / end_here, or fork:<choice id>
        for a popup decision."""
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        decision = str(payload.get("decision") or "")
        if decision not in ("run_rest", "yes", "end_here") and not (decision.startswith("fork:") and len(decision) > 5):
            raise _CommandError("invalid_input", "decision must be run_rest, yes, end_here or fork:<choice>")
        with _waiters_lock:
            w = _waiters.get(workflow_id)
            if w is None:
                raise _CommandError("not_waiting", "This test isn’t waiting for you any more.")
            w["decision"] = decision
            w["event"].set()
        return {"ok": True}

    def cmd_flow_test_stop(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        with _waiters_lock:
            w = _waiters.get(workflow_id)
            if w is not None:
                w["decision"] = "end_here"
                w["event"].set()
        stop = _autofix_stop.get(workflow_id)
        if stop is not None:
            stop.set()
        self.cmd_cancel_test_workflow({"workflow_id": workflow_id}, rid)
        return {"ok": True}

    def cmd_flow_approve(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Approve for packaging. A separate human action after a pass; never publishes."""
        from conxa_compile.conversation import harness
        from conxa_core.storage.workflow_store import get_workflow

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card_id = str(payload.get("card_id") or "")
        card = self._flow_card(workflow.id, card_id)
        if card.get("state") != "passed":
            raise _CommandError("not_passed", "Only a passing test can be approved.")
        if workflow.last_test_status != "passed":
            raise _CommandError("test_required", "The workflow changed since that test. Test it again, then approve.")
        result = self.cmd_sign_off_workflow({"skill_id": workflow.skill_id}, rid)
        if not result.get("signed_off"):
            raise _CommandError("approve_failed", "Couldn’t approve this workflow.")
        version = None
        try:
            from conxa_core.storage.json_store import read_skill

            version = int(((read_skill(workflow.skill_id) or {}).get("meta") or {}).get("version") or 0) or None
        except Exception:  # noqa: BLE001
            pass
        harness.update_card(_thread(workflow.id), card_id, sink=sink, approved=True, approved_version=version,
                            actions=["test_again"], build_error=result.get("build_error"))
        harness.log_event(_thread(workflow.id), "Approved for packaging", glyph="ok", sink=sink,
                          subject=f"version {version}" if version else None)
        from conxa_compile.conversation import thread_store as ts

        from conxa_core.storage.workflow_store import list_workflows

        n = sum(1 for w in list_workflows() if w.signed_off)
        harness.emit_item(sink, _thread(workflow.id), ts.append(
            _thread(workflow.id), "assistant_message",
            text=f"Approved. You now have {n} approved workflow{'s' if n != 1 else ''} waiting in the next release. "
                 "Publish when the whole group is ready."))
        return {"ok": True, "approved": True, "built": bool(result.get("built"))}

    # ── settings ────────────────────────────────────────────────────────────────────────────

    def cmd_get_studio_settings(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import studio_settings

        return {"settings": studio_settings.load()}

    def cmd_set_studio_settings(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import studio_settings

        return {"settings": studio_settings.update(**{k: v for k, v in payload.items() if k != "type"})}

    # ── auto-fix ────────────────────────────────────────────────────────────────────────────

    def cmd_flow_autofix_stop(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        return self.cmd_flow_test_stop(payload, rid)

    def cmd_flow_autofix_resume(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        workflow = self._flow_workflow(payload)
        inputs = dict(workflow.last_test_inputs or {})
        threading.Thread(target=self._tf_autofix, args=(workflow.id, inputs, True), daemon=True).start()
        return {"ok": True}

    def _tf_propose_fix(self, workflow_id: str) -> list[dict[str, Any]]:
        """One gated fix proposal for the latest failure — the copilot's own call and gates."""
        from conxa_compile.conversation.context_pack import build_context_pack
        from conxa_compile.editor.evidence import EvidenceError, build_evidence_bundle
        from conxa_compile.llm.copilot import _capability_manifest_text, copilot_turn
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.skill_id:
            return []
        try:
            digest = build_evidence_bundle(workflow.skill_id, run_id=workflow.last_test_run_id, detail="digest")
        except EvidenceError:
            return []
        message = ("The last test failed. Diagnose the failing step from the evidence and propose at most ONE change "
                   "that is within the allowed change types and that you are confident fixes it. Propose nothing if unsure.")
        pack = build_context_pack(message=message, budget_tokens=12_000, digest=digest,
                                  failure={"last_test": digest.get("last_test"), "runtime_evidence": digest.get("runtime_evidence")})
        user_text = ("Capability manifest — what you may propose and what the runtime does with each step kind:\n"
                     f"{_capability_manifest_text()}\n\n{pack.text}")
        # The page as it was when the step failed: a vision model reads "the button moved" off the screenshot
        # that a text description of the failure can only hint at. None when no failure image was kept.
        shot = (digest.get("runtime_evidence") or {}).get("failure_screenshot_path")
        result = copilot_turn(skill_id=workflow.skill_id, evidence=digest, transcript=[], message=message,
                              on_delta=None, user_text=user_text, screenshot_path=shot)
        ctx = self._thread_context("wf", workflow_id, None)
        return ctx["gate_proposals"]([p for p in result.get("proposals") or [] if isinstance(p, dict)])

    def _tf_autofix(self, workflow_id: str, inputs: dict[str, Any], show_browser: bool) -> None:
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        thread = _thread(workflow_id)
        rid = f"autofix_{uuid.uuid4().hex[:10]}"
        sink = _event_sink(rid)
        stop = _autofix_stop.setdefault(workflow_id, threading.Event())
        stop.clear()
        card = harness.post_card(thread, "autofix", "running", sink=sink, attempt=1, max_attempts=MAX_AUTOFIX_ATTEMPTS,
                                 changes=[], phase="Finding the cause", actions=["stop"])
        card_id = card["card_id"]
        harness.emit_item(sink, thread, ts.append(
            thread, "assistant_message",
            text="I’ll fix this and test again on my own, up to 3 attempts — then I stop and ask you. Re-tests always pause "
                 "before the steps that can’t be undone, so nothing real is sent without your OK. Every change shows up "
                 "here, and you can undo it."))
        changes: list[dict[str, Any]] = []
        self._install_proxy_router(sink=sink, usage_class="ai_credits")

        def stop_with(reason: str) -> None:
            runtime_state.set_flags(workflow_id, autofix_attempt=0)
            harness.update_card(thread, card_id, sink=sink, state="stopped", reason=reason, changes=changes,
                                actions=["resume", "human_edit"])
            harness.log_event(thread, "Auto-fix stopped", glyph="warn", sink=sink, subject=reason)

        try:
            for attempt in range(1, MAX_AUTOFIX_ATTEMPTS + 1):
                runtime_state.set_flags(workflow_id, autofix_attempt=attempt)
                harness.update_card(thread, card_id, sink=sink, attempt=attempt, phase="Finding the cause", changes=changes)
                if stop.is_set():
                    return stop_with("You pressed Stop.")
                try:
                    proposals = self._tf_propose_fix(workflow_id)
                except Exception as exc:  # noqa: BLE001 — entitlement/quota/cloud errors end the loop cleanly
                    return stop_with(str(exc)[:160] or "I couldn’t reach the AI service.")
                if not proposals:
                    return stop_with("I couldn’t find a fix I’m sure about within the safe edit types.")
                if stop.is_set():  # pressed while it was thinking: nothing may be applied after Stop
                    return stop_with("You pressed Stop.")
                proposal = proposals[0]
                change = harness.post_card(
                    thread, "pending_change", "open", sink=sink,
                    refs={"skill_id": self._flow_workflow({"workflow_id": workflow_id}).skill_id, "proposal_id": proposal.get("id"),
                          "step_key": proposal.get("step_key")},
                    title=_change_title(proposal), why=proposal.get("why") or "", proposal=proposal, preview=proposal.get("preview"),
                    actions=["apply", "dismiss"], auto=True,
                )
                applied = self._card_pending_change(thread, change, "apply", {"source": "auto_fix"}, rid, sink)
                if not applied.get("ok"):
                    return stop_with(applied.get("message") or "That change couldn’t be applied.")
                changes.append({"card_id": change["card_id"], "title": _change_title(proposal)})
                harness.update_card(thread, card_id, sink=sink, phase="Re-testing", changes=changes)
                if stop.is_set():
                    return stop_with("You pressed Stop.")
                outcome = self._tf_run_test(workflow_id, inputs, guard=True, show_browser=show_browser,
                                            attempt=attempt + 1, auto=True)
                if outcome == "passed":
                    runtime_state.set_flags(workflow_id, autofix_attempt=0)
                    harness.update_card(thread, card_id, sink=sink, state="passed", attempts=attempt + 1, changes=changes, actions=[])
                    return
                if outcome in ("stopped", "ended", "previewed", "needs_sign_in"):
                    return stop_with("The re-test didn’t run to the end.")
            stop_with("Three attempts didn’t fix it.")
        finally:
            runtime_state.set_flags(workflow_id, autofix_attempt=0)


def _change_title(proposal: dict[str, Any]) -> str:
    if proposal.get("command") == "insert_overlay_branch":
        return "Add a step to dismiss a pop-up"
    if proposal.get("command") == "structural_op":
        return "Change the steps"
    return f"Change “{proposal.get('field') or 'a step'}”"
