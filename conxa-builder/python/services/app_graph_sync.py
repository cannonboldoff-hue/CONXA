"""Share app-map contributions with teammates through Conxa Cloud.

Build Studio builds the contributions and folds them locally; the cloud only stores them
(``conxa-cloud/backend/app/api/app_graph_routes.py``). One ``sync`` pushes what changed here, removes
what was deleted here, then pulls what teammates have. It never raises on a single bad contribution
-- the map is derived data and a failed sync must never get in the way of publishing or editing.

``_sync.json`` remembers, per contribution, the content hash last exchanged and where it came from:
"pushed" (built on this machine) or "pulled" (a teammate's). A pulled one that disappears from the
cloud is removed here; a pushed one is never removed because someone else deleted their copy.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable
from urllib.parse import quote

from conxa_core.models.app_graph import Contribution
from conxa_core.storage import app_graph_store

CloudJson = Callable[..., dict[str, Any]]


def _hash(c: Contribution) -> str:
    data = c.model_dump(mode="json", exclude={"created_at", "workspace_id"})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _path(app_key: str, contribution_id: str) -> str:
    return f"/api/v1/app-graphs/{quote(app_key, safe='')}/contributions/{quote(contribution_id, safe=':')}"


def _load(workspace_id: str) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(app_graph_store.sync_state_path(workspace_id).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(workspace_id: str, state: dict[str, dict[str, str]]) -> None:
    p = app_graph_store.sync_state_path(workspace_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def pull_field(workspace_id: str, cloud_json: CloudJson) -> dict[str, Any]:
    """Fetch what real runs saw (the cloud counts the runtime's scrubbed ``app_obs`` telemetry) for
    every app this machine has a map for, and keep it in the maps. Best effort per app: a workspace
    below the plan that includes telemetry analytics just gets nothing."""
    from conxa_compile.app_graph.field import ingest_field  # noqa: PLC0415

    kept = 0
    errors: list[str] = []
    for row in app_graph_store.list_apps(workspace_id):
        app_key = row["app_key"]
        try:
            rows = cloud_json(f"/api/v1/app-graphs/{quote(app_key, safe='')}/field").get("popups") or []
        except Exception as exc:  # noqa: BLE001
            errors.append(f"field {app_key}: {exc}")
            continue
        kept += ingest_field(workspace_id, app_key, rows)
    return {"field_popups": kept, "errors": errors}


def sync(workspace_id: str, cloud_json: CloudJson) -> dict[str, Any]:
    state = _load(workspace_id)
    local = {f"{c.app_key}/{c.id}": c for c in app_graph_store.iter_contributions(workspace_id)}
    pushed = deleted = pulled = removed = 0
    errors: list[str] = []
    failed_deletes: set[str] = set()

    for key, c in local.items():
        h = _hash(c)
        if state.get(key, {}).get("hash") == h:
            continue
        try:
            cloud_json(_path(c.app_key, c.id), method="PUT", body=c.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"push {key}: {exc}")
            continue
        state[key] = {"hash": h, "origin": state.get(key, {}).get("origin", "pushed")}
        pushed += 1

    for key in [k for k in state if k not in local]:
        app_key, cid = key.split("/", 1)
        if state[key].get("origin") == "pulled":
            state.pop(key)          # a teammate's copy this machine no longer holds: just forget it
            continue
        try:
            cloud_json(_path(app_key, cid), method="DELETE")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"delete {key}: {exc}")
            failed_deletes.add(key)
            continue
        state.pop(key)
        deleted += 1

    list_ok = True
    try:
        apps = [a.get("app_key") for a in cloud_json("/api/v1/app-graphs").get("apps") or []]
    except Exception as exc:  # noqa: BLE001
        apps, list_ok = [], False
        errors.append(f"list: {exc}")
    listed: set[str] = set()
    fetched: set[str] = set()   # apps whose contributions were read successfully this time
    for app_key in apps:
        try:
            remote = cloud_json(f"/api/v1/app-graphs/{quote(str(app_key), safe='')}/contributions").get("contributions") or []
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pull {app_key}: {exc}")
            continue
        fetched.add(str(app_key))
        for doc in remote:
            try:
                c = Contribution.model_validate(doc)
            except ValueError:
                continue
            key = f"{c.app_key}/{c.id}"
            listed.add(key)
            if key in local or key in failed_deletes:
                continue
            if app_graph_store.store_contribution(workspace_id, c):
                state[key] = {"hash": _hash(c), "origin": "pulled"}
                pulled += 1

    for key in [k for k, v in state.items() if v.get("origin") == "pulled" and k not in listed
                and (k.split("/", 1)[0] in fetched or (list_ok and k.split("/", 1)[0] not in apps))]:
        app_key, cid = key.split("/", 1)
        app_graph_store.delete_contribution(workspace_id, app_key, cid)
        state.pop(key)
        removed += 1

    _save(workspace_id, state)
    return {"pushed": pushed, "deleted": deleted, "pulled": pulled, "removed": removed, "errors": errors}
