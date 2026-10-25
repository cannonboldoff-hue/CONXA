"""Compile, Clarify and the workflow summary, driven from a workflow's conversation (BUILD-39 / BUILD-32).

``cmd_flow_compile`` wraps the existing ``cmd_compile``: it waits its turn in the single-slot
compile queue, mirrors the compiler's *own* events onto one ``compile`` card (real stages, no
estimated progress), maps the failure modes to the cards in the ``States`` spec (queued, out of
credits, failed), and on success asks Clarify's questions one at a time.

Every answer becomes an existing command — ``cmd_patch_step`` (gated), ``cmd_insert_step``,
``cmd_confirm_optional_interstitial``, ``cmd_update_workflow_inputs`` — never a new editing path
(see ``conversation/clarify.py``). A card only shows an answer as applied after the commands behind
it have returned.
"""

from __future__ import annotations

import datetime
import threading
import time
import uuid
from typing import Any

from handlers.protocol import JOB_OBSERVERS, _CommandError, _event_sink, _safe_id

_THROTTLE_S = 0.5
# An "applying" answer older than this is taken to belong to an attempt that died with the app.
_APPLY_STALE_S = 60.0


def _thread(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


class CompileFlowMixin:
    # ── compile ─────────────────────────────────────────────────────────────────────────────

    def _flow_start_compile(self, workflow_id: str) -> None:
        """Compile in the background so the command that asked (finishing a recording) returns at once."""
        rid = f"compile_{uuid.uuid4().hex[:10]}"
        threading.Thread(target=self._flow_compile_safe, args=(workflow_id, rid), daemon=True).start()

    def _flow_compile_safe(self, workflow_id: str, rid: str) -> None:
        try:
            self._flow_compile(workflow_id, rid)
        except Exception:  # noqa: BLE001 — a background compile must never take the engine down
            import traceback

            traceback.print_exc()

    def cmd_flow_compile(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Compile (or retry) this workflow's recording. Returns when the compile ends."""
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        return self._flow_compile(workflow_id, rid)

    def _flow_compile(self, workflow_id: str, rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import clarify_store, compile_queue, harness, runtime_state
        from conxa_compile.conversation.compile_progress import CompileProgress
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", "No such workflow.")
        if not workflow.session_id:
            raise _CommandError("not_recorded", "Record this workflow before compiling it.")
        sink = _event_sink(rid)
        thread = _thread(workflow_id)
        progress = CompileProgress()
        card = harness.post_card(thread, "compile", "queued", sink=sink, rows=progress.rows(), credits_left=None,
                                 detail="", actions=[])
        card_id = card["card_id"]

        def on_position(ahead: int, running_id: str | None) -> None:
            if ahead <= 0:
                return
            other = get_workflow(running_id) if running_id else None
            runtime_state.set_flags(workflow_id, queued=ahead)
            harness.update_card(thread, card_id, sink=sink, state="queued", position=ahead,
                                ahead_name=other.name if other else "another workflow", ahead_workflow_id=running_id)

        try:
            compile_queue.acquire(workflow_id, on_position)
        except compile_queue.WaitCancelled:
            return {"ok": False, "cancelled": True}
        runtime_state.set_flags(workflow_id, queued=0, compiling=True, compile_error="", out_of_credits=False,
                                recording_discarded=False)
        harness.update_card(thread, card_id, sink=sink, state="running", rows=progress.rows(), position=0)

        last = {"t": 0.0, "sig": ""}

        def observe(ev: dict[str, Any]) -> None:
            if not progress.observe(ev):
                return
            sig = "|".join(f"{r['status']}:{r['detail']}" for r in progress.rows() if r["key"] != "find") + f":{progress.rows()[1]['status']}"
            now = time.monotonic()
            if sig == last["sig"] and now - last["t"] < _THROTTLE_S:
                return  # a step counter ticking: don't write a revision for every single step
            last["t"], last["sig"] = now, sig
            harness.update_card(thread, card_id, sink=sink, rows=progress.rows(), credits_left=progress.credits_left)

        JOB_OBSERVERS[rid] = observe
        try:
            result = self.cmd_compile({"session_id": workflow.session_id, "workflow_id": workflow_id}, rid)
            # Raised before `compiling` clears below, so there is no moment where the workflow is
            # compiled but its questions don't exist yet and the lifecycle would offer a test.
            runtime_state.set_flags(workflow_id, understanding=True)
            # The flag above lives in memory and dies with the app. This marker survives a restart, so
            # a workflow whose questions were never written can be told apart from an old one that
            # simply has none (see ConversationMixin._reconcile_interrupted).
            clarify_store.save(workflow_id, {"skill_id": result.get("skill_id"), "version": result.get("version"),
                                             "building": True, "questions": []})
        except _CommandError as exc:
            return self._flow_compile_failed(workflow_id, card_id, progress, exc, sink)
        except Exception as exc:  # noqa: BLE001
            return self._flow_compile_failed(workflow_id, card_id, progress, _CommandError("internal_error", str(exc)), sink)
        finally:
            JOB_OBSERVERS.pop(rid, None)
            compile_queue.release(workflow_id)
            runtime_state.set_flags(workflow_id, compiling=False, queued=0)

        try:
            progress.observe({"phase": "compile_done", "step_count": result.get("step_count")})
            harness.update_card(thread, card_id, sink=sink, state="done", rows=progress.rows(),
                                credits_left=progress.credits_left, step_count=result.get("step_count"), actions=[])
            warnings = int(result.get("compile_steps_with_warnings") or 0)
            self._flow_after_compile(workflow_id, result, sink)
        except Exception:  # noqa: BLE001 — the compile itself succeeded; only the questions failed
            import traceback

            traceback.print_exc()
            # Clear first, then post: the renderer re-reads the lifecycle on every thread item, and
            # this item is the only thing that tells it "Checking…" is over.
            runtime_state.set_flags(workflow_id, understanding=False)
            clarify_store.clear(workflow_id)  # no marker left behind, so opening it later doesn't retry and repeat this
            harness.log_event(thread, "Couldn’t prepare the questions", glyph="warn", sink=sink,
                              subject="The workflow compiled; you can still test it")
        finally:
            runtime_state.set_flags(workflow_id, understanding=False)
        return {"ok": True, **result}

    def _flow_rebuild_questions(self, workflow_id: str, result: dict[str, Any], sink: Any) -> None:
        """Write the questions for a compile whose check was cut short (the app closed before
        ``clarify.json`` was written). No model call: the description check is stored with the compile."""
        from conxa_compile.conversation import clarify_store, harness, runtime_state

        runtime_state.set_flags(workflow_id, understanding=True)
        try:
            self._flow_after_compile(workflow_id, result, sink)
        except Exception:  # noqa: BLE001 -- same promise as the compile: the workflow stays testable
            import traceback

            traceback.print_exc()
            runtime_state.set_flags(workflow_id, understanding=False)
            clarify_store.clear(workflow_id)
            harness.log_event(_thread(workflow_id), "Couldn’t prepare the questions", glyph="warn", sink=sink,
                              subject="The workflow compiled; you can still test it")
        finally:
            runtime_state.set_flags(workflow_id, understanding=False)

    def _flow_compile_failed(self, workflow_id: str, card_id: str, progress: Any, exc: _CommandError, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state

        thread = _thread(workflow_id)
        if exc.code == "compile_credit_limit_exceeded":
            runtime_state.set_flags(workflow_id, out_of_credits=True)
            harness.update_card(thread, card_id, sink=sink, state="out_of_credits", rows=progress.rows(),
                                actions=["add_credits", "compile_later"], error=exc.message)
            return {"ok": False, "code": exc.code}
        runtime_state.set_flags(workflow_id, compile_error=exc.message or exc.code)
        # Only an infrastructure failure is refunded (handlers/compile.py); say "no credit was used" only then.
        refunded = exc.code in ("cloud_unreachable", "llm_no_output", "vision_llm_request_failed")
        harness.update_card(thread, card_id, sink=sink, state="failed", rows=progress.rows(), error=exc.message,
                            failed_stage=progress.failed_stage, refunded=refunded, actions=["retry", "details"])
        return {"ok": False, "code": exc.code, "message": exc.message}

    # ── after a compile: questions, then the summary ────────────────────────────────────────

    def _flow_after_compile(self, workflow_id: str, result: dict[str, Any], sink: Any) -> None:
        from conxa_compile.conversation import clarify, clarify_store, thread_store as ts
        from conxa_compile.editor.retarget import find_source_event
        from conxa_compile.recorder import hints
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.session_events import read_session_events
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        doc = read_skill(result["skill_id"]) if result.get("skill_id") else None
        if workflow is None or doc is None:
            return
        steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        try:
            recorded_actions = len(read_session_events(workflow.session_id))
        except Exception:  # noqa: BLE001
            recorded_actions = 0
        report = doc.get("compile_report") or {}
        uncertain_names = [
            clarify.step_name(steps[i]) for i, r in enumerate(report.get("steps") or [])
            if isinstance(r, dict) and r.get("warnings") and i < len(steps)
        ][:2]
        try:
            from conxa_compile.app_graph.hints import known_popups
            from conxa_core.workspace import LOCAL_WORKSPACE_ID

            known = known_popups(doc, skill_id=result["skill_id"], workspace_id=workflow.workspace_id or LOCAL_WORKSPACE_ID)
        except Exception:  # noqa: BLE001 -- a hint, never worth failing the compile flow
            known = []
        # Does the compiled workflow match what the person said, and which recorded values that
        # aren't typed text were only true of this recording? The compile's own review call already
        # answered that (stored as compile_report["understanding"]); no model call is made here.
        # Never raises, never blocks.
        from conxa_compile.conversation import understanding as understanding_mod

        understood = understanding_mod.understand(doc, workflow.task_description, review=report.get("understanding"))
        questions = clarify.generate_questions(
            doc,
            source_lookup=lambda s: find_source_event(s, doc),
            answered_by_recording=hints.load(workflow.session_id).get("varies") or {},
            known_popups=known,
            understanding=understood,
            dropped=report.get("dropped") or [],
            today=datetime.date.today(),
            limit=None,
        )
        # Ask the most important few now; keep the rest instead of silently dropping them.
        for n, q in enumerate(questions):
            if n >= clarify.MAX_QUESTIONS:
                q["status"] = "queued"
        # The questions go out in their plain template wording; a friendlier version follows in the
        # background (_flow_reword), so no question ever waits on a model.
        clarify_store.save(workflow_id, {
            "skill_id": result["skill_id"], "version": result.get("version"), "recorded_actions": recorded_actions,
            "added_steps": 0, "step_count_at_compile": len(steps), "questions": questions,
            "understanding": {k: understood[k] for k in ("goal_summary", "coverage", "source")},
        })
        # Questions now exist, so the lifecycle can see them; clear before the posts below, because
        # the renderer re-reads the lifecycle on each one (the caller's `finally` is only the net).
        from conxa_compile.conversation import runtime_state

        runtime_state.set_flags(workflow_id, understanding=False)
        lines =[f"Compiled into {len(steps)} steps."]
        dropped = report.get("dropped") or []
        if dropped:
            kinds = sorted({str(d.get("action") or "action").replace("_", " ") for d in dropped})[:3]
            lines.append(
                f"{len(dropped)} recorded action{'s were' if len(dropped) != 1 else ' was'} folded away while tidying up "
                f"({', '.join(kinds)}) — repeats, or page loads a click caused. They’re listed in the compile report.")
        if uncertain_names:
            quoted = " and ".join(f"“{n}”" for n in uncertain_names)
            lines.append(f"I’m less sure how to find the element in {quoted} — they’re marked in Human Edit.")
        if questions:
            lines.append("A few questions first, so the test reflects how you’ll really use it.")
        elif understood["source"] == "model":
            lines.append("I checked it against what you described and nothing needs deciding.")
        else:
            lines.append("Nothing here looked uncertain" + (" — though I couldn’t check it against your description." if workflow.task_description.strip() else ", and there was no description to check it against."))
        harness_say = ts.append(_thread(workflow_id), "assistant_message", text=" ".join(lines))
        from conxa_compile.conversation import harness

        harness.emit_item(sink, _thread(workflow_id), harness_say)
        if questions:
            self._flow_post_next_question(workflow_id, sink)
            threading.Thread(target=self._flow_reword_safe, args=(workflow_id, sink), daemon=True).start()
        else:
            self._flow_post_summary(workflow_id, sink)

    def _flow_reword_safe(self, workflow_id: str, sink: Any) -> None:
        try:
            self._flow_reword(workflow_id, sink)
        except Exception:  # noqa: BLE001 -- wording only; the template questions already stand
            import traceback

            traceback.print_exc()

    def _flow_reword(self, workflow_id: str, sink: Any) -> None:
        """One cached model call rewords every question that is still unanswered; the answer is
        validated field by field (``clarify.apply_reworded``) and the card already on screen is
        revised in place. A question answered while the call was running is left exactly as it was."""
        import json

        from conxa_compile.conversation import clarify, clarify_store, harness, thread_store as ts
        from conxa_compile.llm.clarify_question_copy import reword_questions

        pending = [q for q in (clarify_store.load(workflow_id) or {}).get("questions") or []
                   if isinstance(q, dict) and q.get("status") in ("open", "queued")]
        reworded = reword_questions(pending) if pending else None
        if reworded is None:
            return
        changed: list[dict[str, Any]] = []

        def edit(data: dict[str, Any]) -> None:
            live = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("status") in ("open", "queued")]
            before = {q["id"]: json.dumps([q["title"], q["body"], q["choices"]], sort_keys=True) for q in live}
            clarify.apply_reworded(live, reworded)
            changed.extend(dict(q) for q in live if json.dumps([q["title"], q["body"], q["choices"]], sort_keys=True) != before[q["id"]])

        clarify_store.mutate(workflow_id, edit)
        thread = _thread(workflow_id)
        for q in changed:
            card = ts.get_card(thread, q["card_id"]) if q.get("card_id") else None
            if card is None or card.get("state") != "open":
                continue  # not on screen yet, or answered meanwhile: the stored wording is enough
            fields: dict[str, Any] = {"title": q["title"], "body": q["body"]}
            if not q.get("multi"):
                fields["choices"] = q["choices"]  # a tick-list's labels never change, so its ticks are never reset
            harness.update_card(thread, q["card_id"], sink=sink, **fields)

    def _flow_post_next_question(self, workflow_id: str, sink: Any, *, promote_queued: bool = False) -> None:
        from conxa_compile.conversation import clarify, clarify_store, harness

        data = clarify_store.load(workflow_id) or {}
        questions = [q for q in data.get("questions") or [] if isinstance(q, dict)]
        q = next((x for x in questions if x.get("status") == "open"), None)
        if q is None and promote_queued:
            # The person asked for the rest: bring the next few queued questions forward.
            for x in [x for x in questions if x.get("status") == "queued"][: clarify.MAX_QUESTIONS]:
                x["status"] = "open"
            clarify_store.save(workflow_id, {**data, "questions": questions})
            q = next((x for x in questions if x.get("status") == "open"), None)
        if q is None:
            self._flow_post_summary(workflow_id, sink)
            return
        if q.get("kind") == "done_when":
            # Its labels were written at compile time; earlier answers may have turned a value into an input since.
            from conxa_core.storage.json_store import read_skill
            from conxa_core.storage.workflow_store import get_workflow

            wf = get_workflow(workflow_id)
            doc = read_skill(wf.skill_id) if wf and wf.skill_id else None
            if doc is not None:
                fresh = clarify.refresh_question(q, doc)
                q.update(choices=fresh["choices"], step_keys=fresh["step_keys"])
                clarify_store.update_question(workflow_id, q["id"], choices=q["choices"], step_keys=q["step_keys"])
        asked = [x for x in questions if x.get("status") != "queued"]
        number = asked.index(q) + 1
        card = harness.post_card(
            _thread(workflow_id), "clarify_question", "open", sink=sink, refs={"question_id": q["id"]},
            question_id=q["id"], question_kind=q["kind"], title=q["title"], body=q["body"], choices=q["choices"],
            multi=bool(q.get("multi")), index=number, total=len(asked), step_keys=q.get("step_keys") or [],
            actions=["choose", "skip"],
        )
        clarify_store.update_question(workflow_id, q["id"], card_id=card["card_id"])

    def _flow_post_summary(self, workflow_id: str, sink: Any) -> None:
        from conxa_compile.conversation import clarify, clarify_store, harness, thread_store as ts
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        data = clarify_store.load(workflow_id) or {}
        doc = read_skill(workflow.skill_id) if workflow and workflow.skill_id else None
        if workflow is None or doc is None:
            return
        added = int(data.get("added_steps") or 0)
        summary = clarify.build_summary(
            doc, recorded_actions=int(data.get("recorded_actions") or 0), added_steps=added, name=workflow.name,
            uncertain=int(workflow.compile_steps_with_warnings or 0),
        )
        # What the understanding check took the task to be, and which values a run will be asked for.
        summary["goal"] = str((data.get("understanding") or {}).get("goal_summary") or "")
        summary["input_names"] = [str(i.get("id")) for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("id")]
        summary["queued"] = sum(1 for q in data.get("questions") or [] if q.get("status") == "queued")
        questions = data.get("questions") or []
        answered = [q for q in questions if q.get("status") == "answered"]
        if questions:
            text = "That’s everything."
            if added:
                text += f" Your answers added {added} step{'s' if added != 1 else ''}."
            text += f" Here’s the workflow as it stands, saved as version {summary['version']}."
            if summary["queued"]:
                text += f" I held back {summary['queued']} smaller question{'s' if summary['queued'] != 1 else ''} — say “ask me” whenever you want them."
        else:
            text = f"Here’s the workflow, saved as version {summary['version']}."
        harness.emit_item(sink, _thread(workflow_id), ts.append(_thread(workflow_id), "assistant_message", text=text))
        self._flow_offer_variants(workflow_id, doc, sink)
        harness.post_card(
            _thread(workflow_id), "workflow_summary", "open", sink=sink, summary=summary,
            answered=len(answered), actions=["test", "review"] if summary["uncertain"] else ["test"],
        )

    def _flow_offer_variants(self, workflow_id: str, doc: dict[str, Any], sink: Any) -> None:
        """For each popup decision whose "no popup" case was never seen, offer to record that case.
        Optional and skippable: the workflow already handles an absent popup safely (it just
        carries on), the second take only turns that assumption into something observed."""
        from conxa_compile.compiler.step_key import step_keys
        from conxa_compile.conversation import clarify, harness

        steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        keys = step_keys(steps)
        for key, step in zip(keys, steps):
            if clarify._action_name(step) != "outcome_fork":
                continue
            fork = step.get("outcome_fork") or {}
            if (fork.get("clean") or {}).get("observed"):
                continue
            states = fork.get("states") or []
            title = ((states[0].get("signature") or {}).get("title") if states else "") or "the popup"
            harness.post_card(
                _thread(workflow_id), "fork_variant", "open", sink=sink, refs={"fork_step_key": key},
                title=title, actions=["record", "skip"],
            )
            return  # one offer at a time keeps the conversation calm

    def _card_fork_variant(self, thread_id: str, card: dict[str, Any], action: str,
                           body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness
        from conxa_core.storage.workflow_store import get_workflow

        workflow_id = thread_id.replace("wf:", "", 1)
        if card.get("state") != "open":
            return {"ok": False, "code": "not_open", "message": "This was already answered."}
        if action == "skip":
            harness.update_card(thread_id, card["card_id"], sink=sink, state="skipped", actions=[])
            return {"ok": True}
        if action != "record":
            raise _CommandError("card_action_unsupported", f"This card can’t “{action}”.")
        workflow = get_workflow(workflow_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", "That workflow isn’t available any more.")
        key = str((card.get("refs") or {}).get("fork_step_key") or "")
        harness.update_card(thread_id, card["card_id"], sink=sink, state="recording", actions=[])
        self._flow_open_recording(workflow, rid, sink, variant_of=key)
        return {"ok": True}

    # ── clarify answers ─────────────────────────────────────────────────────────────────────

    def _card_clarify_question(self, thread_id: str, card: dict[str, Any], action: str,
                               body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import clarify, clarify_store, harness
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        workflow_id = thread_id.replace("wf:", "", 1)
        card_id = card["card_id"]
        qid = str((card.get("refs") or {}).get("question_id") or card.get("question_id") or "")
        question = clarify_store.get_question(workflow_id, qid)
        if question is None:
            raise _CommandError("question_not_found", "That question isn’t available any more.")
        if card.get("state") not in ("open", "failed"):
            # A card stuck on "applying" after the app stopped mid-answer can be tried again once the
            # attempt is clearly dead; a live one (a double click) is still refused.
            dead_attempt = (card.get("state") == "applying" and question.get("status") == "applying"
                            and time.time() - float(question.get("applying_since") or 0) > _APPLY_STALE_S)
            if not dead_attempt:
                return {"ok": False, "code": "not_open", "message": "This question was already answered."}

        if action == "skip":
            clarify_store.update_question(workflow_id, qid, status="skipped", receipt="Kept as the compiler had it")
            harness.update_card(thread_id, card_id, sink=sink, state="skipped", receipt="Kept as the compiler had it", actions=[])
            self._flow_post_next_question(workflow_id, sink)
            return {"ok": True}
        if action not in ("choose", "retry"):
            raise _CommandError("card_action_unsupported", f"A question can’t “{action}”.")

        answer = {"choice": body.get("choice")} if body.get("choice") else {"selected": list(body.get("selected") or [])}
        if body.get("custom_text"):
            answer["custom_text"] = str(body["custom_text"])
        workflow = get_workflow(workflow_id)
        doc = read_skill(workflow.skill_id) if workflow and workflow.skill_id else None
        if doc is None:
            raise _CommandError("skill_not_found", "Compile this workflow first.")
        ops, receipt = clarify.plan_answer(question, answer, doc)
        base_version = (doc.get("meta") or {}).get("version")
        if question.get("status") == "applying" and question.get("base_version") != base_version:
            # An earlier try stopped part-way after it had already changed the workflow. Running the
            # answer again could add its steps twice, so hand the decision to the person.
            clarify_store.update_question(workflow_id, qid, status="open", base_version=None, applying_since=None)
            harness.update_card(thread_id, card_id, sink=sink, state="failed", actions=["skip"],
                                error="An earlier try stopped part-way. Check the workflow in Human Edit, then skip this question.")
            return {"ok": False, "code": "interrupted", "message": "An earlier try stopped part-way."}
        # Written before the edits, so a stop in the middle leaves a trace the next try can read.
        clarify_store.update_question(workflow_id, qid, status="applying", base_version=base_version,
                                      applying_since=time.time())
        harness.update_card(thread_id, card_id, sink=sink, state="applying", answer=answer, actions=[])
        try:
            added = self._run_clarify_ops(workflow.skill_id, ops, rid)
        except _CommandError as exc:
            clarify_store.update_question(workflow_id, qid, status="open", base_version=None, applying_since=None)
            stale = exc.code == "proposal_stale"
            harness.update_card(thread_id, card_id, sink=sink, state="failed", actions=["choose", "skip"],
                                error="The workflow changed in Human Edit, so this answer no longer fits." if stale else exc.message)
            return {"ok": False, "code": exc.code, "message": exc.message}
        clarify_store.answer_question(workflow_id, qid, answer=answer, receipt=receipt, added_steps=added)
        harness.update_card(thread_id, card_id, sink=sink, state="answered", receipt=receipt, actions=[],
                            step_keys=question.get("step_keys") or [])
        self._flow_post_next_question(workflow_id, sink)
        if question["kind"] == "input_check":
            self._after_input_check(workflow_id, question, answer.get("choice"), rid)
        return {"ok": True, "receipt": receipt}

    def _after_input_check(self, workflow_id: str, question: dict[str, Any], choice: Any, rid: str) -> None:
        """What the person's answer to "is this value right?" sets going: a corrected re-test, or the
        Auto-fix that was held back. Runs after the question is answered, since an open question
        keeps both out of the lifecycle's allowed actions."""
        try:
            if choice == "wrong":
                self.cmd_flow_test_prepare({"workflow_id": workflow_id, "suspect_inputs": question.get("input_ids") or []}, rid)
            else:
                self.cmd_flow_autofix_resume({"workflow_id": workflow_id}, rid)
        except _CommandError:
            pass  # the card is answered; the person can still press Test or Fix from the lifecycle actions

    def _run_clarify_ops(self, skill_id: str, ops: list[dict[str, Any]], rid: str) -> int:
        """Run an answer's ops all together or not at all: if any one fails, the workflow goes back to
        how it was before the answer (and its undo entries are dropped), so a retry can't apply the
        first half twice. Same pattern as ``_apply_change_set`` for chat change cards."""
        import copy

        from conxa_core.storage.json_store import read_skill, write_skill

        pre = read_skill(skill_id)
        snapshot = copy.deepcopy(pre) if pre is not None else None
        undo_stack = self._undo_stacks.setdefault(skill_id, [])
        depth = len(undo_stack)
        try:
            return self._apply_clarify_ops(skill_id, ops, rid)
        except Exception:
            del undo_stack[depth:]
            if snapshot is not None:
                write_skill(skill_id, snapshot)
            raise

    def _apply_clarify_ops(self, skill_id: str, ops: list[dict[str, Any]], rid: str) -> int:
        """Run an answer's ops through the commands a reviewer would use. Returns steps added."""
        from conxa_compile.editor.copilot_proposals import ProposalError, resolve_step_index
        from conxa_compile.editor.patch_gate import CONFIRM_ONLY_REVIEW_SCHEMA
        from conxa_compile.editor.workflow_mutations import _scan_placeholder_ids
        from conxa_core.storage.json_store import read_skill

        added = 0

        def index_of(key: str) -> int:
            doc = read_skill(skill_id) or {}
            try:
                return resolve_step_index(doc, key)
            except ProposalError as exc:
                raise _CommandError(exc.code, exc.message) from exc

        for op in ops:
            before = read_skill(skill_id)
            kind = op["op"]
            if kind == "patch":
                self.cmd_patch_step({"skill_id": skill_id, "step_index": index_of(op["step_key"]), "patch": op["patch"]}, rid)
                self._log_edit("patch_step", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "insert_review":
                i = index_of(op["before_step_key"])
                if i <= 0:
                    raise _CommandError("cannot_insert", "There is no step to put a confirmation in front of.")
                self.cmd_insert_step({"skill_id": skill_id, "action_kind": "ai_review", "insert_after": i - 1}, rid)
                self.cmd_patch_step({"skill_id": skill_id, "step_index": i, "patch": {
                    "ai_review_prompt": op["prompt"], "ai_review_output_schema": dict(CONFIRM_ONLY_REVIEW_SCHEMA),
                    "ai_review_on_failure": "abort"}}, rid)
                self._log_edit("insert_step", skill_id, before, source="clarify", proposal_id=None)
                added += 1
            elif kind == "set_fork_policy":
                self.cmd_set_fork_policy(
                    {"skill_id": skill_id, "step_index": index_of(op["step_key"]), "state_id": op["state_id"],
                     "policy": op["policy"]}, rid)
                self._log_edit("set_fork_policy", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "add_known_popup_fork":
                self.cmd_add_known_popup_fork(
                    {"skill_id": skill_id, "step_index": index_of(op["step_key"]), "popup": op["popup"]}, rid)
                self._log_edit("add_known_popup_fork", skill_id, before, source="clarify", proposal_id=None)
                added += 1
            elif kind == "confirm_optional":
                self.cmd_confirm_optional_interstitial(
                    {"skill_id": skill_id, "step_index": index_of(op["step_key"]), "keep_as_step": bool(op.get("keep_as_step"))}, rid)
                self._log_edit("confirm_optional_interstitial", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "delete_step":
                self.cmd_delete_step({"skill_id": skill_id, "step_index": index_of(op["step_key"])}, rid)
                self._log_edit("delete_step", skill_id, before, source="clarify", proposal_id=None)
                added -= 1
            elif kind == "parameterize":
                self.cmd_parameterize_literal(
                    {"skill_id": skill_id, "literal": op["literal"], "name": op["name"], "default": op.get("default") or "",
                     "kind": op.get("kind") or ""}, rid)
                self._log_edit("parameterize_literal", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "set_date_offset":
                self.cmd_set_date_offset({"skill_id": skill_id, "step_index": index_of(op["step_key"]), "days": op["days"]}, rid)
                self._log_edit("set_date_offset", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "drop_inputs":
                doc = read_skill(skill_id) or {}
                referenced: set[str] = set()
                for s in ((doc.get("skills") or [{}])[0] or {}).get("steps") or []:
                    if isinstance(s, dict):
                        _scan_placeholder_ids(s, referenced)
                drop = {str(i) for i in op["ids"]} - referenced
                kept = [i for i in doc.get("inputs") or [] if not (isinstance(i, dict) and str(i.get("id")) in drop)]
                if len(kept) != len(doc.get("inputs") or []):
                    self.cmd_update_workflow_inputs({"skill_id": skill_id, "inputs": kept}, rid)
                    self._log_edit("update_workflow_inputs", skill_id, before, source="clarify", proposal_id=None)
        return added

    # ── compile / summary card buttons ──────────────────────────────────────────────────────

    def _card_compile(self, thread_id: str, card: dict[str, Any], action: str, body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness

        if action == "retry":
            harness.update_card(thread_id, card["card_id"], sink=sink, state="dismissed", actions=[])
            self._flow_start_compile(thread_id.replace("wf:", "", 1))
            return {"ok": True}
        if action == "compile_later":
            harness.update_card(thread_id, card["card_id"], sink=sink, state="deferred", actions=["retry"])
            return {"ok": True}
        raise _CommandError("card_action_unsupported", f"A compile card can’t “{action}”.")

    def _card_workflow_summary(self, thread_id: str, card: dict[str, Any], action: str, body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        raise _CommandError("card_action_unsupported", "Testing starts from the test card.")
