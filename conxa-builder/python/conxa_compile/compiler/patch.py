"""Phase 6 — merge user-provided fixes into a compiled skill (1-click fix API)."""

from __future__ import annotations

from typing import Any

from conxa_compile.anchors.schema import normalize_anchor_list
from conxa_compile.compiler.action_policy import no_recovery_block, recovery_enabled_for_action
from conxa_compile.compiler.intent_access import get_effective_intent_from_skill_step
from conxa_compile.compiler.recovery_policy import (
    merge_recovery_strategies_for_wait_shape,
    recovery_strategies_for_intent,
)
from conxa_compile.confidence.layered import layered_decision
from conxa_compile.confidence.uncertainty import audit_reference
from conxa_compile.editor.placeholder_grammar import FULL_PLACEHOLDER_RE
from conxa_compile.llm.semantic_llm import SemanticLLMInput, enrich_semantic
from conxa_compile.policy.bundle import get_policy_bundle
from conxa_compile.policy.intent_ontology import sanitize_intent_token


def _build_reference_from_signals(step: dict[str, Any]) -> dict[str, Any]:
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
        "anchors": signals.get("anchors") or [],
        "visual": signals.get("visual") or {},
        "state_after": context.get("state_after") or "",
        "page_url": context.get("page_url") or "",
        "page_title": context.get("page_title") or "",
    }


def _target_identity_changed(patch: dict[str, Any]) -> bool:
    """True if patch touches fields that define which element is being targeted."""
    identity_keys = {"target", "identity_bundle"}
    signals_patch = patch.get("signals") or {}
    if signals_patch.get("dom") is not None or signals_patch.get("selectors") is not None:
        return True
    return any(k in patch for k in identity_keys)


def _regenerate_compiled_selectors(step: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Regenerate compiled_selectors and semantic_description when user edits the target.

    Reads the original recorded event from the session, re-runs selector compilation
    against the same DOM snapshot with the new bbox/element, and updates the step.
    On failure (missing snapshot, missing event, LLM disabled): leaves fields empty.
    """
    out = dict(step)
    snapshot_ref = step.get("snapshot_ref")
    snapshot_hash = step.get("snapshot_dom_hash")

    if not snapshot_ref or not snapshot_hash:
        return out

    try:
        source_session_id = (document.get("meta") or {}).get("source_session_id")
        if not source_session_id:
            return out

        from conxa_compile.editor.retarget import find_source_event
        from conxa_compile.llm.selector_regeneration import (
            compile_selectors_for_task,
            task_from_recorded_event,
        )

        matching_event = find_source_event(step, document)
        if not matching_event:
            return out

        target_bbox = ((step.get("signals") or {}).get("visual") or {}).get("bbox")
        if not target_bbox:
            return out

        task = task_from_recorded_event(matching_event, step.get("index", 0) or 0)
        if task.snapshot_hash != snapshot_hash:
            return out

        task.element_bbox = target_bbox
        task.target_dom = (step.get("target") or {})

        candidates = compile_selectors_for_task(task, session_id=source_session_id)
        if not candidates:
            signals = dict(step.get("signals") or {})
            signals["compiled_selectors"] = []
            out["signals"] = signals
            return out

        signals = dict(step.get("signals") or {})
        signals["compiled_selectors"] = [c.selector for c in candidates[:3]]
        if candidates[0].intent:
            signals["semantic_description"] = candidates[0].intent
        out["signals"] = signals
    except Exception:
        pass

    return out


def _enhance_step_with_llm(step: dict[str, Any], *, user_edited_recovery: bool = False) -> dict[str, Any]:
    """1-click fix enhancement: improve intent + anchors + strategies (assist-only).

    When user_edited_recovery=True, skips vision anchor enrichment to preserve user edits.
    """
    from conxa_compile.llm.anchor_vision_llm import generate_anchors_from_image_bytes

    out = dict(step)
    signals = out.get("signals") or {}
    dom = signals.get("dom") or {}
    semantic = signals.get("semantic") or {}
    context = signals.get("context") or {}
    pol = get_policy_bundle().data
    unc = pol.get("uncertainty") if isinstance(pol.get("uncertainty"), dict) else {}
    patch_min = float(unc.get("patch_llm_min_confidence", 0.8))
    llm = enrich_semantic(
        SemanticLLMInput(
            raw_text=str(dom.get("inner_text") or semantic.get("normalized_text") or ""),
            element_type=str(dom.get("tag") or semantic.get("role") or ""),
            context=str(context.get("page_title") or ""),
        )
    )
    if llm.confidence >= patch_min:
        resolved = str(out.get("intent") or llm.intent or "").strip()
        out["intent"] = resolved
        signals = dict(out.get("signals") or {})
        sem = dict(signals.get("semantic") or {})
        if resolved:
            sem["final_intent"] = resolved
            sem["llm_intent"] = resolved
        signals["semantic"] = sem
        out["signals"] = signals
        if not recovery_enabled_for_action(out.get("action")):
            out["recovery"] = no_recovery_block(resolved)
            return out
        recovery = dict(out.get("recovery") or {})
        recovery["intent"] = resolved or llm.intent
        recovery["final_intent"] = str(recovery.get("intent") or "").strip()
        anchors = list(recovery.get("anchors") or [])

        if not user_edited_recovery:
            visual = signals.get("visual") or {}
            full_screenshot = str(visual.get("full_screenshot") or "").strip()
            if full_screenshot:
                try:
                    from conxa_compile.editor.assets import resolve_skill_asset

                    asset_path = resolve_skill_asset(full_screenshot)
                    if asset_path.is_file():
                        image_bytes = asset_path.read_bytes()
                        intent_hint = resolved or llm.intent or ""
                        vision_anchors = generate_anchors_from_image_bytes(
                            image_bytes, intent_hint, 0, policy=pol
                        )
                        if vision_anchors:
                            anchors = vision_anchors + anchors
                except Exception:
                    pass

        recovery["anchors"] = anchors
        intent_for_recovery = str(recovery.get("intent") or resolved or llm.intent or "").strip()
        strategies = list(recovery_strategies_for_intent(intent_for_recovery, pol))
        if "llm_reasoned_match" not in strategies:
            strategies.append("llm_reasoned_match")
        recovery["strategies"] = strategies
        out["recovery"] = recovery
    return out


def _apply_top_level_step_fields(
    step: dict[str, Any], patch: dict[str, Any], *, sanitize_intent: bool = True
) -> None:
    if "action" in patch and isinstance(patch["action"], dict):
        current = step.get("action")
        base = dict(current) if isinstance(current, dict) else {"action": str(current or "")}
        step["action"] = deep_merge(base, dict(patch["action"]))
    if "intent" in patch and isinstance(patch["intent"], str):
        raw = str(patch["intent"]).strip()
        # The 1-click-fix path slug-sanitizes; the live editor keeps intent as the user's
        # free text (matching patch_gate) so the form field and the final_intent shadow that
        # describe/recovery read show the same thing (audit C-2).
        if sanitize_intent:
            prev = str(step.get("intent") or "").strip()
            resolved = sanitize_intent_token(raw, sanitize_intent_token(prev, "edited_step"))
        else:
            resolved = raw
        step["intent"] = resolved
        signals = dict(step.get("signals") or {})
        sem = dict(signals.get("semantic") or {})
        if resolved:
            sem["final_intent"] = resolved
            sem["llm_intent"] = resolved
        signals["semantic"] = sem
        step["signals"] = signals
    if "value" in patch:
        step["value"] = patch["value"]
        # input_binding is a derived shadow of value: {{name}} → "name", anything else → None.
        # Keep it in lockstep here so no edit path can leave it pointing at a stale variable
        # (audit findings M-1/L-0). This is the single home for that derivation.
        new_value = patch["value"]
        if isinstance(new_value, str):
            full_match = FULL_PLACEHOLDER_RE.match(new_value.strip())
            step["input_binding"] = full_match.group(1) if full_match else None
        else:
            step["input_binding"] = None
    if "url" in patch and isinstance(patch["url"], str):
        url = str(patch["url"]).strip()
        step["url"] = url
        action = step.get("action")
        if isinstance(action, dict):
            action["url"] = url
            step["action"] = action
        signals = dict(step.get("signals") or {})
        context = dict(signals.get("context") or {})
        context["page_url"] = url
        signals["context"] = context
        step["signals"] = signals
    if "semantic_description" in patch and isinstance(patch["semantic_description"], str):
        # Human-readable per-step description (the workflow-intent graph's prose,
        # edited from Human Edit). Deliberately NOT funneled into intent/final_intent —
        # the machine token drives deterministic logic and is validated separately.
        step["semantic_description"] = str(patch["semantic_description"]).strip()
    if "check_kind" in patch and isinstance(patch["check_kind"], str):
        step["check_kind"] = str(patch["check_kind"]).strip() or "url"
    if "check_pattern" in patch and isinstance(patch["check_pattern"], str):
        step["check_pattern"] = str(patch["check_pattern"]).strip()
    if "check_selector" in patch and isinstance(patch["check_selector"], str):
        step["check_selector"] = str(patch["check_selector"]).strip()
    if "check_text" in patch and isinstance(patch["check_text"], str):
        step["check_text"] = str(patch["check_text"]).strip()
    if "check_threshold" in patch:
        try:
            step["check_threshold"] = float(patch["check_threshold"])
        except (TypeError, ValueError):
            pass


def _normalize_step_anchor_blocks(step: dict[str, Any]) -> dict[str, Any]:
    out = dict(step)
    signals = dict(out.get("signals") or {})
    recovery = dict(out.get("recovery") or {})
    signals["anchors"] = normalize_anchor_list(signals.get("anchors") or [])
    recovery["anchors"] = normalize_anchor_list(recovery.get("anchors") or [])
    out["signals"] = signals
    out["recovery"] = recovery
    return out


def _sync_recovery_deterministic(step: dict[str, Any]) -> dict[str, Any]:
    """After a non-LLM patch, align recovery strategies with intent + wait_for (deterministic)."""
    pol = get_policy_bundle().data
    intent = get_effective_intent_from_skill_step(step) or str(step.get("intent") or "").strip()
    if not recovery_enabled_for_action(step.get("action")):
        out = dict(step)
        out["recovery"] = no_recovery_block(intent)
        return out
    recovery = dict(step.get("recovery") or {})
    recovery["intent"] = intent
    recovery["final_intent"] = intent
    recovery["strategies"] = recovery_strategies_for_intent(intent, pol)
    wf = (step.get("validation") or {}).get("wait_for") or {}
    recovery = merge_recovery_strategies_for_wait_shape(recovery, dict(wf) if isinstance(wf, dict) else {}, pol)
    out = dict(step)
    out["recovery"] = recovery
    return out


def _sync_destructive_from_consequence(step: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """A reviewer's (or the copilot's) edit of ``consequence`` must reach the runtime.

    The runtime reads only ``step.destructive``, exported from ``identity_bundle.destructive``
    (skill_package_builder_saved_skill.py) — never ``consequence``. Without this shadow sync,
    marking a step irreversible in Human Edit changed the label and nothing else, so the test
    guard and dry-run would still treat it as safe."""
    if "consequence" not in patch:
        return step
    bundle = step.get("identity_bundle")
    if not isinstance(bundle, dict):
        return step
    out = dict(step)
    out["identity_bundle"] = {**bundle, "destructive": str(step.get("consequence") or "") == "irreversible"}
    return out


def sync_derived_step_fields(
    step: dict[str, Any], patch: dict[str, Any], *, sync_recovery: bool = True
) -> dict[str, Any]:
    """Recompute every field that is a deterministic shadow of an edited source field.

    After any merge of ``patch`` into ``step`` (whichever merge the caller used), this
    re-derives: ``signals.semantic.final_intent``/``llm_intent`` from ``intent``,
    ``input_binding`` from ``value``, ``action.url`` + ``signals.context.page_url`` from
    ``url``, and — when ``sync_recovery`` — the deterministic recovery block from the
    effective intent + ``validation.wait_for``.

    This is the single place that keeps derived shadows coherent, so no edit path (live
    editor, 1-click fix, retarget) can forget one (audit findings C-2/M-1/M-2). Branch-body
    leaf steps pass ``sync_recovery=False`` — they never enter the recovery cascade
    (CLAUDE.md Key Invariants), so their recovery block must stay a no-op marker.
    """
    out = dict(step)
    _apply_top_level_step_fields(out, patch, sanitize_intent=False)
    out = _normalize_step_anchor_blocks(out)
    out = _sync_destructive_from_consequence(out, patch)
    if sync_recovery:
        out = _sync_recovery_deterministic(out)
    return out


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_step_patch(
    document: dict[str, Any],
    step_index: int,
    patch: dict[str, Any],
    *,
    assist_llm: bool = True,
) -> dict[str, Any]:
    """Returns a new document dict with step merged and meta.version incremented.

    When ``assist_llm`` is False (editor saves), semantic enrichment LLM is skipped and
    recovery strategies are recomputed deterministically from intent + wait_for.
    """
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("step_index_out_of_range")
    step = dict(steps[step_index])
    for key in (
        "target",
        "signals",
        "frame",
        "validation",
        "recovery",
        "confidence_protocol",
        "decision_policy",
    ):
        if key in patch and isinstance(patch[key], dict):
            base = step.get(key) or {}
            step[key] = deep_merge(dict(base), dict(patch[key]))
    _apply_top_level_step_fields(step, patch)
    step = _normalize_step_anchor_blocks(step)
    user_edited_recovery = "recovery" in patch and isinstance(patch.get("recovery"), dict)
    if assist_llm:
        step = _enhance_step_with_llm(step, user_edited_recovery=user_edited_recovery)
        if _target_identity_changed(patch):
            step = _regenerate_compiled_selectors(step, doc)
    else:
        step = _sync_recovery_deterministic(step)
    steps[step_index] = step
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def revalidate_step(step: dict[str, Any]) -> dict[str, Any]:
    """Deterministic checks after a user fix (no browser execution)."""
    ref = _build_reference_from_signals(step)
    issues = audit_reference(ref)
    proto = step.get("confidence_protocol") if isinstance(step.get("confidence_protocol"), dict) else None
    self_check = layered_decision(ref, ref, protocol=proto)
    return {"audit_issues": issues, "self_check": self_check}
