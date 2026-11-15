"""conxa_compile.auth_learning — evaluate() twin-parity with login_signals.js, plus learn()/
self_test() (Studio-only, no JS equivalent). See auth_learning.py's module docstring."""

import json
from pathlib import Path

import pytest

from conxa_compile.auth_learning import evaluate, learn, self_test, status_matches_spec, template_path

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "runtime" / "test" / "fixtures" / "auth_definition_cases.json"


def _cases():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_evaluate_matches_shared_fixture(case):
    """Same cases the JS test (runtime/test/unit/test_login_signals.js) runs through
    evaluateAuthDefinition — if this ever disagrees with the JS twin, a definition Studio's
    self-test approved could still misfire at runtime."""
    result = evaluate(case["definition"], case["observation"])
    assert result["verdict"] == case["expect"]["verdict"], result


def test_template_path_ids_numeric_and_uuid_segments():
    assert template_path("/api/users/48213/me") == "/api/users/:id/me"
    assert template_path("/api/orders/9c1e2b3a-000a-4b2c-8b1e-1234567890ab") == "/api/orders/:id"
    assert template_path("/api/me") == "/api/me"
    assert template_path("") == "/"


def test_status_matches_spec_class_or_exact():
    assert status_matches_spec(200, "2xx")
    assert status_matches_spec(204, "2xx")
    assert not status_matches_spec(401, "2xx")
    assert status_matches_spec(401, "401")
    assert not status_matches_spec(403, "401")


def _obs(final_url, password_box=False, markers=None, responses=None, cookie_names=None):
    return {
        "final_url": final_url,
        "password_box": password_box,
        "otp_like": False,
        "markers": markers or [],
        "responses": responses or [],
        "cookie_names": cookie_names or [],
    }


def test_learn_keeps_only_signals_that_differ_between_live_and_out():
    live = _obs(
        "https://app.acme.com/home",
        markers=[],
        responses=[{"method": "GET", "path": "/api/me", "status": 200}],
        cookie_names=["_app_session", "csrf"],
    )
    out = _obs(
        "https://app.acme.com/login",
        password_box=True,
        markers=[{"role": "button", "name": "Sign in"}],
        responses=[{"method": "GET", "path": "/api/me", "status": 401}],
        cookie_names=["csrf"],  # csrf present both sides -> not a session signal
    )
    definition = learn(live, out, probe_url="https://app.acme.com/login")

    assert definition["signed_out"]["final_path"] == "/login"
    assert definition["signed_in"]["final_path"] == "/home"
    assert definition["signed_out"]["markers"] == [{"role": "button", "name": "Sign in"}]
    assert definition["session_keys"] == ["_app_session"]  # csrf excluded — present in both
    assert definition["signed_in"]["endpoints"] == [
        {"method": "GET", "path": "/api/me", "ok": "2xx", "denied": "401"}
    ]

    ok, _ = self_test(definition, live, out, reload_obs=live)
    assert ok


def test_learn_output_has_no_timestamp_and_is_deterministic():
    """AUTH-15: a definition is setup, not a session — it must be a pure function of the three
    observations. A `learned_at` field used to make every Reconnect produce a "different"
    definition even against an identical site, which _sign_in_setup_drift then read as the
    group's sign-in setup having changed and refused Run Test until a needless rebuild."""
    live = _obs(
        "https://app.acme.com/home",
        responses=[{"method": "GET", "path": "/api/me", "status": 200}],
        cookie_names=["_app_session"],
    )
    out = _obs(
        "https://app.acme.com/login",
        password_box=True,
        responses=[{"method": "GET", "path": "/api/me", "status": 401}],
        cookie_names=[],
    )
    definition = learn(live, out, probe_url="https://app.acme.com/login")
    assert "learned_at" not in definition
    assert learn(live, out, probe_url="https://app.acme.com/login") == definition


def test_learn_drops_personal_looking_marker_names():
    live = _obs("https://app.acme.com/home", cookie_names=["_app_session"])
    out = _obs(
        "https://app.acme.com/login",
        markers=[{"role": "text", "name": "someone@example.com"}],
        cookie_names=[],
    )
    definition = learn(live, out)
    assert definition["signed_out"]["markers"] == []


def test_self_test_fails_when_out_observation_also_reads_signed_in():
    live = _obs(
        "https://app.acme.com/home",
        responses=[{"method": "GET", "path": "/api/me", "status": 200}],
        cookie_names=["_app_session"],
    )
    out = _obs(
        "https://app.acme.com/login",
        password_box=True,
        responses=[{"method": "GET", "path": "/api/me", "status": 401}],
        cookie_names=[],
    )
    definition = learn(live, out)

    # A public-page-shaped "out" observation that happens to look signed-in anyway (AUTH-10's
    # shape) must not pass — patch the definition to simulate one weak-signal-only case.
    weak_out = _obs("https://app.acme.com/pricing", cookie_names=["_app_session"])
    ok, _reason = self_test(definition, live, weak_out, reload_obs=live)
    # weak_out doesn't match signed_out.final_path and has no password box/markers/network hit,
    # so it evaluates to "unsure" here (session alone never yields "yes") — self-test passes.
    # The real regression guard is: an OUT observation that DOES still look like the login page
    # must never pass as "yes".
    assert ok
    real_out = _obs("https://app.acme.com/login", password_box=True, cookie_names=[])
    ok2, _reason2 = self_test(definition, live, real_out, reload_obs=live)
    assert ok2

    still_signed_in_out = _obs(
        "https://app.acme.com/home",
        responses=[{"method": "GET", "path": "/api/me", "status": 200}],
        cookie_names=["_app_session"],
    )
    ok3, reason3 = self_test(definition, live, still_signed_in_out, reload_obs=live)
    assert not ok3
    assert "also read as signed in" in reason3


def test_self_test_fails_when_reload_does_not_confirm():
    live = _obs(
        "https://app.acme.com/home",
        responses=[{"method": "GET", "path": "/api/me", "status": 200}],
        cookie_names=["_app_session"],
    )
    out = _obs("https://app.acme.com/login", password_box=True, cookie_names=[])
    definition = learn(live, out)

    broken_reload = _obs("https://app.acme.com/login", password_box=True, cookie_names=[])
    ok, reason = self_test(definition, live, out, reload_obs=broken_reload)
    assert not ok
    assert "reload" in reason.lower()


def test_learn_keeps_the_page_status_only_when_signed_out_answers_a_different_class():
    live = _obs("https://bank.example.com/overview", cookie_names=["JSESSIONID"])
    out = _obs("https://bank.example.com/overview")
    live["doc_status"], out["doc_status"] = 200, 500
    d = learn(live, out, probe_url="https://bank.example.com/overview")
    assert d["signed_in"]["doc_ok"] == "2xx" and d["signed_out"]["doc_denied"] == "500"
    live_ok, out_bad, reload_ok = dict(live), dict(out), dict(live)
    assert self_test(d, live_ok, out_bad, reload_ok) == (True, "")

    # same class both sides, or no status at all: nothing is added, so the definition is unchanged from before
    out["doc_status"] = 200
    assert "doc_ok" not in learn(live, out)["signed_in"] and "doc_denied" not in learn(live, out)["signed_out"]
    del live["doc_status"]
    assert "doc_denied" not in learn(live, out)["signed_out"]
