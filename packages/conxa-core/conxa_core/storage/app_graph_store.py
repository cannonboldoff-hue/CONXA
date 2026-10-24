"""Filesystem persistence for app maps.

Layout (Studio only; the cloud stores raw contributions and never folds, see P2 of the plan):
  data/app_graphs/<workspace>/<app_key>/contrib/<skill_id>.json   one per workflow, per host
  data/app_graphs/<workspace>/<app_key>/graph.json                the fold of the above

A graph is derived data: it is always re-folded from the contribution files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import warnings
from pathlib import Path

from conxa_core.config import settings
from conxa_core.models.app_graph import AppGraph, Contribution, Popup, fold_contributions, graph_summary
from conxa_core.workspace import LOCAL_WORKSPACE_ID, workspace_dir_slug

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name: str) -> str:
    out = _SAFE.sub("_", str(name or "")).strip("._")
    return out or "_"


def _root() -> Path:
    return settings.data_dir / "app_graphs"


def _app_dir(workspace_id: str, app_key: str) -> Path:
    return _root() / workspace_dir_slug(workspace_id or LOCAL_WORKSPACE_ID) / _safe(app_key)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_contributions(app_dir: Path) -> list[Contribution]:
    out: list[Contribution] = []
    for f in sorted((app_dir / "contrib").glob("*.json")):
        try:
            out.append(Contribution.model_validate_json(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            warnings.warn(f"Dropping unreadable app-map contribution {f}: {exc}", stacklevel=2)
    return out


def _skill_locations(skill_id: str) -> list[tuple[str, str]]:
    """Every (workspace_dir, app_dir_name) that currently holds a contribution of this skill."""
    found: list[tuple[str, str]] = []
    fname = f"{_safe(skill_id)}.json"
    root = _root()
    if not root.is_dir():
        return found
    for ws in root.iterdir():
        for app in (ws.iterdir() if ws.is_dir() else []):
            if (app / "contrib" / fname).is_file():
                found.append((ws.name, app.name))
    return found


def _drop_skill(skill_id: str) -> set[tuple[str, str]]:
    touched: set[tuple[str, str]] = set()
    for ws_name, app_name in _skill_locations(skill_id):
        (_root() / ws_name / app_name / "contrib" / f"{_safe(skill_id)}.json").unlink(missing_ok=True)
        touched.add((ws_name, app_name))
    return touched


def _read_policies(app_dir: Path) -> dict[str, dict]:
    try:
        raw = json.loads((app_dir / "policies.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_field(app_dir: Path) -> list[dict]:
    try:
        raw = json.loads((app_dir / "field.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (OSError, ValueError):
        return []


def _apply_field(graph: AppGraph, entries: list[dict]) -> None:
    """Real-run popup counts (conxa_compile.app_graph.field): added to the popup a workflow already
    covers, or kept as a field-only popup nobody has recorded yet."""
    by_id = {p.id: p for p in graph.popups}
    for entry in entries:
        try:
            popup = Popup.model_validate(entry.get("popup") or {})
        except ValueError:
            continue
        target = by_id.get(entry.get("match_popup_id") or "")
        if target is not None:
            target.field_seen += popup.field_seen
        elif popup.id not in by_id:
            graph.popups.append(popup)
            by_id[popup.id] = popup
    graph.popups.sort(key=lambda p: p.id)


def _rebuild_dir(ws_name: str, app_name: str) -> None:
    app_dir = _root() / ws_name / app_name
    contribs = _read_contributions(app_dir)
    if not contribs:
        shutil.rmtree(app_dir, ignore_errors=True)
        return
    first = contribs[0]
    graph = fold_contributions(first.app_key, first.workspace_id or LOCAL_WORKSPACE_ID, contribs)
    _apply_field(graph, _read_field(app_dir))
    policies = _read_policies(app_dir)
    for popup in graph.popups:
        popup.app_policy = policies.get(popup.id)
    _write_atomic(app_dir / "graph.json", graph.model_dump_json(indent=2))


def put_skill_contributions(skill_id: str, workspace_id: str, contribs: dict[str, Contribution]) -> None:
    """Replace everything this skill contributes: hosts it no longer touches lose it, hosts it
    still touches get the new version, and every affected graph is re-folded."""
    touched = _drop_skill(skill_id)
    for app_key, contrib in contribs.items():
        app_dir = _app_dir(workspace_id, app_key)
        _write_atomic(app_dir / "contrib" / f"{_safe(skill_id)}.json", contrib.model_dump_json(indent=2))
        touched.add((app_dir.parent.name, app_dir.name))
    for ws_name, app_name in sorted(touched):
        _rebuild_dir(ws_name, app_name)


def remove_skill(skill_id: str) -> None:
    for ws_name, app_name in sorted(_drop_skill(skill_id)):
        _rebuild_dir(ws_name, app_name)


def get_graph(workspace_id: str, app_key: str) -> AppGraph | None:
    path = _app_dir(workspace_id, app_key) / "graph.json"
    try:
        return AppGraph.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _summaries(ws_dir: Path) -> list[dict]:
    out: list[dict] = []
    for app in sorted(ws_dir.iterdir()) if ws_dir.is_dir() else []:
        try:
            out.append(graph_summary(json.loads((app / "graph.json").read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def list_apps(workspace_id: str) -> list[dict]:
    return _summaries(_root() / workspace_dir_slug(workspace_id or LOCAL_WORKSPACE_ID))


def list_all_apps() -> list[dict]:
    """Every app map on this machine, across workspaces (each row carries its workspace_id)."""
    root = _root()
    out: list[dict] = []
    for ws in sorted(root.iterdir()) if root.is_dir() else []:
        out.extend(_summaries(ws))
    return out


# ─── sharing with teammates (cloud sync, see conxa-builder/python/services/app_graph_sync.py) ─────

def iter_contributions(workspace_id: str) -> list[Contribution]:
    """Every contribution this machine holds for a workspace, across all hosts."""
    ws_dir = _root() / workspace_dir_slug(workspace_id or LOCAL_WORKSPACE_ID)
    out: list[Contribution] = []
    for app in sorted(ws_dir.iterdir()) if ws_dir.is_dir() else []:
        if app.is_dir():
            out.extend(_read_contributions(app))
    return out


def store_contribution(workspace_id: str, contribution: Contribution) -> bool:
    """Keep a contribution that came from a teammate. Never replaces one this machine built itself
    (a skill id is local to the machine that compiled it). Returns whether it was written."""
    skill_id = contribution.id.removeprefix("skill:")
    app_dir = _app_dir(workspace_id, contribution.app_key)
    path = app_dir / "contrib" / f"{_safe(skill_id)}.json"
    if path.is_file():
        return False
    _write_atomic(path, contribution.model_dump_json(indent=2))
    _rebuild_dir(app_dir.parent.name, app_dir.name)
    return True


def delete_contribution(workspace_id: str, app_key: str, contribution_id: str) -> None:
    app_dir = _app_dir(workspace_id, app_key)
    (app_dir / "contrib" / f"{_safe(contribution_id.removeprefix('skill:'))}.json").unlink(missing_ok=True)
    _rebuild_dir(app_dir.parent.name, app_dir.name)


def sync_state_path(workspace_id: str) -> Path:
    return _root() / workspace_dir_slug(workspace_id or LOCAL_WORKSPACE_ID) / "_sync.json"


# ─── the author's standing answers (never derived from a recording) ───────────────────────────────

def set_popup_policy(workspace_id: str, app_key: str, popup_id: str, policy: dict | None) -> None:
    """Keep (or, with None, clear) the author's policy for one popup of one app. Lives in its own
    file so it survives every re-fold; the only caller is cmd_set_app_popup_policy."""
    app_dir = _app_dir(workspace_id, app_key)
    policies = _read_policies(app_dir)
    if policy is None:
        policies.pop(popup_id, None)
    else:
        policies[popup_id] = policy
    _write_atomic(app_dir / "policies.json", json.dumps(policies, indent=2))
    _rebuild_dir(app_dir.parent.name, app_dir.name)


def set_field(workspace_id: str, app_key: str, entries: list[dict]) -> None:
    """Replace the real-run popup data kept for one app (see conxa_compile.app_graph.field)."""
    app_dir = _app_dir(workspace_id, app_key)
    if not app_dir.is_dir():
        return
    _write_atomic(app_dir / "field.json", json.dumps(entries, indent=2))
    _rebuild_dir(app_dir.parent.name, app_dir.name)
