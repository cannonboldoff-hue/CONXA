"""An answer typed in the person's own words counts only when code can read it as one of the offered choices
(WF-5 5.2: 5,000 "a"s answered "Should someone say yes before it happens?" and no approval step was added)."""

from __future__ import annotations

import pytest

from conxa_compile.conversation.clarify import answer_from_words

YES_NO = {"multi": False, "choices": [{"id": "yes", "label": "Yes, ask for approval first"},
                                      {"id": "no", "label": "No, just do it"}]}
THREE = {"multi": False, "choices": [{"id": "same", "label": "Yes, carry on with the same steps"},
                                     {"id": "stop", "label": "No, stop there"},
                                     {"id": "end", "label": "No, that finishes the job"}]}
VALUES = {"multi": True, "choices": [{"id": "full_name", "label": "Full name"}, {"id": "user_email", "label": "User email"},
                                     {"id": "current_address", "label": "Current address"}]}


@pytest.mark.parametrize("q,text,want", [
    (YES_NO, "yes", {"choice": "yes"}),
    (YES_NO, "yes, ask first", {"choice": "yes"}),
    (YES_NO, "हाँ", {"choice": "yes"}),                       # Hindi
    (YES_NO, "नहीं", {"choice": "no"}),
    (YES_NO, "No, just do it", {"choice": "no"}),
    (YES_NO, "2", {"choice": "no"}),
    (THREE, "yes", {"choice": "same"}),
    (VALUES, "full name and user email", {"selected": ["full_name", "user_email"]}),
    (VALUES, "full name and email", {"selected": ["full_name", "user_email"]}),   # one option holds "email"
    ({"multi": False, "choices": [{"id": "real", "label": "Real customer details"},
                                  {"id": "fake", "label": "Made-up sample details"}]}, "made-up", {"choice": "fake"}),
    (VALUES, "none", {"selected": []}),
    (VALUES, "कोई नहीं", {"selected": []}),
    (VALUES, "all of them", {"selected": ["full_name", "user_email", "current_address"]}),
])
def test_words_that_name_one_choice_answer_it(q, text, want):
    assert answer_from_words(q, text) == (want, "")


@pytest.mark.parametrize("q,text", [
    (YES_NO, "a" * 5000),          # too long
    (YES_NO, "a" * 300),           # says nothing
    (YES_NO, "   "),               # blank
    (YES_NO, "yes, don't"),        # both yes and no
    (THREE, "no"),                 # two choices start with "No"
    (VALUES, "😀😀🎉"),
    (VALUES, "full name and phone"),   # "phone" is no option: never dropped silently
    (YES_NO, "yes, but only on weekdays"),   # more than an option says: the person's own wish, not "yes"
    (VALUES, "none, full name"),
])
def test_words_that_name_no_single_choice_are_not_read_as_one(q, text):
    answer, why = answer_from_words(q, text)
    assert answer is None and why


@pytest.mark.parametrize("text", ["", "   ", "a" * 600, "😀😀🎉", "yes, don't"])
def test_unusable_words_get_a_plain_reason_before_any_model_call(text):
    from conxa_compile.conversation.clarify import words_unusable

    assert words_unusable(YES_NO, text)


def test_a_real_wish_is_usable():
    from conxa_compile.conversation.clarify import words_unusable

    assert words_unusable(YES_NO, "ask my manager by email first") == ""


CONFIRM = {**YES_NO, "kind": "confirm"}


@pytest.mark.parametrize("text,want", [("no", "no"), ("नहीं", "no"), ("2", "no"), ("No, just do it", "no"), ("yes", "yes")])
def test_a_step_that_cant_be_undone_takes_only_an_exact_pick_or_a_bare_yes_or_no(text, want):
    assert answer_from_words(CONFIRM, text) == ({"choice": want}, "")


@pytest.mark.parametrize("text", [
    "wait ten seconds, then continue on its own",   # WF-5 E: was closed as "no", approval gone
    "only ask on the first run",                    # WF-5 D: was closed as "yes", "first run" dropped
    "no, just do it later",
    "just do it",
])
def test_anything_more_than_an_exact_pick_on_such_a_step_is_the_persons_own_wish(text):
    assert answer_from_words(CONFIRM, text)[0] is None


def test_a_model_mapping_is_taken_only_when_the_option_holds_every_word():
    from conxa_compile.conversation.clarify import adds_nothing

    yes = YES_NO["choices"][0]
    assert adds_nothing("yes, ask for approval", yes)
    assert not adds_nothing("only ask on the first run", yes)


@pytest.mark.parametrize("text,want", [("check the output shows the email I typed", True), ("Make sure it says Saved", True),
                                       ("also check the page title is ToolsQA", True),   # WF-5 I: was saved as screen words
                                       ("the URL should end in /done", True),
                                       ("Thanks for your order", False), ("Order placed", False), ("Order confirmed", False)])
def test_words_that_describe_a_check_are_told_from_words_to_look_for(text, want):
    from conxa_compile.conversation.clarify import describes_a_check

    assert describes_a_check(text) is want
