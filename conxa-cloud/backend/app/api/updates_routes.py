"""
Phase 4.5 — dependency manifest endpoints for Build Studio bootstrap and runtime self-updater.

Public GET endpoints (no auth):
  GET /updates/deps-manifest          — fetched by Build Studio bootstrap before Clerk login
  GET /updates/conxa-runtime-manifest  — fetched by runtime self-updater (host layer, ~85 MB, quarterly)
  GET /updates/conxa-app-manifest   — fetched by runtime self-updater (app layer, ~60 KB zip, every release)
  GET /updates/studio-manifest        — fetched by web frontend for download link
  GET /updates/studio/latest.yml      — served to electron-updater
  GET /updates/execute-manifest       — fetched by web frontend for download link
  GET /updates/execute/latest.yml     — served to electron-updater

Admin POST endpoints (Bearer CONXA_ADMIN_TOKEN required — called by CI after each build):
  POST /updates/conxa-runtime-manifest — update host manifest vars in memory
  POST /updates/conxa-app-manifest  — update app manifest vars in memory
"""

import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from conxa_core.config import settings
from conxa_core.db import db_get, db_set
from app.api.manifest_signer import load_signing_key, sign_manifest

router = APIRouter(tags=["updates"])
logger = logging.getLogger(__name__)

_ADMIN_TOKEN = os.environ.get("CONXA_ADMIN_TOKEN", "")

# ── Unified signed manifest — KV-backed (conxa_core.db dual store: Postgres in prod, ──
# ── filesystem in local dev). Replaces the module-level globals below as the source ──
# ── of truth; those globals remain only as *defaults* the KV falls back to until the ──
# ── first component version is ever published. ────────────────────────────────────
_COMPONENT_VERSIONS_NS = "component_versions"
_MANIFEST_NS = "manifest"

# ── Update channels ────────────────────────────────────────────────────────────
# Dev builds (prerelease tags) publish to the "dev" channel; promoted, fully tested
# releases live on "stable". A runtime/Studio fetches its own channel via ?channel=,
# so a dev install can never pull a stable artifact and a prod install can never pull
# an untested dev prerelease.
#
# BACKWARD-COMPAT: "stable" maps to the ORIGINAL unsuffixed KV namespaces, so all
# already-published stable data and the default (no ?channel=) behavior are
# byte-identical to before this change. Only "dev" (and any future channel) gets a
# ":<channel>"-suffixed namespace — purely additive.
_STABLE_CHANNEL = "stable"
_KNOWN_CHANNELS = {"stable", "dev"}


def _normalize_channel(raw: str | None) -> str:
    ch = str(raw or "").strip().lower()
    if ch in ("", "prod", "production", "stable"):
        return "stable"
    if ch in ("dev", "development"):
        return "dev"
    if ch in _KNOWN_CHANNELS:
        return ch
    raise HTTPException(status_code=400, detail=f"unknown channel: {raw!r}")


def _ns(base: str, channel: str) -> str:
    """KV namespace for a channel. Stable keeps the legacy unsuffixed name."""
    return base if channel == _STABLE_CHANNEL else f"{base}:{channel}"

# ── Defaults baked in (CI overrides via env) ──────────────────────────────────

_NSIS_VERSION = os.environ.get("CONXA_NSIS_VERSION", "3.10")
_NSIS_URL = os.environ.get(
    "CONXA_NSIS_URL",
    f"https://downloads.sourceforge.net/project/nsis/NSIS%203/{_NSIS_VERSION}/nsis-{_NSIS_VERSION}.zip",
)
_NSIS_SHA256 = os.environ.get("CONXA_NSIS_SHA256", "")

# The actual repo hosting release artifacts. Override via env on Render once a
# dedicated org/repo is established. Default points at the monorepo where CI
# publishes runtime and studio releases.
_GITHUB_REPO = os.environ.get("CONXA_GITHUB_REPO", "cannonboldoff-hue/CONXA")


def _release_url(version: str, filename: str) -> str:
    return f"https://github.com/{_GITHUB_REPO}/releases/download/{version}/{filename}"


# Host layer (large binary, quarterly)
_HOST_VERSION = os.environ.get("CONXA_HOST_VERSION", "host-v1.0.0")
_HOST_WIN_SHA256 = os.environ.get("CONXA_HOST_WIN_SHA256", "")
_RUNTIME_KEYTAR_SHA256 = os.environ.get("CONXA_KEYTAR_WIN_SHA256", "")

# App layer (small zip, every release)
_APP_VERSION = os.environ.get("CONXA_APP_VERSION", "app-v1.0.0")
_APP_MIN_HOST = os.environ.get("CONXA_APP_MIN_HOST", "host-v1.0.0")
_APP_BUNDLE_SHA = os.environ.get("CONXA_APP_BUNDLE_SHA256", "")

_PLAYWRIGHT_VERSION = os.environ.get("CONXA_PLAYWRIGHT_VERSION", "1.61.0")
_CHROMIUM_REVISION = os.environ.get("CONXA_CHROMIUM_REVISION", "1228")

_STUDIO_VERSION = os.environ.get("CONXA_STUDIO_VERSION", "studio-v1.0.0")
_STUDIO_BARE_VERSION = re.sub(r"^studio-v", "", _STUDIO_VERSION).lstrip("v")
_STUDIO_WIN_URL = os.environ.get(
    "CONXA_STUDIO_WIN_URL",
    f"https://github.com/{_GITHUB_REPO}/releases/download/{_STUDIO_VERSION}/Conxa-Build-Studio-Setup-{_STUDIO_BARE_VERSION}.exe",
)
_STUDIO_WIN_SHA256 = os.environ.get("CONXA_STUDIO_WIN_SHA256", "")
_STUDIO_WIN_SHA512 = os.environ.get("CONXA_STUDIO_WIN_SHA512", "")
# Proxied by /updates/studio/latest.yml so electron-updater gets the full
# electron-builder YAML (including blockMapSize) for differential download.
# Override only when hosting artifacts outside GitHub Releases.
_STUDIO_LATEST_YML_URL = os.environ.get(
    "CONXA_STUDIO_LATEST_YML_URL",
    f"https://github.com/{_GITHUB_REPO}/releases/download/{_STUDIO_VERSION}/latest.yml",
)

_EXECUTE_VERSION = os.environ.get("CONXA_EXECUTE_VERSION", "execute-v0.1.0")
_EXECUTE_BARE_VERSION = re.sub(r"^execute-v", "", _EXECUTE_VERSION).lstrip("v")
_EXECUTE_WIN_URL = os.environ.get(
    "CONXA_EXECUTE_WIN_URL",
    f"https://github.com/{_GITHUB_REPO}/releases/download/{_EXECUTE_VERSION}/Conxa-Execute-Setup-{_EXECUTE_BARE_VERSION}.exe",
)
_EXECUTE_WIN_SHA256 = os.environ.get("CONXA_EXECUTE_WIN_SHA256", "")
_EXECUTE_WIN_SHA512 = os.environ.get("CONXA_EXECUTE_WIN_SHA512", "")
_EXECUTE_LATEST_YML_URL = os.environ.get(
    "CONXA_EXECUTE_LATEST_YML_URL",
    f"https://github.com/{_GITHUB_REPO}/releases/download/{_EXECUTE_VERSION}/latest.yml",
)

_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]+$")
# 32 bytes (sha256) or 64 bytes (sha512) of hex — anything else is not a digest.
_VALID_HEX_LENGTHS = {64, 128}


def _validate_digest_env_vars() -> None:
    """Fail fast at import time on a malformed release-artifact digest.

    A CI workflow that prints "<filename>  <hash>" (the two-column line GitHub's release
    notes use) into one of these env vars — instead of the bare hash — used to be served
    to every client verbatim. bootstrap.py/the runtime's own updater then reject the
    download on a checksum mismatch forever, with no signal pointing back at the env var.
    Catch the shape here, at the source, instead of downstream in N different clients.
    """
    digest_env_names = [
        "CONXA_NSIS_SHA256", "CONXA_HOST_WIN_SHA256", "CONXA_KEYTAR_WIN_SHA256",
        "CONXA_APP_BUNDLE_SHA256", "CONXA_STUDIO_WIN_SHA256", "CONXA_STUDIO_WIN_SHA512",
        "CONXA_EXECUTE_WIN_SHA256", "CONXA_EXECUTE_WIN_SHA512",
    ]
    bad: list[str] = []
    for name in digest_env_names:
        value = os.environ.get(name, "").strip()
        if not value:
            continue  # unset is allowed — the dependent manifest entry is simply skipped
        normalized = value.lower()
        if len(normalized) not in _VALID_HEX_LENGTHS or not _HEX_DIGEST_RE.match(normalized):
            bad.append(f"{name}={value!r} (expected a bare 64- or 128-char hex digest)")
    if bad:
        raise RuntimeError(
            "Refusing to start: malformed digest env var(s) — "
            + "; ".join(bad)
        )


_validate_digest_env_vars()


@router.get("/updates/deps-manifest", include_in_schema=False)
def deps_manifest() -> dict:
    """
    Build Studio bootstrap.py fetches this on every startup to check for
    dependency updates. Public — called before the user logs in.

    Returns both legacy top-level keys (for pre-v2 Build Studio installs) and
    a generic ``deps`` dict that the v2 update loop iterates over. Adding a new
    dep only requires updating cloud env vars and the ``deps`` dict below —
    no Build Studio release needed as long as the dep's action is a known type
    ("copy" or "extract_zip").
    """
    return {
        "manifest_version": 2,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deps": {
            "nsis": {
                "version": _NSIS_VERSION,
                "files": [
                    {
                        "filename": "nsis.zip",
                        "url": _NSIS_URL,
                        "sha256": _NSIS_SHA256,
                        "action": "extract_zip",
                    }
                ],
            },
            "conxa-runtime": {
                "version": _HOST_VERSION,
                "files": [
                    {
                        "filename": "conxa-runtime.exe",
                        "url": _release_url(_HOST_VERSION, "conxa-runtime.exe"),
                        "sha256": _HOST_WIN_SHA256,
                        "action": "copy",
                    },
                    {
                        "filename": "keytar.node",
                        "url": _release_url(_HOST_VERSION, "keytar.node"),
                        "sha256": _RUNTIME_KEYTAR_SHA256,
                        "action": "copy",
                    },
                ],
            },
            "conxa-app": {
                "version": _APP_VERSION,
                "files": [
                    {
                        "filename": f"conxa-app-{_APP_VERSION}.zip",
                        "url": _release_url(_APP_VERSION, f"conxa-app-{_APP_VERSION}.zip"),
                        "sha256": _APP_BUNDLE_SHA,
                        "action": "extract_zip",
                    }
                ],
            },
        },
    }


def _latest_yml(latest_yml_url: str, win_url: str, sha512: str, bare_version: str) -> Response:
    """
    electron-updater generic-provider manifest for a desktop app (Studio, Execute).

    When latest_yml_url is set, proxies the real electron-builder generated latest.yml
    from GitHub Releases, rewriting the relative files[].url to an absolute URL so
    electron-updater fetches the .exe (and .blockmap) directly from GitHub. The full
    YAML — including size and blockMapSize — comes through unchanged, which is what
    engages blockmap-differential download.

    Falls back to a minimal hand-crafted YAML if the env var is not set.
    """
    import urllib.request as _urllib_request

    if latest_yml_url:
        try:
            with _urllib_request.urlopen(latest_yml_url, timeout=8) as r:
                content = r.read().decode()
            # Rewrite relative files[].url to absolute GitHub URL.
            # electron-builder emits: "  - url: Conxa Build Studio-Setup-1.0.0.exe"
            # electron-updater then auto-fetches <absolute_url>.blockmap for differential.
            base = latest_yml_url.rsplit("/", 1)[0]
            content = re.sub(
                r"(^\s*-\s*url:\s*)(.+\.exe)",
                lambda m: m.group(1) + base + "/" + m.group(2).strip(),
                content,
                flags=re.MULTILINE,
            )
            return Response(content, media_type="text/yaml")
        except Exception:
            # Falls through to the hand-crafted YAML below, which has no
            # blockMapSize — every user gets a full re-download instead of a
            # differential patch until this is noticed and fixed.
            logger.warning(
                "latest_yml_fetch_failed url=%s — falling back to full-download manifest",
                latest_yml_url,
                exc_info=True,
            )

    # Fallback: minimal YAML (no blockMapSize → full download, not differential).
    filename = unquote(win_url.split("/")[-1])
    lines = [
        f"version: {bare_version}",
        "files:",
        f"  - url: {win_url}",
    ]
    if sha512:
        lines.append(f"    sha512: {sha512}")
    lines += [
        f"path: {filename}",
        f"releaseDate: '{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')}'",
    ]
    return Response("\n".join(lines) + "\n", media_type="text/yaml")


@router.get("/updates/studio/latest.yml", include_in_schema=False)
def studio_latest_yml() -> Response:
    return _latest_yml(_STUDIO_LATEST_YML_URL, _STUDIO_WIN_URL, _STUDIO_WIN_SHA512, _STUDIO_BARE_VERSION)


@router.get("/updates/studio-manifest", include_in_schema=False)
def studio_manifest() -> dict:
    """
    Fetched by the web frontend to surface the Build Studio download link.
    Public — called without authentication.
    Set CONXA_STUDIO_WIN_URL on Render once the installer is published.
    """
    return {
        "version": _STUDIO_VERSION,
        "win_url": _STUDIO_WIN_URL,
        "win_sha256": _STUDIO_WIN_SHA256,
    }


@router.get("/updates/execute/latest.yml", include_in_schema=False)
def execute_latest_yml() -> Response:
    return _latest_yml(_EXECUTE_LATEST_YML_URL, _EXECUTE_WIN_URL, _EXECUTE_WIN_SHA512, _EXECUTE_BARE_VERSION)


@router.get("/updates/execute-manifest", include_in_schema=False)
def execute_manifest() -> dict:
    """
    Fetched by the web frontend to surface the Conxa Execute download link.
    Public — called without authentication.
    Set CONXA_EXECUTE_WIN_URL on Render once the installer is published.
    """
    return {
        "version": _EXECUTE_VERSION,
        "win_url": _EXECUTE_WIN_URL,
        "win_sha256": _EXECUTE_WIN_SHA256,
    }


def _component(name: str, channel: str = _STABLE_CHANNEL) -> dict | None:
    """Read a component_versions KV record for a channel, or None if never published."""
    return db_get(_ns(_COMPONENT_VERSIONS_NS, channel), name)


def _file_sha256(component: dict, filename: str) -> str:
    for f in component.get("files", []):
        if f.get("filename") == filename:
            return f.get("sha256", "")
    return ""


def _file_url(component: dict, filename: str) -> str:
    for f in component.get("files", []):
        if f.get("filename") == filename:
            return f.get("url", "")
    return ""


@router.get("/updates/conxa-runtime-manifest", include_in_schema=False)
def runtime_host_manifest() -> dict:
    """
    Deprecated in favour of GET /api/v1/manifest.json (unified, signed). Kept as a
    thin shim, now reading from the same KV-backed component_versions store instead
    of process-local globals, for runtimes that haven't picked up the manifest-driven
    self-updater yet (see runtime/manifest_manager.js). docs/TRD.md §3.3.
    """
    c = _component("conxa_runtime")
    if c is None:
        return {
            "host_version": _HOST_VERSION,
            "url": _release_url(_HOST_VERSION, "conxa-runtime.exe"),
            "sha256": _HOST_WIN_SHA256,
            "keytar_url": _release_url(_HOST_VERSION, "keytar.node"),
            "keytar_sha256": _RUNTIME_KEYTAR_SHA256,
            "playwright_version": _PLAYWRIGHT_VERSION,
            "chromium_revision": _CHROMIUM_REVISION,
        }
    version = c.get("version", _HOST_VERSION)
    return {
        "host_version": version,
        "url": _file_url(c, "conxa-runtime.exe") or _release_url(version, "conxa-runtime.exe"),
        "sha256": _file_sha256(c, "conxa-runtime.exe"),
        "keytar_url": _file_url(c, "keytar.node") or _release_url(version, "keytar.node"),
        "keytar_sha256": _file_sha256(c, "keytar.node"),
        "playwright_version": c.get("playwright_version", _PLAYWRIGHT_VERSION),
        "chromium_revision": c.get("chromium_revision", _CHROMIUM_REVISION),
    }


@router.get("/updates/conxa-app-manifest", include_in_schema=False)
def runtime_app_manifest() -> dict:
    """
    Deprecated in favour of GET /api/v1/manifest.json (unified, signed). Kept as a
    thin shim reading from the same KV-backed component_versions store. docs/TRD.md §3.3.
    """
    c = _component("conxa_app")
    if c is None:
        return {
            "app_version": _APP_VERSION,
            "min_host": _APP_MIN_HOST,
            "bundle_url": _release_url(_APP_VERSION, f"conxa-app-{_APP_VERSION}.zip"),
            "bundle_sha256": _APP_BUNDLE_SHA,
        }
    version = c.get("version", _APP_VERSION)
    filename = f"conxa-app-{version}.zip"
    return {
        "app_version": version,
        "min_host": c.get("min_host", _APP_MIN_HOST),
        "bundle_url": _file_url(c, filename) or _release_url(version, filename),
        "bundle_sha256": _file_sha256(c, filename),
    }


def _proxy_app_urls(app: dict) -> dict:
    """Point the app zip at our own /updates/artifact route. Runtimes already in the field
    (host-v3.2.1) bake in a downloader that treats GitHub's 302 as an error, so a GitHub
    release URL can never be self-updated from; a direct 200 from here can. No-op without
    SKILL_API_BASE_URL (local dev keeps the GitHub URL)."""
    base = (settings.api_base_url or "").rstrip("/")
    if not base:
        return app
    files = [
        {**f, "url": f"{base}/api/v1/updates/artifact/{app['version']}/{f['filename']}"}
        if f.get("filename", "").endswith(".zip") and "github.com/" in f.get("url", "")
        else f
        for f in app.get("files", [])
    ]
    return {**app, "files": files}


_APP_TAG_RE = re.compile(r"^app-v\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$")
_APP_ZIP_RE = re.compile(r"^conxa-app-app-v\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?\.zip$")


@router.get("/updates/artifact/{tag}/{filename}", include_in_schema=False)
def app_artifact(tag: str, filename: str) -> Response:
    """Serve an app-layer release zip straight from GitHub with a direct 200. Allow-listed
    to app zips only (fixed repo, strict tag/filename patterns) so it can't be used as an
    open proxy; integrity is still the manifest's signed SHA-256, checked by the runtime."""
    if not _APP_TAG_RE.match(tag) or not _APP_ZIP_RE.match(filename):
        raise HTTPException(status_code=404, detail="not found")
    try:
        r = httpx.get(_release_url(tag, filename), follow_redirects=True, timeout=30)
    except httpx.HTTPError as e:
        logger.warning("app artifact fetch failed: %s", e)
        raise HTTPException(status_code=502, detail="upstream unavailable")
    if r.status_code != 200:
        raise HTTPException(status_code=404 if r.status_code == 404 else 502, detail="upstream error")
    return Response(content=r.content, media_type="application/zip")


def _require_admin(authorization: str = Header(default="")) -> None:
    if not _ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != _ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _compose_manifest(channel: str = _STABLE_CHANNEL) -> dict:
    """Assemble the unified manifest for a channel from its component_versions KV
    records, sign it, and persist the result to that channel's `manifest` KV
    namespace. Called after every admin component-versions POST so GET
    /manifest.json is always a cheap KV read with no signing on the read path.

    The Ed25519 signing key is the SAME for every channel — the runtime's baked-in
    public key verifies dev and stable manifests identically; only the channel's set
    of published versions differs."""
    manifest_ns = _ns(_MANIFEST_NS, channel)
    host = _component("conxa_runtime", channel) or {
        "version": _HOST_VERSION,
        "released_at": datetime.now(timezone.utc).isoformat(),
        "files": [
            {"filename": "conxa-runtime.exe", "url": _release_url(_HOST_VERSION, "conxa-runtime.exe"), "sha256": _HOST_WIN_SHA256},
            {"filename": "keytar.node", "url": _release_url(_HOST_VERSION, "keytar.node"), "sha256": _RUNTIME_KEYTAR_SHA256},
        ],
    }
    app = _component("conxa_app", channel) or {
        "version": _APP_VERSION,
        "released_at": datetime.now(timezone.utc).isoformat(),
        "min_host": _APP_MIN_HOST,
        "files": [
            {"filename": f"conxa-app-{_APP_VERSION}.zip", "url": _release_url(_APP_VERSION, f"conxa-app-{_APP_VERSION}.zip"), "sha256": _APP_BUNDLE_SHA},
        ],
    }

    # db_list_kv's filesystem fallback (no SKILL_DATABASE_URL) returns a hashed
    # filename stem instead of the original key, so "company:slug" can't be
    # recovered by parsing db_list_kv's key column portably. Instead we maintain
    # our own index of published skill identifiers (updated in
    # update_component_version) and look each one up directly by its known key —
    # this works identically against Postgres and the filesystem fallback.
    skill_packs: dict[str, dict[str, dict]] = {}
    for identifier in (db_get(manifest_ns, "skill_pack_index") or []):
        company, _, slug = identifier.partition(":")
        record = _component(f"skill_packs:{company}:{slug}", channel)
        if record is not None:
            skill_packs.setdefault(company, {})[slug] = record

    manifest = {
        "manifest_version": 3,
        "channel": channel,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mcp_protocol_version": "2024-11-05",
        "minimum_versions": db_get(manifest_ns, "minimum_versions") or {},
        "compatibility": db_get(manifest_ns, "compatibility") or {},
        "conxa_runtime": host,
        "conxa_app": _proxy_app_urls(app),
        "skill_packs": skill_packs,
        "signature": "",
    }

    signing_key = load_signing_key()
    if signing_key is not None:
        manifest["signature"] = sign_manifest(manifest, signing_key)

    db_set(manifest_ns, "current", manifest)
    return manifest


@router.get("/manifest.json", include_in_schema=False)
def unified_manifest(channel: str | None = None) -> dict:
    """
    Single signed manifest for the runtime's self-updater (runtime/manifest_manager.js):
    component versions, compatibility matrix, minimum versions, rollout, checksums, and
    an Ed25519 signature over the whole thing. Public — served straight from KV, no
    signing on the read path (signing happens once, at publish time, in _compose_manifest).

    `?channel=dev|stable` selects the update channel. Omitted → stable (the promoted,
    fully tested lane), so existing prod runtimes are unaffected.
    """
    ch = _normalize_channel(channel)
    cached = db_get(_ns(_MANIFEST_NS, ch), "current")
    # A manifest composed before _proxy_app_urls existed still carries the GitHub URL —
    # recompose (and re-sign) once instead of waiting for the next publish.
    if cached is not None and _proxy_app_urls(cached["conxa_app"]) == cached["conxa_app"]:
        return cached
    return _compose_manifest(ch)


@router.post("/admin/component-versions/{component}", include_in_schema=False)
def update_component_version(
    component: str,
    body: dict,
    channel: str | None = None,
    authorization: str = Header(default=""),
) -> dict:
    """
    CI (after each host/app build) and publish_routes.py (after each skill-pack
    publish) call this instead of the old per-endpoint POSTs. Persists the given
    component's version record to the channel's KV, then recomposes and re-signs
    that channel's manifest so GET /manifest.json?channel=<channel> reflects it
    immediately.

    `component` is one of: "conxa_runtime", "conxa_app", or "skill_packs:{company}:{skill}".
    `?channel=dev|stable` selects the target channel (default stable). Dev builds
    (prerelease tags) publish to dev; the promotion workflow re-publishes the same
    signed record to stable.
    """
    _require_admin(authorization)
    if not re.match(r"^[a-zA-Z0-9_.-]+(:[a-zA-Z0-9_.-]+){0,2}$", component):
        raise HTTPException(status_code=400, detail="invalid component name")

    ch = _normalize_channel(channel)
    db_set(_ns(_COMPONENT_VERSIONS_NS, ch), component, body)

    if component.startswith("skill_packs:"):
        _, company, slug = component.split(":", 2)
        identifier = f"{company}:{slug}"
        manifest_ns = _ns(_MANIFEST_NS, ch)
        index = db_get(manifest_ns, "skill_pack_index") or []
        if identifier not in index:
            index.append(identifier)
            db_set(manifest_ns, "skill_pack_index", index)

    manifest = _compose_manifest(ch)
    return {
        "ok": True,
        "channel": ch,
        "manifest_version": manifest["manifest_version"],
        "generated_at": manifest["generated_at"],
    }
