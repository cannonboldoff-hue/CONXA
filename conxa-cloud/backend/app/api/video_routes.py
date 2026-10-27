"""Recording-video analysis for the Build Studio (Pegasus via TwelveLabs).

Studio uploads one prepared recording video (raw ``application/octet-stream``,
same pattern as the installer upload — ``python-multipart`` is not installed),
gets a job id back, and polls ``GET /video/analyze/{job_id}`` for the segments.
The TwelveLabs key, metering and asset cleanup all live here; Studio never sees
the key. Jobs are in-memory (``services/jobs.py``), so a restart loses them and
Studio simply resubmits on 404.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from conxa_core.config import settings
from app.api.deps import current_principal, entitlement_http_error
from app.api.llm_proxy_routes import _require_known_client
from app.api.machine_binding import register_request_machine
from app.services import llm_metering, twelvelabs
from app.services.entitlements import ensure_trial_active
from app.services.jobs import enqueue_job, job_store

router = APIRouter(prefix="/video", tags=["video"], include_in_schema=False)
logger = logging.getLogger(__name__)


def _duration_param(request: Request) -> float:
    try:
        return max(0.0, float(request.query_params.get("duration_s", "0")))
    except ValueError:
        return 0.0


@router.post("/analyze")
async def post_analyze(request: Request) -> dict[str, Any]:
    _require_known_client(request)
    principal = current_principal(request)
    if not settings.twelvelabs_api_key:
        raise HTTPException(status_code=503, detail="video_unavailable")
    try:
        register_request_machine(request, principal)
        ensure_trial_active(principal)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc
    if llm_metering.video_quota_exceeded(principal.workspace_id, settings.video_monthly_quota_seconds):
        raise HTTPException(status_code=429, detail="video_quota_exceeded")

    max_bytes = settings.video_analyze_max_bytes
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        raise HTTPException(status_code=413, detail="video_too_large")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty_video_body")
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="video_too_large")

    workspace_id = principal.workspace_id
    claimed_s = _duration_param(request)

    async def _run() -> dict[str, Any]:
        result = await twelvelabs.analyze_video(body)
        llm_metering.record_video_usage(workspace_id, seconds=result.get("duration_s") or claimed_s)
        return result

    # resource_id carries the owning workspace so GET can enforce ownership
    # (job_routes.py's generic reads don't check the caller).
    job = await enqueue_job("video_analyze", _run, resource_id=workspace_id)
    return {"job_id": job.job_id}


@router.get("/analyze/{job_id}")
def get_analyze(job_id: str, request: Request) -> dict[str, Any]:
    _require_known_client(request)
    principal = current_principal(request)
    job = job_store.get(job_id)
    if job is None or job.kind != "video_analyze" or job.resource_id != principal.workspace_id:
        raise HTTPException(status_code=404, detail="unknown_job_id")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "error": job.user_error,
        "result": job.result if job.status == "succeeded" else None,
    }
