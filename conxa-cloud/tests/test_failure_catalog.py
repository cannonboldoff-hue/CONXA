"""W7: a failure that reaches a person is one plain sentence from one catalog (conversation/failure_summary.py)."""

from __future__ import annotations

import re

import pytest

from conxa_compile.conversation import failure_summary as fs
from conxa_compile.conversation.actions import ACTIONS

SENTENCES = [s for _p, s in fs._PATTERNS] + list(fs._BY_KIND.values()) + [fs._GENERIC]


@pytest.mark.parametrize("sentence", SENTENCES)
def test_no_sentence_is_written_for_an_ai_client_or_a_developer(sentence):
    assert not re.search(r"\bCall \w+|get_skill_inputs|list_skills|\w+_\w+|\$\{|\bselector\b|\bDOM\b", sentence), sentence


def test_a_sentence_that_names_an_action_names_one_the_person_has():
    quoted = {m for s in SENTENCES for m in re.findall(r"“([^”]+)”", s)}
    labels = {a.label for a in ACTIONS.values()}
    for name in quoted & {"Build again", "Compile", "Test it"}:
        assert name in labels


@pytest.mark.parametrize("raw, expect", [
    ('Skill "wf" is missing required input(s): po_number. Call get_skill_inputs first.', "needs a value"),
    ("Skill not found: wrk. Call list_skills first.", "Build again"),
    ("navigation_error: Page.goto: net::ERR_X", "start page can’t be reached"),
    ("The test browser is not showing the page at its normal size, so a click ... Run the test again.", "minimized"),
    ("The window showing the test browser is minimized or hidden, so the page ...", "minimized or hidden"),
])
def test_runtime_messages_read_in_plain_words(raw, expect):
    out = fs.summarize(raw)
    assert expect in out and "Call " not in out and "get_skill_inputs" not in out
