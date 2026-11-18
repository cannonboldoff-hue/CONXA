"""Drawing on a canvas is said plainly, never compiled into clicks (WF-8 8.12, 2026-11-18).

An Excalidraw rectangle was recorded as clicks on the board around the canvas; Studio said the workflow "draws a
rectangle", the test could not confirm it, and Approve was still offered. The recorder now reports a canvas press or
drag as ``unsupported_gesture``; the compile leaves it out, and a question the person must answer says so."""

from __future__ import annotations

import json

from conxa_compile.compiler.build import prepare_compile_events
from conxa_compile.conversation import question_plan
from conxa_compile.conversation.clarify import generate_questions, plan_answer, unsupported_sentence
from conxa_compile.policy.bundle import get_policy_bundle
from conxa_compile.recorder.plain import describe_event
from handlers.session import _empty_reason

_PAGE = {"url": "https://excalidraw.example/"}


def _ev(seq: int, action: str, tag: str, label: str, value: str | None = None) -> dict:
    return {"action": {"action": action, "timestamp": "2026-11-18T10:00:00Z", **({"value": value} if value else {})},
            "target": {"tag": tag, "aria_label": label}, "selectors": {"css": f"#{tag}{seq}"},
            "page": _PAGE, "tab": _PAGE, "extras": {"sequence": seq, "element_ref": f"e{seq}"}}


_DRAG = json.dumps({"kind": "canvas", "moved": True})
_TAKE = [_ev(1, "click", "button", "Rectangle"), _ev(2, "unsupported_gesture", "canvas", "", _DRAG),
         _ev(3, "click", "button", "Selection"), _ev(4, "unsupported_gesture", "canvas", "", _DRAG)]


def test_canvas_gestures_are_left_out_and_must_be_acknowledged_before_anything_else():
    ledger: list[dict] = []
    kept = prepare_compile_events(_TAKE, get_policy_bundle().data, ledger)
    assert [e["extras"]["sequence"] for e in kept if e["action"]["action"] == "click"] == [1, 3]
    assert [(d["sequence"], d["pass"]) for d in ledger] == [(2, "unsupported"), (4, "unsupported")]

    doc = {"skills": [{"steps": [{"action": "click", "intent": "pick the rectangle tool"}]}]}
    qs = generate_questions(doc, dropped=ledger, limit=None)
    assert qs[0]["kind"] == "unsupported" and "these 2 actions were left out" in qs[0]["body"]
    assert [c["id"] for c in qs[0]["choices"]] == ["ok"]
    assert plan_answer(qs[0], {"choice": "ok"}, doc) == ([], "Kept without the drawing")
    # A safety-floor question: never assumed for the person, so it blocks test and approval until answered.
    planned, _ = question_plan.apply_scoring([dict(qs[0])], doc, "post_compile")
    assert planned[0]["status"] == "open"


def test_the_plain_sentence_is_code_written_and_absent_when_nothing_was_left_out():
    assert unsupported_sentence([{"pass": "cleanup"}]) == ""
    assert "this action was left out" in unsupported_sentence([{"pass": "unsupported"}])


def test_a_recording_of_drawing_only_has_nothing_to_build_and_says_why():
    assert _empty_reason([]) == "No workflow actions were recorded."
    assert "drawing area" in (_empty_reason([_TAKE[1], _TAKE[3]]) or "")
    assert _empty_reason(_TAKE) is None
    assert "left out" in describe_event(_TAKE[1])["text"]


def test_the_recap_may_not_say_nothing_was_left_out_or_that_it_does_what_they_did():
    from conxa_compile.llm.compile_recap import DRAWING_LEFT_OUT, _problem

    facts = {"step_count": 3, "left_out": [{"kind": "drawing action", "count": 2, "reason": DRAWING_LEFT_OUT}]}
    assert "nothing was left out" in _problem("I made 3 steps from your recording. Nothing was left out of it at all.", facts)
    assert "what they did" in _problem("I made 3 steps that do the same thing you did, and two drawing actions were left out.", facts)
    assert _problem("I made 3 steps. Two drawing actions were left out because drawing can't be recorded yet.", facts) == ""


def test_a_step_is_never_described_as_the_drawing_that_was_left_out():
    from conxa_compile.compiler.build import _describe_without_left_out_drawing
    from conxa_core.models.skill_spec import SkillStep

    sel = SkillStep(action="click", intent="click_canvas_to_draw_rectangle", semantic_description="Click the canvas to draw the rectangle",
                    identity_bundle={"fingerprint": {"tag": "button", "aria_label": "Selection"}})
    draw_tool = SkillStep(action="click", intent="pick_the_draw_tool", semantic_description="Pick the Draw tool",
                          identity_bundle={"fingerprint": {"tag": "button", "aria_label": "Draw"}})
    _describe_without_left_out_drawing([sel, draw_tool])
    assert "draw" not in f"{sel.semantic_description} {sel.intent}".lower() and "Selection" in sel.semantic_description
    assert draw_tool.semantic_description == "Pick the Draw tool"     # a control really named "Draw" keeps its words


def test_a_form_controls_value_is_never_offered_as_words_on_the_screen():
    from conxa_compile.compiler.state_validation import merge_dom_diff_evidence

    ev = {"state_change": {"dom_diff": {"added": ["input|opacity||100", "div|||Shape saved"]}}}
    merged = merge_dom_diff_evidence(ev, {"new_elements": [], "removed_elements": [], "text_change": []})
    assert merged["text_change"] == ["Shape saved"]


def test_the_video_stretch_of_a_drawing_is_never_pinned_on_the_next_action():
    from conxa_compile.video.merge import merge_timeline

    def timed(seq: int, action: str, label: str, wall: int) -> dict:
        return {"ev": {"action": {"action": action}, "target": {"aria_label": label}, "extras": {"sequence": seq}}, "tab": "t", "wall": wall}

    rows = [{"start_s": 0.0, "end_s": 2.0, "action_kind": "click", "visible_target": "rectangle tool"},
            {"start_s": 2.0, "end_s": 5.0, "action_kind": "click", "visible_target": "the blank canvas area"}]
    splice = [{"kind": "piece", "tab_id": "t", "out_start_ms": 0, "out_end_ms": 5000, "wall_start_ms": 0, "wall_end_ms": 5000}]
    out = merge_timeline([timed(1, "click", "Rectangle", 1000), timed(2, "unsupported_gesture", "", 3500),
                          timed(3, "click", "Selection", 4500)], rows, splice)
    events = {r["event_id"]: r for r in out if r["kind"] == "event"}
    assert set(events) == {1, 3}                          # the drawing is never a row
    assert events[1]["segment"]["visible_target"] == "rectangle tool"
    assert events[3]["segment"] is None                   # the drawing's words are not the Selection click's
