"""Named-attribute identity: stability gate + IdentityBundle attr signal."""
from __future__ import annotations

from conxa_compile.compiler.identity_bundle import generate_deterministic_signals
from conxa_compile.compiler.named_attrs import (
    backfill_attrs_from_snapshot,
    filter_identity_attrs,
    synthesize_attr_selector,
)
from conxa_core.models.events import TargetDom


def test_target_dom_round_trips_attributes():
    """bridge.js::collectIdentityAttrs populates target.attributes; RecordedEvent.target is a
    TargetDom. Without a declared `attributes` field, Pydantic's default extra="ignore" silently
    drops it on validate — the compiler's named_attrs.py would never see it, on any recording."""
    t = TargetDom.model_validate({
        "tag": "li",
        "role": "menuitem",
        "attributes": {"role": "menuitem", "data-key": "19"},
    })
    assert t.model_dump(mode="json")["attributes"] == {"role": "menuitem", "data-key": "19"}


def test_target_dom_attributes_defaults_to_empty_dict():
    t = TargetDom.model_validate({"tag": "div"})
    assert t.model_dump(mode="json")["attributes"] == {}


def test_keeps_data_key_and_role_drops_state_and_testid():
    raw = {
        "role": "menuitem",
        "data-key": "19",
        "data-state": "open",
        "data-testid": "file-upload",
        "class": "hashed",
    }
    assert filter_identity_attrs(raw) == {"role": "menuitem", "data-key": "19"}


def test_drops_uuid_and_hashed_token_values():
    raw = {
        "data-id": "550e8400-e29b-41d4-a716-446655440000",
        "data-hash": "O68mGeOQAXzeQu9X",
        "data-key": "19",
    }
    assert filter_identity_attrs(raw) == {"data-key": "19"}


def test_synthesize_unique_data_key_is_bare_attr():
    html = '<html><body><ul><li role="menuitem" data-key="19">File upload</li></ul></body></html>'
    sel = synthesize_attr_selector(
        {"tag": "li", "role": "menuitem", "attributes": {"role": "menuitem", "data-key": "19"}},
        {"role": "menuitem"},
        html,
    )
    assert sel == '[data-key="19"]'


def test_synthesize_qualifies_with_role_when_key_is_shared():
    html = """<html><body>
      <li role="menuitem" data-key="19">File upload</li>
      <div data-key="19">other</div>
    </body></html>"""
    sel = synthesize_attr_selector(
        {"tag": "li", "role": "menuitem", "attributes": {"role": "menuitem", "data-key": "19"}},
        {"role": "menuitem"},
        html,
    )
    assert sel == '[role="menuitem"][data-key="19"]'


def test_synthesize_qualifies_with_tag_and_role_when_needed():
    html = """<html><body>
      <li role="menuitem" data-key="19">File upload</li>
      <span role="menuitem" data-key="19">also</span>
    </body></html>"""
    sel = synthesize_attr_selector(
        {"tag": "li", "role": "menuitem", "attributes": {"role": "menuitem", "data-key": "19"}},
        {"role": "menuitem"},
        html,
    )
    assert sel == 'li[role="menuitem"][data-key="19"]'


def test_synthesize_none_without_data_attrs():
    assert synthesize_attr_selector({"tag": "li", "role": "menuitem", "attributes": {"role": "menuitem"}}) is None


def test_bundle_emits_attr_alongside_role_and_text():
    html = '<html><body><ul><li role="menuitem" data-key="19">File upload</li></ul></body></html>'
    ev = {
        "target": {
            "tag": "li",
            "role": "menuitem",
            "inner_text": "File upload",
            "attributes": {"role": "menuitem", "data-key": "19"},
        },
        "semantic": {"role": "menuitem"},
        "selectors": {
            "css": "ul > li:nth-of-type(1)",
            "text_based": 'text="File upload"',
            "xpath": "/html/body/ul/li[1]",
        },
        "anchors": [],
    }
    signals = generate_deterministic_signals(ev, html, None)
    by_engine = {s.engine: s.selector for s in signals}
    assert by_engine["attr"] == '[data-key="19"]'
    assert any(s.engine == "role" for s in signals)
    assert any(s.engine == "text_based" for s in signals)
    attr = next(s for s in signals if s.engine == "attr")
    assert attr.orthogonality_class == "named-attr"
    assert attr.durability >= 0.96


def test_backfill_recovers_attrs_via_css_when_target_attributes_missing():
    html = '<html><body><ul><li role="menuitem" data-key="19">File upload</li></ul></body></html>'
    selectors = {"css": "ul > li:nth-of-type(1)", "xpath": "/html/body/ul/li[1]"}
    assert backfill_attrs_from_snapshot(selectors, html) == {
        "role": "menuitem",
        "data-key": "19",
    }


def test_backfill_falls_back_to_xpath_when_css_is_not_unique():
    html = """<html><body>
      <li role="menuitem" data-key="19">File upload</li>
      <li role="menuitem" data-key="20">Folder upload</li>
    </body></html>"""
    selectors = {"css": "li", "xpath": "/html/body/li[1]"}
    assert backfill_attrs_from_snapshot(selectors, html) == {
        "role": "menuitem",
        "data-key": "19",
    }


def test_backfill_returns_none_when_node_is_not_in_snapshot():
    html = "<html><body><ul></ul></body></html>"
    selectors = {"css": "ul > li:nth-of-type(1)", "xpath": "/html/body/ul/li[1]"}
    assert backfill_attrs_from_snapshot(selectors, html) is None


def test_backfill_returns_none_without_dom_html_or_selectors():
    assert backfill_attrs_from_snapshot({"css": "li"}, None) is None
    assert backfill_attrs_from_snapshot(None, "<html></html>") is None


def test_bundle_backfills_attr_signal_for_pre_fix_recording_without_target_attributes():
    """Recompiling an old recording (no target.attributes) still recovers the attr signal
    from the saved DOM snapshot, instead of requiring a re-record."""
    html = '<html><body><ul><li role="menuitem" data-key="19">File upload</li></ul></body></html>'
    ev = {
        "target": {
            "tag": "li",
            "role": "menuitem",
            "inner_text": "File upload",
            # no "attributes" key — pre-fix recording
        },
        "semantic": {"role": "menuitem"},
        "selectors": {
            "css": "ul > li:nth-of-type(1)",
            "text_based": 'text="File upload"',
            "xpath": "/html/body/ul/li[1]",
        },
        "anchors": [],
    }
    signals = generate_deterministic_signals(ev, html, None)
    by_engine = {s.engine: s.selector for s in signals}
    assert by_engine["attr"] == '[data-key="19"]'


# WF-3 D (ParaBank Bill Pay): nine boxes in an unlabelled table, every one `input.input`. Only the form field's own
# `name` tells them apart; it used to reach the fallbacks and never the signals, so the type step failed as ambiguous.
_BILL_PAY = (
    '<html><body><form><table>'
    '<tr><td><b>Payee Name:</b></td><td><input class="input" name="payee.name"></td></tr>'
    '<tr><td><b>Account #:</b></td><td><input class="input" name="payee.accountNumber"></td></tr>'
    '<tr><td>Kind</td><td><input type="radio" name="kind" value="a"><input type="radio" name="kind" value="b"></td></tr>'
    '</table></form></body></html>'
)


def _field_event(name: str, input_type: str = "text") -> dict:
    return {
        "target": {"tag": "input", "role": "textbox", "name": name, "label_text": "Account #:", "attributes": {}},
        "semantic": {"role": "textbox", "input_type": input_type},
        "selectors": {"css": "input.input", "aria": f'input[name="{name}"]'},
    }


def test_a_unique_form_field_name_leads_the_identity_bundle():
    signals = generate_deterministic_signals(_field_event("payee.accountNumber"), dom_html=_BILL_PAY)
    assert (signals[0].engine, signals[0].selector, signals[0].unique_at_compile) == (
        "attr", 'input[name="payee.accountNumber"]', True,
    )


def test_a_name_shared_by_a_radio_group_is_not_an_identity():
    signals = generate_deterministic_signals(_field_event("kind", "radio"), dom_html=_BILL_PAY)
    assert not any(s.selector.startswith("input[name=") for s in signals)


def test_a_form_field_name_is_not_used_without_the_saved_page():
    signals = generate_deterministic_signals(_field_event("payee.accountNumber"), dom_html=None)
    assert not any(s.selector.startswith("input[name=") for s in signals)
