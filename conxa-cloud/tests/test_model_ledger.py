"""Engine v2 C1: every model guard counts what it drops, in the same shape the understanding check uses.

A guard still decides what it keeps. These tests pin the count (the rule names), never the decision."""

from __future__ import annotations

import json

from conxa_compile.llm import model_ledger as ml


def test_log_count_summary_flat():
    r: dict = {}
    ml.log(r, "reference", "unknown_ref", "url:9")
    ml.log(r, "reference", "unknown_ref", "url:10")
    ml.log(r, "override", "popup_became_fork")
    ml.log(r, "backfill", "must_ask")
    ml.log(None, "shape", "ignored")  # no ledger: a guard can run without one
    assert r["reference"]["unknown_ref"] == {"count": 2, "example": "url:9"}  # the first example is kept
    assert ml.count(r, "reference") == 2 and ml.count(r, "reference", "nope") == 0
    assert ml.summary(r) == {"dropped": 2, "changed": 1, "added": 1}
    assert ml.flat(r) == {"reference.unknown_ref": 2, "override.popup_became_fork": 1, "backfill.must_ask": 1}


def test_example_has_no_email_and_is_short():
    r: dict = {}
    ml.log(r, "shape", "malformed_item", "bob@example.com " + "x" * 200)
    ex = r["shape"]["malformed_item"]["example"]
    assert "@" not in ex and len(ex) <= 60


def test_append_and_last_per_task(tmp_path):
    ml.append(tmp_path, ml.entry("a", {"shape": {"x": {"count": 1, "example": ""}}}))
    ml.append(tmp_path, ml.entry("a", {"shape": {"x": {"count": 3, "example": ""}}}, applied=2))
    ml.append(tmp_path, ml.entry("b", {}))
    rows = ml.last_per_task(tmp_path)
    assert set(rows) == {"a", "b"} and rows["a"]["report"]["shape"]["x"]["count"] == 3 and rows["a"]["applied"] == 2
    assert len((tmp_path / ml.LEDGER_FILE).read_text("utf-8").splitlines()) == 3
    json.loads((tmp_path / ml.LEDGER_FILE).read_text("utf-8").splitlines()[0])


def test_switch_off_writes_nothing(tmp_path, monkeypatch):
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "model_ledger", "off")
    ml.append(tmp_path, ml.entry("a", {}))
    assert not (tmp_path / ml.LEDGER_FILE).exists()
    assert ml.last_per_task(tmp_path) == {}


def test_append_never_raises_and_missing_folder_is_fine(tmp_path):
    ml.append(None, ml.entry("a", {}))
    assert ml.last_per_task(tmp_path / "missing") == {}


# ── the guards that now count ──────────────────────────────────────────────────────────────────────

def test_second_opinion_guard_counts_each_rule_and_keeps_its_decision():
    from conxa_compile.llm.workflow_semantics import _validate_findings

    steps = [{"key": "k1", "input_binding": "email_2", "has_optional_hint": False},
             {"key": "k2", "input_binding": None, "has_optional_hint": True}]
    raw = [
        {"step_key": "k1", "kind": "rename_binding", "current": "email_2", "proposed": "sender_email", "why": "ok"},
        {"step_key": "ghost", "kind": "rename_binding", "proposed": "x"},
        {"step_key": "k1", "kind": "group_steps", "proposed": "x"},
        {"step_key": "k1", "kind": "suggest_optional", "proposed": "x"},   # the recorder never flagged k1
        {"step_key": "k1", "kind": "label_phase", "proposed": "not-a-phase"},
        "not an object",
    ]
    report: dict = {}
    out = _validate_findings(raw, step_keys_in_workflow={"k1", "k2"}, steps_context=steps, report=report)
    assert [f["kind"] for f in out] == ["rename_binding"]
    assert ml.flat(report) == {
        "reference.unknown_step_key": 1, "shape.unknown_kind": 1, "override.unobserved_optional": 1,
        "shape.bad_phase": 1, "shape.malformed_item": 1,
    }


def test_second_opinion_guard_counts_the_cap():
    from conxa_compile.llm.workflow_semantics import _validate_findings

    steps = [{"key": "k1", "input_binding": None, "has_optional_hint": False}]
    raw = [{"step_key": "k1", "kind": "label_phase", "proposed": "act"} for _ in range(5)]
    report: dict = {}
    out = _validate_findings(raw, step_keys_in_workflow={"k1"}, steps_context=steps, report=report)
    assert ml.count(report, "safety", "over_cap") == 5 - len(out) > 0


def test_second_opinion_guard_without_a_report_is_unchanged():
    from conxa_compile.llm.workflow_semantics import _validate_findings

    assert _validate_findings([{"step_key": "ghost", "kind": "label_phase", "proposed": "x"}],
                              step_keys_in_workflow={"k1"}, steps_context=[]) == []


def test_reworded_questions_count_every_field_that_kept_its_template():
    from conxa_compile.conversation import clarify

    qs = [{"id": "q1", "kind": "value", "status": "open", "title": "Is “Q3” fixed?", "body": "Body",
           "choices": [{"id": "fixed", "label": "Fixed", "detail": ""}]}]
    report: dict = {}
    clarify.apply_reworded(qs, {"questions": [{"id": "q1", "title": "no quote here", "body": "Nicer body",
                                               "choices": [{"id": "fixed", "label": "Keep it", "detail": ""}]}]}, report)
    assert qs[0]["title"] == "Is “Q3” fixed?"            # lost the quoted value: template kept, and counted
    assert qs[0]["body"] == "Nicer body" and qs[0]["choices"][0]["label"] == "Keep it"
    assert ml.flat(report) == {"override.reword_rejected": 1}
    bad: dict = {}
    clarify.apply_reworded(qs, "nonsense", bad)
    assert ml.flat(bad) == {"shape.malformed_reply": 1}


def test_intent_router_counts_what_it_refuses_but_not_a_plain_chat_answer():
    from conxa_compile.conversation.intent_router import route_intent

    def ask(answer):
        report: dict = {}
        got = route_intent("could you please run it now", ["test", "edit"], call=lambda *_a: answer, report=report)
        return got, ml.flat(report)

    assert ask({"action": "chat"}) == (None, {})
    assert ask({"action": "publish_to_prod"}) == (None, {"reference.action_not_allowed": 1})
    assert ask("garbage") == (None, {"shape.malformed_reply": 1})
    got, flat = ask({"action": "test"})
    assert got["action"] == "test" and flat == {}


def test_gate_all_counts_per_kind_not_per_gate():
    from conxa_compile.editor import copilot_proposals as cp

    doc = {"skills": [{"steps": [{"key": "s1", "action": {"action": "click"}}]}]}
    raw = [{"step_key": "ghost", "field": "intent", "patch": "x"},                 # patch kind, unresolvable
           {"op": "insert_step", "evidence_refs": []},                              # structural kind, no evidence
           "junk"]
    report: dict = {}
    assert cp.gate_all(doc, raw, None, report) == []
    assert ml.flat(report) == {"safety.gate_refused": 2, "shape.malformed_item": 1}
    assert cp.gate_all(doc, raw) == []                                              # no ledger: same answer
