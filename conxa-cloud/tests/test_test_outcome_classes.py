"""A test run ends in one of three classes, and only one of them is a pass (BUILD-89/93, structural fix S2).

proven pass · unproven pass (a person must look before Approve) · could not run (not a result at all).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_compile.conversation import failure_summary as fs  # noqa: E402
from conxa_compile.conversation.lifecycle import derive_lifecycle  # noqa: E402
from handlers.workflows import _COULDNT_RUN_META_KEY, _TEST_RUN_ENV, _extract_couldnt_run, unproven_steps  # noqa: E402


def _wf(**kw):
    base = dict(session_id="s", recording_status="ok", skill_id="k", target_url="https://x", last_test_status="never",
                signed_off=False, compile_steps_with_warnings=0, compile_status="ok", last_test_outcome=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_unproven_steps_names_only_what_was_not_proven():
    steps = [{"index": 0, "intent": "Click Save", "verdict": "proven"},
             {"index": 1, "intent": "", "verdict": "couldnt_confirm"},
             {"index": 2, "intent": "Open the list", "verdict": "still_working"}]
    assert unproven_steps(steps) == ["Step 2", "Open the list"]


def test_couldnt_run_is_read_from_meta_only():
    assert _extract_couldnt_run({"_meta": {_COULDNT_RUN_META_KEY: {"kind": "page_size", "step_index": 3}}}) == {
        "kind": "page_size", "step_index": 3}
    assert _extract_couldnt_run({"content": [{"type": "text", "text": "not showing the page at its normal size"}]}) is None


def test_handover_timeout_is_a_couldnt_run_and_a_test_never_parks_a_handover():
    got = _extract_couldnt_run({"_meta": {_COULDNT_RUN_META_KEY: {"kind": "handover_timeout", "step_index": 2}}})
    assert got == {"kind": "handover_timeout", "step_index": 2}
    assert _TEST_RUN_ENV["CONXA_HANDOVER_INCALL_MS"] == "600000"
    assert _TEST_RUN_ENV["CONXA_HANDOVER_NO_PARK"] == "1"


def test_unconfirmed_pass_is_not_a_pass_and_offers_no_approve():
    lc = derive_lifecycle(_wf(last_test_outcome={"outcome": "done_please_check", "unproven_steps": ["Click Save"]}))
    assert lc["substate"] == "passed_unproven"
    assert "approve" not in lc["allowed_actions"]


def test_a_real_pass_still_offers_approve():
    lc = derive_lifecycle(_wf(last_test_status="passed"))
    assert "approve" in lc["allowed_actions"]


def test_package_build_failure_is_said_plainly():
    sentence = fs.summarize("Error: pyinstaller exploded", "package_build")
    assert "ready to test" in sentence and "nothing was run" in sentence
