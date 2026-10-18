"""Recording-visual and step-screenshot command handlers."""

from __future__ import annotations

from typing import Any

from handlers.protocol import _CommandError, _safe_id, _skill_response
from conxa_compile.editor.step_path import StepPathError, parse_nested_step_path

class VisualMixin:
    def cmd_compile_updated(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.json_store import read_skill, write_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        skill_title = str(payload.get("skill_title") or "").strip()
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        doc = dict(doc)
        meta = dict(doc.get("meta") or {})
        if skill_title:
            meta["title"] = skill_title
        meta["version"] = int(meta.get("version") or 1) + 1
        doc["meta"] = meta
        write_skill(skill_id, doc)
        return {"skill_id": skill_id, "ok": True}

    # ─── recording visuals ───────────────────────────────────────────────────

    def cmd_list_recording_screenshots(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from pathlib import Path
        from conxa_core.config import settings
        from conxa_core.storage.json_store import read_skill
        from conxa_compile.editor.recording_visual import screenshot_items_for_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        asset_base_url = f"file://{Path(settings.data_dir) / 'skills' / skill_id / 'assets'}"
        session_id, items = screenshot_items_for_skill(skill_id, doc, asset_base_url=asset_base_url)
        return {"skill_id": skill_id, "session_id": session_id, "items": items}

    def cmd_apply_recording_visual(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.recording_visual import apply_recording_event_visual_to_step_or_raise
        from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        event_index = int(payload.get("event_index") or 0)
        frame_label = str(payload.get("frame_label") or "").strip() or None
        try:
            nested_path = parse_nested_step_path(payload.get("path"))
        except StepPathError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(doc)
        self._install_proxy_router(usage_class="ai_credits")
        try:
            doc = apply_recording_event_visual_to_step_or_raise(
                doc, step_index, event_index, frame_label=frame_label, nested_path=nested_path
            )
        except EntitlementBlocked as exc:
            raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
        except QuotaExceeded as exc:
            raise _CommandError("quota_exceeded", str(exc)) from exc
        except CloudUnreachable as exc:
            raise _CommandError("cloud_unreachable", str(exc)) from exc
        except VisionAnchorGenerationError as exc:
            detail = f" ({exc.hint})" if exc.hint else ""
            raise _CommandError(
                "vision_anchors_failed", f"Could not generate recovery anchors ({exc.reason}){detail}."
            ) from exc
        except ValueError as exc:
            raise _CommandError(str(exc), str(exc)) from exc
        self._push_undo(skill_id, snapshot)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_apply_step_frame(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.recording_visual import apply_step_frame_or_raise
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        frame_label = str(payload.get("frame_label") or "").strip()
        if not frame_label:
            raise _CommandError("invalid_frame_label", "frame_label is required")
        try:
            nested_path = parse_nested_step_path(payload.get("path"))
        except StepPathError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(doc)
        self._install_proxy_router(usage_class="ai_credits")
        try:
            doc = apply_step_frame_or_raise(doc, step_index, frame_label, nested_path=nested_path)
        except EntitlementBlocked as exc:
            raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
        except QuotaExceeded as exc:
            raise _CommandError("quota_exceeded", str(exc)) from exc
        except CloudUnreachable as exc:
            raise _CommandError("cloud_unreachable", str(exc)) from exc
        except ValueError as exc:
            raise _CommandError(str(exc), str(exc)) from exc
        self._push_undo(skill_id, snapshot)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_clear_step_visual(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.recording_visual import clear_step_visual_screenshots_or_raise

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        try:
            nested_path = parse_nested_step_path(payload.get("path"))
        except StepPathError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        try:
            doc = clear_step_visual_screenshots_or_raise(doc, step_index, nested_path=nested_path)
        except ValueError as exc:
            raise _CommandError(str(exc), str(exc)) from exc
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_update_visual_bbox(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.recording_visual import (
            update_step_visual_bbox_and_regenerate_anchors_or_raise,
        )
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        bbox = {k: float(payload.get(k) or 0) for k in ("x", "y", "w", "h")}
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(doc)
        self._install_proxy_router(usage_class="ai_credits")
        try:
            doc = update_step_visual_bbox_and_regenerate_anchors_or_raise(doc, step_index, bbox)
        except EntitlementBlocked as exc:
            raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
        except QuotaExceeded as exc:
            raise _CommandError("quota_exceeded", str(exc)) from exc
        except CloudUnreachable as exc:
            raise _CommandError("cloud_unreachable", str(exc)) from exc
        self._push_undo(skill_id, snapshot)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    # ─── skill library ───────────────────────────────────────────────────────

