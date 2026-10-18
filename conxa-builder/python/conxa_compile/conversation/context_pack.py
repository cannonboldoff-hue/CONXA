"""Build one turn's context under an explicit token budget (BUILD-37).

The cloud LLM proxy is stateless — ``ProxyBody`` has no ``messages`` field, every task sends one
``[system, user]`` pair — so the harness rebuilds the whole picture on every turn. This module
decides what goes in and what gets dropped when it doesn't all fit.

Priority, highest first (the first four are never dropped):

1. lifecycle, and the cards that are open with the actions they allow
2. PINNED FACTS — answered Clarify decisions, inputs, "done when" checks, confirmation points,
   steps that can't be undone. Never summarized away.
3. the person's latest message
4. (the system prompt and capability manifest live outside this text — see ``llm/client.py``)
5. the workflow digest (shrunk step by step before it is ever dropped) and the latest failure
6. a rolling summary of older turns
7. the most recent turns verbatim, oldest trimmed first

Token estimates are ``len(text) / 4`` — close enough for a budget, and free of a tokenizer
dependency in the Studio.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WORKFLOW_BUDGET = 12_000
DEFAULT_CHAT_BUDGET = 6_000
# Older turns that no longer fit are folded into the rolling summary once this many have piled up.
SUMMARIZE_AFTER_DROPPED = 6


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


@dataclass
class ContextPack:
    text: str
    tokens: int
    dropped_turns: list[dict[str, Any]] = field(default_factory=list)
    digest_shrunk: bool = False

    @property
    def needs_summary(self) -> bool:
        return len(self.dropped_turns) >= SUMMARIZE_AFTER_DROPPED


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# Optional per-step keys, dropped in this order when a digest is too big.
_STEP_SHRINK_ORDER = ("edit_history", "compile_report", "has_for_each", "has_branch", "has_assertions", "semantic_description", "phase")


def shrink_digest(digest: dict[str, Any], max_tokens: int) -> tuple[dict[str, Any], bool]:
    """Fit a workflow digest into ``max_tokens``: strip optional per-step keys one at a time,
    then drop trailing steps with an explicit note. Returns ``(digest, shrunk?)``."""
    if estimate_tokens(_dumps(digest)) <= max_tokens:
        return digest, False
    out = dict(digest)
    steps = [dict(s) for s in digest.get("steps") or [] if isinstance(s, dict)]
    for key in _STEP_SHRINK_ORDER:
        for s in steps:
            s.pop(key, None)
        out["steps"] = steps
        if estimate_tokens(_dumps(out)) <= max_tokens:
            return out, True
    # Still too big: keep as many leading steps as fit, and say how many were left out.
    kept = steps
    while kept and estimate_tokens(_dumps({**out, "steps": kept})) > max_tokens:
        kept = kept[: max(0, int(len(kept) * 0.8))]
        if len(kept) <= 1:
            break
    out["steps"] = kept
    out["steps_omitted"] = max(0, len(steps) - len(kept))
    out["note"] = "Digest shortened to fit; ask for the rest via need[] (expand_step)."
    return out, True


def build_context_pack(
    *,
    message: str,
    budget_tokens: int,
    lifecycle: dict[str, Any] | None = None,
    open_cards: list[dict[str, Any]] | None = None,
    pinned_facts: list[str] | None = None,
    digest: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    summary: str | None = None,
    turns: list[dict[str, Any]] | None = None,
    extra_sections: list[tuple[str, str]] | None = None,
    anchor: str | None = None,
) -> ContextPack:
    """Assemble the user-text for one model call. ``turns`` are ``{"role": "user"|"assistant",
    "text": str}`` oldest first. ``extra_sections`` are ``(title, text)`` blocks that always ride
    along (e.g. the capability manifest for the proposal call)."""
    fixed: list[str] = []
    if lifecycle:
        fixed.append(
            "Where this workflow is now: "
            + _dumps({k: lifecycle.get(k) for k in ("stage", "substate", "note", "allowed_actions")})
        )
    if open_cards:
        fixed.append(
            "Open cards waiting on the person (answer them via the card, never by guessing): "
            + _dumps([
                {"card_type": c.get("card_type"), "title": c.get("title"),
                 "choices": [ch.get("id") for ch in (c.get("choices") or []) if isinstance(ch, dict)]}
                for c in open_cards
            ])
        )
    if pinned_facts:
        fixed.append("PINNED FACTS — decided by the person; treat as true, never contradict:\n- " + "\n- ".join(pinned_facts))
    if anchor:
        fixed.append(f"The person is looking at: {anchor}")
    for title, text in extra_sections or []:
        fixed.append(f"{title}:\n{text}")
    tail = f"Person's message: {message}"

    used = sum(estimate_tokens(s) for s in fixed) + estimate_tokens(tail)
    remaining = max(0, budget_tokens - used)

    blocks: list[str] = []
    digest_shrunk = False
    if digest:
        # Leave a share of what's left for the conversation itself — a digest that eats the whole
        # budget would make every turn amnesiac.
        digest_budget = int(remaining * 0.6)
        shrunk, digest_shrunk = shrink_digest(digest, max(200, digest_budget))
        text = "Workflow digest (compact; ask for more via need[]):\n" + _dumps(shrunk)
        blocks.append(text)
        remaining -= estimate_tokens(text)
    if failure:
        text = "Latest test failure:\n" + _dumps(failure)
        blocks.append(text)
        remaining -= estimate_tokens(text)
    if summary:
        text = "Earlier in this conversation (summary):\n" + summary.strip()
        blocks.append(text)
        remaining -= estimate_tokens(text)

    # Recent turns, newest first until the budget runs out, then restored to oldest-first.
    kept: list[str] = []
    kept_turns = 0
    dropped: list[dict[str, Any]] = []
    all_turns = [t for t in (turns or []) if isinstance(t, dict) and str(t.get("text") or "").strip()]
    for i in range(len(all_turns) - 1, -1, -1):
        t = all_turns[i]
        line = f"{str(t.get('role') or 'user')}: {str(t.get('text')).strip()}"
        cost = estimate_tokens(line)
        if cost <= remaining:
            kept.append(line)
            remaining -= cost
            kept_turns += 1
        else:
            dropped = all_turns[: i + 1]
            break
    if kept:
        kept.reverse()
        blocks.append("Recent conversation:\n" + "\n".join(kept))

    parts = fixed + blocks + [tail]
    text = "\n\n".join(parts)
    return ContextPack(text=text, tokens=estimate_tokens(text), dropped_turns=dropped, digest_shrunk=digest_shrunk)
