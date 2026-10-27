"""TwelveLabs Pegasus client for recording-video analysis.

One recording video in, one list of time-based "ui_action" segments out. The
uploaded asset is always deleted afterwards (``finally``) so no customer video
stays on TwelveLabs. Called only from ``app/api/video_routes.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from conxa_core.config import settings

logger = logging.getLogger(__name__)

# Module-level so tests can zero them.
POLL_INTERVAL_S = 3.0
ASSET_READY_TIMEOUT_S = 300.0
TASK_TIMEOUT_S = 900.0

ACTION_KINDS = ["click", "type", "select", "navigate", "scroll", "upload", "download", "dialog", "wait"]

SEGMENT_DEFINITION = {
    "id": "ui_action",
    "description": "One thing the person did on screen, or one thing the screen did by itself",
    "fields": [
        {"name": "action_kind", "type": "string", "description": "One of: " + ", ".join(ACTION_KINDS)},
        {"name": "visible_target", "type": "string", "description": "The control or area acted on, as a person would describe it"},
        {"name": "intent", "type": "string", "description": "What the person was trying to do, in a short verb phrase"},
        {"name": "purpose", "type": "string", "description": "Why this step matters to the task"},
        {"name": "screen_before", "type": "string", "description": "What the screen showed just before"},
        {"name": "screen_after", "type": "string", "description": "What the screen showed just after"},
        {"name": "outside_page", "type": "boolean", "description": "True when it happened outside the web page, e.g. a native dialog or the browser download bar"},
    ],
}


class VideoAnalysisError(Exception):
    """``code`` is a stable machine string; ``status_code`` the HTTP status the route returns."""

    def __init__(self, code: str, status_code: int = 502) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _headers() -> dict[str, str]:
    return {"x-api-key": settings.twelvelabs_api_key}


def _url(path: str) -> str:
    return settings.twelvelabs_base_url.rstrip("/") + path


def _check(resp: httpx.Response, what: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        logger.warning("twelvelabs_%s_failed status=%s body=%s", what, resp.status_code, resp.text[:300])
        raise VideoAnalysisError(f"twelvelabs_{what}_failed")
    try:
        return resp.json()
    except ValueError as exc:
        raise VideoAnalysisError(f"twelvelabs_{what}_bad_response") from exc


async def _wait(deadline: float, code: str) -> None:
    if time.monotonic() >= deadline:
        raise VideoAnalysisError(code, 504)
    await asyncio.sleep(POLL_INTERVAL_S)


async def analyze_video(data: bytes, filename: str = "recording.mp4") -> dict[str, Any]:
    """Returns ``{"segments": [{start_time, end_time, metadata}], "duration_s": float | None}``."""
    if not settings.twelvelabs_api_key:
        raise VideoAnalysisError("video_unavailable", 503)

    asset_id: str | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        try:
            up = await client.post(
                _url("/assets"),
                headers=_headers(),
                data={"method": "direct", "filename": filename},
                files={"file": (filename, data, "video/mp4")},
            )
            asset_id = str(_check(up, "upload").get("_id") or "")
            if not asset_id:
                raise VideoAnalysisError("twelvelabs_upload_bad_response")

            deadline = time.monotonic() + ASSET_READY_TIMEOUT_S
            while True:
                asset = _check(await client.get(_url(f"/assets/{asset_id}"), headers=_headers()), "asset")
                status = asset.get("status")
                if status == "ready":
                    break
                if status == "failed":
                    raise VideoAnalysisError("twelvelabs_asset_failed")
                await _wait(deadline, "twelvelabs_asset_timeout")

            create = await client.post(
                _url("/analyze/tasks"),
                headers=_headers(),
                json={
                    "model_name": "pegasus1.6",
                    "video": {"type": "asset_id", "asset_id": asset_id},
                    "analysis_mode": "time_based_metadata",
                    "max_tokens": 32768,
                    "response_format": {
                        "type": "segment_definitions",
                        "segment_definitions": [SEGMENT_DEFINITION],
                        "segment_time_format": "seconds",
                    },
                },
            )
            task_id = str(_check(create, "task").get("task_id") or "")
            if not task_id:
                raise VideoAnalysisError("twelvelabs_task_bad_response")

            deadline = time.monotonic() + TASK_TIMEOUT_S
            while True:
                task = _check(await client.get(_url(f"/analyze/tasks/{task_id}"), headers=_headers()), "task_poll")
                status = task.get("status")
                if status == "ready":
                    break
                if status in ("failed", "canceled"):
                    raise VideoAnalysisError("twelvelabs_task_failed")
                await _wait(deadline, "twelvelabs_task_timeout")

            return _parse_task(task)
        finally:
            if asset_id:
                try:
                    await client.delete(_url(f"/assets/{asset_id}"), headers=_headers(), params={"force": "true"})
                except Exception:  # noqa: BLE001 - cleanup must never mask the real result/error
                    logger.exception("twelvelabs_asset_delete_failed asset=%s", asset_id)


def _parse_task(task: dict[str, Any]) -> dict[str, Any]:
    result = task.get("result") or {}
    raw = result.get("data")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError as exc:
        raise VideoAnalysisError("twelvelabs_result_unparseable") from exc
    segments = parsed.get("ui_action") if isinstance(parsed, dict) else None
    if not isinstance(segments, list):
        raise VideoAnalysisError("twelvelabs_result_unparseable")
    duration = ((task.get("video_source") or {}).get("system_metadata") or {}).get("duration")
    return {
        "segments": segments,
        "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
        "truncated": result.get("finish_reason") == "length",
    }
