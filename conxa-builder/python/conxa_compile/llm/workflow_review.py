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
from conxa_compile.llm.openapi_client import TRUNCATED_MARKER, infer_workflow_review
from conxa_compile.llm.workflow_intent import _graph_from_raw
from conxa_compile.llm.model_ledger import log as _log
from conxa_compile.llm.workflow_semantics import _validate_findings

_NAMESPACE = "workflow_review"
_CACHE_VERSION = 2
# Bump when the review prompt in openapi_client.infer_workflow_review changes, so a fresh
# recording after the change is not served an answer the old prompt produced.
_PROMPT_VERSION = 7  # 7: each step states its 0-based index; 6: reads the video's words, the understanding and each input's label, may be asked twice; 5: text only, no step screenshots; 4: also answers the description check and loop wording (BUILD-55)


_RETRY_NOTE = "Your last reply could not be read. Return ONLY the JSON object described, nothing before or after it."


def _empty(raw: dict[str, Any] | None) -> bool:
    """No reply, or one with no goal, no step intents and no suggestions."""
    return not raw or (
        not str(raw.get("goal") or "").strip() and not (raw.get("steps") or []) and not (raw.get("suggestions") or [])
    )


def _infra_failure(error_detail: list[str] | None) -> bool:
    """The router or the cloud could not be reached (marked by ``openapi_client._call_llm_or_none``)."""
    return "cloud_unreachable" in (error_detail or [])


# Only when one call's reply is still cut off at the output limit (which grows with the step count, see
# conxa_core/llm/client.py::_output_budget): the steps go in windows of this size, each halved if cut off.
WINDOW_STEPS = 100
_EARLIER_SHOWN = 15  # intents of the steps just before a window, sent so its wording stays consistent
_FIRST_WINDOW_ONLY = ("review_inputs", "loop_candidates")  # whole-recording answers: asked once


def _review_in_windows(
    steps_context: list[dict[str, Any]],
    call_kwargs: dict[str, Any],
    error_detail: list[str] | None,
) -> tuple[dict[str, Any], int]:
    """Review a long workflow one window at a time and join the answers into the shape one call gives.
    A window whose reply was cut off at the output limit is split in half and each half asked again,
    down to one step. Returns (joined answer, number of steps no call could review)."""
    total = len(steps_context)
    merged: dict[str, Any] = {
        "goal": "", "steps": [], "decision_points": [], "expected_end_state": {},
        "suggestions": [], "understanding": None, "loop_copy": [],
    }
    earlier: list[dict[str, str]] = []
    state = {"missed": 0, "dead": False}

    def ask(chunk: list[dict[str, Any]], window: dict[str, Any], **extra: Any) -> tuple[dict[str, Any] | None, list[str]]:
        detail: list[str] = []
        kwargs = {**call_kwargs, **extra}
        return infer_workflow_review(steps=chunk, error_detail=detail, window=window, **kwargs), detail

    def run(start: int, chunk: list[dict[str, Any]]) -> None:
        if state["dead"]:
            state["missed"] += len(chunk)
            return
        first = start == 0
        window = {
            "start": start, "total": total, "first": first, "last": start + len(chunk) >= total,
            "earlier": earlier[-_EARLIER_SHOWN:],
        }
        skip = {} if first else {k: None for k in _FIRST_WINDOW_ONLY}
        raw, detail = ask(chunk, window, **skip)
        if _empty(raw) and TRUNCATED_MARKER not in detail and not _infra_failure(detail):
            raw, detail = ask(chunk, window, retry=_RETRY_NOTE, **skip)
        if TRUNCATED_MARKER in detail and len(chunk) > 1:
            half = len(chunk) // 2
            run(start, chunk[:half])
            run(start + half, chunk[half:])
            return
        if _empty(raw):
            state["missed"] += len(chunk)
            state["dead"] = _infra_failure(detail)
            if error_detail is not None:
                error_detail.extend(detail)
            return
        raw = raw or {}
        for item in raw.get("steps") or []:
            try:
                local = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue  # same rule as _graph_from_raw: an entry without a usable index is dropped
            if 0 <= local < len(chunk):
                earlier.append({"key": str(chunk[local].get("key") or ""), "intent": str(item.get("intent") or "")})
            merged["steps"].append({**item, "index": local + start})
        for point in raw.get("decision_points") or []:
            if isinstance(point, dict) and isinstance(point.get("step_index"), int):
                point = {**point, "step_index": point["step_index"] + start}
            merged["decision_points"].append(point)
        merged["suggestions"].extend(raw.get("suggestions") or [])
        if first:
            merged.update(goal=raw.get("goal") or "", understanding=raw.get("understanding"),
                          loop_copy=raw.get("loop_copy") or [])
        if window["last"]:
            merged["expected_end_state"] = raw.get("expected_end_state") or {}

    for start in range(0, total, WINDOW_STEPS):
        run(start, steps_context[start:start + WINDOW_STEPS])
    return merged, state["missed"]


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


def _zero_based(raw: dict[str, Any], n: int) -> dict[str, Any]:
    """A reply that counted the steps from 1 (no step 0, and one step numbered past the last) is moved back by one.
    Left as it was, every description sat on the step after the one it describes (WF-3 Leg A: "Type a name into
    the search box" on the select-all key). Anything else is left exactly as the model wrote it."""
    idx = [int(s["index"]) for s in raw.get("steps") or [] if isinstance(s, dict) and isinstance(s.get("index"), int)]
    if not idx or 0 in idx or max(idx) != n:
        return raw
    steps = [{**s, "index": s["index"] - 1} if isinstance(s, dict) and isinstance(s.get("index"), int) else s
             for s in raw.get("steps") or []]
    points = [{**p, "step_index": p["step_index"] - 1} if isinstance(p, dict) and isinstance(p.get("step_index"), int) else p
              for p in raw.get("decision_points") or []]
    return {**raw, "steps": steps, "decision_points": points}


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
    prior_understanding: dict[str, Any] | None = None,
) -> tuple[WorkflowIntentGraph, list[dict[str, Any]]]:
    """One multimodal LLM call producing (workflow intent graph, BUILD-25
    findings). Returns (WorkflowIntentGraph(), []) on any failure or an empty
    response — never raises. `suggestions` is additionally forced to [] when
    llm_semantic_suggestions_enabled is False, even on a successful call.

    BUILD-55: the same answer may also carry `understanding` (the description check) and
    `loop_copy` (wording for loop suggestions). Neither is part of the return value; they are
    written into ``extras_out`` when given -- ``understanding`` stamped with the description it
    was written for (``for_task``), so a later edit to the description makes it stale.

    ``prior_understanding`` is what the pre-compile check already settled (goal, settled values, points it
    decided). The goal it holds replaces this call's own, and a `parameterize_literal` for a value it
    settled is dropped, so the chat and the skill never show two goals and a settled value is not
    reopened here."""
    ledger: dict[str, Any] = {}  # what the guards below drop, counted (model_ledger); read back as extras_out["ledger"]
    if extras_out is not None:
        extras_out.update(understanding=None, loop_copy=[], ledger=ledger)
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
    if prior_understanding:
        extra_key["understanding"] = prior_understanding
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
    missed = 0  # steps no window could review (see _review_in_windows)
    if raw is None:
        call_kwargs = dict(
            page_urls=page_urls, sibling_bindings=sibling_bindings or {}, model=model,
            task_description=task, review_inputs=review_inputs,
            loop_candidates=loop_candidates, understanding=prior_understanding,
        )
        detail: list[str] = []
        raw = infer_workflow_review(steps=steps_context, error_detail=detail, **call_kwargs)
        if TRUNCATED_MARKER in detail and len(steps_context) > 1:
            # One reply was not big enough even at the largest output limit: ask in windows instead.
            raw, missed = _review_in_windows(steps_context, call_kwargs, detail)
            if missed:
                detail.append(f"steps_not_reviewed: {missed}")
                _log(ledger, "shape", "steps_not_reviewed")
        elif _empty(raw) and not _infra_failure(detail):
            # One reply that could not be read (cut off, not JSON): ask once more, saying so. Not when
            # the router itself could not be reached; a second try would only wait the same time again.
            _log(ledger, "shape", "unreadable_reply")
            raw = infer_workflow_review(steps=steps_context, error_detail=detail, retry=_RETRY_NOTE, **call_kwargs)
        if error_detail is not None:
            error_detail.extend(detail)
        if not raw or (
            not str(raw.get("goal") or "").strip()
            and not (raw.get("steps") or [])
            and not (raw.get("suggestions") or [])
        ):
            # Empty/failed response — do NOT cache, so the next compile retries.
            return WorkflowIntentGraph(), []
        if not missed:  # a partly reviewed workflow is not pinned: the next compile tries the missing windows again
            write_cached(_NAMESPACE, key, raw, _CACHE_VERSION)
    understanding = raw.get("understanding") if isinstance(raw.get("understanding"), dict) else None
    if understanding is not None:
        understanding = {**understanding, "for_task": task}
        raw = {**raw, "understanding": understanding}
    if extras_out is not None:
        extras_out.update(understanding=understanding, loop_copy=[r for r in raw.get("loop_copy") or [] if isinstance(r, dict)])
    # Pinned beside the recording, so a recompile replays this exact answer even if the shared
    # cache was cleared or the model has since changed its mind.
    if not missed:
        _sidecar_write(sidecar, key, raw)

    graph = _graph_from_raw(_zero_based(raw, len(steps_context)))
    if prior_understanding and prior_understanding.get("goal"):
        graph.goal = str(prior_understanding["goal"])
    if not settings.llm_semantic_suggestions_enabled:
        return graph, []
    settled = frozenset(str(v.get("literal") or "").strip().lower() for v in (prior_understanding or {}).get("settled") or [])
    findings = _validate_findings(
        raw.get("suggestions"), step_keys_in_workflow=step_keys_in_workflow, steps_context=steps_context,
        settled_literals=settled - {""}, report=ledger,
    )
    return graph, findings
