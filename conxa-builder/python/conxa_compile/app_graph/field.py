"""Popups real runs met, folded into the app map (phase 6).

The cloud counts the runtime's scrubbed ``app_obs`` telemetry per popup shape
(``GET /api/v1/app-graphs/{app}/field``); this turns those rows into entries for the store's
``field.json``. A row that matches a popup some workflow already covers raises that popup's
``field_seen``; one that no workflow covers becomes a field-only popup ("seen in real runs, not in
any workflow"), with button roles from the same consequential-word list the compiler uses, so an
author-set policy on it asks for the same confirmation. Nothing here changes a skill or a policy.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from conxa_compile.app_graph.scrub import scrub_label, scrub_tokens
from conxa_compile.compiler.outcome_fork import option_role
from conxa_core.models.app_graph import Popup, PopupOption
from conxa_core.storage import app_graph_store


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _shape(tokens: list[str], buttons: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(tokens), tuple(sorted(_norm(b) for b in buttons))


def field_entries(graph_popups: list[Popup], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{match_popup_id, popup}] for the cloud's rows. ``graph_popups`` are the workflow-covered
    popups (field-only ones from an earlier ingest are ignored so a re-ingest replaces them)."""
    covered = {
        _shape(scrub_tokens(p.title), p.button_labels): p.id
        for p in graph_popups if p.sources != ["field"]
    }
    out: list[dict[str, Any]] = []
    for row in rows:
        tokens = [str(t) for t in row.get("title_tokens") or []][:8]
        labels = [x for x in (scrub_label(b) for b in row.get("buttons") or []) if x]
        count = int(row.get("count") or 0)
        if len(labels) < 2 or count <= 0:
            continue
        shape = _shape(tokens, labels)
        pid = hashlib.sha256(f"field|{shape}".encode()).hexdigest()[:16]
        out.append({
            "match_popup_id": covered.get(shape),
            "popup": Popup(
                id=pid, title=" ".join(tokens), button_labels=labels, popup_class="decision",
                options=[PopupOption(label=l, role=option_role(l)) for l in labels],
                field_seen=count, sources=["field"],
            ).model_dump(mode="json"),
        })
    return out


def ingest_field(workspace_id: str, app_key: str, rows: list[dict[str, Any]]) -> int:
    """Replace this app's field data. Returns how many popup shapes were kept. Nothing happens for
    an app this machine has no map for."""
    graph = app_graph_store.get_graph(workspace_id, app_key)
    if graph is None:
        return 0
    entries = field_entries(graph.popups, rows)
    app_graph_store.set_field(workspace_id, app_key, entries)
    return len(entries)
