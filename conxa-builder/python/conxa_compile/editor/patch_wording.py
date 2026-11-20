"""Plain-English wording for patch_gate rejections.

patch_gate.py raises ValueError with machine codes (e.g. "intent_empty"). Those codes must
never reach a person, so plain_patch_error() turns one into a short sentence a non-technical
user can act on. The code itself stays the _CommandError.code, so the UI can still branch on it.
Lookup order: exact entry in _WORDING, then the first matching rule in _PATTERNS, then a generic
fallback. Add an entry here whenever patch_gate.py gains a new `raise ValueError("...")`
(tests/test_patch_wording.py enforces it).
"""

from __future__ import annotations

import re

_FALLBACK = "That change doesn't fit this step's rules, so it wasn't saved. Open the step in Human Edit to adjust it."
_BRANCH_RETRY = "Something went wrong saving this branch's options. Please try again."
# One refusal, whichever way a free agent part and a step that can't be undone end up side by side.
SPAN_BEFORE_IRREVERSIBLE = (
    "A step that can't be undone can't come straight after a part the AI agent does on its own. "
    "Put a normal step between them."
)

_WORDING: dict[str, str] = {
    # Safety rules
    "irreversible_step_requires_confirmed_entity_binding": "This step can't be undone, so Conxa needs you to confirm which row it should act on first.",
    "irreversible_step_requires_completion_check": "This step can't be undone, so it needs a check that proves it worked. Add one before saving.",
    "destructive_step_cannot_directly_follow_agent_span": SPAN_BEFORE_IRREVERSIBLE,
    "destructive_step_cannot_be_gated_by_span_answer": "A step that can't be undone can't depend on what an agent-driven part answered. Use a confirmed condition instead.",
    "agent_span_cannot_directly_precede_a_destructive_step": SPAN_BEFORE_IRREVERSIBLE,
    "confirmation_guards_a_destructive_step": "This confirmation protects a step that can't be undone, so it can't be changed or removed here.",
    "effect_is_not_a_valid_checklist": "That outcome isn't a valid check for this step. Pick one of the suggested checks instead.",
    "destructive_step_requires_non_none_wait_for": "This step performs an action that can't be undone, so it needs an outcome check that confirms it worked. Add one before saving.",
    "destructive_step_requires_signals_anchors": "This step performs an action that can't be undone and needs recovery anchors. Re-pick the element to generate them.",
    "consequential_step_requires_required_assertion": "This step's result should be confirmed. Keep at least one required outcome check before saving.",
    # Step description / kind
    "intent_empty": "Describe what this step does before saving.",
    "semantic_description_empty": "Describe what this step does before saving.",
    "intent_required_for_non_scroll_step": "Describe what this step does before saving.",
    "invalid_intent_slug": "That description has characters that can't be used. Use plain words.",
    "unsupported_action_kind": "This action type isn't editable here.",
    "recording_marker_steps_are_read_only": "This step is a recording marker and can't be edited.",
    "outcome_fork_step_allows_only_intent_and_description": "Only the description can be edited for a decision step.",
    # Navigate / scroll / check
    "navigate_step_allows_only_url_intent_validation_recovery": "Only the URL, description, outcome checks, and recovery can be edited for a navigate step.",
    "navigate_url_must_be_http_url": "Enter a full web address starting with http:// or https://.",
    "scroll_step_allows_only_intent_and_action": "Only the description can be edited for a scroll step.",
    "scroll_action_patch_required": "Something went wrong saving this scroll step. Please try again.",
    "scroll_action_kind_invalid": "Something went wrong saving this scroll step. Please try again.",
    "scroll_selector_failed_quality_gates": "That scroll target can't be targeted reliably. Choose a different element or scroll by amount instead.",
    "scroll_amount_required": "Enter how far this step should scroll.",
    "scroll_amount_must_be_integer": "The scroll amount must be a whole number.",
    "scroll_amount_out_of_range": "That scroll amount is too large. Use a smaller value.",
    "check_step_allows_only_check_fields": "Only the check's own fields can be edited for a check step.",
    "clipboard_read_name_invalid": "Give the copied value a short name using letters, numbers and underscores only.",
    # Selectors / frames
    "primary_selector_failed_quality_gates": "The main selector can't be targeted reliably. Choose a different element or selector.",
    "fallback_selector_failed_quality_gates": "One of the fallback selectors can't be targeted reliably. Edit or remove it.",
    "frame_must_be_object": "Something went wrong saving this step's frame settings. Please try again.",
    "frame_chain_must_be_array": "Something went wrong saving this step's frame settings. Please try again.",
    # Branches
    "branch_step_allows_only_target_branch_intent_validation_recovery_frame": "Only the target, branch options, description, outcome checks, and recovery can be edited for this step.",
    "branch_body_step_cannot_patch_recovery_or_validation": "Recovery and outcome checks can't be edited on a step nested inside a branch.",
    "branch_must_be_object": "Something went wrong saving this step's branch settings. Please try again.",
    "branch_timeout_ms_must_be_integer": "The branch timeout must be a whole number of milliseconds.",
    "branch_timeout_ms_out_of_range": "The branch timeout must be between 0 and 60,000 ms.",
    "branch_if_present_steps_not_patchable_here": "Edit the steps inside this branch from the branch step list, not here.",
    "branch_candidates_must_be_array": "Something went wrong saving this branch's options. Please try again.",
    "branch_fallback_escape_must_be_boolean": "Something went wrong saving this branch's settings. Please try again.",
    "branch_options_must_be_array": "Something went wrong saving this branch's options. Please try again.",
    "branch_required_must_be_boolean": "Something went wrong saving this branch's settings. Please try again.",
    # Agent-driven part
    "agent_span_step_allows_only_agent_span_fields": "Only the agent part's own settings can be edited for this step.",
    "agent_span_step_cannot_patch_recovery": "Recovery can't be edited on an agent-driven step.",
    "agent_span_goal_empty": "Describe what the agent should achieve before saving.",
    "agent_span_needs_actions_or_answer": "Tell the agent which actions it may take or what answer it should give back.",
    "agent_span_allowed_invalid": "The list of actions the agent may take isn't valid. Open this step in Human Edit and fix it.",
    "agent_span_answer_schema_must_be_object": "The answer the agent should give back isn't set up correctly. Open this step in Human Edit and fix it.",
    "agent_span_attach_invalid": "What the agent is shown isn't set up correctly. Open this step in Human Edit and fix it.",
    "agent_span_budget_invalid": "The agent's limit must be a whole number within the allowed range.",
    "agent_span_on_budget_invalid": "Choose what happens when the agent reaches its limit from the list.",
    "agent_span_on_failure_invalid": "Choose what happens when the agent fails from the list.",
    "agent_span_output_name_invalid": "Give the agent's answer a short name using letters, numbers and underscores only.",
    "agent_span_until_invalid": "The agent's stopping condition isn't set up correctly. Open this step in Human Edit and fix it.",
    "agent_span_until_target_invalid": "The agent's stopping condition points at something that can't be used. Pick a different element.",
    "agent_span_until_target_not_allowed": "That kind of stopping condition can't point at an element. Pick a different condition.",
    "agent_span_until_type_invalid": "Choose the agent's stopping condition from the list.",
    "agent_span_use_default_requires_default_value": "To use a default answer, enter the default value first.",
    # Call another workflow
    "call_workflow_step_allows_only_call_fields": "Only the call's own settings can be edited for this step.",
    "call_workflow_step_cannot_patch_recovery": "Recovery can't be edited on a step that runs another workflow.",
    "call_workflow_inputs_invalid": "The values passed to the other workflow aren't set up correctly. Open this step in Human Edit and fix them.",
    "call_workflow_outputs_invalid": "The values taken back from the other workflow aren't set up correctly. Open this step in Human Edit and fix them.",
    "call_workflow_value_invalid": "One of the values passed to the other workflow isn't valid. Open this step in Human Edit and fix it.",
    # Extract
    "extract_step_allows_only_target_output_name_and_extract_fields": "Only the target, the value's name and the read settings can be edited for this step.",
    "extract_must_be_object": "Something went wrong saving this step's read settings. Please try again.",
    "extract_attribute_required": "Say which part of the element to read the value from.",
    "extract_output_name_invalid": "Give the value a short name using letters, numbers and underscores only.",
    "extract_pattern_invalid": "The pattern used to pick out the value isn't valid. Simplify it or remove it.",
    "extract_pattern_too_long": "The pattern used to pick out the value is too long. Shorten it.",
    "extract_source_invalid": "Choose where to read the value from using the list.",
    # Repeat for each row
    "for_each_must_be_object": "Something went wrong saving this step's repeat settings. Please try again.",
    "for_each_rows_must_be_object": "Something went wrong saving this step's rows settings. Please try again.",
    "for_each_as_must_be_non_empty_string": "Give each repeated item a name before saving.",
    "for_each_items_must_be_valid_input_id": "Choose which list of inputs to repeat over from the list.",
    "for_each_columns_must_be_unique_names": "A table loop needs one to twelve column names, each different, starting with a letter.",
    "for_each_table_needs_columns": "A loop over a table needs the names of its columns.",
    "for_each_max_iterations_must_be_integer": "The maximum number of repeats must be a whole number.",
    "for_each_max_iterations_out_of_range": "That number of repeats is outside the allowed range. Use a different value.",
    "for_each_on_row_error_invalid": "Choose what happens when one row fails from the list.",
    "for_each_requires_exactly_one_row_source": "Pick exactly one source for the rows to repeat over.",
    "for_each_rows_container_selector_failed_quality_gates": "The area holding the rows can't be targeted reliably. Choose a different element.",
    "for_each_step_allows_only_intent_for_each_frame": "Only the description, repeat settings and frame can be edited for this step.",
    "for_each_steps_not_patchable_here": "Edit the steps inside this repeat from its step list, not here.",
    # Hand over to a person
    "handover_step_allows_only_handover_fields": "Only the hand-over message and settings can be edited for this step.",
    "handover_step_cannot_patch_recovery": "Recovery can't be edited on a hand-over step.",
    "handover_message_empty": "Write the message the person will see when Conxa hands over.",
    "handover_on_failure_invalid": "Choose what happens if the hand-over isn't completed from the list.",
    "handover_resume_when_must_be_object": "Something went wrong saving when this step should continue. Please try again.",
    "handover_resume_when_timeout_ms_invalid": "The wait time before continuing must be a whole number of milliseconds.",
    # Wait until
    "wait_until_step_allows_only_wait_until_fields": "Only the wait's own settings can be edited for this step.",
    "wait_until_step_cannot_patch_recovery": "Recovery can't be edited on a step that waits for something.",
    "wait_until_kind_invalid": "Choose what to wait for from the list.",
    "wait_until_load_state_invalid": "Choose which page-loading state to wait for from the list.",
    "wait_until_on_timeout_invalid": "Choose what happens when the wait runs out from the list.",
    "wait_until_timeout_ms_invalid": "The wait time must be a whole number of milliseconds within the allowed range.",
    "wait_until_value_empty": "Enter what this step should wait for.",
    # Conditions
    "run_if_must_be_object": "Something went wrong saving this step's condition. Please try again.",
    "run_if_input_invalid": "Choose which input the condition depends on from the list.",
    "run_if_op_invalid": "Choose how the condition compares the input from the list.",
    "run_if_value_required": "Enter the value the condition should compare against.",
    "on_missing_invalid": "Choose what happens when the element is missing from the list.",
    "on_missing_needs_a_bound_record": "Skipping a missing element only works for a step tied to a specific row. Tie it to a row first.",
}

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"frame_chain_\d+_.*"), "One of this step's frame settings isn't valid. Open this step in Human Edit and re-pick the element."),
    (re.compile(r"branch_option_\d+_selector.*"), "One of this branch's options points at an element that can't be targeted reliably. Choose a different element."),
    (re.compile(r"branch_option_\d+_.*"), _BRANCH_RETRY),
    (re.compile(r"branch_candidate_\d+_.*"), "One of this branch's options can't be targeted reliably. Choose a different element."),
    (re.compile(r"invalid_placeholder_syntax:.*", re.S), "A {{placeholder}} in this value isn't written correctly. Use letters, numbers and underscores only, like {{name}}."),
    (re.compile(r"[a-z]+_step_cannot_patch_recovery"), "Recovery can't be edited on this kind of step."),
    (re.compile(r"[a-z]+_step_allows_only_.*"), "Only the description, outcome checks, and recovery can be edited for this kind of step."),
    (re.compile(r"[a-z]+_value_empty"), "Enter a value before saving."),
    (re.compile(r"agent_span_.*"), "The agent part of this step isn't set up correctly. Open this step in Human Edit and fix it."),
    (re.compile(r"for_each_.*"), "This step's repeat settings aren't valid. Open this step in Human Edit and fix them."),
    (re.compile(r"handover_.*"), "This step's hand-over settings aren't valid. Open this step in Human Edit and fix them."),
    (re.compile(r"wait_until_.*"), "This step's wait settings aren't valid. Open this step in Human Edit and fix them."),
    (re.compile(r"extract_.*"), "This step's read settings aren't valid. Open this step in Human Edit and fix them."),
    (re.compile(r"call_workflow_.*"), "This step's settings for running another workflow aren't valid. Open this step in Human Edit and fix them."),
    (re.compile(r"run_if_.*"), "This step's condition isn't valid. Open this step in Human Edit and fix it."),
    (re.compile(r"on_missing_.*"), "This step's missing-element setting isn't valid. Open this step in Human Edit and fix it."),
    (re.compile(r"branch_.*"), _BRANCH_RETRY),
]


def plain_patch_error(code: str) -> str:
    """Return a plain sentence for a patch_gate rejection code; never the code itself."""
    code = (code or "").strip()
    if code in _WORDING:
        return _WORDING[code]
    for rx, text in _PATTERNS:
        if rx.fullmatch(code):
            return text
    return _FALLBACK
