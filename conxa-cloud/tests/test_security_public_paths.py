"""_is_public_path must exempt the versioned runtime endpoints from the Clerk
gate without opening up workflow_routes.py's Clerk-protected dashboard endpoints
(including its skill-pack list/detail views), which share the same
/api/v1/workflows/ path segment."""

from __future__ import annotations

from app.api.security import _body_limit_for_path, _is_public_path
from conxa_core.config import settings


def test_unified_manifest_is_public():
    assert _is_public_path("/api/v1/manifest.json", "GET")


def test_versioned_skill_pack_delta_is_public():
    assert _is_public_path("/api/v1/workflows/v2/render/skill-packs/delta", "GET")


def test_versioned_tracking_events_is_public():
    assert _is_public_path("/api/v1/workflows/v2/render/tracking/events", "POST")


def test_legacy_skill_pack_delta_still_public():
    assert _is_public_path("/api/v1/skill-packs/render/delta", "GET")


def test_readyz_is_public():
    """Regression: /readyz was missing from PUBLIC_PATHS, so Render's deploy
    gate (Dockerfile: "readiness is gated by /readyz") would 401 against the
    Clerk-auth middleware in production instead of ever reaching the actual
    DB-readiness check."""
    assert _is_public_path("/readyz", "GET")


def test_workflow_dashboard_routes_stay_protected():
    assert not _is_public_path("/api/v1/workflows", "GET")
    assert not _is_public_path("/api/v1/workflows", "POST")
    assert not _is_public_path("/api/v1/workflows/some-workflow-id", "GET")
    assert not _is_public_path("/api/v1/workflows/some-workflow-id", "DELETE")


def test_skill_pack_dashboard_list_and_detail_stay_protected():
    assert not _is_public_path("/api/v1/workflows/skill-packs", "GET")
    assert not _is_public_path("/api/v1/workflows/skill-packs/render", "GET")


def test_versioned_skill_pack_delta_wrong_method_not_public():
    assert not _is_public_path("/api/v1/workflows/v2/render/skill-packs/delta", "POST")


def test_versioned_tracking_events_wrong_method_not_public():
    assert not _is_public_path("/api/v1/workflows/v2/render/tracking/events", "GET")


def test_skill_pack_upload_gets_build_artifact_body_limit():
    # Skill packs (base64-encoded screenshots/DOM snapshots) routinely exceed the
    # generic 1MB JSON body cap — this path must use the 250MB build-artifact cap,
    # not fall through to max_json_body_bytes.
    path = "/api/v1/workflows/v2/render/skill-packs/upload"
    assert _body_limit_for_path(path) == settings.build_artifact_upload_max_bytes
    assert _body_limit_for_path(path) != settings.max_json_body_bytes


def test_governance_policy_read_is_public_but_write_is_not():
    # The runtime fetches its policy with only X-Tracking-Token (the handler checks it).
    assert _is_public_path("/api/v1/tracking/some_ws/policy", "GET")
    assert not _is_public_path("/api/v1/tracking/some_ws/policy", "PUT")
    assert not _is_public_path("/api/v1/tracking/some_ws/runs", "GET")


def test_cashfree_webhooks_are_public_post_paths():
    # Cashfree cannot send a Clerk token; the handlers verify the signature themselves.
    assert _is_public_path("/api/v1/subscriptions/webhooks/cashfree", "POST")
    assert _is_public_path("/api/v1/subscriptions/webhooks/cashfree-orders", "POST")
    assert not _is_public_path("/api/v1/subscriptions/verify", "POST")
