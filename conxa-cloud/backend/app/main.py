"""FastAPI entrypoint for the thin Conxa cloud SaaS.

The cloud serves the metered LLM proxy, auth, billing, dashboard, workflow/installer
hosting, runtime sync/update manifests, and telemetry. Recording, compiling, and
building all happen locally in the Build Studio.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from conxa_core.config import settings
from conxa_core.db import healthcheck, init_db, using_database
from conxa_core.storage.selector_cache import cleanup_expired_entries
from conxa_core.storage.snapshots_gc import cleanup_old_snapshots

from app.api.bug_report_routes import router as bug_report_router
from app.api.byok_routes import router as byok_router
from app.api.entitlement_routes import router as entitlement_router
from app.api.errors import http_error_handler, unhandled_error_handler
from app.api.app_graph_routes import router as app_graph_router
from app.api.job_routes import router as job_router
from app.api.legal_routes import router as legal_router
from app.api.llm_proxy_routes import router as llm_proxy_router
from app.api.workflow_routes import router as workflow_router
from app.api.product_routes import router as product_router
from app.api.publish_routes import admin_router as publish_admin_router, installers_router, router as publish_router
from app.api.release_routes import router as release_router
from app.api.cashfree_routes import router as cashfree_router
from app.api.security import ProductionRequestMiddleware
from app.api.skillpack_update_routes import router as skillpack_update_router
from app.api.skillpack_update_routes import telemetry_router as skillpack_telemetry_router
from app.api.skillpack_update_routes import versioned_router as skillpack_update_versioned_router
from app.api.tracking_routes import public_router as public_tracking_router
from app.api.tracking_routes import router as tracking_router
from app.api.tracking_routes import versioned_router as tracking_versioned_router
from app.api.updates_routes import router as updates_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _validate_production_config() -> None:
    """Fail fast in production rather than booting a half-configured service.

    When ``SKILL_AUTH_REQUIRED`` is true we are running as the public cloud and
    must have a real database, Clerk auth, explicit CORS origins, and Cashfree
    billing configured. (Provider keys are enforced separately by the Settings
    validator.) The filesystem DB fallback must never silently activate here.
    """
    if not settings.auth_required:
        return
    missing: list[str] = []
    if not settings.database_url:
        missing.append("SKILL_DATABASE_URL (filesystem fallback is not allowed in production)")
    if not settings.clerk_issuer:
        missing.append("SKILL_CLERK_ISSUER")
    if not settings.clerk_jwks_url:
        missing.append("SKILL_CLERK_JWKS_URL")
    if not settings.cors_origins:
        missing.append("SKILL_CORS_ORIGINS")
    if not (settings.cashfree_app_id and settings.cashfree_secret_key and settings.cashfree_webhook_secret):
        missing.append("CASHFREE_APP_ID / CASHFREE_SECRET_KEY / CASHFREE_WEBHOOK_SECRET")
    if not (settings.cashfree_starter_plan_id and settings.cashfree_pro_plan_id):
        missing.append("CASHFREE_STARTER_PLAN_ID / CASHFREE_PRO_PLAN_ID")
    if not settings.api_base_url:
        missing.append("SKILL_API_BASE_URL (set to prevent X-Forwarded-Host injection into pack.json)")
    if not settings.tracking_hmac_secret:
        missing.append("SKILL_TRACKING_HMAC_SECRET (required so telemetry ingest rejects unrecognized companies)")
    if not settings.installer_signing_key:
        missing.append("SKILL_INSTALLER_SIGNING_KEY (required to sign time-limited installer download links)")
    # Reads os.environ directly rather than settings.* — this key intentionally bypasses
    # the SKILL_-prefixed Settings object (see manifest_signer.py's header comment): it's a
    # PEM secret shared with the runtime's build-time public-key stamping, not a
    # SKILL_-namespaced setting. Without it, updates_routes.py silently serves every
    # self-update manifest with signature="", which every runtime then discards outright.
    if not os.environ.get("CONXA_MANIFEST_SIGNING_KEY"):
        missing.append("CONXA_MANIFEST_SIGNING_KEY (required so self-update manifests are verifiable)")
    if missing:
        raise RuntimeError(
            "Refusing to start: SKILL_AUTH_REQUIRED=true but these are unset: "
            + ", ".join(missing)
        )


_logger = logging.getLogger(__name__)

# Consecutive GC failure count. A single flaky pass logging the same exception
# forever at the normal level would bury the signal that it's actually stuck —
# past this many in a row, it's escalated so it can't just scroll by unnoticed.
_GC_STUCK_THRESHOLD = 5


async def _run_gc_once(consecutive_failures: int) -> int:
    """Run every background GC pass off the event loop (they do blocking IO).
    Returns the updated consecutive-failure count."""
    try:
        await asyncio.to_thread(cleanup_old_snapshots)
        await asyncio.to_thread(cleanup_expired_entries)
        return 0
    except Exception:  # noqa: BLE001
        consecutive_failures += 1
        if consecutive_failures >= _GC_STUCK_THRESHOLD:
            _logger.error(
                "Background GC pass has failed %d times in a row — it looks stuck, not transient",
                consecutive_failures,
                exc_info=True,
            )
        else:
            _logger.exception("Background GC pass failed")
        return consecutive_failures


async def _gc_loop(interval_secs: int) -> None:
    """Run GC once at startup, then every ``interval_secs``."""
    consecutive_failures = 0
    while True:
        consecutive_failures = await _run_gc_once(consecutive_failures)
        await asyncio.sleep(interval_secs)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _validate_production_config()
    init_db()
    gc_task: asyncio.Task | None = None
    interval = int(settings.gc_interval_secs)
    if interval > 0:
        gc_task = asyncio.create_task(_gc_loop(interval))
        app.state.gc_task = gc_task
    try:
        yield
    finally:
        if gc_task is not None:
            gc_task.cancel()
            try:
                await gc_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Conxa Cloud", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_preview_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ProductionRequestMiddleware)

# One error response shape for every route: {"detail", "message", "request_id"}.
# `detail` is untouched — two shipped clients (the frontend, Build Studio's
# LLMProxyClient) parse it directly — `message`/`request_id` are additive.
# See app/api/errors.py.
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

# Five of these routers (publish, release, skillpack_update_versioned,
# tracking_versioned, workflow) share the "/workflows" prefix, so their path
# templates compete in one namespace resolved by FastAPI's first-match
# registration order below — not by specificity. Two templates that shadow
# each other (e.g. two routers both declaring "/{x}/installer/upload") are a
# silent bug: the second is simply unreachable, no error at registration
# time. This already happened once (see publish_routes.py's post_installer_
# upload_v2 / get_installer_versions_v2, fixed 2026-09-17) and is exactly the
# failure mode to check for before reordering any router below or adding a
# new "/workflows/..." route. See docs/TRD.md's back-compat table for which
# of these paths are permanent and cannot be renamed to avoid the collision.
app.include_router(job_router, prefix="/api/v1")
app.include_router(app_graph_router, prefix="/api/v1")
app.include_router(byok_router, prefix="/api/v1")
app.include_router(bug_report_router, prefix="/api/v1")
app.include_router(entitlement_router, prefix="/api/v1")
app.include_router(product_router, prefix="/api/v1")
app.include_router(workflow_router, prefix="/api/v1")
app.include_router(llm_proxy_router, prefix="/api/v1")
app.include_router(publish_router, prefix="/api/v1")
app.include_router(publish_admin_router, prefix="/api/v1")
app.include_router(release_router, prefix="/api/v1")
app.include_router(installers_router, prefix="/api/v1")
app.include_router(cashfree_router, prefix="/api/v1")
app.include_router(skillpack_update_router, prefix="/api/v1")
app.include_router(skillpack_update_versioned_router, prefix="/api/v1")
app.include_router(skillpack_telemetry_router, prefix="/api/v1")
app.include_router(tracking_router, prefix="/api/v1")
app.include_router(tracking_versioned_router, prefix="/api/v1")
app.include_router(public_tracking_router)  # package-token ingest endpoint for runtimes
app.include_router(updates_router, prefix="/api/v1")
app.include_router(legal_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "conxa_cloud"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness: dependencies (DB) are reachable; used to gate deploys."""
    try:
        healthcheck()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "down", "error": str(exc)[:200]},
        )
    on_database = using_database()
    if settings.auth_required and not on_database:
        # _validate_production_config already refuses to boot without a real
        # database when auth is required, so this should be unreachable in
        # practice — but readiness reporting "ready" while running on the
        # filesystem store in production is exactly the silent-degrade this
        # refactor is about. Agree with startup instead of contradicting it.
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "filesystem", "error": "auth_required but no database configured"},
        )
    return JSONResponse(content={"status": "ready", "database": "up" if on_database else "filesystem"})
