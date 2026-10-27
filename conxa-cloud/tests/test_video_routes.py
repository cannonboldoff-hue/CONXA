"""Recording-video analysis: POST/GET /api/v1/video/analyze + the TwelveLabs client."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conxa_core.config import settings
from app.main import app
from app.services import llm_metering, twelvelabs
from app.services.jobs import job_store

STUDIO_HEADER = {"X-Conxa-Client": settings.llm_proxy_client_header}
SEGMENTS = [{"start_time": 0.0, "end_time": 3.0, "metadata": {"action_kind": "click", "intent": "open the menu"}}]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_machines", False)
    monkeypatch.setattr(settings, "twelvelabs_api_key", "tl-test")
    monkeypatch.setattr(twelvelabs, "POLL_INTERVAL_S", 0)


@pytest.fixture
def api():
    # Context-managed so the event loop outlives each request and the job task keeps running.
    with TestClient(app) as c:
        yield c


def _wait_done(api, job_id: str) -> dict:
    for _ in range(100):
        body = api.get(f"/api/v1/video/analyze/{job_id}", headers=STUDIO_HEADER).json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_requires_known_client(api):
    assert api.post("/api/v1/video/analyze", content=b"x").status_code == 403


def test_503_without_key(api, monkeypatch):
    monkeypatch.setattr(settings, "twelvelabs_api_key", "")
    r = api.post("/api/v1/video/analyze", content=b"x", headers=STUDIO_HEADER)
    assert (r.status_code, r.json()["detail"]) == (503, "video_unavailable")


def test_413_and_empty_body(api, monkeypatch):
    monkeypatch.setattr(settings, "video_analyze_max_bytes", 10)
    r = api.post("/api/v1/video/analyze", content=b"x" * 20, headers=STUDIO_HEADER)
    # The request-size middleware answers first; the route's own check is defense in depth.
    assert r.status_code == 413
    r = api.post("/api/v1/video/analyze", content=b"", headers=STUDIO_HEADER)
    assert r.status_code == 400


def test_quota_blocks_before_upload(api, monkeypatch):
    monkeypatch.setattr(settings, "video_monthly_quota_seconds", 60)
    llm_metering.record_video_usage("wrk_local", seconds=61)
    r = api.post("/api/v1/video/analyze", content=b"x", headers=STUDIO_HEADER)
    assert (r.status_code, r.json()["detail"]) == (429, "video_quota_exceeded")


def test_happy_path_returns_segments_and_records_usage(api, monkeypatch):
    async def fake(data, filename="recording.mp4"):
        assert data == b"video-bytes"
        return {"segments": SEGMENTS, "duration_s": 42.0, "truncated": False}

    monkeypatch.setattr(twelvelabs, "analyze_video", fake)
    r = api.post("/api/v1/video/analyze?duration_s=40", content=b"video-bytes", headers=STUDIO_HEADER)
    assert r.status_code == 200, r.text
    done = _wait_done(api, r.json()["job_id"])
    assert done["status"] == "succeeded"
    assert done["result"]["segments"] == SEGMENTS
    assert llm_metering.get_video_usage("wrk_local")["seconds"] == 42.0


def test_failed_job_reports_error_code(api, monkeypatch):
    async def boom(data, filename="recording.mp4"):
        raise twelvelabs.VideoAnalysisError("twelvelabs_task_failed")

    monkeypatch.setattr(twelvelabs, "analyze_video", boom)
    job_id = api.post("/api/v1/video/analyze", content=b"x", headers=STUDIO_HEADER).json()["job_id"]
    done = _wait_done(api, job_id)
    assert (done["status"], done["error"]) == ("failed", "twelvelabs_task_failed")
    assert llm_metering.get_video_usage("wrk_local")["requests"] == 0


def test_other_workspace_cannot_read_job(api, monkeypatch):
    async def fake(data, filename="recording.mp4"):
        return {"segments": SEGMENTS, "duration_s": 1.0, "truncated": False}

    monkeypatch.setattr(twelvelabs, "analyze_video", fake)
    job_id = api.post("/api/v1/video/analyze", content=b"x", headers=STUDIO_HEADER).json()["job_id"]
    _wait_done(api, job_id)
    job_store.get(job_id).resource_id = "wrk_someone_else"
    assert api.get(f"/api/v1/video/analyze/{job_id}", headers=STUDIO_HEADER).status_code == 404


# --- TwelveLabs client ---------------------------------------------------------

def _mock_twelvelabs(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        twelvelabs.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


def _handler(calls, *, task_status="ready"):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if request.method == "POST" and path.endswith("/assets"):
            return httpx.Response(201, json={"_id": "asset1", "status": "processing"})
        if request.method == "GET" and path.endswith("/assets/asset1"):
            return httpx.Response(200, json={"_id": "asset1", "status": "ready"})
        if request.method == "POST" and path.endswith("/analyze/tasks"):
            body = json.loads(request.content)
            assert body["analysis_mode"] == "time_based_metadata"
            assert body["video"] == {"type": "asset_id", "asset_id": "asset1"}
            return httpx.Response(202, json={"task_id": "t1", "status": "pending"})
        if request.method == "GET" and path.endswith("/analyze/tasks/t1"):
            if task_status != "ready":
                return httpx.Response(200, json={"task_id": "t1", "status": task_status})
            return httpx.Response(200, json={
                "status": "ready",
                "video_source": {"system_metadata": {"duration": 12.5}},
                "result": {"data": json.dumps({"ui_action": SEGMENTS}), "finish_reason": "stop"},
            })
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(500)

    return handle


def test_analyze_video_parses_segments_and_deletes_asset(monkeypatch):
    calls: list = []
    _mock_twelvelabs(monkeypatch, _handler(calls))
    out = asyncio.run(twelvelabs.analyze_video(b"v"))
    assert out == {"segments": SEGMENTS, "duration_s": 12.5, "truncated": False}
    assert ("DELETE", "/v1.3/assets/asset1") in calls


def test_analyze_video_deletes_asset_when_task_fails(monkeypatch):
    calls: list = []
    _mock_twelvelabs(monkeypatch, _handler(calls, task_status="failed"))
    with pytest.raises(twelvelabs.VideoAnalysisError) as exc:
        asyncio.run(twelvelabs.analyze_video(b"v"))
    assert exc.value.code == "twelvelabs_task_failed"
    assert ("DELETE", "/v1.3/assets/asset1") in calls
