"""App-map contribution storage (coordination only: the cloud stores and serves, it never folds).

PUT    /api/v1/app-graphs/{app_key}/contributions/{contribution_id}   one workflow's part of one app's map
DELETE /api/v1/app-graphs/{app_key}/contributions/{contribution_id}
GET    /api/v1/app-graphs/{app_key}/contributions                     every contribution of that app
GET    /api/v1/app-graphs                                             the apps this workspace has maps for

Build Studio builds the contributions (``conxa_compile.app_graph``) and folds them locally; this just
lets teammates share them. A contribution holds screens, actions and popups only -- never values
(``conxa_compile.app_graph.scrub`` is its single entry point), and the server re-validates the shape
and stamps the caller's own workspace on it, whatever the body says.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import current_principal, entitlement_http_error
from app.services.entitlements import ensure_ops_tier
from app.services.saas import Principal
from app.services.tracking import _visible_run_records
from conxa_core.db import db_delete, db_get, db_list, db_set
from conxa_core.models.app_graph import Contribution

router = APIRouter(prefix="/app-graphs", tags=["app-graphs"])

MAX_CONTRIBUTION_BYTES = 64 * 1024
MAX_CONTRIBUTIONS_PER_APP = 500
_APP_KEY = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_CONTRIB_ID = re.compile(r"^skill:[A-Za-z0-9._-]{1,128}$")


def _namespace(workspace_id: str) -> str:
    return f"app_graph__{workspace_id}"


def _check(app_key: str, contribution_id: str | None = None) -> None:
    if not _APP_KEY.match(app_key):
        raise HTTPException(status_code=422, detail="invalid_app_key")
    if contribution_id is not None and not _CONTRIB_ID.match(contribution_id):
        raise HTTPException(status_code=422, detail="invalid_contribution_id")


def _app_rows(workspace_id: str, app_key: str) -> list[dict[str, Any]]:
    # Filter on the stored document, not the KV key: the filesystem fallback hashes keys.
    return [v for v in db_list(_namespace(workspace_id)) if isinstance(v, dict) and v.get("app_key") == app_key]


@router.put("/{app_key}/contributions/{contribution_id}")
def put_contribution(
    app_key: str,
    contribution_id: str,
    payload: dict[str, Any],
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _check(app_key, contribution_id)
    try:
        contribution = Contribution.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_contribution") from exc
    if contribution.app_key != app_key or contribution.id != contribution_id:
        raise HTTPException(status_code=422, detail="contribution_mismatch")
    contribution.workspace_id = principal.workspace_id
    data = contribution.model_dump(mode="json")
    if len(json.dumps(data).encode("utf-8")) > MAX_CONTRIBUTION_BYTES:
        raise HTTPException(status_code=413, detail="contribution_too_large")
    key = f"{app_key}/{contribution_id}"
    if db_get(_namespace(principal.workspace_id), key) is None and len(_app_rows(principal.workspace_id, app_key)) >= MAX_CONTRIBUTIONS_PER_APP:
        raise HTTPException(status_code=409, detail="too_many_contributions")
    db_set(_namespace(principal.workspace_id), key, data)
    return {"ok": True}


@router.delete("/{app_key}/contributions/{contribution_id}")
def delete_contribution(
    app_key: str,
    contribution_id: str,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _check(app_key, contribution_id)
    db_delete(_namespace(principal.workspace_id), f"{app_key}/{contribution_id}")
    return {"ok": True}


@router.get("/{app_key}/contributions")
def list_contributions(
    app_key: str,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _check(app_key)
    rows = sorted(_app_rows(principal.workspace_id, app_key), key=lambda v: str(v.get("id")))
    return {"app_key": app_key, "contributions": rows}


def _short_strings(value: Any, limit: int, width: int = 40) -> list[str]:
    """An untrusted list of short strings, bounded: telemetry comes from a runtime, and a token
    holder can send anything."""
    if not isinstance(value, list):
        return []
    return [str(v)[:width] for v in value[:limit] if isinstance(v, str) and v.strip()]


@router.get("/{app_key}/field")
def field_observations(
    app_key: str,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Decision popups real runs met on this app that no recorded fork placed (``app_obs``), counted
    per popup shape: distinct runs, whether the app map already knew it, and when it was last seen.
    Counts are computed on read from this workspace's own telemetry (same visibility and retention
    as the drift queue, hence the same ops-tier gate); nothing is written back automatically --
    the author decides what to do with them in Build Studio."""
    _check(app_key)
    try:
        ensure_ops_tier(principal, "full")
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc
    shapes: dict[tuple[tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}
    for record in _visible_run_records(principal):
        run_id = str((record.get("summary") or {}).get("run_id") or id(record))
        for evt in record.get("events") or []:
            if not isinstance(evt, dict) or evt.get("e") != "app_obs" or str(evt.get("host") or "").lower() != app_key:
                continue
            tokens = _short_strings(evt.get("tt"), 8)
            buttons = _short_strings(evt.get("bt"), 6)
            if len(buttons) < 2:
                continue
            key = (tuple(tokens), tuple(sorted(b.lower() for b in buttons)))
            row = shapes.setdefault(key, {
                "title_tokens": tokens, "buttons": buttons, "class": "decision",
                "runs": set(), "known": False, "last_seen": 0,
            })
            row["runs"].add(run_id)
            row["known"] = row["known"] or evt.get("k") == 1
            ts = evt.get("ts")
            if isinstance(ts, (int, float)):
                row["last_seen"] = max(row["last_seen"], int(ts))
    popups = sorted(
        ({**{k: v for k, v in row.items() if k != "runs"}, "count": len(row["runs"])} for row in shapes.values()),
        key=lambda p: (-p["count"], p["title_tokens"]),
    )
    return {"app_key": app_key, "popups": popups}


@router.get("")
def list_apps(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for doc in db_list(_namespace(principal.workspace_id)):
        app_key = str(doc.get("app_key") or "") if isinstance(doc, dict) else ""
        if app_key:
            counts[app_key] = counts.get(app_key, 0) + 1
    return {"apps": [{"app_key": k, "contributions": n} for k, n in sorted(counts.items())]}
