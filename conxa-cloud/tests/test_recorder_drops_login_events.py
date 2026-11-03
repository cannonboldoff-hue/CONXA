"""Events captured on a login page are dropped from a recording, never written."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conxa_compile.auth_learning import looks_like_session_login
from conxa_compile.compiler.build import _read_login_drops_sidecar
from conxa_compile.recorder.frame_utils import LoginFlowTracker, looks_like_login_page
from conxa_compile.recorder.session import RecordingSession

# One fixture, two languages: runtime/test/unit/test_login_signals.js runs the same file.
CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "runtime" / "test" / "fixtures" / "login_path_cases.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_recorder_drop_matches_fixture(case: dict) -> None:
    assert LoginFlowTracker().is_login_step(case["url"]) is case["recorder_drop"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_session_login_matches_fixture(case: dict) -> None:
    assert looks_like_session_login({"final_url": case["url"]}) is case["session_login"]


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


def test_sso_hops_are_dropped_only_while_signing_in() -> None:
    t = LoginFlowTracker()
    assert t.is_login_step("https://app.example.com/login")
    assert t.is_login_step("https://idp.example.com/oauth2/authorize")  # a hop of this sign-in
    assert t.is_login_step("https://idp.example.com/sso/continue")
    assert not t.is_login_step("https://app.example.com/dashboard")  # signed in: back to recording
    assert not t.is_login_step("https://app.example.com/settings/auth")  # no longer a hop


def test_a_password_box_marks_the_sign_in_even_on_an_ordinary_address() -> None:
    t = LoginFlowTracker()
    t.arm()
    assert t.is_login_step("https://idp.example.com/oauth2/authorize")


def _bare_session(auth_mode: bool) -> RecordingSession:
    sess = object.__new__(RecordingSession)  # no browser: the drop happens before any attribute is read
    sess.auth_mode = auth_mode
    sess._login_flow = LoginFlowTracker()
    sess._login_drops = []
    return sess


def test_login_page_payload_is_dropped() -> None:
    # Would raise AttributeError (no data_root) if it got past the drop.
    _bare_session(False)._consume_payload_sync({"page": {"url": "https://app.example.com/login"}})


def test_app_page_payload_is_not_dropped() -> None:
    with pytest.raises(AttributeError):
        _bare_session(False)._consume_payload_sync({"page": {"url": "https://app.example.com/dashboard"}})


def test_in_app_auth_settings_payload_is_not_dropped() -> None:
    with pytest.raises(AttributeError):
        _bare_session(False)._consume_payload_sync({"page": {"url": "https://app.example.com/settings/auth"}})


def test_a_drop_is_ledgered_without_any_typed_value() -> None:
    sess = _bare_session(False)
    sess._consume_payload_sync({
        "page": {"url": "https://app.example.com/login"},
        "action": {"action": "type", "value": "hunter2"},
    })
    assert sess._login_drops == [{"pass": "login_page", "action": "type"}]


def test_sidecar_reaches_the_compile_ledger_without_values(tmp_path: Path) -> None:
    (tmp_path / "login_drops.json").write_text(
        json.dumps([{"pass": "login_page", "action": "type", "value": "hunter2"}, "junk"]), encoding="utf-8")
    assert _read_login_drops_sidecar(tmp_path) == [{"pass": "login_page", "action": "type"}]


def test_missing_or_malformed_sidecar_is_no_drops(tmp_path: Path) -> None:
    assert _read_login_drops_sidecar(tmp_path) == []
    (tmp_path / "login_drops.json").write_text("{not json", encoding="utf-8")
    assert _read_login_drops_sidecar(tmp_path) == []
