"""Studio <-> cloud sharing of app-map contributions (P2), against an in-memory fake cloud."""

from __future__ import annotations

import os
import sys
from urllib.parse import unquote

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_core.config import settings  # noqa: E402
from conxa_core.models.app_graph import Action, Contribution, Screen, WorkflowPath  # noqa: E402
from conxa_core.storage import app_graph_store  # noqa: E402
from services.app_graph_sync import sync  # noqa: E402

WS = "wrk_acme"


class FakeCloud:
    """Just enough of /api/v1/app-graphs: PUT / DELETE / GET list / GET contributions."""

    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.fail_paths: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, path, *, method="GET", body=None):
        self.calls.append((method, path))
        if path in self.fail_paths:
            raise RuntimeError("boom")
        parts = unquote(path).split("/")[4:]          # after /api/v1/app-graphs
        if method == "PUT":
            self.docs[(parts[0], parts[2])] = body
            return {"ok": True}
        if method == "DELETE":
            self.docs.pop((parts[0], parts[2]), None)
            return {"ok": True}
        if not parts:
            counts: dict[str, int] = {}
            for (app, _cid) in self.docs:
                counts[app] = counts.get(app, 0) + 1
            return {"apps": [{"app_key": a, "contributions": n} for a, n in sorted(counts.items())]}
        return {"app_key": parts[0], "contributions": [d for (a, _), d in self.docs.items() if a == parts[0]]}


def contrib(skill, app="drive.example.com", action_name="Upload"):
    return Contribution(
        id=f"skill:{skill}", app_key=app, workspace_id=WS,
        screens=[Screen(id="s1", route="/", sources=[f"skill:{skill}"])],
        actions=[Action(id="a1", from_screen="s1", name=action_name, sources=[f"skill:{skill}"])],
        path=WorkflowPath(skill_id=skill, name=skill, action_ids=["a1"]),
    )


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def put_local(skill, **kw):
    c = contrib(skill, **kw)
    app_graph_store.put_skill_contributions(skill, WS, {c.app_key: c})


def test_pushes_new_contributions_once(store):
    put_local("a")
    cloud = FakeCloud()
    first = sync(WS, cloud)
    assert first["pushed"] == 1 and ("drive.example.com", "skill:a") in cloud.docs
    cloud.calls.clear()
    second = sync(WS, cloud)
    assert second["pushed"] == 0 and not [c for c in cloud.calls if c[0] == "PUT"]


def test_pushes_again_when_the_contribution_changes(store):
    put_local("a")
    cloud = FakeCloud()
    sync(WS, cloud)
    put_local("a", action_name="Upload file")
    assert sync(WS, cloud)["pushed"] == 1
    assert cloud.docs[("drive.example.com", "skill:a")]["actions"][0]["name"] == "Upload file"


def test_a_local_delete_removes_it_from_the_cloud(store):
    put_local("a")
    cloud = FakeCloud()
    sync(WS, cloud)
    app_graph_store.remove_skill("a")
    assert sync(WS, cloud)["deleted"] == 1
    assert cloud.docs == {}


def test_pulls_a_teammates_contribution_into_the_local_map(store):
    cloud = FakeCloud()
    theirs = contrib("theirs")
    cloud.docs[("drive.example.com", "skill:theirs")] = theirs.model_dump(mode="json")
    put_local("mine")
    out = sync(WS, cloud)
    assert out["pulled"] == 1 and out["pushed"] == 1
    graph = app_graph_store.get_graph(WS, "drive.example.com")
    assert {p.skill_id for p in graph.paths} == {"mine", "theirs"}
    # and a second sync does not push the teammate's contribution back as if it were ours
    cloud.calls.clear()
    assert sync(WS, cloud)["pushed"] == 0


def test_a_teammates_contribution_that_disappears_is_removed_here(store):
    cloud = FakeCloud()
    cloud.docs[("drive.example.com", "skill:theirs")] = contrib("theirs").model_dump(mode="json")
    sync(WS, cloud)
    cloud.docs.clear()
    cloud.docs[("other.example.com", "skill:x")] = contrib("x", app="other.example.com").model_dump(mode="json")
    out = sync(WS, cloud)
    assert out["removed"] == 1
    assert app_graph_store.get_graph(WS, "drive.example.com") is None


def test_a_pull_that_fails_never_removes_anything(store):
    cloud = FakeCloud()
    cloud.docs[("drive.example.com", "skill:theirs")] = contrib("theirs").model_dump(mode="json")
    sync(WS, cloud)
    cloud.fail_paths.add("/api/v1/app-graphs/drive.example.com/contributions")
    out = sync(WS, cloud)
    assert out["removed"] == 0 and out["errors"]
    assert app_graph_store.get_graph(WS, "drive.example.com") is not None


def test_a_local_contribution_wins_over_a_remote_one_with_the_same_id(store):
    put_local("a", action_name="Mine")
    cloud = FakeCloud()
    cloud.docs[("drive.example.com", "skill:a")] = contrib("a", action_name="Theirs").model_dump(mode="json")
    sync(WS, cloud)
    graph = app_graph_store.get_graph(WS, "drive.example.com")
    assert graph.actions[0].name == "Mine"


def test_a_failed_push_is_reported_and_retried(store):
    put_local("a")
    cloud = FakeCloud()
    cloud.fail_paths.add("/api/v1/app-graphs/drive.example.com/contributions/skill:a")
    first = sync(WS, cloud)
    assert first["pushed"] == 0 and first["errors"]
    cloud.fail_paths.clear()
    assert sync(WS, cloud)["pushed"] == 1
