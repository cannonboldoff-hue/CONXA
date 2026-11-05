"""The video-analysis job record (Engine v2, C2): ``sessions/<id>/video_job.json``.

Pegasus is required, so a wait on the video service must outlive one request and one Studio session. The
record keeps the cloud job id, how many tries it has taken and when the next one is due, so closing
Studio mid-wait polls the same job on the next start instead of uploading the video again. The video
cache (``signals.py``, keyed by the prepared video's fingerprint) is untouched and is still checked first.

States: ``polling`` (a cloud job exists), ``waiting_cloud`` (the budget ran out; next try is scheduled),
``done``, ``blocked`` (quota, trial, signed out: only a person can fix it), ``failed`` (the cloud could not
read the video, or the 24-hour wait ended)."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE = "video_job.json"
ACTIVE = ("polling", "waiting_cloud")
# Seconds to wait after the 1st, 2nd, 3rd, 4th failed attempt; every later one waits the last value.
SCHEDULE_S = (60, 120, 300, 600, 1800)
GIVE_UP_AFTER_S = 24 * 3600


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(session_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(session_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_active(session_dir: Path) -> bool:
    rec = load(session_dir)
    return bool(rec) and rec.get("state") in ACTIVE


class VideoJob:
    """Writes the record as the wait moves. Every method is safe to call from the one watching thread."""

    def __init__(self, session_dir: Path, *, clock: Any = _now) -> None:
        self._dir = Path(session_dir)
        self._clock = clock
        self.rec: dict[str, Any] = load(self._dir) or {}

    def _save(self, **fields: Any) -> None:
        self.rec.update(fields, updated_at=_iso(self._clock()))
        tmp = self._dir / (FILE + ".tmp")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.rec, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._dir / FILE)
        except OSError:
            pass  # a record is advice for a restart, never a reason to stop the wait

    def resume_id(self, fingerprint: str) -> str | None:
        """The saved cloud job id, when it belongs to this exact prepared video and is still wanted."""
        rec = self.rec
        if rec.get("fingerprint") == fingerprint and rec.get("state") in ACTIVE and rec.get("job_id"):
            return str(rec["job_id"])
        return None

    def submitted(self, fingerprint: str, job_id: str) -> None:
        if self.rec.get("fingerprint") != fingerprint or not self.rec.get("first_try_ts"):
            # A different video, or the first submit: the wait and its count start here.
            self.rec.update(attempts=0, first_try_ts=self._clock(), first_try=_iso(self._clock()))
        self._save(fingerprint=fingerprint, job_id=job_id, state="polling", next_try="")

    def waiting(self, reason: str) -> float | None:
        """The budget ran out. Schedule the next try; returns seconds to wait, or None once the 24 hours are up
        (the record is then ``failed``)."""
        now = self._clock()
        attempts = int(self.rec.get("attempts") or 0) + 1
        if now - float(self.rec.setdefault("first_try_ts", now)) >= GIVE_UP_AFTER_S:
            self._save(state="failed", attempts=attempts, last_error=str(reason)[:200], next_try="")
            return None
        delay = SCHEDULE_S[min(attempts - 1, len(SCHEDULE_S) - 1)]
        self._save(state="waiting_cloud", attempts=attempts, last_error=str(reason)[:200], next_try=_iso(now + delay))
        return float(delay)

    def done(self) -> None:
        self._save(state="done", next_try="")

    def blocked(self, code: str) -> None:
        self._save(state="blocked", blocked_code=str(code)[:80], next_try="")

    def failed(self, reason: str) -> None:
        self._save(state="failed", last_error=str(reason)[:200], next_try="")
