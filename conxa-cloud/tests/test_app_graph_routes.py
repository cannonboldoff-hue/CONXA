"""Cloud storage for app-map contributions (P2): workspace-scoped, shape-validated, size-capped,
and never folded server-side."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_principal
from app.main import app
from app.services.saas import Principal
from conxa_core.config import settings
from conxa_core.models.app_graph import Contribution, Popup, Screen


def _principal(ws: str) -> Principal:
    return Principal(user_id=f"user_{ws}", workspace_id=ws, workspace_slug=ws, workspace_name=ws)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    state = {"ws": "wrk_a"}
    app.dependency_overrides[current_principal] = lambda: _principal(state["ws"])
    c = TestClient(app)
    c.state = state  # type: ignore[attr-defined]
    yield c
    app.dependency_overrides.pop(current_principal, None)


def contribution(app_key="drive.example.com", cid="skill:s1", **kw):
    return Contribution(id=cid, app_key=app_key, screens=[Screen(id="a1", route="/x")], **kw).model_dump(mode="json")


def put(client, doc, app_key=None, cid=None):
    return client.put(f"/api/v1/app-graphs/{app_key or doc['app_key']}/contributions/{cid or doc['id']}", json=doc)


def test_round_trip(client):
    assert put(client, contribution()).status_code == 200
    got = client.get("/api/v1/app-graphs/drive.example.com/contributions").json()
    assert [c["id"] for c in got["contributions"]] == ["skill:s1"]
    assert client.get("/api/v1/app-graphs").json()["apps"] == [{"app_key": "drive.example.com", "contributions": 1}]


def test_put_again_replaces(client):
    put(client, contribution())
    put(client, contribution(popups=[Popup(id="p", title="Upload options")]))
    (c,) = client.get("/api/v1/app-graphs/drive.example.com/contributions").json()["contributions"]
    assert c["popups"][0]["title"] == "Upload options"


def test_other_workspaces_never_see_it(client):
    put(client, contribution())
    client.state["ws"] = "wrk_b"
    assert client.get("/api/v1/app-graphs/drive.example.com/contributions").json()["contributions"] == []
    assert client.get("/api/v1/app-graphs").json()["apps"] == []
    client.delete("/api/v1/app-graphs/drive.example.com/contributions/skill:s1")   # b deleting does nothing to a
    client.state["ws"] = "wrk_a"
    assert len(client.get("/api/v1/app-graphs/drive.example.com/contributions").json()["contributions"]) == 1


def test_the_servers_workspace_wins_over_the_bodys(client):
    doc = contribution()
    doc["workspace_id"] = "wrk_victim"
    put(client, doc)
    (c,) = client.get("/api/v1/app-graphs/drive.example.com/contributions").json()["contributions"]
    assert c["workspace_id"] == "wrk_a"


def test_delete(client):
    put(client, contribution())
    assert client.delete("/api/v1/app-graphs/drive.example.com/contributions/skill:s1").status_code == 200
    assert client.get("/api/v1/app-graphs").json()["apps"] == []


def test_rejects_bad_shapes(client):
    assert put(client, contribution(), app_key="Not A Host").status_code == 422
    assert put(client, contribution(), cid="skill:bad id!").status_code == 422
    assert put(client, contribution(), app_key="other.example.com").status_code == 422     # body app_key != path
    assert put(client, contribution(cid="skill:s1"), cid="skill:s2").status_code == 422     # body id != path
    assert client.put("/api/v1/app-graphs/drive.example.com/contributions/skill:s1", json={"screens": "no"}).status_code == 422


def test_size_cap(client):
    big = contribution(popups=[Popup(id=f"p{i}", title="t" * 200, message_tokens=["w"] * 12) for i in range(400)])
    assert put(client, big).status_code == 413


def test_per_app_cap(client, monkeypatch):
    import app.api.app_graph_routes as routes

    monkeypatch.setattr(routes, "MAX_CONTRIBUTIONS_PER_APP", 2)
    assert put(client, contribution(cid="skill:a")).status_code == 200
    assert put(client, contribution(cid="skill:b")).status_code == 200
    assert put(client, contribution(cid="skill:c")).status_code == 409
    assert put(client, contribution(cid="skill:a")).status_code == 200      # replacing an existing one is fine


# ─── field observations (app_obs telemetry) ─────────────────────────────────────────────────

def _obs(host="drive.example.com", tt=("discard", "changes"), bt=("Discard", "Keep editing"), k=0, ts=1000):
    return {"e": "app_obs", "ts": ts, "si": 3, "host": host, "rt": "/x", "tt": list(tt), "bt": list(bt), "cl": "decision", "k": k}


def _records(*runs):
    return [{"summary": {"run_id": f"run{i}"}, "events": list(evts)} for i, evts in enumerate(runs)]


@pytest.fixture()
def field(client, monkeypatch):
    import app.api.app_graph_routes as routes

    monkeypatch.setattr(routes, "ensure_ops_tier", lambda *_a, **_k: None)

    def seed(*runs):
        monkeypatch.setattr(routes, "_visible_run_records", lambda _p: _records(*runs))

    return client, seed


def test_field_counts_distinct_runs_per_popup_shape(field):
    client, seed = field
    seed([_obs(), _obs()], [_obs(ts=2000, k=1)], [_obs(bt=("Replace", "Keep both"), tt=("upload", "options"))])
    got = client.get("/api/v1/app-graphs/drive.example.com/field").json()
    assert got["app_key"] == "drive.example.com"
    first, second = got["popups"]
    assert first["title_tokens"] == ["discard", "changes"] and first["count"] == 2     # two runs, not three events
    assert first["known"] is True and first["last_seen"] == 2000
    assert second["buttons"] == ["Replace", "Keep both"] and second["count"] == 1


def test_field_ignores_other_apps_other_events_and_junk(field):
    client, seed = field
    seed([
        _obs(host="other.example.com"),
        {"e": "step_fail", "host": "drive.example.com"},
        _obs(bt=("Only",)),                                   # not a choice
        {**_obs(), "bt": "not-a-list"},
        {**_obs(), "tt": ["x" * 500] * 50, "bt": ["A" * 500, "B" * 500] + ["c"] * 40},   # oversized: clipped, not rejected
    ])
    (p,) = client.get("/api/v1/app-graphs/drive.example.com/field").json()["popups"]
    assert len(p["title_tokens"]) == 8 and all(len(t) <= 40 for t in p["title_tokens"])
    assert len(p["buttons"]) == 6 and all(len(b) <= 40 for b in p["buttons"])


def test_field_is_gated_like_the_drift_queue(client, monkeypatch):
    import app.api.app_graph_routes as routes
    from app.services.entitlements import EntitlementError

    def deny(*_a, **_k):
        raise EntitlementError("ops_tier_required", 402)

    monkeypatch.setattr(routes, "ensure_ops_tier", deny)
    monkeypatch.setattr(routes, "_visible_run_records", lambda _p: pytest.fail("must not read telemetry when gated"))
    assert client.get("/api/v1/app-graphs/drive.example.com/field").status_code == 402


def test_field_rejects_a_bad_app_key(field):
    client, _seed = field
    assert client.get("/api/v1/app-graphs/Not A Host/field").status_code == 422
