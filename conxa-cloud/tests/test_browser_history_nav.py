"""Browser Back/Forward capture end-to-end shape:

record (session.py CDP navigation-history tracking -> browser_back/browser_forward events)
-> model (ActionKind) -> compile (_build_step real executable steps with tab context)
-> saved-skill export (passthrough to execution.json). Replay-side dispatch pins live in
runtime/test/unit/test_browser_history_nav.js.

The motivating scenario (see FIX.md): Tab A -> URL A, open Tab B -> URL B, switch back to
Tab A, press the browser Back button, act on the resulting page. The Back MUST be recorded
as an explicit step and replayed as page.goBack() on Tab A — never rewritten to a guessed
URL navigation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from conxa_compile.compiler.build import _build_step, _insert_tab_markers
from conxa_compile.compiler.step_anchors import clean_steps
from conxa_compile.editor.action_registry import action_spec, is_supported_action
from conxa_compile.recorder.session import RecordingSession
from conxa_compile.skill_package_builder_saved_skill import _saved_step_to_execution_step
from conxa_core.models.events import ActionMeta


# ---------------------------------------------------------------- model

def test_action_meta_accepts_history_navigation_kinds() -> None:
    assert ActionMeta(action="browser_back", timestamp="2026-08-25T00:00:00Z").action == "browser_back"
    assert ActionMeta(action="browser_forward", timestamp="2026-08-25T00:00:00Z").action == "browser_forward"


def test_action_meta_accepts_manual_navigate() -> None:
    """ActionKind is a strict Literal and RecordedEvent is validated twice on the way to
    events.jsonl (session.py::_finalize_payload_sync and pipeline/run.py::run_pipeline). A kind
    missing from the Literal is silently swallowed into binding_errors and the step never
    reaches the editor at all — which is exactly how address-bar navigations went missing."""
    assert ActionMeta(action="manual_navigate", timestamp="2026-08-26T00:00:00Z").action == "manual_navigate"


def test_registry_knows_history_navigation_kinds() -> None:
    assert is_supported_action("browser_back")
    assert is_supported_action("browser_forward")
    back = action_spec("browser_back")
    assert back.label == "Browser back"
    assert back.category == "flow"
    # Real executable steps — NOT markers, NOT hand-insertable, no selectors/value.
    assert not back.marker
    assert not back.insertable
    assert not back.selectors
    assert not back.value


# ---------------------------------------------------------------- compile

def _history_event(kind: str, tab_id: str, from_url: str, to_url: str) -> dict:
    return {
        "action": {
            "action": kind,
            "timestamp": "2026-08-25T00:00:00Z",
            "value": json.dumps({"from_url": from_url, "to_url": to_url}),
        },
        "tab": {"id": tab_id, "index": 0},
        "page": {"url": to_url, "title": ""},
    }


def test_build_step_compiles_browser_back_to_real_executable_step(tmp_path: Path) -> None:
    from conxa_compile.policy.bundle import get_policy_bundle

    ev = _history_event("browser_back", "tab_0", "https://a.test/list", "https://a.test/detail")
    step = _build_step(ev, get_policy_bundle(), session_root=tmp_path, step_index=3)
    assert step.action == "browser_back"
    assert step.intent == "history_back"
    # The recorded post-navigation URL feeds validation only — replay goes back in history,
    # it never navigates to this URL.
    assert step.url == "https://a.test/detail"
    assert step.validation.wait_for["type"] == "url_change"
    assert step.validation.wait_for["target"] == "https://a.test/detail"
    # Navigation marker-like step: zero-recovery, nothing to re-locate.
    assert step.recovery.strategies == []
    assert step.tab.get("id") == "tab_0"


def test_build_step_compiles_browser_forward_with_empty_value_gracefully(tmp_path: Path) -> None:
    from conxa_compile.policy.bundle import get_policy_bundle

    ev = {
        "action": {"action": "browser_forward", "timestamp": "", "value": ""},
        "tab": {"id": "tab_1", "index": 1},
        "page": {"url": "", "title": ""},
    }
    step = _build_step(ev, get_policy_bundle(), session_root=tmp_path, step_index=0)
    assert step.action == "browser_forward"
    assert step.intent == "history_forward"
    assert step.url == ""
    assert step.recovery.strategies == []


def test_multi_tab_back_scenario_produces_tab_switch_then_browser_back() -> None:
    """Tab A act -> Tab B act -> switch back to Tab A -> Back -> act. The recorded event
    stream must compile to ... tab_switch(tab_0) -> browser_back(tab_0) -> click(tab_0),
    so replay performs the history navigation on the correct tab."""
    events = [
        {"action": {"action": "click"}, "tab": {"id": "tab_0", "index": 0}, "page": {"url": "https://a.test/"}},
        {"action": {"action": "click"}, "tab": {"id": "tab_1", "index": 1}, "page": {"url": "https://b.test/"}},
        _history_event("browser_back", "tab_0", "https://a.test/detail", "https://a.test/list"),
        {"action": {"action": "click"}, "tab": {"id": "tab_0", "index": 0}, "page": {"url": "https://a.test/list"}},
    ]
    out = _insert_tab_markers(events)
    kinds = [(e["action"]["action"], (e.get("tab") or {}).get("id")) for e in out]
    assert kinds == [
        ("click", "tab_0"),
        ("tab_open", "tab_1"),
        ("click", "tab_1"),
        ("tab_switch", "tab_0"),
        ("browser_back", "tab_0"),
        ("click", "tab_0"),
    ]


def test_clean_steps_never_collapses_consecutive_history_navigations() -> None:
    """Two real consecutive Backs are two replayed steps — they carry no element key, so the
    generic same-action-same-key dedupe must not merge them."""
    back_a = _history_event("browser_back", "tab_0", "https://a.test/c", "https://a.test/b")
    back_b = _history_event("browser_back", "tab_0", "https://a.test/b", "https://a.test/a")
    out = clean_steps([dict(back_a), dict(back_b)])
    assert [e["action"]["action"] for e in out] == ["browser_back", "browser_back"]
    values = [json.loads(e["action"]["value"])["to_url"] for e in out]
    assert values == ["https://a.test/b", "https://a.test/a"]


def test_clean_steps_never_collapses_consecutive_manual_navigations() -> None:
    """The motivating report: one tab, address bar edited A -> B, then B -> C. Both are
    keyless (empty element target), so the generic same-action-same-key dedupe collapsed them
    into a single navigate and the C visit vanished from the editor."""
    nav_b = _history_event("manual_navigate", "tab_0", "https://a.test/", "https://b.test/")
    nav_c = _history_event("manual_navigate", "tab_0", "https://b.test/", "https://c.test/")
    out = clean_steps([dict(nav_b), dict(nav_c)])
    assert [json.loads(e["action"]["value"])["to_url"] for e in out] == [
        "https://b.test/",
        "https://c.test/",
    ]


# ---------------------------------------------------------------- saved-skill export

def test_saved_skill_export_keeps_history_navigation_steps() -> None:
    step = {
        "action": "browser_back",
        "intent": "history_back",
        "url": "https://a.test/list",
        "tab": {"id": "tab_0", "index": 0, "opened_by": "initial"},
    }
    out = _saved_step_to_execution_step(step)
    assert out is not None
    assert out["type"] == "browser_back"
    assert out["url"] == "https://a.test/list"
    assert out["tab"]["id"] == "tab_0"

    fwd = dict(step, action="browser_forward")
    assert _saved_step_to_execution_step(fwd)["type"] == "browser_forward"


# ---------------------------------------------------------------- recorder classification

class _FakeCdpSession:
    def __init__(self, snapshots: list[dict]) -> None:
        self._snapshots = snapshots
        self._calls = 0

    def send(self, method: str):
        assert method == "Page.getNavigationHistory"
        snap = self._snapshots[min(self._calls, len(self._snapshots) - 1)]
        self._calls += 1
        return snap


def _fake_page(url: str = "https://a.test/", closed: bool = False) -> SimpleNamespace:
    return SimpleNamespace(is_closed=lambda: closed, url=url)


def _rs_with_state(page: object, state: dict | None) -> RecordingSession:
    rs = RecordingSession(session_id="test-nav")
    key = id(page)
    rs._nav_cdp_sessions[key] = _FakeCdpSession([])
    if state is not None:
        rs._nav_history_state[key] = state
    rs._nav_check_pages.append(page)
    return rs


def _queued(rs: RecordingSession) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    while True:
        try:
            payload, src_page, _ = rs._pending_payloads.get_nowait()
        except Exception:
            break
        out.append((payload["action"]["action"], json.loads(payload["action"]["value"])["to_url"]))
    return out


def test_recorder_classifies_index_decrease_as_browser_back() -> None:
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 1, "entries": [{"url": "https://a.test/a"}, {"url": "https://a.test/b"}]},
    ])
    rs._nav_history_state[id(page)] = {
        "index": 2,
        "entries": ["https://a.test/a", "https://a.test/b", "https://a.test/c"],
    }
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    queued = _queued(rs)
    assert queued == [("browser_back", "https://a.test/b")]


def test_recorder_classifies_pre_existing_entry_as_browser_forward() -> None:
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 2, "entries": [{"url": "https://a.test/a"}, {"url": "https://a.test/b"}, {"url": "https://a.test/c"}]},
    ])
    rs._nav_history_state[id(page)] = {
        "index": 1,
        "entries": ["https://a.test/a", "https://a.test/b", "https://a.test/c"],
    }
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    assert _queued(rs) == [("browser_forward", "https://a.test/c")]


def test_recorder_fresh_link_nav_truncating_forward_entries_is_not_forward() -> None:
    """User went back (so forward entries existed), then clicks a NEW link: Chromium truncates
    the forward entries and lands on a brand-new URL at an occupied-looking slot. The stale
    entry's URL no longer matches -> normal navigation, NO browser_forward event. It IS a
    renderer-initiated nav (a click), so it's also not a manual_navigate."""
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 2, "entries": [{"url": "https://a.test/a"}, {"url": "https://a.test/b"}, {"url": "https://a.test/new"}]},
    ])
    rs._nav_history_state[id(page)] = {
        "index": 1,
        "entries": ["https://a.test/a", "https://a.test/b", "https://a.test/c"],
    }
    rs._nav_check_pages.append(page)
    rs._nav_pending_renderer_initiated[id(page)] = time.monotonic()  # simulates the click's CDP signal
    rs._drain_nav_history_checks_sync()
    assert _queued(rs) == []


def test_recorder_unattributed_nav_emits_manual_navigate() -> None:
    """No CDP frameRequestedNavigation signal preceded this navigation (the user retyped the
    address bar) -> not renderer-initiated -> manual_navigate, so replay can still reach it."""
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 1, "entries": [{"url": "https://a.test/a"}, {"url": "https://b.test/"}]},
    ])
    rs._nav_history_state[id(page)] = {
        "index": 0,
        "entries": ["https://a.test/a"],
    }
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    assert _queued(rs) == [("manual_navigate", "https://b.test/")]


def test_recorder_stale_renderer_flag_does_not_suppress_later_manual_navigate() -> None:
    """The motivating report: a submit-style click fires Page.frameRequestedNavigation but
    never actually navigates (e.g. its handler calls preventDefault) — several unrelated
    events happen on the same URL — then the user genuinely retypes the address bar. The
    long-stale flag from the abandoned request must not suppress this real manual_navigate."""
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 1, "entries": [{"url": "https://a.test/a"}, {"url": "https://b.test/"}]},
    ])
    rs._nav_history_state[id(page)] = {
        "index": 0,
        "entries": ["https://a.test/a"],
    }
    rs._nav_check_pages.append(page)
    # Set well beyond the TTL, simulating a request that fired long before this check.
    rs._nav_pending_renderer_initiated[id(page)] = (
        time.monotonic() - rs._NAV_RENDERER_INITIATED_TTL_S - 1.0
    )
    rs._drain_nav_history_checks_sync()
    assert _queued(rs) == [("manual_navigate", "https://b.test/")]
    # The stale entry must be consumed, not left behind to affect the next check.
    assert id(page) not in rs._nav_pending_renderer_initiated


def test_only_the_main_frame_asking_to_navigate_marks_the_page_as_navigating_itself() -> None:
    """WF-4 Leg C: an ad frame reloading itself flagged the tab as navigating on its own, so the address the person
    typed next (another site) was never recorded as a step."""
    handlers: dict = {}

    class _Cdp:
        def send(self, method: str):
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "MAIN"}}}
            return {"currentIndex": 0, "entries": [{"url": "https://a.test/"}]}

        def on(self, name, fn):
            handlers[name] = fn

    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._context = SimpleNamespace(new_cdp_session=lambda _p: _Cdp())
    rs._ensure_nav_history_session_sync(page)
    handlers["Page.frameRequestedNavigation"]({"frameId": "AD-FRAME"})
    assert id(page) not in rs._nav_pending_renderer_initiated
    handlers["Page.frameRequestedNavigation"]({"frameId": "MAIN"})
    assert id(page) in rs._nav_pending_renderer_initiated


def test_build_step_compiles_manual_navigate_to_a_navigate_step(tmp_path: Path) -> None:
    from conxa_compile.policy.bundle import get_policy_bundle

    ev = _history_event("manual_navigate", "tab_0", "https://a.test/", "https://b.test/")
    step = _build_step(ev, get_policy_bundle(), session_root=tmp_path, step_index=1)
    assert step.action == "navigate"
    assert step.url == "https://b.test/"
    assert step.recovery.strategies == []
    assert step.validation.wait_for["type"] == "url_change"
    assert step.validation.wait_for["target"] == "https://b.test/"


def test_recorder_same_index_reload_emits_nothing() -> None:
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 1, "entries": [{"url": "https://a.test/a"}, {"url": "https://a.test/b"}]},
    ])
    rs._nav_history_state[id(page)] = {
        "index": 1,
        "entries": ["https://a.test/a", "https://a.test/b"],
    }
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    assert _queued(rs) == []


def test_recorder_no_baseline_skips_first_check_but_stores_it() -> None:
    page = _fake_page()
    rs = RecordingSession(session_id="test-nav")
    rs._nav_cdp_sessions[id(page)] = _FakeCdpSession([
        {"currentIndex": 0, "entries": [{"url": "https://a.test/a"}]},
    ])
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    assert _queued(rs) == []
    assert rs._nav_history_state[id(page)]["index"] == 0


def test_recorder_drops_state_for_closed_pages() -> None:
    page = _fake_page(closed=True)
    rs = _rs_with_state(page, {"index": 0, "entries": ["https://a.test/"]})
    rs._drain_nav_history_checks_sync()
    assert id(page) not in rs._nav_history_state
    assert id(page) not in rs._nav_cdp_sessions
