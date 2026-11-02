"""Merge multiple Playwright storageState dicts into one.

A WorkflowGroup can hold several authenticated applications, each captured as
its own storageState file. To record or run a workflow that crosses those
apps in one Chromium context, their sessions need to be combined into a
single storageState. See runtime/browser.js's mergeStorageStates for the JS
twin used at execution time.
"""

from __future__ import annotations

from typing import Any


def merge_storage_states(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Concatenate cookies (de-duped by name+domain+path) and merge localStorage
    origins (later states win per-key on conflicting origins)."""
    cookies: list[dict[str, Any]] = []
    seen_cookies: set[tuple[Any, Any, Any]] = set()
    origins_by_url: dict[str, dict[str, Any]] = {}

    for state in states:
        if not state:
            continue
        for cookie in state.get("cookies") or []:
            key = (cookie.get("name"), cookie.get("domain"), cookie.get("path"))
            if key in seen_cookies:
                continue
            seen_cookies.add(key)
            cookies.append(cookie)

        for origin in state.get("origins") or []:
            url = origin.get("origin")
            if not url:
                continue
            existing = origins_by_url.get(url)
            if existing is None:
                origins_by_url[url] = {"origin": url, "localStorage": list(origin.get("localStorage") or [])}
                continue
            merged_items = {item["name"]: item for item in existing["localStorage"]}
            for item in origin.get("localStorage") or []:
                merged_items[item["name"]] = item
            existing["localStorage"] = list(merged_items.values())

    return {"cookies": cookies, "origins": list(origins_by_url.values())}


def keeps_session(previous: dict[str, Any], refreshed: dict[str, Any], session_keys: list[str]) -> bool:
    """False when ``refreshed`` dropped a session cookie ``previous`` had — the site signed the
    browser out (or never honoured the saved session), so saving it would replace a working
    session with an anonymous one. ``session_keys`` is the app's learned auth_definition list."""
    had = {c.get("name") for c in previous.get("cookies") or []} & set(session_keys)
    now = {c.get("name") for c in refreshed.get("cookies") or []}
    return had <= now


def refresh_app_state(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Re-derive one app's storageState from a post-recording merged state.

    A recording seeds its context with several apps' sessions merged into one
    storageState (see merge_storage_states above); when the recording ends, that
    single blob needs splitting back into per-app updates so each app's saved
    session reflects what the site did to it (rotated cookies, refreshed
    tokens) without picking up cookies that belong to a sibling app in the same
    recording. Scoped to the cookie domains and localStorage origins ``previous``
    already owned — a domain/origin that never appeared before is dropped, since
    nothing here says which app it belongs to.
    """
    domains = {c.get("domain") for c in previous.get("cookies") or []}
    origins = {o.get("origin") for o in previous.get("origins") or []}
    return {
        "cookies": [c for c in current.get("cookies") or [] if c.get("domain") in domains],
        "origins": [o for o in current.get("origins") or [] if o.get("origin") in origins],
    }
