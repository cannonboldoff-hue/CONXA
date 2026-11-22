"""W2: the step a failed test card names is the one the runtime says failed, not whichever progress row was last."""

from __future__ import annotations

import os
import sys

_PY_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python"))
sys.path.insert(0, _PY_DIR)

from handlers.test_flow import _pick_failing_row  # noqa: E402


def _rows():
    return [
        {"index": 0, "name": "Open the page", "status": "done", "step_key": "k0"},
        {"index": 1, "name": "Click Buy", "status": "running", "step_key": "k1"},
    ]


def test_runtime_step_key_wins_over_the_running_row():
    rows = _rows()
    # The result outran the progress line for step 2: the card must not blame step 1's row.
    rows[0]["status"], rows[1]["status"] = "running", "pending"
    got = _pick_failing_row(rows, "k1", 1)
    assert got["name"] == "Click Buy"
    assert rows[0]["status"] == "done"


def test_index_is_used_when_there_is_no_key():
    assert _pick_failing_row(_rows(), None, 0)["name"] == "Open the page"


def test_missing_row_is_made_from_the_step_names():
    rows = [{"index": 0, "name": "Open the page", "status": "running", "step_key": "k0"}]
    got = _pick_failing_row(rows, "k1", 1, {"k1": ("Click Buy", False)})
    assert got["name"] == "Click Buy" and rows[-1] is got and rows[0]["status"] == "done"


def test_without_runtime_answer_the_running_row_is_the_fallback():
    assert _pick_failing_row(_rows(), None, None)["name"] == "Click Buy"
    assert _pick_failing_row([], None, None) is None


def test_failure_card_lists_the_steps_that_already_changed_something():
    # BUILD-100: a test that fails partway names what already ran for real (a transfer sent before the failure).
    from handlers.test_flow import _changed_before_failure

    rows = [
        {"index": 0, "name": "Open the page", "status": "done", "step_key": "k0"},
        {"index": 1, "name": "Click Transfer", "status": "done", "step_key": "k1"},
        {"index": 2, "name": "Click Pay", "status": "done", "step_key": "k2", "destructive": True},
        {"index": 3, "name": "Type the payee", "status": "failed", "step_key": "k3"},
    ]
    names = {"k9": ("Confirm in the popup", True), "k3": ("Type the payee", False)}
    got = _changed_before_failure(rows, rows[3], ["k1", "k9", "k3"], names)
    assert got == ["Click Transfer", "Click Pay", "Confirm in the popup"]
    assert _changed_before_failure(rows[:1], None, [], {}) == []


def test_an_empty_read_value_is_said_plainly():
    from conxa_compile.conversation.failure_summary import summarize

    msg = "Execution failed at step 4: Step 4 (extract) failed: The element was found but it holds no value to extract."
    assert summarize(msg, "bad_input") == "The value this step reads was empty on the page, so the test stopped instead of using nothing."


def test_a_workflow_missing_from_the_built_package_is_not_fresh(tmp_path):
    from handlers.test_flow import _built_package_has

    (tmp_path / "skills" / "mine-1").mkdir(parents=True)
    (tmp_path / "skills" / "mine-1" / "execution.json").write_text("{}")
    assert _built_package_has(str(tmp_path), "mine-1") is True
    assert _built_package_has(str(tmp_path), "never-built-2") is False
    assert _built_package_has(str(tmp_path / "nope"), "mine-1") is False
    assert _built_package_has(str(tmp_path), "") is False
