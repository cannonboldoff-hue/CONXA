"""Map persisted skill JSON -> editor DTOs and step-quality suggestions.

Split out of workflow_service.py: this half converts a compiled skill's raw
step dicts into StepEditorDTO/WorkflowResponse (the editor's read model) and
flags step-quality issues (collect_suggestions). The mutation half
(workflow_mutations.py) calls into collect_suggestions and
_build_reference_for_audit after each edit; nothing here calls back into it.
"""

from __future__ import annotations

from typing import Any

from conxa_compile.anchors.schema import normalize_anchor_list
from conxa_compile.compiler.action_semantics import action_name
from conxa_compile.compiler.destructive_semantics import destructive_compiler_step
from conxa_compile.compiler.intent_access import get_effective_intent, get_effective_intent_from_skill_step
from conxa_compile.compiler.wait_for_shape import (
    destructive_wait_for_is_non_none,
    leaf_wait_for_conditions,
    leaf_wait_type,
    scan_wait_for_binding_targets,
)
from conxa_compile.confidence.uncertainty import audit_reference
from conxa_compile.editor.action_registry import action_spec, action_spec_dict, normalize_action_kind
from conxa_compile.compiler.selector_grammar import compute_merged_display_target, signals_to_display_list
from conxa_compile.compiler.selector_score import confidence_from_signal_rows
from conxa_compile.editor.assets import asset_url
from conxa_compile.editor.describe import describe_step
from conxa_compile.editor.dto import FrameDTO, StepEditorDTO, StepFlags, StepScreenshotDTO, SuggestionItem, WorkflowResponse, _FRAME_OFFSETS
from conxa_compile.editor.step_view import skill_step_for_destructive_check
from conxa_compile.policy.bundle import get_policy_bundle
from conxa_compile.policy.intent_ontology import generic_intents

def _parse_scroll_amount(step: dict[str, Any]) -> int | None:
    action = step.get("action")
    if isinstance(action, dict):
        raw = action.get("delta")
        try:
            if raw is not None:
                return int(raw)
        except (TypeError, ValueError):
            pass
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    visual = signals.get("visual") if isinstance(signals.get("visual"), dict) else {}
    pos = str(visual.get("scroll_position") or "").strip()
    if not pos:
        return None
    _, _, y = pos.partition(",")
    try:
        return int(float(y.strip() or 0))
    except ValueError:
        return None


def _scroll_mode(step: dict[str, Any]) -> str | None:
    action = step.get("action")
    if not isinstance(action, dict) or str(action.get("action") or "").strip().lower() != "scroll":
        return None
    selector = str(action.get("selector") or "").strip()
    if selector:
        return "scroll_to_locate"
    return "scroll_only"


def _scroll_selector(step: dict[str, Any]) -> str | None:
    action = step.get("action")
    if not isinstance(action, dict):
        return None
    selector = str(action.get("selector") or "").strip()
    return selector or None


def _build_reference_for_audit(step: dict[str, Any]) -> dict[str, Any]:
    signals = step.get("signals") or {}
    context = signals.get("context") or {}
    return {
        "action_kind": (step.get("action") or {}).get("action")
        if isinstance(step.get("action"), dict)
        else step.get("action"),
        "target": signals.get("dom") or {},
        "selectors": signals.get("selectors") or {},
        "semantic": signals.get("semantic") or {},
        "context": {k: v for k, v in context.items() if k not in {"page_url", "page_title", "state_after", "timing"}},
        "anchors": normalize_anchor_list(signals.get("anchors") or []),
        "visual": signals.get("visual") or {},
        "state_after": context.get("state_after") or "",
        "page_url": context.get("page_url") or "",
        "page_title": context.get("page_title") or "",
    }


def _editable_fields(step: dict[str, Any], policy: dict[str, Any]) -> dict[str, bool]:
    act = action_name(step).lower()
    spec = action_spec(act)
    is_scroll = act == "scroll"
    is_navigate = act == "navigate"
    # browser_back/browser_forward carry an informational url (where the history nav landed)
    # but it is never edited/navigated — the runtime calls goBack()/goForward() instead.
    is_marker = spec.marker
    dest = destructive_compiler_step(skill_step_for_destructive_check(step), policy)
    return {
        "intent": not is_marker,
        "action": not is_marker,
        "url": is_navigate,
        "selectors": spec.selectors and not is_marker,
        "anchors": spec.recovery and not is_marker,
        "validation": not is_scroll and not is_marker,
        "recovery_strategies": False,
        "value": spec.value and not is_marker,
        "parameterization": not is_scroll and not is_marker,
        "destructive_requires_validation": dest,
    }


def _step_url(step: dict[str, Any]) -> str:
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    if isinstance(action, dict):
        url = str(action.get("url") or "").strip()
        if url:
            return url
    url = str(step.get("url") or "").strip()
    if url:
        return url
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    context = signals.get("context") if isinstance(signals.get("context"), dict) else {}
    return str(context.get("page_url") or "").strip()


def _compiled_selectors(step: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in step.get("compiled_selectors") or []:
        selector = str(raw or "").strip()
        if selector and selector not in seen:
            out.append(selector)
            seen.add(selector)
    return out


def _persisted_visual_path(rel: str, source_session_id: str) -> str:
    r = rel.strip().replace("\\", "/")
    if not r or ".." in r:
        return ""
    if r.startswith("sessions/"):
        return r
    sid = source_session_id.strip()
    if sid and (r.startswith("images/") or r.startswith("frames/")):
        return f"sessions/{sid}/{r}"
    return r


def _screenshot_dto(
    skill_id: str,
    visual: dict[str, Any],
    asset_base_url: str,
    source_session_id: str = "",
) -> StepScreenshotDTO:
    def u(rel: str) -> str | None:
        if not rel or not isinstance(rel, str):
            return None
        persisted = _persisted_visual_path(rel, source_session_id)
        if not persisted:
            return None
        return asset_url(persisted, asset_base_url=asset_base_url, skill_id=skill_id)

    raw_frames = visual.get("frames") if isinstance(visual.get("frames"), dict) else {}
    frame_dtos = [
        FrameDTO(label=label, offset_ms=_FRAME_OFFSETS.get(label, 0), url=u(str(raw_frames.get(label) or "")))
        for label in ("before_far", "before_near", "at", "after_near", "after_far")
        if label in raw_frames
    ]
    return StepScreenshotDTO(
        full_url=u(str(visual.get("full_screenshot") or "")),
        element_url=u(str(visual.get("element_snapshot") or "")),
        scroll_url=u(str(visual.get("scroll_screenshot") or "")),
        bbox=visual.get("bbox") if isinstance(visual.get("bbox"), dict) else {},
        viewport=str(visual.get("viewport") or ""),
        scroll_position=str(visual.get("scroll_position") or ""),
        frames=frame_dtos,
        default_frame_label=str(visual.get("default_frame_label") or "") or None,
    )


def _parameter_bindings_from_step(step: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface {{var}} usage in whitelisted string fields (read-only hints for UI)."""
    from conxa_compile.editor.placeholder_grammar import PLACEHOLDER_RE

    pat = PLACEHOLDER_RE
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def scan(path: str, text: str) -> None:
        for m in pat.finditer(text):
            key = (path, m.group(1))
            if key not in seen:
                seen.add(key)
                out.append({"variable_id": m.group(1), "path": path, "match": m.group(0)})

    tgt = step.get("target") if isinstance(step.get("target"), dict) else {}
    scan("target.primary_selector", str(tgt.get("primary_selector") or ""))
    for i, fb in enumerate(tgt.get("fallback_selectors") or []):
        scan(f"target.fallback_selectors[{i}]", str(fb))
    val = step.get("value")
    if isinstance(val, str):
        scan("value", val)
    val = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    wf = val.get("wait_for") or {}
    if isinstance(wf, dict):
        scan_wait_for_binding_targets(wf, "validation.wait_for", scan)
    return out


def collect_suggestions(steps: list[dict[str, Any]], policy: dict[str, Any]) -> list[SuggestionItem]:
    items: list[SuggestionItem] = []
    gen = generic_intents(policy)

    for idx, step in enumerate(steps):
        if action_spec(action_name(step)).marker:
            continue
        ref = _build_reference_for_audit(step)
        for issue in audit_reference(ref):
            sev: str = "error" if issue in {"missing_selectors", "empty_primary_css", "anchors_empty_required"} else "warn"
            items.append(
                SuggestionItem(
                    step_index=idx,
                    severity=sev,  # type: ignore[arg-type]
                    code=issue,
                    message=_issue_message(issue),
                )
            )
        intent = get_effective_intent_from_skill_step(step).strip().lower()
        if intent in gen or not intent:
            items.append(
                SuggestionItem(
                    step_index=idx,
                    severity="warn",
                    code="generic_or_empty_intent",
                    message="Intent is missing or generic; choose a specific slug.",
                )
            )
        wf = (step.get("validation") or {}).get("wait_for") or {}
        wf_d = dict(wf) if isinstance(wf, dict) else {}
        pri = str((step.get("target") or {}).get("primary_selector") or "").strip()
        missing_element_appear_target = False
        for leaf in leaf_wait_for_conditions(wf_d):
            if leaf_wait_type(leaf) != "element_appear":
                continue
            tgt = str(leaf.get("target") or "").strip()
            if not tgt and not pri:
                missing_element_appear_target = True
                break
        if missing_element_appear_target:
            items.append(
                SuggestionItem(
                    step_index=idx,
                    severity="warn",
                    code="element_appear_without_target",
                    message="wait_for element_appear needs a selector target or primary_selector on the step.",
                )
            )
        cw = (step.get("confidence_protocol") or {}).get("compile_warnings") or {}
        if isinstance(cw, dict) and cw.get("vision_anchor_fallback"):
            items.append(
                SuggestionItem(
                    step_index=idx,
                    severity="warn",
                    code="vision_anchor_fallback",
                    message="Vision anchors were unavailable during compile; deterministic anchors were used.",
                )
            )
        if isinstance(cw, dict) and cw.get("weak_evidence"):
            items.append(
                SuggestionItem(
                    step_index=idx,
                    severity="warn",
                    code="weak_evidence",
                    message="No observed page change backs this step's required check — it only confirms the click did something, not what.",
                )
            )
        if isinstance(cw, dict) and cw.get("frame_chain_incomplete"):
            items.append(
                SuggestionItem(
                    step_index=idx,
                    severity="warn",
                    code="frame_chain_incomplete",
                    message="This step was recorded inside an iframe, but part of the frame chain had no usable identity signals and was dropped — it may run against the wrong part of the page or fail to find its target.",
                )
            )
        if destructive_compiler_step(skill_step_for_destructive_check(step), policy):
            if isinstance(cw, dict) and cw.get("destructive_low_anchor_count"):
                items.append(
                    SuggestionItem(
                        step_index=idx,
                        severity="warn",
                        code="destructive_low_anchor_count",
                        message="Destructive action: add more semantic anchors before relying on this step.",
                    )
                )
            anchors = ref.get("anchors") or []
            if not anchors:
                items.append(
                    SuggestionItem(
                        step_index=idx,
                        severity="error",
                        code="destructive_missing_anchors",
                        message="Destructive step should include anchors for safe recovery.",
                    )
                )
            if not destructive_wait_for_is_non_none(wf_d):
                items.append(
                    SuggestionItem(
                        step_index=idx,
                        severity="warn",
                        code="destructive_weak_validation",
                        message="Destructive step: set an explicit wait_for (e.g. element_appear or url_change).",
                    )
                )
    return items


def _issue_message(code: str) -> str:
    return {
        "missing_selectors": "Selector bundle is empty or unusable.",
        "empty_primary_css": "Primary CSS selector is empty.",
        "anchors_empty": "No semantic anchors on this step.",
        "anchors_empty_required": "Anchors required but missing.",
        "weak_visual_bbox": "Bounding box for visual match is weak or missing.",
    }.get(code, code.replace("_", " ").title())


# Conditional/branch action kinds (EXEC-1). Kept local to this module (rather than imported
# from action_registry's CATEGORIES) since only the "does this step carry a branch body"
# question matters here.
_BRANCH_KINDS = frozenset({"if_present", "try_dismiss", "wait_for_one_of"})


def _recovery_view(recovery: dict[str, Any], is_marker: bool) -> dict[str, Any]:
    """Plain-display projection of the persisted RecoveryBlock. Read-only: recovery tunables
    are policy-owned (predictable recovery, not per-step drift) — see patch_gate.py, which never
    accepts a `recovery_view` patch key."""
    if is_marker or not isinstance(recovery, dict):
        return {}
    return {
        "strategies": list(recovery.get("strategies") or ["semantic match", "position match", "visual match"]),
        "max_attempts": int(recovery.get("max_attempts") or 2),
        "confidence_threshold": float(recovery.get("confidence_threshold") or 0.85),
        "require_diverse_attempts": bool(recovery.get("require_diverse_attempts", True)),
        "anchors": normalize_anchor_list(recovery.get("anchors") or []),
    }


def _fingerprint_view(bundle: dict[str, Any]) -> dict[str, Any]:
    """Read-only "what was recorded" subset of identity_bundle.fingerprint + frame/shadow depth —
    the resolver's scoring oracle, previously invisible outside the compiler/runtime."""
    fp = bundle.get("fingerprint") if isinstance(bundle.get("fingerprint"), dict) else {}
    frame_chain = bundle.get("frame_chain") if isinstance(bundle.get("frame_chain"), list) else []
    shadow_path = bundle.get("shadow_path") if isinstance(bundle.get("shadow_path"), list) else []
    if not fp and not frame_chain and not shadow_path:
        return {}
    return {
        "role": str(fp.get("role") or ""),
        "tag": str(fp.get("tag") or ""),
        "inner_text": str(fp.get("inner_text") or ""),
        "aria_label": str(fp.get("aria_label") or ""),
        "name": str(fp.get("name") or ""),
        "placeholder": str(fp.get("placeholder") or ""),
        "label_text": str(fp.get("label_text") or ""),
        "data_testid": str(fp.get("data_testid") or ""),
        "input_type": str(fp.get("input_type") or ""),
        "position_hint": dict(fp.get("position_hint") or {}),
        "frame_depth": len(frame_chain),
        "shadow_depth": len(shadow_path),
        "destructive": bool(bundle.get("destructive") or False),
        # Diagnostics-tier only (support/Conxa engineer) — integrity hashes, never editable and
        # not meaningful to a Review-tier approver. See DiagnosticsPanel.tsx.
        "stable_hash": str(bundle.get("stable_hash") or ""),
        "compat_fingerprint": str(bundle.get("compat_fingerprint") or ""),
    }


def _handler_hints_view(handler_hints: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(handler_hints, dict):
        return {}
    hover_chain = handler_hints.get("hover_chain") if isinstance(handler_hints.get("hover_chain"), list) else []
    virtualized = str(handler_hints.get("virtualized_container") or "")
    allow_forced = bool(handler_hints.get("allow_forced_action") or False)
    if not hover_chain and not virtualized and not allow_forced:
        return {}
    return {
        "has_hover_chain": bool(hover_chain),
        "hover_chain_len": len(hover_chain),
        "virtualized_container": virtualized,
        "allow_forced_action": allow_forced,
    }


def _entity_binding_view(step: dict[str, Any]) -> dict[str, Any]:
    eb = step.get("entity_binding")
    if not isinstance(eb, dict) or not eb.get("container_selector") or not eb.get("identifier"):
        return {}
    return {
        "container_selector": str(eb.get("container_selector") or ""),
        "identifier": str(eb.get("identifier") or ""),
        "source": str(eb.get("source") or "literal"),
        "confirmed": bool(eb.get("confirmed") or False),
    }


def _safety_view(bundle: dict[str, Any], handler_hints_view: dict[str, Any], is_destructive: bool) -> dict[str, bool]:
    """Drives step-row safety badges — surfaces flags that were previously computed but never
    shown, most importantly handler_hints.allow_forced_action."""
    return {
        "destructive": bool(is_destructive or bundle.get("destructive") or False),
        "allow_forced_action": bool(handler_hints_view.get("allow_forced_action") or False),
        "has_hover_chain": bool(handler_hints_view.get("has_hover_chain") or False),
    }


def _branch_info(
    skill_id: str,
    step: dict[str, Any],
    step_index: int,
    policy: dict[str, Any],
    asset_base_url: str,
    source_session_id: str,
) -> tuple[dict[str, Any] | None, list[StepEditorDTO]]:
    """Read-only projection of step["branch"] (if_present/try_dismiss/wait_for_one_of — EXEC-1).

    if_present's nested `steps` body is projected the same way as top-level steps, addressed by
    a path-based id (`{skill_id}:{step_index}.branch.steps[{j}]`) so patch_gate can re-validate
    edits against the same nested dict. try_dismiss has no nested body (it dismisses via its own
    candidate selectors). wait_for_one_of is an OR of alternative selectors, each with its own
    nested body — full per-option step editing is intentionally out of scope for this pass (see
    TODO BUILD-5 follow-up); its summary surfaces each option's selector + nested step count for
    read-only review only.
    """
    kind = normalize_action_kind(action_name(step))
    if kind not in _BRANCH_KINDS:
        return None, []
    branch = step.get("branch") if isinstance(step.get("branch"), dict) else {}

    if kind == "if_present":
        nested_raw = [s for s in (branch.get("steps") or []) if isinstance(s, dict)]
        branch_steps = [
            step_to_dto(
                skill_id,
                dict(nested),
                j,
                policy,
                asset_base_url,
                source_session_id,
                dto_id=f"{skill_id}:{step_index}.branch.steps[{j}]",
            )
            for j, nested in enumerate(nested_raw)
        ]
        target = step.get("target") if isinstance(step.get("target"), dict) else {}
        probe = str(target.get("primary_selector") or "").strip() or "(uses step's own selector)"
        return {"kind": kind, "probe": probe, "step_count": len(branch_steps)}, branch_steps

    if kind == "try_dismiss":
        candidates = [str(c).strip() for c in (branch.get("candidates") or []) if str(c).strip()]
        summary = {
            "kind": kind,
            "probe": f"{len(candidates)} candidate selector(s)" if candidates else "(uses step's own selector)",
            "step_count": 0,
            "candidates": candidates,
        }
        return summary, []

    # wait_for_one_of
    options_raw = [o for o in (branch.get("options") or []) if isinstance(o, dict)]
    option_summaries = [
        {
            "selector": str(o.get("selector") or "").strip(),
            "step_count": len([s for s in (o.get("steps") or []) if isinstance(s, dict)]),
        }
        for o in options_raw
    ]
    summary = {
        "kind": kind,
        "probe": f"{len(option_summaries)} option(s)",
        "step_count": sum(o["step_count"] for o in option_summaries),
        "options": option_summaries,
    }
    return summary, []


def _fork_info(step: dict[str, Any]) -> dict[str, Any] | None:
    """Read-only projection of SkillStep.outcome_fork for the editor (no nested step DTOs: option
    bodies are edited only through the fork mutations, never patched in place)."""
    fork = step.get("outcome_fork")
    if not isinstance(fork, dict) or not fork.get("states"):
        return None
    clean = fork.get("clean") if isinstance(fork.get("clean"), dict) else {}
    states = []
    for st in fork.get("states") or []:
        if not isinstance(st, dict):
            continue
        sig = st.get("signature") if isinstance(st.get("signature"), dict) else {}
        states.append({
            "id": st.get("id"),
            "title": sig.get("title") or "",
            "policy": dict(st.get("policy") or {}),
            "options": [
                {
                    "id": o.get("id"),
                    "label": o.get("label"),
                    "role": o.get("role"),
                    "recorded": bool(o.get("recorded")),
                    "step_count": len(o.get("steps") or []),
                }
                for o in st.get("options") or [] if isinstance(o, dict)
            ],
        })
    return {
        "timeout_ms": fork.get("timeout_ms"),
        "unknown_policy": fork.get("unknown_policy") or "ask_human",
        "clean_observed": bool(clean.get("observed")),
        "states": states,
    }


def _for_each_info(
    skill_id: str,
    step: dict[str, Any],
    step_index: int,
    policy: dict[str, Any],
    asset_base_url: str,
    source_session_id: str,
) -> tuple[dict[str, Any] | None, list[StepEditorDTO]]:
    """Read-only projection of step["for_each"] (EXEC-38 iteration primitive). Same
    path-addressed-nested-body pattern as _branch_info's if_present case — the body is
    editable only through structural RPCs / path-addressed patches, never through this
    summary. No dedicated editor control exists yet (see TODO.md); this is visibility only,
    same as every other field the 2026-08 audit flagged as hidden."""
    kind = normalize_action_kind(action_name(step))
    if kind != "for_each":
        return None, []
    for_each = step.get("for_each") if isinstance(step.get("for_each"), dict) else {}
    rows = for_each.get("rows") if isinstance(for_each.get("rows"), dict) else {}
    nested_raw = [s for s in (for_each.get("steps") or []) if isinstance(s, dict)]
    for_each_steps = [
        step_to_dto(
            skill_id,
            dict(nested),
            j,
            policy,
            asset_base_url,
            source_session_id,
            dto_id=f"{skill_id}:{step_index}.for_each.steps[{j}]",
        )
        for j, nested in enumerate(nested_raw)
    ]
    summary = {
        "container_selector": str(rows.get("container_selector") or "").strip(),
        "items": str(for_each.get("items") or "").strip(),
        "as": str(for_each.get("as") or "row").strip() or "row",
        "max_iterations": for_each.get("max_iterations"),
        "on_row_error": str(for_each.get("on_row_error") or "stop").strip() or "stop",
        "step_count": len(for_each_steps),
    }
    return summary, for_each_steps


def step_to_dto(
    skill_id: str,
    step: dict[str, Any],
    step_index: int,
    policy: dict[str, Any],
    asset_base_url: str,
    source_session_id: str = "",
    *,
    dto_id: str | None = None,
) -> StepEditorDTO:
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    semantic = signals.get("semantic") if isinstance(signals.get("semantic"), dict) else {}
    recovery = step.get("recovery") if isinstance(step.get("recovery"), dict) else {}
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    visual = signals.get("visual") if isinstance(signals.get("visual"), dict) else {}
    is_url_check = str(action_name(step)).lower() == "check" and str(step.get("check_kind") or "url").lower() in {
        "url",
        "url_exact",
        "url_must_be",
    }

    intent_top = str(step.get("intent") or "").strip()
    final_intent = get_effective_intent(semantic) or intent_top

    gen = generic_intents(policy)
    flags = StepFlags(
        is_destructive=destructive_compiler_step(skill_step_for_destructive_check(step), policy),
        is_scroll=str(action_name(step)).lower() == "scroll",
        generic_intent=(final_intent.strip().lower() in gen) or not final_intent.strip(),
    )

    # Surface identity_bundle signals into the editor's selector list so nothing the
    # runtime uses is hidden. Convert internal: grammar → public display strings, then
    # merge (deduped) with target.primary_selector + fallback_selectors. The bundle
    # signals come durability-ordered; we preserve that order and append any target
    # selectors that are NOT already covered by a signal.
    raw_target = dict(step.get("target") or {})
    bundle = step.get("identity_bundle") if isinstance(step.get("identity_bundle"), dict) else {}
    bundle_signals_raw = bundle.get("signals") if isinstance(bundle.get("signals"), list) else []
    identity_display = signals_to_display_list(bundle_signals_raw)  # [{selector, engine, durability, ...}]

    # Step-level confidence rollup: prefer whatever was persisted at last (re)compile/retarget
    # (target.selector_confidence), else derive it fresh from the bundle's raw signals.
    stored_confidence = raw_target.get("selector_confidence")
    if isinstance(stored_confidence, (int, float)):
        compile_confidence: float | None = float(stored_confidence)
    elif bundle_signals_raw:
        compile_confidence = confidence_from_signal_rows(bundle_signals_raw)
    else:
        compile_confidence = None
    if flags.is_scroll:
        # A scroll step has no element to select — there's nothing for this rollup to score.
        # Any stray 0.0 left on target.selector_confidence (e.g. from before a step was edited
        # into a scroll step) would otherwise render as a misleading "0%" instead of no badge.
        compile_confidence = None

    # Merge: signals first (hot-path, ordered by durability), then any target selectors
    # not already represented (recovery-only / legacy compiled selectors).
    merged_primary, merged_fallbacks = compute_merged_display_target(step)
    # Rebuild target with the merged list so the renderer's defaultsFromStep picks it up.
    merged_target = dict(raw_target)
    if merged_primary or merged_fallbacks:
        merged_target["primary_selector"] = merged_primary
        merged_target["fallback_selectors"] = merged_fallbacks

    handler_hints_view = _handler_hints_view(step.get("handler_hints") if isinstance(step.get("handler_hints"), dict) else {})
    branch_summary, branch_steps = _branch_info(
        skill_id, step, step_index, policy, asset_base_url, source_session_id
    )
    for_each_summary, for_each_steps = _for_each_info(
        skill_id, step, step_index, policy, asset_base_url, source_session_id
    )

    return StepEditorDTO(
        id=dto_id or f"{skill_id}:{step_index}",
        step_index=step_index,
        human_readable_description=describe_step(step, step_index),
        action_type=str(action_name(step)),
        action_payload=dict(step.get("action") or {}) if isinstance(step.get("action"), dict) else {"action": action_name(step)},
        action_spec=action_spec_dict(action_name(step)),
        # Prefer the stored human-readable description (workflow-intent graph prose,
        # editable in Human Edit) over describe_step's recomputed generic label;
        # fall back for steps compiled before the graph carried prose.
        semantic_description=str(step.get("semantic_description") or "").strip() or describe_step(step, step_index),
        intent=intent_top,
        final_intent=final_intent,
        url=_step_url(step),
        frame=dict(step.get("frame") or {}),
        tab=dict(step.get("tab") or {}),
        target=merged_target,
        selectors=dict(signals.get("selectors") or {}),
        compiled_selectors=_compiled_selectors(step),
        identity_engines=identity_display,
        compile_confidence=compile_confidence,
        anchors_signals=[] if is_url_check else normalize_anchor_list(signals.get("anchors") or []),
        anchors_recovery=[] if is_url_check else normalize_anchor_list(recovery.get("anchors") or []),
        validation={
            "wait_for": dict(validation.get("wait_for") or {}),
            "success_conditions": dict(validation.get("success_conditions") or {}),
            "assertions": [dict(a) for a in (validation.get("assertions") or []) if isinstance(a, dict)],
        },
        recovery=dict(recovery),
        value=step.get("value"),
        scroll_mode=_scroll_mode(step),
        scroll_selector=_scroll_selector(step),
        scroll_amount=_parse_scroll_amount(step),
        input_binding=step.get("input_binding"),
        screenshot=_screenshot_dto(skill_id, visual, asset_base_url, source_session_id),
        editable_fields=_editable_fields(step, policy),
        flags=flags,
        parameter_bindings=_parameter_bindings_from_step(step),
        check_kind=str(step.get("check_kind") or "") or None,
        check_pattern=str(step.get("check_pattern") or "") or None,
        check_threshold=step.get("check_threshold") if isinstance(step.get("check_threshold"), (int, float)) else None,
        check_selector=str(step.get("check_selector") or "") or None,
        check_text=str(step.get("check_text") or "") or None,
        ai_review_prompt=str(step.get("ai_review_prompt") or "") or None,
        ai_review_output_schema=step.get("ai_review_output_schema") if isinstance(step.get("ai_review_output_schema"), dict) else None,
        ai_review_on_failure=str(step.get("ai_review_on_failure") or "") or None,
        ai_review_default_value=step.get("ai_review_default_value"),
        recovery_view=_recovery_view(recovery, action_spec(action_name(step)).marker),
        fingerprint=_fingerprint_view(bundle),
        handler_hints_view=handler_hints_view,
        safety=_safety_view(bundle, handler_hints_view, flags.is_destructive),
        entity_binding=_entity_binding_view(step),
        branch_summary=branch_summary,
        branch_steps=branch_steps,
        for_each_summary=for_each_summary,
        for_each_steps=for_each_steps,
        fork_summary=_fork_info(step),
        optional_hint=step.get("optional_hint") if isinstance(step.get("optional_hint"), dict) else None,
    )


def _compile_health(document: dict[str, Any], meta: dict[str, Any], skill_id: str = "") -> dict[str, Any]:
    """Workflow-level compile-health summary — derived from compile_report + meta, both computed
    at compile time (compiler/build.py::_build_compile_report) but never surfaced to Human Edit
    before this redesign."""
    report = document.get("compile_report") if isinstance(document.get("compile_report"), dict) else {}
    if not report:
        return {}
    steps_below_threshold = [
        sr.get("index")
        for sr in (report.get("steps") or [])
        if isinstance(sr, dict) and sr.get("warnings")
    ]
    return {
        "status": str(report.get("status") or ""),
        "steps_total": report.get("steps_total"),
        "min_confidence": report.get("min_confidence"),
        "steps_with_warnings": report.get("steps_with_warnings"),
        "steps_below_threshold": steps_below_threshold,
        "required_runtime": str(meta.get("required_runtime") or ""),
        "compiler_policy_version": str(meta.get("compiler_policy_version") or ""),
        "structural_fingerprint_present": bool(meta.get("structural_fingerprint")),
        # Diagnostics-tier only — provider/router telemetry from compile time, not a
        # Review-tier concern. See DiagnosticsPanel.tsx.
        "llm_router_stats": report.get("llm_router_stats") or {},
        # One-click "generalize this to a loop" suggestion(s) — compiler/loop_suggestion.py.
        # Filtered HERE (read time), not only at compile time: a reviewer's "Dismiss" only logs
        # a decision (edit_log.py), it never edits compile_report on disk, so a plain page
        # reload with no recompile in between would otherwise show the same dismissed
        # suggestion again. Filtering on every read is the one place that covers a fresh
        # compile, a page reload, and a post-accept refresh uniformly.
        "for_each_suggestions": _for_each_suggestions(report, skill_id),
    }


def _for_each_suggestions(report: dict[str, Any], skill_id: str) -> list[dict[str, Any]]:
    suggestions = report.get("for_each_suggestions") or []
    if not suggestions or not skill_id:
        return suggestions
    from conxa_compile.compiler.loop_suggestion import filter_rejected
    from conxa_compile.editor.edit_log import read_edits

    try:
        return filter_rejected(suggestions, read_edits(skill_id))
    except Exception:  # noqa: BLE001 — a suggestion display glitch must never break Human Edit
        return suggestions


def build_workflow_response(skill_id: str, document: dict[str, Any], *, asset_base_url: str) -> WorkflowResponse:
    policy = get_policy_bundle().data
    meta = dict(document.get("meta") or {})
    source_session_id = str(meta.get("source_session_id") or "").strip()
    steps_raw = (document.get("skills") or [{}])[0].get("steps") or []
    if not isinstance(steps_raw, list):
        steps_raw = []
    steps = [
        step_to_dto(skill_id, dict(s), i, policy, asset_base_url, source_session_id)
        for i, s in enumerate(steps_raw)
    ]
    suggestions = collect_suggestions([dict(s) for s in steps_raw], policy)
    intent_graph = document.get("intent_graph") if isinstance(document.get("intent_graph"), dict) else {}
    return WorkflowResponse(
        skill_id=skill_id,
        package_meta=meta,
        inputs=list(document.get("inputs") or []),
        steps=steps,
        suggestions=suggestions,
        asset_base_url=asset_base_url,
        intent_graph=intent_graph,
        compile_health=_compile_health(document, meta, skill_id),
    )


