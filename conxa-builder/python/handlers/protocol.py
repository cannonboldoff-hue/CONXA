"""Shared substrate for the JSON-RPC command handlers in handlers/*.py.

Split out of backend.py so each handlers/<domain>.py mixin can import the
protocol error type, event-emission helpers, and small cross-domain
validation helpers without importing backend.py itself (which would be
circular, since backend.py composes Backend from these mixins).
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


class _CommandError(Exception):
    def __init__(self, code: str, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # Optional (BUILD-26 stage e): cmd_test_workflow's workflow_test_failed error carries the
        # run id the failure was filed under, so cmd_copilot_verify can read the failing step's
        # evidence without re-deriving it from the (deliberately stale) Workflow.last_test_run_id.
        self.run_id = run_id


def _safe_id(value: object, field: str) -> str:
    from services.validation import InvalidInput, safe_identifier

    try:
        return safe_identifier(value, field)
    except InvalidInput as exc:
        raise _CommandError("invalid_input", str(exc)) from exc


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base, preserving unpatched nested keys."""
    result = dict(base)
    for k, v in patch.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# --- stdout protocol ---------------------------------------------------------

_stdout_lock = threading.Lock()


def _write(obj: dict[str, Any]) -> None:
    with _stdout_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def _emit_event(req_id: str | None, **fields: Any) -> None:
    _write({"type": "event", "id": req_id, **fields})


# BUILD-39: a command that wants to watch another command's progress (the compile card mirrors
# cmd_compile's own events) registers an observer under that command's request id. Both the
# per-command event sink below and backend.py's compile sub-step sink feed it.
JOB_OBSERVERS: dict[str, Callable[[dict[str, Any]], None]] = {}


def notify_observer(req_id: str | None, entry: dict[str, Any]) -> None:
    obs = JOB_OBSERVERS.get(req_id or "")
    if obs is not None:
        try:
            obs(entry)
        except Exception:  # noqa: BLE001 — a progress mirror must never break the command it watches
            pass


def _event_sink(req_id: str | None) -> Callable[[dict[str, Any]], None]:
    def sink(entry: dict[str, Any]) -> None:
        _emit_event(req_id, **entry)
        notify_observer(req_id, entry)
    return sink


_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _validate_release_version(value: Any) -> str:
    version = str(value or "").strip()
    if not _SEMVER_RE.fullmatch(version):
        raise _CommandError("invalid_release_version", "Installer version must look like 1.2.3 or 1.2.3-beta.1.")
    return version


def _validate_release_notes(value: Any) -> str:
    notes = str(value or "").strip()
    if not notes:
        raise _CommandError("invalid_release_notes", "Release message is required.")
    if len(notes) > 2000:
        raise _CommandError("invalid_release_notes", "Release message must be 2000 characters or fewer.")
    return notes


def _is_rejected_protected_url(url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return True
    parsed = urlparse(value)
    if parsed.scheme in {"", "about", "data", "blob", "file"}:
        return True
    haystack = " ".join([parsed.path, parsed.query, parsed.fragment]).lower()
    return any(token in haystack for token in ("login", "signin", "sign-in", "auth", "callback", "oauth"))


def _runtime_result_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _sign_in_setup_drift(workflow: Any, pack_json: dict[str, Any]) -> str:
    """Why the BUILT pack's sign-in setup for this workflow's group no longer matches the live group,
    or "" when they agree.

    Run Test executes the built pack (the runtime resolves apps from its ``groups`` block) but
    ``_stage_runtime_auth`` stages the LIVE group's sessions under the live app ids. When the group
    changed after the build — an app re-added under a new id, a sign-in definition learned — the two
    id sets disagree, the runtime never sees the fresh session, and it silently reads a stale orphan
    instead. Refusing until the pack is rebuilt keeps the sandbox testing exactly what ships.
    """
    from conxa_core.storage.group_store import get_group

    group_id = str(getattr(workflow, "group_id", "") or "")
    group = get_group(group_id) if group_id else None
    if group is None:
        return ""
    built_group = next((g for g in pack_json.get("groups") or [] if g.get("id") == group_id), {})
    built = {a.get("id"): a for a in built_group.get("apps") or []}

    changed: list[str] = []
    for app in group.apps:
        b = built.get(app.id)
        if b is None or (b.get("login_url"), b.get("success_url"), b.get("auth_definition")) != (
            app.login_url, app.success_url, getattr(app, "auth_definition", None),
        ):
            changed.append(app.name)
    live_ids = {a.id for a in group.apps}
    changed.extend(str(app_id) for app_id in built if app_id not in live_ids)
    if not changed:
        return ""
    return (
        f"Sign-in setup for the {group.name} group changed after the last build ({', '.join(changed)}). "
        "Rebuild the skill package before testing."
    )


def _stage_runtime_auth(workflow: Any, company: str, data_dir: Path) -> None:
    """Copy every authenticated app in the workflow's group into the test
    sandbox, one file per app (``{company}__{app_id}_raw_state.json``). The
    runtime resolves the group itself from the built pack.json's ``groups``
    block (staged by sync_skill_pack) — see runtime/app/browser.js.
    """
    from conxa_core.storage.group_store import get_group

    group_id = str(getattr(workflow, "group_id", "") or "")
    if not group_id:
        return
    group = get_group(group_id)
    if group is None or not group.apps:
        return

    import shutil

    sessions_dir = data_dir / "cache" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    for app in group.apps:
        state_path = Path(str(app.storage_state_path or ""))
        if state_path.is_file():
            shutil.copy2(state_path, sessions_dir / f"{company}__{app.id}_raw_state.json")


def _skill_response(
    skill_id: str,
    doc: dict[str, Any],
    revalidation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from pathlib import Path
    from conxa_core.config import settings
    from conxa_compile.editor.workflow_dto import build_workflow_response

    asset_base_url = f"file://{Path(settings.data_dir) / 'skills' / skill_id / 'assets'}"
    workflow = build_workflow_response(skill_id, doc, asset_base_url=asset_base_url)
    return {
        "skill_id": skill_id,
        "meta": dict(doc.get("meta") or {}),
        "revalidation": revalidation or {},
        "workflow": workflow.model_dump(mode="json"),
    }
