"""Path addressing for a nested step inside another step's body, and one walker over every step of a document.

A body is a list of steps held by another step. There are four, and each has one path form (BUILD-102 (1)):

* `branch.steps[N]`                                  an if_present / try_dismiss body
* `branch.options[J].steps[N]`                       one way of a wait_for_one_of
* `for_each.steps[N]`                                a loop body
* `outcome_fork.states[S].options[O].steps[N]`       what a popup's option does

Shared by `handlers/workflow_editor.py`'s patch RPCs, the copilot gate and every document walker, so each parses
and resolves the same syntax: a hand-rolled second parser drifts (see CLAUDE.md's try_dismiss-branch-builder
invariant for why that rule exists elsewhere too). The list of bodies a step holds is defined once, in
`conxa_core.models.step_identity.child_step_lists`.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from conxa_core.models.step_identity import child_step_lists

_STEPS_PATH_RE = re.compile(
    r"^(?P<route>branch|for_each|branch\.options\[\d+\]|outcome_fork\.states\[\d+\]\.options\[\d+\])"
    r"\.steps\[(?P<index>\d+)\]$"
)
_TOKEN_RE = re.compile(r"([a-z_]+)(?:\[(\d+)\])?")

# (route to the dict that holds `steps`, index in that list). The route is "branch", "for_each",
# "branch.options[1]" or "outcome_fork.states[0].options[2]": the part of the path before ".steps[N]".
NestedPath = tuple[str, int]


class StepPathError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_nested_step_path(path: str | None) -> NestedPath | None:
    """None for an absent/empty path (ordinary top-level step). Raises StepPathError for a
    non-empty string that is none of the four body path forms."""
    if not path:
        return None
    match = _STEPS_PATH_RE.match(str(path))
    if not match:
        raise StepPathError("invalid_step_path", f"Unsupported step path: {path!r}")
    return match.group("route"), int(match.group("index"))


def _route_keys(route: str) -> list[str | int]:
    keys: list[str | int] = []
    for name, idx in _TOKEN_RE.findall(route):
        keys.append(name)
        if idx != "":
            keys.append(int(idx))
    return keys


def _holder(parent_step: dict[str, Any], route: str) -> dict[str, Any]:
    """The dict at `route` that holds a `steps` list ({} when the shape is not there)."""
    cur: Any = parent_step
    for key in _route_keys(route):
        if isinstance(key, int):
            cur = cur[key] if isinstance(cur, list) and 0 <= key < len(cur) else None
        else:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if cur is None:
            return {}
    return cur if isinstance(cur, dict) else {}


def is_best_effort_body(parent_step: dict[str, Any], nested_path: NestedPath) -> bool:
    """True for a body that runs best-effort, outside the recovery cascade and its checks (an if_present or
    try_dismiss body, or a branch option that is not a whole way through the workflow). A loop body, a fork option
    and a `flow` option run through the real step loop, so they take the same edits as a top-level step."""
    route = nested_path[0]
    if route == "branch":
        return True
    if route.startswith("branch.options"):
        return not bool((parent_step.get("branch") or {}).get("flow"))
    return False


def get_nested_step(parent_step: dict[str, Any], nested_path: NestedPath) -> dict[str, Any]:
    route, nested_index = nested_path
    raw_nested_steps = _holder(parent_step, route).get("steps")
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
    route, nested_index = nested_path
    holder = _holder(parent_step, route)
    nested_steps = list(holder.get("steps") or [])
    if nested_index < 0 or nested_index >= len(nested_steps):
        raise StepPathError("step_not_found", f"Nested step {nested_index} out of range")
    nested_steps[nested_index] = nested_step

    def rebuild(node: Any, keys: list[str | int]) -> Any:
        if not keys:
            out = dict(node)
            out["steps"] = nested_steps
            return out
        head, rest = keys[0], keys[1:]
        if isinstance(head, int):
            copy_list = list(node)
            copy_list[head] = rebuild(copy_list[head], rest)
            return copy_list
        copy_dict = dict(node)
        copy_dict[head] = rebuild(copy_dict[head], rest)
        return copy_dict

    return rebuild(parent_step, _route_keys(route))


_BODY_ADDRESS_SEP = "::"


def body_address(parent_key: str, path: str) -> str:
    """One string naming a body step: its parent's step key and its path, e.g. `rec12::outcome_fork.states[0].options[1].steps[0]`.
    A step key never holds `::`, so a caller that only knows top-level keys can tell the two apart."""
    return f"{parent_key}{_BODY_ADDRESS_SEP}{path}"


def split_body_address(address: str) -> tuple[str, str | None]:
    """(parent step key, path) for a body address; (key, None) for an ordinary step key."""
    key, sep, path = str(address).partition(_BODY_ADDRESS_SEP)
    return (key, path) if sep else (key, None)


def steps_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    skills = doc.get("skills") if isinstance(doc, dict) else None
    block0 = skills[0] if isinstance(skills, list) and skills and isinstance(skills[0], dict) else {}
    steps = block0.get("steps")
    return steps if isinstance(steps, list) else []


def iter_steps(doc: dict[str, Any]) -> Iterator[tuple[int, str | None, dict[str, Any]]]:
    """Every step the editor can address: `(top-level index, path or None, step)`. A top-level step comes with
    path None, each of its body steps right after it with the path `cmd_patch_step` takes. One level deep, as the
    path grammar is; use `walk_steps` for a walk that must reach every step."""
    for i, step in enumerate(steps_of(doc)):
        if not isinstance(step, dict):
            continue
        yield i, None, step
        for suffix, body in child_step_lists(step):
            for j, child in enumerate(body):
                if isinstance(child, dict):
                    yield i, f"{suffix}[{j}]", child


def walk_steps(steps: list[Any]) -> Iterator[dict[str, Any]]:
    """Every step in `steps` and in every body below them, at any depth, each yielded once (the dict itself, so a
    caller may edit it in place). The one walk for anything that must not skip a body (a literal rewrite, a check)."""
    for step in steps:
        if not isinstance(step, dict):
            continue
        yield step
        for _suffix, body in child_step_lists(step):
            yield from walk_steps(body)
