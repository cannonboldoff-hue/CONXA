"""Publish + installer-hosting endpoints used by Build Studio and end users.

Build Studio compiles locally, then publishes the data-only skill pack here so
conxa-runtime.exe instances can pull deltas (served by skillpack_update_routes), and
uploads the built ``{Company}-Setup.exe`` for end-user download.

Ownership: the first workspace to publish a slug owns it. Subsequent publishes
or installer uploads for that slug must come from the same workspace (403
otherwise). Installer *downloads* are public — end users have no Clerk account.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from conxa_core.config import settings
from conxa_core.db import db_get, db_set, db_list_kv, using_database
from conxa_core.sync_paths import sync_delta_path
from conxa_core.workspace import workspace_dir_slug
from conxa_core.storage.skill_pack_store import (
    get_skill_pack,
    get_or_create_skill_pack,
    save_skill_pack,
)
from conxa_core.models.workflow import SkillPackBuild, SkillPackInstaller
from app.api.deps import current_principal, entitlement_http_error, own_workspace_slug
from app.api.installer_storage import (
    installer_dir,
    installer_version_dir,
    installer_versions_ns,
    load_installer_from_db,
    sign_installer,
    verify_installer_signature,
)
from app.api.product_ownership import (
    SUPPORTED_INSTALLER_GENERATIONS,
    validate_installer_version,
)
from app.api.skillpack_storage import (
    read_pack_json_mirror,
    record_known_skill,
    skill_packs_dir,
    skillpack_versions_ns,
    write_pack_json_mirror,
    write_release_snapshot,
)
from app.services.entitlements import (
    _limits_from_billing,
    ensure_distribution_allowed,
    ensure_trial_active,
    ensure_white_label_allowed,
    ensure_workflow_publishable,
    get_installer_domain,
    normalize_plan,
    record_published_workflow,
)
from app.services.rbac import require_admin
from app.services.saas import add_audit_event, billing_for
from app.services import release_channel
from app.services.tracking_analytics import seed_recorded_baseline
from app.api.updates_routes import _require_admin

router = APIRouter(prefix="/workflows", tags=["publish"])
installers_router = APIRouter(prefix="/installers", tags=["installers"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

_SAFE_SLUG = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# Settings KV singleton holding the "current" installer generation stamped
# into newly built installers. Falls back to the last entry in
# SUPPORTED_INSTALLER_GENERATIONS when unset.
_PLATFORM_SETTINGS_NS = "platform"
_INSTALLER_GENERATIONS_KEY = "installer_generations"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class PublishFile(BaseModel):
    path: str = Field(..., min_length=1, max_length=256)
    content_base64: str = Field(..., min_length=1)


class PublishBody(BaseModel):
    slug: str = Field(default="", max_length=64)  # Deprecated — ignored, workspace_id derived from principal
    skill_slug: str = Field(..., min_length=1, max_length=64)
    group_id: str = Field(default="", max_length=64)
    # Display-only names for Cloud's Skill Packages → Group → Workflow
    # navigation — Build Studio already has both on its Group/Workflow
    # entities. Falls back to the slug/group_id itself when absent (an older
    # Build Studio that hasn't picked this up yet).
    group_name: str = Field(default="", max_length=128)
    workflow_name: str = Field(default="", max_length=128)
    display_name: str = Field(default="", max_length=128)
    target_url: str = Field(default="")
    protected_url: str = Field(default="")
    skill_pack_version: str = Field(..., min_length=1, max_length=32)
    release_notes: str = Field(default="", max_length=2000)
    # Whether Build Studio's local test run passed for this workflow before
    # publish — surfaced to Cloud admins reviewing a "ready for release"
    # version alongside its diff and artifact.
    tests_passed: bool = Field(default=False)
    # How long the human took to perform the workflow once, live, during recording
    # (Build Studio's Workflow.recording_duration_seconds). Seeds the ROI baseline —
    # see tracking_analytics.seed_recorded_baseline. None for a Build Studio that
    # hasn't picked this up yet, or if the recording predates the field.
    recording_duration_seconds: float | None = Field(default=None)
    files: list[PublishFile] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_slug(slug: str) -> str:
    s = slug.strip()
    if not s or any(c not in _SAFE_SLUG for c in s) or ".." in s:
        raise HTTPException(status_code=400, detail="invalid_slug")
    return s


def _validate_rel_path(rel: str) -> str:
    r = rel.strip().replace("\\", "/")
    if not r or r.startswith("/") or ".." in r.split("/"):
        raise HTTPException(status_code=400, detail=f"invalid_file_path: {rel}")
    return r


def _validate_version(version: str) -> str:
    value = str(version or "").strip()
    if not _SEMVER_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid_installer_version")
    return value


def _validate_skill_pack_version(version: str) -> str:
    value = str(version or "").strip()
    if not _SEMVER_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid_skill_pack_version")
    return value


def _validate_release_notes(notes: str | None) -> str:
    value = str(notes or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="release_notes_required")
    if len(value) > 2000:
        raise HTTPException(status_code=400, detail="release_notes_too_long")
    return value


def _random_installer_name() -> str:
    """Free-plan installers get an unbranded name — see docs/PRD.md §11."""
    return "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(10))


def _mint_pack_token(
    namespace: str, slug: str, workspace_id: str, version: str, owner_user_id: str
) -> str:
    """Return the per-company long-lived token in ``namespace``, minting one on
    first publish and reusing it on republish.

    Rotating a token is done by deleting its KV entry, which forces a new one on
    the next publish. Keyed by slug (the workspace identifier) so that tracking
    aggregation and verification can find tokens by the workspace slug."""
    existing = db_get(namespace, slug)
    if isinstance(existing, dict) and existing.get("token"):
        token = str(existing["token"])
    else:
        token = secrets.token_urlsafe(32)
    db_set(
        namespace,
        slug,
        {
            "token": token,
            "company": slug,
            "version": version,
            "workspace_id": workspace_id,
            "owner_user_id": owner_user_id,
            "updated_at": time.time(),
        },
    )
    return token


def _tracking_token(slug: str, workspace_id: str, version: str, owner_user_id: str) -> str:
    """Telemetry ingest token embedded in pack.json (see tracking_routes)."""
    return _mint_pack_token("tracking_tokens", slug, workspace_id, version, owner_user_id)


def _sync_token(slug: str, workspace_id: str, version: str, owner_user_id: str) -> str:
    """Long-lived skill-pack delta-sync token embedded in pack.json and shipped
    inside the installer so the runtime can pull updates without a Conxa login."""
    return _mint_pack_token("sync_tokens", slug, workspace_id, version, owner_user_id)


def _api_base(request: Request) -> str:
    if settings.api_base_url:
        return settings.api_base_url.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _upsert_published_skill_pack(
    body: PublishBody, slug: str, skill_pack_version: str, workspace_id: str
) -> None:
    """Mirror a publish into the cloud's own SkillPack record for this workspace —
    dashboard-visible build metadata. The workflows that compiled into this
    release live in Build Studio's local store, not here; the cloud only ever
    sees the published artifact, so it tracks the package, not the sources."""
    name = body.display_name.strip() or slug
    pack = get_or_create_skill_pack(workspace_id, display_name=name)
    pack.build = SkillPackBuild(last_built_at=time.time(), output_path="", version=skill_pack_version)
    save_skill_pack(pack)


def _decode_publish_files(files: list[PublishFile]) -> dict[str, bytes]:
    """Validate + base64-decode every file once, so the artifact hash, the
    immutable snapshot, and the mutable mirror all read from the same bytes."""
    decoded: dict[str, bytes] = {}
    for f in files:
        rel = _validate_rel_path(f.path)
        try:
            decoded[rel] = base64.b64decode(f.content_base64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise HTTPException(status_code=400, detail=f"invalid_base64: {rel}") from exc
    return decoded


def _artifact_sha256(files: dict[str, bytes]) -> str:
    """Canonical hash over sorted (path, bytes) pairs — the release fingerprint
    used both to identify a published release and to detect a byte-identical
    republish (``skill_pack_artifact_unchanged`` below)."""
    hasher = hashlib.sha256()
    for path in sorted(files):
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(files[path])
        hasher.update(b"\x00")
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Publish skill pack data
# ---------------------------------------------------------------------------

def _publish_skill_pack_impl(
    slug: str, body: PublishBody, principal: Any, request: Request, installer_version: str | None
) -> dict[str, Any]:
    """Shared by the legacy ``/publish`` route and the versioned
    ``/{installer_version}/{slug}/skill-packs/upload`` route. ``installer_version``
    is ``None`` for the legacy route (unversioned URLs are minted into pack.json,
    matching every runtime already deployed against them).

    Publishing is a release transaction (docs/Implementation-Plan.md §3.4): the
    stable channel only ever moves after the immutable per-version snapshot and
    its version-history row are durably persisted, and never moves at all if
    anything before that fails — see the numbered steps below. A duplicate
    version number is rejected outright; a byte-identical republish (same
    artifact hash as the current stable release) is rejected explicitly rather
    than silently minting a no-op version.
    """
    skill_slug = body.skill_slug.strip()
    if not skill_slug:
        raise HTTPException(status_code=400, detail="skill_slug_required")

    try:
        ensure_trial_active(principal)
        ensure_workflow_publishable(principal, [skill_slug])
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc

    skill_pack_version = _validate_skill_pack_version(body.skill_pack_version)
    decoded_files = _decode_publish_files(body.files)
    artifact_sha256 = _artifact_sha256(decoded_files)

    # A given semver is an immutable release. The one exception: a row left
    # behind by a publish attempt that's still "ready" (uploaded, awaiting a
    # Cloud admin's Release/Deploy decision) or that crashed before even
    # reaching "ready" (legacy "pending") — neither was ever activated, so
    # retrying the same version number is safe and expected, not an overwrite.
    existing_row = db_get(skillpack_versions_ns(slug, skill_slug), skill_pack_version)
    if isinstance(existing_row, dict) and existing_row.get("status", "published") not in ("ready", "pending"):
        raise HTTPException(status_code=409, detail="skill_pack_version_exists")

    # Publishing an artifact byte-identical to what's already live is almost
    # always a mistake (forgot to rebuild, re-ran a stale script) — reject it
    # explicitly instead of quietly minting a no-op version. Prefer blocking
    # over an implicit "republish" per the release-system plan. Scoped to this
    # skill's own stable version — a sibling skill's artifact never affects it.
    current_stable_version = release_channel.get_stable_version(slug, skill_slug)
    if current_stable_version:
        current_stable_row = db_get(skillpack_versions_ns(slug, skill_slug), current_stable_version)
        if isinstance(current_stable_row, dict) and current_stable_row.get("artifact_sha256") == artifact_sha256:
            raise HTTPException(status_code=409, detail="skill_pack_artifact_unchanged")

    release_channel.record_release_event(
        principal, slug, skill_slug, release_channel.EVT_PUBLISH_STARTED, metadata={"version": skill_pack_version}
    )

    try:
        published_at = time.time()
        api_base = _api_base(request)
        if installer_version:
            sync_url_path = sync_delta_path(slug, installer_version)
            tracking_url_path = f"/api/v1/workflows/{installer_version}/{slug}/tracking/events"
        else:
            sync_url_path = sync_delta_path(slug, None)
            tracking_url_path = f"/api/tracking/{slug}/events"
        tracking = {
            "enabled": True,
            "tracking_url": f"{api_base}{tracking_url_path}",
            "tracking_token": _tracking_token(slug, principal.workspace_id, skill_pack_version, principal.user_id),
            "workspace_id": slug,
            "schema_version": 1,
            "protocol_version": 1,
        }
        sync_token = _sync_token(slug, principal.workspace_id, skill_pack_version, principal.user_id)

        # 1. Write the immutable per-version snapshot FIRST. This is the artifact
        #    a future rollback (or the initial Release) restores from — never
        #    touched again after this call. Scoped to this skill's own files
        #    only (never pack.json — see skillpack_storage.write_release_snapshot).
        write_release_snapshot(slug, skill_slug, skill_pack_version, decoded_files)

        # 2. Version-history row, written as "ready" — uploaded and durably
        #    persisted, but NOT yet activated. A Cloud admin's explicit
        #    Release/Deploy action (release_routes.post_release_release) is
        #    what flips this to "published" and moves the stable channel —
        #    publishing here never touches runtime-servable state. One row
        #    per (slug, skill_slug, version) — this skill's own independent
        #    history.
        history_row = {
            "slug": slug,
            "skill_slug": skill_slug,
            "version": skill_pack_version,
            "release_notes": body.release_notes.strip(),
            "group_id": body.group_id.strip(),
            "tests_passed": body.tests_passed,
            "workspace_id": principal.workspace_id,
            "owner_user_id": principal.user_id,
            "published_by": {"user_id": principal.user_id, "email": principal.email, "name": principal.name},
            "published_at": published_at,
            "files_written": len(decoded_files),
            "file_count": len(decoded_files),
            "size_bytes": sum(len(v) for v in decoded_files.values()),
            "artifact_sha256": artifact_sha256,
            "status": "ready",
            "is_latest": False,
        }
        db_set(skillpack_versions_ns(slug, skill_slug), skill_pack_version, history_row)
        release_channel.record_release_event(
            principal, slug, skill_slug, release_channel.EVT_VERSION_CREATED, metadata={"version": skill_pack_version}
        )

        # Company-level pack.json plumbing only — sync endpoint/token, tracking,
        # display name. Deliberately excludes `skills`/`skill_groups` (the union
        # that tells a runtime "what skills does this company have") and any
        # component_versions/manifest entry for this skill — those are what
        # `_build_delta` actually serves, and moving them here would make this
        # skill downloadable before anyone released it. The Release/Deploy
        # endpoint (release_routes.post_release_release) adds them.
        existing_pack = read_pack_json_mirror(slug)
        pack_updates = {
            "workspace_id": slug,
            "display_name": body.display_name.strip() or existing_pack.get("display_name") or slug,
            "release_notes": body.release_notes.strip(),
            "published_at": published_at,
            "sync_endpoint": f"{api_base}{sync_url_path}",
            "sync_token": sync_token,
            "tracking": tracking,
        }
        if installer_version:
            pack_updates["installer_version"] = installer_version
        write_pack_json_mirror(slug, pack_updates)
        _upsert_published_skill_pack(body, slug, skill_pack_version, principal.workspace_id)
        record_known_skill(
            slug, skill_slug,
            group_id=body.group_id.strip(), group_name=body.group_name.strip(), workflow_name=body.workflow_name.strip(),
        )
        record_published_workflow(principal.workspace_id, skill_slug)
        seed_recorded_baseline(principal.workspace_id, skill_slug, body.recording_duration_seconds)

        add_audit_event(
            principal,
            "publish",
            resource_type="skill_pack",
            resource_id=slug,
            metadata={"skill_slug": skill_slug, "version": skill_pack_version, "files_written": len(decoded_files)},
        )
        release_channel.record_release_event(
            principal, slug, skill_slug, release_channel.EVT_PUBLISH_SUCCEEDED, metadata={"version": skill_pack_version}
        )
    except Exception as exc:  # noqa: BLE001
        release_channel.record_release_event(
            principal,
            slug,
            skill_slug,
            release_channel.EVT_PUBLISH_FAILED,
            metadata={"version": skill_pack_version, "error": str(exc)[:300]},
        )
        raise

    billing = billing_for(principal)
    limits = _limits_from_billing(billing)
    return {
        "slug": slug,
        "version": skill_pack_version,
        "files_written": len(decoded_files),
        "sync_url": sync_url_path,
        "sync_token": sync_token,
        "tracking": tracking,
        "workspace_id": principal.workspace_id,
        "published_at": published_at,
        "plan": normalize_plan(billing),
        "distribution": limits["distribution"],
    }


@router.post("/publish")
def post_publish(body: PublishBody, request: Request) -> dict[str, Any]:
    """Legacy, unversioned publish route. Kept permanently — see
    ``post_publish_v2`` for the versioned equivalent, and docs/TRD.md §3.3
    for why this can never be removed."""
    principal = current_principal(request)
    require_admin(principal)
    slug = workspace_dir_slug(principal.workspace_id)
    return _publish_skill_pack_impl(slug, body, principal, request, installer_version=None)


@router.post("/{installer_version}/skill-packs/upload")
def post_publish_v2(
    installer_version: str, body: PublishBody, request: Request
) -> dict[str, Any]:
    principal = current_principal(request)
    require_admin(principal)
    installer_version = validate_installer_version(installer_version)
    slug = workspace_dir_slug(principal.workspace_id)
    return _publish_skill_pack_impl(slug, body, principal, request, installer_version=installer_version)


@router.get("/{installer_version}/skill-packs/versions")
def get_skill_pack_versions_v2(
    installer_version: str, skill_slug: str, request: Request
) -> dict[str, Any]:
    principal = current_principal(request)
    require_admin(principal)
    slug = workspace_dir_slug(principal.workspace_id)
    return _skill_pack_versions_impl(slug, skill_slug, request)


def _skill_pack_versions_impl(slug: str, skill_slug: str, request: Request) -> dict[str, Any]:
    """Authenticated release history for ONE skill — the
    version/release-comment/publishing-limit surface for Skill Pack Publishing.
    Every skill has its own independent history; a sibling skill's versions
    never appear here."""
    principal = current_principal(request)
    require_admin(principal)
    slug = _validate_slug(slug)

    versions = [
        row
        for _key, row in db_list_kv(skillpack_versions_ns(slug, skill_slug))
        if isinstance(row, dict) and row.get("workspace_id") == principal.workspace_id
    ]
    versions.sort(key=lambda item: float(item.get("published_at") or 0), reverse=True)

    current_stable_version = release_channel.get_stable_version(slug, skill_slug)
    current_stable = next((v for v in versions if v.get("version") == current_stable_version), None)
    return {"slug": slug, "skill_slug": skill_slug, "versions": versions, "current_stable": current_stable}


# ---------------------------------------------------------------------------
# Installer upload (authed) + download (public)
# ---------------------------------------------------------------------------

async def _upload_installer_impl(slug: str, request: Request) -> dict[str, Any]:
    """Upload the built installer .exe as a raw octet-stream body.

    Query params: ``filename`` (display name), ``version``, ``release_notes``,
    ``distribution`` (``internal`` default | ``external``), ``white_label``.

    No product/skill-pack-slot entitlement check here — the per-slug slot limit
    was removed 2026-08-08 (see docs/Implementation-Plan.md). What's gated now
    is *reach*: Free and Starter may only upload an internal-use installer;
    external distribution requires Pro or Enterprise, and custom branding
    requires Enterprise (see docs/PRD.md §11, the capability ladder).
    """
    principal = current_principal(request)
    require_admin(principal)
    slug = _validate_slug(slug)

    distribution = request.query_params.get("distribution", "internal").strip().lower()
    if distribution not in ("internal", "external"):
        distribution = "internal"
    white_label = request.query_params.get("white_label", "false").strip().lower() == "true"
    try:
        ensure_trial_active(principal)
        ensure_distribution_allowed(principal, external=distribution == "external")
        ensure_white_label_allowed(principal, custom_branding=white_label)
    except Exception as exc:  # noqa: BLE001
        raise entitlement_http_error(exc) from exc

    max_bytes = settings.build_artifact_upload_max_bytes
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        raise HTTPException(status_code=413, detail="installer_too_large")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty_installer_body")
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="installer_too_large")

    sha256 = hashlib.sha256(body).hexdigest()
    version = _validate_version(request.query_params.get("version", ""))
    release_notes = _validate_release_notes(request.query_params.get("release_notes"))
    filename = request.query_params.get("filename")
    if not filename:
        if normalize_plan(billing_for(principal)) == "free":
            filename = f"{_random_installer_name()}.exe"
        else:
            domain = get_installer_domain(principal.workspace_id)
            filename = f"{domain}.exe" if domain else f"{slug}-Setup.exe"
    filename = Path(filename).name  # strip any path components
    # Skill count for the installer's meta.json — read from the already-published
    # pack.json, the source of truth for what this slug's release actually contains.
    # A malformed pack.json is left out of meta entirely (the field is optional
    # downstream) rather than reported as 0 — 0 reads as "this release has no
    # skills," which is a different and misleading claim from "we couldn't count".
    workflow_count: int | None = None
    published_pack_path = skill_packs_dir(slug) / "pack.json"
    if published_pack_path.is_file():
        try:
            workflow_count = len(json.loads(published_pack_path.read_text(encoding="utf-8")).get("skills") or [])
        except Exception:
            logger.warning("installer_upload_workflow_count_unreadable slug=%s", slug)
            workflow_count = None

    out_dir = installer_dir(slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    version_dir = installer_version_dir(slug, version)
    if version_dir.exists() or db_get(installer_versions_ns(slug), version) is not None:
        raise HTTPException(status_code=409, detail="installer_version_exists")
    version_dir.mkdir(parents=True, exist_ok=False)
    version_exe_path = version_dir / "installer.exe"
    tmp = version_exe_path.with_suffix(".exe.tmp")
    tmp.write_bytes(body)
    tmp.replace(version_exe_path)

    uploaded_at = time.time()
    meta: dict[str, Any] = {
        "slug": slug,
        "filename": filename,
        "version": version,
        "release_notes": release_notes,
        "sha256": sha256,
        "size": len(body),
        "uploaded_at": uploaded_at,
        "workspace_id": principal.workspace_id,
        "is_latest": True,
        "distribution": distribution,
        "white_label": white_label,
    }
    if workflow_count is not None:
        meta["workflow_count"] = workflow_count
    (version_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    for other_meta_path in (out_dir / "versions").glob("*/meta.json"):
        if other_meta_path == version_dir / "meta.json":
            continue
        try:
            other_meta = json.loads(other_meta_path.read_text(encoding="utf-8"))
            if other_meta.get("is_latest"):
                other_meta["is_latest"] = False
                other_meta_path.write_text(json.dumps(other_meta, indent=2), encoding="utf-8")
        except Exception:
            # A version whose `is_latest` flag couldn't be cleared stays marked
            # latest alongside the one just uploaded — two rows would claim
            # is_latest. Log it so a stuck flag is visible instead of a silent
            # display inconsistency the next time this list is viewed.
            logger.warning("installer_meta_is_latest_clear_failed slug=%s path=%s", slug, other_meta_path)
            continue

    # Store metadata only — the binary is too large (~20 MB) to fit in a JSONB field.
    # Disk is the primary store; DB tracks version history and is_latest state.
    db_set(
        installer_versions_ns(slug),
        version,
        meta,
    )
    for _other_key, other_meta in db_list_kv(installer_versions_ns(slug)):
        if not isinstance(other_meta, dict):
            continue
        other_version = other_meta.get("version")
        if other_version == version:
            continue
        if other_meta.get("is_latest"):
            other_meta["is_latest"] = False
            db_set(installer_versions_ns(slug), other_version, other_meta)

    latest_exe_path = out_dir / "installer.exe"
    tmp_latest = latest_exe_path.with_suffix(".exe.tmp")
    tmp_latest.write_bytes(body)
    tmp_latest.replace(latest_exe_path)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Persist installer metadata onto the SkillPack record so the dashboard can
    # surface version and a download button without reading the filesystem.
    pack = get_skill_pack(principal.workspace_id)
    if pack is not None:
        pack.installer = SkillPackInstaller(
            built_at=uploaded_at,
            installer_path=str(latest_exe_path),
            filename=filename,
            version=version,
            runtime_version="",
            release_notes=release_notes,
        )
        save_skill_pack(pack)

    add_audit_event(
        principal,
        "installer_upload",
        resource_type="installer",
        resource_id=slug,
        metadata={"version": version, "size": len(body), "sha256": sha256, "release_notes": release_notes},
    )

    return {
        "slug": slug,
        "version": version,
        "sha256": sha256,
        "size": len(body),
        "download_url": f"/api/v1/installers/{slug}",
        "version_download_url": f"/api/v1/installers/{slug}/versions/{version}",
    }


def _legacy_own_slug(request: Request, slug: str) -> str:
    """Legacy ``/workflows/{slug}/installer/...`` routes: the path value must be the caller's
    own workspace. A generation value (``v1``/``v2``) is what shipped Studios put there, so
    it means "my own slug" rather than a shared slot."""
    principal = current_principal(request)
    slug = _validate_slug(slug)
    if slug in SUPPORTED_INSTALLER_GENERATIONS:
        return workspace_dir_slug(principal.workspace_id)
    return own_workspace_slug(principal, slug)


@router.post("/{slug}/installer/upload")
async def post_installer_upload(slug: str, request: Request) -> dict[str, Any]:
    """Legacy, unversioned installer-upload route. Kept permanently — see
    ``post_installer_upload_v2`` for the versioned equivalent, and
    docs/TRD.md §3.3 for why this can never be removed."""
    return await _upload_installer_impl(_legacy_own_slug(request, slug), request)


@router.post("/installer/upload")
async def post_installer_upload_v2(request: Request) -> dict[str, Any]:
    """Versioned-generation equivalent of ``post_installer_upload``. No path
    parameter — the workspace (and thus slug) comes from the authenticated
    principal, so there is nothing for ``{installer_version}``/``{slug}`` to
    disambiguate. See docs/TRD.md's back-compat table."""
    principal = current_principal(request)
    require_admin(principal)
    slug = workspace_dir_slug(principal.workspace_id)
    return await _upload_installer_impl(slug, request)


def _installer_versions_impl(slug: str, request: Request) -> dict[str, Any]:
    """Authenticated installer release history for the dashboard."""
    principal = current_principal(request)
    require_admin(principal)
    slug = _validate_slug(slug)

    def _row_from_meta(meta: dict[str, Any], fallback_version: str) -> dict[str, Any]:
        version = str(meta.get("version") or fallback_version)
        ts = int(time.time())
        sig = sign_installer(slug, version, ts)
        download_url = f"/api/v1/installers/{slug}/versions/{version}"
        if settings.installer_signing_key:
            download_url += f"?ts={ts}&sig={sig}"
        # Every current upload always writes a real sha256/size (computed from the
        # uploaded bytes, never absent) — an empty/zero value here means a row
        # written by an older schema or a corrupted meta.json, not "verified
        # empty". Log it so a genuinely missing checksum doesn't go unnoticed.
        if not meta.get("sha256") or not meta.get("size"):
            logger.warning("installer_meta_missing_integrity slug=%s version=%s", slug, version)
        row = {
            "slug": slug,
            "version": version,
            "release_notes": str(meta.get("release_notes") or ""),
            "filename": str(meta.get("filename") or f"{slug}-Setup.exe"),
            "sha256": str(meta.get("sha256") or ""),
            "size": int(meta.get("size") or 0),
            "uploaded_at": float(meta.get("uploaded_at") or 0),
            "workspace_id": str(meta.get("workspace_id") or ""),
            "is_latest": bool(meta.get("is_latest")),
            "download_url": download_url,
        }
        if "workflow_count" in meta:
            row["workflow_count"] = int(meta.get("workflow_count") or 0)
        return row

    # Merge disk (fast path, may be missing after a wipe) with Postgres (durable source
    # of truth). Postgres wins on conflict since disk can only ever be stale or absent.
    versions_by_key: dict[str, dict[str, Any]] = {}
    versions_dir = installer_dir(slug) / "versions"
    if versions_dir.is_dir():
        for meta_path in versions_dir.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                # Skipped silently before — a corrupted meta.json just vanished
                # from the version history with no trace. Postgres (below) is
                # the durable source of truth, so the row usually still shows
                # up from there; log so a disk-only gap is at least visible.
                logger.warning("installer_meta_unreadable slug=%s path=%s", slug, meta_path)
                continue
            if meta.get("workspace_id") != principal.workspace_id:
                continue
            versions_by_key[meta_path.parent.name] = _row_from_meta(meta, meta_path.parent.name)
    if using_database():
        for key, meta in db_list_kv(installer_versions_ns(slug)):
            if not isinstance(meta, dict) or meta.get("workspace_id") != principal.workspace_id:
                continue
            dedup_key = str(meta.get("version") or key)
            versions_by_key[dedup_key] = _row_from_meta(meta, dedup_key)

    versions = list(versions_by_key.values())
    versions.sort(key=lambda item: float(item.get("uploaded_at") or 0), reverse=True)
    return {"slug": slug, "versions": versions}


@router.get("/{slug}/installer/versions")
def get_installer_versions(slug: str, request: Request) -> dict[str, Any]:
    """Legacy, unversioned route. Kept permanently — see
    ``get_installer_versions_v2`` for the versioned equivalent, and
    docs/TRD.md §3.3 for why this can never be removed."""
    return _installer_versions_impl(_legacy_own_slug(request, slug), request)


@router.get("/installer/versions")
def get_installer_versions_v2(request: Request) -> dict[str, Any]:
    """Versioned-generation equivalent of ``get_installer_versions``. No path
    parameter — the workspace (and thus slug) comes from the authenticated
    principal. This is the URL the Cloud dashboard's Build Installer page
    calls. See docs/TRD.md's back-compat table."""
    principal = current_principal(request)
    require_admin(principal)
    slug = workspace_dir_slug(principal.workspace_id)
    return _installer_versions_impl(slug, request)


@router.get("/generations")
def get_installer_generations() -> dict[str, Any]:
    """Public. Returns the current default installer generation that Build
    Studio/CI should stamp into newly built installers, plus the full
    supported/deprecated sets. `{installer_version}` is frozen into each
    installer at build time — this endpoint only ever affects *new* builds,
    never already-installed runtimes."""
    row = db_get(_PLATFORM_SETTINGS_NS, _INSTALLER_GENERATIONS_KEY)
    if isinstance(row, dict) and row.get("current") in SUPPORTED_INSTALLER_GENERATIONS:
        return {
            "current": row["current"],
            "supported": list(SUPPORTED_INSTALLER_GENERATIONS),
            "deprecated": list(row.get("deprecated") or []),
        }
    return {
        "current": SUPPORTED_INSTALLER_GENERATIONS[-1],
        "supported": list(SUPPORTED_INSTALLER_GENERATIONS),
        "deprecated": [],
    }


@admin_router.post("/workflows/generations")
def post_installer_generations(body: dict[str, Any], authorization: str = Header(default="")) -> dict[str, Any]:
    """Admin-only (Bearer CONXA_ADMIN_TOKEN). Flips the default generation
    stamped into new installer builds. Never affects already-installed
    runtimes — see ``get_installer_generations`` docstring above."""
    _require_admin(authorization)
    current = str(body.get("current") or "").strip()
    if current not in SUPPORTED_INSTALLER_GENERATIONS:
        raise HTTPException(status_code=400, detail="unsupported_installer_version")
    deprecated = [v for v in (body.get("deprecated") or []) if v in SUPPORTED_INSTALLER_GENERATIONS]
    row = {"current": current, "deprecated": deprecated, "updated_at": time.time()}
    db_set(_PLATFORM_SETTINGS_NS, _INSTALLER_GENERATIONS_KEY, row)
    return row


def _stream_installer(
    exe_path: Path, meta_path: Path, *, slug: str, version: str | None
) -> StreamingResponse:
    if not exe_path.is_file() or not meta_path.is_file():
        fallback = load_installer_from_db(slug, version)
        if fallback is None:
            raise HTTPException(status_code=404, detail="installer_not_published")
        meta, content = fallback
        try:
            exe_path.parent.mkdir(parents=True, exist_ok=True)
            exe_path.write_bytes(content)
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError:
            pass
        headers = {
            "Content-Disposition": f'attachment; filename="{meta.get("filename", "setup.exe")}"',
            "X-Conxa-SHA256": str(meta.get("sha256", "")),
        }
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/octet-stream",
            headers=headers,
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    headers = {
        "Content-Disposition": f'attachment; filename="{meta.get("filename", "setup.exe")}"',
        "X-Conxa-SHA256": str(meta.get("sha256", "")),
    }
    return StreamingResponse(
        open(exe_path, "rb"),  # noqa: SIM115
        media_type="application/octet-stream",
        headers=headers,
    )


@installers_router.get("/{slug}/versions/{version}")
def get_installer_version(slug: str, version: str, request: Request) -> StreamingResponse:
    """Installer download for an exact version. Requires a valid ts+sig when
    SKILL_INSTALLER_SIGNING_KEY is configured (SG-07); public otherwise (dev)."""
    slug = _validate_slug(slug)
    version = _validate_version(version)
    verify_installer_signature(slug, version, request)
    version_dir = installer_version_dir(slug, version)
    return _stream_installer(version_dir / "installer.exe", version_dir / "meta.json", slug=slug, version=version)


@installers_router.get("/{slug}")
def get_installer(slug: str, request: Request) -> StreamingResponse:
    """Latest installer download. SHA-256 returned in X-Conxa-SHA256. Requires a
    valid ts+sig when SKILL_INSTALLER_SIGNING_KEY is configured (SG-07); public
    otherwise (dev)."""
    slug = _validate_slug(slug)
    verify_installer_signature(slug, None, request)
    out_dir = installer_dir(slug)
    return _stream_installer(out_dir / "installer.exe", out_dir / "meta.json", slug=slug, version=None)
