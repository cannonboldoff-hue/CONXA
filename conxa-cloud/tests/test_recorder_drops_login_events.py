"""Events captured on a login page are dropped from a recording, never written."""

from __future__ import annotations

import pytest

from conxa_compile.recorder.frame_utils import looks_like_login_page
from conxa_compile.recorder.session import RecordingSession


@pytest.mark.parametrize("url", [
    "https://app.example.com/login",
    "https://accounts.google.com/ServiceLogin?x=1",
    "https://app.example.com/auth/",
    "https://app.example.com/session-expired",
])
def test_login_paths_detected(url: str) -> None:
    assert looks_like_login_page(url)


@pytest.mark.parametrize("url", ["https://app.example.com/", "https://app.example.com/dashboard", "about:blank"])
def test_app_pages_not_login(url: str) -> None:
    assert not looks_like_login_page(url)


def _bare_session(auth_mode: bool) -> RecordingSession:
    sess = object.__new__(RecordingSession)  # no browser: the drop happens before any attribute is read
    sess.auth_mode = auth_mode
    return sess


def test_login_page_payload_is_dropped() -> None:
    # Would raise AttributeError (no data_root) if it got past the drop.
    _bare_session(False)._consume_payload_sync({"page": {"url": "https://app.example.com/login"}})


def test_app_page_payload_is_not_dropped() -> None:
    with pytest.raises(AttributeError):
        _bare_session(False)._consume_payload_sync({"page": {"url": "https://app.example.com/dashboard"}})
