"""Build Studio -> Conxa Cloud client for recording-video analysis (Pegasus).

Uploads the prepared video as a raw ``application/octet-stream`` body (same shape
as the installer upload), then polls the job. Pegasus is a hard dependency, so a
transient failure (5xx, no network, a job lost to a cloud restart) is retried
with backoff and reported through ``on_wait`` rather than failing; only after
``max_wait_s`` does it raise ``VideoUnavailable`` so the UI can offer Retry.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from services.machine_id import get_machine_id_hash

_RETRY_STATUSES = {502, 503, 504}


class VideoUnavailable(RuntimeError):
    """Analysis could not complete within the wait budget (cloud down / Pegasus slow). Retryable."""


class VideoAnalysisFailed(RuntimeError):
    """The cloud accepted the video but analysis failed; ``code`` is the cloud's error string."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VideoBlocked(RuntimeError):
    """Entitlement, trial or quota blocked the request; retrying will not help."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Retry(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _JobLost(Exception):
    pass


class VideoAnalysisClient:
    def __init__(
        self,
        cloud_api: str,
        token_provider: Callable[[], str],
        *,
        client_header: str = "build-studio",
        on_wait: Callable[[dict[str, Any]], None] | None = None,
        poll_interval_s: float = 3.0,
        max_wait_s: float = 600.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cloud_api = cloud_api.rstrip("/")
        self._token_provider = token_provider
        self._client_header = client_header
        self._on_wait = on_wait
        self._poll_interval_s = poll_interval_s
        self._max_wait_s = max_wait_s
        self._sleep = sleep
        self._clock = clock
        self._auth_failures = 0

    def analyze(
        self,
        video_path: Path,
        duration_s: float = 0.0,
        *,
        resume_job_id: str | None = None,
        on_job_id: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Blocking. Returns the cloud result: {segments, duration_s, truncated}.

        ``resume_job_id`` polls a job an earlier run already submitted, so a restart does not upload the video
        again; if the cloud no longer knows it, the video is submitted afresh as before. ``on_job_id`` is called
        with each new job id the moment it exists, so the caller can save it (video/job.py)."""
        self._auth_failures = 0
        started = self._clock()
        data: bytes | None = None
        resubmits = 0
        job_id = resume_job_id
        while True:
            if job_id is None:
                if data is None:
                    data = Path(video_path).read_bytes()
                job_id = self._retrying(lambda d=data: self._submit(d, duration_s), started)
                if on_job_id:
                    on_job_id(job_id)
            try:
                return self._poll(job_id, started)
            except _JobLost:
                resubmits += 1
                if resubmits > 2:
                    raise VideoUnavailable("analysis job was lost repeatedly") from None
                self._notify("retrying", started, "job lost, resubmitting")
                job_id = None

    # -- internals -----------------------------------------------------------

    def _notify(self, state: str, started: float, reason: str = "") -> None:
        if self._on_wait:
            self._on_wait({"state": state, "elapsed_s": round(self._clock() - started, 1), "reason": reason})

    def _retrying(self, fn: Callable[[], Any], started: float) -> Any:
        delay = 2.0
        while True:
            try:
                return fn()
            except _Retry as exc:
                if self._clock() - started > self._max_wait_s:
                    raise VideoUnavailable(exc.reason) from None
                self._notify("retrying", started, exc.reason)
                self._sleep(delay)
                delay = min(delay * 2, 30.0)

    def _call(self, method: str, path: str, *, data: bytes | None = None, timeout: float = 60.0) -> dict[str, Any]:
        req = urllib.request.Request(f"{self._cloud_api}/api/v1{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/octet-stream")
        req.add_header("X-Conxa-Client", self._client_header)
        req.add_header("Authorization", f"Bearer {self._token_provider()}")
        machine_hash = get_machine_id_hash()
        if machine_hash:
            req.add_header("X-Conxa-Machine", machine_hash)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = str(json.loads(exc.read().decode("utf-8")).get("detail") or "")
            except Exception:  # noqa: BLE001
                pass
            if exc.code == 404 and method == "GET":
                raise _JobLost() from exc
            if exc.code == 401:
                self._auth_failures += 1
                if self._auth_failures > 1:
                    raise VideoBlocked("unauthorized") from exc
                raise _Retry("sign-in refreshed, retrying") from exc
            if exc.code in _RETRY_STATUSES:
                raise _Retry(detail or f"cloud answered {exc.code}") from exc
            raise VideoBlocked(detail or f"http_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _Retry(f"cloud unreachable: {exc}") from exc

    def _submit(self, data: bytes, duration_s: float) -> str:
        out = self._call("POST", f"/video/analyze?duration_s={duration_s:.1f}", data=data, timeout=300.0)
        return str(out["job_id"])

    def _poll(self, job_id: str, started: float) -> dict[str, Any]:
        while True:
            body = self._retrying(lambda: self._call("GET", f"/video/analyze/{job_id}"), started)
            status = body.get("status")
            if status == "succeeded":
                return body["result"]
            if status == "failed":
                raise VideoAnalysisFailed(str(body.get("error") or "analysis_failed"))
            if self._clock() - started > self._max_wait_s:
                raise VideoUnavailable("timed out waiting for analysis")
            self._notify("watching", started)
            self._sleep(self._poll_interval_s)
