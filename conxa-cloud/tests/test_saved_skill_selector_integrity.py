"""Regression test for BUILD-22: a compiled step's selector must never be silently
overwritten with an unrelated preceding step's input placeholder.

Root cause was two blind-rewrite passes in skill_package_builder_saved_skill.py
(_repair_parameterized_search_result_selectors for execution.json,
_repair_saved_step_click_selectors for recovery.json) that armed on any `type`/`fill`
step whose value was a lone placeholder and unconditionally rewrote the NEXT click's
`text=` selector with it, with no check that the two steps were related. On the
mega-workflow recording this turned a Login button's selector into
`text="{{user_password}}"` — a credential shape baked into a text matcher, and a
selector that could never match at replay. Both passes have been deleted; this test
pins the fix.
"""

from __future__ import annotations

import json

from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill


def test_login_click_selector_is_not_overwritten_by_the_preceding_password_type(tmp_path):
    saved_skill = {
        "meta": {"id": "skill_login", "title": "Login"},
        "inputs": [],
        "skills": [
            {
                "steps": [
                    {
                        "action": {"action": "navigate", "url": "https://the-internet.herokuapp.com/login"},
                        "intent": "navigate_to_page",
                    },
                    {
                        "action": "type",
                        "intent": "enter_password",
                        "target": {"primary_selector": "#password"},
                        "value": "{{user_password}}",
                    },
                    {
                        "action": "click",
                        "intent": "click_login_button",
                        "target": {"primary_selector": "text=Login"},
                    },
                ],
            }
        ],
    }

    _build_workflow_from_saved_skill(
        bundle_root=tmp_path,
        workflow_slug="login_skill",
        saved_skill=saved_skill,
    )

    skill_dir = tmp_path / "skills" / "login_skill"
    execution = json.loads((skill_dir / "execution.json").read_text(encoding="utf-8"))
    click_step = next(step for step in execution if step["type"] == "click")
    assert click_step["selector"] == "text=Login"
    assert "{{" not in click_step["selector"]

    recovery = json.loads((skill_dir / "recovery.json").read_text(encoding="utf-8"))
    click_entry = next(entry for entry in recovery["steps"] if entry["intent"] == "click_login_button")
    assert click_entry["selector_context"]["primary"] == "text=Login"
    assert click_entry["target"]["text"] == "Login"


def _login_events() -> list[dict]:
    """A recording on the-internet's login page: type the password, then click Login (session 9d19890f's shape)."""
    from tests.test_phases import _minimal_click_event

    url = "https://the-internet.herokuapp.com/login"
    pw = _minimal_click_event()
    pw["action"] = {"action": "type", "timestamp": "2026-01-01T00:00:00Z", "value": "SuperSecretPassword!"}
    pw["target"] = {"tag": "input", "id": "password", "classes": [], "inner_text": "", "role": "textbox",
                    "aria_label": None, "name": "password", "type": "password"}
    pw["selectors"] = {"css": "#password", "xpath": "//input[@id='password']", "text_based": "", "aria": ""}
    pw["semantic"] = {"normalized_text": "password", "role": "textbox", "input_type": "password", "intent_hint": ""}
    pw["page"] = {"url": url, "title": "The Internet"}
    pw["extras"] = {"sequence": 1}
    login = _minimal_click_event()
    login["action"]["timestamp"] = "2026-01-01T00:00:02Z"
    login["target"] = {"tag": "button", "id": None, "classes": ["radius"], "inner_text": " Login", "role": "button",
                       "aria_label": None, "name": None, "type": "submit"}
    login["selectors"] = {"css": "button.radius", "xpath": "//button", "text_based": 'text="Login"', "aria": '[role="button"]'}
    login["semantic"] = {"normalized_text": "login", "role": "button", "input_type": None, "intent_hint": ""}
    login["page"] = {"url": url, "title": "The Internet"}
    login["extras"] = {"sequence": 2}
    login["visual"] = {**login["visual"], "full_screenshot": "images/evt_0002_full.jpg"}
    return [pw, login]


def test_a_recorded_password_then_login_compiles_to_a_login_click_that_matches_its_own_identity(tmp_path):
    """The whole current path (pipeline, compiler, packager) on the recording's shape, not a hand-built skill."""
    import shutil
    from contextlib import ExitStack

    from conxa_compile.compiler.build import compile_skill_package
    from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values
    from conxa_compile.pipeline.run import run_pipeline
    from tests.test_phases import _compile_with_vision_mocks

    evs = run_pipeline(_login_events())
    data_dir, *patchers = _compile_with_vision_mocks("sess-build22", evs)
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            pkg = compile_skill_package(evs, skill_id="s", source_session_id="sess-build22", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    doc, _ = reconcile_inputs_with_step_values(pkg.model_dump(mode="json"))
    warnings: list[str] = []
    _build_workflow_from_saved_skill(bundle_root=tmp_path, workflow_slug="w", saved_skill=doc, on_warning=warnings.append)

    skill_dir = tmp_path / "skills" / "w"
    execution = json.loads((skill_dir / "execution.json").read_text(encoding="utf-8"))
    typed = next(s for s in execution if s["type"] == "type")
    assert typed["value"] == "{{user_password}}"
    click = next(s for s in execution if s["type"] == "click")
    assert "{{" not in click["selector"] and "Login" in click["selector"]
    assert click["selector"] in {str(s["selector"]).replace("internal:", "", 1) for s in click["identity_bundle"]["signals"]}
    recovery = json.loads((skill_dir / "recovery.json").read_text(encoding="utf-8"))
    click_entry = recovery["steps"][-1]
    assert "login" in click_entry["intent"] and "password" not in click_entry["intent"]
    assert "{{" not in click_entry["selector_context"]["primary"]
    assert not any("looks for its target by the value" in w for w in warnings)


def _click_skill(primary: str, bundle_selector: str) -> dict:
    return {
        "meta": {"id": "skill_login", "title": "Login"},
        "inputs": [],
        "skills": [{"steps": [
            {"action": "type", "intent": "enter_password", "target": {"primary_selector": "#password"},
             "value": "{{user_password}}"},
            {"action": "click", "intent": "click_login_button", "target": {"primary_selector": primary},
             "identity_bundle": {"signals": [{"engine": "text", "selector": bundle_selector}]}},
        ]}],
    }


def test_a_selector_naming_an_input_its_identity_never_saw_is_a_build_warning(tmp_path):
    warnings: list[str] = []
    _build_workflow_from_saved_skill(
        bundle_root=tmp_path, workflow_slug="w", on_warning=warnings.append,
        saved_skill=_click_skill('text="{{user_password}}"', 'internal:text="Login"'),
    )
    flagged = [w for w in warnings if "looks for its target by the value" in w]
    assert len(flagged) == 1 and flagged[0].startswith("Step 2:") and "user_password" in flagged[0]


def test_a_selector_whose_identity_carries_the_same_input_is_not_flagged(tmp_path):
    """The 1-click click-text rewrite and a Human Edit re-point write the placeholder into the bundle too."""
    warnings: list[str] = []
    _build_workflow_from_saved_skill(
        bundle_root=tmp_path, workflow_slug="w", on_warning=warnings.append,
        saved_skill=_click_skill('text="{{user_password}}"', 'internal:text="{{user_password}}"'),
    )
    assert not any("looks for its target by the value" in w for w in warnings)
