"""Deterministic diff between two skill-pack release candidates.

Pure stdlib (``json`` + ``difflib``) — no LLM, no wall-clock, no randomness, so
the same two inputs always produce byte-identical output. Used both for the
pre-publish "what will change" preview and for ``GET .../releases/{version}/diff``
after the fact, so the number shown before publishing is the number recorded
after (see plan §7).

Execution steps have no stable id (they're 1-based positions assigned at
compile time), so steps are aligned by a semantic content key rather than by
index — a step whose targeting metadata was re-healed by the compiler still
reads as "modified", not as one step removed and a different one added.
"""

from __future__ import annotations

import difflib
import json
from typing import Any

# Fixed per-skill file set the compiler always writes (see skill_package_builder_output.py).
_SKILL_FILES = ("execution.json", "recovery.json", "inputs.json", "manifest.json", "validation.json", "app_knowledge.json")

# Targeting metadata the compiler/recovery may rewrite without the step's actual
# behavior changing — excluded from the "is this step the same" comparison so a
# re-healed selector reads as unchanged rather than as a fabricated edit.
_VOLATILE_STEP_KEYS = ("identity_bundle", "compiled_selectors", "confidence")


def _decode_json(raw: bytes | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _index_by_skill(files: dict[str, bytes], skill_groups: dict[str, str]) -> dict[str, dict[str, bytes]]:
    """Reindex a flat {relative_path: bytes} dict (as published) into
    {skill_slug: {filename: bytes}}. Skill files always live at
    "{group_id}/{slug}/{filename}" (see skill_package_builder_output.py), so the
    slug is positionally the second path segment regardless of whether the
    group id is known — ``skill_groups`` is consulted only as a cross-check,
    never required."""
    out: dict[str, dict[str, bytes]] = {}
    for path, content in files.items():
        parts = path.split("/")
        if len(parts) < 3:
            continue  # package-level file (pack.json) — not a per-skill file
        group_id, positional_slug = parts[0], parts[1]
        filename = "/".join(parts[2:])
        if filename not in _SKILL_FILES:
            continue
        slug = positional_slug
        for candidate_slug, candidate_group in skill_groups.items():
            if candidate_group == group_id and candidate_slug == positional_slug:
                slug = candidate_slug
                break
        out.setdefault(slug, {})[filename] = content
    return out


def _step_key(step: Any) -> tuple:
    """Semantic identity for a step, deliberately excluding volatile targeting
    metadata so a re-healed selector reads as 'modified', not remove+add."""
    if not isinstance(step, dict):
        return ("_raw", repr(step))
    return (
        step.get("type"),
        step.get("tab"),
        json.dumps(step.get("frame") or step.get("frame_chain") or [], sort_keys=True),
        step.get("selector") or step.get("url") or step.get("kind") or step.get("value") or "",
    )


def _step_body(step: Any) -> Any:
    """Step dict with volatile targeting metadata stripped, for equality checks
    once two steps have already been aligned by ``_step_key``."""
    if not isinstance(step, dict):
        return step
    return {k: v for k, v in step.items() if k not in _VOLATILE_STEP_KEYS}


def _diff_execution_steps(prev: Any, curr: Any) -> dict[str, int]:
    prev_steps = prev if isinstance(prev, list) else []
    curr_steps = curr if isinstance(curr, list) else []
    prev_keys = [_step_key(s) for s in prev_steps]
    curr_keys = [_step_key(s) for s in curr_steps]

    added = removed = modified = 0
    matcher = difflib.SequenceMatcher(a=prev_keys, b=curr_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if _step_body(prev_steps[i]) != _step_body(curr_steps[j]):
                    modified += 1
        elif tag == "replace":
            span = min(i2 - i1, j2 - j1)
            modified += span
            added += max(0, (j2 - j1) - span)
            removed += max(0, (i2 - i1) - span)
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1
    return {"steps_added": added, "steps_removed": removed, "steps_modified": modified}


def _changed(prev_raw: bytes | None, curr_raw: bytes | None) -> bool:
    return (prev_raw or b"") != (curr_raw or b"")


def compute_diff(
    previous_files: dict[str, bytes],
    candidate_files: dict[str, bytes],
    *,
    previous_skill_groups: dict[str, str] | None = None,
    candidate_skill_groups: dict[str, str] | None = None,
) -> dict[str, Any]:
    """previous_files / candidate_files: {relative_path: raw_bytes} — either an
    immutable release snapshot or a not-yet-published candidate file set. Both
    may be empty (first-ever publish has no ``previous_files``)."""
    prev_by_skill = _index_by_skill(previous_files, previous_skill_groups or {})
    curr_by_skill = _index_by_skill(candidate_files, candidate_skill_groups or {})

    all_skills = sorted(set(prev_by_skill) | set(curr_by_skill))
    skills_added = sorted(set(curr_by_skill) - set(prev_by_skill))
    skills_removed = sorted(set(prev_by_skill) - set(curr_by_skill))

    steps_added = steps_removed = steps_modified = 0
    recovery_changed_skills: list[str] = []
    metadata_changed_skills: list[str] = []
    per_skill: dict[str, Any] = {}

    for slug in all_skills:
        prev = prev_by_skill.get(slug, {})
        curr = curr_by_skill.get(slug, {})
        exec_diff = _diff_execution_steps(_decode_json(prev.get("execution.json")), _decode_json(curr.get("execution.json")))
        steps_added += exec_diff["steps_added"]
        steps_removed += exec_diff["steps_removed"]
        steps_modified += exec_diff["steps_modified"]

        skill_recovery_changed = _changed(prev.get("recovery.json"), curr.get("recovery.json"))
        skill_metadata_changed = (
            _changed(prev.get("manifest.json"), curr.get("manifest.json"))
            or _changed(prev.get("inputs.json"), curr.get("inputs.json"))
            or _changed(prev.get("validation.json"), curr.get("validation.json"))
            or _changed(prev.get("app_knowledge.json"), curr.get("app_knowledge.json"))
        )
        if skill_recovery_changed:
            recovery_changed_skills.append(slug)
        if skill_metadata_changed:
            metadata_changed_skills.append(slug)

        if slug in skills_added:
            status = "added"
        elif slug in skills_removed:
            status = "removed"
        elif exec_diff["steps_added"] or exec_diff["steps_removed"] or exec_diff["steps_modified"] or skill_recovery_changed or skill_metadata_changed:
            status = "changed"
        else:
            status = "unchanged"

        per_skill[slug] = {
            "status": status,
            **exec_diff,
            "recovery_changed": skill_recovery_changed,
            "metadata_changed": skill_metadata_changed,
        }

    summary_parts = [
        f"+{steps_added} step{'s' if steps_added != 1 else ''} added",
        f"~{steps_modified} modified",
        f"-{steps_removed} removed",
    ]
    summary = ", ".join(summary_parts)
    if recovery_changed_skills:
        summary += f", ~{len(recovery_changed_skills)} recovery rule{'s' if len(recovery_changed_skills) != 1 else ''} changed"
    if skills_added:
        summary = f"+{len(skills_added)} skill{'s' if len(skills_added) != 1 else ''} added, " + summary
    if skills_removed:
        summary += f", -{len(skills_removed)} skill{'s' if len(skills_removed) != 1 else ''} removed"

    return {
        "skills_added": skills_added,
        "skills_removed": skills_removed,
        "steps_added": steps_added,
        "steps_removed": steps_removed,
        "steps_modified": steps_modified,
        "recovery_changed_skills": recovery_changed_skills,
        "metadata_changed_skills": metadata_changed_skills,
        "summary": summary,
        "per_skill": per_skill,
    }
