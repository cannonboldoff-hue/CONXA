"""The slice of an app map that ships to a customer's machine: ``app_knowledge.json``.

Only decision POPUPS of the hosts a skill actually visits -- title, button labels, message words,
dialog container, the buttons' roles and the author's standing policy. No screens, no actions, no
workflow names or paths (the runtime has no use for them and they are the author's business). Every
string already went through ``app_graph.scrub``. It is advisory: an old or missing file just means
the runtime knows nothing extra, and the file only ever changes when the skill is published again
(the same approval-by-publishing rule as every other skill file).

Runtime side: ``runtime/app/app_knowledge.js`` + ``run.js::runForkStep`` (an unrecognised popup that
the map knows) -- see docs/TRD.md section 11.5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from conxa_compile.app_graph.extract import app_key_of
from conxa_core.models.app_graph import AppGraph
from conxa_core.storage import app_graph_store

FILE_NAME = "app_knowledge.json"
SCHEMA_VERSION = 1
MAX_POPUPS_PER_HOST = 40

GraphFor = Callable[[str], "AppGraph | None"]


def build_app_knowledge(
    workspace_id: str,
    hosts: list[str],
    graph_for: GraphFor | None = None,
) -> dict[str, Any] | None:
    """{schema_version, apps: {host: {popups: [...]}}} for the given hosts, or None when the map
    knows no popup on any of them."""
    lookup = graph_for or (lambda host: app_graph_store.get_graph(workspace_id, host))
    apps: dict[str, Any] = {}
    for raw in hosts:
        host = app_key_of(raw if "//" in str(raw) else f"https://{raw}")
        if not host or host in apps:
            continue
        graph = lookup(host)
        if graph is None:
            continue
        seen = [p for p in graph.popups if (p.appeared > 0 or p.field_seen > 0) and len(p.button_labels) >= 2]
        seen.sort(key=lambda p: (-(p.appeared + p.field_seen), p.id))
        popups = [{
            "id": p.id,
            "class": p.popup_class,
            "signature": {
                "title": p.title,
                "button_labels": list(p.button_labels),
                "message_tokens": list(p.message_tokens),
                "container_selector": p.container_selector,
            },
            "options": [{"label": o.label, "role": o.role, "chosen": o.chosen} for o in p.options],
            "app_policy": p.app_policy,
        } for p in seen[:MAX_POPUPS_PER_HOST]]
        if popups:
            apps[host] = {"popups": popups}
    return {"schema_version": SCHEMA_VERSION, "apps": apps} if apps else None


def write_app_knowledge(skill_dir: Path, workspace_id: str, hosts: list[str], graph_for: GraphFor | None = None) -> bool:
    """Write ``skill_dir/app_knowledge.json`` (or remove a stale one). Returns whether one was written."""
    path = skill_dir / FILE_NAME
    knowledge = build_app_knowledge(workspace_id, hosts, graph_for)
    if knowledge is None:
        path.unlink(missing_ok=True)
        return False
    path.write_text(json.dumps(knowledge, indent=2, ensure_ascii=False), encoding="utf-8")
    return True
