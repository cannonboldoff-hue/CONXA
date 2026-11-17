"""Proof-of-Done picker: a decision model ranks which kinds of proof fit each step; code decides what gets built.

For each compiled step that starts work (it already has a ``validation.effect``), the model is asked one yes/no
question per probe type in ``EFFECT_PROBES`` ("would X prove this step worked?") and returns a probability for
each. The top ``TOP_N`` at or above ``MIN_P`` are *picks*. A pick never becomes a check by itself: it goes through
that probe's gate in ``proof_gates.GATES``, which builds a check only from evidence the recording really holds, or returns
None. The model only ever adds: a check code already built stays whatever the model thinks of it, and the
failure-signal veto is never touched. It writes no text, selector or rung, only a probability per fixed question.

Every pick and what became of it is written to ``proof_picks.json`` beside the session, so the model can be
compared with the rules. Any failure (no key, offline, quota, a bad reply) leaves the steps exactly as code built
them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from conxa_compile.app_graph.scrub import safe_element_name, scrub_text
from conxa_compile.compiler.proof_gates import GATES
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.llm.llm_cache import cache_key, read_cached, write_cached
from conxa_core.models.skill_spec import EFFECT_PROBES, Effect, SkillStep
from conxa_core.progress import append_current_job_event

MIN_P = 0.6          # a starting guess: tune it on proof_picks.json against test-run results
TOP_N = 5
SIDECAR = "proof_picks.json"
NAMESPACE = "proof_picks"
_CACHE_VERSION = 1    # bump when PROBE_QUESTIONS or the context shape changes
_TIMEOUT_MS = 20_000
_TASK = "proof_pick"

# One plain question per probe type. The model sees these and nothing else about the probes.
PROBE_QUESTIONS: dict[str, str] = {
    "input_has_files": "Would the file field now holding the chosen file prove this step worked?",
    "request_chain": "Would the app answering a data-changing request with success prove this step worked?",
    "failure_signal": "Would the page showing no error or refusal message prove this step worked?",
    "new_text": "Would a message appearing on the page that says the work is finished prove this step worked?",
    "download_file": "Would a new file arriving on this computer prove this step worked?",
    "value_equals": "Would a field still showing the entered value after the page is opened again prove this step worked?",
    "row_with_identity": "Would the item appearing in a list one more time than before prove this step worked?",
    "url_stays": "Would the page moving to a new address and staying there prove this step worked?",
    "fresh_look": "Would opening the same page again and finding the item there prove this step worked?",
    "later_step": "Would a later step of this workflow, which only works if this step succeeded, prove this step worked?",
}
assert set(PROBE_QUESTIONS) == set(EFFECT_PROBES), "every probe type needs a question"

def step_context(step: SkillStep, effect: Effect) -> str:
    """The value-free facts the model sees. Never a typed value, a file name or an address: text goes through
    ``scrub`` (quoted spans, emails, file-like words and anything with a digit are dropped); inputs go by name only."""
    vc = step.video_context
    names = [n for n in [step.input_binding, *step.consumes] if n]
    facts = {
        "kind": effect.kind,
        "what_the_step_does": scrub_text(step.intent, 120),
        "button": safe_element_name(step.model_dump(mode="json")) or "",
        "screen_after": scrub_text(vc.screen_after, 160) if vc else "",
        "purpose": scrub_text(vc.purpose, 120) if vc else "",
        "app_words": effect.words,
        "checks_already_built": sorted({str(c.probe.get("type")) for c in effect.checks if c.role == "support"}),
        "page_checks": sorted({a.type for a in step.validation.assertions}),
        "input_names": sorted(set(names)),
    }
    return json.dumps(facts, ensure_ascii=False, sort_keys=True)


def rank(probabilities: dict[str, float]) -> list[tuple[str, float]]:
    """The top ``TOP_N`` known probes at or above ``MIN_P``, best first; ties break by probe name."""
    known = [(p, float(v)) for p, v in probabilities.items() if p in PROBE_QUESTIONS]
    return sorted((x for x in known if x[1] >= MIN_P), key=lambda x: (-x[1], x[0]))[:TOP_N]


def _ask(call: Callable[..., Any], context: str) -> dict[str, float] | None:
    key = cache_key(_CACHE_VERSION, context=context)
    hit = read_cached(NAMESPACE, key, _CACHE_VERSION)
    if hit is not None:
        return {str(k): float(v) for k, v in (hit.get("p") or {}).items()}
    questions = [{"name": p, "instructions": q} for p, q in PROBE_QUESTIONS.items()]
    out = call(_TASK, {"input": context, "questions": questions}, _TIMEOUT_MS)
    probs = out.get("probabilities") if isinstance(out, dict) else None
    if not isinstance(probs, dict):
        return None   # not configured, or the call failed: nothing is cached, nothing changes
    clean = {str(k): float(v) for k, v in probs.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    write_cached(NAMESPACE, key, {"p": clean}, _CACHE_VERSION)
    return clean


def apply_picks(steps: list[SkillStep], i: int, picks: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Record what became of each pick; a gate-built check goes in just before the veto. Only adds."""
    effect = steps[i].validation.effect
    out: list[dict[str, Any]] = []
    for probe, p in picks:
        row: dict[str, Any] = {"probe": probe, "p": round(p, 3)}
        have = {str(c.probe.get("type")) for c in effect.checks}
        gate = GATES.get(probe)
        if probe in have:
            row["status"] = "already_built"
        elif gate is None:
            row["status"] = "no_gate"
        else:
            check = gate(steps, i)
            if check is None:
                row["status"] = "gate_rejected"
            else:
                at = next((i for i, c in enumerate(effect.checks) if c.role == "veto"), len(effect.checks))
                effect.checks.insert(at, check.model_copy(update={"id": f"c{len(effect.checks) + 1}"}))
                row["status"] = "built"
        out.append(row)
    return out


def add_page_side_proof(steps: list[SkillStep]) -> None:
    """Code's floor, whatever the model ranked: a save or send made after typing on the same page gets "what you
    entered now shows up on the page" unless it already has a check that looks at the page. Its only other proof is
    "the app accepted the request", which a page-only form never sends; a stray ad or analytics request answered it
    by chance (WF-3 A: four Submits "proven" that way, the fifth unconfirmed). A delete on one row of a list gets
    "what you removed is gone from the page" the same way (WF-3 Leg B: a cart kept in the browser)."""
    for i, step in enumerate(steps):
        effect = step.validation.effect
        if effect is None or effect.kind not in {"save", "send", "delete"}:
            continue
        if any(c.role != "veto" and c.witness in {"renderer", "fresh_look", "later_step"} for c in effect.checks):
            continue
        apply_picks(steps, i, [("row_with_identity", 1.0)])


def run_proof_picker(steps: list[SkillStep], session_root: Path | None = None) -> dict[str, Any]:
    """Ask once per step that has an effect and apply the picks. Never raises: the effect checks code built are
    the floor. Returns the report that is also written to ``proof_picks.json``."""
    report: dict[str, Any] = {"version": 1, "min_p": MIN_P, "top_n": TOP_N, "status": "off", "steps": []}
    try:
        from conxa_core.llm import get_router

        call = getattr(get_router(), "route_decision", None)
    except Exception:  # noqa: BLE001 -- no router installed: the picker is simply off
        call = None
    if call is None:
        return report
    report["status"] = "ok"
    for i, (key, step) in enumerate(zip(step_keys(steps), steps)):
        effect = step.validation.effect
        if effect is None:
            continue
        try:
            probs = _ask(call, step_context(step, effect))
        except Exception as exc:  # noqa: BLE001 -- offline, quota, a bad reply: stop asking, change nothing
            probs, report["error"] = None, f"{type(exc).__name__}: {exc}"[:200]
        if probs is None:
            # One failure ends the pass: every step would hit the same wall, and each retry costs seconds.
            report["status"] = "unavailable"
            break
        picks = rank(probs)
        report["steps"].append({"step_key": key, "kind": effect.kind, "all": {k: round(v, 3) for k, v in probs.items()},
                                "picks": apply_picks(steps, i, picks)})
    if report["status"] == "unavailable" and report.get("error"):
        append_current_job_event("compile_phase", "Proof picks skipped: the decision model was unavailable.",
                                 {"phase": "proof_picks_unavailable", "reason": report["error"]})
    if session_root is not None and report["steps"]:
        try:
            (Path(session_root) / SIDECAR).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass   # a sidecar that cannot be written costs the comparison, not the compile
    return report
