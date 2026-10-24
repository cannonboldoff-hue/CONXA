"""Navigation that an action CAUSED is that action's effect, not a step of its own.

Click "Submit" and the application redirects to /orders/847291?token=abc123: the recorder sees a
main-frame navigation, but nothing the user did on their own produced it, and replaying the
literal URL would (a) hardcode a per-run id/token into the skill and (b) force the page there
instead of letting the application decide where the click lands. So such a navigation is folded
into the action that caused it:

  * the synthetic `manual_navigate` event is dropped (nothing to `goto`); and
  * the destination survives only as the causing action's post-condition
    (`post_condition.url_delta`), which build.py::_build_assertions turns into an ADVISORY
    `url_pattern` assertion built by `generalize_url_pattern` — ids/tokens/query stripped.

A `manual_navigate` the user really performed (retyped the address bar, bookmark) is untouched, as
are back/forward, popup and tab events.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

# Same set the recorder stamps as "the user did something" (recorder/session.py).
_INTERACTION_ACTIONS = frozenset({
    "click", "dblclick", "right_click", "type", "fill", "select", "select_option",
    "set_checkbox", "set_radio", "date_pick", "keyboard_shortcut", "submit", "drag_drop",
    "upload", "upload_intent",
})
# Sessions recorded before the recorder tagged navigations with a cause: a navigation this soon
# after an interaction, on the same tab and starting from that interaction's page, is its effect.
_LEGACY_WINDOW_S = 5.0
# An effect the compiler already turns into its own enforced assertion; never overwrite it.
_STRONGER_EFFECTS = frozenset({"dialog_opened", "value_set"})
_DYNAMIC_SEGMENT_MIN_LEN = 16


def _tab_id(ev: dict[str, Any]) -> str:
    return str((ev.get("tab") or {}).get("id") or "")


def _action(ev: dict[str, Any]) -> str:
    return str((ev.get("action") or {}).get("action") or "").lower()


def _timestamp(ev: dict[str, Any]) -> float | None:
    raw = str((ev.get("action") or {}).get("timestamp") or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _navigation_value(ev: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(str((ev.get("action") or {}).get("value") or ""))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _last_interaction(out: list[dict[str, Any]], tab: str) -> dict[str, Any] | None:
    for prev in reversed(out):
        if _tab_id(prev) == tab and _action(prev) in _INTERACTION_ACTIONS:
            return prev
    return None


def _is_action_caused(ev: dict[str, Any], nav: dict[str, Any], cause: dict[str, Any] | None) -> bool:
    if nav.get("cause") == "action":
        return True
    if cause is None:
        return False
    if str((cause.get("page") or {}).get("url") or "") != str(nav.get("from_url") or ""):
        return False
    t_nav, t_cause = _timestamp(ev), _timestamp(cause)
    return t_nav is not None and t_cause is not None and 0 <= t_nav - t_cause <= _LEGACY_WINDOW_S


def fold_action_caused_navigations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop every `manual_navigate` that an action on the same tab caused, recording its
    destination on that action's `post_condition.url_delta`. Everything else passes through."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if _action(ev) != "manual_navigate":
            out.append(ev)
            continue
        nav = _navigation_value(ev)
        cause = _last_interaction(out, _tab_id(ev))
        if not _is_action_caused(ev, nav, cause):
            out.append(ev)
            continue
        to_url = str(nav.get("to_url") or "")
        if cause is not None and to_url:
            pc = cause.get("post_condition")
            pc = pc if isinstance(pc, dict) else {}
            if str(pc.get("classified_effect") or "") not in _STRONGER_EFFECTS and not (pc.get("url_delta") or {}).get("after"):
                pc["classified_effect"] = "navigation"
                pc["url_delta"] = {"before": str(nav.get("from_url") or ""), "after": to_url}
                cause["post_condition"] = pc
    return out


def _is_dynamic_segment(segment: str) -> bool:
    return any(c.isdigit() for c in segment) or len(segment) >= _DYNAMIC_SEGMENT_MIN_LEN


def generalize_url_pattern(url: str) -> str:
    """A regex that matches the STRUCTURE of `url` and none of its per-run values: origin and
    literal path words kept, any segment carrying a digit or a long token (ids, uuids, hashes,
    slugs-with-ids) replaced by a wildcard, query string and fragment dropped. "" when `url` is
    not an absolute http(s) URL."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    path = "".join(
        "/" + (r"[^/?#]+" if _is_dynamic_segment(seg) else re.escape(seg))
        for seg in parts.path.split("/")
        if seg
    )
    return "^" + re.escape(f"{parts.scheme}://{parts.netloc}") + path


def route_template(url: str) -> str:
    """The readable twin of ``generalize_url_pattern``: just the path, with every per-run segment
    (same rule) shown as ``:id``. "/orders/:id", "/" for the root; "" when `url` is not an absolute
    http(s) URL. Used to name a screen in the app map, so two visits to /orders/1 and /orders/2 are
    the same screen."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    segs = [":id" if _is_dynamic_segment(seg) else seg for seg in parts.path.split("/") if seg]
    return "/" + "/".join(segs)


if __name__ == "__main__":
    assert route_template("https://app.com/orders/847291?token=abc123") == "/orders/:id"
    assert route_template("https://app.com") == "/"
    assert route_template("about:blank") == ""
    assert generalize_url_pattern("https://app.com/orders/847291?token=abc123") == r"^https://app\.com/orders/[^/?#]+"
    assert generalize_url_pattern("https://app.com/success/12345#x") == r"^https://app\.com/success/[^/?#]+"
    assert generalize_url_pattern("https://app.com/") == r"^https://app\.com"
    assert generalize_url_pattern("about:blank") == ""
    print("ok")
