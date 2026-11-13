"""The Human Review Copilot's `need[]` retrieval loop (BUILD-26 stage g, L2 in the plan).

The cloud proxy has no tool-calling primitive and the provider pool (Groq / Google AI Studio /
NVIDIA NIM) is uneven on the ones that do — so retrieval rides the same JSON contract
`copilot_diagnose` already returns. The model names what it needs in a `need[]` array; this
module resolves each entry against a CLOSED set of existing readers (never an open eval, never a
new selector-reading path) and returns compact results for `llm/copilot.py` to fold into the next
round's `user_text`.

Every reader here wraps something that already has a docstring elsewhere — this file adds no new
data access, only a uniform `{tool, args} -> result` dispatch over five that already exist.
"""

from __future__ import annotations

from typing import Any

# The fetch loop itself lives in llm/copilot.py (it owns the LLM round-trip); this is just the
# ceiling both that loop and its own docstring reference, kept in one place.
MAX_RETRIEVAL_ROUNDS = 2


def resolve_need(skill_id: str, need: dict[str, Any]) -> dict[str, Any]:
    """Resolve one `{tool, args}` entry. Never raises — an unresolvable need degrades to an
    explicit `{"error": ...}` result the model can see and route around, the same
    graceful-degrade contract every other copilot-adjacent reader in this pipeline follows."""
    tool = str(need.get("tool") or "").strip()
    raw_args = need.get("args")
    args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
    try:
        if tool == "expand_step":
            return _expand_step(skill_id, args)
        if tool == "get_step_screenshot":
            return _get_step_screenshot(skill_id, args)
        if tool == "get_failure_evidence":
            return _get_failure_evidence(skill_id, args)
        if tool == "get_edit_history":
            return _get_edit_history(skill_id, args)
        if tool == "list_workflows":
            return _list_workflows()
        return {"error": f"unknown_tool:{tool}"}
    except Exception as exc:  # noqa: BLE001 — a retrieval failure must not abort the turn
        return {"error": str(exc)}


def _expand_step(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from conxa_compile.editor.evidence import expand_step

    step_key = str(args.get("step_key") or "").strip()
    if not step_key:
        return {"error": "step_key_required"}
    return expand_step(skill_id, step_key)


def _get_step_screenshot(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Resolve a step's recorded screenshot to a filesystem path `copilot_turn` can read and
    downscale exactly like the failure screenshot — never a URL, since this is consumed
    server-side, not by a browser."""
    from conxa_compile.editor.assets import resolve_skill_asset
    from conxa_compile.editor.source_event import find_source_event
    from conxa_compile.compiler.build import _persisted_visual_asset_path
    from conxa_core.storage.json_store import read_skill

    step_key = str(args.get("step_key") or "").strip()
    if not step_key:
        return {"error": "step_key_required"}
    doc = read_skill(skill_id)
    if doc is None:
        return {"error": "skill_not_found"}
    skills = doc.get("skills") if isinstance(doc.get("skills"), list) else []
    block0 = skills[0] if skills and isinstance(skills[0], dict) else {}
    raw_steps = block0.get("steps")
    steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []
    from conxa_compile.compiler.step_key import step_keys

    keys = step_keys(steps)
    step = next((s for s, k in zip(steps, keys) if k == step_key), None)
    if step is None:
        return {"error": "step_not_found"}
    event = find_source_event(step, doc)
    if not event:
        return {"screenshot_path": None, "reason": "no_recorded_event"}
    rel = (event.get("visual") or {}).get("full_screenshot")
    session_id = (doc.get("meta") or {}).get("source_session_id")
    persisted = _persisted_visual_asset_path(event, rel, session_id_fallback=str(session_id or ""))
    if not persisted:
        return {"screenshot_path": None, "reason": "no_screenshot_recorded"}
    try:
        path = resolve_skill_asset(persisted)
    except ValueError:
        return {"screenshot_path": None, "reason": "asset_path_invalid"}
    if not path.is_file():
        return {"screenshot_path": None, "reason": "asset_missing_on_disk"}
    return {"screenshot_path": str(path)}


def _get_failure_evidence(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from conxa_compile.editor.evidence import _read_runtime_evidence, find_workflow_for_skill

    run_id = args.get("run_id")
    run_id = str(run_id).strip() if run_id else None
    if not run_id:
        workflow = find_workflow_for_skill(skill_id)
        run_id = workflow.last_test_run_id if workflow else None
    return _read_runtime_evidence(run_id)


def _get_edit_history(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    from conxa_compile.editor.edit_log import read_edits

    step_key = str(args.get("step_key") or "").strip()
    edits = read_edits(skill_id)
    if step_key:
        edits = [e for e in edits if e.get("step_key") == step_key]
    return {"edits": edits[-30:]}


def _list_workflows() -> dict[str, Any]:
    from conxa_core.storage.workflow_store import list_workflows

    return {
        "workflows": [
            {"id": wf.id, "name": wf.name, "skill_id": wf.skill_id, "signed_off": wf.signed_off}
            for wf in list_workflows()
        ]
    }
