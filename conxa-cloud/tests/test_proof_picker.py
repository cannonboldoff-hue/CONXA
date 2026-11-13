"""Proof-of-Done picker (compiler/proof_picker.py): a decision model ranks proof types, code decides what is built."""

import json

import pytest

from conxa_compile.compiler import proof_picker
from conxa_compile.compiler.effects import build_effect
from conxa_core import llm as llm_mod
from conxa_core.config import settings
from conxa_core.models.skill_spec import EFFECT_PROBES, EffectCheck, SkillStep, ValidationBlock, VideoContext


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    yield
    llm_mod.set_router(None)


def _send_step(**kw) -> SkillStep:
    ev = {"action": {"action": "click"}, "target": {"inner_text": "Send invoice"},
          "post_watch": {"progress_seen": True, "waited_ms": 3000,
                         "final_status": [{"kind": "status", "words": ["invoice", "sent"]}]}}
    effect = build_effect(ev, consequence="irreversible")
    return SkillStep(action="click", intent="Send the invoice", validation=ValidationBlock(effect=effect), **kw)


class _Router:
    def __init__(self, probs=None, *, fail=False):
        self.calls: list[dict] = []
        self._probs, self._fail = probs, fail

    def route_decision(self, task, payload, timeout_ms, *, error_detail=None):
        self.calls.append(payload)
        if self._fail:
            raise RuntimeError("offline")
        return {"probabilities": self._probs}


def test_every_probe_has_a_question():
    assert set(proof_picker.PROBE_QUESTIONS) == set(EFFECT_PROBES)


def test_rank_keeps_top_five_at_or_above_the_threshold():
    probs = {p: 0.9 - i * 0.02 for i, p in enumerate(EFFECT_PROBES)}   # 0.90 .. 0.72, all above
    top = proof_picker.rank(probs)
    assert [p for p, _ in top] == list(EFFECT_PROBES)[:5]
    assert proof_picker.rank({"new_text": 0.59, "request_chain": 0.6}) == [("request_chain", 0.6)]
    assert proof_picker.rank({"made_up_probe": 0.99}) == []


def test_the_context_is_value_free():
    step = _send_step(video_context=VideoContext(screen_after='Banner "Invoice 4821 for Acme" sent to bob@acme.com',
                                                 purpose="Bill the customer"), input_binding="customer_email")
    ctx = step_ctx = proof_picker.step_context(step, step.validation.effect)
    for leaked in ("4821", "Acme", "bob@acme.com"):
        assert leaked not in ctx, leaked
    facts = json.loads(step_ctx)
    assert facts["kind"] == "send" and facts["input_names"] == ["customer_email"]


def test_picks_are_recorded_and_the_code_built_checks_are_untouched(tmp_path):
    step = _send_step()
    before = step.validation.effect.model_dump()
    router = _Router({"request_chain": 0.95, "new_text": 0.9, "url_stays": 0.8, "fresh_look": 0.1})
    llm_mod.set_router(router)

    report = proof_picker.run_proof_picker([step], tmp_path)

    assert step.validation.effect.model_dump() == before          # every gate said no: nothing is added or removed
    assert len(router.calls) == 1 and len(router.calls[0]["questions"]) == len(EFFECT_PROBES)
    row = report["steps"][0]
    status = {p["probe"]: p["status"] for p in row["picks"]}
    # url_stays has a gate but this lone step never landed on another address; the others need no new check
    assert status == {"request_chain": "already_built", "new_text": "already_built", "url_stays": "gate_rejected"}
    assert json.loads((tmp_path / proof_picker.SIDECAR).read_text("utf-8"))["steps"][0]["kind"] == "send"


def test_a_gate_adds_a_check_before_the_veto_and_a_rejecting_gate_adds_nothing(monkeypatch):
    step = _send_step()
    built = EffectCheck(rung="completed", witness="renderer", probe={"type": "url_stays"}, plain="the page moved on")
    monkeypatch.setitem(proof_picker.GATES, "url_stays", lambda steps, i: built)
    monkeypatch.setitem(proof_picker.GATES, "later_step", lambda steps, i: None)
    picks = [("url_stays", 0.9), ("later_step", 0.8), ("download_file", 0.7)]
    status = {r["probe"]: r["status"] for r in proof_picker.apply_picks([step], 0, picks)}
    assert status == {"url_stays": "built", "later_step": "gate_rejected", "download_file": "no_gate"}
    checks = step.validation.effect.checks
    assert checks[-1].role == "veto"                                   # the veto stays last, built by code
    assert checks[-2].probe["type"] == "url_stays"
    assert len({c.id for c in checks}) == len(checks)                  # ids stay unique


def test_no_router_support_or_a_failing_call_changes_nothing(tmp_path):
    for router in (object(), _Router(fail=True), _Router(None)):
        step = _send_step()
        before = step.validation.effect.model_dump()
        llm_mod.set_router(router)
        report = proof_picker.run_proof_picker([step, _send_step()], tmp_path)
        assert step.validation.effect.model_dump() == before
        assert report["status"] in {"off", "unavailable"}
    assert not (tmp_path / proof_picker.SIDECAR).exists()


def test_one_failure_stops_the_pass_and_a_second_run_reuses_the_cache(tmp_path):
    failing = _Router(fail=True)
    llm_mod.set_router(failing)
    proof_picker.run_proof_picker([_send_step(), _send_step(), _send_step()], tmp_path)
    assert len(failing.calls) == 1                                     # not one slow retry per step

    good = _Router({"new_text": 0.9})
    llm_mod.set_router(good)
    proof_picker.run_proof_picker([_send_step()], tmp_path)
    proof_picker.run_proof_picker([_send_step()], tmp_path)
    assert len(good.calls) == 1                                        # same step facts: answered from the cache


def test_compile_runs_the_picker_with_the_compiled_steps_and_survives_its_failure():
    """The hook in compile_skill_package: called once with the final steps, and an exception cannot fail a compile."""
    import shutil
    from contextlib import ExitStack
    from unittest.mock import patch

    from conxa_compile.compiler import build
    from tests.test_second_opinion import _compile_fixture, _minimal_click_event

    seen: list[tuple[int, object]] = []

    def run_ok(steps, root):
        seen.append((len(steps), root))
        return {}

    def run_boom(steps, root):
        raise RuntimeError("picker blew up")

    for fake in (run_ok, run_boom):
        ev = _minimal_click_event()
        ev["extras"] = {"sequence": 1}
        data_dir, patchers = _compile_fixture("sess", [ev])
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                stack.enter_context(patch.object(build, "run_proof_picker", fake))
                build.compile_skill_package([ev], skill_id="skill_test", source_session_id="sess", title="t", version=1)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
    assert len(seen) == 1 and seen[0][0] >= 1 and seen[0][1] is not None


def test_real_gates_add_checks_before_the_veto_and_the_effect_stays_valid(tmp_path):
    from conxa_core.models.skill_spec import Effect

    typed = SkillStep(action={"action": "type"}, url="https://app.test/form", input_binding="item_title", value="{{item_title}}")
    save = _send_step(url="https://app.test/form")
    save.validation.effect.kind = "save"
    steps = [typed, save]
    llm_mod.set_router(_Router({"request_chain": 0.95, "row_with_identity": 0.9, "value_equals": 0.8, "later_step": 0.7}))

    report = proof_picker.run_proof_picker(steps, tmp_path)

    status = {p["probe"]: p["status"] for p in report["steps"][0]["picks"]}
    assert status == {"request_chain": "already_built", "row_with_identity": "built", "value_equals": "built",
                      "later_step": "gate_rejected"}
    checks = save.validation.effect.checks
    assert [c.probe["type"] for c in checks[-3:]] == ["row_with_identity", "value_equals", "failure_signal"]
    assert checks[-1].role == "veto" and len({c.id for c in checks}) == len(checks)
    Effect.model_validate(save.validation.effect.model_dump())      # still a valid effect: a pack can ship it
    assert "{{item_title}}" in json.dumps([c.probe for c in checks])
    assert "Quarterly" not in (tmp_path / proof_picker.SIDECAR).read_text("utf-8")
