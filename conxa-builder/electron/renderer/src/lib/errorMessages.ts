/**
 * Plain-English copy for backend error codes.
 *
 * The Python backend raises `_CommandError(code, message)`; the `code` is a
 * stable machine string (e.g. `cloud_unreachable`) while `message` is sometimes
 * a raw exception string that is unhelpful to end users. This map lets the UI
 * render a friendly, actionable sentence keyed on the code, and fall back to the
 * backend message only when a code is unmapped.
 *
 * Keep this in sync with the `_CommandError(...)` call sites in
 * `conxa-builder/python/backend.py`, the entitlement copy in
 * `backend.py:_entitlement_error_message`, and the patch-validation codes raised as
 * `ValueError`s from `conxa_compile/editor/patch_gate.py` (surfaced by `cmd_patch_step` as
 * `_CommandError(str(exc), "Patch rejected: <exc>")` — the code IS the raw ValueError string).
 * Codes with a dynamic index in their name (e.g. `frame_chain_0_selector_required`,
 * `branch_option_2_must_be_object`) aren't enumerated here — those UI surfaces (frame chain
 * editing, wait_for_one_of options) have no editor yet, so the raw code reaching a toast isn't
 * reachable in practice today. Add a mapping if/when that gap is closed.
 */
// Why "Finish and save" is greyed out before anything was recorded.
export const NEEDS_ACTION_HINT = 'Click or type something on the page first. A workflow needs at least one action.'

export const errorMessages: Record<string, string> = {
  // Cloud / connectivity
  cloud_unreachable:
    "Can't reach Conxa Cloud right now. Check your internet connection and try again.",
  cloud_auth_required: "Your sign-in has expired. Sign in to Conxa Build Studio and try again.",
  // Distinct from cloud_unreachable on purpose: the cloud answered, it just couldn't get an
  // answer out of any AI provider. Sending the reviewer to check a working network wastes
  // their time on the one failure a retry usually does fix.
  llm_no_output:
    "The AI model didn't return an answer this time. Try asking again.",
  cloud_publish_failed:
    "Publishing to Conxa Cloud didn't complete. Please try publishing again.",
  installer_upload_failed:
    "The installer couldn't be uploaded to the cloud. Please try again.",
  entitlements_unavailable:
    "Conxa Cloud can't confirm your plan right now, so this action is paused. Try again in a moment.",

  // Plan limits / quota
  quota_exceeded:
    "You've reached your monthly usage limit for this workspace. Upgrade your plan or wait for the next cycle.",
  compile_credit_limit_exceeded:
    "You've used all of this month's compile credits. Upgrade your plan or wait for the next cycle.",
  human_edit_pool_exceeded:
    "You've used all of this month's AI Usage Credits. Upgrade your plan or wait for the next cycle.",
  seat_limit_exceeded:
    "Your workspace is at its seat limit. Upgrade your plan to add more members.",
  machine_limit_exceeded:
    "This workspace's plan allows fewer build machines than are currently registered. Revoke an old machine in Settings, or upgrade.",
  execute_seat_limit_exceeded:
    "Your workspace is at its Conxa Execute seat limit. Upgrade your plan or revoke an existing grant to invite someone new.",
  trial_expired:
    "Your 30-day free trial has ended. Upgrade to keep building and publishing.",
  distribution_not_permitted:
    "This plan can only build installers for your own team. Upgrade to Pro to distribute to customers.",
  white_label_not_permitted:
    "White-label installer branding requires the Enterprise plan.",
  invalid_usage_class: "Something went wrong charging this action. Please try again.",

  // Recording / auth
  recording_in_progress:
    "A recording is already running. Finish or stop it before starting another.",
  recorder_launch_failed:
    "The recorder browser couldn't start. Close any leftover browser windows and try again.",
  empty_recording: "No actions were recorded. Record at least one step before continuing.",
  no_events: "This session has no recorded actions to work with.",
  // auth_required is deliberately NOT mapped here — the backend's own message (from
  // handlers/session.py's pre-flight gate) already names the specific app(s) that need
  // authentication ("Authenticate Salesforce before recording." / "Re-authenticate Salesforce
  // before recording — the saved session has expired."), which is strictly more useful than any
  // generic copy this map could substitute. Falls through to errorMessage()'s second branch
  // (the raw backend message) by design. The Record action's error handler also opens
  // GroupAuthWizard scoped to the named app(s) — see RecordWorkflowDialog.tsx.
  auth_capture_failed:
    "The sign-in window closed before it could be saved. Please record auth again.",
  auth_file_in_build_input:
    "Sign-in credentials were found in the build folder and can't be shipped. Remove them and rebuild.",

  // Release Center (publish / rollback) — conxa-cloud/backend/app/api/publish_routes.py
  // and app/api/release_routes.py
  skill_pack_version_exists:
    "This version was already published and can never be overwritten. Bump the version and publish again.",
  skill_pack_artifact_unchanged:
    "Nothing has changed since the current stable release — this exact skill pack is already published.",
  release_not_found: "That release no longer exists.",
  release_not_published: "That release never finished publishing, so it can't be rolled back to.",
  already_stable: "That release is already the stable channel — there's nothing to roll back.",
  release_snapshot_unavailable:
    "This release predates version history and has no saved snapshot, so it can't be rolled back to.",
  slug_owned_by_another_workspace:
    "This skill pack's slug is already published by a different Conxa workspace.",
  "admin role required":
    "Publishing and rollback require an admin or owner role in this Conxa workspace.",

  // Build / compile / test
  pack_not_built: "Build this skill pack before continuing.",
  skill_package_not_built: "Build the skill package before testing its workflows.",
  not_built: "The skill package hasn't been built yet.",
  workflow_not_compiled: "Compile this workflow before testing it.",
  workflow_test_failed: "The workflow test didn't pass. Review the steps and try again.",
  skill_pack_not_found: "No skill package has been built for this workspace yet.",
  invalid_document: "This skill file is missing its skills and can't be opened.",
  invalid_bbox: "That selection isn't a valid region. Try drawing the box again.",
  invalid_selector:
    "One of the recorded elements can't be targeted reliably. Re-record that step.",
  vision_anchors_failed:
    "Couldn't re-derive this step's recovery anchors right now (vision LLM/cloud proxy issue). Try again in a moment.",
  session_artifacts_missing:
    "The original recording for this step is no longer available, so we can't re-find the element. You can still reposition it without changing the selector.",
  bbox_too_small: "Draw a larger region around the element — the selection is too small to identify.",
  primary_selector_required: "Choose one of the generated selectors (or enter one manually) before applying.",

  // Input / validation
  invalid_input: "Some required information is missing. Fill in the highlighted fields.",
  invalid_release_version:
    "The installer version must look like 1.2.3 (optionally 1.2.3-beta.1).",
  invalid_release_notes: "A release message is required (up to 2000 characters).",
  invalid_frame_label: "A frame label is required for this step.",

  // Step editor — patch validation (patch_gate.py)
  intent_empty: "Describe what this step does before saving.",
  semantic_description_empty: "Describe what this step does before saving.",
  invalid_intent_slug: "That description has characters that can't be used. Use plain words.",
  intent_required_for_non_scroll_step: "Describe what this step does before saving.",
  unsupported_action_kind: "This action type isn't editable here.",
  recording_marker_steps_are_read_only: "This step is a recording marker and can't be edited.",
  navigate_step_allows_only_url_intent_validation_recovery:
    "Only the URL, description, outcome checks, and recovery can be edited for a navigate step.",
  navigate_url_must_be_http_url: "Enter a full web address starting with http:// or https://.",
  scroll_step_allows_only_intent_and_action: "Only the description can be edited for a scroll step.",
  scroll_action_patch_required: "Something went wrong saving this scroll step. Please try again.",
  scroll_action_kind_invalid: "Something went wrong saving this scroll step. Please try again.",
  scroll_selector_failed_quality_gates:
    "That scroll target can't be targeted reliably. Choose a different element or scroll by amount instead.",
  scroll_amount_required: "Enter how far this step should scroll.",
  scroll_amount_must_be_integer: "The scroll amount must be a whole number.",
  scroll_amount_out_of_range: "That scroll amount is too large. Use a smaller value.",
  check_step_allows_only_check_fields: "Only the check's own fields can be edited for a check step.",
  wait_step_allows_only_action_intent_validation_recovery:
    "Only the description, outcome checks, and recovery can be edited for a wait step.",
  screenshot_step_allows_only_action_intent_validation_recovery:
    "Only the description, outcome checks, and recovery can be edited for a screenshot step.",
  branch_step_allows_only_target_branch_intent_validation_recovery_frame:
    "Only the target, branch options, description, outcome checks, and recovery can be edited for this step.",
  branch_body_step_cannot_patch_recovery_or_validation:
    "Recovery and outcome checks can't be edited on a step nested inside a branch.",
  branch_must_be_object: "Something went wrong saving this step's branch settings. Please try again.",
  branch_timeout_ms_must_be_integer: "The branch timeout must be a whole number of milliseconds.",
  branch_timeout_ms_out_of_range: "The branch timeout must be between 0 and 60,000 ms.",
  branch_if_present_steps_not_patchable_here:
    "Edit the steps inside this branch from the branch step list, not here.",
  branch_candidates_must_be_array: "Something went wrong saving this branch's options. Please try again.",
  branch_fallback_escape_must_be_boolean: "Something went wrong saving this branch's settings. Please try again.",
  branch_options_must_be_array: "Something went wrong saving this branch's options. Please try again.",
  branch_required_must_be_boolean: "Something went wrong saving this branch's settings. Please try again.",
  primary_selector_failed_quality_gates:
    "The main selector can't be targeted reliably. Choose a different element or selector.",
  fallback_selector_failed_quality_gates:
    "One of the fallback selectors can't be targeted reliably. Edit or remove it.",
  destructive_step_requires_non_none_wait_for:
    "This step performs an action that can't be undone, so it needs an outcome check that confirms it worked. Add one before saving.",
  destructive_step_requires_signals_anchors:
    "This step performs an action that can't be undone and needs recovery anchors. Re-pick the element to generate them.",
  consequential_step_requires_required_assertion:
    "This step's result should be confirmed. Keep at least one required outcome check before saving.",

  // Not found
  not_found: "We couldn't find what you were looking for.",
  workflow_not_found: "That workflow no longer exists.",
  session_not_found: "That recording session no longer exists.",
  skill_not_found: "That skill no longer exists.",
  step_not_found: "That step no longer exists.",
  package_not_found: "That package no longer exists.",
  run_not_found: "That run no longer exists.",

  // Edit history
  nothing_to_undo: "There's nothing to undo.",
  nothing_to_redo: "There's nothing to redo.",

  // Transport / system
  unknown_command: "Something went wrong inside the app. Please try again.",
  internal_error: "Something went wrong inside the app. Please try again.",
  bad_json: "The app received a malformed response. Please try again.",
  backend_not_running:
    "The Build Studio engine isn't running. Restart the app and try again.",
  error: "Something went wrong. Please try again.",
}
