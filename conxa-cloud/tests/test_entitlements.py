from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conxa_core.config import settings
from conxa_core.db import db_get, db_set
from app.main import app
from app.services import llm_metering
from app.services.entitlements import current_period, usage_key
from app.services.saas import Principal, upsert_billing

client = TestClient(app)
STUDIO_HEADER = {"X-Conxa-Client": settings.llm_proxy_client_header}


def _set_plan(plan: str, **limits: object) -> None:
    patch: dict[str, object] = {"plan": plan}
    if limits:
        patch["entitlement_overrides"] = limits
    upsert_billing("wrk_local", patch)


def test_basic_plan_maps_to_starter(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _set_plan("basic")

    r = client.get("/api/v1/entitlements/current")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == "starter"
    assert body["meters"]["seats"]["limit"] == 3
    assert body["meters"]["machines"]["limit"] == 3
    assert body["meters"]["compile_credits"]["limit"] == 200
    assert body["meters"]["human_edit_tokens"]["limit"] == 2_500_000
    assert body["capabilities"]["distribution"] == "external"
    assert body["capabilities"]["white_label"] is False
    assert body["capabilities"]["ops_tier"] == "basic"
    assert body["capabilities"]["vision_fallback_on_exhaustion"] is False


def test_vision_fallback_on_exhaustion_defaults_false_and_is_per_workspace_overridable(monkeypatch, tmp_path):
    """Build Studio's compiler used to only be able to read this off a local
    Windows environment variable — no cloud-side control at all, and setting it
    on the cloud backend's own env did nothing. It's now a workspace entitlement,
    same override mechanism as white_label/byok."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _set_plan("free")

    default = client.get("/api/v1/entitlements/current").json()
    assert default["capabilities"]["vision_fallback_on_exhaustion"] is False

    _set_plan("free", vision_fallback_on_exhaustion=True)
    overridden = client.get("/api/v1/entitlements/current").json()
    assert overridden["capabilities"]["vision_fallback_on_exhaustion"] is True


def test_vision_fallback_on_exhaustion_is_not_plan_gated(monkeypatch, tmp_path):
    """Deliberately not a paid-tier capability like white_label/byok — it's an
    ops reliability lever Conxa can flip for any workspace, Free included.
    Every plan defaults to False; only entitlement_overrides moves it."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    for plan in ("free", "starter", "pro", "enterprise"):
        _set_plan(plan)
        body = client.get("/api/v1/entitlements/current").json()
        assert body["capabilities"]["vision_fallback_on_exhaustion"] is False, plan

    _set_plan("free", vision_fallback_on_exhaustion=True)
    body = client.get("/api/v1/entitlements/current").json()
    assert body["capabilities"]["vision_fallback_on_exhaustion"] is True


def test_paid_usage_window_follows_razorpay_payment_date(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    upsert_billing(
        "wrk_local",
        {
            "plan": "starter",
            "current_period_end": 1893456000,
        },
    )
    db_set(
        "entitlement_usage",
        usage_key("wrk_local", "billing:1893456000"),
        {
            "workspace_id": "wrk_local",
            "period": "billing:1893456000",
            "compile_credits_used": 7,
        },
    )

    r = client.get("/api/v1/entitlements/current")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == "billing:1893456000"
    assert body["reset_at"] == "2030-01-01T00:00:00Z"
    assert body["meters"]["compile_credits"]["used"] == 7


def test_compile_reserve_commit_release_idempotency(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free")

    body = {
        "reservation_id": "cmp_test_one",
        "workflow_id": "wf_1",
        "session_id": "sess_1",
    }
    first = client.post("/api/v1/usage/compile/reserve", json=body)
    duplicate = client.post("/api/v1/usage/compile/reserve", json=body)
    commit = client.post("/api/v1/usage/compile/commit", json={"reservation_id": "cmp_test_one"})
    duplicate_commit = client.post("/api/v1/usage/compile/commit", json={"reservation_id": "cmp_test_one"})
    release_after_commit = client.post("/api/v1/usage/compile/release", json={"reservation_id": "cmp_test_one"})

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    assert first.json()["remaining_compile_credits"] == 24
    assert duplicate.json()["remaining_compile_credits"] == 24
    assert commit.status_code == 200, commit.text
    assert duplicate_commit.status_code == 200, duplicate_commit.text
    assert release_after_commit.status_code == 200, release_after_commit.text
    assert release_after_commit.json()["status"] == "committed"

    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["meters"]["compile_credits"]["used"] == 1
    assert entitlements["meters"]["compile_credits"]["remaining"] == 24


def test_compile_refund_gives_back_a_committed_credit(monkeypatch, tmp_path):
    """A compile that aborts on an infra failure (cloud proxy unavailable, vision
    anchor exhaustion) after the credit was already committed should get it back —
    otherwise a transient outage burns a paid credit for nothing."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free")

    client.post(
        "/api/v1/usage/compile/reserve",
        json={"reservation_id": "cmp_refund_one", "workflow_id": "wf_1", "session_id": "sess_1"},
    )
    commit = client.post("/api/v1/usage/compile/commit", json={"reservation_id": "cmp_refund_one"})
    assert commit.status_code == 200, commit.text

    refund = client.post("/api/v1/usage/compile/refund", json={"reservation_id": "cmp_refund_one"})
    assert refund.status_code == 200, refund.text
    assert refund.json()["status"] == "refunded"

    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["meters"]["compile_credits"]["used"] == 0
    assert entitlements["meters"]["compile_credits"]["remaining"] == 25


def test_compile_refund_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free")

    client.post(
        "/api/v1/usage/compile/reserve",
        json={"reservation_id": "cmp_refund_twice", "workflow_id": "wf_1", "session_id": "sess_1"},
    )
    client.post("/api/v1/usage/compile/commit", json={"reservation_id": "cmp_refund_twice"})

    first = client.post("/api/v1/usage/compile/refund", json={"reservation_id": "cmp_refund_twice"})
    second = client.post("/api/v1/usage/compile/refund", json={"reservation_id": "cmp_refund_twice"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "refunded"

    entitlements = client.get("/api/v1/entitlements/current").json()
    # A double refund must not credit the workspace twice.
    assert entitlements["meters"]["compile_credits"]["used"] == 0


def test_compile_refund_rejects_a_reservation_that_was_never_committed(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free")

    client.post(
        "/api/v1/usage/compile/reserve",
        json={"reservation_id": "cmp_refund_uncommitted", "workflow_id": "wf_1", "session_id": "sess_1"},
    )

    refund = client.post("/api/v1/usage/compile/refund", json={"reservation_id": "cmp_refund_uncommitted"})
    assert refund.status_code == 409, refund.text


def test_compile_reserve_blocks_last_credit_concurrently(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free", compile_credits=1)

    first = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_last_1"})
    second = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_last_2"})

    assert first.status_code == 200, first.text
    assert second.status_code == 402
    assert second.json()["detail"] == "compile_credit_limit_exceeded"


def test_expired_reservation_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free", compile_credits=1)
    period = current_period()
    db_set(
        "compile_reservations",
        "cmp_expired",
        {
            "reservation_id": "cmp_expired",
            "workspace_id": "wrk_local",
            "period": period,
            "amount": 1,
            "status": "reserved",
            "expires_at": 1,
        },
    )

    reserve = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_after_expired"})

    assert reserve.status_code == 200, reserve.text
    assert reserve.json()["remaining_compile_credits"] == 0


def test_human_edit_pool_blocks_when_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_human_edit", True)
    _set_plan("free", human_edit_tokens=5)
    period = current_period()
    db_set(
        "entitlement_usage",
        usage_key("wrk_local", period),
        {
            "workspace_id": "wrk_local",
            "period": period,
            "human_edit_input_tokens": 5,
            "human_edit_output_tokens": 0,
        },
    )

    r = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "selector_repair", "payload": {"prompt": "x"}, "usage_class": "human_edit"},
        headers=STUDIO_HEADER,
    )

    assert r.status_code == 402
    assert r.json()["detail"] == "human_edit_pool_exceeded"


def test_compile_llm_usage_does_not_consume_human_edit_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _set_plan("free")
    from app.api import llm_proxy_routes

    class FakeRouter:
        def route_text(self, task, payload, timeout_ms, *, error_detail=None, pool=None):
            return {"text": "ok"}

    before = llm_metering.get_usage("wrk_local")["requests"]
    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: FakeRouter())
    r = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "intent", "payload": {"prompt": "hello"}, "usage_class": "compile"},
        headers=STUDIO_HEADER,
    )

    assert r.status_code == 200, r.text
    assert llm_metering.get_usage("wrk_local")["requests"] == before + 1
    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["meters"]["human_edit_tokens"]["used"] == 0


def test_installer_upload_duplicate_version_rejected_but_new_slug_allowed(monkeypatch, tmp_path):
    """There is no per-slug slot limit any more (removed 2026-08-08) — a
    workspace may publish under unlimited slugs on any plan. Only the
    per-version duplicate-upload guard, and the internal/external distribution
    gate (see test_distribution_gating_on_installer_upload below), remain."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    _set_plan("free")

    first = client.post(
        "/api/v1/workflows/slug-one/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=First",
        content=b"MZfirst",
    )
    duplicate = client.post(
        "/api/v1/workflows/slug-one/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=Duplicate",
        content=b"MZduplicate",
    )
    update = client.post(
        "/api/v1/workflows/slug-one/installer/upload?filename=Setup.exe&version=1.0.1&release_notes=Second",
        content=b"MZsecond",
    )
    another_slug = client.post(
        "/api/v1/workflows/slug-two/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=First",
        content=b"MZother",
    )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "installer_version_exists"
    assert update.status_code == 200, update.text
    assert another_slug.status_code == 200, another_slug.text


def _publish(slug: str, skill_slug: str, version: str):
    import base64

    # A byte-identical artifact is now rejected outright (skill_pack_artifact_unchanged
    # — see release-system plan §5), so give each call a distinct marker file rather
    # than the empty file list every prior version shared. One skill per call — see
    # the per-skill publishing architecture (1 Workflow = 1 Skill = 1 Skill Package).
    marker = base64.b64encode(f"{skill_slug}:{version}".encode()).decode()
    return client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": slug,
            "skill_slug": skill_slug,
            "skill_pack_version": version,
            "files": [{"path": "execution.json", "content_base64": marker}],
        },
    )


def test_downgrade_soft_locks_oldest_excess_workflows(monkeypatch, tmp_path):
    """A workspace that published up to its plan's cap, then downgrades to a
    lower cap, keeps every workflow it built — but only the newest N stay
    active. The rest are locked (not deleted) until it upgrades again, and
    republishing a locked workflow, or publishing a brand-new one past the
    cap, is blocked."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("free", compile_credits=2)

    first = _publish("acme", "wf1", "1.0.0")
    second = _publish("acme", "wf2", "1.0.0")
    over_cap = _publish("acme", "wf3", "1.0.0")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert over_cap.status_code == 402, over_cap.text
    assert over_cap.json()["detail"] == "workflow_limit_exceeded"

    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["workflow_lock"]["active"] == 2
    assert entitlements["workflow_lock"]["locked"] == 0

    # Downgrade below the current workflow count.
    _set_plan("free", compile_credits=1)

    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["workflow_lock"]["active"] == 1
    assert entitlements["workflow_lock"]["locked"] == 1
    locked_ids = {
        row["workflow_id"] for row in entitlements["workflow_lock"]["workflows"] if row["locked"]
    }
    assert locked_ids == {"wf1"}  # oldest published stays locked, wf2 (newer) stays active

    republish_locked = _publish("acme", "wf1", "1.0.3")
    republish_active = _publish("acme", "wf2", "1.0.4")

    assert republish_locked.status_code == 402, republish_locked.text
    assert republish_locked.json()["detail"] == "workflow_locked"
    assert republish_active.status_code == 200, republish_active.text

    # Upgrading again reopens room and unlocks the oldest workflow back up.
    _set_plan("free", compile_credits=2)
    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["workflow_lock"]["locked"] == 0
    assert entitlements["workflow_lock"]["active"] == 2


def test_distribution_gating_on_installer_upload(monkeypatch, tmp_path):
    """Free builds internal-only installers; Starter/Pro/Enterprise may
    distribute externally (Starter's volume is naturally bounded by its
    compile-credit ceiling, not a distribution flag). Custom branding is
    Enterprise-only. Enforced server-side in
    publish_routes._upload_installer_impl, not just UI-hidden."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_distribution", True)
    _set_plan("free")

    internal_ok = client.post(
        "/api/v1/workflows/dist-one/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=x",
        content=b"MZinternal",
    )
    external_blocked = client.post(
        "/api/v1/workflows/dist-two/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=x&distribution=external",
        content=b"MZexternal",
    )

    assert internal_ok.status_code == 200, internal_ok.text
    assert external_blocked.status_code == 402
    assert external_blocked.json()["detail"] == "distribution_not_permitted"

    _set_plan("starter")
    starter_external_ok = client.post(
        "/api/v1/workflows/dist-starter/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=x&distribution=external",
        content=b"MZstarterexternal",
    )
    assert starter_external_ok.status_code == 200, starter_external_ok.text

    _set_plan("pro")
    external_ok = client.post(
        "/api/v1/workflows/dist-three/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=x&distribution=external",
        content=b"MZexternal2",
    )
    white_label_blocked = client.post(
        "/api/v1/workflows/dist-four/installer/upload"
        "?filename=Setup.exe&version=1.0.0&release_notes=x&distribution=external&white_label=true",
        content=b"MZbranded",
    )

    assert external_ok.status_code == 200, external_ok.text
    assert white_label_blocked.status_code == 402
    assert white_label_blocked.json()["detail"] == "white_label_not_permitted"

    _set_plan("enterprise", distribution="external", white_label=True)
    white_label_ok = client.post(
        "/api/v1/workflows/dist-five/installer/upload"
        "?filename=Setup.exe&version=1.0.0&release_notes=x&distribution=external&white_label=true",
        content=b"MZbranded2",
    )
    assert white_label_ok.status_code == 200, white_label_ok.text


def test_machine_limit_blocks_new_device_but_allows_known_one(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    from app.services.entitlements import EntitlementError, ensure_machine_slot

    _set_plan("free")  # free's machine limit is 1
    principal = Principal(
        user_id="u1", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )

    first = ensure_machine_slot(principal, "machine-a", "10.0.0.1")
    assert first["used"] == 1

    with pytest.raises(EntitlementError) as exc_info:
        ensure_machine_slot(principal, "machine-b", "10.0.0.2")
    assert exc_info.value.code == "machine_limit_exceeded"

    # Re-registering the already-known machine keeps working — it doesn't
    # double-count against the limit.
    again = ensure_machine_slot(principal, "machine-a", "10.0.0.1")
    assert again["used"] == 1


def test_seat_limit_blocks_new_member_but_allows_known_one(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_seats", True)
    import app.services.entitlements as entitlements_mod
    from app.services.entitlements import EntitlementError, ensure_seats_available
    from app.services.saas import is_known_member

    upsert_billing("org_seat_test", {"plan": "free"})  # free's seat limit is 1

    founder = Principal(
        user_id="u_founder", workspace_id="org_seat_test", workspace_slug="seat-test",
        workspace_name="Seat Test", role="owner", email=None, name=None, auth_provider="clerk",
    )
    newcomer = Principal(
        user_id="u_newcomer", workspace_id="org_seat_test", workspace_slug="seat-test",
        workspace_name="Seat Test", role="member", email=None, name=None, auth_provider="clerk",
    )

    # Founder is the workspace's only member so far — Clerk reports 1.
    monkeypatch.setattr(entitlements_mod, "_clerk_org_member_count", lambda principal: 1)
    ensure_seats_available(founder)  # does not raise

    # A second member has already been invited in Clerk (count now 2) but has
    # no local membership row yet — over the free plan's 1-seat cap.
    assert is_known_member("u_newcomer", "org_seat_test") is False
    monkeypatch.setattr(entitlements_mod, "_clerk_org_member_count", lambda principal: 2)
    with pytest.raises(EntitlementError) as exc_info:
        ensure_seats_available(newcomer)
    assert exc_info.value.code == "seat_limit_exceeded"

    # Enterprise's seat count defaults to 0 (a floor forcing an explicit
    # per-contract override, see PLAN_LIMITS) — only an explicit "unlimited"
    # override actually lifts the cap.
    upsert_billing(
        "org_seat_test",
        {"plan": "enterprise", "entitlement_overrides": {"seats": "unlimited"}},
    )
    monkeypatch.setattr(entitlements_mod, "_clerk_org_member_count", lambda principal: 500)
    ensure_seats_available(newcomer)  # does not raise


def test_current_principal_maps_seat_limit_to_http_error_not_500(monkeypatch, tmp_path):
    """A brand-new (user, workspace) pairing over the seat cap must surface as a
    well-formed HTTPException, not an unhandled EntitlementError that FastAPI
    turns into a bare 500 — see FIX.md 2026-09-17."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_seats", True)
    import app.api.deps as deps_mod
    import app.services.entitlements as entitlements_mod
    from fastapi import HTTPException

    upsert_billing("org_deps_seat_test", {"plan": "free"})  # free's seat limit is 1

    newcomer = Principal(
        user_id="u_deps_newcomer", workspace_id="org_deps_seat_test", workspace_slug="deps-seat-test",
        workspace_name="Deps Seat Test", role="member", email=None, name=None, auth_provider="clerk",
    )
    monkeypatch.setattr(deps_mod, "principal_from_request", lambda request: newcomer)
    monkeypatch.setattr(entitlements_mod, "_clerk_org_member_count", lambda principal: 2)

    with pytest.raises(HTTPException) as exc_info:
        deps_mod.current_principal(request=None)
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail == "seat_limit_exceeded"


def test_trial_expired_blocks_compile_but_not_sync(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    import time as time_mod

    upsert_billing("wrk_local", {"plan": "free", "trial_started_at": time_mod.time() - 31 * 86400})

    entitlements = client.get("/api/v1/entitlements/current")
    reserve = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_trial_expired"})

    assert entitlements.status_code == 200, entitlements.text
    assert entitlements.json()["trial_expired"] is True
    assert reserve.status_code == 402
    assert reserve.json()["detail"] == "trial_expired"


def test_execute_seat_limit_blocks_new_grant_but_allows_repeat_invite(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_execute_seats", True)
    from app.services.entitlements import EntitlementError, create_execute_grant

    _set_plan("free")  # free's execute_seats limit is 1
    principal = Principal(
        user_id="u1", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )

    first = create_execute_grant(principal, "person-a@example.com")
    assert first["status"] == "pending"

    with pytest.raises(EntitlementError) as exc_info:
        create_execute_grant(principal, "person-b@example.com")
    assert exc_info.value.code == "execute_seat_limit_exceeded"

    # Re-inviting the same still-pending email is idempotent — doesn't double-count.
    again = create_execute_grant(principal, "person-a@example.com")
    assert again["grant_id"] == first["grant_id"]


def test_execute_grant_claim_binds_pool_without_creating_membership(monkeypatch, tmp_path):
    """The whole reason ensure_execute_pool_available/record_execute_pool_usage
    exist as workspace_id-scoped twins of the Principal-based functions: a
    claimed grant must never register the claimant as a Build Studio
    workspace member (which would silently inflate the seats meter)."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    from app.services.entitlements import (
        claim_execute_grant,
        create_execute_grant,
        ensure_execute_pool_available,
        execute_pool_binding_for_user,
        record_execute_pool_usage,
    )
    from app.services.saas import is_known_member

    _set_plan("pro")
    principal = Principal(
        user_id="u_admin", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )
    grant = create_execute_grant(principal, "grantee@example.com")
    seats_before_claim = client.get("/api/v1/entitlements/current").json()["meters"]["seats"]["used"]

    claim_execute_grant(grant_id=grant["grant_id"], user_id="u_grantee", email="grantee@example.com")

    binding = execute_pool_binding_for_user("u_grantee")
    assert binding is not None
    assert binding["workspace_id"] == "wrk_local"

    ensure_execute_pool_available("wrk_local", estimated_tokens=100)  # does not raise
    record_execute_pool_usage("wrk_local", input_tokens=100, output_tokens=50)

    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["meters"]["ai_usage_credits"]["used"] == 150
    # The grantee never became a counted Build Studio workspace member —
    # claiming and spending from the pool leave the seats meter untouched.
    assert is_known_member("u_grantee", "wrk_local") is False
    assert entitlements["meters"]["seats"]["used"] == seats_before_claim


def test_execute_grant_claim_rejects_email_mismatch_and_revoke_frees_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    from app.services.entitlements import (
        EntitlementError,
        claim_execute_grant,
        create_execute_grant,
        execute_grant_count,
        execute_pool_binding_for_user,
        revoke_execute_grant,
    )

    _set_plan("free")
    principal = Principal(
        user_id="u1", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )
    grant = create_execute_grant(principal, "grantee@example.com")

    with pytest.raises(EntitlementError) as exc_info:
        claim_execute_grant(grant_id=grant["grant_id"], user_id="u_x", email="wrong@example.com")
    assert exc_info.value.code == "execute_grant_email_mismatch"

    claim_execute_grant(grant_id=grant["grant_id"], user_id="u_grantee", email="grantee@example.com")
    assert execute_grant_count("wrk_local") == 1

    revoke_execute_grant(principal, grant["grant_id"])
    assert execute_grant_count("wrk_local") == 0
    assert execute_pool_binding_for_user("u_grantee") is None


def test_execute_chat_shares_pool_with_human_edit_but_counts_separately(monkeypatch, tmp_path):
    """execute_chat is its own usage_class for reporting, but must draw down
    the exact same AI Usage Credits pool/limit as human_edit — not a second,
    independent quota."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    from app.services.entitlements import record_llm_usage

    _set_plan("free")  # 500_000 human_edit_tokens/mo
    principal = Principal(
        user_id="u1", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )
    record_llm_usage(principal, usage_class="human_edit", input_tokens=100, output_tokens=50)
    record_llm_usage(principal, usage_class="execute_chat", input_tokens=200, output_tokens=25)

    entitlements = client.get("/api/v1/entitlements/current").json()
    meters = entitlements["meters"]
    # Same combined pool: the shared meter sums both classes.
    assert meters["ai_usage_credits"]["used"] == 375
    assert meters["human_edit_tokens"]["used"] == 375
    # But reported separately for the execute_chat line item.
    assert meters["execute_chat_tokens"]["used"] == 225
    assert meters["execute_chat_tokens"]["unlimited"] is True


def test_execute_chat_exhausts_shared_pool_gate(monkeypatch, tmp_path):
    """A workspace already at its human_edit limit should also block a
    subsequent execute_chat call (not skip the gate just because the class
    label differs) — this is the "same pool" half of the design."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_human_edit", True)
    from app.services.entitlements import EntitlementError, record_llm_usage

    _set_plan("free")  # 500_000 human_edit_tokens/mo
    principal = Principal(
        user_id="u1", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )
    record_llm_usage(principal, usage_class="human_edit", input_tokens=500_000, output_tokens=0)
    with pytest.raises(EntitlementError) as exc_info:
        record_llm_usage(principal, usage_class="execute_chat", input_tokens=1, output_tokens=0)
    assert exc_info.value.code == "human_edit_pool_exceeded"


def test_execute_grant_auto_claims_on_sign_in(monkeypatch, tmp_path):
    """Replaces the old manual invite-code paste: a pending grant binds to a
    Clerk user_id automatically once that verified email is seen, with no
    grant_id involved at all."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    from app.services.entitlements import (
        auto_claim_pending_grants_for,
        create_execute_grant,
        execute_pool_binding_for_user,
        list_claimed_grant_workspaces_for,
    )

    _set_plan("pro")
    principal = Principal(
        user_id="u_admin", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )
    create_execute_grant(principal, "grantee@example.com")

    # A sign-in from an unrelated email is a no-op.
    assert auto_claim_pending_grants_for(user_id="u_other", email="nobody@example.com") == []
    assert execute_pool_binding_for_user("u_other") is None

    claimed = auto_claim_pending_grants_for(user_id="u_grantee", email="grantee@example.com")
    assert len(claimed) == 1
    assert claimed[0]["status"] == "claimed"
    binding = execute_pool_binding_for_user("u_grantee")
    assert binding is not None and binding["workspace_id"] == "wrk_local"
    assert [w["workspace_id"] for w in list_claimed_grant_workspaces_for("u_grantee")] == ["wrk_local"]

    # Idempotent: signing in again finds nothing left pending.
    assert auto_claim_pending_grants_for(user_id="u_grantee", email="grantee@example.com") == []


def test_ops_tier_gates_dashboard_and_drift_by_plan(monkeypatch, tmp_path):
    """Free: no dashboard at all. Starter ("basic"): runs list yes, drift no.
    Pro ("full"): everything. Mirrors the capability ladder in docs/PRD.md §11."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    _set_plan("free")
    free_dashboard = client.get("/api/v1/tracking/dashboard")
    assert free_dashboard.status_code == 403
    assert free_dashboard.json()["detail"] == "ops_tier_required"

    _set_plan("starter")
    starter_runs = client.get("/api/v1/tracking/some-company/runs")
    starter_drift = client.get("/api/v1/tracking/drift")
    assert starter_runs.status_code == 200, starter_runs.text
    assert starter_drift.status_code == 403
    assert starter_drift.json()["detail"] == "ops_tier_required"

    _set_plan("pro")
    pro_drift = client.get("/api/v1/tracking/drift")
    assert pro_drift.status_code == 200, pro_drift.text


def test_addon_wallet_grant_and_overflow_consumption(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("starter")  # 200 compile credits/mo base

    from app.services.entitlements import grant_credit_wallet

    # One-time purchase lands in the wallet and never touches the plan meter.
    wallet = grant_credit_wallet(
        "wrk_local", compile_credits=20, human_edit_tokens=200_000
    )
    assert wallet["compile_credits"] == 20
    entitlements = client.get("/api/v1/entitlements/current")
    assert entitlements.status_code == 200, entitlements.text
    body = entitlements.json()
    # Plan limit stays at the base 200 — packs no longer stack onto it.
    assert body["meters"]["compile_credits"]["limit"] == 200
    assert body["wallet"]["compile_credits"] == 20
    assert body["wallet"]["human_edit_tokens"] == 200_000

    # Exhaust the monthly allowance; the next reserve draws from the wallet.
    upsert_billing("wrk_local", {})
    for i in range(200):
        rid = f"cmp_base_{i}"
        client.post("/api/v1/usage/compile/reserve", json={"reservation_id": rid})
        client.post("/api/v1/usage/compile/commit", json={"reservation_id": rid})
    over = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_over"})
    assert over.status_code == 200, over.text
    commit = client.post("/api/v1/usage/compile/commit", json={"reservation_id": "cmp_over"})
    assert commit.status_code == 200, commit.text
    after = client.get("/api/v1/entitlements/current").json()
    assert after["wallet"]["compile_credits"] == 19

    # Drain the remaining wallet credits; once dry, compiles are blocked again.
    for i in range(19):
        rid = f"cmp_wallet_{i}"
        client.post("/api/v1/usage/compile/reserve", json={"reservation_id": rid})
        client.post("/api/v1/usage/compile/commit", json={"reservation_id": rid})
    drained = client.get("/api/v1/entitlements/current").json()
    assert drained["wallet"]["compile_credits"] == 0
    blocked = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_over2"})
    assert blocked.status_code == 402
    assert blocked.json()["detail"] == "compile_credit_limit_exceeded"


def test_development_plan_is_unlimited(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", True)
    _set_plan("development")

    entitlements = client.get("/api/v1/entitlements/current")
    reserve = client.post("/api/v1/usage/compile/reserve", json={"reservation_id": "cmp_dev"})

    assert entitlements.status_code == 200, entitlements.text
    assert entitlements.json()["meters"]["compile_credits"]["unlimited"] is True
    assert entitlements.json()["meters"]["machines"]["unlimited"] is True
    assert reserve.status_code == 200, reserve.text
    assert reserve.json()["remaining_compile_credits"] is None


def test_ai_credits_class_meters_into_the_shared_pool_and_legacy_counters_still_count(monkeypatch, tmp_path):
    """CLOUD-25: "ai_credits" is the canonical class, "human_edit" a permanent alias. Both draw
    from one pool, new usage is written under the new counters, and rows written before the
    rename (human_edit_* counters) are still summed."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    from app.services.entitlements import record_llm_usage

    _set_plan("free")
    principal = Principal(
        user_id="u1", workspace_id="wrk_local", workspace_slug="local", workspace_name="Local",
        role="owner", email=None, name=None, auth_provider="local",
    )
    record_llm_usage(principal, usage_class="human_edit", input_tokens=100, output_tokens=50)
    record_llm_usage(principal, usage_class="ai_credits", input_tokens=200, output_tokens=25)

    meters = client.get("/api/v1/entitlements/current").json()["meters"]
    assert meters["ai_usage_credits"]["used"] == 375

    usage = db_get("entitlement_usage", usage_key("wrk_local", current_period()))
    assert usage["ai_credits_input_tokens"] == 300  # the legacy alias is normalized on write
    assert usage["ai_credits_requests"] == 2


def test_ai_credits_pool_exhaustion_uses_the_new_error_code_only_for_the_new_class(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_human_edit", True)
    _set_plan("free", human_edit_tokens=5)
    db_set(
        "entitlement_usage",
        usage_key("wrk_local", current_period()),
        {"workspace_id": "wrk_local", "period": current_period(), "ai_credits_input_tokens": 5, "ai_credits_output_tokens": 0},
    )
    new = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "selector_repair", "payload": {"prompt": "x"}, "usage_class": "ai_credits"},
        headers=STUDIO_HEADER,
    )
    old = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "selector_repair", "payload": {"prompt": "x"}, "usage_class": "human_edit"},
        headers=STUDIO_HEADER,
    )
    assert (new.status_code, new.json()["detail"]) == (402, "ai_credits_exceeded")
    assert (old.status_code, old.json()["detail"]) == (402, "human_edit_pool_exceeded")
