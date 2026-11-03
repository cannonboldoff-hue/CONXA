"""Each compile cleanup pass has its own switch; compile_keep_every_action is only the default for them.

Decision popups (outcome forks) never follow the umbrella: turning "keep every action" on must not
make a recorded click on "Replace" replay as a plain click.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from conxa_core.config import settings

from conxa_compile.compiler import build
from conxa_compile.policy.bundle import get_policy_bundle
from tests.test_redirect_navigation import FORM, ORDER, _click, _nav

PASSES = ["cleanup", "fold_navigations", "collapse_choice", "collapse_dates", "flag_noise", "upstream_dedupe"]


@pytest.mark.parametrize("name", PASSES)
def test_a_pass_follows_the_umbrella_until_it_is_set(name: str) -> None:
    with patch.object(settings, f"compile_{name}", None):
        with patch.object(settings, "compile_keep_every_action", True):
            assert settings.compile_pass(name) is False
        with patch.object(settings, "compile_keep_every_action", False):
            assert settings.compile_pass(name) is True


@pytest.mark.parametrize("name", PASSES)
def test_a_pass_switch_wins_over_the_umbrella(name: str) -> None:
    with patch.object(settings, "compile_keep_every_action", True), patch.object(settings, f"compile_{name}", True):
        assert settings.compile_pass(name) is True
    with patch.object(settings, "compile_keep_every_action", False), patch.object(settings, f"compile_{name}", False):
        assert settings.compile_pass(name) is False


def _prepare(events):
    return build.prepare_compile_events(events, get_policy_bundle().data, [])


def _folding_events():
    return [_click("2026-01-01T00:00:00Z"), _nav("2026-01-01T00:00:30Z", cause="action")]


def test_navigation_fold_can_be_on_while_everything_else_is_kept() -> None:
    with patch.object(settings, "compile_keep_every_action", True), patch.object(settings, "compile_fold_navigations", True):
        assert [e["action"]["action"] for e in _prepare(_folding_events())] == ["click"]


def test_navigation_fold_can_be_off_while_everything_else_is_tidied() -> None:
    with patch.object(settings, "compile_keep_every_action", False), patch.object(settings, "compile_fold_navigations", False):
        assert [e["action"]["action"] for e in _prepare(_folding_events())] == ["click", "manual_navigate"]


def _forks_called(umbrella: bool, forks: bool) -> bool:
    calls: list[int] = []

    def spy(steps, session_id):
        calls.append(1)
        return steps

    from tests.test_second_opinion import _compile_fixture, _minimal_click_event
    import shutil
    from contextlib import ExitStack

    ev = _minimal_click_event()
    ev["extras"] = {"sequence": 1}
    data_dir, patchers = _compile_fixture("sess", [ev])
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            stack.enter_context(patch.object(settings, "compile_keep_every_action", umbrella))
            stack.enter_context(patch.object(settings, "compile_outcome_forks", forks))
            stack.enter_context(patch.object(build, "build_outcome_forks", spy))
            build.compile_skill_package([ev], skill_id="skill_test", source_session_id="sess", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    return bool(calls)


def test_decision_popups_are_still_turned_into_forks_when_every_action_is_kept() -> None:
    assert _forks_called(umbrella=True, forks=True)


def test_outcome_forks_have_their_own_off_switch() -> None:
    assert not _forks_called(umbrella=False, forks=False)


def test_outcome_forks_default_on() -> None:
    assert settings.compile_outcome_forks is True
