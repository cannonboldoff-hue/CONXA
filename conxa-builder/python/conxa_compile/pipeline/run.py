"""Phase 2 entrypoint — validate, clean, normalize, dedupe, enrich."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from conxa_core.models.events import RecordedEvent
from conxa_compile.pipeline.dedupe import (
    collapse_select_interaction_noise,
    dedupe_scroll_events,
    drop_superseded_focus_events,
)
from conxa_compile.pipeline.enrich import enrich_event
from conxa_compile.pipeline.selectors import canonicalize_selectors
from conxa_compile.pipeline.signals import apply_signal_budget
from conxa_compile.pipeline.text import collapse_ws, normalize_class_token
from conxa_compile.policy.bundle import get_policy_bundle

PIPELINE_VERSION = "2.0.0"


def _clean_target(target: dict[str, Any]) -> dict[str, Any]:
    t = dict(target)
    t["inner_text"] = collapse_ws(str(t.get("inner_text", "")), max_len=2000)
    raw_classes = t.get("classes") or []
    cleaned: list[str] = []
    for c in raw_classes:
        nc = normalize_class_token(str(c))
        if nc and nc not in cleaned:
            cleaned.append(nc)
    cleaned.sort()
    t["classes"] = cleaned
    return t


def _clean_semantic(sem: dict[str, Any]) -> dict[str, Any]:
    s = dict(sem)
    s["normalized_text"] = collapse_ws(str(s.get("normalized_text", "")), max_len=500).lower()
    return s


def _clean_context(ctx: dict[str, Any]) -> dict[str, Any]:
    c = dict(ctx)
    sibs = []
    for x in c.get("siblings") or []:
        sx = collapse_ws(str(x), max_len=120)
        if sx and sx not in sibs:
            sibs.append(sx)
    c["siblings"] = sibs
    c["parent"] = collapse_ws(str(c.get("parent", "")), max_len=200)
    if c.get("form_context"):
        c["form_context"] = collapse_ws(str(c["form_context"]), max_len=200)
    return c


def _clean_one(ev: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    row = dict(ev)
    row["target"] = _clean_target(dict(row.get("target") or {}))
    selectors, selector_meta = canonicalize_selectors(dict(row.get("selectors") or {}), policy)
    row["selectors"] = selectors
    extras = dict(row.get("extras") or {})
    extras.update(selector_meta)
    row["extras"] = extras
    row["semantic"] = _clean_semantic(dict(row.get("semantic") or {}))
    row["context"] = _clean_context(dict(row.get("context") or {}))
    if row.get("page"):
        p = dict(row["page"])
        p["title"] = collapse_ws(str(p.get("title", "")), max_len=300)
        row["page"] = p
    return apply_signal_budget(row, policy)


def _infer_missing_input_type(ev: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Fill semantic.input_type from policy regex when the recorder left it blank.

    No LLM: compile intent comes from the workflow-intent graph (or per-step
    intent fallback). Human Edit 1-click fix still uses enrich_semantic.
    """
    sem = ev.get("semantic")
    if not isinstance(sem, dict) or sem.get("input_type"):
        return ev
    sig = policy.get("signals") if isinstance(policy.get("signals"), dict) else {}
    detectors = sig.get("input_type_detectors") or []
    if not isinstance(detectors, list):
        return ev
    target = ev.get("target") if isinstance(ev.get("target"), dict) else {}
    norm = str(sem.get("normalized_text") or target.get("inner_text") or "").lower()
    inferred = ""
    for det in detectors:
        if not isinstance(det, dict):
            continue
        pat = str(det.get("regex") or "")
        val = str(det.get("value") or "").strip()
        if pat and val and re.search(pat, norm, re.I):
            inferred = val
            break
    if not inferred:
        return ev
    out = dict(ev)
    out["semantic"] = {**sem, "input_type": inferred}
    return out


def _parse_scroll_position(raw: object) -> tuple[int, int]:
    text = str(raw or "").strip()
    if not text:
        return 0, 0
    left, _, right = text.partition(",")
    try:
        return int(float(left.strip() or 0)), int(float(right.strip() or 0))
    except ValueError:
        return 0, 0


def _annotate_scroll_amounts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_y = 0
    for ev in events:
        row = dict(ev)
        visual = dict(row.get("visual") or {})
        _, current_y = _parse_scroll_position(visual.get("scroll_position"))
        extras = dict(row.get("extras") or {})
        if str((row.get("action") or {}).get("action") or "").strip().lower() == "scroll":
            extras["scroll_amount"] = current_y - last_y
        row["extras"] = extras
        out.append(row)
        last_y = current_y
    return out


def _action_name(ev: dict[str, Any]) -> str:
    action = ev.get("action") if isinstance(ev.get("action"), dict) else {}
    return str((action or {}).get("action") or "").strip().lower()


def _has_weak_visual_bbox(ev: dict[str, Any]) -> bool:
    visual = ev.get("visual") if isinstance(ev.get("visual"), dict) else {}
    bbox = visual.get("bbox") if isinstance(visual.get("bbox"), dict) else {}
    try:
        return int(bbox.get("w") or 0) < 2 or int(bbox.get("h") or 0) < 2
    except (TypeError, ValueError):
        return True


def _drop_non_actionable_hover_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ev
        for ev in events
        if not (_action_name(ev) == "hover" and _has_weak_visual_bbox(ev))
    ]


def _reorder_by_timestamp(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recorded events normally arrive in causal order, but a native-dialog answer is queued
    from a different thread than the click that triggered it (session.py's DialogSyncServer
    resolves on its own request thread, while the triggering click's bridge.js payload only
    reaches the recorder once the page's JS thread unblocks after the dialog closes) — so the
    two can land in `_pending_payloads` in either order even though their own timestamps are
    unambiguous. A stable sort on each event's own `action.timestamp` restores wall-clock order
    without disturbing events that were already in order (the overwhelming majority). A missing
    or unparseable timestamp sorts as if it arrived at its original position (min of all valid
    timestamps minus its index), never reordered past events that do have one."""
    def _key(pair: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        idx, ev = pair
        raw = ((ev.get("action") or {}).get("timestamp"))
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return (float("-inf"), idx)
        return (ts, idx)

    return [ev for _, ev in sorted(enumerate(events), key=_key)]


def run_pipeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bundle = get_policy_bundle()
    policy = bundle.data
    # BUILD-38: answers the person gave while recording ("this value changes each run"). A no-op
    # when there are none, so a recording without hints compiles exactly as before.
    session_id = next((str((e.get("extras") or {}).get("session_id") or "") for e in events if (e.get("extras") or {}).get("session_id")), "")
    if session_id:
        from conxa_compile.recorder.hints import apply_to_events

        events = apply_to_events(session_id, [dict(e) for e in events])
    validated: list[dict[str, Any]] = []
    for row in _reorder_by_timestamp(events):
        validated.append(RecordedEvent.model_validate(row).model_dump(mode="json"))
    cleaned = [_clean_one(e, policy) for e in _drop_non_actionable_hover_events(validated)]
    typed = [_infer_missing_input_type(e, policy) for e in cleaned]
    # collapse_select_interaction_noise runs first: fewer noise events left for
    # drop_superseded_focus_events' own lookahead to have to skip past.
    deduped = dedupe_scroll_events(
        drop_superseded_focus_events(collapse_select_interaction_noise(typed))
    )
    scroll_annotated = _annotate_scroll_amounts(deduped)
    return [
        enrich_event(e, pipeline_version=PIPELINE_VERSION, ordinal=i) for i, e in enumerate(scroll_annotated)
    ]
