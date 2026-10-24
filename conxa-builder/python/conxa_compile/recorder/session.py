"""Playwright-backed recording session (Phase 1 — capture only, no execution)."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Literal
from urllib.parse import urlsplit

if os.environ.get("SKILL_ENVIRONMENT") == "production" or os.environ.get("RENDER"):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from playwright.sync_api import sync_playwright

from conxa_core.config import settings
from conxa_core.metrics.store import metrics
from conxa_core.models.events import RecordedEvent
from conxa_core.models.workflow import GroupApp
from conxa_compile import auth_learning
from conxa_compile.policy.bundle import get_policy_bundle
from conxa_compile.policy.timing import resolve_event_timing
from conxa_core.sanitize import scrub_surrogates as _sanitize_surrogates
from conxa_core.storage import snapshots as snapshot_store


from conxa_compile.recorder import host_view
from conxa_compile.recorder.dialog_server import DIALOG_OVERRIDE_SCRIPT_TEMPLATE, DialogSyncServer
from conxa_compile.recorder.frame_utils import (
    _frame_context_and_offset_sync,
    _frame_element_attrs_and_rect,
    _frame_parent,
    _frame_url,
    _load_bridge_script,
    _typing_target_key,
    _viewport_string_from_page,
    format_startup_error,
    is_blank_url,
)
from conxa_compile.recorder.screencast_video import ScreencastVideo

# Both is_connected() and context.pages have been observed to read "gone" for
# several real seconds — not just one flaky tick — while the browser is still
# open and busy loading a heavy same-page iframe (e.g. HubSpot's "Create new"
# object-builder embed). Ending the recording on the first bad reading silently
# truncates it mid-session. Require the bad reading to persist across this many
# consecutive checks before treating it as a real close/crash — a genuine close
# is unaffected, it just confirms a few seconds later. Measured against real
# HubSpot recordings, the 8s (40-tick) window was consistently exhausted by
# that specific panel's load time, so this is now 20s.
#
# The connectivity check itself is throttled to ~once/second (not every 0.2s
# loop tick — see _last_connectivity_check_at in the pump loop) because
# is_connected()/context.pages are themselves CDP round-trips, and running
# them 5x/second was a source of visible browser window flicker during
# recording, the same class of bug already fixed for storage-state autosaving
# (see _autosave_storage_state_sync). Each streak increment now represents
# ~1s of real time, so this constant is counted in checks-at-~1Hz, not raw
# 0.2s loop ticks — kept at ~20s grace by dividing the old tick-based value
# by 5.
_NO_PAGES_GRACE_TICKS = 20  # ~20s at the check's ~1s cadence

# An unanswered native alert/confirm/prompt blocks the page's renderer thread for as long as
# it's pending — a forgotten Studio modal must not be able to wedge a recording forever.
# ponytail: fixed timeout, not configurable — raise it if a real workflow needs longer.
_JS_DIALOG_TIMEOUT_S = 120.0

# Embedded recording: how long a download keeps the dispatcher turning before it is recorded, so
# the click that started it (held by bridge.js for up to click_settle_max_ms) lands first.
_DOWNLOAD_CLICK_GRACE_S = 0.75


def url_matches_pattern(url: str, pattern: str, *, exclude_prefix: str = "") -> bool:
    """True if `url` reaches the success page described by `pattern`.

    `pattern` may use a literal `{}` as a wildcard for "anything after this
    point" (e.g. "vercel.com/{}" matches any path under vercel.com/); a
    pattern with no `{}` is matched as an exact prefix. `exclude_prefix`, when
    given, rules out URLs that are just a variant of the starting page (e.g. a
    login page that appends ?returnUrl=... before auth completes).
    """
    if not pattern or not url:
        return False
    if exclude_prefix and url.startswith(exclude_prefix):
        return False
    prefix = pattern.split("{}", 1)[0]
    return bool(prefix) and url.startswith(prefix)


def _quiet_target_closed_exception_handler(loop: Any, context: dict[str, Any]) -> None:
    """asyncio exception handler for the sync-Playwright driver's own event loop.

    Swallows only TargetClosedError (matched by name, not isinstance — this loop's
    exceptions come from playwright._impl internals we don't want a hard import on)
    so a real bug still surfaces via the loop's default handler.
    """
    exc = context.get("exception")
    if exc is not None and type(exc).__name__ == "TargetClosedError":
        return
    loop.default_exception_handler(context)


def extract_visited_hosts(events: list[dict[str, Any]]) -> list[str]:
    """Every hostname a recording actually navigated to — main frame
    (ev.page.url) and any tab opened during the recording (ev.tab.url).

    Shared by the compiler (SkillMeta.visited_hosts, which feeds the manifest's
    unclaimed_hosts via group_store.unclaimed_hosts) and the recording save
    path (handlers/session.py persists it onto Workflow.visited_hosts so
    platform tags show before the first compile). Kept here so both callers
    derive identical hosts from identical events.
    """
    from urllib.parse import urlparse

    hosts: set[str] = set()
    for ev in events:
        for url in ((ev.get("page") or {}).get("url"), (ev.get("tab") or {}).get("url")):
            if not url:
                continue
            try:
                host = (urlparse(url).hostname or "").lower()
            except ValueError:
                host = ""
            if host:
                hosts.add(host)
    return sorted(hosts)


def _gather_page_observation_sync(page: Any, context: Any, responses: list[dict]) -> dict:
    """The Playwright-touching half of an auth_learning observation — reads exactly what
    evaluate()/learn() consume (see auth_learning.py's module docstring) and nothing else.
    `responses` is filled by the caller via a page.on("response") listener attached BEFORE
    navigating; this only reads the page/cookies once navigation has settled."""
    try:
        probe = page.evaluate(auth_learning.PROBE_SCRIPT)
    except Exception:  # noqa: BLE001
        probe = {"passwordBox": False, "otpLike": False, "markers": []}
    try:
        cookie_names = [c["name"] for c in context.cookies()]
    except Exception:  # noqa: BLE001
        cookie_names = []
    return {
        "final_url": page.url,
        "password_box": bool(probe.get("passwordBox")),
        "otp_like": bool(probe.get("otpLike")),
        "markers": probe.get("markers") or [],
        "responses": responses,
        "cookie_names": cookie_names,
    }


def _navigate_and_observe_sync(page: Any, context: Any, url: str, *, timeout_ms: int = 8000) -> dict:
    """Navigate `page` to `url`, capturing same-origin XHR/fetch responses along the way, then
    gather one auth_learning observation. Used for all three of learn_auth's LIVE/OUT/RELOAD
    captures — same probe, different browser context each time."""
    responses: list[dict] = []

    def _on_response(resp: Any) -> None:
        try:
            if len(responses) >= 30:
                return
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            from urllib.parse import urlsplit as _urlsplit

            responses.append({
                "method": resp.request.method,
                "path": _urlsplit(resp.url).path or "/",
                "status": resp.status,
            })
        except Exception:  # noqa: BLE001
            pass

    page.on("response", _on_response)
    try:
        page.goto(url, wait_until="load", timeout=timeout_ms)
        page.wait_for_timeout(1500)  # let SPA XHRs fired on load settle before reading responses
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:  # noqa: BLE001
            pass
    return _gather_page_observation_sync(page, context, responses)


SessionProbe = Literal["ready", "expired", "unverified"]

# A URL path/host that names a sign-in step. Generic on purpose (no per-app list): any app that
# bounces an unauthenticated visitor lands on something that says so in its address.
_LOGIN_URL_RE = re.compile(
    r"sign[-_ ]?in|log[-_ ]?in|signon|(?<![a-z])sso(?![a-z])|oauth|(?<![a-z])(?:identifier|challenge)(?![a-z])|servicelogin|/auth(?:enticate|orize)?(?:/|$)",
    re.IGNORECASE,
)
# Visible control names that only appear when nobody is signed in.
_SIGNED_OUT_MARKER_RE = re.compile(
    r"^(sign[-_ ]?in|log[-_ ]?in|sign[-_ ]?up|get started|continue with .+|sign in with .+|log in with .+)$",
    re.IGNORECASE,
)


def _looks_signed_out(observation: dict, login_url: str) -> bool:
    """Positive evidence that the page the probe landed on is a sign-in page: a password box, a
    sign-in-shaped address, or a Sign in / Log in control. Reads only the auth_learning
    observation shape (final_url, password_box, markers)."""
    if observation.get("password_box"):
        return True
    final_url = str(observation.get("final_url") or "")
    login_base = login_url.split("?")[0].split("#")[0].rstrip("/")
    if login_base and final_url.split("?")[0].split("#")[0].rstrip("/").startswith(login_base):
        return True
    try:
        parts = urlsplit(final_url)
    except ValueError:
        return False
    if _LOGIN_URL_RE.search(f"{parts.netloc}{parts.path}"):
        return True
    return any(_SIGNED_OUT_MARKER_RE.match(str(m.get("name") or "").strip()) for m in observation.get("markers") or [])


def _probe_app_session_sync(app: GroupApp) -> SessionProbe:
    """The actual headless probe body — see check_app_session_sync for the
    policy this implements. Split out so it can be run on a bounded thread.

    Only a positive confirmation returns "ready". Anything that could not be read (navigation
    failed, the driver errored, the page is neither clearly signed in nor clearly signed out)
    returns "unverified" — never "ready"."""
    definition = app.auth_definition
    probe_url = (definition or {}).get("probe_url") or (
        (app.success_url.split("{}", 1)[0] if app.success_url else "") or app.login_url
    )
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(storage_state=app.storage_state_path)
                page = context.new_page()
                observation = _navigate_and_observe_sync(page, context, probe_url, timeout_ms=10000)
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 — a probe failure is inconclusive, not a verdict
        return "unverified"

    landed = str(observation.get("final_url") or "")
    if not landed.startswith(("http://", "https://")):
        return "unverified"  # navigation never completed (about:blank / error page)

    if definition:
        verdict = auth_learning.evaluate(definition, observation)["verdict"]
        if verdict == "yes":
            return "ready"
        if verdict == "no" or _looks_signed_out(observation, app.login_url):
            return "expired"
        return "unverified"

    if _looks_signed_out(observation, app.login_url):
        return "expired"
    if app.success_url and url_matches_pattern(landed, app.success_url):
        return "ready"
    if not app.success_url and landed.split("#")[0] != app.login_url.split("#")[0]:
        # No success page to match against: the best available evidence is that the login URL
        # let us through to a different page that shows no sign-in form or control.
        return "ready"
    return "unverified"


def check_app_session_sync(app: GroupApp, *, timeout_s: float = 20.0) -> SessionProbe:
    """Headless probe: is `app`'s saved session still authenticated?

    Loads the app's saved storage state into a throwaway headless browser, navigates to its
    success page (or login page if no success_url is set), and reads what it lands on.

    Three outcomes, and the middle one is the point:
      "ready"      — positively confirmed signed in.
      "expired"    — a missing/corrupt saved session, or the app showed a sign-in page.
      "unverified" — could not tell (timeout, network error, a wedged driver, an unrecognised
                     page). Callers must NOT show this as signed in; they also must not lock the
                     user out on it, so the recording gate treats it as "not known to be bad".

    The probe body's only internal timeout covers page.goto(); launching the driver, opening a
    context, and closing the browser are all unbounded, so a wedged Playwright driver can hang
    forever and take its caller down with it (this happened — see FIX.md). Run the whole probe on
    a daemon thread with a hard deadline instead: on timeout report "unverified".
    """
    if not app.storage_state_path or not Path(app.storage_state_path).is_file():
        return "expired"
    try:
        with open(app.storage_state_path, encoding="utf-8") as f:
            json.load(f)
    except (OSError, ValueError):
        return "expired"

    result: list[SessionProbe] = []
    # ponytail: a timed-out probe thread (and its Playwright driver/browser)
    # keeps running in the background until it finishes or the backend exits
    # — daemon=True only stops it from blocking process shutdown. Kill-able
    # subprocess isolation if that leak shows up in practice; sys.executable
    # is the backend exe in frozen builds, so it's not a one-liner.
    thread = threading.Thread(target=lambda: result.append(_probe_app_session_sync(app)), daemon=True)
    thread.start()
    thread.join(timeout_s)
    return result[0] if result else "unverified"


@dataclass
class RecordingSession:
    """
    Owns one browser context + page, drains in-page events into structured JSON.

    Threading: Playwright calls `expose_binding` from the driver thread; we forward
    payloads into an asyncio.Queue via call_soon_threadsafe for a single consumer.
    """

    session_id: str
    start_url: str = "about:blank"
    data_root: Path = field(default_factory=lambda: settings.data_dir)
    storage_state_path: str = ""  # if set, browser context is restored from this Playwright storage_state file
    storage_state_autosave_path: str = ""  # if set, periodically persist context storage_state while open
    # Video recording is MANDATORY for non-auth sessions; no flag to disable.
    # auth_mode sessions are exempt (no video, no bridge, no events — see auth_mode field).
    _video_session_start: float = 0.0  # monotonic time when recording started; events.timestamp_ms is relative to this
    _video_session_start_wall_ms: int = 0  # wall-clock ms-since-epoch at video start (for ISO timestamp → relative ms conversion)
    _playwright: Any = None
    _browser: Any = None
    _context: Any = None
    _page: Any = None
    _thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop_requested: threading.Event = field(default_factory=threading.Event)
    _startup_done: threading.Event = field(default_factory=threading.Event)
    _startup_error: str = ""
    _seq: int = 0
    _materialized: list[RecordedEvent] = field(default_factory=list)
    _pending_payloads: SimpleQueue = field(default_factory=SimpleQueue)
    # BUILD-38: pause/resume. While paused, payloads are stamped at enqueue time (not at drain
    # time — the queue can lag behind the page by a few ticks, and a resume must not replay what
    # happened during the pause) and dropped by _consume_payload_sync.
    _paused: bool = False
    # Optional callback(event: RecordedEvent) fired after each event is materialized, so the
    # Studio can show a live "captured so far" list. Never raises into the recorder.
    event_hook: Any = None
    _last_enqueue_at: float = 0.0
    _last_storage_state_save_at: float = 0.0
    _last_url_sweep_at: float = 0.0
    _last_connectivity_check_at: float = 0.0
    _bridge_script: str = ""
    _bridge_install_error_keys: set[str] = field(default_factory=set)
    _frame_diag_seen: set[str] = field(default_factory=set)
    _traces: list[dict] = field(default_factory=list)
    _frame_snapshots: list[dict] = field(default_factory=list)
    # Event-driven (not sampled) frame attach/navigate/detach log — complements the periodic
    # _frame_snapshots, which only sees a frame if it's still around at the ~1s sample tick.
    # This catches a frame that attaches and detaches faster than any polling interval.
    _frame_lifecycle: list[dict] = field(default_factory=list)
    # Wall-clock (monotonic) of each frame's most recent attach/navigate event, keyed by Python
    # object identity (stable for the frame's lifetime, unlike its URL which is empty mid-
    # transition). Used to defer bridge-install checks away from the exact instant a frame is
    # least stable — see _BRIDGE_INSTALL_SETTLE_S.
    _frame_last_transition_ts: dict[int, float] = field(default_factory=dict)
    _pump_tick: int = 0
    _no_pages_streak: int = 0
    binding_errors: list[str] = field(default_factory=list)
    browser_open: bool = False
    ended_by_user: bool = False
    wait_for_url: str = ""
    reached_wait_url: bool = False
    auth_mode: bool = False  # skip bridge/events — only capture storage state
    capture_hover: bool = False  # opt-in per recording — hover signals are noisy, off by default
    # auth_mode only: screen rect {x, y, width, height} (DIPs) to open the login window at, so
    # Studio can dock it beside the Applications column. None = the browser's own default placement.
    window_rect: dict | None = None
    # What the browser panel titles this run's view (the app's name for a sign-in).
    label: str = ""
    # Record inside Build Studio's browser panel (host_view.py) instead of launching Chromium.
    # Decided once, in SessionRegistry.create. What changes when True, all in this file:
    #   - the page is a panel view found by marker; the context is Electron's one shared CDP
    #     context, so only the run's own pages (_run_page_ids, from the panel's `targets`) are
    #     recorded, and the binding + scripts are installed per run page, never context-wide;
    #   - video is our own screencast per tab (screencast_video.py), not record_video_dir;
    #   - storage state is seeded/captured through the run's pages (host_view), not the context;
    #   - teardown disconnects and asks the panel to close the run instead of closing a browser;
    #   - a sign-in (auth_mode) is a `signin` run at the pane's own size, and learn_auth's LIVE
    #     capture uses a background tab of the run (_embedded_live_observation_sync).
    embedded: bool = False
    _run_page_ids: set[int] = field(default_factory=set)
    _foreign_page_ids: set[int] = field(default_factory=set)
    _page_target_ids: dict[int, str] = field(default_factory=dict)
    _run_pages_open: set[int] = field(default_factory=set)
    _screencasts: dict[int, ScreencastVideo] = field(default_factory=dict)
    _dialog_script: str = ""
    # auth_mode only: set the moment a storage-state save actually succeeds, so the
    # caller can tell "browser closed after a session was saved" from "closed with
    # nothing captured" (see cmd_finish_group_app_auth). Distinct from reached_wait_url,
    # which requires a configured success_url match — this fires on any successful save.
    auth_captured: bool = False
    # auth_mode only: the last URL an off-start-page autosave fired for, so the ~1s URL
    # sweep below saves once per post-login navigation rather than repeatedly.
    _auth_saved_url: str = ""
    current_url: str = ""
    # auth_mode only: every distinct host the login tab's own page visited, in order — a
    # diagnostic breadcrumb (e.g. ["app.acme.com", "accounts.google.com", "app.acme.com"])
    # stored on the learned definition (auth_learning.learn's journey_hosts) but never consulted
    # by evaluate(). ponytail: main-tab host list only, not a full per-tab/per-popup graph — good
    # enough to explain a cross-host SSO hop; widen if a real multi-tab flow needs more detail.
    journey_hosts: list[str] = field(default_factory=list)
    _journey_last_url: str = ""
    # Cross-thread bridge for learn_auth() (see its own docstring below) — same polling-flag
    # pattern as _stop_requested/_startup_done above: the recorder THREAD (not the caller's
    # asyncio loop) is the only thread allowed to touch self._context/self._playwright, so a
    # request/result pair plus an Event is how the async method hands work to that thread and
    # waits for it.
    _learn_auth_pending: dict | None = None
    _learn_auth_done: threading.Event = field(default_factory=threading.Event)
    _learn_auth_result: tuple = (None, "")
    # Phase 2: dedup state for DOM snapshots. Maps short bridge signature -> snapshot_ref.
    _snapshot_refs_by_sig: dict[str, str] = field(default_factory=dict)
    _last_snapshot_hash: str = ""
    # Per-session workspace downloads land in during recording — mirrors the runtime's
    # runs/{runId}/ (see _run_sync_recorder). None for auth_mode sessions, which never record.
    _downloads_dir: Path | None = None
    _taken_download_names: set[str] = field(default_factory=set)
    # Folder to default the Studio's file-pick dialog to — the most recent download's folder
    # (or its zip-extract folder), falling back to _downloads_dir. See _on_download/_on_file_chooser.
    _last_download_dir: Path | None = None
    # Set by the caller (handlers/session.py) before start(): (request_id, default_dir, multiple) -> None.
    # Invoked from the Playwright driver thread when a native file-chooser dialog would otherwise
    # open — see _on_file_chooser. None means no interception request is dispatched anywhere.
    on_file_picker_request: Any = None
    _pending_file_chooser: Any = None  # (request_id, chooser) for the one outstanding pick
    _file_pick_results: SimpleQueue = field(default_factory=SimpleQueue)
    _zip_downloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    _pending_file_pick: dict[str, Any] | None = None
    # Set by the caller (handlers/session.py) before start(): (request_id, dialog_type, message,
    # default_value) -> None. Invoked from the Playwright driver thread when a native JS dialog
    # (alert/confirm/prompt) opens — see _on_dialog. None means no interception request is
    # dispatched anywhere (auto-accept with no captured text, the old behavior).
    on_js_dialog_request: Any = None
    # Set by the caller before start(): (request_id) -> None. Fired when a dialog left
    # unanswered too long is auto-accepted by _drain_js_dialog_sync's timeout fallback, so the
    # Studio can close a modal that would otherwise sit there forever.
    on_js_dialog_cancelled: Any = None
    # Set by the caller before start(): (request_id) -> None. Fired once a human-answered dialog
    # has actually been accepted/dismissed AND _bring_dialog_page_to_front_sync has run — not
    # when resolve_js_dialog() is merely called. The Studio RPC handler acks resolve_js_dialog
    # the instant the answer is queued, well before this pump-loop tick processes it; if the
    # Studio dropped its own always-on-top pin on that early ack, neither window would hold real
    # OS focus and the human's next click would land on nothing. Waiting for this event before
    # unpinning is what actually hands focus back to the recording browser.
    on_js_dialog_resolved: Any = None
    # (request_id, dialog, type, message, opened_at_monotonic, src_page) for the one outstanding
    # dialog. A JS dialog blocks the page's renderer thread until accept()/dismiss() is called,
    # so only one can ever be pending at a time.
    _pending_dialog: Any = None
    _dialog_results: SimpleQueue = field(default_factory=SimpleQueue)
    # Local HTTP server that answers window.alert/confirm/prompt via the page-side override
    # injected in _run_sync_recorder — see dialog_server.py's module docstring for why: the
    # CDP-based path above still exists (for beforeunload, which isn't overridable this way),
    # but a slow-answered alert/confirm/prompt held open via CDP could be resolved by Chromium
    # itself before our own accept() call reached it. None outside a non-auth_mode recording.
    _dialog_server: DialogSyncServer | None = None
    # (accepted, dialog_type, message, text, href) tuples queued by the dialog-server callbacks
    # (foreign thread) for _drain_dialog_server_events_sync (driver thread) to turn into
    # recorded dialog_accept/dialog_dismiss steps.
    _dialog_server_events: SimpleQueue = field(default_factory=SimpleQueue)
    _last_snapshot_ref: str = ""
    # A11y capture: one-strike degradation if slow (> 500ms).
    _last_a11y_capture_time: float = 0.0
    _a11y_skip_count: int = 0
    _a11y_capture_error_logged: bool = False
    # Multi-tab identity: Page (by object identity) -> "tab_0", "tab_1", ... in discovery order.
    # Populated only from the pump loop (Playwright calls are unsafe inside expose_binding
    # callbacks — see _binding_sink_sync). tab_0 is always the initial page, so single-tab
    # recordings are unaffected.
    _tab_ids: dict[int, str] = field(default_factory=dict)
    _tab_pages: dict[str, Any] = field(default_factory=dict)
    _tab_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    _tab_seq: int = 0
    # Pages seen via context "page" event but not yet registered by the pump loop
    # (Playwright calls on them must wait for the pump thread).
    _unregistered_pages: list[Any] = field(default_factory=list)
    # Browser Back/Forward capture. The injected bridge only sees in-page DOM events —
    # pressing the browser Back button produces none, so history navigations used to be
    # invisible to recordings (replay then guessed URLs instead of reproducing what the
    # user did). Each page gets its own CDP session; after every main-frame navigation we
    # compare Page.getNavigationHistory against that page's previous snapshot:
    #   currentIndex decreased                          -> browser_back
    #   moved forward into an entry that already existed -> browser_forward (URL at the new
    #                                                      index must equal the stale entry,
    #                                                      so a fresh link click that merely
    #                                                      truncated forward entries is NOT
    #                                                      misread as a forward)
    #   anything else, renderer-initiated                 -> normal navigation (a click/submit/
    #                                                      script the recording already captured
    #                                                      as its own step), no event
    #   anything else, NOT renderer-initiated              -> manual_navigate (user typed a new
    #                                                      URL / used a bookmark — nothing else
    #                                                      in the recording would ever reproduce
    #                                                      it), see _nav_pending_renderer_initiated
    # Checks are deferred to the pump loop via _nav_check_pages (CDP calls are unsafe inside
    # Playwright event callbacks — same reentrancy rule as _binding_sink_sync). Strictness
    # principle: a missed Back degrades to the old behavior; a false positive corrupts replay.
    _nav_cdp_sessions: dict[int, Any] = field(default_factory=dict)
    _nav_history_state: dict[int, dict[str, Any]] = field(default_factory=dict)
    _nav_check_pages: list[Any] = field(default_factory=list)
    # Set to the request's monotonic timestamp by the CDP "Page.frameRequestedNavigation"
    # event, which CDP only fires for navigations the *page itself* initiates (link click,
    # form submit, script) — never for ones the browser initiates (address bar edit,
    # bookmark, our own goto()). Consumed and cleared the next time this page's navigation
    # is classified in _drain_nav_history_checks_sync. A requested navigation that never
    # commits (e.g. a submit button whose handler calls preventDefault) leaves this stale
    # until some later, unrelated navigation is classified — the timestamp lets that check
    # tell "this request just committed" apart from "this request was abandoned ages ago."
    _nav_pending_renderer_initiated: dict[int, float] = field(default_factory=dict)
    # A requested navigation must commit within this long to count as the cause of the
    # next observed navigation; real link/form/script navigations commit far faster.
    _NAV_RENDERER_INITIATED_TTL_S = 10.0
    # When each page's last main-frame navigation COMMITTED (stamped in _on_page_navigated), so the
    # TTL above is judged against the commit, not against whenever the pump loop gets around to
    # draining the check (a slow server or a busy pump used to push a real click past the old 2 s).
    _nav_committed_at: dict[int, float] = field(default_factory=dict)
    # Pages that saw a same-document navigation (pushState/replaceState/hash) since the last
    # check. CDP never fires frameRequestedNavigation for those, so without this a SPA router
    # redirect after a click was indistinguishable from the user retyping the address bar.
    # ponytail: a user typing a hash-only URL by hand also lands here; rare, accepted.
    _nav_same_document: set[int] = field(default_factory=set)
    # When the page last reported a user interaction (click, submit, typing, ...) through the
    # bridge — a navigation committing shortly after one is the interaction's effect.
    _last_interaction_at: dict[int, float] = field(default_factory=dict)
    _NAV_INTERACTION_WINDOW_S = 5.0
    _INTERACTION_ACTIONS = frozenset({
        "click", "dblclick", "right_click", "type", "fill", "select", "select_option",
        "set_checkbox", "set_radio", "date_pick", "keyboard_shortcut", "submit", "drag_drop",
        "upload", "upload_intent",
    })

    def _remember_current_url(self, url: str) -> None:
        value = str(url or "").strip()
        if not value or is_blank_url(value):
            return
        self.current_url = value

    def _remember_page_url_sync(self, page: Any | None) -> None:
        if page is None:
            return
        try:
            if page.is_closed():
                return
            url = page.url
            self._remember_current_url(url)
            tab_id = self._tab_id_for_page(page)
            if tab_id and tab_id in self._tab_meta:
                value = str(url or "").strip()
                if value and not is_blank_url(value):
                    self._tab_meta[tab_id]["last_url"] = value
        except Exception:  # noqa: BLE001
            pass

    def _url_matches_wait_target(self, url: str) -> bool:
        # Exclude start URL and any query-param/fragment/sub-path variant of it
        # (e.g. login pages that append ?returnUrl=... before auth completes)
        start_base = self.start_url.split("?")[0].split("#")[0]
        return url_matches_pattern(url, self.wait_for_url, exclude_prefix=start_base)

    def _shutdown_playwright_sync(self) -> None:
        if self._dialog_server is not None:
            self._dialog_server.stop()
            self._dialog_server = None
        if self.embedded:
            # The views are the panel's: stop the videos while their pages still exist, then
            # disconnect (browser.close() on a CDP connection only disconnects, and drops the
            # run pages' scripts with it) and ask the panel to close the run.
            try:
                self._finalize_video_file_sync()
            except Exception as exc:  # noqa: BLE001
                self.binding_errors.append(f"video_finalize_error: {exc!s}")
            self._context = None
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:  # noqa: BLE001
                    pass
                self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
            host_view.release(self.session_id)
            self._page = None
            self._nav_cdp_sessions.clear()
            self._nav_history_state.clear()
            self._nav_check_pages.clear()
            self._nav_committed_at.clear()
            self._nav_same_document.clear()
            self._last_interaction_at.clear()
            return
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None
        self._nav_cdp_sessions.clear()
        self._nav_history_state.clear()
        self._nav_check_pages.clear()
        self._nav_committed_at.clear()
        self._nav_same_document.clear()
        self._last_interaction_at.clear()
        try:
            self._finalize_video_file_sync()
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"video_finalize_error: {exc!s}")

    def _finalize_video_file_sync(self) -> None:
        """Rename each tab's auto-generated .webm to a stable name and record the mapping.

        Frame extraction no longer happens here — it runs at compile time
        (see handlers/compile.py), so it can be retried per-event on
        recompile instead of being a one-shot, all-or-nothing step tied to
        the lifetime of this recorder thread. Auth-mode sessions are exempt
        (they don't record video).

        Multi-tab: every tab records its own video (Playwright writes one file per page in a
        video-enabled context). tab_0 keeps the pre-existing "recording.webm" name so single-tab
        recordings and every reader of that name (frame_extractor.py's non-multi-tab path,
        old sessions) are unaffected; every other tab is written as "recording-<tab_id>.webm".
        A videos.json alongside them maps tab_id -> {file, start_wall_ms} for frame_extractor.py
        to pick the right video per event (see RecordedEvent.tab.id).
        """
        if self.auth_mode:
            return
        session_dir = self.data_root / "sessions" / self.session_id
        if not session_dir.is_dir():
            raise RuntimeError(f"session_dir missing at finalize: {session_dir}")
        if self.embedded:
            self._finalize_screencasts_sync(session_dir)
            return

        target = session_dir / "recording.webm"
        webm_files = [
            p for p in session_dir.iterdir()
            if p.suffix == ".webm" and p.name != "recording.webm" and not p.name.startswith("recording-tab_")
        ]

        # Primary path: match each source .webm to its tab by the exact path Playwright reported
        # at page-creation time (video.path() is deterministic from context creation, not just
        # after close — see _register_new_pages_sync). Any tab whose path didn't resolve (or
        # whose file doesn't actually exist — a very short-lived tab can be closed before
        # Playwright ever flushes anything) falls through to the mtime-order fallback below.
        by_path = {p.resolve(): p for p in webm_files}
        matched: dict[str, Path] = {}
        for tab_id, meta in self._tab_meta.items():
            raw_path = meta.get("video_path")
            if not raw_path:
                continue
            candidate = Path(raw_path).resolve()
            if candidate in by_path:
                matched[tab_id] = by_path.pop(candidate)

        # Fallback for any tab not resolved above: assign remaining files by mtime order to
        # remaining tabs in discovery order — the same heuristic this function always used when
        # there was exactly one tab.
        unmatched_tabs = [t for t in sorted(self._tab_meta, key=lambda t: self._tab_meta[t]["index"]) if t not in matched]
        remaining_files = sorted(by_path.values(), key=lambda p: p.stat().st_mtime)
        # A session whose tabs were never registered via the pump loop (e.g. auth-mode-adjacent
        # edge cases, or this exact shape constructed directly in tests) can still have a real
        # .webm file needing a name. Synthesize tab ids for any leftover file with no real tab
        # to pair with, so a single video always becomes "tab_0" -> recording.webm — the
        # original, tab-tracking-free behavior this function always had for one video.
        used_ids = set(matched) | set(unmatched_tabs)
        synth_index = 0
        while len(unmatched_tabs) < len(remaining_files):
            candidate = f"tab_{synth_index}"
            synth_index += 1
            if candidate in used_ids:
                continue
            used_ids.add(candidate)
            unmatched_tabs.append(candidate)
        for tab_id, path in zip(unmatched_tabs, remaining_files):
            matched[tab_id] = path

        if not matched and not target.is_file():
            raise RuntimeError(
                f"recording.webm not produced by Playwright in {session_dir}. "
                "Check that the browser context was launched with record_video_dir and the "
                "session closed cleanly via context.close()."
            )

        videos: dict[str, dict[str, Any]] = {}
        for tab_id, src in matched.items():
            dest = target if tab_id == "tab_0" else session_dir / f"recording-{tab_id}.webm"
            if src.resolve() != dest.resolve():
                src.replace(dest)
            videos[tab_id] = {
                "file": dest.name,
                "start_wall_ms": self._tab_meta.get(tab_id, {}).get("video_start_wall_ms", self._video_session_start_wall_ms),
            }
        if "tab_0" not in videos and target.is_file():
            videos["tab_0"] = {"file": target.name, "start_wall_ms": self._video_session_start_wall_ms}

        if videos:
            (session_dir / "videos.json").write_text(json.dumps(videos, indent=2), encoding="utf-8")

    # ── Embedded (browser panel) recording ─────────────────────────────────────────────────────

    def _finalize_screencasts_sync(self, session_dir: Path) -> None:
        """Embedded counterpart of the rename above: each tab's screencast already wrote its file
        under its final name (recording.webm for tab_0, recording-<tab>.webm otherwise), so this
        only stops them and writes the same videos.json contract frame_extractor reads."""
        videos: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for key, rec in list(self._screencasts.items()):
            try:
                rec.stop()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{rec.out_path.name}: {exc!s}")
                continue
            tab_id = self._tab_ids.get(key) or ("tab_0" if rec.out_path.name == "recording.webm" else None)
            if tab_id and rec.out_path.is_file():
                videos[tab_id] = {"file": rec.out_path.name, "start_wall_ms": rec.start_wall_ms}
        self._screencasts.clear()
        if videos:
            (session_dir / "videos.json").write_text(json.dumps(videos, indent=2), encoding="utf-8")
        if errors or not videos:
            raise RuntimeError("; ".join(errors) or f"no recording video produced in {session_dir}")

    def _start_screencast_sync(self, page: Any, file_name: str) -> ScreencastVideo:
        from conxa_compile.recorder.frame_extractor import _find_ffmpeg

        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg not available — install Playwright's browsers (it ships ffmpeg)")
        target = self._page_target_ids.get(id(page)) or host_view.target_id(self._context, page)
        self._page_target_ids[id(page)] = target
        session_dir = self.data_root / "sessions" / self.session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        rec = ScreencastVideo(
            host_view.page_ws_url(target),
            session_dir / file_name,
            ffmpeg,
            scale_fn=lambda: host_view.control("scale", timeout_s=1.0, runId=self.session_id)["scale"],
        )
        rec.start()
        self._screencasts[id(page)] = rec
        return rec

    def _adopt_run_page_sync(self, page: Any) -> None:
        """Make `page` one of the recording's pages: the bridge's binding and scripts go on THIS
        page only (context-wide ones would also reach Studio's own window and other runs' views),
        plus the listeners _on_context_page attaches in the launched path. A page adopted after
        its first document loaded gets the binding at once (expose_binding reaches existing
        frames) and the bridge from _ensure_bridge_installed_sync's backstop — the same path a
        launched popup's first document takes."""
        key = id(page)
        self._run_page_ids.add(key)
        self._run_pages_open.add(key)
        page.on("close", lambda _p: self._on_run_page_closed(key))
        if self.auth_mode:
            return  # a sign-in records nothing: membership and "closed" are all it needs
        page.expose_binding("__skillReport", self._binding_sink_sync)
        page.add_init_script(self._bridge_script)
        page.add_init_script(self._dialog_script)
        self._attach_page_listeners(page)

    def _on_run_page_closed(self, key: int) -> None:
        # The panel closes a run whose last tab closed (the person closed it, or Studio did):
        # that is this recording's "browser closed", seen at once rather than after the
        # no-pages grace period.
        self._run_pages_open.discard(key)
        if not self._run_pages_open:
            self._on_browser_disconnected()

    def _classify_pages_sync(self, pages: list[Any]) -> list[Any]:
        """The subset of Electron's pages that belong to this recording. A page is looked up once
        (its devtools target id against the panel's list for this run) and remembered either way;
        a lookup that fails leaves it unknown for the next call to retry."""
        unknown = [p for p in pages if id(p) not in self._run_page_ids and id(p) not in self._foreign_page_ids]
        if unknown:
            try:
                ours = host_view.run_target_ids(self.session_id)
            except Exception as exc:  # noqa: BLE001
                self.binding_errors.append(f"panel_targets_error: {exc!s}")
                ours = None
            for page in unknown if ours is not None else []:
                try:
                    target = host_view.target_id(self._context, page)
                except Exception:  # noqa: BLE001 — closing or not yet attached: retry next time
                    continue
                if target not in ours:
                    self._foreign_page_ids.add(id(page))
                    continue
                self._page_target_ids[id(page)] = target
                try:
                    self._adopt_run_page_sync(page)
                    self._page = page  # a newly opened tab becomes the active one, as in _on_context_page
                except Exception as exc:  # noqa: BLE001
                    self.binding_errors.append(f"adopt_page_error: {exc!s}")
        return [p for p in pages if id(p) in self._run_page_ids]

    def _start_embedded_sync(self) -> None:
        """Embedded counterpart of the launch + new_context + new_page block in
        _run_sync_recorder: attach to a panel view and set it up the way that block sets up a
        launched context. Video starts here, before the first navigation, as record_video_dir's
        does from context creation. A sign-in (auth_mode) gets a panel view at the panel's own
        size and nothing else — no bridge, dialog override or video, as in the launched path."""
        browser, context, page, _tab = host_view.acquire(
            self._playwright,
            self.session_id,
            kind="signin" if self.auth_mode else "record",
            label=self.label,
            viewport=None if self.auth_mode else host_view.RECORD_VIEWPORT,
        )
        self._browser, self._context, self._page = browser, context, page
        self._browser.on("disconnected", lambda _: self._on_browser_disconnected())
        self.browser_open = True
        try:
            self._page_target_ids[id(page)] = host_view.target_id(context, page)
            if self.storage_state_path and Path(self.storage_state_path).is_file():
                state = json.loads(Path(self.storage_state_path).read_text(encoding="utf-8"))
                host_view.seed_storage_state(context, page, state)
            if self.auth_mode:
                self._adopt_run_page_sync(page)
                return
            self._video_session_start = time.monotonic()
            self._bridge_script = _load_bridge_script(self.capture_hover)
            self._dialog_server = DialogSyncServer(
                on_opened=self._on_dialog_server_opened,
                on_answered=self._on_dialog_server_answered,
                on_timed_out=self._on_dialog_server_timed_out,
            )
            self._dialog_script = DIALOG_OVERRIDE_SCRIPT_TEMPLATE.replace("{PORT}", str(self._dialog_server.port))
            self._adopt_run_page_sync(page)
            rec = self._start_screencast_sync(page, "recording.webm")
            self._video_session_start_wall_ms = rec.start_wall_ms
        except Exception:
            host_view.release(self.session_id)
            raise

    def _compute_event_timestamp_ms(self, action: dict[str, Any], tab_id: str | None = None) -> int | None:
        """Convert ISO 8601 action.timestamp to ms offset from that tab's own video start.

        Each tab's video starts at a different wall-clock moment (whenever that page opened),
        so the offset must be relative to the tab that produced the event, not the session's
        first tab — otherwise a second tab's frame extraction cuts at the wrong timestamp
        entirely. Falls back to the session-wide start (tab_0's start, identical value) when
        tab_id is unknown, which keeps single-tab recordings byte-identical to before.
        """
        ts_iso = str((action or {}).get("timestamp") or "")
        if not ts_iso:
            return None
        try:
            from datetime import datetime, timezone
            # Bridge.js produces e.g. "2025-02-15T14:32:10.123Z" — replace Z with +00:00 for fromisoformat
            iso_clean = ts_iso.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            wall_ms = int(dt.timestamp() * 1000)
            tab_meta = self._tab_meta.get(tab_id or "") if tab_id else None
            start_wall_ms = (
                tab_meta.get("video_start_wall_ms") if tab_meta else None
            ) or self._video_session_start_wall_ms
            offset = wall_ms - start_wall_ms
            return max(0, offset)
        except (ValueError, TypeError):
            return None

    def _autosave_storage_state_sync(self, *, force: bool = False) -> None:
        if not self.storage_state_autosave_path or self._context is None:
            return
        # context.storage_state() is a live CDP round-trip. If the browser has already
        # disconnected (e.g. the user just closed the only window and the process is
        # mid-teardown), this call can block indefinitely waiting for a response that
        # will never arrive — freezing this thread forever and leaving browser_open
        # stuck True, since the finally block that clears it never gets reached.
        # Skip rather than risk that hang; the last successful periodic save (or lack
        # of one) is the best we can do once the connection is already gone.
        if not self.browser_open:
            return
        now = time.monotonic()
        if not force and now - self._last_storage_state_save_at < 6.0:
            return
        try:
            path = Path(self.storage_state_autosave_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.embedded:
                # context.storage_state() would read Electron's default session, not the run's.
                previous = None
                if path.is_file():
                    try:
                        previous = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        previous = None
                state = host_view.capture_state(self._context, self._open_pages_sync(), previous)
                path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            else:
                self._context.storage_state(path=str(path))
            self._last_storage_state_save_at = now
            if self.auth_mode:
                self.auth_captured = True
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"storage_state_autosave_error: {exc!s}")

    def _standalone_probe_observation_sync(self, url: str, *, storage_state: str | None) -> dict | None:
        """A throwaway headless browser, launched from this session's already-running
        sync_playwright driver (self._playwright) without disturbing the live login browser —
        same pattern check_app_session_sync/_probe_app_session_sync use standalone. `storage_state`
        is a path to load (the RELOAD capture) or None for a genuinely fresh, cookie-less context
        (the OUT capture)."""
        if self._playwright is None:
            return None
        try:
            browser = self._playwright.chromium.launch(headless=True)
            try:
                context = (
                    browser.new_context(storage_state=storage_state) if storage_state else browser.new_context()
                )
                page = context.new_page()
                return _navigate_and_observe_sync(page, context, url)
            finally:
                browser.close()
        except Exception:  # noqa: BLE001
            return None

    def _perform_learn_auth_sync(self, login_url: str, existing: dict | None = None) -> tuple[dict | None, str]:
        """Runs on the recorder's OWN thread (called from the pump loop — see the
        _learn_auth_pending check above) because it touches self._context, which only that
        thread may touch. See learn_auth() below for the public, cross-thread-safe entry point.

        Observes the SAME probe url (login_url — the existing "login entry" concept the runtime
        already keys off, see runtime/app/browser.js::_loginEntryUrl) three ways — LIVE (a new
        tab in the just-authenticated context), OUT (a fresh, cookie-less context) and RELOAD (a
        fresh context restored from the just-saved session) — and only returns a definition that
        passes auth_learning.self_test. See auth_learning.py's module docstring for why.

        `existing` is the app's currently-saved definition (Reconnect, not a first-time Connect).
        If it still passes self_test against these three fresh observations, it's returned
        UNCHANGED rather than replaced by a freshly-learned one: the site's sign-in setup hasn't
        actually changed just because the session was refreshed, and a byte-identical definition
        keeps the built pack's `_sign_in_setup_drift` check from firing a false "rebuild" demand
        (AUTH-15) on plain re-logins."""
        if self._context is None or not self.browser_open:
            return None, "The login window closed before this could be confirmed."

        self._autosave_storage_state_sync(force=True)
        if not self.storage_state_autosave_path or not Path(self.storage_state_autosave_path).is_file():
            return None, "Nothing was captured to learn from yet."

        try:
            if self.embedded:
                live_obs = self._embedded_live_observation_sync(login_url)
            else:
                live_page = self._context.new_page()
                try:
                    live_obs = _navigate_and_observe_sync(live_page, self._context, login_url)
                finally:
                    live_page.close()
        except Exception as exc:  # noqa: BLE001
            return None, f"Couldn't check the signed-in page just now: {exc}"

        out_obs = self._standalone_probe_observation_sync(login_url, storage_state=None)
        if out_obs is None:
            return None, "Couldn't open a private check of the sign-in page."

        reload_obs = self._standalone_probe_observation_sync(
            login_url, storage_state=self.storage_state_autosave_path
        )
        if reload_obs is None:
            return None, "Couldn't confirm the saved session on its own."

        if existing and auth_learning.self_test(existing, live_obs, out_obs, reload_obs)[0]:
            return existing, ""  # site's setup hasn't changed — keep it byte-identical

        definition = auth_learning.learn(
            live_obs, out_obs, journey_hosts=list(self.journey_hosts), probe_url=login_url
        )
        ok, reason = auth_learning.self_test(definition, live_obs, out_obs, reload_obs)
        if not ok:
            return None, reason
        return definition, ""

    def _embedded_live_observation_sync(self, login_url: str) -> dict:
        """The LIVE capture in the panel: Electron can't open a page over CDP, so the panel adds
        a background tab to the sign-in run (same partition, same session) and this observes it.
        Its cookie names come from that partition, not context.cookies() (Electron's default
        session — see host_view.py)."""
        created = host_view.control("new_tab", runId=self.session_id, label="Checking sign-in", focus=False)
        try:
            page = host_view.find_page_by_marker(self._context, created["markerUrl"])
            obs = _navigate_and_observe_sync(page, self._context, login_url)
            obs["cookie_names"] = [c["name"] for c in host_view.page_cookies(self._context, page)]
            return obs
        finally:
            try:
                host_view.control("close_view", runId=self.session_id, tabId=created["tabId"])
            except Exception:  # noqa: BLE001 — the run may already be closing
                pass

    async def learn_auth(self, login_url: str, existing: dict | None = None) -> tuple[dict | None, str]:
        """Cross-thread entry point for cmd_finish_group_app_auth (handlers/groups.py), called
        from the Backend's own asyncio loop while the login browser is still open — same
        request/poll bridge start()/stop() use to reach across into the recorder thread. Returns
        (definition, "") on success, or (None, human-readable reason) when the self-test failed
        or the browser was in no state to check.

        `existing` is the app's currently-saved definition, passed through on a Reconnect so a
        still-valid definition comes back unchanged instead of a freshly re-learned one — see
        _perform_learn_auth_sync's docstring."""
        if self._context is None or not self.browser_open:
            return None, "The login window closed before this could be confirmed."
        self._learn_auth_done.clear()
        self._learn_auth_pending = {"login_url": login_url, "existing": existing}
        while not self._learn_auth_done.is_set():
            await asyncio.sleep(0.05)
        self._learn_auth_pending = None
        return self._learn_auth_result

    def _idle_sync(self, seconds: float) -> None:
        """The pump loop's pause between ticks. Playwright's sync API only takes in the browser's
        events (a navigation, a new page) while this thread is inside a Playwright call; a recording
        makes plenty each tick, but a sign-in makes none, so its page.url stayed on the login page
        forever — nothing saved on leaving it, success_url never matched, and the conversation could
        never notice the person had signed in. Waiting through the page keeps the events flowing."""
        if self.auth_mode and self._page is not None:
            try:
                self._page.wait_for_timeout(seconds * 1000)
                return
            except Exception:  # noqa: BLE001 — the page closed under us: a plain pause
                pass
        time.sleep(seconds)

    def _open_pages_sync(self) -> list[Any]:
        if self._context is None:
            return []
        try:
            pages = [page for page in self._context.pages if not page.is_closed()]
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"page_list_error: {exc!s}")
            return []
        return self._classify_pages_sync(pages) if self.embedded else pages

    def _active_page_sync(self) -> Any | None:
        if self._page is not None:
            try:
                if not self._page.is_closed():
                    self._remember_page_url_sync(self._page)
                    return self._page
            except Exception:  # noqa: BLE001
                pass
        pages = self._open_pages_sync()
        if pages:
            self._page = pages[-1]
            self._remember_page_url_sync(self._page)
            return self._page
        return None

    def _register_new_pages_sync(self) -> None:
        """Assign a stable tab id to every page not yet registered.

        Pump-loop only — resolves page.opener()/page.video via real Playwright calls, which
        are unsafe from inside the expose_binding dispatch (see _binding_sink_sync). Called
        every tick, so a newly opened tab gets an id within one tick (~0.2s) even before its
        first recorded event arrives.
        """
        if self.auth_mode:
            return
        for page in self._open_pages_sync():
            key = id(page)
            if key in self._tab_ids:
                continue
            tab_id = f"tab_{self._tab_seq}"
            self._tab_seq += 1
            self._tab_ids[key] = tab_id
            self._tab_pages[tab_id] = page
            opened_by = "initial"
            opener_tab: str | None = None
            if self._tab_seq > 1:  # not the first page ever seen
                try:
                    opener = page.opener()
                except Exception:  # noqa: BLE001
                    opener = None
                if opener is not None:
                    opened_by = "site"
                    opener_tab = self._tab_ids.get(id(opener))
                else:
                    opened_by = "user"
            video_path: str | None = None
            video_start_wall_ms = int(time.time() * 1000)
            if self.embedded:
                # tab_0's video started before its first navigation (_start_embedded_sync).
                rec = self._screencasts.get(key)
                try:
                    if rec is None:
                        rec = self._start_screencast_sync(page, f"recording-{tab_id}.webm")
                    video_path = str(rec.out_path)
                    video_start_wall_ms = rec.start_wall_ms
                except Exception as exc:  # noqa: BLE001
                    self.binding_errors.append(f"screencast_start_error:{tab_id}: {exc!s}")
            else:
                try:
                    video_path = page.video.path() if page.video else None
                except Exception:  # noqa: BLE001
                    video_path = None
            try:
                page_url = page.url
            except Exception:  # noqa: BLE001
                page_url = ""
            self._tab_meta[tab_id] = {
                "index": self._tab_seq - 1,
                "opened_by": opened_by,
                "opener_tab": opener_tab,
                "last_url": str(page_url or "").strip(),
                "video_start_wall_ms": video_start_wall_ms,
                "video_path": video_path,
            }
            self._ensure_nav_history_session_sync(page)

    def _tab_id_for_page(self, page: Any | None) -> str | None:
        if page is None:
            return None
        return self._tab_ids.get(id(page))

    def _ensure_nav_history_session_sync(self, page: Any) -> None:
        """Create the per-page CDP session used for Back/Forward detection and take the
        baseline history snapshot. Pump-loop only (new_cdp_session is a real Playwright
        call). The baseline is taken at registration time — for tab_0 that is right after
        the recording's own start goto(), so the start navigation itself is never misread.
        A page whose baseline could not be taken simply skips its first check (no event),
        which covers a popup/user tab that navigated before its first pump tick."""
        key = id(page)
        if key in self._nav_cdp_sessions:
            return
        try:
            session = self._context.new_cdp_session(page)
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"nav_history_cdp_error: {exc!s}")
            return
        self._nav_cdp_sessions[key] = session
        state = self._read_nav_history_sync(session)
        if state is not None:
            self._nav_history_state[key] = state
        try:
            session.send("Page.enable")
            session.on(
                "Page.frameRequestedNavigation",
                lambda _evt, k=key: self._nav_pending_renderer_initiated.__setitem__(
                    k, time.monotonic()
                ),
            )
            session.on(
                "Page.navigatedWithinDocument",
                lambda _evt, k=key: self._nav_same_document.add(k),
            )
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: worst case, every navigation on this page is treated as
            # browser-initiated (over-reports manual_navigate instead of under-reporting).
            self.binding_errors.append(f"nav_renderer_flag_setup_error: {exc!s}")

    def _read_nav_history_sync(self, session: Any) -> dict[str, Any] | None:
        try:
            hist = session.send("Page.getNavigationHistory")
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"nav_history_read_error: {exc!s}")
            return None
        try:
            entries = [str(e.get("url") or "") for e in (hist.get("entries") or [])]
            return {"index": int(hist.get("currentIndex") or 0), "entries": entries}
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"nav_history_parse_error: {exc!s}")
            return None

    def _pump_ping_sync(self, page: Any) -> None:
        """Forces a Playwright round-trip so its sync-API dispatcher keeps servicing incoming
        messages (bridge/binding callbacks, dialog and nav events) between recorded actions —
        called once per open page, every pump tick. The return value is never used; only the
        round-trip matters.

        Deliberately NOT page.evaluate()/frame.evaluate(): those execute inside the page's own
        renderer, which a native JS dialog blocks with no way back — Playwright's evaluate() has
        no timeout parameter at all (see the note on _BRIDGE_INSTALL_SETTLE_S for the same
        constraint elsewhere in this file). If a dialog opened in the instant this ping was
        in flight, that call would simply never return, wedging this exact loop — which is the
        only thing that can ever resolve the dialog it's now stuck behind. That reentrancy
        deadlock was the actual root cause of a real bug: Studio's own dialog modal would get
        answered but never close, and the still-unacknowledged native dialog would eventually
        surface in Chromium and need answering a second time.

        Page.getNavigationHistory is a browser/target-level CDP command — it never touches
        Runtime.evaluate, so it cannot be blocked by an open dialog. Reusing the per-page CDP
        session _ensure_nav_history_session_sync already keeps for Back/Forward tracking avoids
        creating a second one just for this."""
        session = self._nav_cdp_sessions.get(id(page))
        if session is not None:
            try:
                session.send("Page.getNavigationHistory")
                return
            except Exception:  # noqa: BLE001
                pass  # fall through to the evaluate() fallback below
        # No CDP session available for this page (creation failed earlier, or it hasn't been
        # through _register_new_pages_sync yet this tick) — evaluate() is a fallback for that
        # rarer case, not the routine path, so the dialog-hang risk it carries is bounded by how
        # rarely it's actually reached.
        page.evaluate("() => 0")

    def _queue_nav_history_check(self, page: Any) -> None:
        if self.auth_mode or page is None:
            return
        if any(p is page for p in self._nav_check_pages):
            return
        self._nav_check_pages.append(page)

    def _drain_nav_history_checks_sync(self) -> None:
        """Classify queued navigations against each page's last known history snapshot.
        Pump-loop only. Multiple navigations between checks coalesce into one comparison —
        acceptable degradation, see the strictness note on the field comment."""
        while self._nav_check_pages:
            page = self._nav_check_pages.pop(0)
            key = id(page)
            session = self._nav_cdp_sessions.get(key)
            try:
                closed = page.is_closed()
            except Exception:  # noqa: BLE001
                closed = True
            if closed:
                self._nav_cdp_sessions.pop(key, None)
                self._nav_history_state.pop(key, None)
                self._nav_pending_renderer_initiated.pop(key, None)
                self._nav_committed_at.pop(key, None)
                self._nav_same_document.discard(key)
                self._last_interaction_at.pop(key, None)
                continue
            if session is None:
                continue
            cur = self._read_nav_history_sync(session)
            if cur is None:
                continue
            prev = self._nav_history_state.get(key)
            self._nav_history_state[key] = cur
            if prev is None:
                # No earlier snapshot — first navigation observed on this page.
                continue
            prev_index = int(prev["index"])
            cur_index = int(cur["index"])
            kind = ""
            if cur_index < prev_index:
                kind = "browser_back"
            elif (
                cur_index > prev_index
                and 0 <= cur_index < len(prev["entries"])
                and cur_index < len(cur["entries"])
                and str(prev["entries"][cur_index]) == str(cur["entries"][cur_index])
            ):
                # Moved forward into a slot the previous snapshot already occupied with the
                # same URL — a real history forward. A fresh link click truncates forward
                # entries and lands on a NEW url, so it fails this equality test.
                kind = "browser_forward"
            to_url = str(cur["entries"][cur_index]) if 0 <= cur_index < len(cur["entries"]) else ""
            from_url = str(prev["entries"][prev_index]) if 0 <= prev_index < len(prev["entries"]) else ""
            if from_url and is_blank_url(from_url):
                # A panel tab starts on its internal marker page (about:blank?conxa_run=...); the
                # recording should say what a browser would — the tab was blank.
                from_url = "about:blank"
            if not kind:
                # Not a Back/Forward. If the page itself never asked to navigate (no
                # Page.frameRequestedNavigation since the last check), the browser did this on
                # its own — the user retyped the address bar, used a bookmark, etc. Nothing else
                # in the recording will ever reproduce that, so it needs its own explicit step.
                requested_at = self._nav_pending_renderer_initiated.pop(key, None)
                committed_at = self._nav_committed_at.pop(key, None) or time.monotonic()
                renderer_initiated = (
                    requested_at is not None
                    and 0 <= (committed_at - requested_at) <= self._NAV_RENDERER_INITIATED_TTL_S
                )
                same_document = key in self._nav_same_document
                self._nav_same_document.discard(key)
                interacted_at = self._last_interaction_at.get(key)
                after_interaction = (
                    interacted_at is not None
                    and -1.0 <= (committed_at - interacted_at) <= self._NAV_INTERACTION_WINDOW_S
                )
                if renderer_initiated or not to_url or to_url == from_url or is_blank_url(to_url):
                    continue
                # Not requested by the page through CDP, but a SPA route change or a navigation
                # right after a click/submit is still that action's EFFECT, not something the
                # user did on their own. Kept as evidence (cause="action") so the compiler can
                # fold it into the action's post-condition instead of replaying the URL.
                payload = {"from_url": from_url, "to_url": to_url}
                if same_document or after_interaction:
                    payload["cause"] = "action"
                self._enqueue_synthetic("manual_navigate", json.dumps(payload), src_page=page)
                continue
            self._nav_pending_renderer_initiated.pop(key, None)
            self._nav_committed_at.pop(key, None)
            self._nav_same_document.discard(key)
            self._enqueue_synthetic(
                kind,
                json.dumps({"from_url": from_url, "to_url": to_url}),
                src_page=page,
            )

    def _tab_context_for_page(self, page: Any | None) -> dict[str, Any]:
        return self._tab_context_for_tab_id(self._tab_id_for_page(page))

    def _tab_context_for_tab_id(self, tab_id: str | None) -> dict[str, Any]:
        tab_id = tab_id or "tab_0"
        meta = self._tab_meta.get(tab_id, {})
        return {
            "id": tab_id,
            "index": meta.get("index", 0),
            "opened_by": meta.get("opened_by", "initial"),
            "opener_tab": meta.get("opener_tab"),
            # This tab's own last-known URL — not the session-wide current_url, which is
            # shared across every open tab and gets clobbered by whichever page navigated
            # most recently (e.g. a popup on another tab), mislabeling this tab's steps.
            "url": meta.get("last_url") or self.current_url,
        }

    def _remember_bridge_install_error(self, message: str) -> None:
        key = str(message or "").strip()[:240]
        if not key or key in self._bridge_install_error_keys:
            return
        self._bridge_install_error_keys.add(key)
        self.binding_errors.append(f"bridge_frame_install_error: {key}")

    def _ensure_bridge_installed_sync(self, page: Any) -> None:
        if self.auth_mode or not self._bridge_script or page is None:
            return
        try:
            if page.is_closed():
                return
        except Exception:  # noqa: BLE001
            return
        frames = []
        try:
            frames = list(getattr(page, "frames", []) or [])
        except Exception as exc:  # noqa: BLE001
            self._remember_bridge_install_error(str(exc))
            return
        for frame in frames:
            self._ensure_bridge_installed_in_frame_sync(frame)

    def _log_bridge_attempt(self, url: str, outcome: str) -> None:
        # Deliberately NOT deduplicated (unlike _remember_bridge_install_error) — only logged for
        # frames that need install/reinstall or fail, so it stays small for well-behaved frames
        # while capturing the full timing/repeat-count pattern for a frame that keeps failing.
        if len(self._frame_lifecycle) >= 2000:
            return
        self._frame_lifecycle.append({
            "kind": "bridge_attempt",
            "url": url,
            "parent_url": None,
            "ts": int(time.time() * 1000),
            "outcome": outcome,
        })

    # HubSpot-style embedded panels (e.g. the "Create Contact" object-builder-ui iframe) can
    # churn through several attach/navigate/detach cycles before settling — confirmed via
    # recorder_diag.json's bridge_attempt log across several real recordings: the same frame
    # fails every single check ("Frame was detached"/"Execution context was destroyed") for its
    # entire practical interactive lifetime, only happening to stabilize right as the session
    # ends, too late for anything typed earlier to have been captured. A single check racing
    # that churn has no better than a coin-flip's chance of landing inside a stable window; a
    # short in-place retry loop does, since the churn cycle itself is typically tens of
    # milliseconds — much shorter than the ~200ms gap between external pump ticks/frame events.
    _BRIDGE_INSTALL_MAX_ATTEMPTS = 5
    _BRIDGE_INSTALL_RETRY_DELAY_S = 0.05
    # How long to stay away from a frame after it attaches/navigates before calling
    # frame.evaluate() into it at all. Playwright's sync-API evaluate() has no timeout and is
    # thread-affine (no safe cross-thread watchdog is possible), so a call that lands on a frame
    # mid-churn can hang the recorder's single pump thread forever with no exception raised —
    # confirmed across multiple real HubSpot recordings where the whole recorder went silent the
    # instant a check landed right on/near an attach event for a heavy, rapidly re-parenting
    # iframe. bridge.js's own add_init_script + in-page Document.prototype reinstall patch already
    # handle injection with zero Python round-trip, so this polling check is only a backstop —
    # there's no real cost to letting a freshly-transitioned frame settle first.
    #
    # 0.3s was tried first and wasn't enough — real recordings show HubSpot's object-builder-ui
    # panel can keep re-navigating/re-parenting for several seconds before settling (one session's
    # bridge_attempt log showed ~38 failed attempts before it ever stabilized), and even a
    # deliberately slow, ~52s-paced recording attempt never recovered on its own afterward. Widened
    # to 2.5s to clear that window; _on_frame_navigated refreshes the timestamp on every further
    # navigation, so a frame still actively churning keeps deferring rather than getting checked
    # exactly once at a fixed offset from its first attach.
    _BRIDGE_INSTALL_SETTLE_S = 2.5

    def _ensure_bridge_installed_in_frame_sync(self, frame: Any) -> None:
        if self.auth_mode or not self._bridge_script or frame is None:
            return
        last_transition = self._frame_last_transition_ts.get(id(frame))
        if last_transition is not None and (time.monotonic() - last_transition) < self._BRIDGE_INSTALL_SETTLE_S:
            return
        try:
            frame_url_for_log = _frame_url(frame)
        except Exception:  # noqa: BLE001
            frame_url_for_log = ""
        last_exc: Exception | None = None
        for attempt in range(self._BRIDGE_INSTALL_MAX_ATTEMPTS):
            try:
                # Check both window flag AND document flag. The window flag persists across
                # document.open() (HubSpot micro-frontend pattern) but the document flag does
                # not always — bridge.js's own Document.prototype.open/write/writeln patch is
                # the primary defense against that swap (re-attaches listeners synchronously,
                # in-page, the moment it happens), so this check is now just a backstop for the
                # rare frame where that patch didn't take.
                state = frame.evaluate("""
                    () => ({
                        hasWin: !!window.__SKILL_BRIDGE_V1__,
                        hasDoc: !!(document && document.__SKILL_BRIDGE_DOC_V1__),
                    })
                """)
                bridge_action_taken = False
                if not state.get("hasWin"):
                    frame.evaluate(self._bridge_script)
                    self._log_bridge_attempt(frame_url_for_log, "installed_fresh")
                    bridge_action_taken = True
                elif not state.get("hasDoc"):
                    # Bridge already installed once in this realm — reinstall via the exposed
                    # hook (reuses the persisted closures from the original install) rather than
                    # re-running the whole script, which would recreate fresh closures and
                    # duplicate listeners instead of replacing the stale ones. The hook's return
                    # value (listener count) confirms real listeners were actually attached, not
                    # just that the call didn't throw.
                    listener_count = frame.evaluate(
                        "() => (typeof window.__SKILL_REINSTALL_DOC__ === 'function')"
                        " ? window.__SKILL_REINSTALL_DOC__() : -1"
                    )
                    self._log_bridge_attempt(
                        frame_url_for_log, f"reinstalled_doc (listener_count={listener_count})"
                    )
                    bridge_action_taken = True
                # Emit one diagnostic per unique frame URL so failures are visible. Deferred to a
                # tick where the frame is already stable (no install/reinstall just happened this
                # call) — two more evaluate() calls immediately after reinstalling into a frame
                # that's still mid-churn (e.g. HubSpot's object-builder-ui) is exactly the
                # highest-risk moment for a call that never returns rather than raising.
                if not bridge_action_taken:
                    frame_url = _frame_url(frame)
                    if frame_url and frame_url not in self._frame_diag_seen:
                        self._frame_diag_seen.add(frame_url)
                        try:
                            bridge_ok = frame.evaluate("() => !!window.__SKILL_BRIDGE_V1__")
                            binding_ok = frame.evaluate("() => typeof window.__skillReport === 'function'")
                            if not bridge_ok:
                                self.binding_errors.append(f"frame_bridge_missing:{frame_url}")
                            if not binding_ok:
                                self.binding_errors.append(f"frame_binding_missing:{frame_url}")
                        except Exception:  # noqa: BLE001
                            self.binding_errors.append(f"frame_diag_error:{frame_url}")
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._log_bridge_attempt(
                    frame_url_for_log,
                    f"attempt {attempt + 1}/{self._BRIDGE_INSTALL_MAX_ATTEMPTS} failed: {exc!s}"[:200],
                )
                msg = str(exc).lower()
                transient = "detached" in msg or "destroyed" in msg or "navigation" in msg
                if not transient or attempt == self._BRIDGE_INSTALL_MAX_ATTEMPTS - 1:
                    break
                time.sleep(self._BRIDGE_INSTALL_RETRY_DELAY_S)
        if last_exc is not None:
            self._remember_bridge_install_error(str(last_exc))

    def _log_frame_lifecycle(self, kind: str, frame: Any) -> None:
        # A frame mid-detach can raise on nearly any access (this is the exact "Frame was
        # detached"/"Execution context was destroyed" failure mode this log exists to catch) —
        # guard each lookup individually so the event itself is still recorded even when its
        # url/parent can't be resolved, rather than dropping the whole entry.
        if len(self._frame_lifecycle) >= 2000:
            return
        try:
            url = _frame_url(frame)
        except Exception:  # noqa: BLE001
            url = ""
        try:
            parent = _frame_parent(frame)
            parent_url = _frame_url(parent) if parent else None
        except Exception:  # noqa: BLE001
            parent_url = None
        self._frame_lifecycle.append({
            "kind": kind,
            "url": url,
            "parent_url": parent_url,
            "ts": int(time.time() * 1000),
        })

    def _on_frame_ready(self, frame: Any) -> None:
        self._ensure_bridge_installed_in_frame_sync(frame)

    def _on_frame_attached(self, frame: Any) -> None:
        self._frame_last_transition_ts[id(frame)] = time.monotonic()
        self._log_frame_lifecycle("attached", frame)
        self._on_frame_ready(frame)

    def _on_frame_navigated(self, frame: Any) -> None:
        self._frame_last_transition_ts[id(frame)] = time.monotonic()
        self._log_frame_lifecycle("navigated", frame)
        self._on_frame_ready(frame)

    def _on_frame_detached(self, frame: Any) -> None:
        self._log_frame_lifecycle("detached", frame)

    def _consume_payload_safe_sync(
        self, payload: dict[str, Any], src_page: Any | None = None, src_frame: Any | None = None
    ) -> None:
        try:
            self._consume_payload_sync(payload, src_page, src_frame)
        except Exception as exc:  # noqa: BLE001
            raw_action = payload.get("action") if isinstance(payload, dict) else {}
            action = str((raw_action or {}).get("action") or "")
            suffix = f":{action}" if action else ""
            self.binding_errors.append(f"event_capture_error{suffix}: {exc!s}")

    async def start(self) -> None:
        self._stop_requested.clear()
        self._startup_done.clear()
        self._startup_error = ""
        self.browser_open = False
        self.ended_by_user = False
        self._thread = threading.Thread(target=self._run_sync_recorder, daemon=True)
        self._thread.start()

        while not self._startup_done.is_set():
            await asyncio.sleep(0.05)

        if self._startup_error:
            raise RuntimeError(self._startup_error)

    def _on_browser_disconnected(self) -> None:
        self.browser_open = False
        self.ended_by_user = True
        self._stop_requested.set()

    def _binding_sink_sync(self, source: Any, payload: dict[str, Any]) -> None:
        try:
            payload_copy = copy.deepcopy(payload)
            # Trace payloads go to the diagnostics list, not the event queue.
            if payload_copy.get("_trace"):
                if len(self._traces) < 5000:
                    self._traces.append(payload_copy)
                return
            src_page = source.get("page") if isinstance(source, dict) else None
            src_frame = source.get("frame") if isinstance(source, dict) else None
            # Do NOT call _frame_context_and_offset_sync (or any other Playwright sync-API
            # call) here — this callback is invoked by Playwright's dispatcher while
            # delivering an expose_binding call, and a nested blocking call to the same
            # dispatcher from inside that delivery is a reentrancy hazard that can deadlock
            # forever with no exception raised (confirmed: every captured iframe-sourced
            # event across many real HubSpot recordings silently vanished right here, since
            # this was the only extra Playwright call in the path from browser to
            # events.jsonl for any event with a non-top-level source frame — top-level
            # clicks, which skip this call entirely, always worked fine). Deferred to
            # _consume_payload_sync, which runs from the plain pump-loop drain, not from
            # inside a callback the dispatcher is still in the middle of delivering.
            if self._paused:
                payload_copy["_paused"] = True
            self._pending_payloads.put((payload_copy, src_page, src_frame))
            self._last_enqueue_at = time.monotonic()
            if src_page is not None and str((payload_copy.get("action") or {}).get("action") or "") in self._INTERACTION_ACTIONS:
                self._last_interaction_at[id(src_page)] = self._last_enqueue_at
        except Exception as exc:  # noqa: BLE001 — recorder must never crash from page callback
            self.binding_errors.append(f"binding_error: {exc!s}")

    def _delete_visual_assets(self, session_dir: Path, event: RecordedEvent) -> None:
        visual = event.visual
        for rel in (visual.full_screenshot, visual.element_snapshot):
            if not rel:
                continue
            try:
                p = (session_dir / rel).resolve()
                p.unlink(missing_ok=True)
            except Exception:
                # Best effort cleanup; recorder should never fail due to file deletion.
                continue

    def _write_diagnostics_sync(self, session_dir: Path) -> None:
        """Write binding_errors, traces, a frame tree snapshot, and the event-driven frame
        lifecycle log to recorder_diag.json."""
        frame_tree: list[dict] = []
        try:
            page = self._active_page_sync()
            if page is not None and not page.is_closed():
                for fr in list(getattr(page, "frames", []) or []):
                    entry: dict = {"url": _frame_url(fr)}
                    parent = _frame_parent(fr)
                    entry["parent_url"] = _frame_url(parent) if parent else None
                    try:
                        info = fr.evaluate(
                            "() => ({ bridge: !!window.__SKILL_BRIDGE_V1__, binding: typeof window.__skillReport === 'function', isTop: window === window.top })"
                        )
                        entry.update(info if isinstance(info, dict) else {})
                    except Exception:
                        entry["eval_error"] = True
                    if parent is not None:
                        try:
                            attrs, rect = _frame_element_attrs_and_rect(fr)
                            entry["iframe_attrs"] = {
                                k: attrs.get(k) for k in ("id", "data-test-id", "data-selenium-test", "name", "sandbox", "src")
                            }
                            entry["iframe_rect"] = rect
                        except Exception:
                            pass
                    frame_tree.append(entry)
        except Exception:  # noqa: BLE001
            pass
        out = session_dir / "recorder_diag.json"
        diag = {
            "binding_errors": self.binding_errors,
            "traces": self._traces,
            "frame_tree": frame_tree,
            "frame_snapshots": self._frame_snapshots,
            "frame_lifecycle": self._frame_lifecycle,
            "tabs": self._tab_meta,
        }
        try:
            out.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _write_environment_sync(self, session_dir: Path, page: Any) -> None:
        """EXEC-36: capture the recording environment (locale, timezone, viewport, device pixel
        ratio, a locale-formatted date sample, platform) once, at session start, so the runtime
        can WARN — never block — when a replay happens in a materially different environment
        (docs/TRD.md §9.7). Best-effort and swallowed on failure, same discipline as
        _write_diagnostics_sync; a missing sidecar (old session, or a page that never finished
        loading) compiles cleanly — build.py treats an absent environment.json as "unknown."
        Role/permissions are deliberately NOT captured here: there is no generic, reliable way to
        read the recording user's permissions from a page, and guessing would produce false
        warnings — left to PROD-1's first-run calibration instead.
        """
        try:
            env = page.evaluate(
                "() => {"
                "  const d = new Intl.DateTimeFormat().resolvedOptions();"
                "  return {"
                "    locale: navigator.language || '',"
                "    timezone: d.timeZone || '',"
                "    utc_offset_minutes: -new Date().getTimezoneOffset(),"
                "    viewport: { w: window.innerWidth, h: window.innerHeight },"
                "    device_pixel_ratio: window.devicePixelRatio || 1,"
                "    date_format_sample: new Date(2026, 0, 31).toLocaleDateString(),"
                "    platform: navigator.platform || '',"
                "  };"
                "}"
            )
        except Exception:  # noqa: BLE001
            return
        if not isinstance(env, dict):
            return
        out = session_dir / "environment.json"
        try:
            out.write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _rewrite_events_jsonl(self, session_dir: Path) -> None:
        out = session_dir / "events.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for ev in self._materialized:
                f.write(json.dumps(ev.model_dump(mode="json"), ensure_ascii=False) + "\n")
        self._write_diagnostics_sync(session_dir)

    def _should_merge_typing(self, prev: RecordedEvent, curr: RecordedEvent) -> bool:
        if prev.action.action != "type" or curr.action.action != "type":
            return False
        if prev.tab.id != curr.tab.id:
            return False
        return _typing_target_key(prev) == _typing_target_key(curr)

    def _consume_payload_sync(
        self, payload: dict[str, Any], src_page: Any | None = None, src_frame: Any | None = None
    ) -> None:
        # BUILD-38: a payload that arrived while paused is dropped, not recorded — and not
        # replayed on resume, which is why the flag is stamped at enqueue time.
        if payload.pop("_paused", False):
            return
        # Frame-chain/geometry extraction needs Playwright sync-API calls (walking the
        # ancestor chain via frame.evaluate()); safe here since this runs from the plain
        # pump-loop drain, never from inside the expose_binding callback that first received
        # this payload (see _binding_sink_sync for why it can't be done there).
        if src_frame is not None:
            frame_context, frame_offset = _frame_context_and_offset_sync(src_frame)
            if frame_context:
                payload["frame"] = frame_context
                payload["_frame_offset"] = frame_offset
        session_dir = self.data_root / "sessions" / self.session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        page_for_visuals = src_page or self._active_page_sync()
        # _tab_key (see _enqueue_synthetic) names a page the recorder could not resolve to a tab
        # id when the event fired. It is resolvable now — the pump loop registers new pages
        # before draining this queue. src_page stays the visuals/URL source either way: for a
        # popup that is deliberately the tab that CLICKED the link, not the tab that opened.
        tab_key = payload.pop("_tab_key", None)
        deferred_tab_id = self._tab_ids.get(tab_key) if tab_key is not None else None
        payload["tab"] = (
            self._tab_context_for_tab_id(deferred_tab_id)
            if deferred_tab_id
            else self._tab_context_for_page(page_for_visuals)
        )
        event = self._finalize_payload_sync(page_for_visuals, session_dir, payload)
        with self._lock:
            if self._materialized and self._should_merge_typing(self._materialized[-1], event):
                prev = self._materialized[-1]
                self._delete_visual_assets(session_dir, prev)
                self._materialized[-1] = event
            else:
                self._materialized.append(event)
        self._rewrite_events_jsonl(session_dir)
        metrics.inc("events_captured")
        hook = self.event_hook
        if hook is not None:
            try:
                hook(event)
            except Exception:  # noqa: BLE001 — a UI callback must never break recording
                pass

    A11Y_CAPTURE_TIMEOUT_MS = 2000

    def _capture_a11y_snapshot(self, page: Any) -> dict[str, Any] | None:
        """Capture an a11y snapshot, bounded by Playwright's own timeout.

        `Page.accessibility` was removed from Playwright (this repo pins 1.58.0, which has
        no such attribute) — `Locator.aria_snapshot()` is the replacement, returning a YAML
        string rather than a tree dict. Wrapped as {"aria_snapshot": <yaml>} so on-disk blob
        shape and read_a11y_snapshot's return type stay dict-shaped for callers.
        Returns the wrapper dict, or None on failure/timeout.

        MUST run on the thread that owns the Playwright session. The sync API is greenlet-based
        and bound to its creating thread, so calling it from a worker thread raises
        "Cannot switch to a different thread" — which is exactly what the previous
        threading.Thread wrapper did, on every event of every recording, making a11y capture
        dead-on-arrival since it was written. The blast radius was silent and wide: with no
        a11y tree on disk, identity_bundle.py's resolves_to_nothing() could not verify that a
        role+name selector actually resolves in the page it came from, so fabricated accessible
        names (a react-select input "named" after the nearest heading, e.g.) shipped as the
        bundle's highest-durability signal and matched nothing at replay.

        The thread only ever existed to bound the call; aria_snapshot takes a native `timeout`,
        so the bound is kept without leaving the session's thread.
        """
        if not settings.snapshot_capture_a11y:
            return None
        result: dict[str, Any] = {"snapshot": None, "elapsed": 0.0, "error": None}
        start = time.time()
        try:
            result["snapshot"] = page.locator("body").aria_snapshot(
                timeout=self.A11Y_CAPTURE_TIMEOUT_MS
            )
        except Exception as exc:  # noqa: BLE001 — reported below, not swallowed silently
            result["error"] = str(exc)
        finally:
            result["elapsed"] = time.time() - start
        snapshot = result.get("snapshot")
        elapsed = result.get("elapsed", 0.0)
        self._last_a11y_capture_time = elapsed
        if elapsed > 0.5:
            self._a11y_skip_count = 3
        if not isinstance(snapshot, str) or not snapshot.strip():
            if not self._a11y_capture_error_logged:
                self._a11y_capture_error_logged = True
                self.binding_errors.append(f"a11y_capture_error: {result.get('error') or 'empty snapshot'}")
            return None
        return {"aria_snapshot": snapshot}

    def _capture_dom_snapshot_sync(self, page: Any, dom_sig_short: str) -> dict[str, Any]:
        """Capture (and dedupe) the full DOM + a11y tree for the current page.

        Returns dict with keys: ref, dom_hash, dom_path, a11y_path. Empty on failure.
        Skips capture if the short bridge signature matches the previous one (dedup).
        """
        if not settings.snapshot_dedup_enabled:
            return {}
        if page is None:
            return {}
        try:
            if page.is_closed():
                return {}
        except Exception:  # noqa: BLE001
            return {}

        # Dedup fast path: same short signature as last action → reuse ref.
        if dom_sig_short and dom_sig_short == getattr(self, "_last_dom_sig_short", ""):
            return {
                "ref": self._last_snapshot_ref,
                "dom_hash": self._last_snapshot_hash,
                "dom_path": snapshot_store.relative_blob_path(self.session_id, self._last_snapshot_hash, "html.gz") if self._last_snapshot_hash else None,
                "a11y_path": snapshot_store.relative_blob_path(self.session_id, self._last_snapshot_hash, "a11y.json") if self._last_snapshot_hash else None,
            }

        # Capture full HTML.
        try:
            html = page.content()
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"dom_snapshot_error: {exc!s}")
            return {}

        h, _ = snapshot_store.save_dom_snapshot(self.session_id, html or "")

        # Capture a11y tree (best-effort, may be unavailable on some pages).
        a11y_path_str: str | None = None
        if settings.snapshot_capture_a11y and self._a11y_skip_count == 0:
            tree = self._capture_a11y_snapshot(page)
            if tree is not None:
                try:
                    if snapshot_store.save_a11y_snapshot(self.session_id, tree, h):
                        a11y_path_str = snapshot_store.relative_blob_path(self.session_id, h, "a11y.json")
                except Exception as exc:  # noqa: BLE001
                    self.binding_errors.append(f"a11y_snapshot_error: {exc!s}")
        if self._a11y_skip_count > 0:
            self._a11y_skip_count -= 1

        # Assign ref (UUID) per unique hash so events can join back to a snapshot.
        if h == self._last_snapshot_hash and self._last_snapshot_ref:
            ref = self._last_snapshot_ref
        else:
            ref = snapshot_store.new_snapshot_ref()
        self._last_snapshot_hash = h
        self._last_snapshot_ref = ref
        if dom_sig_short:
            self._last_dom_sig_short = dom_sig_short  # type: ignore[attr-defined]

        return {
            "ref": ref,
            "dom_hash": h,
            "dom_path": snapshot_store.relative_blob_path(self.session_id, h, "html.gz"),
            "a11y_path": a11y_path_str,
        }

    def _finalize_payload_sync(self, page, session_dir: Path, payload: dict[str, Any]) -> RecordedEvent:
        payload = _sanitize_surrogates(payload)
        self._seq += 1
        seq = self._seq
        vph = dict(payload.get("visual_placeholder") or {})
        bbox = dict(vph.get("bbox") or {"x": 0, "y": 0, "w": 0, "h": 0})
        frame_offset = payload.get("_frame_offset") if isinstance(payload.get("_frame_offset"), dict) else {}
        if frame_offset:
            try:
                bbox["x"] = int(round(float(bbox.get("x") or 0) + float(frame_offset.get("x") or 0)))
                bbox["y"] = int(round(float(bbox.get("y") or 0) + float(frame_offset.get("y") or 0)))
                if page is not None and not page.is_closed():
                    viewport = _viewport_string_from_page(page)
                    if viewport:
                        vph["viewport"] = viewport
            except (TypeError, ValueError):
                pass
        action = payload["action"]
        action_name = str((action or {}).get("action") or "")
        # Screenshots are no longer captured synchronously during recording.
        # full_screenshot and element_snapshot are set at session shutdown by
        # frame_extractor.py after extracting 5 frames per action from recording.webm.
        full_rel: str | None = None
        el_rel: str | None = None
        pol = get_policy_bundle().data
        timing = resolve_event_timing(action_name, pol)

        # Phase 2: capture full DOM + a11y snapshot (deduped by hash).
        dom_sig_short = str(payload.get("dom_signature_short") or "")
        snapshot_info: dict[str, Any] = {}
        try:
            snapshot_info = self._capture_dom_snapshot_sync(page, dom_sig_short)
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"snapshot_capture_error:{action_name}: {exc!s}")

        timestamp_ms: int | None = None
        if not self.auth_mode:
            if not self._video_session_start_wall_ms:
                raise RuntimeError(
                    "video session start time not initialized; non-auth sessions must record video"
                )
            timestamp_ms = self._compute_event_timestamp_ms(action, tab_id=(payload.get("tab") or {}).get("id"))
        extras: dict[str, Any] = {"sequence": seq, "session_id": self.session_id}
        if action_name == "upload_intent" and self._pending_file_pick:
            extras["file_pick"] = self._pending_file_pick
            self._pending_file_pick = None
        body = {
            "action": action,
            "target": payload["target"],
            "selectors": payload["selectors"],
            "context": payload["context"],
            "semantic": payload["semantic"],
            "anchors": payload.get("anchors") or [],
            "visual": {
                "full_screenshot": full_rel,
                "element_snapshot": el_rel,
                "bbox": bbox,
                "viewport": vph.get("viewport") or "",
                "scroll_position": vph.get("scroll_position") or "0,0",
                "timestamp_ms": timestamp_ms,
                "frames": {},
            },
            "page": payload["page"],
            "state_change": payload.get("state_change") or {"before": "", "after": ""},
            "timing": timing,
            "extras": extras,
            "frame": payload.get("frame") if isinstance(payload.get("frame"), dict) else {},
            "tab": payload.get("tab") if isinstance(payload.get("tab"), dict) else {},
            # Phase 2 signals.
            "ancestors": payload.get("ancestors") or [],
            "surrounding_text": str(payload.get("surrounding_text") or ""),
            "snapshot": {
                "ref": str(snapshot_info.get("ref") or ""),
                "dom_hash": str(snapshot_info.get("dom_hash") or ""),
                "dom_path": snapshot_info.get("dom_path"),
                "a11y_path": snapshot_info.get("a11y_path"),
            },
        }
        # bridge.js's optional observations. `body` is an explicit allow-list, so anything not
        # named here is silently replaced by RecordedEvent's None default — which is exactly what
        # happened to all five of these: the bridge computed them correctly on every event and
        # the finalizer dropped them on the floor, leaving date_context/choice_context/branch_hint
        # null in every recording ever made. That made the date-picker and multiple-choice
        # features dead on arrival end-to-end (the compiler had nothing to collapse or derive a
        # choice payload from) despite both being implemented and unit-tested on either side of
        # this line. Copy through only when present, so an older bridge that does not send them
        # still validates.
        for observation in ("branch_hint", "date_context", "choice_context",
                            "optionality", "post_condition", "dialog_context"):
            value = payload.get(observation)
            if value is not None:
                body[observation] = value
        return RecordedEvent.model_validate(body)

    def _make_synthetic_payload(self, kind: str, value_str: str, src_page: Any | None = None) -> dict[str, Any]:
        """Build a minimal bridge-compatible payload dict for Playwright-side events.

        `src_page`, when given, is the actual page whose listener fired this event (see
        _attach_page_listeners) — using it instead of always falling back to
        _active_page_sync() is what makes _consume_payload_sync's tab stamping correct for a
        download/dialog/popup that fires on a background tab while a different tab is active."""
        page_url = ""
        try:
            page = src_page if src_page is not None else self._active_page_sync()
            if page is not None and not page.is_closed():
                page_url = page.url
        except Exception:  # noqa: BLE001
            pass
        payload = {
            "action": {
                "action": kind,
                "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                "value": value_str,
            },
            "target": {"tag": "", "id": None, "classes": [], "inner_text": "", "role": None, "aria_label": None, "name": None},
            "selectors": {"css": "", "xpath": "", "text_based": "", "aria": ""},
            "context": {"parent": "", "siblings": [], "index_in_parent": 0, "form_context": None},
            "semantic": {"normalized_text": "", "role": "", "input_type": None, "intent_hint": ""},
            "anchors": [],
            "visual_placeholder": {"bbox": {"x": 0, "y": 0, "w": 0, "h": 0}, "viewport": "", "scroll_position": "0,0"},
            "page": {"url": page_url, "title": ""},
            "state_change": {"before": "", "after": ""},
            # Phase 2 defaults (synthetic events have no bridge signals).
            "ancestors": [],
            "surrounding_text": "",
            "dom_signature_short": "",
        }
        if kind in ("dialog_accept", "dialog_dismiss"):
            ctx = self._native_dialog_context(kind, value_str)
            if ctx:
                payload["dialog_context"] = ctx
        return payload

    @staticmethod
    def _native_dialog_context(kind: str, value_str: str) -> dict[str, Any] | None:
        """DialogContext for a native alert/confirm/prompt. Its message is carried inside the
        event's JSON value; the two possible buttons are OK and Cancel, with the answer given
        marked as chosen."""
        try:
            data = __import__("json").loads(value_str or "{}")
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        accepted = kind == "dialog_accept"
        dtype = str(data.get("type") or "")

        def _btn(name: str, chosen: bool) -> dict[str, Any]:
            return {"tag": "button", "role": "button", "name": name, "text": name, "chosen": chosen}

        buttons = [_btn("OK", accepted)]
        if dtype in ("confirm", "prompt"):
            buttons.append(_btn("Cancel", not accepted))
        return {
            "kind": "native",
            "role": "alertdialog",
            "title": dtype,
            "message": str(data.get("message") or "")[:300],
            "has_fields": dtype == "prompt",
            "buttons": buttons,
        }

    def _enqueue_synthetic(
        self, kind: str, value_str: str, src_page: Any | None = None, tab_key: int | None = None
    ) -> None:
        """`tab_key` is id(page) for a page whose tab id is NOT yet assigned at enqueue time —
        a popup, which Playwright reports from inside its own event callback, a tick before
        _register_new_pages_sync gets to name it. Stashing the raw object key (no Playwright
        call, so it is safe from inside that callback) defers the lookup to the pump-loop
        drain, which runs after registration. Without it the event is stamped with the OPENER's
        tab and the new tab never appears in the event stream at all."""
        try:
            payload = self._make_synthetic_payload(kind, value_str, src_page)
            if tab_key is not None:
                payload["_tab_key"] = tab_key
            if self._paused:
                payload["_paused"] = True
            self._pending_payloads.put((payload, src_page, None))
            self._last_enqueue_at = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"synthetic_event_error:{kind}: {exc!s}")

    @staticmethod
    def _unique_download_name(fname: str, taken: set[str]) -> str:
        # Two downloads in one recording can suggest the same filename — reserve a distinct
        # name so the second doesn't silently overwrite the first, same idea already shipped
        # on the runtime replay side (run.js::uniqueDownloadName, EXEC-11).
        base, ext = os.path.splitext(fname)
        candidate, n = fname, 2
        while candidate in taken:
            candidate = f"{base} ({n}){ext}"
            n += 1
        taken.add(candidate)
        return candidate

    @staticmethod
    def _zip_extract_dir(zip_path: Path) -> Path:
        """Return the folder containing extracted files (unwrap one wrapping dir).

        Mirrors run.js::extractZipOnce so record and replay behave identically.
        """
        dest = zip_path.with_suffix("")
        if not dest.exists():
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest)
            dest.mkdir(parents=True, exist_ok=True)
        entries = list(dest.iterdir())
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0]
        return dest

    @staticmethod
    def _extract_zip_once(zip_path: Path) -> list[str]:
        """Extract a downloaded zip into a sibling folder and return its top-level filenames.

        Mirrors run.js::extractZipOnce so record and replay behave identically: same
        stem-named sibling folder, idempotent (a second download of the same name is a no-op
        here since the caller already gave it a unique name), and a single top-level wrapping
        folder inside the zip is unwrapped once so the returned names are the real files, not
        one subdirectory. Recorded here (not read from the zip at compile time) because compile
        can run in a different session/machine that no longer has this file on disk.
        """
        dest = RecordingSession._zip_extract_dir(zip_path)
        return sorted(p.name for p in dest.iterdir() if p.is_file())

    @staticmethod
    def _fsync_path(path: Path) -> None:
        """Flush a file or directory to disk so the OS picker sees it immediately."""
        import sys as _sys

        if not path.exists():
            return
        if path.is_file():
            try:
                with open(path, "rb") as f:
                    os.fsync(f.fileno())
            except OSError:
                pass
            dir_to_sync = path.parent
        elif path.is_dir():
            dir_to_sync = path
        else:
            return
        # ponytail: no portable directory-fsync on Windows; file fsync above is enough
        if _sys.platform == "win32":
            return
        try:
            fd = os.open(str(dir_to_sync), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    @staticmethod
    def _notify_dir_changed(dir_path: Path) -> None:
        """Tell Windows Explorer a folder changed so the file picker lists new files."""
        import sys as _sys

        if _sys.platform != "win32":
            return
        try:
            import ctypes

            SHCNE_UPDATEDIR = 0x00001000
            SHCNF_PATHW = 0x0005
            ctypes.windll.shell32.SHChangeNotify(
                SHCNE_UPDATEDIR, SHCNF_PATHW, str(dir_path), None
            )
        except Exception:
            pass

    def _on_download(self, download: Any, src_page: Any | None = None) -> None:
        try:
            zip_members: list[str] | None = None
            if self._downloads_dir is not None:
                name = self._unique_download_name(
                    download.suggested_filename or f"download_{int(time.time())}",
                    self._taken_download_names,
                )
                dest = self._downloads_dir / name
                if self.embedded:
                    self._save_panel_download_sync(download.url, dest, src_page)
                else:
                    download.save_as(str(dest))
                if not dest.exists():
                    raise FileNotFoundError(f"download did not land on disk: {dest}")
                self._fsync_path(dest)
                if name.lower().endswith(".zip"):
                    extract_dir = self._zip_extract_dir(dest)
                    for entry in extract_dir.iterdir():
                        if entry.is_file():
                            self._fsync_path(entry)
                    self._fsync_path(extract_dir)
                    zip_members = self._extract_zip_once(dest)
                    self._last_download_dir = extract_dir
                    self._zip_downloads[name] = {
                        "zip_path": dest,
                        "extract_dir": extract_dir,
                        "members": zip_members,
                    }
                else:
                    self._last_download_dir = self._downloads_dir
            payload: dict[str, Any] = {"url": download.url, "suggested_filename": download.suggested_filename}
            if zip_members is not None:
                payload["zip_members"] = zip_members
            self._enqueue_synthetic("download_observed", json.dumps(payload), src_page=src_page)
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"download_event_error: {exc!s}")

    def _save_panel_download_sync(self, url: str, dest: Path, src_page: Any | None) -> None:
        """A panel run's download is saved by Electron, never by Playwright: the panel hands over
        the finished file (same as the runtime's EXEC-46 path).

        The click that started the download must be recorded BEFORE download_observed — the
        compiler reads "the next step is download_observed" as "this click downloads". bridge.js
        holds a click's report until the DOM settles (click_settle_max_ms, 250 ms by default), and
        Electron announces a download sooner than a launched Chromium does, so the report is often
        still in flight here. Waiting keeps Playwright's dispatcher turning (as download.save_as()
        does) for at least _DOWNLOAD_CLICK_GRACE_S after the download began, which delivers it."""
        started = time.monotonic()
        done = threading.Event()
        errors: list[Exception] = []

        def _save() -> None:
            try:
                host_view.control(
                    "save_download", timeout_s=125.0,
                    runId=self.session_id, url=url, dest=str(dest), timeoutMs=120000,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                done.set()

        threading.Thread(target=_save, daemon=True, name="panel-download").start()
        pump = src_page or self._page
        while not done.is_set() or time.monotonic() - started < _DOWNLOAD_CLICK_GRACE_S:
            try:
                pump.wait_for_timeout(50)
            except Exception:  # noqa: BLE001 — the page closed: just wait
                done.wait(0.05)
        if errors:
            raise errors[0]

    def _on_file_chooser(self, chooser: Any) -> None:
        """Suppresses the native OS file picker and asks the Studio to show its own,
        pre-pointed at the most recent download's folder. Never blocks — the user's pick can
        take minutes; resolve_file_pick() delivers the answer back via the pump loop."""
        try:
            request_id = str(uuid.uuid4())
            self._pending_file_chooser = (request_id, chooser)
            default_dir = self._last_download_dir or self._downloads_dir or (Path.home() / "Downloads")
            self._notify_dir_changed(default_dir)
            if self.on_file_picker_request is not None:
                self.on_file_picker_request(
                    request_id, str(default_dir), bool(getattr(chooser, "is_multiple", False))
                )
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"file_chooser_event_error: {exc!s}")

    def resolve_file_pick(self, request_id: str, paths: list[str] | None) -> None:
        """Called from any thread with the Studio dialog's result. Queued, not applied
        directly: FileChooser.set_files() is a Playwright sync-API call and must run on the
        recorder's own driver thread — see the pump loop that drains this."""
        self._file_pick_results.put((request_id, paths))

    def _on_dialog(self, dialog: Any, src_page: Any | None = None) -> None:
        # Deliberately NOT accepted/dismissed here (outside auth_mode). Merely registering this
        # listener already stops Chromium from rendering the native alert/confirm/prompt box at
        # all (Playwright intercepts Page.javascriptDialogOpening over CDP) — so auto-accepting
        # immediately, as this used to do unconditionally, made every dialog vanish before a
        # human could ever see or answer it, and a prompt() was always accepted with empty text.
        # Instead: stash it and ask the Studio; the pump loop (_drain_js_dialog_sync) resolves it
        # once the human answers.
        if self.auth_mode:
            # The login-capture browser has its own separate pump loop that never drains
            # _pending_dialog (see _run_sync_recorder's `if not self.auth_mode:` gate), so
            # holding a dialog here would hang that page forever with no way to answer it.
            # Out of scope for this fix (see TODO.md) — preserve the old auto-accept behavior.
            try:
                dialog.accept()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            if self._pending_dialog is not None:
                # A second dialog can only fire once the first is resolved (the renderer is
                # blocked until then) — but guard anyway rather than leaking the older one.
                try:
                    self._pending_dialog[1].accept()
                except Exception:  # noqa: BLE001
                    pass
                self.binding_errors.append("dialog_event_overlap: auto-accepted stale pending dialog")
            request_id = str(uuid.uuid4())
            default_value = ""
            try:
                default_value = dialog.default_value or ""
            except Exception:  # noqa: BLE001
                pass
            self._pending_dialog = (
                request_id, dialog, dialog.type, dialog.message, time.monotonic(), src_page,
            )
            if self.on_js_dialog_request is not None:
                self.on_js_dialog_request(request_id, dialog.type, dialog.message, default_value)
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"dialog_event_error: {exc!s}")

    def resolve_js_dialog(self, request_id: str, accepted: bool, text: str | None) -> None:
        """Called from any thread with the Studio dialog's answer. request_id belongs to
        exactly one of two mechanisms: the local dialog server (alert/confirm/prompt, the
        common case — resolving it there simply unblocks the page-side synchronous XHR
        directly, nothing further to do) or, if that doesn't recognize it, the older CDP path
        (beforeunload only) — queued there, not applied directly, since Dialog.accept()/
        dismiss() are sync-API calls that must run on the recorder's own driver thread. See
        _drain_js_dialog_sync in the pump loop."""
        if self._dialog_server is not None and self._dialog_server.resolve(
            request_id, accepted, text or ""
        ):
            return
        self._dialog_results.put((request_id, accepted, text or ""))

    # ─── DialogSyncServer callbacks — run on the server's own per-request thread, NOT the ───
    # recorder's driver thread. Only Studio-notification calls (on_js_dialog_request/
    # _resolved/_cancelled — plain stdio JSON broadcasts, no Playwright involved) are safe to
    # make directly from here. Anything that touches a Playwright page object is queued onto
    # _dialog_server_events instead and drained on the driver thread by
    # _drain_dialog_server_events_sync, exactly like resolve_file_pick/_file_pick_results do
    # for the same reason elsewhere in this file.

    def _on_dialog_server_opened(
        self, request_id: str, dialog_type: str, message: str, default_value: str
    ) -> None:
        if self.on_js_dialog_request is not None:
            try:
                self.on_js_dialog_request(request_id, dialog_type, message, default_value)
            except Exception:  # noqa: BLE001
                pass

    def _on_dialog_server_answered(
        self,
        request_id: str,
        dialog_type: str,
        message: str,
        accepted: bool,
        text: str,
        href: str,
    ) -> None:
        self._dialog_server_events.put((accepted, dialog_type, message, text, href))
        if self.on_js_dialog_resolved is not None:
            try:
                self.on_js_dialog_resolved(request_id)
            except Exception:  # noqa: BLE001
                pass

    def _on_dialog_server_timed_out(
        self, request_id: str, dialog_type: str, message: str, href: str
    ) -> None:
        # Mirrors the old CDP path's timeout fallback exactly: forced accept with empty text,
        # rather than a dismiss, so a workflow depending on "something" being entered doesn't
        # additionally have to handle a forgotten-dialog dismiss as a distinct outcome.
        self.binding_errors.append("dialog_timeout_autoaccept")
        self._dialog_server_events.put((True, dialog_type, message, "", href))
        if self.on_js_dialog_cancelled is not None:
            try:
                self.on_js_dialog_cancelled(request_id)
            except Exception:  # noqa: BLE001
                pass

    def _page_by_href_sync(self, href: str) -> Any | None:
        """Best-effort page lookup by URL — dialog-server events carry only the page's
        location.href at the moment of the call, not a direct Playwright page reference (the
        override runs as in-page JS talking to an HTTP server, not a Playwright event with a
        page already attached). Falls back to the active page if no exact match is found (e.g.
        the page navigated between the call and this lookup); a same-URL multi-tab race is a
        rare, accepted degradation — this file already tolerates comparable edge cases
        elsewhere (see e.g. _ensure_nav_history_session_sync's docstring)."""
        if href:
            for page in self._open_pages_sync():
                try:
                    if page.url == href:
                        return page
                except Exception:  # noqa: BLE001
                    continue
        return self._active_page_sync()

    def _drain_dialog_server_events_sync(self) -> None:
        """Pump-loop only, every tick. Turns queued dialog-server answers/timeouts into
        recorded dialog_accept/dialog_dismiss steps — deferred here (rather than done directly
        in the _on_dialog_server_* callbacks above) because resolving a page by URL and
        building the synthetic payload both touch Playwright objects, unsafe outside the driver
        thread."""
        while True:
            try:
                accepted, dialog_type, message, text, href = self._dialog_server_events.get_nowait()
            except Empty:
                return
            src_page = self._page_by_href_sync(href)
            self._enqueue_synthetic(
                "dialog_accept" if accepted else "dialog_dismiss",
                json.dumps({"type": dialog_type, "message": message, "value": text}),
                src_page=src_page,
            )

    def _bring_dialog_page_to_front_sync(self, src_page: Any | None) -> None:
        """Accepting/dismissing a dialog closes it at the CDP level immediately, but the
        window sat behind the Studio modal (which we pin on top while asking) while it closed,
        and Windows can leave the now-dead dialog's pixels on screen until that window repaints.
        Activating the tab forces Chromium to redraw, clearing the stale image — no minimizing,
        the human lands back in the same browser window they were using.

        Windows' foreground-lock restriction blocks a background process from stealing focus
        unless it holds a one-shot grant from AllowSetForegroundWindow — _run_sync_recorder
        takes that grant once at browser launch so the *first* bring_to_front() (right after
        goto()) succeeds, but that grant is consumed by that single use. Every later call here
        (i.e. every dialog answered during the recording) needs its own fresh grant, or Windows
        silently no-ops the activation with no error anywhere, and Studio is left looking
        permanently pinned on top of the recording browser."""
        if self.embedded:
            return  # the page is a view inside Studio's own window — nothing to raise
        import sys as _sys
        if _sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except Exception:  # noqa: BLE001
                pass
        try:
            page = src_page if src_page is not None else self._active_page_sync()
            if page is not None and not page.is_closed():
                page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

    def _drain_js_dialog_sync(self) -> None:
        """Runs once per pump tick while a dialog is pending. Resolves it from the Studio's
        answer if one has arrived, otherwise auto-accepts past _JS_DIALOG_TIMEOUT_S so a
        forgotten dialog can never wedge the recording — see the pump loop's stall guard,
        which skips every other per-tick Playwright call (they'd hang against the blocked
        renderer) while this is the only thing draining."""
        pending = self._pending_dialog
        if pending is None:
            return
        request_id, dialog, dtype, message, opened_at, src_page = pending
        answer = None
        try:
            answer = self._dialog_results.get_nowait()
        except Empty:
            pass
        if answer is not None:
            ans_request_id, accepted, text = answer
            if ans_request_id != request_id:
                # Was live when the Studio was asked but no longer matches the currently
                # pending dialog (e.g. a second dialog fired and overwrote _pending_dialog
                # before this answer was drained — see the overlap guard in _on_dialog). The
                # human's answer is lost; previously this returned with no trace at all, which
                # made a stuck recording undiagnosable. The Studio never hears back for this
                # request either way, so it's left showing a dialog that can no longer be
                # resolved — the real dialog stays open until the human answers it natively or
                # the timeout below auto-accepts it.
                self.binding_errors.append(
                    f"dialog_answer_dropped: expected {request_id}, got {ans_request_id}"
                )
                return
            self._pending_dialog = None
            resolved = False
            try:
                if accepted:
                    dialog.accept(text)
                else:
                    dialog.dismiss()
                resolved = True
            except Exception as exc:  # noqa: BLE001
                self.binding_errors.append(f"dialog_resolve_error: {exc!s}")
            self._bring_dialog_page_to_front_sync(src_page)
            self._enqueue_synthetic(
                "dialog_accept" if accepted else "dialog_dismiss",
                json.dumps({"type": dtype, "message": message, "value": text}),
                src_page=src_page,
            )
            if resolved and self.on_js_dialog_resolved is not None:
                try:
                    self.on_js_dialog_resolved(request_id)
                except Exception:  # noqa: BLE001
                    pass
            elif not resolved and self.on_js_dialog_cancelled is not None:
                # accept()/dismiss() actually failed (dialog_resolve_error above) — the Studio
                # otherwise has no way to know its answer didn't take, and would stay pinned on
                # top forever waiting for a "resolved" event that will never come.
                try:
                    self.on_js_dialog_cancelled(request_id)
                except Exception:  # noqa: BLE001
                    pass
            return
        if time.monotonic() - opened_at > _JS_DIALOG_TIMEOUT_S:
            self._pending_dialog = None
            try:
                dialog.accept("")
            except Exception as exc:  # noqa: BLE001
                self.binding_errors.append(f"dialog_timeout_error: {exc!s}")
            self._bring_dialog_page_to_front_sync(src_page)
            self.binding_errors.append("dialog_timeout_autoaccept")
            self._enqueue_synthetic(
                "dialog_accept",
                json.dumps({"type": dtype, "message": message, "value": ""}),
                src_page=src_page,
            )
            if self.on_js_dialog_cancelled is not None:
                try:
                    self.on_js_dialog_cancelled(request_id)
                except Exception:  # noqa: BLE001
                    pass

    def _on_popup(self, popup: Any, src_page: Any | None = None) -> None:
        try:
            url = ""
            try:
                url = popup.url
                self._remember_current_url(url)
            except Exception:  # noqa: BLE001
                pass
            # Best-effort dict lookup only — no Playwright call here (same reentrancy hazard
            # documented at _binding_sink_sync). The popup page is almost always still
            # unregistered at this instant, so this is usually None; id(popup) below is what
            # actually resolves it, one pump tick later.
            tab_id = self._tab_id_for_page(popup)
            # src_page is the page whose "popup" listener fired — the tab that actually clicked
            # the link, not whatever tab happens to be _active_page_sync() at this instant. Without
            # this, a popup opened from a background tab gets stamped with the wrong tab (usually
            # tab_0), and the compiler inserts a spurious tab_switch back to it before the real
            # tab_open for the new tab — see _insert_tab_markers.
            #
            # tab_key stamps the event with the POPUP's own tab instead of the opener's. That is
            # what makes the new tab exist in the event stream: _insert_tab_markers derives
            # tab_open/tab_switch purely from a tab-id transition between consecutive events, so
            # a popup stamped with its opener produces no transition, no markers, and a compiled
            # skill that never mentions the second tab — the replay then drives the original tab
            # from the background for the rest of the run. src_page is untouched and still owns
            # visuals and page.url.
            self._enqueue_synthetic(
                "popup",
                json.dumps({"url": url, "tab_id": tab_id}),
                src_page=src_page,
                tab_key=id(popup),
            )
        except Exception as exc:  # noqa: BLE001
            self.binding_errors.append(f"popup_event_error: {exc!s}")

    def _on_page_navigated(self, page: Any, frame: Any) -> None:
        try:
            parent = getattr(frame, "parent_frame", None)
            parent = parent() if callable(parent) else parent
            if parent is not None:
                return
        except Exception:  # noqa: BLE001
            return
        self._page = page
        self._nav_committed_at[id(page)] = time.monotonic()
        self._remember_page_url_sync(page)
        # Main-frame navigation happened — defer the Back/Forward history comparison to the
        # pump loop (CDP round-trips are unsafe inside Playwright event callbacks).
        self._queue_nav_history_check(page)

    def _attach_page_listeners(self, page: Any) -> None:
        # Bind `page` — the tab this listener is attached to — into each handler, so the event
        # is stamped with the tab that actually fired it rather than whatever tab happens to be
        # _active_page_sync() when the pump loop later drains it (see _make_synthetic_payload).
        page.on("download", lambda download: self._on_download(download, page))
        page.on("dialog", lambda dialog: self._on_dialog(dialog, page))
        page.on("popup", lambda popup: self._on_popup(popup, page))
        # Intercepts the native OS file picker (see _on_file_chooser) so the Studio can show
        # its own dialog pre-pointed at the download folder instead. set_files() on the
        # resulting FileChooser still dispatches the input's change event via CDP, so
        # bridge.js's upload_intent capture is unaffected.
        page.on("filechooser", lambda chooser: self._on_file_chooser(chooser))
        page.on("framenavigated", lambda frame: self._on_page_navigated(page, frame))
        if not self.auth_mode:
            page.on("frameattached", self._on_frame_attached)
            page.on("framenavigated", self._on_frame_navigated)
            page.on("framedetached", self._on_frame_detached)

    def _on_context_page(self, page: Any) -> None:
        self._page = page
        self._attach_page_listeners(page)

    def _run_sync_recorder(self) -> None:
        try:
            import sys as _sys
            playwright_cm = sync_playwright()
            self._playwright = playwright_cm.start()
            # expose_binding's internal delivery of a __skillReport result back to the page
            # (asyncio.create_task(binding_call.call(...)) inside playwright's own dispatcher,
            # never awaited by our code) races the user closing the tab/browser mid-recording.
            # Losing that race raises TargetClosedError on an orphaned task, which asyncio logs
            # as "Task exception was never retrieved" — cosmetic, since our handler already ran;
            # only the result delivery back to the already-gone page failed. Silence just that.
            playwright_cm._loop.set_exception_handler(_quiet_target_closed_exception_handler)
            # Isolated per-session workspace, mirroring the runtime's runs/{runId}/ — downloads
            # taken during recording land here instead of nowhere durable.
            if not self.auth_mode:
                self._downloads_dir = self.data_root / "sessions" / self.session_id / "downloads"
                self._downloads_dir.mkdir(parents=True, exist_ok=True)
            if self.embedded:
                # Build Studio's own browser panel instead of a Chromium window (host_view.py).
                self._start_embedded_sync()
            else:
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-session-crashed-bubble",
                    "--disable-infobars",
                    "--no-first-run",
                ]
                has_storage_state = bool(self.storage_state_path and Path(self.storage_state_path).is_file())
                ctx_kwargs: dict[str, Any] = {}
                if has_storage_state:
                    ctx_kwargs["storage_state"] = self.storage_state_path
                if self.auth_mode and self.window_rect:
                    r = self.window_rect
                    launch_args += [
                        f"--window-position={int(r['x'])},{int(r['y'])}",
                        f"--window-size={int(r['width'])},{int(r['height'])}",
                    ]
                    # Let the page fill the window instead of Playwright's fixed 1280x720 emulation.
                    ctx_kwargs["no_viewport"] = True
                if not self.auth_mode:
                    # Video recording is mandatory for non-auth sessions. Confirmed not the (sole)
                    # cause of recording-window flicker — disabling it during investigation did not
                    # eliminate the flicker, which came from the periodic storage_state autosave
                    # instead (see the pump-loop comment further down). Safe to keep enabled.
                    session_dir = self.data_root / "sessions" / self.session_id
                    session_dir.mkdir(parents=True, exist_ok=True)
                    ctx_kwargs["record_video_dir"] = str(session_dir)
                    ctx_kwargs["record_video_size"] = {"width": 1280, "height": 720}
                self._browser = self._playwright.chromium.launch(headless=False, args=launch_args)
                self._context = self._browser.new_context(**ctx_kwargs)
                self._browser.on("disconnected", lambda _: self._on_browser_disconnected())
                self.browser_open = True
                # Allow Chromium to steal the foreground on Windows despite focus-lock
                if _sys.platform == "win32":
                    try:
                        import ctypes
                        ctypes.windll.user32.AllowSetForegroundWindow(-1)
                    except Exception:
                        pass
                if not self.auth_mode:
                    import time as _time
                    self._video_session_start = _time.monotonic()
                    self._video_session_start_wall_ms = int(_time.time() * 1000)
                    self._bridge_script = _load_bridge_script(self.capture_hover)
                    self._context.expose_binding("__skillReport", self._binding_sink_sync)
                    self._context.add_init_script(self._bridge_script)
                    dialog_server = DialogSyncServer(
                        on_opened=self._on_dialog_server_opened,
                        on_answered=self._on_dialog_server_answered,
                        on_timed_out=self._on_dialog_server_timed_out,
                    )
                    self._dialog_server = dialog_server
                    self._context.add_init_script(
                        DIALOG_OVERRIDE_SCRIPT_TEMPLATE.replace("{PORT}", str(dialog_server.port))
                    )
                    self._context.on("page", self._on_context_page)
                    self._page = self._context.new_page()
                else:
                    self._page = self._context.new_page()
            try:
                self._page.goto(self.start_url, wait_until="load", timeout=30000)
                self._remember_page_url_sync(self._page)
            except Exception as goto_err:
                self.binding_errors.append(f"navigation_error: {goto_err!s}")
            try:
                if not self.embedded:
                    self._page.bring_to_front()
            except Exception:
                pass
            if not self.auth_mode:
                page = self._active_page_sync()
                if page is not None:
                    self._ensure_bridge_installed_sync(page)
                    bridge_ok = page.evaluate("() => !!window.__SKILL_BRIDGE_V1__")
                    binding_ok = page.evaluate("() => typeof window.__skillReport === 'function'")
                    if not bridge_ok:
                        self.binding_errors.append("bridge_not_loaded_on_start_page")
                    if not binding_ok:
                        self.binding_errors.append("binding_not_available_on_start_page")
                    # Recomputed rather than reusing the `session_dir` local from the context-setup
                    # block above (same value; avoids depending on both `if not self.auth_mode:`
                    # blocks staying in lockstep across a method this long).
                    self._write_environment_sync(self.data_root / "sessions" / self.session_id, page)
                else:
                    self.binding_errors.append("start_page_closed")
            self._startup_done.set()

            while not self._stop_requested.is_set():
                # A held JS dialog blocks its page's renderer thread. Every other per-tick
                # Playwright call below (page.evaluate, bridge install, url reads, ...) would
                # hang against that blocked page and wedge this whole pump loop — which is the
                # only thing that can ever resolve the dialog. So while one is pending, do
                # nothing else this tick except try to resolve it.
                if self._pending_dialog is not None:
                    self._drain_js_dialog_sync()
                    time.sleep(0.2)
                    continue
                # Assign tab ids to any page not yet registered (new tab, popup, Ctrl+T) —
                # must happen before anything below reads self._tab_ids/_tab_meta.
                self._register_new_pages_sync()
                if not self.auth_mode:
                    # Independent of the CDP _pending_dialog machinery above — the dialog
                    # server (alert/confirm/prompt) resolves itself on its own thread the
                    # instant a human answers, unrelated to this loop's cadence; this just
                    # turns whatever landed in the queue since the last tick into recorded
                    # steps. See _drain_dialog_server_events_sync's docstring for why this
                    # can't be done directly from the dialog-server thread instead.
                    self._drain_dialog_server_events_sync()
                # Pump the Playwright sync driver so binding callbacks are delivered
                # continuously while recording (not only around teardown calls).
                # Skip in auth_mode — no bridge callbacks to pump.
                if not self.auth_mode:
                    for page in self._open_pages_sync():
                        if self._pending_dialog is not None and self._pending_dialog[5] is page:
                            # A dialog opened on exactly this page since the top-of-loop check
                            # a few lines up — skip every renderer-touching call against it this
                            # tick (bridge install, pump ping) so we don't reproduce the
                            # reentrancy deadlock that guard exists to prevent. See
                            # _pump_ping_sync's docstring for why the guard above alone isn't
                            # enough: this closes the same race for the page it just landed on.
                            continue
                        try:
                            self._log_bridge_attempt("<pump>", "tick_frame_check_begin")
                            self._ensure_bridge_installed_sync(page)
                            self._log_bridge_attempt("<pump>", "tick_page_eval_begin")
                            self._pump_ping_sync(page)
                            self._log_bridge_attempt("<pump>", "tick_page_eval_done")
                        except Exception as exc:  # noqa: BLE001
                            self.binding_errors.append(f"pump_error: {exc!s}")
                    # Take a lightweight frame topology snapshot every 5th tick (~1 s)
                    # so recorder_diag.json captures which iframes exist during recording,
                    # not just at session end when transient iframes may be gone.
                    self._pump_tick += 1
                    if self._pump_tick % 5 == 0 and len(self._frame_snapshots) < 300:
                        try:
                            page = self._active_page_sync()
                            if page is not None and not page.is_closed():
                                self._frame_snapshots.append({
                                    "ts": int(time.time() * 1000),
                                    "frames": [
                                        {
                                            "url": _frame_url(fr),
                                            "parent_url": _frame_url(_frame_parent(fr)) if _frame_parent(fr) else None,
                                        }
                                        for fr in list(getattr(page, "frames", []) or [])
                                    ],
                                })
                        except Exception:  # noqa: BLE001
                            pass
                # No periodic autosave here on purpose. context.storage_state() is a heavy,
                # all-at-once CDP call (every cookie + every page's localStorage), and calling
                # it repeatedly on a timer — even throttled to every 6s — was a confirmed source
                # of visible recording-window flicker (originally diagnosed in auth_mode, later
                # found to affect ordinary workflow recording too once autosave got wired on for
                # it). Authentication is pre-flight-only now: recording only needs to capture the
                # session state once, which happens at the two real signals that matter — reaching
                # the wait_for_url target (below) and session teardown (force=True at the end of
                # this method) — never on a blind timer.
                # Throttled to ~1s (like the connectivity check below) instead of every 0.2s
                # tick — reading page.url for every open page every tick is more CDP traffic
                # than this cheap bookkeeping needs, and it stacks with the other per-tick
                # checks to contribute to visible recording-window flicker.
                now_for_url_sweep = time.monotonic()
                if now_for_url_sweep - self._last_url_sweep_at >= 1.0:
                    self._last_url_sweep_at = now_for_url_sweep
                    self._log_bridge_attempt("<pump>", "tick_page_url_sweep_begin")
                    for page in self._open_pages_sync():
                        self._remember_page_url_sync(page)
                    self._log_bridge_attempt("<pump>", "tick_page_url_sweep_done")
                if self.wait_for_url and not self.reached_wait_url:
                    try:
                        for page in self._open_pages_sync():
                            current_url = page.url
                            self._remember_current_url(current_url)
                            if self._url_matches_wait_target(current_url):
                                self._autosave_storage_state_sync(force=True)
                                # reached_wait_url is a passive hint only, never an auto-stop
                                # trigger — only cmd_finish_group_app_auth's confirmation ends an
                                # auth-mode session (asked for by Done, or by the conversation's
                                # watcher, which treats this as a cue to check), so a
                                # transient page that merely LOOKS like success_url (a redirect
                                # hop, an MFA interstitial that briefly matches) can't close the
                                # window out from under a login the human hasn't finished yet.
                                self.reached_wait_url = True
                                break
                    except Exception:  # noqa: BLE001
                        pass
                # auth_mode only: breadcrumb the hosts the login tab's own page visits, in
                # order — see journey_hosts's field comment. Cheap: reuses the page/url already
                # read for the auth-mode autosave check just below, no extra Playwright call.
                if self.auth_mode:
                    try:
                        from urllib.parse import urlparse as _urlparse

                        pages = self._open_pages_sync()
                        if pages:
                            url_now = pages[0].url
                            if url_now and not is_blank_url(url_now) and url_now != self._journey_last_url:
                                self._journey_last_url = url_now
                                host = _urlparse(url_now).hostname or ""
                                if host and (not self.journey_hosts or self.journey_hosts[-1] != host):
                                    self.journey_hosts.append(host)
                    except Exception:  # noqa: BLE001
                        pass
                # auth_mode only: save the session the moment the browser navigates away
                # from the login page, even when no success_url is configured (or it never
                # matches) — teardown's force=True save at the end of this method is a
                # no-op once browser_open is already False (see _autosave_storage_state_sync),
                # which is exactly the state when the user closes Chromium after signing in.
                # Capturing here, while the browser is still open, is what makes "sign in,
                # then close the window" a reliable success path instead of a silent loss.
                # Guarded to at most one save per distinct URL so a page that keeps
                # navigating post-login doesn't reintroduce timer-like autosave flicker.
                if self.auth_mode and not self.reached_wait_url:
                    try:
                        start_base = self.start_url.split("?")[0].split("#")[0]
                        for page in self._open_pages_sync():
                            current_url = page.url
                            if (
                                current_url
                                and not is_blank_url(current_url)
                                and not current_url.startswith(start_base)
                                and current_url != self._auth_saved_url
                            ):
                                self._auth_saved_url = current_url
                                self._autosave_storage_state_sync(force=True)
                            break
                    except Exception:  # noqa: BLE001
                        pass
                # learn_auth()'s cross-thread request, if one is pending — see that method's
                # docstring for why this can only run here, on the recorder's own thread.
                if self._learn_auth_pending is not None and not self._learn_auth_done.is_set():
                    login_url = self._learn_auth_pending.get("login_url", "")
                    existing = self._learn_auth_pending.get("existing")
                    try:
                        self._learn_auth_result = self._perform_learn_auth_sync(login_url, existing)
                    except Exception as exc:  # noqa: BLE001
                        self._learn_auth_result = (None, f"Couldn't learn sign-in signals: {exc}")
                    finally:
                        self._learn_auth_done.set()
                if not self.auth_mode:
                    try:
                        payload, src_page, src_frame = self._pending_payloads.get_nowait()
                        self._consume_payload_safe_sync(payload, src_page, src_frame)
                    except Empty:
                        pass
                    # Event-driven: only pages that just navigated get checked (one CDP
                    # round-trip each), so this adds no per-tick traffic.
                    try:
                        self._drain_nav_history_checks_sync()
                    except Exception as exc:  # noqa: BLE001
                        self.binding_errors.append(f"nav_history_drain_error: {exc!s}")
                    try:
                        request_id, paths = self._file_pick_results.get_nowait()
                        pending = self._pending_file_chooser
                        if pending is not None and pending[0] == request_id:
                            self._pending_file_chooser = None
                            if paths:
                                try:
                                    from conxa_compile.compiler.upload_binding import classify_file_pick

                                    self._pending_file_pick = classify_file_pick(
                                        paths,
                                        self._downloads_dir,
                                        self._zip_downloads,
                                    )
                                    pending[1].set_files(paths)
                                except Exception as exc:  # noqa: BLE001
                                    self.binding_errors.append(f"file_chooser_set_files_error: {exc!s}")
                    except Empty:
                        pass
                # Throttled to ~1s instead of every 0.2s tick — context.pages is itself a CDP
                # round-trip, and running it 5x/second was a source of visible recording-window
                # flicker (see _NO_PAGES_GRACE_TICKS comment above). browser_open is a plain flag
                # (set False by the browser/context "disconnected"/"close" event) so it costs
                # nothing to check every tick, unlike a live is_connected() round-trip.
                # The gate timestamp is updated unconditionally whenever it opens (not only on
                # the bad-path branch below) so the throttle keeps engaging once the browser is
                # healthy — updating it only on the bad path would leave it at 0 forever in the
                # common case and defeat the throttle entirely. When the gate is closed this
                # tick, skip the check outright and leave _no_pages_streak untouched.
                now_for_connectivity_check = time.monotonic()
                if now_for_connectivity_check - self._last_connectivity_check_at >= 1.0:
                    self._last_connectivity_check_at = now_for_connectivity_check
                    if not self.browser_open or not self._open_pages_sync():
                        # A heavy same-page iframe load (e.g. HubSpot's "Create new" object
                        # panel) can make both of these read "gone" for several real seconds
                        # while the browser is still open and busy — the page list has been
                        # observed to flicker under CDP/network pressure. Debounce under one
                        # streak so neither ends the recording on a single bad check; a real
                        # close/crash still confirms well within the grace window.
                        self._no_pages_streak += 1
                        # Log the streak's shape (not just its eventual give-up) — on the first
                        # check and then every ~10th check while it continues — so
                        # recorder_diag.json shows how long a "pages look gone" stretch actually
                        # lasted even when it resolves on its own well under the give-up
                        # threshold, instead of being invisible.
                        if self._no_pages_streak == 1 or self._no_pages_streak % 10 == 0:
                            connected = self.browser_open
                            if len(self._frame_lifecycle) < 2000:
                                self._frame_lifecycle.append({
                                    "kind": "no_pages_tick",
                                    "url": self.current_url,
                                    "parent_url": None,
                                    "ts": int(time.time() * 1000),
                                    "streak": self._no_pages_streak,
                                    "browser_connected": connected,
                                })
                        if self._no_pages_streak >= _NO_PAGES_GRACE_TICKS:
                            still_connected = self.browser_open
                            self.binding_errors.append(
                                f"recorder_gave_up: streak={self._no_pages_streak} "
                                f"browser_connected={still_connected} current_url={self.current_url!r}"
                            )
                            self.ended_by_user = True
                            break
                    else:
                        if self._no_pages_streak > 0 and len(self._frame_lifecycle) < 2000:
                            self._frame_lifecycle.append({
                                "kind": "no_pages_resolved",
                                "url": self.current_url,
                                "parent_url": None,
                                "ts": int(time.time() * 1000),
                                "streak": self._no_pages_streak,
                            })
                        self._no_pages_streak = 0
                self._idle_sync(0.2)

            if self._pending_dialog is not None:
                # Recording was stopped (browser closed, Save clicked, etc.) while a dialog sat
                # unanswered. The shutdown drain below calls page.evaluate on every open page
                # unconditionally, which would hang forever against this page's blocked
                # renderer — force it closed now rather than waiting out _JS_DIALOG_TIMEOUT_S.
                _, dialog, dtype, message, _opened_at, src_page = self._pending_dialog
                self._pending_dialog = None
                try:
                    dialog.accept("")
                except Exception:  # noqa: BLE001
                    pass
                self._enqueue_synthetic(
                    "dialog_accept",
                    json.dumps({"type": dtype, "message": message, "value": ""}),
                    src_page=src_page,
                )

            if not self.auth_mode:
                # Stop waits for a short "idle queue" condition so delayed
                # Playwright binding callbacks can still be consumed.
                shutdown_start = time.monotonic()
                while True:
                    for page in self._open_pages_sync():
                        try:
                            self._ensure_bridge_installed_sync(page)
                            self._pump_ping_sync(page)
                        except Exception:
                            pass
                    self._drain_dialog_server_events_sync()
                    drained = 0
                    try:
                        while True:
                            payload, src_page, src_frame = self._pending_payloads.get_nowait()
                            drained += 1
                            self._consume_payload_safe_sync(payload, src_page, src_frame)
                    except Empty:
                        pass
                    elapsed = time.monotonic() - shutdown_start
                    idle_for = time.monotonic() - self._last_enqueue_at if self._last_enqueue_at else elapsed
                    if elapsed >= 5.0 and idle_for >= 1.0 and drained == 0:
                        break
                    time.sleep(0.05)
                # Final diagnostics snapshot after all events are drained.
                session_dir = self.data_root / "sessions" / self.session_id
                session_dir.mkdir(parents=True, exist_ok=True)
                self._write_diagnostics_sync(session_dir)
            self._autosave_storage_state_sync(force=True)
        except Exception as exc:  # noqa: BLE001
            self._startup_error = format_startup_error(exc)
            self.binding_errors.append(f"start_error: {exc!s}")
            self._startup_done.set()
        finally:
            self.browser_open = False
            self._shutdown_playwright_sync()

    async def stop(self) -> None:
        self._stop_requested.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._thread and not self._thread.is_alive():
            self._thread = None
        self.browser_open = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            event_count = len(self._materialized)
        return {
            "session_id": self.session_id,
            "browser_open": self.browser_open,
            "event_count": event_count,
            "paused": self._paused,
            "ended_by_user": self.ended_by_user,
            "binding_errors": self.binding_errors,
            "reached_wait_url": self.reached_wait_url,
            "auth_captured": self.auth_captured,
            "current_url": self.current_url,
        }

    def pause(self) -> None:
        """Stop recording what the person does in the browser. Nothing is captured while paused,
        and nothing captured before is touched. The video keeps running."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def snapshot_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.model_dump(mode="json") for e in self._materialized]


class SessionRegistry:
    """In-memory MVP registry (swap for Redis/DB in production)."""

    def __init__(self) -> None:
        self._sessions: dict[str, RecordingSession] = {}

    def create(
        self,
        start_url: str = "about:blank",
        storage_state_path: str = "",
        storage_state_autosave_path: str = "",
        wait_for_url: str = "",
        auth_mode: bool = False,
        capture_hover: bool = False,
        window_rect: dict | None = None,
        label: str = "",
        in_panel: bool = True,
    ) -> RecordingSession:
        sid = str(uuid.uuid4())
        sess = RecordingSession(
            session_id=sid,
            start_url=start_url,
            storage_state_path=storage_state_path,
            storage_state_autosave_path=storage_state_autosave_path,
            wait_for_url=wait_for_url,
            auth_mode=auth_mode,
            capture_hover=capture_hover,
            window_rect=window_rect,
            label=label,
            embedded=bool(in_panel and settings.record_in_panel and host_view.available()),
        )
        self._sessions[sid] = sess
        return sess

    def get(self, session_id: str) -> RecordingSession | None:
        return self._sessions.get(session_id)

    def all(self) -> list[RecordingSession]:
        return list(self._sessions.values())

    def pop(self, session_id: str) -> RecordingSession | None:
        """Remove a session without stopping the browser (used on failed start)."""
        return self._sessions.pop(session_id, None)

    async def remove(self, session_id: str) -> bool:
        sess = self._sessions.get(session_id)
        if not sess:
            return False
        await sess.stop()
        return True


registry = SessionRegistry()
