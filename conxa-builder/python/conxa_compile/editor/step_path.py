"""Path addressing for a nested step inside another step's body — `branch.steps[N]` (if_present,
EXEC-1) or `for_each.steps[N]` (EXEC-38). Shared by `handlers/workflow_editor.py`'s patch
RPCs, so every write path parses and resolves the same syntax — a hand-rolled second parser drifts (see
CLAUDE.md's try_dismiss-branch-builder invariant for why that rule exists elsewhere too).
"""

from __future__ import annotations

import re
from typing import Any

_NESTED_STEP_PATH_RE = re.compile(r"^(branch|for_each)\.steps\[(\d+)\]$")

NestedPath = tuple[str, int]


class StepPathError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_nested_step_path(path: str | None) -> NestedPath | None:
    """None for an absent/empty path (ordinary top-level step). Raises StepPathError for a
    non-empty string that isn't `branch.steps[N]` or `for_each.steps[N]`."""
    if not path:
        return None
    match = _NESTED_STEP_PATH_RE.match(str(path))
    if not match:
        raise StepPathError("invalid_step_path", f"Unsupported step path: {path!r}")
    return match.group(1), int(match.group(2))


def get_nested_step(parent_step: dict[str, Any], nested_path: NestedPath) -> dict[str, Any]:
    container_key, nested_index = nested_path
    raw_container = parent_step.get(container_key)
    container: dict[str, Any] = raw_container if isinstance(raw_container, dict) else {}
    raw_nested_steps = container.get("steps")
    nested_steps: list[Any] = raw_nested_steps if isinstance(raw_nested_steps, list) else []
    if nested_index < 0 or nested_index >= len(nested_steps):
        raise StepPathError("step_not_found", f"Nested step {nested_index} out of range")
    return dict(nested_steps[nested_index])


def with_nested_step(
    parent_step: dict[str, Any], nested_path: NestedPath, nested_step: dict[str, Any]
) -> dict[str, Any]:
    """Returns a NEW parent_step dict with `nested_step` written back at `nested_path`. Raises
    StepPathError if the index is out of range — callers always resolve via get_nested_step
    first, so this only re-validates against a document that hasn't changed shape in between."""
    container_key, nested_index = nested_path
    container = dict(parent_step.get(container_key) or {})
    nested_steps = list(container.get("steps") or [])
    if nested_index < 0 or nested_index >= len(nested_steps):
        raise StepPathError("step_not_found", f"Nested step {nested_index} out of range")
    nested_steps[nested_index] = nested_step
    container["steps"] = nested_steps
    new_parent = dict(parent_step)
    new_parent[container_key] = container
    return new_parent
