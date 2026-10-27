"""Stamp what the video model saw onto the steps it matched (understanding-first pipeline).

Reads ``merged_timeline.json`` beside the recording (written when the recording was watched) and,
for each step whose source event has a Pegasus segment, sets ``video_context`` and uses Pegasus's
intent as the step's plain-words description. A step Pegasus missed, or a recording that was
never watched, is left exactly as the compiler built it. Words only: nothing here touches a
target, selector or signal.
"""

from __future__ import annotations

import json
from pathlib import Path

from conxa_core.models.skill_spec import SkillStep, VideoContext

MERGED_FILE = "merged_timeline.json"


def apply_video_context(steps: list[SkillStep], session_root: Path) -> int:
    """Returns how many steps got a video context. Never raises: a bad file means no context."""
    try:
        rows = json.loads((Path(session_root) / MERGED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    by_event = {
        r["event_id"]: r["segment"] for r in rows
        if isinstance(r, dict) and r.get("kind") == "event" and r.get("event_id") is not None and isinstance(r.get("segment"), dict)
    }
    stamped = 0
    for step in steps:
        seg = by_event.get(step.source_event) if step.source_event is not None else None
        if not seg:
            continue
        ctx = VideoContext(
            visible_target=str(seg.get("visible_target") or ""), screen_before=str(seg.get("screen_before") or ""),
            screen_after=str(seg.get("screen_after") or ""), purpose=str(seg.get("purpose") or ""),
        )
        if any(ctx.model_dump().values()):
            step.video_context = ctx
            stamped += 1
        intent = str(seg.get("intent") or "").strip()
        if intent:
            step.semantic_description = intent
    return stamped
