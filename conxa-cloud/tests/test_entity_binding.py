"""PROD-3 entity binding: repeating-container detection, identifier selection, and the
danger-classification + serializer wiring that makes the runtime halt actually fire."""

from __future__ import annotations

from conxa_compile.compiler.destructive_semantics import classify_consequence
from conxa_compile.compiler.entity_binding import (
    collect_input_literal_values,
    detect_entity_binding,
    edited_values,
    typed_values,
    upgrade_entity_binding_identifiers,
)
from conxa_compile.skill_package_builder_saved_skill import _copy_saved_common

_POLICY: dict = {}


def _row(tag: str, classes: list[str], text: str) -> str:
    cls = f' class="{" ".join(classes)}"' if classes else ""
    return f"<{tag}{cls}>{text}</{tag}>"


def _table_event(rows: list[str], target_row_index: int, *, parent_id: str = "invoices") -> dict:
    row_tag = "tr"
    row_classes = ["invoice-row"]
    ancestors = [
        {"tag": row_tag, "id": "", "classes": row_classes, "outer_html": rows[target_row_index]},
        {"tag": "tbody", "id": parent_id, "classes": [],
         "outer_html": f'<tbody id="{parent_id}">{"".join(rows)}</tbody>'},
        {"tag": "table", "id": "", "classes": [], "outer_html": ""},
    ]
    return {
        "action": {"action": "click", "value": None},
        "semantic": {"final_intent": "delete_invoice_row", "llm_intent": "delete_invoice_row"},
        "ancestors": ancestors,
    }


class TestDetectEntityBinding:
    def test_detects_repeating_row_and_picks_digit_identifier(self):
        rows = [
            _row("tr", ["invoice-row"], "Invoice #12344 Delete"),
            _row("tr", ["invoice-row"], "Invoice #12345 Delete"),
            _row("tr", ["invoice-row"], "Invoice #12346 Delete"),
        ]
        ev = _table_event(rows, target_row_index=1)
        eb = detect_entity_binding(ev)
        assert eb is not None
        assert "tr" in eb.container_selector
        assert "invoices" in eb.container_selector or "invoice-row" in eb.container_selector
        assert "12345" in eb.identifier
        assert eb.source == "literal"
        assert eb.confirmed is False

    def test_identifier_never_picks_the_action_button_label(self):
        rows = [_row("tr", ["invoice-row"], f"Invoice #{n} Delete") for n in (1, 2, 3)]
        ev = _table_event(rows, target_row_index=0)
        eb = detect_entity_binding(ev)
        assert eb is not None
        assert eb.identifier.lower() != "delete"

    def test_no_repeating_siblings_returns_none(self):
        ev = {
            "action": {"action": "click"},
            "semantic": {"final_intent": "delete_account"},
            "ancestors": [
                {"tag": "div", "id": "", "classes": ["danger-zone"], "outer_html": "<div>Delete my account</div>"},
                {"tag": "section", "id": "", "classes": [],
                 "outer_html": '<section><div class="danger-zone">Delete my account</div></section>'},
            ],
        }
        assert detect_entity_binding(ev) is None

    def test_no_ancestors_returns_none(self):
        assert detect_entity_binding({"action": {"action": "click"}, "ancestors": []}) is None

    def test_single_sibling_below_minimum_returns_none(self):
        rows = [_row("tr", ["invoice-row"], "Invoice #1 Delete")]
        ev = _table_event(rows, target_row_index=0)
        assert detect_entity_binding(ev) is None

    def test_ignores_layout_grid_siblings_far_up_the_tree(self):
        # Regression: a login button on a page built with a Foundation/Bootstrap-style CSS
        # framework, where <body> happens to have two unrelated `<div class="row">` children
        # (a flash-messages banner and the page's own content wrapper) purely for layout.
        # Unbounded ancestor search used to treat that coincidence as a 2-row "list" and pick a
        # fragment of the page's own help text as the record identifier, turning an ordinary
        # button into a phantom record lookup that halts execution with "could not be uniquely
        # located". The real target sits tightly inside its own (non-repeating) form, several
        # levels below body — the row match must not reach that far up the tree.
        ancestors = [
            {"tag": "form", "id": "login", "classes": [], "outer_html": "<form id=\"login\"></form>"},
            {"tag": "div", "id": "", "classes": ["example"], "outer_html": "<div class=\"example\"></div>"},
            {"tag": "div", "id": "content", "classes": ["large-12", "columns"], "outer_html": "<div id=\"content\"></div>"},
            {"tag": "div", "id": "", "classes": ["row"], "outer_html": "<div class=\"row\">page content</div>"},
            {"tag": "body", "id": "", "classes": [],
             "outer_html": (
                 '<body>'
                 '<div class="row"><div id="flash-messages"></div></div>'
                 '<div class="row"><div id="content"></div></div>'
                 '</body>'
             )},
        ]
        ev = {"action": {"action": "click"}, "semantic": {"final_intent": "click_login_button"}, "ancestors": ancestors}
        assert detect_entity_binding(ev) is None


class TestIdentifierUpgrade:
    def test_prefers_declared_input_over_literal(self):
        rows = [
            _row("tr", ["invoice-row"], "Invoice #12344 Delete"),
            _row("tr", ["invoice-row"], "Invoice #12345 Delete"),
        ]
        ev = _table_event(rows, target_row_index=1)
        eb = detect_entity_binding(ev)
        assert eb is not None

        class _Step:
            def __init__(self, entity_binding):
                self.entity_binding = entity_binding

        step = _Step(eb)
        input_values = {"invoice_no": "12345"}
        upgrade_entity_binding_identifiers([step], input_values)
        # only the typed part is the input: the runtime matches the row's text whole, never a part of it (WF-7)
        assert step.entity_binding.identifier == "Invoice #{{invoice_no}} Delete"
        assert step.entity_binding.source == "input"

    def test_no_match_leaves_literal_untouched(self):
        class _Step:
            def __init__(self, entity_binding):
                self.entity_binding = entity_binding

        from conxa_core.models.skill_spec import EntityBinding
        eb = EntityBinding(container_selector="tr", identifier="Invoice #99999", source="literal", confirmed=False)
        step = _Step(eb)
        upgrade_entity_binding_identifiers([step], {"invoice_no": "12345"})
        assert step.entity_binding.identifier == "Invoice #99999"
        assert step.entity_binding.source == "literal"

    def test_collect_input_literal_values_reads_recorded_fill_events(self):
        events = [
            {
                "action": {"action": "type", "value": "12345"},
                "target": {"tag": "input", "label_text": "Invoice number"},
                "semantic": {},
            },
        ]
        values = collect_input_literal_values(events, _POLICY)
        assert "12345" in values.values()


class TestClassifyConsequence:
    def test_delete_click_is_irreversible(self):
        step = {"action": {"action": "click"}, "semantic": {"final_intent": "delete_invoice"}}
        assert classify_consequence(step, _POLICY) == "irreversible"

    def test_scroll_is_read_only(self):
        step = {"action": {"action": "scroll"}, "semantic": {}}
        assert classify_consequence(step, _POLICY) == "read_only"

    def test_ordinary_click_is_reversible(self):
        step = {"action": {"action": "click"}, "semantic": {"final_intent": "open_menu"}}
        assert classify_consequence(step, _POLICY) == "reversible"

    def test_fill_is_never_irreversible_even_with_destructive_text(self):
        # Irreversible mirrors destructive_compiler_step's click-only gate — a fill/select can
        # feed a later irreversible click but is not itself the point of no return.
        step = {"action": {"action": "fill"}, "semantic": {"final_intent": "delete_confirmation_text"}}
        assert classify_consequence(step, _POLICY) != "irreversible"


class TestSerializerWiring:
    """The bug PROD-3 fixes: identity_bundle.destructive was always False (RecordedEvent has no
    `destructive` field), and even a True value never reached the runtime step's top-level
    `destructive` key that cascade.js actually reads."""

    def test_destructive_identity_bundle_reaches_top_level_execution_step(self):
        step = {"identity_bundle": {"destructive": True, "signals": []}}
        out = _copy_saved_common(step, {"type": "click", "selector": "#delete-btn"})
        assert out["destructive"] is True

    def test_non_destructive_identity_bundle_omits_the_flag(self):
        step = {"identity_bundle": {"destructive": False, "signals": []}}
        out = _copy_saved_common(step, {"type": "click", "selector": "#save-btn"})
        assert "destructive" not in out

    def test_entity_binding_reaches_top_level_execution_step(self):
        step = {
            "identity_bundle": {"destructive": True, "signals": []},
            "entity_binding": {
                "container_selector": "table#invoices tr",
                "identifier": "Invoice #12345",
                "source": "literal",
                "confirmed": True,
            },
        }
        out = _copy_saved_common(step, {"type": "click", "selector": "#delete-btn"})
        assert out["entity_binding"]["identifier"] == "Invoice #12345"

    def test_incomplete_entity_binding_is_not_emitted(self):
        step = {"entity_binding": {"container_selector": "", "identifier": "", "source": "literal"}}
        out = _copy_saved_common(step, {"type": "click", "selector": "#delete-btn"})
        assert "entity_binding" not in out


# --- when a row binding is worth having (W3: the saucedemo and "Buy Now" cases from the 2026-11-15 run) ---------------
_SHOP = [
    ("Sauce Labs Backpack", "$29.99"), ("Sauce Labs Bike Light", "$9.99"), ("Sauce Labs Bolt T-Shirt", "$15.99"),
    ("Sauce Labs Fleece Jacket", "$49.99"), ("Sauce Labs Onesie", "$7.99"), ("Test.allTheThings() T-Shirt", "$15.99"),
]


def _shop_dom() -> str:
    items = "".join(
        f'<div class="inventory_item"><div class="inventory_item_name">{n}</div>'
        f'<div class="inventory_item_desc">Great product number {i} in the catalogue for everyday carrying</div>'
        f'<div class="inventory_item_price">{p}</div>'
        f'<button class="btn" data-test="add-to-cart-{i}">Add to cart</button></div>'
        for i, (n, p) in enumerate(_SHOP)
    )
    return f'<html><body><div class="inventory_list">{items}</div></body></html>'


def _shop_event(index: int) -> dict:
    n, p = _SHOP[index]
    row_html = (f'<div class="inventory_item"><div class="inventory_item_name">{n}</div>'
                f'<div class="inventory_item_price">{p}</div><button class="btn">Add to cart</button></div>')
    # The recorder cuts the parent's html at 2000 characters, so a long list shows only its first rows.
    parent_html = _shop_dom().split('<div class="inventory_list">')[1][:1500]
    return {
        "action": {"action": "click", "value": None},
        "target": {"inner_text": "Add to cart", "tag": "button", "role": "button"},
        "ancestors": [
            {"tag": "div", "id": "", "classes": ["inventory_item"], "outer_html": row_html},
            {"tag": "div", "id": "", "classes": ["inventory_list"], "outer_html": f'<div class="inventory_list">{parent_html}'},
            {"tag": "body", "id": "", "classes": [], "outer_html": ""},
        ],
    }


class TestBindingOnlyWhereItTellsRowsApart:
    def test_a_price_two_products_share_is_never_the_identifier(self):
        eb = detect_entity_binding(_shop_event(2), dom_html=_shop_dom())  # Bolt T-Shirt, $15.99 twice in the list
        assert eb is not None and eb.identifier == "Sauce Labs Bolt T-Shirt"

    def test_the_old_choice_is_gone_even_when_the_list_is_cut_short_in_the_ancestor_chain(self):
        # Without the full page the cut-off list hides the second $15.99; the page snapshot is what exposes it.
        with_page = detect_entity_binding(_shop_event(2), dom_html=_shop_dom())
        assert "$15.99" not in with_page.identifier

    def test_a_harmless_step_with_its_own_unique_address_gets_no_binding(self):
        assert detect_entity_binding(_shop_event(2), dom_html=_shop_dom(), address_unique=True) is None

    def test_a_step_that_can_do_harm_keeps_its_binding_even_with_a_unique_address(self):
        eb = detect_entity_binding(_shop_event(1), dom_html=_shop_dom(), address_unique=True, destructive=True)
        assert eb is not None and eb.identifier == "Sauce Labs Bike Light"

    def test_without_a_page_snapshot_a_unique_address_is_not_trusted(self):
        # Nothing proves the address is unique when no page was kept, so the row stays.
        assert detect_entity_binding(_shop_event(2), address_unique=True) is not None

    def test_the_controls_own_label_is_not_the_row_identity(self):
        # "Buy Now" is on the button itself; the heading beside it is all there is to name the row.
        row = '<div class="card"><h2>Recovery fixture</h2><button>Buy Now</button></div>'
        other = '<div class="card"><h2>Second offer</h2><button>Buy Now</button></div>'
        parent = f'<div id="app">{row}{other}</div>'
        ev = {
            "action": {"action": "click", "value": None},
            "target": {"inner_text": "Buy Now", "tag": "button", "role": "button"},
            "ancestors": [
                {"tag": "div", "id": "", "classes": ["card"], "outer_html": row},
                {"tag": "div", "id": "app", "classes": [], "outer_html": parent},
                {"tag": "body", "id": "", "classes": [], "outer_html": ""},
            ],
        }
        eb = detect_entity_binding(ev)
        assert eb is not None and eb.identifier.lower() != "buy now"

    def test_a_harmless_step_with_nothing_that_names_its_row_gets_no_binding(self):
        row = '<li class="r"><span>Same</span><button>Go</button></li>'
        ev = {
            "action": {"action": "click", "value": None},
            "target": {"inner_text": "Go", "tag": "button", "role": "button"},
            "ancestors": [
                {"tag": "li", "id": "", "classes": ["r"], "outer_html": row},
                {"tag": "ul", "id": "l", "classes": [], "outer_html": f'<ul id="l">{row}{row}</ul>'},
                {"tag": "body", "id": "", "classes": [], "outer_html": ""},
            ],
        }
        assert detect_entity_binding(ev) is None
        assert detect_entity_binding(ev, destructive=True) is not None  # kept, unconfirmed, for a person to check

    def test_a_row_cut_out_of_the_list_is_still_named_by_its_own_text_not_a_shared_price(self):
        # WF-3 Leg B cart: the ancestor chain kept only the first rows. The Bolt T-Shirt row was not among them, the red
        # tee ($15.99 too) was, so the price looked unique and the name looked absent. Without a page snapshot.
        def row(i: int) -> str:
            n, p = _SHOP[i]
            return (f'<div class="cart_item"><div class="name">{n}</div><div class="price">{p}</div>'
                    f'<button class="btn">Remove</button></div>')
        ev = {
            "action": {"action": "click", "value": None},
            "target": {"inner_text": "Remove", "tag": "button", "role": "button"},
            "ancestors": [
                {"tag": "div", "id": "", "classes": ["cart_item"], "outer_html": row(2)},
                {"tag": "div", "id": "", "classes": ["cart_list"], "outer_html": f'<div class="cart_list">{row(5)}{row(4)}'},
                {"tag": "body", "id": "", "classes": [], "outer_html": ""},
            ],
        }
        eb = detect_entity_binding(ev, destructive=True)
        assert eb is not None and eb.identifier == "Sauce Labs Bolt T-Shirt"


# WF-3 D r2 (ParaBank, link clicks with no saved page): 22 steps were bound to rows that did not exist or did not last.
_ACCOUNT_ROWS = [
    '<tr><td><a href="activity.htm?id=12567">12567</a></td><td>$136.46</td><td>$136.46</td></tr>',
    '<tr><td><a href="activity.htm?id=12678">12678</a></td><td>-$105.31</td><td>$0.00</td></tr>',
    '<tr><td><a href="activity.htm?id=12789">12789</a></td><td>$123.33</td><td>$123.33</td></tr>',
]


def _account_link_event(destructive_intent: bool = False) -> dict:
    return {
        "action": {"action": "click", "value": None},
        "target": {"tag": "a", "role": "link", "inner_text": "12567"},
        "semantic": {"role": "link"},
        "ancestors": [
            {"tag": "td", "id": "", "classes": [], "outer_html": '<td><a href="activity.htm?id=12567">12567</a></td>'},
            {"tag": "tr", "id": "", "classes": [], "outer_html": _ACCOUNT_ROWS[0]},
            {"tag": "tbody", "id": "", "classes": [], "outer_html": "<tbody>" + "".join(_ACCOUNT_ROWS) + "</tbody>"},
        ],
    }


class TestNoBindingWhereNoRowIsNeeded:
    def test_side_by_side_layout_panels_are_not_rows(self):
        panels = '<div id="leftPanel"><ul><li><a>Accounts Overview</a></li><li><a>Update Contact Info</a></li></ul></div><div id="rightPanel"><p>Welcome</p></div>'
        ev = {
            "action": {"action": "click", "value": None},
            "target": {"tag": "a", "role": "link", "inner_text": "Accounts Overview"},
            "semantic": {"role": "link"},
            "ancestors": [
                {"tag": "li", "id": "", "classes": [], "outer_html": "<li><a>Accounts Overview</a></li>"},
                {"tag": "ul", "id": "", "classes": [], "outer_html": "<ul><li><a>Accounts Overview</a></li><li><a>Update Contact Info</a></li></ul>"},
                {"tag": "div", "id": "leftPanel", "classes": [], "outer_html": panels.split('<div id="rightPanel">')[0]},
                {"tag": "div", "id": "bodyPanel", "classes": [], "outer_html": f'<div id="bodyPanel">{panels}</div>'},
            ],
        }
        assert detect_entity_binding(ev) is None

    def test_a_link_named_by_its_own_row_text_needs_no_binding(self):
        assert detect_entity_binding(_account_link_event()) is None

    def test_a_step_that_can_do_harm_keeps_its_row_binding(self):
        assert detect_entity_binding(_account_link_event(), destructive=True) is not None

    def test_a_short_recorded_value_inside_a_balance_is_not_the_row_input(self):
        from conxa_core.models.skill_spec import EntityBinding

        class _Step:
            entity_binding = EntityBinding(container_selector="tbody > tr", identifier="$136.46", source="literal", confirmed=False)

        step = _Step()
        upgrade_entity_binding_identifiers([step], {"account_type": "1", "amount": "136"})
        assert (step.entity_binding.identifier, step.entity_binding.source) == ("$136.46", "literal")


# ── WF-7: search, then act on the one result ───────────────────────────────────────────────────────────────────

_CAROL = ('<tr data-id="103"><td>Carol</td><td class="em">carol@example.com</td>'
          '<td><button class="edit" type="button">Edit</button> <button class="del" type="button">Delete</button></td></tr>')


def _search_result_event(row_html: str = _CAROL, body: str | None = None) -> dict:
    """The Edit button on the only row a search left in the table (WF-7 7.6)."""
    return {
        "action": {"action": "click", "value": None},
        "target": {"tag": "button", "classes": ["edit"], "inner_text": "Edit", "role": "button"},
        "ancestors": [
            {"tag": "td", "id": None, "classes": [], "outer_html": '<td><button class="edit" type="button">Edit</button></td>'},
            {"tag": "tr", "id": None, "classes": [], "outer_html": row_html},
            {"tag": "tbody", "id": None, "classes": [], "outer_html": f"<tbody>{body or row_html}</tbody>"},
            {"tag": "table", "id": None, "classes": [], "outer_html": ""},
        ],
    }


def _typed(value: str, *, input_type: str = "text", parent_html: str = "") -> dict:
    return {"action": {"action": "type", "value": value}, "semantic": {"input_type": input_type},
            "target": {"tag": "input"}, "ancestors": [{"tag": "label", "outer_html": parent_html}]}


class TestSearchedRowBinding:
    def test_the_only_row_a_search_found_is_bound_to_the_searched_value(self):
        """It was left unbound (one row is "not a list"), so a search that found nothing clicked an Edit elsewhere."""
        eb = detect_entity_binding(_search_result_event(), typed_before=typed_values([_typed("Carol")]))
        assert eb is not None and eb.identifier == "Carol" and eb.container_selector == "tbody > tr"
        assert eb.confirmed is False

    def test_a_unique_address_does_not_skip_the_searched_row(self):
        eb = detect_entity_binding(_search_result_event(), dom_html="<html></html>", address_unique=True,
                                   typed_before=["Carol"])
        assert eb is not None and eb.identifier == "Carol"

    def test_it_becomes_the_run_input_the_search_types(self):
        from types import SimpleNamespace

        step = SimpleNamespace(entity_binding=detect_entity_binding(_search_result_event(), typed_before=["Carol"]))
        upgrade_entity_binding_identifiers([step], {"search_by_name": "Carol"})
        assert step.entity_binding.identifier == "{{search_by_name}}" and step.entity_binding.source == "input"

    def test_one_row_and_nothing_typed_stays_unbound(self):
        assert detect_entity_binding(_search_result_event()) is None
        assert detect_entity_binding(_search_result_event(), typed_before=["Dave"]) is None  # not this row's text

    def test_part_of_a_word_is_not_the_searched_value(self):
        assert detect_entity_binding(_search_result_event(), typed_before=["Car"]) is None

    def test_it_counts_the_saved_page_rows_that_show_the_value(self):
        """BUILD-109 (1): twin rows the value cannot tell apart are counted so the questions can warn about them."""
        twin = _CAROL.replace("103", "104").replace("carol@", "carol2@")
        carolann = _CAROL.replace("103", "105").replace("Carol", "Carolann").replace("carol@", "ann@")  # part of a word does not count
        page = f"<html><body><table><tbody>{_CAROL}{twin}{carolann}</tbody></table></body></html>"
        eb = detect_entity_binding(_search_result_event(body=_CAROL + twin), dom_html=page, typed_before=["Carol"])
        assert eb.recorded_matches == 2
        one = f"<html><body><table><tbody>{_CAROL}</tbody></table></body></html>"
        assert detect_entity_binding(_search_result_event(), dom_html=one, typed_before=["Carol"]).recorded_matches == 1
        assert detect_entity_binding(_search_result_event(), typed_before=["Carol"]).recorded_matches == 0  # no page

    def test_passwords_and_keys_are_never_a_row_identity(self):
        assert typed_values([_typed("Carol", input_type="password"), _typed("x"),
                             {"action": {"action": "keyboard_shortcut", "value": "Carol"}}]) == []

    def test_text_the_workflow_types_over_never_names_the_row(self):
        """The row was named by the email the run itself edits, so the next run could not find it (WF-7 S2)."""
        rows = "".join(_CAROL.replace("103", str(n)).replace("Carol", name).replace("carol", name.lower())
                       for n, name in ((101, "Alice"), (102, "Bob"), (103, "Carol")))
        ev = _search_result_event(body=rows)
        assert detect_entity_binding(ev).identifier == "carol@example.com"  # the longest unique text, by default
        edited = edited_values([_typed("carol.s@example.com",
                                       parent_html='<td class="em"><input class="emi" value="carol@example.com"></td>')])
        assert edited == {"carol@example.com"}
        assert detect_entity_binding(ev, edited=edited).identifier == "Carol"


def test_a_refused_row_says_whether_it_was_missing_or_matched_more_than_once():
    """WF-7 S2: two matching rows were reported as "wasn't found"."""
    from conxa_compile.conversation.failure_summary import summarize

    several = summarize("Step 5 (click) failed: Bound record matches more than one row on the page — refusing to guess "
                        "which one to act on", "entity_not_found")
    missing = summarize("Step 5 (click) failed: Bound record could not be uniquely located on the page — refusing to "
                        "act on a different row", "entity_not_found")
    assert "More than one row" in several and "isn’t on the page" in missing


# ── WF-7 7.1: edit the email on Carol's row (third of five) ────────────────────────────────────────────────────

_NAMES = ("Alice", "Bob", "Carol", "Dave", "Eve")


def _people(names=_NAMES, *, carol_editing: bool = False) -> str:
    out = []
    for n, name in enumerate(names, start=101):
        email = f"{name.lower()}@example.com"
        if carol_editing and name == "Carol":  # the page is saved just after the click: her row is already open
            out.append(f'<tr data-id="{n}"><td>{name}</td><td class="em"><input class="emi" placeholder="Email" '
                       f'value="{email}"/></td><td><button class="save" type="button">Save</button> '
                       f'<button class="del" type="button">Delete</button></td></tr>')
        else:
            out.append(_CAROL.replace("103", str(n)).replace("Carol", name).replace("carol", name.lower()))
    return "".join(out)


_EDITED = {"carol@example.com"}


def test_the_edited_row_is_named_by_the_text_the_run_leaves_alone_even_on_a_page_saved_after_the_click():
    """The saved page showed Carol's row already open (no email text, Save instead of Edit). It counted as another row
    named "Carol", the email was rightly excluded, and nothing was left: Edit went unbound and edited Bob's row."""
    ev = _search_result_event(body=_people())
    dom = f"<html><body><table><tbody>{_people(carol_editing=True)}</tbody></table></body></html>"
    eb = detect_entity_binding(ev, dom_html=dom, edited=_EDITED)
    assert eb is not None and eb.identifier == "Carol" and eb.container_selector == "tbody > tr"


def test_a_repeated_row_action_is_told_apart_from_a_control_that_names_its_row():
    from conxa_compile.compiler.entity_binding import row_action_in_list

    assert row_action_in_list(_search_result_event(body=_people())) is True             # every row has an Edit
    assert row_action_in_list(_search_result_event()) is False                          # one row: nothing to mix up
    save = _search_result_event(row_html=_people(("Carol",), carol_editing=True), body=_people(carol_editing=True))
    save["target"] = {"tag": "button", "classes": ["save"], "inner_text": "Save", "role": "button"}
    assert row_action_in_list(save) is False                                            # the one Save names its row


def test_an_unbound_row_action_keeps_no_signal_that_finds_it_by_position(tmp_path, monkeypatch):
    """Backstop: when no text names the row, the third Edit is not "the row's Edit" after the list re-sorts. Without
    the positional path the remaining look-alike signals tie, and the runtime refuses instead of acting."""
    from conxa_compile.compiler.build import _build_step
    from conxa_compile.policy.bundle import get_policy_bundle

    same = "".join(f'<tr><td>Pending</td><td><button class="edit" type="button">Edit</button></td></tr>' for _ in range(3))
    ev = _search_result_event(row_html='<tr><td>Pending</td><td><button class="edit" type="button">Edit</button></td></tr>',
                              body=same)
    ev.update({"tab": {"id": "tab_0", "index": 0}, "page": {"url": "http://localhost/rows.html", "title": ""},
               # (no role: without the saved accessibility tree a role signal cannot be counted, so it reads as unique)
               "target": {"tag": "button", "classes": ["edit"], "inner_text": "Edit"},
               "selectors": {"text_based": 'text="Edit"', "xpath": "/html[1]/body[1]/table[1]/tbody[1]/tr[3]/td[2]/button[1]"}})
    ev["action"]["timestamp"] = "2026-11-18T00:00:00Z"
    from conxa_compile.compiler import build

    monkeypatch.setattr(build, "_load_step_snapshot", lambda _ev, _sid: (f"<html><body><table><tbody>{same}</tbody></table></body></html>", None))
    step = _build_step(ev, get_policy_bundle(), session_root=tmp_path, step_index=0)
    assert step.entity_binding is None
    engines = {s.engine for s in step.identity_bundle.signals}
    assert "xpath" not in engines and engines, engines
    # a control with nothing to confuse it with keeps its path
    lone = dict(ev, ancestors=_search_result_event()["ancestors"])
    monkeypatch.setattr(build, "_load_step_snapshot", lambda _ev, _sid: (f"<html><body><table><tbody>{_CAROL}</tbody></table></body></html>", None))
    assert "xpath" in {s.engine for s in _build_step(lone, get_policy_bundle(), session_root=tmp_path, step_index=0).identity_bundle.signals}


def test_an_upgraded_row_name_keeps_the_text_around_the_typed_value():
    from conxa_compile.compiler.entity_binding import template_identifier

    assert template_identifier("Invoice #12345", "12345", "invoice_no") == "Invoice #{{invoice_no}}"
    assert template_identifier("Carol", "carol", "search_by_name") == "{{search_by_name}}"
    assert template_identifier("Carolann", "Carol", "x") == "Carolann"            # never part of a word


# ── E2E16-8: the same record in two panels, and a picked suggestion that is the clicked target itself ───────────


def _panel(pid: str, rows: str, *, cls: str = "") -> str:
    attrs = f' id="{pid}"' if pid else ""
    attrs += f' class="{cls}"' if cls else ""
    return f"<section{attrs}><table><tbody>{rows}</tbody></table></section>"


def _carol_in_panel(rows: str, *, pid: str = "results", cls: str = "") -> dict:
    ev = _search_result_event(body=rows)
    ev["ancestors"] += [{"tag": "section", "id": pid, "classes": [cls] if cls else [], "outer_html": ""},
                        {"tag": "body", "id": "", "classes": [], "outer_html": ""}]
    return ev


def test_the_same_record_in_two_panels_is_bound_in_the_panel_it_was_recorded_in():
    """BUILD-108 (3): "Results" and "Recently edited" both show Carol under `tbody > tr`, so every run refused as
    "more than one row". The list is scoped by the nearest ancestor that leaves only one list's rows."""
    others = "".join(_CAROL.replace("103", str(n)).replace("Carol", name).replace("carol", name.lower())
                     for n, name in ((101, "Alice"), (102, "Bob")))
    page = f"<html><body>{_panel('results', others + _CAROL)}{_panel('recent', _CAROL)}</body></html>"
    eb = detect_entity_binding(_carol_in_panel(others + _CAROL), dom_html=page, typed_before=["Carol"])
    assert eb.container_selector == "#results tbody > tr" and eb.identifier == "Carol"
    # the row-text path (a Delete with nothing typed) is scoped the same way, so Carol counts once
    eb = detect_entity_binding(_carol_in_panel(others + _CAROL), dom_html=page, destructive=True)
    assert eb.container_selector == "#results tbody > tr"
    # no saved page, or one list only: unchanged
    assert detect_entity_binding(_carol_in_panel(_CAROL), typed_before=["Carol"]).container_selector == "tbody > tr"
    one = f"<html><body>{_panel('results', _CAROL)}</body></html>"
    assert detect_entity_binding(_carol_in_panel(_CAROL), dom_html=one, typed_before=["Carol"]).container_selector == "tbody > tr"
    # two panels nothing tells apart keep the plain list: the runtime still refuses rather than guess
    same = f"<html><body>{_panel('', _CAROL, cls='panel')}{_panel('', _CAROL, cls='panel')}</body></html>"
    eb = detect_entity_binding(_carol_in_panel(_CAROL, pid="", cls="panel"), dom_html=same, typed_before=["Carol"])
    assert eb.container_selector == "tbody > tr"


# The click on OrangeHRM's employee suggestion, as the recorder saves it (session 56ac04e6, WF-4 Leg A): the option
# itself is the target, so the chain starts at the list.
_ORANGE_LIST = ('<div data-v-3ebc98ba="" data-v-390abb6d="" role="listbox" class="oxd-autocomplete-dropdown --positon-bottom">'
                '<div data-v-da59eaf4="" data-v-390abb6d="" role="option" class="oxd-autocomplete-option">'
                '<span data-v-08362132="">Amelia  Brown</span><!----><!----></div></div>')


def _orangehrm_pick() -> dict:
    return {
        "action": {"action": "click", "value": None},
        "semantic": {"role": "option"},
        "target": {"tag": "div", "id": None, "classes": ["oxd-autocomplete-option"], "inner_text": "Amelia Brown",
                   "role": "option", "attributes": {"role": "option"}},
        "ancestors": [
            {"tag": "div", "id": "", "classes": ["oxd-autocomplete-dropdown", "--positon-bottom"], "outer_html": _ORANGE_LIST},
            {"tag": "div", "id": "", "classes": ["oxd-autocomplete-wrapper"], "outer_html": f'<div class="oxd-autocomplete-wrapper">{_ORANGE_LIST}</div>'},
            {"tag": "div", "id": "", "classes": [], "outer_html": ""},
        ],
    }


def test_a_picked_suggestion_that_is_the_clicked_target_gets_the_inert_pick_match_binding():
    """BUILD-102 (6): the binding was never seen on the live OrangeHRM page because only the target's parents were
    searched for a row, and the parent of a clicked option is the list."""
    from types import SimpleNamespace

    eb = detect_entity_binding(_orangehrm_pick(), typed_before=["Amelia"])
    assert eb is not None and (eb.match, eb.self_target, eb.confirmed) == ("contains", True, False)
    assert eb.container_selector == '[role="listbox"] > [role="option"]' and eb.identifier == "Amelia"
    step = SimpleNamespace(entity_binding=eb)
    upgrade_entity_binding_identifiers([step], {"employee_name": "Amelia"})
    assert step.entity_binding.identifier == "{{employee_name}}"
    # the option is exactly what was typed, or nothing typed shows in it: not bound (that would change what is clicked)
    assert detect_entity_binding(_orangehrm_pick(), typed_before=["Amelia Brown"]) is None
    assert detect_entity_binding(_orangehrm_pick(), typed_before=["Asha"]) is None


# ── BUILD-101 (7): a captured value whose words change every run is bound by its place, inert until a yes ───────────


def _result(name: str, price: str) -> str:
    return f'<li class="result"><a>{name}</a><span class="price">{price}</span></li>'


_RESULTS = _result("Red mug", "Rs. 400") + _result("Blue mug", "Rs. 550") + _result("Tall mug", "Rs. 610")


def _price_capture(price: str = "Rs. 400", *, action: str = "extract", source: str = "text") -> dict:
    return {
        "action": {"action": action, "value": None},
        "extras": {"extract": {"source": source}},
        "target": {"tag": "span", "classes": ["price"], "inner_text": price},
        "ancestors": [
            {"tag": "li", "id": None, "classes": ["result"], "outer_html": _result("Red mug", "Rs. 400") if price == "Rs. 400" else _result("Blue mug", price)},
            {"tag": "ul", "id": None, "classes": ["results"], "outer_html": f'<ul class="results">{_RESULTS}</ul>'},
            {"tag": "body", "id": None, "classes": [], "outer_html": ""},
        ],
    }


def test_a_captured_value_in_one_list_is_bound_by_its_place_and_left_unconfirmed():
    page = f'<html><body><ul class="results">{_RESULTS}</ul></body></html>'
    eb = detect_entity_binding(_price_capture(), dom_html=page)
    assert (eb.container_selector, eb.position, eb.identifier, eb.confirmed) == ("ul.results > li.result", 1, "", False)
    assert detect_entity_binding(_price_capture("Rs. 550"), dom_html=page).position == 2


def test_no_place_binding_without_a_saved_page_one_list_or_a_value():
    page = f'<html><body><ul class="results">{_RESULTS}</ul></body></html>'
    assert detect_entity_binding(_price_capture()) is None                                  # no saved page
    two = f'<html><body><ul class="results">{_RESULTS}</ul><ul class="results">{_RESULTS}</ul></body></html>'
    assert detect_entity_binding(_price_capture(), dom_html=two) is None                    # two lists: "first" is a guess
    assert detect_entity_binding(_price_capture(action="click"), dom_html=page) is None     # not a captured value
    assert detect_entity_binding(_price_capture(source="value"), dom_html=page) is None
    label = _price_capture()
    label["target"]["inner_text"] = "Price"                                                 # a label, not a value
    assert detect_entity_binding(label, dom_html=page).position == 0  # found by its words as before, never by place
