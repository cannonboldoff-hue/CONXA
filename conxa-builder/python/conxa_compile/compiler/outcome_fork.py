"""Outcome forks: turn a recorded *decision dialog* into a step that handles every known state.

Why this exists. A popup like Drive's "file already exists: Replace / Keep both / Cancel" used to be
treated as a dismissable nuisance: the recorder flagged the click as an optional interstitial and
the compiler turned it into a ``try_dismiss`` whose first candidate is the button the author
happened to press. That silently replays one recorded choice forever, and says nothing about the
case where the popup never appears. A decision dialog is different in kind: the app can
legitimately land in several states, and what to do in each is a *policy*, not a selector.

What this module does (pure, no LLM, no I/O):
  * ``classify_dialog`` separates decision dialogs from consent banners, forms and plain
    close-me popups. Only decisions become forks; everything else keeps today's path.
  * ``build_outcome_forks`` collapses the run of recorded steps made inside one decision dialog into
    ONE ``outcome_fork`` step. The recorded option reuses the real compiled step(s); options nobody
    clicked get a deterministic role+name click scoped to the dialog (``bundle_from_descriptor``) —
    no model ever writes a selector (CLAUDE.md Key Invariants).
  * Defaults are safe before any human reviews them: a recorded choice that is harmless runs
    automatically, a consequential one (replace / overwrite / delete ...) asks a person.

The runtime side is ``runtime/app/fork.js`` + ``run.js::runForkStep``.
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from conxa_compile.compiler.action_policy import no_recovery_block
from conxa_compile.editor.overlay_identity import bundle_from_descriptor
from conxa_core.models.skill_spec import IdentitySignal, SkillStep

FORK_INTENT = "handle_popup_decision"
DEFAULT_TIMEOUT_MS = 4000

# Fork-local on purpose. Widening compiler/destructive_semantics' global token list would make
# "Replace" in a find-and-replace screen look destructive everywhere; here the word only matters
# because it is a button inside a popup that is asking the person to choose.
_CONSEQUENTIAL_RE = re.compile(
    r"\b(replace|overwrite|discard|delete|remove|erase|override|permanently|"
    r"don'?t save|do not save|revoke|reset|clear all)\b",
    re.IGNORECASE,
)
_CONSENT_RE = re.compile(
    r"\b(cookie|cookies|consent|gdpr|privacy|accept all|reject all|manage preferences|onetrust)\b",
    re.IGNORECASE,
)
# Buttons that only ever mean "go away". A dialog made of nothing but these is dismissable, not a
# decision.
_PURE_DISMISS = frozenset({
    "close", "cancel", "dismiss", "no thanks", "not now", "skip", "later", "maybe later", "x", "×",
})

DECISION = "decision"
DISMISSABLE = "dismissable"
CONSENT = "consent"
FORM = "form"


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _slug(s: Any, fallback: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")
    return out[:40] or fallback


def message_tokens(text: str) -> list[str]:
    """Words of a dialog message with quoted text and digits stripped, so "report.pdf already
    exists" and "q3.xlsx already exists" tokenize identically (and customer file names never ride
    along into a shipped pack). Mirrors runtime/app/fork.js::messageTokens."""
    stripped = re.sub(r"([\"'“”‘’]).*?\1", " ", str(text or ""))
    stripped = re.sub(r"\d+", " ", stripped)
    seen: list[str] = []
    for w in _norm(stripped).split():
        if len(w) > 2 and w not in seen:
            seen.append(w)
    return seen[:12]


def _button_name(b: dict[str, Any]) -> str:
    return str(b.get("name") or b.get("text") or "").strip()


def classify_dialog(ctx: Any) -> str:
    """decision | dismissable | consent | form. Only ``decision`` becomes a fork."""
    if not isinstance(ctx, dict):
        return DISMISSABLE
    # A native alert/confirm/prompt is answered by its own dialog_accept/dialog_dismiss step
    # today; forking those needs the dialog pre-armed before the click that raises it.
    if ctx.get("kind") == "native":
        return DISMISSABLE
    if ctx.get("has_fields"):
        return FORM
    buttons = [b for b in (ctx.get("buttons") or []) if isinstance(b, dict) and _button_name(b)]
    haystack = " ".join(
        [str(ctx.get("title") or ""), str(ctx.get("message") or ""), str(ctx.get("container_signal") or "")]
        + [_button_name(b) for b in buttons]
    )
    if _CONSENT_RE.search(haystack):
        return CONSENT
    if len(buttons) < 2:
        return DISMISSABLE
    if all(_norm(_button_name(b)) in _PURE_DISMISS for b in buttons):
        return DISMISSABLE
    return DECISION


def option_role(label: str, *, recorded_consequence: str = "") -> str:
    if _CONSEQUENTIAL_RE.search(label or "") or recorded_consequence == "irreversible":
        return "consequential"
    if _norm(label) in _PURE_DISMISS:
        return "dismiss"
    return "neutral"


def _chosen_name(run: list[SkillStep]) -> str:
    for step in reversed(run):
        ctx = step.signals.get("dialog_context") or {}
        for b in ctx.get("buttons") or []:
            if isinstance(b, dict) and b.get("chosen"):
                return _button_name(b)
    return ""


def _scoped_selector(container: str, selector: str) -> str:
    return f"{container} >> {selector}" if container else selector


def _synthetic_click(template: SkillStep, button: dict[str, Any], container: str, *, consequential: bool) -> SkillStep | None:
    """A click on a button nobody pressed while recording. Identity comes from the button's own
    visible descriptor (role + name, testid, id), scoped to the dialog container — deterministic,
    never model-written."""
    bundle = bundle_from_descriptor(button)
    if bundle is None:
        return None
    if container:
        for sig in bundle.signals:
            if isinstance(sig, IdentitySignal):
                sig.selector = _scoped_selector(container, sig.selector)
    name = _button_name(button)
    step = copy.deepcopy(template)
    step.intent = f"click_{_slug(name, 'option')}"
    step.semantic_description = f'Click "{name}" in the popup'
    step.identity_bundle = bundle
    step.identity_bundle.destructive = bool(consequential)
    step.consequence = "irreversible" if consequential else "reversible"
    step.target = {
        "primary_selector": bundle.signals[0].selector if bundle.signals else "",
        "tag": str(button.get("tag") or "button"),
        "role": str(button.get("role") or "button"),
        "inner_text": str(button.get("text") or name),
    }
    step.compiled_selectors = []
    step.value = None
    step.input_binding = None
    step.entity_binding = None
    step.optional_hint = None
    step.validation = step.validation.model_copy(update={"assertions": [], "wait_for": step.validation.wait_for})
    step.signals = {}
    step.branch = {}
    return step


def _dump(step: SkillStep) -> dict[str, Any]:
    return step.model_dump(mode="json")


def _fork_signature(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(ctx.get("title") or ""),
        "button_labels": [_button_name(b) for b in ctx.get("buttons") or [] if isinstance(b, dict) and _button_name(b)],
        "message_tokens": message_tokens(str(ctx.get("message") or "")),
        "container_selector": str(ctx.get("container_signal") or ""),
    }


def _same_dialog(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a.get("container_signal") == b.get("container_signal")
        and _norm(a.get("title")) == _norm(b.get("title"))
    )


def _find_runs(steps: list[SkillStep]) -> list[tuple[int, int]]:
    """[start, end) index ranges of consecutive steps recorded inside one decision dialog."""
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(steps):
        ctx = steps[i].signals.get("dialog_context")
        if classify_dialog(ctx) != DECISION:
            i += 1
            continue
        j = i + 1
        while j < len(steps):
            nxt = steps[j].signals.get("dialog_context")
            if classify_dialog(nxt) == DECISION and _same_dialog(ctx, nxt):
                j += 1
            else:
                break
        runs.append((i, j))
        i = j
    return runs


def _relax_dialog_assertions(steps: list[SkillStep], container: str) -> None:
    """The step that opened the dialog carries a required "dialog is present" assertion
    (build.py::_build_assertions). With a fork, "no dialog appeared" is a valid outcome, so that
    assertion must not fail the run."""
    if not container:
        return
    for step in steps:
        for a in step.validation.assertions:
            if a.type == "selector_present" and str(a.target) == container and a.required:
                a.required = False


def _build_fork(run: list[SkillStep], session_id: str = "") -> SkillStep | None:
    ctx = next((s.signals.get("dialog_context") for s in reversed(run) if s.signals.get("dialog_context")), None)
    if not isinstance(ctx, dict):
        return None
    chosen = _chosen_name(run)
    if not chosen:
        return None  # can't tell which option was recorded: leave the steps as they are
    container = str(ctx.get("container_signal") or "")
    buttons = [b for b in ctx.get("buttons") or [] if isinstance(b, dict) and _button_name(b)]
    last = run[-1]

    options: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    recorded_id = ""
    for b in buttons:
        name = _button_name(b)
        oid = _slug(name, "option")
        n = 2
        while oid in used_ids:
            oid = f"{_slug(name, 'option')}_{n}"
            n += 1
        used_ids.add(oid)
        is_recorded = _norm(name) == _norm(chosen)
        role = option_role(name, recorded_consequence=last.consequence if is_recorded else "")
        if is_recorded:
            recorded_id = oid
            steps_for_option = [copy.deepcopy(s) for s in run]
            for s in steps_for_option:
                s.signals = {}
                s.optional_hint = None
            if role == "consequential":
                tail = steps_for_option[-1]
                tail.identity_bundle.destructive = True
                tail.consequence = "irreversible"
        else:
            synth = _synthetic_click(last, b, container, consequential=(role == "consequential"))
            if synth is None:
                continue  # nothing addressable about this button: skip rather than guess
            steps_for_option = [synth]
        options.append({
            "id": oid,
            "label": name,
            "role": role,
            "recorded": is_recorded,
            "steps": [_dump(s) for s in steps_for_option],
        })
    if not recorded_id or len(options) < 2:
        return None

    recorded = next(o for o in options if o["id"] == recorded_id)
    if recorded["role"] == "consequential":
        policy: dict[str, Any] = {"mode": "ask_human", "suggested": recorded_id}
    else:
        policy = {"mode": "auto", "option_id": recorded_id, "author_approved": False}

    sig = _fork_signature(ctx)
    title = sig["title"] or "popup"
    session_ids = [session_id] if session_id else []
    state_id = f"dialog_{_slug(title, '1')}"
    fork = SkillStep(
        action="outcome_fork",
        intent=FORK_INTENT,
        semantic_description=f'Handle the "{title}" popup',
        tab=dict(run[0].tab),
        frame=dict(run[0].frame),
        recovery=no_recovery_block(FORK_INTENT),
        outcome_fork={
            "timeout_ms": DEFAULT_TIMEOUT_MS,
            "unknown_policy": "ask_human",
            "clean": {"observed": False, "observed_in": []},
            "states": [{
                "id": state_id,
                "kind": "dialog",
                "signature": sig,
                "options": options,
                "policy": policy,
                "observed_in": session_ids,
            }],
        },
    )
    fork.identity_bundle.stable_hash = hashlib.sha256(
        f"fork|{container}|{_norm(title)}|{'|'.join(sorted(_norm(l) for l in sig['button_labels']))}".encode()
    ).hexdigest()[:16]
    return fork


def build_outcome_forks(steps: list[SkillStep], session_id: str = "") -> list[SkillStep]:
    """Collapse every decision-dialog run into an ``outcome_fork`` step. Returns the new list.
    Always strips the transient ``signals["dialog_context"]`` stash (it carries message text that
    is compile-time evidence only and must not travel with the skill)."""
    runs = _find_runs(steps)
    out: list[SkillStep] = []
    cursor = 0
    for start, end in runs:
        fork = _build_fork(steps[start:end], session_id)
        if fork is None:
            continue
        out.extend(steps[cursor:start])
        container = str((steps[start].signals.get("dialog_context") or {}).get("container_signal") or "")
        _relax_dialog_assertions(steps[:start], container)
        out.append(fork)
        cursor = end
    out.extend(steps[cursor:])
    for s in out:
        s.signals.pop("dialog_context", None)
    return out


def state_from_dialog(
    ctx: dict[str, Any], template: SkillStep, session_id: str, used_state_ids: set[str]
) -> dict[str, Any] | None:
    """A new fork state for a decision popup seen only in a variant recording. Every option is a
    deterministic, container-scoped click built from the button's own descriptor (the variant is
    not compiled, so there is no recorded step to reuse); the button the author pressed is marked
    recorded and drives the same safe default as a compiled fork. ``template`` supplies the tab and
    frame the clicks run in."""
    if classify_dialog(ctx) != DECISION:
        return None
    container = str(ctx.get("container_signal") or "")
    buttons = [b for b in ctx.get("buttons") or [] if isinstance(b, dict) and _button_name(b)]
    chosen = next((_button_name(b) for b in buttons if b.get("chosen")), "")
    options: list[dict[str, Any]] = []
    used: set[str] = set()
    recorded_id = ""
    for b in buttons:
        name = _button_name(b)
        oid = _slug(name, "option")
        n = 2
        while oid in used:
            oid = f"{_slug(name, 'option')}_{n}"
            n += 1
        used.add(oid)
        role = option_role(name)
        synth = _synthetic_click(template, b, container, consequential=(role == "consequential"))
        if synth is None:
            continue
        is_recorded = bool(chosen) and _norm(name) == _norm(chosen)
        if is_recorded:
            recorded_id = oid
        options.append({"id": oid, "label": name, "role": role, "recorded": is_recorded, "steps": [_dump(synth)]})
    if len(options) < 2:
        return None
    recorded = next((o for o in options if o["id"] == recorded_id), None)
    if recorded is None or recorded["role"] == "consequential":
        policy: dict[str, Any] = {"mode": "ask_human", **({"suggested": recorded_id} if recorded_id else {})}
    else:
        policy = {"mode": "auto", "option_id": recorded_id, "author_approved": False}
    sig = _fork_signature(ctx)
    state_id = f"dialog_{_slug(sig['title'], '1')}"
    base, n = state_id, 2
    while state_id in used_state_ids:
        state_id = f"{base}_{n}"
        n += 1
    return {
        "id": state_id,
        "kind": "dialog",
        "signature": sig,
        "options": options,
        "policy": policy,
        "observed_in": [session_id] if session_id else [],
    }
