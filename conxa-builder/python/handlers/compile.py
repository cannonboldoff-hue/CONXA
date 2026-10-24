"""Pipeline and compile command handlers."""

from __future__ import annotations

from typing import Any

from conxa_compile.recorder.session import registry as _recorder_registry
from conxa_core.progress import job_event_scope
from handlers.protocol import _CommandError, _event_sink, _safe_id

class CompileMixin:
    def cmd_run_pipeline(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_compile.pipeline.run import run_pipeline
        from conxa_core.storage.session_events import read_session_events
        registry = _recorder_registry

        session_id = _safe_id(payload.get("session_id"), "session_id")
        sess = registry.get(session_id)
        raw = sess.snapshot_events() if sess else read_session_events(session_id)
        normalized = run_pipeline(raw)
        return {"session_id": session_id, "event_count": len(normalized)}

    def cmd_compile(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        import time as _time
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_core.storage.workflow_store import get_workflow, save_workflow
        from conxa_core.storage.session_events import read_session_events, session_events_path
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded
        from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError
        registry = _recorder_registry

        session_id = _safe_id(payload.get("session_id"), "session_id")
        workflow_id = str(payload.get("workflow_id") or "").strip()
        workflow = None
        if workflow_id:
            workflow_id = _safe_id(workflow_id, "workflow_id")
            workflow = get_workflow(workflow_id)
            if workflow is None:
                raise _CommandError("workflow_not_found", f"No workflow {workflow_id}")
            # A segment (re-record from a step) is a separate take beside the workflow's own recording.
            if workflow.session_id != session_id and not payload.get("segment"):
                raise _CommandError("workflow_not_found", f"Workflow {workflow_id} has no recording for session {session_id}")

        title = str(payload.get("skill_title") or "").strip()
        if not title and workflow is not None:
            title = workflow.name.strip()
        if not title:
            raise _CommandError("invalid_input", "skill_title is required")

        usage_class = "compile"
        reservation_id: str | None = None
        reservation_committed = False

        sink = _event_sink(rid)

        def _log(message: str, level: str = "info") -> None:
            sink({"phase": "compile_log", "message": message, "level": level, "ts": _time.time()})

        reservation_id = self._compile_reservation_id(rid, workflow_id, session_id)
        _log("Reserving one compile credit...")
        reserve = self._reserve_compile_credit(
            reservation_id=reservation_id,
            workflow_id=workflow_id,
            session_id=session_id,
        )
        sink({"phase": "quota", "meter": "compile_credits", "status": "reserved", **reserve})

        sink({"phase": "pipeline_start"})
        sink({"phase": "compile_step", "step": "normalize", "status": "running"})
        _log("Loading session events…")

        try:
            sess = registry.get(session_id)
            if sess is not None:
                # recording.webm is renamed into place in the recorder thread's
                # shutdown path (session.py:_finalize_video_file_sync). Wait for
                # that to finish before extracting frames from it.
                thread = getattr(sess, '_thread', None)
                if thread is not None and thread.is_alive():
                    _log("Waiting for recording to finalize…")
                    thread.join(timeout=30)
                    if thread.is_alive():
                        _log("Recording finalize still running after 30 s — compiling without frames.", level="warn")

            session_dir = session_events_path(session_id).parent
            if (session_dir / "recording.webm").is_file():
                from conxa_compile.recorder.frame_extractor import extract_frames_for_session

                _log("Extracting frames from the recording…")

                def _on_frame_progress(done: int, total: int) -> None:
                    _log(f"Extracted frames for event {done}/{total}.")

                try:
                    _, frame_failures = extract_frames_for_session(
                        session_dir, on_progress=_on_frame_progress
                    )
                except Exception as exc:  # noqa: BLE001
                    # Session-wide extraction problem (missing ffmpeg, corrupt video, etc.) —
                    # compile continues with DOM-only anchors rather than blocking.
                    _log(f"Warning: frame_extraction_error: {exc!s}", level="warn")
                else:
                    for idx, message in frame_failures:
                        _log(f"Warning: frame_extraction_error: event {idx + 1}: {message}", level="warn")

            _log("Reading normalized events from disk…")
            raw = read_session_events(session_id)
        except Exception:
            if reservation_id and not reservation_committed:
                self._release_compile_credit(reservation_id)
            raise
        if not raw:
            if reservation_id and not reservation_committed:
                self._release_compile_credit(reservation_id)
            raise _CommandError("no_events", "No recorded events for this session.")

        if reservation_id:
            _log("Committing compile credit before LLM-assisted compiler work...")
            try:
                commit = self._commit_compile_credit(reservation_id)
                reservation_committed = True
                sink({"phase": "quota", "meter": "compile_credits", "status": "committed", **commit})
            except Exception:
                self._release_compile_credit(reservation_id)
                raise

        self._install_proxy_router(sink=sink, usage_class=usage_class)
        self._apply_vision_fallback_entitlement()
        _log(f"Running normalization pipeline on {len(raw)} events…")
        try:
            normalized = run_pipeline(raw)
        except (CloudUnreachable, EntitlementBlocked, QuotaExceeded) as exc:
            _log(str(exc), level="error")
            sink({"phase": "compile_error", "message": str(exc), "failed_step": "normalize"})
            if isinstance(exc, EntitlementBlocked):
                raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
            if isinstance(exc, QuotaExceeded):
                raise _CommandError("quota_exceeded", str(exc)) from exc
            # CloudUnreachable (incl. ProxyUnavailable) is the only infra-class
            # failure here — refund the credit already committed above, since
            # nothing the user did caused this. Never refund for EntitlementBlocked/
            # QuotaExceeded — those are real per-workspace limits, not infra.
            if reservation_id and reservation_committed:
                self._refund_compile_credit(reservation_id)
            raise _CommandError("cloud_unreachable", str(exc)) from exc
        except Exception as exc:
            _log(str(exc), level="error")
            sink({"phase": "compile_error", "message": str(exc), "failed_step": "normalize"})
            raise

        _log(f"Pipeline produced {len(normalized)} normalized events")
        sink({"phase": "pipeline_done", "event_count": len(normalized)})
        for step in ("normalize", "dedupe", "enrich"):
            sink({"phase": "compile_step", "step": step, "status": "done"})
        sink({"phase": "compile_step", "step": "selectors", "status": "running"})

        skill_id = f"skill_{session_id}"
        existing = read_skill(skill_id)
        version = int((existing.get("meta") or {}).get("version") or 0) + 1 if existing else 1

        _log("Starting compiler — generating selectors, assertions, recovery blocks…")
        sink({"phase": "compiler_start"})
        try:
            with job_event_scope(rid):
                package = compile_skill_package(
                    normalized,
                    skill_id=skill_id,
                    source_session_id=session_id,
                    title=title,
                    version=version,
                    # Only when the person gave one, so a compile without a description is called
                    # exactly as it always was.
                    **({"task_description": workflow.task_description}
                       if workflow is not None and workflow.task_description.strip() else {}),
                    # Picks up on a page that is already open: no start-page navigation is added.
                    **({"segment": True} if payload.get("segment") else {}),
                )
        except (CloudUnreachable, EntitlementBlocked, QuotaExceeded) as exc:
            _log(str(exc), level="error")
            sink({"phase": "compile_error", "message": str(exc), "failed_step": "selectors"})
            if isinstance(exc, EntitlementBlocked):
                raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
            if isinstance(exc, QuotaExceeded):
                raise _CommandError("quota_exceeded", str(exc)) from exc
            if reservation_id and reservation_committed:
                self._refund_compile_credit(reservation_id)
            raise _CommandError("cloud_unreachable", str(exc)) from exc
        except VisionAnchorGenerationError as exc:
            _log(str(exc), level="error")
            sink({"phase": "compile_error", "message": str(exc), "failed_step": "selectors"})
            # Only the infra-class reason (the proxy call itself failed, after the
            # client's own retries and the router's own pool failover) is worth a
            # refund. vision_llm_empty_response / vision_llm_invalid_anchor_sentence
            # are content-quality outcomes — the LLM answered, just not usefully —
            # not something a refund is meant to cover.
            if exc.reason == "vision_llm_request_failed" and reservation_id and reservation_committed:
                self._refund_compile_credit(reservation_id)
            # Wrap into _CommandError (matching the cloud_unreachable pattern just
            # above) instead of a bare raise: VisionAnchorGenerationError isn't a
            # _CommandError, so backend.py's dispatcher used to fall through to its
            # generic "internal_error" arm — which the renderer shows as "Something
            # went wrong inside the app. Please try again." — even though the
            # specific, correct message (errorMessages.ts, code
            # vision_anchors_failed) already existed and exc.api_detail() already
            # knew to ask for it.
            raise _CommandError(exc.api_detail()["code"], str(exc)) from exc
        except Exception as exc:
            _log(str(exc), level="error")
            sink({"phase": "compile_error", "message": str(exc), "failed_step": "selectors"})
            raise

        write_skill(skill_id, package.model_dump(mode="json"))
        from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values
        from conxa_compile.compiler.upload_binding import filter_runtime_only_inputs

        doc = read_skill(skill_id) or package.model_dump(mode="json")
        doc, _ = reconcile_inputs_with_step_values(doc)
        doc["inputs"] = filter_runtime_only_inputs(list(doc.get("inputs") or []))
        write_skill(skill_id, doc)
        step_count = len(package.skills[0].steps)
        sink({"phase": "compiler_done", "step_count": step_count})
        for step in ("selectors", "assertions", "recovery", "package"):
            sink({"phase": "compile_step", "step": step, "status": "done"})
            _log(f"Completed: {step}")
        compile_report = package.compile_report or {}
        if workflow is not None and not payload.get("segment"):
            workflow.skill_id = skill_id
            workflow.recording_status = "compiled"
            workflow.compile_status = compile_report.get("status")
            workflow.compile_min_confidence = compile_report.get("min_confidence")
            workflow.compile_steps_with_warnings = compile_report.get("steps_with_warnings")
            save_workflow(workflow)
            # The two write_skill calls above ran before the workflow pointed at this skill, so the
            # app map filed it as unassigned; now that the link exists, refile it under its workspace.
            try:
                from conxa_compile.app_graph import refresh_skill  # noqa: PLC0415

                refresh_skill(skill_id)
            except Exception:  # noqa: BLE001 -- derived data, never worth failing a compile
                pass
        _log(f"Skill packaged: {skill_id} (version {version}, {step_count} steps)")
        sink({"phase": "compile_done", "skill_id": skill_id, "version": version, "step_count": step_count})
        return {
            "skill_id": skill_id,
            "version": version,
            "step_count": step_count,
            "compile_status": compile_report.get("status"),
            "compile_min_confidence": compile_report.get("min_confidence"),
            "compile_steps_with_warnings": compile_report.get("steps_with_warnings"),
        }

