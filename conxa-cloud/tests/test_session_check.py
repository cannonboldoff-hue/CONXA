"""conxa_compile.auth_learning.classify_session — twin-parity with login_signals.js::classifySession.

One rule decides "is this saved session still signed in?" for Build Studio's Check now / Check all /
recording gate and for the runtime's pre-flight. Both languages run the same fixture."""

import json
from pathlib import Path

import pytest

from conxa_compile.auth_learning import classify_session, matches_success_url

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "runtime" / "test" / "fixtures" / "session_check_cases.json"


def _cases():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_classify_session_matches_shared_fixture(case):
    """If this ever disagrees with the JS twin (runtime/test/unit/test_login_signals.js), Studio's
    badge and a run's pre-flight would answer differently for the same page."""
    state = classify_session(
        case["observation"], case["baseline"], case["app"], no_baseline_ok=bool(case.get("noBaselineOk")),
    )
    assert state == case["expect"]["state"]


def test_matches_success_url_wildcard_and_prefix():
    assert matches_success_url("https://drive.google.com/drive/my-drive", "https://drive.google.com/{}")
    assert not matches_success_url("https://accounts.google.com/", "https://drive.google.com/{}")
    assert not matches_success_url("https://a.test/", "")
    assert not matches_success_url("https://a.test/", "{}")
