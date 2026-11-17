"""The gates of the proof picker: one per probe type a decision model may pick that code did not already mine.

A gate gets the compiled step list and the index of a step that has an ``effect`` and returns a check built from
evidence the recording really left on the steps, or None. Nothing here is asked of a model, and a gate never
settles for weaker evidence: when the evidence is not on the steps it says None and the pick is only recorded.
No check holds a selector; an earlier step is named by its step key and found again by the runtime through that
step's own identity bundle.

  url_stays          the compiler saw this step land on a different address (an advisory ``url_pattern`` assertion)
                     and that address is not the one the step started on.
  row_with_identity  a save or send, after a value the run enters was typed on the same page: that value must show
                     up on the page one more time than before (``runtime/app/result_probes.js``). A delete on one row
                     of a list: that row's identifying text must show one time fewer.
  value_equals       a save, after such a value was typed: a fresh copy of the page must still hold it. Rung
                     ``completed``, not ``result_exists``: a form that restores a draft shows the value without
                     any save, so it must never meet the bar that "the recorded result is absent" raises.
  later_step         this step makes a file a later step consumes (``produces`` / ``consumes``): that later step
                     working proves it. Only ever settles a step the run could not confirm.
"""

from __future__ import annotations

import re
from typing import Callable

from conxa_compile.compiler.navigation_effects import generalize_url_pattern
from conxa_compile.compiler.step_key import step_keys
from conxa_core.models.skill_spec import EffectCheck, SkillStep

Gate = Callable[[list[SkillStep], int], EffectCheck | None]

_TYPING = {"type", "fill"}


def _action(step: SkillStep) -> str:
    a = step.action
    return str((a.get("action") if isinstance(a, dict) else a) or "").lower()


def _typed_before(steps: list[SkillStep], i: int) -> tuple[int, str] | None:
    """The nearest earlier step on the same page that types a value the run provides: (its index, its input name)."""
    for j in range(i - 1, -1, -1):
        s = steps[j]
        if s.input_binding and _action(s) in _TYPING and s.url == steps[i].url:
            return j, s.input_binding
    return None


def url_stays(steps: list[SkillStep], i: int) -> EffectCheck | None:
    step = steps[i]
    landed = next((a.target for a in step.validation.assertions if a.type == "url_pattern" and a.target), "")
    started = generalize_url_pattern(step.url)
    if not landed or not started or landed == started:
        return None   # a move to the same kind of address says nothing: the query string is not part of the template
    try:
        if re.search(landed, step.url):
            return None   # the step began on an address the pattern already matches
    except re.error:
        return None
    return EffectCheck(rung="accepted", witness="renderer", probe={"type": "url_stays", "pattern": landed},
                       plain="the page moved on to its next screen and stayed there")


def _typed_in_this_entry(steps: list[SkillStep], i: int) -> list[str]:
    """Every value the run provides typed on this page since the last step that saved or sent, nearest first."""
    out: list[str] = []
    for j in range(i - 1, -1, -1):
        s = steps[j]
        if s.validation.effect is not None or s.url != steps[i].url:
            break
        if s.input_binding and _action(s) in _TYPING and s.input_binding not in out:
            out.append(s.input_binding)
    return out


def row_with_identity(steps: list[SkillStep], i: int) -> EffectCheck | None:
    effect = steps[i].validation.effect
    row = steps[i].entity_binding
    if effect is not None and effect.kind == "delete" and row is not None and str(row.identifier or "").strip():
        # A delete on one row: that row's own text shows on the page one time fewer. A page-only app (a cart kept in
        # the browser) sends no request, so "the app accepted the request" can never answer there (WF-3 Leg B).
        return EffectCheck(rung="result_exists", witness="renderer",
                           probe={"type": "row_with_identity", "key": row.identifier, "keys": [row.identifier], "delta": -1},
                           plain="what you removed is gone from the page")
    typed = _typed_in_this_entry(steps, i)
    # An upload already has its own "go and look" for the file's name; a second check on it would count it twice.
    if effect is None or effect.kind not in {"save", "send"} or not typed:
        return None
    # Every value of the entry, nearest first: the runtime uses the first long enough to mean something
    # (result_probes.wantedIdentity), so a short nearest one ("QA") does not leave the step unprovable.
    keys = ["{{" + b + "}}" for b in typed]
    return EffectCheck(rung="result_exists", witness="renderer",
                       probe={"type": "row_with_identity", "key": keys[0], "keys": keys, "delta": 1},
                       plain="what you entered now shows up on the page")


def value_equals(steps: list[SkillStep], i: int) -> EffectCheck | None:
    effect = steps[i].validation.effect
    typed = _typed_before(steps, i)
    if effect is None or effect.kind != "save" or typed is None:
        return None
    return EffectCheck(rung="completed", witness="fresh_look",
                       probe={"type": "value_equals", "field_step": step_keys(steps)[typed[0]], "value": "{{" + typed[1] + "}}"},
                       plain="what you entered is still there when the page is opened again")


def later_step(steps: list[SkillStep], i: int) -> EffectCheck | None:
    made = set(steps[i].produces)
    if not made:
        return None
    for j in range(i + 1, len(steps)):
        if made & set(steps[j].consumes):
            return EffectCheck(rung="completed", witness="later_step",
                               probe={"type": "later_step", "step": step_keys(steps)[j]},
                               plain="a later step that needs the file this one makes worked")
    return None


GATES: dict[str, Gate] = {
    "url_stays": url_stays,
    "row_with_identity": row_with_identity,
    "value_equals": value_equals,
    "later_step": later_step,
}
