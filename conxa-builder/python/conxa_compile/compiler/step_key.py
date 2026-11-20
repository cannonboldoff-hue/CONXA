"""Stable, position-independent identity for a compiled step (BUILD-25 stage a).

`step_index` renumbers the moment anyone inserts/deletes/reorders a step (see
BUILD-22/BUILD-23) — so it is the wrong key for anything meant to outlive one
compile, e.g. the reviewer edit log and the compiler's own semantic
suggestions. `identity_bundle.stable_hash` (compiler/stable_hash.py) is
*element* identity, not *step* identity: it is empty for element-less steps
(scroll/navigate/frame markers) and collides when the same element is acted on
twice in one workflow (the COMPILE-1 double date-pick). step_key() closes both
gaps with a fallback hash plus an occurrence ordinal, so two steps never share
a key even when their underlying element does.

Accepts either a plain step dict (as loaded from a saved skill document — the
editor's world) or a SkillStep model instance (the compiler's world, before
`model_dump()`), so both callers share one implementation instead of two.
"""

from __future__ import annotations

from typing import Any

# BUILD-104 (1): a step now carries its own `step_id` (conxa_core/models/step_identity.py). The derived
# `<element hash>#<n>` key below is only what a step with no id yields, so keys stored before ids existed
# keep resolving. Re-exported here so every existing import keeps working.
from conxa_core.models.step_identity import (  # noqa: F401
    assign_compile_ids,
    ensure_step_ids,
    step_id_of,
    step_key,
    step_keys,
)


def step_key_index_map(steps: list[Any]) -> dict[str, int]:
    """The companion lookup to step_keys(): maps each step's key back to its position in
    `steps`, for any code that holds a step_key (from a stored suggestion/finding/edit-log
    entry) and needs the real step behind it — not just to recompute the key list itself.
    Shared rather than re-derived per caller (`{k: i for i, k in enumerate(step_keys(...))}`
    was starting to get copy-pasted — see build.py::_rewrite_loop_suggestions_for_review)."""
    return {k: i for i, k in enumerate(step_keys(steps))}


if __name__ == "__main__":
    click_a = {"action": {"action": "click"}, "url": "https://x", "identity_bundle": {"stable_hash": "h1"}}
    click_b = {"action": {"action": "click"}, "url": "https://x", "identity_bundle": {"stable_hash": "h1"}}
    scroll = {"action": {"action": "scroll"}, "url": "https://x"}

    keys = step_keys([click_a, scroll, click_b])
    assert len(set(keys)) == 3, keys  # same element twice must not collide
    assert keys[0] != keys[2]

    # Inserting a step ahead of an existing one must not change its key.
    keys_with_insert = step_keys([scroll, click_a, scroll, click_b])
    assert keys_with_insert[1] == "h1#1"
    assert keys_with_insert[3] == "h1#2"

    class _FakeBundle:
        stable_hash = "h2"

    class _FakeStep:
        action = {"action": "click"}
        url = "https://y"
        identity_bundle = _FakeBundle()

    # Attribute-style (pydantic-shaped) steps must key identically to the
    # equivalent dict shape.
    obj_key = step_keys([_FakeStep()])[0]
    dict_key = step_keys([{"action": {"action": "click"}, "url": "https://y", "identity_bundle": {"stable_hash": "h2"}}])[0]
    assert obj_key == dict_key == "h2#1"

    print("ok")
