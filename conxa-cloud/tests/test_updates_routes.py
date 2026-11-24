"""Phase 4.5: deps-manifest, conxa-runtime-manifest, and conxa-app-manifest endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_deps_manifest_public_no_auth():
    """Endpoint is public — no Authorization header needed."""
    r = client.get("/api/v1/updates/deps-manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["manifest_version"] == 2
    deps = body["deps"]
    assert "nsis" in deps
    assert "conxa-runtime" in deps
    assert "conxa-app" in deps
    nsis = deps["nsis"]
    assert "version" in nsis
    assert "files" in nsis
    conxa_runtime = deps["conxa-runtime"]
    assert "version" in conxa_runtime
    files = conxa_runtime["files"]
    assert any(f["filename"] == "conxa-runtime.exe" for f in files)


def test_runtime_host_manifest_public_no_auth():
    r = client.get("/api/v1/updates/conxa-runtime-manifest")
    assert r.status_code == 200
    body = r.json()
    assert "host_version" in body
    assert "url" in body
    assert "sha256" in body
    assert "playwright_version" in body
    assert "chromium_revision" in body


def test_runtime_app_manifest_public_no_auth():
    r = client.get("/api/v1/updates/conxa-app-manifest")
    assert r.status_code == 200
    body = r.json()
    assert "app_version" in body
    assert "min_host" in body
    assert "bundle_url" in body
    assert "bundle_sha256" in body


def test_old_runtime_manifest_url_gone():
    r = client.get("/api/v1/updates/runtime-manifest")
    assert r.status_code == 404


def test_deps_manifest_not_in_openapi():
    """include_in_schema=False keeps these out of the public API docs."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    assert "/api/v1/updates/deps-manifest" not in paths
    assert "/api/v1/updates/conxa-runtime-manifest" not in paths
    assert "/api/v1/updates/conxa-app-manifest" not in paths


def test_deps_manifest_env_override(monkeypatch):
    """Env vars let CI update manifest without a redeploy."""
    monkeypatch.setenv("CONXA_NSIS_VERSION", "3.99")
    monkeypatch.setenv("CONXA_NSIS_URL", "https://example.com/nsis-3.99.zip")
    # Re-import to pick up new env (routes read env at import time via module globals)
    import importlib
    import app.api.updates_routes as m
    importlib.reload(m)
    r2 = TestClient(app).get("/api/v1/updates/deps-manifest")
    # The running app still has old values — just verify the module parses env correctly
    assert m._NSIS_VERSION == "3.99"
    assert m._NSIS_URL == "https://example.com/nsis-3.99.zip"


def test_deps_manifest_follows_published_kv_record(monkeypatch, tmp_path):
    """SHIP-1: Studio's deps come from what CI last published, not hand-set env vars."""
    from conxa_core.config import settings
    from conxa_core.db import db_set
    import app.api.updates_routes as m

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def deps():
        return client.get("/api/v1/updates/deps-manifest").json()["deps"]

    # Nothing published yet: env-var defaults.
    assert deps()["conxa-app"]["version"] == m._APP_VERSION
    assert deps()["conxa-runtime"]["version"] == m._HOST_VERSION

    def publish_app(version, sha):
        zip_name = f"conxa-app-{version}.zip"
        db_set("component_versions", "conxa_app", {"version": version, "files": [
            {"filename": zip_name, "url": f"https://x/{zip_name}", "sha256": sha}]})

    publish_app("app-v9.0.0", "a" * 64)
    db_set("component_versions", "conxa_runtime", {"version": "host-v9.0.0", "files": [
        {"filename": "conxa-runtime.exe", "url": "https://x/rt.exe", "sha256": "c" * 64},
        {"filename": "keytar.node", "url": "https://x/k.node", "sha256": "d" * 64}]})
    d = deps()
    assert d["conxa-app"]["version"] == "app-v9.0.0"
    assert d["conxa-app"]["files"][0] == {"filename": "conxa-app-app-v9.0.0.zip",
                                         "url": "https://x/conxa-app-app-v9.0.0.zip",
                                         "sha256": "a" * 64, "action": "extract_zip"}
    rt = {f["filename"]: f for f in d["conxa-runtime"]["files"]}
    assert d["conxa-runtime"]["version"] == "host-v9.0.0"
    assert rt["conxa-runtime.exe"]["sha256"] == "c" * 64 and rt["conxa-runtime.exe"]["url"] == "https://x/rt.exe"
    assert rt["keytar.node"]["sha256"] == "d" * 64

    # A re-publish (new version, or same tag clobbered with a new digest) changes the answer.
    publish_app("app-v9.0.1", "b" * 64)
    f = deps()["conxa-app"]["files"][0]
    assert deps()["conxa-app"]["version"] == "app-v9.0.1"
    assert f["filename"] == "conxa-app-app-v9.0.1.zip" and f["sha256"] == "b" * 64
    publish_app("app-v9.0.1", "e" * 64)
    assert deps()["conxa-app"]["files"][0]["sha256"] == "e" * 64


def test_malformed_digest_env_var_refuses_to_import(monkeypatch):
    """The exact bug found live: a release-notes "<filename>  <hash>" line pasted whole into a
    *_SHA256 env var must fail fast at import time, not silently serve a manifest every client
    then rejects on a checksum mismatch."""
    import importlib
    import app.api.updates_routes as m

    monkeypatch.setenv(
        "CONXA_APP_BUNDLE_SHA256",
        "conxa-app-app-v3.2.0.zip dcd009bf8c1f87c8d500bc4e3f0a169720e31710fddaeace6ef0ab48a27a016f",
    )
    try:
        with pytest.raises(RuntimeError, match="CONXA_APP_BUNDLE_SHA256"):
            importlib.reload(m)
    finally:
        monkeypatch.delenv("CONXA_APP_BUNDLE_SHA256", raising=False)
        importlib.reload(m)  # restore the module to its clean state for later tests


def test_bare_hex_digest_env_var_imports_cleanly(monkeypatch):
    monkeypatch.setenv(
        "CONXA_APP_BUNDLE_SHA256",
        "dcd009bf8c1f87c8d500bc4e3f0a169720e31710fddaeace6ef0ab48a27a016f",
    )
    import importlib
    import app.api.updates_routes as m
    try:
        importlib.reload(m)
        assert m._APP_BUNDLE_SHA == "dcd009bf8c1f87c8d500bc4e3f0a169720e31710fddaeace6ef0ab48a27a016f"
    finally:
        monkeypatch.delenv("CONXA_APP_BUNDLE_SHA256", raising=False)
        importlib.reload(m)


def test_app_zip_url_is_proxied_when_api_base_set(monkeypatch):
    import app.api.updates_routes as m
    monkeypatch.setattr(m.settings, "api_base_url", "https://apis.example")
    app = {"version": "app-v3.2.3", "files": [
        {"filename": "conxa-app-app-v3.2.3.zip", "url": "https://github.com/o/r/releases/download/app-v3.2.3/conxa-app-app-v3.2.3.zip", "sha256": "x"}]}
    out = m._proxy_app_urls(app)["files"][0]
    assert out["url"] == "https://apis.example/api/v1/updates/artifact/app-v3.2.3/conxa-app-app-v3.2.3.zip"
    assert out["sha256"] == "x"
    monkeypatch.setattr(m.settings, "api_base_url", "")
    assert m._proxy_app_urls(app) == app


def test_app_artifact_rejects_non_app_files():
    assert client.get("/api/v1/updates/artifact/host-v3.2.1/conxa-runtime.exe").status_code == 404
    assert client.get("/api/v1/updates/artifact/app-v3.2.3/..%2fevil.zip").status_code == 404


def test_app_artifact_serves_upstream_bytes(monkeypatch):
    import app.api.updates_routes as m

    class R:
        status_code = 200
        content = b"PK-zip"

    seen = {}
    monkeypatch.setattr(m.httpx, "get", lambda url, **kw: seen.update(url=url) or R())
    r = client.get("/api/v1/updates/artifact/app-v3.2.3/conxa-app-app-v3.2.3.zip")
    assert r.status_code == 200 and r.content == b"PK-zip"
    assert seen["url"].endswith("/releases/download/app-v3.2.3/conxa-app-app-v3.2.3.zip")
