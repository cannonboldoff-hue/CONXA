"""Build-time checks and version pin for ``call_workflow`` steps.

A workflow that calls another one is built against a specific version of it. Here, while a skill's files
are being written, every call is checked and stamped with the version of the workflow it calls:

  * the called workflow must exist (built in this call, or by an earlier one);
  * no workflow may call itself, directly or through others, and the chain stays within MAX_CALL_DEPTH;
  * a called workflow may not hold a pause (ai_review, handover, a popup decision) -- a pause inside a
    call would resume at the wrong step;
  * the inputs handed over must be ones the called workflow takes, every one it requires must be given
    (or have a default), and every result asked for must be one it produces.

The runtime repeats these checks at run start (runtime/app/call_workflow.js) and refuses to run when the
installed version of a called workflow differs from the pinned one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_CALL_DEPTH = 3
_PAUSE_STEP_TYPES = {"ai_review", "handover", "outcome_fork"}
_PAUSE_NAMES = {"ai_review": "an AI review", "handover": "a hand-over to a person", "outcome_fork": "a popup decision"}


def _walk(steps: Any, visit: Any) -> None:
    for s in steps if isinstance(steps, list) else []:
        if not isinstance(s, dict):
            continue
        visit(s)
        _walk(s.get("steps"), visit)
        if isinstance(s.get("for_each"), dict):
            _walk(s["for_each"].get("steps"), visit)
        if isinstance(s.get("branch"), dict):
            _walk(s["branch"].get("steps"), visit)
            for o in s["branch"].get("options") or []:
                _walk(o.get("steps") if isinstance(o, dict) else None, visit)
        for o in s.get("options") if isinstance(s.get("options"), list) else []:
            _walk(o.get("steps") if isinstance(o, dict) else None, visit)


def call_steps(steps: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _walk(steps, lambda s: out.append(s) if s.get("type") == "call_workflow" else None)
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def pin_called_workflows(
    *,
    dest_dir: Path,
    slug: str,
    version: str,
    building: dict[str, Path],
    skill_packs: Path,
) -> None:
    """Check and pin the ``call_workflow`` steps in ``dest_dir/execution.json`` (rewritten in place).

    ``building`` maps each skill slug being written by THIS build to its source directory (which
    already holds execution.json / input.json). A called workflow found only in ``skill_packs`` was built
    earlier; its version is read from its manifest.
    """
    from conxa_compile.skill_package_builder_output import _compute_inputs_required

    exec_path = dest_dir / "execution.json"
    steps = _read_json(exec_path)
    if not isinstance(steps, list) or not call_steps(steps):
        return

    def locate(callee: str) -> tuple[str, list[Any], dict[str, Any]] | None:
        """(version, execution steps, parsed inputs.json) of a built workflow, or None."""
        if callee in building:
            src = building[callee]
            return version, _read_json(src / "execution.json") or [], _read_json(src / "input.json") or {}
        for manifest_path in sorted(skill_packs.glob(f"*/{callee}/manifest.json")):
            manifest = _read_json(manifest_path) or {}
            return (
                str(manifest.get("version") or ""),
                _read_json(manifest_path.parent / "execution.json") or [],
                _read_json(manifest_path.parent / "inputs.json") or {},
            )
        return None

    def outputs_of(callee_steps: list[Any]) -> set[str]:
        names: set[str] = set()
        _walk(callee_steps, lambda s: names.add(str(s["output_name"])) if s.get("type") == "extract" and s.get("output_name") else None)
        return names

    def check(owner: str, owner_steps: list[Any], stack: list[str], pins: dict[str, str]) -> None:
        for step in call_steps(owner_steps):
            call = step.get("call") if isinstance(step.get("call"), dict) else {}
            callee = str(call.get("skill") or "").strip()
            if not callee:
                raise RuntimeError(f"Workflow {owner!r} has a 'call another workflow' step that does not say which workflow to call.")
            if callee in stack:
                raise RuntimeError(f"Workflows call each other in a circle ({' -> '.join([*stack, callee])}).")
            if len(stack) > MAX_CALL_DEPTH:
                raise RuntimeError(f"Workflows call each other more than {MAX_CALL_DEPTH} levels deep ({' -> '.join([*stack, callee])}).")
            found = locate(callee)
            if found is None:
                raise RuntimeError(f"Workflow {owner!r} calls {callee!r}, which has not been built yet. Build {callee!r} first.")
            callee_version, callee_steps, callee_inputs = found
            pause = next((t for t in _PAUSE_STEP_TYPES if _has_type(callee_steps, t)), None)
            if pause:
                raise RuntimeError(f"Workflow {callee!r} contains {_PAUSE_NAMES[pause]}, which cannot run inside another workflow.")
            declared = {i.get("name"): i for i in callee_inputs.get("inputs", []) if isinstance(i, dict) and i.get("name")}
            given = call.get("inputs") if isinstance(call.get("inputs"), dict) else {}
            unknown = [n for n in given if n not in declared]
            if unknown:
                raise RuntimeError(f"Workflow {owner!r} hands {callee!r} input(s) it does not take: {', '.join(unknown)}.")
            lacking = [
                n for n in _compute_inputs_required(callee_inputs)
                if n not in given and declared.get(n, {}).get("default") in (None, "")
            ]
            if lacking:
                raise RuntimeError(f"Workflow {owner!r} does not give {callee!r} the input(s) it needs: {', '.join(lacking)}.")
            asked = call.get("outputs") if isinstance(call.get("outputs"), dict) else {}
            produced = outputs_of(callee_steps)
            missing = [n for n in asked if n not in produced]
            if missing:
                raise RuntimeError(f"Workflow {owner!r} asks {callee!r} for result(s) it does not produce: {', '.join(missing)}.")
            call["version"] = callee_version
            step["call"] = call
            check(callee, callee_steps, [*stack, callee], pins)

    check(slug, steps, [slug], {})
    exec_path.write_text(json.dumps(steps, indent=2, ensure_ascii=False), encoding="utf-8")


def _has_type(steps: Any, wanted: str) -> bool:
    found = False

    def visit(s: dict[str, Any]) -> None:
        nonlocal found
        if s.get("type") == wanted:
            found = True

    _walk(steps, visit)
    return found
