"""Gates a Human Review Copilot proposal against the editor's own patch gate before it is ever
shown, and resolves it back to a live step at accept time (BUILD-26 stages c1/c2/c3).

The copilot never writes a selector: `capability_manifest.editable_fields` is the set of fields the
patch gate accepts for a step's kind, minus `target`, `identity_bundle`, `compiled_selectors` and every
other selector-bearing key — the same category as the two sanctioned re-compile exceptions in
CLAUDE.md, deliberately narrower (it never touches an element address at all, only meaning fields
already writable through `cmd_patch_step`).

A proposal stores `step_key` (`compiler/step_key.py`), never a baked `step_index` — an insert,
delete, or reorder between when the proposal was shown and when it is accepted renumbers every
later step (BUILD-22/BUILD-23), which is exactly the failure `editor/edit_log.py`'s docstring
warns about for the review-edit log. `resolve_step_index` re-resolves the key at accept time and
refuses cleanly if it is gone; `cmd_patch_step`'s own `_apply_step_patch` re-runs
`validate_editor_patch` on the resolved step regardless, so accept time is never trusting a
gate result computed against a document that may no longer exist.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any
from urllib.parse import urlsplit

from conxa_core.login_paths import SIGN_IN_PAGE_RE

from conxa_compile.compiler.action_semantics import action_name
from conxa_compile.compiler.second_opinion import build_try_dismiss_from_hint
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.editor.action_registry import SELECTOR_ACTIONS, action_spec, is_supported_action
from conxa_compile.editor.capability_manifest import ADDRESS_KEYS, chat_may_write, editable_fields
from conxa_compile.editor.describe import describe_step
from conxa_compile.editor.overlay_identity import bundle_from_descriptor
from conxa_compile.editor.patch_gate import validate_editor_patch
from conxa_compile.editor.step_path import StepPathError, get_nested_step, parse_nested_step_path
from conxa_compile.editor.workflow_mutations import (
    _new_manual_step,
    delete_branch_step,
    flatten_fork_to_recorded_option,
    insert_branch_step,
    merge_skill_inputs,
    parameterize_click_text,
    parameterize_literal,
    reorder_branch_steps,
    replace_string_literals_in_skill_document,
    set_date_offset,
    set_fork_policy,
)
from conxa_compile.policy.bundle import get_policy_bundle

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
        if not step_key or not field:
            continue
        resolved = _resolve_step(doc, step_key)
        if resolved is None:
            continue
        step_index, step = resolved
        # An optional `branch.steps[N]` / `for_each.steps[N]` addresses a step inside this one's body —
        # parsed and resolved by the same helpers cmd_patch_step uses, so the gate and the apply agree.
        path = str(raw.get("path") or "").strip() or None
        target_step, in_branch_body = step, False
        if path:
            try:
                nested_path = parse_nested_step_path(path)
                target_step = get_nested_step(step, nested_path) if nested_path else step
            except StepPathError:
                continue
            in_branch_body = bool(nested_path) and nested_path[0] == "branch"
        kind = action_name(target_step).lower()
        value = raw.get("patch")
        patch = _patch_for(field, value)
        if not chat_may_write(kind, patch) or _value_holds_address(kind, target_step, field, value):
            continue
        if refuses_sign_in(kind, target_step, patch):
            continue
        previous_step = steps[step_index - 1] if step_index > 0 and not path else None
        try:
            validate_editor_patch(
                target_step, patch, policy, in_branch_body=in_branch_body, previous_step=previous_step,
                earlier_steps=None if path else steps[:step_index],
            )
        except ValueError:
            continue
        out.append({
            "id": str(uuid.uuid4()),
            "step_key": step_key,
            "command": "patch_step",
            "field": field,
            "patch": patch,
            "path": path,
            "step_label": _step_label(doc, step_key),
            "is_agent_goal": kind == "agent_span",
            "why": _plain_why(raw.get("why")),
            "evidence_refs": _evidence_refs(raw),
            "preview": {"before": _current_value(target_step, field), "after": value},
        })
    return out


_TYPING_KINDS = frozenset({"type", "fill"})


def _is_password_step(step: dict[str, Any]) -> bool:
    fingerprint = (step.get("identity_bundle") or {}).get("fingerprint") or {}
    semantic = (step.get("signals") or {}).get("semantic") or {}
    return "password" in (str(fingerprint.get("input_type") or "").lower(), str(semantic.get("input_type") or "").lower())


def _is_sign_in_address(url: Any) -> bool:
    return bool(SIGN_IN_PAGE_RE.search(urlsplit(str(url or "")).path or "/"))


def refuses_sign_in(kind: str, step: dict[str, Any], patch: dict[str, Any]) -> bool:
    """Signing in is never part of a workflow and a credential never enters one (CLAUDE.md): a change that types
    into a password box, or sends the run to a sign-in page, is refused here for every kind of proposal. `patch` is
    the nested change (`{"value": ...}`, `{"url": ...}`); for an insert it is the new step's fields, and `step` the
    step whose element it would copy. Sign-in is the group's job (conxa_core.login_paths is the one word list)."""
    if kind in _TYPING_KINDS and _is_password_step(step) and ("value" in patch or "input_binding" in patch):
        return True
    if kind == "navigate":
        action = patch.get("action")
        return any(_is_sign_in_address(v) for v in (patch.get("url"), patch.get("value"),
                                                    action.get("url") if isinstance(action, dict) else None))
    return False


def _value_holds_address(kind: str, step: dict[str, Any], field: str, value: Any) -> bool:
    """True when a `value` edit would write a page selector: a wait-until that waits on an element keeps
    its selector in `value`. (A drag-drop's `value` is already removed by `editable_fields`.)"""
    if field != "value" or kind != "wait_until":
        return False
    return str(step.get("wait_until_kind") or "load_state") in ("element_visible", "element_hidden")


def gate_all(
    doc: dict[str, Any],
    raw_proposals: list[Any],
    observed_overlays: list[dict[str, Any]] | None = None,
    report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The three gates over one raw list, in the order the callers always used (patch_step, overlay
    branch when overlays were observed, structural ops), plus the count of what none of them took.

    Every gate sees the same raw list and skips the other kinds' proposals, so a per-gate count would
    report every proposal as dropped by two of three. The count is therefore made here, per raw kind:
    ``shape.malformed_item`` for a non-object, ``safety.gate_refused`` (example: ``patch``, ``overlay``
    or ``structural``) for a proposal its own gate dropped (llm/model_ledger)."""
    from conxa_compile.llm.model_ledger import log as _log  # noqa: PLC0415

    raw_list = raw_proposals if isinstance(raw_proposals, list) else []
    accepted = gate_proposals(doc, raw_list)
    if observed_overlays:
        accepted = accepted + gate_overlay_proposals(doc, observed_overlays, raw_list)
    accepted = accepted + gate_structural_proposals(doc, raw_list)
    if report is not None:
        def kind_of(r: dict[str, Any]) -> str:
            return "structural" if r.get("op") else "overlay" if r.get("overlay_id") else "patch"

        wanted: dict[str, int] = {}
        for r in raw_list:
            if isinstance(r, dict):
                wanted[kind_of(r)] = wanted.get(kind_of(r), 0) + 1
            else:
                _log(report, "shape", "malformed_item", type(r).__name__)
        taken = {"patch": 0, "overlay": 0, "structural": 0}
        for p in accepted:
            taken["overlay" if p.get("command") == "insert_overlay_branch" else
                  "structural" if p.get("command") == "structural_op" or p.get("from_op") else "patch"] += 1
        for kind, n in wanted.items():
            for _ in range(max(0, n - taken.get(kind, 0))):
                _log(report, "safety", "gate_refused", kind)
    return accepted


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
            "why": _plain_why(raw.get("why")),
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


_STRUCTURAL_OPS = frozenset({
    "insert_step", "delete_step", "move_step", "update_inputs", "replace_literals", "parameterize",
    "body_insert_step", "body_delete_step", "body_move_step", "set_popup_decision", "flatten_popup",
    "set_date_offset", "rename_workflow",
})

# Kinds that cannot be put inside an if-present body: they pause the run, or hold a body of their own
# (the step path addresses one level only).
_NOT_IN_BODY = frozenset({
    "agent_span", "handover", "call_workflow", "for_each", "if_present", "try_dismiss", "wait_for_one_of",
    "outcome_fork",
})
_FORK_MODES = frozenset({"auto", "by_input", "ask_human", "stop"})


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _step_label(doc: dict[str, Any], step_key: str | None) -> str:
    """"step 6" for a step_key (1-based, as the person counts), computed when the proposal is gated so
    the card never shows a hash."""
    resolved = _resolve_step(doc, step_key) if step_key else None
    return f"step {resolved[0] + 1}" if resolved else "the start"


def _merge_nested(into: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _merge_nested(into[key], value)
        else:
            into[key] = value


def _fields_patch(fields: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for field, value in fields.items():
        _merge_nested(patch, _patch_for(field, value))
    return patch


def _fork_state(step: dict[str, Any], state_id: str) -> dict[str, Any] | None:
    states = (step.get("outcome_fork") or {}).get("states") or []
    return next((s for s in states if isinstance(s, dict) and s.get("id") == state_id), None)

# The two code-owned rewrites behind `parameterize`, in the order they are applied. The model only
# names the recorded value and the input; these decide which places change.
_PARAMETERIZERS = (("click", parameterize_click_text), ("value", parameterize_literal))


_RUN_IF_WORDS = {"not_empty": "is filled in", "equals": "is", "not_equals": "is not"}


def _plain_why(text: Any) -> str:
    """A model's reason for a change, only when it reads plainly: one that names an internal field is dropped, and the
    card then says the change from code wording (``_patch_summary``)."""
    from conxa_compile.conversation.plain_words import plain_ok

    t = str(text or "").strip()
    return t if plain_ok(t) else ""


def _clip(text: Any, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _patch_summary(p: dict[str, Any]) -> str:
    where = p.get("step_label") or f"step {p.get('step_key')}"
    field, value = p.get("field"), (p.get("preview") or {}).get("after")
    if p.get("path"):
        where = f"an item inside {where}"
    if field == "run_if":
        if not isinstance(value, dict) or not value:
            return f"Run {where} every time (remove its condition)"
        word = _RUN_IF_WORDS.get(str(value.get("op") or "not_empty"), "is")
        tail = f" “{value.get('value')}”" if value.get("op") in ("equals", "not_equals") else ""
        return f"Run {where} only when the input {value.get('input')} {word}{tail}"
    if field == "value" and p.get("is_agent_goal"):
        return f"Change what the agent does in {where} to: {_clip(value)}"
    if field in ("validation", "validation.assertions"):
        return _checks_summary(p, where)
    if field == "value" and "{{" in str(value or ""):
        return f"Make the text in {where} come from a run input: “{_clip(value)}”"
    return f"Change {field_words(field)} on {where}"


# What a card calls a field; the field's own name stays behind "Show exact change" (WF-12).
_FIELD_WORDS = {
    "value": "the text or value it uses",
    "intent": "the step's name",
    "semantic_description": "the step's description",
    "input_binding": "which input it fills in",
    "on_missing": "what happens when it isn't there",
    "consequence": "whether it can be undone",
    "entity_binding.confirmed": "the row it acts on",
    "handler_hints.hover_chain": "what it hovers over first",
    "branch.timeout_ms": "how long it waits",
    "wait_until_timeout_ms": "how long it waits",
    "for_each.max_iterations": "how many rows it goes through",
    "for_each.on_row_error": "what happens when a row fails",
}


def field_words(field: Any) -> str:
    """A field's name in everyday words: "semantic_description" is "the step's description"."""
    field = str(field or "")
    return _FIELD_WORDS.get(field) or "the " + field.split(".")[-1].replace("_", " ").strip() if field else "a step"


def _said(text: Any) -> str:
    """A check's words as a person reads them: "{{user_email}}" is "the user email you entered"."""
    from conxa_core.models.prose_binding import say_text

    return say_text(_clip(text), limit=160)


def _check_words(a: dict[str, Any]) -> str:
    kind = a.get("type")
    if kind == "text_present":
        return f"the page shows {_said(a.get('target') or a.get('expected'))}"
    if kind == "text_absent":
        return f"the page no longer shows {_said(a.get('target') or a.get('expected'))}"
    if kind == "value_equals":
        return f"the box holds {_said(a.get('expected'))}"
    if kind in ("url_changed", "url_pattern"):
        where = a.get("expected") or a.get("target")
        return f"the page address matches {_said(where)}" if where else "the page address changes"
    return "the step's result can be seen"


def _checks_summary(p: dict[str, Any], where: str) -> str:
    """A change to a step's checks in plain words; the exact assertions stay behind "Show exact change"."""
    def rows(v: Any) -> list[dict[str, Any]]:
        v = v.get("assertions") if isinstance(v, dict) else v
        return [a for a in v or [] if isinstance(a, dict)]

    preview = p.get("preview") or {}
    before, after = rows(preview.get("before")), rows(preview.get("after"))
    added, removed = [a for a in after if a not in before], [a for a in before if a not in after]
    parts = [f"also check that {_check_words(a)}" for a in added] + [f"stop checking that {_check_words(a)}" for a in removed]
    if not parts:
        return f"Change how {where} is checked"
    text = " and ".join(parts)
    return f"After {where}, {text}"


def _insert_summary(p: dict[str, Any]) -> str:
    kind, after = p.get("action_kind"), p.get("after_label") or "the end"
    where = after if after.startswith("the ") else f"after {after}"
    if kind == "agent_span":
        fields = p.get("fields") or {}
        acts = ", ".join(fields.get("agent_span_allowed") or ()) or "any browser action"
        return (f"Add an agent step {where}: “{_clip(fields.get('value'))}” (may {acts}). "
                "A workflow with an agent step can't run on a schedule")
    return f"Add a {kind} step {where}"


def proposal_summary(p: dict[str, Any]) -> str:
    """One plain sentence for a gated proposal, written by code from its validated fields — shown on
    the card and given to the reply, so what is said and what is offered cannot drift apart."""
    cmd, op = p.get("command"), p.get("op")
    if cmd == "insert_overlay_branch":
        return str((p.get("preview") or {}).get("after") or "Add a step to dismiss a popup")
    if cmd == "patch_step":
        return _patch_summary(p)
    label = p.get("step_label") or "that step"
    if op == "insert_step":
        return _insert_summary(p)
    if op == "delete_step":
        return f"Remove {label}"
    if op == "move_step":
        return f"Move {label} to after {p.get('after_label') or 'the start'}"
    if op == "body_insert_step":
        return f"Add a {p.get('action_kind')} step inside {label}"
    if op == "body_delete_step":
        return f"Remove item {int(p.get('nested_index') or 0) + 1} inside {label}"
    if op == "body_move_step":
        return f"Reorder the steps inside {label}"
    if op == "set_popup_decision":
        policy, title = p.get("policy") or {}, p.get("popup_title") or "popup"
        mode = policy.get("mode")
        if mode == "auto":
            tail = " This can't be undone; pressing Apply is your approval." if p.get("option_role") == "consequential" else ""
            return f"On the “{title}” popup, always press “{p.get('option_label')}” ({label}).{tail}"
        if mode == "ask_human":
            return f"On the “{title}” popup, ask a person each time ({label})"
        if mode == "by_input":
            return f"On the “{title}” popup, let the person running it choose ({label})"
        if mode == "stop":
            return f"On the “{title}” popup, stop the run ({label})"
        return f"Change what happens after a choice on the “{title}” popup ({label})"
    if op == "flatten_popup":
        return f"Make the “{p.get('popup_title')}” popup an ordinary recorded step ({label}); it must show up every run"
    if op == "set_date_offset":
        return f"Make the date in {label} today {int(p.get('days') or 0):+d} days"
    if op == "rename_workflow":
        return f"Rename the workflow to “{p.get('name')}”"
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
    writes one. A kind not in SELECTOR_ACTIONS (agent_span, handover, wait, check, assert,
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
        why = _plain_why(raw.get("why"))
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
            if _names_an_address(action_kind, fields):
                continue
            source_step = _resolve_step(doc, identity_from_step_key)[1] if identity_from_step_key else {}
            if refuses_sign_in(action_kind, source_step, fields):
                continue
            allowed_fields = {f: v for f, v in fields.items() if f in editable_fields(action_kind)}
            # An agent step with no goal would ship nothing (the packager drops it): refuse it here.
            if action_kind == "agent_span" and not str(allowed_fields.get("value") or "").strip():
                continue
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
                "after_label": _step_label(doc, after_step_key) if after_step_key else "the end",
                "fields": allowed_fields, "identity_from_step_key": identity_from_step_key,
                "preview": {"before": None, "after": f"Insert {action_kind} after "
                            f"{_step_label(doc, after_step_key) if after_step_key else 'the last step'}"},
            })

        elif op == "delete_step":
            step_key = str(raw.get("step_key") or "").strip()
            resolved = _resolve_step(doc, step_key)
            if not step_key or resolved is None:
                continue
            out.append({**base, "step_key": step_key, "step_label": _step_label(doc, step_key),
                        "preview": {"before": describe_step(resolved[1], resolved[0]), "after": None}})

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
                        "step_label": _step_label(doc, step_key),
                        "after_label": _step_label(doc, after_step_key) if after_step_key else "the start",
                        "preview": {"before": None, "after": f"Move to after "
                                    f"{_step_label(doc, after_step_key) if after_step_key else 'the start'}"}})

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
                # Typed text is neither rewrite's job: it becomes the same `value` edit Human Edit makes, so
                # "make the typed note an input" gets a card on every wording, not only on some (WF-12 12.3).
                out.extend({**p, "from_op": op} for p in gate_proposals(doc, _typed_value_edits(doc, literal, name, why, evidence_refs)))
                continue
            out.append({**base, "literal": literal, "name": name, "kinds": kinds, "changed": changed,
                        "preview": {"before": literal, "after": "{{%s}}" % name}})

        elif op in _STEP_SCOPED_OPS:
            built = _gate_step_scoped_op(doc, raw, op, base, policy)
            if built is not None:
                out.append(built)

        elif op == "rename_workflow":
            name = str(raw.get("name") or "").strip()
            current = str((doc.get("meta") or {}).get("title") or "")
            if not name or len(name) > 120 or name == current:
                continue
            out.append({**base, "name": name, "preview": {"before": current or None, "after": name}})

    return out


def _names_an_address(kind: str, fields: dict[str, Any]) -> bool:
    """A new step whose fields ask for an address (a page to open, an element) gets no card at all: dropping
    only that field would offer a step that goes somewhere other than what was asked."""
    for f, v in fields.items():
        name = str(f).strip().lower()
        if chat_may_write(kind, _patch_for(str(f), v)):
            continue
        if name.split(".")[0] in ADDRESS_KEYS or any(w in name for w in ("url", "selector", "address")):
            return True
    return False


def _typed_value_edits(doc: dict[str, Any], literal: str, name: str, why: str, refs: list[str]) -> list[dict[str, Any]]:
    """Raw `value` field edits for every type / fill step whose typed text holds ``literal``: the literal
    becomes ``{{name}}`` and the rest of the text stays as typed."""
    token = "{{%s}}" % name
    keys = step_keys(_steps_of(doc))
    return [
        {"step_key": key, "field": "value", "patch": str(step.get("value")).replace(literal, token), "why": why,
         "evidence_refs": refs}
        for key, step in zip(keys, _steps_of(doc))
        if isinstance(step, dict) and action_name(step).lower() in ("type", "fill")
        and isinstance(step.get("value"), str) and literal in step["value"]
    ]


_STEP_SCOPED_OPS = frozenset({
    "body_insert_step", "body_delete_step", "body_move_step", "set_popup_decision", "flatten_popup",
    "set_date_offset",
})


def _gate_step_scoped_op(
    doc: dict[str, Any], raw: dict[str, Any], op: str, base: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any] | None:
    """The ops that act on one existing step (named by `step_key`). Each is dry-run on a copy with the
    SAME mutation the accept calls, so a card that is shown can be applied; any ValueError drops it.
    Returns the proposal, or None when it fails a check."""
    step_key = str(raw.get("step_key") or "").strip()
    resolved = _resolve_step(doc, step_key) if step_key else None
    if resolved is None:
        return None
    index, step = resolved
    kind = action_name(step).lower()
    # The body a body_* op edits follows the step it names: a loop's steps for a for_each, else an if-present's.
    container = "for_each" if kind == "for_each" else "branch"
    scoped = {**base, "step_key": step_key, "step_label": _step_label(doc, step_key)}
    try:
        if op == "body_delete_step":
            nested_index = raw.get("nested_index")
            if not _is_int(nested_index):
                return None
            delete_branch_step(copy.deepcopy(doc), index, nested_index, container)
            return {**scoped, "nested_index": nested_index, "preview": {"before": f"item {nested_index + 1} inside", "after": None}}

        if op == "body_move_step":
            new_order = raw.get("new_order")
            if not isinstance(new_order, list) or not all(_is_int(i) for i in new_order):
                return None
            reorder_branch_steps(copy.deepcopy(doc), index, new_order, container)
            return {**scoped, "new_order": new_order, "preview": {"before": None, "after": f"Reorder inside to {new_order}"}}

        if op == "body_insert_step":
            action_kind = str(raw.get("action_kind") or "").strip().lower().replace("-", "_")
            if not is_supported_action(action_kind) or not action_spec(action_kind).insertable or action_kind in _NOT_IN_BODY:
                return None
            after_nested = raw.get("after_nested_index")
            if after_nested is not None and not _is_int(after_nested):
                return None
            identity_from = str(raw.get("identity_from_step_key") or "").strip() or None
            patch: dict[str, Any] = {}
            if action_kind in SELECTOR_ACTIONS:
                source = _resolve_step(doc, identity_from) if identity_from else None
                if source is None or not (source[1].get("identity_bundle") or {}):
                    return None
                patch.update(identity_patch_from_source(action_kind, source[1]))
            elif identity_from:
                return None
            raw_fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
            if _names_an_address(action_kind, raw_fields):
                return None
            fields = {f: v for f, v in raw_fields.items() if f in editable_fields(action_kind)}
            insert_branch_step(copy.deepcopy(doc), index, action_kind, after_nested, container)
            _merge_nested(patch, _fields_patch(fields))
            validate_editor_patch(_new_manual_step(action_kind, ""), patch, policy, in_branch_body=container == "branch")
            return {**scoped, "action_kind": action_kind, "after_nested_index": after_nested, "fields": fields,
                    "identity_from_step_key": identity_from,
                    "preview": {"before": None, "after": f"Insert {action_kind} inside {_step_label(doc, step_key)}"}}

        if op in ("set_popup_decision", "flatten_popup"):
            state_id = str(raw.get("state_id") or "").strip()
            state = _fork_state(step, state_id) if kind == "outcome_fork" else None
            if state is None:
                return None
            title = str((state.get("signature") or {}).get("title") or "popup")
            if op == "flatten_popup":
                flatten_fork_to_recorded_option(copy.deepcopy(doc), index, state_id)
                return {**scoped, "state_id": state_id, "popup_title": title,
                        "preview": {"before": "Popup decision", "after": "The recorded steps, every time"}}
            decision = raw.get("policy")
            if not isinstance(decision, dict) or (decision.get("mode") and decision.get("mode") not in _FORK_MODES):
                return None
            set_fork_policy(copy.deepcopy(doc), index, state_id, decision)
            option = next((o for o in state.get("options") or [] if isinstance(o, dict) and o.get("id") == decision.get("option_id")), None)
            return {**scoped, "state_id": state_id, "policy": decision, "popup_title": title,
                    # Worded from the fork itself, never from the model: what the card says is what Apply writes.
                    "option_label": str((option or {}).get("label") or ""),
                    "option_role": str((option or {}).get("role") or ""),
                    "preview": {"before": (state.get("policy") or {}).get("mode"), "after": decision.get("mode") or "then"}}

        if op == "set_date_offset":
            days = raw.get("days")
            if not _is_int(days):
                return None
            set_date_offset(copy.deepcopy(doc), index, days)
            return {**scoped, "days": days, "preview": {"before": None, "after": f"today {days:+d} days"}}
    except ValueError:
        return None
    return None
