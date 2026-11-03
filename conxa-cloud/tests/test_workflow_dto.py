"""step_to_dto: the editor DTO must surface validation.assertions to the Human Edit UI."""

from __future__ import annotations

from conxa_compile.editor.workflow_dto import build_workflow_response, step_to_dto


def _fill_step() -> dict:
    return {
        "action": {"action": "fill", "value": "alice@example.com"},
        "intent": "Enter email address",
        "value": "alice@example.com",
        "target": {"primary_selector": "#email", "fallback_selectors": []},
        "signals": {"selectors": {}, "semantic": {}},
        "validation": {
            "wait_for": {"type": "none"},
            "success_conditions": {},
            "assertions": [
                {"type": "value_equals", "target": "#email", "expected": "alice@example.com", "required": True},
            ],
        },
    }


def test_step_to_dto_surfaces_assertions():
    dto = step_to_dto("skill_1", _fill_step(), 0, {}, "")
    assert dto.validation["wait_for"] == {"type": "none"}
    assert dto.validation["assertions"] == [
        {"type": "value_equals", "target": "#email", "expected": "alice@example.com", "required": True},
    ]


def test_step_to_dto_assertions_default_to_empty_list():
    step = _fill_step()
    del step["validation"]["assertions"]
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.validation["assertions"] == []


def test_step_to_dto_drops_non_dict_assertion_entries():
    step = _fill_step()
    step["validation"]["assertions"].append("not-a-dict")  # malformed data shouldn't reach the UI
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert len(dto.validation["assertions"]) == 1


def test_step_to_dto_surfaces_identity_signal_metadata_and_confidence():
    """identity_engines must carry orthogonality_class/unique_at_compile/source (not just
    selector/engine/durability), and compile_confidence must be derived from the bundle when
    nothing is already persisted at target.selector_confidence."""
    step = _fill_step()
    step["identity_bundle"] = {
        "signals": [
            {
                "engine": "testid",
                "selector": "[data-testid=\"email\"]",
                "durability": 0.99,
                "orthogonality_class": "test-contract",
                "unique_at_compile": True,
                "source": "compiler",
            },
            {
                "engine": "css-id",
                "selector": "#email",
                "durability": 0.45,
                "orthogonality_class": "structural",
                "unique_at_compile": True,
                "source": "compiler",
            },
        ]
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.identity_engines[0] == {
        "selector": "[data-testid=\"email\"]",
        "engine": "testid",
        "durability": 0.99,
        "orthogonality_class": "test-contract",
        "unique_at_compile": True,
        "source": "compiler",
    }
    # Two orthogonality classes represented + a unique-at-compile signal -> no discount factors.
    assert dto.compile_confidence == 0.99


def test_step_to_dto_prefers_persisted_selector_confidence_over_bundle_derivation():
    step = _fill_step()
    step["identity_bundle"] = {
        "signals": [
            {"engine": "testid", "selector": "[data-testid=\"email\"]", "durability": 0.99,
             "orthogonality_class": "test-contract", "unique_at_compile": True, "source": "compiler"},
        ]
    }
    step["target"]["selector_confidence"] = 0.42
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.compile_confidence == 0.42


def test_step_to_dto_compile_confidence_none_without_identity_bundle():
    dto = step_to_dto("skill_1", _fill_step(), 0, {}, "")
    assert dto.compile_confidence is None


def test_step_to_dto_scroll_step_confidence_always_none():
    """A scroll step has no element to select — even a stray persisted selector_confidence
    (e.g. left over from before the step was edited into a scroll step) must not render."""
    step = _fill_step()
    step["action"] = {"action": "scroll"}
    step["target"]["selector_confidence"] = 0.0
    step["identity_bundle"] = {
        "signals": [
            {"engine": "testid", "selector": "[data-testid=\"email\"]", "durability": 0.99,
             "orthogonality_class": "test-contract", "unique_at_compile": True, "source": "compiler"},
        ]
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.compile_confidence is None


# ─────────────────────────────────────────────────
# 2026-07-10 Human Edit vs. Skill Package redesign — read-only visibility additions.
# ─────────────────────────────────────────────────

def test_step_to_dto_recovery_view_projects_recovery_block():
    step = _fill_step()
    step["recovery"] = {
        "strategies": ["semantic match"],
        "max_attempts": 2,
        "confidence_threshold": 0.85,
        "require_diverse_attempts": True,
        "anchors": [{"kind": "near", "value": "email"}],
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.recovery_view["strategies"] == ["semantic match"]
    assert dto.recovery_view["max_attempts"] == 2
    assert dto.recovery_view["confidence_threshold"] == 0.85


def test_step_to_dto_recovery_view_empty_for_marker_step():
    step = {"action": {"action": "frame_enter"}, "target": {}, "signals": {}, "recovery": {"max_attempts": 2}}
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.recovery_view == {}


def test_step_to_dto_fingerprint_view_projects_identity_bundle_fingerprint():
    step = _fill_step()
    step["identity_bundle"] = {
        "fingerprint": {"role": "textbox", "inner_text": "", "data_testid": "email-input"},
        "destructive": False,
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.fingerprint["role"] == "textbox"
    assert dto.fingerprint["data_testid"] == "email-input"
    assert dto.fingerprint["destructive"] is False


def test_step_to_dto_fingerprint_view_includes_diagnostics_only_hashes():
    step = _fill_step()
    step["identity_bundle"] = {
        "fingerprint": {"role": "textbox"},
        "stable_hash": "abc123",
        "compat_fingerprint": "def456",
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.fingerprint["stable_hash"] == "abc123"
    assert dto.fingerprint["compat_fingerprint"] == "def456"


def test_step_to_dto_fingerprint_view_empty_without_identity_bundle():
    dto = step_to_dto("skill_1", _fill_step(), 0, {}, "")
    assert dto.fingerprint == {}


def test_step_to_dto_safety_view_reflects_handler_hints_allow_forced_action():
    step = _fill_step()
    step["handler_hints"] = {"allow_forced_action": True, "hover_chain": [{"selector": ".menu"}]}
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.safety["allow_forced_action"] is True
    assert dto.safety["has_hover_chain"] is True
    assert dto.handler_hints_view["hover_chain_len"] == 1


def test_step_to_dto_safety_view_defaults_false_without_handler_hints():
    dto = step_to_dto("skill_1", _fill_step(), 0, {}, "")
    assert dto.safety == {"destructive": False, "allow_forced_action": False, "has_hover_chain": False}


def test_step_to_dto_branch_summary_none_for_ordinary_step():
    dto = step_to_dto("skill_1", _fill_step(), 0, {}, "")
    assert dto.branch_summary is None
    assert dto.branch_steps == []


def test_step_to_dto_if_present_projects_nested_branch_steps_with_path_addressed_ids():
    step = {
        "action": {"action": "if_present"},
        "intent": "dismiss_if_present",
        "target": {"primary_selector": ".cookie-banner", "fallback_selectors": []},
        "signals": {},
        "branch": {
            "timeout_ms": 1500,
            "steps": [
                {"action": {"action": "click"}, "intent": "click_accept", "target": {"primary_selector": "#accept"}},
            ],
        },
    }
    dto = step_to_dto("skill_1", step, 3, {}, "")
    assert dto.branch_summary == {"kind": "if_present", "probe": ".cookie-banner", "step_count": 1}
    assert len(dto.branch_steps) == 1
    assert dto.branch_steps[0].id == "skill_1:3.branch.steps[0]"
    assert dto.branch_steps[0].intent == "click_accept"


def test_step_to_dto_try_dismiss_summary_includes_candidates():
    step = {
        "action": {"action": "try_dismiss"},
        "intent": "try_dismiss_interstitial",
        "target": {"primary_selector": "", "fallback_selectors": []},
        "signals": {},
        "branch": {"candidates": ["#accept-cookies", ".cookie .accept"], "timeout_ms": 800},
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.branch_summary["kind"] == "try_dismiss"
    assert dto.branch_summary["step_count"] == 0
    assert dto.branch_summary["candidates"] == ["#accept-cookies", ".cookie .accept"]
    assert dto.branch_steps == []


def test_step_to_dto_wait_for_one_of_summary_includes_options():
    step = {
        "action": {"action": "wait_for_one_of"},
        "intent": "wait_for_one_of_states",
        "target": {"primary_selector": "", "fallback_selectors": []},
        "signals": {},
        "branch": {
            "options": [
                {"selector": "#mfa-code", "steps": [{"action": {"action": "fill"}}]},
                {"selector": "#dashboard"},
            ],
            "timeout_ms": 8000,
        },
    }
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.branch_summary["kind"] == "wait_for_one_of"
    assert dto.branch_summary["options"] == [
        {"selector": "#mfa-code", "step_count": 1},
        {"selector": "#dashboard", "step_count": 0},
    ]
    assert dto.branch_summary["step_count"] == 1
    # Per-option nested bodies are read-only this pass — branch_steps stays empty.
    assert dto.branch_steps == []


# ─────────────────────────────────────────────────
# recording-next-steps.md Priority 2 — conditional-state observation (optional_hint).
# ─────────────────────────────────────────────────

def test_step_to_dto_surfaces_optional_hint_when_flagged():
    step = _fill_step()
    step["action"] = {"action": "click"}
    step["optional_hint"] = {"kind": "try_dismiss", "container_signal": '[role="dialog"]'}
    dto = step_to_dto("skill_1", step, 0, {}, "")
    assert dto.optional_hint == {"kind": "try_dismiss", "container_signal": '[role="dialog"]'}


def test_step_to_dto_optional_hint_none_for_ordinary_step():
    dto = step_to_dto("skill_1", _fill_step(), 0, {}, "")
    assert dto.optional_hint is None


def _document_with_intent_graph_and_compile_report() -> dict:
    return {
        "meta": {
            "version": 1,
            "required_runtime": ">=1.0.3",
            "compiler_policy_version": "v3",
            "structural_fingerprint": {"h": "abc"},
        },
        "inputs": [],
        "intent_graph": {
            "goal": "Dismiss the cookie banner then submit the form",
            "steps": [{"index": 0, "intent": "dismiss_if_present", "verification_anchor": "banner gone"}],
            "decision_points": [],
            "expected_end_state": {"url_contains": "/done"},
        },
        "compile_report": {
            "status": "review_needed",
            "steps_total": 2,
            "min_confidence": 0.62,
            "steps_with_warnings": 1,
            "llm_router_stats": {"provider": "groq"},
            "steps": [
                {"index": 0, "warnings": []},
                {"index": 1, "warnings": [{"code": "low_selector_confidence", "message": "low"}]},
            ],
        },
        "skills": [{"name": "default", "steps": [_fill_step(), _fill_step()]}],
    }


def test_build_workflow_response_surfaces_intent_graph_verbatim():
    doc = _document_with_intent_graph_and_compile_report()
    wf = build_workflow_response("skill_1", doc, asset_base_url="file:///tmp")
    assert wf.intent_graph["goal"] == "Dismiss the cookie banner then submit the form"
    assert wf.intent_graph["steps"][0]["verification_anchor"] == "banner gone"


def test_build_workflow_response_derives_compile_health_from_report_and_meta():
    doc = _document_with_intent_graph_and_compile_report()
    wf = build_workflow_response("skill_1", doc, asset_base_url="file:///tmp")
    assert wf.compile_health["status"] == "review_needed"
    assert wf.compile_health["min_confidence"] == 0.62
    assert wf.compile_health["steps_below_threshold"] == [1]
    assert wf.compile_health["required_runtime"] == ">=1.0.3"
    assert wf.compile_health["compiler_policy_version"] == "v3"
    assert wf.compile_health["structural_fingerprint_present"] is True
    assert wf.compile_health["llm_router_stats"] == {"provider": "groq"}


def test_build_workflow_response_empty_intent_graph_and_compile_health_when_absent():
    doc = {"meta": {"version": 1}, "inputs": [], "skills": [{"name": "default", "steps": []}]}
    wf = build_workflow_response("skill_1", doc, asset_base_url="file:///tmp")
    assert wf.intent_graph == {}
    assert wf.compile_health == {}


def _document_with_for_each_suggestions(suggestions: list[dict]) -> dict:
    doc = _document_with_intent_graph_and_compile_report()
    doc["compile_report"]["for_each_suggestions"] = suggestions
    return doc


def test_build_workflow_response_surfaces_for_each_suggestions():
    suggestion = {"id": "s1", "upload_step_key": "u1", "why": "generalize?"}
    doc = _document_with_for_each_suggestions([suggestion])
    wf = build_workflow_response("skill_no_edits", doc, asset_base_url="file:///tmp")
    assert wf.compile_health["for_each_suggestions"] == [suggestion]


def test_build_workflow_response_filters_a_previously_rejected_suggestion(tmp_path, monkeypatch):
    # A "Dismiss" click only logs a decision (edit_log.py) — it never edits compile_report on
    # disk — so filtering has to happen at READ time (here) to cover a plain page reload with no
    # recompile in between, not just the next compile.
    from conxa_core.config import settings
    from conxa_compile.editor.edit_log import append_decision

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    skill_id = "skill_rejected_suggestion"
    append_decision(
        skill_id, proposal_id="s1", decision="rejected", command="reject_for_each_suggestion",
        field="for_each_suggestion", step_key="u1",
    )

    kept = {"id": "s2", "upload_step_key": "u2", "why": "generalize?"}
    doc = _document_with_for_each_suggestions([
        {"id": "s1", "upload_step_key": "u1", "why": "generalize?"},
        kept,
    ])
    wf = build_workflow_response(skill_id, doc, asset_base_url="file:///tmp")
    assert wf.compile_health["for_each_suggestions"] == [kept]


def test_human_edit_title_names_the_action_and_never_a_selector():
    step = {"action": {"action": "click"}, "intent": "activate_control_new",
            "target": {"primary_selector": "button.x-9n", "fallback_selectors": []},
            "identity_bundle": {"fingerprint": {"inner_text": "New"}},
            "signals": {"selectors": {}, "semantic": {}}, "validation": {}}
    assert step_to_dto("skill_1", step, 2, {}, "").human_readable_description == "Step 3: Click “New”"
