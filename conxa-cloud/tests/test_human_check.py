"""A robot check is never a step (WF-8 8.13, 2026-11-18).

One click on reCAPTCHA's "I'm not a robot" compiled into a step; Studio said the workflow "gets past the robot check",
the test passed and Approve was offered. Now the click is left out, a question the person must answer offers a
hand-over step (inserted by code) or leaving it out, and the summary may not claim it gets past the check."""

from __future__ import annotations

from conxa_compile.compiler.build import prepare_compile_events
from conxa_compile.compiler.human_check import in_human_check
from conxa_compile.conversation import question_plan
from conxa_compile.conversation.clarify import generate_questions, human_check_sentence, plan_answer
from conxa_compile.llm.compile_recap import HUMAN_CHECK_LEFT_OUT, _problem
from conxa_compile.policy.bundle import get_policy_bundle
from conxa_compile.recorder.plain import describe_event

_DEMO = {"url": "https://www.google.com/recaptcha/api2/demo"}
_ANCHOR = "https://www.google.com/recaptcha/api2/anchor?ar=1&k=6Le-wvkS&size=normal"


def _ev(seq: int, label: str, frame_url: str | None = None) -> dict:
    return {"action": {"action": "click", "timestamp": "2026-11-18T11:00:00Z"}, "target": {"tag": "span", "aria_label": label},
            "selectors": {"css": f"#e{seq}"}, "page": {"url": frame_url or _DEMO["url"]}, "tab": _DEMO,
            "frame": {"chain": [{"url": frame_url}]} if frame_url else {}, "extras": {"sequence": seq, "element_ref": f"e{seq}"}}


def test_frames_only_the_demo_page_itself_is_an_ordinary_page():
    assert in_human_check(_ev(1, "I'm not a robot", _ANCHOR))
    assert in_human_check(_ev(1, "x", "https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html#frame=checkbox"))
    assert in_human_check(_ev(1, "x", "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile"))
    assert not in_human_check(_ev(1, "Submit"))                       # google.com/recaptcha/api2/demo, top page
    assert "robot check" in describe_event(_ev(1, "I'm not a robot", _ANCHOR))["text"]


def test_the_click_is_left_out_and_a_hand_over_is_offered_and_inserted_by_code():
    ledger: list[dict] = []
    kept = prepare_compile_events([_ev(1, "Name"), _ev(2, "I'm not a robot", _ANCHOR), _ev(3, "Submit")],
                                  get_policy_bundle().data, ledger)
    assert [e["extras"]["sequence"] for e in kept if e["action"]["action"] == "click"] == [1, 3]
    assert [(d["sequence"], d["pass"], d.get("after_sequence")) for d in ledger] == [(2, "human_check", 1)]

    doc = {"skills": [{"steps": [{"action": "navigate", "url": _DEMO["url"]},
                                 {"action": "click", "source_event": 1, "intent": "type the name"},
                                 {"action": "click", "source_event": 3, "intent": "submit"}]}]}
    q = generate_questions(doc, dropped=ledger, limit=None)[0]
    assert q["kind"] == "human_check" and [c["id"] for c in q["choices"]] == ["handover", "leave"]
    ops, receipt = plan_answer(q, {"choice": "handover"}, doc)
    assert ops[0]["op"] == "insert_handover" and ops[0]["after_step_key"] == q["step_keys"][0]
    assert plan_answer(q, {"choice": "leave"}, doc)[0] == []
    planned, _ = question_plan.apply_scoring([dict(q)], doc, "post_compile")
    assert planned[0]["status"] == "open"                              # never assumed: test and approve wait for it


def test_nothing_may_say_it_gets_past_the_robot_check():
    assert "robot check" in human_check_sentence([{"pass": "human_check"}]) and human_check_sentence([]) == ""
    facts = {"step_count": 2, "left_out": [{"kind": "robot check", "count": 1, "reason": HUMAN_CHECK_LEFT_OUT}]}
    assert _problem("I made 2 steps that get past the robot check on a web form for you.", facts)
    assert _problem("I made 2 steps. The robot check was left out because only a person may do it.", facts) == ""


def test_the_hand_over_goes_right_after_the_typing_before_the_check_and_is_offered_only_when_it_can_be_placed():
    dropped = [{"sequence": 3, "pass": "human_check", "action": "click", "after_sequence": 2}]
    doc = {"skills": [{"steps": [{"action": "navigate", "url": _DEMO["url"]},
                                 {"action": "focus", "source_event": 2}, {"action": "type", "source_event": 2, "value": "Ada"},
                                 {"action": "click", "source_event": 4, "intent": "submit"}]}]}
    q = next(q for q in generate_questions(doc, dropped=dropped, limit=None) if q["kind"] == "human_check")
    from conxa_compile.compiler.step_key import step_keys

    assert q["step_keys"] == [step_keys(doc["skills"][0]["steps"])[2]]   # after the typing, not between focus and typing
    alone = next(q for q in generate_questions({"skills": [{"steps": []}]}, dropped=dropped, limit=None) if q["kind"] == "human_check")
    assert [c["id"] for c in alone["choices"]] == ["leave"] and "hand-over" not in alone["body"]
