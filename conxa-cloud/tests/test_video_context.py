"""Pegasus words stamped onto matched steps at compile."""

from __future__ import annotations

import json

from conxa_compile.compiler.video_context import apply_video_context
from conxa_core.models.skill_spec import SkillStep


def step(seq, **kw):
    return SkillStep(action="click", intent="click_button", source_event=seq, **kw)


def timeline(tmp_path, rows):
    (tmp_path / "merged_timeline.json").write_text(json.dumps(rows))


def event(n, **seg):
    return {"kind": "event", "event_id": n, "segment": seg or None}


def test_matched_steps_get_words_and_unmatched_stay_untouched(tmp_path):
    timeline(tmp_path, [
        event(1, intent="download the invoice PDF", visible_target="Download PDF button", screen_after="download bar", purpose="save it"),
        event(2),                                   # Pegasus missed it
        {"kind": "observation", "type": "off_page", "segment": {"intent": "ignored"}},
    ])
    steps = [step(1, semantic_description="old words"), step(2, semantic_description="kept"), step(None), step(9)]
    assert apply_video_context(steps, tmp_path) == 1
    assert steps[0].semantic_description == "download the invoice PDF"
    assert steps[0].video_context.visible_target == "Download PDF button" and steps[0].video_context.screen_before == ""
    assert steps[0].intent == "click_button"        # the intent token and every address field are untouched
    assert all(s.video_context is None for s in steps[1:]) and steps[1].semantic_description == "kept"


def test_no_timeline_or_a_broken_one_is_a_no_op(tmp_path):
    steps = [step(1)]
    assert apply_video_context(steps, tmp_path) == 0
    (tmp_path / "merged_timeline.json").write_text("{not json")
    assert apply_video_context(steps, tmp_path) == 0 and steps[0].video_context is None


def test_recovery_json_carries_the_words_and_skips_empty_contexts(tmp_path):
    from conxa_compile.skill_package_builder import _build_workflow_from_saved_skill

    ctx = {"visible_target": "Download PDF button", "screen_before": "", "screen_after": "Download bar shows the PDF", "purpose": ""}
    saved_skill = {
        "meta": {"id": "skill_1", "title": "Get invoice"}, "inputs": [],
        "skills": [{"steps": [
            {"action": "click", "intent": "download", "target": {"primary_selector": "#dl", "role": "button"}, "video_context": ctx},
            {"action": "click", "intent": "other", "target": {"primary_selector": "#o", "role": "button"},
             "video_context": {"visible_target": "", "screen_before": "", "screen_after": "", "purpose": ""}},
            {"action": "click", "intent": "plain", "target": {"primary_selector": "#p", "role": "button"}},
        ]}],
    }
    _build_workflow_from_saved_skill(bundle_root=tmp_path, workflow_slug="get_invoice", saved_skill=saved_skill)
    recovery = json.loads((tmp_path / "skills" / "get_invoice" / "recovery.json").read_text(encoding="utf-8"))
    assert recovery["steps"][0]["video_context"] == ctx
    assert "video_context" not in recovery["steps"][1] and "video_context" not in recovery["steps"][2]
