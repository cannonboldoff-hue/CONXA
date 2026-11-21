"""An address check that names the recording's start site is stamped, so a run can follow another start site (BUILD-101 (6))."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_compile.compiler.navigation_effects import generalize_url_pattern  # noqa: E402
from conxa_compile.compiler.start_origin import stamp_start_origin, start_origin_of  # noqa: E402
from conxa_core.models.skill_spec import Assertion, SkillStep, ValidationBlock  # noqa: E402


def _nav(url):
    return SkillStep(action={"action": "navigate"}, url=url)


def _landing(url):
    return SkillStep(action={"action": "click"}, validation=ValidationBlock(
        assertions=[Assertion(type="url_pattern", target=generalize_url_pattern(url), required=False)]))


def test_a_check_on_the_start_site_is_stamped_and_one_on_another_site_is_not():
    steps = [_nav("https://en.wikipedia.org/wiki/Main_Page"), _landing("https://en.wikipedia.org/wiki/Automation"),
             _landing("https://login.example.com/callback")]
    assert stamp_start_origin(steps) == 1
    assert steps[1].validation.assertions[0].start_origin == "https://en.wikipedia.org"
    assert steps[2].validation.assertions[0].start_origin == ""


def test_checks_inside_a_popup_option_are_stamped_too():
    inner = {"action": {"action": "click"}, "validation": {"assertions": [
        {"type": "url_pattern", "target": generalize_url_pattern("https://a.test/done"), "required": False}]}}
    fork = SkillStep(action={"action": "outcome_fork"},
                     outcome_fork={"states": [{"options": [{"steps": [inner]}]}]})
    assert stamp_start_origin([_nav("https://a.test/"), fork]) == 1
    assert inner["validation"]["assertions"][0]["start_origin"] == "https://a.test"


def test_no_navigate_step_means_nothing_is_stamped():
    steps = [_landing("https://a.test/x")]
    assert start_origin_of(steps) == "" and stamp_start_origin(steps) == 0


def test_the_runtime_spells_a_site_the_way_python_does():
    # runtime/test/unit/test_start_origin.js pins the same two strings
    assert re.escape("https://en.wikipedia.org") == r"https://en\.wikipedia\.org"
    assert re.escape("https://my-app.example.com:8443") == r"https://my\-app\.example\.com:8443"
