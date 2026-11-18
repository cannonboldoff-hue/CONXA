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


# A recording that was understood before the build was already asked about a clicked or addressed value,
# a file. Those two kinds are not asked again after it. Everything else the compiled document can raise is
# still asked, in two passes (see _flow_after_compile). "Does this cover what you said?" can only be judged
# once the workflow exists (the compile's own review call), so it is asked here, not before.
_ASKED_BEFORE_COMPILE = {"upload", "chosen_values"}
# Kinds an earlier answer is replayed onto: built from the document as compiled, before replay changes it.
_REPLAY_KINDS = frozenset({"upload", "confirm", "popup", "fork", "fork_then", "known_popup"})
# Kinds built afterwards from the document replay left, so their bindings and step keys are current.
_REOPENED_KINDS = frozenset({"repeat_rows", "confirm_row", "values", "relative_date", "skip_when_empty", "missing_item", "output", "done_when", "dropped_value", "coverage", "unsupported", "human_check"})


def _thread(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


def _clarify_events():
    from conxa_compile.conversation import clarify_events

    return clarify_events


def _step_by_key(doc: dict[str, Any], key: str) -> dict[str, Any] | None:
    from conxa_compile.compiler.step_key import step_keys

    steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
    keys = step_keys(steps)
    return steps[keys.index(key)] if key in keys else None


def _is_agent_step(step: dict[str, Any] | None) -> bool:
    act = (step or {}).get("action")
    return (act.get("action") if isinstance(act, dict) else act) == "agent_span"


def _without_agent_steps(proposals: list[dict[str, Any]], doc: dict[str, Any]) -> list[dict[str, Any]]:
    """A typed answer never changes an agent or approval step (its goal, its allowed actions, removing it): the
    approval before a step that can't be undone is not the person's to drop by typing (WF-5 G)."""
    out = []
    for p in proposals:
        keys = {str(p.get(k) or "") for k in ("step_key", "after_step_key", "target_step_key")} - {""}
        if (p.get("is_agent_goal") or str(p.get("field") or "").startswith("agent_span")
                or any(_is_agent_step(_step_by_key(doc, k)) for k in keys)):
            continue
        out.append(p)
    return out


def _question_step_line(question: dict[str, Any], doc: dict[str, Any]) -> str:
    """Name the step the question is about by its key, so the model never counts steps (an inserted approval step
    shifts every number after it, WF-5 H)."""
    from conxa_compile.conversation import clarify

    for key in question.get("step_keys") or []:
        step = _step_by_key(doc, key)
        if step is not None and not _is_agent_step(step):
            return f" The question is about the step “{clarify.step_name(step)}” (step_key {key}); a new check goes on that step."
    return ""


def _check_wish_template(question: dict[str, Any], text: str, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The common wish "also check the page shows the <input> (I typed)", built as one text check on the question's
    own step from the workflow's declared inputs: a raw proposal for the gate, or [] when the words don't name
    exactly one input (then the model is asked)."""
    import re

    from conxa_compile.conversation import clarify

    if not clarify.describes_a_check(text):
        return []
    key = next((k for k in question.get("step_keys") or [] if not _is_agent_step(_step_by_key(doc, k))), "")
    step = _step_by_key(doc, key) if key else None
    if step is None:
        return []
    words = {w for w in re.split(r"[^\w']+", text.lower()) if w}
    ids = [str(i.get("id")) for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("id")]
    full = [i for i in ids if set(i.lower().split("_")) <= words]
    named = full if len(full) == 1 else [i for i in ids if words & (set(i.lower().split("_")) - {"user", "value"})]
    if len(named) != 1:
        return []
    current = [a for a in (step.get("validation") or {}).get("assertions") or [] if isinstance(a, dict)]
    check = {"type": "text_present", "target": "{{" + named[0] + "}}", "required": True}
    if check in current:
        return []
    return [{"step_key": key, "field": "validation.assertions", "patch": current + [check],
             "why": f"Check that the page shows the {named[0].replace('_', ' ')} that was typed in"}]


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
        self._require_questions_answered_before_build(workflow_id)
        return self._flow_compile(workflow_id, rid)

    @staticmethod
    def _require_questions_answered_before_build(workflow_id: str) -> None:
        """The build reads the answers to the questions asked while the recording was understood, so it never
        starts while one of them is open. No entry point (button, card retry, direct command) may skip this."""
        from conxa_compile.conversation import clarify_store

        summary = clarify_store.summary(workflow_id) or {}
        if summary.get("stage") == "understand" and int(summary.get("unanswered") or 0) > 0:
            raise _CommandError("unanswered_questions", "Answer the questions first — the build uses your answers.")

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
            # `ledger`: what the questions asked before the build settled; this marker must not erase it.
            before = clarify_store.load(workflow_id) or {}
            carried = before.get("ledger")
            # The new round writes its own questions: close any card of the old round still waiting, so it
            # is not answered against a workflow it was never about.
            for old in before.get("questions") or []:
                if isinstance(old, dict) and old.get("status") in ("open", "applying") and old.get("card_id"):
                    harness.update_card(thread, old["card_id"], sink=sink, state="reverted", actions=[],
                                        receipt="Replaced by the new build")
            clarify_store.save(workflow_id, {"skill_id": result.get("skill_id"), "version": result.get("version"),
                                             "building": True, "questions": [], **({"ledger": carried} if carried else {})})
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
            # A build step that crashed is said out loud, on the card that says the build is over, not left in
            # the report: the workflow is saved but part of it was never worked out.
            broken = self._flow_degraded_passes(result.get("skill_id"))
            if broken:
                harness.update_card(thread, card_id, sink=sink, state="partial", rows=progress.rows(),
                                    credits_left=progress.credits_left, step_count=result.get("step_count"),
                                    error="\n".join(broken), actions=["retry", "details"])
            else:
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

    @staticmethod
    def _flow_degraded_passes(skill_id: Any) -> list[str]:
        """The technical line for each build pass that crashed (``compile_report["degraded"]``), or [] when none did.
        A pass that was switched off or found nothing is not listed (build.py only records a real failure)."""
        from conxa_core.storage.json_store import read_skill

        doc = read_skill(str(skill_id)) if skill_id else None
        degraded = ((doc or {}).get("compile_report") or {}).get("degraded") or []
        return [f"{d.get('pass')}: {d.get('reason')}: {d.get('detail')}"[:400] for d in degraded if isinstance(d, dict)]

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
        from conxa_compile.editor.source_event import find_source_event
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

        # A recording that went through the understanding-first flow already asked about its values
        # and coverage (understanding.json); its answers are replayed below instead of asked again.
        prior = self._flow_prior_understanding(workflow)
        understood = prior or understanding_mod.understand(doc, workflow.task_description, review=report.get("understanding"))
        if prior:
            # The compile's own review call already judged whether the workflow covers what was said (stored with
            # the compile, no model call here); that verdict only exists now, so it is asked now.
            prior["coverage"] = understanding_mod.understand(doc, workflow.task_description, review=report.get("understanding"))["coverage"]
        limits: dict[str, int] = {}

        def ask(from_doc: dict[str, Any], count: bool = True, **only: Any) -> list[dict[str, Any]]:
            return clarify.generate_questions(
                from_doc,
                source_lookup=lambda s: find_source_event(s, from_doc),
                answered_by_recording=hints.load(workflow.session_id).get("varies") or {},
                known_popups=known,
                understanding={"coverage": prior["coverage"], "candidates": []} if prior else understood,
                dropped=report.get("dropped") or [],
                today=datetime.date.today(),
                limit=None,
                limits=limits if count else None,
                **only,
            )

        # With an earlier understanding, the kinds an answer is replayed onto come first (they match
        # the document as compiled); the rest are built after replay, from the document it left.
        questions = ask(doc, count=False, kinds=_REPLAY_KINDS) if prior else ask(doc)
        replayed_steps = self._flow_replay_decisions(workflow, result, doc, questions, lambda s: find_source_event(s, doc), sink)
        if prior:
            questions = [q for q in questions if q["kind"] not in _ASKED_BEFORE_COMPILE]
            questions += ask(read_skill(result["skill_id"]) or doc, kinds=_REOPENED_KINDS,
                             typed_fixed_events=self._flow_typed_fixed_events(workflow),
                             skip_events=self._flow_asked_before_events(workflow))
            questions.sort(key=lambda q: clarify._PRIORITY[q["kind"]])
        # Name the slot each question settles, and find (report) or drop (enforce) the vacuous and the
        # duplicated ones. After replay, from the document replay left.
        from conxa_compile.compiler.step_key import step_keys as _step_keys
        from conxa_compile.conversation import question_plan

        doc_now = read_skill(result["skill_id"]) or doc
        steps_now = ((doc_now.get("skills") or [{}])[0] or {}).get("steps") or []
        seq_by_key = {k: ((find_source_event(s, doc_now) or {}).get("extras") or {}).get("sequence")
                      for k, s in zip(_step_keys(steps_now), steps_now)}
        questions, ledger_report = question_plan.review(questions, doc=doc_now, event_id_of_step_key=seq_by_key.get,
                                                        found_at="post_compile")
        # What the person already settled before this rebuild (a pencil on an earlier question) is not asked again
        # while the question is the same one; the answer is replayed onto the new document instead.
        questions, carried_steps = self._flow_carry_settled(workflow_id, questions, result["skill_id"])
        replayed_steps += carried_steps
        doc_now = read_skill(result["skill_id"]) or doc_now
        # Worth a person's attention? Shadow mode only records the decision; "on" lets the compiler's default
        # stand for cheap ones (listed in the summary) and leaves test-revealable ones for a failing test.
        questions, assumptions = question_plan.apply_scoring(questions, doc_now, "post_compile")
        kept_ledger = (clarify_store.load(workflow_id) or {}).get("ledger") or {}
        # Ask the most important few now; keep the rest instead of silently dropping them.
        shown = 0
        for q in questions:
            if q.get("status") != "open":
                continue
            shown += 1
            if shown > clarify.MAX_QUESTIONS:
                q["status"] = "queued"
        # The questions go out in their plain template wording; a friendlier version follows in the
        # background (_flow_reword), so no question ever waits on a model.
        clarify_store.save(workflow_id, {
            "skill_id": result["skill_id"], "version": result.get("version"), "recorded_actions": recorded_actions,
            "added_steps": replayed_steps, "step_count_at_compile": len(steps), "questions": questions,
            "understanding": {k: understood[k] for k in ("goal_summary", "coverage", "source")},
            **({"limits": limits} if limits else {}),
            **({"ledger_report": ledger_report} if ledger_report else {}),
            **({"assumptions": assumptions} if assumptions else {}),
            # what the questions asked before the build settled (the questions above replace theirs)
            **({"ledger": kept_ledger} if kept_ledger else {}),
        })
        # Questions now exist, so the lifecycle can see them; clear before the posts below, because
        # the renderer re-reads the lifecycle on each one (the caller's `finally` is only the net).
        from conxa_compile.conversation import runtime_state

        # Drawing the recording could not keep is said first by code, and the recap gets no goal to restate (given the
        # person's goal it once said the workflow "draws a rectangle" when it drew nothing, WF-8 8.12).
        cant = " ".join(filter(None, (clarify.unsupported_sentence(report.get("dropped")),
                                      clarify.human_check_sentence(report.get("dropped")))))
        dropped = [d for d in report.get("dropped") or [] if d.get("pass") not in ("unsupported", "human_check")]
        # Facts for the model's recap: kinds and counts only, never the recorded target text (page noise).
        kind_words = {"click": "click", "scroll": "scroll", "navigate": "page visit", "type": "typing step", "fill": "typing step"}
        counts: dict[str, int] = {}
        for d in dropped:
            k = kind_words.get(str(d.get("action") or ""), "other action")
            counts[k] = counts.get(k, 0) + 1
        passes = {d.get("pass") for d in dropped}
        why = {"not_from_user": "the page did them on its own, not the person",
               "login_page": "they happened on a sign-in page, which is saved separately",
               "upstream_dedupe": "they were repeats"}.get(
            next(iter(passes)) if len(passes) == 1 else None, "they were repeats, or things the page did on its own")
        left_out = [{"kind": k, "count": n, "reason": why} for k, n in counts.items()]
        # Said in its own words, never folded into "nothing was left out" (the recap once said exactly that right
        # after the drawing line, WF-8 8.12).
        from conxa_compile.llm.compile_recap import DRAWING_LEFT_OUT, HUMAN_CHECK_LEFT_OUT, write_recap

        for wall, kind, reason in (("unsupported", "drawing action", DRAWING_LEFT_OUT), ("human_check", "robot check", HUMAN_CHECK_LEFT_OUT)):
            if (n := sum(1 for d in report.get("dropped") or [] if d.get("pass") == wall)):
                left_out.append({"kind": kind, "count": n, "reason": reason})
        # The message is written by one model call over these facts (llm/compile_recap.py), before `understanding`
        # clears so the UI stays busy and nothing can be tested meanwhile. No template stands in for it.
        from conxa_compile.conversation import harness

        open_qs = [q for q in questions if q.get("status") == "open"]
        coverage = understood.get("coverage") or {}
        recap = write_recap({
            "step_count": len(steps),
            "goal": "" if cant else str(understood.get("goal_summary") or workflow.task_description or "")[:200],
            "has_description": bool(workflow.task_description.strip()),
            "checked_against_description": understood.get("source") == "model",
            "input_names": [str(i["id"]).replace("_", " ") for i in doc_now.get("inputs") or [] if isinstance(i, dict) and i.get("id")],
            "left_out": left_out,
            "unsure_steps": [f"“{n}”" for n in uncertain_names],
            "similar_not_asked": sum(limits.values()) if limits else 0,
            "assumed": [str(a.get("title")) for a in assumptions if isinstance(a, dict) and a.get("title")][:3],
            "coverage_gap": str(coverage.get("note") or "") if coverage.get("ok") is False else "",
            "question_count": len(open_qs),
            "question_titles": [str(q.get("title")) for q in open_qs][:4],
        })
        runtime_state.set_flags(workflow_id, understanding=False)
        if cant:
            harness.emit_item(sink, _thread(workflow_id), ts.append(_thread(workflow_id), "assistant_message", text=cant))
        if recap:
            harness.emit_item(sink, _thread(workflow_id), ts.append(_thread(workflow_id), "assistant_message", text=recap))
        else:
            harness.log_event(_thread(workflow_id), "Couldn’t write the summary of what was built", glyph="warn", sink=sink,
                              subject="The workflow is saved; its questions and summary are below")
        if questions:
            self._flow_post_next_question(workflow_id, sink)
            threading.Thread(target=self._flow_reword_safe, args=(workflow_id, sink), daemon=True).start()
        else:
            self._flow_post_summary(workflow_id, sink)

    def _flow_carry_settled(self, workflow_id: str, questions: list[dict[str, Any]], skill_id: str) -> tuple[list[dict[str, Any]], int]:
        """Mark as answered each fresh question that asks what an earlier answer already settled: same slot, same
        situation (``clarify.situation_sig``), answered through the card. The answer is applied to the new
        document the way a click would; if that fails the question is simply asked again. Safety questions
        (popups, irreversible steps) are never carried: they always ask. Returns the questions and steps added."""
        import uuid

        from conxa_compile.conversation import clarify, clarify_store
        from conxa_core.storage.json_store import read_skill

        ledger = (clarify_store.load(workflow_id) or {}).get("ledger") or {}
        added = 0
        for q in questions:
            unknown = q.get("unknown") or {}
            prev = ledger.get(unknown.get("slot"))
            if (not prev or q.get("status") != "open" or unknown.get("safety_floor") or prev.get("phase") != "post_compile"
                    or prev.get("status") != "answered" or not prev.get("answer") or prev.get("sig") != clarify.situation_sig(q)):
                continue
            try:
                ops, receipt = clarify.plan_answer(q, prev["answer"], read_skill(skill_id) or {})
                added += self._run_clarify_ops(skill_id, ops, f"carry_{uuid.uuid4().hex[:8]}")
            except Exception:  # noqa: BLE001 -- not replayable now: it stays open and is asked again
                continue
            q.update(status="answered", answer=prev["answer"], receipt=receipt, carried=True)
        return questions, added

    def _flow_reevaluate_safe(self, workflow_id: str) -> None:
        try:
            self._flow_reevaluate(workflow_id)
        except Exception:  # noqa: BLE001 -- an answer is already applied; a missing follow-up must not undo it
            import traceback

            traceback.print_exc()

    def _flow_reevaluate(self, workflow_id: str) -> None:
        """After an answer edited the workflow, run the compile-time detectors again on the document as it is
        now and add the questions it newly raises (for example a record the run names, once a clicked item
        became an input). A situation already asked about or settled (same question id, or same slot in the
        ledger) is never asked twice. Deterministic: no model call."""
        import datetime

        from conxa_compile.compiler.step_key import step_keys
        from conxa_compile.conversation import clarify, clarify_store, question_plan
        from conxa_compile.editor.source_event import find_source_event
        from conxa_compile.recorder import hints
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        doc = read_skill(workflow.skill_id) if workflow and workflow.skill_id else None
        data = clarify_store.load(workflow_id) or {}
        if doc is None or data.get("stage") or data.get("building"):
            return
        prior = self._flow_prior_understanding(workflow)
        fresh = clarify.generate_questions(
            doc,
            source_lookup=lambda s: find_source_event(s, doc),
            answered_by_recording=hints.load(workflow.session_id).get("varies") or {},
            known_popups=[],                   # the app map's popups were offered at compile time
            dropped=(doc.get("compile_report") or {}).get("dropped") or [],
            today=datetime.date.today(),
            limit=None,
            typed_fixed_events=self._flow_typed_fixed_events(workflow) if prior else None,
            skip_events=self._flow_asked_before_events(workflow) if prior else None,
            kinds=None if not prior else frozenset(clarify._PRIORITY) - _ASKED_BEFORE_COMPILE,
        )
        steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        seq_by_key = {k: ((find_source_event(s, doc) or {}).get("extras") or {}).get("sequence") for k, s in zip(step_keys(steps), steps)}
        fresh, _ = question_plan.review(fresh, doc=doc, event_id_of_step_key=seq_by_key.get, found_at="post_compile")
        known = data.get("questions") or []
        taken_ids = {q.get("id") for q in known if isinstance(q, dict)}
        taken_slots = {(q.get("unknown") or {}).get("slot") for q in known if isinstance(q, dict)} | set(data.get("ledger") or {})
        new = [q for q in fresh if q["id"] not in taken_ids and (q.get("unknown") or {}).get("slot") not in taken_slots]
        if not new:
            return
        new, assumptions = question_plan.apply_scoring(new, doc, "post_compile")

        def add(d: dict[str, Any]) -> None:
            room = clarify.MAX_QUESTIONS - sum(1 for q in d.get("questions") or [] if q.get("status") in ("open", "applying"))
            for q in new:
                if q.get("status") == "open":
                    if room <= 0:
                        q["status"] = "queued"
                    room -= 1
            d["questions"] = [*(d.get("questions") or []), *new]
            if assumptions:
                d["assumptions"] = [*(d.get("assumptions") or []), *assumptions]

        clarify_store.mutate(workflow_id, add)

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
        ledger: dict[str, Any] = {}  # fields that kept the template wording, counted (llm/model_ledger)

        def edit(data: dict[str, Any]) -> None:
            live = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("status") in ("open", "queued")]
            before = {q["id"]: json.dumps([q["title"], q["body"], q["choices"]], sort_keys=True) for q in live}
            clarify.apply_reworded(live, reworded, ledger)
            changed.extend(dict(q) for q in live if json.dumps([q["title"], q["body"], q["choices"]], sort_keys=True) != before[q["id"]])

        clarify_store.mutate(workflow_id, edit)
        if ledger:
            from conxa_compile.llm import model_ledger
            from conxa_core.config import settings
            from conxa_core.storage.workflow_store import get_workflow

            workflow = get_workflow(workflow_id)
            if workflow is not None and workflow.session_id:
                model_ledger.append(settings.data_dir / "sessions" / workflow.session_id,
                                    model_ledger.entry("clarify_question_copy", ledger, applied=len(changed)))
        thread = _thread(workflow_id)
        for q in changed:
            card = ts.get_card(thread, q["card_id"]) if q.get("card_id") else None
            if card is None or card.get("state") != "open":
                continue  # not on screen yet, or answered meanwhile: the stored wording is enough
            fields: dict[str, Any] = {"title": q["title"], "body": q["body"]}
            if not q.get("multi"):
                fields["choices"] = q["choices"]  # a tick-list's labels never change, so its ticks are never reset
            harness.update_card(thread, q["card_id"], sink=sink, **fields)

    def _flow_post_next_question(self, workflow_id: str, sink: Any, *, promote_assumed: bool = False) -> None:
        from conxa_compile.conversation import clarify, clarify_store, harness, thread_store as ts

        data = clarify_store.load(workflow_id) or {}
        questions = [q for q in data.get("questions") or [] if isinstance(q, dict)]
        q = next((x for x in questions if x.get("status") == "open"), None)
        if q is None:
            # Every question that was asked must be answered: the ones held back past the first few come
            # forward as soon as nothing is open. "Ask me" also brings forward what the harness was sure about.
            wanted = ("queued", "assumed") if promote_assumed else ("queued",)
            for x in [x for x in questions if x.get("status") in wanted][: clarify.MAX_QUESTIONS]:
                x.update(status="open", assumed=False)
            if any(x.get("status") == "open" for x in questions):
                clarify_store.save(workflow_id, {**data, "questions": questions})
            q = next((x for x in questions if x.get("status") == "open"), None)
        if q is None:
            if data.get("stage") == "understand":
                self._flow_after_understand(workflow_id, sink)   # last pre-compile answer: build it
                return
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
        live = ts.get_card(_thread(workflow_id), q["card_id"]) if q.get("card_id") else None
        if live and live.get("state") in ("open", "failed", "applying"):
            return  # its card is already on screen: asking again would only stack a second copy
        asked = [x for x in questions if x.get("status") not in ("queued", "assumed", "superseded") and not x.get("carried")]
        number = asked.index(q) + 1
        card = harness.post_card(
            _thread(workflow_id), "clarify_question", "open", sink=sink, refs={"question_id": q["id"]},
            question_id=q["id"], question_kind=q["kind"], title=q["title"], body=q["body"], choices=q["choices"],
            multi=bool(q.get("multi")), index=number, total=len(asked), step_keys=q.get("step_keys") or [],
            actions=["choose"],
        )
        clarify_store.update_question(workflow_id, q["id"], card_id=card["card_id"])
        _clarify_events().log(workflow_id, "shown", q)

    def _flow_route_notes(self, workflow_id: str) -> None:
        """The words the person typed to a question before the build only rode along as compile context, so no
        step changed. Once the build is done and no question is open, hand them to the workflow chat once, where
        the three proposal gates and an Apply / Undo card are the only way they reach a step. Never raises."""
        import threading
        import uuid

        from conxa_compile.conversation import clarify_store, decisions
        from conxa_core.config import settings
        from conxa_core.storage.workflow_store import get_workflow

        try:
            workflow = get_workflow(workflow_id)
            data = clarify_store.load(workflow_id) or {}
            if (not settings.clarify_notes_to_chat or workflow is None or not workflow.session_id or data.get("notes_routed")
                    or any(q.get("status") in ("open", "applying") for q in data.get("questions") or [])):
                return
            notes = [str(d["note"])[:300] for d in decisions.load(settings.data_dir / "sessions" / workflow.session_id)
                     if d.get("note")][:5]
            if not notes:
                return
            clarify_store.mutate(workflow_id, lambda d: d.__setitem__("notes_routed", True))
            message = ("In your answers you asked me to keep in mind: " + " | ".join(f"“{n}”" for n in notes) +
                       ". Propose the change to the workflow if one applies, or tell me it needs none. The person cannot see "
                       "this message, so start your reply by saying in a few plain words which note you are answering.")
            threading.Thread(target=lambda: self.cmd_thread_turn({"thread_id": _thread(workflow_id), "message": message, "hidden": True},
                                                                 f"notes_{uuid.uuid4().hex[:10]}"), daemon=True).start()
        except Exception:  # noqa: BLE001 -- a missing follow-up must never break the summary
            import traceback

            traceback.print_exc()

    def _flow_post_summary(self, workflow_id: str, sink: Any, *, updated: bool = False) -> None:
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
        # Not the goal when drawing or a robot check was left out: the workflow does not do what the person set out to do.
        walls = (doc.get("compile_report") or {}).get("dropped")
        cant = clarify.unsupported_sentence(walls) or clarify.human_check_sentence(walls)
        summary["goal"] = "" if cant else str((data.get("understanding") or {}).get("goal_summary") or "")
        summary["input_names"] = [str(i.get("id")) for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("id")]
        summary["queued"] = sum(1 for q in data.get("questions") or [] if q.get("status") == "assumed")
        summary["assumptions"] = [a for a in data.get("assumptions") or [] if isinstance(a, dict)
                                  and any(q.get("id") == a.get("qid") and q.get("status") == "assumed" for q in data.get("questions") or [])]
        questions = data.get("questions") or []
        answered = [q for q in questions if q.get("status") == "answered"]
        if updated:
            # After a chat change was applied: the card again, with the new version, and nothing re-offered.
            text = f"Updated — here’s the workflow as it stands, saved as version {summary['version']}."
        elif questions:
            text = "That’s everything."
            if added:
                text += f" Your answers added {added} step{'s' if added != 1 else ''}."
            text += f" Here’s the workflow as it stands, saved as version {summary['version']}."
            if summary["queued"]:
                text += (f" I was sure about {summary['queued']} small thing{'s' if summary['queued'] != 1 else ''}, so I didn’t ask"
                         " — say “ask me” if you want to check them.")
        else:
            text = f"Here’s the workflow, saved as version {summary['version']}."
        harness.emit_item(sink, _thread(workflow_id), ts.append(_thread(workflow_id), "assistant_message", text=text))
        if not updated:
            self._flow_offer_variants(workflow_id, doc, sink)
        harness.post_card(
            _thread(workflow_id), "workflow_summary", "open", sink=sink, summary=summary,
            answered=len(answered), actions=["test", "review"] if summary["uncertain"] else ["test"],
        )
        if not updated:
            self._flow_route_notes(workflow_id)

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
        if action == "reopen":
            _clarify_events().log(workflow_id, "reopened", clarify_store.get_question(workflow_id, qid) or {"id": qid})
            return self._reopen_question(thread_id, card, workflow_id, qid, sink)
        question = clarify_store.get_question(workflow_id, qid)
        if question is None:
            raise _CommandError("question_not_found", "That question isn’t available any more.")
        if action == "resume":
            # The chat turn that followed an "own answer" is done: only now does the next question appear,
            # since an open question would otherwise catch the person's typed message.
            if not question.get("own_pending"):
                return {"ok": True}
            clarify_store.update_question(workflow_id, qid, own_pending=False)
            self._flow_post_next_question(workflow_id, sink)
            return {"ok": True}
        if question.get("phase") == "pre_compile":
            return self._pre_compile_answer(thread_id, card, question, action, body, sink)
        if card.get("state") not in ("open", "failed"):
            # A card stuck on "applying" after the app stopped mid-answer can be tried again once the
            # attempt is clearly dead; a live one (a double click) is still refused.
            dead_attempt = (card.get("state") == "applying" and question.get("status") == "applying"
                            and time.time() - float(question.get("applying_since") or 0) > _APPLY_STALE_S)
            if not dead_attempt:
                return {"ok": False, "code": "not_open", "message": "This question was already answered."}

        if action == "checked":
            # Only offered after an interrupted apply: the person looked at the workflow and says it is right.
            clarify_store.answer_question(workflow_id, qid, answer={"checked": True}, receipt="Checked by hand in Human Edit",
                                          added_steps=0)
            clarify_store.ledger_set(workflow_id, question, "answered", "checked")
            harness.update_card(thread_id, card_id, sink=sink, state="answered", receipt="Checked by hand in Human Edit",
                                answer={"checked": True}, actions=[])
            self._flow_post_next_question(workflow_id, sink)
            return {"ok": True}
        if action == "own":
            # The typed box is a third option: what the person wants when no option says it. Unusable words never
            # reach the model; words that clearly name an option are applied like a click; anything else is the
            # person's own wish, turned into a change they apply (_own_wish). The question closes only once it is.
            text = str(body.get("custom_text") or "")
            why = clarify.words_unusable(question, text)
            if why:
                harness.update_card(thread_id, card_id, sink=sink, state="failed", error=why, note="")
                return {"ok": False, "code": "answer_unclear", "message": why}
            words_answer, _ = clarify.answer_from_words(question, text)
            if words_answer is None:
                return self._own_wish(thread_id, card, question, text.strip(), rid, sink)
            action, body = "choose", words_answer
        if action not in ("choose", "retry"):
            raise _CommandError("card_action_unsupported", f"A question can’t “{action}”.")

        answer = {"choice": body.get("choice")} if body.get("choice") else {"selected": list(body.get("selected") or [])}
        if body.get("custom_text"):
            custom = str(body["custom_text"]).strip()
            if question["kind"] == "done_when" and (why := clarify.words_unusable(question, custom)):
                harness.update_card(thread_id, card_id, sink=sink, state="failed", error=why)   # "😀😀🎉" is no screen text
                return {"ok": False, "code": "answer_unclear", "message": why}
            if question["kind"] == "done_when" and clarify.describes_a_check(custom):
                # "check the output shows the email I typed" describes a check: a wish, applied as a change; the ticks
                # ride along and are applied with it once the person presses Apply.
                return self._own_wish(thread_id, card, question, custom, rid, sink,
                                      keep={"selected": list(answer.get("selected") or [])})
            if question["kind"] == "done_when" and len(custom) > 120:
                # Never cut the words silently: a check for half a sentence is not what the person typed.
                why = "Those words are longer than 120 characters. Type a shorter part of what you see on the screen."
                harness.update_card(thread_id, card_id, sink=sink, state="failed", error=why)
                return {"ok": False, "code": "answer_too_long", "message": why}
            answer["custom_text"] = custom
        workflow = get_workflow(workflow_id)
        doc = read_skill(workflow.skill_id) if workflow and workflow.skill_id else None
        if doc is None:
            raise _CommandError("skill_not_found", "Compile this workflow first.")
        ops, receipt = clarify.plan_answer(question, answer, doc)
        can_reopen = question["kind"] != "input_check" and self._save_snapshot(workflow_id, qid, workflow.skill_id, question)
        base_version = (doc.get("meta") or {}).get("version")
        if question.get("status") == "applying" and question.get("base_version") != base_version:
            # An earlier try stopped part-way after it had already changed the workflow. Running the
            # answer again could add its steps twice, so hand the decision to the person.
            clarify_store.update_question(workflow_id, qid, status="open", base_version=None, applying_since=None)
            harness.update_card(thread_id, card_id, sink=sink, state="failed", actions=["checked"],
                                error="An earlier try stopped part-way. Check the workflow in Human Edit, then confirm it here.")
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
            harness.update_card(thread_id, card_id, sink=sink, state="failed", actions=["choose"],
                                error="The workflow changed in Human Edit, so this answer no longer fits." if stale else exc.message)
            return {"ok": False, "code": exc.code, "message": exc.message}
        clarify_store.answer_question(workflow_id, qid, answer=answer, receipt=receipt, added_steps=added)
        self._drop_wish_cards(workflow_id, qid, sink)
        clarify_store.ledger_set(workflow_id, question, "answered", answer.get("choice") or answer.get("selected"), answer=answer)
        from conxa_compile.conversation import answer_memory

        answer_memory.remember(answer_memory.host_of(doc), question, answer)       # next workflow on this app asks less
        _clarify_events().answered(workflow_id, question, answer)
        harness.update_card(thread_id, card_id, sink=sink, state="answered", receipt=receipt, actions=[],
                            step_keys=question.get("step_keys") or [], can_reopen=can_reopen)
        if any(op.get("op") == "wrap_rows_loop" for op in ops):
            # Questions about the copies that became one repeated step have nothing left to ask; the re-check below
            # asks again about the repeated step itself (for example, approve before it starts).
            from conxa_compile.compiler.step_key import step_keys
            from conxa_core.storage.json_store import read_skill

            live = ((read_skill(workflow.skill_id) or {}).get("skills") or [{}])[0].get("steps") or []
            clarify_store.mark_stale(workflow_id, set(step_keys(live)), why="Now part of the repeated steps", keep=qid)
            self._close_superseded_cards(workflow_id, sink)
        if ops:
            self._flow_reevaluate_safe(workflow_id)       # the answer changed the workflow: it may raise a question it could not before
        self._flow_post_next_question(workflow_id, sink)
        if question["kind"] == "input_check":
            self._after_input_check(workflow_id, question, answer.get("choice"), rid)
        return {"ok": True, "receipt": receipt}

    # ── the pencil: go back to an earlier question ──────────────────────────────────────────

    def _save_snapshot(self, workflow_id: str, qid: str, skill_id: str, question: dict[str, Any]) -> bool:
        """The workflow as it stood just before this question was answered: what the pencil rewinds to.
        Never blocks an answer; False means the pencil is not offered for it."""
        import json

        from conxa_compile.conversation import clarify_store
        from conxa_core.config import settings
        from conxa_core.storage.json_store import read_skill

        path = settings.data_dir / "workflows" / workflow_id / "clarify_snapshots" / f"{qid}.json"
        if question.get("status") == "applying" and path.is_file():
            return True  # a retry of an interrupted answer: the doc may be half changed, keep the first snapshot
        try:
            doc = read_skill(skill_id)
            if doc is None:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            added = int((clarify_store.load(workflow_id) or {}).get("added_steps") or 0)
            path.write_text(json.dumps({"doc": doc, "added_steps": added}), encoding="utf-8")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── the typed box: the person's own wish (third option) ─────────────────────────────────

    def _own_wish(self, thread_id: str, card: dict[str, Any], question: dict[str, Any], text: str,
                  rid: str, sink: Any, keep: dict[str, Any] | None = None) -> dict[str, Any]:
        """What the person typed when no option says what they want. The model first checks whether it is one
        of the options after all (then it is applied like a click); otherwise it proposes a change, through the
        same gates as a chat change, on a change card the person applies. The question stays open until that
        change is applied (``_close_question_by_wish``); a wish no safe change can carry leaves it open."""
        import types

        from conxa_compile.conversation import harness

        workflow_id = thread_id.replace("wf:", "", 1)
        card_id = card["card_id"]
        self._drop_wish_cards(workflow_id, question["id"], sink)  # one wish, at most one live change card

        def still_open(why: str) -> dict[str, Any]:
            harness.update_card(thread_id, card_id, sink=sink, state="failed", error=why, note="")
            return {"ok": False, "code": "wish_not_applied", "message": why}

        ctx = self._thread_context("wf", workflow_id, sink)
        if not ctx.get("skill_id"):
            return still_open("That isn’t one of the options. Pick the closest one; once the workflow is built you can "
                              "ask for anything else in the chat.")
        self._install_proxy_router(sink=sink, usage_class="ai_credits")
        try:
            from conxa_compile.conversation import clarify

            if not question.get("multi") and not clarify.is_safety_question(question):
                # The model may say the wish is an option after all, but only one that holds every word of it.
                pick = harness.map_free_text_to_choice(question, text)
                choice = next((c for c in question.get("choices") or [] if c.get("id") == pick), None)
                if choice is not None and clarify.adds_nothing(text, choice):
                    return self._card_clarify_question(thread_id, card, "choose", {"choice": pick}, rid, sink)
            from conxa_core.storage.json_store import read_skill

            doc = read_skill(ctx["skill_id"]) or {}
            # "also check the page shows the email I typed": built by code on the question's own step, no model.
            proposals = ctx["gate_proposals"](_check_wish_template(question, text, doc))
            if not proposals:
                choices = "; ".join(str(c.get("label") or c.get("id")) for c in question.get("choices") or [])
                context = (f"The person is answering Studio's question “{question.get('title')}”"
                           f"{' about the step “' + str(question.get('step_name')) + '”' if question.get('step_name') else ''}. "
                           f"{question.get('body') or ''}\nThe offered options were: {choices}. None of them says what they "
                           f"want, so they typed their own answer. Propose the change to the workflow that does what they "
                           f"asked, and only that.{_question_step_line(question, doc)} Never change an agent step or an "
                           f"approval step (an agent_span).\nTheir answer: {text}")
                digest = ctx["load_digest"]()
                for _attempt in range(2):  # the model misses a valid change now and then (WF-5: 1 of 3): one more try
                    proposals = _without_agent_steps(harness._propose(
                        message=text, base_pack=types.SimpleNamespace(text=context), digest=digest,
                        skill_id=ctx["skill_id"], gate_proposals=ctx["gate_proposals"], cancel=threading.Event()), doc)
                    if proposals:
                        break
        except Exception as exc:  # noqa: BLE001 -- out of credits, no network: the question just stays open
            return still_open(f"I couldn’t work that out just now ({str(exc)[:120]}). Pick one of the options, or try again.")
        if not proposals:
            return still_open("I couldn’t turn that into a change Studio can make safely, so this question is still open. "
                              "Pick one of the options, or say what you want another way.")
        from conxa_compile.editor.copilot_proposals import proposal_summary

        summaries = [proposal_summary(p) for p in proposals]
        harness.post_card(
            thread_id, "pending_change", "open", sink=sink,
            refs={"skill_id": ctx["skill_id"], "proposal_ids": [p.get("id") for p in proposals],
                  "question_id": question["id"], "question_card_id": card_id},
            title=f"Your answer to “{question.get('title')}”", why=f"You asked: “{text}”", proposals=proposals,
            summaries=summaries, preview=proposals[0].get("preview") if len(proposals) == 1 else None,
            own_text=text, keep_answer=keep or None, actions=["apply", "dismiss"])
        harness.update_card(thread_id, card_id, sink=sink, state="open", error="",
                            note="Your answer needs a change to the workflow. Check it below and press Apply; "
                                 "this question closes once it is applied.")
        return {"ok": True, "pending_change": True}

    def _drop_wish_cards(self, workflow_id: str, qid: str, sink: Any) -> None:
        """Close any change card still offered for this question's earlier typed answer: a new attempt, a picked
        option or a closed question replaces it, so nothing stale is left to apply twice or to hold "Test it"."""
        from conxa_compile.conversation import harness, thread_store as ts

        thread = _thread(workflow_id)
        for c in ts.fold(ts.read_raw(thread)):
            if (c.get("kind") == "card" and c.get("card_type") == "pending_change" and c.get("state") == "open"
                    and (c.get("refs") or {}).get("question_id") == qid):
                harness.update_card(thread, c["card_id"], sink=sink, state="replaced", actions=[])

    def _close_question_by_wish(self, workflow_id: str, change_card: dict[str, Any], rid: str, sink: Any) -> None:
        """A wish's change was applied and saved: only now is its question answered. A question about a step that
        can't be undone keeps its approval: a typed wish never removes it (picking "No, just do it" does)."""
        from conxa_compile.conversation import clarify, clarify_store, harness
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        refs = change_card.get("refs") or {}
        qid, q_card = str(refs.get("question_id") or ""), str(refs.get("question_card_id") or "")
        question = clarify_store.get_question(workflow_id, qid)
        if question is None or question.get("status") == "answered":
            return  # answered another way meanwhile: the change stands on its own
        thread = _thread(workflow_id)
        changed = "; ".join(str(s) for s in change_card.get("summaries") or []) or "the workflow"
        receipt, added = f"Changed: {changed}", 0
        keep = change_card.get("keep_answer")
        if isinstance(keep, dict) and keep.get("selected"):
            # What the person ticked beside their wish ("How can we tell it worked?"), applied with it.
            workflow = get_workflow(workflow_id)
            ops, _ = clarify.plan_answer(question, {"selected": keep["selected"]}, read_skill(workflow.skill_id) or {})
            try:
                added += self._run_clarify_ops(workflow.skill_id, ops, rid)
            except _CommandError as exc:
                harness.update_card(thread, q_card, sink=sink, state="failed", note="",
                                    error=f"Your change was applied, but what you ticked could not be ({exc.message}). "
                                          "Pick again to finish.")
                return
            n = len(keep["selected"])
            receipt += f", plus the {n} check{'s' if n != 1 else ''} you ticked"  # short: the ticked labels are long
        if question.get("kind") == "confirm":
            workflow = get_workflow(workflow_id)
            ops, _ = clarify.plan_answer(question, {"choice": "yes"}, read_skill(workflow.skill_id) or {})
            try:
                added = self._run_clarify_ops(workflow.skill_id, ops, rid)
            except _CommandError as exc:
                harness.update_card(thread, q_card, sink=sink, state="failed", note="",
                                    error=f"Your change was applied, but the approval before this step could not be kept "
                                          f"({exc.message}). Pick an option to finish.")
                return
            receipt += f". Someone still approves “{question.get('step_name') or 'this step'}” first."
        answer = {"custom_text": str(change_card.get("own_text") or ""), "change": change_card.get("summaries") or []}
        clarify_store.answer_question(workflow_id, qid, answer=answer, receipt=receipt, added_steps=added)
        clarify_store.ledger_set(workflow_id, question, "answered", "own", answer=answer)
        _clarify_events().answered(workflow_id, question, answer)
        harness.update_card(thread, q_card, sink=sink, state="answered", receipt=receipt, answer=answer, actions=[], note="",
                            step_keys=question.get("step_keys") or [], can_reopen=False)
        self._flow_reevaluate_safe(workflow_id)
        self._flow_post_next_question(workflow_id, sink)

    def _remind_open_question(self, workflow_id: str, sink: Any) -> None:
        """After a typed message the question is still waiting: say so once, naming it. Never raises."""
        from conxa_compile.conversation import clarify_store

        try:
            qs = [x for x in (clarify_store.load(workflow_id) or {}).get("questions") or [] if isinstance(x, dict)]
            q = next((x for x in qs if x.get("status") == "open"), None)
            # The turn that follows an "own words" answer is that answer's hand-off: the next card comes after it.
            if q is not None and not any(x.get("own_pending") for x in qs):
                self._flow_say(workflow_id, f"Still need your answer: {q.get('title') or 'the question above'}", sink)
        except Exception:  # noqa: BLE001
            pass

    def _close_superseded_cards(self, workflow_id: str, sink: Any) -> None:
        """A question whose steps were re-recorded away, or folded into a repeated step, has nothing left to ask:
        close its card, or it would sit pinned and unanswerable."""
        from conxa_compile.conversation import clarify_store, harness, thread_store as ts

        for q in (clarify_store.load(workflow_id) or {}).get("questions") or []:
            if isinstance(q, dict) and q.get("status") == "superseded" and q.get("card_id"):
                card = ts.get_card(_thread(workflow_id), q["card_id"])
                if card and card.get("state") in ("open", "failed"):
                    harness.update_card(_thread(workflow_id), q["card_id"], sink=sink, state="reverted", actions=[],
                                        receipt=q.get("receipt") or "No longer needed")

    def _reask_unanswered(self, workflow_id: str, sink: Any) -> None:
        """A question nobody answered keeps coming back: opening the conversation again (after a restart, a
        recompile, a card that went missing) puts the next one in front of the person. A card already on
        screen is left alone. Never raises: a missing re-ask must not stop the conversation from opening."""
        from conxa_compile.conversation import clarify_store, runtime_state

        try:
            self._close_superseded_cards(workflow_id, sink)
            flags = runtime_state.get_flags(workflow_id)
            if any(flags.get(k) for k in ("compiling", "testing", "understanding", "watching", "queued", "applying")):
                return
            summary = clarify_store.summary(workflow_id) or {}
            if int(summary.get("unanswered") or 0) > 0 and not (clarify_store.load(workflow_id) or {}).get("building"):
                self._flow_post_next_question(workflow_id, sink)
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()

    def _reopen_question(self, thread_id: str, card: dict[str, Any], workflow_id: str, qid: str, sink: Any) -> dict[str, Any]:
        """Rewind to just before this question was answered and ask it again. Everything done after it
        (later answers, chat changes) is discarded. A question asked before the build also rebuilds it."""
        from conxa_compile.conversation import clarify_store, runtime_state
        from conxa_core.storage.workflow_store import get_workflow

        flags = runtime_state.get_flags(workflow_id)
        if any(flags.get(k) for k in ("compiling", "testing", "understanding", "watching", "queued")):
            return {"ok": False, "code": "busy", "message": "Wait for what is running to finish, then go back."}
        if card.get("state") not in ("answered", "skipped"):
            return {"ok": False, "code": "not_answered", "message": "This question isn’t answered yet."}
        workflow = get_workflow(workflow_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", "That workflow isn’t available any more.")
        question = clarify_store.get_question(workflow_id, qid)
        if question is not None and question.get("phase") != "pre_compile":
            return self._rewind_after_compile(thread_id, card, workflow, qid, sink)
        return self._rewind_before_compile(thread_id, card, workflow, qid, sink)

    @staticmethod
    def _revert_cards_after(thread_id: str, card_id: str, extra_ids: set[str], sink: Any) -> None:
        from conxa_compile.conversation import harness, thread_store as ts

        items = ts.fold(ts.read_raw(thread_id))
        pos = next((k for k, it in enumerate(items) if it.get("card_id") == card_id), -1)
        for it in items[pos + 1:]:
            if it.get("kind") == "card" and (it["card_id"] in extra_ids or it.get("state") == "applied"):
                harness.update_card(thread_id, it["card_id"], sink=sink, state="reverted", actions=[])

    def _rewind_after_compile(self, thread_id: str, card: dict[str, Any], workflow: Any, qid: str, sink: Any) -> dict[str, Any]:
        import json

        from conxa_compile.conversation import clarify, clarify_store, harness
        from conxa_core.config import settings
        from conxa_core.storage.json_store import write_skill

        path = settings.data_dir / "workflows" / workflow.id / "clarify_snapshots" / f"{qid}.json"
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "code": "cannot_rewind", "message": "This answer was given before going back was possible."}
        write_skill(workflow.skill_id, snap["doc"])
        self._undo_stacks.pop(workflow.skill_id, None)
        later_cards: set[str] = set()
        reopened: set[str] = set()

        def edit(data: dict[str, Any]) -> None:
            qs = [q for q in data.get("questions") or [] if isinstance(q, dict)]
            i = next(k for k, q in enumerate(qs) if q.get("id") == qid)
            for q in qs[i:]:
                if q.get("status") in ("answered", "skipped", "applying") or q.get("id") == qid:
                    reopened.add(q["id"])
                    q.update(status="open", answer=None, receipt="", own_pending=False, base_version=None, applying_since=None)
                if q.get("id") != qid and q.get("card_id"):
                    later_cards.add(q["card_id"])
                    q["card_id"] = None
            data["added_steps"] = int(snap.get("added_steps") or 0)

        clarify_store.mutate(workflow.id, edit)
        clarify_store.ledger_drop(workflow.id, reopened)
        fields: dict[str, Any] = {}
        q = clarify_store.get_question(workflow.id, qid) or {}
        if q.get("kind") == "done_when":
            fresh = clarify.refresh_question(q, snap["doc"])
            fields = {"choices": fresh["choices"], "step_keys": fresh["step_keys"]}
            clarify_store.update_question(workflow.id, qid, **fields)
        self._revert_cards_after(thread_id, card["card_id"], later_cards, sink)
        harness.update_card(thread_id, card["card_id"], sink=sink, state="open", receipt=None, answer=None, error=None,
                            can_reopen=False, actions=["choose"], **fields)
        return {"ok": True}

    def _rewind_before_compile(self, thread_id: str, card: dict[str, Any], workflow: Any, qid: str, sink: Any) -> dict[str, Any]:
        import json

        from conxa_compile.conversation import clarify_store, decisions, harness, thread_store as ts
        from conxa_core.config import settings

        arch = settings.data_dir / "workflows" / workflow.id / "pre_questions.json"
        try:
            data = json.loads(arch.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "code": "cannot_rewind", "message": "This answer was given before going back was possible."}
        qs = [q for q in data.get("questions") or [] if isinstance(q, dict)]
        ids = [q.get("id") for q in qs]
        if qid not in ids or not workflow.session_id:
            return {"ok": False, "code": "cannot_rewind", "message": "This question can’t be asked again."}
        i = ids.index(qid)
        card_of: dict[str, dict[str, Any]] = {}
        for it in ts.fold(ts.read_raw(thread_id)):
            if it.get("kind") == "card" and it.get("card_type") == "clarify_question":
                card_of[str((it.get("refs") or {}).get("question_id") or it.get("question_id") or "")] = it
        later_cards: set[str] = set()
        for k, q in enumerate(qs):
            shown = card_of.get(q["id"])
            q["card_id"] = shown["card_id"] if shown else None
            if k < i:
                if shown:
                    q["status"] = "skipped" if shown.get("state") == "skipped" else "answered"
            elif shown:
                q.update(status="open", answer=None, receipt="")
                if k > i:
                    later_cards.add(shown["card_id"])
                    q["card_id"] = None
        # Every after-the-build question card is history now: the rebuild writes new ones.
        for other_id, it in card_of.items():
            if other_id in ids:
                continue
            if it.get("state") in ("open", "failed"):
                later_cards.add(it["card_id"])
            else:
                harness.update_card(thread_id, it["card_id"], sink=sink, can_reopen=False)
        session_dir = settings.data_dir / "sessions" / workflow.session_id
        drop = {q["id"] for q in qs[i:]}
        kept = [d for d in decisions.load(session_dir) if d.get("qid") not in drop]
        (session_dir / decisions.FILE).write_text(json.dumps(kept), encoding="utf-8")
        ledger = {slot: v for slot, v in ((clarify_store.load(workflow.id) or {}).get("ledger") or {}).items() if v.get("qid") not in drop}
        clarify_store.save(workflow.id, {**data, "stage": "understand", "skill_id": None, "questions": qs,
                                         **({"ledger": ledger} if ledger else {})})
        later_cards.discard(card["card_id"])
        for cid in later_cards:
            harness.update_card(thread_id, cid, sink=sink, state="reverted", actions=[])
        harness.update_card(thread_id, card["card_id"], sink=sink, state="open", receipt=None, answer=None, error=None,
                            can_reopen=False, actions=["choose"])
        self._flow_say(workflow.id, "Going back here. Answer again and I’ll rebuild it from your new answer.", sink)
        return {"ok": True}

    def _after_input_check(self, workflow_id: str, question: dict[str, Any], choice: Any, rid: str) -> None:
        """What the person's answer to "is this value right?" sets going: a corrected re-test, or the
        Auto-fix that was held back. Runs after the question is answered, since an open question
        keeps both out of the lifecycle's allowed actions."""
        try:
            if choice == "wrong":
                self.cmd_flow_test_prepare({"workflow_id": workflow_id, "suspect_inputs": question.get("input_ids") or []}, rid)
            else:
                self.cmd_flow_investigate({"workflow_id": workflow_id}, rid)
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
        from conxa_compile.editor.workflow_mutations import _scan_placeholder_ids
        from conxa_core.storage.json_store import read_skill, write_skill

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
                self.cmd_insert_step({"skill_id": skill_id, "action_kind": "agent_span", "insert_after": i - 1,
                                      "confirm_only": True}, rid)
                # Named for what it asks, not its kind ("Agent span goal" on the test card, WF-5).
                self.cmd_patch_step({"skill_id": skill_id, "step_index": i,
                                     "patch": {"value": op["prompt"], "semantic_description": op["title"]}}, rid)
                self._log_edit("insert_step", skill_id, before, source="clarify", proposal_id=None)
                added += 1
            elif kind == "insert_handover":
                # The person asked for it (the robot-check question): a hand-over where the robot check was.
                i = index_of(op["after_step_key"])
                self.cmd_insert_step({"skill_id": skill_id, "action_kind": "handover", "insert_after": i}, rid)
                self.cmd_patch_step({"skill_id": skill_id, "step_index": i + 1,
                                     "patch": {"value": op["message"], "semantic_description": "A person does the robot check"}}, rid)
                self._log_edit("insert_step", skill_id, before, source="clarify", proposal_id=None)
                added += 1
            elif kind == "set_fork_policy":
                self.cmd_set_fork_policy(
                    {"skill_id": skill_id, "step_index": index_of(op["step_key"]), "state_id": op["state_id"],
                     "policy": op["policy"]}, rid)
                self._log_edit("set_fork_policy", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "flatten_fork":
                self.cmd_flatten_fork(
                    {"skill_id": skill_id, "step_index": index_of(op["step_key"]), "state_id": op["state_id"]}, rid)
                self._log_edit("flatten_fork", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "add_known_popup_fork":
                self.cmd_add_known_popup_fork(
                    {"skill_id": skill_id, "step_index": index_of(op["step_key"]), "popup": op["popup"]}, rid)
                self._log_edit("add_known_popup_fork", skill_id, before, source="clarify", proposal_id=None)
                added += 1
            elif kind == "confirm_effect":
                self.cmd_confirm_effect({"skill_id": skill_id, "step_index": index_of(op["step_key"])}, rid)
                self._log_edit("confirm_effect", skill_id, before, source="clarify", proposal_id=None)
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
            elif kind == "wrap_rows_loop":
                import copy

                from conxa_compile.editor.workflow_mutations import wrap_repeated_entries
                from conxa_core.storage.json_store import write_skill

                doc = read_skill(skill_id) or {}
                self._push_undo(skill_id, copy.deepcopy(doc))
                try:
                    write_skill(skill_id, wrap_repeated_entries(doc, op["suggestion"]))
                except ValueError as exc:
                    raise _CommandError(str(exc), "The repeated steps changed since this was asked, so the list could not be set up.") from exc
                self._log_edit("wrap_repeated_entries", skill_id, before, source="clarify", proposal_id=None)
                added -= int(op["suggestion"].get("block_len") or 0) * (int(op["suggestion"].get("count") or 0) - 1)
            elif kind == "set_date_offset":
                self.cmd_set_date_offset({"skill_id": skill_id, "step_index": index_of(op["step_key"]), "days": op["days"]}, rid)
                self._log_edit("set_date_offset", skill_id, before, source="clarify", proposal_id=None)
            elif kind == "drop_inputs":
                doc = read_skill(skill_id) or {}
                # A row named by a value that stops being an input is named by the recorded value again
                # (compiler/entity_binding.py bound it to "{{id}}"); otherwise it would keep the input alive.
                literals = op.get("literals") or {}
                restored = False
                for s in ((doc.get("skills") or [{}])[0] or {}).get("steps") or []:
                    eb = s.get("entity_binding") if isinstance(s, dict) else None
                    ident = str((eb or {}).get("identifier") or "")
                    named = [b for b, lit in literals.items() if lit and "{{" + b + "}}" in ident]
                    for b in named:
                        ident = ident.replace("{{" + b + "}}", literals[b])
                    if named:
                        eb["identifier"] = ident
                        if "{{" not in ident:
                            eb["source"] = "literal"
                        restored = True
                if restored:
                    write_skill(skill_id, doc)
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
            self._require_questions_answered_before_build(thread_id.replace("wf:", "", 1))
            harness.update_card(thread_id, card["card_id"], sink=sink, state="dismissed", actions=[])
            self._flow_start_compile(thread_id.replace("wf:", "", 1))
            return {"ok": True}
        if action == "compile_later":
            harness.update_card(thread_id, card["card_id"], sink=sink, state="deferred", actions=["retry"])
            return {"ok": True}
        raise _CommandError("card_action_unsupported", f"A compile card can’t “{action}”.")

    def _card_workflow_summary(self, thread_id: str, card: dict[str, Any], action: str, body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        raise _CommandError("card_action_unsupported", "Testing starts from the test card.")
