"""URL/iframe fingerprinting and frame-context helpers for the recorder.

Split out of session.py: these are pure functions with no dependency on
RecordingSession's instance state — normalizing frame URLs into stable
patterns, building iframe identity fingerprints, and reading Playwright
frame/page geometry. RecordingSession calls into these; nothing here calls
back into the class.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from conxa_core.models.events import RecordedEvent
from conxa_compile.policy.bundle import get_policy_bundle


_URL_DYNAMIC_SEG = re.compile(r"^(?:[0-9]+|[0-9a-f]{8,}|[A-Za-z0-9_-]{16,})$")


_URL_VOLATILE_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ts", "_", "t", "ref",
})


def _normalize_frame_url_pattern(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        host_part = re.escape(parsed.scheme + "://" + parsed.netloc)
        segments = parsed.path.split("/")
        normalized = [
            "[^/]+" if _URL_DYNAMIC_SEG.match(seg) else re.escape(seg)
            for seg in segments
        ]
        path_pattern = "/".join(normalized)
        qs = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _URL_VOLATILE_PARAMS and not k.startswith("utm_")]
        query_suffix = ("\\?" + re.escape(urlencode(qs))) if qs else ""
        return f"^{host_part}{path_pattern}{query_suffix}$"
    except Exception:
        return ""


def _css_attr_selector(tag: str, attr: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'{tag}[{attr}="{escaped}"]'


# Maps (attr_name, css_attr) → (engine, base_durability) for FrameFingerprint signal ranking.
_FRAME_ATTR_SIGNALS: list[tuple[str, str, str, float]] = [
    ("data-test-id",       "data-test-id",       "testid",        0.99),
    ("data-selenium-test", "data-selenium-test",  "testid",        0.99),
    ("aria-label",         "aria-label",          "aria",          0.95),
    ("name",               "name",                "name",          0.95),
    ("title",              "title",               "text_based",    0.85),
    ("id",                 "id",                  "css-id",        0.45),
]


def _iframe_fingerprint_from_attrs(attrs: dict[str, Any], frame_url: str = "") -> dict[str, Any]:
    """Return a FrameFingerprint-compatible dict with durability-ranked signals."""
    from conxa_compile.compiler.selector_score import tag_orthogonality_class
    signals: list[dict[str, Any]] = []
    seen_selectors: set[str] = set()
    for attr, css_attr, engine, durability in _FRAME_ATTR_SIGNALS:
        val = str(attrs.get(attr) or "").strip()
        if not val:
            continue
        selector = _css_attr_selector("iframe", css_attr, val)
        if not selector or selector in seen_selectors:
            continue
        seen_selectors.add(selector)
        signals.append({
            "engine": engine,
            "selector": selector,
            "durability": durability,
            "orthogonality_class": tag_orthogonality_class(engine),
            "unique_at_compile": False,
            "source": "compiler",
        })
    # src_pattern signal (structural, url-based)
    src = str(attrs.get("src") or "").strip()
    if src:
        url_pattern = _normalize_frame_url_pattern(src)
        if url_pattern:
            signals.append({
                "engine": "css-structural",
                "selector": f'iframe[src="{src}"]',
                "durability": 0.50,
                "orthogonality_class": tag_orthogonality_class("css-structural"),
                "unique_at_compile": False,
                "source": "compiler",
            })
    # Sort descending by durability
    signals.sort(key=lambda s: s["durability"], reverse=True)
    return {
        "signals": signals,
        "url": frame_url,
        "url_pattern": _normalize_frame_url_pattern(frame_url),
    }


def _frame_parent(frame: Any) -> Any | None:
    parent = getattr(frame, "parent_frame", None)
    if callable(parent):
        try:
            return parent()
        except Exception:
            return None
    return parent


def _frame_url(frame: Any) -> str:
    raw = getattr(frame, "url", "")
    try:
        raw = raw() if callable(raw) else raw
    except Exception:
        return ""
    return str(raw or "")


def _frame_element_attrs_and_rect(frame: Any) -> tuple[dict[str, Any], dict[str, float]]:
    handle = frame.frame_element()
    attrs = handle.evaluate(
        """el => ({
          id: el.getAttribute("id") || "",
          "data-test-id": el.getAttribute("data-test-id") || "",
          "data-selenium-test": el.getAttribute("data-selenium-test") || "",
          name: el.getAttribute("name") || "",
          title: el.getAttribute("title") || "",
          "aria-label": el.getAttribute("aria-label") || "",
          src: el.getAttribute("src") || ""
        })"""
    )
    rect = handle.evaluate(
        """el => {
          const r = el.getBoundingClientRect();
          return { x: r.left || 0, y: r.top || 0, w: r.width || 0, h: r.height || 0 };
        }"""
    )
    return (
        attrs if isinstance(attrs, dict) else {},
        rect if isinstance(rect, dict) else {},
    )


def _frame_context_and_offset_sync(frame: Any | None) -> tuple[dict[str, Any], dict[str, float]]:
    if frame is None or _frame_parent(frame) is None:
        return {}, {"x": 0.0, "y": 0.0}

    frames: list[Any] = []
    cur = frame
    while cur is not None and _frame_parent(cur) is not None:
        frames.append(cur)
        cur = _frame_parent(cur)
    frames.reverse()

    chain: list[dict[str, Any]] = []
    offset = {"x": 0.0, "y": 0.0}
    for item in frames:
        try:
            attrs, rect = _frame_element_attrs_and_rect(item)
        except Exception:
            continue
        frame_url = _frame_url(item)
        fingerprint = _iframe_fingerprint_from_attrs(attrs, frame_url)
        if not fingerprint.get("signals"):
            continue
        spec = {
            "url": frame_url or str(attrs.get("src") or ""),
            "url_pattern": _normalize_frame_url_pattern(frame_url),
            "fingerprint": fingerprint,
        }
        chain.append(spec)
        try:
            offset["x"] += float(rect.get("x") or 0)
            offset["y"] += float(rect.get("y") or 0)
        except (TypeError, ValueError):
            pass

    return ({"chain": chain} if chain else {}), offset


def _viewport_string_from_page(page: Any) -> str:
    try:
        size = getattr(page, "viewport_size", None)
        size = size() if callable(size) else size
        if isinstance(size, dict):
            width = int(size.get("width") or 0)
            height = int(size.get("height") or 0)
            if width > 0 and height > 0:
                return f"{width}x{height}"
    except Exception:
        pass
    try:
        return str(page.evaluate("() => `${Math.round(window.innerWidth)}x${Math.round(window.innerHeight)}`") or "")
    except Exception:
        return ""


_LOGIN_URL_PATTERNS = ("login", "signin", "sign-in", "auth", "sso", "oauth", "session/new", "account/login")


def is_blank_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    # about:blank?... is still a blank page — the browser panel's marker URL is one (host_view.py).
    return not value or value in {"about:blank", "chrome://newtab/"} or value.startswith(("about:blank?", "about:blank#"))


def classify_login_flow(events: list[RecordedEvent]) -> str:
    """Return 'login' if the event list looks like an auth recording, else 'workflow'.

    Heuristic: a login flow contains at least one password-type input interaction.
    URL patterns (e.g. /login) are a supporting signal but not required.
    """
    has_password_input = False
    has_login_url = False

    for event in events:
        # Check for password-type input
        input_type = str(event.semantic.input_type or "").lower()
        target_name = str(event.target.name or "").lower()
        if input_type == "password" or "password" in target_name:
            has_password_input = True

        # Check URL for login patterns
        url = str(event.page.url or "").lower()
        if any(marker in url for marker in _LOGIN_URL_PATTERNS):
            has_login_url = True

        if has_password_input:
            break

    if has_password_input:
        return "login"
    if has_login_url:
        return "login"
    return "workflow"


def format_startup_error(exc: Exception) -> str:
    """Normalize Playwright launch failures into concise user-facing text."""
    message = str(exc).strip() or exc.__class__.__name__
    if "Executable doesn't exist" in message:
        return (
            "Playwright browser binaries are missing. "
            "Run `python -m playwright install chromium` and restart the API server."
        )
    return message


def _load_bridge_script(capture_hover: bool = False) -> str:
    here = Path(__file__).resolve().parent / "bridge.js"
    bridge = here.read_text(encoding="utf-8")
    profile = dict(get_policy_bundle().data.get("capture_profile") or {})
    profile["hover_capture_enabled"] = capture_hover
    return (
        f"window.__SKILL_CAPTURE_PROFILE__ = {json.dumps(profile)};\n"
        "window.__SKILL_TRACE__ = true;\n"
        + bridge
    )


def _typing_target_key(event: RecordedEvent) -> tuple[str, str, str, str, str]:
    selectors = event.selectors
    semantic = event.semantic
    chain = event.frame.chain if event.frame else []
    frame_sig = ">".join(
        str(level.get("url_pattern") or level.get("url") or "")
        for level in chain
        if isinstance(level, dict)
    )
    return (
        frame_sig,
        str(selectors.css or ""),
        str(selectors.xpath or ""),
        str(semantic.input_type or ""),
        str(event.page.url or ""),
    )


