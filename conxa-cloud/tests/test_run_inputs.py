"""One rule decides which inputs a run needs, and the test card prefills from the recording itself (BUILD-93 (2), BUILD-95 (7))."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_compile.editor import source_event  # noqa: E402
from conxa_compile.editor.workflow_mutations import inputs_a_run_needs  # noqa: E402


def _doc(steps, inputs, session="s1"):
    return {"meta": {"source_session_id": session}, "inputs": inputs, "skills": [{"steps": steps}]}


def test_run_needs_used_but_undeclared_and_not_declared_but_unused():
    doc = _doc(
        [{"action": "type", "value": "{{used}}"}, {"action": "type", "value": "{{surprise}}"}],
        [{"id": "used", "type": "text"}, {"id": "orphan", "type": "text"}],
    )
    assert [r["id"] for r in inputs_a_run_needs(doc)] == ["used", "surprise"]


def test_prefill_comes_from_the_recorded_event_including_a_prompt_answer(monkeypatch):
    events = [
        {"extras": {"sequence": 4}, "action": {"value": "Ada"}},
        {"extras": {"sequence": 9}, "action": {"value": json.dumps({"type": "prompt", "message": "Name?", "value": "Grace"})}},
    ]
    monkeypatch.setattr("conxa_core.storage.session_events.read_session_events", lambda sid: events)
    doc = _doc(
        [{"action": "type", "value": "{{first}}", "input_binding": "first", "source_event": 4},
         {"action": "dialog_accept", "value": "x", "input_binding": "dialog_answer", "source_event": 9}],
        [],
    )
    assert source_event.recorded_input_values(doc) == {"first": "Ada", "dialog_answer": "Grace"}


def test_no_recording_means_no_prefill_and_no_crash(monkeypatch):
    def boom(sid):
        raise OSError("gone")

    monkeypatch.setattr("conxa_core.storage.session_events.read_session_events", boom)
    assert source_event.recorded_input_values(_doc([{"input_binding": "a", "source_event": 1}], [])) == {}


def test_a_date_called_always_this_one_before_the_build_is_not_asked_about_again_as_relative():
    import datetime

    from conxa_compile.conversation.clarify import generate_questions

    step = {"action": {"action": "date_pick"}, "intent": "pick_due", "input_binding": "due_date", "value": "{{due_date}}",
            "handler_hints": {"date_picker": {"kind": "text_field", "display_format": "YYYY-MM-DD", "recorded_value": "2026-10-31"}}}
    doc = {"inputs": [{"id": "due_date", "type": "date", "default": "2026-10-31"}], "skills": [{"steps": [step]}]}
    today = datetime.date(2026, 10, 24)
    look = lambda s: {"extras": {"sequence": 7}}  # noqa: E731
    asked = [q for q in generate_questions(doc, today=today, limit=None, source_lookup=look) if q["kind"] == "relative_date"]
    settled = [q for q in generate_questions(doc, today=today, limit=None, source_lookup=look, typed_fixed_events={7})
               if q["kind"] == "relative_date"]
    assert asked and not settled
