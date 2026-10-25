"""Which test input to blame when a step could not find its element. Pure, no LLM, no selectors.

A step that looks something up by an input ("click the file named {{gitignore_file}}") fails the
same way whether the workflow is wrong or the typed value is. The recorded value worked when the
workflow was recorded, so an input the person changed — or one that breaks its own definition — is
the first suspect; the workflow is edited only once the person says the value is right.

``runtime_evidence`` is the runtime's ``evidence.json`` (``failure_response.js``): ``failure_kind``
and ``input_refs`` (``[{id, empty}]`` — the runtime never reports values). Evidence from an older
runtime lacks them, which means "unknown": no suspects, so behaviour is exactly as before.
"""

from __future__ import annotations

import re
from typing import Any

# A step that failed to FIND its element is where a wrong value shows up. Other failures (a
# verification, a frame, a timeout) say nothing about what was typed.
FIND_FAILURES = frozenset({"ambiguous", "not_found", "entity_not_found"})

_REDACTED = "{{REDACTED}}"


def _problem(defn: dict[str, Any], value: str, *, compare_recorded: bool) -> str | None:
    """Why ``value`` is doubtful for this input, or None. Reason codes, never prose with the value."""
    if defn.get("sensitive"):
        return "empty" if not value.strip() else None
    if not value.strip():
        return "empty"
    options = [str(o) for o in defn.get("options") or []]
    if options and defn.get("type") in ("select", "multiselect") and value not in options:
        return "not_an_option"
    pattern = defn.get("pattern")
    if pattern:
        try:
            if not re.search(str(pattern), value):
                return "pattern_mismatch"
        except re.error:
            pass  # a pattern that does not compile says nothing about the value
    recorded = str(defn.get("default") or "")
    if compare_recorded and recorded.strip() and recorded != _REDACTED and value != recorded:
        return "differs_from_recorded"
    return None


def find_input_suspects(
    runtime_evidence: dict[str, Any] | None, test_inputs: dict[str, Any], input_defs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inputs the failing step read whose tested value is doubtful: ``[{id, reason}]``."""
    evidence = runtime_evidence or {}
    if evidence.get("failure_kind") not in FIND_FAILURES:
        return []
    defs = {str(d["id"]): d for d in input_defs or [] if isinstance(d, dict) and d.get("id")}
    out: list[dict[str, Any]] = []
    for ref in evidence.get("input_refs") or []:
        defn = defs.get(str(ref.get("id") or "")) if isinstance(ref, dict) else None
        if defn is None:
            continue
        value = test_inputs.get(defn["id"])
        reason = _problem(defn, "" if value is None else str(value), compare_recorded=True)
        if reason:
            out.append({"id": defn["id"], "reason": reason})
    return out


def preflight_problems(input_defs: list[dict[str, Any]], values: dict[str, Any]) -> list[dict[str, Any]]:
    """Before a run: inputs that break their own definition (not an option, fails its pattern).
    A different-from-recorded value is the whole point of testing, so it is not a problem here."""
    out: list[dict[str, Any]] = []
    for defn in input_defs or []:
        if not isinstance(defn, dict) or not defn.get("id") or str(defn["id"]) not in values:
            continue
        value = values[defn["id"]]
        reason = _problem(defn, "" if value is None else str(value), compare_recorded=False)
        if reason and reason != "empty":  # an empty required input is already refused by the run itself
            out.append({"id": defn["id"], "reason": reason})
    return out
