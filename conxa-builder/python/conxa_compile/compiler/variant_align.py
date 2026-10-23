"""Compare a *variant* recording with the workflow's own recording, around an outcome fork.

An author who recorded "file already exists: Replace / Keep both / Cancel" can record the same task
again where the popup does NOT appear. This module answers one question from those two takes: where
do they differ, and is every difference explained by the fork?

  * The variant simply has no popup  -> the clean path is now OBSERVED (``clean_observed``).
  * The variant has a different decision popup the primary never saw -> ``new_dialogs`` (a new
    state for the fork).
  * Anything else that differs -> ``unexplained`` -- reported to the author, NEVER merged. A variant
    is evidence about a popup, not a way to silently rewrite the workflow.

Pure and deterministic: no model, no I/O, no compile. The two event lists are compared with a
longest-common-subsequence over a value-free signature of each event (action + what it acted on),
so a different file name or typed value is not a difference.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from conxa_compile.compiler.outcome_fork import DECISION, classify_dialog

_NAV_ACTIONS = frozenset({"navigate", "manual_navigate", "goto", "go_to", "open"})


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def event_signature(ev: dict[str, Any]) -> str:
    """Value-free identity of one recorded event: what kind of action, on what."""
    action = ev.get("action") or {}
    kind = str(action.get("action") or "")
    if kind in _NAV_ACTIONS:
        url = str(action.get("url") or (ev.get("page") or {}).get("url") or "")
        parts = urlsplit(url)
        return f"{kind}|{parts.netloc.lower()}{parts.path.rstrip('/')}"
    target = ev.get("target") or {}
    label = target.get("aria_label") or target.get("label_text") or target.get("inner_text") or target.get("name") or ""
    role = target.get("role") or target.get("tag") or ""
    return f"{kind}|{_norm(role)}|{_norm(label)[:40]}"


def _lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Index pairs of one longest common subsequence of ``a`` and ``b``."""
    n, m = len(a), len(b)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if a[i] == b[j] else max(table[i + 1][j], table[i][j + 1])
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _dialog_key(ctx: Any) -> tuple[str, str]:
    ctx = ctx if isinstance(ctx, dict) else {}
    return (str(ctx.get("container_signal") or ""), _norm(ctx.get("title")))


def _describe(ev: dict[str, Any]) -> str:
    from conxa_compile.recorder.plain import describe_event

    try:
        return str(describe_event(ev).get("text") or "").strip() or event_signature(ev)
    except Exception:  # noqa: BLE001 -- a description is only for the author to read
        return event_signature(ev)


def compare_variant(
    primary: list[dict[str, Any]],
    variant: list[dict[str, Any]],
    known_dialogs: list[tuple[str, str]],
) -> dict[str, Any]:
    """``known_dialogs`` are the ``(container_selector, normalized title)`` of the fork's existing
    states. Returns ``{clean_observed, new_dialogs, unexplained}`` (see module docstring)."""
    known = {(c, _norm(t)) for c, t in known_dialogs}
    sig_p = [event_signature(e) for e in primary]
    sig_v = [event_signature(e) for e in variant]
    pairs = _lcs_pairs(sig_p, sig_v)
    matched_p = {i for i, _ in pairs}
    matched_v = {j for _, j in pairs}

    popup_events_missing_in_variant = 0
    unexplained: list[str] = []
    for i, ev in enumerate(primary):
        if i in matched_p:
            continue
        if _dialog_key(ev.get("dialog_context")) in known and classify_dialog(ev.get("dialog_context")) == DECISION:
            popup_events_missing_in_variant += 1
        else:
            unexplained.append(f"Only in the original recording: {_describe(ev)}")

    new_dialogs: list[dict[str, Any]] = []
    seen_new: set[tuple[str, str]] = set()
    for j, ev in enumerate(variant):
        if j in matched_v:
            continue
        ctx = ev.get("dialog_context")
        key = _dialog_key(ctx)
        if classify_dialog(ctx) == DECISION and key not in known:
            if key not in seen_new:
                seen_new.add(key)
                new_dialogs.append(ctx)
        elif classify_dialog(ctx) == DECISION and key in known:
            continue  # the variant hit a popup the fork already knows -- nothing new to learn
        else:
            unexplained.append(f"Only in the new recording: {_describe(ev)}")

    # A variant that hit a popup the fork already knows taught us nothing about the clean path.
    variant_saw_known_popup = any(
        classify_dialog(ev.get("dialog_context")) == DECISION and _dialog_key(ev.get("dialog_context")) in known
        for ev in variant
    )
    return {
        "clean_observed": popup_events_missing_in_variant > 0 and not unexplained and not variant_saw_known_popup and not new_dialogs,
        "new_dialogs": new_dialogs,
        "unexplained": unexplained,
    }


def normalize_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same rule-based clean-up a compile applies before building steps (no model call, no
    credit, nothing written), so a primary and a variant are compared like for like."""
    from conxa_compile.compiler.navigation_effects import fold_action_caused_navigations
    from conxa_compile.compiler.step_anchors import clean_steps, fix_step_order
    from conxa_compile.pipeline.run import run_pipeline
    from conxa_compile.policy.bundle import get_policy_bundle

    policy = get_policy_bundle().data
    events = run_pipeline(raw)
    return fold_action_caused_navigations(fix_step_order(clean_steps(events, policy), policy))
