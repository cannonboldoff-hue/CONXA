"""Understand a recording before compiling it (understanding-first pipeline, Q1).

Stopping a recording starts two tracks side by side: the video (prepare, Pegasus through the Conxa
cloud, cached) and the browser events. They are lined up, one text call writes the understanding,
and one ranked round of questions is asked. Answers are stored as decisions (never applied here);
when the last shown question is answered the compile starts by itself and replays them.

Pegasus is a hard dependency: if the cloud is down, slow or refuses, nothing past the merge starts.
The ``watching_video`` card says so and offers Retry (or Record again).
"""

from __future__ import annotations

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
        und = tu.understand_timeline(merged, workflow.task_description)
        (session_dir / "understanding.json").write_text(_dumps(und), encoding="utf-8")
        questions = tu.questions_from_understanding(und, merged)
        clarify_store.save(workflow_id, {
            "stage": "understand", "skill_id": None, "questions": questions,
            "understanding": {"goal_summary": und["goal"], "coverage": {"ok": not und["gaps"], "note": ""}, "source": und["source"]},
        })
        # Questions exist, so the lifecycle can see them; clear before the posts below because the
        # renderer re-reads the lifecycle on each one.
        runtime_state.set_flags(workflow_id, watching=False)
        harness.update_card(thread, card_id, sink=sink, state="done", elapsed_s=0, reason="", actions=[])
        self._flow_say(workflow_id, "I watched the recording and lined it up with what the browser saw. "
                       "A few quick questions first — most already have my best guess filled in.", sink)
        self._flow_post_next_question(workflow_id, sink)

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
        if action == "skip":
            decisions.record(session_dir, question, None)
            clarify_store.update_question(workflow_id, qid, status="skipped", receipt="Left as recorded")
            harness.update_card(thread_id, card_id, sink=sink, state="skipped", receipt="Left as recorded", actions=[])
            self._flow_post_next_question(workflow_id, sink)
            return {"ok": True}
        if action not in ("choose", "retry"):
            raise _CommandError("card_action_unsupported", f"A question can’t “{action}”.")
        answer = {"choice": body.get("choice")} if body.get("choice") else {"selected": list(body.get("selected") or [])}
        decisions.record(session_dir, question, answer)
        receipt = "Noted"
        clarify_store.answer_question(workflow_id, qid, answer=answer, receipt=receipt, added_steps=0)
        harness.update_card(thread_id, card_id, sink=sink, state="answered", receipt=receipt, answer=answer, actions=[])
        # "This isn't what I recorded" / "a part is missing": stop here so the person can record again.
        if (question["kind"] == "confirm_summary" and answer.get("choice") == "no") or (
                question["kind"] == "coverage" and answer.get("choice") == "incomplete"):
            self._flow_stop_for_rerecord(workflow_id, sink)
            return {"ok": True, "receipt": receipt}
        self._flow_post_next_question(workflow_id, sink)
        return {"ok": True, "receipt": receipt}

    def _flow_stop_for_rerecord(self, workflow_id: str, sink: Any) -> None:
        from conxa_compile.conversation import clarify_store

        def edit(data: dict[str, Any]) -> None:
            data["stage"] = "rejected"
            for q in data.get("questions") or []:
                if isinstance(q, dict) and q.get("status") in ("open", "queued"):
                    q["status"] = "skipped"
                    q["receipt"] = "Stopped before building"

        clarify_store.mutate(workflow_id, edit)
        self._flow_say(workflow_id, "Okay — I’ll hold off on building. Record it again when you’re ready, "
                       "or compile this recording as it is.", sink)

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
            runtime_state.set_flags(workflow_id, compiling=True)   # no moment where the lifecycle offers anything else
            self._flow_say(workflow_id, "Thanks — building it now.", sink)
            self._flow_start_compile(workflow_id)


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
