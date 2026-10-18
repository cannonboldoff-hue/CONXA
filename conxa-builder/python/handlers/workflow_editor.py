"""Workflow-editor mutation command handlers (patch/reorder/undo/redo/sign-off)."""

from __future__ import annotations

from typing import Any

from handlers.protocol import _CommandError, _deep_merge, _event_sink, _safe_id, _skill_response
from conxa_compile.editor.step_path import (
    StepPathError,
    get_nested_step,
    parse_nested_step_path,
    with_nested_step,
)


class WorkflowEditorMixin:
    def _apply_step_patch(
        self,
        step: dict[str, Any],
        patch: dict[str, Any],
        *,
        in_branch_body: bool = False,
        previous_step: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate + deep-merge a patch into a single (possibly nested) step dict, rebuilding
        identity_bundle.signals when the target selector list changed. Shared by cmd_patch_step's
        top-level and path-addressed (branch-body) flows so both go through the same gate.

        `previous_step` (top-level patches only — see validate_editor_patch's docstring) is the
        step immediately before this one, for EXEC-13's destructive-after-ai_review lint."""
        from conxa_compile.compiler.selector_filters import selector_passes_filters
        from conxa_compile.compiler.selector_grammar import (
            compute_merged_display_target,
            rebuild_identity_signals_from_target,
        )
        from conxa_compile.compiler.build import _confidence_from_identity_bundle
        from conxa_compile.compiler.patch import sync_derived_step_fields
        from conxa_compile.editor.patch_gate import validate_editor_patch
        from conxa_compile.policy.bundle import get_policy_bundle

        try:
            validate_editor_patch(step, patch, get_policy_bundle().data, in_branch_body=in_branch_body,
                                   previous_step=previous_step)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Patch rejected: {exc}") from exc

        merged = _deep_merge(dict(step), patch)
        # A plain deep-merge leaves every DERIVED field stale — semantic.final_intent (which
        # wins over top-level intent everywhere it matters), input_binding, the url shadows,
        # and the recovery block. Re-derive them here so the saved step is internally
        # consistent (audit findings C-2/M-1/M-2). Branch-body leaf steps never enter the
        # recovery cascade, so their recovery block is left untouched.
        merged = sync_derived_step_fields(merged, patch, sync_recovery=not in_branch_body)

        if "target" in patch and isinstance(patch.get("target"), dict):
            tgt = merged.get("target") if isinstance(merged.get("target"), dict) else {}
            primary = str(tgt.get("primary_selector") or "").strip()
            if primary and not selector_passes_filters(primary):
                raise _CommandError("invalid_selector", f"primary_selector failed quality gates: {primary!r}")
            fallbacks = [str(fb).strip() for fb in (tgt.get("fallback_selectors") or []) if str(fb).strip()]
            for fb_s in fallbacks:
                if not selector_passes_filters(fb_s):
                    raise _CommandError("invalid_selector", f"fallback_selector failed quality gates: {fb_s!r}")

            # The form round-trips target: {primary_selector, fallback_selectors} on every save
            # (StepConfigForm.tsx), even ones that only touched intent/assertions — and that
            # display list already has the DTO's "recovery extras" (legacy/recovery-only
            # selectors) folded in. Only rebuild identity_bundle.signals — which promotes those
            # extras into the runtime hot path and can reset compile-time uniqueness/provenance
            # — when the saved selector list actually differs from what was displayed (audit H-4).
            displayed_primary, displayed_fallbacks = compute_merged_display_target(step)
            if primary != displayed_primary or fallbacks != displayed_fallbacks:
                # Rebuild identity_bundle.signals from the edited target selector list so the
                # runtime hot path reflects exactly what the editor shows. Editor order = priority.
                new_signals = rebuild_identity_signals_from_target(merged)
                if new_signals:
                    bundle = dict(merged.get("identity_bundle") or {})
                    bundle["signals"] = new_signals
                    merged["identity_bundle"] = bundle
                    try:
                        merged.setdefault("target", {})
                        merged["target"]["selector_confidence"] = _confidence_from_identity_bundle(bundle)
                    except Exception:
                        pass  # Non-critical — confidence will be recomputed on next compile.

        return merged

    def cmd_patch_step(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.compiler.patch import revalidate_step
        from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        patch = dict(payload.get("patch") or {})
        # Optional path addressing a nested body step — "branch.steps[N]" (if_present, EXEC-1) or
        # "for_each.steps[N]" (EXEC-38). Absent for ordinary top-level step patches (unchanged
        # behavior). See conxa_compile/editor/step_path.py.
        path = payload.get("path")
        try:
            nested_path = parse_nested_step_path(path)
        except StepPathError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(doc)
        doc = dict(doc)
        skills = list(doc.get("skills") or [])
        if not skills:
            raise _CommandError("invalid_document", "No skills block")
        block = dict(skills[0])
        steps = list(block.get("steps") or [])
        if step_index < 0 or step_index >= len(steps):
            raise _CommandError("step_not_found", f"Step {step_index} out of range")
        parent_step = dict(steps[step_index])

        if nested_path is not None:
            try:
                nested_step = get_nested_step(parent_step, nested_path)
            except StepPathError as exc:
                raise _CommandError(exc.code, exc.message) from exc
            # A for_each loop body runs through the real recovery cascade (unlike a branch body —
            # see validate_editor_patch's docstring), so it must NOT set in_branch_body=True: that
            # would wrongly reject the Validation phase's own recovery/validation patches.
            merged_nested = self._apply_step_patch(nested_step, patch, in_branch_body=nested_path[0] == "branch")
            try:
                step = with_nested_step(parent_step, nested_path, merged_nested)
            except StepPathError as exc:
                raise _CommandError(exc.code, exc.message) from exc
            revalidation_target = merged_nested
        else:
            previous_step = steps[step_index - 1] if step_index > 0 else None
            step = self._apply_step_patch(parent_step, patch, in_branch_body=False, previous_step=previous_step)
            revalidation_target = step

        steps[step_index] = step
        block["steps"] = steps
        skills[0] = block
        doc["skills"] = skills
        # A value edit can introduce a brand-new {{var}} — declare it so the runtime prompts
        # for it instead of substituting empty, and so a renamed variable's old declaration
        # isn't the only one left (audit finding M-3). Orphans are left for the drawer to flag.
        if "value" in patch:
            doc, _ = reconcile_inputs_with_step_values(doc)
        meta = dict(doc.get("meta") or {})
        meta["version"] = int(meta.get("version", 1)) + 1
        doc["meta"] = meta
        revalidation = revalidate_step(revalidation_target)
        self._push_undo(skill_id, snapshot)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc, revalidation)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_insert_branch_step(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Insert a leaf step into an if_present step's nested body (structural — mirrors
        cmd_insert_step, scoped to `steps[step_index]["branch"]["steps"]`)."""
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import insert_branch_step

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        action_kind = str(payload.get("action_kind") or "click")
        insert_after = payload.get("insert_after")
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        try:
            doc = insert_branch_step(doc, step_index, action_kind, insert_after)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Could not insert branch step: {exc}") from exc
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_delete_branch_step(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import delete_branch_step

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        nested_index = int(payload.get("nested_index") or 0)
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        try:
            doc = delete_branch_step(doc, step_index, nested_index)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Could not delete branch step: {exc}") from exc
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_reorder_branch_steps(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import reorder_branch_steps

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        new_order = list(payload.get("new_order") or [])
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        try:
            doc = reorder_branch_steps(doc, step_index, new_order)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Could not reorder branch steps: {exc}") from exc
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_confirm_optional_interstitial(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Human confirms a recorder-flagged optional interstitial (recording-next-steps.md
        Priority 2) should be treated as one — converts the step into a real try_dismiss branch.
        Structural mutation, same shape as cmd_insert_branch_step: no patch_gate involved, the
        step's action/branch are rewritten wholesale by workflow_mutations.py."""
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import confirm_optional_interstitial

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        keep_as_step = bool(payload.get("keep_as_step"))
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        try:
            doc = confirm_optional_interstitial(doc, step_index, keep_as_step=keep_as_step)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Could not confirm optional interstitial: {exc}") from exc
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_validate_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.json_store import read_skill
        from conxa_compile.editor.workflow_mutations import validate_skill_document

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        return validate_skill_document(doc)

    def cmd_reorder_steps(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import reorder_steps

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        new_order = list(payload.get("new_order") or [])
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        doc = reorder_steps(doc, new_order)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_insert_step(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import insert_step_after

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        action_kind = str(payload.get("action_kind") or "click")
        insert_after = payload.get("insert_after")
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        doc = insert_step_after(doc, action_kind, insert_after)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_delete_step(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import delete_step_at

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        doc = delete_step_at(doc, step_index)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_update_workflow_inputs(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import merge_skill_inputs

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        inputs = list(payload.get("inputs") or [])
        title = payload.get("title")
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(doc)
        try:
            doc = merge_skill_inputs(doc, inputs, title)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Invalid variables: {exc}") from exc
        self._push_undo(skill_id, snapshot)
        write_skill(skill_id, doc)
        return {"skill_id": skill_id, "ok": True}

    def cmd_replace_literals(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import (
            reconcile_inputs_with_step_values,
            replace_string_literals_in_skill_document,
        )

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        find = str(payload.get("find") or "")
        replace_with = str(payload.get("replace_with") or "")
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        doc, match_count = replace_string_literals_in_skill_document(doc, find, replace_with)
        # A replace that turns literal text into {{var}} must declare that variable too (M-3).
        doc, _ = reconcile_inputs_with_step_values(doc)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        result["match_count"] = match_count
        return result

    def cmd_undo_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        undo_stack = self._undo_stacks.get(skill_id, [])
        if not undo_stack:
            raise _CommandError("nothing_to_undo", "No undo history for this skill")
        current = read_skill(skill_id)
        if current is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._redo_stacks.setdefault(skill_id, []).append(copy.deepcopy(current))
        prev_doc = undo_stack.pop()
        prev_doc = dict(prev_doc)
        meta = dict(prev_doc.get("meta") or {})
        meta["version"] = int(meta.get("version", 1)) + 1
        prev_doc["meta"] = meta
        write_skill(skill_id, prev_doc)
        result = _skill_response(skill_id, prev_doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_redo_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        redo_stack = self._redo_stacks.get(skill_id, [])
        if not redo_stack:
            raise _CommandError("nothing_to_redo", "No redo history for this skill")
        current = read_skill(skill_id)
        if current is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._undo_stacks.setdefault(skill_id, []).append(copy.deepcopy(current))
        next_doc = redo_stack.pop()
        next_doc = dict(next_doc)
        meta = dict(next_doc.get("meta") or {})
        meta["version"] = int(meta.get("version", 1)) + 1
        next_doc["meta"] = meta
        write_skill(skill_id, next_doc)
        result = _skill_response(skill_id, next_doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_retarget_preview(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Phase 2/3 preview for the re-target wizard: generate selector candidates and a
        validation diff for a user-drawn bbox, without persisting anything."""
        from conxa_compile.editor.retarget import RetargetError, preview_retarget
        from conxa_core.storage.json_store import read_skill
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        bbox = {k: payload.get(k) for k in ("x", "y", "w", "h")}
        # regenerate=False means the user is only reviewing an unchanged element, so the
        # already-compiled selectors are read back — no LLM call, no router needed.
        regenerate = bool(payload.get("regenerate", True))
        # Optional path addressing a for_each loop-body step ("for_each.steps[N]") — lets the
        # same 3-phase wizard a top-level step gets run against a nested step instead. Branch
        # bodies don't use this: they keep their own simpler BranchBodyEditor, never the wizard.
        try:
            nested_path = parse_nested_step_path(payload.get("path"))
        except StepPathError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        if regenerate:
            self._install_proxy_router(usage_class="ai_credits")
        try:
            return preview_retarget(doc, step_index, bbox, regenerate=regenerate, nested_path=nested_path)
        except RetargetError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        except EntitlementBlocked as exc:
            raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
        except QuotaExceeded as exc:
            raise _CommandError("quota_exceeded", str(exc)) from exc
        except CloudUnreachable as exc:
            raise _CommandError("cloud_unreachable", str(exc)) from exc

    def cmd_retarget_apply(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Atomically apply the re-target wizard's chosen selector + bbox + validation.

        Lands as a single undo entry — nothing from `cmd_retarget_preview` was persisted.
        """
        import copy

        from conxa_compile.editor.retarget import RetargetError, apply_retarget
        from conxa_compile.compiler.patch import revalidate_step
        from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError
        from conxa_core.storage.json_store import read_skill, write_skill
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_index = int(payload.get("step_index") or 0)
        try:
            nested_path = parse_nested_step_path(payload.get("path"))
        except StepPathError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(doc)
        self._install_proxy_router(usage_class="ai_credits")
        try:
            doc = apply_retarget(doc, step_index, payload, nested_path=nested_path)
        except RetargetError as exc:
            raise _CommandError(exc.code, exc.message) from exc
        except VisionAnchorGenerationError as exc:
            detail = f" ({exc.hint})" if exc.hint else ""
            raise _CommandError(
                "vision_anchors_failed", f"Could not re-derive recovery anchors ({exc.reason}){detail}."
            ) from exc
        except EntitlementBlocked as exc:
            raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
        except QuotaExceeded as exc:
            raise _CommandError("quota_exceeded", str(exc)) from exc
        except CloudUnreachable as exc:
            raise _CommandError("cloud_unreachable", str(exc)) from exc

        skills = doc.get("skills") or [{}]
        steps = (skills[0] or {}).get("steps") or []
        parent_step = steps[step_index] if step_index < len(steps) else {}
        if nested_path is not None:
            try:
                step = get_nested_step(parent_step, nested_path)
            except StepPathError as exc:
                raise _CommandError(exc.code, exc.message) from exc
        else:
            step = parent_step
        revalidation = revalidate_step(step)
        self._push_undo(skill_id, snapshot)
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc, revalidation)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_sign_off_workflow(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        import time
        from conxa_core.storage.workflow_store import list_workflows, save_workflow
        from conxa_core.workspace import LOCAL_WORKSPACE_ID

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        target_workflow = None
        for wf in list_workflows(workspace_id):
            if wf.skill_id == skill_id:
                wf.edited_at = time.time()
                wf.signed_off = True
                save_workflow(wf)
                target_workflow = wf
                break

        if target_workflow is None:
            return {"skill_id": skill_id, "signed_off": True, "built": False, "waiting_on": []}

        # Sign-off gates the build (skill_package_builder.py's raise-on-uncompiled/unedited
        # is the enforcement point); auto-build fires the moment THIS workflow satisfies
        # that gate — scoped to only_workflow_id, so a sibling workflow that isn't
        # compiled/edited yet never blocks or gets swept into this workflow's build.
        from conxa_compile.skill_package_builder import build_skill_package

        sink = _event_sink(rid)
        try:
            # company_name is only used the first time this workspace ever builds a
            # skill package (SkillPack.get_or_create) — falls back to this workflow's
            # own name, matching the old company_name = plugin.name default.
            build_skill_package(
                workspace_id,
                company_name=target_workflow.name,
                only_workflow_id=target_workflow.id,
                realtime_sink=sink,
            )
            return {"skill_id": skill_id, "signed_off": True, "built": True, "waiting_on": []}
        except Exception as exc:
            sink({"kind": "skill_package_build", "message": f"Auto-build failed: {exc}"})
            return {
                "skill_id": skill_id,
                "signed_off": True,
                "built": False,
                "waiting_on": [],
                "build_error": str(exc),
            }

