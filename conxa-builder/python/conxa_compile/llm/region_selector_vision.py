"""Vision-based selector generation for the re-target wizard's "draw a new region" path.

When a user draws a fresh bounding box on the Pick-element screenshot, there is no stored
per-element geometry to map that box to a DOM node (only the originally-recorded element has a
bbox — see `editor/retarget.py` module docstring). A text-only prompt can't resolve this either:
it has no way to relate pixel coordinates to a position in the DOM. This module sends the full
screenshot — with the drawn region highlighted, same as `anchor_vision_llm` does for anchors —
together with the recorded DOM snippet to a vision LLM, which can *see* which element the box
points to and read the DOM to emit selectors for it.

This is a second, narrow exception to "LLM does not write selector strings on the primary compile
path" (see CLAUDE.md Key Invariants) alongside the 1-click-fix API's `selector_regeneration.py` —
both are user-initiated re-compile paths, not part of the primary compiler. Candidates returned
here are validated/pruned by the caller (`editor/retarget.py`) exactly like every other selector
source: uniqueness against the DOM snapshot, then the durability floor.
"""

from __future__ import annotations

import base64
from typing import Any

from conxa_core.config import settings
from conxa_core.storage import selector_cache
from conxa_compile.llm.anchor_vision_llm import (
    VisionAnchorGenerationError,
    _apply_bbox_highlight,
    _vision_cfg,
    resolve_screenshot_path,
)
from conxa_compile.llm.client import call_llm
from conxa_compile.llm.openapi_client import SelectorCandidate
from conxa_compile.llm.selector_regeneration import _dom_snippet_for_llm
from conxa_compile.policy.bundle import get_policy_bundle
from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded


def compile_selectors_for_region(
    *,
    matching_event: dict[str, Any],
    session_id: str,
    bbox: dict[str, int],
    dom_snapshot: str | None,
    action_type: str = "",
    policy: dict[str, Any] | None = None,
) -> list[SelectorCandidate]:
    """Identify the element under a drawn region and return selector candidates for it.

    Returns [] on any failure (missing screenshot, vision call failure, empty response) — the
    caller treats an empty list the same as any other source that found nothing, surfacing the
    wizard's "re-pick element" prompt rather than raising.
    """
    pol: dict[str, Any] = policy if isinstance(policy, dict) else get_policy_bundle().data
    vcfg = _vision_cfg(pol)
    if not bool(vcfg.get("enabled", True)):
        return []

    visual = matching_event.get("visual") if isinstance(matching_event.get("visual"), dict) else {}
    rel_path = str(visual.get("full_screenshot") or "").strip()
    session_root = (settings.data_dir / "sessions" / session_id).resolve()
    if not rel_path:
        # A recording made since the understanding-first pipeline stores no screenshots: cut the one
        # frame this fix needs from the local video, now, the first time a person uses it.
        try:
            from conxa_compile.video.frame_at import event_frame

            rel_path = event_frame(session_root, matching_event) or ""
        except RuntimeError:
            return []
        if not rel_path:
            return []
    try:
        abs_path = resolve_screenshot_path(session_root, rel_path)
    except VisionAnchorGenerationError:
        return []
    if not abs_path.is_file():
        return []

    raw_bytes = abs_path.read_bytes()
    viewport = str(visual.get("viewport") or "")
    highlight_alpha = float(vcfg.get("highlight_alpha", 0.35))
    image_bytes = _apply_bbox_highlight(raw_bytes, bbox, viewport, highlight_alpha=highlight_alpha)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    dom_snippet = _dom_snippet_for_llm(dom_snapshot or "")

    snapshot_hash = str((matching_event.get("snapshot") or {}).get("dom_hash") or "")
    effective_model = settings.llm_vision_model or "default"
    if snapshot_hash:
        cached = selector_cache.get(snapshot_hash, bbox, effective_model)
        if cached:
            return [SelectorCandidate.from_dict(c) for c in cached]

    user_text = (
        "Look at this UI screenshot. The red highlighted region is the element to target.\n\n"
        f"Viewport (CSS px): {viewport or 'unknown'}.\n"
        f"Highlighted region (CSS px): x={bbox.get('x')}, y={bbox.get('y')}, "
        f"w={bbox.get('w')}, h={bbox.get('h')}.\n"
        f"Action to perform on it: {action_type or 'unknown'}.\n\n"
        "Below is the page's DOM HTML. Find the element the highlighted region points to, then "
        "return Playwright CSS selectors that uniquely match exactly that element.\n\n"
        f"DOM:\n{dom_snippet}"
    )

    payload = {
        "user_text": user_text,
        "image_base64": image_b64,
        "image_mime": "image/jpeg",
    }
    err_lines: list[str] = []
    try:
        data = call_llm(
            "region_selector",
            payload,
            settings.llm_vision_timeout_ms,
            error_detail=err_lines,
        )
        if not isinstance(data, dict):
            return []

        raw_candidates = data.get("candidates")
        if not isinstance(raw_candidates, list):
            return []

        candidates: list[SelectorCandidate] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            sel = str(item.get("selector") or "").strip()
            if not sel:
                continue
            candidates.append(SelectorCandidate.from_dict(item))
    except (QuotaExceeded, EntitlementBlocked, CloudUnreachable):
        # Infrastructure failures get specific, actionable messages upstream
        # (cmd_retarget_preview) — let them propagate instead of swallowing them here.
        raise
    except Exception:
        # Anything else (a malformed candidate from the vision LLM, a cache hiccup, etc.)
        # honors this function's documented "[] on any failure" contract instead of
        # surfacing as a generic internal-error crash.
        return []

    if snapshot_hash and candidates:
        selector_cache.set(snapshot_hash, bbox, effective_model, [c.to_dict() for c in candidates])

    return candidates
