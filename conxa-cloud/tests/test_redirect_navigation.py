"""A navigation an action CAUSED (click Submit -> app redirects to /orders/847291?token=...) is that
action's effect, not a workflow step: it must never compile to a hardcoded `navigate`.

Covers the recorder's cause classification (recorder/session.py), the compiler fold
(compiler/navigation_effects.py) and the advisory post-condition it leaves on the click.
"""
from __future__ import annotations

from unittest.mock import patch

from conxa_core.config import settings

import json
import time
from types import SimpleNamespace

from conxa_compile.compiler.navigation_effects import fold_action_caused_navigations
from conxa_compile.recorder.session import RecordingSession
from tests.test_date_format import _compile_steps
from tests.test_phases import _minimal_click_event

FORM = "https://app.com/form"
ORDER = "https://app.com/orders/847291?token=abc123"
ORDER_PATTERN = r"^https://app\.com/orders/[^/?#]+"


# ---------------------------------------------------------------- recorder classification

class _Cdp:
    def __init__(self, entries: list[str], index: int) -> None:
        self._hist = {"currentIndex": index, "entries": [{"url": u} for u in entries]}

    def send(self, method: str):
        return self._hist


def _drain(rs: RecordingSession, page, before: list[str], after: list[str]) -> list[dict]:
    rs._nav_cdp_sessions[id(page)] = _Cdp(after, len(after) - 1)
    rs._nav_history_state[id(page)] = {"index": len(before) - 1, "entries": before}
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    out = []
    while not rs._pending_payloads.empty():
        payload, _, _ = rs._pending_payloads.get_nowait()
        out.append({"kind": payload["action"]["action"], **json.loads(payload["action"]["value"])})
    return out


def _page():
    return SimpleNamespace(is_closed=lambda: False, url=FORM)


def test_address_bar_navigation_is_still_a_plain_manual_navigate():
    rs, page = RecordingSession(session_id="t"), _page()
    assert _drain(rs, page, [FORM], [FORM, "https://other.test/"]) == [
        {"kind": "manual_navigate", "from_url": FORM, "to_url": "https://other.test/", "cause": "user", "cause_source": "clock"}
    ]


def test_spa_route_change_is_tagged_as_caused_by_the_action():
    """pushState/replaceState never fire Page.frameRequestedNavigation."""
    rs, page = RecordingSession(session_id="t"), _page()
    rs._nav_same_document.add(id(page))
    [nav] = _drain(rs, page, [FORM], [FORM, ORDER])
    assert nav["cause"] == "action"


def test_a_history_api_route_change_is_tagged_by_the_browser_not_the_clock():
    """CDP says navigationType "historyApi": the page changed its own address, no timing involved.
    (Measured on real Chromium 2026-11-05: pushState fires navigatedWithinDocument/historyApi, a link
    click fires frameRequestedNavigation/anchorClick, an address-bar load fires neither.)"""
    rs, page = RecordingSession(session_id="t"), _page()
    rs._nav_same_document.add(id(page))
    rs._nav_same_document_type[id(page)] = "historyApi"
    [nav] = _drain(rs, page, [FORM], [FORM, ORDER])
    assert (nav["cause"], nav["cause_source"]) == ("action", "browser")


def test_a_fragment_change_still_falls_back_to_the_clock():
    rs, page = RecordingSession(session_id="t"), _page()
    rs._nav_same_document.add(id(page))
    rs._nav_same_document_type[id(page)] = "fragment"
    [nav] = _drain(rs, page, [FORM], [FORM, ORDER])
    assert (nav["cause"], nav["cause_source"]) == ("action", "clock")


def test_navigation_right_after_a_click_is_tagged_as_caused_by_the_action():
    rs, page = RecordingSession(session_id="t"), _page()
    now = time.monotonic()
    rs._last_interaction_at[id(page)] = now - 0.5
    rs._nav_committed_at[id(page)] = now
    [nav] = _drain(rs, page, [FORM], [FORM, ORDER])
    assert nav["cause"] == "action"


def test_navigation_long_after_the_last_click_is_tagged_as_the_users_own():
    rs, page = RecordingSession(session_id="t"), _page()
    now = time.monotonic()
    rs._last_interaction_at[id(page)] = now - 60
    rs._nav_committed_at[id(page)] = now
    [nav] = _drain(rs, page, [FORM], [FORM, ORDER])
    assert nav["cause"] == "user"


def test_site_hop_right_after_a_page_load_is_tagged_as_a_redirect():
    rs, page = RecordingSession(session_id="t"), _page()
    first = "https://drive.example.com/"
    hop = "https://drive.example.com/drive/home"
    [a] = _drain(rs, page, [FORM], [FORM, first])
    assert a["cause"] == "user"
    [b] = _drain(rs, page, [FORM, first], [FORM, first, hop])
    assert b["cause"] == "redirect"


def test_same_site_navigation_after_a_click_since_the_page_load_is_not_a_redirect():
    rs, page = RecordingSession(session_id="t"), _page()
    first, second = "https://drive.example.com/", "https://drive.example.com/other"
    _drain(rs, page, [FORM], [FORM, first])
    rs._last_interaction_at[id(page)] = time.monotonic() + 0.01  # a click happened after the load
    rs._nav_committed_at[id(page)] = time.monotonic() + 30  # ...and the hop is well after it
    [nav] = _drain(rs, page, [FORM, first], [FORM, first, second])
    assert nav["cause"] != "redirect"


def test_hop_to_another_site_is_not_a_redirect():
    rs, page = RecordingSession(session_id="t"), _page()
    first = "https://drive.example.com/"
    _drain(rs, page, [FORM], [FORM, first])
    [nav] = _drain(rs, page, [FORM, first], [FORM, first, "https://other.example.org/"])
    assert nav["cause"] == "user"


def test_slow_redirect_still_counts_as_requested_by_the_page():
    """The TTL is measured to the navigation's COMMIT, not to whenever the pump loop drains it:
    a request that committed 1 s later must stay silent however late the drain runs."""
    rs, page = RecordingSession(session_id="t"), _page()
    now = time.monotonic()
    rs._nav_pending_renderer_initiated[id(page)] = now - 30
    rs._nav_committed_at[id(page)] = now - 29
    assert _drain(rs, page, [FORM], [FORM, ORDER]) == []


def test_binding_sink_stamps_interactions_but_not_scrolls():
    rs, page = RecordingSession(session_id="t"), _page()
    rs._binding_sink_sync({"page": page}, {"action": {"action": "scroll"}})
    assert id(page) not in rs._last_interaction_at
    rs._binding_sink_sync({"page": page}, {"action": {"action": "click"}})
    assert id(page) in rs._last_interaction_at


# ---------------------------------------------------------------- compiler fold

def _click(ts: str, url: str = FORM, tab: str = "tab_0") -> dict:
    return {"action": {"action": "click", "timestamp": ts}, "page": {"url": url}, "tab": {"id": tab}}


def _nav(ts: str, to_url: str = ORDER, tab: str = "tab_0", **extra) -> dict:
    return {
        "action": {
            "action": "manual_navigate",
            "timestamp": ts,
            "value": json.dumps({"from_url": FORM, "to_url": to_url, **extra}),
        },
        "page": {"url": to_url},
        "tab": {"id": tab},
    }


def test_fold_drops_a_tagged_navigation_and_keeps_it_as_the_click_post_condition():
    click = _click("2026-01-01T00:00:00Z")
    out = fold_action_caused_navigations([click, _nav("2026-01-01T00:00:30Z", cause="action")])
    assert out == [click]
    assert click["post_condition"] == {
        "classified_effect": "navigation",
        "url_delta": {"before": FORM, "after": ORDER},
    }


def test_fold_catches_legacy_sessions_recorded_before_the_cause_tag():
    click = _click("2026-01-01T00:00:00Z")
    out = fold_action_caused_navigations([click, _nav("2026-01-01T00:00:02Z")])
    assert out == [click]


def test_fold_keeps_a_navigation_the_user_did_on_their_own():
    events = [_click("2026-01-01T00:00:00Z"), _nav("2026-01-01T00:01:00Z")]
    assert fold_action_caused_navigations(events) == events


def test_a_click_that_lands_on_a_sign_in_page_keeps_where_it_landed():
    """WF-1 Leg B: "Log out" lands on /login. The recorder keeps that arrival (`to_sign_in`) only so it becomes the
    click's result; anything else on a sign-in page is still dropped, and the arrival is never a step of its own."""
    login = "https://shop.test/login"
    click = _click("2026-01-01T00:00:00Z")
    out = fold_action_caused_navigations([click, _nav("2026-01-01T00:00:01Z", to_url=login, cause="action", to_sign_in=True)])
    assert out == [click]
    assert click["post_condition"]["url_delta"]["after"] == login
    assert fold_action_caused_navigations([_nav("2026-01-01T00:00:01Z", to_url=login, cause="action", to_sign_in=True)]) == []


def test_recorder_keeps_only_a_click_caused_arrival_on_a_sign_in_page():
    from conxa_compile.recorder.session import _mark_sign_in_arrival

    caused = {"action": {"action": "manual_navigate", "value": json.dumps({"to_url": "https://shop.test/login", "cause": "action"})}}
    assert _mark_sign_in_arrival(caused) is True
    assert json.loads(caused["action"]["value"])["to_sign_in"] is True
    typed = {"action": {"action": "manual_navigate", "value": json.dumps({"to_url": "https://shop.test/login", "cause": "user"})}}
    assert _mark_sign_in_arrival(typed) is False
    assert _mark_sign_in_arrival({"action": {"action": "click"}}) is False


def test_fold_never_crosses_tabs():
    events = [_click("2026-01-01T00:00:00Z", tab="tab_0"), _nav("2026-01-01T00:00:01Z", tab="tab_1")]
    assert fold_action_caused_navigations(events) == events


def test_fold_leaves_back_forward_alone():
    back = {"action": {"action": "browser_back", "timestamp": "2026-01-01T00:00:01Z", "value": "{}"}, "tab": {"id": "tab_0"}}
    events = [_click("2026-01-01T00:00:00Z"), back]
    assert fold_action_caused_navigations(events) == events


# A site moving on by itself right after a page load (the first address redirects on): the second
# navigation starts where the first landed, same host, nothing done in between.
D1 = "https://drive.example.com/?pli=1"
D2 = "https://drive.example.com/drive/home"


def _goto(ts: str, frm: str, to: str, tab: str = "tab_0") -> dict:
    return {
        "action": {"action": "manual_navigate", "timestamp": ts, "value": json.dumps({"from_url": frm, "to_url": to})},
        "page": {"url": to},
        "tab": {"id": tab},
    }


def test_fold_drops_a_same_site_redirect_right_after_a_page_load():
    first, hop = _goto("2026-01-01T00:00:00Z", "about:blank", D1), _goto("2026-01-01T00:00:01Z", D1, D2)
    assert fold_action_caused_navigations([first, hop]) == [first]


def test_fold_trusts_the_tag_over_the_clock():
    """A tagged recording is decided by its tag: a deliberate same-site navigation 1 s after a
    load is kept; a tagged redirect is dropped even with the page-load 60 s earlier."""
    first = _goto("2026-01-01T00:00:00Z", "about:blank", D1)
    typed = _goto("2026-01-01T00:00:01Z", D1, D2)
    typed["action"]["value"] = json.dumps({"from_url": D1, "to_url": D2, "cause": "user", "cause_source": "clock"})
    assert fold_action_caused_navigations([first, typed]) == [first, typed]
    hop = _goto("2026-01-01T00:01:00Z", D1, D2)
    hop["action"]["value"] = json.dumps({"from_url": D1, "to_url": D2, "cause": "redirect"})
    assert fold_action_caused_navigations([first, hop]) == [first]


def test_fold_does_not_fold_a_user_tagged_navigation_into_a_recent_click():
    click = _click("2026-01-01T00:00:00Z")
    nav = _nav("2026-01-01T00:00:02Z", cause="user")
    assert fold_action_caused_navigations([click, nav]) == [click, nav]


def test_fold_keeps_a_second_navigation_to_another_site():
    events = [_goto("2026-01-01T00:00:00Z", "about:blank", D1),
              _goto("2026-01-01T00:00:01Z", D1, "https://other.example.org/")]
    assert fold_action_caused_navigations(events) == events


def test_fold_keeps_a_second_navigation_that_starts_somewhere_else():
    events = [_goto("2026-01-01T00:00:00Z", "about:blank", D1),
              _goto("2026-01-01T00:00:01Z", "https://elsewhere.example.com/", D2)]
    assert fold_action_caused_navigations(events) == events


def test_fold_keeps_a_same_site_navigation_after_the_window():
    events = [_goto("2026-01-01T00:00:00Z", "about:blank", D1), _goto("2026-01-01T00:00:30Z", D1, D2)]
    assert fold_action_caused_navigations(events) == events


def test_fold_site_redirect_never_crosses_tabs():
    events = [_goto("2026-01-01T00:00:00Z", "about:blank", D1, tab="tab_0"),
              _goto("2026-01-01T00:00:01Z", D1, D2, tab="tab_1")]
    assert fold_action_caused_navigations(events) == events


# ---------------------------------------------------------------- record -> compile

def _event(action: str, ts: str, url: str, text: str, css: str) -> dict:
    ev = _minimal_click_event()
    ev["action"] = {"action": action, "timestamp": ts, "value": None}
    ev["page"] = {"url": url, "title": ""}
    ev["target"].update({"id": css, "inner_text": text})
    ev["selectors"].update({"css": f"#{css}", "text_based": f'text="{text}"'})
    ev["semantic"]["normalized_text"] = text.lower()
    return ev


def _nav_event(ts: str, **extra) -> dict:
    ev = _event("manual_navigate", ts, ORDER, "", "nav")
    ev["action"]["value"] = json.dumps({"from_url": FORM, "to_url": ORDER, **extra})
    return ev


def _recording(nav: dict) -> list[dict]:
    return [
        _event("click", "2026-01-01T00:00:00Z", FORM, "Submit", "submit"),
        nav,
        _event("click", "2026-01-01T00:02:00Z", ORDER, "Download", "download"),
    ]


def _replayable(pkg) -> str:
    """What the runtime is actually handed for every step (execution.json rows). A step's recorded
    `page_url` context describes the recording visit and is never replayed, so it is not part of it."""
    from conxa_compile.skill_package_builder_saved_skill import _saved_step_to_execution_step

    rows = [_saved_step_to_execution_step(s.model_dump(mode="json")) for s in pkg.skills[0].steps]
    return json.dumps([r for r in rows if r])


@patch.object(settings, "compile_keep_every_action", False)


def test_redirect_after_submit_compiles_to_click_click_with_no_hardcoded_url():
    pkg = _compile_steps(_recording(_nav_event("2026-01-01T00:00:01Z", cause="action")), "sess-redirect")
    steps = pkg.skills[0].steps
    assert [s.action for s in steps] == ["navigate", "click", "click"]  # only the leading start navigate
    assert steps[0].url == FORM
    blob = _replayable(pkg)
    assert "847291" not in blob and "abc123" not in blob
    landed = [a for a in steps[1].validation.assertions if a.type == "url_pattern"]
    assert [(a.target, a.required) for a in landed] == [(ORDER_PATTERN, False)]
    # COMPILE-3: the click that navigated is enforced as "left the form" (target = the before-URL).
    moved = [a for a in steps[1].validation.assertions if a.type == "url_changed"]
    assert [(a.target, a.required) for a in moved] == [(FORM, True)]


def test_state_diff_sees_the_url_an_action_landed_on():
    """COMPILE-3: the after-snapshot takes the landed URL from the post-condition, not the start page."""
    from conxa_compile.compiler.state_validation import capture_state_snapshot, compare_state

    def diff(ev):
        return compare_state(capture_state_snapshot(ev, before=True), capture_state_snapshot(ev, before=False))

    click = _click("2026-01-01T00:00:00Z")
    assert diff(click)["url_changed"] is False
    click["post_condition"] = {"url_delta": {"before": FORM, "after": ORDER}}
    assert diff(click)["url_changed"] is True


@patch.object(settings, "compile_keep_every_action", False)


def test_legacy_recording_without_cause_tag_compiles_the_same_way():
    pkg = _compile_steps(_recording(_nav_event("2026-01-01T00:00:03Z")), "sess-redirect-legacy")
    assert [s.action for s in pkg.skills[0].steps] == ["navigate", "click", "click"]
    assert "847291" not in _replayable(pkg)


def test_user_typed_navigation_still_compiles_to_a_navigate_step():
    pkg = _compile_steps(_recording(_nav_event("2026-01-01T00:01:00Z")), "sess-typed-nav")
    steps = pkg.skills[0].steps
    assert [s.action for s in steps] == ["navigate", "click", "navigate", "click"]
    # The address keeps everything but its secret-looking value: a token never ships in a package (WF-8 secrets);
    # the hidden value is a run input the person fills in (BUILD-110 (9)).
    assert steps[2].url == ORDER.replace("token=abc123", "token={{token}}")


def test_ledger_says_how_each_folded_navigation_was_decided() -> None:
    """A folded navigation records whether the browser or a clock decided it, so the clock fallback can be
    deleted once only "browser" shows up."""
    from conxa_compile.compiler.navigation_effects import decided_by

    def nav(**extra):
        return {"action": {"action": "manual_navigate", "value": json.dumps({"from_url": "a", "to_url": "b", **extra})}}

    assert decided_by(nav(cause="action", cause_source="browser")) == "browser"
    assert decided_by(nav(cause="redirect")) == "clock"            # tagged by an older recorder build
    assert decided_by(nav()) == "legacy_clock"                      # a session recorded before tagging


# ---- W5-f: the browser says how the address was reached; the clock is only the fallback --------------------------

class _CdpWithTransition(_Cdp):
    def __init__(self, entries, index, transition):
        super().__init__(entries, index)
        self._hist["entries"][index]["transitionType"] = transition


def _drain_with_transition(rs, page, transition):
    before, after = [FORM], [FORM, "https://elsewhere.test/"]
    rs._nav_cdp_sessions[id(page)] = _CdpWithTransition(after, 1, transition)
    rs._nav_history_state[id(page)] = {"index": 0, "entries": before}
    rs._last_interaction_at[id(page)] = time.monotonic() - 1.5   # a click a moment ago
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    payload, _, _ = rs._pending_payloads.get_nowait()
    return json.loads(payload["action"]["value"])


def test_an_address_typed_a_moment_after_a_click_is_the_users_own_not_the_clicks_effect():
    got = _drain_with_transition(RecordingSession(session_id="t"), _page(), "typed")
    assert got["cause"] == "user" and got["cause_source"] == "browser"


def test_a_redirect_that_keeps_the_clicks_link_transition_is_still_the_clicks_effect():
    got = _drain_with_transition(RecordingSession(session_id="t"), _page(), "link")
    assert got["cause"] == "action"


def test_without_a_transition_from_the_browser_the_clock_still_decides():
    got = _drain_with_transition(RecordingSession(session_id="t"), _page(), "")
    assert got["cause"] == "action" and got["cause_source"] == "clock"


# ---------------------------------------------------------------- a page load the person's last action caused (WF-1 Leg B)

SECURE = "https://shop.test/secure"
LOGIN = "https://shop.test/login"


def test_a_page_load_right_after_an_action_tells_that_action_where_it_landed():
    """A link the page follows itself ("Log out") is renderer-initiated, so it is not a step of its own; the old page
    dies before it can say where it went. The recorder now queues that landing for the step, behind it."""
    rs, page = RecordingSession(session_id="t"), _page()
    now = time.monotonic()
    rs._last_interaction_at[id(page)] = now - 0.5
    rs._nav_committed_at[id(page)] = now
    rs._nav_pending_renderer_initiated[id(page)] = now - 0.2
    rs._nav_cdp_sessions[id(page)] = _Cdp([SECURE, LOGIN], 1)
    rs._nav_history_state[id(page)] = {"index": 0, "entries": [SECURE]}
    rs._nav_check_pages.append(page)
    rs._drain_nav_history_checks_sync()
    payload, _, _ = rs._pending_payloads.get_nowait()
    assert payload == {"_landing": {"from_url": SECURE, "to_url": LOGIN}}
    assert rs._pending_payloads.empty()


def test_the_landing_is_written_onto_the_step_taken_on_the_page_that_was_left(tmp_path):
    from conxa_core.models.events import RecordedEvent

    rs = RecordingSession(session_id="t", data_root=tmp_path)
    ev = _event("click", "2026-01-01T00:00:00Z", SECURE, "Logout", "logout")
    ev["tab"] = {"id": "tab_0"}
    ev["post_condition"] = {"classified_effect": "none", "url_delta": None}
    rs._materialized.append(RecordedEvent.model_validate(ev))
    rs._apply_landing_sync({"from_url": SECURE, "to_url": LOGIN}, "tab_0")
    pc = rs._materialized[-1].model_dump(mode="json")["post_condition"]
    assert pc["classified_effect"] == "navigation" and pc["url_delta"] == {"before": SECURE, "after": LOGIN}
    # A landing from a page the last action was not taken on is not that action's.
    rs._apply_landing_sync({"from_url": "https://shop.test/other", "to_url": "https://shop.test/x"}, "tab_0")
    assert rs._materialized[-1].model_dump(mode="json")["post_condition"]["url_delta"]["after"] == LOGIN


@patch.object(settings, "compile_keep_every_action", False)
def test_a_last_step_that_lands_on_sign_in_compiles_with_that_landing():
    click = _event("click", "2026-01-01T00:00:00Z", SECURE, "Logout", "logout")
    click["post_condition"] = {"classified_effect": "navigation", "url_delta": {"before": SECURE, "after": LOGIN}}
    pkg = _compile_steps([click], "sess-logout")
    last = pkg.skills[0].steps[-1]
    assert [a.target for a in last.validation.assertions if a.type == "url_pattern"] == [r"^https://shop\.test/login"]
