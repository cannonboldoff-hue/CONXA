"""Metadata-only "compile updated" command handler."""

from __future__ import annotations

from typing import Any

from handlers.protocol import _CommandError, _safe_id


class VisualMixin:
    def cmd_compile_updated(self, payload: dict[str, Any], _rid: str) -> dict[str, Any]:
        from conxa_core.storage.json_store import read_skill, write_skill

        skill_id = _safe_id(payload.get("skill_id"), "skill_id")
        skill_title = str(payload.get("skill_title") or "").strip()
        doc = read_skill(skill_id)
        if doc is None:
            raise _CommandError("skill_not_found", f"No skill {skill_id}")
        doc = dict(doc)
        meta = dict(doc.get("meta") or {})
        if skill_title:
            meta["title"] = skill_title
        meta["version"] = int(meta.get("version") or 1) + 1
        doc["meta"] = meta
        write_skill(skill_id, doc)
        return {"skill_id": skill_id, "ok": True}
