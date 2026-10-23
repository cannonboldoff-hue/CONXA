"""Structural edits to a persisted skill document (reorder/insert/delete/replace).

Split out of workflow_service.py: this half performs the workflow editor's
structural mutations. Calls into workflow_dto.py for revalidation
(_build_reference_for_audit, collect_suggestions) after each edit.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from conxa_compile.compiler.action_policy import no_recovery_block
from conxa_compile.compiler.second_opinion import build_try_dismiss_from_hint
from conxa_compile.compiler.patch import revalidate_step
from conxa_compile.compiler.loop_suggestion import replace_url_literal, url_contains_literal
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.compiler.upload_binding import (
    _RUNTIME_ONLY_PLACEHOLDER_RE,
    for_each_loop_variable_names,
)
from conxa_compile.confidence.uncertainty import audit_reference
from conxa_compile.editor.action_registry import action_spec, default_action_value, is_supported_action
from conxa_compile.editor.dto import SkillInputVariable
from conxa_compile.editor.placeholder_grammar import PLACEHOLDER_RE
from conxa_compile.editor.workflow_dto import _build_reference_for_audit, collect_suggestions
from conxa_compile.policy.bundle import get_policy_bundle
from pydantic import ValidationError as PydanticValidationError


def _invalidate_compile_report(doc: dict[str, Any]) -> None:
    """Mark `compile_report` stale in place after a mutation changes the top-level step list's
    length, order, or per-step action/intent.

    compile_report (compiler/build.py::_build_compile_report) is built once at compile time and
    is indexed by top-level step position. A reorder/insert/delete shifts every later index out
    from under it; workflow_dto.py::_compile_health then surfaces confidence/warnings against
    steps that no longer exist at those positions — Human Edit's compile-health banner pointed an
    approver at the wrong step. Clearing `steps` (rather than trying to patch indices) is the
    only way to stop misleading step-index buttons; only a real recompile can produce
    trustworthy per-step confidence again.

    Deliberately leaves `compile_report["second_opinion"]` (BUILD-25) in place: unlike `steps`,
    it is keyed on step_key (compiler/step_key.py), not position, so a reorder/insert/delete
    does not invalidate it — this is the whole reason that pass keys on identity instead of
    index. It is an audit record of what the pass already wrote, not a pending action, so it
    stays true regardless of what happens to the step list afterwards."""
    report = doc.get("compile_report")
    if not isinstance(report, dict) or not report:
        return
    report = dict(report)
    report["status"] = "stale"
    report["steps"] = []
    doc["compile_report"] = report


def validate_skill_document(document: dict[str, Any]) -> dict[str, Any]:
    policy = get_policy_bundle().data
    steps_raw = (document.get("skills") or [{}])[0].get("steps") or []
    if not isinstance(steps_raw, list):
        steps_raw = []
    per_step: list[dict[str, Any]] = []
    for idx, s in enumerate(steps_raw):
        step = dict(s)
        ref = _build_reference_for_audit(step)
        per_step.append(
            {
                "step_index": idx,
                "audit_issues": audit_reference(ref),
                "revalidation": revalidate_step(step),
            }
        )
    return {"steps": per_step, "suggestions": [m.model_dump() for m in collect_suggestions([dict(s) for s in steps_raw], policy)]}


def reorder_steps(document: dict[str, Any], new_order: list[int]) -> dict[str, Any]:
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    n = len(steps)
    if sorted(new_order) != list(range(n)):
        raise ValueError("invalid_reorder_permutation")
    new_steps = [dict(steps[i]) for i in new_order]
    block["steps"] = new_steps
    skills[0] = block
    doc["skills"] = skills
    doc = _remap_intent_graph_indices(doc, {new_order[p]: p for p in range(n)})
    _invalidate_compile_report(doc)
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def delete_step_at(document: dict[str, Any], step_index: int) -> dict[str, Any]:
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("step_index_out_of_range")
    original_len = len(steps)
    del steps[step_index]
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    doc = _remap_intent_graph_indices(
        doc,
        {i: (None if i == step_index else i if i < step_index else i - 1) for i in range(original_len)},
    )
    _invalidate_compile_report(doc)
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def _branch_body_steps(document: dict[str, Any], step_index: int) -> tuple[dict[str, Any], list[Any]]:
    """Resolve `steps[step_index]["branch"]["steps"]` — the only branch body currently editable
    (if_present's nested body; try_dismiss/wait_for_one_of have no editable nested body — see
    patch_gate.py::_validate_branch_patch). Returns (parent_step_dict, nested_steps_list)."""
    skills = list(document.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("step_index_out_of_range")
    parent = dict(steps[step_index])
    action = parent.get("action") if isinstance(parent.get("action"), dict) else {}
    kind = str(action.get("action") or "").strip().lower()
    if kind != "if_present":
        raise ValueError("branch_body_only_editable_for_if_present")
    branch = dict(parent.get("branch") or {})
    nested = list(branch.get("steps") or [])
    return parent, nested


def _save_branch_body_steps(
    document: dict[str, Any], step_index: int, nested: list[Any]
) -> dict[str, Any]:
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    parent = dict(steps[step_index])
    branch = dict(parent.get("branch") or {})
    branch["steps"] = nested
    parent["branch"] = branch
    steps[step_index] = parent
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def insert_branch_step(
    document: dict[str, Any], step_index: int, action_kind: str, insert_after: int | None = None
) -> dict[str, Any]:
    """Insert a new leaf step into an if_present step's nested body — mirrors insert_step_after
    but scoped to `steps[step_index]["branch"]["steps"]`. Branch bodies are best-effort (never
    enter Tier 1-4 recovery), but the scaffolded step itself is an ordinary leaf step shape —
    only its own recovery/validation are meaningless there (enforced at patch time, not here)."""
    _, nested = _branch_body_steps(document, step_index)
    if insert_after is None:
        insert_at = len(nested)
        anchor_index = len(nested) - 1
    else:
        if insert_after < -1 or insert_after >= len(nested):
            raise ValueError("branch_step_index_out_of_range")
        insert_at = insert_after + 1
        anchor_index = insert_after
    nested.insert(insert_at, _new_manual_step(action_kind, _last_known_page_url(nested, anchor_index)))
    return _save_branch_body_steps(document, step_index, nested)


def delete_branch_step(document: dict[str, Any], step_index: int, nested_index: int) -> dict[str, Any]:
    _, nested = _branch_body_steps(document, step_index)
    if nested_index < 0 or nested_index >= len(nested):
        raise ValueError("branch_step_index_out_of_range")
    del nested[nested_index]
    return _save_branch_body_steps(document, step_index, nested)


def reorder_branch_steps(document: dict[str, Any], step_index: int, new_order: list[int]) -> dict[str, Any]:
    _, nested = _branch_body_steps(document, step_index)
    n = len(nested)
    if sorted(new_order) != list(range(n)):
        raise ValueError("invalid_reorder_permutation")
    reordered = [dict(nested[i]) for i in new_order]
    return _save_branch_body_steps(document, step_index, reordered)


def set_fork_policy(
    document: dict[str, Any], step_index: int, state_id: str, policy: dict[str, Any]
) -> dict[str, Any]:
    """The author's decision for one popup state of an outcome fork -- the ONLY writer of a state's
    ``policy`` and so of ``author_approved`` (patch_gate refuses a generic patch to a fork, and the
    compiler always writes ``author_approved: false``).

    ``policy`` is one of ``{"mode": "auto", "option_id"}`` (the author picked an option to run every
    time; picking it here IS the approval, including for a consequential one), ``{"mode":
    "by_input"[, "input"]}`` (the person running it chooses per run: declares a select input whose
    options are the option labels plus "ask", default "ask"), ``{"mode": "ask_human"}`` or
    ``{"mode": "stop"[, "message"]}``. A state the author has answered is marked ``reviewed`` so it
    is never asked about again."""
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("step_index_out_of_range")
    step = dict(steps[step_index])
    action = step.get("action")
    if (action.get("action") if isinstance(action, dict) else action) != "outcome_fork":
        raise ValueError("step_is_not_an_outcome_fork")
    fork = dict(step.get("outcome_fork") or {})
    states = [dict(s) for s in fork.get("states") or [] if isinstance(s, dict)]
    state = next((s for s in states if s.get("id") == state_id), None)
    if state is None:
        raise ValueError("fork_state_not_found")
    options = [o for o in state.get("options") or [] if isinstance(o, dict)]

    mode = str((policy or {}).get("mode") or "")
    new_policy: dict[str, Any]
    if mode == "auto":
        option_id = str(policy.get("option_id") or "")
        if option_id not in {o.get("id") for o in options}:
            raise ValueError("fork_option_not_found")
        new_policy = {"mode": "auto", "option_id": option_id, "author_approved": True}
    elif mode == "by_input":
        input_id = re.sub(r"[^a-z0-9_]+", "_", str(policy.get("input") or "on_popup").lower()).strip("_") or "on_popup"
        new_policy = {"mode": "by_input", "input": input_id}
        recorded = next((o.get("id") for o in options if o.get("recorded")), None)
        if recorded:
            new_policy["suggested"] = recorded
    elif mode == "ask_human":
        recorded = next((o.get("id") for o in options if o.get("recorded")), None)
        new_policy = {"mode": "ask_human", **({"suggested": recorded} if recorded else {})}
    elif mode == "stop":
        new_policy = {"mode": "stop", "message": str(policy.get("message") or f"Needs a decision: {(state.get('signature') or {}).get('title') or 'a popup'}")}
    else:
        raise ValueError("fork_policy_mode_invalid")
    new_policy["reviewed"] = True
    state["policy"] = new_policy
    fork["states"] = [state if s.get("id") == state_id else s for s in states]
    step["outcome_fork"] = fork

    inputs = [dict(i) for i in doc.get("inputs") or [] if isinstance(i, dict)]
    if mode == "by_input":
        input_id = new_policy["input"]
        spec = {
            "id": input_id,
            "type": "select",
            "options": [str(o.get("label") or o.get("id")) for o in options] + ["ask"],
            "default": "ask",
            "optional": True,
        }
        inputs = [i for i in inputs if str(i.get("id") or "").lower() != input_id.lower()] + [spec]
        _validate_skill_inputs(inputs)
        # The step names the input it reads, so "this input is unused" never flags it.
        step["input_binding"] = input_id
        step["value"] = "{{" + input_id + "}}"
    elif step.get("input_binding") and not any(
        s.get("policy", {}).get("mode") == "by_input" for s in fork["states"]
    ):
        step["input_binding"] = None
        step["value"] = None
    doc["inputs"] = inputs

    steps[step_index] = step
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    _invalidate_compile_report(doc)
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def apply_variant_observation(
    document: dict[str, Any], step_index: int, session_id: str, observation: dict[str, Any]
) -> dict[str, Any]:
    """Fold what a variant recording showed into an outcome fork WITHOUT recompiling.

    ``observation`` is compiler/variant_align.py::compare_variant's result. A clean path the variant
    observed flips ``clean.observed``; a decision popup only the variant saw becomes a new state.
    Anything in ``unexplained`` is deliberately ignored here -- it is reported to the author, never
    merged. Existing states, their policies and the author's other edits are untouched, which is
    exactly why this is a mutation on the saved skill and not a second compile."""
    from conxa_compile.compiler.outcome_fork import state_from_dialog
    from conxa_core.models.skill_spec import SkillStep

    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("step_index_out_of_range")
    step = dict(steps[step_index])
    action = step.get("action")
    if (action.get("action") if isinstance(action, dict) else action) != "outcome_fork":
        raise ValueError("step_is_not_an_outcome_fork")
    fork = dict(step.get("outcome_fork") or {})
    states = [dict(s) for s in fork.get("states") or [] if isinstance(s, dict)]

    if observation.get("clean_observed"):
        clean = dict(fork.get("clean") or {})
        seen = [x for x in clean.get("observed_in") or [] if x != session_id] + [session_id]
        fork["clean"] = {"observed": True, "observed_in": seen}

    template_dict = next(
        (o["steps"][0] for s in states for o in s.get("options") or [] if isinstance(o, dict) and o.get("steps")), None
    )
    if template_dict is not None:
        template = SkillStep.model_validate(template_dict)
        used = {str(s.get("id")) for s in states}
        for ctx in observation.get("new_dialogs") or []:
            new_state = state_from_dialog(ctx, template, session_id, used)
            if new_state is not None:
                states.append(new_state)
                used.add(new_state["id"])
    fork["states"] = states
    step["outcome_fork"] = fork
    steps[step_index] = step
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    _invalidate_compile_report(doc)
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def confirm_optional_interstitial(
    document: dict[str, Any], step_index: int, keep_as_step: bool = False
) -> dict[str, Any]:
    """Human-gated resolution of a recorder-flagged optional interstitial (recording-next-steps.md
    Priority 2; SkillStep.optional_hint).

    This is the review-time half of the conversion. The compiler's second-opinion pass
    (compiler/second_opinion.py) converts the hints it is confident about at compile time; this
    handles the ones it left alone, when a reviewer decides the step really is optional. Both
    call build_try_dismiss_from_hint so the two paths cannot produce different branch shapes.

    keep_as_step=True is the reviewer's "No" answer (mandatory Human Edit review questions): the
    hint is cleared without converting the step, so it never resurfaces as a pending question,
    but the step's action/branch are left exactly as compiled.
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
    hint = step.get("optional_hint") if isinstance(step.get("optional_hint"), dict) else None
    if not hint:
        raise ValueError("step_has_no_optional_hint")

    if not keep_as_step:
        target = step.get("target") if isinstance(step.get("target"), dict) else {}
        built = build_try_dismiss_from_hint(
            str(target.get("primary_selector") or ""),
            str(hint.get("container_signal") or ""),
        )
        action = dict(step.get("action") if isinstance(step.get("action"), dict) else {})
        action["action"] = "try_dismiss"
        step["action"] = action
        step["intent"] = built["intent"]
        step["branch"] = built["branch"]
        step["recovery"] = built["recovery"]
    step["optional_hint"] = None  # consumed by this confirmation

    steps[step_index] = step
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    _invalidate_compile_report(doc)
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def _last_known_page_url(steps: list[Any], insert_after: int) -> str:
    for raw in reversed(steps[: insert_after + 1]):
        step = dict(raw) if isinstance(raw, dict) else {}
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        url = str(action.get("url") or step.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
        signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
        context = signals.get("context") if isinstance(signals.get("context"), dict) else {}
        url = str(context.get("page_url") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _new_manual_step(action_kind: str, page_url: str) -> dict[str, Any]:
    kind = action_kind.strip().lower().replace("-", "_")
    if not is_supported_action(kind) or not action_spec(kind).insertable:
        raise ValueError("unsupported_action_kind")

    intent = {
        "navigate": "navigate_to_page",
        "click": "click_target",
        "dblclick": "double_click_target",
        "right_click": "right_click_target",
        "hover": "hover_target",
        "focus": "focus_target",
        "type": "type_into_field",
        "fill": "fill_field",
        "set_checkbox": "set_checkbox",
        "set_radio": "set_radio_option",
        "select": "select_option",
        "select_option": "select_option",
        "date_pick": "pick_date",
        "drag_drop": "drag_and_drop",
        "keyboard_shortcut": "press_keyboard_shortcut",
        "scroll": "scroll_page",
        "check": "check_page_state",
        "assert": "assert_page_state",
        "wait": "wait_for_page",
        "screenshot": "capture_screenshot",
        "upload": "upload_file",
        "if_present": "dismiss_if_present",
        "try_dismiss": "try_dismiss_interstitial",
        "wait_for_one_of": "wait_for_one_of_states",
        "for_each": "process_each_row",
        "ai_review": "ai_review_checkpoint",
    }.get(kind, f"{kind}_target")
    url = page_url if page_url.startswith(("http://", "https://")) else ""
    action: dict[str, Any] = {"action": kind}
    if kind == "navigate":
        action["url"] = url or "https://example.com"
        url = action["url"]
    elif kind == "scroll":
        action["delta"] = 600
    elif kind == "wait":
        action["ms"] = 1000
    default_value = default_action_value(kind)
    if default_value is not None:
        action["value"] = default_value

    step: dict[str, Any] = {
        "action": action,
        "intent": intent,
        "url": url,
        "target": {
            "primary_selector": "",
            "fallback_selectors": [],
        },
        "signals": {
            "dom": {},
            "selectors": {"css": "", "aria": "", "text_based": "", "xpath": ""},
            "semantic": {"final_intent": intent, "llm_intent": intent},
            "context": {"page_url": url, "page_title": ""},
            "anchors": [],
            "visual": {},
        },
        "state": {},
        "value": default_value,
        "input_binding": None,
        "validation": {
            "wait_for": {"type": "none", "timeout": 5000},
            "success_conditions": {},
        },
        "recovery": no_recovery_block(intent),
        "confidence_protocol": {},
        "decision_policy": {},
    }
    if kind == "navigate":
        step["validation"] = {
            "wait_for": {"type": "url_change", "target": url, "timeout": 60000},
            "success_conditions": {"url": url},
        }
    if kind in {"check", "assert"}:
        step["check_kind"] = "url"
        step["check_pattern"] = url
    if kind in {"if_present", "try_dismiss", "wait_for_one_of"}:
        # Branch bodies are best-effort and never enter the Tier 1-4 recovery cascade — see
        # CLAUDE.md Key Invariants. Scaffold an empty body per kind; authored via
        # BranchBodyEditor.tsx (nested steps for if_present) or the probe/candidate/option
        # selector fields directly (try_dismiss/wait_for_one_of) — see patch_gate.py.
        if kind == "if_present":
            step["branch"] = {"steps": [], "timeout_ms": 3000}
        elif kind == "try_dismiss":
            step["branch"] = {"candidates": [], "timeout_ms": 3000, "fallback_escape": True}
        else:  # wait_for_one_of
            step["branch"] = {"options": [], "timeout_ms": 5000, "required": True}
    if kind == "for_each":
        # EXEC-38: same best-effort-scaffold pattern as the branch primitives above, but
        # max_iterations is REQUIRED (the runtime refuses to run an uncapped loop) — scaffold a
        # conservative default rather than leaving it unset, so a freshly-inserted step is at
        # least loadable while the author fills in rows.container_selector and a body.
        step["for_each"] = {
            "rows": {"container_selector": ""},
            "as": "row",
            "max_iterations": 50,
            "on_row_error": "stop",
            "steps": [],
        }
    if kind == "ai_review":
        # EXEC-13: an ai_review step carries no selector/identity_bundle (see
        # skill_package_builder_saved_skill.py's ai_review branch) — these top-level
        # `ai_review_*` fields, not `action`, are what patch_gate.py validates and the
        # serializer reads. The prompt is deliberately left blank: the patch gate rejects a
        # blank prompt on save (`ai_review_prompt_empty`), and a step left unconfigured is
        # dropped at build time with a compile-report warning rather than shipping empty.
        # `output_schema` seeds the canonical "yes/no + why" shape editable in the renderer's
        # preset picker; an author who wants no schema can clear it.
        step["ai_review_prompt"] = ""
        step["ai_review_output_schema"] = {
            "type": "object",
            "required": ["answer", "why"],
            "properties": {
                "answer": {"type": "string", "enum": ["yes", "no"]},
                "why": {"type": "string"},
            },
        }
        step["ai_review_on_failure"] = "abort"
    return step


def insert_step_after(document: dict[str, Any], action_kind: str, insert_after: int | None = None) -> dict[str, Any]:
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    if insert_after is None:
        insert_at = len(steps)
        anchor_index = len(steps) - 1
    else:
        if insert_after < -1 or insert_after >= len(steps):
            raise ValueError("step_index_out_of_range")
        insert_at = insert_after + 1
        anchor_index = insert_after
    original_len = len(steps)
    steps.insert(insert_at, _new_manual_step(action_kind, _last_known_page_url(steps, anchor_index)))
    block["steps"] = steps
    skills[0] = block
    doc["skills"] = skills
    doc = _remap_intent_graph_indices(
        doc, {i: (i if i < insert_at else i + 1) for i in range(original_len)}
    )
    _invalidate_compile_report(doc)
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def apply_for_each_loop_suggestion(
    document: dict[str, Any], suggestion: dict[str, Any], *, remove_redundant_click: bool = True
) -> dict[str, Any]:
    """Apply a `compiler/loop_suggestion.py` finding: wrap the steps between (and including) a
    navigate whose URL names one recorded file and its matching `download_observed` into a new
    `for_each` step driven by a runtime input, then rebind the later upload step to the whole
    run's download folder.

    One atomic document write, not insert-then-patch: `patch_gate.py::_validate_for_each_patch`
    rejects a `steps` key in a `for_each` patch (`for_each_steps_not_patchable_here`), and there
    is no `for_each.steps[N]` path-addressed patch route today (`_BRANCH_STEP_PATH_RE` is
    `branch`-only) — an empty-bodied `for_each` also silently vanishes from `execution.json` at
    package time (`_saved_for_each_step` returns None for an empty body). So the wrap, the body
    rewrite, the upload rebind, and the new input all land in this one document transformation.

    Re-resolves every step_key at apply time (never trusts a stale index) — the same staleness
    discipline `copilot_proposals.py::resolve_step_index` established for chat-driven proposals.

    `remove_redundant_click` is the reviewer's answer to the SEPARATE "remove this leftover
    click too?" question Human Edit asks only when `suggestion["redundant_click_key"]` is set —
    the loop question itself never implies an answer to it (ReviewQuestionsDialog.tsx asks them
    as two distinct popups). The click is never wrapped into the loop body either way (a
    hardcoded click on one fixed filename would fire on every iteration only to have the
    per-item navigate override it) — the only choice is whether it's archived (default) or left
    in place as an inert standalone step immediately before the new loop.
    """
    doc = dict(document)
    skills = list(doc.get("skills") or [])
    if not skills:
        raise ValueError("no_skills_block")
    block = dict(skills[0])
    steps = list(block.get("steps") or [])
    keys = step_keys(steps)
    key_to_index = {k: i for i, k in enumerate(keys)}

    start = key_to_index.get(str(suggestion.get("wrap_start_key") or ""))
    end = key_to_index.get(str(suggestion.get("wrap_end_key") or ""))
    upload_index = key_to_index.get(str(suggestion.get("upload_step_key") or ""))
    if start is None or end is None or upload_index is None or start > end or upload_index <= end:
        raise ValueError("suggestion_stale")

    # A hardcoded click on the one recorded file, right before the per-item navigate, is
    # redundant once the loop navigates straight to each item's URL — the detector
    # (compiler/loop_suggestion.py) already folded it into wrap_start_key when present. Split
    # it back out here: it gets archived, not wrapped into the loop body, since wrapping it
    # would click one fixed filename on every iteration only to have the navigate override it.
    redundant_click_key = suggestion.get("redundant_click_key")
    redundant_index: int | None = None
    if redundant_click_key:
        redundant_index = key_to_index.get(str(redundant_click_key))
        if redundant_index is None or redundant_index != start or start == end:
            raise ValueError("suggestion_stale")
    keep_click = redundant_index is not None and not remove_redundant_click
    body_start = start + 1 if redundant_index is not None else start

    template_literal = str(suggestion.get("template_literal") or "")
    as_name = str(suggestion.get("as_name") or "row").strip() or "row"
    input_name = str(suggestion.get("suggested_input_name") or "files").strip() or "files"
    if not template_literal:
        raise ValueError("suggestion_missing_template_literal")

    # Defensive re-check: an intervening manual edit may have changed the very URL this
    # suggestion is about to template — refuse rather than substitute blindly. Decodes
    # percent-encoding before checking (url_contains_literal) — a raw substring check here
    # would wrongly refuse every filename containing a URL-reserved character, since the
    # recorded literal is decoded but the step's own url field is not (see that function's
    # docstring — this is the same bug that had to be fixed in the detector itself).
    if not any(
        url_contains_literal(str(steps[i].get("url") or ""), template_literal)
        for i in range(body_start, end + 1)
    ):
        raise ValueError("suggestion_stale")

    archived_click: dict[str, Any] | None = None
    if redundant_index is not None and not keep_click:
        archived_click = {
            "step_key": str(redundant_click_key),
            "step": copy.deepcopy(steps[redundant_index]),
            "category": "superseded_by_loop_navigate",
            "why": (
                "This click picked one fixed file by name; the new loop navigates straight to "
                "each item's URL, so the click is redundant and was removed."
            ),
        }

    body = []
    for i in range(body_start, end + 1):
        wrapped = copy.deepcopy(steps[i])
        url = str(wrapped.get("url") or "")
        # replace_url_literal decodes percent-encoding before matching/replacing — a raw
        # `url.replace(template_literal, ...)` would silently no-op whenever the literal
        # contains a URL-reserved character (the recorded filename is decoded, the step's own
        # url field is not), leaving the loop hardcoded to one file with no error. The returned
        # URL is the decoded form, which page.goto() (runtime/app/handlers.js) accepts fine.
        templated = replace_url_literal(url, template_literal, f"{{{{{as_name}_id}}}}")
        if templated is not None:
            wrapped["url"] = templated
        # EXEC-44: url is not the only field that can carry the recorded filename. A body step's
        # entity_binding.identifier is what runtime/app/resolution.js::entityRoots actually
        # narrows the live page to on EVERY iteration — left as the one recorded row's literal
        # text, iteration 1 (matching the original recording) passes and every other iteration
        # fails closed (entityRoots requires exactly one hasText match). Same match rule and same
        # literal→{{var}} rewrite compiler/entity_binding.py::upgrade_entity_binding_identifiers
        # already applies elsewhere — adapted here for a dict-shaped step rather than the
        # pydantic SkillStep that helper expects.
        eb = wrapped.get("entity_binding")
        if isinstance(eb, dict) and eb.get("source") == "literal" and eb.get("identifier"):
            ident_lower = str(eb["identifier"]).lower()
            literal_lower = template_literal.lower().strip()
            if literal_lower and (literal_lower in ident_lower or ident_lower in literal_lower):
                eb = dict(eb)
                eb["identifier"] = f"{{{{{as_name}_id}}}}"
                eb["source"] = "input"
                wrapped["entity_binding"] = eb
        # Deliberately not generalized further: a wrapped step's `value` or an assertion's text
        # is left untouched even if it happens to contain the filename — entity_binding is the
        # only field with a proven runtime consequence, and blindly substring-replacing arbitrary
        # text risks corrupting content that merely coincides with the filename.
        body.append(wrapped)

    wrapper = _new_manual_step("for_each", "")
    wrapper["intent"] = "download_each_selected_file"
    wrapper["for_each"] = {
        "items": input_name,
        "as": as_name,
        "max_iterations": 50,
        "on_row_error": "stop",
        "steps": body,
    }

    original_len = len(steps)
    kept_click_step = [steps[start]] if keep_click else []
    new_steps = steps[:start] + kept_click_step + [wrapper] + steps[end + 1:]
    block["steps"] = new_steps
    skills[0] = block
    doc["skills"] = skills

    # The upload step's own content is unchanged by the splice, so its step_key is stable —
    # re-resolve its new position rather than doing splice-index arithmetic by hand.
    new_keys = step_keys(new_steps)
    new_upload_index = new_keys.index(keys[upload_index])
    upload_step = dict(new_steps[new_upload_index])
    upload_step["value"] = "{{downloaded_files_dir}}"
    upload_step["input_binding"] = None
    new_steps[new_upload_index] = upload_step
    block["steps"] = new_steps
    skills[0] = block
    doc["skills"] = skills

    inputs = list(doc.get("inputs") or [])
    if not any(str(i.get("id") or "").strip().lower() == input_name.lower() for i in inputs if isinstance(i, dict)):
        # `optional` is deliberately left at the model default (False, required) — the loop
        # cannot run without a value for `files`, so requiring it is correct, not an oversight.
        inputs.append({
            "id": input_name, "type": "text", "default": None, "options": [],
            "description": "Filenames to download, comma-separated.",
        })
    # EXEC-44: this used to append straight to doc["inputs"] with no shape/uniqueness check,
    # unlike every other input-declaring path (merge_skill_inputs runs this same validator). A
    # bad id or a case-insensitive collision with an existing input landed silently.
    _validate_skill_inputs(inputs)
    doc["inputs"] = inputs

    # Old index `start` is the click when one was absorbed (redundant_index == start),
    # otherwise the first body step. Either way it maps to `start` unchanged: with no click,
    # `start` becomes the new for_each step's own slot; with a kept click, the click itself
    # stays at `start` (unmoved) and the wrapper is the one that lands at start+1 instead.
    # start+1..end are merged into the wrapper (no single new index to point at — dropped,
    # same as a delete); everything after `end` shifts down by however many steps the wrap
    # actually collapsed away — one fewer than the remove-click case when the click was kept,
    # since it's still occupying a slot rather than having been dropped. Advisory-only per-step
    # data, same "gone is honest, misattributed is not" reasoning as delete_step_at above.
    collapsed = (end - start - 1) if keep_click else (end - start)
    old_to_new = {}
    for i in range(original_len):
        if i < start:
            old_to_new[i] = i
        elif i == start:
            old_to_new[i] = start
        elif i <= end:
            old_to_new[i] = None
        else:
            old_to_new[i] = i - collapsed
    doc = _remap_intent_graph_indices(doc, old_to_new)
    _invalidate_compile_report(doc)

    # Clear ALL pending suggestions, not just the applied one: `download_observed`'s fallback
    # step_key (compiler/step_key.py) hashes on action+url alone, which is identical across
    # every download_observed step in a workflow — moving one occurrence out of the top-level
    # list (into this wrap's nested body) shifts the OCCURRENCE ORDINAL of every remaining
    # sibling download_observed still at top level, invalidating any other pending suggestion's
    # step_key silently. Matches `_invalidate_compile_report`'s own philosophy just above: wipe
    # derived data that can no longer be trusted rather than risk it pointing at the wrong step
    # — a recompile regenerates whatever is still genuinely there.
    report = doc.get("compile_report")
    if isinstance(report, dict) and report.get("for_each_suggestions"):
        report = dict(report)
        report["for_each_suggestions"] = []
        doc["compile_report"] = report

    if archived_click is not None:
        # EXEC-44: NOT compile_report["archived_steps"] — compiler/build.py::_apply_second_opinion
        # rebuilds compile_report from scratch on every recompile from that compile's OWN
        # flag_noise findings alone; anything written here would be silently discarded the next
        # time this workflow is compiled. editor_archived_steps is a document-level field the
        # compile pipeline never reads or writes, so an editor-time archive (this one) actually
        # survives a recompile, unlike the docs' prior "stays reversible" claim implied.
        doc["editor_archived_steps"] = list(doc.get("editor_archived_steps") or []) + [archived_click]

    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def _input_error_code(input_id: str, exc: PydanticValidationError) -> str:
    """Collapse a Pydantic ValidationError into the compact `code:detail` string the RPC layer
    surfaces. `cmd_update_workflow_inputs` uses str(exc) as the machine-readable error code, so
    a raw multi-line Pydantic message must never reach it."""
    first = exc.errors()[0]
    field = str(first["loc"][0]) if first.get("loc") else "input"
    if field == "id":
        return f"invalid_input_id:{input_id}"
    # A model_validator raises its own already-compact code (e.g. select_default_not_in_options).
    if first.get("type") == "value_error":
        return str(first.get("ctx", {}).get("error") or first.get("msg") or f"invalid_input:{input_id}")
    return f"invalid_input_{field}:{input_id}"


def _validate_skill_inputs(inputs: list[dict[str, Any]]) -> None:
    """Trust boundary for every saved input row. Shape/field rules live in SkillInputVariable so
    the declared DTO and the enforced contract can't drift; only the cross-row uniqueness check
    stays here, because no single-row model can see its siblings."""
    seen: set[str] = set()
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("input_must_be_object")
        input_id = str(item.get("id") or "").strip()
        if not input_id:
            raise ValueError("input_id_required")
        # Validates the id grammar, the text/select type, and that a select's default is one of
        # its options — the form already enforces these, but the Advanced JSON editor and direct
        # RPC bypass the form (audit finding L-5).
        try:
            SkillInputVariable.model_validate({**item, "id": input_id})
        except PydanticValidationError as exc:
            raise ValueError(_input_error_code(input_id, exc)) from exc
        # Ids collide case-insensitively at runtime ({{Email}} and {{email}} bind the same
        # slot), so uniqueness must be enforced the same way — matching the frontend
        # rowsToServerPayload check so the Advanced JSON editor / direct RPC can't slip a
        # pair past the trust boundary (audit finding L-4/L-5 sibling).
        id_key = input_id.lower()
        if id_key in seen:
            raise ValueError(f"duplicate_input_id:{input_id}")
        seen.add(id_key)


def _scan_placeholder_ids(value: Any, out: set[str]) -> None:
    """Collect every {{id}} variable referenced anywhere in a step (matches the frontend's
    collectVariableIdsFromSteps whole-step scan so 'spotted' means the same on both sides)."""
    if isinstance(value, str):
        for m in PLACEHOLDER_RE.finditer(value):
            out.add(m.group(1))
    elif isinstance(value, list):
        for item in value:
            _scan_placeholder_ids(item, out)
    elif isinstance(value, dict):
        for item in value.values():
            _scan_placeholder_ids(item, out)


def _date_pick_defaults(steps: list[Any]) -> dict[str, str]:
    """{{input_binding}} -> recorded ISO date/datetime, for every date_pick step the custom-
    calendar collapse pass produced (compiler/date_picker.py). Native `<input type=date>` steps
    carry the same shape once collapsed through the same handler_hints.date_picker contract, so
    this covers both without distinguishing them."""
    out: dict[str, str] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        action_name = action if isinstance(action, str) else (action or {}).get("action")
        if action_name != "date_pick":
            continue
        binding = str(step.get("input_binding") or "").strip()
        if not binding:
            continue
        hints = step.get("handler_hints") or {}
        date_picker = hints.get("date_picker") if isinstance(hints, dict) else None
        recorded = (date_picker or {}).get("recorded_value") if isinstance(date_picker, dict) else None
        if recorded:
            out[binding] = str(recorded)
    return out


def _choice_specs(steps: list[Any]) -> dict[str, dict[str, Any]]:
    """{{input_binding}} -> {type, options, default} for every choice step compiler/choice.py
    produced (radio/select/aria_radio/aria_listbox/checkbox-group). Mirrors _date_pick_defaults's
    shape/role: options come from the group's own recorded label set, never the answer, so the
    auto-declared input is a real MCQ (`enum` in input.json, constrained in the MCP tool schema)
    instead of a free-text field."""
    out: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        hints = step.get("handler_hints") or {}
        if not isinstance(hints, dict) or hints.get("control_kind") != "choice":
            continue
        choice = hints.get("choice")
        if not isinstance(choice, dict):
            continue
        binding = str(step.get("input_binding") or "").strip()
        if not binding:
            continue
        options = [
            str(o.get("label") or o.get("value") or "")
            for o in (choice.get("options") or [])
            if isinstance(o, dict) and (o.get("label") or o.get("value"))
        ]
        if not options:
            continue
        if choice.get("multi"):
            values_by_value = {str(o.get("value") or ""): str(o.get("label") or o.get("value") or "") for o in (choice.get("options") or []) if isinstance(o, dict)}
            default_labels = [values_by_value.get(v, v) for v in (choice.get("recorded_values") or [])]
            out[binding] = {"type": "multiselect", "options": options, "default": default_labels}
        else:
            default_label = str(choice.get("recorded_label") or "")
            out[binding] = {"type": "select", "options": options, "default": default_label if default_label in options else ""}
    return out


def reconcile_inputs_with_step_values(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Auto-declare any {{var}} referenced in a step but missing from the inputs list.

    Without this, editing a value to reference {{new}} left the runtime prompting for the
    old, now-dead variable while {{new}} resolved to empty at execution (audit finding M-3).
    Orphaned declarations are deliberately left in place — an unrelated value edit shouldn't
    silently drop a variable the user may still be wiring up; the drawer flags those as unused.
    Returns (possibly-mutated document, whether anything was added).
    """
    steps = ((document.get("skills") or [{}])[0] or {}).get("steps") or []
    spotted: set[str] = set()
    for step in steps:
        if isinstance(step, dict):
            _scan_placeholder_ids(step, spotted)
    if not spotted:
        return document, False
    date_defaults = _date_pick_defaults(steps)
    choice_specs = _choice_specs(steps)
    # Two families of {{placeholder}} are populated automatically at replay time and must never
    # become a user-facing input: a for_each loop's own {{<as>_id}}/{{<as>_index}} (run.js sets
    # them from the loop's row/items list) and the {{downloaded_file*}}/{{downloaded_files_dir}}
    # family (run.js's download_observed handler binds them from an earlier download in the same
    # run — see upload_binding.py). The compile handler used to rely on a SEPARATE downstream
    # filter_runtime_only_inputs() call to strip the latter after this function ran, but two
    # editor RPCs (handlers/workflow_editor.py's patch-value and replace-literal paths) call this
    # function directly with no such follow-up — a step whose value already held
    # {{downloaded_file}} (e.g. via upload_binding's own rewrite) would have that surfaced as a
    # real "supply a value" input the moment either RPC ran. Excluding both families right here
    # is the fix that covers every caller, not just the compile handler's.
    loop_vars = for_each_loop_variable_names(steps)
    inputs = list(document.get("inputs") or [])
    declared = {str(i.get("id") or "").strip().lower() for i in inputs if isinstance(i, dict)}
    added = False
    for sid in sorted(spotted):
        if sid in loop_vars or _RUNTIME_ONLY_PLACEHOLDER_RE.match(sid):
            continue
        if sid.lower() not in declared:
            spec = choice_specs.get(sid)
            if spec is not None:
                row = {"id": sid, "type": spec["type"], "default": spec["default"], "options": spec["options"]}
            else:
                default = date_defaults.get(sid)
                row = {"id": sid, "type": "date" if default else "text", "default": default, "options": []}
            inputs.append(row)
            declared.add(sid.lower())
            added = True
    if not added:
        return document, False
    doc = dict(document)
    doc["inputs"] = inputs
    return doc, True


def _remap_intent_graph_indices(
    document: dict[str, Any], old_to_new: dict[int, int | None]
) -> dict[str, Any]:
    """Renumber intent_graph.steps[].index after a structural edit so the advisory per-step
    plan keeps lining up with the steps it describes (audit finding L-1). Entries whose step
    was deleted (mapped to None) are dropped; the rest are re-sorted by their new index."""
    graph = document.get("intent_graph")
    if not isinstance(graph, dict):
        return document
    entries = graph.get("steps")
    if not isinstance(entries, list) or not entries:
        return document
    remapped: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        new_index = old_to_new.get(int(entry.get("index", -1)), None)
        if new_index is None:
            continue
        e = dict(entry)
        e["index"] = new_index
        remapped.append(e)
    remapped.sort(key=lambda e: e["index"])
    doc = dict(document)
    new_graph = dict(graph)
    new_graph["steps"] = remapped
    doc["intent_graph"] = new_graph
    return doc


def merge_skill_inputs(document: dict[str, Any], inputs: list[dict[str, Any]], title: str | None) -> dict[str, Any]:
    _validate_skill_inputs(inputs)
    doc = dict(document)
    doc["inputs"] = list(inputs)
    if title is not None:
        meta = dict(doc.get("meta") or {})
        meta["title"] = title
        doc["meta"] = meta
    meta = dict(doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    doc["meta"] = meta
    return doc


def _replace_outside_placeholders(val: str, find: str, replace: str) -> tuple[str, int]:
    """Replace `find` only in the literal segments between {{...}} placeholders, never inside
    a placeholder. Protects both a pure `{{id}}` value AND a mixed one like `{{db}}/extra` —
    a naive replace of `db`→`{{db_name}}` on the latter would nest braces into
    `{{{{db_name}}}}/extra`, which no scanner interpolates (audit finding L-3)."""
    out: list[str] = []
    count = 0
    last = 0
    for m in PLACEHOLDER_RE.finditer(val):
        segment = val[last : m.start()]
        count += segment.count(find)
        out.append(segment.replace(find, replace))
        out.append(m.group(0))  # placeholder span left untouched
        last = m.end()
    tail = val[last:]
    count += tail.count(find)
    out.append(tail.replace(find, replace))
    return "".join(out), count


def _replace_in_step(step: dict[str, Any], find: str, replace: str) -> tuple[dict[str, Any], int]:
    """Replace `find` with `replace` inside this step's own `value` field only (never
    selectors/URLs/identity signals — audit finding C1), recursing into if_present branch
    bodies. Only the literal text outside any {{id}} placeholder is touched, so an unrelated
    replace can't corrupt or nest braces inside an existing binding (audit findings M3/L-3)."""
    step = dict(step)
    count = 0
    val = step.get("value")
    if isinstance(val, str) and find in val:
        new_val, seg_count = _replace_outside_placeholders(val, find, replace)
        if seg_count:
            step["value"] = new_val
            count += seg_count
    branch = step.get("branch")
    if isinstance(branch, dict):
        nested = branch.get("steps")
        if isinstance(nested, list):
            new_nested = []
            for nested_step in nested:
                if isinstance(nested_step, dict):
                    nested_step, nested_count = _replace_in_step(nested_step, find, replace)
                    count += nested_count
                new_nested.append(nested_step)
            branch = dict(branch)
            branch["steps"] = new_nested
            step["branch"] = branch
    return step, count


def replace_string_literals_in_skill_document(
    document: dict[str, Any], find: str, replace: str
) -> tuple[dict[str, Any], int]:
    """Replace a literal substring inside every step's `value` field (the recorded user-typed
    content) — never selectors, URLs, identity_bundle signals, or meta, which a blind
    substring replace previously corrupted (audit finding C1). Returns (new_document,
    match_count) so callers can surface a no-op or a large blast radius to the user."""
    if not isinstance(find, str) or not find:
        raise ValueError("find_must_be_nonempty")
    if not isinstance(replace, str):
        raise ValueError("replace_with_must_be_string")
    new_doc = dict(document)
    skills = [dict(s) for s in (new_doc.get("skills") or [])]
    match_count = 0
    if skills:
        block = dict(skills[0])
        steps = list(block.get("steps") or [])
        new_steps = []
        for step in steps:
            if isinstance(step, dict):
                step, count = _replace_in_step(step, find, replace)
                match_count += count
            new_steps.append(step)
        block["steps"] = new_steps
        skills[0] = block
    new_doc["skills"] = skills
    meta = dict(new_doc.get("meta") or {})
    meta["version"] = int(meta.get("version", 1)) + 1
    new_doc["meta"] = meta
    return new_doc, match_count
