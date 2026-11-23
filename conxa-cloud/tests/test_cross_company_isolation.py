"""Two companies, one deployment (SG-28): a company id taken from a URL or body is never
trusted for a write. Every route fixed in the 2026-11-23 sweep is hit as company A against
company B's data; each must refuse (403/404) and leave B's data exactly as it was."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.services.jobs import job_store
from app.services.saas import upsert_billing
from conxa_core.config import settings
from conxa_core.db import db_get, db_set

STUDIO = {"X-Conxa-Client": settings.llm_proxy_client_header}
# A's id is mixed case on purpose: Clerk ids are, and the slug (org_a) differs from the id.
CLAIMS = {
    "tok_a": {"sub": "user_a", "org_id": "org_A", "org_role": "org:admin"},
    "tok_b": {"sub": "user_b", "org_id": "org_b", "org_role": "org:admin"},
    "tok_b_member": {"sub": "user_b2", "org_id": "org_b", "org_role": "org:member"},
}


def _auth(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "entitlements_enforce_machines", False)

    def fake_verify(token: str) -> dict:
        if token not in CLAIMS:
            raise HTTPException(status_code=401, detail="invalid_clerk_token")
        return CLAIMS[token]

    upsert_billing("org_A", {"plan": "pro"})
    upsert_billing("org_b", {"plan": "pro"})
    with patch("app.api.security.verify_clerk_jwt", fake_verify):
        c = TestClient(app)  # no lifespan: its production-config startup check needs the full env
        yield c


def _installer(api, tok, path, version, body=b"MZ"):
    return api.post(f"{path}?version={version}&release_notes=x", content=body, headers=_auth(tok))


def test_generic_job_routes_are_gone_and_b_still_reads_its_own_video_job(api):
    job = job_store.create("video_analyze", resource_id="org_b")
    job_store.mark_succeeded(job.job_id, {"segments": [{"visible_target": "SECRET screen text"}]})
    job_store.get(job.job_id).resource_id = "org_b"  # mark_succeeded only rewrites it for a skill_id result
    job_id = job.job_id

    for method, path in (
        ("GET", "/api/v1/jobs"),
        ("GET", f"/api/v1/jobs/{job_id}"),
        ("GET", f"/api/v1/jobs/{job_id}/events"),
        ("POST", f"/api/v1/jobs/{job_id}/cancel"),
    ):
        res = api.request(method, path, headers=_auth("tok_a"))
        assert res.status_code in (404, 405), (path, res.status_code)
        assert "SECRET" not in res.text
    # A cannot read it through the video route either; B still can.
    assert api.get(f"/api/v1/video/analyze/{job_id}", headers={**STUDIO, **_auth("tok_a")}).status_code == 404
    mine = api.get(f"/api/v1/video/analyze/{job_id}", headers={**STUDIO, **_auth("tok_b")})
    assert mine.status_code == 200 and mine.json()["status"] == "succeeded"
    assert job_store.get(job_id).status == "succeeded"


def test_installer_upload_needs_own_slug_and_never_touches_b(api):
    b_up = _installer(api, "tok_b", "/api/v1/workflows/installer/upload", "1.0.0", b"MZ-B-bytes")
    assert b_up.status_code == 200, b_up.text
    assert b_up.json()["slug"] == "org_b"

    # Legacy route with B's slug: refused, B's installer and version list unchanged.
    res = _installer(api, "tok_a", "/api/v1/workflows/org_b/installer/upload", "9.9.9", b"MZ-A-evil")
    assert res.status_code == 403, res.text
    assert res.json()["detail"] == "workspace_forbidden"
    assert api.get("/api/v1/installers/org_b").content == b"MZ-B-bytes"
    b_versions = api.get("/api/v1/workflows/installer/versions", headers=_auth("tok_b")).json()["versions"]
    assert [v["version"] for v in b_versions] == ["1.0.0"]
    # A cannot list B's versions through the legacy route either.
    assert api.get("/api/v1/workflows/org_b/installer/versions", headers=_auth("tok_a")).status_code == 403

    # A generation value ("v2", what shipped Studios send) means "my own slug", never a shared slot.
    a_up = _installer(api, "tok_a", "/api/v1/workflows/v2/installer/upload", "1.0.0", b"MZ-A-bytes")
    assert a_up.status_code == 200, a_up.text
    assert a_up.json()["slug"] == "org_a"
    assert api.get("/api/v1/installers/org_a").content == b"MZ-A-bytes"
    assert api.get("/api/v1/installers/v2").status_code == 404
    assert api.get("/api/v1/installers/org_b").content == b"MZ-B-bytes"


def test_installer_upload_is_admin_only_even_in_own_workspace(api):
    res = _installer(api, "tok_b_member", "/api/v1/workflows/org_b/installer/upload", "1.0.0")
    assert res.status_code == 403
    assert res.json()["detail"] == "admin_role_required"
    assert api.get("/api/v1/installers/org_b").status_code == 404


def test_policy_put_for_another_company_is_refused_and_own_is_stored_by_slug(api):
    db_set("tracking_tokens", "org_b", {"token": "track_b"})
    db_set("tracking_tokens", "org_a", {"token": "track_a"})

    def track(t: str) -> dict[str, str]:
        return {"X-Tracking-Token": t}

    b_put = api.put("/api/v1/tracking/org_b/policy", json={"denied_hosts": ["b.example"]}, headers=_auth("tok_b"))
    assert b_put.status_code == 200, b_put.text
    before = api.get("/api/v1/tracking/org_b/policy", headers=track("track_b"))
    assert before.status_code == 200  # no bearer needed: the runtime sends only the tracking token

    evil = api.put(
        "/api/v1/tracking/org_b/policy", json={"denied_hosts": ["evil.example"], "enforce": True}, headers=_auth("tok_a")
    )
    assert evil.status_code == 403
    assert evil.json()["detail"] == "workspace_forbidden"
    after = api.get("/api/v1/tracking/org_b/policy", headers=track("track_b")).json()
    assert after["policy_version"] == before.json()["policy_version"]
    assert after["denied_hosts"] == ["b.example"] and not after.get("enforce")

    # A writing under its own mixed-case id lands under the slug, where the runtime reads it.
    a_put = api.put("/api/v1/tracking/org_A/policy", json={"denied_hosts": ["a.example"]}, headers=_auth("tok_a"))
    assert a_put.status_code == 200, a_put.text
    assert api.get("/api/v1/tracking/org_a/policy", headers=track("track_a")).json()["denied_hosts"] == ["a.example"]

    # The read stays token-guarded: wrong token, and another company's token, are refused.
    wrong = api.get("/api/v1/tracking/org_b/policy", headers=track("wrong"))
    assert (wrong.status_code, wrong.json()["detail"]) == (401, "invalid_tracking_token")
    assert api.get("/api/v1/tracking/org_b/policy", headers=track("track_a")).status_code == 401


def test_subscription_verify_refuses_another_companys_subscription_and_unpaid_own(api):
    upsert_billing("org_A", {"plan": "starter"})
    db_set("cashfree_sub_workspace", "subref_b", {"workspace_id": "org_b", "tier": "pro"})
    db_set("cashfree_sub_workspace", "subref_a_unpaid", {"workspace_id": "org_A", "tier": "pro"})

    class _Resp:
        status_code = 200
        content = b"{}"
        text = "{}"
        status = "ACTIVE"

        def json(self):
            return {"subscription": {"status": self.status, "planId": "plan_pro"}}

    resp = _Resp()
    with patch.object(settings, "cashfree_app_id", "cf_app"), patch.object(settings, "cashfree_pro_plan_id", "plan_pro"), \
            patch("app.api.cashfree_routes._cf_request", return_value=resp) as cf:
        theirs = api.post("/api/v1/subscriptions/verify", json={"subscription_id": "subref_b"}, headers=_auth("tok_a"))
        assert theirs.status_code == 403
        assert theirs.json()["detail"] == "subscription_forbidden"
        cf.assert_not_called()  # refused before Cashfree is even asked

        resp.status = "INITIALIZED"
        unpaid = api.post("/api/v1/subscriptions/verify", json={"subscription_id": "subref_a_unpaid"}, headers=_auth("tok_a"))
        assert unpaid.status_code == 400
        assert unpaid.json()["detail"] == "subscription_not_active"
    assert api.get("/api/v1/billing/subscription", headers=_auth("tok_a")).json()["subscription"]["plan"] == "starter"


def test_cashfree_webhooks_reach_their_signature_check_without_a_bearer(api):
    # Public path (no 401 from the sign-in gate), then fail closed: no secret -> 503, bad signature -> 400.
    with patch.object(settings, "cashfree_webhook_secret", ""):
        for path in ("webhooks/cashfree", "webhooks/cashfree-orders"):
            res = api.post(f"/api/v1/subscriptions/{path}", json={"cf_event": "x"})
            assert (res.status_code, res.json()["detail"]) == (503, "webhook_not_configured"), path
    with patch.object(settings, "cashfree_webhook_secret", "whsec"):
        for path in ("webhooks/cashfree", "webhooks/cashfree-orders"):
            res = api.post(f"/api/v1/subscriptions/{path}", json={"cf_event": "x"})
            assert (res.status_code, res.json()["detail"]) == (400, "invalid_signature"), path
