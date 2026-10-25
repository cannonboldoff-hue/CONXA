"""Gates a Human Review Copilot proposal against the editor's own patch gate before it is ever
shown, and resolves it back to a live step at accept time (BUILD-26 stages c1/c2/c3).

The copilot never writes a selector: `_ALLOWED_FIELDS` excludes `target`, `identity_bundle`,
`compiled_selectors`, and every selector-bearing field — the same category as the two sanctioned
re-compile exceptions in CLAUDE.md, deliberately narrower (it never touches an element address
at all, only meaning fields already writable through `cmd_patch_step`).

A proposal stores `step_key` (`compiler/step_key.py`), never a baked `step_index` — an insert,
delete, or reorder between when the proposal was shown and when it is accepted renumbers every
later step (BUILD-22/BUILD-23), which is exactly the failure `editor/edit_log.py`'s docstring
warns about for the review-edit log. `resolve_step_index` re-resolves the key at accept time and
refuses cleanly if it is gone; `cmd_patch_step`'s own `_apply_step_patch` re-runs
`validate_editor_patch` on the resolved step regardless, so accept time is never trusting a
gate result computed against a document that may no longer exist.
"""

from __future__ import annotations

import uuid
from typing import Any

from conxa_compile.compiler.second_opinion import build_try_dismiss_from_hint
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.editor.action_registry import SELECTOR_ACTIONS, action_spec, is_supported_action
from conxa_compile.editor.describe import describe_step
from conxa_compile.editor.overlay_identity import bundle_from_descriptor
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.workflow_mutations import (
    _new_manual_step,
    merge_skill_inputs,
    parameterize_click_text,
    parameterize_literal,
    replace_string_literals_in_skill_document,
)
from conxa_compile.policy.bundle import get_policy_bundle

# BUILD-26 stage g: widened from the original five to every field that actually changes
# execution behaviour without ever touching an element address — see CLAUDE.md's correction that
# recovery.max_attempts/no_recovery_block are NOT here because the runtime never reads them.
# ai_review_*/handover_* aren't listed here since they're only meaningful on those two kinds —
# `gate_proposals` below intersects this set against what `validate_editor_patch` accepts for the
# step's actual kind, so an ai_review_* field proposed against a click step is dropped there.
_ALLOWED_FIELDS = frozenset({
    "value", "input_binding", "intent", "semantic_description", "validation.assertions",
    "consequence", "entity_binding.confirmed", "branch.timeout_ms",
    "for_each.max_iterations", "for_each.on_row_error", "handler_hints.hover_chain",
    "ai_review_prompt", "ai_review_output_schema", "ai_review_on_failure", "ai_review_default_value",
    "handover_on_failure", "handover_resume_when", "handover_resume_when_timeout_ms",
})


class ProposalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _steps_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    skills = doc.get("skills") if isinstance(doc, dict) else None
    block0 = skills[0] if isinstance(skills, list) and skills and isinstance(skills[0], dict) else {}
    steps = block0.get("steps")
    return steps if isinstance(steps, list) else []


def _resolve_step(doc: dict[str, Any], step_key: str) -> tuple[int, dict[str, Any]] | None:
    steps = _steps_of(doc)
    for i, key in enumerate(step_keys(steps)):
        if key == step_key:
            return i, steps[i]
    return None


def _current_value(step: dict[str, Any], field: str) -> Any:
    cur: Any = step
    for part in field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _patch_for(field: str, value: Any) -> dict[str, Any]:
    """The LLM names one dotted field (e.g. "validation.assertions"); cmd_patch_step wants a
    nested patch dict ({"validation": {"assertions": value}})."""
    if "." not in field:
        return {field: value}
    top, rest = field.split(".", 1)
    return {top: _patch_for(rest, value)}


def gate_proposals(doc: dict[str, Any], raw_proposals: list[Any]) -> list[dict[str, Any]]:
    """Filter+shape raw {step_key, field, patch, why} objects from the LLM into proposals safe
    to render. Each candidate is pre-validated against `validate_editor_patch` so an accepted
    one is never rejected by the gate a second later. A proposal that fails any check is simply
    dropped, never shown with an error — a malformed field name from the model is not the
    reviewer's problem to see."""
    policy = get_policy_bundle().data
    steps = _steps_of(doc)
    out: list[dict[str, Any]] = []
    for raw in raw_proposals:
        if not isinstance(raw, dict):
            continue
        step_key = str(raw.get("step_key") or "").strip()
        field = str(raw.get("field") or "").strip()
        if not step_key or field not in _ALLOWED_FIELDS:
            continue
        resolved = _resolve_step(doc, step_key)
        if resolved is None:
            continue
        step_index, step = resolved
        value = raw.get("patch")
        patch = _patch_for(field, value)
        previous_step = steps[step_index - 1] if step_index > 0 else None
        try:
            validate_editor_patch(step, patch, policy, previous_step=previous_step)
        except ValueError:
            continue
        out.append({
            "id": str(uuid.uuid4()),
            "step_key": step_key,
            "command": "patch_step",
            "field": field,
            "patch": patch,
            "why": str(raw.get("why") or "").strip(),
            "evidence_refs": _evidence_refs(raw),
            "preview": {"before": _current_value(step, field), "after": value},
        })
    return out


def _evidence_refs(raw: dict[str, Any]) -> list[str]:
    """BUILD-26 stage g: cosmetic passthrough for the two pre-existing proposal kinds (their
    system prompt now asks for evidence_refs too, but neither gate REQUIRES it — that would
    silently break every proposal a model produced before this field existed). Required and
    enforced only on the new structural-ops kind below, which has no legacy shape to preserve."""
    refs = raw.get("evidence_refs")
    return [str(r).strip() for r in refs if str(r).strip()] if isinstance(refs, list) else []


def gate_overlay_proposals(
    doc: dict[str, Any], observed_overlays: list[dict[str, Any]], raw_proposals: list[Any]
) -> list[dict[str, Any]]:
    """BUILD-26 stage f: gates a SECOND proposal kind — inserting an if_present/try_dismiss
    branch built from an overlay the runtime actually observed (`overlays.jsonl` via
    `editor/evidence.py`'s `observed_overlays`), never a selector the model invented.

    The raw shape is `{overlay_id, control_index, primitive, after_step_key, why}` — an index
    into the run's captured overlay set plus a choice of primitive, NOT a target or selector
    field. `overlay_id` must name something this evidence bundle actually observed; a proposal
    referencing an unknown overlay is dropped, the same as an unresolvable step_key above.
    Every selector is built by `editor/overlay_identity.py::bundle_from_descriptor`
    (deterministic) or `compiler/second_opinion.py::build_try_dismiss_from_hint` (the ONE
    sanctioned try_dismiss builder — this is its third caller) — the model never writes one.
    """
    keys = step_keys(_steps_of(doc))
    overlays_by_id = {
        str(o.get("overlay_id")): o for o in (observed_overlays or [])
        if isinstance(o, dict) and o.get("overlay_id")
    }
    policy = get_policy_bundle().data
    out: list[dict[str, Any]] = []
    for raw in raw_proposals:
        if not isinstance(raw, dict):
            continue
        overlay_id = str(raw.get("overlay_id") or "").strip()
        overlay = overlays_by_id.get(overlay_id)
        if overlay is None:
            continue  # the model cannot reference an overlay nobody observed
        primitive = str(raw.get("primitive") or "").strip()
        if primitive not in ("try_dismiss", "if_present"):
            continue
        after_step_key = str(raw.get("after_step_key") or "").strip() or None
        if after_step_key is not None and after_step_key not in keys:
            continue

        container_signal = str((overlay.get("container") or {}).get("signal") or "").strip()
        controls = overlay.get("controls") or []
        control_index = raw.get("control_index")
        control = controls[control_index] if isinstance(control_index, int) and 0 <= control_index < len(controls) else None
        control_bundle = bundle_from_descriptor(control) if control else None
        control_selector = control_bundle.signals[0].selector if control_bundle and control_bundle.signals else ""

        if primitive == "if_present" and not (control_bundle and container_signal):
            # No synthesizable nested click target — fall back to the primitive that needs no
            # bundle at all, rather than dropping the proposal outright.
            primitive = "try_dismiss"

        if primitive == "try_dismiss":
            if not container_signal and not control_selector:
                continue
            built = build_try_dismiss_from_hint(control_selector, container_signal)
            scaffold = _new_manual_step("try_dismiss", "")
            merged_patch = {"intent": built["intent"], "branch": built["branch"], "recovery": built["recovery"]}
            nested_step = None
        else:  # if_present
            scaffold = _new_manual_step("if_present", "")
            merged_patch = {
                "intent": "dismiss_if_present",
                "target": {"primary_selector": container_signal, "fallback_selectors": []},
            }
            nested_step = {
                "target": {"primary_selector": control_selector, "fallback_selectors": []},
                "identity_bundle": control_bundle.model_dump(mode="json") if control_bundle else None,
                "intent": "click_target",
                "semantic_description": (
                    f"Dismiss the observed overlay ({control.get('name') or control.get('text') or 'control'})"
                    if control else "Dismiss the observed overlay"
                ),
            }

        try:
            validate_editor_patch(scaffold, merged_patch, policy)
            if nested_step is not None:
                # Best-effort pre-check of the nested step's own patchability — the definitive
                # check still re-runs at accept time against the real inserted scaffold, exactly
                # as resolve_step_index/cmd_patch_step already do for the patch_step kind above.
                nested_scaffold = _new_manual_step("click", "")
                validate_editor_patch(nested_scaffold, nested_step, policy, in_branch_body=True)
        except ValueError:
            continue

        out.append({
            "id": str(uuid.uuid4()),
            "step_key": None,
            "command": "insert_overlay_branch",
            "primitive": primitive,
            "overlay_id": overlay_id,
            "after_step_key": after_step_key,
            "patch": merged_patch,
            "nested_step": nested_step,
            "why": str(raw.get("why") or "").strip(),
            "evidence_refs": _evidence_refs(raw),
            "preview": {
                "before": None,
                "after": f"Insert {primitive} after {'step ' + after_step_key if after_step_key else 'the last step'}",
            },
        })
    return out


def identity_patch_from_source(action_kind: str, source_step: dict[str, Any]) -> dict[str, Any]:
    """The patch that points a newly inserted step at `source_step`'s element. A branch step
    (if_present / try_dismiss / wait_for_one_of) is refused a root `identity_bundle` by the patch gate
    — its element lives in the branch body — so a "wait for this element" insert of that kind carries
    the source's selector and bundle as its one wait_for_one_of option instead of being refused."""
    target = source_step.get("target")
    bundle = source_step.get("identity_bundle")
    if action_kind != "wait_for_one_of":
        return {"identity_bundle": bundle, "target": target} if action_kind not in {"if_present", "try_dismiss"} else {"target": target}
    selector = str((target or {}).get("primary_selector") or "").strip()
    return {"branch": {"options": [{"selector": selector, "identity_bundle": bundle}]}} if selector else {}


def resolve_step_index(doc: dict[str, Any], step_key: str) -> int:
    """Re-resolve `step_key` to the CURRENT `step_index` at accept time. Raises ProposalError
    (never a bare ValueError) when the step is gone — an insert/delete/reorder happened between
    the proposal being shown and being accepted."""
    resolved = _resolve_step(doc, step_key)
    if resolved is None:
        raise ProposalError(
            "proposal_stale",
            "This step no longer exists — the workflow changed since this was proposed.",
        )
    return resolved[0]


_STRUCTURAL_OPS = frozenset({"insert_step", "delete_step", "move_step", "update_inputs", "replace_literals", "parameterize"})

# The two code-owned rewrites behind `parameterize`, in the order they are applied. The model only
# names the recorded value and the input; these decide which places change.
_PARAMETERIZERS = (("click", parameterize_click_text), ("value", parameterize_literal))


def proposal_summary(p: dict[str, Any]) -> str:
    """One plain sentence for a gated proposal, written by code from its validated fields — shown on
    the card and given to the reply, so what is said and what is offered cannot drift apart."""
    cmd, op = p.get("command"), p.get("op")
    if cmd == "insert_overlay_branch":
        return str((p.get("preview") or {}).get("after") or "Add a step to dismiss a popup")
    if cmd == "patch_step":
        return f"Change “{p.get('field')}” on step {p.get('step_key')}"
    if op == "parameterize":
        return (f"Make “{p.get('literal')}” a run input called {p.get('name')} "
                f"({p.get('changed')} place{'s' if p.get('changed') != 1 else ''}); the recorded value stays its default")
    if op == "update_inputs":
        return f"Set the run inputs to: {', '.join(str(i.get('id')) for i in p.get('inputs') or []) or 'none'}"
    if op == "replace_literals":
        return f"Replace “{p.get('find')}” with “{p.get('replace')}” in typed values"
    return str((p.get("preview") or {}).get("after") or p.get("why") or "Change the steps")


def gate_structural_proposals(doc: dict[str, Any], raw_proposals: list[Any]) -> list[dict[str, Any]]:
    """BUILD-26 stage g: a THIRD proposal kind — typed structural ops (insert/delete/move a step,
    or a workflow-level input/literal edit) — gated the same way the other two are: pre-validated
    here so an accepted op is never rejected by the gate a second later, dropped silently on any
    failure, and re-resolved by step_key (never a baked index) at accept time.

    Unlike the other two kinds, every op here REQUIRES a non-empty `evidence_refs` — this kind has
    no legacy shape to preserve, so the plan's "no ref → the gate drops it" rule is enforced from
    day one rather than only advisory.

    The selector rule is enforced exactly once, here: `insert_step` of a kind in SELECTOR_ACTIONS
    is dropped unless it names `identity_from_step_key`, an EXISTING step whose identity_bundle is
    then copied verbatim — the model names a source, Python copies the signal, the model never
    writes one. A kind not in SELECTOR_ACTIONS (ai_review, handover, wait, check, assert,
    navigate, scroll, screenshot) never needs one.
    """
    policy = get_policy_bundle().data
    out: list[dict[str, Any]] = []
    for raw in raw_proposals:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "").strip()
        if op not in _STRUCTURAL_OPS:
            continue
        evidence_refs = _evidence_refs(raw)
        if not evidence_refs:
            continue
        why = str(raw.get("why") or "").strip()
        base = {"id": str(uuid.uuid4()), "command": "structural_op", "op": op, "why": why, "evidence_refs": evidence_refs}

        if op == "insert_step":
            action_kind = str(raw.get("action_kind") or "").strip().lower().replace("-", "_")
            if not is_supported_action(action_kind) or not action_spec(action_kind).insertable:
                continue
            after_step_key = str(raw.get("after_step_key") or "").strip() or None
            if after_step_key is not None and _resolve_step(doc, after_step_key) is None:
                continue
            identity_from_step_key = str(raw.get("identity_from_step_key") or "").strip() or None
            if action_kind in SELECTOR_ACTIONS:
                if not identity_from_step_key:
                    continue
                source = _resolve_step(doc, identity_from_step_key)
                if source is None or not (source[1].get("identity_bundle") or {}):
                    continue
            elif identity_from_step_key:
                continue  # a non-selector kind carries no target — a source here is a model error
            raw_fields = raw.get("fields")
            fields: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
            allowed_fields = {f: v for f, v in fields.items() if f in _ALLOWED_FIELDS}
            # Best-effort pre-check against a scaffold of the right kind — the definitive check
            # re-runs at accept time against the real inserted step, same as every other kind here.
            try:
                scaffold = _new_manual_step(action_kind, "")
                for field, value in allowed_fields.items():
                    validate_editor_patch(scaffold, _patch_for(field, value), policy)
            except ValueError:
                continue
            out.append({
                **base, "action_kind": action_kind, "after_step_key": after_step_key,
                "fields": allowed_fields, "identity_from_step_key": identity_from_step_key,
                "preview": {"before": None, "after": f"Insert {action_kind} after "
                            f"{'step ' + after_step_key if after_step_key else 'the last step'}"},
            })

        elif op == "delete_step":
            step_key = str(raw.get("step_key") or "").strip()
            resolved = _resolve_step(doc, step_key)
            if not step_key or resolved is None:
                continue
            out.append({**base, "step_key": step_key, "preview": {"before": describe_step(resolved[1], resolved[0]), "after": None}})

        elif op == "move_step":
            step_key = str(raw.get("step_key") or "").strip()
            after_step_key = raw.get("after_step_key")
            after_step_key = str(after_step_key).strip() if after_step_key else None
            if not step_key or _resolve_step(doc, step_key) is None:
                continue
            if after_step_key is not None and _resolve_step(doc, after_step_key) is None:
                continue
            if after_step_key == step_key:
                continue
            out.append({**base, "step_key": step_key, "after_step_key": after_step_key,
                        "preview": {"before": None, "after": f"Move to after "
                                    f"{'step ' + after_step_key if after_step_key else 'the start'}"}})

        elif op == "update_inputs":
            inputs = raw.get("inputs")
            if not isinstance(inputs, list):
                continue
            try:
                merge_skill_inputs(doc, inputs, None)  # the same trust boundary the accept runs
            except ValueError:
                continue
            out.append({**base, "inputs": inputs, "preview": {"before": doc.get("inputs"), "after": inputs}})

        elif op == "replace_literals":
            find = str(raw.get("find") or "")
            replace = str(raw.get("replace") or "")
            if not find:
                continue
            try:
                _, matches = replace_string_literals_in_skill_document(doc, find, replace)
            except ValueError:
                continue
            if not matches:
                continue
            out.append({**base, "find": find, "replace": replace, "preview": {"before": find, "after": replace}})

        elif op == "parameterize":
            literal = str(raw.get("literal") or "").strip()
            name = str(raw.get("name") or "").strip()
            if not literal or not name:
                continue
            # Dry-run each code-owned rewrite on a copy (both are pure and never raise on a
            # no-match other than ValueError); keep only those that would really change the workflow.
            kinds: list[str] = []
            changed = 0
            for kind, mutate in _PARAMETERIZERS:
                try:
                    _, n = mutate(doc, literal, name, default=literal)
                except ValueError:
                    continue
                if n:
                    kinds.append(kind)
                    changed += n
            if not kinds:
                continue
            out.append({**base, "literal": literal, "name": name, "kinds": kinds, "changed": changed,
                        "preview": {"before": literal, "after": "{{%s}}" % name}})

    return out
