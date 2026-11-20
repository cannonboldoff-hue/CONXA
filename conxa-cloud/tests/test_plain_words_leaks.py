"""No internal name reaches a person (BUILD-91 (4), BUILD-93 (8)(10), structural fix S5).

Every producer of person-facing words is fed awkward input and its output must pass the one gate (``plain_words.plain_ok``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SKILL_ALLOW_NO_PROVIDERS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

import pytest  # noqa: E402
from conxa_compile.conversation import failure_summary as fs  # noqa: E402
from conxa_compile.conversation.clarify import _assertion_words  # noqa: E402
from conxa_compile.conversation.plain_words import plain_ok, plain_or  # noqa: E402
from conxa_compile.editor.copilot_proposals import _plain_why, field_words  # noqa: E402
from conxa_core.models.prose_binding import say_text  # noqa: E402


@pytest.mark.parametrize("raw", ["“{{search_wikipedia}}”", '"{{search_wikipedia}}"', "{{search_wikipedia}}",
                                 "Results for “{{query}}”", "Thank you for your order!", "‘Hello’"])
def test_check_text_is_quoted_once_and_never_names_an_input_raw(raw):
    said = say_text(raw)
    assert plain_ok(said), said
    assert "““" not in said and "””" not in said and "{{" not in said


def test_assertion_words_for_a_quoted_input_token():
    step = {"validation": {"assertions": [{"required": True, "type": "text_present", "target": "“{{search_wikipedia}}”"}]}}
    assert _assertion_words(step) == ["the search wikipedia you entered appears"]


@pytest.mark.parametrize("field", ["entity_binding.confirmed", "validation.assertions", "some.deep_field.name", "intent", None])
def test_a_field_is_named_in_words(field):
    assert plain_ok(field_words(field)), field_words(field)


@pytest.mark.parametrize("message", ["Execution failed: Step 3 (click) failed: boom", "missing required input(s): a_b. Call get_skill_inputs first.",
                                     "navigation_error: Page.goto: net::ERR_NAME_NOT_RESOLVED", "", "weird"])
@pytest.mark.parametrize("kind", [None, "ambiguous", "package_build", "fault:page_size", "not_found"])
def test_failure_summary_never_leaks(message, kind):
    assert plain_ok(fs.summarize(message, kind)), (message, kind)


def test_a_model_reason_that_names_a_field_is_dropped_for_code_wording():
    assert _plain_why("Change entity_binding.confirmed on step 2") == ""
    assert _plain_why("It was typed in by mistake.") == "It was typed in by mistake."
    assert plain_or("validation.assertions changed", "A check changed.") == "A check changed."


def test_the_recap_may_not_talk_about_a_tab_the_workflow_never_opens():
    from conxa_compile.llm.compile_recap import _problem

    ok = "I built 3 steps: it opens the page, types the search words and presses Enter. Nothing was left out."
    facts = {"step_count": 3, "tabs": {"opened": 0, "switched": 0}}
    assert _problem(ok, facts) == ""
    said = "I built 3 steps: it opens the page and then clicks the second link, which opens in a new tab. Nothing left out."
    assert "tabs" in _problem(said, facts)
    assert _problem(said, {**facts, "tabs": {"opened": 1, "switched": 0}}) == ""
