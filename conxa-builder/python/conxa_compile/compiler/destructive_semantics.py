"""Compile-time destructive intent detection (shared tokens with confidence audit; policy-extensible)."""

from __future__ import annotations

from typing import Any

from conxa_compile.compiler.action_semantics import action_name, commit_intent_hit
from conxa_compile.compiler.intent_access import get_effective_intent
from conxa_compile.confidence.uncertainty import default_destructive_tokens

Step = dict[str, Any]

# PROD-3: action kinds that never mutate page/application state — never irreversible regardless
# of intent text. Mirrors ActionKind (conxa_core.models.events) minus every mutating kind.
_READ_ONLY_ACTIONS = frozenset({
    "scroll", "hover", "screenshot", "wait", "wait_until", "clipboard_write", "clipboard_read", "focus", "check", "assert",
    "frame_enter", "frame_exit", "browser_back", "browser_forward",
    "clipboard_copy", "dialog_appeared", "file_chooser_opened",
})


def destructive_intent_tokens(policy: dict[str, Any]) -> tuple[str, ...]:
    """The default words (policy data) plus any a custom policy adds."""
    val = policy.get("validation") if isinstance(policy.get("validation"), dict) else {}
    extra = val.get("destructive_intent_substrings")
    if isinstance(extra, list):
        merged = list(default_destructive_tokens()) + [str(x).lower() for x in extra if x]
        return tuple(dict.fromkeys(merged))
    return default_destructive_tokens()


def label_has_destructive_word(label: str, policy: dict[str, Any] | None = None) -> bool:
    """True when a control's visible label names a destructive action (same words, same match as an intent)."""
    lowered = str(label or "").lower()
    return bool(lowered) and any(tok and tok in lowered for tok in destructive_intent_tokens(policy or {}))


def step_has_destructive_intent(step: Step, policy: dict[str, Any]) -> bool:
    semantic = step.get("semantic") or {}
    if semantic.get("is_destructive") is True:
        return True
    intent = get_effective_intent(semantic).lower()
    if not intent:
        return False
    for tok in destructive_intent_tokens(policy):
        if tok and tok in intent:
            return True
    return False


def destructive_compiler_step(step: Step, policy: dict[str, Any]) -> bool:
    """True when step is an actionable click under destructive intent (compiler record shape)."""
    if action_name(step).lower() != "click":
        return False
    return step_has_destructive_intent(step, policy)


def classify_consequence(step: Step, policy: dict[str, Any]) -> str:
    """PROD-3 danger class: "read_only" | "reversible" | "irreversible".

    Irreversible mirrors the click-only gate every existing destructive/commit check already
    uses (destructive_compiler_step, is_consequential_click in build.py, patch_gate.py) — a
    fill/select can feed a later irreversible click but is not itself the point of no return.
    """
    action = action_name(step).lower()
    if action in _READ_ONLY_ACTIONS:
        return "read_only"
    if action == "click" and (destructive_compiler_step(step, policy) or commit_intent_hit(step, policy)):
        return "irreversible"
    return "reversible"
