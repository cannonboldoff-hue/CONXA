"""Download→upload binding: record provenance at pick time, compile to runtime placeholders."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

_RUNTIME_ONLY_PLACEHOLDER_RE = re.compile(
    r"^(downloaded_file(_\d+)?(_dir)?|downloaded_files_dir)$"
)


def is_recorded_file_metadata(value: str) -> bool:
    """True for bridge.js ``[{name, size, type}, ...]`` — not a path list."""
    if not value.startswith("["):
        return False
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not parsed:
            return False
        return isinstance(parsed[0], dict) and "name" in parsed[0]
    except Exception:  # noqa: BLE001
        return False


def upload_recorded_filenames(value: str) -> list[str]:
    if not is_recorded_file_metadata(value):
        return []
    try:
        parsed = json.loads(value)
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for item in parsed:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def classify_file_pick(
    paths: list[str],
    downloads_dir: Path | None,
    zip_downloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Classify Studio picker result against session download/extract folders."""
    if not paths:
        return {}
    resolved = [Path(p).resolve() for p in paths]
    names = [p.name for p in resolved]

    allowed_roots: list[Path] = []
    if downloads_dir is not None:
        allowed_roots.append(downloads_dir.resolve())

    for info in zip_downloads.values():
        extract_dir = info.get("extract_dir")
        if extract_dir is not None:
            allowed_roots.append(Path(extract_dir).resolve())
        zip_path = info.get("zip_path")
        if zip_path is not None:
            allowed_roots.append(Path(zip_path).resolve())

    def under_allowed(path: Path) -> bool:
        for root in allowed_roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    left_context = any(not under_allowed(p) for p in resolved)

    for zip_name, info in zip_downloads.items():
        zip_path = Path(info["zip_path"]).resolve()
        if all(p == zip_path for p in resolved):
            return {
                "source": "zip",
                "names": names,
                "download_filename": zip_name,
                "left_context": left_context,
            }

    for zip_name, info in zip_downloads.items():
        extract_dir = Path(info["extract_dir"]).resolve()
        members = set(info.get("members") or [])
        if members and all(p.parent == extract_dir for p in resolved):
            member_names = [p.name for p in resolved]
            if all(n in members for n in member_names):
                return {
                    "source": "zip_extract",
                    "names": member_names,
                    "download_filename": zip_name,
                    "left_context": left_context,
                }

    if downloads_dir is not None:
        dl = downloads_dir.resolve()
        if all(p.parent == dl for p in resolved):
            return {
                "source": "download",
                "names": names,
                "left_context": False,
            }

    if left_context or not under_allowed(resolved[0]):
        return {
            "source": "external",
            "names": names,
            "left_context": True,
        }

    return {
        "source": "external",
        "names": names,
        "left_context": left_context,
    }


def parse_download_suggested_filename(raw_value: str) -> str:
    """Extract ``suggested_filename`` from a ``download_observed`` step's raw JSON value
    (``{"url": ..., "suggested_filename": ...}``) — the same shape ``_BindingState.add_download``
    parses. Pulled out standalone so `compiler/loop_suggestion.py`'s detector can identify a
    download's filename without duplicating this parsing."""
    try:
        payload = json.loads(raw_value) if raw_value else {}
    except (TypeError, ValueError):
        return ""
    return str((payload or {}).get("suggested_filename") or "").strip()


class _BindingState:
    def __init__(self) -> None:
        self.total_downloads = 0
        self.pending_by_name: dict[str, list[int]] = {}
        self.pending_zip_members: dict[int, Counter[str]] = {}
        self.zip_by_name: dict[str, int] = {}

    def _dir_placeholder(self, order: int) -> str:
        if self.total_downloads == 1:
            return "downloaded_file_dir"
        return f"downloaded_file_{order}_dir"

    def add_download(self, raw_value: str) -> None:
        try:
            payload = json.loads(raw_value) if raw_value else {}
        except (TypeError, ValueError):
            payload = {}
        # A recorded download that failed/was cancelled left no file, so no later upload may bind to
        # it. Payloads from before `status` existed have none and count as completed.
        # shortcut: the runtime still numbers downloaded_file_N by real downloads, so a replay that
        # succeeds where the recording failed shifts later indices by one; upgrade by recording the
        # failed step's number in the payload if that ever bites.
        if isinstance(payload, dict) and payload.get("status", "completed") != "completed":
            return
        self.total_downloads += 1
        order = self.total_downloads
        name = parse_download_suggested_filename(raw_value)
        if name:
            self.pending_by_name.setdefault(name, []).append(order)
            members = payload.get("zip_members") if isinstance(payload, dict) else None
            if members:
                member_list = [str(m).strip() for m in members if str(m).strip()]
                if member_list:
                    self.pending_zip_members[order] = Counter(member_list)
                    self.zip_by_name[name] = order

    def bind_zip_order(self, zip_filename: str) -> int | None:
        return self.zip_by_name.get(zip_filename)

    def bind_value_for_names(self, names: list[str]) -> str | None:
        if not names:
            return None
        single_download = self.total_downloads == 1

        if len(names) == 1:
            name = names[0]
            queue = self.pending_by_name.get(name)
            if queue:
                matched_order = queue.pop(0)
                return "{{downloaded_file}}" if single_download else f"{{{{downloaded_file_{matched_order}}}}}"
            for zip_order, remaining in self.pending_zip_members.items():
                if remaining.get(name, 0) > 0:
                    remaining[name] -= 1
                    ph = self._dir_placeholder(zip_order)
                    return f"{{{{{ph}}}}}/{name}"
            return None

        needed = Counter(names)
        if all(len(self.pending_by_name.get(name, [])) >= count for name, count in needed.items()):
            for name in names:
                self.pending_by_name[name].pop(0)
            return "{{downloaded_files_dir}}"

        for zip_order, remaining in self.pending_zip_members.items():
            if needed == remaining:
                remaining.clear()
                ph = self._dir_placeholder(zip_order)
                return f"{{{{{ph}}}}}"
            if needed <= remaining and needed != remaining:
                ph = self._dir_placeholder(zip_order)
                paths = [f"{{{{{ph}}}}}/{name}" for name in names]
                for name in names:
                    remaining[name] -= 1
                return json.dumps(paths)

        return None

    def bind_from_file_pick(self, file_pick: dict[str, Any]) -> str | None:
        if not file_pick:
            return None
        if file_pick.get("left_context") or file_pick.get("source") == "external":
            return None
        source = str(file_pick.get("source") or "")
        names = [str(n).strip() for n in (file_pick.get("names") or []) if str(n).strip()]
        if not names:
            return None
        zip_name = str(file_pick.get("download_filename") or "").strip()

        if source == "zip" and len(names) == 1:
            queue = self.pending_by_name.get(names[0]) or self.pending_by_name.get(zip_name)
            if queue:
                matched_order = queue.pop(0)
                if self.total_downloads == 1:
                    return "{{downloaded_file}}"
                return f"{{{{downloaded_file_{matched_order}}}}}"
            return None

        if source in {"zip_extract", "download"}:
            return self.bind_value_for_names(names)

        return None


def bind_upload_step_value(
    step: dict[str, Any],
    *,
    file_pick: dict[str, Any] | None,
    raw_metadata_value: str,
    state: _BindingState,
) -> bool:
    """Rewrite step value/input_binding when a same-run download match exists."""
    bound: str | None = None
    if file_pick:
        bound = state.bind_from_file_pick(file_pick)
    if bound is None and raw_metadata_value:
        names = upload_recorded_filenames(raw_metadata_value)
        if names:
            bound = state.bind_value_for_names(names)

    if not bound:
        return False

    step["value"] = bound
    step["input_binding"] = None
    return True


def apply_bindings_to_export_steps(export_steps: list[dict[str, Any]]) -> None:
    """Mutate export/saved-skill steps (download_observed + upload/upload_intent)."""
    state = _BindingState()
    for step in export_steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "")
        if action == "download_observed":
            raw_value = str(step.get("value") or "")
            if not raw_value:
                nested = step.get("action")
                if isinstance(nested, dict):
                    raw_value = str(nested.get("value") or "")
            state.add_download(raw_value)
        elif action in {"upload", "upload_intent"}:
            raw_value = str(step.get("value") or "").strip()
            metadata = raw_value if is_recorded_file_metadata(raw_value) else ""
            extras = step.get("extras") if isinstance(step.get("extras"), dict) else {}
            file_pick = extras.get("file_pick") if isinstance(extras.get("file_pick"), dict) else None
            bind_upload_step_value(
                step,
                file_pick=file_pick,
                raw_metadata_value=metadata,
                state=state,
            )


def _step_action_str(step: Any) -> str:
    """SkillStep.action is ``str | dict`` — a scroll step's action is
    ``{"action": "scroll", "delta": ...}``, not a bare string. Normalize the same
    way build.py's other step.action readers do before any ``in {...}`` check."""
    action = getattr(step, "action", None)
    return action if isinstance(action, str) else str((action or {}).get("action") or "")


def apply_bindings_to_compiled_steps(steps: list[Any], events: list[dict[str, Any]]) -> None:
    """After SkillStep list is built, bind uploads using parallel upload events."""
    upload_events = [
        e
        for e in events
        if str((e.get("action") or {}).get("action") or "") in {"upload", "upload_intent"}
    ]
    upload_steps = [s for s in steps if _step_action_str(s) in {"upload", "upload_intent"}]

    if not upload_steps:
        return

    state = _BindingState()
    event_idx = 0
    for step in steps:
        action = _step_action_str(step)
        if action == "download_observed":
            state.add_download(str(step.value or ""))
        elif action in {"upload", "upload_intent"}:
            ev = upload_events[event_idx] if event_idx < len(upload_events) else {}
            event_idx += 1
            raw_action = (ev.get("action") or {}).get("value")
            metadata = str(raw_action or "")
            if not is_recorded_file_metadata(metadata):
                metadata = ""
            extras = ev.get("extras") if isinstance(ev.get("extras"), dict) else {}
            file_pick = extras.get("file_pick") if isinstance(extras.get("file_pick"), dict) else None
            patch: dict[str, Any] = {
                "value": step.value,
                "input_binding": step.input_binding,
            }
            if bind_upload_step_value(
                patch,
                file_pick=file_pick,
                raw_metadata_value=metadata,
                state=state,
            ):
                step.value = patch["value"]
                step.input_binding = patch["input_binding"]


def for_each_loop_variable_names(payload: Any) -> set[str]:
    """Every ``{{<as>_id}}``/``{{<as>_index}}`` name a `for_each` loop injects at replay time
    (EXEC-38), walking both step shapes a caller might hand in: the editor/document shape
    (``step["for_each"] = {"as": ..., "steps": [...]}``) and the flat execution/export shape
    (``{"type": "for_each", "as": ..., "steps": [...]}``). These names are populated by
    `run.js::runForEachStep` from the loop's own row/items list, never something a user/agent
    supplies — the same reason `downloaded_file*` is excluded from auto-declared inputs above —
    so both `reconcile_inputs_with_step_values` and
    `_merge_saved_inputs_with_execution_placeholders` exclude whatever this returns.

    Recurses through the whole structure rather than only known loop-body keys, so a `for_each`
    nested inside another primitive's body (a branch, another loop) is still found.
    """
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for_each = value.get("for_each")
            as_name: Any = None
            if isinstance(for_each, dict):
                as_name = for_each.get("as")
            elif value.get("type") == "for_each":
                as_name = value.get("as")
            if as_name:
                as_name = str(as_name).strip()
                if as_name:
                    names.add(f"{as_name}_id")
                    names.add(f"{as_name}_index")
            for v in value.values():
                visit(v)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return names


def filter_runtime_only_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop auto-declared downloaded_file* placeholders from skill inputs."""
    out: list[dict[str, Any]] = []
    for item in inputs:
        name = str(item.get("name") or item.get("id") or "").strip()
        if name and _RUNTIME_ONLY_PLACEHOLDER_RE.match(name):
            continue
        out.append(item)
    return out
