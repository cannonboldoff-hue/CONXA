"""Studio's best-effort group upsert must hit the route Cloud actually serves."""

from __future__ import annotations

from types import SimpleNamespace

from handlers.groups import GroupsMixin


class _Studio(GroupsMixin):
    def __init__(self):
        self.calls = []

    def _installer_generation(self):
        return "v2"

    def _cloud_json(self, path, method="GET", body=None):
        self.calls.append((path, method, body))
        return {}


def test_a_group_is_upserted_without_a_workspace_segment(monkeypatch):
    # release_routes.py serves PUT /{installer_version}/groups/{group_id}; a slug segment is a 404.
    monkeypatch.setattr("conxa_core.storage.skill_pack_store.get_skill_pack", lambda ws: None)
    studio = _Studio()
    group = SimpleNamespace(id="10b02fdf-9e05", name="Handoffs", workspace_id="wrk_local")
    assert studio._sync_group_to_cloud(group) is True
    assert studio.calls == [("/api/v1/workflows/v2/groups/10b02fdf-9e05", "PUT",
                             {"group_name": "Handoffs", "display_name": "Local workspace"})]
