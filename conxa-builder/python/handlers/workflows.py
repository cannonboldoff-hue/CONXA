"""Workflow CRUD, skill-package build, installer, and publish command handlers."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from services import bootstrap as _bootstrap_pkg
from conxa_compile.recorder.session import registry as _recorder_registry
from conxa_core.workspace import LOCAL_WORKSPACE_ID
from handlers.protocol import (
    _CommandError,
    _event_sink,
    _runtime_result_text,
    _safe_id,
    _sign_in_setup_drift,
    _stage_runtime_auth,
    _validate_release_notes,
    _validate_release_version,
)
from handlers.status import derive_workflow_stage


def _redact_sensitive_test_inputs(skill_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Never let a `sensitive`-flagged input's value reach persisted test history.

    The "Sensitive (mask value)" checkbox was written into skill.json/inputs.json at every
    prior stage but read by nothing — this is its runtime consumer (audit H-5). Redacted to
    "" rather than dropped so the field still round-trips as declared (just empty, so it
    doesn't silently re-prefill a secret in the test form on the next run).
    """
    from conxa_core.storage.json_store import read_skill

    doc = read_skill(skill_id) or {}
    declared = doc.get("inputs") if isinstance(doc.get("inputs"), list) else []
    sensitive_ids = {
        str(i.get("id") or i.get("name") or "").strip().lower()
        for i in declared
        if isinstance(i, dict) and i.get("sensitive")
    }
    if not sensitive_ids:
        return inputs
    return {k: ("" if str(k).strip().lower() in sensitive_ids else v) for k, v in inputs.items()}


# EXEC-13: bounds the Studio sandbox's answer-and-resume loop for an ai_review checkpoint (see
# cmd_test_workflow). A misconfigured workflow whose later steps never see a satisfying answer
# would otherwise hold the parked browser/host-lock open indefinitely.
_MAX_AI_REVIEW_PAUSES = 5
_AI_REVIEW_META_KEY = "conxa/ai_review"


def _extract_ai_review_pause(result: dict[str, Any]) -> dict[str, Any] | None:
    """Pull {step_index, prompt, output_schema} from an ai_review pause response, or None if
    `result` isn't one. Primary source is `_meta["conxa/ai_review"]`
    (runtime/app/review_pause.js::buildReviewRequest) — the MCP SDK's ResultSchema is a loose
    object, so this survives the stdio round-trip unmodified. Falls back to the prose header's
    `resume_from: N` line (no schema available on that path, so the answer goes unvalidated
    locally and leans on the runtime's own bounded re-ask instead)."""
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    review = meta.get(_AI_REVIEW_META_KEY)
    if isinstance(review, dict) and isinstance(review.get("step_index"), int):
        return {
            "step_index": review["step_index"],
            "prompt": str(review.get("prompt") or ""),
            "output_schema": review.get("output_schema") if isinstance(review.get("output_schema"), dict) else None,
        }
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            m = re.search(r"resume_from:\s*(\d+)", str(item.get("text") or ""))
            if m:
                return {"step_index": int(m.group(1)), "prompt": "", "output_schema": None}
    return None


_GUARD_META_KEY = "conxa/guard"
_FORK_META_KEY = "conxa/fork"
_FORK_STOP = "__stop__"
_SESSION_EXPIRED_META_KEY = "conxa/session_expired"


def _extract_fork_pause(result: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the question from an outcome-fork pause (a popup that needs a PERSON to decide), or None
    when `result` isn't one. Only `_meta` is trusted, and this MUST be checked before
    _extract_ai_review_pause: the fork's prose also contains "resume_from: N", which the review
    extractor's text fallback would misread as an ai_review question and hand to the model --
    exactly what a human-only decision must never reach."""
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    fork = meta.get(_FORK_META_KEY)
    if isinstance(fork, dict) and isinstance(fork.get("step_index"), int):
        choices = [c for c in fork.get("choices") or [] if isinstance(c, dict) and c.get("id")]
        return {
            "step_index": fork["step_index"],
            "title": str(fork.get("title") or ""),
            "message": str(fork.get("message") or ""),
            "reason": str(fork.get("reason") or ""),
            "unknown": bool(fork.get("unknown")),
            "suggested": fork.get("suggested"),
            "choices": [{"id": str(c["id"]), "label": str(c.get("label") or c["id"]), "role": str(c.get("role") or "")} for c in choices],
        }
    return None


def _extract_guard_pause(result: dict[str, Any]) -> dict[str, Any] | None:
    """Pull {step_index, step_intent, remaining_irreversible, run_id} from an RT-9 guard pause
    (runtime/app/server.js: the run stopped, parked, before a step that can't be undone), or None
    when `result` isn't one. Only `_meta` is trusted — there is deliberately no prose fallback,
    because the ai_review extractor above treats any "resume_from" text as a review pause."""
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    guard = meta.get(_GUARD_META_KEY)
    if isinstance(guard, dict) and isinstance(guard.get("step_index"), int):
        return {
            "step_index": guard["step_index"],
            "step_intent": str(guard.get("step_intent") or ""),
            "remaining": int(guard.get("remaining_irreversible") or 0),
            "run_id": str(guard.get("run_id") or ""),
        }
    return None


def _extract_session_expired(result: dict[str, Any]) -> dict[str, Any] | None:
    """The runtime stopped because a saved sign-in is no longer valid (not because a step broke).
    Only `_meta` is trusted; an older engine sends plain text and so reads as an ordinary failure."""
    meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
    info = meta.get(_SESSION_EXPIRED_META_KEY)
    if isinstance(info, dict):
        step = info.get("step_index")
        return {"step_index": step if isinstance(step, int) else None, "run_id": str(info.get("run_id") or "")}
    return None


def _app_layer_supports(conxa_dir: Path, capability: str) -> bool:
    """Does the staged app layer declare `capability` in its version.json? An older engine silently
    ignores a Studio-only argument it doesn't know — for the test guard that would mean sending a
    real invoice — so the Studio asks first and falls back to a dry-run preview."""
    try:
        meta = json.loads((conxa_dir / "conxa-app" / "current" / "version.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    caps = meta.get("capabilities") if isinstance(meta, dict) else None
    return isinstance(caps, list) and capability in caps


def _is_confirm_only_review_request(review: dict[str, Any]) -> bool:
    from conxa_compile.editor.patch_gate import is_confirm_only_review

    return is_confirm_only_review({"ai_review_output_schema": review.get("output_schema"), "ai_review_on_failure": "abort"})


def _drive_runtime_pauses(
    result: dict[str, Any],
    run_args: dict[str, Any],
    *,
    call: Any,
    cancel_run: Any,
    answer_review: Any,
    guard_hook: Any,
    review_hook: Any,
    guard_requested: bool,
    previewed: bool,
    mark: Any,
    fork_hook: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Keep calling the runtime until it stops pausing. Returns ``(final result, stopped_at)``.

    Four kinds of pause: a popup decision (an outcome fork -- only the person decides, never the
    model; with no one to ask the test ends there), the RT-9 guard (a step that can't be undone — the person decides), a
    confirm-only review (the workflow author's own "should a person confirm?" — the person answers,
    and "yes" also releases the guard in the same resume so they are not asked twice), and an
    ordinary ai_review (the sandbox model answers, as before). ``stopped_at`` is set when the person
    ended the test (or said no); nothing past that point ran. Bounded, so a misbehaving answerer
    fails the test loudly instead of looping while holding a parked browser open."""
    pauses_seen = 0
    while True:
        # Guard first: the review extractor's prose fallback would misread any pause as a review.
        guard = _extract_guard_pause(result)
        if guard is not None:
            decision = guard_hook(guard) if guard_hook is not None else "end_here"
            if decision != "run_rest":
                try:  # best effort: free the parked browser instead of waiting out its timeout
                    cancel_run(guard["run_id"])
                except Exception:  # noqa: BLE001
                    pass
                return result, guard
            mark(f"Running the steps that can't be undone, from {guard['step_intent'] or 'here'}…")
            run_args = {**run_args, "resume_from": guard["step_index"], "_guard_release": True}
            result = call(run_args)
            continue
        fork = _extract_fork_pause(result)
        if fork is not None:
            pauses_seen += 1
            if pauses_seen > _MAX_AI_REVIEW_PAUSES:
                raise RuntimeError(
                    f"A popup decision paused {pauses_seen} times in one test run — stopping instead of looping."
                )
            mark(f"Waiting for your decision on “{fork['title'] or 'a popup'}”…")
            choice = fork_hook(fork) if fork_hook is not None else _FORK_STOP
            valid = {c["id"] for c in fork["choices"]}
            if choice not in valid or choice == _FORK_STOP:
                return result, {"step_index": fork["step_index"], "step_intent": fork["title"],
                                "remaining": 0, "run_id": "", "declined": True}
            run_args = {**run_args, "resume_from": fork["step_index"],
                        "review_results": {str(fork["step_index"]): {"choice": choice}}}
            result = call(run_args)
            continue
        review = _extract_ai_review_pause(result)
        if review is None:
            return result, None
        pauses_seen += 1
        if pauses_seen > _MAX_AI_REVIEW_PAUSES:
            raise RuntimeError(
                f"AI review paused {pauses_seen} times in one test run — stopping "
                "instead of looping. Check the workflow for a review step whose answer "
                "never satisfies a later step."
            )
        extra: dict[str, Any] = {}
        if review_hook is not None and _is_confirm_only_review_request(review):
            mark(f"Waiting for your confirmation at step {review['step_index'] + 1}…")
            if not review_hook(review):
                return result, {"step_index": review["step_index"], "step_intent": review.get("prompt", ""),
                                "remaining": 0, "run_id": "", "declined": True}
            answer = {"confirmed": True}
            if guard_requested and not previewed:
                extra["_guard_release"] = True
        else:
            mark(f"Answering AI review at step {review['step_index'] + 1}…")
            answer = answer_review(review, result.get("content") or [])
        run_args = {**run_args, "resume_from": review["step_index"], "review_results": {str(review["step_index"]): answer}, **extra}
        result = call(run_args)


def _review_context_from_content(content: list[Any]) -> tuple[str, tuple[str, str] | None]:
    """Split an ai_review pause's MCP content blocks into (joined text, last image). The LAST
    image block is always the current-page screenshot — buildReviewRequest pushes an optional
    reference image first, the current one last."""
    text_parts: list[str] = []
    image: tuple[str, str] | None = None
    for item in content or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                text_parts.append(text)
        elif item.get("type") == "image" and item.get("data"):
            image = (str(item["data"]), str(item.get("mimeType") or "image/jpeg"))
    return "\n\n".join(text_parts), image


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    return True  # unknown type token — don't block on it


def _validate_ai_review_answer(answer: Any, output_schema: dict[str, Any] | None) -> list[str]:
    """Same hand-rolled structural check as runtime/app/review_pause.js::validateReviewAnswer
    (required fields + type + enum, not full JSON Schema) — mirrored here so a malformed Studio
    answer fails fast instead of burning the runtime's own two bounded re-asks."""
    if not output_schema:
        return []
    if not isinstance(answer, dict):
        return ["answer must be a JSON object"]
    errors: list[str] = []
    for key in output_schema.get("required") or []:
        if key not in answer:
            errors.append(f'missing required field "{key}"')
    properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
    for key, spec in properties.items():
        if key not in answer or not isinstance(spec, dict):
            continue
        value = answer[key]
        type_name = spec.get("type")
        if type_name and not _matches_type(value, type_name):
            errors.append(f'field "{key}" must be of type {type_name}')
            continue
        enum = spec.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f'field "{key}" must be one of {json.dumps(enum)}')
    return errors


# EXEC-35: tracks the one in-flight "Run Test" per workflow so cmd_cancel_test_workflow can find
# it. run_id is only known a second or so into the run (relayed via _phase_sink, see
# cmd_test_workflow) — cancel_requested lets an earlier click still take effect once it arrives.
_test_run_lock = threading.Lock()
_active_test_runs: dict[str, dict[str, Any]] = {}


def _cancel_active_test_run(workflow_id: str) -> None:
    """Fire the runtime's cancel_execution tool for workflow_id's tracked run_id.
    Best-effort: the run may finish on its own between the flag being set and this
    call landing, in which case the runtime just reports no such run — not an error
    worth surfacing to the user who already sees the UI return to idle."""
    with _test_run_lock:
        tracked = _active_test_runs.get(workflow_id)
        run_id = tracked.get("run_id") if tracked else None
    if not tracked or not run_id:
        return
    from conxa_compile.runtime_tool import call_runtime_tool

    try:
        call_runtime_tool(
            tracked["runtime_dir"], "cancel_execution", {"run_id": run_id},
            conxa_dir=tracked["conxa_dir"],
        )
    except Exception:
        pass


class WorkflowsMixin:
    def cmd_create_workflow(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import get_group
        from conxa_core.storage.workflow_store import create_workflow as _create

        name = str(payload.get("name") or "").strip()
        if not name:
            raise _CommandError("invalid_input", "name is required")
        target_url = str(payload.get("target_url") or "about:blank").strip()

        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            raise _CommandError("invalid_input", "group_id is required")
        group_id = _safe_id(group_id, "group_id")
        if get_group(group_id) is None:
            raise _CommandError("group_not_found", f"No group {group_id}")

        workflow = _create(
            name=name, target_url=target_url, group_id=group_id,
            task_description=str(payload.get("task_description") or "").strip()[:300],
        )
        # The workflow's conversation starts here, whoever created it (the form, a chat draft).
        from conxa_compile.conversation.harness import log_event

        log_event(f"wf:{workflow.id}", "Workflow created", glyph="ok", sink=_event_sink(rid), subject=name)
        return {"workflow": workflow.model_dump(mode="json")}

    def cmd_list_workflows(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.workflow_store import list_workflows as _list

        workflows = _list()
        result = []
        for wf in workflows:
            data = wf.model_dump(mode="json")
            data["stage"] = derive_workflow_stage(wf)
            result.append(data)
        return {"workflows": result}

    def cmd_get_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.workflow_store import get_workflow
        from conxa_core.storage.json_store import read_skill

        # The Human Edit page also uses cmd_get_workflow(skill_id=...) for a compiled
        # skill's editor payload — that lookup is disambiguated in handlers/workflow_editor.py.
        # This one is the Workflow-entity lookup used by the Workflow page.
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        workflow = get_workflow(workflow_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", f"No workflow {workflow_id}")
        data = workflow.model_dump(mode="json")
        step_count = 0
        if workflow.skill_id:
            try:
                skill = read_skill(workflow.skill_id)
                if skill:
                    step_count = len((skill.get("skills") or [{}])[0].get("steps") or [])
            except Exception:
                pass
        data["step_count"] = step_count
        data["stage"] = derive_workflow_stage(workflow)
        return {"workflow": data}

    def cmd_get_skill_pack(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Read-only status for the Publish / Build Installer pages: is the
        workspace's shared skill package built yet, and — per workflow — is it
        ready to publish. Every workflow is returned so the Publish page can
        offer a skill picker; each workflow's own test/compile status is what
        gates *that* workflow's publish, never a sibling's."""
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.storage.workflow_store import list_workflows

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        pack = get_skill_pack(workspace_id)
        workflows = [
            {"id": wf.id, "name": wf.name, "slug": wf.slug, "skill_id": wf.skill_id,
             "signed_off": wf.signed_off, "last_test_status": wf.last_test_status}
            for wf in list_workflows(workspace_id)
        ]
        return {
            "skill_pack": pack.model_dump(mode="json") if pack is not None else None,
            "workflows": workflows,
        }

    def _resolve_workflow_by_skill_slug(self, workspace_id: str, skill_slug: str):
        from conxa_core.storage.workflow_store import list_workflows

        skill_slug = _safe_id(skill_slug, "skill_slug")
        workflows = list_workflows(workspace_id)
        for wf in workflows:
            if wf.slug == skill_slug:
                return wf
        # Stale slug from the UI before a length heal shortened wf.slug.
        if len(skill_slug) > 8 and "-" in skill_slug:
            suffix = skill_slug.rsplit("-", 1)[-1]
            if len(suffix) == 8:
                for wf in workflows:
                    if wf.id.startswith(suffix):
                        return wf
        raise _CommandError("workflow_not_found", f"No workflow with slug {skill_slug!r}")

    def _require_confirmed_entity_bindings(self, skill_id: str, skill_slug: str) -> None:
        """PROD-3 publish gate: an irreversible step whose compiler-detected entity binding
        (a repeating row/record container) was never confirmed by the vendor cannot be
        released. Mirrors patch_gate.py's irreversible_step_requires_confirmed_entity_binding
        invariant, which only runs on individual edits — this is the check that actually stops
        an unconfirmed binding reaching a customer install."""
        from conxa_core.storage.json_store import read_skill
        from conxa_compile.compiler.destructive_semantics import destructive_compiler_step
        from conxa_compile.editor.step_view import skill_step_for_destructive_check
        from conxa_compile.policy.bundle import get_policy_bundle

        skill = read_skill(skill_id)
        if not skill:
            return
        steps = (skill.get("skills") or [{}])[0].get("steps") or []
        policy = get_policy_bundle().data
        # EXEC-38: recurse into for_each loop bodies — a destructive step buried inside a loop
        # is exactly the case this gate exists to catch (the row it targets changes every
        # iteration), so a top-level-only walk would let it reach a customer install unconfirmed.
        flat: list[tuple[str, dict]] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            flat.append((f"step {i + 1}", step))
            body = ((step.get("for_each") or {}).get("steps")) if isinstance(step.get("for_each"), dict) else None
            if isinstance(body, list):
                for j, sub in enumerate(body):
                    if isinstance(sub, dict):
                        flat.append((f"step {i + 1}'s loop body, step {j + 1}", sub))
        for label, step in flat:
            view = skill_step_for_destructive_check(step)
            if not destructive_compiler_step(view, policy):
                continue
            eb = step.get("entity_binding") if isinstance(step.get("entity_binding"), dict) else {}
            if eb.get("container_selector") and not eb.get("confirmed"):
                raise _CommandError(
                    "irreversible_step_requires_confirmed_entity_binding",
                    f"Skill {skill_slug!r} {label} is a destructive action inside a "
                    "repeating row/list, but its entity binding was never confirmed in the "
                    "editor. Confirm the binding before publishing.",
                )

    def cmd_build_skill_package(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Build one workflow's compiled skill into the shared local package
        directory. Scoped to that one workflow — a sibling workflow that isn't
        compiled, edited, or tested yet never blocks this build."""
        from conxa_compile.skill_package_builder import build_skill_package

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        display_name = str(payload.get("display_name") or "").strip() or None
        version = str(payload.get("version") or "0.1.0")
        skill_slug = str(payload.get("skill_slug") or "").strip()
        only_workflow_id = None
        if skill_slug:
            only_workflow_id = self._resolve_workflow_by_skill_slug(workspace_id, skill_slug).id
        return build_skill_package(
            workspace_id,
            company_name=display_name,
            version=version,
            only_workflow_id=only_workflow_id,
            realtime_sink=_event_sink(rid),
            # A test build (for_test) isn't a release: it doesn't need the approval that the
            # publish/installer paths require, because testing comes before approving.
            require_signed_off=not bool(payload.get("for_test")),
        )

    def cmd_publish_skill_pack(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Publish ONE skill's release to Conxa Cloud — the primary, mandatory
        release-management action. Skill Pack Publishing owns version history,
        release notes, and publishing limits per skill; Build Installer (below)
        becomes a secondary, advanced action that requires a release to already
        exist.

        Skill-scoped: publishes only the one workflow named by skill_slug. A
        sibling workflow's compile/test/sign-off status is never checked and
        its files are never uploaded."""
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.workspace import workspace_dir_slug

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        pack = get_skill_pack(workspace_id)
        if pack is None:
            raise _CommandError("skill_pack_not_found", f"No skill pack built yet for workspace {workspace_id}")
        skill_slug = str(payload.get("skill_slug") or "").strip()
        if not skill_slug:
            raise _CommandError("invalid_input", "skill_slug is required")
        workflow = self._resolve_workflow_by_skill_slug(workspace_id, skill_slug)
        skill_slug = workflow.slug
        if not workflow.skill_id:
            raise _CommandError("workflow_uncompiled", f"Workflow {skill_slug!r} is not compiled yet")

        # PROD-3: patch_gate only fires on an edit — this is the check that actually blocks a
        # release. A destructive step whose compile-time-detected entity binding is still
        # unconfirmed must never reach a real customer's machine.
        self._require_confirmed_entity_bindings(workflow.skill_id, skill_slug)

        company_slug = workspace_dir_slug(workspace_id)
        version = _validate_release_version(payload.get("version"))
        release_notes = _validate_release_notes(payload.get("release_notes"))

        # Invariant: auth.json must never leave the machine, including via publish.
        from conxa_core.config import settings as _settings
        skill_pack_dir = Path(_settings.data_dir) / "skill-packs" / company_slug
        if skill_pack_dir.exists() and any(skill_pack_dir.rglob("auth.json")):
            raise _CommandError(
                "auth_file_in_build_input",
                "Refusing to publish: auth.json found under the built skill pack.",
            )

        from conxa_core.storage.group_store import get_group

        group = get_group(workflow.group_id) if workflow.group_id else None
        sink = _event_sink(rid)
        publish_info = self._publish_skill_pack(
            company_slug=company_slug,
            company_name=pack.display_name,
            skill_slug=skill_slug,
            version=version,
            release_notes=release_notes,
            group_id=workflow.group_id or "_default",
            group_name=group.name if group else "",
            workflow_name=workflow.name,
            tests_passed=workflow.last_test_status == "passed",
            recording_duration_seconds=workflow.recording_duration_seconds,
            sink=sink,
        )
        if not publish_info:
            raise _CommandError(
                "cloud_publish_failed",
                "Skill pack publish did not complete. Check your Conxa Cloud connection and sign-in.",
            )
        return publish_info

    def cmd_list_skill_pack_versions(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Release history for ONE skill on the Skill Pack Publishing / Release
        Center page — version, release notes, publish timestamp, and the
        current stable release, all scoped to this skill's own history."""
        generation, company_slug = self._resolve_release_company(payload)
        skill_slug = _safe_id(str(payload.get("skill_slug") or ""), "skill_slug")
        return self._cloud_json(
            f"/api/v1/workflows/{generation}/skill-packs/versions?skill_slug={quote(skill_slug)}"
        )

    def _resolve_release_company(self, payload: dict[str, Any]) -> tuple[str, str]:
        """Shared by every Release Center RPC handler below: resolve
        (installer_generation, company_slug) for cloud API calls. company_slug
        is now derived from workspace_id via workspace_dir_slug."""
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.workspace import workspace_dir_slug

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        pack = get_skill_pack(workspace_id)
        if pack is None:
            raise _CommandError("skill_pack_not_found", f"No skill pack built yet for workspace {workspace_id}")
        company_slug = workspace_dir_slug(workspace_id)
        return self._installer_generation(), company_slug

    def cmd_release_preview(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Everything the Release Center needs to show before the user clicks
        Publish: proposed vs. previous version, deterministic diff, artifact hash,
        and whether the candidate is a duplicate or a no-op republish. Reads the
        already-built pack exactly as ``cmd_publish_skill_pack`` would upload it —
        read-only, no mutation — so nothing shown here can drift from what
        actually gets recorded on Publish. Scoped to one skill: only that
        skill's own files are read, against only that skill's own previous
        version."""
        generation, company_slug = self._resolve_release_company(payload)
        skill_slug = _safe_id(str(payload.get("skill_slug") or ""), "skill_slug")
        files = self._collect_skill_pack_files(company_slug, skill_slug)
        body = {
            "version": str(payload.get("version") or ""),
            "skill_slug": skill_slug,
            "group_id": self._skill_group_id(company_slug, skill_slug),
            "files": files,
        }
        return self._cloud_json(
            f"/api/v1/workflows/{generation}/releases/preview", method="POST", body=body
        )

    def cmd_release_detail(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        generation, company_slug = self._resolve_release_company(payload)
        version = _safe_id(str(payload.get("version") or ""), "version")
        skill_slug = _safe_id(str(payload.get("skill_slug") or ""), "skill_slug")
        return self._cloud_json(
            f"/api/v1/workflows/{generation}/releases/{quote(version)}?skill_slug={quote(skill_slug)}"
        )

    def cmd_release_diff(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Deterministic diff for an already-published release, against the
        release immediately preceding it in this same skill's own history —
        used by the Release History detail view ("what changed in this
        version")."""
        generation, company_slug = self._resolve_release_company(payload)
        version = _safe_id(str(payload.get("version") or ""), "version")
        skill_slug = _safe_id(str(payload.get("skill_slug") or ""), "skill_slug")
        return self._cloud_json(
            f"/api/v1/workflows/{generation}/releases/{quote(version)}/diff"
            f"?skill_slug={quote(skill_slug)}"
        )

    def cmd_build_installer(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Package the already-published skill pack into a distributable NSIS
        installer. Advanced/secondary action — routine skill-pack updates never
        need this; see cmd_publish_skill_pack. Installer upload to the cloud is
        optional: a failure there is reported as a warning, not a build failure."""
        from pathlib import Path
        from services.installer_builder import build_installer
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.workspace import workspace_dir_slug

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        pack = get_skill_pack(workspace_id)
        if pack is None:
            raise _CommandError("skill_pack_not_found", f"No skill pack built yet for workspace {workspace_id}")
        company_slug = workspace_dir_slug(workspace_id)
        version = _validate_release_version(payload.get("version"))
        release_notes = _validate_release_notes(payload.get("release_notes"))

        # Invariant: auth.json must never enter the installer input. Captured
        # auth lives under each workflow's own state dir, but the installer stages
        # only the built skill pack.
        from conxa_core.config import settings as _settings
        skill_pack_dir = Path(_settings.data_dir) / "skill-packs" / company_slug
        if skill_pack_dir.exists() and any(skill_pack_dir.rglob("auth.json")):
            raise _CommandError(
                "auth_file_in_build_input",
                "Refusing to build: auth.json found under the built skill pack.",
            )

        pack_path = skill_pack_dir / "pack.json"
        published = pack_path.is_file() and bool(json.loads(pack_path.read_text(encoding="utf-8")).get("sync_token"))
        if not published:
            raise _CommandError(
                "skill_pack_not_published",
                "Publish a skill pack release before building an installer.",
            )

        installer_name = str(payload.get("installer_name") or "").strip() or None
        if installer_name and self._auto_publish_enabled():
            # Best-effort: persists the domain the user typed here so the Cloud
            # Dashboard's own installer naming (publish_routes.py's
            # get_installer_domain) stays consistent with what this local build
            # just used — never blocks the build if the cloud is unreachable.
            try:
                self._cloud_json(
                    "/api/v1/entitlements/installer-domain",
                    method="POST",
                    body={"domain": installer_name},
                )
            except Exception:
                pass
        logo_path = str(payload.get("logo_path") or "").strip() or None
        if logo_path:
            # Custom installer icons are a paid-plan capability (see docs/PRD.md
            # §11) — Build Studio has no local plan awareness, so ask the cloud.
            # Any doubt (call fails, plan missing) defaults to Free, i.e. no icon.
            try:
                plan = str(self._cloud_json("/api/v1/entitlements/current").get("plan") or "free")
            except Exception:
                plan = "free"
            if plan == "free":
                logo_path = None
        sink = _event_sink(rid)
        result = build_installer(
            workspace_id,
            company_slug=company_slug,
            logo_path=logo_path,
            version=version,
            release_notes=release_notes,
            installer_name=installer_name,
            realtime_sink=sink,
        )
        result["installed_runtime_path"] = (
            r"C:\Program Files\Conxa\runtime\conxa-runtime.exe"
            if sys.platform == "win32"
            else str(Path.home() / ".conxa" / "runtime" / "runtime")
        )
        sink(
            {
                "kind": "installer_build",
                "message": (
                    f"Post-install check: restart Claude, confirm Conxa MCP tools are available, "
                    f"run list_skills, then execute a skill. Runtime path: {result['installed_runtime_path']}"
                ),
            }
        )
        try:
            result = self._upload_installer_for_download(
                company_slug=company_slug,
                result=result,
                release_notes=release_notes,
                sink=sink,
            )
        except _CommandError as exc:
            # Installer upload is optional (requirement: Conxa will host/build installers
            # centrally). A failed or skipped upload never fails the build — the local
            # installer and the already-published skill pack are unaffected either way.
            result = dict(result)
            result["cloud_upload_error"] = exc.code
            result["cloud_upload_error_message"] = exc.message
            sink({
                "kind": "installer_build",
                "message": f"Installer upload skipped: {exc.message}",
                "warning": True,
            })
        return result

    def _answer_ai_review(self, review: dict[str, Any], content: list[Any]) -> dict[str, Any]:
        """EXEC-13: Studio's stand-in answerer for an ai_review checkpoint when no MCP agent is
        present to answer it (see cmd_test_workflow's park/resume loop). Metered exactly like
        every other Human Edit vision call — `usage_class="ai_credits"` routes through
        `ensure_ai_credits_available`/`record_llm_usage` on the cloud proxy, no new entitlement
        surface (conxa-cloud/backend/app/api/llm_proxy_routes.py::_meter_and_call).

        Tries the text-model route first (`call_llm`'s default for a task not in VISION_TASKS —
        deliberate: multimodal providers accept the image on either route, only the *model
        selected* differs) and falls back once to the vision route directly on a provider-side
        failure. Raises RuntimeError on any failure — the caller (cmd_test_workflow) already
        catches RuntimeError and surfaces it as a failed test.
        """
        from conxa_compile.llm.client import call_llm
        from conxa_core import llm as core_llm
        from conxa_core.config import settings as _settings
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, QuotaExceeded

        self._install_proxy_router(usage_class="ai_credits")

        context_text, image = _review_context_from_content(content)
        prompt = str(review.get("prompt") or "")
        output_schema = review.get("output_schema")
        schema_note = (
            f"\n\nYour answer must be a JSON object matching this schema:\n{json.dumps(output_schema)}"
            if output_schema
            else "\n\nReturn a JSON object with a single \"answer\" string field."
        )
        payload: dict[str, Any] = {"user_text": f"Question: {prompt}{schema_note}\n\n{context_text}"}
        if image:
            payload["image_base64"], payload["image_mime"] = image

        timeout_ms = _settings.llm_vision_timeout_ms
        try:
            data = call_llm("ai_review", payload, timeout_ms)
        except (QuotaExceeded, EntitlementBlocked):
            # Retrying via the vision route hits the same quota/entitlement wall — propagate
            # immediately rather than burning a second (also-blocked) call.
            raise
        except CloudUnreachable:
            data = core_llm.get_router().route_vision("ai_review", payload, timeout_ms)

        if not isinstance(data, dict):
            raise RuntimeError("AI review answerer returned no usable response.")
        errors = _validate_ai_review_answer(data, output_schema if isinstance(output_schema, dict) else None)
        if errors:
            raise RuntimeError(f"AI review answer did not match the required output: {'; '.join(errors)}")
        return data

    def cmd_test_workflow(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Run a workflow end-to-end against the local Conxa runtime.

        Validates the workflow is compiled, stages the workspace's built skill pack
        and this workflow's captured auth session into a local test runtime, then
        calls the shared MCP runtime's ``execute_skill`` tool over stdio.
        """
        from conxa_compile.conxa_runtime import (
            RuntimeToolError,
            _bootstrap_app_dir,
            ensure_chromium_installed,
            ensure_test_sandbox,
            resolve_runtime_dir,
            sync_skill_pack,
        )
        from conxa_compile.runtime_tool import call_runtime_tool
        from conxa_core.config import settings as _settings
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.storage.workflow_store import (
            get_workflow,
            set_workflow_test_error,
            set_workflow_test_result,
        )

        workspace_id = str(payload.get("workspace_id") or "").strip() or LOCAL_WORKSPACE_ID
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        # BUILD-40: options for the conversation's test run. All optional and all off by default, so
        # every older caller (the group page, the copilot's verify) behaves exactly as before.
        #   guard        — pause before the first step that can't be undone (RT-9) instead of running it
        #   _phase_hook  — called with each runtime test_phase entry (step starts, with names)
        #   _guard_hook  — called when the guard pauses; returns "run_rest" or "end_here"
        #   _review_hook — called with a confirm-only ai_review request; returns True (yes) or False (no)
        # The hooks are Python callables an in-process caller passes; a JSON caller cannot supply them.
        guard_requested = bool(payload.get("guard"))
        _hook = lambda name: payload.get(name) if callable(payload.get(name)) else None  # noqa: E731
        phase_hook, guard_hook, review_hook = _hook("_phase_hook"), _hook("_guard_hook"), _hook("_review_hook")
        fork_hook = _hook("_fork_hook")  # called with a popup-decision question; returns a choice id

        workflow = get_workflow(workflow_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", f"No workflow {workflow_id}")
        if not workflow.skill_id:
            raise _CommandError("workflow_not_compiled", "Compile this workflow before testing.")
        pack = get_skill_pack(workspace_id)
        if pack is None or pack.build is None:
            raise _CommandError("skill_package_not_built", "Build the skill package before testing its workflows.")
        # Backend mirror of the UI's isStaleTest guard (PluginWorkflowTests.tsx) — edited_at is
        # bumped on every skill.json write (json_store.write_skill → invalidate_workflow_test_by_skill),
        # so this catches edits made after the last build regardless of which caller skipped the
        # UI check (a state race, a non-UI caller, etc).
        if workflow.edited_at is not None and workflow.edited_at > pack.build.last_built_at:
            raise _CommandError(
                "workflow_stale",
                "This workflow was edited after the last build. Rebuild the skill package before testing.",
            )

        sink = _event_sink(rid)
        # Diagnostic timing: each _mark() call reports elapsed time since this test started, so a
        # slow "Run Test" click can be broken down into real numbers (sandbox stage vs. skill-pack
        # sync vs. the runtime's own Chromium-launch phases) instead of one opaque wait. [runtime]
        # marks relay test_phase log entries server.js emits — see runtime_tool.py's phase_sink.
        _t0 = time.monotonic()

        def _mark(message: str) -> None:
            sink({"kind": "workflow_test", "message": f"{message} (t+{time.monotonic() - _t0:.1f}s)"})

        def _phase_sink(entry: dict[str, Any]) -> None:
            if phase_hook is not None:
                try:
                    phase_hook(entry)
                except Exception:  # noqa: BLE001 — a progress mirror must never break the run
                    pass
            _mark(f"[runtime] {entry.get('phase', '?')} (+{entry.get('ms', 0) / 1000:.1f}s in-runtime)")
            # EXEC-35: run_id first appears here (relayed from server.js's test_phase log lines),
            # seconds before call_runtime_tool returns — capture it so cmd_cancel_test_workflow has
            # something to target, and honor a cancel click that arrived before it was known.
            run_id = entry.get("run_id")
            if not run_id:
                return
            with _test_run_lock:
                tracked = _active_test_runs.get(workflow_id)
                if tracked is None or tracked.get("run_id") is not None:
                    return
                tracked["run_id"] = run_id
                pending_cancel = tracked.get("cancel_requested", False)
            if pending_cancel:
                _cancel_active_test_run(workflow_id)

        _mark(f"Preparing test for {workflow.name!r}…")

        # BUILD-26 (a2): captured from _phase_sink as soon as the run's own test_phase log line
        # reports it — the same run_id the runtime files failure evidence under
        # (runs/{run_id}/_evidence/, see failure_response.js) and conxa_compile/editor/evidence.py
        # resolves. Persisted below regardless of pass/fail/error so a re-opened Human Edit page
        # can still point the copilot at the run that just happened.
        _test_run_id: str | None = None

        is_frozen = getattr(sys, "frozen", False)

        runtime_dir = resolve_runtime_dir()
        if runtime_dir is None:
            message = (
                "Conxa runtime not found. Run dependency bootstrap so Build Studio downloads it."
                if is_frozen
                else "Local dev runtime not built yet. Run scripts\\build-runtime-local.ps1 first "
                "(writes into deps\\conxa-runtime\\, same place a download would)."
            )
            raise _CommandError("runtime_not_found", message)

        from conxa_core.workspace import workspace_dir_slug
        company = workspace_dir_slug(workspace_id)

        data_dir = Path(_settings.data_dir)
        source_dir = data_dir / "skill-packs" / company
        if not source_dir.is_dir():
            raise _CommandError(
                "skill_pack_not_built",
                f"Built skill pack not found: skill-packs/{company}. Run Build Skill Package again.",
            )
        # Same idea as the workflow_stale guard above, for the group's sign-in setup: the sandbox
        # tests the BUILT pack, so a group changed since (a re-Connected app, a learned sign-in
        # definition) must be rebuilt in first — see _sign_in_setup_drift.
        try:
            built_pack_json = json.loads((source_dir / "pack.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            built_pack_json = {}
        drift = _sign_in_setup_drift(workflow, built_pack_json)
        if drift:
            raise _CommandError("sign_in_setup_stale", drift)

        try:
            # ── Assemble (or refresh) the customer-faithful sandbox ───────────
            # sandbox/.conxa/ mirrors ~/.conxa/ on a real install; sandbox/data/
            # mirrors ~/AppData/Roaming/Conxa. The sandbox is persistent: the exe
            # and conxa-app are only re-staged when a new version appears in deps/
            # (a download in Production; scripts/build-runtime-local.ps1 /
            # build-app-local.ps1 writing there directly in Dev).
            _mark("Preparing test sandbox…")
            app_dir = _bootstrap_app_dir()
            conxa_dir, test_data_dir = ensure_test_sandbox(runtime_dir, app_dir)

            with _test_run_lock:
                _active_test_runs[workflow_id] = {
                    "run_id": None,
                    "cancel_requested": False,
                    "runtime_dir": runtime_dir,
                    "conxa_dir": conxa_dir,
                }

            if not (conxa_dir / "conxa-app" / "current" / "server.js").is_file():
                message = (
                    "Conxa app layer not found. Run dependency bootstrap so Build Studio downloads it."
                    if is_frozen
                    else "Local dev app layer not built yet. Run scripts\\build-app-local.ps1 first "
                    "(writes into deps\\conxa-app\\, same place a download would)."
                )
                raise _CommandError("runtime_app_not_built", message)

            _mark("Staging skill pack for the runtime…")
            sync_skill_pack(company, source_dir, conxa_dir, data_dir=test_data_dir)
            _stage_runtime_auth(workflow, company, test_data_dir)

            # Chromium is junctioned/symlinked into conxa_dir by ensure_test_sandbox.
            # Still call ensure_chromium_installed to download on first use; after that
            # it's a no-op (fast binary check). Always use the shared deps/chromium so
            # both frozen and dev point at the same managed revision.
            browsers_dir = _bootstrap_pkg.chromium_dir()
            ensure_chromium_installed(
                browsers_dir,
                runtime_dir,
                log_sink=lambda msg: _mark(msg),
            )

            _mark(f"Running {workflow.name!r}…")
            run_args: dict[str, Any] = {
                "skill": workflow.slug,
                "company": company,
                "inputs": inputs,
                "watch": not bool(payload.get("headless")),
                # A missing sign-in must fail this test now, not detach and start a run later that
                # nothing is waiting on (AUTH-4) — Studio's own group-auth gate has already run.
                "wait_for_auth": False,
            }
            previewed = False
            if guard_requested:
                if _app_layer_supports(conxa_dir, "test_guard"):
                    run_args["_pause_before_irreversible"] = True
                else:
                    # An engine that predates the guard would ignore the argument and run the
                    # irreversible steps for real. Preview instead: every step is resolved, the ones
                    # that can't be undone are checked but never acted on.
                    run_args["dry_run"] = True
                    previewed = True
                    _mark("This engine predates the test guard — previewing instead: steps that can't be undone are checked but not run.")
            result = call_runtime_tool(
                runtime_dir, "execute_skill", run_args,
                conxa_dir=conxa_dir,
                env={"CONXA_DATA_DIR": str(test_data_dir)},
                phase_sink=_phase_sink,
            )
            # EXEC-13: no MCP agent is present in a Studio test run to answer an ai_review
            # checkpoint's park/resume request — Studio answers it itself through the metered
            # cloud proxy and resumes, exactly like a real agent would. Bounded (not `while True`)
            # so a misbehaving answerer fails the test loudly instead of looping forever holding
            # the parked browser/host-lock open.
            result, stopped_at_guard = _drive_runtime_pauses(
                result, run_args,
                call=lambda args: call_runtime_tool(
                    runtime_dir, "execute_skill", args, conxa_dir=conxa_dir,
                    env={"CONXA_DATA_DIR": str(test_data_dir)}, phase_sink=_phase_sink),
                cancel_run=lambda run_id: call_runtime_tool(
                    runtime_dir, "cancel_execution", {"run_id": run_id}, conxa_dir=conxa_dir,
                    env={"CONXA_DATA_DIR": str(test_data_dir)}),
                answer_review=self._answer_ai_review, guard_hook=guard_hook, review_hook=review_hook,
                guard_requested=guard_requested, previewed=previewed, mark=_mark, fork_hook=fork_hook,
            )
            _mark("Runtime call finished")
        except (RuntimeToolError, RuntimeError) as exc:
            # Read before `finally` pops the tracked entry below.
            with _test_run_lock:
                tracked = _active_test_runs.get(workflow_id)
                _test_run_id = tracked.get("run_id") if tracked else None
            message = str(exc)
            set_workflow_test_error(
                workflow_id, message,
                inputs=_redact_sensitive_test_inputs(workflow.skill_id, inputs),
                run_id=_test_run_id,
            )
            raise _CommandError("workflow_test_failed", message, run_id=_test_run_id) from exc
        finally:
            with _test_run_lock:
                tracked = _active_test_runs.pop(workflow_id, None)
                if _test_run_id is None and tracked is not None:
                    _test_run_id = tracked.get("run_id")

        if stopped_at_guard is not None:
            # The person ended the test before a step that can't be undone (or declined its
            # confirmation). Nothing real happened past this point, and it is NOT a pass: the last
            # real result is left untouched, like a cancel.
            sink({"kind": "workflow_test", "message": f"Test ended before {stopped_at_guard.get('step_intent') or 'a step that can’t be undone'}."})
            return {
                "status": "stopped_at_guard", "message": "Ended before a step that can't be undone.",
                "company": company, "skill": workflow.slug, "run_id": _test_run_id,
                "stopped_at": stopped_at_guard["step_index"], "stopped_intent": stopped_at_guard.get("step_intent"),
                "declined": bool(stopped_at_guard.get("declined")),
            }

        message = _runtime_result_text(result)
        expired = _extract_session_expired(result)
        if expired is not None:
            # Paused, not failed: nothing in the workflow is wrong, so the last real test result stays
            # as it was and nothing downstream (auto-fix) tries to "repair" a login.
            sink({"kind": "workflow_test", "message": message})
            return {"status": "needs_sign_in", "message": message, "company": company, "skill": workflow.slug,
                    "run_id": _test_run_id, "failed_at": expired["step_index"]}
        if previewed and message.startswith("Dry run complete"):
            # A preview proves every target is still findable, but it never ran the irreversible
            # steps, so it must not count as a passing test.
            sink({"kind": "workflow_test", "message": message})
            return {"status": "previewed", "message": message, "company": company, "skill": workflow.slug, "run_id": _test_run_id}
        if not message.startswith("Done."):
            failure = message or "Runtime test failed without a result message."
            # EXEC-35: a user-initiated cancel (see cmd_cancel_test_workflow) isn't a real
            # pass/fail signal — don't clobber the last real test result with it.
            if failure.startswith("Execution cancelled"):
                sink({"kind": "workflow_test", "message": failure})
                return {
                    "status": "cancelled", "message": failure, "company": company,
                    "skill": workflow.slug, "run_id": _test_run_id,
                }
            set_workflow_test_error(
                workflow_id, failure,
                inputs=_redact_sensitive_test_inputs(workflow.skill_id, inputs),
                run_id=_test_run_id,
            )
            raise _CommandError("workflow_test_failed", failure, run_id=_test_run_id)

        set_workflow_test_result(
            workflow_id, status="passed",
            inputs=_redact_sensitive_test_inputs(workflow.skill_id, inputs),
            run_id=_test_run_id,
        )
        sink({"kind": "workflow_test", "message": message})
        return {
            "status": "passed", "message": message, "company": company,
            "skill": workflow.slug, "run_id": _test_run_id,
        }

    def cmd_cancel_test_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        """Cancel an in-progress Run Test (EXEC-35). A workflow with no active test is a
        benign no-op, matching cmd_cancel_recording's tolerance of a stale/already-finished id."""
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        with _test_run_lock:
            tracked = _active_test_runs.get(workflow_id)
            if tracked is None:
                return {"ok": True}
            tracked["cancel_requested"] = True
            run_id = tracked.get("run_id")
        if run_id:
            _cancel_active_test_run(workflow_id)
        return {"ok": True}

    def cmd_get_usage(self, _payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        import urllib.request

        try:
            entitlements = self._cloud_json("/api/v1/entitlements/current")
        except _CommandError as exc:
            entitlements = {
                "entitlements_unavailable": True,
                "error": {"code": exc.code, "message": exc.message},
            }

        try:
            req = urllib.request.Request(f"{self._cloud_api}/api/v1/llm/proxy/usage")
            req.add_header("X-Conxa-Client", os.environ.get("CONXA_PROXY_CLIENT", "build-studio"))
            req.add_header("Authorization", f"Bearer {self._auth_service().get_token()}")
            with urllib.request.urlopen(req, timeout=30) as resp:
                legacy = json.loads(resp.read().decode("utf-8"))
        except Exception:
            legacy = {}
        if "meters" in entitlements:
            return {**entitlements, "legacy_llm_usage": legacy}
        return {**entitlements, "legacy_llm_usage": legacy}

    # ─── helpers ────────────────────────────────────────────────────────────

    def cmd_delete_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.workflow_store import delete_workflow

        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        return {"deleted": bool(delete_workflow(workflow_id))}

    def cmd_update_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.group_store import get_group
        from conxa_core.storage.workflow_store import get_workflow, save_workflow

        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        workflow = get_workflow(workflow_id)
        if workflow is None:
            raise _CommandError("workflow_not_found", f"No workflow {workflow_id}")
        if "skill_id" in payload:
            workflow.skill_id = payload["skill_id"]
        if "status" in payload:
            workflow.status = payload["status"]
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise _CommandError("invalid_input", "name cannot be empty")
            workflow.name = name  # slug is frozen at creation — it's the runtime skill key
        if "group_id" in payload:
            group_id = _safe_id(payload["group_id"], "group_id")
            group = get_group(group_id)
            if group is None:
                raise _CommandError("group_not_found", f"No group {group_id}")
            workflow.group_id = group_id
            workflow.status = "ready" if all(a.captured_at for a in group.apps) and group.apps else (
                "needs_auth" if group.apps else workflow.status
            )
        save_workflow(workflow)
        return {"workflow_id": workflow_id, "skill_id": workflow.skill_id, "status": workflow.status, "group_id": workflow.group_id, "name": workflow.name}

    # ─── recording status ────────────────────────────────────────────────────

    def cmd_get_recording_status(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        session_id = _safe_id(payload.get("session_id"), "session_id")
        sess = _recorder_registry.get(session_id)
        if sess is None:
            raise _CommandError("session_not_found", f"No session {session_id}")
        return sess.status()

    # ─── skill workflow (human edit) ─────────────────────────────────────────

    def cmd_get_skill_workflow(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from pathlib import Path
        from conxa_core.config import settings
        from conxa_core.storage.json_store import read_skill
        from conxa_compile.editor.workflow_dto import build_workflow_response

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        document = read_skill(skill_id)
        if document is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        asset_base_url = f"file://{Path(settings.data_dir) / 'skills' / skill_id / 'assets'}"
        return build_workflow_response(skill_id, document, asset_base_url=asset_base_url).model_dump(mode="json")
