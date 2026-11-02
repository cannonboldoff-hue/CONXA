"""The compiler's second opinion (BUILD-25).

One whole-workflow LLM call decides four things no per-step rule can decide
correctly (llm/workflow_semantics.py), and compiler/second_opinion.py writes
them onto the compiled steps. Human Review is the gate — a reviewer sees
finished work and edits a wrong call there, so these tests cover the two things
that must hold for that to be safe:

  1. What the pass writes is confined to meaning — binding names, value
     placeholders, phase, optionality. Never a selector, an identity bundle, or
     an assertion (CLAUDE.md: "LLM does not write selector strings on the
     primary compile path").
  2. Disabled / failed / empty all fall back to the rules-only compile: the
     COMPILED STEPS must be byte-identical to a compile that never ran the pass
     at all (same guarantee as always). The compile_report is no longer
     required to match between disabled and failed — a genuine failure now
     bumps `status` to at least "review_needed" and adds a `degraded` entry, so
     a reviewer can tell "this pass never ran" apart from "this pass tried and
     the provider pool was down." Nothing about the executable package changes
     either way.
"""

from __future__ import annotations

from unittest.mock import patch

from conxa_core.config import settings

import json
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image

from conxa_core.config import settings
from conxa_core.models.skill_spec import SkillStep, WorkflowIntentGraph
from conxa_compile.compiler.second_opinion import (
    apply_second_opinion,
    archive_flagged_steps,
    build_try_dismiss_from_hint,
    rewrite_placeholder,
)
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.llm.workflow_semantics import _validate_findings


def _minimal_click_event() -> dict:
    return {
        "action": {"action": "click", "timestamp": "2026-01-01T00:00:00Z", "value": None},
        "target": {
            "tag": "button", "id": "submit", "classes": ["btn"], "inner_text": "  Submit  ",
            "role": "button", "aria_label": None, "name": None,
        },
        "selectors": {"css": "#submit", "xpath": "/button[1]", "text_based": 'text="Submit"', "aria": '[role="button"]'},
        "context": {"parent": "form#f", "siblings": [], "index_in_parent": 0, "form_context": "form#f"},
        "semantic": {"normalized_text": "submit", "role": "button", "input_type": None, "intent_hint": "activate_control"},
        "anchors": [{"element": "h1", "relation": "above"}],
        "visual": {
            "full_screenshot": "images/evt_0001_full.jpg", "element_snapshot": "images/evt_0001_element.jpg",
            "bbox": {"x": 10, "y": 20, "w": 80, "h": 32}, "viewport": "800x600", "scroll_position": "0,0",
            "timestamp_ms": 0,
        },
        "page": {"url": "https://example.com/app", "title": "App"},
        "state_change": {"before": "aaa", "after": "bbb"},
        "timing": {"wait_for": "load", "timeout": 5000},
        "ancestors": [], "surrounding_text": "", "snapshot": {"ref": "", "dom_hash": ""}, "extras": {},
    }


_VISION_ANCHOR_OK = {
    "chosen_frame": "before_near",
    "anchor_sentence": "The submit control inside the login form",
}


class _FakeRouter:
    pool = (object(),)

    def route_text(self, *a, **k):
        return None

    def route_vision(self, *a, **k):
        return None

    def stats(self):
        return {}


def _compile_fixture(session_id: str, events: list[dict]):
    data_dir = Path(tempfile.mkdtemp())
    for ev in events:
        rel = str(ev["visual"]["full_screenshot"])
        dest = data_dir / "sessions" / session_id / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 240), (210, 210, 210)).save(dest, "JPEG")
    return data_dir, (
        patch.object(settings, "data_dir", data_dir),
        patch("conxa_core.llm._router", _FakeRouter()),
        patch("conxa_compile.llm.anchor_vision_llm.call_llm", return_value=_VISION_ANCHOR_OK),
    )


def _type_step(**kw: Any) -> SkillStep:
    return SkillStep(action={"action": "type"}, **kw)


class SecondOpinionCompileTests(unittest.TestCase):
    """The pass, end to end through compile_skill_package."""

    def _compile(self, semantics_patch=None):
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        evs = run_pipeline([_minimal_click_event()])
        data_dir, patchers = _compile_fixture("sess", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                if semantics_patch is not None:
                    stack.enter_context(semantics_patch)
                return compile_skill_package(
                    evs, skill_id="skill_test", source_session_id="sess", title="t", version=1,
                )
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

    def _assert_rules_only(self, pkg) -> None:
        """A compile that fell back to the rules-only route leaves no trace."""
        self.assertNotIn("second_opinion", pkg.compile_report)
        self.assertEqual({s.phase for s in pkg.skills[0].steps}, {""})

    def test_disabled_flag_falls_back_to_rules_only(self) -> None:
        with patch.object(settings, "llm_semantic_suggestions_enabled", False):
            pkg = self._compile()
        self._assert_rules_only(pkg)

    def test_pass_failure_degrades_without_raising(self) -> None:
        boom = patch(
            "conxa_compile.llm.workflow_review.build_workflow_review",
            side_effect=RuntimeError("provider pool drained"),
        )
        pkg = self._compile(semantics_patch=boom)
        self._assert_rules_only(pkg)
        # A genuine failure — unlike a disabled pass — is now visible in the report.
        self.assertEqual(pkg.compile_report.get("status"), "review_needed")
        degraded = pkg.compile_report.get("degraded") or []
        self.assertTrue(any(d.get("pass") == "second_opinion" for d in degraded), degraded)

    def test_empty_response_falls_back_to_rules_only(self) -> None:
        empty = patch(
            "conxa_compile.llm.workflow_review.build_workflow_review",
            return_value=(WorkflowIntentGraph(), []),
        )
        pkg = self._compile(semantics_patch=empty)
        self._assert_rules_only(pkg)

    def test_disabled_and_failed_compiles_keep_identical_steps_but_differ_in_report(self) -> None:
        """The compiled STEPS stay byte-identical whether the pass was disabled or it
        genuinely failed — the Key Invariant this whole feature is scoped by. The
        compile_report is deliberately NOT required to match: a real failure must be
        visible to a reviewer, not indistinguishable from the pass simply being off."""
        with patch.object(settings, "llm_semantic_suggestions_enabled", False):
            disabled = self._compile()
        failed = self._compile(
            semantics_patch=patch(
                "conxa_compile.llm.workflow_review.build_workflow_review",
                side_effect=RuntimeError("provider pool drained"),
            )
        )
        self.assertEqual(
            [s.model_dump() for s in disabled.skills[0].steps],
            [s.model_dump() for s in failed.skills[0].steps],
        )
        self.assertEqual(disabled.compile_report.get("status"), "ok")
        self.assertNotIn("degraded", disabled.compile_report)
        self.assertEqual(failed.compile_report.get("status"), "review_needed")
        self.assertTrue(failed.compile_report.get("degraded"))

    def test_applied_finding_reaches_the_compiled_step(self) -> None:
        with patch.object(settings, "llm_semantic_suggestions_enabled", False):
            baseline = self._compile()
        key = step_keys(baseline.skills[0].steps)[0]
        canned = [{"step_key": key, "kind": "label_phase", "current": "", "proposed": "act", "why": "x"}]
        pkg = self._compile(
            semantics_patch=patch(
                "conxa_compile.llm.workflow_review.build_workflow_review",
                return_value=(WorkflowIntentGraph(), canned),
            )
        )

        self.assertEqual(pkg.compile_report["second_opinion"], canned)
        self.assertEqual(pkg.skills[0].steps[0].phase, "act")

        # ...and nothing about HOW the element is found moved.
        applied_step = pkg.skills[0].steps[0].model_dump()
        baseline_step = baseline.skills[0].steps[0].model_dump()
        for field in ("target", "identity_bundle", "compiled_selectors", "validation", "frame", "tab"):
            self.assertEqual(applied_step[field], baseline_step[field], field)

    @patch.object(settings, "compile_keep_every_action", False)

    def test_flag_noise_finding_removes_step_and_archives_it(self) -> None:
        """flag_noise (stage d) is the pass's one destructive kind: applied,
        but as an archive rather than a silent delete — the step disappears
        from what ships, but its full data survives under
        compile_report["archived_steps"] so it can be restored later."""
        baseline_steps = self._compile().skills[0].steps
        self.assertEqual(len(baseline_steps), 2, "fixture compiles to a synthetic navigate + the click")
        keys = step_keys(baseline_steps)
        click_key = keys[1]
        canned = [{
            "step_key": click_key, "kind": "flag_noise", "current": "",
            "proposed": "no_op_action", "why": "recorder observed no effect",
        }]
        pkg = self._compile(
            semantics_patch=patch(
                "conxa_compile.llm.workflow_review.build_workflow_review",
                return_value=(WorkflowIntentGraph(), canned),
            )
        )
        self.assertEqual(len(pkg.skills[0].steps), 1, "only the navigate step ships")
        self.assertEqual(pkg.skills[0].steps[0].action, "navigate")
        self.assertEqual(len(pkg.compile_report["archived_steps"]), 1)
        archived = pkg.compile_report["archived_steps"][0]
        self.assertEqual(archived["step_key"], click_key)
        self.assertEqual(archived["category"], "no_op_action")
        self.assertEqual(archived["step"]["action"], "click")

    def test_finding_for_an_unknown_step_applies_nothing(self) -> None:
        canned = [{"step_key": "not-a-real-key#1", "kind": "label_phase", "current": "", "proposed": "act"}]
        pkg = self._compile(
            semantics_patch=patch(
                "conxa_compile.llm.workflow_review.build_workflow_review",
                return_value=(WorkflowIntentGraph(), canned),
            )
        )
        self._assert_rules_only(pkg)


class ApplySecondOpinionTests(unittest.TestCase):
    """The applier itself, over synthetic steps — one case per kind."""

    def test_rename_binding_rewrites_binding_and_value(self) -> None:
        step = _type_step(input_binding="email_2", value="{{email_2}}")
        keys = step_keys([step])
        applied = apply_second_opinion(
            [step], [{"step_key": keys[0], "kind": "rename_binding", "current": "email_2", "proposed": "sender_email"}]
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(step.input_binding, "sender_email")
        self.assertEqual(step.value, "{{sender_email}}")

    def test_parameterize_literal_binds_a_recorded_value(self) -> None:
        step = _type_step(value="INV-2024-0891")
        keys = step_keys([step])
        apply_second_opinion(
            [step],
            [{"step_key": keys[0], "kind": "parameterize_literal", "current": "INV-2024-0891", "proposed": "reference_number"}],
        )
        self.assertEqual(step.input_binding, "reference_number")
        self.assertEqual(step.value, "{{reference_number}}")

    def test_parameterize_literal_never_repoints_an_existing_binding(self) -> None:
        step = _type_step(input_binding="already_bound", value="{{already_bound}}")
        keys = step_keys([step])
        self.assertEqual(
            apply_second_opinion(
                [step], [{"step_key": keys[0], "kind": "parameterize_literal", "current": "x", "proposed": "other"}]
            ),
            [],
        )
        self.assertEqual(step.input_binding, "already_bound")

    def test_parameterize_literal_never_repoints_a_download_upload_binding(self) -> None:
        # Regression: upload_binding.py deliberately writes {{downloaded_file}} into `value`
        # while setting input_binding to None (a download-populated placeholder must never
        # become a user-facing input) — the old `step.input_binding or ...` guard read that as
        # "unbound" and clobbered it into a hand-typed input, which is why a generalized
        # download->upload skill used to ask the operator for a file path at all.
        step = _type_step(input_binding=None, value="{{downloaded_file}}")
        keys = step_keys([step])
        self.assertEqual(
            apply_second_opinion(
                [step],
                [{"step_key": keys[0], "kind": "parameterize_literal", "current": "{{downloaded_file}}", "proposed": "uploaded_file_path"}],
            ),
            [],
        )
        self.assertIsNone(step.input_binding)
        self.assertEqual(step.value, "{{downloaded_file}}")

    def test_parameterize_literal_still_binds_a_genuine_recorded_literal(self) -> None:
        step = _type_step(input_binding=None, value="INV-2024-0891")
        keys = step_keys([step])
        applied = apply_second_opinion(
            [step],
            [{"step_key": keys[0], "kind": "parameterize_literal", "current": "INV-2024-0891", "proposed": "reference_number"}],
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(step.input_binding, "reference_number")
        self.assertEqual(step.value, "{{reference_number}}")

    def test_label_phase_sets_phase(self) -> None:
        step = _type_step()
        keys = step_keys([step])
        apply_second_opinion([step], [{"step_key": keys[0], "kind": "label_phase", "current": "", "proposed": "login"}])
        self.assertEqual(step.phase, "login")

    def test_suggest_optional_builds_a_try_dismiss_branch(self) -> None:
        step = _type_step(
            target={"primary_selector": "#cookie-ok"},
            optional_hint={"container_signal": ".cookie-banner"},
        )
        keys = step_keys([step])
        apply_second_opinion([step], [{"step_key": keys[0], "kind": "suggest_optional", "current": "", "proposed": "true"}])
        self.assertEqual(step.action["action"], "try_dismiss")
        self.assertEqual(step.intent, "try_dismiss_interstitial")
        self.assertEqual(step.branch["candidates"], ["#cookie-ok", ".cookie-banner"])
        self.assertIsNone(step.optional_hint)
        # try_dismiss bodies never enter the recovery cascade.
        self.assertEqual(step.recovery.max_attempts, 0)

    def test_suggest_optional_without_a_recorder_hint_applies_nothing(self) -> None:
        step = _type_step(target={"primary_selector": "#x"})
        keys = step_keys([step])
        self.assertEqual(
            apply_second_opinion([step], [{"step_key": keys[0], "kind": "suggest_optional", "proposed": "true"}]), []
        )
        self.assertEqual(step.branch, {})

    def test_rename_on_a_step_with_no_binding_applies_nothing(self) -> None:
        step = _type_step(value="literal")
        keys = step_keys([step])
        self.assertEqual(
            apply_second_opinion([step], [{"step_key": keys[0], "kind": "rename_binding", "proposed": "x"}]), []
        )

    def test_no_findings_is_a_no_op(self) -> None:
        step = _type_step(input_binding="a", value="{{a}}")
        self.assertEqual(apply_second_opinion([step], []), [])
        self.assertEqual(step.input_binding, "a")

    def test_suggest_assertion_appends_advisory_assertion(self) -> None:
        step = _type_step()
        keys = step_keys([step])
        proposal = json.dumps({"type": "text_present", "target": "Payment successful"})
        applied = apply_second_opinion(
            [step],
            [{"step_key": keys[0], "kind": "suggest_assertion", "current": "", "proposed": proposal}],
        )
        self.assertEqual(len(applied), 1)
        self.assertEqual(len(step.validation.assertions), 1)
        self.assertEqual(step.validation.assertions[0].type, "text_present")
        self.assertEqual(step.validation.assertions[0].target, "Payment successful")
        self.assertFalse(step.validation.assertions[0].required, "must always be advisory")

    def test_suggest_assertion_duplicate_is_a_noop(self) -> None:
        step = _type_step()
        keys = step_keys([step])
        proposal = json.dumps({"type": "text_present", "target": "Payment successful"})
        finding = {"step_key": keys[0], "kind": "suggest_assertion", "current": "", "proposed": proposal}
        apply_second_opinion([step], [finding])
        self.assertEqual(apply_second_opinion([step], [finding]), [], "already have this exact check")
        self.assertEqual(len(step.validation.assertions), 1)

    def test_suggest_assertion_malformed_json_applies_nothing(self) -> None:
        step = _type_step()
        keys = step_keys([step])
        self.assertEqual(
            apply_second_opinion(
                [step], [{"step_key": keys[0], "kind": "suggest_assertion", "proposed": "not json"}]
            ),
            [],
        )
        self.assertEqual(step.validation.assertions, [])


class ArchiveFlaggedStepsTests(unittest.TestCase):
    """flag_noise's own operation (stage d) — removes a step but never
    discards it, unlike every other kind, which only ever rewrites a field."""

    def test_matched_step_removed_and_archived(self) -> None:
        keep_a, drop_b, keep_c = _type_step(), _type_step(), _type_step()
        keys = step_keys([keep_a, drop_b, keep_c])
        kept, archived = archive_flagged_steps(
            [keep_a, drop_b, keep_c],
            [{
                "step_key": keys[1], "kind": "flag_noise",
                "proposed": "no_op_action", "why": "no observed effect",
            }],
        )
        self.assertEqual(kept, [keep_a, keep_c])
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["step_key"], keys[1])
        self.assertEqual(archived[0]["category"], "no_op_action")
        self.assertEqual(archived[0]["step"]["action"], {"action": "type"})

    def test_no_flag_noise_findings_returns_steps_unchanged(self) -> None:
        step = _type_step()
        keys = step_keys([step])
        kept, archived = archive_flagged_steps(
            [step], [{"step_key": keys[0], "kind": "label_phase", "proposed": "act"}]
        )
        self.assertEqual(kept, [step])
        self.assertEqual(archived, [])

    def test_finding_for_an_unknown_step_key_is_ignored(self) -> None:
        step = _type_step()
        kept, archived = archive_flagged_steps(
            [step], [{"step_key": "not-a-real-key#1", "kind": "flag_noise", "proposed": "no_op_action"}]
        )
        self.assertEqual(kept, [step])
        self.assertEqual(archived, [])

    def test_refuses_to_archive_a_click_that_triggers_a_download(self) -> None:
        # This is the exact real-world shape that shipped broken: a "no visible page change"
        # click whose entire job is to start a download the very next step waits for.
        click = SkillStep(action={"action": "click"})
        download = SkillStep(action={"action": "download_observed"}, value="{}")
        keys = step_keys([click, download])
        kept, archived = archive_flagged_steps(
            [click, download],
            [{"step_key": keys[0], "kind": "flag_noise", "proposed": "no_op_action"}],
        )
        self.assertEqual(kept, [click, download])
        self.assertEqual(archived, [])

    def test_refuses_to_archive_a_click_that_opens_a_file_chooser(self) -> None:
        click = SkillStep(action={"action": "click"})
        upload = SkillStep(action={"action": "upload_intent"}, value="{{downloaded_file}}")
        keys = step_keys([click, upload])
        kept, archived = archive_flagged_steps(
            [click, upload],
            [{"step_key": keys[0], "kind": "flag_noise", "proposed": "no_op_action"}],
        )
        self.assertEqual(kept, [click, upload])
        self.assertEqual(archived, [])

    def test_still_archives_an_isolated_click_with_no_side_effect_after_it(self) -> None:
        click = SkillStep(action={"action": "click"})
        harmless = SkillStep(action={"action": "click"})
        keys = step_keys([click, harmless])
        kept, archived = archive_flagged_steps(
            [click, harmless],
            [{"step_key": keys[0], "kind": "flag_noise", "proposed": "no_op_action"}],
        )
        self.assertEqual(kept, [harmless])
        self.assertEqual(len(archived), 1)

    def test_still_archives_when_a_real_action_sits_between_click_and_marker(self) -> None:
        # The download's real trigger is the click right before it — a flagged click two
        # steps earlier, separated by a genuine action, is not that trigger.
        click = SkillStep(action={"action": "click"})
        other = SkillStep(action={"action": "click"})
        download = SkillStep(action={"action": "download_observed"}, value="{}")
        keys = step_keys([click, other, download])
        kept, archived = archive_flagged_steps(
            [click, other, download],
            [{"step_key": keys[0], "kind": "flag_noise", "proposed": "no_op_action"}],
        )
        self.assertEqual(kept, [other, download])
        self.assertEqual(len(archived), 1)

    def test_lookahead_skips_tab_and_frame_markers(self) -> None:
        click = SkillStep(action={"action": "click"})
        tab_switch = SkillStep(action={"action": "tab_switch"})
        download = SkillStep(action={"action": "download_observed"}, value="{}")
        keys = step_keys([click, tab_switch, download])
        kept, archived = archive_flagged_steps(
            [click, tab_switch, download],
            [{"step_key": keys[0], "kind": "flag_noise", "proposed": "no_op_action"}],
        )
        self.assertEqual(kept, [click, tab_switch, download])
        self.assertEqual(archived, [])


class RewritePlaceholderTests(unittest.TestCase):
    """Shared by the applier and build.py::_deduplicate_input_bindings — a
    mixed value must not keep the old token while its binding moves on."""

    def test_mixed_value_rewritten(self) -> None:
        self.assertEqual(rewrite_placeholder("prefix {{ name }} suffix", "name", "sender"), "prefix {{sender}} suffix")

    def test_exact_value_rewritten(self) -> None:
        self.assertEqual(rewrite_placeholder("{{name}}", "name", "sender"), "{{sender}}")

    def test_unrelated_token_untouched(self) -> None:
        self.assertEqual(rewrite_placeholder("{{other}}", "name", "sender"), "{{other}}")

    def test_non_string_passes_through(self) -> None:
        self.assertIsNone(rewrite_placeholder(None, "name", "sender"))


class BuildTryDismissFromHintTests(unittest.TestCase):
    """One builder, two callers (the compiler's pass and the editor's
    confirm_optional_interstitial) — they cannot produce different shapes."""

    def test_dedupes_and_drops_blanks(self) -> None:
        built = build_try_dismiss_from_hint("#same", "#same")
        self.assertEqual(built["branch"]["candidates"], ["#same"])
        self.assertEqual(build_try_dismiss_from_hint("", "  ")["branch"]["candidates"], [])

    def test_editor_confirmation_produces_the_same_branch(self) -> None:
        from conxa_compile.editor.workflow_mutations import confirm_optional_interstitial

        doc = {
            "meta": {"version": 1},
            "skills": [{"steps": [{
                "action": {"action": "click"},
                "target": {"primary_selector": "#cookie-ok"},
                "optional_hint": {"container_signal": ".cookie-banner"},
            }]}],
        }
        out = confirm_optional_interstitial(doc, 0)
        step = out["skills"][0]["steps"][0]
        built = build_try_dismiss_from_hint("#cookie-ok", ".cookie-banner")
        self.assertEqual(step["branch"], built["branch"])
        self.assertEqual(step["intent"], built["intent"])
        self.assertEqual(step["recovery"], built["recovery"])
        self.assertIsNone(step["optional_hint"])

    def test_confirming_an_already_converted_step_fails_cleanly(self) -> None:
        from conxa_compile.editor.workflow_mutations import confirm_optional_interstitial

        doc = {
            "meta": {"version": 1},
            "skills": [{"steps": [{"action": {"action": "try_dismiss"}, "optional_hint": None}]}],
        }
        with self.assertRaises(ValueError) as ctx:
            confirm_optional_interstitial(doc, 0)
        self.assertEqual(str(ctx.exception), "step_has_no_optional_hint")


class ValidateFindingsTests(unittest.TestCase):
    """_validate_findings is the trust boundary between an LLM and a compiled
    artifact a customer will run. Every gate below is load-bearing now that
    what survives is applied rather than shown."""

    def setUp(self) -> None:
        self.steps_context = [
            {"key": "k1", "input_binding": "email_2", "has_optional_hint": False},
            {"key": "k2", "input_binding": None, "has_optional_hint": True},
        ]
        self.keys = {"k1", "k2"}

    def _validate(self, raw):
        return _validate_findings(raw, step_keys_in_workflow=self.keys, steps_context=self.steps_context)

    def test_good_finding_survives(self) -> None:
        out = self._validate([{"step_key": "k1", "kind": "rename_binding", "current": "email_2", "proposed": "sender_email", "why": "clear"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["proposed"], "sender_email")

    def test_unknown_step_key_dropped(self) -> None:
        self.assertEqual(self._validate([{"step_key": "not_in_workflow", "kind": "rename_binding", "proposed": "x"}]), [])

    def test_unknown_kind_dropped(self) -> None:
        self.assertEqual(self._validate([{"step_key": "k1", "kind": "group_steps", "proposed": "x"}]), [])

    def test_invalid_identifier_dropped(self) -> None:
        self.assertEqual(self._validate([{"step_key": "k1", "kind": "rename_binding", "proposed": "not valid!"}]), [])

    def test_colliding_binding_name_dropped(self) -> None:
        self.assertEqual(self._validate([{"step_key": "k2", "kind": "rename_binding", "proposed": "email_2"}]), [])

    def test_suggest_optional_without_recorder_hint_dropped(self) -> None:
        self.assertEqual(self._validate([{"step_key": "k1", "kind": "suggest_optional", "proposed": "true"}]), [])

    def test_suggest_optional_with_recorder_hint_survives(self) -> None:
        self.assertEqual(len(self._validate([{"step_key": "k2", "kind": "suggest_optional", "proposed": "true"}])), 1)

    def test_invalid_phase_dropped(self) -> None:
        self.assertEqual(self._validate([{"step_key": "k1", "kind": "label_phase", "proposed": "shopping"}]), [])

    def test_valid_phase_survives(self) -> None:
        self.assertEqual(len(self._validate([{"step_key": "k1", "kind": "label_phase", "proposed": "login"}])), 1)

    def test_mixed_batch_keeps_only_the_good_one(self) -> None:
        out = self._validate([
            {"step_key": "unknown", "kind": "rename_binding", "proposed": "x"},
            {"step_key": "k1", "kind": "totally_made_up", "proposed": "x"},
            {"step_key": "k1", "kind": "rename_binding", "proposed": "not valid!"},
            {"step_key": "k2", "kind": "rename_binding", "proposed": "email_2"},
            {"step_key": "k1", "kind": "suggest_optional", "proposed": "true"},
            {"step_key": "k1", "kind": "rename_binding", "current": "email_2", "proposed": "sender_email", "why": "clear"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["proposed"], "sender_email")

    def test_not_a_list_returns_empty(self) -> None:
        self.assertEqual(self._validate(None), [])

    def _validate_custom(self, raw: Any, steps_context: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = {str(s.get("key") or "") for s in steps_context}
        return _validate_findings(raw, step_keys_in_workflow=keys, steps_context=steps_context)

    def test_suggest_assertion_grounded_text_survives(self) -> None:
        ctx = [{"key": "k1", "target_text": "Payment successful", "intent": "", "url": ""}]
        proposal = json.dumps({"type": "text_present", "target": "Payment successful"})
        out = self._validate_custom(
            [{"step_key": "k1", "kind": "suggest_assertion", "proposed": proposal}], ctx
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(
            json.loads(out[0]["proposed"]), {"type": "text_present", "target": "Payment successful"}
        )

    def test_suggest_assertion_ungrounded_target_dropped(self) -> None:
        ctx = [{"key": "k1", "target_text": "", "intent": "", "url": ""}]
        proposal = json.dumps({"type": "text_present", "target": "Text nowhere in this workflow"})
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "suggest_assertion", "proposed": proposal}], ctx
            ),
            [],
        )

    def test_suggest_assertion_selector_type_rejected(self) -> None:
        """Never a selector-bearing type — its target is a raw Playwright
        selector, and this pass may never write one."""
        ctx = [{"key": "k1", "target_text": "#submit", "intent": "", "url": ""}]
        proposal = json.dumps({"type": "selector_present", "target": "#submit"})
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "suggest_assertion", "proposed": proposal}], ctx
            ),
            [],
        )

    def test_suggest_assertion_state_changed_allows_empty_target(self) -> None:
        ctx = [{"key": "k1", "target_text": "", "intent": "", "url": ""}]
        proposal = json.dumps({"type": "state_changed", "target": ""})
        out = self._validate_custom(
            [{"step_key": "k1", "kind": "suggest_assertion", "proposed": proposal}], ctx
        )
        self.assertEqual(len(out), 1)

    def test_suggest_assertion_malformed_json_dropped(self) -> None:
        ctx = [{"key": "k1", "target_text": "x", "intent": "", "url": ""}]
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "suggest_assertion", "proposed": "not json"}], ctx
            ),
            [],
        )

    def _noise_step(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "key": "k1", "action": "click", "post_condition_effect": "none",
            "has_required_assertion": False, "input_binding": None,
        }
        base.update(overrides)
        return base

    def test_flag_noise_recorder_verified_case_survives(self) -> None:
        out = self._validate_custom(
            [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
            [self._noise_step()],
        )
        self.assertEqual(len(out), 1)

    def test_flag_noise_unsafe_action_dropped(self) -> None:
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
                [self._noise_step(action="type")],
            ),
            [],
        )

    def test_flag_noise_real_effect_dropped(self) -> None:
        """The model's own opinion is never enough — only the recorder's own
        observation of no effect can trigger this."""
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
                [self._noise_step(post_condition_effect="value_set")],
            ),
            [],
        )

    def test_flag_noise_required_assertion_dropped(self) -> None:
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
                [self._noise_step(has_required_assertion=True)],
            ),
            [],
        )

    def test_flag_noise_input_binding_dropped(self) -> None:
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
                [self._noise_step(input_binding="reference_number")],
            ),
            [],
        )

    def test_flag_noise_causes_download_dropped(self) -> None:
        """build.py::_next_effect_cause marks a click whose only job is to start a
        download — the model must never get to flag it as a no-op even if it tries."""
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
                [self._noise_step(causes="file_download")],
            ),
            [],
        )

    def test_flag_noise_causes_file_chooser_dropped(self) -> None:
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "no_op_action"}],
                [self._noise_step(causes="file_chooser")],
            ),
            [],
        )

    def test_flag_noise_unknown_category_dropped(self) -> None:
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "made_up_category"}],
                [self._noise_step()],
            ),
            [],
        )

    # flag_noise's navigate exception: no post_condition_effect signal exists for a
    # navigate, so this path is gated instead on the immediately-previous step being a
    # navigate to the identical url on the identical tab — see workflow_semantics.py.

    def _nav_step(self, key: str, **overrides: Any) -> dict[str, Any]:
        base = {
            "key": key, "action": "navigate", "url": "https://drive.google.com/drive/?pli=1",
            "tab_id": "tab_1", "has_required_assertion": False, "input_binding": None,
        }
        base.update(overrides)
        return base

    def test_flag_noise_navigate_duplicate_of_previous_survives(self) -> None:
        ctx = [self._nav_step("k1"), self._nav_step("k2")]
        out = self._validate_custom(
            [{"step_key": "k2", "kind": "flag_noise", "proposed": "duplicate_action"}], ctx
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["step_key"], "k2")

    def test_flag_noise_navigate_different_url_dropped(self) -> None:
        ctx = [self._nav_step("k1"), self._nav_step("k2", url="https://drive.google.com/other")]
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k2", "kind": "flag_noise", "proposed": "duplicate_action"}], ctx
            ),
            [],
        )

    def test_flag_noise_navigate_different_tab_dropped(self) -> None:
        ctx = [self._nav_step("k1"), self._nav_step("k2", tab_id="tab_2")]
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k2", "kind": "flag_noise", "proposed": "duplicate_action"}], ctx
            ),
            [],
        )

    def test_flag_noise_navigate_previous_not_a_navigate_dropped(self) -> None:
        ctx = [self._noise_step(key="k1"), self._nav_step("k2")]
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k2", "kind": "flag_noise", "proposed": "duplicate_action"}], ctx
            ),
            [],
        )

    def test_flag_noise_navigate_no_op_category_dropped(self) -> None:
        """The navigate exception only ever accepts duplicate_action — it has no
        post_condition_effect evidence to support no_op_action or orphaned_hover."""
        ctx = [self._nav_step("k1"), self._nav_step("k2")]
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k2", "kind": "flag_noise", "proposed": "no_op_action"}], ctx
            ),
            [],
        )

    def test_flag_noise_navigate_first_step_no_previous_dropped(self) -> None:
        ctx = [self._nav_step("k1")]
        self.assertEqual(
            self._validate_custom(
                [{"step_key": "k1", "kind": "flag_noise", "proposed": "duplicate_action"}], ctx
            ),
            [],
        )
