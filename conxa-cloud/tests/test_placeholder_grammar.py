"""The Python placeholder grammar finds the same ids as the runtime's interpolate.js (shared fixture)."""
import json
from pathlib import Path

import pytest

from conxa_compile.editor.placeholder_grammar import PLACEHOLDER_RE, is_valid_placeholder_id

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "runtime" / "test" / "fixtures" / "placeholder_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_same_ids_as_the_runtime(case):
    assert [m.group(1) for m in PLACEHOLDER_RE.finditer(case["text"])] == case["ids"]


def test_an_input_is_never_called_by_a_dotted_name():
    assert is_valid_placeholder_id("to") and not is_valid_placeholder_id("row.to")
