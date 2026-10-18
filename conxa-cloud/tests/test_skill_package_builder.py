"""Tests for conxa_compile/skill_package_builder.py — Phase 1 (Foundation)."""

from __future__ import annotations

import json
import re

import pytest
from PIL import Image

from conxa_compile.skill_package_builder import build_skill_package
from conxa_compile.skill_package_builder_output import (
    _clean_stale_artifacts,
    _copy_skill_package_templates,
    _render_license,
    _render_readme,
)
from conxa_compile.editor.action_registry import is_supported_action
from conxa_compile.skill_package_builder_saved_skill import (
    _build_workflow_from_saved_skill,
    _is_login_step,
    _merge_saved_inputs_with_execution_placeholders,
    _normalize_saved_skill_inputs,
    _saved_step_to_execution_step,
    _upload_input_descriptions,
    strip_login_steps,
)




# ─────────────────────────────────────────────────
# _render_readme
# ─────────────────────────────────────────────────

class TestRenderReadme:
    def test_contains_company_name(self):
        md = _render_readme("Render.com", "render_abc", "https://render.com", ["deploy"])
        assert "Render.com" in md

    def test_contains_target_url(self):
        md = _render_readme("Test", "test_abc", "https://app.test.com", [])
        assert "https://app.test.com" in md

    def test_lists_skills(self):
        md = _render_readme("Test", "test_abc", "https://test.com", ["create-service", "deploy"])
        assert "create-service" in md
        assert "deploy" in md

    def test_points_to_installer_flow(self):
        md = _render_readme("Test", "test_slug", "https://test.com", [])
        assert "Build Installer" in md
        assert "npx -y conxa install" not in md

    def test_package_id_uses_package_id_when_given(self):
        md = _render_readme("Test", "test_slug", "https://test.com", [], package_id="acme/x")
        assert "Package ID: `acme/x`" in md

    def test_contains_auth_reference(self):
        md = _render_readme("Test", "test_slug", "https://test.com", [])
        assert "auth" in md.lower()


class TestSkillPackageTemplateCopy:
    def test_does_not_write_credentials_example(self, tmp_path):
        _copy_skill_package_templates(
            tmp_path,
            company_name="Test",
            bundle_slug="test",
            target_url="https://example.com",
            version="0.1.0",
            skill_slugs=[],
        )

        assert (tmp_path / "Claude.md").is_file()
        assert not (tmp_path / "auth" / "credentials.example.json").exists()

    def test_clean_stale_artifacts_removes_auth_credentials(self, tmp_path):
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        (auth_dir / "credentials.example.json").write_text("{}", encoding="utf-8")
        (auth_dir / "credentials.json").write_text("{}", encoding="utf-8")

        _clean_stale_artifacts(tmp_path)

        assert not (auth_dir / "credentials.example.json").exists()
        assert not (auth_dir / "credentials.json").exists()


# ─────────────────────────────────────────────────
# _render_license
# ─────────────────────────────────────────────────

class TestRenderLicense:
    def test_contains_mit(self):
        assert "MIT License" in _render_license()

    def test_contains_year(self):
        import datetime
        year = str(datetime.datetime.now().year)
        assert year in _render_license()


# ─────────────────────────────────────────────────
# login step detection
# ─────────────────────────────────────────────────

class TestLoginStepDetection:
    def _make_step(self, url="", title="", inner_text="", semantic="", aria="") -> dict:
        return {
            "page": {"url": url, "title": title},
            "target": {"inner_text": inner_text, "aria_label": aria},
            "semantic": {"normalized_text": semantic},
        }

    def test_detects_password_field_step(self):
        step = self._make_step(inner_text="password")
        assert _is_login_step(step) is True

    def test_detects_sign_in_url(self):
        step = self._make_step(url="https://app.example.com/sign-in")
        assert _is_login_step(step) is True

    def test_detects_login_title(self):
        step = self._make_step(title="Log in to Render")
        assert _is_login_step(step) is True

    def test_neutral_step_not_detected(self):
        step = self._make_step(url="https://dashboard.render.com/services", title="Services")
        assert _is_login_step(step) is False

    def test_strip_login_steps_removes_login_events(self):
        events = [
            self._make_step(url="https://app.example.com/login", inner_text="email"),
            self._make_step(url="https://app.example.com/login", inner_text="password"),
            self._make_step(url="https://app.example.com/dashboard", title="Dashboard"),
            self._make_step(url="https://app.example.com/dashboard", inner_text="Create service"),
        ]
        clean = strip_login_steps(events)
        assert len(clean) == 2
        for e in clean:
            assert "dashboard" in e["page"]["url"]

    def test_strip_login_steps_no_login_returns_original(self):
        events = [
            self._make_step(url="https://dashboard.render.com/services", title="Services"),
            self._make_step(url="https://dashboard.render.com/services", inner_text="New service"),
        ]
        assert strip_login_steps(events) == events

    def test_strip_login_steps_empty_returns_empty(self):
        assert strip_login_steps([]) == []

    def test_strip_all_results_in_original_returned(self):
        events = [
            self._make_step(url="https://app.example.com/login", inner_text="password"),
        ]
        result = strip_login_steps(events)
        assert result == events


# ─────────────────────────────────────────────────
# EXEC-1: conditional/branch step serializer passthrough
# ─────────────────────────────────────────────────

class TestBranchStepSerialization:
    """A saved skill's if_present/try_dismiss/wait_for_one_of steps — including their nested
    step bodies — must survive _saved_step_to_execution_step into the flat runtime step shape
    execution.json carries, or the branch silently vanishes at build time."""

    def test_action_kinds_are_supported(self):
        for kind in ("if_present", "try_dismiss", "wait_for_one_of"):
            assert is_supported_action(kind) is True

    def test_if_present_serializes_with_nested_body(self):
        step = {
            "action": "if_present",
            "target": {"primary_selector": ".cookie-banner"},
            "branch": {
                "timeout_ms": 1500,
                "steps": [
                    {"action": "click", "target": {"primary_selector": "#accept-cookies"}},
                ],
            },
        }
        out = _saved_step_to_execution_step(step)
        assert out == {
            "type": "if_present",
            "selector": ".cookie-banner",
            "steps": [{"type": "click", "selector": "#accept-cookies"}],
            "timeout_ms": 1500,
        }

    def test_if_present_dropped_when_probe_selector_missing(self):
        step = {"action": "if_present", "branch": {"steps": [{"action": "click", "target": {"primary_selector": "#x"}}]}}
        assert _saved_step_to_execution_step(step) is None

    def test_if_present_dropped_when_body_empty(self):
        step = {"action": "if_present", "target": {"primary_selector": ".cookie-banner"}, "branch": {}}
        assert _saved_step_to_execution_step(step) is None

    def test_try_dismiss_dedupes_own_selector_into_candidates(self):
        step = {
            "action": "try_dismiss",
            "target": {"primary_selector": "#accept-cookies"},
            "branch": {"timeout_ms": 800, "candidates": ["#accept-cookies", ".cookie .accept"]},
        }
        out = _saved_step_to_execution_step(step)
        assert out["type"] == "try_dismiss"
        assert out["candidates"] == ["#accept-cookies", ".cookie .accept"]
        assert out["timeout_ms"] == 800

    def test_try_dismiss_carries_fallback_escape_false(self):
        step = {"action": "try_dismiss", "branch": {"candidates": ["#x"], "fallback_escape": False}}
        out = _saved_step_to_execution_step(step)
        assert out["fallback_escape"] is False

    def test_try_dismiss_dropped_when_no_candidates(self):
        assert _saved_step_to_execution_step({"action": "try_dismiss", "branch": {}}) is None

    def test_wait_for_one_of_serializes_options_with_nested_steps(self):
        step = {
            "action": "wait_for_one_of",
            "branch": {
                "timeout_ms": 8000,
                "required": True,
                "options": [
                    {
                        "selector": "#mfa-code",
                        "steps": [
                            {"action": "fill", "target": {"primary_selector": "#mfa-code"}, "value": "{{otp}}"},
                        ],
                    },
                    {"selector": "#dashboard"},
                ],
            },
        }
        out = _saved_step_to_execution_step(step)
        assert out == {
            "type": "wait_for_one_of",
            "options": [
                {"selector": "#mfa-code", "steps": [{"type": "fill", "selector": "#mfa-code", "value": "{{otp}}"}]},
                {"selector": "#dashboard"},
            ],
            "timeout_ms": 8000,
            "required": True,
        }

    def test_wait_for_one_of_drops_options_without_a_selector(self):
        step = {"action": "wait_for_one_of", "branch": {"options": [{"steps": []}, {"selector": "#dashboard"}]}}
        out = _saved_step_to_execution_step(step)
        assert out["options"] == [{"selector": "#dashboard"}]

    def test_wait_for_one_of_dropped_when_no_options(self):
        assert _saved_step_to_execution_step({"action": "wait_for_one_of", "branch": {}}) is None

    def test_unsupported_action_still_drops_to_none(self):
        assert _saved_step_to_execution_step({"action": "not_a_real_action"}) is None

    # ── EXEC-13: ai_review ──────────────────────────────────────────────────
    # Config lives in top-level ai_review_* fields (the check_kind/check_pattern shape), not
    # nested under `action` — `action` here is the plain-string saved-skill shape, matching
    # every other _saved_step_to_execution_step branch in this file.

    def test_ai_review_step_serializes_prompt_and_defaults_on_failure_to_abort(self):
        step = {"action": "ai_review", "ai_review_prompt": "Is the banner visible?"}
        out = _saved_step_to_execution_step(step)
        assert out == {"type": "ai_review", "prompt": "Is the banner visible?", "on_failure": "abort"}

    def test_ai_review_step_dropped_when_prompt_is_blank(self):
        assert _saved_step_to_execution_step({"action": "ai_review", "ai_review_prompt": "   "}) is None
        assert _saved_step_to_execution_step({"action": "ai_review"}) is None

    def test_ai_review_step_serializes_full_config(self):
        step = {
            "action": "ai_review",
            "ai_review_prompt": "Is the banner visible?",
            "ai_review_output_schema": {"type": "object", "required": ["visible"]},
            "ai_review_reference_screenshot_ref": "visuals/Image_3.jpg",
            "ai_review_on_failure": "use_default",
            "ai_review_default_value": {"visible": False},
            "value": "banner_visible",
        }
        out = _saved_step_to_execution_step(step)
        assert out == {
            "type": "ai_review",
            "prompt": "Is the banner visible?",
            "on_failure": "use_default",
            "output_schema": {"type": "object", "required": ["visible"]},
            "reference_screenshot_ref": "visuals/Image_3.jpg",
            "default_value": {"visible": False},
            "output_name": "banner_visible",
        }

    def test_ai_review_step_omits_default_value_unless_on_failure_is_use_default(self):
        step = {
            "action": "ai_review",
            "ai_review_prompt": "Is the banner visible?",
            "ai_review_default_value": {"visible": False},
        }
        out = _saved_step_to_execution_step(step)
        assert "default_value" not in out
        assert out["on_failure"] == "abort"

    def test_ai_review_step_omits_empty_output_schema_and_reference_ref(self):
        step = {
            "action": "ai_review",
            "ai_review_prompt": "Is the banner visible?",
            "ai_review_output_schema": {},
            "ai_review_reference_screenshot_ref": "   ",
        }
        out = _saved_step_to_execution_step(step)
        assert "output_schema" not in out
        assert "reference_screenshot_ref" not in out

    # ── EXEC-21 (hand-over shape): handover ──────────────────────────────────
    # The human sibling of ai_review above — same shape, but the message shown to the person
    # rides the generic `value` field rather than a dedicated `handover_message` field.

    def test_handover_step_serializes_message_and_defaults_on_failure_to_abort(self):
        step = {"action": "handover", "value": "Please complete the 2FA challenge, then click Done."}
        out = _saved_step_to_execution_step(step)
        assert out == {
            "type": "handover",
            "message": "Please complete the 2FA challenge, then click Done.",
            "on_failure": "abort",
        }

    def test_handover_step_dropped_when_message_is_blank(self):
        assert _saved_step_to_execution_step({"action": "handover", "value": "   "}) is None
        assert _saved_step_to_execution_step({"action": "handover"}) is None

    def test_handover_step_serializes_full_config(self):
        step = {
            "action": "handover",
            "value": "Please sign the document, then click Done.",
            "handover_on_failure": "continue",
            "handover_resume_when": {"selector": "#signed-confirmation"},
            "handover_resume_when_timeout_ms": 15000,
        }
        out = _saved_step_to_execution_step(step)
        assert out == {
            "type": "handover",
            "message": "Please sign the document, then click Done.",
            "on_failure": "continue",
            "resume_when": {"selector": "#signed-confirmation"},
            "resume_when_timeout_ms": 15000,
        }

    def test_handover_step_omits_resume_when_timeout_unless_resume_when_is_set(self):
        step = {
            "action": "handover",
            "value": "Please log in.",
            "handover_resume_when_timeout_ms": 15000,
        }
        out = _saved_step_to_execution_step(step)
        assert "resume_when" not in out
        assert "resume_when_timeout_ms" not in out

    def test_handover_step_omits_empty_resume_when(self):
        step = {"action": "handover", "value": "Please log in.", "handover_resume_when": {}}
        out = _saved_step_to_execution_step(step)
        assert "resume_when" not in out

    def test_editor_authored_if_present_step_serializes_correctly(self):
        """An if_present step scaffolded via the Human Edit editor (workflow_mutations.py::
        _new_manual_step + insert_branch_step — the 2026-07-10 branch-authoring work closing
        EXEC-1's remaining editor gap) must survive the same saved-skill -> execution-step path
        as a hand-built fixture. Exercises the real scaffold + insert functions, not a fixture
        dict, so a shape mismatch between authoring and serialization would be caught here."""
        import sys

        sys.path.insert(0, "../conxa-builder/python")
        from conxa_compile.editor.workflow_mutations import insert_branch_step, insert_step_after

        doc = {"meta": {"version": 1}, "inputs": [], "skills": [{"name": "default", "steps": []}]}
        doc = insert_step_after(doc, "if_present", None)
        doc = insert_branch_step(doc, 0, "click", None)
        scaffolded_step = doc["skills"][0]["steps"][0]

        # The scaffold starts with empty selectors (nothing is a real target until a human picks
        # one) — set them the way the editor's re-target flow would before this step is usable,
        # so serialization has something real to carry.
        scaffolded_step["target"]["primary_selector"] = ".cookie-banner"
        scaffolded_step["branch"]["steps"][0]["target"]["primary_selector"] = "#accept-cookies"

        # _saved_step_to_execution_step expects action as a plain string (the saved-skill shape
        # plugin_builder_saved_skill.py normalizes to) rather than the editor's {"action": ...}
        # dict — normalize the same way the real save path does before asserting.
        saved_step = dict(scaffolded_step)
        saved_step["action"] = scaffolded_step["action"]["action"]
        nested = saved_step["branch"]["steps"]
        saved_step["branch"] = dict(saved_step["branch"])
        saved_step["branch"]["steps"] = [
            {**n, "action": n["action"]["action"]} for n in nested
        ]

        out = _saved_step_to_execution_step(saved_step)
        assert out["type"] == "if_present"
        assert out["selector"] == ".cookie-banner"
        assert len(out["steps"]) == 1
        assert out["steps"][0] == {"type": "click", "selector": "#accept-cookies"}

    def test_confirmed_optional_interstitial_serializes_to_try_dismiss(self):
        """recording-next-steps.md Priority 2: a recorder-flagged optional_hint, once a human
        confirms it via workflow_mutations.confirm_optional_interstitial, must produce a real
        try_dismiss branch that survives into the runtime step shape — the whole point of the
        human-gated conversion (CLAUDE.md Key Invariants: branch steps compile only from
        observed states + human confirmation)."""
        import sys

        sys.path.insert(0, "../conxa-builder/python")
        from conxa_compile.editor.workflow_mutations import confirm_optional_interstitial

        doc = {
            "meta": {"version": 1},
            "inputs": [],
            "skills": [
                {
                    "name": "default",
                    "steps": [
                        {
                            "action": {"action": "click"},
                            "intent": "close_dialog",
                            "target": {"primary_selector": ".gdpr-consent button", "fallback_selectors": []},
                            "signals": {},
                            "validation": {"wait_for": {"type": "none"}, "success_conditions": {}},
                            "optional_hint": {"kind": "try_dismiss", "container_signal": '[role="dialog"]'},
                        },
                    ],
                }
            ],
        }
        doc = confirm_optional_interstitial(doc, 0)
        step = doc["skills"][0]["steps"][0]
        assert step["action"]["action"] == "try_dismiss"
        assert step["branch"]["candidates"] == [".gdpr-consent button", '[role="dialog"]']
        assert step["optional_hint"] is None  # consumed by confirmation

        saved_step = dict(step)
        saved_step["action"] = step["action"]["action"]
        out = _saved_step_to_execution_step(saved_step)
        assert out["type"] == "try_dismiss"
        assert out["candidates"] == [".gdpr-consent button", '[role="dialog"]']

    def test_confirm_optional_interstitial_rejects_step_without_hint(self):
        import sys

        sys.path.insert(0, "../conxa-builder/python")
        from conxa_compile.editor.workflow_mutations import confirm_optional_interstitial

        doc = {
            "meta": {"version": 1},
            "inputs": [],
            "skills": [{"name": "default", "steps": [{"action": {"action": "click"}, "target": {}}]}],
        }
        try:
            confirm_optional_interstitial(doc, 0)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert str(exc) == "step_has_no_optional_hint"

    def test_confirm_optional_interstitial_keep_as_step_clears_hint_only(self):
        import sys

        sys.path.insert(0, "../conxa-builder/python")
        from conxa_compile.editor.workflow_mutations import confirm_optional_interstitial

        original_action = {"action": "click"}
        doc = {
            "meta": {"version": 1},
            "inputs": [],
            "skills": [
                {
                    "name": "default",
                    "steps": [
                        {
                            "action": dict(original_action),
                            "target": {"primary_selector": "#accept"},
                            "optional_hint": {"kind": "try_dismiss", "container_signal": ".cookie-banner"},
                        }
                    ],
                }
            ],
        }
        result = confirm_optional_interstitial(doc, 0, keep_as_step=True)
        step = result["skills"][0]["steps"][0]
        assert step["optional_hint"] is None
        assert step["action"] == original_action
        assert "branch" not in step


# ─────────────────────────────────────────────────
# skill_package.json structure (integration-level)
# ─────────────────────────────────────────────────

class TestSkillPackageConfigStructure:
    """Validates the structure that build_skill_package would write to skill_package.json."""

    def _make_config(
        self,
        name="Test Plugin",
        version="0.2.0",
        skills=None,
        protected_url="https://app.example.com/dashboard",
    ) -> dict:
        skills = skills or [{"slug": "do-thing", "path": "skills/do-thing"}]
        return {
            "slug": "test_plugin",
            "name": name,
            "version": version,
            "target_url": "https://app.example.com",
            "protected_url": protected_url,
            "skills": skills,
            "compatibility": {"conxa_runtime": ">=1.0.0"},
        }

    def test_required_fields_present(self):
        cfg = self._make_config()
        for field in ("slug", "name", "version", "target_url", "protected_url", "skills"):
            assert field in cfg, f"Missing field: {field}"

    def test_skills_are_objects_not_strings(self):
        cfg = self._make_config()
        for skill in cfg["skills"]:
            assert isinstance(skill, dict)
            assert "slug" in skill
            assert "path" in skill

    def test_version_semver_format(self):
        cfg = self._make_config(version="1.2.3")
        assert re.match(r"^\d+\.\d+\.\d+$", cfg["version"])


# ─────────────────────────────────────────────────
# saved Human Edit skill → plugin files
# ─────────────────────────────────────────────────

class TestSavedSkillJsonBuild:
    def test_saved_skill_export_keeps_the_compiler_synthesized_start_navigation(self, tmp_path):
        """The current compiler's own synthesized leading navigate step (intent
        navigate_to_page, see build.py::_insert_start_navigate_step) is exported like any
        other step — no special-casing strips it. (An older compiler's synthetic first step,
        tagged navigate_to_start_url, no longer gets any special treatment either: recompiling
        that old data was dropped in favor of a clear re-record message.)"""
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Delete Database"},
            "inputs": [{"id": "service_name", "label": "Service Name", "type": "text"}],
            "skills": [
                {
                    "name": "recorded",
                    "steps": [
                        {
                            "action": {"action": "navigate", "url": "https://dashboard.render.com/"},
                            "intent": "navigate_to_page",
                            "target": {},
                            "signals": {
                                "semantic": {
                                    "final_intent": "navigate_to_page",
                                    "llm_intent": "navigate_to_page",
                                },
                                "selectors": {},
                                "anchors": [],
                                "visual": {},
                            },
                        },
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[placeholder="Search"]'},
                            "value": "{{service_name}}",
                        },
                        {
                            "action": "click",
                            "target": {"primary_selector": "text={{service_name}}"},
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        skill_dir = tmp_path / "skills" / "delete_database"
        execution = json.loads((skill_dir / "execution.json").read_text(encoding="utf-8"))
        assert [step["type"] for step in execution] == ["navigate", "type", "click"]

        # navigate has no recovery block (Key Invariant: navigation markers are never
        # retried) — only the type/click steps (2, 3) appear in recovery.json.
        recovery = json.loads((skill_dir / "recovery.json").read_text(encoding="utf-8"))
        assert [step["step_id"] for step in recovery["steps"]] == [2, 3]
        assert recovery["steps"][0]["selector_context"]["primary"] == 'input[placeholder="Search"]'
        assert recovery["steps"][1]["selector_context"]["primary"] == "text={{service_name}}"

    def test_saved_skill_export_preserves_real_first_navigation(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Open Settings"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": {"action": "navigate", "url": "https://dashboard.render.com/settings"},
                            "intent": "navigate_to_account_settings",
                        },
                        {
                            "action": "click",
                            "target": {"primary_selector": "text=Members"},
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="open_settings",
            saved_skill=saved_skill,
        )

        execution = json.loads((tmp_path / "skills" / "open_settings" / "execution.json").read_text(encoding="utf-8"))
        assert [step["type"] for step in execution] == ["navigate", "click"]
        assert execution[0]["url"] == "https://dashboard.render.com/settings"

    def test_preserves_human_edit_placeholders_and_removes_recorded_literal(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Delete Database"},
            "inputs": [{"id": "service_name", "label": "Service Name", "type": "text"}],
            "skills": [
                {
                    "name": "recorded",
                    "steps": [
                        {
                            "action": {"action": "navigate", "url": "https://dashboard.render.com/"},
                            "url_state": {
                                "before": {"url_pattern": "^https://dashboard\\.render\\.com/$"},
                                "after": {"url_pattern": "^https://dashboard\\.render\\.com/$"},
                            },
                        },
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[placeholder="Search"]'},
                            "value": "{{service_name}}",
                        },
                        {
                            "action": "click",
                            "target": {"primary_selector": "text={{service_name}}"},
                        },
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[name="sudoCommand"]'},
                            "value": "sudo delete database {{service_name}}",
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        skill_dir = tmp_path / "skills" / "delete_database"
        execution_raw = (skill_dir / "execution.json").read_text(encoding="utf-8")
        assert "{{service_name}}" in execution_raw
        assert "conxa-db" not in execution_raw
        assert "url_state" not in execution_raw

        execution = json.loads(execution_raw)
        assert execution[1]["value"] == "{{service_name}}"
        assert execution[2]["selector"] == "text={{service_name}}"
        assert execution[3]["value"] == "sudo delete database {{service_name}}"

        input_json = json.loads((skill_dir / "input.json").read_text(encoding="utf-8"))
        assert input_json["inputs"][0]["name"] == "service_name"

        recovery = json.loads((skill_dir / "recovery.json").read_text(encoding="utf-8"))
        assert [step["step_id"] for step in recovery["steps"]] == [2, 3, 4]
        assert recovery["steps"][1]["selector_context"]["primary"] == "text={{service_name}}"
        assert recovery["steps"][2]["selector_context"]["primary"] == 'input[name="sudoCommand"]'

    def test_saved_skill_export_infers_missing_inputs_from_execution_placeholders(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Create Service"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "type",
                            "target": {"primary_selector": 'label:has-text("Search repositories") + input'},
                            "value": "{{search_repositories}}",
                        },
                        {
                            "action": "click",
                            "target": {"primary_selector": 'label:has-text("{{repository_name}}") + button'},
                        },
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[name="name"]'},
                            "value": "{{blueprint_name}}",
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="create_service",
            saved_skill=saved_skill,
        )

        input_json = json.loads((tmp_path / "skills" / "create_service" / "input.json").read_text(encoding="utf-8"))
        assert [item["name"] for item in input_json["inputs"]] == [
            "search_repositories",
            "repository_name",
            "blueprint_name",
        ]

    def test_saved_skill_does_not_rewrite_a_hardcoded_search_result_click_with_the_prior_input(self, tmp_path):
        # This used to be "repaired" into `text="{{database_name}}"` by a blind rewrite that
        # armed on ANY preceding type step's placeholder, with no check the click actually
        # related to it (BUILD-22: the same mechanism turned a Login button's selector into
        # `text="{{user_password}}"` on an unrelated recording). The rewrite has been removed —
        # a step's selector must always match its own recorded identity. The tradeoff: a
        # search->click-the-result skill replayed with a different search term now keeps this
        # recorded literal selector and falls through to the recovery cascade instead of
        # following the input automatically. See TODO.md for a follow-up that reintroduces this
        # binding safely at compile time, gated on the click text actually matching the typed
        # literal.
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Delete Database"},
            "inputs": [{"id": "database_name", "label": "Database Name", "type": "text"}],
            "skills": [
                {
                    "steps": [
                        {
                            "action": {"action": "navigate", "url": "https://dashboard.render.com/"},
                        },
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[type="text"]'},
                            "value": "{{database_name}}",
                        },
                        {
                            "action": "click",
                            "intent": "click_conxa_db",
                            "target": {"primary_selector": 'text="conxa-db"'},
                            "recovery": {
                                "anchors": [
                                    {"element": "conxa-db", "relation": "target"},
                                ]
                            },
                        },
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[name="sudoCommand"]'},
                            "value": "sudo delete database {{database_name}}",
                        },
                        {
                            "action": "click",
                            "target": {"primary_selector": 'text="Delete Database"'},
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        skill_dir = tmp_path / "skills" / "delete_database"
        execution_raw = (skill_dir / "execution.json").read_text(encoding="utf-8")
        recovery_raw = (skill_dir / "recovery.json").read_text(encoding="utf-8")

        execution = json.loads(execution_raw)
        click_steps = [step for step in execution if step["type"] == "click"]
        assert click_steps[0]["selector"] == 'text="conxa-db"'
        assert click_steps[1]["selector"] == 'text="Delete Database"'
        assert "recovery_metadata" not in recovery_raw
        assert "generated_by" not in recovery_raw
        assert '"mode"' not in recovery_raw
        assert "visual_metadata" not in recovery_raw

        recovery = json.loads(recovery_raw)
        click_entry = next(entry for entry in recovery["steps"] if entry["intent"] == "click_conxa_db")
        assert click_entry["selector_context"]["primary"] == 'text="conxa-db"'
        assert click_entry["target"]["text"] == "conxa-db"

    def test_saved_skill_recovery_is_built_from_saved_human_edit_fields(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Delete Database"},
            "inputs": [{"id": "database_name", "label": "Database Name", "type": "text"}],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "intent": "open_database_from_saved_json",
                            "target": {
                                "primary_selector": 'text="{{database_name}}"',
                                "fallback_selectors": ['[role="link"][name="{{database_name}}"]'],
                                "role": "link",
                            },
                            "recovery": {
                                "anchors": [
                                    {"element": "{{database_name}}", "relation": "target"},
                                    {"element": "Databases", "relation": "near"},
                                ],
                                "strategies": ["semantic match", "visual match"],
                            },
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        recovery = json.loads((tmp_path / "skills" / "delete_database" / "recovery.json").read_text(encoding="utf-8"))
        assert recovery["steps"] == [
            {
                "step_id": 1,
                "intent": "open_database_from_saved_json",
                "target": {"text": "{{database_name}}", "role": "link"},
                "anchors": [
                    {"text": "{{database_name}}", "priority": 2},
                ],
                # `text_variants` and `selector_context.alternatives` are deliberately absent:
                # they fed the fallback-selector walk and fuzzy text match deleted from the
                # runtime cascade in 2026-09, and nothing reads them now.
                "fallback": {"role": "link"},
                "selector_context": {"primary": 'text="{{database_name}}"'},
            }
        ]

    def test_saved_skill_recovery_carries_the_labeled_phase(self, tmp_path):
        """BUILD-25 stage e: the compiler's second-opinion pass's label_phase
        finding, read by runtime/app/failure_response.js's Tier B recovery
        prompt. Absent on any step the pass never labeled."""
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Delete Database"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "intent": "sign_in",
                            "phase": "login",
                            "target": {"primary_selector": "#login-submit", "role": "button"},
                        },
                        {
                            "action": "click",
                            "intent": "unlabeled_step",
                            "target": {"primary_selector": "#other", "role": "button"},
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        recovery = json.loads((tmp_path / "skills" / "delete_database" / "recovery.json").read_text(encoding="utf-8"))
        assert recovery["steps"][0]["phase"] == "login"
        assert "phase" not in recovery["steps"][1]

    def test_saved_skill_recovery_writes_visual_refs_from_saved_step_screenshots(self, tmp_path, monkeypatch):
        import conxa_compile.skill_package_builder_saved_skill as skill_package_builder_saved_skill

        data_dir = tmp_path / "data"
        image_dir = data_dir / "sessions" / "sess_visual" / "images"
        image_dir.mkdir(parents=True)
        source_image = image_dir / "click.jpg"
        Image.new("RGB", (120, 80), "white").save(source_image)
        monkeypatch.setattr(skill_package_builder_saved_skill, "resolve_skill_asset", lambda rel: data_dir / rel)

        saved_skill = {
            "meta": {
                "id": "skill_123",
                "title": "Delete Database",
                "source_session_id": "sess_visual",
            },
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "target": {"primary_selector": 'text="Delete Database"'},
                            "signals": {
                                "visual": {
                                    "full_screenshot": "images/click.jpg",
                                    "bbox": {"x": 10, "y": 12, "w": 40, "h": 20},
                                    "viewport": "120x80",
                                }
                            },
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        skill_dir = tmp_path / "skills" / "delete_database"
        visual_path = skill_dir / "visuals" / "Image_1.jpg"
        assert visual_path.is_file()
        assert visual_path.read_bytes() != source_image.read_bytes()

        recovery = json.loads((skill_dir / "recovery.json").read_text(encoding="utf-8"))
        assert recovery["steps"][0]["visual_ref"] == "visuals/Image_1.jpg"

    def test_saved_skill_recovery_visual_ref_uses_chosen_frame_and_carries_anchor_sentence(self, tmp_path, monkeypatch):
        """visual_ref now resolves through signals.visual.default_frame_label (the
        vision LLM's chosen_frame) against signals.visual.frames, instead of always
        the deterministic before_near/full_screenshot frame — and the full prose
        anchor sentence rides alongside the short anchors as its own field."""
        import conxa_compile.skill_package_builder_saved_skill as skill_package_builder_saved_skill

        data_dir = tmp_path / "data"
        image_dir = data_dir / "sessions" / "sess_visual" / "images"
        image_dir.mkdir(parents=True)
        Image.new("RGB", (120, 80), "white").save(image_dir / "before_near.jpg")
        chosen_image = image_dir / "at.jpg"
        Image.new("RGB", (120, 80), (10, 20, 30)).save(chosen_image)
        monkeypatch.setattr(skill_package_builder_saved_skill, "resolve_skill_asset", lambda rel: data_dir / rel)

        saved_skill = {
            "meta": {
                "id": "skill_456",
                "title": "Submit Form",
                "source_session_id": "sess_visual",
            },
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "target": {"primary_selector": 'text="Submit"'},
                            "signals": {
                                "visual": {
                                    "full_screenshot": "images/before_near.jpg",
                                    "default_frame_label": "at",
                                    "frames": {
                                        "before_near": "images/before_near.jpg",
                                        "at": "images/at.jpg",
                                    },
                                    "bbox": {"x": 10, "y": 12, "w": 40, "h": 20},
                                    "viewport": "120x80",
                                },
                                "anchors": [
                                    {"element": "submit", "relation": "near"},
                                    {"element": "The blue Submit button", "relation": "target_sentence"},
                                ],
                            },
                            "recovery": {
                                "anchors": [
                                    {"element": "submit", "relation": "near"},
                                    {"element": "The blue Submit button", "relation": "target_sentence"},
                                ]
                            },
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="submit_form",
            saved_skill=saved_skill,
        )

        skill_dir = tmp_path / "skills" / "submit_form"
        visual_path = skill_dir / "visuals" / "Image_1.jpg"
        assert visual_path.is_file()

        recovery = json.loads((skill_dir / "recovery.json").read_text(encoding="utf-8"))
        entry = recovery["steps"][0]
        assert entry["anchor_sentence"] == "The blue Submit button"
        # The sentence must not also show up as one of the short anchor phrases.
        assert all(a["text"] != "The blue Submit button" for a in entry["anchors"])

    def test_saved_skill_recovery_carries_recorded_page_structure(self, tmp_path):
        """The agent recovery tier is handed a ranked list of what is on the page NOW. That
        describes the present but not the change, and drift IS a change — so the recorded
        neighbourhood ships alongside it as the structural before-picture."""
        saved_skill = {
            "meta": {"id": "skill_ctx", "title": "Export Report"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "target": {"primary_selector": 'text="Export"'},
                            "signals": {
                                "context": {
                                    "parent": 'div#toolbar[role=toolbar]',
                                    "siblings": [f"button#b{i}:Btn {i}" for i in range(12)],
                                    "index_in_parent": 3,
                                    "form_context": "form#report",
                                    # Merged in by the compiler, but they describe the visit,
                                    # not where the element sat — must not be carried over.
                                    "page_url": "https://app.example/reports",
                                    "page_title": "Reports",
                                    "timing": {"dwell_ms": 900},
                                },
                            },
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="export_report",
            saved_skill=saved_skill,
        )

        recovery = json.loads(
            (tmp_path / "skills" / "export_report" / "recovery.json").read_text(encoding="utf-8")
        )
        ctx = recovery["steps"][0]["recorded_context"]
        assert ctx["parent"] == "div#toolbar[role=toolbar]"
        assert ctx["index_in_parent"] == 3
        assert ctx["form_context"] == "form#report"
        # Capped so the block stays a few hundred bytes and cannot crowd out the live ranked
        # digest it sits beside (candidate_digest.js enforces a 40k-char budget).
        assert ctx["siblings"] == [f"button#b{i}:Btn {i}" for i in range(8)]
        assert not {"page_url", "page_title", "timing"} & set(ctx)

    def test_saved_skill_recovery_omits_recorded_context_when_unrecorded(self, tmp_path):
        """Packs recorded before this shipped simply have no context signals — the key is
        omitted rather than emitted empty, so the payload never carries a meaningless block."""
        saved_skill = {
            "meta": {"id": "skill_noctx", "title": "Export Report"},
            "inputs": [],
            "skills": [
                {"steps": [{"action": "click", "target": {"primary_selector": 'text="Export"'}}]}
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="export_report",
            saved_skill=saved_skill,
        )

        recovery = json.loads(
            (tmp_path / "skills" / "export_report" / "recovery.json").read_text(encoding="utf-8")
        )
        assert "recorded_context" not in recovery["steps"][0]

    def test_saved_skill_export_drops_url_state_and_preserves_frame(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Delete Database"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "target": {"primary_selector": 'text="Delete Database"'},
                            "frame": {
                                "chain": [
                                    {
                                        "selector": 'iframe[id="object-builder-ui"]',
                                        "fallback_selectors": ['iframe[data-test-id="object-builder-ui-iframe"]'],
                                        "url": "https://app-na2.hubspot.com/object-builder/246242636/0-1/embed?",
                                        "url_pattern": "^https://app\\-na2\\.hubspot\\.com/object\\-builder/[^/]+/0\\-1/embed$",
                                    }
                                ]
                            },
                            "url_state": {
                                "before": {
                                    "url": "https://dashboard.render.com/d/dpg-123",
                                    "url_pattern": "^https://dashboard\\.render\\.com/d/[^/]+$",
                                    "title_includes": "conxa-db ・ Database ・ Render Dashboard",
                                },
                                "after": {
                                    "url": "https://dashboard.render.com/",
                                    "url_pattern": "^https://dashboard\\.render\\.com/$",
                                    "title_includes": "conxa-db ・ Database ・ Render Dashboard",
                                },
                                "edited_by_user": True,
                            },
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="delete_database",
            saved_skill=saved_skill,
        )

        execution_raw = (tmp_path / "skills" / "delete_database" / "execution.json").read_text(encoding="utf-8")
        assert "url_state" not in execution_raw
        assert "title_includes" not in execution_raw
        assert "edited_by_user" not in execution_raw

        execution = json.loads(execution_raw)
        assert execution[0]["frame"]["chain"][0]["selector"] == 'iframe[id="object-builder-ui"]'

    def test_saved_skill_export_drops_placeholder_url_state(self, tmp_path):
        saved_skill = {
            "meta": {
                "id": "skill_session",
                "title": "Create Lead",
                "source_session_id": "session_123",
            },
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {
                            "action": "click",
                            "target": {"primary_selector": '[aria-label="Contacts"]'},
                            "url_state": {
                                "before": {
                                    "url_pattern": "^https://{{Organisation_Name}}\\.pipedrive\\.com/setup\\-guide$"
                                },
                                "after": {
                                    "url_pattern": "^https://{{Organisation_Name}}\\.pipedrive\\.com/setup\\-guide$"
                                },
                            },
                        },
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="create_a_lead",
            saved_skill=saved_skill,
        )

        execution_raw = (tmp_path / "skills" / "create_a_lead" / "execution.json").read_text(encoding="utf-8")
        assert "url_state" not in execution_raw
        assert "{{Organisation_Name}}" not in execution_raw

    def test_saved_skill_export_preserves_extended_actions_and_markers(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Action Parity"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {"action": {"action": "dblclick"}, "target": {"primary_selector": 'text="Open"'}},
                        {
                            "action": {"action": "set_checkbox", "value": "false"},
                            "target": {"primary_selector": 'input[name="enabled"]'},
                            "value": "true",
                        },
                        {
                            "action": {"action": "keyboard_shortcut", "value": "Control+K"},
                            "value": "Control+K",
                        },
                        {
                            "action": {
                                "action": "drag_drop",
                                "value": '{"src_selector":"#source","dst_selector":"#target"}',
                            },
                        },
                        {"action": {"action": "wait", "ms": 750}},
                        {"action": {"action": "download_observed", "value": '{"suggested_filename":"report.csv"}'}},
                    ],
                }
            ],
        }

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="action_parity",
            saved_skill=saved_skill,
        )

        execution = json.loads((tmp_path / "skills" / "action_parity" / "execution.json").read_text(encoding="utf-8"))
        assert [step["type"] for step in execution] == [
            "dblclick",
            "set_checkbox",
            "keyboard_shortcut",
            "drag_drop",
            "wait",
            "download_observed",
        ]
        assert execution[1]["value"] == "false"
        assert execution[3]["src_selector"] == "#source"
        assert execution[3]["dst_selector"] == "#target"
        assert execution[4]["ms"] == 750
        assert execution[5]["recording_marker"] is True

    def test_saved_skill_export_rejects_malformed_supported_action(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Bad Drag"},
            "inputs": [],
            "skills": [{"steps": [{"action": {"action": "drag_drop", "value": "{}"}}]}],
        }

        with pytest.raises(ValueError, match="no executable steps"):
            _build_workflow_from_saved_skill(
                bundle_root=tmp_path,
                workflow_slug="bad_drag",
                saved_skill=saved_skill,
            )

    def test_saved_skill_export_drops_unexportable_drag_drop_with_warning(self, tmp_path):
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Bad Drag Among Others"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {"action": {"action": "drag_drop", "value": "{}"}},
                        {"action": {"action": "wait", "ms": 750}},
                    ]
                }
            ],
        }
        warnings = []

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="bad_drag_among_others",
            saved_skill=saved_skill,
            on_warning=warnings.append,
        )

        execution = json.loads(
            (tmp_path / "skills" / "bad_drag_among_others" / "execution.json").read_text(encoding="utf-8")
        )
        assert [step["type"] for step in execution] == ["wait"]
        assert len(warnings) == 1
        assert "drag" in warnings[0].lower()

    def test_saved_skill_export_drops_unconfigured_ai_review_with_warning(self, tmp_path):
        """EXEC-13: an inserted-but-never-configured AI Review step (blank prompt, the
        _new_manual_step scaffold) is dropped with an actionable warning — same shape as
        drag_drop above — instead of failing the whole build."""
        saved_skill = {
            "meta": {"id": "skill_123", "title": "Unconfigured Review Among Others"},
            "inputs": [],
            "skills": [
                {
                    "steps": [
                        {"action": {"action": "ai_review"}, "ai_review_prompt": ""},
                        {"action": {"action": "wait", "ms": 750}},
                    ]
                }
            ],
        }
        warnings = []

        _build_workflow_from_saved_skill(
            bundle_root=tmp_path,
            workflow_slug="unconfigured_review_among_others",
            saved_skill=saved_skill,
            on_warning=warnings.append,
        )

        execution = json.loads(
            (tmp_path / "skills" / "unconfigured_review_among_others" / "execution.json").read_text(encoding="utf-8")
        )
        assert [step["type"] for step in execution] == ["wait"]
        assert len(warnings) == 1
        assert "ai review" in warnings[0].lower()
        assert "prompt" in warnings[0].lower()

    def test_normalizes_human_edit_input_id_to_runtime_name(self):
        inputs = _normalize_saved_skill_inputs(
            [{"id": "service_name", "label": "Service Name", "type": "text"}]
        )

        assert inputs == [
            {
                "name": "service_name",
                "type": "string",
                "description": "Service Name",
            }
        ]

    def test_build_skill_package_prefers_saved_skill_over_original_recording(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        import conxa_compile.skill_package_builder as skill_package_builder

        workspace_id = "wrk_test"
        pack = SimpleNamespace(display_name="Render")
        workflow = SimpleNamespace(
            id="wf1",
            workspace_id=workspace_id,
            group_id="grp_any",
            slug="delete_database",
            name="Delete Database",
            session_id="workflow-session",
            skill_id="skill_saved",
            edited_at=1, signed_off=True,
            target_url="https://dashboard.render.com",
            protected_url="https://dashboard.render.com/",
        )
        saved_skill = {
            "meta": {
                "id": "skill_saved",
                "title": "Delete Database",
                "source_session_id": "workflow-session",
            },
            "inputs": [{"id": "service_name", "label": "Service Name", "type": "text"}],
            "skills": [
                {
                    "steps": [
                        {"action": {"action": "navigate", "url": "https://dashboard.render.com/"}},
                        {
                            "action": "type",
                            "target": {"primary_selector": 'input[placeholder="Search"]'},
                            "value": "{{service_name}}",
                        },
                    ]
                }
            ],
        }
        monkeypatch.setattr(skill_package_builder, "get_or_create_skill_pack", lambda _workspace_id, display_name=None: pack)
        monkeypatch.setattr(skill_package_builder, "list_workflows", lambda _workspace_id: [workflow])
        monkeypatch.setattr(skill_package_builder, "read_skill", lambda skill_id: saved_skill if skill_id == "skill_saved" else None)
        monkeypatch.setattr(skill_package_builder, "_bundle_root", lambda _bundle_slug: tmp_path)
        monkeypatch.setattr(skill_package_builder, "set_build", lambda *args, **kwargs: None)
        # Every built workflow must belong to an existing group (see build_skill_package).
        monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _gid: SimpleNamespace(id="grp_any", name="Any", apps=[]))

        build_skill_package(workspace_id, company_name="Render")

        execution_raw = (tmp_path / "skills" / "delete_database" / "execution.json").read_text(encoding="utf-8")
        assert "{{service_name}}" in execution_raw
        assert "conxa-db" not in execution_raw

        # Data-only artifact: marketplace shim and runtime/ never ship.
        assert not (tmp_path / ".claude-plugin").exists()
        assert not (tmp_path / "runtime").exists()
        assert not (tmp_path / "sessions").exists()
        assert not any(path.name == "events.jsonl" for path in tmp_path.rglob("*"))

        # v2 manifest fields written by build_skill_package
        manifest = json.loads((tmp_path / "skill_package.json").read_text(encoding="utf-8"))
        assert manifest["package_format"] == 2
        assert manifest["id"]  # falls back to bundle slug when package_id unset
        assert manifest["auth_requirements"] == {"kind": "cookie", "manual_login": True}
        assert manifest["runtime_min_version"] == "1.0.0"

        # Claude.md points at the conxa MCP runtime flow, not the deleted npm CLI.
        claude_md = (tmp_path / "Claude.md").read_text(encoding="utf-8")
        assert "conxa" in claude_md
        assert "npx -y conxa install" not in claude_md

    def test_manifests_carry_no_per_skill_required_apps(self, tmp_path, monkeypatch):
        """A group is the unit of sign-in: the runtime gates every workflow on EVERY app in
        its group (pack.json's `groups` block), so no manifest may carry a per-skill subset —
        not for a workflow that targets an app, and not for one that touches none."""
        from types import SimpleNamespace
        import conxa_compile.skill_package_builder as skill_package_builder
        from conxa_core.config import settings

        monkeypatch.setattr(settings, "data_dir", tmp_path)

        workspace_id = "wrk_test"
        pack = SimpleNamespace(display_name="Acme")

        render_app = SimpleNamespace(
            id="app_render", name="Render",
            login_url="https://dashboard.render.com/login",
            success_url="https://dashboard.render.com",
        )
        sales_group = SimpleNamespace(id="grp_sales", name="Sales", apps=[render_app])

        unrelated_wf = SimpleNamespace(
            id="wf_unrelated", workspace_id=workspace_id, group_id="grp_sales",
            slug="upload_8_files", name="Upload 8 files", session_id="s1",
            skill_id="skill_unrelated", edited_at=1, signed_off=True,
            target_url="https://filebin.net/wn8n9o7mlzkvpvcl", protected_url="",
        )
        render_wf = SimpleNamespace(
            id="wf_render", workspace_id=workspace_id, group_id="grp_sales",
            slug="sync_contact_to_crm", name="Sync contact to CRM", session_id="s2",
            skill_id="skill_render", edited_at=1, signed_off=True,
            target_url="https://dashboard.render.com", protected_url="https://dashboard.render.com/",
        )

        def _saved_skill_for(slug):
            return {
                "meta": {"id": f"skill_{slug}", "title": slug},
                "inputs": [],
                "skills": [{"steps": [{"action": {"action": "navigate", "url": "https://example.com"}}]}],
            }

        saved_skills = {"skill_unrelated": _saved_skill_for("upload_8_files"), "skill_render": _saved_skill_for("sync_contact_to_crm")}

        monkeypatch.setattr(skill_package_builder, "get_or_create_skill_pack", lambda _workspace_id, display_name=None: pack)
        monkeypatch.setattr(skill_package_builder, "list_workflows", lambda _workspace_id: [unrelated_wf, render_wf])
        monkeypatch.setattr(skill_package_builder, "read_skill", lambda skill_id: saved_skills.get(skill_id))
        monkeypatch.setattr(skill_package_builder, "_bundle_root", lambda _bundle_slug: tmp_path)
        monkeypatch.setattr(skill_package_builder, "set_build", lambda *args, **kwargs: None)
        monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _group_id: sales_group)

        build_skill_package(workspace_id, company_name="Acme")

        pack_dir = tmp_path / "skill-packs" / "wrk_test"

        for slug in ("upload_8_files", "sync_contact_to_crm"):
            manifest = json.loads((pack_dir / "grp_sales" / slug / "manifest.json").read_text())
            assert "required_apps" not in manifest, slug
            assert manifest["group_id"] == "grp_sales"

        # The one thing the runtime gates on is the group's full app list.
        pack_json = json.loads((pack_dir / "pack.json").read_text())
        (grp,) = [g for g in pack_json["groups"] if g["id"] == "grp_sales"]
        assert [a["id"] for a in grp["apps"]] == ["app_render"]

    def test_pack_json_carries_the_learned_auth_definition_and_no_secrets(self, tmp_path, monkeypatch):
        """P0: a learned auth_definition ships with its app in pack.json's groups block, and
        never carries a cookie value, a response body, or a query string/fragment in any of its
        stored urls — the runtime is the only place a definition should ever be evaluated
        against a live page, never distributed with anything sensitive baked in."""
        from types import SimpleNamespace
        import conxa_compile.skill_package_builder as skill_package_builder
        from conxa_core.config import settings

        monkeypatch.setattr(settings, "data_dir", tmp_path)

        workspace_id = "wrk_test"
        pack = SimpleNamespace(display_name="Acme")

        learned = {
            "version": 1,
            "probe_url": "https://dashboard.render.com/login",
            "signed_out": {"final_path": "/login", "markers": [{"role": "button", "name": "Sign in"}], "password_box": True},
            "signed_in": {"final_path": "/home", "endpoints": [{"method": "GET", "path": "/api/me", "ok": "2xx", "denied": "401"}]},
            "session_keys": ["_render_session"],
            "journey_hosts": ["dashboard.render.com"],
        }
        render_app = SimpleNamespace(
            id="app_render", name="Render",
            login_url="https://dashboard.render.com/login",
            success_url="https://dashboard.render.com",
            auth_definition=learned,
        )
        no_def_app = SimpleNamespace(
            id="app_legacy", name="Legacy", login_url="https://legacy.test/login", success_url="",
        )  # no auth_definition attribute at all — an app connected before this field existed
        sales_group = SimpleNamespace(id="grp_sales", name="Sales", apps=[render_app, no_def_app])

        render_wf = SimpleNamespace(
            id="wf_render", workspace_id=workspace_id, group_id="grp_sales",
            slug="sync_contact_to_crm", name="Sync contact to CRM", session_id="s2",
            skill_id="skill_render", edited_at=1, signed_off=True,
            target_url="https://dashboard.render.com", protected_url="https://dashboard.render.com/",
        )

        saved_skill = {
            "meta": {"id": "skill_render", "title": "sync_contact_to_crm"},
            "inputs": [],
            "skills": [{"steps": [{"action": {"action": "navigate", "url": "https://example.com"}}]}],
        }

        monkeypatch.setattr(skill_package_builder, "get_or_create_skill_pack", lambda _workspace_id, display_name=None: pack)
        monkeypatch.setattr(skill_package_builder, "list_workflows", lambda _workspace_id: [render_wf])
        monkeypatch.setattr(skill_package_builder, "read_skill", lambda skill_id: saved_skill if skill_id == "skill_render" else None)
        monkeypatch.setattr(skill_package_builder, "_bundle_root", lambda _bundle_slug: tmp_path)
        monkeypatch.setattr(skill_package_builder, "set_build", lambda *args, **kwargs: None)
        monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _group_id: sales_group)

        build_skill_package(workspace_id, company_name="Acme")

        pack_json = json.loads((tmp_path / "skill-packs" / "wrk_test" / "pack.json").read_text(encoding="utf-8"))
        apps_by_id = {a["id"]: a for a in pack_json["groups"][0]["apps"]}

        assert apps_by_id["app_render"]["auth_definition"] == learned
        assert apps_by_id["app_legacy"]["auth_definition"] is None

        # No secrets, ever, in whatever ships: no cookie values (only names, under session_keys),
        # no response bodies, no query string or fragment on any stored url.
        blob = json.dumps(apps_by_id["app_render"]["auth_definition"])
        assert "?" not in blob and "#" not in blob
        assert all(isinstance(k, str) for k in apps_by_id["app_render"]["auth_definition"]["session_keys"])

    def _single_workflow_build(self, tmp_path, monkeypatch, *, visited_hosts, group):
        from types import SimpleNamespace
        import conxa_compile.skill_package_builder as skill_package_builder
        from conxa_core.config import settings

        monkeypatch.setattr(settings, "data_dir", tmp_path)
        wf = SimpleNamespace(
            id="wf_render", workspace_id="wrk_test", group_id="grp_sales",
            slug="sync_contact_to_crm", name="Sync contact to CRM", session_id="s2",
            skill_id="skill_render", edited_at=1, signed_off=True,
            target_url="https://dashboard.render.com", protected_url="https://dashboard.render.com/",
        )
        skill = {
            "meta": {"id": "skill_render", "title": "sync_contact_to_crm", "visited_hosts": visited_hosts},
            "inputs": [],
            "skills": [{"steps": [{"action": {"action": "navigate", "url": "https://dashboard.render.com"}}]}],
        }
        monkeypatch.setattr(skill_package_builder, "get_or_create_skill_pack", lambda _w, display_name=None: SimpleNamespace(display_name="Acme"))
        monkeypatch.setattr(skill_package_builder, "list_workflows", lambda _w: [wf])
        monkeypatch.setattr(skill_package_builder, "read_skill", lambda _s: skill)
        monkeypatch.setattr(skill_package_builder, "_bundle_root", lambda _b: tmp_path)
        monkeypatch.setattr(skill_package_builder, "set_build", lambda *a, **k: None)
        monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _g: group)
        build_skill_package("wrk_test", company_name="Acme")
        return json.loads((tmp_path / "skill-packs" / "wrk_test" / "grp_sales" / "sync_contact_to_crm" / "manifest.json").read_text())

    def test_unclaimed_visited_hosts_land_in_the_manifest_and_are_omitted_when_none(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        render_app = SimpleNamespace(
            id="app_render", name="Render",
            login_url="https://dashboard.render.com/login", success_url="https://dashboard.render.com",
        )
        group = SimpleNamespace(id="grp_sales", name="Sales", apps=[render_app])

        manifest = self._single_workflow_build(
            tmp_path, monkeypatch, visited_hosts=["dashboard.render.com", "drive.google.com"], group=group,
        )
        assert "required_apps" not in manifest
        assert manifest["unclaimed_hosts"] == ["drive.google.com"]

        manifest = self._single_workflow_build(tmp_path, monkeypatch, visited_hosts=["dashboard.render.com"], group=group)
        assert "unclaimed_hosts" not in manifest  # omitted, not written empty

    def test_a_workflow_whose_group_no_longer_exists_fails_the_build(self, tmp_path, monkeypatch):
        """The runtime has no single-session fallback (AUTH-2): a pack whose group is missing would
        fail only on the customer's machine, so the build refuses it where the vendor can fix it."""
        with pytest.raises(RuntimeError, match="not in an existing group.*Sync contact to CRM"):
            self._single_workflow_build(tmp_path, monkeypatch, visited_hosts=[], group=None)


# ─────────────────────────────────────────────────
# Upload steps
# ─────────────────────────────────────────────────

# bridge.js records a file input's change event as JSON.stringify(files) — file *metadata*,
# never a path, because browsers only ever expose File.name. That string is truthy, so the old
# `_action_value_text(step) or "{{file_path}}"` fallback never fired and the metadata blob was
# emitted as the upload path. setInputFiles would then fail on a nonsense filename and, since
# `upload` is in RECOVERY_ACTION_TYPES, burn Tier 1-4 recovery healing a selector that was fine.
RECORDED_FILE_METADATA = json.dumps(
    [{"name": "kyc_document.pdf", "size": 8421, "type": "application/pdf"}]
)


def _upload_step(value, action="upload_intent"):
    return {
        "action": {"action": action, "value": value},
        "target": {"primary_selector": "#file-upload"},
    }


class TestUploadStepSerialization:
    """Uploads are always parameterised: the compiled step must carry {{file_path}} so the real
    path arrives as a runtime input, and so the required-input gate in server.js can enforce it."""

    def test_upload_is_a_supported_action(self):
        assert is_supported_action("upload") is True

    def test_recorded_file_metadata_becomes_the_file_path_placeholder(self):
        out = _saved_step_to_execution_step(_upload_step(RECORDED_FILE_METADATA))
        assert out["type"] == "upload"
        assert out["value"] == "{{file_path}}"
        assert "kyc_document.pdf" not in out["value"]

    def test_recorded_metadata_from_an_upload_action_is_also_parameterised(self):
        out = _saved_step_to_execution_step(_upload_step(RECORDED_FILE_METADATA, action="upload"))
        assert out["value"] == "{{file_path}}"

    def test_empty_value_becomes_the_file_path_placeholder(self):
        assert _saved_step_to_execution_step(_upload_step(""))["value"] == "{{file_path}}"

    def test_hand_authored_literal_path_is_preserved(self):
        # A path typed into Human Edit is a deliberate choice — don't overwrite it.
        out = _saved_step_to_execution_step(_upload_step("C:/fixed/form.pdf"))
        assert out["value"] == "C:/fixed/form.pdf"

    def test_hand_authored_custom_placeholder_is_preserved(self):
        out = _saved_step_to_execution_step(_upload_step("{{invoice_pdf}}"))
        assert out["value"] == "{{invoice_pdf}}"

    def test_selector_is_carried_through(self):
        out = _saved_step_to_execution_step(_upload_step(RECORDED_FILE_METADATA))
        assert out["selector"] == "#file-upload"


class TestUploadInputDeclaration:
    """{{file_path}} must auto-declare a required input, described well enough that an agent
    calling get_skill_inputs knows a real file on disk is expected."""

    def test_file_path_is_auto_declared_from_the_placeholder(self):
        steps = [{"type": "upload", "value": "{{file_path}}", "selector": "#file-upload"}]
        inputs = _merge_saved_inputs_with_execution_placeholders([], steps)
        assert [i["name"] for i in inputs] == ["file_path"]

    def test_description_names_the_recorded_example_file(self):
        descriptions = _upload_input_descriptions([_upload_step(RECORDED_FILE_METADATA)])
        assert descriptions["file_path"].startswith(
            "Path to the file to upload (e.g. kyc_document.pdf)."
        )

    def test_description_omits_the_example_when_none_was_recorded(self):
        descriptions = _upload_input_descriptions([_upload_step("")])
        assert descriptions["file_path"].startswith("Path to the file to upload.")

    def test_description_offers_a_folder_for_batch_uploads(self):
        """EXEC-15: a batch upload is driven by one folder path, not N file paths -- an agent
        moving 20 or 200 documents must learn that from get_skill_inputs alone."""
        description = _upload_input_descriptions([_upload_step(RECORDED_FILE_METADATA)])["file_path"]
        assert "folder path" in description
        assert "name order" in description

    def test_description_never_claims_how_many_files_the_control_accepts(self):
        """Whether the control takes one file or many is a property of the live page, not of
        how many files happened to be picked while recording -- a one-file recording against a
        multi-select control is normal. The runtime probes the element instead; the description
        must not assert a count derived from the recording."""
        two_files = json.dumps([{"name": "a.pdf"}, {"name": "b.pdf"}])
        description = _upload_input_descriptions([_upload_step(two_files)])["file_path"]
        assert "2 files" not in description
        assert "were selected" not in description

    def test_no_description_without_an_upload_step(self):
        click = {"action": {"action": "click"}, "target": {"primary_selector": "#go"}}
        assert _upload_input_descriptions([click]) == {}

    def test_upload_description_reaches_the_declared_input(self):
        steps = [{"type": "upload", "value": "{{file_path}}", "selector": "#file-upload"}]
        inputs = _merge_saved_inputs_with_execution_placeholders(
            [], steps, _upload_input_descriptions([_upload_step(RECORDED_FILE_METADATA)])
        )
        assert inputs[0]["description"].startswith(
            "Path to the file to upload (e.g. kyc_document.pdf)."
        )

    def test_other_placeholders_keep_the_generic_description(self):
        steps = [
            {"type": "upload", "value": "{{file_path}}"},
            {"type": "fill", "value": "{{borrower_name}}"},
        ]
        inputs = _merge_saved_inputs_with_execution_placeholders(
            [], steps, _upload_input_descriptions([_upload_step(RECORDED_FILE_METADATA)])
        )
        by_name = {i["name"]: i["description"] for i in inputs}
        assert by_name["borrower_name"] == "Enter borrower name"
        assert by_name["file_path"].startswith("Path to the file to upload")

    def test_override_replaces_an_already_declared_generic_description(self):
        # The primary record->compile path (compiler/build.py) declares file_path itself, with a
        # generic humanized label ("File Path"), before this function ever runs -- so the
        # "not yet seen" branch never fires for it. The override must still win.
        declared = [{"id": "file_path", "label": "File Path"}]
        steps = [{"type": "upload", "value": "{{file_path}}", "selector": "#file-upload"}]
        inputs = _merge_saved_inputs_with_execution_placeholders(
            declared, steps, _upload_input_descriptions([_upload_step(RECORDED_FILE_METADATA)])
        )
        assert len(inputs) == 1
        assert inputs[0]["description"].startswith(
            "Path to the file to upload (e.g. kyc_document.pdf)."
        )

    def test_no_override_leaves_other_declared_descriptions_untouched(self):
        declared = [{"id": "borrower_name", "label": "Borrower Name"}]
        inputs = _merge_saved_inputs_with_execution_placeholders(declared, [], {})
        assert inputs[0]["description"] == "Borrower Name"
