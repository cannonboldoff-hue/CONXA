"""Filesystem JSON persistence for sessions and compiled skill packages."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

from conxa_core.config import settings
from conxa_core.db import db_get, db_set, db_delete, db_list
from conxa_core.sanitize import scrub_surrogates
from conxa_core.storage.skill_hooks import notify_skill_deleted, notify_skill_written


def skills_dir() -> Path:
    p = settings.data_dir / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_skill(skill_id: str, document: dict[str, Any]) -> Path:
    document = scrub_surrogates(document)
    is_update = read_skill(skill_id) is not None
    db_set("skills", skill_id, document)  # the durable copy — Postgres, or db.py's own KV fallback
    path = skills_dir() / f"{skill_id}.json"
    try:
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        # A secondary, human-readable-by-id copy — db_set above already holds the
        # canonical one, so this failing doesn't lose data, but silently returning
        # `path` as if it were written is misleading to anyone who stats/reads it directly.
        warnings.warn(f"Secondary skill file write failed for {skill_id!r} at {path}: {exc}", stacklevel=2)
    if is_update:
        try:
            from conxa_core.storage.workflow_store import invalidate_workflow_test_by_skill  # noqa: PLC0415
            invalidate_workflow_test_by_skill(skill_id)
        except Exception as exc:  # noqa: BLE001
            # A reviewer signs off against last_test_* fields — if this reset silently
            # fails, a stale pass/fail from before this edit can survive and mislead them.
            warnings.warn(
                f"Failed to invalidate stale test results for skill {skill_id!r} after an "
                f"edit — last_test_* may now be stale: {type(exc).__name__}: {exc}",
                stacklevel=2,
            )
    notify_skill_written(skill_id, document)
    return path


def read_skill(skill_id: str) -> dict[str, Any] | None:
    data = db_get("skills", skill_id)
    if data is not None:
        return scrub_surrogates(data)
    path = skills_dir() / f"{skill_id}.json"
    if not path.is_file():
        return None
    return scrub_surrogates(json.loads(path.read_text(encoding="utf-8")))


def delete_skill(skill_id: str) -> bool:
    db_delete("skills", skill_id)
    path = skills_dir() / f"{skill_id}.json"
    if path.is_file():
        path.unlink()
    notify_skill_deleted(skill_id)
    return True


def list_skill_summaries() -> list[dict[str, Any]]:
    """Return newest-first summaries for skills."""
    db_items = db_list("skills")
    if db_items:
        out: list[dict[str, Any]] = []
        for doc in db_items:
            skill_id = doc.get("meta", {}).get("id") or ""
            meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
            skills_raw = doc.get("skills") or []
            block0 = skills_raw[0] if isinstance(skills_raw, list) and skills_raw and isinstance(skills_raw[0], dict) else {}
            steps = block0.get("steps") if isinstance(block0.get("steps"), list) else []
            out.append({
                "skill_id": skill_id,
                "title": str(meta.get("title") or skill_id),
                "version": int(meta.get("version") or 1),
                "step_count": len(steps),
                "modified_at": 0.0,
            })
        return list(reversed(out))
    # File fallback for local dev
    out = []
    base = skills_dir()
    paths = [p for p in base.glob("*.json") if p.is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in paths:
        skill_id = path.stem
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.warn(f"Dropping corrupt skill file {path} from list_skill_summaries: {exc}", stacklevel=2)
            continue
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        skills_raw = doc.get("skills") or []
        block0 = skills_raw[0] if isinstance(skills_raw, list) and skills_raw and isinstance(skills_raw[0], dict) else {}
        steps = block0.get("steps") if isinstance(block0.get("steps"), list) else []
        out.append({
            "skill_id": skill_id,
            "title": str(meta.get("title") or skill_id),
            "version": int(meta.get("version") or 1),
            "step_count": len(steps),
            "modified_at": path.stat().st_mtime,
        })
    return out
