"""Recording inside Build Studio's browser panel: host_view.py (the control channel + the run's
storage) and the embedded branches of RecordingSession. Real Electron is exercised by the Gate B
harness (docs/archive/spikes/in-app-recording-2026-10.md); these pin the bookkeeping."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from conxa_compile.recorder import host_view
from conxa_compile.recorder import session as recorder_session
from conxa_compile.recorder.session import RecordingSession


@pytest.fixture
def control_server(monkeypatch):
    calls: list[dict] = []
    replies: dict[str, tuple[int, dict]] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            status, reply = replies.get(body["op"], (200, {"ok": True}))
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CONXA_HOST_CONTROL_URL", f"http://127.0.0.1:{srv.server_address[1]}/control?token=t")
    monkeypatch.setenv("CONXA_HOST_BROWSER_CDP", "http://127.0.0.1:9222")
    yield calls, replies
    srv.shutdown()


def test_control_posts_the_op_and_surfaces_the_panels_error(control_server) -> None:
    calls, replies = control_server
    replies["targets"] = (200, {"targets": [{"tabId": "a", "targetId": "T1", "opener": None}]})
    assert host_view.run_target_ids("run-1") == {"T1"}
    assert calls[-1] == {"op": "targets", "runId": "run-1"}
    replies["scale"] = (500, {"ok": False, "error": "browser_panel: no run x"})
    with pytest.raises(RuntimeError, match="no run x"):
        host_view.control("scale", runId="x")


def test_release_never_raises_when_the_panel_is_gone(monkeypatch) -> None:
    monkeypatch.setenv("CONXA_HOST_CONTROL_URL", "http://127.0.0.1:9/control")
    host_view.release("run-1")


def test_page_ws_url_points_at_the_page_target_on_the_cdp_port(control_server) -> None:
    assert host_view.page_ws_url("ABC") == "ws://127.0.0.1:9222/devtools/page/ABC"


class _Cdp:
    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.sent: list[tuple] = []
        self.detached = False

    def send(self, method, params=None):
        self.sent.append((method, params))
        return self.replies.get(method, {})

    def detach(self):
        self.detached = True


class _Context:
    def __init__(self, cdp: _Cdp) -> None:
        self.cdp = cdp

    def new_cdp_session(self, _page):
        return self.cdp


def test_page_cookies_map_cdp_cookies_to_storage_state_shape() -> None:
    cdp = _Cdp({"Network.getAllCookies": {"cookies": [
        {"name": "sid", "value": "1", "domain": "a.test", "path": "/", "expires": 99.0, "session": False,
         "httpOnly": True, "secure": True, "sameSite": "Strict"},
        {"name": "tmp", "value": "2", "domain": "a.test", "path": "/", "expires": -1, "session": True},
    ]}})
    cookies = host_view.page_cookies(_Context(cdp), object())
    assert cookies[0] == {"name": "sid", "value": "1", "domain": "a.test", "path": "/", "expires": 99.0,
                          "httpOnly": True, "secure": True, "sameSite": "Strict"}
    assert cookies[1]["expires"] == -1 and cookies[1]["sameSite"] == "Lax"
    assert cdp.detached


def test_set_page_cookies_drops_session_expiry() -> None:
    cdp = _Cdp({})
    host_view.set_page_cookies(_Context(cdp), object(), [
        {"name": "a", "value": "1", "domain": "a.test", "path": "/", "expires": -1},
        {"name": "b", "value": "2", "domain": "a.test", "path": "/", "expires": 50},
    ])
    method, params = cdp.sent[0]
    assert method == "Network.setCookies"
    assert "expires" not in params["cookies"][0] and params["cookies"][1]["expires"] == 50


class _Page:
    def __init__(self, origin: str, storage: list[dict], url: str = "") -> None:
        self.origin, self.storage, self.url = origin, storage, url
        self.closed = False

    def evaluate(self, script, *_args):
        if "location.origin" in script:
            return self.origin
        return self.storage

    def is_closed(self):
        return self.closed


def test_capture_state_reads_open_pages_and_keeps_closed_origins_from_before() -> None:
    cdp = _Cdp({"Network.getAllCookies": {"cookies": []}})
    pages = [_Page("https://app.test", [{"name": "tok", "value": "new"}]), _Page("null", [])]
    previous = {"origins": [
        {"origin": "https://app.test", "localStorage": [{"name": "tok", "value": "old"}]},
        {"origin": "https://sso.test", "localStorage": [{"name": "s", "value": "1"}]},
    ]}
    state = host_view.capture_state(_Context(cdp), pages, previous)
    assert state["origins"] == [
        {"origin": "https://app.test", "localStorage": [{"name": "tok", "value": "new"}]},
        {"origin": "https://sso.test", "localStorage": [{"name": "s", "value": "1"}]},
    ]
    with pytest.raises(RuntimeError):
        host_view.capture_state(_Context(cdp), [], previous)


def test_find_page_by_marker_waits_on_the_context() -> None:
    marker = "about:blank?conxa_run=r&conxa_tab=t"

    class Ctx:
        def __init__(self):
            self.pages = [_Page("", [], url="file:///studio")]
            self.waits = 0

        def wait_for_event(self, _name, timeout):
            self.waits += 1
            if self.waits == 2:
                self.pages.append(_Page("", [], url=marker))
            raise TimeoutError

    ctx = Ctx()
    assert host_view.find_page_by_marker(ctx, marker).url == marker
    assert ctx.waits == 2


def test_only_the_runs_own_pages_are_recorded(monkeypatch) -> None:
    sess = RecordingSession(session_id="run-1", embedded=True)
    studio, ours, other = _Page("", []), _Page("", []), _Page("", [])
    targets = {id(studio): "STUDIO", id(ours): "OURS", id(other): "OTHER"}
    monkeypatch.setattr(host_view, "run_target_ids", lambda _rid: {"OURS"})
    looked_up: list[int] = []

    def target_id(_ctx, page):
        looked_up.append(id(page))
        return targets[id(page)]

    monkeypatch.setattr(host_view, "target_id", target_id)
    adopted: list[int] = []
    monkeypatch.setattr(sess, "_adopt_run_page_sync", lambda page: (adopted.append(id(page)), sess._run_page_ids.add(id(page))))
    assert sess._classify_pages_sync([studio, ours, other]) == [ours]
    assert sess._classify_pages_sync([studio, ours, other]) == [ours]
    assert adopted == [id(ours)]
    assert len(looked_up) == 3, "each page is looked up once, then remembered"


def test_a_page_whose_lookup_fails_is_retried(monkeypatch) -> None:
    sess = RecordingSession(session_id="run-1", embedded=True)
    page = _Page("", [])
    monkeypatch.setattr(host_view, "run_target_ids", lambda _rid: {"T"})
    attempts = iter([RuntimeError("not attached yet"), "T"])

    def target_id(_ctx, _page):
        nxt = next(attempts)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(host_view, "target_id", target_id)
    monkeypatch.setattr(sess, "_adopt_run_page_sync", lambda p: sess._run_page_ids.add(id(p)))
    assert sess._classify_pages_sync([page]) == []
    assert sess._classify_pages_sync([page]) == [page]


def test_closing_the_last_run_page_ends_the_recording() -> None:
    sess = RecordingSession(session_id="run-1", embedded=True)
    sess._run_pages_open = {1, 2}
    sess.browser_open = True
    sess._on_run_page_closed(1)
    assert not sess._stop_requested.is_set()
    sess._on_run_page_closed(2)
    assert sess._stop_requested.is_set() and sess.ended_by_user and not sess.browser_open


def test_embedded_finalize_writes_the_videos_json_contract(tmp_path) -> None:
    sess = RecordingSession(session_id="s1", data_root=tmp_path, embedded=True)
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    class Rec:
        def __init__(self, name, start):
            self.out_path = session_dir / name
            self.start_wall_ms = start
            self.stopped = False

        def stop(self):
            self.stopped = True
            self.out_path.write_bytes(b"webm")

    first, popup = Rec("recording.webm", 1000), Rec("recording-tab_1.webm", 5000)
    sess._screencasts = {11: first, 22: popup}
    sess._tab_ids = {11: "tab_0", 22: "tab_1"}
    sess._finalize_video_file_sync()
    assert first.stopped and popup.stopped
    assert json.loads((session_dir / "videos.json").read_text()) == {
        "tab_0": {"file": "recording.webm", "start_wall_ms": 1000},
        "tab_1": {"file": "recording-tab_1.webm", "start_wall_ms": 5000},
    }


def test_registry_uses_the_panel_only_when_enabled_offered_and_wanted(monkeypatch) -> None:
    reg = recorder_session.SessionRegistry()
    monkeypatch.setenv("CONXA_HOST_BROWSER_CDP", "http://127.0.0.1:1")
    monkeypatch.setenv("CONXA_HOST_CONTROL_URL", "http://127.0.0.1:2/control")
    monkeypatch.setattr(recorder_session.settings, "record_in_panel", False)
    assert reg.create().embedded is False
    monkeypatch.setattr(recorder_session.settings, "record_in_panel", True)
    assert reg.create().embedded is True
    assert reg.create(auth_mode=True, label="Billing").embedded is True
    assert reg.create(in_panel=False).embedded is False, "a caller with nowhere to draw the view"
    monkeypatch.delenv("CONXA_HOST_CONTROL_URL")
    assert reg.create().embedded is False


class _ScriptedPage(_Page):
    def __init__(self) -> None:
        super().__init__("", [])
        self.calls: list[str] = []

    def on(self, event, _fn):
        self.calls.append(f"on:{event}")

    def expose_binding(self, *_a):
        self.calls.append("expose_binding")

    def add_init_script(self, *_a):
        self.calls.append("add_init_script")


def test_a_sign_in_page_gets_no_bridge_only_membership_and_close() -> None:
    sess = RecordingSession(session_id="s", embedded=True, auth_mode=True)
    page = _ScriptedPage()
    sess._adopt_run_page_sync(page)
    assert page.calls == ["on:close"]
    assert id(page) in sess._run_pages_open


def test_sign_in_live_check_runs_in_a_background_panel_tab(control_server, monkeypatch) -> None:
    calls, replies = control_server
    replies["new_tab"] = (200, {"markerUrl": "about:blank?conxa_run=s&conxa_tab=t2", "tabId": "t2"})
    sess = RecordingSession(session_id="s", embedded=True, auth_mode=True)
    sess._context = object()
    probe = object()
    monkeypatch.setattr(host_view, "find_page_by_marker", lambda _ctx, marker: probe if marker.endswith("t2") else None)
    monkeypatch.setattr(recorder_session, "_navigate_and_observe_sync",
                        lambda page, _ctx, url: {"final_url": url, "cookie_names": ["from-default-session"]})
    monkeypatch.setattr(host_view, "page_cookies", lambda _ctx, page: [{"name": "sid"}] if page is probe else [])
    obs = sess._embedded_live_observation_sync("https://app.test/login")
    assert obs == {"final_url": "https://app.test/login", "cookie_names": ["sid"]}
    assert calls[0] == {"op": "new_tab", "runId": "s", "label": "Checking sign-in", "focus": False}
    assert calls[-1] == {"op": "close_view", "runId": "s", "tabId": "t2"}
