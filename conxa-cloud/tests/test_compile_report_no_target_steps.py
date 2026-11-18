"""compiler/build.py::_build_compile_report must not score steps with no element target
(scroll, navigate, frame markers, ...) — they have no selector to grade, so treating a
missing target as selector_confidence=0.0 would spuriously drag min_confidence to 0% and
flag the step as "below threshold" for having no selector it was never supposed to have.
"""

from __future__ import annotations

import pytest

from conxa_compile.compiler.build import _build_compile_report
from conxa_core.models.skill_spec import SkillStep


class _FakeRouter:
    pool = ["fake-provider"]

    def stats(self):
        return {"provider": "fake-provider"}


@pytest.fixture(autouse=True)
def _fake_llm_router(monkeypatch):
    import conxa_core.llm as core_llm

    monkeypatch.setattr(core_llm, "get_router", lambda: _FakeRouter())


def _click_step(confidence: float) -> SkillStep:
    return SkillStep(
        action={"action": "click"},
        intent="click_submit",
        target={"primary_selector": "#submit", "selector_confidence": confidence},
    )


def _scroll_step() -> SkillStep:
    return SkillStep(action={"action": "scroll"}, intent="scroll_viewport")


def test_scroll_step_excluded_from_confidence_and_warnings():
    report = _build_compile_report([_click_step(0.95), _scroll_step()])
    assert report["min_confidence"] == 0.95
    assert report["steps_with_warnings"] == 0
    scroll_report = report["steps"][1]
    assert scroll_report["confidence"] is None
    assert scroll_report["warnings"] == []


def test_low_confidence_click_step_still_flagged():
    report = _build_compile_report([_click_step(0.2), _scroll_step()])
    assert report["min_confidence"] == 0.2
    assert report["steps_with_warnings"] == 1
    assert report["steps"][0]["warnings"][0]["code"] == "low_selector_confidence"
    assert report["steps"][1]["warnings"] == []


# --- failed means "no unique, lasting way to find the element" (BUILD-87, 91(1), 98(1)) -----------

from types import SimpleNamespace  # noqa: E402

from conxa_compile.compiler.selector_score import confidence_from_signal_rows  # noqa: E402


def _step_with_signals(confidence: float, signals: list[dict]) -> SkillStep:
    step = _click_step(confidence)
    step.identity_bundle = SimpleNamespace(
        signals=[SimpleNamespace(**s) for s in signals], fingerprint=None
    )
    return step


def test_lone_unique_readable_id_is_not_a_failure():
    # demoqa's Permanent Address: only `#permanentAddress`, unique, not generated.
    sigs = [{"durability": 0.45, "unique_at_compile": True, "orthogonality_class": "structural"}]
    assert confidence_from_signal_rows(sigs) == 0.45
    report = _build_compile_report([_step_with_signals(0.45, sigs)])
    assert report["status"] == "review_needed"
    assert report["steps_with_warnings"] == 0


def test_step_with_only_a_bare_xpath_is_the_failure():
    sigs = [{"durability": 0.1, "unique_at_compile": True, "orthogonality_class": "structural"}]
    report = _build_compile_report([_step_with_signals(0.1, sigs)])
    assert report["status"] == "failed"
    assert report["steps"][0]["warnings"][0]["code"] == "low_selector_confidence"


def test_one_failed_step_among_fine_ones_fails_the_report_but_many_weak_ones_do_not():
    good = [{"durability": 0.95, "unique_at_compile": True, "orthogonality_class": "semantic-aria"},
            {"durability": 0.99, "unique_at_compile": True, "orthogonality_class": "test-contract"}]
    weak_but_unique = [{"durability": 0.45, "unique_at_compile": True, "orthogonality_class": "structural"}]
    report = _build_compile_report([_step_with_signals(0.99, good)] + [_step_with_signals(0.45, weak_but_unique) for _ in range(58)])
    assert report["status"] == "review_needed"


def test_a_signal_that_matched_many_elements_does_not_lift_confidence():
    sigs = [
        {"durability": 0.95, "unique_at_compile": False, "orthogonality_class": "semantic-aria"},
        {"durability": 0.45, "unique_at_compile": True, "orthogonality_class": "structural"},
    ]
    assert confidence_from_signal_rows(sigs) == 0.45
