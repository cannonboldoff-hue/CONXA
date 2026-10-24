"""group_auth_status must derive readiness from stored fields alone — no
browser probe on the read path. See FIX.md: a probe used to run on every
Group Page open and could wedge the whole backend."""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

from conxa_core.models.workflow import GroupApp  # noqa: E402
from handlers.groups import _detect_warning, group_auth_status  # noqa: E402


def _group(*apps: GroupApp) -> SimpleNamespace:
    return SimpleNamespace(id="grp", apps=list(apps))


def test_missing_app_has_no_captured_at() -> None:
    group = _group(GroupApp(id="a", name="A", login_url="https://x.test/login"))

    status = group_auth_status(group)

    assert status["apps"][0]["state"] == "missing"
    assert status["ready"] is False
    assert status["first_missing_app_id"] == "a"


def test_captured_app_with_no_error_is_ready() -> None:
    group = _group(
        GroupApp(id="a", name="A", login_url="https://x.test/login", captured_at=1.0, storage_state_path="s.json")
    )

    status = group_auth_status(group)

    assert status["apps"][0]["state"] == "ready"
    assert status["ready"] is True
    assert status["apps_authenticated"] == 1


def test_captured_app_with_last_error_is_expired() -> None:
    """This is how the recording gate's bounded probe (check_app_session_sync,
    called from cmd_start_recording) now surfaces a stale session — by writing
    last_error — rather than group_auth_status launching its own browser."""
    group = _group(
        GroupApp(
            id="a",
            name="A",
            login_url="https://x.test/login",
            captured_at=1.0,
            storage_state_path="s.json",
            last_error="Session expired — sign in again.",
        )
    )

    status = group_auth_status(group)

    assert status["apps"][0]["state"] == "expired"
    assert status["ready"] is False
    assert status["first_missing_app_id"] == "a"


def test_ready_app_with_no_checked_at_is_unverified() -> None:
    """A session file existing (state == "ready") is not the same claim as "a probe
    recently confirmed it works" — see group_auth_status's docstring. Never probed
    since capture must read as unverified, not silently trusted."""
    group = _group(
        GroupApp(id="a", name="A", login_url="https://x.test/login", captured_at=1.0, storage_state_path="s.json")
    )

    status = group_auth_status(group)

    assert status["apps"][0]["state"] == "ready"
    assert status["apps"][0]["verified"] is False


def test_ready_app_with_fresh_checked_at_is_verified() -> None:
    group = _group(
        GroupApp(
            id="a", name="A", login_url="https://x.test/login",
            captured_at=1.0, storage_state_path="s.json", checked_at=time.time(),
        )
    )

    status = group_auth_status(group)

    assert status["apps"][0]["verified"] is True


def test_ready_app_with_stale_checked_at_is_unverified() -> None:
    group = _group(
        GroupApp(
            id="a", name="A", login_url="https://x.test/login",
            captured_at=1.0, storage_state_path="s.json", checked_at=time.time() - 3600,
        )
    )

    status = group_auth_status(group)

    assert status["apps"][0]["verified"] is False


def test_detect_warning_is_empty_when_the_login_reached_success_url() -> None:
    assert _detect_warning("https://drive.google.com/{}", True, False) == ""


def test_detect_warning_names_success_url_when_login_was_closed_by_hand() -> None:
    warning = _detect_warning("https://drive.google.com/{}", False, False)

    assert "https://drive.google.com/{}" in warning


def test_detect_warning_flags_a_missing_success_url() -> None:
    assert "No success URL" in _detect_warning("", False, False)
    assert "No success URL" in _detect_warning("", True, False)


def test_detect_warning_is_empty_when_a_definition_was_learned() -> None:
    """A learned auth definition detects sign-in on its own — nothing to warn about, no matter
    what success_url says or whether the login self-detected it (P0: Application Authentication
    Recording)."""
    assert _detect_warning("", False, True) == ""
    assert _detect_warning("https://drive.google.com/{}", False, True) == ""


def test_detect_warning_is_surfaced_but_never_changes_state() -> None:
    group = _group(
        GroupApp(id="a", name="A", login_url="https://x.test/login", captured_at=1.0,
                 storage_state_path="s.json", detect_warning="heads up")
    )

    app = group_auth_status(group)["apps"][0]

    assert app["detect_warning"] == "heads up"
    assert app["state"] == "ready"  # advisory only: the saved session is still valid


def test_ready_app_without_a_recent_probe_is_not_counted_verified() -> None:
    """A saved session file is "ready" but only a live probe makes it "verified" — the count the
    UI uses to decide whether anything may show green."""
    import time

    from conxa_core.models.workflow import GroupApp, WorkflowGroup

    now = time.time()
    def _app(i, checked_at):
        return GroupApp(id=i, name=i, login_url="https://x.test/login", captured_at=now, storage_state_path="s.json", checked_at=checked_at)

    group = WorkflowGroup(id="g", slug="g", name="G", apps=[_app("a", now), _app("b", None)])
    status = group_auth_status(group)
    assert status["apps_authenticated"] == 2
    assert status["apps_verified"] == 1
