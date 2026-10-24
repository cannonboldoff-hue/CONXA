"""App map command handlers.

The map itself is kept up to date by hooks on every skill write (see conxa_compile.app_graph);
this mixin holds the commands the Studio calls on top of it.
"""

from __future__ import annotations

from typing import Any


class AppGraphMixin:
    def cmd_rebuild_app_graphs(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Backfill: recompute every app map from the workflows already compiled on this machine."""
        from conxa_compile.app_graph import rebuild_all  # noqa: PLC0415

        return {"rebuilt": rebuild_all()}

    def cmd_list_app_graphs(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """The apps Studio has a map for. All workspaces on this machine unless one is named."""
        from conxa_core.storage import app_graph_store  # noqa: PLC0415

        workspace_id = str(payload.get("workspace_id") or "").strip()
        apps = app_graph_store.list_apps(workspace_id) if workspace_id else app_graph_store.list_all_apps()
        return {"apps": apps}

    def cmd_get_app_graph(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage import app_graph_store  # noqa: PLC0415
        from handlers.protocol import _CommandError  # noqa: PLC0415

        app_key = str(payload.get("app_key") or "").strip()
        graph = app_graph_store.get_graph(str(payload.get("workspace_id") or ""), app_key)
        if graph is None:
            raise _CommandError("app_graph_not_found", f"No map for {app_key or 'that app'} yet")
        return {"graph": graph.model_dump(mode="json")}

    def cmd_set_app_popup_policy(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """The author's standing answer for one popup of one app: the ONLY writer of
        ``Popup.app_policy`` and so of its ``author_approved``. Takes effect on customers' machines
        the next time a skill touching that app is published.

        ``policy``: ``{"mode": "auto", "option": "<button label>"}`` (picking it here IS the
        approval; a consequential option additionally needs ``confirm_risky: true``),
        ``{"mode": "ask_human"}``, ``{"mode": "stop"[, "message"]}``, or ``{"mode": "clear"}``."""
        from conxa_core.storage import app_graph_store  # noqa: PLC0415
        from conxa_core.workspace import LOCAL_WORKSPACE_ID  # noqa: PLC0415
        from handlers.protocol import _CommandError  # noqa: PLC0415

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        app_key = str(payload.get("app_key") or "").strip()
        popup_id = str(payload.get("popup_id") or "").strip()
        graph = app_graph_store.get_graph(workspace_id, app_key)
        popup = next((p for p in graph.popups if p.id == popup_id), None) if graph else None
        if popup is None:
            raise _CommandError("popup_not_found", "That popup is not in this app's map.")
        raw = payload.get("policy") if isinstance(payload.get("policy"), dict) else {}
        mode = str(raw.get("mode") or "")
        policy: dict[str, Any] | None
        if mode == "clear":
            policy = None
        elif mode == "auto":
            label = str(raw.get("option") or "")
            option = next((o for o in popup.options if o.label == label), None)
            if option is None:
                raise _CommandError("option_not_found", f"'{label}' is not one of this popup's buttons.")
            if option.role == "consequential" and payload.get("confirm_risky") is not True:
                raise _CommandError("confirmation_required", f"'{label}' can change or remove data. Confirm to let it run without asking.")
            policy = {"mode": "auto", "option": option.label, "author_approved": True}
        elif mode == "ask_human":
            policy = {"mode": "ask_human"}
        elif mode == "stop":
            policy = {"mode": "stop", "message": str(raw.get("message") or f"Needs a decision: {popup.title or 'a popup'}")}
        else:
            raise _CommandError("invalid_policy", "Unknown policy mode.")
        app_graph_store.set_popup_policy(workspace_id, app_key, popup_id, policy)
        return {"ok": True, "app_policy": policy}

    def cmd_sync_app_graphs(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Share this workspace's app maps with teammates through Conxa Cloud (push what changed,
        pull what teammates have)."""
        from conxa_core.workspace import LOCAL_WORKSPACE_ID  # noqa: PLC0415
        from services.app_graph_sync import pull_field, sync  # noqa: PLC0415

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        out = sync(workspace_id, self._cloud_json)
        field = pull_field(workspace_id, self._cloud_json)
        return {**out, **field, "errors": [*out["errors"], *field["errors"]]}

    def _sync_app_graphs_quietly(self, workspace_id: str) -> None:
        """Best-effort sync for flows that must never fail because of it (publishing)."""
        try:
            self.cmd_sync_app_graphs({"workspace_id": workspace_id}, "")
        except Exception:  # noqa: BLE001 -- derived data
            pass
