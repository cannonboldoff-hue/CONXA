"""The Studio's pack.json sync_endpoint and the cloud publish must name the same delta route."""

from app.main import app
from conxa_core.sync_paths import sync_delta_path


def _routes():
    return set(app.openapi()["paths"])


def test_versioned_path_is_a_real_cloud_route():
    assert sync_delta_path("render", "v2") == "/api/v1/workflows/v2/render/skill-packs/delta"
    assert "/api/v1/workflows/{installer_version}/{workspace_id}/skill-packs/delta" in _routes()


def test_default_generation_is_versioned():
    assert sync_delta_path("render") == sync_delta_path("render", "v2")


def test_empty_generation_is_the_legacy_route():
    assert sync_delta_path("render", None) == "/api/v1/skill-packs/render/delta"
    assert "/api/v1/skill-packs/{workspace_id}/delta" in _routes()


def test_publish_routes_uses_the_helper(monkeypatch):
    import inspect
    from app.api import publish_routes

    src = inspect.getsource(publish_routes)
    assert "sync_delta_path(slug, installer_version)" in src
    assert "/skill-packs/delta" not in src.replace("sync_delta_path", "")
