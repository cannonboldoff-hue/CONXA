"""A compiled skill document -> its contributions to the app maps (one per host it touched).

Walks the COMPILED steps (not the raw recording) so everything the author fixed in review is what
the map learns. Pure: no I/O, no LLM.

A screen is identified by host + route (``navigation_effects.route_template``): "/orders/:id" is one
screen however many orders were opened. v1 limitation: single-page apps that keep the URL while the
view changes collapse into one screen; the compiled steps do not carry the page landmarks needed to
split them (TODO APP-1).
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Iterator, NamedTuple
from urllib.parse import urlsplit

from conxa_compile.app_graph.scrub import safe_element_name, scrub_label, scrub_tokens
from conxa_compile.compiler.navigation_effects import route_template
from conxa_core.models.app_graph import Action, Contribution, Popup, PopupOption, Screen, WorkflowPath

# Navigation markers and branch bodies are not things a person does on a screen.
_SKIP_ACTIONS = frozenset({"frame_enter", "frame_exit", "try_dismiss"})
_FORK = "outcome_fork"


def _sha16(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def app_key_of(url: str) -> str:
    """The app a URL belongs to: its lowercase hostname without a leading "www."."""
    try:
        host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _action_name(step: dict[str, Any]) -> str:
    a = step.get("action")
    return str(a.get("action") if isinstance(a, dict) else a or "")


def screen_ref(url: str) -> tuple[str, str] | None:
    """(app_key, screen id) for a URL, or None when it is not an absolute http(s) URL."""
    key = app_key_of(url)
    route = route_template(url)
    return (key, _sha16(key, route)) if key and route else None


def _screen(url: str, title: Any) -> tuple[str, Screen] | None:
    key = app_key_of(url)
    route = route_template(url)
    if not key or not route:
        return None
    return key, Screen(id=_sha16(key, route), route=route, title_tokens=scrub_tokens(title))


def _popups_from_fork(step: dict[str, Any], after_action: str, source: str) -> list[Popup]:
    fork = step.get("outcome_fork") or {}
    clean = fork.get("clean") or {}
    absent = len(clean.get("observed_in") or []) or (1 if clean.get("observed") else 0)
    out: list[Popup] = []
    for state in fork.get("states") or []:
        sig = state.get("signature") or {}
        title = scrub_label(sig.get("title")) or ""
        labels = [x for x in (scrub_label(l) for l in sig.get("button_labels") or []) if x]
        if not title and not labels:
            continue
        container = str(sig.get("container_selector") or "")
        appeared = len(state.get("observed_in") or []) or (0 if state.get("from_map") else 1)
        options = [
            PopupOption(
                label=lab,
                role=str(o.get("role") or "neutral"),
                chosen=appeared if o.get("recorded") else 0,
            )
            for o in state.get("options") or []
            if (lab := scrub_label(o.get("label")))
        ]
        out.append(Popup(
            id=_sha16("fork", container, _norm(title), "|".join(sorted(_norm(l) for l in labels))),
            title=title,
            button_labels=labels,
            message_tokens=[str(t) for t in (sig.get("message_tokens") or [])][:12],
            popup_class="decision",
            container_selector=container,
            after_actions=[after_action] if after_action else [],
            appeared=appeared,
            absent=absent,
            options=options,
            sources=[source],
        ))
    return out


class Walked(NamedTuple):
    """One step of a skill document, placed in an app. ``action`` is None for an outcome_fork step
    (``host`` is then the host of the action before it)."""
    index: int
    step: dict[str, Any]
    host: str
    action: Action | None
    from_screen: Screen | None
    to: tuple[str, Screen] | None


def walk_doc(doc: dict[str, Any]) -> Iterator[Walked]:
    """The doc's steps in order with the screens, host and Action each one maps to. The single place
    that decides an action's identity, shared by the extractor and the hint matcher so they can
    never disagree about whether two workflows did "the same thing"."""
    skills = doc.get("skills") or []
    block = skills[0] if skills and isinstance(skills[0], dict) else {}
    prev_url = ""
    last_host = ""
    for index, step in enumerate(block.get("steps") or []):
        if not isinstance(step, dict):
            continue
        name_ = _action_name(step)
        if name_ == _FORK:
            yield Walked(index, step, last_host, None, None, None)
            continue
        if name_ in _SKIP_ACTIONS or step.get("branch"):
            continue
        state = step.get("state") or {}
        before = state.get("before") or {}
        after = state.get("after") or {}
        from_url = str(before.get("url") or prev_url or step.get("url") or "")
        to_url = str(after.get("url") or step.get("url") or from_url)
        frm = _screen(from_url, before.get("page_title"))
        if frm is None:
            continue
        host, from_screen = frm
        to = _screen(to_url, after.get("page_title"))
        prev_url = to_url or prev_url
        bundle = step.get("identity_bundle") or {}
        fp = bundle.get("fingerprint") or {}
        stable = str(bundle.get("stable_hash") or "")
        action = Action(
            id=_sha16(from_screen.id, name_, stable or str(step.get("intent") or "")),
            from_screen=from_screen.id,
            to_screen=to[1].id if to is not None else None,
            kind=name_ or "click",
            role=str(fp.get("role") or fp.get("tag") or ""),
            name=safe_element_name(step),
            stable_hash=stable,
            intent=str(step.get("intent") or ""),
            consequence=str(step.get("consequence") or ""),
            input_name=str(step["input_binding"]) if step.get("input_binding") else None,
        )
        last_host = host
        yield Walked(index, step, host, action, from_screen, to)


def contributions_from_doc(
    doc: dict[str, Any],
    *,
    skill_id: str,
    workspace_id: str = "",
    name: str = "",
) -> dict[str, Contribution]:
    """{app_key: Contribution} for every host the skill's steps ran on."""
    source = f"skill:{skill_id}"
    title = name or str((doc.get("meta") or {}).get("title") or "")
    by_host: dict[str, Contribution] = {}

    def contrib(host: str) -> Contribution:
        if host not in by_host:
            by_host[host] = Contribution(
                id=source, app_key=host, workspace_id=workspace_id, created_at=time.time(),
                path=WorkflowPath(skill_id=skill_id, name=title),
            )
        return by_host[host]

    def add_screen(host: str, screen: Screen) -> None:
        c = contrib(host)
        if all(s.id != screen.id for s in c.screens):
            c.screens.append(screen.model_copy(update={"sources": [source]}))

    last_action = ""
    for w in walk_doc(doc):
        if w.action is None:                       # an outcome_fork
            if w.host:
                for popup in _popups_from_fork(w.step, last_action, source):
                    contrib(w.host).popups.append(popup)
            continue
        add_screen(w.host, w.from_screen)
        if w.to is not None:
            add_screen(w.to[0], w.to[1])
        action = w.action.model_copy(update={"sources": [source]})
        c = contrib(w.host)
        existing = next((a for a in c.actions if a.id == action.id), None)
        if existing is not None:
            existing.seen += 1
        else:
            c.actions.append(action)
        c.path.action_ids.append(action.id)
        last_action = action.id

    return by_host
