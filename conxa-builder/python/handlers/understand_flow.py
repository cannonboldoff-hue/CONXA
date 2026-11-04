"""Understand a recording before compiling it (understanding-first pipeline, Q1).

Stopping a recording starts two tracks side by side: the video (prepare, Pegasus through the Conxa
cloud, cached) and the browser events. They are lined up, one text call writes the understanding,
and one ranked round of questions is asked. Answers are stored as decisions (never applied here);
when the last shown question is answered the compile starts by itself and replays them.

Pegasus is a hard dependency: if the cloud is down, slow or refuses, nothing past the merge starts.
The ``watching_video`` card says so and offers Retry (or Record again).
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from handlers.protocol import _CommandError, _event_sink

_CARD_THROTTLE_S = 1.0


def _thread(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


class UnderstandFlowMixin:
    # ── watch + understand ──────────────────────────────────────────────────────────────────

    def _flow_start_understand(self, workflow_id: str) -> None:
        rid = f"understand_{uuid.uuid4().hex[:10]}"
        threading.Thread(target=self._flow_understand_safe, args=(workflow_id, rid), daemon=True).start()

    def _flow_understand_safe(self, workflow_id: str, rid: str) -> None:
        try:
            self._flow_understand(workflow_id, rid)
        except Exception:  # noqa: BLE001 -- a background flow must never take the engine down
            import traceback

            traceback.print_exc()
            self._flow_video_failed(workflow_id, None, "Something went wrong while watching the recording.", _event_sink(rid))

    def _blur_all_typed_values(self) -> bool:
        """The company's "blur every typed value" setting (an entitlement capability). Off if it can't be read."""
        try:
            caps = self._cloud_json("/api/v1/entitlements/current").get("capabilities") or {}
            return caps.get("blur_all_typed_values") is True
        except Exception:  # noqa: BLE001 -- blurring passwords always happens; this only widens it
            return False

    def _flow_understand(self, workflow_id: str, rid: str) -> None:
        import os

        from conxa_compile.conversation import clarify_store, harness, runtime_state
        from conxa_compile.conversation import timeline_understanding as tu
        from conxa_compile.video import track_a, track_b
        from conxa_compile.video.prepare import NoVideoError
        from conxa_core.config import settings
        from conxa_core.storage.workflow_store import get_workflow
        from services.video_analysis_client import (
            VideoAnalysisClient, VideoAnalysisFailed, VideoBlocked, VideoUnavailable,
        )

        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.session_id:
            raise _CommandError("not_recorded", "Record this workflow first.")
        sink = _event_sink(rid)
        thread = _thread(workflow_id)
        session_dir = settings.data_dir / "sessions" / workflow.session_id
        runtime_state.set_flags(workflow_id, watching=True, video_error="")
        # A re-watch replaces the questions of the last round: close their cards so none is answered twice.
        for old in (clarify_store.load(workflow_id) or {}).get("questions") or []:
            if isinstance(old, dict) and old.get("status") == "open" and old.get("card_id"):
                harness.update_card(thread, old["card_id"], sink=sink, state="skipped", actions=[],
                                    receipt="Replaced by a fresh look at the recording")
        card = harness.post_card(thread, "watching_video", "watching", sink=sink, elapsed_s=0, reason="", actions=[])
        card_id = card["card_id"]
        last = {"t": 0.0}

        def on_wait(info: dict[str, Any]) -> None:
            now = time.monotonic()
            if now - last["t"] < _CARD_THROTTLE_S:
                return
            last["t"] = now
            harness.update_card(thread, card_id, sink=sink, state="watching",
                                elapsed_s=info.get("elapsed_s", 0), reason=str(info.get("reason") or ""))

        try:
            client = VideoAnalysisClient(
                self._cloud_api_base(), lambda: self._auth_service().get_token(),
                client_header=os.environ.get("CONXA_PROXY_CLIENT", "build-studio"), on_wait=on_wait)
            blur_all = self._blur_all_typed_values()
            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_a = pool.submit(track_a.run_track_a, session_dir, client, blur_all=blur_all)
                fut_b = pool.submit(track_b.run_track_b, session_dir)
                a, timed = fut_a.result(), fut_b.result()
        except VideoUnavailable as exc:
            return self._flow_video_failed(workflow_id, card_id, f"Still couldn’t reach the video service ({exc}). Try again in a moment.", sink)
        except VideoBlocked as exc:
            return self._flow_video_failed(workflow_id, card_id, self._video_blocked_message(exc.code), sink)
        except VideoAnalysisFailed as exc:
            return self._flow_video_failed(workflow_id, card_id, f"The video service couldn’t read this recording ({exc.code}).", sink)
        except NoVideoError as exc:
            return self._flow_video_failed(workflow_id, card_id, f"This recording has no video to watch ({exc}). Record it again.", sink)

        merged = track_b.build_merged_timeline(session_dir, a, timed)
        self._install_proxy_router(sink, usage_class="compile")
        # What the person already said while recording (hints.json) and the inputs an earlier compile of
        # this workflow already declared: the model is told both, so it neither re-asks nor reuses a name.
        from conxa_compile.recorder import hints
        from conxa_core.storage.json_store import read_skill
        prior = read_skill(workflow.skill_id) if workflow.skill_id else None
        taken = {str(i.get("id")) for i in (prior or {}).get("inputs") or [] if isinstance(i, dict) and i.get("id")}
        und = tu.understand_timeline(merged, workflow.task_description, existing_inputs=taken or None,
                                     hints=hints.load(workflow.session_id).get("varies"))
        (session_dir / "understanding.json").write_text(_dumps(und), encoding="utf-8")
        questions = tu.questions_from_understanding(und, merged)
        clarify_store.save(workflow_id, {
            "stage": "understand", "skill_id": None, "questions": questions,
            "understanding": {"goal_summary": und["goal"], "coverage": {"ok": True, "note": ""}, "source": und["source"]},
        })
        # Questions exist, so the lifecycle can see them; clear before the posts below because the
        # renderer re-reads the lifecycle on each one.
        runtime_state.set_flags(workflow_id, watching=False)
        harness.update_card(thread, card_id, sink=sink, state="done", elapsed_s=0, reason="", actions=[])
        self._flow_say(workflow_id, self._understood_message(und, bool(questions)), sink)
        self._flow_post_next_question(workflow_id, sink)   # builds straight away when there is nothing to ask

    @staticmethod
    def _understood_message(und: dict[str, Any], has_questions: bool) -> str:
        """What the model understood, as one chat message: the summary, the few points it settled by
        itself (with the reason, so a wrong call is easy to spot), and a gap if there is one."""
        # A point a question below asks about is the person's to settle, not stated as decided; a gap
        # is one of those questions.
        asked = {q.get("row") for q in und.get("questions") or []}
        extra = [f"• {d['text']}" + (f" — {d['reason']}" if d.get("reason") else "")
                 for d in (und.get("decided") or []) if d.get("row") not in asked][:3]
        closing = ("A few quick questions first — most already have my best guess filled in." if has_questions
                   else "That's all I need — building it now.")
        if und.get("source") == "fallback":
            # The model's read of the recording failed or came back empty: say so, so nobody trusts a
            # thin summary or waits for guesses that are not there.
            closing = ("I couldn’t work out the details of this recording this time, so these questions come straight "
                       "from what was clicked and nothing is filled in for you." if has_questions
                       else "I couldn’t work out the details of this recording this time, so I’m building it as recorded. "
                            "Check the steps carefully once it’s done.")
        head = und.get("short_summary") or und.get("goal") or "I watched the recording."
        return "\n\n".join([head, *(["\n".join(extra)] if extra else []), closing])

    @staticmethod
    def _video_blocked_message(code: str) -> str:
        return {
            "video_quota_exceeded": "This month’s video-watching allowance is used up.",
            "trial_expired": "Your free trial has ended, so recordings can’t be watched any more.",
            "unauthorized": "You’re signed out. Sign in again, then retry.",
            "video_unavailable": "The video service isn’t set up on this server yet.",
        }.get(code, f"The video service refused this recording ({code}).")

    def _flow_video_failed(self, workflow_id: str, card_id: str | None, message: str, sink: Any) -> None:
        from conxa_compile.conversation import harness, runtime_state

        runtime_state.set_flags(workflow_id, watching=False, video_error=message)
        if card_id:
            harness.update_card(_thread(workflow_id), card_id, sink=sink, state="failed", reason=message,
                                actions=["retry_video", "re_record"])
        else:
            harness.post_card(_thread(workflow_id), "watching_video", "failed", sink=sink, reason=message, elapsed_s=0,
                              actions=["retry_video", "re_record"])

    def _card_watching_video(self, thread_id: str, card: dict[str, Any], action: str,
                             body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        workflow_id = thread_id.replace("wf:", "", 1)
        if action == "retry_video":
            self._require_allowed(workflow_id, "retry_video")
            self._flow_start_understand(workflow_id)
            return {"ok": True}
        if action == "re_record":
            self._require_allowed(workflow_id, "re_record")
            self.cmd_flow_begin({"workflow_id": workflow_id}, rid)
            return {"ok": True}
        raise _CommandError("card_action_unsupported", f"This card can’t “{action}”.")

    # ── after the compile: replay the stored decisions ──────────────────────────────────────

    def _flow_prior_understanding(self, workflow: Any) -> dict[str, Any] | None:
        """The understanding written before this compile, shaped like ``understanding.understand()``'s
        result (no candidates: they were asked about already). None for a recording that never went through it."""
        import json

        from conxa_core.config import settings

        path = settings.data_dir / "sessions" / str(workflow.session_id) / "understanding.json"
        try:
            und = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # Every gap was asked before the build (timeline_understanding.validate), so none is left open.
        return {"candidates": [], "goal_summary": und.get("goal") or "", "source": und.get("source") or "model",
                "coverage": {"ok": True, "note": ""}}

    def _flow_replay_decisions(self, workflow: Any, result: dict[str, Any], doc: dict[str, Any],
                               questions: list[dict[str, Any]], look: Any, sink: Any) -> int:
        """Apply the pre-compile answers to the compiled workflow through the same edits a button uses.
        Marks the compile-time questions they answer, and adds a question for anything that could not
        be applied. Returns the steps added. Never raises: the compile stands whatever happens here."""
        from conxa_compile.compiler.step_key import step_keys
        from conxa_compile.conversation import decisions, replay
        from conxa_core.config import settings

        try:
            stored = decisions.load(settings.data_dir / "sessions" / str(workflow.session_id))
            if not stored:
                return 0
            steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
            seq_by_key = {k: ((look(s) or {}).get("extras") or {}).get("sequence") for k, s in zip(step_keys(steps), steps)}
            plan = replay.plan(stored, doc, questions, lambda key: seq_by_key.get(key))
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            return 0
        added, failed_values = 0, []
        for g in plan["groups"]:
            try:
                added += self._run_clarify_ops(result["skill_id"], g["ops"], f"replay_{uuid.uuid4().hex[:8]}")
            except Exception:  # noqa: BLE001 -- one answer that no longer fits must not undo the rest
                if g.get("value"):
                    failed_values.append(g["value"])
                continue
            for q in questions:
                if g.get("qid") and q["id"] == g["qid"]:
                    q.update(status="answered", answer=g["answer"], receipt=g["receipt"])
        if failed_values:
            names = ", ".join(f"“{v['literal']}”" for v in failed_values[:3])
            self._flow_say(workflow.id, f"I couldn’t make {names} change each run, so it stays as you recorded. "
                           "You can change it in Human Edit.", sink)
        for u in plan.get("unapplied") or []:
            # An answer that matched nothing is never dropped quietly.
            self._flow_say(workflow.id, f"Your answer about {replay.SUBJECT.get(u['kind'], 'that step')} didn’t match anything "
                           "in the workflow I built, so it stays as recorded. Tell me here what you want changed.", sink)
        return added

    # ── answers: stored, then the compile starts ────────────────────────────────────────────

    def _pre_compile_answer(self, thread_id: str, card: dict[str, Any], question: dict[str, Any],
                            action: str, body: dict[str, Any], sink: Any) -> dict[str, Any]:
        """Store the answer as a decision (nothing is applied), then ask the next question or compile."""
        from conxa_compile.conversation import clarify_store, decisions, harness
        from conxa_core.config import settings
        from conxa_core.storage.workflow_store import get_workflow

        workflow_id = thread_id.replace("wf:", "", 1)
        qid, card_id = question["id"], card["card_id"]
        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.session_id:
            raise _CommandError("workflow_not_found", "That workflow isn’t available any more.")
        session_dir = settings.data_dir / "sessions" / workflow.session_id
        if card.get("state") not in ("open", "failed"):
            return {"ok": False, "code": "not_open", "message": "This question was already answered."}
        self._archive_pre_questions(workflow_id, qid)
        if action == "skip":
            decisions.record(session_dir, question, None)
            clarify_store.update_question(workflow_id, qid, status="skipped", receipt="Left as recorded")
            harness.update_card(thread_id, card_id, sink=sink, state="skipped", receipt="Left as recorded", actions=[],
                                can_reopen=True)
            self._flow_post_next_question(workflow_id, sink)
            return {"ok": True}
        if action == "own":
            # Nothing is compiled yet, so chat has nothing to change: keep the words and carry on as recorded.
            text = str(body.get("custom_text") or "").strip()[:500]
            if not text:
                raise _CommandError("invalid_input", "Type your answer first.")
            # A note's own words are what the build should know (they reach the compile as context).
            decisions.record(session_dir, question, None, own_text=text if question["kind"] == "note" else "")
            receipt = f"Noted: “{text}” — left as recorded. Tell the chat after it’s built if a step should change."
            clarify_store.update_question(workflow_id, qid, status="skipped", receipt=receipt, own_text=text)
            harness.update_card(thread_id, card_id, sink=sink, state="skipped", receipt=receipt, actions=[], can_reopen=True)
            self._flow_post_next_question(workflow_id, sink)
            return {"ok": True}
        if action not in ("choose", "retry"):
            raise _CommandError("card_action_unsupported", f"A question can’t “{action}”.")
        answer = {"choice": body.get("choice")} if body.get("choice") else {"selected": list(body.get("selected") or [])}
        decisions.record(session_dir, question, answer)
        picked = set(answer.get("selected") or []) | {answer.get("choice")}
        receipt = ", ".join(c["label"] for c in question.get("choices") or [] if c.get("id") in picked) or "Noted"
        clarify_store.answer_question(workflow_id, qid, answer=answer, receipt=receipt, added_steps=0)
        harness.update_card(thread_id, card_id, sink=sink, state="answered", receipt=receipt, answer=answer, actions=[],
                            can_reopen=True)
        self._flow_post_next_question(workflow_id, sink)
        return {"ok": True, "receipt": receipt}

    @staticmethod
    def _archive_pre_questions(workflow_id: str, qid: str) -> None:
        # The questions asked before the build, kept beside the workflow: the build replaces the question
        # list, and going back to one of them (the pencil) needs it again. Written once per recording.
        import json

        from conxa_compile.conversation import clarify_store
        from conxa_core.config import settings

        path = settings.data_dir / "workflows" / workflow_id / "pre_questions.json"
        try:
            if path.is_file() and qid in {q.get("id") for q in json.loads(path.read_text(encoding="utf-8")).get("questions") or []}:
                return
            data = clarify_store.load(workflow_id)
            if data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass  # only the pencil depends on it

    def _flow_after_understand(self, workflow_id: str, sink: Any) -> None:
        """Every shown question is answered: build it. Runs once per recording."""
        from conxa_compile.conversation import clarify_store, runtime_state

        started: list[bool] = []

        def edit(data: dict[str, Any]) -> None:
            if data.get("stage") == "understand":
                data["stage"] = "building"
                started.append(True)

        clarify_store.mutate(workflow_id, edit)
        if started:
            _fill_description(workflow_id)
            runtime_state.set_flags(workflow_id, compiling=True)   # no moment where the lifecycle offers anything else
            self._flow_say(workflow_id, "Thanks — building it now.", sink)
            self._flow_start_compile(workflow_id)


def _fill_description(workflow_id: str) -> None:
    """A workflow recorded with no description gets the one the model wrote for it."""
    import json

    from conxa_core.config import settings
    from conxa_core.storage.workflow_store import get_workflow, save_workflow

    workflow = get_workflow(workflow_id)
    if workflow is None or not workflow.session_id or workflow.task_description.strip():
        return
    try:
        und = json.loads((settings.data_dir / "sessions" / workflow.session_id / "understanding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    text = str(und.get("short_summary") or "").strip()
    if text:
        # A value the person said changes each run is not this workflow's fixed subject: name it by its input.
        from conxa_compile.conversation import decisions

        for d in decisions.load(settings.data_dir / "sessions" / workflow.session_id):
            for v in d.get("values") or []:
                for lit in {str(v.get("literal") or "").strip(), _short_value(v)}:
                    if len(lit) >= 3:
                        text = re.sub(r"(?:\bthe\s+)?" + re.escape(lit), f"the chosen {str(v.get('name') or 'value').replace('_', ' ')}", text, flags=re.I)
        workflow.task_description = text
        save_workflow(workflow)


def _short_value(v: dict[str, Any]) -> str:
    """A clicked label without the kind the page appended ("FuelPHP.gitignore, (File)"); a URL's last part."""
    lit = str(v.get("literal") or "")
    if v.get("kind") == "url":
        return lit.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r",?\s*\([^)]*\)\s*$", "", lit).strip()


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
