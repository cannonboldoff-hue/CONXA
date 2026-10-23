"""Apply the second-opinion pass's findings to the compiled steps (BUILD-25).

The pass (llm/workflow_semantics.py) looks at the whole workflow at once and
decides four things no per-step rule can decide correctly. This module is what
writes those decisions into the steps, so a reviewer opening Human Review sees
finished work rather than a list of chips to approve.

  rename_binding        {{email_2}}          -> {{sender_email}}
  parameterize_literal  "INV-2024-0891"      -> {{reference_number}}
  label_phase           step.phase           -> login|navigate|act|verify|cleanup
  suggest_optional      a required step      -> a try_dismiss branch
  suggest_assertion     (nothing)            -> an advisory validation.assertions entry
  flag_noise            a no-op step         -> removed from `steps`, archived (see below)

What this module deliberately does NOT touch: selectors, identity bundles,
compiled_selectors, wait_for/success_conditions, frame/tab chains, or anything
else describing *how* an element is found. CLAUDE.md's "LLM does not write
selector strings on the primary compile path" invariant is intact —
suggest_assertion may only append a text/URL/state Assertion (never a
selector-bearing type) and always forces required=False, so a wrong one can
never halt a run. flag_noise is the pass's one destructive kind; see
archive_flagged_steps below for why it archives rather than deletes.

Human Review is the gate. Every field written here is one a reviewer can edit
in Human Edit, and nothing marks it as machine-written — an applied rename is
indistinguishable from one the fixed rules produced, by design.

Applying `suggest_optional` at compile time retires the older "branch steps
compile only from observed states + human confirmation" rule: the hint is still
an observed state (the recorder saw the step behave non-deterministically), but
the confirmation is now after the fact, in Human Review, instead of before.
editor/workflow_mutations.py::confirm_optional_interstitial remains for hints
this pass left alone, and shares build_try_dismiss_from_hint below so the two
paths cannot drift.
"""

from __future__ import annotations

import json
import re
from typing import Any

from conxa_core.models.skill_spec import Assertion, RecoveryBlock, SkillStep

from conxa_compile.compiler.action_policy import no_recovery_block
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.editor.action_registry import is_marker_action
from conxa_compile.editor.placeholder_grammar import PLACEHOLDER_RE

TRY_DISMISS_INTENT = "try_dismiss_interstitial"


def rewrite_placeholder(value: Any, old: str, new: str) -> Any:
    """Rewrite {{old}} -> {{new}} wherever it appears in a step value.

    Not an equality check: a mixed value like "prefix {{name}}" would otherwise
    keep {{name}} while its binding became something else — a value/binding
    mismatch (audit finding L-2). Optional inner whitespace matches the
    placeholder grammar. Non-string values pass through untouched.
    """
    if not isinstance(value, str) or not old:
        return value
    return re.sub(r"\{\{\s*" + re.escape(old) + r"\s*\}\}", f"{{{{{new}}}}}", value)


def build_try_dismiss_from_hint(primary_selector: str, container_signal: str) -> dict[str, Any]:
    """The try_dismiss shape a recorder optional_hint converts into.

    Seeds the branch's candidates with the step's own recorded selector plus the
    recorder's observed container_signal, mirroring the scaffold
    _new_manual_step builds for a manually-inserted branch step. Returns plain
    data so both callers can apply it in their own shape — the compiler onto a
    SkillStep model, the editor onto a saved step dict.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for c in (str(primary_selector or "").strip(), str(container_signal or "").strip()):
        if c and c not in seen:
            seen.add(c)
            candidates.append(c)
    return {
        "intent": TRY_DISMISS_INTENT,
        "branch": {"candidates": candidates, "timeout_ms": 3000, "fallback_escape": True},
        "recovery": no_recovery_block(TRY_DISMISS_INTENT),
    }


def _apply_one(step: SkillStep, kind: str, current: str, proposed: str) -> bool:
    """Write one finding onto one step. Returns whether anything changed."""
    if kind == "rename_binding":
        if not step.input_binding:
            return False
        step.value = rewrite_placeholder(step.value, step.input_binding, proposed)
        step.input_binding = proposed
        return True

    if kind == "parameterize_literal":
        # Only ever freezes a recorded LITERAL into a variable — never re-points a value that
        # already holds a placeholder. `input_binding` alone doesn't catch every such case:
        # upload_binding.py deliberately writes {{downloaded_file}} into `value` while setting
        # input_binding to None (a download-populated placeholder must never become a
        # user-facing input), which the old `step.input_binding or ...` guard read as "unbound"
        # and clobbered — the exact bug this comment used to describe without enforcing.
        if (
            step.input_binding
            or not isinstance(step.value, str)
            or not step.value
            or PLACEHOLDER_RE.search(step.value)
        ):
            return False
        step.input_binding = proposed
        step.value = f"{{{{{proposed}}}}}"
        return True

    if kind == "label_phase":
        step.phase = proposed
        return True

    if kind == "suggest_optional":
        hint = step.optional_hint if isinstance(step.optional_hint, dict) else None
        if not hint:
            return False
        built = build_try_dismiss_from_hint(
            str((step.target or {}).get("primary_selector") or ""),
            str(hint.get("container_signal") or ""),
        )
        action = dict(step.action) if isinstance(step.action, dict) else {"action": step.action}
        action["action"] = "try_dismiss"
        step.action = action
        step.intent = built["intent"]
        step.branch = built["branch"]
        step.recovery = RecoveryBlock(**built["recovery"])
        step.optional_hint = None  # consumed by this conversion
        return True

    if kind == "suggest_assertion":
        try:
            parsed = json.loads(proposed)
        except (ValueError, TypeError):
            return False
        if not isinstance(parsed, dict):
            return False
        new_assertion = Assertion(
            type=str(parsed.get("type") or ""),
            target=str(parsed.get("target") or ""),
            required=False,  # advisory only — a wrong call here can never halt a run
        )
        if any(
            a.type == new_assertion.type and a.target == new_assertion.target
            for a in step.validation.assertions
        ):
            return False  # idempotent: already have this exact check (e.g. across recompiles)
        step.validation = step.validation.model_copy(
            update={"assertions": [*step.validation.assertions, new_assertion]}
        )
        return True

    return False


def apply_second_opinion(steps: list[SkillStep], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Write every finding that maps onto a real step, returning the subset
    actually applied (the compile-report audit record). Findings arrive keyed on
    step_key, already validated by llm/workflow_semantics.py — this only guards
    the shape mismatches that validation can't see (a rename on a step with no
    binding, an optional conversion on a step whose hint is gone)."""
    if not findings:
        return []
    by_key = dict(zip(step_keys(steps), steps))
    applied: list[dict[str, Any]] = []
    for item in findings:
        step = by_key.get(str(item.get("step_key") or ""))
        if step is None:
            continue
        if _apply_one(
            step,
            str(item.get("kind") or ""),
            str(item.get("current") or ""),
            str(item.get("proposed") or ""),
        ):
            applied.append(item)
    return applied


def _step_causes_observed_effect(steps: list[SkillStep], i: int) -> bool:
    """True when the step at index i is immediately followed (past pure
    navigation markers) by a download or a file-chooser upload — the two
    shapes where a click's job is to trigger something invisible on the page
    itself. This mirrors build.py::_next_effect_cause exactly, but is
    recomputed here from the real `steps` list rather than trusted from the
    review payload's "causes" field: this is the pass's one destructive kind,
    so the invariant belongs where it cannot be bypassed by a stale/absent
    context field or a model that ignored the prompt. The compiler must never
    delete the step that causes an observed side effect."""
    for j in range(i + 1, len(steps)):
        name = steps[j].action.get("action") if isinstance(steps[j].action, dict) else steps[j].action
        if is_marker_action(name) and name in {"tab_switch", "frame_enter", "frame_exit"}:
            continue
        return name == "download_observed" or name in {"upload", "upload_intent"}
    return False


def archive_flagged_steps(
    steps: list[SkillStep], findings: list[dict[str, Any]]
) -> tuple[list[SkillStep], list[dict[str, Any]]]:
    """Remove every step a validated flag_noise finding matched, archiving each
    one in full rather than discarding it.

    This is the pass's one destructive kind, and the only BUILD-25 kind that
    changes the *shape* of `steps` rather than a field on one step — a wrong
    call here would otherwise permanently lose a step's IdentityBundle (only
    ever produced from a DOM snapshot at record time). Archiving instead of
    deleting makes it reversible: the removed step is saved in full under
    compile_report["archived_steps"] (never shipped to the customer pack —
    skill_package_builder_saved_skill.py only copies from the *returned*,
    filtered `steps`), so a person can restore one later if the compiler was
    wrong. Intentionally not folded into apply_second_opinion/_apply_one,
    which assume a 1:1 walk over unchanged-length `steps`.

    Refuses any finding whose step triggers an observed download or opens a
    file-chooser upload (_step_causes_observed_effect): such a click leaves
    post_condition_effect at "none" by design (the page itself doesn't
    visibly react), which is exactly the evidence flag_noise otherwise reads
    as "did nothing" — a real bug this caught (see TODO.md).
    """
    flagged = {
        str(f.get("step_key") or ""): f for f in findings if f.get("kind") == "flag_noise"
    }
    if not flagged:
        return steps, []
    kept: list[SkillStep] = []
    archived: list[dict[str, Any]] = []
    keys = step_keys(steps)
    for i, (step, key) in enumerate(zip(steps, keys)):
        finding = flagged.get(key)
        # An outcome fork has no page effect of its own to observe (its job is the branch it
        # takes), which is exactly what flag_noise reads as "did nothing". Never archive one.
        is_fork = (step.action.get("action") if isinstance(step.action, dict) else step.action) == "outcome_fork"
        if finding is None or is_fork or _step_causes_observed_effect(steps, i):
            kept.append(step)
            continue
        archived.append({
            "step_key": key,
            "step": step.model_dump(mode="json"),
            "category": str(finding.get("proposed") or ""),
            "why": str(finding.get("why") or ""),
        })
    return kept, archived


if __name__ == "__main__":
    assert rewrite_placeholder("prefix {{ name }} suffix", "name", "sender") == "prefix {{sender}} suffix"
    assert rewrite_placeholder("{{name}}", "name", "sender") == "{{sender}}"
    assert rewrite_placeholder(None, "name", "sender") is None
    assert rewrite_placeholder("{{other}}", "name", "sender") == "{{other}}"

    def _step(**kw: Any) -> SkillStep:
        return SkillStep(action={"action": "type"}, **kw)

    rename = _step(input_binding="email_2", value="{{email_2}}")
    literal = _step(value="INV-2024-0891")
    phase = _step()
    optional = _step(
        target={"primary_selector": "#cookie-ok"},
        optional_hint={"container_signal": ".cookie-banner"},
    )
    steps = [rename, literal, phase, optional]
    keys = step_keys(steps)
    applied = apply_second_opinion(
        steps,
        [
            {"step_key": keys[0], "kind": "rename_binding", "current": "email_2", "proposed": "sender_email"},
            {"step_key": keys[1], "kind": "parameterize_literal", "current": "INV-2024-0891", "proposed": "reference_number"},
            {"step_key": keys[2], "kind": "label_phase", "current": "", "proposed": "login"},
            {"step_key": keys[3], "kind": "suggest_optional", "current": "", "proposed": "true"},
            {"step_key": "not-a-real-key#1", "kind": "label_phase", "current": "", "proposed": "act"},
        ],
    )
    assert len(applied) == 4, applied
    assert rename.input_binding == "sender_email" and rename.value == "{{sender_email}}"
    assert literal.input_binding == "reference_number" and literal.value == "{{reference_number}}"
    assert phase.phase == "login"
    assert optional.action["action"] == "try_dismiss"
    assert optional.branch["candidates"] == ["#cookie-ok", ".cookie-banner"]
    assert optional.optional_hint is None
    assert optional.recovery.max_attempts == 0

    # Shape guards: nothing to rename, and a hint already consumed.
    leftovers = step_keys([phase, optional])
    assert (
        apply_second_opinion(
            [phase, optional],
            [
                {"step_key": leftovers[0], "kind": "rename_binding", "current": "", "proposed": "x"},
                {"step_key": leftovers[1], "kind": "suggest_optional", "current": "", "proposed": "true"},
            ],
        )
        == []
    )

    # suggest_assertion: appends an advisory (required=False) assertion; a
    # duplicate proposal is a no-op.
    asserted = _step()
    (akey,) = step_keys([asserted])
    proposal = json.dumps({"type": "text_present", "target": "Payment successful"})
    applied_assertion = apply_second_opinion(
        [asserted],
        [{"step_key": akey, "kind": "suggest_assertion", "current": "", "proposed": proposal}],
    )
    assert len(applied_assertion) == 1
    assert len(asserted.validation.assertions) == 1
    assert asserted.validation.assertions[0].type == "text_present"
    assert asserted.validation.assertions[0].required is False
    dup_applied = apply_second_opinion(
        [asserted],
        [{"step_key": akey, "kind": "suggest_assertion", "current": "", "proposed": proposal}],
    )
    assert dup_applied == []
    assert len(asserted.validation.assertions) == 1

    # archive_flagged_steps: removes the matched step, keeps the rest, and the
    # archived entry round-trips the full step.
    keep_a, drop_b, keep_c = _step(), _step(), _step()
    kept, archived = archive_flagged_steps(
        [keep_a, drop_b, keep_c],
        [
            {
                "step_key": step_keys([keep_a, drop_b, keep_c])[1],
                "kind": "flag_noise",
                "current": "",
                "proposed": "no_op_action",
                "why": "click had no observed effect",
            }
        ],
    )
    assert kept == [keep_a, keep_c]
    assert len(archived) == 1
    assert archived[0]["category"] == "no_op_action"
    assert archived[0]["step"]["action"] == {"action": "type"}

    print("ok")
