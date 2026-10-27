"""A3 video rows, the result cache, the Studio upload client and Track A orchestration."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from conxa_compile.video import signals, track_a
from services import video_analysis_client as vac


def seg(start, end, **meta):
    return {"start_time": start, "end_time": end, "metadata": meta}


# --- rows -------------------------------------------------------------------------

def test_rows_normalise_sort_and_drop_malformed():
    rows = signals.rows_from_segments([
        seg(5, 7, action_kind="Type", visible_target=" search box ", intent="find invoice", outside_page=False),
        seg(0, 2, action_kind="download", outside_page=True),
        seg(8, 7, action_kind="click"),            # end before start
        {"start_time": "x", "end_time": 3},        # not numbers
        "garbage",
        seg(9, 10, action_kind="teleport"),        # unknown kind, on page
        seg(11, 12, action_kind="teleport", outside_page=True),
    ])
    assert [(r["start_s"], r["action_kind"]) for r in rows] == [
        (0, "download"), (5, "type"), (9, "unknown"), (11, "wait")]
    assert rows[1]["visible_target"] == "search box" and rows[1]["screen_after"] == ""


def test_rows_clip_to_video_duration():
    rows = signals.rows_from_segments([seg(0, 30, action_kind="wait"), seg(40, 50, action_kind="click")], duration_s=10)
    assert [(r["start_s"], r["end_s"]) for r in rows] == [(0, 10)]


def test_cache_only_matches_its_fingerprint(tmp_path):
    signals.write_cache(tmp_path, "abc", {"segments": [seg(0, 1)], "duration_s": 3.0})
    assert signals.read_cache(tmp_path, "abc")["segments"] == [seg(0, 1)]
    assert signals.read_cache(tmp_path, "other") is None


# --- Studio client ----------------------------------------------------------------

class FakeResp:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, detail=""):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps({"detail": detail}).encode()))


def make_client(monkeypatch, script, **kw):
    """script: results/exceptions returned by successive urlopen calls."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url))
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    waits = []
    client = vac.VideoAnalysisClient("https://cloud.test", lambda: "tok", on_wait=waits.append,
                                     sleep=lambda s: None, poll_interval_s=0, **kw)
    return client, calls, waits


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "prepared.mp4"
    p.write_bytes(b"vid")
    return p


def test_client_uploads_then_polls_until_succeeded(monkeypatch, video):
    result = {"segments": [seg(0, 1)], "duration_s": 3.0}
    client, calls, waits = make_client(monkeypatch, [
        {"job_id": "j1"}, {"status": "running"}, {"status": "succeeded", "result": result}])
    assert client.analyze(video, 3.0) == result
    assert calls[0][0] == "POST" and "/api/v1/video/analyze?duration_s=3.0" in calls[0][1]
    assert calls[1] == ("GET", "https://cloud.test/api/v1/video/analyze/j1")
    assert any(w["state"] == "watching" for w in waits)


def test_client_retries_5xx_and_network_errors_then_succeeds(monkeypatch, video):
    client, calls, waits = make_client(monkeypatch, [
        http_error(503, "video_unavailable"), urllib.error.URLError("down"),
        {"job_id": "j1"}, {"status": "succeeded", "result": {"segments": []}}])
    assert client.analyze(video)["segments"] == []
    assert [w["state"] for w in waits].count("retrying") == 2


def test_client_gives_up_as_unavailable_after_the_wait_budget(monkeypatch, video):
    ticks = iter(range(0, 1000, 3))     # each clock read advances 3s
    client, _, _ = make_client(monkeypatch, [http_error(503)] * 10, max_wait_s=5, clock=lambda: next(ticks))
    with pytest.raises(vac.VideoUnavailable):
        client.analyze(video)


def test_client_resubmits_when_the_job_is_lost(monkeypatch, video):
    client, calls, _ = make_client(monkeypatch, [
        {"job_id": "j1"}, http_error(404, "unknown_job_id"),
        {"job_id": "j2"}, {"status": "succeeded", "result": {"segments": []}}])
    client.analyze(video)
    assert sum(1 for m, _ in calls if m == "POST") == 2


def test_client_does_not_retry_quota_or_failed_analysis(monkeypatch, video):
    client, _, _ = make_client(monkeypatch, [http_error(429, "video_quota_exceeded")])
    with pytest.raises(vac.VideoBlocked) as blocked:
        client.analyze(video)
    assert blocked.value.code == "video_quota_exceeded"

    client, _, _ = make_client(monkeypatch, [{"job_id": "j"}, {"status": "failed", "error": "twelvelabs_task_failed"}])
    with pytest.raises(vac.VideoAnalysisFailed) as failed:
        client.analyze(video)
    assert failed.value.code == "twelvelabs_task_failed"


# --- Track A ------------------------------------------------------------------------

class FakePrepared:
    def __init__(self, path):
        self.path, self.fingerprint, self.splice_map, self.duration_ms = path, "fp1", [{"kind": "piece"}], 8000


class CountingClient:
    def __init__(self):
        self.calls = 0

    def analyze(self, path, duration_s=0.0):
        self.calls += 1
        return {"segments": [seg(1, 2, action_kind="click", intent="open menu")], "duration_s": 8.0}


def test_track_a_second_run_hits_the_cache_without_calling_pegasus(monkeypatch, tmp_path):
    monkeypatch.setattr(track_a, "prepare_video", lambda d, blur_all=False: FakePrepared(tmp_path / "prepared.mp4"))
    client = CountingClient()
    first = track_a.run_track_a(tmp_path, client)
    second = track_a.run_track_a(tmp_path, client)
    assert client.calls == 1
    assert (first["from_cache"], second["from_cache"]) == (False, True)
    assert second["rows"][0]["intent"] == "open menu" and second["fingerprint"] == "fp1"
