"""Telemetry aggregation and query helpers for company-scoped tracking.

Pure functions over the ``tracking/{company}`` KV namespaces: workspace-scoped
visibility, run summaries, recovery-tier classification, dashboard metrics, and
the admin drift-review queues. The FastAPI routes in ``app.api.tracking_routes``
are thin wrappers over these.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Any

from conxa_core.db import db_get, db_list, db_list_kv, db_set
from conxa_core.config import settings
from conxa_core.storage.workflow_store import list_workflows
from conxa_core.storage.skill_pack_store import list_skill_packs
from conxa_core.workspace import workspace_dir_slug
from app.services.entitlements import analytics_retention_cutoff_ms
from app.services.manifest_signing import load_signing_key, sign_manifest
from app.services.saas import (
    Principal,
    personal_workspace_id,
    visible_workspace_ids_for,
)

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000
_RECOVERY_TYPES = ("Selector", "Text Anchor", "Text Variant", "Vision")
# Product names (A/B). Method is still Selector/…/Vision; keys must stay unique so
# two methods in the same product tier don't overwrite each other.
_RECOVERY_TIERS = (
    ("Tier A", "Selector"),
    ("Tier A", "Text Anchor"),
    ("Tier B", "Text Variant"),
    ("Tier B", "Vision"),
)


def _verify_token(company: str, token: str) -> dict[str, Any] | None:
    """Verify the tracking token for a company.

    Published packs get a server-issued token stored in kv_store keyed by company slug.
    Local dev without a stored token or secret still accepts telemetry for convenience —
    but in production (SKILL_AUTH_REQUIRED=true) a missing token is rejected
    rather than silently treated as an empty workspace (SG-05).
    """
    stored = db_get("tracking_tokens", company)
    if isinstance(stored, dict) and stored.get("token"):
        expected = str(stored.get("token") or "")
        if token and secrets.compare_digest(expected, token):
            return stored
        return None
    if not settings.tracking_hmac_secret and not settings.auth_required:
        return {"workspace_id": ""}
    logger.warning("tracking_token_missing company=%s auth_required=%s", company, settings.auth_required)
    return None


def _canonical_json(obj: dict[str, Any]) -> bytes:
    """Deterministic serialization — matches runtime/app/canonical_json.js and
    manifest_signing.py's `_canonical_json` byte-for-byte (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _verify_chain_link(token: str, body: dict[str, Any], raw_evts: list) -> dict[str, Any]:
    """Verify one batch's PROD-18 evidence-chain HMAC.

    MUST be called on `raw_evts` — the events exactly as received, before `_cap_events`
    truncates any field. Hashing the post-truncation events can never match what the
    runtime actually signed (runtime/app/tracker.js::_buildEnvelope).

    The HMAC key is the raw bearer token from this request, not the stored token record —
    by the time this runs `_verify_token` has already confirmed they're equal (or that
    this is the unauthenticated local-dev fallback, where there is no shared secret to
    check against and the link is reported unverifiable via `ok: None` below).

    A batch with no `seq` predates this feature (every runtime before this change) —
    reported as `ok: None`, which callers must treat as "unchained", not as a failure.
    """
    seq = body.get("seq")
    if seq is None:
        return {"seq": None, "prev": "", "h": "", "ok": None}
    prev = str(body.get("prev", ""))
    h = str(body.get("h", ""))
    linkable = {"e": raw_evts, "p": prev, "r": body.get("rid", ""), "s": seq}
    expected = hmac.new((token or "").encode("utf-8"), _canonical_json(linkable), hashlib.sha256).hexdigest()
    ok = bool(h) and secrets.compare_digest(expected, h)
    return {"seq": seq, "prev": prev, "h": h, "ok": ok}


def _chain_state(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the per-batch chain links stored on a run's batches into one verdict.

    - "none"     every batch predates the evidence chain (no `seq` anywhere).
    - "verified" every batch's own HMAC checked out, `seq` is contiguous from 0, and each
                 batch's `prev` matches its immediate predecessor's `h`.
    - "gap"      some `seq` is missing — a batch was never delivered (dropped POST, or
                 a spilled batch not yet drained). This is PROD-18's headline signal: it
                 makes "evidence never arrived" detectable instead of silently invisible.
    - "broken"   a present batch failed its own HMAC, or two adjacent batches don't link —
                 tampering or reordering, not just loss.
    """
    chains: list[dict[str, Any]] = []
    for b in batches:
        c = b.get("chain")
        if isinstance(c, dict):
            chains.append(c)
    seqed = sorted((c for c in chains if c.get("seq") is not None), key=lambda c: c["seq"])
    if not seqed:
        return {"state": "none", "batches": len(batches), "missing_seqs": []}

    seqs = [c["seq"] for c in seqed]
    missing_seqs = [s for s in range(0, seqs[-1] + 1) if s not in seqs]

    broken = any(c.get("ok") is False for c in seqed)
    if not broken:
        if seqed[0]["seq"] == 0 and seqed[0]["prev"] != "":
            broken = True
        for i in range(1, len(seqed)):
            prev_c, cur_c = seqed[i - 1], seqed[i]
            if cur_c["seq"] == prev_c["seq"] + 1 and cur_c["prev"] != prev_c["h"]:
                broken = True
                break

    state = "broken" if broken else ("gap" if missing_seqs else "verified")
    return {"state": state, "batches": len(chains), "missing_seqs": missing_seqs}


_GOVERNANCE_POLICY_NS = "governance_policy"
_POLICY_DEFAULT_TTL_S = 7 * 86_400  # 7 days — re-signed on every PUT; see docs/Audit-and-Control.md
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PolicyValidationError(ValueError):
    """A governance-policy PUT body failed validation — reported as 422, not 500."""


def _validate_policy_windows(windows: Any) -> list[dict[str, Any]]:
    """Validate PROD-18 execution-window rules. Each window: {skills, tz, days, start, end}.
    `skills` empty or ["*"] means every skill in the workspace; windows are RESTRICTIONS —
    a skill matching no window is unrestricted, since a default-deny would block every skill
    the moment any policy exists at all for the workspace."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if windows is None:
        return []
    if not isinstance(windows, list):
        raise PolicyValidationError("windows must be a list")
    out: list[dict[str, Any]] = []
    for i, w in enumerate(windows):
        if not isinstance(w, dict):
            raise PolicyValidationError(f"windows[{i}] must be an object")
        tz = str(w.get("tz", ""))
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise PolicyValidationError(f"windows[{i}].tz {tz!r} is not a known IANA timezone") from exc
        days = w.get("days", [1, 2, 3, 4, 5, 6, 7])
        if not isinstance(days, list) or not all(isinstance(d, int) and 1 <= d <= 7 for d in days):
            raise PolicyValidationError(f"windows[{i}].days must be a list of ints 1(Mon)-7(Sun)")
        start, end = str(w.get("start", "")), str(w.get("end", ""))
        if not _HHMM_RE.match(start) or not _HHMM_RE.match(end):
            raise PolicyValidationError(f"windows[{i}].start/end must be HH:MM (24h)")
        skills = w.get("skills") or ["*"]
        if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
            raise PolicyValidationError(f"windows[{i}].skills must be a list of strings")
        out.append({"skills": skills, "tz": tz, "days": sorted(set(days)), "start": start, "end": end})
    return out


def write_governance_policy(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, sign, and store a workspace's PROD-18 governance policy.

    Signed with the same Ed25519 key and canonical-JSON scheme as the runtime update
    manifest (`app.services.manifest_signing`) — the runtime already carries and verifies
    against that public key, so this adds no new trust anchor. `key_id` is carried but
    unused today; it exists so a future key rotation is a data change instead of a
    wire-format break (see docs/Security.md's SG-19 gap: this remains a single keypair
    with no rotation).

    Windows are restrictions, not an allow-list: an unlisted skill is unaffected by them.
    `enforce: false` (default) ships the policy as audit-only — the runtime records what it
    WOULD have blocked without actually blocking, the recommended rollout per
    research-analysis/04-architecture/subsystems/enterprise.md §1.
    """
    windows = _validate_policy_windows(payload.get("windows"))
    denied_hosts_raw = payload.get("denied_hosts") or []
    if not isinstance(denied_hosts_raw, list) or not all(isinstance(h, str) for h in denied_hosts_raw):
        raise PolicyValidationError("denied_hosts must be a list of strings")
    denied_hosts = sorted({h.strip().lower() for h in denied_hosts_raw if h.strip()})

    existing = db_get(_GOVERNANCE_POLICY_NS, workspace_id)
    prev_version = int(existing.get("policy_version", 0)) if isinstance(existing, dict) else 0
    now = time.time()
    ttl_s = int(payload.get("ttl_s") or _POLICY_DEFAULT_TTL_S)

    doc: dict[str, Any] = {
        "policy_version": prev_version + 1,
        "workspace_id": workspace_id,
        "key_id": "conxa-manifest-1",
        "issued_at": int(now),
        "expires_at": int(now) + ttl_s,
        "enforce": bool(payload.get("enforce", False)),
        "require_receipt": bool(payload.get("require_receipt", False)),
        "windows": windows,
        "denied_hosts": denied_hosts,
        "signature": "",
    }
    signing_key = load_signing_key()
    if signing_key is not None:
        doc["signature"] = sign_manifest(doc, signing_key)
    else:
        logger.warning("governance_policy_unsigned workspace_id=%s — CONXA_MANIFEST_SIGNING_KEY unset", workspace_id)

    db_set(_GOVERNANCE_POLICY_NS, workspace_id, doc)
    return doc


def read_governance_policy(workspace_id: str) -> dict[str, Any] | None:
    """Return the stored, signed governance policy for a workspace, or None if it has
    never had one written. No signing happens on this read path (signing happens once,
    at PUT time) — matches updates_routes.py's manifest.json pattern."""
    stored = db_get(_GOVERNANCE_POLICY_NS, workspace_id)
    return stored if isinstance(stored, dict) else None


def _owner_from_record(record: dict[str, Any]) -> str:
    owner = str(record.get("owner_user_id") or "")
    if owner:
        return owner
    workspace_id = str(record.get("workspace_id") or "")
    if workspace_id.startswith("personal_"):
        return workspace_id.removeprefix("personal_")
    return ""


def _record_visible_to_principal(record: dict[str, Any], principal: Principal) -> bool:
    workspace_id = str(record.get("workspace_id") or "")
    if not workspace_id or workspace_id == principal.workspace_id:
        return True
    if workspace_id == personal_workspace_id(principal.user_id):
        owner = _owner_from_record(record)
        return not owner or owner == principal.user_id
    return False


def _batches_for_workspace(value: Any, workspace_id: str) -> list[dict]:
    batches: list[dict] = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    if not workspace_id:
        return batches
    return [
        batch
        for batch in batches
        if not batch.get("workspace_id") or batch.get("workspace_id") == workspace_id
    ]


def _batches_for_principal(value: Any, principal: Principal) -> list[dict]:
    batches: list[dict] = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    visible_ids = set(visible_workspace_ids_for(principal))
    return [
        batch
        for batch in batches
        if not batch.get("workspace_id") or batch.get("workspace_id") in visible_ids
    ]


def _run_summary(run_id: str, batches: list[dict]) -> dict:
    """Derive a compact summary from a list of ingested event batches."""
    events: list[dict] = []
    meta = batches[-1] if batches else {}
    for b in batches:
        events.extend(b.get("events", []))

    status = "running"
    duration_ms = 0
    total_steps = 0
    recovered_steps = 0
    failed_step_id = None
    failure_code = None
    started_at = 0
    outcome = None  # Proof of Done: done | done_please_check | submitted | partly_done | couldnt_confirm

    for evt in events:
        code = evt.get("e", "")
        if code == "wf_start":
            started_at = evt.get("ts", 0)
        elif code == "wf_ok":
            status = "ok"
            duration_ms = evt.get("dur", 0)
            total_steps = evt.get("tot", 0)
            recovered_steps = evt.get("rec", 0)
            outcome = evt.get("oc") or "done"  # a runtime that predates Proof of Done only ever said "done"
        elif code == "wf_fail":
            status = "fail"
            duration_ms = evt.get("dur", 0)
            failed_step_id = evt.get("fsi")
            failure_code = evt.get("fc")
            outcome = evt.get("oc")

    return {
        "run_id":         meta.get("run_id", run_id),
        "workflow_id":      meta.get("workflow_id", ""),
        "workflow_ver":     meta.get("workflow_ver", ""),
        "runtime_ver":    meta.get("runtime_ver", ""),
        "uid":            meta.get("uid", ""),
        "wid":            meta.get("wid", ""),
        "status":         status,
        "duration_ms":    duration_ms,
        "total_steps":    total_steps,
        "recovered_steps": recovered_steps,
        "failed_step_id": failed_step_id,
        "failure_code":   failure_code,
        "outcome":        outcome,
        "started_at":     started_at,
        "server_ts":      meta.get("server_ts", 0),
        "chain":          _chain_state(batches),
    }


def _company_run_stats(company: str, principal: Principal) -> tuple[int, float]:
    run_count = 0
    last_seen = 0.0
    for _run_id, batches in db_list_kv(f"tracking/{company}"):
        scoped = _batches_for_principal(batches, principal)
        if not scoped:
            continue
        run_count += 1
        for batch in scoped:
            try:
                last_seen = max(last_seen, float(batch.get("server_ts") or 0))
            except (TypeError, ValueError):
                continue
    return run_count, last_seen


def _tracking_company_rows(principal: Principal) -> list[dict[str, Any]]:
    """Row per workspace with production telemetry or a published pack.

    ``company`` MUST stay the telemetry storage key (the slug ingest writes
    ``tracking/{slug}/...`` under — see ``publish_routes._mint_pack_token`` and
    ``tracking_routes._ingest_events_impl``), never a human label: every reader
    of this function (``_visible_run_records`` here, and the frontend's
    ``/dashboard/runs/{company}/...`` and ``/dashboard/workflows/{company}/...``
    routes) uses it to look the run data back up. The display label is a
    separate ``display_name`` field. Conflating the two here once made every
    published workspace's dashboard read an empty key — see FIX.md.
    """
    rows: dict[str, dict[str, Any]] = {}
    visible_workspace_ids = set(visible_workspace_ids_for(principal))
    display_names = {
        pack.workspace_id: pack.display_name
        for pack in list_skill_packs()
        if pack.display_name and (pack.workspace_id == principal.workspace_id or pack.workspace_id in visible_workspace_ids)
    }

    # tracking_tokens is keyed by workspace slug, but the filesystem KV
    # fallback's list-scan key is a SHA-256 hash of the real key (see
    # conxa_core.db._fs_path), not the original string — the real slug must
    # be read from the record's own "company" field to survive that.
    for kv_key, record in db_list_kv("tracking_tokens"):
        if not isinstance(record, dict):
            continue
        if not _record_visible_to_principal(record, principal):
            continue
        slug = str(record.get("company") or kv_key or "").strip()
        if not slug:
            continue
        workspace_id = str(record.get("workspace_id") or slug).strip()
        run_count, last_seen = _company_run_stats(slug, principal)
        rows[workspace_id] = {
            "company": slug,
            "display_name": display_names.get(workspace_id) or slug,
            "workspace_id": workspace_id,
            "run_count": run_count,
            "last_seen": last_seen or float(record.get("updated_at") or 0),
        }

    for pack in list_skill_packs():
        if pack.workspace_id != principal.workspace_id and pack.workspace_id not in visible_workspace_ids:
            continue
        workspace_id = pack.workspace_id
        current = rows.get(workspace_id)
        if current is None:
            slug = workspace_dir_slug(workspace_id)
            run_count, last_seen = _company_run_stats(slug, principal)
            rows[workspace_id] = {
                "company": slug,
                "display_name": pack.display_name or workspace_id,
                "workspace_id": workspace_id,
                "run_count": run_count,
                "last_seen": last_seen or float(pack.updated_at or 0),
            }
        elif not current.get("last_seen"):
            current["last_seen"] = float(pack.updated_at or 0)

    return sorted(
        rows.values(),
        key=lambda row: (float(row.get("last_seen") or 0), str(row.get("display_name") or "")),
        reverse=True,
    )


def _tracking_diagnostics(principal: Principal) -> dict[str, Any]:
    visible_workspace_ids = set(visible_workspace_ids_for(principal))
    visible_companies = _tracking_company_rows(principal)
    same_user_personal = 0
    hidden_same_user_personal = 0
    for _key, record in db_list_kv("tracking_tokens"):
        if not isinstance(record, dict):
            continue
        workspace_id = str(record.get("workspace_id") or "")
        owner = _owner_from_record(record)
        if workspace_id == personal_workspace_id(principal.user_id) and (not owner or owner == principal.user_id):
            same_user_personal += 1
            if workspace_id not in visible_workspace_ids:
                hidden_same_user_personal += 1
    workflow_count = 0
    for workflow in list_workflows():
        if workflow.workspace_id == principal.workspace_id or (
            workflow.workspace_id in visible_workspace_ids and workflow.owner_user_id == principal.user_id
        ):
            workflow_count += 1
    return {
        "workspace_id": principal.workspace_id,
        "user_id": principal.user_id,
        "personal_workspace_id": personal_workspace_id(principal.user_id),
        "identity_source": principal.identity_source,
        "proxy_identity_trusted": principal.proxy_identity_trusted,
        "proxy_identity_status": principal.proxy_identity_status,
        "visible_workspace_ids": list(visible_workspace_ids),
        "visible_company_count": len(visible_companies),
        "workflow_count": workflow_count,
        "same_user_personal_company_count": same_user_personal,
        "hidden_same_user_personal_count": hidden_same_user_personal,
    }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _epoch_ms(value: Any) -> int:
    n = _number(value)
    if n <= 0:
        return 0
    return int(n if n > 10_000_000_000 else n * 1000)


def _date_key(epoch_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch_ms / 1000))


_RANGE_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}


def _range_days(value: str) -> int:
    return _RANGE_DAYS.get(str(value or "").lower().strip(), 7)


def _visible_runtime_registrations(principal: Principal) -> list[dict[str, Any]]:
    visible_ids = set(visible_workspace_ids_for(principal))
    cutoff_ms = analytics_retention_cutoff_ms(principal)
    registrations: list[dict[str, Any]] = []
    for reg in db_list("runtime_registrations"):
        if not isinstance(reg, dict):
            continue
        workspace_id = str(reg.get("workspace_id") or "")
        if not workspace_id or workspace_id not in visible_ids:
            continue
        if cutoff_ms is not None and _epoch_ms(reg.get("last_seen")) < cutoff_ms:
            continue
        registrations.append(reg)
    return registrations


def _visible_run_records(principal: Principal) -> list[dict[str, Any]]:
    cutoff_ms = analytics_retention_cutoff_ms(principal)
    records: list[dict[str, Any]] = []
    for row in _tracking_company_rows(principal):
        company = str(row.get("company") or "").strip()
        if not company:
            continue
        for run_id, batches in db_list_kv(f"tracking/{company}"):
            scoped = _batches_for_principal(batches, principal)
            if not scoped:
                continue
            events: list[dict[str, Any]] = []
            for batch in scoped:
                events.extend(batch.get("events", []))
            summary = _run_summary(run_id, scoped)
            record = {"company": company, "summary": summary, "events": events}
            if cutoff_ms is not None and _record_time_ms(record) < cutoff_ms:
                continue
            records.append(record)
    records.sort(key=lambda r: _record_time_ms(r), reverse=True)
    return records


def _record_time_ms(record: dict[str, Any]) -> int:
    summary = record.get("summary") or {}
    return max(_epoch_ms(summary.get("started_at")), _epoch_ms(summary.get("server_ts")))


def event_recovery_type(evt: dict[str, Any]) -> str:
    code = str(evt.get("e") or "").lower()
    # Agent-mediated (Tier B) escalation. The runtime ships digest + screenshots in one
    # request — so this is classified as "Vision", the more complete signal.
    if code == "tier_escalated":
        return "Vision"
    key = str(evt.get("rt") or evt.get("sc") or evt.get("tier") or evt.get("sel") or "").lower()
    if code not in {"rec_ok", "tier_ok"}:
        return ""
    if code == "tier_ok" and key == "tier1_compiled" and not evt.get("sel"):
        return ""
    if key in {"selector", "selector_retry", "candidate_fallback", "dialog_scope", "tier1_compiled"}:
        return "Selector"
    if key.startswith("a11y") or key in {"text_anchor", "tier2_a11y"}:
        return "Text Anchor"
    if key in {"text_variant", "fuzzy", "fuzzy_dom"}:
        return "Text Variant"
    if key in {"vision", "vision_recovery", "tier4_vision", "llm_intent", "tier3_llm"}:
        return "Vision"
    return ""


def event_recovery_tier(evt: dict[str, Any], recovery_type: str) -> str:
    code = str(evt.get("e") or "").lower()
    if code == "tier_escalated":
        return "Tier B"
    key = str(evt.get("rt") or evt.get("sc") or evt.get("tier") or evt.get("sel") or "").lower()
    if key in {"selector", "selector_retry", "candidate_fallback", "dialog_scope", "tier1_compiled"}:
        return "Tier A"
    if key.startswith("a11y") or key in {"text_anchor", "tier2_a11y"}:
        return "Tier A"
    if key in {"text_variant", "fuzzy", "fuzzy_dom", "tier3_llm", "llm_intent"}:
        return "Tier B"
    if key in {"vision", "vision_recovery", "tier4_vision"}:
        return "Tier B"
    for tier, mapped_type in _RECOVERY_TIERS:
        if recovery_type == mapped_type:
            return tier
    return "Unknown"


def _run_has_recovery(record: dict[str, Any]) -> bool:
    summary = record.get("summary") or {}
    if int(_number(summary.get("recovered_steps"))) > 0:
        return True
    return any(event_recovery_type(evt) for evt in record.get("events") or [])


def _drift_review_queue(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate runtime repair_event drift signals into an admin review queue.

    Detection is automatic and fleet-wide; this surfaces the evidence to the conxa-cloud
    admin. Nothing here re-signs or publishes — a durable fix is always an admin-reviewed,
    manually published new version (see the durability-flywheel design docs).

    ``records`` is the workspace-visible run set (see ``_visible_run_records``),
    passed in so the caller can compute it once and share it with the pre-exec queue.
    """
    # Distinct runs per (workflow_id, workflow_ver) — the denominator for occurrence_rate_pct.
    # A run can emit more than one repair_event for the same step (retried across escalating
    # tiers), so "how often does this step need repair" must count runs, not raw events.
    runs_by_version: dict[tuple[str, str], set[str]] = {}
    by_key: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for record in records:
        summary = record.get("summary") or {}
        workflow_id = str(summary.get("workflow_id") or "")
        workflow_ver = str(summary.get("workflow_ver") or "")
        run_id = str(summary.get("run_id") or "")
        runs_by_version.setdefault((workflow_id, workflow_ver), set()).add(run_id)
        for evt in record.get("events") or []:
            if evt.get("e") != "repair_event":
                continue
            step_id = evt.get("step_id")
            key = (workflow_id, workflow_ver, step_id)
            entry = by_key.get(key)
            if entry is None:
                entry = {
                    "workflow_id": workflow_id,
                    "workflow_ver": workflow_ver,
                    "step_id": step_id,
                    "occurrences": 0,
                    "tiers": {},
                    "methods": {},
                    "run_ids": set(),
                    "stable_hash": evt.get("stable_hash", ""),
                    "app_version_fingerprint": evt.get("app_version_fingerprint", ""),
                    "last_seen": 0,
                    "status": "needs_review",  # admin-gated; never auto-published
                }
                by_key[key] = entry
            entry["occurrences"] += 1
            entry["run_ids"].add(run_id)
            tier = str(evt.get("tier") or "")
            method = str(evt.get("method") or evt.get("drift_hint") or "")
            if tier:
                entry["tiers"][tier] = entry["tiers"].get(tier, 0) + 1
            if method:
                entry["methods"][method] = entry["methods"].get(method, 0) + 1
            entry["last_seen"] = max(int(entry["last_seen"]), _epoch_ms(evt.get("ts")))

    queue = []
    for entry in by_key.values():
        version_key = (entry["workflow_id"], entry["workflow_ver"])
        total_runs = len(runs_by_version.get(version_key) or set()) or 1
        run_count = len(entry.pop("run_ids"))
        entry["run_count"] = run_count
        entry["total_runs"] = total_runs
        entry["occurrence_rate_pct"] = round((run_count / total_runs) * 100, 1)
        entry["dominant_tier"] = max(entry["tiers"], key=entry["tiers"].get) if entry["tiers"] else ""
        entry["dominant_method"] = max(entry["methods"], key=entry["methods"].get) if entry["methods"] else ""
        queue.append(entry)
    # Surface "this step fails in most of its runs" ahead of "this step has many events
    # because the workflow runs constantly" — rate first, raw occurrences as a tiebreaker.
    queue.sort(key=lambda e: (e["occurrence_rate_pct"], e["occurrences"], e["last_seen"]), reverse=True)
    return queue


def _pre_exec_drift_queue(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate runtime pre-execution `drift_detected` signals per workflow version.

    These are advisory: the runtime warns (never blocks) when a pack's recorded
    structural landmarks are mostly missing at run start, hinting the target app
    was redesigned. Grouped per (workflow_id, workflow_ver) since the signal is
    per-run, not per-step. ``records`` is the shared workspace-visible run set.
    """
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        summary = record.get("summary") or {}
        workflow_id = str(summary.get("workflow_id") or "")
        workflow_ver = str(summary.get("workflow_ver") or "")
        for evt in record.get("events") or []:
            if evt.get("e") != "drift_detected":
                continue
            key = (workflow_id, workflow_ver)
            entry = by_key.get(key)
            if entry is None:
                entry = {
                    "workflow_id": workflow_id,
                    "workflow_ver": workflow_ver,
                    "occurrences": 0,
                    "max_drift_ratio": 0.0,
                    "missing_intents": {},
                    "last_seen": 0,
                    "status": "needs_review",
                }
                by_key[key] = entry
            entry["occurrences"] += 1
            entry["max_drift_ratio"] = max(float(entry["max_drift_ratio"]), _number(evt.get("drift_ratio")))
            for intent in evt.get("missing_intents") or []:
                label = str(intent)
                entry["missing_intents"][label] = entry["missing_intents"].get(label, 0) + 1
            entry["last_seen"] = max(int(entry["last_seen"]), _epoch_ms(evt.get("ts")))
    queue = list(by_key.values())
    queue.sort(key=lambda e: (e["occurrences"], e["last_seen"]), reverse=True)
    return queue


def _failed_step_index(record: dict[str, Any]) -> int | None:
    summary = record.get("summary") or {}
    value = summary.get("failed_step_id")
    if value is None:
        for evt in reversed(record.get("events") or []):
            if evt.get("e") == "step_fail" and evt.get("si") is not None:
                value = evt.get("si")
                break
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event_step_index(evt: dict[str, Any]) -> int | None:
    value = evt.get("si")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _assertion_health_by_step(records: list[dict[str, Any]], limit: int | None = 12) -> list[dict[str, Any]]:
    """Aggregate runtime `verify_result` events into a per-step assertion pass-rate view.

    `verify_result` carries `{si, ok, n, advFail, ct}` for every step that ran post-condition
    assertions (`runtime/run.js::verifyStep`'s full audit, emitted on the primary execution
    path). This is the fleet-wide early-warning signal for assertion decay: an advisory check
    that's starting to fail more often is visible here before it ever becomes a required-
    assertion hard failure. Sorted worst-pass-rate first so decaying steps surface at the top.
    """
    by_key: dict[tuple[str, str, int | None], dict[str, Any]] = {}
    for record in records:
        summary = record.get("summary") or {}
        workflow = str(summary.get("workflow_id") or "Unknown workflow")
        company = str(record.get("company") or "")
        for evt in record.get("events") or []:
            if evt.get("e") != "verify_result":
                continue
            step_index = _event_step_index(evt)
            key = (company, workflow, step_index)
            entry = by_key.get(key)
            if entry is None:
                entry = {
                    "company": company,
                    "workflow": workflow,
                    "step_index": step_index,
                    "step_label": f"Step {step_index + 1}" if step_index is not None else "Unknown step",
                    "total": 0,
                    "passed": 0,
                    "advisory_failures": 0,
                    "cant_tell": 0,
                    "last_seen": 0,
                }
                by_key[key] = entry
            entry["total"] += 1
            if evt.get("ok"):
                entry["passed"] += 1
            entry["advisory_failures"] += int(_number(evt.get("advFail")))
            # Checks that passed only because they could not look (Proof of Done): a step whose proof is
            # quietly all "couldn't tell" reads healthy by pass rate alone.
            entry["cant_tell"] += int(_number(evt.get("ct")))
            entry["last_seen"] = max(int(entry["last_seen"]), _epoch_ms(evt.get("ts")))

    rows = [
        {**entry, "pass_rate": round((entry["passed"] / entry["total"]) * 100, 1) if entry["total"] else 100.0}
        for entry in by_key.values()
    ]
    rows.sort(key=lambda r: (r["pass_rate"], -r["total"]))
    return rows if limit is None else rows[:limit]


def _dashboard_metrics(
    principal: Principal,
    range_value: str,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Adoption, reliability, and recovery aggregates for one range.

    ``records`` lets a caller that has already run ``_visible_run_records`` pass the scan in
    rather than paying for a second one — see ``tracking_analytics.dashboard``, which composes
    this with the operations analytics into a single response.
    """
    days = _range_days(range_value)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * _DAY_MS)
    last_24h_ms = now_ms - _DAY_MS

    registrations = _visible_runtime_registrations(principal)
    install_keys = {
        str(reg.get("install_id") or f"{reg.get('company', '')}:{reg.get('platform', '')}")
        for reg in registrations
        if reg.get("install_id") or reg.get("company") or reg.get("platform")
    }
    active_install_keys = {
        str(reg.get("install_id") or f"{reg.get('company', '')}:{reg.get('platform', '')}")
        for reg in registrations
        if _epoch_ms(reg.get("last_seen")) >= start_ms
        and (reg.get("install_id") or reg.get("company") or reg.get("platform"))
    }

    all_records = _visible_run_records(principal) if records is None else records
    range_records = [record for record in all_records if _record_time_ms(record) >= start_ms]
    last_24h_records = [record for record in all_records if _record_time_ms(record) >= last_24h_ms]
    completed_records = [r for r in range_records if (r.get("summary") or {}).get("status") in {"ok", "fail"}]
    ok_records = [r for r in completed_records if (r.get("summary") or {}).get("status") == "ok"]
    failed_records = [r for r in completed_records if (r.get("summary") or {}).get("status") == "fail"]
    recovered_records = [r for r in range_records if _run_has_recovery(r)]
    durations = [
        int(_number((r.get("summary") or {}).get("duration_ms")))
        for r in completed_records
        if _number((r.get("summary") or {}).get("duration_ms")) > 0
    ]

    range_install_ids = {
        str((r.get("summary") or {}).get("uid") or "")
        for r in range_records
        if (r.get("summary") or {}).get("uid")
    }
    active_users = len(active_install_keys | range_install_ids)
    active_companies = len({str(r.get("company") or "") for r in range_records if r.get("company")})

    recovery_counts = {name: 0 for name in _RECOVERY_TYPES}
    recovery_step_usage: dict[str, dict[str, Any]] = {}
    recovery_workflows: dict[str, dict[str, Any]] = {}

    def record_recovery_usage(
        *,
        company: str,
        workflow: str,
        step_index: int | None,
        recovery_type: str,
        tier: str,
        count: int,
        last_seen: int,
    ) -> None:
        if count <= 0:
            return
        step_label = f"Step {step_index + 1}" if step_index is not None else "Unknown step"
        flat_key = f"{company}:{workflow}:{step_index if step_index is not None else 'unknown'}:{recovery_type}"
        current = recovery_step_usage.setdefault(
            flat_key,
            {
                "company": company,
                "workflow": workflow,
                "step_index": step_index,
                "step_label": step_label,
                "recovery_type": recovery_type,
                "tier": tier,
                "count": 0,
                "last_seen": 0,
            },
        )
        current["count"] += count
        current["last_seen"] = max(int(current["last_seen"]), last_seen)

        workflow_key = f"{company}:{workflow}"
        workflow_row = recovery_workflows.setdefault(
            workflow_key,
            {
                "company": company,
                "workflow": workflow,
                "count": 0,
                "last_seen": 0,
                "steps": {},
            },
        )
        workflow_row["count"] += count
        workflow_row["last_seen"] = max(int(workflow_row["last_seen"]), last_seen)

        steps = workflow_row["steps"]
        step_key = str(step_index) if step_index is not None else "unknown"
        step_row = steps.setdefault(
            step_key,
            {
                "step_index": step_index,
                "step_label": step_label,
                "total_count": 0,
                "last_seen": 0,
                "tier_counts": {
                    mapped_type: {"tier": name, "recovery_type": mapped_type, "count": 0}
                    for name, mapped_type in _RECOVERY_TIERS
                },
            },
        )
        step_row["total_count"] += count
        step_row["last_seen"] = max(int(step_row["last_seen"]), last_seen)
        bucket = step_row["tier_counts"].setdefault(
            recovery_type, {"tier": tier, "recovery_type": recovery_type, "count": 0}
        )
        bucket["tier"] = tier
        bucket["count"] += count

    for record in range_records:
        summary = record.get("summary") or {}
        company = str(record.get("company") or "")
        workflow = str(summary.get("workflow_id") or "Unknown workflow")
        for evt in record.get("events") or []:
            recovery_type = event_recovery_type(evt)
            if recovery_type:
                recovery_counts[recovery_type] += 1
                step_index = _event_step_index(evt)
                record_recovery_usage(
                    company=company,
                    workflow=workflow,
                    step_index=step_index,
                    recovery_type=recovery_type,
                    tier=event_recovery_tier(evt, recovery_type),
                    count=1,
                    last_seen=_epoch_ms(evt.get("ts")) or _record_time_ms(record),
                )
    # Older runtimes may only send wf_ok.rec. Keep recovered runs visible as selector saves.
    if not any(recovery_counts.values()):
        recovery_counts["Selector"] = sum(
            1 for record in range_records if int(_number((record.get("summary") or {}).get("recovered_steps"))) > 0
        )
        for record in range_records:
            recovered_steps = int(_number((record.get("summary") or {}).get("recovered_steps")))
            if recovered_steps <= 0:
                continue
            summary = record.get("summary") or {}
            company = str(record.get("company") or "")
            workflow = str(summary.get("workflow_id") or "Unknown workflow")
            record_recovery_usage(
                company=company,
                workflow=workflow,
                step_index=None,
                recovery_type="Selector",
                tier="Tier A",
                count=recovered_steps,
                last_seen=_record_time_ms(record),
            )

    workflow_failures: dict[str, dict[str, Any]] = {}
    step_failures: dict[str, dict[str, Any]] = {}
    for record in failed_records:
        summary = record.get("summary") or {}
        workflow = str(summary.get("workflow_id") or "Unknown workflow")
        current = workflow_failures.setdefault(
            workflow,
            {"workflow": workflow, "failed_executions": 0, "last_failure_code": "", "last_seen": 0},
        )
        current["failed_executions"] += 1
        current["last_failure_code"] = summary.get("failure_code") or current["last_failure_code"]
        current["last_seen"] = max(int(current["last_seen"]), _record_time_ms(record))

        step_index = _failed_step_index(record)
        step_key = f"{workflow}:{step_index if step_index is not None else 'unknown'}"
        step = step_failures.setdefault(
            step_key,
            {
                "workflow": workflow,
                "step_index": step_index,
                "step_label": f"Step {step_index + 1}" if step_index is not None else "Unknown step",
                "failed_executions": 0,
                "last_failure_code": "",
                "last_seen": 0,
            },
        )
        step["failed_executions"] += 1
        step["last_failure_code"] = summary.get("failure_code") or step["last_failure_code"]
        step["last_seen"] = max(int(step["last_seen"]), _record_time_ms(record))

    buckets = {
        _date_key(now_ms - ((_range_days(range_value) - 1 - i) * _DAY_MS)): {
            "date": _date_key(now_ms - ((_range_days(range_value) - 1 - i) * _DAY_MS)),
            "executions": 0,
            "successful": 0,
            "failed": 0,
            "recovered": 0,
        }
        for i in range(days)
    }
    for record in range_records:
        key = _date_key(_record_time_ms(record))
        if key not in buckets:
            continue
        summary = record.get("summary") or {}
        buckets[key]["executions"] += 1
        if summary.get("status") == "ok":
            buckets[key]["successful"] += 1
        if summary.get("status") == "fail":
            buckets[key]["failed"] += 1
        if _run_has_recovery(record):
            buckets[key]["recovered"] += 1

    total_range = len(range_records)
    success_rate = round((len(ok_records) / len(completed_records)) * 100, 1) if completed_records else 0.0
    recovery_rate = round((len(recovered_records) / total_range) * 100, 1) if total_range else 0.0

    return {
        "range": f"{days}d",
        "metrics": {
            "total_installs": len(install_keys),
            "active_users": active_users,
            "active_companies": active_companies,
            "total_executions": len(all_records),
            "executions_last_24h": len(last_24h_records),
            "success_rate": success_rate,
            "failed_executions": len(failed_records),
            "recovery_rate": recovery_rate,
            "average_execution_time": round(sum(durations) / len(durations)) if durations else 0,
        },
        "recovery_type_usage": [
            {"type": name, "count": recovery_counts[name]}
            for name in _RECOVERY_TYPES
        ],
        "recovery_usage_by_step": sorted(
            recovery_step_usage.values(),
            key=lambda row: (row["count"], row["last_seen"]),
            reverse=True,
        )[:12],
        "recovery_usage_by_workflow": [
            {
                "company": row["company"],
                "workflow": row["workflow"],
                "count": row["count"],
                "last_seen": row["last_seen"],
                "steps": [
                    {
                        "step_index": step["step_index"],
                        "step_label": step["step_label"],
                        "total_count": step["total_count"],
                        "last_seen": step["last_seen"],
                        "tier_counts": [
                            step["tier_counts"][mapped_type]
                            for _tier, mapped_type in _RECOVERY_TIERS
                            if step["tier_counts"].get(mapped_type, {}).get("count", 0) > 0
                        ],
                    }
                    for step in sorted(
                        row["steps"].values(),
                        key=lambda step: (step["total_count"], step["last_seen"]),
                        reverse=True,
                    )
                ],
            }
            for row in sorted(
                recovery_workflows.values(),
                key=lambda row: (row["count"], row["last_seen"]),
                reverse=True,
            )[:8]
        ],
        "most_failed_workflows": sorted(
            workflow_failures.values(),
            key=lambda row: (row["failed_executions"], row["last_seen"]),
            reverse=True,
        )[:6],
        "most_failed_steps": sorted(
            step_failures.values(),
            key=lambda row: (row["failed_executions"], row["last_seen"]),
            reverse=True,
        )[:6],
        "execution_trend": list(buckets.values()),
        "assertion_health_by_step": _assertion_health_by_step(range_records),
    }



def _cap_events(evts: list[Any], company: str) -> list[Any]:
    """Cap batch size and per-field string length to bound telemetry storage (SG-06)."""
    max_events = settings.tracking_max_events_per_batch
    max_chars = settings.tracking_max_field_chars
    truncated_fields = 0

    capped = evts[:max_events]

    result: list[Any] = []
    for evt in capped:
        if not isinstance(evt, dict):
            result.append(evt)
            continue
        clean: dict[str, Any] = {}
        for k, v in evt.items():
            if isinstance(v, str) and len(v) > max_chars:
                clean[k] = v[:max_chars]
                truncated_fields += 1
            else:
                clean[k] = v
        result.append(clean)

    if len(evts) > max_events or truncated_fields:
        logger.warning(
            "tracking_batch_capped company=%s original_events=%d kept_events=%d truncated_fields=%d",
            company, len(evts), len(result), truncated_fields,
        )
    return result

