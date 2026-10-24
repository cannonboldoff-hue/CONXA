"""App map: what Conxa has learned about HOW a web app is used, across every workflow recorded on it.

A map is a *fold* over contributions. Each compiled workflow contributes the screens, actions and
popups it passed through (one Contribution per host it touched); `fold_contributions` merges them
by id and sums the counts. Nothing is ever edited in place, so re-compiling a workflow replaces its
contribution (no double counting) and deleting a workflow removes it.

The map holds the SHAPE of an app, never anyone's data: no typed values, no page text, no file
names, no selectors. `conxa_compile.app_graph.scrub` is the only way text enters it. See
docs/TRD.md "App maps".
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class Screen(BaseModel):
    id: str                                   # sha16(app_key | route)
    route: str = "/"                          # path with per-run segments as ":id"
    title_tokens: list[str] = Field(default_factory=list)   # scrubbed words of the page title (<=8)
    seen: int = 1
    sources: list[str] = Field(default_factory=list)        # contribution ids


class Action(BaseModel):
    id: str                                   # sha16(from_screen | kind | element identity)
    from_screen: str
    to_screen: str | None = None              # may sit on another host (a sign-in redirect)
    kind: str = "click"
    role: str = ""
    name: str | None = None                   # scrubbed control label; None when it could carry data
    stable_hash: str = ""
    intent: str = ""
    consequence: str = ""
    input_name: str | None = None             # the bound input's NAME (the author's parameter), never a value
    seen: int = 1
    sources: list[str] = Field(default_factory=list)


class PopupOption(BaseModel):
    label: str
    role: str = "neutral"                     # dismiss | consequential | neutral
    chosen: int = 0                           # how many recordings picked this option


class Popup(BaseModel):
    id: str                                   # same formula as the outcome fork's stable hash
    title: str = ""
    button_labels: list[str] = Field(default_factory=list)
    message_tokens: list[str] = Field(default_factory=list)
    popup_class: str = "decision"             # decision | dismissable | consent | form
    container_selector: str = ""
    after_actions: list[str] = Field(default_factory=list)  # action ids this popup was seen after
    appeared: int = 0                         # recordings in which the popup showed up
    absent: int = 0                           # recordings of the same step in which it did not
    options: list[PopupOption] = Field(default_factory=list)
    field_seen: int = 0                       # reported by real runs (P6)
    # The author's standing answer for this popup in this app, set only by
    # handlers/app_graph.py::cmd_set_app_popup_policy (never derived, never from a recording):
    # {"mode": "auto", "option": "<button label>", "author_approved": true} | {"mode": "ask_human"}
    # | {"mode": "stop"[, "message"]}. Applied on top of the fold by the store.
    app_policy: dict[str, Any] | None = None
    sources: list[str] = Field(default_factory=list)


class WorkflowPath(BaseModel):
    skill_id: str
    name: str = ""
    action_ids: list[str] = Field(default_factory=list)     # in order, this host only


class Contribution(BaseModel):
    id: str                                   # "skill:<skill_id>"
    app_key: str
    workspace_id: str = ""
    created_at: float = Field(default_factory=time.time)
    screens: list[Screen] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    popups: list[Popup] = Field(default_factory=list)
    path: WorkflowPath | None = None


class AppGraph(BaseModel):
    app_key: str
    workspace_id: str = ""
    schema_version: int = SCHEMA_VERSION
    built_at: float = 0.0
    screens: list[Screen] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    popups: list[Popup] = Field(default_factory=list)
    paths: list[WorkflowPath] = Field(default_factory=list)
    contribution_ids: list[str] = Field(default_factory=list)


def _union(*lists: list[str], limit: int | None = None) -> list[str]:
    out = sorted({x for lst in lists for x in lst})
    return out[:limit] if limit else out


def fold_contributions(
    app_key: str,
    workspace_id: str,
    contributions: list[Contribution],
    *,
    built_at: float | None = None,
) -> AppGraph:
    """Merge contributions into one graph. Pure and order-independent: the same set of
    contributions always gives the same graph (only `built_at` differs)."""
    screens: dict[str, Screen] = {}
    actions: dict[str, Action] = {}
    popups: dict[str, Popup] = {}
    paths: list[WorkflowPath] = []

    for c in sorted(contributions, key=lambda c: c.id):
        for s in c.screens:
            cur = screens.get(s.id)
            if cur is None:
                screens[s.id] = s.model_copy(deep=True)
            else:
                cur.seen += s.seen
                cur.title_tokens = _union(cur.title_tokens, s.title_tokens, limit=8)
                cur.sources = _union(cur.sources, s.sources)
        for a in c.actions:
            cur = actions.get(a.id)
            if cur is None:
                actions[a.id] = a.model_copy(deep=True)
            else:
                cur.seen += a.seen
                cur.to_screen = cur.to_screen or a.to_screen
                cur.name = cur.name or a.name
                cur.sources = _union(cur.sources, a.sources)
        for p in c.popups:
            cur = popups.get(p.id)
            if cur is None:
                popups[p.id] = p.model_copy(deep=True)
                continue
            cur.appeared += p.appeared
            cur.absent += p.absent
            cur.field_seen += p.field_seen
            cur.after_actions = _union(cur.after_actions, p.after_actions)
            cur.sources = _union(cur.sources, p.sources)
            by_label = {o.label: o for o in cur.options}
            for o in p.options:
                if o.label in by_label:
                    by_label[o.label].chosen += o.chosen
                else:
                    cur.options.append(o.model_copy(deep=True))
        if c.path is not None:
            paths.append(c.path.model_copy(deep=True))

    return AppGraph(
        app_key=app_key,
        workspace_id=workspace_id,
        built_at=time.time() if built_at is None else built_at,
        screens=sorted(screens.values(), key=lambda s: s.id),
        actions=sorted(actions.values(), key=lambda a: a.id),
        popups=sorted(popups.values(), key=lambda p: p.id),
        paths=sorted(paths, key=lambda p: p.skill_id),
        contribution_ids=sorted(c.id for c in contributions),
    )


def graph_summary(graph: AppGraph | dict[str, Any]) -> dict[str, Any]:
    g = graph.model_dump(mode="json") if isinstance(graph, AppGraph) else graph
    return {
        "app_key": g.get("app_key", ""),
        "workspace_id": g.get("workspace_id", ""),
        "workflows": len(g.get("paths") or []),
        "screens": len(g.get("screens") or []),
        "actions": len(g.get("actions") or []),
        "popups": len(g.get("popups") or []),
        "built_at": g.get("built_at", 0.0),
    }
