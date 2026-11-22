"""The model's step names come back as a list lined up by position. A name that belongs to a neighbouring step is a
line shifted by one (BUILD-93 (5), BUILD-95 (3)): it is dropped for the step's own wording."""

from __future__ import annotations

from types import SimpleNamespace

from conxa_compile.compiler.build import _apply_workflow_review_to_steps, _prose_borrows_from_neighbour, _step_own_words
from conxa_core.models.skill_spec import SkillStep


def _step(text: str = "", row: str = "") -> SkillStep:
    step = SkillStep(action={"action": "click"}, intent="click")
    step.identity_bundle = SimpleNamespace(fingerprint=SimpleNamespace(inner_text=text, aria_label="", placeholder="", label_text="", value_label="", alt="", title=""))
    step.entity_binding = SimpleNamespace(identifier=row) if row else None
    return step


def test_a_name_from_the_next_row_is_not_taken_by_this_step():
    own = [_step("Add to cart", "Sauce Labs Bolt T-Shirt"), _step("Add to cart", "Sauce Labs Onesie")]
    words = [_step_own_words(s) for s in [own[0], own[1]]]
    assert _prose_borrows_from_neighbour("Add the bolt t-shirt", words, 1) is True
    assert _prose_borrows_from_neighbour("Add the bolt t-shirt", words, 0) is False
    assert _prose_borrows_from_neighbour("Add the onesie to the cart", words, 1) is False


def test_a_name_with_no_foreign_words_is_kept():
    words = [_step_own_words(_step("Save")), _step_own_words(_step("Cancel"))]
    assert _prose_borrows_from_neighbour("Click Save to keep your changes", words, 0) is False


def test_shifted_names_fall_back_to_the_steps_own_wording():
    steps = [_step("Add to cart", "Sauce Labs Bolt T-Shirt"), _step("Add to cart", "Sauce Labs Onesie")]
    graph = SimpleNamespace(steps=[
        SimpleNamespace(index=0, intent="Add the bolt t-shirt", intent_token="add_bolt"),
        SimpleNamespace(index=1, intent="Add the bolt t-shirt again", intent_token="add_bolt_again"),
    ])
    _apply_workflow_review_to_steps(steps, graph)
    assert steps[0].semantic_description == "Add the bolt t-shirt"
    assert steps[1].semantic_description != "Add the bolt t-shirt again"


def test_a_name_of_another_like_button_is_dropped_even_far_away():
    # saucedemo (BUILD-95 (3)): six "Add to cart" buttons told apart only by their test id; the video reading put
    # the bolt t-shirt's name on the onesie's button, several steps away from the bolt t-shirt's own click.
    def add(testid: str) -> SkillStep:
        step = _step("Add to cart")
        step.identity_bundle.fingerprint.data_testid = testid
        return step

    steps = [add("add-to-cart-sauce-labs-bolt-t-shirt"), _step("Open Menu"), _step("Checkout"), _step("Continue"),
             add("add-to-cart-sauce-labs-onesie"), add("add-to-cart-sauce-labs-onesie")]
    graph = SimpleNamespace(steps=[
        SimpleNamespace(index=0, intent="Add the bolt t-shirt to the cart", intent_token=""),
        SimpleNamespace(index=4, intent="Add the bolt t-shirt to the cart", intent_token=""),
        SimpleNamespace(index=5, intent="Add the onesie to the shopping cart again", intent_token=""),
    ])
    _apply_workflow_review_to_steps(steps, graph)
    assert steps[0].semantic_description == "Add the bolt t-shirt to the cart"
    assert steps[4].semantic_description != "Add the bolt t-shirt to the cart"
    # Its own item, and words no like button owns ("shopping"), keep the model's name; so does a repeat click.
    assert steps[5].semantic_description == "Add the onesie to the shopping cart again"


def test_a_captured_value_is_named_by_its_label_not_by_the_value():
    from conxa_compile.compiler.build import _extract_label

    ev = {"extras": {"section_heading": "Order summary"}, "anchors": [{"element": "Total"}]}
    assert _extract_label(ev, {"inner_text": "Rs. 400"}) == "Order summary"
    assert _extract_label(ev, {"inner_text": "Order total"}) == "Order total"
    assert _extract_label({"anchors": [{"element": "Total"}]}, {"inner_text": "rs_400 9"}) == "Total"
    assert _extract_label(ev, {"aria_label": "Price"}) == "Price"


def test_the_live_list_names_an_element_as_the_compiler_does():
    from conxa_compile.recorder.plain import _name_of

    # demoqa: a search box beside an "Add" button is the search box.
    assert _name_of({"tag": "input", "placeholder": "Type to search", "label_text": "Add"}) == "Type to search"
    # An autocomplete suggestion is the suggestion, not the field caption.
    assert _name_of({"tag": "div", "inner_text": "Java", "label_text": "Tags:"}) == "Java"


def test_a_guessed_caption_does_not_name_the_input():
    from conxa_compile.compiler.input_binding import derive_input_binding

    def event(**target):
        return {"action": {"action": "type", "value": "ada"}, "target": {"tag": "input", **target}, "semantic": {}}

    # demoqa: the search box's caption was guessed from the "Add" button beside it.
    _, name = derive_input_binding(event(placeholder="Type to search", label_text="Add", label_nearby=True), {})
    assert name == "type_to_search"
    # A caption tied to the box by markup still wins.
    _, name = derive_input_binding(event(placeholder="name@example.com", label_text="Email", label_nearby=False), {})
    assert name == "email"
    # Recordings made before the flag keep the old order.
    _, name = derive_input_binding(event(placeholder="Type to search", label_text="Add"), {})
    assert name == "add"
