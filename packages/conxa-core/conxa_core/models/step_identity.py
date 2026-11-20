"""A step's own identity, kept apart from its element's identity (BUILD-104 (1), plan W-A).

`step_key` used to be derived on the fly from the element's hash plus a counter, so anything that
changed the element's hash (or inserted a step with the same element) moved every key that edit logs,
questions, test results and suggestions had stored. A step now carries its own `step_id`, written once
and never recomputed:

* A compile gives each step `rec<seq>` (the recorder's event number), or a fresh key for a step no event
  produced (`assign_compile_ids`).
* A saved skill from before this field has no ids; the first write freezes each step's old derived key as
  its id (`ensure_step_ids`), so every key already stored anywhere keeps resolving byte for byte.
* A step with no id still gets the old derived key when asked (`step_key`), so a half-built list keys the
  way it always did.

Lives in conxa_core (not the compiler) because `write_skill` freezes ids on every save.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Iterable, Iterator


def _field(step: Any, *path: str) -> Any:
    """Read a (possibly nested) field off a dict-shaped step or a pydantic SkillStep."""
    cur = step
    for name in path:
        if cur is None:
            return None
        cur = cur.get(name) if isinstance(cur, dict) else getattr(cur, name, None)
    return cur


def _fallback_hash(step: Any) -> str:
    action_name = _field(step, "action")
    if isinstance(action_name, dict):
        action_name = action_name.get("action")
    raw = f"{action_name or ''}|{_field(step, 'url') or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def step_id_of(step: Any) -> str:
    return str(_field(step, "step_id") or "").strip()


def legacy_base(step: Any) -> str:
    """The element hash an old-style key was built on (a step with no element falls back to action + url)."""
    return str(_field(step, "identity_bundle", "stable_hash") or "").strip() or _fallback_hash(step)


def step_key(step: Any, seen: Counter[str], taken: Iterable[str] = ()) -> str:
    """This step's key: its own id, else the old derived `<hash>#<n>` (skipping any key an id already holds).
    Pass the same Counter across a full step list."""
    sid = step_id_of(step)
    if sid:
        return sid
    base = legacy_base(step)
    while True:
        seen[base] += 1
        key = f"{base}#{seen[base]}"
        if key not in taken:
            return key


def step_keys(steps: list[Any]) -> list[str]:
    """step_key() for a full step list in order, sharing one counter."""
    taken = {sid for s in steps if (sid := step_id_of(s))}
    seen: Counter[str] = Counter()
    return [step_key(s, seen, taken) for s in steps]


def child_step_lists(step: Any) -> Iterator[tuple[str, list[Any]]]:
    """The nested step lists a step holds, each with a path suffix: branch steps, branch options, loop body,
    and the options of an outcome fork. One definition, so nothing walks bodies its own way."""
    branch = _field(step, "branch")
    if isinstance(branch, dict):
        if isinstance(branch.get("steps"), list):
            yield "branch.steps", branch["steps"]
        for j, opt in enumerate(branch.get("options") or []):
            if isinstance(opt, dict) and isinstance(opt.get("steps"), list):
                yield f"branch.options[{j}].steps", opt["steps"]
    loop = _field(step, "for_each")
    if isinstance(loop, dict) and isinstance(loop.get("steps"), list):
        yield "for_each.steps", loop["steps"]
    fork = _field(step, "outcome_fork")
    if isinstance(fork, dict):
        for s, state in enumerate(fork.get("states") or []):
            if not isinstance(state, dict):
                continue
            for o, opt in enumerate(state.get("options") or []):
                if isinstance(opt, dict) and isinstance(opt.get("steps"), list):
                    yield f"outcome_fork.states[{s}].options[{o}].steps", opt["steps"]


def _unique(candidate: str, used: set[str]) -> str:
    out, n = candidate, 1
    while out in used:
        n += 1
        out = f"{candidate}.{n}"
    used.add(out)
    return out


def assign_compile_ids(steps: list[Any]) -> None:
    """Give every top-level SkillStep of a fresh compile its own id: `rec<seq>` from the recorder's event
    number, else a key from its own action and address. Existing ids are kept. Deterministic for one
    recording, so a rebuild of it keys the same."""
    used = {sid for s in steps if (sid := step_id_of(s))}
    seen: Counter[str] = Counter()
    for s in steps:
        if step_id_of(s):
            continue
        seq = _field(s, "source_event")
        if seq is not None:
            new = _unique(f"rec{seq}", used)
        else:
            new = step_key(s, seen, used)
            used.add(new)
        if isinstance(s, dict):
            s["step_id"] = new
        else:
            s.step_id = new


def _freeze_bodies(step: dict[str, Any], parent: str, used: set[str]) -> bool:
    changed = False
    for suffix, body in child_step_lists(step):
        for i, child in enumerate(body):
            if not isinstance(child, dict):
                continue
            if not step_id_of(child):
                child["step_id"] = _unique(f"{parent}/{suffix}[{i}]", used)
                changed = True
            if _freeze_bodies(child, step_id_of(child), used):
                changed = True
    return changed


def ensure_step_ids(document: dict[str, Any]) -> bool:
    """Freeze an id on every step of a saved skill that lacks one (top level, then bodies). A step with no
    id gets its old derived key, so nothing stored under that key stops resolving. Returns whether it changed
    anything. Safe to run on every write."""
    skills = document.get("skills") if isinstance(document, dict) else None
    if not isinstance(skills, list):
        return False
    changed = False
    for block in skills:
        steps = block.get("steps") if isinstance(block, dict) else None
        if not isinstance(steps, list) or not steps:
            continue
        top = [s for s in steps if isinstance(s, dict)]
        keys = step_keys(top)
        used = set(keys)
        for s, k in zip(top, keys):
            if not step_id_of(s):
                s["step_id"] = k
                changed = True
        for s in top:
            if _freeze_bodies(s, step_id_of(s), used):
                changed = True
    return changed
