"""Endpoints consumed by conxa-runtime.exe at startup for skill pack sync.

Every route here is package-token authenticated (or public), never Clerk —
see app.api.security.PUBLIC_SKILL_PACK_SYNC_PREFIXES, which treats the whole
"/api/v1/skill-packs/" prefix as bypassing the Clerk gate. Dashboard-facing
SkillPack list/detail views live in workflow_routes.py instead, precisely to
avoid landing on this public prefix."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from conxa_core.config import settings
from conxa_core.db import db_get, db_set, db_list, db_list_kv, using_database
from app.api.product_ownership import validate_installer_version
from app.api.skillpack_storage import skill_packs_dir, skillpack_files_ns
from app.services.saas import principal_from_request, ensure_principal
from app.services.rbac import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skill-packs", tags=["skill-packs"])
# Versioned equivalent of the delta route below, nested under /workflows so it
# shares one mental model with the other three per-company endpoints
# (publish, installer upload, tracking) — see publish_routes.py.
versioned_router = APIRouter(prefix="/workflows", tags=["skill-packs"])

_STALE_RUNTIME_DAYS = 30

# Rate limiter: last sync timestamp per token hash. Persisted in the KV store
# (namespace `rate_limits`) so the limit survives process restarts and is shared
# across multiple app instances (Render horizontal scaling). Falls back to an
# in-memory dict in local dev where no database is configured.
_rate_cache: dict[str, float] = {}
_RATE_LIMIT_SECONDS = 300  # 5 minutes between sync requests per token
_RATE_LIMIT_NS = "rate_limits"


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


def _rate_limit_key(token: str) -> str:
    # Use first 16 chars of token hash as key — avoids storing full tokens
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _rate_limit_last(key: str) -> float:
    if using_database():
        rec = db_get(_RATE_LIMIT_NS, key)
        if isinstance(rec, dict):
            try:
                return float(rec.get("last_ts", 0.0))
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    return _rate_cache.get(key, 0.0)


def _rate_limit_set(key: str, ts: float) -> None:
    if using_database():
        db_set(_RATE_LIMIT_NS, key, {"last_ts": ts})
    else:
        _rate_cache[key] = ts


def _check_rate_limit(token: str) -> None:
    key = _rate_limit_key(token)
    now = time.time()
    last = _rate_limit_last(key)
    if now - last < _RATE_LIMIT_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="Too many sync requests. Wait 5 minutes between syncs.",
            headers={"Retry-After": str(int(_RATE_LIMIT_SECONDS - (now - last)))},
        )
    _rate_limit_set(key, now)


def _verify_sync_token(workspace_id: str, token: str | None) -> None:
    """Validate the Bearer token against the per-workspace sync_token.

    In production (SKILL_AUTH_REQUIRED=true) a valid sync token is required.
    The sync token is minted at publish time and embedded in the installer's
    pack.json — end users never need a Conxa login.

    In local dev (auth_required=false) validation is skipped so the Build
    Studio can sync without a published token.
    """
    if not settings.auth_required:
        return
    stored = db_get("sync_tokens", workspace_id)
    if not isinstance(stored, dict) or not stored.get("token"):
        raise HTTPException(status_code=401, detail="sync_token_not_configured")
    if not token or not secrets.compare_digest(str(stored["token"]), token):
        raise HTTPException(status_code=401, detail="invalid_sync_token")


def _ensure_skill_pack_on_disk(workspace_id: str) -> None:
    """Rehydrate the local skill-pack cache from Postgres if the disk was wiped.

    Render's free plan has no persistent disk and idles out, so the files written by
    publish_routes.post_publish can vanish between runtime sync requests. Postgres
    (written at publish time under the same namespace) is the durable source of truth.
    """
    if not using_database():
        return
    pack_path = skill_packs_dir(workspace_id) / "pack.json"
    if pack_path.is_file():
        return
    rows = db_list_kv(skillpack_files_ns(workspace_id))
    if not rows:
        return
    packs_dir = skill_packs_dir(workspace_id)
    for rel, value in rows:
        if not isinstance(value, dict) or not value.get("content_base64"):
            continue
        target = packs_dir / str(value.get("path") or rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(value["content_base64"]))


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


# The skill's code: small JSON, shipped inline in the delta response and required before a
# workflow can run at all. Everything else in a skill directory is an ARTIFACT — today that
# means `visuals/`, the recording-time screenshots the agent recovery tier compares against.
# Artifacts are bytes, not kilobytes, so they never ride the delta body; they are listed there
# as metadata and fetched separately (see get_skill_artifacts).
# app_knowledge.json (advisory popup knowledge from the app map, docs/TRD.md 11.5) rides with the code
# so it activates atomically with the skill version it was published with; it is optional per skill.
_CODE_FILES = ("execution.json", "recovery.json", "inputs.json", "manifest.json", "validation.json", "app_knowledge.json")


def _resolve_skill_dir(packs_dir: Path, group_id: str, slug: str) -> Path:
    """Packs published before group-nesting shipped still have their files at the old flat
    ``packs_dir/slug/`` location — serve from there so already-published companies don't go
    stuck on "no_change" forever just because they haven't republished."""
    skill_dir = packs_dir / group_id / slug
    if skill_dir.is_dir():
        return skill_dir
    legacy_dir = packs_dir / slug
    return legacy_dir if legacy_dir.is_dir() else skill_dir


def _artifact_entries(skill_dir: Path) -> list[dict[str, str]]:
    """``[{path, sha256}]`` for every non-code file in the skill — metadata only, never content.

    Deliberately a "not code" rule rather than a `visuals/` allow-list: a new kind of recovery
    artifact then ships without also needing a change here, and the failure mode of the inverse
    (a new artifact silently never syncing) is the exact bug this whole endpoint exists to fix.
    """
    if not skill_dir.is_dir():
        return []
    out: list[dict[str, str]] = []
    for fpath in sorted(skill_dir.rglob("*")):
        if not fpath.is_file():
            continue
        rel = fpath.relative_to(skill_dir).as_posix()
        if rel in _CODE_FILES:
            continue
        out.append({"path": rel, "sha256": _sha256_file(fpath)})
    return out


def _pack_version(workspace_id: str) -> str:
    # No pack.json yet is a real, legitimate state (nothing published for this
    # workspace) — "0" here means "everything is new", which is correct.
    pack_path = skill_packs_dir(workspace_id) / "pack.json"
    if not pack_path.is_file():
        return "0"
    try:
        return json.loads(pack_path.read_text(encoding="utf-8")).get("skill_pack_version", "0")
    except Exception as exc:
        # A pack.json that exists but fails to parse is corruption, not absence.
        # Silently returning "0" here made every runtime see "changed" forever —
        # an infinite full-resync loop that looks like a healthy sync from the
        # runtime's side. Raise so the corruption gets fixed instead of masked.
        raise HTTPException(status_code=500, detail="pack_json_corrupted") from exc


def _skill_version(workspace_id: str, slug: str) -> str:
    """Each skill's own version, recorded independently in component_versions KV at
    publish time (see publish_routes.post_publish). Falls back to the shared pack-level
    version for skill packs published before independent per-skill versioning existed,
    so old publishes don't regress to "always changed"."""
    rec = db_get("component_versions", f"skill_packs:{workspace_id}:{slug}")
    if isinstance(rec, dict) and rec.get("version"):
        return str(rec["version"])
    return _pack_version(workspace_id)


def _build_delta(workspace_id: str, since_map: dict[str, str]) -> dict[str, Any]:
    """Per-skill delta: each skill in the pack is compared independently against the
    client's last-known version for that specific skill, so only the skills that
    actually changed are shipped — never the whole workspace just because one skill
    was republished."""
    _ensure_skill_pack_on_disk(workspace_id)
    packs_dir = skill_packs_dir(workspace_id)
    pack_path = packs_dir / "pack.json"
    if not pack_path.is_file():
        raise HTTPException(status_code=404, detail=f"Skill pack not found: {workspace_id}")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    skill_groups = pack.get("skill_groups") or {}

    skills_out: list[dict[str, Any]] = []
    for slug in pack.get("skills", []):
        current_version = _skill_version(workspace_id, slug)
        client_version = since_map.get(slug, "0")
        group_id = skill_groups.get(slug) or "_default"
        # `group_id` is still reported below so the client writes its copy into the new
        # nested location regardless of where it was served from.
        skill_dir = _resolve_skill_dir(packs_dir, group_id, slug)

        if current_version == client_version or not skill_dir.is_dir():
            # Artifacts are listed even when the CODE is unchanged. They are fetched on a
            # separate pass that may not have finished (or may never have run, for a pack
            # installed before artifact sync existed), so gating the listing on a version bump
            # would leave every already-installed customer waiting for an unrelated republish
            # before their recovery screenshots could ever arrive.
            entry: dict[str, Any] = {"name": slug, "action": "no_change", "group": group_id}
            artifacts = _artifact_entries(skill_dir)
            if artifacts:
                entry["artifacts"] = artifacts
            skills_out.append(entry)
            continue

        files: list[dict[str, Any]] = []
        for fname in _CODE_FILES:
            fpath = skill_dir / fname
            if not fpath.is_file():
                continue
            files.append({
                "path": fname,
                "sha256": _sha256_file(fpath),
                "content_base64": base64.b64encode(fpath.read_bytes()).decode("ascii"),
            })
        update: dict[str, Any] = {
            "name": slug, "version": current_version, "action": "update",
            "group": group_id, "files": files,
        }
        artifacts = _artifact_entries(skill_dir)
        if artifacts:
            update["artifacts"] = artifacts
        skills_out.append(update)

    return {"skills": skills_out}


def _delta_impl(workspace_id: str, since: str, request: Request) -> dict[str, Any]:
    """Shared by the legacy ``/skill-packs/{workspace_id}/delta`` route and the
    versioned ``/workflows/{installer_version}/{workspace_id}/skill-packs/delta``
    route. `since` is a JSON-encoded map of {skill_slug: last_known_version},
    letting each skill be compared and shipped independently instead of one
    shared pack version.

    Authentication: Bearer token must match the per-workspace sync_token minted
    at publish time and embedded in the installer's pack.json.
    Rate limited: 1 request per 5 minutes per token.
    """
    token = _extract_token(request) if request else None
    _verify_sync_token(workspace_id, token)
    if token:
        _check_rate_limit(token)
    try:
        since_map = json.loads(since) if since else {}
        if not isinstance(since_map, dict):
            logger.warning("skillpack_delta_since_not_a_dict workspace_id=%s", workspace_id)
            since_map = {}
    except (json.JSONDecodeError, TypeError):
        # Falls back to "client knows nothing" — a full resync, not an error —
        # since this is an unattended background sync loop with no one to see
        # a 400. But a malformed `since` from a runtime's own saved state is
        # still worth knowing about: it means something wrote bad state.
        logger.warning("skillpack_delta_since_unparseable workspace_id=%s since=%r", workspace_id, since)
        since_map = {}
    return _build_delta(workspace_id, {str(k): str(v) for k, v in since_map.items()})


@router.get("/{workspace_id}/delta")
def get_skill_pack_delta(workspace_id: str, since: str = "{}", request: Request = None) -> dict[str, Any]:
    """Legacy, unversioned delta route. Kept permanently — see
    ``get_skill_pack_delta_v2`` for the versioned equivalent, and
    docs/TRD.md §3.3 for why this can never be removed."""
    return _delta_impl(workspace_id, since, request)


@versioned_router.get("/{installer_version}/{workspace_id}/skill-packs/delta")
def get_skill_pack_delta_v2(
    installer_version: str, workspace_id: str, since: str = "{}", request: Request = None
) -> dict[str, Any]:
    """Versioned delta route. ``installer_version`` is validated but not yet
    branched on — reserved for a future skill-pack wire-format generation, not
    dead code to remove. The wire contract today is identical across
    generations; see ``_delta_impl``/``_build_delta``."""
    validate_installer_version(installer_version)
    return _delta_impl(workspace_id, since, request)


# ─── Recovery artifacts ───────────────────────────────────────────────────────


class ArtifactRequest(BaseModel):
    # SHA-256 hashes the machine already holds in its content-addressed store. Everything the
    # skill has that is NOT in this list comes back in the zip.
    have: list[str] = Field(default_factory=list)


@versioned_router.post("/{installer_version}/{workspace_id}/skill-packs/{slug}/artifacts")
def get_skill_artifacts(
    installer_version: str,
    workspace_id: str,
    slug: str,
    body: ArtifactRequest,
    request: Request = None,
) -> Response:
    """One zip holding exactly the artifacts this machine is missing, for one skill.

    The machine already knows what it lacks — its artifact store is keyed by content hash, so
    "do I have this hash" IS the delta computation, done locally. It sends the answer; this
    returns precisely the difference. That makes first install (send nothing, get everything)
    and a later update (send all but three, get three) the same code path, with no separate
    protocol for either.

    NOT rate limited, unlike the delta route beside it. The artifact pass runs immediately after
    the code files land — that is the whole point, so execution is never blocked waiting for
    screenshots — and the delta route's 5-minutes-per-token limit would refuse it every time.
    Authentication is identical, so this exempts pacing, never access.

    Returns the zip bytes directly rather than a link to one. Render's free plan has no
    persistent disk (the reason _ensure_skill_pack_on_disk exists at all), so an archive written
    somewhere to be fetched afterwards is exactly the kind of thing that would be gone by the
    time the client asked for it.
    """
    validate_installer_version(installer_version)
    token = _extract_token(request) if request else None
    _verify_sync_token(workspace_id, token)

    _ensure_skill_pack_on_disk(workspace_id)
    packs_dir = skill_packs_dir(workspace_id)
    pack_path = packs_dir / "pack.json"
    if not pack_path.is_file():
        raise HTTPException(status_code=404, detail=f"Skill pack not found: {workspace_id}")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    if slug not in (pack.get("skills") or []):
        raise HTTPException(status_code=404, detail=f"Skill not found: {slug}")

    group_id = (pack.get("skill_groups") or {}).get(slug) or "_default"
    skill_dir = _resolve_skill_dir(packs_dir, group_id, slug)

    # An unknown hash is ignored rather than rejected: the store is shared across every skill and
    # workspace on the machine, so it legitimately holds hashes this particular skill never had.
    have = {str(h).lower() for h in body.have if isinstance(h, str)}

    buf = io.BytesIO()
    # ZIP_STORED, not DEFLATED: these are JPEG/PNG, already compressed. Deflating them again
    # spends CPU on a shared instance to save approximately nothing.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for entry in _artifact_entries(skill_dir):
            if entry["sha256"].lower() in have:
                continue
            zf.write(skill_dir / entry["path"], arcname=entry["path"])

    payload = buf.getvalue()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            # The client verifies this before extracting — same contract as every other
            # artifact the runtime downloads (manifest_manager.downloadArtifact).
            "X-Artifact-Sha256": hashlib.sha256(payload).hexdigest(),
            "Cache-Control": "no-store",
        },
    )


# ─── Telemetry ────────────────────────────────────────────────────────────────

class TelemetryBody(BaseModel):
    runtime_version: str = ""
    companies: list[str] = []
    platform: str = ""
    install_id: str = ""
    # Optional: {company: {skill_slug: installed_version}}, as reported by
    # runtime/server.js's _phonehome (added for the release-system's deployment
    # view — see docs/App-Flow.md). Absent for any runtime that hasn't picked up
    # that change yet; those registrations read as deployment status "unknown"
    # rather than a fabricated "up to date".
    skill_versions: dict[str, dict[str, str]] = {}
    # Optional: {company: {skill_slug: {code, at}}} — per-skill sync failures
    # (checksum mismatch, download/activation error), reported by
    # runtime/sync_errors.js via the same phone-home. Lets get_deployments
    # (release_routes.py) show a real "failed" status instead of "pending".
    sync_errors: dict[str, dict[str, dict[str, str]]] = {}
    # Optional fleet-visibility fields (Machine Registry) — all sticky like
    # skill_versions above, since an older runtime that hasn't self-updated
    # yet simply omits them and shouldn't blank out a previously-reported value.
    hostname: str = ""
    username: str = ""
    os_release: str = ""
    os_arch: str = ""
    capabilities: dict[str, str] = {}


telemetry_router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@telemetry_router.post("/runtime-start")
def post_telemetry_runtime_start(body: TelemetryBody) -> dict[str, Any]:
    """Non-critical. Records device registrations for ops visibility.

    Public endpoint — installed runtimes have no Clerk session.
    Workspace is derived from the per-company sync_token KV entry (set at publish time).
    Spoofing inflates counts but leaks nothing.
    """
    now = time.time()
    companies = [c.strip() for c in (body.companies or []) if c.strip()]
    platform = (body.platform or "").strip() or "unknown"
    install_id = "".join(c for c in (body.install_id or "").strip() if c.isalnum() or c in "-_")[:96]

    for company in companies:
        workspace_id = ""
        stored_token = db_get("sync_tokens", company)
        if isinstance(stored_token, dict):
            workspace_id = str(stored_token.get("workspace_id") or "")

        key = f"{company}:{install_id or platform}"
        existing = db_get("runtime_registrations", key) or {}
        company_skill_versions = body.skill_versions.get(company) if isinstance(body.skill_versions, dict) else None
        row: dict[str, Any] = {
            "company": company,
            "install_id": install_id,
            "platform": platform,
            "runtime_version": (body.runtime_version or "").strip(),
            "workspace_id": workspace_id,
            "last_seen": now,
            "first_seen": existing.get("first_seen", now),
            # Runtime never sends this — admin-only, set via /telemetry/runtimes/revoke.
            "revoked": bool(existing.get("revoked")) if isinstance(existing, dict) else False,
        }
        # Only overwrite skill_versions when this phone-home actually reported
        # it — an older runtime that hasn't self-updated yet keeps reporting
        # nothing for this field, and its last-known value (if any) shouldn't
        # be wiped back to "unknown" just because it phoned home again.
        if company_skill_versions:
            row["skill_versions"] = company_skill_versions
        elif isinstance(existing.get("skill_versions"), dict):
            row["skill_versions"] = existing["skill_versions"]

        company_sync_errors = body.sync_errors.get(company) if isinstance(body.sync_errors, dict) else None
        # Always overwrite (unlike skill_versions above) — sync_errors reflects
        # only the most recent sync pass, so an empty {} here is a real signal
        # ("nothing failed this time"), not a gap to preserve the old value for.
        row["sync_errors"] = company_sync_errors if isinstance(company_sync_errors, dict) else {}

        # Fleet-visibility fields — sticky: keep the last-known value when this
        # phone-home didn't report one (older runtime, or a field that failed
        # to read on this OS).
        for field in ("hostname", "username", "os_release", "os_arch"):
            reported = (getattr(body, field) or "").strip()
            row[field] = reported or (existing.get(field, "") if isinstance(existing, dict) else "")
        row["capabilities"] = body.capabilities if body.capabilities else (
            existing.get("capabilities", {}) if isinstance(existing, dict) else {}
        )

        db_set("runtime_registrations", key, row)

    return {"ok": True}


@telemetry_router.get("/runtimes")
def get_runtime_registrations(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Return runtime device registrations for the authenticated workspace.

    Filters to the caller's workspace; flags runtimes not seen in 30 days.
    Paginated via limit/offset (ponytail: in-process slice, not a DB-level
    query — fine up to roughly 10k rows per workspace, revisit if that
    changes). stale_count/version_distribution are computed over the full
    filtered set, not just the returned page.
    """
    principal = principal_from_request(request)
    ensure_principal(principal)

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    stale_cutoff = time.time() - _STALE_RUNTIME_DAYS * 86400
    all_regs = db_list("runtime_registrations")

    registrations = []
    version_counts: dict[str, int] = {}
    stale_count = 0

    for reg in all_regs:
        if not isinstance(reg, dict):
            continue
        if reg.get("workspace_id") != principal.workspace_id:
            continue
        is_stale = reg.get("last_seen", 0) < stale_cutoff
        if is_stale:
            stale_count += 1
        v = reg.get("runtime_version") or "unknown"
        version_counts[v] = version_counts.get(v, 0) + 1
        status = "revoked" if reg.get("revoked") else ("stale" if is_stale else "active")
        registrations.append({**reg, "stale": is_stale, "status": status})

    registrations.sort(key=lambda r: r.get("last_seen", 0), reverse=True)
    total = len(registrations)
    return {
        "registrations": registrations[offset:offset + limit],
        "stale_count": stale_count,
        "version_distribution": version_counts,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class RevokeRuntimeBody(BaseModel):
    install_id: str = Field(..., min_length=1, max_length=96)


@telemetry_router.post("/runtimes/revoke")
def post_revoke_runtime(body: RevokeRuntimeBody, request: Request) -> dict[str, Any]:
    """Admin-only. Marks a runtime registration as revoked for dashboard
    visibility/audit only — this never blocks skill sync, telemetry ingest,
    or execution, matching the architectural rule that the cloud never
    enforces on the running side (docs/TRD.md §13.6). There is no un-revoke;
    a reinstall generates a fresh install_id and re-registers clean.
    """
    principal = principal_from_request(request)
    ensure_principal(principal)
    require_admin(principal)

    install_id = "".join(c for c in body.install_id.strip() if c.isalnum() or c in "-_")[:96]
    if not install_id:
        raise HTTPException(status_code=400, detail="invalid_install_id")

    revoked_any = False
    for reg in db_list("runtime_registrations"):
        if not isinstance(reg, dict):
            continue
        if reg.get("workspace_id") != principal.workspace_id or reg.get("install_id") != install_id:
            continue
        key = f"{reg.get('company', '')}:{install_id or reg.get('platform', '')}"
        db_set("runtime_registrations", key, {**reg, "revoked": True})
        revoked_any = True

    if not revoked_any:
        raise HTTPException(status_code=404, detail="runtime_not_found")
    return {"install_id": install_id, "revoked": True}
