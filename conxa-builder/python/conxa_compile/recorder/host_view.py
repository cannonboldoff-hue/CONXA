"""The recorder's side of Build Studio's browser panel — a Python port of the runtime's
runtime/app/host_browser.js, for record (and later sign-in) runs.

Electron exposes one CDP endpoint (CONXA_HOST_BROWSER_CDP) and a loopback control channel
(CONXA_HOST_CONTROL_URL, conxa-builder/electron/browser_control.js). Electron can't create pages
or contexts over CDP, so the panel creates every view and the recorder finds it by a marker URL.
Playwright's single CDP context is Electron's DEFAULT session and holds every page in the app —
Studio's own window and other runs' views included — while each run's views live in their own
partition. So cookies go through a run page's own CDP session (never context.cookies()/
add_cookies(), AUTH-21), and which pages are the run's comes from the panel (`targets`).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

RECORD_VIEWPORT = {"width": 1280, "height": 720}


def cdp_endpoint() -> str:
    return os.environ.get("CONXA_HOST_BROWSER_CDP", "").strip()


def control_url() -> str:
    return os.environ.get("CONXA_HOST_CONTROL_URL", "").strip()


def available() -> bool:
    return bool(cdp_endpoint() and control_url())


def control(op: str, timeout_s: float = 5.0, **body: Any) -> dict[str, Any]:
    """POST {op, ...body} to the panel's control channel and return its JSON reply."""
    req = urllib.request.Request(
        control_url(),
        data=json.dumps({"op": op, **body}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 — loopback only
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            detail = None
        raise RuntimeError(f"browser panel {op} failed: {detail or exc}") from exc


def page_ws_url(target_id: str) -> str:
    """The devtools WebSocket of one page target (what the screencast video connects to)."""
    parts = urlsplit(cdp_endpoint())
    return f"ws://{parts.hostname}:{parts.port}/devtools/page/{target_id}"


def target_id(context: Any, page: Any) -> str:
    cdp = context.new_cdp_session(page)
    try:
        return str(cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"])
    finally:
        try:
            cdp.detach()
        except Exception:  # noqa: BLE001
            pass


def find_page_by_marker(context: Any, marker_url: str, timeout_s: float = 5.0) -> Any:
    """The sync API only dispatches CDP events inside Playwright calls, so this waits ON the
    context (wait_for_event) instead of sleeping — a plain sleep never sees the new page."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for page in context.pages:
            if page.url == marker_url:
                return page
        try:
            context.wait_for_event("page", timeout=100)
        except Exception:  # noqa: BLE001 — timeout: look again
            pass
    raise RuntimeError(f"browser panel view did not appear (marker {marker_url})")


def acquire(playwright: Any, run_id: str, *, kind: str, label: str = "",
            viewport: dict | None = None) -> tuple[Any, Any, Any, str]:
    """Ask the panel for a view and attach to it. Returns (browser, context, page, tab_id).
    `browser.close()` later only disconnects — the view is the panel's until release()."""
    created = control("new_view", runId=run_id, kind=kind, label=label, viewport=viewport)
    browser = playwright.chromium.connect_over_cdp(cdp_endpoint())
    try:
        if not browser.contexts:
            raise RuntimeError("browser panel: no browser context on connect")
        context = browser.contexts[0]
        page = find_page_by_marker(context, created["markerUrl"])
    except Exception:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
        release(run_id)
        raise
    return browser, context, page, str(created["tabId"])


def run_target_ids(run_id: str) -> set[str]:
    return {t["targetId"] for t in control("targets", runId=run_id).get("targets") or []}


def release(run_id: str) -> None:
    try:
        control("close_run", runId=run_id)
    except Exception:  # noqa: BLE001 — the panel may already be gone (app quitting)
        pass


def page_cookies(context: Any, page: Any) -> list[dict[str, Any]]:
    """Cookies of the partition `page` uses, in Playwright's storage_state cookie shape."""
    cdp = context.new_cdp_session(page)
    try:
        cookies = cdp.send("Network.getAllCookies").get("cookies") or []
    finally:
        try:
            cdp.detach()
        except Exception:  # noqa: BLE001
            pass
    return [
        {
            "name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"],
            "expires": -1 if c.get("session") else c.get("expires", -1),
            "httpOnly": bool(c.get("httpOnly")), "secure": bool(c.get("secure")),
            "sameSite": c.get("sameSite") or "Lax",
        }
        for c in cookies
    ]


def set_page_cookies(context: Any, page: Any, cookies: list[dict[str, Any]]) -> None:
    cdp = context.new_cdp_session(page)
    try:
        cdp.send("Network.setCookies", {"cookies": [
            {k: v for k, v in c.items() if not (k == "expires" and not (isinstance(v, (int, float)) and v > 0))}
            for c in cookies
        ]})
    finally:
        try:
            cdp.detach()
        except Exception:  # noqa: BLE001
            pass


def seed_storage_state(context: Any, page: Any, state: dict[str, Any] | None) -> None:
    """The two halves of new_context(storage_state=...): cookies through the page's own CDP
    session, then each origin's localStorage set from a page on that origin (there is no CDP
    call for it — Playwright does the same visit when it owns the context). One origin failing
    to seed loses only that origin's localStorage, never the run."""
    if not state:
        return
    cookies = state.get("cookies") or []
    if cookies:
        set_page_cookies(context, page, cookies)
    for origin in state.get("origins") or []:
        entries = origin.get("localStorage") or []
        if not entries:
            continue
        try:
            page.goto(origin["origin"], wait_until="domcontentloaded", timeout=15000)
            page.evaluate(
                "(entries) => { for (const { name, value } of entries) {"
                " try { window.localStorage.setItem(name, value); } catch (_) {} } }",
                entries,
            )
        except Exception:  # noqa: BLE001
            continue


def capture_state(context: Any, pages: list[Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """context.storage_state()'s shape, read from the run's own pages. localStorage is read from
    each open page's origin; an origin the run no longer has open keeps what `previous` held for
    it (the tab that showed it closed — Playwright, owning the context, could still read it)."""
    if not pages:
        raise RuntimeError("no open page to read the sign-in from")
    origins: dict[str, list[dict[str, str]]] = {}
    for page in pages:
        try:
            origin = page.evaluate("() => location.origin")
            if not origin or origin == "null" or origin in origins:
                continue
            origins[origin] = page.evaluate(
                "() => Object.keys(localStorage).map((name) => ({ name, value: localStorage.getItem(name) }))"
            )
        except Exception:  # noqa: BLE001 — about:blank, a closing page, storage blocked
            continue
    for old in (previous or {}).get("origins") or []:
        if old.get("origin") and old["origin"] not in origins:
            origins[old["origin"]] = old.get("localStorage") or []
    return {
        "cookies": page_cookies(context, pages[0]),
        "origins": [{"origin": o, "localStorage": ls} for o, ls in origins.items() if ls],
    }
