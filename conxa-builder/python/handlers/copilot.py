"""Human Review Copilot command handlers (BUILD-26 stages b-f).

`cmd_copilot_turn` diagnoses from the evidence bundle and, when it has something to propose,
returns pre-gated proposals as accept/reject diffs — never applying anything itself. Two
proposal KINDS exist: `patch_step` (edit a field on an existing step) and, since stage (f),
`insert_overlay_branch` (insert an if_present/try_dismiss branch built from an overlay the
runtime actually observed during a test run — never a selector the model invented).

`cmd_accept_copilot_proposal` and `cmd_reject_copilot_proposal` are the two ways a reviewer
disposes of one. Accept branches on the proposal's `command`: `patch_step` resolves step_key to
the CURRENT step_index and delegates to cmd_patch_step, the same validated, undo-tracked path a
manual edit takes; `insert_overlay_branch` composes cmd_insert_step + cmd_patch_step (+
cmd_insert_branch_step for if_present's nested click) — the same existing, gate-validated RPCs,
no new mutation logic. Reject writes straight to the same decision log accept goes through
backend.py's dispatch() hook, because a rejected proposal changes no document and would
otherwise leave no trace at all.

`cmd_copilot_verify` (stage e) rebuilds the skill package and retests the workflow after an
accepted proposal, then writes a fixed/still_failing/progressed verdict into the same decision
log — the label that says whether an accepted fix actually worked, not just that a human liked it.
"""

from __future__ import annotations

from typing import Any

from handlers.protocol import _CommandError, _event_sink, _safe_id, _skill_response


class CopilotMixin:
    def cmd_copilot_turn(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.editor.copilot_proposals import (
            gate_overlay_proposals,
            gate_proposals,
            gate_structural_proposals,
        )
        from conxa_compile.editor.evidence import EvidenceError, build_evidence_bundle
        from conxa_compile.llm.copilot import copilot_turn
        from conxa_core.storage.json_store import read_skill
        from services.llm_proxy_client import (
            CloudUnreachable,
            EntitlementBlocked,
            ProxyUnavailable,
            QuotaExceeded,
        )

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        message = str(payload.get("message") or "").strip()
        if not message:
            raise _CommandError("invalid_input", "message is required")
        transcript = payload.get("transcript") if isinstance(payload.get("transcript"), list) else []
        run_id = payload.get("run_id")
        run_id = str(run_id).strip() if run_id else None

        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")

        try:
            # BUILD-26 stage g: the DIGEST, not the old always-full bundle — every step, no heavy
            # fields. The copilot pulls detail on demand via the need[] retrieval loop instead
            # (conxa_compile/editor/copilot_retrieval.py, driven from llm/copilot.py).
            evidence = build_evidence_bundle(skill_id, run_id=run_id, detail="digest")
        except EvidenceError as exc:
            raise _CommandError(exc.code, exc.message) from exc

        screenshot_path = (evidence.get("runtime_evidence") or {}).get("failure_screenshot_path")

        sink = _event_sink(rid)
        self._install_proxy_router(sink=sink, usage_class="ai_credits")
        try:
            result = copilot_turn(
                skill_id=skill_id,
                evidence=evidence,
                transcript=transcript,
                message=message,
                screenshot_path=screenshot_path,
                on_delta=lambda text: sink({"phase": "copilot_delta", "text": text}),
            )
        except EntitlementBlocked as exc:
            raise _CommandError(exc.code, self._entitlement_error_message(exc.code)) from exc
        except QuotaExceeded as exc:
            raise _CommandError("quota_exceeded", str(exc)) from exc
        except ProxyUnavailable as exc:
            # Above CloudUnreachable (its parent) on purpose: the cloud answered, it just couldn't
            # get an answer out of any provider. Reporting that as "check your internet connection"
            # sends the reviewer to debug a network that is working fine.
            raise _CommandError("llm_no_output", str(exc)) from exc
        except CloudUnreachable as exc:
            raise _CommandError("cloud_unreachable", str(exc)) from exc

        raw_proposals = result.get("proposals") or []
        proposals = gate_proposals(doc, raw_proposals)
        # BUILD-26 stage f: a second proposal KIND from the same model call — an overlay
        # insertion, gated separately since it needs the run's observed_overlays rather than
        # patch_gate's field allow-list. Stage g adds a THIRD kind — typed structural ops. All
        # three are concatenated: the renderer only ever shows proposals[0].
        observed_overlays = (evidence.get("runtime_evidence") or {}).get("observed_overlays") or []
        if observed_overlays:
            proposals = proposals + gate_overlay_proposals(doc, observed_overlays, raw_proposals)
        proposals = proposals + gate_structural_proposals(doc, raw_proposals)
        return {"reply": result.get("reply") or "", "proposals": proposals}

    def cmd_accept_copilot_proposal(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        # BUILD-26 stage f: a second proposal kind branches here before the patch_step path below
        # even looks at step_key — insert_overlay_branch has no step_key of its own (it inserts a
        # new step, it doesn't edit an existing one).
        command = str(payload.get("command") or "").strip()
        if command == "insert_overlay_branch":
            return self._accept_overlay_branch_proposal(payload, rid)
        # BUILD-26 stage g: the third proposal kind — one or more typed structural ops, applied
        # as a single all-or-nothing batch with ONE undo entry (see _accept_structural_proposal).
        if command == "structural_op":
            return self._accept_structural_proposal(payload, rid)

        from conxa_compile.editor.copilot_proposals import ProposalError, resolve_step_index
        from conxa_core.storage.json_store import read_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_key = str(payload.get("step_key") or "").strip()
        patch = payload.get("patch")
        if not step_key or not isinstance(patch, dict):
            raise _CommandError("invalid_input", "step_key and patch are required")

        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        try:
            step_index = resolve_step_index(doc, step_key)
        except ProposalError as exc:
            raise _CommandError(exc.code, exc.message) from exc

        # Delegates to the exact command a manual edit uses — its own patch_gate re-run, its own
        # undo entry, its own edits.jsonl lines. dispatch()'s edit-log hook attributes the write
        # to THIS command (accept_copilot_proposal, in _EDIT_COMMANDS), not "patch_step", so the
        # accepted line in edits.jsonl carries source="copilot" (backend.py's dispatch()).
        return self.cmd_patch_step(
            {"skill_id": skill_id, "step_index": step_index, "patch": patch}, rid
        )

    def _apply_change_set(self, skill_id: str, proposals: list[dict[str, Any]], rid: str) -> None:
        """Apply every proposal from one chat reply as one all-or-nothing change with ONE undo entry
        (the pattern `_accept_structural_proposal` uses within structural ops, extended to any mix
        of kinds). One failing proposal restores the document, so the person never ends up with half
        of what the reply described."""
        import copy

        from conxa_core.storage.json_store import read_skill, write_skill

        pre = read_skill(skill_id)
        if pre is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(pre)
        undo_stack = self._undo_stacks.setdefault(skill_id, [])
        depth = len(undo_stack)
        try:
            for proposal in proposals:
                self.cmd_accept_copilot_proposal({**proposal, "skill_id": skill_id, "proposal_id": proposal.get("id")}, rid)
        except _CommandError:
            del undo_stack[depth:]
            write_skill(skill_id, snapshot)
            raise
        del undo_stack[depth:]
        undo_stack.append(snapshot)
        self._redo_stacks[skill_id] = []

    def _accept_overlay_branch_proposal(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Composes three existing, gate-validated RPCs — cmd_insert_step, cmd_patch_step, and
        (if_present only) cmd_insert_branch_step — rather than writing new mutation logic. All
        three run inside this one dispatch() call, so the edit-log's before/after doc diff (see
        backend.py) captures every change from a single accept as one attributed batch.

        Known ceiling: several internal cmd_* calls means several undo entries for one accept,
        not one compound entry — acceptable for now; only worth collapsing if reviewers complain.
        """
        from conxa_compile.compiler.step_key import step_keys
        from conxa_core.storage.json_store import read_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        primitive = str(payload.get("primitive") or "").strip()
        after_step_key = payload.get("after_step_key")
        patch = payload.get("patch")
        nested_step = payload.get("nested_step")
        if primitive not in ("try_dismiss", "if_present") or not isinstance(patch, dict):
            raise _CommandError("invalid_input", "primitive and patch are required")

        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        skills = doc.get("skills") if isinstance(doc.get("skills"), list) else []
        block0 = skills[0] if skills and isinstance(skills[0], dict) else {}
        steps = block0.get("steps") if isinstance(block0.get("steps"), list) else []

        insert_after: int | None = None
        if after_step_key:
            keys = step_keys(steps)
            try:
                insert_after = keys.index(str(after_step_key))
            except ValueError as exc:
                raise _CommandError(
                    "proposal_stale",
                    "The step this was proposed after no longer exists — the workflow changed since this was proposed.",
                ) from exc
        new_index = (insert_after + 1) if insert_after is not None else len(steps)

        self.cmd_insert_step({"skill_id": skill_id, "action_kind": primitive, "insert_after": insert_after}, rid)
        result = self.cmd_patch_step({"skill_id": skill_id, "step_index": new_index, "patch": patch}, rid)

        if primitive == "if_present" and isinstance(nested_step, dict):
            self.cmd_insert_branch_step({"skill_id": skill_id, "step_index": new_index, "action_kind": "click"}, rid)
            result = self.cmd_patch_step(
                {"skill_id": skill_id, "step_index": new_index, "path": "branch.steps[0]", "patch": nested_step}, rid
            )
        return result

    def _accept_structural_proposal(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Apply one or more gated structural ops (`gate_structural_proposals`) as a single
        all-or-nothing batch, collapsed to exactly ONE undo entry — this is what fixes the
        multi-undo-entry ceiling `_accept_overlay_branch_proposal` above accepted as unavoidable.

        Each op still goes through the SAME cmd_* RPC a manual edit would use (cmd_insert_step,
        cmd_patch_step, cmd_delete_step, cmd_reorder_steps, cmd_update_workflow_inputs,
        cmd_replace_literals) — no new mutation logic — so every individual op re-runs the patch
        gate and re-resolves its own step_key against the CURRENT document. Accepts either a
        single op at the top level (`payload["op"]` + its own fields) or a batch (`payload["ops"]`
        — a list of the same shape), so a renderer can send one proposal or several accepted
        together.

        Any op failing mid-batch aborts the WHOLE batch: the document is restored to its
        pre-batch snapshot and every undo entry the batch's own cmd_* calls pushed is discarded,
        so a partial structural change is never left half-applied.
        """
        import copy

        from conxa_compile.editor.copilot_proposals import ProposalError
        from conxa_core.storage.json_store import read_skill, write_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        ops = payload.get("ops") if isinstance(payload.get("ops"), list) else [payload]
        ops = [op for op in ops if isinstance(op, dict) and op.get("op")]
        if not ops:
            raise _CommandError("invalid_input", "op or ops is required")

        pre_batch_doc = read_skill(skill_id)
        if pre_batch_doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        snapshot = copy.deepcopy(pre_batch_doc)
        undo_stack = self._undo_stacks.setdefault(skill_id, [])
        depth_before = len(undo_stack)

        result: dict[str, Any] = {}
        try:
            for op in ops:
                result = self._apply_one_structural_op(skill_id, op, rid)
        except _CommandError:
            del undo_stack[depth_before:]
            write_skill(skill_id, snapshot)
            raise
        except ProposalError as exc:
            del undo_stack[depth_before:]
            write_skill(skill_id, snapshot)
            raise _CommandError(exc.code, exc.message) from exc

        # Collapse however many undo entries this batch's cmd_* calls pushed into exactly ONE —
        # the pre-batch snapshot — so a reviewer's single Undo after accepting reverses the whole
        # proposal, not just its last op.
        del undo_stack[depth_before:]
        undo_stack.append(snapshot)
        self._redo_stacks[skill_id] = []
        return result

    def _apply_one_structural_op(self, skill_id: str, op: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.editor.copilot_proposals import resolve_step_index
        from conxa_core.storage.json_store import read_skill

        kind = str(op.get("op") or "").strip()
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")

        if kind == "insert_step":
            after_step_key = op.get("after_step_key")
            insert_after = resolve_step_index(doc, after_step_key) if after_step_key else None
            skills = doc.get("skills") if isinstance(doc.get("skills"), list) else []
            block0 = skills[0] if skills and isinstance(skills[0], dict) else {}
            pre_len = len(block0.get("steps") or [])
            action_kind = str(op.get("action_kind") or "").strip()
            result = self.cmd_insert_step(
                {"skill_id": skill_id, "action_kind": action_kind, "insert_after": insert_after}, rid
            )
            new_index = (insert_after + 1) if insert_after is not None else pre_len

            identity_from_step_key = op.get("identity_from_step_key")
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
            patch: dict[str, Any] = {}
            if identity_from_step_key:
                source_index = resolve_step_index(doc, identity_from_step_key)
                source_step = (block0.get("steps") or [])[source_index]
                patch["identity_bundle"] = source_step.get("identity_bundle")
                patch["target"] = source_step.get("target")
            for field, value in fields.items():
                nested = patch
                parts = field.split(".")
                for part in parts[:-1]:
                    nested = nested.setdefault(part, {})
                nested[parts[-1]] = value
            if patch:
                result = self.cmd_patch_step({"skill_id": skill_id, "step_index": new_index, "patch": patch}, rid)
            return result

        if kind == "delete_step":
            step_key = str(op.get("step_key") or "").strip()
            step_index = resolve_step_index(doc, step_key)
            return self.cmd_delete_step({"skill_id": skill_id, "step_index": step_index}, rid)

        if kind == "move_step":
            step_key = str(op.get("step_key") or "").strip()
            after_step_key = op.get("after_step_key")
            step_index = resolve_step_index(doc, step_key)
            after_index = resolve_step_index(doc, after_step_key) if after_step_key else None
            skills = doc.get("skills") if isinstance(doc.get("skills"), list) else []
            block0 = skills[0] if skills and isinstance(skills[0], dict) else {}
            n = len(block0.get("steps") or [])
            order = [i for i in range(n) if i != step_index]
            target_pos = 0 if after_index is None else (order.index(after_index) + 1)
            new_order = order[:target_pos] + [step_index] + order[target_pos:]
            return self.cmd_reorder_steps({"skill_id": skill_id, "new_order": new_order}, rid)

        if kind == "update_inputs":
            inputs = op.get("inputs") if isinstance(op.get("inputs"), list) else []
            return self.cmd_update_workflow_inputs({"skill_id": skill_id, "inputs": inputs}, rid)

        if kind == "parameterize":
            # Same command the understanding check's "varies" answer uses; one call per rewrite the
            # gate's dry run found (click label, page address / row id). Each declares the input.
            if not op.get("kinds"):
                raise _CommandError("invalid_input", "Nothing to make an input from.")
            result = {}
            for rewrite in op["kinds"]:
                result = self.cmd_parameterize_literal({
                    "skill_id": skill_id, "literal": op.get("literal"), "name": op.get("name"),
                    "default": op.get("literal"), "kind": "click" if rewrite == "click" else "value",
                }, rid)
            return result

        if kind == "replace_literals":
            find = str(op.get("find") or "")
            replace = str(op.get("replace") or "")
            return self.cmd_replace_literals({"skill_id": skill_id, "find": find, "replace_with": replace}, rid)

        raise _CommandError("unsupported_structural_op", f"Unknown structural op: {kind!r}")

    def cmd_copilot_load_session(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_compile.editor.copilot_sessions import load_last_session

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        return {"messages": load_last_session(skill_id)}

    def cmd_copilot_save_session(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_compile.editor.copilot_sessions import save_copilot_session

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        transcript = payload.get("transcript") if isinstance(payload.get("transcript"), list) else []
        save_copilot_session(skill_id, transcript)
        return {"ok": True}

    def cmd_copilot_verify(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Rebuild + retest a workflow after an accepted copilot proposal, then log the verdict.

        Two-phase, matching the renderer's confirm-then-run flow: `confirmed=False` (the default)
        only counts steps whose compiled `consequence` is "irreversible" and returns them for a
        confirmation prompt — no build, no browser. `confirmed=True` runs the exact commands a
        reviewer would click by hand (cmd_build_skill_package, then cmd_test_workflow) and derives
        a fixed/still_failing/progressed verdict by comparing the new run's failing step (if any)
        to the step under verification, written into edits.jsonl via append_verification.
        """
        from conxa_core.storage.json_store import read_skill

        from conxa_compile.compiler.step_key import step_keys
        from conxa_compile.editor.edit_log import append_verification
        from conxa_compile.editor.describe import describe_step
        from conxa_compile.editor.evidence import EvidenceError, build_evidence_bundle, find_workflow_for_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_key = str(payload.get("step_key") or "").strip()
        proposal_id = str(payload.get("proposal_id") or "").strip() or None
        confirmed = bool(payload.get("confirmed"))

        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        workflow = find_workflow_for_skill(skill_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", f"No workflow compiles skill {skill_id}")

        skills = doc.get("skills") if isinstance(doc.get("skills"), list) else []
        block0 = skills[0] if skills and isinstance(skills[0], dict) else {}
        steps = block0.get("steps") if isinstance(block0.get("steps"), list) else []

        if not confirmed:
            # Pre-flight only — CLAUDE.md/PROD-3's own consequence classification, not a re-typed
            # copy of the runtime's step-type idempotency table (they'd disagree). Known ceiling:
            # classify_consequence only marks a destructive/commit CLICK irreversible, so an
            # upload/keyboard-shortcut/checkbox-toggle step counts as 0 here even though the
            # runtime's isNonIdempotent guard treats it as non-reversible too. The dialog names
            # what it actually counted rather than implying a full audit.
            keys = step_keys(steps)
            irreversible = [
                {"step_key": key, "description": describe_step(step, i)}
                for i, (step, key) in enumerate(zip(steps, keys))
                if step.get("consequence") == "irreversible"
            ]
            return {"status": "confirm_required", "irreversible_count": len(irreversible), "irreversible_steps": irreversible}

        sink = _event_sink(rid)

        def _relay(message: str) -> None:
            sink({"phase": "copilot_verify", "text": message})

        _relay("Rebuilding skill package…")
        try:
            self.cmd_build_skill_package({"skill_slug": workflow.slug, "for_test": True}, rid)
        except _CommandError as exc:
            _relay(f"Build failed: {exc.message}")
            raise

        _relay(f"Running {workflow.name!r}…")
        try:
            result = self.cmd_test_workflow(
                {"workflow_id": workflow.id, "inputs": workflow.last_test_inputs, "headless": False},
                rid,
            )
        except _CommandError as exc:
            if exc.code != "workflow_test_failed":
                raise
            new_run_id = exc.run_id
            try:
                evidence = build_evidence_bundle(skill_id, run_id=new_run_id) if new_run_id else None
            except EvidenceError:
                evidence = None
            failed_step_key = evidence.get("failed_step_key") if evidence else None
            verdict = "still_failing" if failed_step_key == step_key else "progressed"
            if proposal_id:
                append_verification(
                    skill_id, proposal_id=proposal_id, step_key=step_key, verdict=verdict,
                    run_id=new_run_id or "", message=exc.message,
                )
            _relay(f"Retest failed: {exc.message}")
            return {"status": "verified", "verdict": verdict, "run_id": new_run_id, "message": exc.message}

        if result.get("status") == "cancelled":
            # EXEC-35: a user-initiated cancel is neither a pass nor a failure — writing a verdict
            # for it would poison the correction dataset with a false "fixed"/"still_failing".
            _relay("Verification cancelled.")
            return {"status": "cancelled", "run_id": result.get("run_id")}

        new_run_id = result.get("run_id")
        if proposal_id:
            append_verification(
                skill_id, proposal_id=proposal_id, step_key=step_key, verdict="fixed",
                run_id=new_run_id or "", message=result.get("message") or "",
            )
        _relay("Retest passed.")
        return {"status": "verified", "verdict": "fixed", "run_id": new_run_id, "message": result.get("message")}

    def cmd_reject_copilot_proposal(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_compile.editor.edit_log import append_decision

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        step_key = str(payload.get("step_key") or "").strip()
        proposal_id = str(payload.get("proposal_id") or "").strip()
        if not proposal_id:
            raise _CommandError("invalid_input", "proposal_id is required")
        if not step_key:
            # insert_overlay_branch proposals (BUILD-26 stage f) insert a new step rather than
            # editing an existing one, so they carry no step_key — log against the overlay
            # instead of leaving the field empty, so the decision log stays one shape.
            overlay_id = str(payload.get("overlay_id") or "").strip()
            step_key = f"overlay:{overlay_id}" if overlay_id else "overlay:unknown"

        append_decision(
            skill_id,
            proposal_id=proposal_id,
            decision="rejected",
            command=str(payload.get("command") or "patch_step"),
            field=str(payload.get("field") or ""),
            why=str(payload.get("why") or ""),
            step_key=step_key,
        )
        return {"ok": True}

    def cmd_accept_for_each_suggestion(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Accept a `compiler/loop_suggestion.py` finding (a one-click "generalize this to a
        loop" suggestion — no LLM involved in producing it, unlike the copilot proposals above,
        but sharing their accept/reject/undo/edit-log machinery since the governance story is
        the same: a compiler-computed change never applies itself).

        One atomic mutation (`apply_for_each_loop_suggestion`), not a composition of several
        cmd_* calls like `_accept_overlay_branch_proposal` — the wrap, the body rewrite, the
        upload rebind, and the new input all land in a single document write, so this is one
        undo entry, not several.

        `remove_redundant_click` (default true) answers the SEPARATE "remove this leftover
        click too?" popup Human Edit only asks when the suggestion has a
        `redundant_click_key` — a distinct Yes/No from accepting the loop itself
        (ReviewQuestionsDialog.tsx)."""
        import copy
        from conxa_core.storage.json_store import read_skill, write_skill
        from conxa_compile.editor.workflow_mutations import apply_for_each_loop_suggestion

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        suggestion = payload.get("suggestion")
        if not isinstance(suggestion, dict):
            raise _CommandError("invalid_input", "suggestion is required")
        remove_redundant_click = bool(payload.get("remove_redundant_click", True))
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        self._push_undo(skill_id, copy.deepcopy(doc))
        try:
            doc = apply_for_each_loop_suggestion(doc, suggestion, remove_redundant_click=remove_redundant_click)
        except ValueError as exc:
            raise _CommandError(str(exc), f"Could not apply this suggestion: {exc}") from exc
        write_skill(skill_id, doc)
        result = _skill_response(skill_id, doc)
        result.update(self._history_flags(skill_id))
        return result

    def cmd_reject_for_each_suggestion(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Log the dismissal so `loop_suggestion.py::filter_rejected` (via
        `handlers/compile.py`) excludes this exact suggestion from the next compile's findings —
        writes into the SAME edits.jsonl every other proposal decision uses, so this feature's
        accept/reject rate is visible in the same place, not a second log."""
        from conxa_compile.editor.edit_log import append_decision

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        suggestion = payload.get("suggestion")
        if not isinstance(suggestion, dict):
            raise _CommandError("invalid_input", "suggestion is required")
        upload_step_key = str(suggestion.get("upload_step_key") or "").strip()
        suggestion_id = str(suggestion.get("id") or "").strip()
        if not upload_step_key or not suggestion_id:
            raise _CommandError("invalid_input", "suggestion.upload_step_key and suggestion.id are required")

        append_decision(
            skill_id,
            proposal_id=suggestion_id,
            decision="rejected",
            command="reject_for_each_suggestion",
            field="for_each_suggestion",
            why=str(suggestion.get("why") or ""),
            step_key=upload_step_key,
        )
        return {"ok": True}
