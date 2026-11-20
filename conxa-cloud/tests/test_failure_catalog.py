"""W7: a failure that reaches a person is one plain sentence from one catalog (conversation/failure_summary.py)."""

from __future__ import annotations

import re

import pytest

from conxa_compile.conversation import failure_summary as fs
from conxa_compile.conversation.actions import ACTIONS

SENTENCES = ([s for _p, s in fs._PATTERNS] + list(fs._BY_KIND.values()) + [fs._GENERIC]
             + [s for _p, s in fs._DOWNLOAD_REASONS] + [fs._DOWNLOAD_OTHER])


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


# WF-14: a download that got no usable file is marked bad input (to skip recovery), but the headline names the real reason.
@pytest.mark.parametrize("why, expect", [
    ("the downloaded file was empty or incomplete", "empty or cut short"),
    ("it did not finish downloading within 120 seconds", "took too long"),
    ("the download was interrupted before it finished", "stopped before"),
    ("none arrived within 120 seconds — the click before this step may not have triggered one", "No file started"),
    ("the download failed (HTTP 500)", "couldn’t be downloaded"),
])
def test_a_failed_download_names_its_reason_not_a_bad_value(why, expect):
    out = fs.summarize(f"Step 8 (download_observed) failed: Expected a file download at this step, but {why}.", "bad_input")
    assert expect in out and "values given" not in out


def test_a_real_bad_value_keeps_the_bad_input_sentence():
    raw = "Step 2 (upload) failed: this upload control accepts only one file, but 3 files were given"
    assert fs.summarize(raw, "bad_input") == fs._BY_KIND["bad_input"]
