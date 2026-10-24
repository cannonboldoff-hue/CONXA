from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

from conxa_compile.recorder import session as recorder_session
from conxa_compile.recorder.session import RecordingSession, check_app_session_sync, url_matches_pattern
from conxa_core.models.workflow import GroupApp


def _payload(action: str = "click") -> dict:
    return {
        "action": {"action": action, "timestamp": "2026-01-01T00:00:00Z", "value": None},
        "target": {"tag": "button", "id": None, "classes": [], "inner_text": "Save", "role": "button", "aria_label": None, "name": None},
        "selectors": {"css": "button", "xpath": "/button[1]", "text_based": 'text="Save"', "aria": '[role="button"][name="Save"]'},
        "context": {"parent": "body", "siblings": [], "index_in_parent": 0, "form_context": None},
        "semantic": {"normalized_text": "save", "role": "button", "input_type": None, "intent_hint": "activate_control"},
        "anchors": [],
        "visual_placeholder": {"bbox": {"x": 1, "y": 1, "w": 20, "h": 10}, "viewport": "1280x720", "scroll_position": "0,0"},
        "page": {"url": "https://example.com", "title": "Example"},
        "state_change": {"before": "", "after": ""},
    }


def test_payload_capture_error_is_recorded_without_raising(monkeypatch) -> None:
    sess = RecordingSession(session_id="safe-capture")

    def fail_capture(*_args, **_kwargs):
        raise ValueError("bad event")

    monkeypatch.setattr(sess, "_consume_payload_sync", fail_capture)

    sess._consume_payload_safe_sync({"action": {"action": "hover"}})

    assert sess.binding_errors == ["event_capture_error:hover: bad event"]


def test_url_matches_pattern_supports_curly_brace_wildcard() -> None:
    pattern = "https://vercel.com/{}"

    assert url_matches_pattern("https://vercel.com/dashboard", pattern)
    assert url_matches_pattern("https://vercel.com/team/settings", pattern)
    assert not url_matches_pattern("https://vercel.com", pattern)  # missing the trailing slash the prefix requires
    assert not url_matches_pattern("https://example.com/vercel.com/dashboard", pattern)


def test_url_matches_pattern_excludes_start_url_variants() -> None:
    pattern = "https://app.example.com/dashboard"

    assert url_matches_pattern("https://app.example.com/dashboard", pattern)
    assert not url_matches_pattern(
        "https://app.example.com/login?returnUrl=/dashboard",
        pattern,
        exclude_prefix="https://app.example.com/login",
    )


def test_status_exposes_current_url_and_ignores_blank_urls() -> None:
    sess = RecordingSession(session_id="current-url")

    sess._remember_current_url("about:blank")
    assert sess.status()["current_url"] == ""

    sess._remember_current_url("https://example.com/app?team=abc#leads")

    assert sess.status()["current_url"] == "https://example.com/app?team=abc#leads"


def test_tab_url_is_per_tab_not_shared_globally() -> None:
    sess = RecordingSession(session_id="per-tab-url")

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url

        def is_closed(self) -> bool:
            return False

    page_a = FakePage("https://a.example.com/")
    page_b = FakePage("https://b.example.com/")
    sess._tab_ids[id(page_a)] = "tab_0"
    sess._tab_meta["tab_0"] = {"index": 0, "opened_by": "initial", "opener_tab": None, "last_url": "https://a.example.com/"}
    sess._tab_ids[id(page_b)] = "tab_1"
    sess._tab_meta["tab_1"] = {"index": 1, "opened_by": "user", "opener_tab": None, "last_url": "https://b.example.com/"}

    # tab_1 navigates elsewhere — must not bleed into tab_0's reported url (the bug this guards:
    # _tab_context_for_page used to return the session-wide current_url for every tab).
    page_b.url = "https://c.example.com/"
    sess._remember_page_url_sync(page_b)

    assert sess._tab_context_for_page(page_a)["url"] == "https://a.example.com/"
    assert sess._tab_context_for_page(page_b)["url"] == "https://c.example.com/"


def test_ensure_bridge_installs_missing_child_frame() -> None:
    sess = RecordingSession(session_id="frame-bridge")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class FakeFrame:
        def __init__(self, installed: bool) -> None:
            self.installed = installed
            self.calls: list[str] = []

        def evaluate(self, script: str) -> dict | None:
            self.calls.append(script)
            if "hasWin: !!window.__SKILL_BRIDGE_V1__" in script:
                return {"hasWin": self.installed, "hasDoc": self.installed}
            self.installed = True
            return None

    class FakePage:
        frames: list[FakeFrame]

        def __init__(self) -> None:
            self.frames = [FakeFrame(installed=True), FakeFrame(installed=False)]

        def is_closed(self) -> bool:
            return False

    page = FakePage()

    sess._ensure_bridge_installed_sync(page)

    assert len(page.frames[0].calls) == 1
    assert "hasWin: !!window.__SKILL_BRIDGE_V1__" in page.frames[0].calls[0]
    assert len(page.frames[1].calls) == 2
    assert "hasWin: !!window.__SKILL_BRIDGE_V1__" in page.frames[1].calls[0]
    assert page.frames[1].calls[1] == sess._bridge_script
    assert page.frames[1].installed is True
    assert sess.binding_errors == []


def test_ensure_bridge_reinstalls_doc_listeners_when_only_doc_flag_stale() -> None:
    """Bridge already installed once in this realm (hasWin) but the document-scoped
    listener flag is stale (hasDoc false) — e.g. bridge.js's own Document.prototype
    open/write/writeln patch didn't take for some reason. The pump-loop backstop must
    call the exposed reinstall hook, not re-run (and duplicate listeners from) the
    whole bridge script."""
    sess = RecordingSession(session_id="frame-bridge-reinstall")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class FakeFrame:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def evaluate(self, script: str) -> dict | None:
            self.calls.append(script)
            if "hasWin: !!window.__SKILL_BRIDGE_V1__" in script:
                return {"hasWin": True, "hasDoc": False}
            return None

    frame = FakeFrame()
    sess._ensure_bridge_installed_in_frame_sync(frame)

    assert len(frame.calls) == 2
    assert "hasWin: !!window.__SKILL_BRIDGE_V1__" in frame.calls[0]
    assert "__SKILL_REINSTALL_DOC__" in frame.calls[1]
    assert frame.calls[1] != sess._bridge_script


def test_ensure_bridge_retries_through_transient_detach_then_succeeds() -> None:
    """HubSpot-style embedded panels (e.g. the Create Contact object-builder-ui iframe) churn
    through several attach/navigate/detach cycles before settling — a real recording's diagnostic
    log showed the same frame failing 'Frame was detached'/'Execution context was destroyed' on
    every single check for its entire practical lifetime, only stabilizing once there was no time
    left to matter. A single check has no better than a coin-flip's chance of landing inside the
    stable window between churns; the retry loop must keep trying through transient failures
    within one call instead of waiting for the next external trigger."""
    sess = RecordingSession(session_id="frame-churn-retry")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class ChurningFrame:
        def __init__(self, fail_count: int) -> None:
            self.fail_count = fail_count
            self.calls = 0
            self.installed = False

        def evaluate(self, script: str) -> dict | None:
            self.calls += 1
            if self.calls <= self.fail_count:
                raise Exception("Frame.evaluate: Frame was detached")
            if "hasWin: !!window.__SKILL_BRIDGE_V1__" in script:
                return {"hasWin": self.installed, "hasDoc": self.installed}
            self.installed = True
            return None

    frame = ChurningFrame(fail_count=3)
    sess._ensure_bridge_installed_in_frame_sync(frame)

    assert frame.installed is True
    assert sess.binding_errors == [], "should not report an error once a retry succeeds"


def test_ensure_bridge_gives_up_after_max_attempts_on_persistent_churn() -> None:
    sess = RecordingSession(session_id="frame-churn-exhausted")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class AlwaysDetachedFrame:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, script: str) -> dict | None:
            self.calls += 1
            raise Exception("Frame.evaluate: Frame was detached")

    frame = AlwaysDetachedFrame()
    sess._ensure_bridge_installed_in_frame_sync(frame)

    assert frame.calls == sess._BRIDGE_INSTALL_MAX_ATTEMPTS
    assert len(sess.binding_errors) == 1
    assert "Frame was detached" in sess.binding_errors[0]


def test_ensure_bridge_does_not_retry_non_transient_errors() -> None:
    sess = RecordingSession(session_id="frame-real-error")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class BrokenFrame:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, script: str) -> dict | None:
            self.calls += 1
            raise Exception("SyntaxError: unexpected token")

    frame = BrokenFrame()
    sess._ensure_bridge_installed_in_frame_sync(frame)

    assert frame.calls == 1, "a non-transient error should not trigger the churn retry loop"
    assert len(sess.binding_errors) == 1


def test_frame_ready_installs_bridge_immediately() -> None:
    sess = RecordingSession(session_id="frame-ready")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class FakeFrame:
        def __init__(self) -> None:
            self.installed = False
            self.calls: list[str] = []

        def evaluate(self, script: str) -> dict | None:
            self.calls.append(script)
            if "hasWin: !!window.__SKILL_BRIDGE_V1__" in script:
                return {"hasWin": self.installed, "hasDoc": self.installed}
            self.installed = True
            return None

    frame = FakeFrame()

    sess._on_frame_ready(frame)

    assert len(frame.calls) == 2
    assert "hasWin: !!window.__SKILL_BRIDGE_V1__" in frame.calls[0]
    assert frame.calls[1] == sess._bridge_script
    assert frame.installed is True


def test_frame_lifecycle_logs_attach_navigate_detach_even_when_frame_is_gone() -> None:
    """Diagnostic-only log (recorder_diag.json's frame_lifecycle) — event-driven, not sampled
    like frame_snapshots, so it catches a frame that attaches and detaches faster than any
    polling interval could observe. Must not blow up once the frame is actually gone."""
    sess = RecordingSession(session_id="frame-lifecycle")
    sess._bridge_script = "window.__SKILL_BRIDGE_V1__ = true;"

    class FakeFrame:
        def __init__(self, url: str, parent: "FakeFrame | None" = None) -> None:
            self.url = url
            self.parent_frame = parent

        def evaluate(self, script: str) -> dict | None:
            if "hasWin: !!window.__SKILL_BRIDGE_V1__" in script:
                return {"hasWin": True, "hasDoc": True}
            return None

    top = FakeFrame("https://app-na2.hubspot.com/contacts/1")
    child = FakeFrame("https://app-na2.hubspot.com/object-builder/1/embed", parent=top)

    sess._on_frame_attached(child)
    sess._on_frame_navigated(child)
    sess._on_frame_detached(child)

    kinds = [(e["kind"], e["url"], e["parent_url"]) for e in sess._frame_lifecycle]
    assert kinds == [
        ("attached", child.url, top.url),
        ("navigated", child.url, top.url),
        ("detached", child.url, top.url),
    ]
    assert all(isinstance(e["ts"], int) for e in sess._frame_lifecycle)

    # A frame that's already gone (evaluate/parent_frame raise) must not crash the logger.
    class DeadFrame:
        @property
        def url(self) -> str:
            raise RuntimeError("Frame was detached")

        @property
        def parent_frame(self) -> None:
            raise RuntimeError("Frame was detached")

    sess._on_frame_detached(DeadFrame())
    assert len(sess._frame_lifecycle) == 4
    assert sess._frame_lifecycle[-1]["kind"] == "detached"
    assert sess._frame_lifecycle[-1]["url"] == ""


def test_binding_source_child_frame_adds_frame_context() -> None:
    sess = RecordingSession(session_id="frame-context")

    class FakeElement:
        def evaluate(self, script: str) -> dict:
            if "getBoundingClientRect" in script:
                return {"x": 42, "y": 18, "w": 600, "h": 720}
            return {
                "id": "object-builder-ui",
                "data-test-id": "object-builder-ui-iframe",
                "data-selenium-test": "associate-panel-iframe",
                "name": "",
                "title": "",
                "aria-label": "",
                "src": "https://app-na2.hubspot.com/object-builder/246242636/0-1/embed?",
            }

    class FakeFrame:
        def __init__(self, parent: object | None, url: str = "") -> None:
            self.parent_frame = parent
            self.url = url

        def frame_element(self) -> FakeElement:
            return FakeElement()

    class FakePage:
        pass

    payload = _payload()
    src_frame = FakeFrame(
        object(),
        "https://app-na2.hubspot.com/object-builder/246242636/0-1/embed?",
    )
    sess._binding_sink_sync(
        {
            "page": FakePage(),
            "frame": src_frame,
        },
        payload,
    )

    # _binding_sink_sync must NOT compute frame context itself — that requires Playwright
    # sync-API calls, and this callback runs from inside Playwright's own dispatcher while
    # delivering an expose_binding call; a nested blocking call there is a reentrancy hazard
    # that can deadlock forever with no exception raised. It only passes the raw frame
    # through; extraction happens later, from the plain pump-loop drain (see
    # _consume_payload_sync), which is never invoked from inside that dispatch.
    queued_payload, _page, queued_frame = sess._pending_payloads.get_nowait()
    assert queued_frame is src_frame
    assert "frame" not in queued_payload
    assert "_frame_offset" not in queued_payload

    frame_context, frame_offset = recorder_session._frame_context_and_offset_sync(queued_frame)
    # Cutover: frame chain carries durability-ranked fingerprint signals, not a single selector.
    chain0 = frame_context["chain"][0]
    signals = chain0["fingerprint"]["signals"]
    selectors = [s["selector"] for s in signals]
    # Highest-durability frame signal is a test-id attribute selector.
    assert signals[0]["selector"].startswith("iframe[data-")
    assert 'iframe[data-test-id="object-builder-ui-iframe"]' in selectors
    assert 'iframe[id="object-builder-ui"]' in selectors
    assert frame_offset == {"x": 42.0, "y": 18.0}


def test_frame_offset_adjusts_visual_bbox_before_capture(tmp_path) -> None:
    # Screenshots are no longer taken synchronously; we verify the bbox offset adjustment
    # and viewport capture happen correctly in _finalize_payload_sync.
    sess = RecordingSession(session_id="frame-bbox", data_root=tmp_path)
    sess._video_session_start_wall_ms = 1

    class FakePage:
        viewport_size = {"width": 1280, "height": 720}

        def is_closed(self) -> bool:
            return False

    payload = _payload()
    payload["_frame_offset"] = {"x": 50, "y": 20}
    payload["frame"] = {
        "chain": [
            {
                "selector": 'iframe[id="object-builder-ui"]',
                "fallback_selectors": [],
                "url": "",
                "url_pattern": "",
            }
        ]
    }

    event = sess._finalize_payload_sync(FakePage(), tmp_path / "sessions" / sess.session_id, payload)

    assert event.visual.bbox == {"x": 51, "y": 21, "w": 20, "h": 10}
    assert event.visual.viewport == "1280x720"
    assert event.frame.chain[0]["selector"] == 'iframe[id="object-builder-ui"]'
    # full_screenshot is None during recording; frame extractor sets it at shutdown.
    assert event.visual.full_screenshot is None


def test_visual_capture_is_deferred_to_frame_extraction(tmp_path) -> None:
    # Screenshots are no longer captured synchronously during recording.
    # full_screenshot and element_snapshot are always None until frame_extractor runs at shutdown.
    sess = RecordingSession(session_id="visual-fallback", data_root=tmp_path)
    sess._video_session_start_wall_ms = 1

    class FakePage:
        def is_closed(self) -> bool:
            return False

    sess._page = FakePage()
    sess._consume_payload_sync(_payload())

    assert len(sess.snapshot_events()) == 1
    assert sess.snapshot_events()[0]["visual"]["full_screenshot"] is None
    assert sess.snapshot_events()[0]["visual"]["element_snapshot"] is None
    # No visual capture errors — capture is deferred, not attempted during recording.
    assert not any(err.startswith("visual_capture_error:") for err in sess.binding_errors)


def test_finalize_video_file_renames_webm_without_touching_events(tmp_path) -> None:
    # Frame extraction moved to compile time (see handlers/compile.py and
    # frame_extractor.py) so it can be retried per-event on recompile.
    # Finalize at recorder shutdown now only renames Playwright's raw .webm —
    # it no longer needs or touches events.jsonl at all.
    sess = RecordingSession(session_id="empty-video", data_root=tmp_path)
    session_dir = tmp_path / "sessions" / sess.session_id
    session_dir.mkdir(parents=True)
    raw_video = session_dir / "playwright-output.webm"
    raw_video.write_bytes(b"video")

    sess._finalize_video_file_sync()

    assert not raw_video.exists()
    assert (session_dir / "recording.webm").read_bytes() == b"video"
    assert not (session_dir / "events.jsonl").exists()
    assert sess.binding_errors == []


def test_check_app_session_sync_returns_unverified_within_deadline_when_probe_wedges(monkeypatch, tmp_path) -> None:
    """Regression guard: a wedged Playwright driver inside the probe body must
    not be able to hang the calling thread forever — see FIX.md. Bound the
    probe with a short deadline and confirm it returns (rather than blocking
    for the monkeypatched 60s sleep) at "unverified" — an inconclusive probe
    must never read as signed in."""
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    app = GroupApp(id="a", name="App", login_url="https://x.test/login", storage_state_path=str(state_path))

    def _wedge(_app):
        time.sleep(60)
        return "expired"

    monkeypatch.setattr(recorder_session, "_probe_app_session_sync", _wedge)

    started = time.monotonic()
    result = check_app_session_sync(app, timeout_s=0.2)
    elapsed = time.monotonic() - started

    assert result == "unverified"
    assert elapsed < 5, f"probe should return near the 0.2s deadline, took {elapsed:.2f}s"


def test_filechooser_listener_is_registered() -> None:
    """The Studio intercepts the native OS file picker (see _on_file_chooser) so it can
    show its own dialog pre-pointed at the download folder. FileChooser.set_files() still
    dispatches the input's change event via CDP, so bridge.js's upload_intent capture is
    unaffected — unlike a bare unhandled listener, which would break it.
    """
    sess = RecordingSession(session_id="filechooser-registered")
    registered: list[str] = []

    class _FakePage:
        def on(self, event: str, _handler) -> None:
            registered.append(event)

    sess._attach_page_listeners(_FakePage())

    assert "filechooser" in registered
    # The other page-level listeners must still be wired, or this test would pass vacuously.
    assert {"download", "dialog", "popup", "framenavigated"} <= set(registered)


def test_unique_download_name_first_claim_keeps_the_suggested_name() -> None:
    taken: set[str] = set()
    assert RecordingSession._unique_download_name("report.pdf", taken) == "report.pdf"


def test_unique_download_name_collisions_get_distinct_suffixed_names() -> None:
    taken: set[str] = set()
    names = [RecordingSession._unique_download_name("invoice.pdf", taken) for _ in range(3)]
    assert names == ["invoice.pdf", "invoice (2).pdf", "invoice (3).pdf"]
    assert len(set(names)) == 3


def test_unique_download_name_suffix_goes_before_the_extension() -> None:
    taken: set[str] = set()
    RecordingSession._unique_download_name("export.csv", taken)
    assert RecordingSession._unique_download_name("export.csv", taken) == "export (2).csv"


def _make_zip_bytes(names: list[str]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return buf.getvalue()


class _FakeDownload:
    def __init__(self, url: str, suggested_filename: str, content: bytes) -> None:
        self.url = url
        self.suggested_filename = suggested_filename
        self._content = content

    def save_as(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self._content)


def test_extract_zip_once_lists_top_level_files(tmp_path) -> None:
    zip_path = tmp_path / "archive.zip"
    zip_path.write_bytes(_make_zip_bytes(["b.pdf", "a.pdf", "c.pdf"]))

    members = RecordingSession._extract_zip_once(zip_path)

    assert members == ["a.pdf", "b.pdf", "c.pdf"]
    assert (tmp_path / "archive" / "a.pdf").exists()


def test_extract_zip_once_unwraps_a_single_top_level_wrapping_folder(tmp_path) -> None:
    zip_path = tmp_path / "archive.zip"
    zip_path.write_bytes(_make_zip_bytes(["export/a.pdf", "export/b.pdf"]))

    members = RecordingSession._extract_zip_once(zip_path)

    assert members == ["a.pdf", "b.pdf"]


def test_extract_zip_once_is_idempotent(tmp_path) -> None:
    zip_path = tmp_path / "archive.zip"
    zip_path.write_bytes(_make_zip_bytes(["a.pdf"]))

    first = RecordingSession._extract_zip_once(zip_path)
    second = RecordingSession._extract_zip_once(zip_path)

    assert first == second == ["a.pdf"]


def test_on_download_of_a_zip_extracts_it_and_records_zip_members(tmp_path) -> None:
    sess = RecordingSession(session_id="zip-download")
    sess._downloads_dir = tmp_path / "downloads"
    sess._downloads_dir.mkdir(parents=True)

    download = _FakeDownload(
        "https://x.test/export.zip", "export.zip", _make_zip_bytes(["a.pdf", "b.pdf"])
    )
    sess._on_download(download)

    assert sess.binding_errors == []
    payload, _page, _frame = sess._pending_payloads.get_nowait()
    value = json.loads(payload["action"]["value"])
    assert value["suggested_filename"] == "export.zip"
    assert value["zip_members"] == ["a.pdf", "b.pdf"]
    assert (sess._downloads_dir / "export.zip").exists()
    assert (sess._downloads_dir / "export" / "a.pdf").exists()
    assert sess._last_download_dir == sess._downloads_dir / "export"


def test_on_download_of_a_non_zip_file_has_no_zip_members_field(tmp_path) -> None:
    sess = RecordingSession(session_id="plain-download")
    sess._downloads_dir = tmp_path / "downloads"
    sess._downloads_dir.mkdir(parents=True)

    download = _FakeDownload("https://x.test/report.pdf", "report.pdf", b"not a zip")
    sess._on_download(download)

    assert sess.binding_errors == []
    payload, _page, _frame = sess._pending_payloads.get_nowait()
    value = json.loads(payload["action"]["value"])
    assert "zip_members" not in value


class _FakeChooser:
    def __init__(self, is_multiple: bool = False) -> None:
        self.is_multiple = is_multiple
        self.set_files_calls: list[list[str] | None] = []

    def set_files(self, paths) -> None:
        self.set_files_calls.append(paths)


def test_on_file_chooser_defaults_to_the_downloads_dir(tmp_path) -> None:
    sess = RecordingSession(session_id="chooser-default-dir")
    sess._downloads_dir = tmp_path / "downloads"
    sess._downloads_dir.mkdir(parents=True)
    requests: list[tuple[str, str, bool]] = []
    sess.on_file_picker_request = lambda request_id, default_dir, multiple: requests.append(
        (request_id, default_dir, multiple)
    )

    sess._on_file_chooser(_FakeChooser())

    assert sess.binding_errors == []
    assert len(requests) == 1
    request_id, default_dir, multiple = requests[0]
    assert default_dir == str(sess._downloads_dir)
    assert multiple is False
    assert sess._pending_file_chooser[0] == request_id


def test_on_file_chooser_prefers_the_zip_extract_dir_after_a_zip_download(tmp_path) -> None:
    sess = RecordingSession(session_id="chooser-zip-dir")
    sess._downloads_dir = tmp_path / "downloads"
    sess._downloads_dir.mkdir(parents=True)
    sess._on_download(
        _FakeDownload("https://x.test/export.zip", "export.zip", _make_zip_bytes(["a.pdf"]))
    )

    requests: list[tuple[str, str, bool]] = []
    sess.on_file_picker_request = lambda rid, default_dir, multiple: requests.append(
        (rid, default_dir, multiple)
    )
    sess._on_file_chooser(_FakeChooser(is_multiple=True))

    assert requests[0][1] == str(sess._downloads_dir / "export")
    assert requests[0][2] is True


def test_on_file_chooser_defaults_to_unwrapped_folder_for_wrapping_zip(tmp_path) -> None:
    sess = RecordingSession(session_id="chooser-wrapping-zip")
    sess._downloads_dir = tmp_path / "downloads"
    sess._downloads_dir.mkdir(parents=True)
    sess._on_download(
        _FakeDownload(
            "https://x.test/export.zip",
            "export.zip",
            _make_zip_bytes(["export/a.pdf", "export/b.pdf"]),
        )
    )

    requests: list[tuple[str, str, bool]] = []
    sess.on_file_picker_request = lambda rid, default_dir, multiple: requests.append(
        (rid, default_dir, multiple)
    )
    sess._on_file_chooser(_FakeChooser())

    assert requests[0][1] == str(sess._downloads_dir / "export" / "export")
    assert (sess._downloads_dir / "export" / "export" / "a.pdf").exists()


def test_on_file_chooser_falls_back_to_home_downloads_with_no_prior_download() -> None:
    sess = RecordingSession(session_id="chooser-no-download-yet")
    requests: list[tuple[str, str, bool]] = []
    sess.on_file_picker_request = lambda rid, default_dir, multiple: requests.append(
        (rid, default_dir, multiple)
    )

    sess._on_file_chooser(_FakeChooser())

    assert requests[0][1] == str(Path.home() / "Downloads")


def test_resolve_file_pick_is_queued_not_applied_directly() -> None:
    """set_files() is a Playwright sync-API call and must run on the recorder's own driver
    thread (the pump loop), never on whatever thread calls resolve_file_pick — see the class
    docstring's threading note."""
    sess = RecordingSession(session_id="resolve-pick-queued")
    chooser = _FakeChooser()
    sess._pending_file_chooser = ("req-1", chooser)

    sess.resolve_file_pick("req-1", ["C:\\downloads\\file.pdf"])

    assert chooser.set_files_calls == []
    request_id, paths = sess._file_pick_results.get_nowait()
    assert request_id == "req-1"
    assert paths == ["C:\\downloads\\file.pdf"]


class _FakeDialog:
    """Mimics Playwright's Dialog just enough for _on_dialog/_drain_js_dialog_sync — records
    whether/how it was resolved instead of actually blocking anything."""

    def __init__(self, dialog_type: str, message: str, default_value: str = "") -> None:
        self.type = dialog_type
        self.message = message
        self.default_value = default_value
        self.accepted_with: list[str] = []
        self.dismissed = False

    def accept(self, prompt_text: str = "") -> None:
        self.accepted_with.append(prompt_text)

    def dismiss(self) -> None:
        self.dismissed = True


def test_on_dialog_does_not_auto_accept_it_holds_and_asks(monkeypatch) -> None:
    """The bug this whole fix addresses: the old code's synchronous dialog.accept() raced the
    human and closed the dialog almost immediately after it opened, so it vanished before
    anyone could see or answer it. _on_dialog must NOT call accept()/dismiss() itself — it
    must stash the dialog and fire the Studio callback instead."""
    sess = RecordingSession(session_id="dialog-holds-open")
    requests: list[tuple[str, str, str, str]] = []
    sess.on_js_dialog_request = lambda rid, dtype, message, default: requests.append(
        (rid, dtype, message, default)
    )
    dialog = _FakeDialog("confirm", "I am a JS Confirm")

    sess._on_dialog(dialog)

    assert dialog.accepted_with == []
    assert dialog.dismissed is False
    assert sess.binding_errors == []
    assert len(requests) == 1
    request_id, dialog_type, message, _default = requests[0]
    assert dialog_type == "confirm"
    assert message == "I am a JS Confirm"
    assert sess._pending_dialog[0] == request_id


def test_on_dialog_in_auth_mode_still_auto_accepts() -> None:
    """The login-capture browser's own pump loop never drains _pending_dialog (see
    _run_sync_recorder's auth_mode gate), so holding a dialog there would hang that page
    forever. Deliberately out of scope for this fix — preserve the old behavior in auth_mode."""
    sess = RecordingSession(session_id="dialog-auth-mode", auth_mode=True)
    dialog = _FakeDialog("alert", "Please sign in first")

    sess._on_dialog(dialog)

    assert dialog.accepted_with == [""]
    assert sess._pending_dialog is None


def test_resolve_js_dialog_is_queued_not_applied_directly() -> None:
    """Dialog.accept()/dismiss() are Playwright sync-API calls and must run on the recorder's
    own driver thread, never on whatever thread calls resolve_js_dialog."""
    sess = RecordingSession(session_id="dialog-resolve-queued")
    dialog = _FakeDialog("confirm", "Are you sure?")
    sess._pending_dialog = ("req-1", dialog, "confirm", "Are you sure?", 0.0, None)

    sess.resolve_js_dialog("req-1", True, "")

    assert dialog.accepted_with == []
    request_id, accepted, text = sess._dialog_results.get_nowait()
    assert (request_id, accepted, text) == ("req-1", True, "")


def test_drain_js_dialog_accepts_with_the_studio_answer_and_records_it() -> None:
    sess = RecordingSession(session_id="dialog-drain-accept")
    dialog = _FakeDialog("prompt", "I am a JS Prompt")
    sess._pending_dialog = ("req-1", dialog, "prompt", "I am a JS Prompt", 0.0, None)
    sess.resolve_js_dialog("req-1", True, "Conxa prompt")

    sess._drain_js_dialog_sync()

    assert dialog.accepted_with == ["Conxa prompt"]
    assert sess._pending_dialog is None
    payload, _src_page, _src_frame = sess._pending_payloads.get_nowait()
    assert payload["action"]["action"] == "dialog_accept"
    recorded = json.loads(payload["action"]["value"])
    assert recorded == {"type": "prompt", "message": "I am a JS Prompt", "value": "Conxa prompt"}


def test_drain_js_dialog_dismiss_records_dialog_dismiss() -> None:
    sess = RecordingSession(session_id="dialog-drain-dismiss")
    dialog = _FakeDialog("confirm", "Are you sure?")
    sess._pending_dialog = ("req-1", dialog, "confirm", "Are you sure?", 0.0, None)
    sess.resolve_js_dialog("req-1", False, "")

    sess._drain_js_dialog_sync()

    assert dialog.dismissed is True
    assert dialog.accepted_with == []
    payload, _src_page, _src_frame = sess._pending_payloads.get_nowait()
    assert payload["action"]["action"] == "dialog_dismiss"


class _FakePageForBringToFront:
    def __init__(self, closed: bool = False) -> None:
        self._closed = closed
        self.bring_to_front_calls = 0

    def is_closed(self) -> bool:
        return self._closed

    def bring_to_front(self) -> None:
        self.bring_to_front_calls += 1


def test_drain_js_dialog_brings_the_page_to_front_after_resolving() -> None:
    """The dialog is closed at the CDP level the instant accept()/dismiss() is called, but the
    window sat behind the Studio modal while that happened, and Windows can leave the now-dead
    native dialog's pixels on screen until that window repaints. Activating the tab forces a
    repaint — without minimizing anything — so the human doesn't come back to a stale ghost box.
    """
    sess = RecordingSession(session_id="dialog-drain-bring-to-front")
    dialog = _FakeDialog("alert", "I am a JS Alert")
    page = _FakePageForBringToFront()
    sess._pending_dialog = ("req-1", dialog, "alert", "I am a JS Alert", 0.0, page)
    sess.resolve_js_dialog("req-1", True, "")

    sess._drain_js_dialog_sync()

    assert dialog.accepted_with == [""]
    assert page.bring_to_front_calls == 1


def test_drain_js_dialog_timeout_also_brings_the_page_to_front() -> None:
    from conxa_compile.recorder import session as recorder_session

    sess = RecordingSession(session_id="dialog-timeout-bring-to-front")
    dialog = _FakeDialog("alert", "Forgotten alert")
    page = _FakePageForBringToFront()
    opened_at = time.monotonic() - (recorder_session._JS_DIALOG_TIMEOUT_S + 1)
    sess._pending_dialog = ("req-1", dialog, "alert", "Forgotten alert", opened_at, page)

    sess._drain_js_dialog_sync()

    assert dialog.accepted_with == [""]
    assert page.bring_to_front_calls == 1


def test_drain_js_dialog_ignores_a_stale_answer_for_an_already_resolved_dialog() -> None:
    """A second dialog can only open after the first resolves, but an in-flight Studio answer
    for the first must never be misapplied to whatever dialog is pending now."""
    sess = RecordingSession(session_id="dialog-drain-stale")
    dialog = _FakeDialog("alert", "Second alert")
    sess._pending_dialog = ("req-2", dialog, "alert", "Second alert", 0.0, None)
    sess.resolve_js_dialog("req-1", True, "")  # answer for a dialog that's no longer pending

    sess._drain_js_dialog_sync()

    assert dialog.accepted_with == []
    assert sess._pending_dialog is not None
    assert sess._pending_dialog[0] == "req-2"


def test_drain_js_dialog_logs_a_dropped_answer_instead_of_silently_discarding_it() -> None:
    """Previously the stale-answer branch above just `return`ed with no trace — a human's answer
    to a Studio dialog could vanish with nothing anywhere to explain why the recording seemed to
    hang. Losing the answer is still correct (see the test above); doing it silently was the bug."""
    sess = RecordingSession(session_id="dialog-drain-stale-logged")
    dialog = _FakeDialog("alert", "Second alert")
    sess._pending_dialog = ("req-2", dialog, "alert", "Second alert", 0.0, None)
    sess.resolve_js_dialog("req-1", True, "")

    sess._drain_js_dialog_sync()

    assert any(
        e.startswith("dialog_answer_dropped: expected req-2, got req-1")
        for e in sess.binding_errors
    )


def test_drain_js_dialog_fires_on_js_dialog_resolved_only_after_a_successful_answer() -> None:
    """The Studio waits for this event before releasing its always-on-top pin (see
    RecordWorkflowDialog.tsx's answerJsDialog) — it must only fire once the dialog was actually
    accepted/dismissed, not merely because an answer arrived."""
    sess = RecordingSession(session_id="dialog-drain-resolved-event")
    dialog = _FakeDialog("prompt", "I am a JS Prompt")
    sess._pending_dialog = ("req-1", dialog, "prompt", "I am a JS Prompt", 0.0, None)
    sess.resolve_js_dialog("req-1", True, "Conxa prompt")
    resolved: list[str] = []
    sess.on_js_dialog_resolved = resolved.append

    sess._drain_js_dialog_sync()

    assert resolved == ["req-1"]


def test_drain_js_dialog_notifies_cancelled_when_accept_itself_fails() -> None:
    """If dialog.accept()/dismiss() raises (e.g. the dialog handle went stale), the Studio must
    not be told "resolved" — it would otherwise stay pinned on top forever waiting for a
    confirmation that already happened wrong. Falling back to on_js_dialog_cancelled reuses the
    signal the Studio already understands as "stop waiting, close your modal"."""
    sess = RecordingSession(session_id="dialog-drain-resolve-error")

    class _ExplodingDialog(_FakeDialog):
        def accept(self, prompt_text: str = "") -> None:
            raise RuntimeError("dialog already handled")

    dialog = _ExplodingDialog("prompt", "I am a JS Prompt")
    sess._pending_dialog = ("req-1", dialog, "prompt", "I am a JS Prompt", 0.0, None)
    sess.resolve_js_dialog("req-1", True, "Conxa prompt")
    resolved: list[str] = []
    cancelled: list[str] = []
    sess.on_js_dialog_resolved = resolved.append
    sess.on_js_dialog_cancelled = cancelled.append

    sess._drain_js_dialog_sync()

    assert resolved == []
    assert cancelled == ["req-1"]
    assert any(e.startswith("dialog_resolve_error:") for e in sess.binding_errors)


def test_drain_js_dialog_auto_accepts_past_the_timeout(monkeypatch) -> None:
    """A forgotten Studio modal must not be able to wedge a recording forever — see
    _JS_DIALOG_TIMEOUT_S. The recorder must still get an explicit signal to close its UI."""
    from conxa_compile.recorder import session as recorder_session

    sess = RecordingSession(session_id="dialog-drain-timeout")
    dialog = _FakeDialog("alert", "Forgotten alert")
    opened_at = time.monotonic() - (recorder_session._JS_DIALOG_TIMEOUT_S + 1)
    sess._pending_dialog = ("req-1", dialog, "alert", "Forgotten alert", opened_at, None)
    cancelled: list[str] = []
    sess.on_js_dialog_cancelled = cancelled.append

    sess._drain_js_dialog_sync()

    assert dialog.accepted_with == [""]
    assert sess._pending_dialog is None
    assert cancelled == ["req-1"]
    assert "dialog_timeout_autoaccept" in sess.binding_errors
    payload, _src_page, _src_frame = sess._pending_payloads.get_nowait()
    assert payload["action"]["action"] == "dialog_accept"


class _FakeCdpSession:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append(method)
        return {}


class _FakePageThatHangsOnEvaluate:
    """Stands in for a page whose renderer is blocked by an open native dialog: evaluate()
    never returns (here: raises instead, since a test can't actually hang) — exactly the
    reentrancy deadlock _pump_ping_sync exists to avoid touching in the first place."""

    def evaluate(self, expression: str, arg=None) -> None:
        raise AssertionError(
            "page.evaluate() was called — this is exactly the call that hangs forever "
            "against a dialog-blocked renderer with no timeout available"
        )


def test_pump_ping_uses_the_cdp_session_and_never_touches_evaluate() -> None:
    """The routine path, every pump tick: must go through Page.getNavigationHistory (a
    browser/target-level CDP command immune to a dialog blocking the renderer), never
    page.evaluate() — see _pump_ping_sync's docstring for the deadlock this replaced."""
    sess = RecordingSession(session_id="pump-ping-cdp")
    page = _FakePageThatHangsOnEvaluate()
    cdp = _FakeCdpSession()
    sess._nav_cdp_sessions[id(page)] = cdp

    sess._pump_ping_sync(page)  # would raise via the fake if it fell through to evaluate()

    assert cdp.sent == ["Page.getNavigationHistory"]


def test_pump_ping_falls_back_to_evaluate_only_without_a_cdp_session() -> None:
    """No CDP session yet for this page (creation failed, or it hasn't been registered this
    tick) — the rarer fallback path still exists, and is the only path where evaluate() is
    used at all."""
    sess = RecordingSession(session_id="pump-ping-fallback")

    class _FakePage:
        def __init__(self) -> None:
            self.evaluate_calls = 0

        def evaluate(self, expression: str, arg=None) -> int:
            self.evaluate_calls += 1
            return 0

    page = _FakePage()

    sess._pump_ping_sync(page)

    assert page.evaluate_calls == 1


# The other half of the reentrancy race — a dialog opening on a page between the pump loop's
# top-of-tick _pending_dialog check and this per-page block reaching that same page — lives
# entirely inside _run_sync_recorder's per-tick loop (real Playwright pages, real threading),
# not something this file's fixtures can drive without a live browser. It's covered by the
# guard directly above the bridge-install/pump-ping calls in that loop: skip a page the
# pending dialog names before touching either. See docs/testing/ for the live recording
# repro that exercises this end to end.


# ─── DialogSyncServer integration (session.py side) ────────────────────────────────────────
# A real recording showed alert() succeeding but confirm()/prompt() failing whenever the human
# took more than a few seconds to answer — Chromium apparently has its own patience limit for
# an unacknowledged CDP dialog that no amount of sequencing on the Python side can extend. These
# tests cover the pieces added to route alert/confirm/prompt through DialogSyncServer instead
# (a local HTTP server the page's own overridden window.alert/confirm/prompt talk to via a
# synchronous XHR, so the real native dialog never opens and there's nothing for Chromium to
# grow impatient with). beforeunload still goes through the older CDP path tested above.


class _FakeDialogServer:
    def __init__(self, owns_request_id: str | None = None) -> None:
        self.owns_request_id = owns_request_id
        self.resolve_calls: list[tuple[str, bool, str]] = []

    def resolve(self, request_id: str, accepted: bool, text: str) -> bool:
        self.resolve_calls.append((request_id, accepted, text))
        return request_id == self.owns_request_id


def test_resolve_js_dialog_routes_through_the_dialog_server_first() -> None:
    """The common case: alert/confirm/prompt answers belong to the dialog server, which
    unblocks the page's own synchronous XHR directly — must not also fall through to the
    older CDP-path queue."""
    sess = RecordingSession(session_id="resolve-routes-to-server")
    fake = _FakeDialogServer(owns_request_id="req-1")
    sess._dialog_server = fake

    sess.resolve_js_dialog("req-1", True, "conxa")

    assert fake.resolve_calls == [("req-1", True, "conxa")]
    assert sess._dialog_results.empty()


def test_resolve_js_dialog_falls_back_to_cdp_queue_for_a_request_the_server_does_not_own() -> None:
    """beforeunload answers don't belong to the dialog server (it only intercepts
    alert/confirm/prompt) — must still reach the older _dialog_results queue that
    _drain_js_dialog_sync drains."""
    sess = RecordingSession(session_id="resolve-falls-back-to-cdp")
    fake = _FakeDialogServer(owns_request_id="some-other-request")
    sess._dialog_server = fake

    sess.resolve_js_dialog("req-1", True, "")

    assert fake.resolve_calls == [("req-1", True, "")]
    request_id, accepted, text = sess._dialog_results.get_nowait()
    assert (request_id, accepted, text) == ("req-1", True, "")


class _FakePageForHref:
    def __init__(self, url: str, closed: bool = False) -> None:
        self.url = url
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


def test_page_by_href_finds_the_matching_open_page() -> None:
    sess = RecordingSession(session_id="page-by-href-match")
    wanted = _FakePageForHref("https://example.com/checkout")
    other = _FakePageForHref("https://example.com/cart")

    class _Ctx:
        pages = [other, wanted]

    sess._context = _Ctx()

    found = sess._page_by_href_sync("https://example.com/checkout")

    assert found is wanted


def test_page_by_href_falls_back_to_active_page_when_nothing_matches() -> None:
    """A page can navigate between the dialog-server call and this lookup — matching this
    file's existing tolerance for similar edge cases (e.g. nav-history's own docstring)."""
    sess = RecordingSession(session_id="page-by-href-fallback")
    only_page = _FakePageForHref("https://example.com/somewhere-else")

    class _Ctx:
        pages = [only_page]

    sess._context = _Ctx()
    sess._page = only_page

    found = sess._page_by_href_sync("https://example.com/checkout")

    assert found is only_page


def test_dialog_server_answered_queues_a_dialog_accept_event_and_resolves_the_studio() -> None:
    sess = RecordingSession(session_id="dialog-server-answered")
    resolved: list[str] = []
    sess.on_js_dialog_resolved = resolved.append

    sess._on_dialog_server_answered("req-1", "prompt", "Enter name", True, "conxa", "https://x.test")

    assert resolved == ["req-1"]
    accepted, dialog_type, message, text, href = sess._dialog_server_events.get_nowait()
    assert (accepted, dialog_type, message, text, href) == (True, "prompt", "Enter name", "conxa", "https://x.test")


def test_dialog_server_timed_out_queues_a_forced_accept_and_logs_it() -> None:
    """Mirrors the old CDP timeout path exactly: forced accept with empty text (not a
    dismiss), so a downstream step depending on "something" being entered doesn't also have
    to handle a distinct forgotten-dialog-dismiss outcome."""
    sess = RecordingSession(session_id="dialog-server-timeout")
    cancelled: list[str] = []
    sess.on_js_dialog_cancelled = cancelled.append

    sess._on_dialog_server_timed_out("req-1", "confirm", "Are you sure?", "https://x.test")

    assert cancelled == ["req-1"]
    assert "dialog_timeout_autoaccept" in sess.binding_errors
    accepted, dialog_type, message, text, href = sess._dialog_server_events.get_nowait()
    assert (accepted, dialog_type, message, text, href) == (True, "confirm", "Are you sure?", "", "https://x.test")


def test_drain_dialog_server_events_records_a_dialog_accept_step() -> None:
    sess = RecordingSession(session_id="drain-dialog-server-events")
    page = _FakePageForHref("https://example.com/checkout")

    class _Ctx:
        pages = [page]

    sess._context = _Ctx()
    sess._dialog_server_events.put((True, "prompt", "Enter name", "conxa", "https://example.com/checkout"))

    sess._drain_dialog_server_events_sync()

    payload, src_page, _src_frame = sess._pending_payloads.get_nowait()
    assert payload["action"]["action"] == "dialog_accept"
    assert json.loads(payload["action"]["value"]) == {
        "type": "prompt",
        "message": "Enter name",
        "value": "conxa",
    }
    assert src_page is page


def _obs(final_url="https://app.test/home", password_box=False, markers=None):
    return {"final_url": final_url, "password_box": password_box, "markers": markers or [], "responses": [], "cookie_names": []}


def _stub_probe(monkeypatch, observation=None, raises=False):
    class _Pw:
        def __enter__(self):
            if raises:
                raise RuntimeError("driver died")
            return self

        def __exit__(self, *a):
            return False

        chromium = type("C", (), {"launch": staticmethod(lambda headless=True: type("B", (), {
            "new_context": lambda self, storage_state=None: type("X", (), {"new_page": lambda self: None})(),
            "close": lambda self: None,
        })())})()

    monkeypatch.setattr(recorder_session, "sync_playwright", lambda: _Pw())
    monkeypatch.setattr(recorder_session, "_navigate_and_observe_sync", lambda *a, **k: observation)


def _probe_app(**kw):
    return GroupApp(id="a", name="App", login_url="https://accounts.test/", storage_state_path="x.json", **kw)


def test_probe_driver_failure_is_unverified_not_ready(monkeypatch) -> None:
    _stub_probe(monkeypatch, raises=True)
    assert recorder_session._probe_app_session_sync(_probe_app()) == "unverified"


def test_probe_navigation_that_never_completed_is_unverified(monkeypatch) -> None:
    _stub_probe(monkeypatch, _obs(final_url="about:blank"))
    assert recorder_session._probe_app_session_sync(_probe_app()) == "unverified"


def test_probe_signin_page_on_a_different_path_is_expired(monkeypatch) -> None:
    """The login URL's prefix didn't match where the bounce landed, which used to read as ready."""
    _stub_probe(monkeypatch, _obs(final_url="https://accounts.test/v3/signin/identifier"))
    assert recorder_session._probe_app_session_sync(_probe_app()) == "expired"


def test_probe_signin_button_or_password_box_is_expired(monkeypatch) -> None:
    _stub_probe(monkeypatch, _obs(markers=[{"role": "a", "name": "Sign in"}]))
    assert recorder_session._probe_app_session_sync(_probe_app()) == "expired"
    _stub_probe(monkeypatch, _obs(password_box=True))
    assert recorder_session._probe_app_session_sync(_probe_app()) == "expired"


def test_probe_leaving_login_with_no_signin_evidence_is_ready(monkeypatch) -> None:
    _stub_probe(monkeypatch, _obs(final_url="https://myaccount.test/"))
    assert recorder_session._probe_app_session_sync(_probe_app()) == "ready"


def test_probe_unmatched_page_with_success_url_is_unverified(monkeypatch) -> None:
    _stub_probe(monkeypatch, _obs(final_url="https://app.test/onboarding"))
    assert recorder_session._probe_app_session_sync(_probe_app(success_url="https://app.test/dashboard")) == "unverified"
    _stub_probe(monkeypatch, _obs(final_url="https://app.test/dashboard"))
    assert recorder_session._probe_app_session_sync(_probe_app(success_url="https://app.test/dashboard")) == "ready"
