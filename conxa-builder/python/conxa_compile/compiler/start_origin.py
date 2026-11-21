"""Mark the address checks that name the recording's start site, so a run can follow another start site (BUILD-101 (6)).

A landing check (`url_pattern`, and the `url_stays` look built from it) is written as `^<escaped site><path>`
(navigation_effects.py::generalize_url_pattern). When the person later changes the workflow's start page to another
site of the same app, the check still names the old site and fails a run that did exactly what it should. A check whose
site is the recording's start site is stamped with that site (`start_origin`); the runtime swaps it for the site the run
starts on (runtime/app/start_origin.js). A check on any other site (a sign-in host, a payment page) is not stamped, so
it keeps naming where it was recorded.

Pure, no model. Reaches every step including popup-option, loop and branch bodies, whatever form the step is in (a
SkillStep while compiling, a dict once saved).
"""

from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import urlsplit

from conxa_core.models.step_identity import child_step_lists

from conxa_compile.editor.step_path import walk_steps


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _origin(url: Any) -> str:
    parts = urlsplit(str(url or "").strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def start_origin_of(steps: list[Any]) -> str:
    """The site of the workflow's first navigate step ("" when it has none, or it is not an http(s) address)."""
    for s in steps:
        action = _get(s, "action")
        name = action.get("action") if isinstance(action, dict) else action
        if str(name or "").lower() == "navigate":
            return _origin(_get(s, "url"))
    return ""


def _all_steps(steps: list[Any]) -> Iterator[Any]:
    for s in steps:
        yield s
        for _suffix, body in child_step_lists(s):
            yield from walk_steps(body)


def _set(obj: Any, name: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def stamp_start_origin(steps: list[Any]) -> int:
    """Stamp `start_origin` on every address check that names the start site. Returns how many were stamped."""
    origin = start_origin_of(steps)
    if not origin:
        return 0
    prefix = "^" + re.escape(origin)
    stamped = 0
    for step in _all_steps(steps):
        validation = _get(step, "validation")
        for a in _get(validation, "assertions", None) or []:
            if _get(a, "type") == "url_pattern" and str(_get(a, "target") or "").startswith(prefix):
                _set(a, "start_origin", origin)
                stamped += 1
        for c in _get(_get(validation, "effect", None), "checks", None) or []:
            probe = _get(c, "probe")
            if isinstance(probe, dict) and probe.get("type") == "url_stays" and str(probe.get("pattern") or "").startswith(prefix):
                probe["start_origin"] = origin
                stamped += 1
    return stamped
