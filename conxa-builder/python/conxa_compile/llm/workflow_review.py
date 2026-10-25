"""Merged workflow-intent + second-opinion LLM call (BUILD-26).

Used to be two whole-workflow text-only calls: workflow_intent.py (goal +
per-step intent, ran before the per-step compile loop) and
workflow_semantics.py (BUILD-25's second opinion, ran after every step was
compiled). Both were deliberately fed tiny payloads — no DOM, no screenshots,
truncated text — a cost/latency choice, not a quality one.

This module merges them into one multimodal call that runs where the old
second-opinion call did (after every step, including its vision anchor, is
compiled), so it can see the full compiled context AND, per step, the
screenshot the vision-anchor stage already chose as the click target —
compiler/build.py builds that image side-map while step/event alignment still
holds and threads it in by step_key. See compiler/build.py's
`_review_inputs`/`_apply_workflow_review_to_steps` for how the request is
built and the response applied.

Consequence of merging: the intent graph (goal, per-step semantic_description,
SkillPackage.intent_graph) is no longer available before the per-step build
loop runs. Vision-anchor selection and each step's `intent` field fall back to
the recorder's own heuristic (normalize_compiler_intent's content-derived
path) instead of an LLM-resolved token — build.py backfills `step.intent` from
this call's result afterward where the heuristic left it blank/generic, but
the anchor-selection prompt has already run by then; that quality loss is real
and not recovered.

Both original modules' parsing/validation are reused unchanged:
workflow_intent.py's `_graph_from_raw` for the goal/steps/decision_points
section, workflow_semantics.py's `_validate_findings` for the suggestions
section — only the orchestration (one call instead of two) is new.

llm_semantic_suggestions_enabled gates only the `suggestions` section, exactly
as it gated the old standalone second-opinion call — the intent graph still
generates unconditionally, matching its old ungated behavior. A failure/empty
response degrades the whole thing to (WorkflowIntentGraph(), []) — the
rules-only compile — never raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conxa_core.config import settings
from conxa_core.models.skill_spec import WorkflowIntentGraph

from conxa_compile.llm.llm_cache import cache_key, read_cached, write_cached
from conxa_compile.llm.openapi_client import infer_workflow_review
from conxa_compile.llm.workflow_intent import _graph_from_raw
from conxa_compile.llm.workflow_semantics import _validate_findings

_NAMESPACE = "workflow_review"
_CACHE_VERSION = 2
# Bump when the review prompt in openapi_client.infer_workflow_review changes, so a fresh
# recording after the change is not served an answer the old prompt produced.
_PROMPT_VERSION = 4  # 4: the call also answers the description check and loop wording (BUILD-55)


def _sidecar_read(sidecar: Path | None, key: str) -> dict[str, Any] | None:
    if sidecar is None:
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = data.get("raw") if isinstance(data, dict) and data.get("key") == key else None
    return raw if isinstance(raw, dict) else None


def _sidecar_write(sidecar: Path | None, key: str, raw: dict[str, Any]) -> None:
    if sidecar is None:
        return
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({"key": key, "raw": raw}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # a pin that can't be written is just a cache miss next time


def build_workflow_review(
    steps_context: list[dict[str, Any]],
    *,
    page_urls: list[str],
    sibling_bindings: dict[str, list[str]] | None = None,
    model: str | None = None,
    error_detail: list[str] | None = None,
    task_description: str = "",
    sidecar: Path | None = None,
    review_inputs: dict[str, Any] | None = None,
    loop_candidates: list[dict[str, Any]] | None = None,
    extras_out: dict[str, Any] | None = None,
) -> tuple[WorkflowIntentGraph, list[dict[str, Any]]]:
    """One multimodal LLM call producing (workflow intent graph, BUILD-25
    findings). Returns (WorkflowIntentGraph(), []) on any failure or an empty
    response — never raises. `suggestions` is additionally forced to [] when
    llm_semantic_suggestions_enabled is False, even on a successful call.

    BUILD-55: the same answer may also carry `understanding` (the description check) and
    `loop_copy` (wording for loop suggestions). Neither is part of the return value; they are
    written into ``extras_out`` when given -- ``understanding`` stamped with the description it
    was written for (``for_task``), so a later edit to the description makes it stale."""
    if extras_out is not None:
        extras_out.update(understanding=None, loop_copy=[])
    if not steps_context:
        return WorkflowIntentGraph(), []

    step_keys_in_workflow = {str(s.get("key") or "") for s in steps_context}
    # BUILD-29: hash the text fields only — steps_context may carry a base64 image per step
    # (megabytes, hashed for no discriminating power beyond what the rest of this same step
    # dict already captures) and, more importantly, _review_inputs's image-budget trim can
    # drop a subset of images without changing anything else about a step. Keying on the
    # untrimmed images would let two differently-trimmed requests collide on one cache entry.
    steps_for_key = [
        {k: v for k, v in s.items() if k not in ("image_base64", "image_mime")}
        for s in steps_context
    ]
    task = (task_description or "").strip()[:300]
    # Only when present, so a compile with no description keeps hitting entries written before
    # this field existed.
    extra_key = {"task_description": task} if task else {}
    if model:
        extra_key["model"] = model
    # Same rule: only when present, so a compile that asks for neither keeps its old cache entries.
    if task and review_inputs and review_inputs.get("candidates"):
        extra_key["review_candidates"] = review_inputs["candidates"]
    if loop_candidates:
        extra_key["loop_candidates"] = loop_candidates
    key = cache_key(
        _CACHE_VERSION,
        steps=steps_for_key,
        page_urls=page_urls,
        sibling_bindings=sibling_bindings or {},
        prompt_version=_PROMPT_VERSION,
        **extra_key,
    )
    cached = read_cached(_NAMESPACE, key, _CACHE_VERSION)
    if cached is None:
        cached = _sidecar_read(sidecar, key)
    raw = cached
    if raw is None:
        raw = infer_workflow_review(
            steps=steps_context,
            page_urls=page_urls,
            sibling_bindings=sibling_bindings or {},
            model=model,
            error_detail=error_detail,
            task_description=task,
            review_inputs=review_inputs,
            loop_candidates=loop_candidates,
        )
        if not raw or (
            not str(raw.get("goal") or "").strip()
            and not (raw.get("steps") or [])
            and not (raw.get("suggestions") or [])
        ):
            # Empty/failed response — do NOT cache, so the next compile retries.
            return WorkflowIntentGraph(), []
        write_cached(_NAMESPACE, key, raw, _CACHE_VERSION)
    understanding = raw.get("understanding") if isinstance(raw.get("understanding"), dict) else None
    if understanding is not None:
        understanding = {**understanding, "for_task": task}
        raw = {**raw, "understanding": understanding}
    if extras_out is not None:
        extras_out.update(understanding=understanding, loop_copy=[r for r in raw.get("loop_copy") or [] if isinstance(r, dict)])
    # Pinned beside the recording, so a recompile replays this exact answer even if the shared
    # cache was cleared or the model has since changed its mind.
    _sidecar_write(sidecar, key, raw)

    graph = _graph_from_raw(raw)
    if not settings.llm_semantic_suggestions_enabled:
        return graph, []
    findings = _validate_findings(
        raw.get("suggestions"), step_keys_in_workflow=step_keys_in_workflow, steps_context=steps_context
    )
    return graph, findings
