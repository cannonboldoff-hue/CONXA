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


def test_a_workflow_missing_from_the_built_package_is_not_fresh(tmp_path):
    from handlers.test_flow import _built_package_has

    (tmp_path / "skills" / "mine-1").mkdir(parents=True)
    (tmp_path / "skills" / "mine-1" / "execution.json").write_text("{}")
    assert _built_package_has(str(tmp_path), "mine-1") is True
    assert _built_package_has(str(tmp_path), "never-built-2") is False
    assert _built_package_has(str(tmp_path / "nope"), "mine-1") is False
    assert _built_package_has(str(tmp_path), "") is False
