"""Learned application-authentication definitions (P0: Application Authentication Recording).

Build Studio's Connect flow used to save nothing but a session (storageState) — the runtime then
had to *guess* whether a saved session was still valid using generic page heuristics (see
runtime/app/login_signals.js). This module is the other half: it turns three observations of the
same probe URL, gathered once at Connect's Done, into a small structured "auth definition" the
runtime can evaluate cheaply and deterministically, with no LLM involved (that rule — Tier A costs
zero tokens — extends here: detection stays zero-token at both learn time and runtime).

The core idea is contrast, not journey-reading: a signal is only worth keeping if it is true when
signed in AND false when signed out, on the SAME url. That's why every definition is learned from
exactly two observations —

    LIVE — the same probe url, fetched in the just-authenticated browser context
    OUT  — the same probe url, fetched in a fresh, cookie-less context

— and then proven against a third before it's ever saved:

    RELOAD — the same probe url, fetched in a fresh context restored from the saved session

`evaluate()` below is the twin of runtime/app/login_signals.js::evaluateAuthDefinition — same
decision rule, kept honest by one shared fixture (runtime/test/fixtures/auth_definition_cases.json)
exercised by both languages' unit tests. `learn()`/`self_test()` have no JS equivalent; they only
run at Connect time, in Studio.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from conxa_core.login_paths import ANY_RE

# ── Path templating (mirrors login_signals.js::templatePath) ───────────────────────────────────
_ID_SEGMENT_RE = re.compile(r"^[0-9]+$|^[0-9a-f-]{8,}$", re.IGNORECASE)


def template_path(path: str) -> str:
    """"/api/users/48213/me" -> "/api/users/:id/me" — so a learned endpoint still matches when
    the live id differs from the one seen while learning. Deliberately identical logic to the JS
    twin; the shared fixture is what actually keeps them in sync, not eyeballing."""
    if not path:
        return "/"
    return "/".join(":id" if seg and _ID_SEGMENT_RE.match(seg) else seg for seg in path.split("/"))


def status_matches_spec(status: int | None, spec: str) -> bool:
    """spec is "2xx".."5xx" or an exact status like "401"."""
    if not spec or status is None:
        return False
    m = re.match(r"^([1-5])xx$", spec)
    if m:
        return status // 100 == int(m.group(1))
    try:
        return int(spec) == status
    except (TypeError, ValueError):
        return False


def _path_of(url: str) -> str | None:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return None


def _url_class(definition: dict, final_path: str | None) -> str | None:
    if final_path is None:
        return None
    out = definition.get("signed_out") or {}
    inn = definition.get("signed_in") or {}
    if out.get("final_path") and final_path == out["final_path"]:
        return "no"
    if inn.get("final_path") and final_path == inn["final_path"]:
        return "yes"
    return None


def _network_class(definition: dict, responses: list[dict]) -> str | None:
    endpoints = ((definition.get("signed_in") or {}).get("endpoints")) or []
    if not endpoints or not responses:
        return None
    saw_ok = False
    for r in responses:
        for ep in endpoints:
            if (ep.get("method") or "GET").upper() != (r.get("method") or "GET").upper():
                continue
            if template_path(ep.get("path", "")) != template_path(r.get("path", "")):
                continue
            if ep.get("denied") and status_matches_spec(r.get("status"), ep["denied"]):
                return "no"
            if ep.get("ok") and status_matches_spec(r.get("status"), ep["ok"]):
                saw_ok = True
    return "yes" if saw_ok else None


def _marker_present(markers: list[dict], wanted: dict) -> bool:
    return any(m.get("role") == wanted.get("role") and m.get("name") == wanted.get("name") for m in markers or [])


def _dom_class(definition: dict, observation: dict) -> str | None:
    if observation.get("password_box"):
        return "no"
    out_markers = (definition.get("signed_out") or {}).get("markers") or []
    if not out_markers:
        return None
    any_present = any(_marker_present(observation.get("markers") or [], w) for w in out_markers)
    return "no" if any_present else "yes"


def _session_class(definition: dict, cookie_names: list[str]) -> str | None:
    keys = definition.get("session_keys") or []
    if not keys:
        return None
    present = any(c in keys for c in cookie_names or [])
    return "yes" if present else None  # weak — never a veto, never counts alone


def evaluate(definition: dict | None, observation: dict | None) -> dict:
    """{"verdict": "yes"|"no"|"unsure", "classes": [...]} — twin of the JS evaluator. See the
    module docstring for why "unsure" (not a false "yes") is the safe default everywhere here."""
    if not definition or not observation:
        return {"verdict": "unsure", "classes": []}
    if observation.get("otp_like"):
        return {"verdict": "unsure", "classes": []}

    results = {
        "url": _url_class(definition, _path_of(observation.get("final_url", ""))),
        "network": _network_class(definition, observation.get("responses") or []),
        "dom": _dom_class(definition, observation),
        "session": _session_class(definition, observation.get("cookie_names") or []),
    }
    if "no" in results.values():
        return {"verdict": "no", "classes": [k for k, v in results.items() if v == "no"]}
    positives = [k for k, v in results.items() if v == "yes"]
    non_session = [k for k in positives if k != "session"]
    if len(positives) >= 2 and non_session:
        return {"verdict": "yes", "classes": positives}
    return {"verdict": "unsure", "classes": positives}


# ── Session check (Check now / Check all, the recording gate, and the runtime's pre-flight) ────
# Twin of runtime/app/login_signals.js::classifySession; kept honest by one shared fixture,
# runtime/test/fixtures/session_check_cases.json, run by both languages' unit tests.
# Visible control names that only appear when nobody is signed in.
_SIGNED_OUT_MARKER_RE = re.compile(
    r"^(sign[-_ ]?in|log[-_ ]?in|sign[-_ ]?up|get started|continue with .+|sign in with .+|log in with .+)$",
    re.IGNORECASE,
)


def matches_success_url(url: str, pattern: str) -> bool:
    """success_url may carry a literal "{}" wildcard; only the part before it is a prefix to match."""
    prefix = (pattern or "").split("{}", 1)[0]
    return bool(prefix) and (url or "").startswith(prefix)


def _same_address(a: str, b: str) -> bool:
    """Same origin + path, ignoring query and fragment."""
    try:
        pa, pb = urlsplit(a), urlsplit(b)
    except ValueError:
        return a == b
    return (pa.scheme, pa.netloc, pa.path or "/") == (pb.scheme, pb.netloc, pb.path or "/")


def looks_like_session_login(observation: dict) -> bool:
    """A password box, or an address with a sign-in word as a whole path segment at any depth. An
    unreadable address is no evidence either way. Also what the settle loop in recorder/session.py
    watches, so a client-side redirect off the login page can finish."""
    if observation.get("password_box"):
        return True
    try:
        return bool(ANY_RE.search(urlsplit(str(observation.get("final_url") or "")).path))
    except ValueError:
        return False


def _looks_signed_out_session(observation: dict) -> bool:
    return looks_like_session_login(observation) or any(
        _SIGNED_OUT_MARKER_RE.match(str(m.get("name") or "").strip()) for m in observation.get("markers") or []
    )


def classify_session(observation: dict | None, baseline: dict | None, app: dict | None = None, *, no_baseline_ok: bool = False) -> str:
    """"ready" | "expired" | "unverified". Only a positive confirmation is "ready"; anything that
    could not be read is "unverified", never "ready".

    observation  the evaluate() observation shape, gathered after navigating to the app's login_url
    baseline     {"url", "hasPasswordBox"}: what that login_url shows a cookie-less browser, or None
    app          {"success_url", "auth_definition"}
    no_baseline_ok  Conxa Execute cannot open a cookie-less context, so it accepts "not login-shaped"
    """
    obs = observation or {}
    app = app or {}
    final_url = str(obs.get("final_url") or "")
    if not final_url.lower().startswith(("http://", "https://")):
        return "unverified"  # navigation never completed

    if app.get("auth_definition"):
        # A learned rule decides alone. When it cannot tell, only positive sign-out evidence moves
        # the answer off "unverified": success page and baseline are the generic path's proof.
        verdict = evaluate(app["auth_definition"], obs)["verdict"]
        if verdict == "yes":
            return "ready"
        if verdict == "no" or _looks_signed_out_session(obs):
            return "expired"
        return "unverified"

    if looks_like_session_login(obs):
        return "expired"
    if matches_success_url(final_url, app.get("success_url", "")):
        return "ready"
    if _looks_signed_out_session(obs):
        return "expired"
    if baseline:
        return "unverified" if _same_address(baseline.get("url", ""), final_url) else "ready"
    return "ready" if no_baseline_ok else "unverified"


# ── Learning (Studio-only: builds a definition from LIVE vs. OUT) ──────────────────────────────

_PERSONAL_RE = re.compile(r"@|^\d{2,}$")


def _clean_markers(markers: list[dict]) -> list[dict]:
    """Drop marker names that look account-specific (an email address, a mostly-numeric id) —
    those would fail to reproduce for a different customer's account. Signed-out markers (what
    this is used for) are usually generic ("Sign in") to begin with; this is a safety net."""
    out = []
    for m in markers or []:
        name = m.get("name") or ""
        if _PERSONAL_RE.search(name):
            continue
        out.append(m)
    return out


def _markers_only_in(a: list[dict], b: list[dict]) -> list[dict]:
    """Markers present in `a` but not in `b`, deduped by (role, name)."""
    seen_b = {(m.get("role"), m.get("name")) for m in b or []}
    out, seen = [], set()
    for m in a or []:
        key = (m.get("role"), m.get("name"))
        if key in seen_b or key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def _classify_status(status: int) -> str:
    return f"{status // 100}xx"


def _learn_endpoints(live_responses: list[dict], out_responses: list[dict]) -> list[dict]:
    """An endpoint is worth keeping only if the SAME templated (method, path) answered
    differently signed-in vs. signed-out — e.g. 200 live, 401 out. Never stores response bodies,
    only the method/path/exact-status shape (evaluate() matches an exact "denied" status
    literally, e.g. "401" — the status class alone, "4xx", would also match a client-side 400
    that has nothing to do with auth)."""
    out_by_key: dict[tuple[str, str], set[int]] = {}
    for r in out_responses or []:
        key = ((r.get("method") or "GET").upper(), template_path(r.get("path", "")))
        out_by_key.setdefault(key, set()).add(r.get("status", 0))

    endpoints: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in live_responses or []:
        key = ((r.get("method") or "GET").upper(), template_path(r.get("path", "")))
        if key in seen:
            continue
        live_status = r.get("status", 0)
        live_class = _classify_status(live_status)
        out_statuses = out_by_key.get(key)
        if not out_statuses or live_status in out_statuses:
            continue  # signed-out answered the same way (or we never saw it out) — not distinguishing
        if not live_class.startswith("2"):
            continue  # only a successful live response is worth learning as the "ok" side
        seen.add(key)
        # A single representative denied status: 401/403 preferred (the ones an auth check
        # actually returns), else whatever else was seen out.
        denied = next((s for s in out_statuses if s in (401, 403)), next(iter(out_statuses)))
        endpoints.append({"method": key[0], "path": key[1], "ok": live_class, "denied": str(denied)})
    return endpoints


def learn(live_obs: dict, out_obs: dict, journey_hosts: list[str] | None = None, probe_url: str = "") -> dict:
    """Build a definition from a signed-in observation and a genuine signed-out one of the SAME
    probe url. Keeps only what differs between the two — see the module docstring."""
    signed_out_markers = _clean_markers(_markers_only_in(out_obs.get("markers") or [], live_obs.get("markers") or []))
    session_keys = [c for c in (live_obs.get("cookie_names") or []) if c not in (out_obs.get("cookie_names") or [])]
    endpoints = _learn_endpoints(live_obs.get("responses") or [], out_obs.get("responses") or [])

    live_path = _path_of(live_obs.get("final_url", ""))
    out_path = _path_of(out_obs.get("final_url", ""))

    return {
        "version": 1,
        "probe_url": probe_url,
        "signed_out": {
            "final_path": out_path if out_path and out_path != live_path else "",
            "markers": signed_out_markers,
            "password_box": bool(out_obs.get("password_box")),
        },
        "signed_in": {
            "final_path": live_path if live_path and live_path != out_path else "",
            "endpoints": endpoints,
        },
        "session_keys": session_keys,
        "journey_hosts": journey_hosts or [],
    }


def self_test(definition: dict, live_obs: dict, out_obs: dict, reload_obs: dict) -> tuple[bool, str]:
    """The three-way proof a definition must pass before Studio will save it (spec's Done means
    "learn AND confirm", not just "learn"). Returns (ok, reason) — reason is a short, user-facing
    explanation of which leg failed, never raw signal data."""
    live_v = evaluate(definition, live_obs)["verdict"]
    if live_v != "yes":
        return False, "The signed-in page didn't confirm sign-in against what was learned."
    out_v = evaluate(definition, out_obs)["verdict"]
    if out_v == "yes":
        return False, "A signed-out browser also read as signed in — the learned signals aren't specific enough."
    reload_v = evaluate(definition, reload_obs)["verdict"]
    if reload_v != "yes":
        return False, "The saved session didn't confirm sign-in when reloaded fresh."
    return True, ""


# ── Page probe (shared JS injected for all three observations) ─────────────────────────────────
# Gathers exactly what evaluate() above reads — nothing else. No values, only shapes: role/name
# for interactive markers, method/path/status for responses (attached by the caller from
# page.on("response"), not from this script), cookie/storage KEY NAMES only.
PROBE_SCRIPT = """
() => {
  const hasPassword = !!document.querySelector('input[type="password"]');
  const otpInputs = document.querySelectorAll(
    'input[autocomplete="one-time-code"], input[inputmode="numeric"][maxlength="1"]'
  );
  const otpLike = otpInputs.length >= 2 || !!document.querySelector('input[autocomplete="one-time-code"]');
  const markers = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('button, a, [role="button"], [role="menuitem"]')) {
    // AUTH-22: footer links ("Docs", "Contact Support") get cut off the signed-in page by the cap
    // below, then read as signed-out-only markers. Same filter + cap in runtime page_scripts.js.
    if (el.closest('footer, [role="contentinfo"]')) continue;
    const name = (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60);
    if (!name) continue;
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    const key = role + '|' + name;
    if (seen.has(key)) continue;
    seen.add(key);
    markers.push({ role, name });
    if (markers.length >= 100) break;
  }
  return { passwordBox: hasPassword, otpLike, markers };
}
"""
