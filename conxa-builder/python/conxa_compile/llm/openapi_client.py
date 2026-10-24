"""High-level LLM abstraction for selector generation + runtime recovery.

Wraps conxa_compile.llm.client.call_llm with task-specific helpers.
call_llm internally uses the multi-provider router (if available) or falls back to single-endpoint config.
"""

from __future__ import annotations

from typing import Any

from conxa_core.config import settings
from conxa_compile.llm.client import call_llm
from services.llm_proxy_client import CloudUnreachable


def _call_llm_or_none(task: str, payload: dict[str, Any], timeout_ms: int, *, error_detail: list[str] | None) -> dict[str, Any] | None:
    """call_llm, but an infra failure (ProxyUnavailable/CloudUnreachable — the
    proxy client and cloud router already retried/failed-over before raising)
    degrades to None like every other failure mode these functions document,
    instead of propagating past callers that don't expect an exception here."""
    try:
        return call_llm(task, payload, timeout_ms, error_detail=error_detail)
    except CloudUnreachable:
        return None


class SelectorCandidate:
    """One LLM-generated selector candidate with rank + rationale."""

    __slots__ = ("selector", "rank", "rationale", "intent")

    def __init__(self, selector: str, rank: int, rationale: str = "", intent: str = ""):
        self.selector = selector
        self.rank = rank
        self.rationale = rationale
        self.intent = intent

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "rank": self.rank,
            "rationale": self.rationale,
            "intent": self.intent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelectorCandidate":
        return cls(
            selector=str(data.get("selector") or ""),
            rank=int(data.get("rank") or 0),
            rationale=str(data.get("rationale") or ""),
            intent=str(data.get("intent") or ""),
        )


def generate_selector_candidates(
    *,
    dom_snippet: str,
    element_bbox: dict[str, int],
    element_ancestors: list[dict[str, Any]],
    surrounding_text: str,
    action_type: str,
    target_dom: dict[str, Any] | None = None,
    a11y_node: dict[str, Any] | None = None,
    candidates_wanted: int | None = None,
    model: str | None = None,
    error_detail: list[str] | None = None,
) -> list[SelectorCandidate]:
    """Generate Playwright CSS selector candidates for one element.

    Returns empty list on LLM failure. Caller should validate candidates against
    the recorded DOM snapshot before accepting them.
    """
    n = candidates_wanted or settings.llm_selector_candidates
    input_dict: dict[str, Any] = {
        "dom_snippet": dom_snippet,
        "element_bbox": element_bbox,
        "ancestors": element_ancestors,
        "surrounding_text": surrounding_text,
        "action_type": action_type,
        "target_dom": target_dom or {},
        "candidates_wanted": n,
    }
    if a11y_node is not None:
        input_dict["a11y_node"] = a11y_node
    payload = {
        "task": "selector_generation",
        "model": model,
        "input": input_dict,
    }
    data = _call_llm_or_none(
        "selector_generation",
        payload,
        settings.llm_selector_timeout_ms,
        error_detail=error_detail,
    )
    if data is None:
        return []

    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list):
        return []

    out: list[SelectorCandidate] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        sel = str(item.get("selector") or "").strip()
        if not sel:
            continue
        out.append(SelectorCandidate.from_dict(item))
    return out


def resolve_element_recovery(
    *,
    semantic_description: str,
    original_bbox: dict[str, int] | None,
    original_ancestors: list[dict[str, Any]] | None,
    current_dom_snippet: str,
    action_type: str,
    model: str | None = None,
    error_detail: list[str] | None = None,
) -> dict[str, Any] | None:
    """Runtime Tier 3 recovery: locate an element on the current DOM via LLM.

    Returns {selector, confidence, reason} or None on failure.
    """
    payload = {
        "task": "recovery_resolve",
        "model": model,
        "input": {
            "semantic_description": semantic_description,
            "original_bbox": original_bbox or {},
            "original_ancestors": original_ancestors or [],
            "current_dom_snippet": current_dom_snippet,
            "action_type": action_type,
        },
    }
    data = _call_llm_or_none(
        "recovery_resolve",
        payload,
        settings.llm_selector_timeout_ms,
        error_detail=error_detail,
    )
    if data is None:
        return None
    sel = str(data.get("selector") or "").strip()
    if not sel:
        return None
    return {
        "selector": sel,
        "confidence": float(data.get("confidence") or 0.0),
        "reason": str(data.get("reason") or ""),
    }


def infer_workflow_review(
    *,
    steps: list[dict[str, Any]],
    page_urls: list[str],
    sibling_bindings: dict[str, list[str]],
    model: str | None = None,
    error_detail: list[str] | None = None,
    task_description: str = "",
) -> dict[str, Any] | None:
    """Single multimodal whole-workflow LLM call producing BOTH the workflow
    intent graph (goal + per-step intent) AND BUILD-25's compile-time review
    suggestions — merges what used to be two text-only calls
    (workflow_intent, workflow_semantics) into one. Each step in `steps` may
    carry `image_base64`/`image_mime` (the frame the vision-anchor stage
    already chose for that step) alongside its text context. Never selectors,
    never a change to compiled behavior."""
    payload = {
        "task": "workflow_review",
        "model": model,
        "input": {
            "steps": steps,
            "page_urls": page_urls,
            "sibling_bindings": sibling_bindings,
            **({"task_description": task_description} if task_description else {}),
        },
    }
    data = _call_llm_or_none(
        "workflow_review",
        payload,
        settings.llm_vision_timeout_ms,
        error_detail=error_detail,
    )
    if data is None:
        return None
    return {
        "goal": str(data.get("goal") or ""),
        "steps": list(data.get("steps") or []),
        "decision_points": list(data.get("decision_points") or []),
        "expected_end_state": dict(data.get("expected_end_state") or {}),
        "suggestions": list(data.get("suggestions") or []),
    }
