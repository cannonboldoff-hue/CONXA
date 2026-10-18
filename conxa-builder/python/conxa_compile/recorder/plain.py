"""Plain-language rows for the live "Captured so far" list (BUILD-38).

A person recording a task wants to see "Typed “PO-44821” into PO number", not an event kind and a
selector. This turns one recorded event into ``{sequence, kind, text, value, can_vary}``:

- ``kind`` is the chip category (click / type / navigate / select / scroll / other) — the same
  vocabulary ``renderer/src/lib/status.ts`` colours.
- ``value`` is the typed text, shown so the "Changes each run?" toggle makes sense next to it.
- ``can_vary`` is True only for a typed value the person could reasonably turn into an input.
  A redacted (password-like) field never offers it: its value is not recorded at all.

Pure and dependency-free of Playwright, so it is unit-testable from an event dict.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

_REDACTED = "{{REDACTED}}"
_MAX = 48


def _clip(text: str, n: int = _MAX) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _name_of(target: dict[str, Any]) -> str:
    """The best human name for an element: what a person would call it."""
    for key in ("aria_label", "label_text", "placeholder", "inner_text", "title", "alt", "name"):
        v = _clip(target.get(key) or "")
        if v:
            return v
    return str(target.get("tag") or "element")


def _page_name(url: str) -> str:
    try:
        u = urlparse(url)
    except ValueError:
        return url
    path = [p for p in u.path.split("/") if p]
    if path:
        return _clip(path[-1].replace("-", " ").replace("_", " "))
    return u.netloc or url


def describe_event(event: dict[str, Any]) -> dict[str, Any]:
    """``event`` is ``RecordedEvent.model_dump(mode="json")``."""
    action = event.get("action") or {}
    kind_raw = str(action.get("action") or "")
    value = action.get("value")
    target = event.get("target") or {}
    page = event.get("page") or {}
    seq = int((event.get("extras") or {}).get("sequence") or 0)
    name = _name_of(target)

    kind, text, shown_value, can_vary = "other", kind_raw.replace("_", " ").capitalize() or "Action", None, False

    if kind_raw in ("navigate", "manual_navigate", "browser_back", "browser_forward"):
        kind = "navigate"
        text = {"browser_back": "Went back", "browser_forward": "Went forward"}.get(kind_raw) or f"Opened the {_page_name(str(page.get('url') or value or ''))} page"
    elif kind_raw in ("click", "double_click", "right_click", "dblclick"):
        kind, text = "click", f"Clicked “{name}”"
    elif kind_raw in ("type", "fill", "input"):
        kind = "type"
        if value == _REDACTED:
            text = f"Typed a hidden value into {name}"
        else:
            shown_value = _clip(str(value or ""), 40)
            text = f"Typed “{shown_value}” into {name}"
            can_vary = bool(shown_value)
    elif kind_raw == "keyboard_shortcut":
        kind, text = "type", "Pressed a key"
    elif kind_raw in ("select", "select_option"):
        kind = "select"
        shown_value = _clip(str(value or ""), 40) or None
        text = f"Chose “{shown_value}” in {name}" if shown_value else f"Chose an option in {name}"
        can_vary = bool(shown_value)
    elif kind_raw in ("check", "uncheck"):
        kind, text = "click", f"{'Ticked' if kind_raw == 'check' else 'Unticked'} “{name}”"
    elif kind_raw in ("scroll",):
        kind, text = "scroll", "Scrolled"
    elif kind_raw in ("hover", "mouseover"):
        kind, text = "hover", f"Pointed at “{name}”"
    elif kind_raw in ("upload", "upload_intent", "file_chooser_opened"):
        text = "Chose a file to upload"
    elif kind_raw == "download_observed":
        text = "Downloaded a file"
    elif kind_raw in ("dialog_appeared", "dialog_accept", "dialog_dismiss"):
        text = "Answered a pop-up message"
    elif kind_raw in ("tab_open", "popup"):
        text = "A new tab opened"
    elif kind_raw == "tab_switch":
        text = "Switched tab"
    elif kind_raw in ("frame_enter", "frame_exit"):
        text = "Moved into a page section" if kind_raw == "frame_enter" else "Moved back out of a page section"

    return {
        "sequence": seq,
        "kind": kind,
        "text": text,
        "value": shown_value,
        "can_vary": can_vary,
        "tab": str((event.get("tab") or {}).get("id") or ""),
    }
