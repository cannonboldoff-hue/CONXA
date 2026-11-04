"""Compiler multi-tab support: tab_open/tab_switch markers get inserted at tab-boundary
crossings, every compiled step's identity-relevant target key stays distinct across tabs even
when DOM signals coincidentally match, and _build_tab_context carries a step's tab forward from
its recorded event. See conxa_compile/compiler/build.py and compiler/step_anchors.py.
"""
from __future__ import annotations

import json

from conxa_compile.compiler.build import (
    _build_tab_context,
    _insert_start_navigate_step,
    _insert_tab_markers,
)
from conxa_compile.compiler.choice import collapse_choice_group_runs
from conxa_compile.compiler.date_picker import collapse_date_picker_runs
from conxa_compile.compiler.step_anchors import _tab_signature, _target_key
from conxa_core.models.skill_spec import SkillStep



def _ev(tab_id: str, action: str = "click", url: str = "") -> dict:
    return {"action": {"action": action}, "tab": {"id": tab_id, "index": 0}, "page": {"url": url}}


def _user_tab(tab_id: str, index: int = 1) -> dict:
    return {"id": tab_id, "index": index, "opened_by": "user", "opener_tab": None}


def _site_tab(tab_id: str, index: int = 1, opener: str = "tab_0") -> dict:
    return {"id": tab_id, "index": index, "opened_by": "site", "opener_tab": opener}


def _step(action: str, tab: dict) -> SkillStep:
    return SkillStep(action=action, tab=tab)


def test_no_marker_for_the_recording_s_starting_tab() -> None:
    events = [_ev("tab_0"), _ev("tab_0"), _ev("tab_0")]
    out = _insert_tab_markers(events)
    assert [e["action"]["action"] for e in out] == ["click", "click", "click"]


def test_tab_open_marker_on_first_visit_tab_switch_on_return() -> None:
    events = [_ev("tab_0"), _ev("tab_1"), _ev("tab_1"), _ev("tab_0")]
    out = _insert_tab_markers(events)
    kinds = [(e["action"]["action"], e.get("tab", {}).get("id")) for e in out]
    assert kinds == [
        ("click", "tab_0"),
        ("tab_open", "tab_1"),
        ("click", "tab_1"),
        ("click", "tab_1"),
        ("tab_switch", "tab_0"),
        ("click", "tab_0"),
    ]


def test_no_events_returns_empty_list_unchanged() -> None:
    assert _insert_tab_markers([]) == []


def test_single_tab_recording_gets_no_markers_at_all() -> None:
    events = [_ev("tab_0")] * 5
    out = _insert_tab_markers(events)
    assert len(out) == 5
    assert all(e["action"]["action"] == "click" for e in out)


def test_build_tab_context_explicit_for_tab_0_empty_when_absent() -> None:
    """tab_0 gets an explicit block too: a return-to-the-initial-tab tab_switch marker must
    name its destination, or the runtime's mis-stamp guard (stepInheritsPage) treats it as
    'stay on the current page' and the switch back to tab_0 replays as a no-op."""
    ctx = _build_tab_context({"tab": {"id": "tab_0"}})
    assert ctx["id"] == "tab_0"
    assert ctx["opened_by"] == "initial"
    assert _build_tab_context({}) == {}
    assert _build_tab_context({"tab": {}}) == {}


def test_return_to_initial_tab_marker_names_its_destination() -> None:
    """End-to-end shape of the A→B→A bug: the tab_switch event inserted when recording returns
    to tab_0 carries the raw recorded tab dict through _build_step's MARKER path — which reads
    _build_tab_context — so the compiled step must end up with an explicit tab_0 block."""
    events = [_ev("tab_0"), _ev("tab_1"), _ev("tab_1"), _ev("tab_0")]
    out = _insert_tab_markers(events)
    switch = [e for e in out if e["action"]["action"] == "tab_switch"]
    assert len(switch) == 1
    ctx = _build_tab_context(switch[0])
    assert ctx["id"] == "tab_0"


def test_build_tab_context_carries_real_tab_forward() -> None:
    ctx = _build_tab_context({"tab": {"id": "tab_1", "index": 1, "opened_by": "site", "opener_tab": "tab_0", "url": "https://b.test/"}})
    assert ctx["id"] == "tab_1"
    assert ctx["opened_by"] == "site"
    assert ctx["opener_tab"] == "tab_0"
    assert ctx["url"] == "https://b.test/"


def test_target_key_differs_across_tabs_for_identical_dom_signals() -> None:
    """Two tabs on the same site can render an identical-looking element (same empty selectors,
    same tag/name) — the dedup/merge logic in clean_steps must never conflate them."""
    step_tab0 = {"tab": {"id": "tab_0"}, "target": {"tag": "input"}, "selectors": {}}
    step_tab1 = {"tab": {"id": "tab_1"}, "target": {"tag": "input"}, "selectors": {}}
    assert _target_key(step_tab0) != _target_key(step_tab1)


def test_target_key_identical_for_same_tab_and_signals() -> None:
    step_a = {"tab": {"id": "tab_1"}, "target": {"tag": "input", "name": "q"}, "selectors": {}}
    step_b = {"tab": {"id": "tab_1"}, "target": {"tag": "input", "name": "q"}, "selectors": {}}
    assert _target_key(step_a) == _target_key(step_b)


def test_tab_signature_defaults_to_tab_0_when_absent() -> None:
    assert _tab_signature({}) == "tab_0"
    assert _tab_signature({"tab": {}}) == "tab_0"


def test_user_opened_tab_gets_synthesized_manual_navigate_event() -> None:
    """runtime/tabs.js creates a blank page for opened_by="user" tabs and expects the first
    action on it to be a navigate — the compiler must actually produce one. This now happens at
    the EVENT level (a synthetic manual_navigate event right after the tab_open marker), not as
    a step spliced in after the step-building loop — inserting it as a bare step used to desync
    steps[i] from events[i] for every later index-based pass (hover chains, choice/date-picker
    collapse), which is exactly what broke mega-workflow's date_pick step (2026-09-02)."""
    tab = _user_tab("tab_1")
    events = [
        _ev("tab_0", url="https://a.test/"),
        {"action": {"action": "click"}, "tab": tab, "page": {"url": "https://b.test/dashboard"}},
    ]
    out = _insert_tab_markers(events)
    kinds = [(e["action"]["action"], e.get("tab", {}).get("id")) for e in out]
    assert kinds == [
        ("click", "tab_0"),
        ("tab_open", "tab_1"),
        ("manual_navigate", "tab_1"),
        ("click", "tab_1"),
    ]
    nav_ev = out[2]
    assert json.loads(nav_ev["action"]["value"]) == {"to_url": "https://b.test/dashboard"}
    assert nav_ev["tab"] == tab


def test_site_opened_tab_gets_no_synthesized_navigate_event() -> None:
    """opened_by="site" tabs (a link/window.open) are navigated by the click that's already
    replayed as a normal step — no synthetic navigate event should be inserted."""
    tab = _site_tab("tab_1")
    events = [
        _ev("tab_0", url="https://a.test/"),
        {"action": {"action": "click"}, "tab": tab, "page": {"url": "https://b.test/"}},
    ]
    out = _insert_tab_markers(events)
    assert [e["action"]["action"] for e in out] == ["click", "tab_open", "click"]


def test_tab_switch_gets_no_synthesized_navigate_event() -> None:
    """Only tab_open (first visit) should ever get a synthesized navigate — tab_switch means
    the tab is already live and resolveStepPage reuses the existing page."""
    tab1 = _user_tab("tab_1")
    events = [
        _ev("tab_0", url="https://a.test/"),
        {"action": {"action": "click"}, "tab": tab1, "page": {"url": "https://b.test/"}},
        _ev("tab_0", url="https://a.test/"),
    ]
    out = _insert_tab_markers(events)
    kinds = [e["action"]["action"] for e in out]
    assert kinds == ["click", "tab_open", "manual_navigate", "click", "tab_switch", "click"]


def test_user_opened_tab_with_no_recorded_url_gets_no_navigate_event() -> None:
    """Never synthesize a navigate to an empty URL — no worse than the pre-fix behavior."""
    tab = _user_tab("tab_1")
    events = [
        _ev("tab_0"),
        {"action": {"action": "click"}, "tab": tab, "page": {"url": ""}},
    ]
    out = _insert_tab_markers(events)
    assert [e["action"]["action"] for e in out] == ["click", "tab_open", "click"]


def test_recording_gets_a_leading_navigate_to_where_it_started() -> None:
    """The recording's own starting tab (tab_0) gets no tab_open marker from
    _insert_tab_markers, so nothing else in the compiled skill records where step 1 is
    supposed to run — the runtime used to guess wrong (see FIX.md). This gives tab_0 the
    same leading navigate every other tab already gets."""
    steps = [_step("click", {}), _step("click", {})]
    events = [{"page": {"url": "https://filebin.net/wn8n9o7mlzkvpvcl"}}, {"page": {"url": "https://filebin.net/wn8n9o7mlzkvpvcl"}}]
    out = _insert_start_navigate_step(steps, events)
    assert [s.action for s in out] == ["navigate", "click", "click"]
    nav = out[0]
    assert nav.url == "https://filebin.net/wn8n9o7mlzkvpvcl"
    assert nav.tab == {}
    assert nav.intent == "navigate_to_page"


def test_recording_already_starting_with_a_navigate_gets_no_second_one() -> None:
    steps = [_step("navigate", {}), _step("click", {})]
    events = [{"page": {"url": "https://a.test/"}}, {"page": {"url": "https://a.test/"}}]
    out = _insert_start_navigate_step(steps, events)
    assert out is steps


def test_blank_starting_url_gets_no_leading_navigate() -> None:
    steps = [_step("click", {})]
    events = [{"page": {"url": "about:blank"}}]
    assert _insert_start_navigate_step(steps, events) == steps


def test_empty_steps_or_events_is_a_no_op() -> None:
    assert _insert_start_navigate_step([], [{"page": {"url": "https://a.test/"}}]) == []
    steps = [_step("click", {})]
    assert _insert_start_navigate_step(steps, []) == steps


def test_site_opened_popup_gets_a_tab_open_and_a_switch_back() -> None:
    """Regression (2026-09-02, mega-workflow steps 28-30). Recorded: click a target="_blank"
    link on tab_0, the browser opens tab_1, the user switches straight back to tab_0 and keeps
    working — tab_1 is never interacted with. Once the recorder stamps the popup event with the
    popup's OWN tab (session.py::_on_popup's tab_key), the tab-id transition exists and this
    function produces the pair the runtime needs: bind + front the popup, then front tab_0
    again. Without it the replay drives tab_0 from behind the popup for the rest of the run.
    """
    events = [
        _ev("tab_0", "click", "https://x.test/windows"),
        _ev("tab_1", "popup", "https://x.test/windows/new"),
        _ev("tab_0", "navigate", "https://x.test/login"),
        _ev("tab_0", "click", "https://x.test/login"),
    ]
    out = _insert_tab_markers(events)
    assert [(e["action"]["action"], e["tab"]["id"]) for e in out] == [
        ("click", "tab_0"),
        ("tab_open", "tab_1"),
        ("popup", "tab_1"),
        ("tab_switch", "tab_0"),
        ("navigate", "tab_0"),
        ("click", "tab_0"),
    ]


def test_site_opened_popup_marker_carries_its_opener_forward() -> None:
    """The tab_open the popup produces must stay opened_by="site" with its opener — that is
    what routes the runtime to drain the pending-page queue (binding the real popup) instead of
    calling context.newPage(), and what keeps _insert_tab_markers from synthesizing a
    manual_navigate event the replayed click already performs."""
    popup_ev = {
        "action": {"action": "popup"},
        "tab": {"id": "tab_1", "index": 1, "opened_by": "site", "opener_tab": "tab_0"},
        "page": {"url": "https://x.test/windows/new"},
    }
    out = _insert_tab_markers([_ev("tab_0"), popup_ev, _ev("tab_0")])
    opened = next(e for e in out if e["action"]["action"] == "tab_open")
    ctx = _build_tab_context(opened)
    assert ctx["id"] == "tab_1"
    assert ctx["opened_by"] == "site"
    assert ctx["opener_tab"] == "tab_0"

    # opened_by="site" tabs must NOT get a synthesized manual_navigate event — the replayed
    # click opens them.
    open_idx = out.index(opened)
    assert out[open_idx + 1]["action"]["action"] != "manual_navigate"


def _date_open_event() -> dict:
    return {
        "action": {"action": "focus"},
        "tab": {"id": "tab_1", "index": 1},
        "target": {"tag": "input", "label_text": "Date of Birth"},
        "selectors": {"css": "#dateOfBirthInput", "aria": "", "xpath": ""},
        "date_context": None,
        "page": {"url": "https://b.test/form"},
    }


def _date_day_event() -> dict:
    return {
        "action": {"action": "click"},
        "tab": {"id": "tab_1", "index": 1},
        "target": {"tag": "div"},
        "selectors": {"css": ".day--15", "aria": "", "xpath": ""},
        "date_context": {
            "role": "day",
            "iso_date": "2026-09-15",
            "cell_attr": "",
            "grid": ".calendar",
            "header": ".calendar__month",
            "header_text": "September 2026",
            "prev": ".calendar__prev",
            "next": ".calendar__next",
            "cell": ".day--15",
            "field": "#dateOfBirthInput",
            "field_display_value": "",
        },
        "page": {"url": "https://b.test/form"},
    }


def test_user_tab_open_no_longer_desyncs_a_later_date_picker_collapse() -> None:
    """Regression (2026-09-02, mega-workflow steps 62-67): a `date_pick` step compiled with the
    wrong element's identity because _insert_user_tab_navigate_steps spliced a bare `navigate`
    step into `steps` without a matching event, shifting every following steps[i]/events[i] pair
    by one and feeding collapse_date_picker_runs a desynced pair. It silently collapsed the
    wrong step instead of the day-cell run.

    Now the navigate is a synthetic EVENT (_make_tab_navigate_event, inserted by
    _insert_tab_markers), so a steps list built 1:1 from the resulting events list — exactly
    what build.py's step-building loop produces — stays aligned into the date-picker collapse
    below, and the collapsed date_pick correctly picks up the day cell's own identity."""
    user_tab_open = {
        "action": {"action": "click"},
        "tab": {"id": "tab_0", "index": 0},
        "page": {"url": "https://a.test/"},
    }
    tab1_click = {
        "action": {"action": "click"},
        "tab": _user_tab("tab_1"),
        "page": {"url": "https://b.test/form"},
    }
    cleaned_events = [user_tab_open, tab1_click, _date_open_event(), _date_day_event()]
    events = _insert_tab_markers(cleaned_events)

    # Sanity: the marker pass inserted exactly one tab_open + one manual_navigate event ahead
    # of the original 4 — steps built 1:1 from `events` must total 6, never fewer.
    assert len(events) == 6

    # Stand in for build.py's per-event step-building loop (_build_step): one step per event,
    # action carried straight through, matching cleaned_events[i]'s content 1:1.
    steps = [
        _step("tab_open" if e["action"]["action"] == "tab_open" else
              "navigate" if e["action"]["action"] == "manual_navigate" else
              e["action"]["action"], e.get("tab") or {})
        for e in events
    ]
    steps[4].semantic_description = "opener-marker"  # the focus step that opens the field
    steps[5].semantic_description = "day-cell-marker"  # the actual day-cell pick

    out = collapse_date_picker_runs(steps, events, {})
    assert len(out) == 5, "no step should be dropped by the collapse"
    date_pick = out[-1]
    assert date_pick.action == "date_pick"
    assert date_pick.semantic_description == "opener-marker", (
        "the collapsed date_pick must fold the OPENER step (steps[4]), not some shifted step — "
        "this is the exact corruption the desync produced in mega-workflow"
    )
    assert date_pick.handler_hints.date_picker["strategy"] == "typed_first"


def test_collapse_choice_group_runs_passes_unmatched_steps_through() -> None:
    """Steps and events of different lengths used to be a crash. A step with no findable event now just
    passes through unchanged, because nothing says it belongs to a group."""
    steps = [_step("click", {}), _step("click", {})]
    assert collapse_choice_group_runs(steps, [_ev("tab_0")], {}) == steps


def test_collapse_date_picker_runs_passes_unmatched_steps_through() -> None:
    steps = [_step("click", {}), _step("click", {})]
    assert collapse_date_picker_runs(steps, [_ev("tab_0")], {}) == steps
