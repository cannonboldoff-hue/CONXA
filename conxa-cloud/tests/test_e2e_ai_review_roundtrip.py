"""EXEC-13 end-to-end sanity check: insert scaffold -> author fills it in -> compiles."""
from __future__ import annotations
import json
import tempfile
from pathlib import Path

from conxa_compile.editor.workflow_mutations import _new_manual_step
from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill


def test_scaffold_through_to_compiled_execution_json():
    step = _new_manual_step("ai_review", "https://x.test")
    step["ai_review_prompt"] = "Is there an error banner on this page?"
    step["ai_review_on_failure"] = "use_default"
    step["ai_review_default_value"] = {"answer": "no", "why": "no answer received"}

    saved_skill = {
        "meta": {"id": "skill_x", "title": "AI Review E2E"},
        "inputs": [],
        "skills": [{"steps": [step]}],
    }
    with tempfile.TemporaryDirectory() as td:
        _build_workflow_from_saved_skill(bundle_root=Path(td), workflow_slug="ai_review_e2e", saved_skill=saved_skill)
        execution = json.loads((Path(td) / "skills" / "ai_review_e2e" / "execution.json").read_text())

    assert execution[0].pop("source_step_key")        # the document step this came from (maps a test result back)
    assert execution == [{
        "type": "ai_review",
        "prompt": "Is there an error banner on this page?",
        "on_failure": "use_default",
        "output_schema": {
            "type": "object",
            "required": ["answer", "why"],
            "properties": {
                "answer": {"type": "string", "enum": ["yes", "no"]},
                "why": {"type": "string"},
            },
        },
        "default_value": {"answer": "no", "why": "no answer received"},
        "output_name": "review_answer",
    }]
