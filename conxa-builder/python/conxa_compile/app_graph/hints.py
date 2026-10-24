"""What the app map already knows that a freshly compiled workflow does not cover.

``known_popups`` finds, for each step of a compiled skill, decision popups that OTHER workflows met
right after the same action in the same app, where this skill has no fork. It reuses the extractor's
step walker, so "the same action" means exactly what the map means by it. Pure apart from the
injected graph lookup.
"""

from __future__ import annotations

from typing import Any, Callable

from conxa_compile.app_graph.extract import screen_ref, walk_doc
from conxa_compile.app_graph.scrub import safe_element_name
from conxa_core.models.app_graph import AppGraph
from conxa_core.storage import app_graph_store

GraphFor = Callable[[str], "AppGraph | None"]


def known_popups(
    doc: dict[str, Any],
    *,
    skill_id: str,
    workspace_id: str,
    graph_for: GraphFor | None = None,
) -> list[dict[str, Any]]:
    """[{step_index, popup}] -- ``popup`` is the map's Popup as a dict (scrubbed, data-free)."""
    lookup = graph_for or (lambda host: app_graph_store.get_graph(workspace_id, host))
    steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
    own = f"skill:{skill_id}"
    graphs: dict[str, AppGraph | None] = {}
    out: list[dict[str, Any]] = []
    for w in walk_doc(doc):
        if w.action is None:
            continue
        nxt = steps[w.index + 1] if w.index + 1 < len(steps) else None
        nxt_action = nxt.get("action") if isinstance(nxt, dict) else None
        if (nxt_action.get("action") if isinstance(nxt_action, dict) else nxt_action) == "outcome_fork":
            continue                      # this workflow already handles whatever follows this step
        if w.host not in graphs:
            graphs[w.host] = lookup(w.host)
        graph = graphs[w.host]
        if graph is None:
            continue
        for p in graph.popups:
            if (
                w.action.id in p.after_actions
                and p.popup_class == "decision"
                and p.appeared > 0
                and len(p.options) >= 2
                and set(p.sources) - {own}
            ):
                out.append({"step_index": w.index, "popup": p.model_dump(mode="json")})
    return out


class RecordingHinter:
    """Live hints while someone records: "other workflows sometimes get a popup after this".

    Called with each recorded event (as a dict). Matches it to a map Action by screen (host + route)
    and control label -- the only things a raw event and a compiled step share without a compile --
    and says each popup at most once per recording. Advisory only: it never changes the recording."""

    def __init__(self, workspace_id: str, graph_for: GraphFor | None = None) -> None:
        self._lookup = graph_for or (lambda host: app_graph_store.get_graph(workspace_id, host))
        self._graphs: dict[str, AppGraph | None] = {}
        self._said: set[str] = set()

    def __call__(self, event: dict[str, Any]) -> str | None:
        ref = screen_ref(str((event.get("page") or {}).get("url") or ""))
        if ref is None:
            return None
        host, screen_id = ref
        target = event.get("target") or {}
        label = safe_element_name({"identity_bundle": {"fingerprint": {
            k: target.get(k) for k in ("aria_label", "label_text", "inner_text", "placeholder", "title", "alt", "name")
        }}})
        if not label:
            return None
        if host not in self._graphs:
            self._graphs[host] = self._lookup(host)
        graph = self._graphs[host]
        if graph is None:
            return None
        for action in graph.actions:
            if action.from_screen != screen_id or not action.name or action.name.lower() != label.lower():
                continue
            for p in graph.popups:
                if action.id in p.after_actions and p.popup_class == "decision" and p.appeared > 0 and p.id not in self._said:
                    self._said.add(p.id)
                    buttons = ", ".join(p.button_labels[:4])
                    return (
                        f"Heads up: other workflows on {host} sometimes show “{p.title}” ({buttons}) after "
                        f"“{label}”. If it appears, answer it as you normally would; if it doesn’t, "
                        "I’ll ask afterwards whether to cover it."
                    )
        return None
