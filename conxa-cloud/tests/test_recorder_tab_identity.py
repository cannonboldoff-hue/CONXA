"""Multi-tab recording: RecordingSession assigns a stable tab id + opened_by classification to
every page discovered via the pump loop, and stamps that context onto recorded events. See
conxa_compile/recorder/session.py::_register_new_pages_sync / _tab_context_for_page.
"""
from __future__ import annotations

import pytest

from conxa_compile.recorder.session import RecordingSession


class FakeContext:
    def __init__(self, pages: list) -> None:
        self.pages = pages


class FakeVideo:
    def __init__(self, path: str | None) -> None:
        self._path = path

    def path(self) -> str:
        if self._path is None:
            raise Exception("no video path yet")
        return self._path


class FakePage:
    def __init__(self, *, opener=None, video_path: str | None = "video.webm", url: str = "https://x.test/") -> None:
        self._opener = opener
        self.video = FakeVideo(video_path)
        self.url = url

    def is_closed(self) -> bool:
        return False

    def opener(self):
        return self._opener


def test_first_registered_page_is_tab_0_opened_by_initial() -> None:
    sess = RecordingSession(session_id="tab-first")
    page = FakePage()
    sess._context = FakeContext([page])

    sess._register_new_pages_sync()

    assert sess._tab_ids[id(page)] == "tab_0"
    assert sess._tab_meta["tab_0"]["opened_by"] == "initial"
    assert sess._tab_meta["tab_0"]["opener_tab"] is None


def test_second_page_with_opener_is_classified_site_opened() -> None:
    sess = RecordingSession(session_id="tab-site")
    initial = FakePage()
    sess._context = FakeContext([initial])
    sess._register_new_pages_sync()

    popup = FakePage(opener=initial)
    sess._context.pages = [initial, popup]
    sess._register_new_pages_sync()

    assert sess._tab_ids[id(popup)] == "tab_1"
    assert sess._tab_meta["tab_1"]["opened_by"] == "site"
    assert sess._tab_meta["tab_1"]["opener_tab"] == "tab_0"


def test_second_page_with_no_opener_is_classified_user_opened() -> None:
    """Ctrl+T — nothing on the page opened this tab, so page.opener() is None."""
    sess = RecordingSession(session_id="tab-user")
    initial = FakePage()
    sess._context = FakeContext([initial])
    sess._register_new_pages_sync()

    manual = FakePage(opener=None)
    sess._context.pages = [initial, manual]
    sess._register_new_pages_sync()

    assert sess._tab_meta["tab_1"]["opened_by"] == "user"
    assert sess._tab_meta["tab_1"]["opener_tab"] is None


def test_already_registered_pages_are_not_reassigned() -> None:
    sess = RecordingSession(session_id="tab-stable")
    page = FakePage()
    sess._context = FakeContext([page])
    sess._register_new_pages_sync()
    sess._register_new_pages_sync()  # second tick, same page list

    assert sess._tab_ids[id(page)] == "tab_0"
    assert sess._tab_seq == 1


def test_auth_mode_never_registers_tabs() -> None:
    sess = RecordingSession(session_id="tab-auth", auth_mode=True)
    sess._context = FakeContext([FakePage()])
    sess._register_new_pages_sync()
    assert sess._tab_meta == {}


def test_tab_context_for_page_falls_back_to_tab_0_for_unregistered_page() -> None:
    sess = RecordingSession(session_id="tab-fallback")
    ctx = sess._tab_context_for_page(FakePage())
    assert ctx["id"] == "tab_0"
    assert ctx["opened_by"] == "initial"


def test_compute_event_timestamp_ms_uses_the_named_tabs_own_video_start() -> None:
    """Each tab's video starts at a different wall-clock moment — the offset must be relative
    to the tab that produced the event, not always the session-wide (tab_0) start."""
    sess = RecordingSession(session_id="tab-timing")
    sess._video_session_start_wall_ms = 1_000_000
    sess._tab_meta["tab_1"] = {"video_start_wall_ms": 2_000_000}

    from datetime import datetime, timezone
    # 2_000_500 ms epoch -> tab_1's own start (2_000_000) yields offset 500, not 1_000_500.
    ts_iso = datetime.fromtimestamp(2_000_500 / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    action = {"timestamp": ts_iso}

    assert sess._compute_event_timestamp_ms(action, tab_id="tab_1") == 500
    assert sess._compute_event_timestamp_ms(action, tab_id=None) == 1_000_500


def test_popup_event_is_stamped_with_the_opener_tab_not_the_active_tab() -> None:
    """A popup opened from a background tab must be enqueued against the tab whose listener
    fired it, not whatever tab happens to be _active_page_sync() when the event fires — the
    Create-a-Lead bug: a click on tab_1 opens a login popup, but tab_0 was the recorder's
    "active" page at that instant, so the popup event was mis-stamped tab_0 and the compiler
    inserted a spurious tab_switch back to it. See _attach_page_listeners / _on_popup."""
    sess = RecordingSession(session_id="tab-popup-src")
    tab0 = FakePage()
    tab1 = FakePage(opener=tab0)
    sess._context = FakeContext([tab0, tab1])
    sess._register_new_pages_sync()  # assigns tab0 -> tab_0, tab1 -> tab_1
    sess._page = tab0  # tab_0 is "active" even though the click happened on tab_1

    popup = FakePage(opener=tab1, video_path=None, url="https://search-engine-5nfe.vercel.app/login")
    sess._on_popup(popup, src_page=tab1)

    payload, src_page, src_frame = sess._pending_payloads.get_nowait()
    assert src_page is tab1
    assert src_frame is None
    assert payload["page"]["url"] == tab1.url


def test_on_popup_without_src_page_falls_back_to_active_page() -> None:
    """Back-compat: a caller that doesn't know the opener page (there is none today, but the
    parameter is optional) still gets the pre-existing behavior."""
    sess = RecordingSession(session_id="tab-popup-fallback")
    tab0 = FakePage(url="https://dashboard.render.com/")
    sess._context = FakeContext([tab0])
    sess._register_new_pages_sync()
    sess._page = tab0

    popup = FakePage(video_path=None, url="https://vercel.com/new")
    sess._on_popup(popup)

    payload, src_page, _ = sess._pending_payloads.get_nowait()
    assert src_page is None
    assert payload["page"]["url"] == tab0.url


def test_should_merge_typing_never_merges_across_a_tab_boundary() -> None:
    """Two 'type' events into what looks like the same target key must not merge if they
    happened on different tabs — otherwise typing recorded on tab_0 could silently absorb a
    later keystroke recorded on tab_1."""
    from conxa_core.models.events import RecordedEvent

    sess = RecordingSession(session_id="tab-merge-guard")
    base = {
        "action": {"action": "type", "timestamp": "2025-01-01T00:00:00Z", "value": "a"},
        "target": {"tag": "input", "name": "q"},
        "selectors": {}, "context": {"parent": ""}, "semantic": {"normalized_text": "", "role": "", "intent_hint": ""},
        "visual": {"bbox": {"x": 0, "y": 0, "w": 0, "h": 0}, "viewport": "", "scroll_position": "0,0", "timestamp_ms": 0},
        "page": {"url": "", "title": ""}, "state_change": {"before": "", "after": ""}, "timing": {},
        "ancestors": [], "surrounding_text": "", "snapshot": {},
    }
    import copy
    ev_tab0 = RecordedEvent.model_validate({**copy.deepcopy(base), "tab": {"id": "tab_0"}})
    ev_tab1 = RecordedEvent.model_validate({**copy.deepcopy(base), "tab": {"id": "tab_1"}})

    assert sess._should_merge_typing(ev_tab0, ev_tab1) is False
    assert sess._should_merge_typing(ev_tab0, ev_tab0.model_copy(deep=True)) is True


def test_popup_event_is_stamped_with_the_popups_own_tab_not_the_openers() -> None:
    """Regression (2026-09-02, mega-workflow steps 28-30). The popup event used to be stamped
    with the tab that CLICKED the link, because _on_popup runs inside Playwright's own event
    callback — a tick before _register_new_pages_sync names the new page. Every event around the
    popup then said the same tab id, _insert_tab_markers saw no transition, and the compiled
    skill never mentioned the second tab: the replay drove the original tab from the background
    for the rest of the run. The lookup is now deferred to the pump-loop drain by object key.
    """
    sess = RecordingSession(session_id="tab-popup-deferred")
    opener = FakePage(url="https://the-internet.herokuapp.com/windows")
    sess._context = FakeContext([opener])
    sess._register_new_pages_sync()
    sess._page = opener

    # The popup fires BEFORE the pump loop has registered it — exactly the real ordering.
    popup = FakePage(opener=opener, video_path=None, url="https://the-internet.herokuapp.com/windows/new")
    sess._on_popup(popup, opener)
    assert sess._tab_id_for_page(popup) is None, "popup must still be unregistered at enqueue time"

    payload, src_page, _ = sess._pending_payloads.get_nowait()
    assert payload["_tab_key"] == id(popup)
    # src_page is untouched: visuals and page.url still belong to the tab that clicked.
    assert src_page is opener
    assert payload["page"]["url"] == opener.url

    # Pump tick: registration happens, then the drain resolves the deferred key.
    sess._context.pages = [opener, popup]
    sess._register_new_pages_sync()

    tab_key = payload.pop("_tab_key")
    tab = sess._tab_context_for_tab_id(sess._tab_ids.get(tab_key))
    assert tab["id"] == "tab_1"
    assert tab["opened_by"] == "site"
    assert tab["opener_tab"] == "tab_0"


def test_popup_that_closes_before_registration_keeps_the_openers_tab() -> None:
    """A popup that never survives to a pump tick is never assigned an id; the deferred lookup
    misses and the event falls back to today's opener-stamped behavior rather than failing."""
    sess = RecordingSession(session_id="tab-popup-vanished")
    opener = FakePage(url="https://x.test/")
    sess._context = FakeContext([opener])
    sess._register_new_pages_sync()
    sess._page = opener

    popup = FakePage(opener=opener, video_path=None, url="https://x.test/gone")
    sess._on_popup(popup, opener)
    payload, _, _ = sess._pending_payloads.get_nowait()

    # No _register_new_pages_sync — the popup is gone.
    tab_key = payload.pop("_tab_key")
    assert sess._tab_ids.get(tab_key) is None
    assert sess._tab_context_for_page(opener)["id"] == "tab_0"


def test_a_popup_event_waits_for_its_tab_to_be_registered_and_keeps_its_place_in_the_queue():
    from queue import Empty

    from conxa_compile.recorder.session import RecordingSession

    sess = RecordingSession(session_id="popup-race")
    popup = {"kind": "popup", "_tab_key": 111}
    later = {"kind": "click"}
    sess._pending_payloads.put((popup, None, None))
    sess._pending_payloads.put((later, None, None))
    with pytest.raises(Empty):
        sess._next_payload_sync()                    # tab 111 is not registered yet: hold the popup...
    with pytest.raises(Empty):
        sess._next_payload_sync()                    # ...and the click behind it waits too (order kept)
    sess._tab_ids[111] = "tab_1"                     # the pump registers the new page
    assert sess._next_payload_sync()[0] is popup
    assert "_held_since" not in popup
    assert sess._next_payload_sync()[0] is later


def test_a_popup_whose_tab_never_registers_goes_with_the_opener_after_the_wait_and_at_shutdown():
    from queue import Empty

    from conxa_compile.recorder.session import RecordingSession

    sess = RecordingSession(session_id="popup-race-2")
    sess.POPUP_TAB_WAIT_S = 0.0
    popup = {"kind": "popup", "_tab_key": 222}
    sess._pending_payloads.put((popup, None, None))
    assert sess._next_payload_sync()[0] is popup     # wait elapsed: released as before (opener's tab)
    sess.POPUP_TAB_WAIT_S = 60
    sess._pending_payloads.put(({"kind": "popup", "_tab_key": 333}, None, None))
    assert sess._next_payload_sync(final=True)[0]["_tab_key"] == 333   # shutdown never waits
    with pytest.raises(Empty):
        sess._next_payload_sync()
