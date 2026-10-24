"""Filesystem JSON persistence for WorkflowGroup entities.

A WorkflowGroup is a business-domain folder ("Sales") that owns both a set of
workflows and the authenticated applications those workflows need. See
Workflow.group_id (models/workflow.py) and docs/TRD.md's Workflow Groups
section.

Layout:
  data/groups/{group_id}.json                      — one file per group
  data/groups/{group_id}/auth/{app_id}.json         — that app's storage state
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from conxa_core.config import settings
from conxa_core.db import db_get, db_set, db_delete, db_list
from conxa_core.models.workflow import GroupApp, WorkflowGroup
from conxa_core.workspace import LOCAL_WORKSPACE_ID

DEFAULT_GROUP_NAME = "Default"


def _groups_dir() -> Path:
    p = settings.data_dir / "groups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _group_path(group_id: str) -> Path:
    return _groups_dir() / f"{group_id}.json"


def group_auth_dir(group_id: str) -> Path:
    p = _groups_dir() / group_id / "auth"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _url_hostname(value: str) -> str:
    """Hostname of a value that may be a full URL ("https://x.com/y") or a bare
    hostname ("x.com", as stored in SkillMeta.visited_hosts). urlparse only
    recognizes a hostname when the value has a scheme, so a bare host falls
    through to the plain-hostname heuristic below: no scheme, no path/query,
    no whitespace.
    """
    try:
        parsed = (urlparse(value).hostname or "").lower()
    except ValueError:
        parsed = ""
    if parsed:
        return parsed
    candidate = value.strip().lower()
    if not candidate or "://" in candidate or "/" in candidate or " " in candidate:
        return ""
    return candidate


def apps_for_workflow(apps: list[GroupApp], *urls: str) -> list[GroupApp]:
    """Narrow a group's apps down to the ones a specific workflow actually
    touches, matched by SITE (not exact hostname) against each app's
    login_url/success_url — a workflow visiting drive.google.com matches an
    app whose login_url is accounts.google.com, since that's the same
    sign-in as far as a customer configuring the group is concerned. Same
    site rule unclaimed_hosts below already used, so the two agree on what
    counts as "covered" instead of one gating on an app the other calls
    unclaimed for the identical host.

    `urls` accepts both full URLs (target_url/protected_url) and bare
    hostnames (SkillMeta.visited_hosts) — _url_hostname normalizes either.

    Shared by the recording gate (handlers/session.py) and the group page's
    platform chips (handlers/groups.py) so "which apps does this workflow
    touch" can't disagree between them. It no longer decides runtime gating:
    the runtime signs in to EVERY app in a workflow's group.
    """
    wf_sites = {_site(h) for h in (_url_hostname(u) for u in urls) if h}
    if not wf_sites:
        return []
    return [
        a for a in apps
        if _site(_url_hostname(a.login_url)) in wf_sites or _site(_url_hostname(a.success_url)) in wf_sites
    ]


def _site(host: str) -> str:
    """Registrable-ish domain of a hostname: its last two labels ("mail.google.com" -> "google.com").
    An IP address or single-label host is its own site.

    ponytail: last-two-labels, so a multi-part suffix ("a.co.uk" vs "b.co.uk") reads as the same
    site — a missed warning, never a false one. Swap in the public-suffix list if that ever bites.
    """
    labels = host.split(".")
    if len(labels) < 2 or all(part.isdigit() for part in labels):
        return host
    return ".".join(labels[-2:])


def unclaimed_hosts(apps: list[GroupApp], *urls: str) -> list[str]:
    """Hosts a workflow visited that none of the group's apps covers — the ones it will hit a
    login wall on with no pre-flight sign-in. `urls` takes the same mix of full URLs and bare
    hostnames as apps_for_workflow, and this is computed from the same inputs at build time, so
    Build Studio and the runtime warn about the identical list.

    A host on the same site as any app (sub/parent domain, e.g. accounts.google.com next to a
    mail.google.com app) is treated as covered: it is that app's own sign-in hop, not a new one.
    """
    claimed = {
        _site(h) for a in apps for h in (_url_hostname(a.login_url), _url_hostname(a.success_url)) if h
    }
    visited = {_url_hostname(u) for u in urls} - {""}
    return sorted(h for h in visited if _site(h) not in claimed)


def _read_raw(group_id: str) -> dict[str, Any] | None:
    data = db_get("groups", group_id)
    if data is not None:
        return data
    path = _group_path(group_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.warn(f"Corrupt group file {path}: {exc}", stacklevel=2)
        return None


def _write_raw(group: WorkflowGroup) -> None:
    d = group.model_dump(mode="json")
    db_set("groups", group.id, d)
    try:
        _group_path(group.id).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def create_group(name: str, workspace_id: str = "") -> WorkflowGroup:
    slug_base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "group"
    group_id = str(uuid.uuid4())
    now = time.time()
    group = WorkflowGroup(
        id=group_id,
        slug=f"{slug_base}-{group_id[:8]}",
        name=name,
        workspace_id=workspace_id or LOCAL_WORKSPACE_ID,
        created_at=now,
        updated_at=now,
    )
    _write_raw(group)
    return group


def get_group(group_id: str, workspace_id: str = "") -> WorkflowGroup | None:
    raw = _read_raw(group_id)
    if raw is None:
        return None
    try:
        group = WorkflowGroup.model_validate(raw)
        if workspace_id and group.workspace_id != workspace_id:
            return None
        return group
    except Exception:
        return None


def list_groups(workspace_id: str = "") -> list[WorkflowGroup]:
    db_items = db_list("groups")
    if db_items:
        out: list[WorkflowGroup] = []
        for raw in db_items:
            try:
                group = WorkflowGroup.model_validate(raw)
                if workspace_id and group.workspace_id != workspace_id:
                    continue
                out.append(group)
            except Exception as exc:
                # A corrupt/unvalidatable record silently excluded here is
                # indistinguishable from the group having been deleted — warn.
                warnings.warn(
                    f"Dropping corrupt group record {raw.get('id') if isinstance(raw, dict) else '?'!r} "
                    f"from list_groups: {type(exc).__name__}: {exc}",
                    stacklevel=2,
                )
                continue
        return sorted(out, key=lambda g: g.created_at)
    out = []
    for path in sorted(_groups_dir().glob("*.json"), key=lambda p: p.stat().st_mtime_ns):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            group = WorkflowGroup.model_validate(raw)
            if workspace_id and group.workspace_id != workspace_id:
                continue
            out.append(group)
        except Exception as exc:
            warnings.warn(f"Dropping corrupt group file {path} from list_groups: {exc}", stacklevel=2)
            continue
    return out


def save_group(group: WorkflowGroup) -> WorkflowGroup:
    group = group.model_copy(update={"updated_at": time.time()})
    _write_raw(group)
    return group


def delete_group(group_id: str) -> bool:
    group = get_group(group_id)
    group_dir = _groups_dir() / group_id
    db_delete("groups", group_id)
    path = _group_path(group_id)
    existed = group is not None or path.is_file() or group_dir.exists()
    if path.is_file():
        path.unlink()
    if group_dir.is_dir():
        shutil.rmtree(group_dir)
    return existed


def ensure_default_group(workspace_id: str = "") -> WorkflowGroup:
    """Return this workspace's Default group, creating it on first use.

    This is the migration target for workflows that predate group_id, and the
    default `create_workflow` group when none is specified.
    """
    workspace_id = workspace_id or LOCAL_WORKSPACE_ID
    for group in list_groups(workspace_id):
        if group.name == DEFAULT_GROUP_NAME:
            return group
    return create_group(DEFAULT_GROUP_NAME, workspace_id=workspace_id)


def _find_app(group: WorkflowGroup, app_id: str) -> GroupApp | None:
    for app in group.apps:
        if app.id == app_id:
            return app
    return None


def add_app(group_id: str, name: str, login_url: str, success_url: str = "") -> WorkflowGroup | None:
    group = get_group(group_id)
    if group is None:
        return None
    app_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"app-{len(group.apps) + 1}"
    base_id = app_id
    n = 2
    existing_ids = {a.id for a in group.apps}
    while app_id in existing_ids:
        app_id = f"{base_id}-{n}"
        n += 1
    group.apps.append(GroupApp(id=app_id, name=name, login_url=login_url, success_url=success_url))
    return save_group(group)


def update_app(group_id: str, app_id: str, **fields: Any) -> WorkflowGroup | None:
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(app, key):
            setattr(app, key, value)
    return save_group(group)


def remove_app(group_id: str, app_id: str) -> WorkflowGroup | None:
    group = get_group(group_id)
    if group is None:
        return None
    group.apps = [a for a in group.apps if a.id != app_id]
    auth_file = group_auth_dir(group_id) / f"{app_id}.json"
    if auth_file.is_file():
        auth_file.unlink()
    return save_group(group)


def set_group_app_auth(group_id: str, app_id: str, storage_state_path: str) -> WorkflowGroup | None:
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    app.storage_state_path = storage_state_path
    app.captured_at = time.time()
    app.checked_at = app.captured_at  # a just-completed login is inherently verified
    app.check_note = ""
    app.last_error = ""
    return save_group(group)


def mark_group_app_unverified(group_id: str, app_id: str) -> WorkflowGroup | None:
    """The app's saved session was rewritten from a browser we never checked (a recording's
    write-back), so the next gate must probe it again. Unlike set_group_app_auth this is not a
    fresh login: it never vouches for the session or clears an expired verdict."""
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    app.checked_at = None
    app.check_note = ""
    return save_group(group)


def set_group_app_auth_definition(group_id: str, app_id: str, definition: dict[str, Any] | None) -> WorkflowGroup | None:
    """Sets auth_definition directly. Unlike update_app's **fields (which skips None values on
    purpose — right for an ordinary partial edit), this allows clearing a definition back to
    None: a re-connect whose self-test failed, or a login_url edit, must be able to drop a stale
    definition rather than silently keep an old one that no longer matches this app."""
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    app.auth_definition = definition
    return save_group(group)


def set_group_app_error(group_id: str, app_id: str, error: str) -> WorkflowGroup | None:
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    app.last_error = error[:500]
    app.checked_at = time.time()
    return save_group(group)


def set_group_app_checked(group_id: str, app_id: str, state: str) -> WorkflowGroup | None:
    """Record the outcome of a probe (check_app_session_sync).

    "ready"      — confirmed signed in: stamps checked_at (the only thing that makes an app read
                   as verified) and clears last_error / check_note.
    "expired"    — confirmed signed out: stamps checked_at and sets last_error so the badge and
                   the recording gate agree.
    "unverified" — the probe could not tell. Clears checked_at so the app can never read as
                   verified off a probe that proved nothing, and leaves last_error alone: an
                   inconclusive read is not evidence the session recovered, nor that it died.

    "ready"/"expired" always overwrite last_error rather than preserving an existing one — this
    field is also written by set_group_app_error (an unrelated auth-capture failure). A fresh
    verdict is the most current signal about the session; deferring to a stale,
    differently-sourced message would let it survive display as if it explained the CURRENT state.
    """
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    if state == "unverified":
        app.checked_at = None
        app.check_note = "Couldn't confirm this session — the app didn't give a clear answer."
        return save_group(group)
    app.checked_at = time.time()
    app.check_note = ""
    app.last_error = "Session expired — sign in again." if state == "expired" else ""
    return save_group(group)


def clear_group_app_auth(group_id: str, app_id: str) -> WorkflowGroup | None:
    group = get_group(group_id)
    if group is None:
        return None
    app = _find_app(group, app_id)
    if app is None:
        return None
    app.captured_at = None
    app.checked_at = None
    app.check_note = ""
    app.storage_state_path = ""
    app.last_error = ""
    auth_file = group_auth_dir(group_id) / f"{app_id}.json"
    if auth_file.is_file():
        auth_file.unlink()
    return save_group(group)
