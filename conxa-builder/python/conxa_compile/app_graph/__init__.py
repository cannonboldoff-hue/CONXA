"""App maps: a learned, data-free map of each web app, folded from every workflow recorded on it.

``install_hooks()`` makes every skill write (compile, editor mutation, undo) refresh the maps, and
every skill delete remove the skill's part. Both are best-effort: the map is derived data and must
never be the reason a compile or an edit fails (``conxa_core.storage.skill_hooks`` turns a raised
error into a warning).
"""

from __future__ import annotations

from typing import Any

from conxa_core.storage import app_graph_store
from conxa_core.storage.skill_hooks import on_skill_deleted, on_skill_written
from conxa_core.workspace import LOCAL_WORKSPACE_ID

from conxa_compile.app_graph.extract import contributions_from_doc


def _workflow_for_skill(skill_id: str) -> Any:
    from conxa_core.storage.workflow_store import list_workflows  # noqa: PLC0415

    return next((w for w in list_workflows() if w.skill_id == skill_id), None)


def refresh_skill(skill_id: str, doc: dict[str, Any] | None = None) -> None:
    """Recompute this skill's contributions from its saved document and re-fold the affected maps."""
    if doc is None:
        from conxa_core.storage.json_store import read_skill  # noqa: PLC0415

        doc = read_skill(skill_id)
    if not isinstance(doc, dict):
        return
    wf = _workflow_for_skill(skill_id)
    workspace_id = (wf.workspace_id if wf else "") or LOCAL_WORKSPACE_ID
    contribs = contributions_from_doc(doc, skill_id=skill_id, workspace_id=workspace_id, name=wf.name if wf else "")
    app_graph_store.put_skill_contributions(skill_id, workspace_id, contribs)


def install_hooks() -> None:
    on_skill_written(refresh_skill)
    on_skill_deleted(app_graph_store.remove_skill)


def rebuild_all() -> int:
    """Backfill: recompute the map from every workflow that has a compiled skill. Returns how many."""
    from conxa_core.storage.workflow_store import list_workflows  # noqa: PLC0415

    n = 0
    for wf in list_workflows():
        if wf.skill_id:
            refresh_skill(wf.skill_id)
            n += 1
    return n
