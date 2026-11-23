"""Phase 1: LLM proxy metering + workflow publish / installer hosting."""

from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from conxa_core.config import settings
from conxa_core.db import db_get, db_set
from conxa_core.storage.skill_pack_store import list_skill_packs
from app.main import app
from app.services import llm_metering
from app.services.saas import upsert_billing

client = TestClient(app)
STUDIO_HEADER = {"X-Conxa-Client": settings.llm_proxy_client_header}


@pytest.fixture(autouse=True)
def _reset_quota(monkeypatch, tmp_path):
    original = settings.llm_proxy_monthly_token_quota
    original_proxy_secret = settings.api_proxy_shared_secret
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "entitlements_enforce_compile", False)
    monkeypatch.setattr(settings, "entitlements_enforce_human_edit", False)
    monkeypatch.setattr(settings, "entitlements_enforce_distribution", False)
    monkeypatch.setattr(settings, "entitlements_enforce_machines", False)
    yield
    settings.llm_proxy_monthly_token_quota = original
    settings.api_proxy_shared_secret = original_proxy_secret


# --- LLM proxy ---------------------------------------------------------------

def test_estimate_request_tokens_excludes_base64_blobs():
    blob = base64.b64encode(b"x" * 200_000).decode()
    payload = {
        "prompt": "hello world",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "anchor?"},
                {"type": "image", "image_base64": f"data:image/jpeg;base64,{blob}"},
            ]},
        ],
    }
    tokens = llm_metering.estimate_request_tokens(payload)
    # ~4 chars/token on the remaining text (a few dozen chars), plus the fixed
    # per-image estimate — NOT the ~50k a naive json.dumps would count.
    assert 1000 <= tokens < 3000
    # The original payload is not mutated.
    assert len(payload["messages"][0]["content"][1]["image_base64"]) > 100_000


def test_estimate_request_tokens_plain_text_unchanged():
    import json as _json

    payload = {"prompt": "a" * 40}
    expected = llm_metering.estimate_tokens(_json.dumps(payload, ensure_ascii=False))
    assert llm_metering.estimate_request_tokens(payload) == expected


def test_proxy_requires_studio_header():
    r = client.post("/api/v1/llm/proxy/text", json={"task": "intent", "payload": {}})
    assert r.status_code == 403
    assert r.json()["detail"] == "proxy_requires_known_client"


def test_proxy_forwards_and_meters(monkeypatch):
    from app.api import llm_proxy_routes

    class FakeRouter:
        def route_text(self, task, payload, timeout_ms, *, error_detail=None, pool=None):
            return {"text": "ok", "output": "ok"}

    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: FakeRouter())
    settings.llm_proxy_monthly_token_quota = 1_000_000

    before = llm_metering.get_usage("wrk_local")["requests"]
    r = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "intent", "payload": {"prompt": "hello world"}},
        headers=STUDIO_HEADER,
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "ok"
    after = llm_metering.get_usage("wrk_local")["requests"]
    assert after == before + 1


def test_proxy_execute_chat_meters_as_its_own_class(monkeypatch):
    from app.api import llm_proxy_routes

    class FakeRouter:
        def route_text(self, task, payload, timeout_ms, *, error_detail=None, pool=None):
            assert payload["messages"][0]["content"] == "hi"
            return {"text": "hello there"}

    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: FakeRouter())
    r = client.post(
        "/api/v1/llm/proxy/text",
        json={
            "task": "execute_chat",
            "payload": {"messages": [{"role": "user", "content": "hi"}]},
            "usage_class": "execute_chat",
        },
        headers={"X-Conxa-Client": "conxa-execute"},
    )
    assert r.status_code == 200, r.text
    entitlements = client.get("/api/v1/entitlements/current").json()
    assert entitlements["meters"]["execute_chat_tokens"]["used"] > 0


def test_proxy_execute_chat_rejects_workspace_caller_cannot_access(monkeypatch):
    from app.api import llm_proxy_routes

    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: object())
    r = client.post(
        "/api/v1/llm/proxy/text",
        json={
            "task": "execute_chat",
            "payload": {"messages": [{"role": "user", "content": "hi"}]},
            "usage_class": "execute_chat",
            "target_workspace_id": "wrk_someone_elses",
        },
        headers={"X-Conxa-Client": "conxa-execute"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "execute_context_not_available"


def test_proxy_enforces_quota(monkeypatch):
    from app.api import llm_proxy_routes

    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: object())
    settings.llm_proxy_monthly_token_quota = 1
    # Push usage over the 1-token quota.
    llm_metering.record_usage("wrk_local", input_tokens=10, output_tokens=10)

    r = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "intent", "payload": {"prompt": "x"}},
        headers=STUDIO_HEADER,
    )
    assert r.status_code == 429
    assert r.json()["detail"] == "quota_exceeded"


def test_proxy_enforces_per_workspace_concurrency_limit(monkeypatch):
    """One workspace's compile burst must not be able to hold every concurrency
    slot the shared pool has — over the cap gets 429 + Retry-After instead of
    queuing indefinitely or (pre-fix) draining the pool into 502s for everyone."""
    from app.api import llm_proxy_routes

    monkeypatch.setattr(settings, "llm_proxy_max_concurrent_per_workspace", 1)
    llm_proxy_routes._inflight_by_workspace.clear()

    class FakeRouter:
        def route_text(self, task, payload, timeout_ms, *, error_detail=None, pool=None):
            return {"text": "ok", "output": "ok"}

    monkeypatch.setattr(llm_proxy_routes, "get_router", lambda: FakeRouter())
    settings.llm_proxy_monthly_token_quota = 1_000_000

    # Simulate one call already in flight for this workspace.
    llm_proxy_routes._acquire_workspace_slot("wrk_local")
    try:
        r = client.post(
            "/api/v1/llm/proxy/text",
            json={"task": "intent", "payload": {"prompt": "x"}},
            headers=STUDIO_HEADER,
        )
        assert r.status_code == 429
        assert r.json()["detail"] == "workspace_concurrency_limit"
        assert r.headers.get("retry-after")
    finally:
        llm_proxy_routes._release_workspace_slot("wrk_local")

    # Slot freed — the next call goes through normally.
    r2 = client.post(
        "/api/v1/llm/proxy/text",
        json={"task": "intent", "payload": {"prompt": "x"}},
        headers=STUDIO_HEADER,
    )
    assert r2.status_code == 200, r2.text


# --- Publish + installer hosting --------------------------------------------

def test_publish_and_sync_roundtrip():
    files = [
        {
            "path": "pack.json",
            "content_base64": base64.b64encode(
                b'{"company":"acme-test","tracking":{"tracking_url":"http://127.0.0.1:8000/api/tracking/acme-test/events"}}'
            ).decode(),
        },
        {"path": "deploy/execution.json", "content_base64": base64.b64encode(b'{"steps":[]}').decode()},
    ]
    r = client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": "acme-test",
            "display_name": "Acme Test",
            "target_url": "https://acme.test",
            "protected_url": "https://acme.test/app",
            "skill_pack_version": "0.3.0",
            "skill_slug": "deploy",
            "files": files,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "wrk_local"
    assert body["files_written"] == 2
    assert body["tracking"]["tracking_url"].endswith("/api/tracking/wrk_local/events")
    assert body["tracking"]["tracking_token"]
    assert db_get("tracking_tokens", "wrk_local")["workspace_id"] == "wrk_local"
    packs = list_skill_packs(workspace_id="wrk_local")
    assert any(p.workspace_id == "wrk_local" and p.display_name == "Acme Test" for p in packs)

    # Publish alone never makes a skill runtime-visible — release it first.
    rel = client.post("/api/v1/workflows/v2/releases/0.3.0/release?skill_slug=deploy")
    assert rel.status_code == 200, rel.text

    # The delta endpoint should now serve the published pack, per skill.
    d = client.get("/api/v1/skill-packs/wrk_local/delta?since=%7B%7D")
    assert d.status_code == 200
    skills = {s["name"]: s for s in d.json()["skills"]}
    assert skills["deploy"]["action"] == "update"
    assert skills["deploy"]["version"] == "0.3.0"

    companies = client.get("/api/v1/tracking/companies")
    assert companies.status_code == 200
    # "company" must stay the telemetry storage key (what tracking/{key}/... was written
    # under), not the human display name — see tracking.py::_tracking_company_rows.
    assert any(
        row["company"] == "wrk_local" and row["display_name"] == "Acme Test"
        for row in companies.json()["companies"]
    )

    # Regression: a run ingested after publish must be readable back through the
    # dashboard's run-listing path, keyed by the same "company" the row above reports —
    # not silently dropped because that key was overwritten with the display name.
    tracking_post = client.post(
        f"/api/v1/tracking/wrk_local/events",
        json={"rid": "run-1", "evts": [{"e": "wf_start", "ts": 1000}, {"e": "wf_ok", "dur": 500, "tot": 1, "rec": 0}]},
        headers={"x-tracking-token": body["tracking"]["tracking_token"]},
    )
    assert tracking_post.status_code == 202, tracking_post.text
    runs = client.get("/api/v1/tracking/wrk_local/runs")
    assert runs.status_code == 200, runs.text
    assert any(r["run_id"] == "run-1" for r in runs.json()["runs"])


def test_delta_ships_only_the_skill_that_actually_changed():
    """Two skills under one company must version and sync independently — bumping one
    skill's component_versions record must not affect the other's delta result. Each
    is published independently (one skill per publish call — see the per-skill
    publishing architecture), never bundled into one call together."""
    import json as _json

    for skill_slug in ("invoice-automation", "approval-workflow"):
        r = client.post(
            "/api/v1/workflows/publish",
            json={
                "slug": "multi-skill-test",
                "display_name": "Multi Skill Test",
                "target_url": "https://multi.test",
                "skill_pack_version": "1.0.0",
                "skill_slug": skill_slug,
                "files": [
                    {
                        "path": f"{skill_slug}/execution.json",
                        "content_base64": base64.b64encode(b'{"steps":[]}').decode(),
                    }
                ],
            },
        )
        assert r.status_code == 200, r.text
        rel = client.post(
            f"/api/v1/workflows/v2/releases/1.0.0/release?skill_slug={skill_slug}"
        )
        assert rel.status_code == 200, rel.text

    # Simulate an independent version bump for one skill only (a future enhanced
    # publish flow would do this directly; today the KV record is what the delta
    # endpoint actually reads, per _skill_version()).
    db_set(
        "component_versions",
        "skill_packs:wrk_local:invoice-automation",
        {"version": "1.1.0", "released_at": "2026-07-01T00:00:00Z", "files": []},
    )

    since = _json.dumps({"invoice-automation": "1.0.0", "approval-workflow": "1.0.0"})
    d = client.get(f"/api/v1/skill-packs/wrk_local/delta?since={since}")
    assert d.status_code == 200, d.text
    skills = {s["name"]: s for s in d.json()["skills"]}

    assert skills["invoice-automation"]["action"] == "update"
    assert skills["invoice-automation"]["version"] == "1.1.0"
    assert skills["approval-workflow"]["action"] == "no_change"
    assert "files" not in skills["approval-workflow"]


def test_skill_pack_delta_survives_disk_wipe(monkeypatch, tmp_path):
    """Render's free plan has no persistent disk; this simulates an idle-out/redeploy
    wiping local disk between requests and asserts the Postgres-backed copy heals it."""
    monkeypatch.setattr("app.api.publish_routes.using_database", lambda: True)
    monkeypatch.setattr("app.api.skillpack_update_routes.using_database", lambda: True)
    slug = "wrk_local"
    files = [
        {"path": "pack.json", "content_base64": base64.b64encode(b'{"workspace_id":"wrk_local"}').decode()},
        {"path": "deploy/execution.json", "content_base64": base64.b64encode(b'{"steps":[]}').decode()},
    ]
    r = client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": slug,
            "display_name": "Wipe Skillpack Test",
            "target_url": "https://wipe.test",
            "skill_pack_version": "1.0.0",
            "skill_slug": "deploy",
            "files": files,
        },
    )
    assert r.status_code == 200, r.text
    rel = client.post("/api/v1/workflows/v2/releases/1.0.0/release?skill_slug=deploy")
    assert rel.status_code == 200, rel.text

    import shutil
    shutil.rmtree(tmp_path / "skill-packs", ignore_errors=True)
    assert not (tmp_path / "skill-packs" / slug / "pack.json").exists()

    d = client.get(f"/api/v1/skill-packs/{slug}/delta?since=%7B%7D")
    assert d.status_code == 200, d.text
    skills = {s["name"]: s for s in d.json()["skills"]}
    assert skills["deploy"]["action"] == "update"
    assert skills["deploy"]["version"] == "1.0.0"
    assert "execution.json" in {f["path"] for f in skills["deploy"]["files"]}


def test_publish_upsert_updates_existing_skill_pack_slug(monkeypatch, tmp_path):
    """Publishing twice under the same slug updates the one SkillPack record
    for that slug rather than creating a duplicate."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    body = {
        "slug": "render",
        "display_name": "Render",
        "target_url": "https://dashboard.render.com",
        "skill_pack_version": "1.0.0",
        "skill_slug": "deploy",
        "files": [],
    }
    r1 = client.post("/api/v1/workflows/publish", json=body)
    assert r1.status_code == 200, r1.text

    # A second publish must carry a different artifact — an identical one is
    # rejected as skill_pack_artifact_unchanged (see release-system plan §5).
    body2 = {
        **body,
        "skill_pack_version": "1.0.1",
        "files": [{"path": "pack.json", "content_base64": base64.b64encode(b'{"marker":"v2"}').decode()}],
    }
    r2 = client.post("/api/v1/workflows/publish", json=body2)
    assert r2.status_code == 200, r2.text

    packs = list_skill_packs(workspace_id="wrk_local")
    assert len(packs) == 1
    assert packs[0].display_name == "Render"
    assert packs[0].build.version == "1.0.1"
    assert packs[0].workspace_id == "wrk_local"


def test_skill_pack_delta_requires_sync_token_when_cloud_auth_required(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    packs_dir = tmp_path / "skill-packs" / "public-sync"
    packs_dir.mkdir(parents=True)
    (packs_dir / "pack.json").write_text(
        '{"company":"public-sync","skill_pack_version":"9.9.9","skills":[]}',
        encoding="utf-8",
    )

    r = client.get("/api/v1/skill-packs/public-sync/delta?since=0")

    assert r.status_code == 401
    assert r.json()["detail"] == "sync_token_not_configured"

    db_set("sync_tokens", "public-sync", {"token": "sync-secret", "workspace_id": "wrk_local"})
    ok = client.get(
        "/api/v1/skill-packs/public-sync/delta?since=0",
        headers={"Authorization": "Bearer sync-secret"},
    )

    assert ok.status_code == 200, ok.text
    assert ok.json()["skills"] == []  # pack.json declares no skills in this fixture


def test_tracking_ingest_requires_published_token_and_lists_runs():
    pub = client.post(
        "/api/v1/workflows/publish",
        json={"slug": "wrk_local", "skill_pack_version": "1.0.0", "skill_slug": "deploy", "files": []},
    )
    assert pub.status_code == 200, pub.text
    token = pub.json()["tracking"]["tracking_token"]

    denied = client.post(
        "/api/tracking/wrk_local/events",
        json={"rid": "run-denied", "evts": [{"e": "wf_start", "ts": 1}]},
        headers={"X-Tracking-Token": "wrong"},
    )
    assert denied.status_code == 401

    accepted = client.post(
        "/api/tracking/wrk_local/events",
        json={
            "rid": "run-ok",
            "wfid": "delete-a-service",
            "wfv": "1.0.0",
            "rv": "1.0.0",
            "evts": [{"e": "wf_start", "ts": 1}, {"e": "wf_ok", "ts": 2, "dur": 10, "tot": 2, "rec": 0}],
        },
        headers={"X-Tracking-Token": token},
    )
    assert accepted.status_code == 202

    runs = client.get("/api/v1/tracking/wrk_local/runs")
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["run_id"] == "run-ok"


def test_tracking_runs_report_workspace_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_set(
        "tracking_tokens",
        "workspace-hidden-test",
        {"token": "hidden-token", "workspace_id": "wrk_other", "version": "1.0.0"},
    )

    accepted = client.post(
        "/api/tracking/workspace-hidden-test/events",
        json={
            "rid": "run-hidden",
            "wfid": "hidden-skill",
            "evts": [{"e": "wf_start", "ts": 1}],
        },
        headers={"X-Tracking-Token": "hidden-token"},
    )
    assert accepted.status_code == 202

    runs = client.get("/api/v1/tracking/workspace-hidden-test/runs")
    assert runs.status_code == 200
    body = runs.json()
    assert body["runs"] == []
    assert body["workspace_id"] == "wrk_local"
    assert body["total_all_workspaces"] == 1
    assert body["hidden_workspace_runs"] == 1


def test_tracking_dashboard_empty_workspace_has_v1_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")

    dashboard = client.get("/api/v1/tracking/dashboard?range=30d")

    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()
    assert body["range"] == "30d"
    assert set(body["metrics"]) == {
        "total_installs",
        "active_users",
        "active_companies",
        "total_executions",
        "executions_last_24h",
        "success_rate",
        "failed_executions",
        "recovery_rate",
        "average_execution_time",
    }
    assert [row["type"] for row in body["recovery_type_usage"]] == [
        "Selector",
        "Text Anchor",
        "Text Variant",
        "Vision",
    ]
    assert body["recovery_usage_by_step"] == []
    assert body["recovery_usage_by_workflow"] == []
    assert "recent_activity" not in body
    assert len(body["execution_trend"]) == 30


def test_tracking_dashboard_aggregates_workspace_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    now_ms = int(time.time() * 1000)

    db_set("sync_tokens", "acme", {"token": "sync", "workspace_id": "wrk_local"})
    db_set(
        "tracking_tokens",
        "acme",
        {"token": "track-token", "company": "acme", "workspace_id": "wrk_local", "version": "1.0.0"},
    )
    db_set(
        "tracking_tokens",
        "hidden",
        {"token": "hidden-token", "company": "hidden", "workspace_id": "wrk_other", "version": "1.0.0"},
    )

    for install_id in ("install-a", "install-b"):
        telemetry = client.post(
            "/api/v1/telemetry/runtime-start",
            json={
                "runtime_version": "1.0.0",
                "companies": ["acme"],
                "platform": "win32",
                "install_id": install_id,
            },
        )
        assert telemetry.status_code == 200, telemetry.text

    ok = client.post(
        "/api/tracking/acme/events",
        json={
            "rid": "run-ok",
            "wfid": "workflow-a",
            "uid": "install-a",
            "evts": [
                {"e": "wf_start", "ts": now_ms - 60_000},
                {"e": "rec_ok", "ts": now_ms - 50_000, "si": 0, "sc": "selector"},
                {"e": "tier_ok", "ts": now_ms - 49_000, "si": 0, "tier": "tier2_a11y"},
                {"e": "rec_ok", "ts": now_ms - 48_000, "si": 0, "sc": "text_variant"},
                {"e": "tier_ok", "ts": now_ms - 45_000, "si": 1, "tier": "tier2_a11y"},
                {"e": "rec_ok", "ts": now_ms - 40_000, "si": 2, "sc": "text_variant"},
                {"e": "rec_ok", "ts": now_ms - 35_000, "si": 3, "sc": "vision"},
                {"e": "wf_ok", "ts": now_ms - 30_000, "dur": 1200, "tot": 4, "rec": 1},
            ],
        },
        headers={"X-Tracking-Token": "track-token"},
    )
    assert ok.status_code == 202, ok.text

    fail = client.post(
        "/api/tracking/acme/events",
        json={
            "rid": "run-fail",
            "wfid": "workflow-b",
            "uid": "install-b",
            "evts": [
                {"e": "wf_start", "ts": now_ms - 20_000},
                {"e": "step_fail", "ts": now_ms - 15_000, "si": 2, "fc": "timeout"},
                {"e": "wf_fail", "ts": now_ms - 10_000, "dur": 3000, "fsi": 2, "fc": "timeout"},
            ],
        },
        headers={"X-Tracking-Token": "track-token"},
    )
    assert fail.status_code == 202, fail.text

    hidden = client.post(
        "/api/tracking/hidden/events",
        json={
            "rid": "run-hidden",
            "wfid": "hidden-workflow",
            "evts": [{"e": "wf_start", "ts": now_ms}, {"e": "wf_fail", "ts": now_ms, "dur": 1}],
        },
        headers={"X-Tracking-Token": "hidden-token"},
    )
    assert hidden.status_code == 202, hidden.text

    dashboard = client.get("/api/v1/tracking/dashboard?range=7d")
    assert dashboard.status_code == 200, dashboard.text
    body = dashboard.json()

    assert body["range"] == "7d"
    assert body["metrics"]["total_installs"] == 2
    assert body["metrics"]["active_users"] == 2
    assert body["metrics"]["active_companies"] == 1
    assert body["metrics"]["total_executions"] == 2
    assert body["metrics"]["executions_last_24h"] == 2
    assert body["metrics"]["success_rate"] == 50
    assert body["metrics"]["failed_executions"] == 1
    assert body["metrics"]["recovery_rate"] == 50
    assert body["metrics"]["average_execution_time"] == 2100

    recovery = {row["type"]: row["count"] for row in body["recovery_type_usage"]}
    assert recovery == {"Selector": 1, "Text Anchor": 2, "Text Variant": 2, "Vision": 1}
    recovery_by_step = {
        (row["workflow"], row["step_index"], row["recovery_type"]): row["count"]
        for row in body["recovery_usage_by_step"]
    }
    assert recovery_by_step == {
        ("workflow-a", 0, "Selector"): 1,
        ("workflow-a", 0, "Text Anchor"): 1,
        ("workflow-a", 0, "Text Variant"): 1,
        ("workflow-a", 1, "Text Anchor"): 1,
        ("workflow-a", 2, "Text Variant"): 1,
        ("workflow-a", 3, "Vision"): 1,
    }
    workflow_recovery = body["recovery_usage_by_workflow"][0]
    assert workflow_recovery["company"] == "acme"
    assert workflow_recovery["workflow"] == "workflow-a"
    assert workflow_recovery["count"] == 6
    step_zero = next(step for step in workflow_recovery["steps"] if step["step_index"] == 0)
    assert step_zero["total_count"] == 3
    assert {
        (row["tier"], row["recovery_type"]): row["count"]
        for row in step_zero["tier_counts"]
    } == {
        ("Tier A", "Selector"): 1,
        ("Tier A", "Text Anchor"): 1,
        ("Tier B", "Text Variant"): 1,
    }
    assert body["most_failed_workflows"][0]["workflow"] == "workflow-b"
    assert body["most_failed_steps"][0]["step_index"] == 2
    assert sum(row["executions"] for row in body["execution_trend"]) == 2
    assert all(item["company"] != "hidden" for item in body["recovery_usage_by_step"])
    assert "recent_activity" not in body


def test_tracking_companies_discovers_token_backed_events_without_plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_set(
        "tracking_tokens",
        "token-only-company",
        {
            "token": "token-only-secret",
            "company": "token-only-company",
            "workspace_id": "wrk_local",
            "version": "1.0.0",
            "updated_at": 10,
        },
    )

    accepted = client.post(
        "/api/tracking/token-only-company/events",
        json={"rid": "run-token-only", "wfid": "skill-a", "evts": [{"e": "wf_start", "ts": 1}]},
        headers={"X-Tracking-Token": "token-only-secret"},
    )
    assert accepted.status_code == 202

    companies = client.get("/api/v1/tracking/companies")
    assert companies.status_code == 200
    row = next((r for r in companies.json()["companies"] if r["company"] == "token-only-company"), None)
    assert row is not None
    assert row["workspace_id"] == "wrk_local"
    assert row["run_count"] == 1
    assert row["last_seen"] > 0


def test_tracking_companies_hides_other_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_set(
        "tracking_tokens",
        "other-workspace-company",
        {
            "token": "other-workspace-secret",
            "company": "other-workspace-company",
            "workspace_id": "wrk_other",
            "version": "1.0.0",
        },
    )

    accepted = client.post(
        "/api/tracking/other-workspace-company/events",
        json={"rid": "run-other", "wfid": "skill-b", "evts": [{"e": "wf_start", "ts": 1}]},
        headers={"X-Tracking-Token": "other-workspace-secret"},
    )
    assert accepted.status_code == 202

    companies = client.get("/api/v1/tracking/companies")
    assert companies.status_code == 200
    assert all(row["company"] != "other-workspace-company" for row in companies.json()["companies"])


def test_org_dashboard_sees_same_user_personal_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "api_proxy_shared_secret", "proxy-secret")
    # Dashboard/runs endpoints are ops_tier-gated (Free has none) — this test is
    # about workspace-scoping visibility, not billing tiers, so give the org
    # workspace dashboard access rather than asserting against the Free default.
    upsert_billing("org_same", {"plan": "pro"})
    personal_headers = {
        "x-conxa-proxy-secret": "proxy-secret",
        "x-conxa-user-id": "user_same",
    }
    org_headers = {
        "x-conxa-proxy-secret": "proxy-secret",
        "x-conxa-user-id": "user_same",
        "x-conxa-org-id": "org_same",
    }

    pub = client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": "personal-visible",
            "display_name": "Personal Visible",
            "target_url": "https://example.test",
            "skill_pack_version": "1.0.0",
            "skill_slug": "deploy",
            "files": [],
        },
        headers=personal_headers,
    )
    assert pub.status_code == 200, pub.text
    token_record = db_get("tracking_tokens", "personal_user_same")
    assert token_record["workspace_id"] == "personal_user_same"
    assert token_record["owner_user_id"] == "user_same"

    accepted = client.post(
        "/api/tracking/personal_user_same/events",
        json={
            "rid": "run-personal-visible",
            "wfid": "skill-visible",
            "evts": [{"e": "wf_start", "ts": 1}, {"e": "wf_ok", "ts": 2, "dur": 10, "tot": 1, "rec": 0}],
        },
        headers={"X-Tracking-Token": pub.json()["tracking"]["tracking_token"]},
    )
    assert accepted.status_code == 202

    packs = client.get("/api/v1/workflows/skill-packs", headers=org_headers)
    assert packs.status_code == 200
    assert any(p["workspace_id"] == "personal_user_same" and p["display_name"] == "Personal Visible" for p in packs.json()["skill_packs"])

    companies = client.get("/api/v1/tracking/companies", headers=org_headers)
    assert companies.status_code == 200
    assert any(
        row["workspace_id"] == "personal_user_same" and row["display_name"] == "Personal Visible"
        for row in companies.json()["companies"]
    )

    runs = client.get("/api/v1/tracking/personal_user_same/runs", headers=org_headers)
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["run_id"] == "run-personal-visible"

    diagnostics = client.get("/api/v1/tracking/diagnostics", headers=org_headers)
    assert diagnostics.status_code == 200
    diagnostics_body = diagnostics.json()
    assert diagnostics_body["workspace_id"] == "org_same"
    assert diagnostics_body["personal_workspace_id"] == "personal_user_same"
    assert diagnostics_body["identity_source"] == "trusted_proxy"
    assert diagnostics_body["proxy_identity_trusted"] is True
    assert diagnostics_body["proxy_identity_status"] == "trusted"
    assert diagnostics_body["same_user_personal_company_count"] == 1


def test_org_member_without_a_role_is_not_admin(monkeypatch, tmp_path):
    """The personal-workspace owner default must not leak into org workspaces: a user
    inside an org whose role header is missing stays a basic member and cannot publish."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "api_proxy_shared_secret", "proxy-secret")

    pub = client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": "org-no-role",
            "display_name": "Org No Role",
            "target_url": "https://example.test",
            "skill_pack_version": "1.0.0",
            "skill_slug": "deploy",
            "files": [],
        },
        headers={
            "x-conxa-proxy-secret": "proxy-secret",
            "x-conxa-user-id": "user_norole",
            "x-conxa-org-id": "org_norole",
        },
    )
    assert pub.status_code == 403, pub.text
    assert pub.json()["detail"] == "admin_role_required"


def test_org_dashboard_cannot_see_other_user_personal_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "api_proxy_shared_secret", "proxy-secret")
    upsert_billing("org_same", {"plan": "pro"})

    pub = client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": "personal-hidden",
            "display_name": "Personal Hidden",
            "target_url": "https://example.test",
            "skill_pack_version": "1.0.0",
            "skill_slug": "deploy",
            "files": [],
        },
        headers={
            "x-conxa-proxy-secret": "proxy-secret",
            "x-conxa-user-id": "user_other",
        },
    )
    assert pub.status_code == 200, pub.text

    org_headers = {
        "x-conxa-proxy-secret": "proxy-secret",
        "x-conxa-user-id": "user_same",
        "x-conxa-org-id": "org_same",
    }
    packs = client.get("/api/v1/workflows/skill-packs", headers=org_headers)
    assert packs.status_code == 200
    assert all(p["display_name"] != "Personal Hidden" for p in packs.json()["skill_packs"])

    companies = client.get("/api/v1/tracking/companies", headers=org_headers)
    assert companies.status_code == 200
    assert all(row["company"] != "personal-hidden" for row in companies.json()["companies"])


def test_publish_rejects_path_traversal():
    files = [{"path": "../escape.json", "content_base64": base64.b64encode(b"x").decode()}]
    r = client.post(
        "/api/v1/workflows/publish",
        json={"slug": "trav-test", "skill_pack_version": "1.0.0", "skill_slug": "deploy", "files": files},
    )
    assert r.status_code == 400
    assert "invalid_file_path" in r.json()["detail"]


def test_installer_upload_and_public_download():
    payload = b"MZ\x90\x00fake-exe-bytes"
    up = client.post(
        "/api/v1/workflows/wrk_local/installer/upload?filename=Acme-Setup.exe&version=1.2.0&release_notes=Initial%20release",
        content=payload,
    )
    assert up.status_code == 200, up.text
    sha = up.json()["sha256"]
    assert up.json()["version_download_url"] == "/api/v1/installers/wrk_local/versions/1.2.0"

    dl = client.get("/api/v1/installers/wrk_local")
    assert dl.status_code == 200
    assert dl.content == payload
    assert dl.headers["X-Conxa-SHA256"] == sha

    exact = client.get("/api/v1/installers/wrk_local/versions/1.2.0")
    assert exact.status_code == 200
    assert exact.content == payload


def test_installer_upload_allows_build_artifact_larger_than_json_cap():
    payload = b"MZ" + (b"x" * (settings.max_json_body_bytes + 1024))
    up = client.post(
        "/api/v1/workflows/wrk_local/installer/upload?filename=Big-Setup.exe&version=1.2.0&release_notes=Large%20installer",
        content=payload,
    )
    assert up.status_code == 200, up.text
    assert up.json()["size"] == len(payload)


def test_installer_upload_rejects_duplicate_version_and_preserves_history():
    first = b"MZfirst"
    second = b"MZsecond"
    slug = "wrk_local"  # the caller's own slug: installer routes accept no other

    up1 = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Versioned-Setup.exe&version=1.2.0&release_notes=First%20release",
        content=first,
    )
    assert up1.status_code == 200, up1.text

    duplicate = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Versioned-Setup.exe&version=1.2.0&release_notes=Duplicate",
        content=second,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "installer_version_exists"

    up2 = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Versioned-Setup.exe&version=1.2.1&release_notes=Second%20release",
        content=second,
    )
    assert up2.status_code == 200, up2.text

    latest = client.get(f"/api/v1/installers/{slug}")
    assert latest.status_code == 200
    assert latest.content == second

    old = client.get(f"/api/v1/installers/{slug}/versions/1.2.0")
    assert old.status_code == 200
    assert old.content == first

    versions = client.get(f"/api/v1/workflows/{slug}/installer/versions")
    assert versions.status_code == 200, versions.text
    rows = versions.json()["versions"]
    assert [row["version"] for row in rows[:2]] == ["1.2.1", "1.2.0"]
    assert rows[0]["is_latest"] is True
    assert rows[0]["release_notes"] == "Second release"
    assert rows[1]["is_latest"] is False


def test_installer_upload_versions_v2_route_has_no_path_collision():
    """Regression test: post_installer_upload_v2/get_installer_versions_v2 used to be
    registered at `/{installer_version}/installer/upload` and `/{installer_version}/
    installer/versions` — the exact same path template as the legacy `/{slug}/...`
    routes on the same router, so the first (legacy) registration always won and the
    v2 handlers were unreachable. They now live at the param-free `/installer/upload`
    and `/installer/versions`, deriving slug from the authenticated principal."""
    from conxa_core.workspace import workspace_dir_slug
    from app.services.saas import LOCAL_WORKSPACE_ID

    expected_slug = workspace_dir_slug(LOCAL_WORKSPACE_ID)
    body = b"MZv2route"

    up = client.post(
        "/api/v1/workflows/installer/upload?filename=V2-Setup.exe&version=9.0.0&release_notes=v2%20route",
        content=body,
    )
    assert up.status_code == 200, up.text
    assert up.json()["slug"] == expected_slug

    versions = client.get("/api/v1/workflows/installer/versions")
    assert versions.status_code == 200, versions.text
    rows = versions.json()["versions"]
    assert any(row["version"] == "9.0.0" for row in rows)

    # Same data as the legacy, slug-in-path route for the same workspace.
    legacy_versions = client.get(f"/api/v1/workflows/{expected_slug}/installer/versions")
    assert legacy_versions.status_code == 200, legacy_versions.text
    assert {r["version"] for r in legacy_versions.json()["versions"]} == {
        r["version"] for r in rows
    }


def test_installer_history_survives_disk_wipe_but_binary_does_not(monkeypatch, tmp_path):
    """Render's free plan has no persistent disk and idles out, wiping local files
    between requests. Version *history* is Postgres-backed (faked here via the fs KV
    fallback, since `using_database` is forced on) and survives. The .exe itself is
    disk-only — installer upload stores metadata only, deliberately, because a ~20 MB
    base64 blob does not belong in a JSONB field — so a wiped binary is a clean 404
    until the vendor re-uploads. Object storage is the real fix; see TODO.md CLOUD-2."""
    monkeypatch.setattr("app.api.publish_routes.using_database", lambda: True)
    slug = "wrk_local"  # the caller's own slug: installer routes accept no other
    first = b"MZfirst-wipe"
    second = b"MZsecond-wipe"

    up1 = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Wipe-Setup.exe&version=1.0.0&release_notes=v1",
        content=first,
    )
    assert up1.status_code == 200, up1.text

    import shutil
    shutil.rmtree(tmp_path / "installers", ignore_errors=True)

    up2 = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Wipe-Setup.exe&version=1.0.1&release_notes=v2",
        content=second,
    )
    assert up2.status_code == 200, up2.text

    versions = client.get(f"/api/v1/workflows/{slug}/installer/versions")
    assert versions.status_code == 200, versions.text
    rows = {row["version"]: row for row in versions.json()["versions"]}
    assert set(rows) == {"1.0.0", "1.0.1"}
    assert rows["1.0.1"]["is_latest"] is True
    assert rows["1.0.0"]["is_latest"] is False

    # 1.0.1 was uploaded after the wipe, so its bytes are still on disk and serve fine.
    current_download = client.get(f"/api/v1/installers/{slug}/versions/1.0.1")
    assert current_download.status_code == 200
    assert current_download.content == second

    # 1.0.0's bytes went with the wiped disk. History knows the version exists; the
    # download is a clean 404, not a 500 or a truncated file.
    old_download = client.get(f"/api/v1/installers/{slug}/versions/1.0.0")
    assert old_download.status_code == 404
    assert old_download.json()["detail"] == "installer_not_published"

    # Same for "latest" once its bytes are wiped too.
    shutil.rmtree(tmp_path / "installers", ignore_errors=True)
    latest_download = client.get(f"/api/v1/installers/{slug}")
    assert latest_download.status_code == 404
    assert latest_download.json()["detail"] == "installer_not_published"


def test_installer_upload_updates_skill_pack_latest_metadata():
    slug = "wrk_local"  # the caller's own slug: installer routes accept no other
    pub = client.post(
        "/api/v1/workflows/publish",
        json={
            "slug": slug,
            "display_name": "Skill Pack Meta Installer",
            "target_url": "https://example.test",
            "skill_pack_version": "1.0.0",
            "skill_slug": "deploy",
            "files": [],
        },
    )
    assert pub.status_code == 200, pub.text

    up = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Meta-Setup.exe&version=1.4.0&release_notes=Dashboard%20release",
        content=b"MZmeta",
    )
    assert up.status_code == 200, up.text

    packs = client.get("/api/v1/workflows/skill-packs")
    assert packs.status_code == 200
    pack = next(row for row in packs.json()["skill_packs"] if row["display_name"] == "Skill Pack Meta Installer")
    assert pack["installer"]["version"] == "1.4.0"
    assert pack["installer"]["release_notes"] == "Dashboard release"


def test_installer_download_missing_is_404():
    r = client.get("/api/v1/installers/nope-not-here")
    assert r.status_code == 404


# --- SG-05: tracking token fallback must reject, not accept, in production ---

def test_tracking_rejects_unknown_company_when_auth_required(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    r = client.post(
        "/api/tracking/no-such-company/events",
        json={"rid": "run-x", "evts": [{"e": "wf_start", "ts": 1}]},
        headers={"X-Tracking-Token": "whatever"},
    )
    assert r.status_code == 401


def test_tracking_accepts_unknown_company_in_dev_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "tracking_hmac_secret", "")
    r = client.post(
        "/api/tracking/dev-unknown-company/events",
        json={"rid": "run-dev", "evts": [{"e": "wf_start", "ts": 1}]},
        headers={"X-Tracking-Token": "whatever"},
    )
    assert r.status_code == 202


def test_tracking_rejects_unknown_company_when_hmac_secret_set(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "tracking_hmac_secret", "some-secret")
    r = client.post(
        "/api/tracking/no-such-company-2/events",
        json={"rid": "run-y", "evts": [{"e": "wf_start", "ts": 1}]},
        headers={"X-Tracking-Token": "whatever"},
    )
    assert r.status_code == 401


# --- SG-06: telemetry batch/field caps ---------------------------------------

def test_tracking_ingest_caps_oversized_batch_and_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "tracking_max_events_per_batch", 3)
    monkeypatch.setattr(settings, "tracking_max_field_chars", 10)

    pub = client.post(
        "/api/v1/workflows/publish",
        json={"slug": "cap-test", "skill_pack_version": "1.0.0", "skill_slug": "deploy", "files": []},
    )
    assert pub.status_code == 200, pub.text
    token = pub.json()["tracking"]["tracking_token"]

    evts = [{"e": f"event-{i}", "ts": i} for i in range(10)]
    evts[0]["msg"] = "x" * 50  # longer than the 10-char cap

    r = client.post(
        "/api/tracking/cap-test/events",
        json={"rid": "run-cap", "evts": evts},
        headers={"X-Tracking-Token": token},
    )
    assert r.status_code == 202, r.text

    timeline = client.get("/api/v1/tracking/cap-test/runs/run-cap")
    assert timeline.status_code == 200, timeline.text
    stored = timeline.json()["timeline"]
    assert len(stored) == 3  # capped from 10
    assert len(stored[0]["msg"]) == 10  # truncated from 50 chars


def test_tracking_ingest_passes_through_normal_batches_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    pub = client.post(
        "/api/v1/workflows/publish",
        json={"slug": "no-cap-test", "skill_pack_version": "1.0.0", "skill_slug": "deploy", "files": []},
    )
    assert pub.status_code == 200, pub.text
    token = pub.json()["tracking"]["tracking_token"]

    evts = [{"e": "wf_start", "ts": 1}, {"e": "wf_ok", "ts": 2, "dur": 10, "tot": 1, "rec": 0}]
    r = client.post(
        "/api/tracking/no-cap-test/events",
        json={"rid": "run-no-cap", "evts": evts},
        headers={"X-Tracking-Token": token},
    )
    assert r.status_code == 202, r.text

    timeline = client.get("/api/v1/tracking/no-cap-test/runs/run-no-cap")
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["timeline"] == evts


# --- SG-07: signed installer download links ----------------------------------

def test_installer_download_requires_signature_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "installer_signing_key", "installer-secret")
    payload = b"MZ\x90\x00signed-exe-bytes"
    up = client.post(
        "/api/v1/workflows/wrk_local/installer/upload?filename=Signed-Setup.exe&version=1.0.0&release_notes=v1",
        content=payload,
    )
    assert up.status_code == 200, up.text

    # No ts/sig at all -> rejected.
    bare = client.get("/api/v1/installers/wrk_local")
    assert bare.status_code == 401

    # Tampered signature -> rejected.
    ts = int(time.time())
    tampered = client.get(f"/api/v1/installers/wrk_local?ts={ts}&sig=deadbeef")
    assert tampered.status_code == 401

    # Expired timestamp -> rejected.
    old_ts = ts - settings.installer_signing_window - 60
    from app.api.installer_storage import sign_installer
    expired_sig = sign_installer("wrk_local", None, old_ts)
    expired = client.get(f"/api/v1/installers/wrk_local?ts={old_ts}&sig={expired_sig}")
    assert expired.status_code == 401

    # Valid ts/sig -> succeeds.
    valid_sig = sign_installer("wrk_local", None, ts)
    valid = client.get(f"/api/v1/installers/wrk_local?ts={ts}&sig={valid_sig}")
    assert valid.status_code == 200
    assert valid.content == payload


def test_installer_versions_endpoint_mints_signed_download_urls(monkeypatch):
    monkeypatch.setattr(settings, "installer_signing_key", "installer-secret")
    payload = b"MZversioned"
    slug = "wrk_local"  # the caller's own slug: installer routes accept no other
    up = client.post(
        f"/api/v1/workflows/{slug}/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=v1",
        content=payload,
    )
    assert up.status_code == 200, up.text

    versions = client.get(f"/api/v1/workflows/{slug}/installer/versions")
    assert versions.status_code == 200, versions.text
    row = versions.json()["versions"][0]
    assert "ts=" in row["download_url"] and "sig=" in row["download_url"]

    dl = client.get(row["download_url"])
    assert dl.status_code == 200
    assert dl.content == payload


def test_installer_download_stays_public_when_signing_key_unset():
    """Dev-mode fallback: SKILL_INSTALLER_SIGNING_KEY unset -> unsigned downloads still work."""
    payload = b"MZunsigned"
    up = client.post(
        "/api/v1/workflows/wrk_local/installer/upload?filename=Setup.exe&version=1.0.0&release_notes=v1",
        content=payload,
    )
    assert up.status_code == 200, up.text
    dl = client.get("/api/v1/installers/wrk_local")
    assert dl.status_code == 200
    assert dl.content == payload
