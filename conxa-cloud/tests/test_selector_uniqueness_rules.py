"""W5-a: a selector is chosen for being verifiably unique on the recorded page, and numbers that change by themselves
(counts in a tab label) never decide an element's identity. Built from the 2026-11-15 GitHub and saucedemo recordings."""

from __future__ import annotations

from conxa_compile.compiler.identity_bundle import generate_deterministic_signals, strip_volatile_count

NAV = """<html><body><nav>
<a href="/o/r/issues" data-discover="true" data-tab-item="issues">Issues <span>1.1k</span></a>
<a href="/o/r/pulls" data-discover="true" data-tab-item="pull-requests">Pull requests <span>58</span></a>
<a href="/o/r/actions" data-discover="true" data-tab-item="actions">Actions</a>
</nav></body></html>"""

PLAIN = """<html><body><nav>
<a href="/o/r/issues">Issues 12</a><a href="/o/r/pulls">Pull requests 58 (58)</a><a href="/o/r/actions">Actions</a>
</nav></body></html>"""


def _event(css, text, attrs=None):
    return {
        "target": {"tag": "a", "role": "link", "inner_text": text, "attributes": attrs or {}},
        "semantic": {"role": "link"},
        "selectors": {"css": css, "text_based": f'text="{text}"'},
    }


def _by_engine(signals):
    return {s.engine: s for s in signals}


def test_a_shared_attribute_is_not_chosen_when_a_distinguishing_one_exists():
    ev = _event("nav > a:nth-of-type(2)", "Pull requests 58",
                {"data-discover": "true", "data-tab-item": "pull-requests"})
    attr = _by_engine(generate_deterministic_signals(ev, NAV))["attr"]
    assert attr.selector == '[data-tab-item="pull-requests"]' and attr.unique_at_compile is True


def test_a_selector_that_matches_nothing_on_the_page_is_not_trusted_over_one_that_matches_one():
    # `[role="link"][data-discover="true"]` matches no node in the saved HTML (the link role is implicit).
    ev = _event("nav > a:nth-of-type(2)", "Pull requests 58", {"data-discover": "true", "data-tab-item": "pull-requests"})
    assert all('[role="link"]' not in s.selector for s in generate_deterministic_signals(ev, NAV))


def test_a_link_with_no_data_attributes_is_found_by_its_address():
    ev = _event("nav > a:nth-of-type(2)", "Pull requests 58 (58)")
    sel = {s.selector for s in generate_deterministic_signals(ev, PLAIN)}
    assert 'a[href="/o/r/pulls"]' in sel


def test_a_count_in_a_name_does_not_become_part_of_the_name():
    ev = _event("nav > a:nth-of-type(2)", "Pull requests 58 (58)")
    role = _by_engine(generate_deterministic_signals(ev, PLAIN)).get("role")
    assert role is not None and "58" not in role.selector


def test_text_that_carries_a_number_ranks_below_the_address():
    ev = _event("nav > a:nth-of-type(2)", "Pull requests 58 (58)")
    sig = {s.selector: s for s in generate_deterministic_signals(ev, PLAIN)}
    assert sig['a[href="/o/r/pulls"]'].durability > sig['internal:text="Pull requests 58 (58)"'].durability


def test_a_bare_number_is_never_a_text_signal():
    ev = {"target": {"tag": "span", "role": "button", "inner_text": "3"}, "semantic": {}, "selectors": {"css": "span.badge", "text_based": 'text="3"'}}
    assert not any(s.engine == "text_based" for s in generate_deterministic_signals(ev, None))


def test_strip_volatile_count():
    assert strip_volatile_count("Pull requests 58 (58)") == "Pull requests"
    assert strip_volatile_count("Issues 1.1k (1.1k)") == "Issues"
    assert strip_volatile_count("Inbox (274)") == "Inbox"
    assert strip_volatile_count("Actions") == "Actions"
    assert strip_volatile_count("3") == "3"


def test_ids_a_framework_hands_out_per_render_are_dynamic():
    from conxa_compile.compiler.selector_filters import is_dynamic_id

    for gen in ("#:rh", "#:tv", "#\:rh", "#ember1234", "#react-select-3-input"):
        assert is_dynamic_id(gen), gen
    for stable in ("#email", "#checkout-btn", "#order-form"):
        assert not is_dynamic_id(stable), stable


def test_what_an_element_is_doing_right_now_is_not_its_identity():
    from conxa_compile.compiler.named_attrs import filter_identity_attrs

    got = filter_identity_attrs({"data-transitioning": "transitioning", "data-status": "active", "data-discover": "true",
                                 "data-tab-item": "pull-requests", "role": "link"})
    assert got == {"data-tab-item": "pull-requests", "role": "link"}


def test_a_trailing_number_stays_in_the_role_name_when_it_is_what_tells_siblings_apart():
    from conxa_compile.compiler.identity_bundle import generate_deterministic_signals

    html = "<html><body>" + "".join(f"<button>Pick item {n}</button>" for n in (26, 27, 28)) + "</body></html>"
    ev = {"target": {"tag": "button", "role": "button", "text": "Pick item 27", "aria_label": "Pick item 27"},
          "semantic": {"role": "button"}, "selectors": {}}
    tree = {"aria_snapshot": chr(10).join(f'- button "Pick item {n}"' for n in (26, 27, 28))}
    roles = [s.selector for s in generate_deterministic_signals(ev, html, tree) if s.selector.startswith("internal:role=")]
    assert roles and all("Pick item 27" in r for r in roles), roles
    # a live count that is the same element whatever it says is still stripped
    only = "<html><body><a role='link'>Inbox (274)</a></body></html>"
    ev2 = {"target": {"tag": "a", "role": "link", "text": "Inbox (274)", "aria_label": "Inbox (274)"}, "semantic": {"role": "link"}, "selectors": {}}
    tree2 = {"aria_snapshot": '- link "Inbox (274)"'}
    roles2 = [s.selector for s in generate_deterministic_signals(ev2, only, tree2) if s.selector.startswith("internal:role=")]
    assert roles2 and all("274" not in r for r in roles2), roles2
