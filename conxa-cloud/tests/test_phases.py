"""Lightweight regression tests for pipeline, confidence, and compiler (no Playwright)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from conxa_core.config import settings


def _minimal_click_event() -> dict:
    return {
        "action": {"action": "click", "timestamp": "2026-01-01T00:00:00Z", "value": None},
        "target": {
            "tag": "button",
            "id": "submit",
            "classes": ["btn", "primary"],
            "inner_text": "  Submit  ",
            "role": "button",
            "aria_label": None,
            "name": None,
        },
        "selectors": {
            "css": "#submit",
            "xpath": "/button[1]",
            "text_based": 'text="Submit"',
            "aria": '[role="button"]',
        },
        "context": {"parent": "form#f", "siblings": [], "index_in_parent": 0, "form_context": "form#f"},
        "semantic": {
            "normalized_text": "submit",
            "role": "button",
            "input_type": None,
            "intent_hint": "activate_control",
        },
        "anchors": [{"element": "h1", "relation": "above"}],
        "visual": {
            "full_screenshot": "images/evt_0001_full.jpg",
            "element_snapshot": "images/evt_0001_element.jpg",
            # Current recorder always emits all 5 offsets (frame_extractor.py); every label
            # points at the same test JPEG since these tests mock the LLM call itself.
            "frames": {
                "before_far": "images/evt_0001_full.jpg",
                "before_near": "images/evt_0001_full.jpg",
                "at": "images/evt_0001_full.jpg",
                "after_near": "images/evt_0001_full.jpg",
                "after_far": "images/evt_0001_full.jpg",
            },
            "bbox": {"x": 10, "y": 20, "w": 80, "h": 32},
            "viewport": "800x600",
            "scroll_position": "0,0",
            "timestamp_ms": 0,
        },
        "page": {"url": "https://example.com/app", "title": "App"},
        "state_change": {"before": "aaa", "after": "bbb"},
        "timing": {"wait_for": "load", "timeout": 5000},
        "ancestors": [],
        "surrounding_text": "",
        "snapshot": {"ref": "", "dom_hash": ""},
        "extras": {},
    }


def _write_minimal_screenshot(session_id: str, ev: dict, *, data_dir: Path) -> None:
    rel = str(ev["visual"]["full_screenshot"])
    dest = data_dir / "sessions" / session_id / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), (210, 210, 210)).save(dest, "JPEG")


_VISION_ANCHOR_OK = {
    "chosen_frame": "before_near",
    "anchor_sentence": "The submit control inside the login form",
}


class _FakeRouter:
    """LLM router stub for compile tests.

    A non-empty ``pool`` satisfies the compiler's provider gate; ``route_*`` are
    never reached because the per-task ``call_llm`` entry points are mocked.
    """

    pool = (object(),)

    def route_text(self, *args, **kwargs):
        return None

    def route_vision(self, *args, **kwargs):
        return None

    def stats(self):
        return {}


def _compile_with_vision_mocks(session_id: str, events: list[dict], *, call_llm_return=_VISION_ANCHOR_OK):
    """Prepare temp session JPEGs and return (data_dir, patch context managers)."""
    data_dir = Path(tempfile.mkdtemp())
    for ev in events:
        if str((ev.get("action") or {}).get("action") or "") != "scroll":
            _write_minimal_screenshot(session_id, ev, data_dir=data_dir)
    return (
        data_dir,
        patch.object(settings, "data_dir", data_dir),
        patch("conxa_core.llm._router", _FakeRouter()),
        patch("conxa_compile.llm.anchor_vision_llm.call_llm", return_value=call_llm_return),
    )


class PhaseTests(unittest.TestCase):
    def test_phase2_pipeline_cleans_and_enriches(self) -> None:
        from conxa_compile.pipeline.run import PIPELINE_VERSION, run_pipeline

        out = run_pipeline([_minimal_click_event()])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["extras"]["pipeline_version"], PIPELINE_VERSION)
        self.assertEqual(out[0]["target"]["inner_text"], "Submit")
        self.assertIn("content_fp", out[0]["extras"])
        self.assertEqual(out[0]["extras"]["primary_selector_kind"], "css")
        self.assertIn("selector_signature", out[0]["extras"])
        self.assertFalse(out[0]["semantic"].get("llm_intent"))

    def test_phase2_infers_input_type_from_label_without_llm(self) -> None:
        from conxa_compile.pipeline.run import run_pipeline

        ev = _minimal_click_event()
        ev["action"]["action"] = "fill"
        ev["target"]["tag"] = "input"
        ev["target"]["inner_text"] = "Work email"
        ev["semantic"]["normalized_text"] = "work email"
        ev["semantic"]["input_type"] = None
        with patch("conxa_compile.llm.semantic_llm.enrich_semantic") as enrich:
            out = run_pipeline([ev])
        enrich.assert_not_called()
        self.assertEqual(out[0]["semantic"].get("input_type"), "email")
        self.assertFalse(out[0]["semantic"].get("llm_intent"))

    def test_phase5_layered_self_match_executes(self) -> None:
        from conxa_compile.compiler.build import build_signal_reference
        from conxa_compile.confidence.layered import layered_decision

        ev = _minimal_click_event()
        ref = build_signal_reference(ev)
        decision = layered_decision(ref, ref)
        self.assertEqual(decision["decision"], "execute")
        self.assertEqual(decision["layer"], "dom")

    def test_phase3_compiler_emits_steps(self) -> None:
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        evs = run_pipeline([_minimal_click_event()])
        data_dir, *patchers = _compile_with_vision_mocks("sess", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                pkg = compile_skill_package(
                    evs,
                    skill_id="skill_test",
                    source_session_id="sess",
                    title="t",
                    version=1,
                )
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
        self.assertEqual(pkg.meta.id, "skill_test")
        # steps[0] is the leading synthetic navigate to the recording's starting page
        # (compiler/build.py::_insert_start_navigate_step) — the click under test is steps[1].
        self.assertEqual(len(pkg.skills[0].steps), 2)
        self.assertEqual(pkg.skills[0].steps[0].action, "navigate")
        self.assertEqual(pkg.skills[0].steps[1].action, "click")
        dumped = pkg.skills[0].steps[1].model_dump()
        self.assertIn("signals", dumped)
        self.assertIn("decision_policy", dumped)
        self.assertIn("intent", dumped)
        self.assertNotIn("state_diff", dumped)
        sem = dumped.get("signals", {}).get("semantic") or {}
        self.assertEqual(sem.get("final_intent"), dumped.get("intent"))
        self.assertEqual(sem.get("llm_intent"), dumped.get("intent"))
        anchors = dumped.get("signals", {}).get("anchors") or []
        self.assertTrue(anchors)
        self.assertEqual(anchors[0].get("relation"), "target")
        blob = " ".join(str(a.get("element") or "") for a in anchors)
        self.assertNotIn("h1", blob)

    def test_phase3_compiler_preserves_frame_context(self) -> None:
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        ev = _minimal_click_event()
        url = "https://app-na2.hubspot.com/object-builder/246242636/0-1/embed?"
        url_pattern = "^https://app\\-na2\\.hubspot\\.com/object\\-builder/[^/]+/0\\-1/embed$"
        ev["frame"] = {
            "chain": [
                {
                    "url": url,
                    "url_pattern": url_pattern,
                    "fingerprint": {
                        "signals": [
                            {"engine": "css-id", "selector": 'iframe[id="object-builder-ui"]',
                             "durability": 0.45, "orthogonality_class": "structural",
                             "unique_at_compile": False, "source": "compiler"},
                            {"engine": "testid", "selector": 'iframe[data-test-id="object-builder-ui-iframe"]',
                             "durability": 0.99, "orthogonality_class": "test-contract",
                             "unique_at_compile": False, "source": "compiler"},
                        ],
                        "url": url,
                        "url_pattern": url_pattern,
                    },
                }
            ]
        }
        evs = run_pipeline([ev])
        data_dir, *patchers = _compile_with_vision_mocks("s-frame", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                pkg = compile_skill_package(
                    evs,
                    skill_id="skill_frame",
                    source_session_id="s-frame",
                    title="t",
                    version=1,
                )
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

        # steps[0] is the leading synthetic navigate to the recording's starting page; the
        # click under test (with the frame chain) is steps[1].
        step = pkg.skills[0].steps[1].model_dump(mode="json")
        # Cutover: structural marker keeps url/url_pattern only (no selector field).
        assert step["frame"]["chain"][0]["url_pattern"] == url_pattern
        assert "selector" not in step["frame"]["chain"][0]
        # Frame resolution is driven by identity_bundle.frame_chain signals.
        frame_sigs = step["identity_bundle"]["frame_chain"][0]["signals"]
        assert any(s["selector"] == 'iframe[data-test-id="object-builder-ui-iframe"]' for s in frame_sigs)

    def test_phase3_compiler_keeps_distinct_iframe_steps_with_weak_selectors(self) -> None:
        """End-to-end: recorded steps across several iframes with generic/empty
        selectors (typical of HubSpot-style embedded micro-frontends) must all
        survive run_pipeline() + compile_skill_package() as distinct steps.

        Regression for a bug where clean_steps()'s target-dedup key ignored the
        frame chain, so weak-selector steps recorded in *different* iframes were
        treated as "the same field" and silently merged/dropped at compile time.
        """
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        def _frame_chain_event(label: str, idx: int, value: str) -> dict:
            ev = _minimal_click_event()
            ev["action"] = {"action": "type", "timestamp": "2026-01-01T00:00:00Z", "value": value}
            ev["target"] = {
                "tag": "input", "id": None, "classes": [], "inner_text": "",
                "role": None, "aria_label": None, "name": None,
            }
            ev["selectors"] = {"css": "", "xpath": "", "text_based": "", "aria": ""}
            ev["visual"]["full_screenshot"] = f"images/evt_{idx:04d}_full.jpg"
            ev["visual"]["element_snapshot"] = f"images/evt_{idx:04d}_element.jpg"
            ev["frame"] = {
                "chain": [
                    {
                        "url": f"https://app.hubspot.com/widgets/{label}",
                        "url_pattern": f"pattern_{label}",
                        "fingerprint": {
                            "signals": [
                                {"engine": "css-structural", "selector": f'iframe[src="{label}"]',
                                 "durability": 0.5, "orthogonality_class": "structural",
                                 "unique_at_compile": False, "source": "compiler"},
                            ],
                            "url": f"https://app.hubspot.com/widgets/{label}",
                            "url_pattern": f"pattern_{label}",
                        },
                    }
                ]
            }
            return ev

        raw = [
            _frame_chain_event("contact-editor", 0, "Alice"),
            _frame_chain_event("form-widget", 1, "Bob"),
            _frame_chain_event("note-composer", 2, "Carol"),
        ]
        evs = run_pipeline(raw)
        data_dir, *patchers = _compile_with_vision_mocks("s-multi-frame", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                pkg = compile_skill_package(
                    evs,
                    skill_id="skill_multi_frame",
                    source_session_id="s-multi-frame",
                    title="t",
                    version=1,
                )
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

        steps = pkg.skills[0].steps
        type_steps = [s for s in steps if s.action == "type"]
        self.assertEqual(len(type_steps), 3, "steps from different iframes must not collapse")
        values = [s.value for s in type_steps]
        self.assertEqual(values, ["Alice", "Bob", "Carol"])
        patterns = [s.model_dump(mode="json")["frame"]["chain"][0]["url_pattern"] for s in type_steps]
        self.assertEqual(patterns, ["pattern_contact-editor", "pattern_form-widget", "pattern_note-composer"])

    def test_phase6_patch_bumps_version(self) -> None:
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.compiler.patch import apply_step_patch
        from conxa_compile.pipeline.run import run_pipeline

        evs = run_pipeline([_minimal_click_event()])
        data_dir, *patchers = _compile_with_vision_mocks("s", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                pkg = compile_skill_package(
                    evs,
                    skill_id="skill_x",
                    source_session_id="s",
                    title="t",
                    version=1,
                )
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
        doc = pkg.model_dump(mode="json")
        patched = apply_step_patch(
            doc,
            0,
            {"target": {"primary_selector": "#submit2"}},
        )
        self.assertEqual(patched["meta"]["version"], 2)
        self.assertEqual(patched["skills"][0]["steps"][0]["target"]["primary_selector"], "#submit2")

    def test_phase6_patch_recomputes_recovery_strategies_from_resolved_intent(self) -> None:
        from unittest.mock import patch

        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.compiler.patch import apply_step_patch
        from conxa_compile.llm.semantic_llm import SemanticLLMOutput
        from conxa_compile.pipeline.run import run_pipeline

        evs = run_pipeline([_minimal_click_event()])
        data_dir, *patchers = _compile_with_vision_mocks("s", evs)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                pkg = compile_skill_package(
                    evs,
                    skill_id="skill_nav_patch",
                    source_session_id="s",
                    title="t",
                    version=1,
                )
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
        doc = pkg.model_dump(mode="json")
        # steps[0] is the leading synthetic navigate to the recording's starting page; the
        # click under test is steps[1].
        step = doc["skills"][0]["steps"][1]
        step["intent"] = ""
        rec = dict(step.get("recovery") or {})
        rec["intent"] = ""
        rec["final_intent"] = ""
        rec["strategies"] = ["semantic match", "position match", "visual match"]
        step["recovery"] = rec

        fake = SemanticLLMOutput(
            intent="navigate_to_checkout",
            normalized_text="checkout",
            confidence=0.95,
            source="test",
        )
        with patch("conxa_compile.compiler.patch.enrich_semantic", return_value=fake):
            patched = apply_step_patch(doc, 1, {"target": {"primary_selector": "#go"}})

        out_rec = patched["skills"][0]["steps"][1]["recovery"]
        self.assertEqual(out_rec.get("intent"), "navigate_to_checkout")
        self.assertEqual(out_rec.get("final_intent"), "navigate_to_checkout")
        self.assertNotIn("url_state_match", out_rec.get("strategies") or [])
        self.assertIn("llm_reasoned_match", out_rec.get("strategies") or [])

    def test_compiler_validation_commit_waits_dom_when_no_url_signal(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit", "confirm"]},
            "validation": {
                "default_timeout_ms": 5000,
                "submit_min_timeout_ms": 8000,
                "commit_no_evidence_wait": "intent_outcome",
            },
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"llm_intent": "submit_login_form"},
            "target": {"tag": "button", "inner_text": "Sign in", "type": "submit"},
            "timing": {"timeout": 5000},
        }
        state_diff = {"url_changed": False, "dom_changed": False}
        wf = infer_wait_for_shape(step, state_diff, policy)
        self.assertEqual(wf.get("type"), "intent_outcome")
        self.assertGreaterEqual(int(wf.get("timeout") or 0), 8000)

    def test_compiler_validation_commit_prefers_url_when_recorded(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "validation": {"default_timeout_ms": 5000, "submit_min_timeout_ms": 8000},
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"llm_intent": "submit_form"},
            "target": {"tag": "button", "type": "submit", "inner_text": "OK"},
            "timing": {"timeout": 5000},
        }
        state_diff = {"url_changed": True, "dom_changed": False}
        wf = infer_wait_for_shape(step, state_diff, policy)
        self.assertEqual(wf.get("type"), "url_change")
        self.assertGreaterEqual(int(wf.get("timeout") or 0), 8000)

    def test_compiler_validation_commit_no_diff_prefers_url_when_intent_checkout(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "validation": {"default_timeout_ms": 5000, "submit_min_timeout_ms": 8000, "commit_no_evidence_wait": "intent_outcome"},
            "decision_layer": {
                "intent_primary_validation": True,
                "commit_intent_prefer_url_substrings": ["checkout", "payment"],
                "commit_intent_prefer_dom_substrings": ["modal", "dialog"],
            },
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"final_intent": "submit_checkout_payment"},
            "target": {"tag": "button", "type": "submit", "inner_text": "Pay"},
            "timing": {"timeout": 5000},
        }
        state_diff = {"url_changed": False, "dom_changed": False}
        wf = infer_wait_for_shape(step, state_diff, policy)
        self.assertEqual(wf.get("type"), "url_change")
        self.assertGreaterEqual(int(wf.get("timeout") or 0), 8000)

    def test_compiler_validation_non_commit_intent_dropdown_element_appear(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "validation": {"default_timeout_ms": 5000, "navigation_min_timeout_ms": 6000},
            "decision_layer": {
                "intent_primary_validation": True,
                "intent_validation_facets": [
                    {
                        "intent_substrings": ["dropdown", "menu"],
                        "actions": ["click"],
                        "skip_when_commit": True,
                        "wait_for_type": "element_appear",
                    }
                ],
            },
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"final_intent": "open_filter_dropdown"},
            "selectors": {"aria": '[aria-haspopup="listbox"]'},
            "target": {"tag": "button", "inner_text": "Filter"},
            "timing": {"timeout": 4000},
        }
        state_diff = {"url_changed": False, "dom_changed": False}
        wf = infer_wait_for_shape(step, state_diff, policy)
        self.assertEqual(wf.get("type"), "element_appear")
        self.assertIn("aria-haspopup", str(wf.get("target") or ""))

    def test_infer_success_conditions_merges_intent_tokens_when_intent_primary(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_success_conditions

        policy = {
            "decision_layer": {"intent_primary_validation": True, "success_add_intent_tokens": True},
            "validation": {"default_timeout_ms": 5000},
        }
        wait_for = {"type": "intent_outcome", "timeout": 8000}
        state_diff = {"new_elements": [], "removed_elements": [], "text_change": ["welcome"]}
        out = infer_success_conditions(wait_for, state_diff, "https://ex.test/app", policy, final_intent="submit_login_form")
        tokens = out.get("expected_text_tokens") or []
        self.assertIn("welcome", tokens)
        self.assertIn("submit", tokens)
        self.assertIn("login", tokens)
        self.assertTrue(out.get("intent_validation_primary"))
        self.assertEqual(out.get("final_intent"), "submit_login_form")

    def test_intent_tokens_grounded_in_context_drops_ungrounded_keeps_grounded(self) -> None:
        # "new"/"button" describe the clicked element, not page content — neither appears in
        # this step's own recorded context, so both must be dropped. "welcome" does appear (in
        # inner_text) and must survive.
        from conxa_compile.compiler.decision_layer import intent_tokens_grounded_in_context

        ev = {
            "page": {"title": "Dashboard"},
            "target": {"inner_text": "Welcome back"},
            "semantic": {"normalized_text": "welcome back"},
            "context": {"siblings": []},
        }
        out = intent_tokens_grounded_in_context(["new", "button", "welcome"], ev)
        self.assertEqual(out, ["welcome"])

    def test_infer_success_conditions_drops_ungrounded_intent_tokens_for_plain_click(self) -> None:
        # Regression: "click_new_button" used to add "new"/"button" as text_present checks
        # regardless of whether either word actually appears anywhere near the clicked element —
        # almost every page has the word "button" somewhere, making the check meaningless. With a
        # real source_step whose context doesn't mention either word, both must be dropped.
        from conxa_compile.compiler.validation_planner import infer_success_conditions

        policy = {
            "decision_layer": {"intent_primary_validation": True, "success_add_intent_tokens": True},
            "validation": {"default_timeout_ms": 5000},
        }
        wait_for = {"type": "none", "timeout": 5000}
        state_diff = {"new_elements": [], "removed_elements": [], "text_change": []}
        source_step = {
            "page": {"title": "Repositories"},
            "target": {"inner_text": "New"},
            "semantic": {"normalized_text": "new"},
            "context": {"siblings": []},
        }
        out = infer_success_conditions(
            wait_for, state_diff, "https://ex.test/repos", policy,
            final_intent="click_new_button", source_step=source_step,
        )
        tokens = out.get("expected_text_tokens") or []
        self.assertNotIn("button", tokens)

    def test_merge_dom_diff_evidence_folds_recorded_added_elements(self) -> None:
        # bridge.js already scans the whole page before/after an action and records which
        # interactive elements appeared/disappeared (state_change.dom_diff) — this was validated
        # onto the model but never read by the compiler. A testid-bearing added element should
        # become a promotable selector; a plain-text one should become a text token; both should
        # lift evidence_strength above zero even when the step's own before/after descriptors
        # (compare_state) saw nothing.
        from conxa_compile.compiler.state_validation import merge_dom_diff_evidence

        ev = {
            "state_change": {
                "dom_diff": {
                    "added": [
                        'div|save-confirmation-banner|aria|Draft saved successfully',
                        'span||aria|Untagged element',
                    ],
                }
            }
        }
        state_diff = {"new_elements": [], "removed_elements": [], "text_change": [], "evidence_strength": 0.0}
        out = merge_dom_diff_evidence(ev, state_diff)
        self.assertIn('[data-testid="save-confirmation-banner"]', out["new_elements"])
        self.assertIn("Draft saved successfully", out["text_change"])
        self.assertIn("Untagged element", out["text_change"])
        self.assertGreater(out["evidence_strength"], 0.0)

    def test_merge_dom_diff_evidence_is_a_no_op_without_recorded_added_elements(self) -> None:
        from conxa_compile.compiler.state_validation import merge_dom_diff_evidence

        state_diff = {"new_elements": [], "removed_elements": [], "text_change": [], "evidence_strength": 0.0}
        out = merge_dom_diff_evidence({}, state_diff)
        self.assertEqual(out, state_diff)

    def test_capture_state_snapshot_does_not_leak_page_fingerprint_as_text(self) -> None:
        # state_change.before/after is bridge.js's pageFingerprint() — "url|title|hash" — an
        # internal identity string, not real page text. It used to get folded into
        # important_text_blocks and, when the hash segment changed between before/after, leak
        # straight into a user-facing text_present check like "the text
        # 'https://dashboard.render.com/|Render Dashboard|391b9b69' appears on the page".
        from conxa_compile.compiler.state_validation import capture_state_snapshot

        step = {
            "page": {"url": "https://dashboard.render.com/", "title": "Render Dashboard"},
            "target": {"tag": "button", "inner_text": "New"},
            "context": {},
            "selectors": {},
            "state_change": {
                "before": "https://dashboard.render.com/|Render Dashboard|391b9b69",
                "after": "https://dashboard.render.com/|Render Dashboard|a1b2c3d4",
            },
        }
        after = capture_state_snapshot(step, before=False)
        blocks = after["important_text_blocks"]
        self.assertNotIn("https://dashboard.render.com/|Render Dashboard|a1b2c3d4", blocks)
        self.assertTrue(all("|" not in b for b in blocks))
        self.assertIn("New", blocks)

    def test_infer_success_conditions_demotes_ephemeral_elements_from_required(self) -> None:
        # A cookie-consent banner and a toast happened to appear in the recorded diff alongside
        # a legitimate confirmation element — only the legitimate one may become the REQUIRED
        # promotion candidate; the ephemeral ones are demoted to advisory text tokens instead of
        # silently dropped.
        from conxa_compile.compiler.validation_planner import infer_success_conditions

        policy = {"validation": {}}
        wait_for = {"type": "element_appear", "timeout": 8000}
        state_diff = {
            "new_elements": [
                '[aria-label="Accept all cookies"]',
                ".order-confirmation",
                ".toast-notification",
            ],
            "removed_elements": [],
            "text_change": [],
        }
        out = infer_success_conditions(wait_for, state_diff, "https://ex.test/checkout", policy)
        required = out.get("required_elements") or []
        self.assertNotIn('[aria-label="Accept all cookies"]', required)
        self.assertNotIn(".toast-notification", required)
        self.assertIn(".order-confirmation", required)
        tokens = out.get("expected_text_tokens") or []
        self.assertIn('[aria-label="Accept all cookies"]', tokens)
        self.assertIn(".toast-notification", tokens)

    def test_effective_intent_prefers_final_intent_field(self) -> None:
        from conxa_compile.compiler.intent_access import get_effective_intent

        self.assertEqual(
            get_effective_intent({"final_intent": "focus_email", "llm_intent": "old_value"}),
            "focus_email",
        )

    def test_clean_anchors_prefers_semantic_parent_scope_over_bare_form(self) -> None:
        from conxa_compile.compiler.step_anchors import clean_anchors
        from conxa_compile.policy.bundle import load_policy_bundle

        pol = load_policy_bundle().data
        out = clean_anchors(
            [{"element": "h1", "relation": "above"}],
            {"parent": "form#checkout", "siblings": [], "form_context": "form#checkout"},
            pol,
            target={"inner_text": "Place order", "tag": "button", "aria_label": "", "name": ""},
            semantic={"normalized_text": "place order"},
        )
        elements = [str(a.get("element") or "") for a in out]
        self.assertTrue(any("form#checkout" in e for e in elements))
        self.assertNotIn("form", elements)

    def test_anchor_ranking_orders_by_target_overlap(self) -> None:
        from conxa_compile.compiler.decision_layer import rank_merged_anchors
        from conxa_compile.policy.bundle import load_policy_bundle

        pol = load_policy_bundle().data
        ev = {
            "target": {"inner_text": "Save", "name": "save_btn", "aria_label": "Save draft"},
            "semantic": {"normalized_text": "save"},
            "context": {"parent": "div.toolbar", "siblings": ["span:autosaved"]},
        }
        anchors = [
            {"element": "form", "relation": "inside"},
            {"element": "save draft", "relation": "near"},
        ]
        ranked = rank_merged_anchors(anchors, ev, "click_save", pol)
        self.assertEqual(ranked[0].get("element"), "save draft")

    def test_anchor_ranking_prefers_scope_and_intent_over_bare_form(self) -> None:
        from conxa_compile.compiler.decision_layer import rank_merged_anchors
        from conxa_compile.policy.bundle import load_policy_bundle

        pol = load_policy_bundle().data
        ev = {
            "target": {
                "inner_text": "Pay now",
                "name": "pay",
                "aria_label": "",
                "placeholder": "",
            },
            "semantic": {"normalized_text": "pay now"},
            "context": {"parent": "div#pay-panel", "siblings": []},
        }
        anchors = [
            {"element": "form", "relation": "inside"},
            {"element": "form#checkout", "relation": "inside"},
        ]
        ranked = rank_merged_anchors(anchors, ev, "submit_checkout_payment", pol)
        self.assertEqual(ranked[0].get("element"), "form#checkout")

    def test_recovery_strategies_merge_decision_layer_intent_facets(self) -> None:
        from conxa_compile.compiler.recovery_policy import recovery_strategies_for_intent
        from conxa_compile.policy.bundle import load_policy_bundle

        pol = load_policy_bundle().data
        strat = recovery_strategies_for_intent("navigate_to_account_settings", pol)
        self.assertIn("semantic match", strat)
        self.assertNotIn("url_state_match", strat)

    def test_default_recovery_block_includes_final_intent(self) -> None:
        from conxa_compile.compiler.recovery_policy import default_recovery_block
        from conxa_core.models.skill_spec import RecoveryBlock
        from conxa_compile.policy.bundle import load_policy_bundle

        pol = load_policy_bundle().data
        raw = default_recovery_block("open_filter_dropdown", [], pol)
        block = RecoveryBlock(**raw)
        self.assertEqual(block.intent, "open_filter_dropdown")
        self.assertEqual(block.final_intent, "open_filter_dropdown")

    def test_clean_steps_merges_nonconsecutive_duplicate_type_in_place(self) -> None:
        """Later type on same field updates the earlier type row; cross-field order stays chronological."""
        from conxa_compile.compiler.step_anchors import clean_steps

        def _ev(action: str, name: str, value: str | None = None) -> dict:
            base = {
                "target": {"tag": "input", "name": name, "id": f"id_{name}"},
                "selectors": {
                    "css": f"#{name}",
                    "aria": f'[role="input"][name="{name}"]',
                    "text_based": "",
                },
                "semantic": {"role": "input", "input_type": "email" if name == "email" else "password"},
                "context": {"form_context": "form"},
                "page": {"url": "https://example.com/login", "title": "Login"},
                "timing": {"timeout": 5000},
            }
            if action == "type":
                return {**base, "action": {"action": "type", "value": value}}
            return {**base, "action": {"action": "click", "value": None}}

        seq = [
            _ev("click", "email"),
            _ev("type", "email", "a@b.com"),
            _ev("click", "password"),
            _ev("type", "email", "c@d.com"),
            _ev("type", "password", "secret"),
        ]
        out = clean_steps(seq, {})
        names = []
        for s in out:
            act = (s.get("action") or {}).get("action")
            nm = (s.get("target") or {}).get("name")
            val = (s.get("action") or {}).get("value")
            names.append((act, nm, val))
        self.assertEqual(
            names,
            [
                ("type", "email", "c@d.com"),
                ("type", "password", "secret"),
            ],
        )
        from conxa_compile.compiler.step_anchors import sanitize_steps_preserving_order

        with_focus = sanitize_steps_preserving_order(out, {})
        actions = [(s.get("action") or {}).get("action") for s in with_focus]
        self.assertEqual(actions, ["focus", "type", "focus", "type"])

    def test_clean_steps_keeps_same_looking_fields_in_different_iframes(self) -> None:
        """Weak/generic-selector steps in different iframes must not collapse into one.

        HubSpot-style micro-frontends embed several iframes whose fields share identical
        (often empty) name/id/aria/css signals. Without frame identity in the dedup key,
        clean_steps() treated "the first empty <input>" in every frame as the same target
        and silently merged/dropped the rest.
        """
        from conxa_compile.compiler.step_anchors import clean_steps

        def _ev(frame_label: str, value: str) -> dict:
            return {
                "action": {"action": "type", "value": value},
                "target": {"tag": "input", "name": "", "id": "", "placeholder": ""},
                "selectors": {"aria": "", "text_based": "", "css": ""},
                "semantic": {},
                "frame": {"chain": [{"url": f"https://app.hubspot.com/widget/{frame_label}", "url_pattern": f"pattern_{frame_label}"}]},
                "page": {"url": "https://app.hubspot.com/contacts/123"},
                "context": {},
                "timing": {"timeout": 5000},
            }

        seq = [_ev("A", "Alice"), _ev("B", "Bob"), _ev("C", "Carol")]
        out = clean_steps(seq, {})
        values = [(s["frame"]["chain"][0]["url_pattern"], s["action"]["value"]) for s in out]
        self.assertEqual(
            values,
            [("pattern_A", "Alice"), ("pattern_B", "Bob"), ("pattern_C", "Carol")],
        )

    def test_sanitize_steps_preserving_order_inserts_focus_only_when_needed(self) -> None:
        from conxa_compile.compiler.step_anchors import clean_steps, sanitize_steps_preserving_order

        type_email = {
            "action": {"action": "type", "value": "x@y.com"},
            "target": {"tag": "input", "name": "email"},
            "selectors": {"css": "#e", "aria": '[role="input"][name="email"]', "text_based": ""},
            "semantic": {"role": "input", "input_type": "email"},
            "context": {"form_context": "form"},
            "page": {"url": "https://example.com/login", "title": "Login"},
            "timing": {"timeout": 5000},
        }
        focus_pw = {
            "action": {"action": "focus"},
            "target": {"tag": "input", "name": "password"},
            "selectors": {"css": "#p", "aria": '[role="input"][name="password"]', "text_based": ""},
            "semantic": {"role": "input", "input_type": "password"},
            "context": {"form_context": "form"},
            "page": {"url": "https://example.com/login", "title": "Login"},
            "timing": {"timeout": 5000},
        }
        type_pw = {
            "action": {"action": "type", "value": "pw"},
            "target": {"tag": "input", "name": "password"},
            "selectors": {"css": "#p", "aria": '[role="input"][name="password"]', "text_based": ""},
            "semantic": {"role": "input", "input_type": "password"},
            "context": {"form_context": "form"},
            "page": {"url": "https://example.com/login", "title": "Login"},
            "timing": {"timeout": 5000},
        }
        cleaned = clean_steps([type_email, focus_pw, type_pw], {})
        out = sanitize_steps_preserving_order(cleaned, {})
        actions = [(s.get("action") or {}).get("action") for s in out]
        self.assertEqual(actions, ["focus", "type", "focus", "type"])

    def test_compiler_clean_steps_drops_post_type_field_click(self) -> None:
        from conxa_compile.compiler.step_anchors import clean_steps

        type_ev = {
            "action": {"action": "type", "value": "secret"},
            "target": {"tag": "input", "name": "password", "id": "x"},
            "selectors": {"css": "#x", "aria": "[role=\"input\"][name=\"password\"]"},
            "semantic": {"role": "input", "input_type": "password"},
            "context": {"form_context": "form"},
            "page": {"url": "https://example.com/login", "title": "Login"},
            "timing": {"timeout": 5000},
        }
        click_ev = {
            "action": {"action": "click"},
            "target": {"tag": "input", "name": "password", "id": "x"},
            "selectors": {"css": "#x", "aria": "[role=\"input\"][name=\"password\"]"},
            "semantic": {"role": "input", "input_type": "password"},
            "context": {"form_context": "form"},
            "page": {"url": "https://example.com/login", "title": "Login"},
            "timing": {"timeout": 5000},
        }
        out = clean_steps([type_ev, click_ev], {})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["action"]["action"], "type")

    def test_clean_steps_drops_prep_click_before_upload_intent(self) -> None:
        """Regression: a recorded click on a file input followed by upload_intent must not
        survive into the compiled output. If it does, the compiler's click->focus rewrite turns
        it into a `focus` step, and runtime's focus handler clicks before it focuses -- reopening
        an OS file dialog nothing can drive during an unattended run. This is the exact 3-event
        sequence (click, upload_intent, click) that produced a real hung skill."""
        from conxa_compile.compiler.step_anchors import clean_steps

        def _file_step(action: str, value=None) -> dict:
            return {
                "action": {"action": action, "value": value},
                "target": {"tag": "input", "id": "file-upload", "name": "file", "label_text": "File Uploader"},
                "selectors": {"css": "#file-upload", "aria": '[role="textbox"][name="file"]'},
                "semantic": {"role": "textbox", "input_type": "file"},
                "context": {"form_context": "form"},
                "page": {"url": "https://example.com/upload", "title": "Upload"},
                "timing": {"timeout": 5000},
            }

        submit_click = {
            "action": {"action": "click"},
            "target": {"tag": "input", "id": "file-submit", "type": "submit"},
            "selectors": {"css": "#file-submit"},
            "semantic": {"role": "button"},
            "context": {},
            "page": {"url": "https://example.com/upload", "title": "Upload"},
            "timing": {"timeout": 5000},
        }
        seq = [
            _file_step("click"),
            _file_step("upload_intent", value='[{"name":"kyc.pdf","size":1,"type":"application/pdf"}]'),
            submit_click,
        ]
        out = clean_steps(seq, {})
        actions = [(s.get("action") or {}).get("action") for s in out]
        self.assertEqual(actions, ["upload_intent", "click"])

    def test_clean_steps_drops_file_input_click_even_with_intervening_menu_click(self) -> None:
        """Google Drive (and similar) programmatically activate a hidden <input type=file>
        when the user clicks 'File upload' in a menu. The recorder therefore emits:
        click(file input), click(menu item), upload_intent(file input). The existing
        immediate-predecessor merge only pops cleaned[-1], so the file-input click survived
        compile, replayed as a 0x0 textbox, and failed Test Skill with resolve miss."""
        from conxa_compile.compiler.step_anchors import clean_steps

        def _file_step(action: str, value=None) -> dict:
            return {
                "action": {"action": action, "value": value},
                "target": {"tag": "input", "id": None, "name": None},
                "selectors": {"css": "body > input:nth-of-type(2)", "aria": "", "text_based": ""},
                "semantic": {"role": "textbox", "input_type": "file"},
                "context": {},
                "page": {"url": "https://drive.google.com/drive/folders/x", "title": "Data"},
                "timing": {"timeout": 5000},
            }

        menu_click = {
            "action": {"action": "click"},
            "target": {"tag": "li", "inner_text": "File upload Alt+C then U"},
            "selectors": {"css": "ul[role=menu] > li:nth-of-type(3)", "text_based": 'text="File upload Alt+C then U"'},
            "semantic": {"role": "menuitem"},
            "context": {},
            "page": {"url": "https://drive.google.com/drive/folders/x", "title": "Data"},
            "timing": {"timeout": 5000},
        }
        seq = [
            _file_step("click"),
            menu_click,
            _file_step("upload_intent", value='[{"name":"Ada.gitignore","size":51,"type":"text/plain"}]'),
        ]
        out = clean_steps(seq, {})
        actions = [(s.get("action") or {}).get("action") for s in out]
        self.assertEqual(actions, ["click", "upload_intent"])
        self.assertEqual((out[0].get("target") or {}).get("tag"), "li")

    def test_is_editable_target_excludes_file_inputs(self) -> None:
        """A file input isn't a text-entry field -- clicking it invokes a native OS picker, not
        caret placement. Treating it as editable made the click->focus rewrite fire, and
        runtime's focus handler clicks before it focuses, reopening an undismissable dialog."""
        from conxa_compile.compiler.action_semantics import is_editable_target

        file_input = {"target": {"tag": "input"}, "semantic": {"input_type": "file"}}
        self.assertFalse(is_editable_target(file_input))

        text_input = {"target": {"tag": "input"}, "semantic": {"input_type": "text"}}
        self.assertTrue(is_editable_target(text_input))

        untyped_input = {"target": {"tag": "input"}}
        self.assertTrue(is_editable_target(untyped_input))

        self.assertTrue(is_editable_target({"target": {"tag": "textarea"}}))
        self.assertTrue(is_editable_target({"target": {"tag": "select"}}))
        self.assertFalse(is_editable_target({"target": {"tag": "button"}}))

    def test_normalize_prep_click_to_focus_leaves_file_input_click_alone(self) -> None:
        """With is_editable_target excluding file inputs, a lone click on one (e.g. a recorded
        picker that was opened then cancelled, leaving no upload_intent to merge against) is no
        longer relabeled `focus` -- which would make runtime's click-first focus handler open an
        undismissable OS dialog. It stays a literal `click`; see TODO.md for the residual risk
        that a lone click on a file input is still not runtime-safe on its own."""
        from conxa_compile.compiler.step_anchors import clean_steps

        click_only = {
            "action": {"action": "click"},
            "target": {"tag": "input", "id": "file-upload", "name": "file"},
            "selectors": {"css": "#file-upload"},
            "semantic": {"role": "textbox", "input_type": "file"},
            "context": {},
            "page": {"url": "https://example.com/upload", "title": "Upload"},
            "timing": {"timeout": 5000},
        }
        out = clean_steps([click_only], {})
        self.assertEqual(out[0]["action"]["action"], "click")

    def test_derive_input_binding_always_names_upload_file_path(self) -> None:
        """Without this, the binding name is guessed from whatever label text sits near the file
        input ("File Uploader", "Attach document", "Upload CSV", ...), so every upload skill asks
        for a differently-named input for the same concept."""
        from conxa_compile.compiler.input_binding import derive_input_binding

        ev = {
            "action": {"action": "upload_intent", "value": '[{"name":"kyc.pdf","size":1,"type":"application/pdf"}]'},
            "target": {"label_text": "File Uploader", "aria_label": "Upload CSV", "placeholder": "Choose a file"},
            "semantic": {"input_type": "file"},
        }
        value, binding = derive_input_binding(ev, {})
        self.assertEqual((value, binding), ("{{file_path}}", "file_path"))

        # Same for a step already normalized to the "upload" action name (post-compile shape).
        ev2 = {**ev, "action": {"action": "upload", "value": None}}
        value2, binding2 = derive_input_binding(ev2, {})
        self.assertEqual((value2, binding2), ("{{file_path}}", "file_path"))

    def test_derive_input_binding_skips_value_pattern_for_a_select(self) -> None:
        """Regression: a nameless year <select>'s recorded value ("2007") matched
        _DIGITS_ONLY_REGEX and bound to the generic {{number}} — the same name a completely
        unrelated mobile-number field on the same form also bound to, so the select tried to
        pick whatever the caller supplied for THAT field instead. Pattern classification is a
        free-text heuristic; a select's value is one of a fixed set of options, not free text."""
        from conxa_compile.compiler.input_binding import derive_input_binding

        ev = {
            "action": {"action": "select", "value": "2007"},
            "target": {"tag": "select", "label_text": None, "placeholder": None, "aria_label": None, "name": None, "id": None},
            "semantic": {"input_type": "text"},
        }
        value, binding = derive_input_binding(ev, {})
        self.assertNotEqual(binding, "number", "must not classify a select's value as a free-text number pattern")
        self.assertEqual((value, binding), ("2007", None), "falls through to the literal recorded value")

        # Control: the same value pattern classification still fires for a plain type/fill on
        # a non-select target — the guard is select-specific, not a blanket removal.
        ev_type = {
            "action": {"action": "type", "value": "2007"},
            "target": {"tag": "input", "label_text": None, "placeholder": None, "aria_label": None},
            "semantic": {"input_type": "text"},
        }
        value_type, binding_type = derive_input_binding(ev_type, {})
        self.assertEqual((value_type, binding_type), ("{{number}}", "number"))

    def test_pipeline_drops_zero_bbox_hover_events(self) -> None:
        from conxa_compile.pipeline.run import _drop_non_actionable_hover_events

        def _event(action: str, width: int, height: int) -> dict:
            return {
                "action": {"action": action, "timestamp": "2026-01-01T00:00:00Z", "value": None},
                "target": {"tag": "div", "id": "loading", "classes": [], "inner_text": "Loading", "role": "status"},
                "selectors": {"css": "#loading", "xpath": "/div[1]", "text_based": 'text="Loading"', "aria": '[role="status"][name="Loading"]'},
                "context": {"parent": "main", "siblings": [], "index_in_parent": 0, "form_context": None},
                "semantic": {"normalized_text": "loading", "role": "status", "input_type": None, "intent_hint": "interact"},
                "anchors": [{"element": "Loading", "relation": "inside"}],
                "visual": {"bbox": {"x": 0, "y": 0, "w": width, "h": height}, "viewport": "1280x720", "scroll_position": "0,0"},
                "page": {"url": "https://example.com", "title": "Example"},
                "state_change": {"before": "", "after": ""},
                "timing": {"wait_for": "load", "timeout": 5000},
                "extras": {},
            }

        out = _drop_non_actionable_hover_events([
            _event("hover", 0, 0),
            _event("click", 0, 0),
            _event("hover", 20, 10),
        ])

        self.assertEqual([item["action"]["action"] for item in out], ["click", "hover"])

    def test_selector_filters_reject_dynamic_id_and_weak_tokens(self) -> None:
        from conxa_compile.compiler.selector_filters import selector_passes_filters

        self.assertFalse(selector_passes_filters("#_r_3_"))
        self.assertFalse(selector_passes_filters("password"))
        self.assertFalse(selector_passes_filters('[role="input"][name="email"]'))
        self.assertFalse(selector_passes_filters('[role="path"]'))
        self.assertTrue(selector_passes_filters('input[name="password"]'))

    def test_intent_normalization_maps_click_prefix_on_editable(self) -> None:
        from conxa_compile.policy.intent_ontology import normalize_compiler_intent

        ev = {
            "action": {"action": "focus"},
            "target": {"tag": "input", "name": "password"},
            "semantic": {"input_type": "password"},
        }
        policy = {"intent": {"generic_intents": ["interact"]}}
        out = normalize_compiler_intent(ev, "click_password", policy)
        self.assertEqual(out, "focus_password")

    def test_normalize_returns_blank_for_ordinary_element_with_no_llm_intent(self) -> None:
        # No LLM intent, a plain named/labeled element (not the icon-only path/svg/g case, not a
        # bare tag-only button/input) — there's nothing real to derive from, so this must come
        # back blank rather than a synthesized "click_<name>" template.
        from conxa_compile.policy.intent_ontology import normalize_compiler_intent

        ev = {
            "action": {"action": "click"},
            "target": {"tag": "li", "name": "Acme Corp", "aria_label": "", "inner_text": ""},
            "semantic": {"role": "option"},
        }
        policy = {"intent": {"generic_intents": ["interact"]}}
        out = normalize_compiler_intent(ev, "", policy)
        self.assertEqual(out, "")

    def test_static_audit_flags_weak_reference(self) -> None:
        from conxa_compile.confidence.uncertainty import audit_reference

        ref = {
            "action_kind": "click",
            "selectors": {"css": ""},
            "anchors": [],
            "visual": {"bbox": {"x": 1, "y": 1, "w": 0, "h": 0}},
        }
        issues = audit_reference(ref)
        self.assertIn("empty_primary_css", issues)
        self.assertIn("anchors_empty", issues)
        self.assertIn("weak_visual_bbox", issues)

    def test_static_audit_downgrades_missing_anchors_when_signals_are_strong(self) -> None:
        from conxa_compile.confidence.uncertainty import audit_reference

        ref = {
            "action_kind": "type",
            "selectors": {"css": 'input[name="email"]', "aria": '[aria-label="Email"]'},
            "semantic": {"llm_intent": "enter_email"},
            "anchors": [],
            "visual": {"bbox": {"x": 1, "y": 1, "w": 40, "h": 20}},
        }
        issues = audit_reference(ref)
        self.assertIn("anchors_empty_warn", issues)
        self.assertNotIn("anchors_empty", issues)

    def test_static_audit_requires_anchors_for_destructive_intent(self) -> None:
        from conxa_compile.confidence.uncertainty import audit_reference

        ref = {
            "action_kind": "click",
            "selectors": {"css": 'button[data-action="delete"]'},
            "target": {"inner_text": "Delete account", "role": "button", "type": "button"},
            "semantic": {"llm_intent": "delete_account"},
            "anchors": [],
            "visual": {"bbox": {"x": 2, "y": 2, "w": 30, "h": 20}},
        }
        issues = audit_reference(ref)
        self.assertIn("anchors_empty_required", issues)

    def test_static_audit_weak_destructive_intent_is_warning_only(self) -> None:
        from conxa_compile.confidence.uncertainty import audit_reference

        ref = {
            "action_kind": "click",
            "selectors": {"css": 'button[data-action="remove-filter"]', "aria": '[aria-label="Apply"]'},
            "target": {"inner_text": "Apply", "role": "button", "type": "button"},
            "semantic": {"llm_intent": "remove_filter"},
            "anchors": [],
            "visual": {"bbox": {"x": 2, "y": 2, "w": 30, "h": 20}},
        }
        issues = audit_reference(ref)
        self.assertIn("anchors_empty_warn", issues)
        self.assertNotIn("anchors_empty_required", issues)

    def test_static_audit_explicit_destructive_flag_requires_anchors(self) -> None:
        from conxa_compile.confidence.uncertainty import audit_reference

        ref = {
            "action_kind": "click",
            "selectors": {"css": 'button[data-kind="danger"]'},
            "target": {"inner_text": "Confirm", "role": "button", "type": "button"},
            "semantic": {"llm_intent": "confirm_action", "is_destructive": True},
            "anchors": [],
            "visual": {"bbox": {"x": 2, "y": 2, "w": 30, "h": 20}},
        }
        issues = audit_reference(ref)
        self.assertIn("anchors_empty_required", issues)

    def test_infer_wait_non_commit_ignores_dom_when_policy_none(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "validation": {"default_timeout_ms": 5000, "non_commit_dom_wait_on_diff": "none"},
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"final_intent": "click_sidebar_item"},
            "target": {"tag": "span"},
            "timing": {},
        }
        state_diff = {"url_changed": False, "dom_changed": True}
        wf = infer_wait_for_shape(step, state_diff, policy)
        self.assertEqual(wf.get("type"), "none")

    def test_destructive_click_uses_element_appear_when_selector_present(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "decision_layer": {"intent_primary_validation": True},
            "validation": {
                "default_timeout_ms": 4000,
                "submit_min_timeout_ms": 8000,
                "navigation_min_timeout_ms": 6000,
                "destructive_require_confirmation_wait": True,
                "destructive_wait_for_type": "element_appear",
            },
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"final_intent": "delete_account_row"},
            "selectors": {"aria": '[data-testid="confirm-delete"]'},
            "target": {"tag": "button", "inner_text": "Delete"},
            "timing": {"timeout": 4000},
        }
        wf = infer_wait_for_shape(step, {"url_changed": False, "dom_changed": False}, policy)
        self.assertEqual(wf.get("type"), "element_appear")
        self.assertIn("confirm-delete", str(wf.get("target") or ""))

    def test_commit_no_evidence_intent_first_prefers_url_for_checkout(self) -> None:
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "validation": {
                "default_timeout_ms": 5000,
                "submit_min_timeout_ms": 8000,
                "commit_no_evidence_wait": "intent_outcome",
                "commit_no_evidence_intent_first": True,
            },
            "decision_layer": {
                "intent_primary_validation": True,
                "commit_intent_prefer_url_substrings": ["checkout", "payment"],
            },
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"final_intent": "submit_checkout_payment"},
            "target": {"tag": "button", "type": "submit", "inner_text": "Pay"},
            "timing": {"timeout": 5000},
        }
        state_diff = {"url_changed": False, "dom_changed": False}
        wf = infer_wait_for_shape(step, state_diff, policy)
        self.assertEqual(wf.get("type"), "url_change")

    def test_commit_no_evidence_wait_rejects_retired_dom_change(self) -> None:
        """dom_change was retired in favor of intent_outcome — a policy file still specifying
        it must fail the compile loudly, not silently translate (see validation_planner.py)."""
        from conxa_compile.compiler.validation_planner import infer_wait_for_shape

        policy = {
            "workflow": {"commit_intent_substrings": ["submit"]},
            "validation": {
                "default_timeout_ms": 5000,
                "submit_min_timeout_ms": 8000,
                "commit_no_evidence_wait": "dom_change",
            },
        }
        step = {
            "action": {"action": "click"},
            "semantic": {"llm_intent": "submit_form"},
            "target": {"tag": "button", "type": "submit", "inner_text": "OK"},
            "timing": {"timeout": 5000},
        }
        state_diff = {"url_changed": False, "dom_changed": False}
        with self.assertRaises(ValueError):
            infer_wait_for_shape(step, state_diff, policy)

    def test_normalize_upgrades_click_button_with_visible_text(self) -> None:
        from conxa_compile.policy.bundle import get_policy_bundle
        from conxa_compile.policy.intent_ontology import normalize_compiler_intent

        pol = get_policy_bundle().data
        ev = {
            "action": {"action": "click"},
            "target": {"tag": "button", "inner_text": "Save draft", "name": "", "aria_label": ""},
            "semantic": {"normalized_text": "save draft", "role": "button"},
        }
        out = normalize_compiler_intent(ev, "click_button", pol)
        self.assertTrue(out.startswith("activate_control_") or "save" in out)

    def test_normalize_strips_click_path_uses_intent_hint(self) -> None:
        from conxa_compile.policy.bundle import get_policy_bundle
        from conxa_compile.policy.intent_ontology import normalize_compiler_intent

        pol = get_policy_bundle().data
        ev = {
            "action": {"action": "click"},
            "target": {"tag": "path", "inner_text": "Export CSV", "name": "", "aria_label": ""},
            "semantic": {"normalized_text": "export csv", "role": "graphics-symbol", "intent_hint": "activate_control"},
        }
        out = normalize_compiler_intent(ev, "click_path", pol)
        self.assertNotIn("path", out)
        self.assertNotEqual(out, "click_path")

    def test_compile_requires_a_source_session(self) -> None:
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        evs = run_pipeline([_minimal_click_event()])
        with self.assertRaises(ValueError) as ctx:
            compile_skill_package(
                evs,
                skill_id="x",
                source_session_id="   ",
                title="t",
                version=1,
            )
        self.assertEqual(str(ctx.exception), "source_session_id_required")

    def test_consequential_click_with_zero_evidence_flags_weak_evidence(self) -> None:
        # A commit-style click ("Submit") with no wait_for evidence and no recorded DOM/dom_diff
        # change gets build.py's synthesized state_changed fallback ("something happened, but we
        # don't know what") — this must be surfaced as a compile warning, not stay silent until
        # the step's fake-pass check fails at runtime.
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        ev = _minimal_click_event()
        ev["state_change"] = {"before": "aaa", "after": "aaa"}
        evs = run_pipeline([ev])
        data_dir, *patchers = _compile_with_vision_mocks("sess", evs, call_llm_return=_VISION_ANCHOR_OK)
        try:
            with ExitStack() as stack:
                for p in patchers:
                    stack.enter_context(p)
                pkg = compile_skill_package(
                    evs,
                    skill_id="z",
                    source_session_id="sess",
                    title="t",
                    version=1,
                )
                # steps[0] is the leading synthetic navigate to the recording's starting page;
                # the click under test is steps[1].
                step = pkg.skills[0].steps[1].model_dump()
                assertions = step.get("validation", {}).get("assertions") or []
                self.assertTrue(any(a.get("type") == "state_changed" for a in assertions))
                cw = (step.get("confidence_protocol") or {}).get("compile_warnings") or {}
                self.assertTrue(cw.get("weak_evidence"))
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)

    def test_anchor_vision_frameset_sends_all_frames_and_returns_sentence_plus_phrases(self) -> None:
        from conxa_compile.llm.anchor_vision_llm import generate_anchors_for_step_or_raise
        from conxa_compile.policy.bundle import get_policy_bundle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            for name in ("before_far", "before_near", "at", "after_near", "after_far"):
                Image.new("RGB", (100, 80), "white").save(root / "images" / f"{name}.jpg")
            with (
                patch.object(settings, "data_dir", root),
                patch("conxa_core.llm._router", _FakeRouter()),
                patch("conxa_compile.llm.anchor_vision_llm.supports_multimodal_chat", return_value=True),
                patch(
                    "conxa_compile.llm.anchor_vision_llm.call_llm",
                    return_value={
                        "chosen_frame": "before_near",
                        "anchor_sentence": "The blue Sign in button below the password field",
                    },
                ) as call,
            ):
                result = generate_anchors_for_step_or_raise(
                    {
                        "visual": {
                            "frames": {
                                l: f"images/{l}.jpg"
                                for l in ("before_far", "before_near", "at", "after_near", "after_far")
                            },
                            "bbox": {"x": 1, "y": 1, "w": 30, "h": 20},
                            "viewport": "100x80",
                        }
                    },
                    session_root=root,
                    final_intent="enter_email",
                    policy=get_policy_bundle().data,
                    step_index=0,
                )

        self.assertEqual(call.call_args.args[0], "anchor_vision_frameset")
        payload = call.call_args.args[1]
        self.assertEqual(len(payload["frames"]), 5)
        self.assertEqual([f["label"] for f in payload["frames"]],
                          ["before_far", "before_near", "at", "after_near", "after_far"])
        self.assertEqual(result["chosen_frame"], "before_near")
        self.assertTrue(result["anchor_sentence"])
        self.assertTrue(result["anchor_phrases"])

    def test_anchor_vision_frameset_rejects_recordings_with_no_frames(self) -> None:
        """A recording missing the 5-frame set (predates current extraction, or extraction
        failed for this step) must fail the compile loudly — no silent 1-frame degrade."""
        from conxa_compile.llm.anchor_vision_llm import (
            VisionAnchorGenerationError,
            generate_anchors_for_step_or_raise,
        )
        from conxa_compile.policy.bundle import get_policy_bundle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            Image.new("RGB", (1280, 720), "white").save(root / "images" / "a.png", "PNG")
            with (
                patch.object(settings, "data_dir", root),
                patch("conxa_core.llm._router", _FakeRouter()),
                patch("conxa_compile.llm.anchor_vision_llm.supports_multimodal_chat", return_value=True),
            ):
                with self.assertRaises(VisionAnchorGenerationError) as ctx:
                    generate_anchors_for_step_or_raise(
                        {
                            "visual": {
                                "full_screenshot": "images/a.png",
                                "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                                "viewport": "1280x720",
                            }
                        },
                        session_root=root,
                        final_intent="enter_email",
                        policy=get_policy_bundle().data,
                        step_index=0,
                    )
        self.assertEqual(ctx.exception.reason, "full_screenshot_path_missing")

    def test_prefetch_vision_anchors_parallel_populates_cache_for_the_per_step_call(self) -> None:
        """One anchor_vision_frameset call per step, fired concurrently, writes
        successes into the anchor cache, so generate_anchors_for_step_or_raise's
        later per-step call becomes a cache hit — zero extra network calls for
        anything the prefetch already resolved."""
        from conxa_compile.llm.anchor_vision_llm import (
            generate_anchors_for_step_or_raise,
            prefetch_vision_anchors_parallel,
        )
        from conxa_compile.policy.bundle import get_policy_bundle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            Image.new("RGB", (100, 80), "white").save(root / "images" / "a.jpg")
            Image.new("RGB", (100, 80), "white").save(root / "images" / "b.jpg")
            events = [
                {
                    "visual": {
                        "full_screenshot": "images/a.jpg",
                        "frames": {"before_near": "images/a.jpg"},
                        "bbox": {"x": 1, "y": 1, "w": 30, "h": 20},
                        "viewport": "100x80",
                    }
                },
                {
                    "visual": {
                        "full_screenshot": "images/b.jpg",
                        "frames": {"before_near": "images/b.jpg"},
                        "bbox": {"x": 5, "y": 5, "w": 25, "h": 15},
                        "viewport": "100x80",
                    }
                },
            ]

            def _fake_call_llm(task, payload, *args, **kwargs):
                user_text = payload.get("user_text") or ""
                if "enter_email" in user_text or "images/a" in str(payload):
                    return {"chosen_frame": "before_near", "anchor_sentence": "email field"}
                return {"chosen_frame": "before_near", "anchor_sentence": "password field"}

            with (
                patch.object(settings, "data_dir", root),
                patch("conxa_core.llm._router", _FakeRouter()),
                patch("conxa_compile.llm.anchor_vision_llm.supports_multimodal_chat", return_value=True),
                patch(
                    "conxa_compile.llm.anchor_vision_llm.call_llm",
                    side_effect=_fake_call_llm,
                ) as call,
            ):
                prefetch_vision_anchors_parallel(
                    [(0, events[0], "enter_email"), (1, events[1], "enter_password")],
                    session_root=root,
                    policy=get_policy_bundle().data,
                )
                self.assertEqual(call.call_args.args[0], "anchor_vision_frameset")

                # Both steps' anchors must now be cache hits — no further call_llm.
                with patch(
                    "conxa_compile.llm.anchor_vision_llm.call_llm",
                    side_effect=AssertionError("should be a cache hit after prefetch"),
                ):
                    result_a = generate_anchors_for_step_or_raise(
                        events[0], session_root=root, final_intent="enter_email",
                        policy=get_policy_bundle().data, step_index=0,
                    )
                    result_b = generate_anchors_for_step_or_raise(
                        events[1], session_root=root, final_intent="enter_password",
                        policy=get_policy_bundle().data, step_index=1,
                    )
                self.assertEqual(result_a["anchor_sentence"], "email field")
                self.assertEqual(result_b["anchor_sentence"], "password field")

    def test_prefetch_vision_anchors_parallel_requeues_only_rate_limited_steps(self) -> None:
        """A step whose call raises ProxyUnavailable gets requeued into the next
        wave and retried; a step that succeeds on the first wave is never called
        again."""
        from services.llm_proxy_client import ProxyUnavailable
        from conxa_compile.llm.anchor_vision_llm import prefetch_vision_anchors_parallel
        from conxa_compile.policy.bundle import get_policy_bundle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            Image.new("RGB", (100, 80), "white").save(root / "images" / "a.jpg")
            Image.new("RGB", (100, 80), "white").save(root / "images" / "b.jpg")
            events = [
                {"visual": {"full_screenshot": "images/a.jpg", "frames": {"before_near": "images/a.jpg"}, "bbox": {"x": 1, "y": 1, "w": 30, "h": 20}, "viewport": "100x80"}},
                {"visual": {"full_screenshot": "images/b.jpg", "frames": {"before_near": "images/b.jpg"}, "bbox": {"x": 5, "y": 5, "w": 25, "h": 15}, "viewport": "100x80"}},
            ]
            call_counts = {"a": 0, "b": 0}

            def _fake_call_llm(task, payload, *args, **kwargs):
                key = "a" if "enter_email" in str(payload) else "b"
                call_counts[key] += 1
                if key == "b" and call_counts["b"] == 1:
                    raise ProxyUnavailable("pool exhausted")
                return {"chosen_frame": "before_near", "anchor_sentence": f"field {key}"}

            with (
                patch.object(settings, "data_dir", root),
                patch.object(settings, "llm_anchor_vision_max_concurrent_steps", 2),
                patch("conxa_compile.llm.anchor_vision_llm.time.sleep"),
                patch("conxa_core.llm._router", _FakeRouter()),
                patch("conxa_compile.llm.anchor_vision_llm.supports_multimodal_chat", return_value=True),
                patch("conxa_compile.llm.anchor_vision_llm.call_llm", side_effect=_fake_call_llm),
            ):
                prefetch_vision_anchors_parallel(
                    [(0, events[0], "enter_email"), (1, events[1], "enter_password")],
                    session_root=root,
                    policy=get_policy_bundle().data,
                )

        self.assertEqual(call_counts["a"], 1)  # never requeued
        self.assertEqual(call_counts["b"], 2)  # requeued once after ProxyUnavailable

    def test_prefetch_vision_anchors_parallel_never_raises_on_llm_failure(self) -> None:
        """Prefetch is pure optimization — if every call fails entirely, the
        per-step path must still be able to run its own (unmocked-away) call."""
        from conxa_compile.llm.anchor_vision_llm import prefetch_vision_anchors_parallel
        from conxa_compile.policy.bundle import get_policy_bundle

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "images").mkdir()
            Image.new("RGB", (100, 80), "white").save(root / "images" / "a.jpg")
            ev = {
                "visual": {
                    "full_screenshot": "images/a.jpg",
                    "frames": {"before_near": "images/a.jpg"},
                    "bbox": {"x": 1, "y": 1, "w": 30, "h": 20},
                    "viewport": "100x80",
                }
            }
            with (
                patch.object(settings, "data_dir", root),
                patch("conxa_core.llm._router", _FakeRouter()),
                patch("conxa_compile.llm.anchor_vision_llm.supports_multimodal_chat", return_value=True),
                patch(
                    "conxa_compile.llm.anchor_vision_llm.call_llm",
                    side_effect=RuntimeError("provider pool exhausted"),
                ),
            ):
                # Must not raise.
                prefetch_vision_anchors_parallel(
                    [(0, ev, "enter_email")], session_root=root, policy=get_policy_bundle().data,
                )

if __name__ == "__main__":
    unittest.main()


class CompileWithoutFramesTests(unittest.TestCase):
    def test_compile_makes_no_vision_call_and_needs_no_screenshot(self) -> None:
        """Understanding-first pipeline: the compile cuts no frames and asks no model to look at the
        page. Anchors come from the recorded DOM; a recording with no screenshots compiles normally."""
        from conxa_compile.compiler.build import compile_skill_package
        from conxa_compile.pipeline.run import run_pipeline

        evs = run_pipeline([_minimal_click_event()])
        data_dir = Path(tempfile.mkdtemp())
        try:
            with patch.object(settings, "data_dir", data_dir), \
                    patch("conxa_core.llm._router", _FakeRouter()), \
                    patch("conxa_compile.llm.anchor_vision_llm.call_llm",
                          side_effect=AssertionError("no vision call may be made")) as vision, \
                    patch("conxa_compile.llm.workflow_review.infer_workflow_review", return_value=None):
                pkg = compile_skill_package(evs, skill_id="nf", source_session_id="sess", title="t", version=1)
            vision.assert_not_called()
            step = pkg.skills[0].steps[1]
            self.assertTrue(step.source_event is None or isinstance(step.source_event, int))
            visual = step.signals.get("visual") or {}
            self.assertEqual(set(visual), {"bbox", "viewport", "scroll_position"})      # no screenshot paths, no frames
            self.assertNotIn("vision_anchor_fallback", (step.confidence_protocol.get("compile_warnings") or {}))
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)


def test_an_anchor_the_recorder_flagged_ephemeral_never_reaches_the_compiled_step():
    from conxa_compile.compiler.step_anchors import clean_anchors

    out = clean_anchors(
        [{"element": "region", "relation": "above", "ephemeral": True},
         {"element": "heading", "relation": "above"}],
        {}, None,
    )
    assert [a["element"] for a in out] == ["heading"]
