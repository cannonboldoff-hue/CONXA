"""Enterprise operations analytics over tracking records.

Pure functions over the ``records`` list ``tracking._visible_run_records`` produces
(``[{"company", "summary", "events"}, ...]``). Everything here is derived from telemetry
the runtime already emits — no new event codes, no LLM calls. ``_dashboard_metrics``
composes these into one response so a single KV scan serves the whole dashboard.

Recovery-tier classification is deliberately delegated to ``tracking.event_recovery_type``
and ``tracking.event_recovery_tier`` rather than reimplemented, so the cascade view and the
existing recovery-usage panels can never disagree about what counts as a recovery.
"""

from __future__ import annotations

import time
from typing import Any

from conxa_core.db import db_get, db_set
from app.services.saas import Principal
from app.services.tracking import (
    _DAY_MS,
    _assertion_health_by_step,
    _proof_health_by_step,
    _dashboard_metrics,
    _drift_review_queue,
    _epoch_ms,
    event_recovery_tier,
    event_recovery_type,
    _event_step_index,
    _number,
    _record_time_ms,
    _run_has_recovery,
    _visible_run_records,
    _visible_runtime_registrations,
)

ROI_NAMESPACE = "roi_assumptions"

_HOUR_MS = 3_600_000

_TIER_ORDER = ("Tier A", "Tier B")
_ZERO_TOKEN_TIERS = ("Tier A",)  # in-process; Tier B is the agent-mediated handoff

_RANGES: dict[str, tuple[int, str]] = {
    "24h": (1, "hour"),
    "7d": (7, "day"),
    "30d": (30, "day"),
    "90d": (90, "day"),
}

# Health-score factor weights. Sum to 100 so the score reads as a percentage and each
# factor's contribution is directly comparable in the UI breakdown.
_HEALTH_WEIGHTS = {
    "success_rate": 40,
    "assertion_pass_rate": 20,
    "drift_pressure": 15,
    "agent_dependence": 15,
    "runtime_freshness": 10,
}


# ---------------------------------------------------------------------------
# range + bucketing
# ---------------------------------------------------------------------------

def range_spec(value: str) -> dict[str, Any]:
    """Normalize a range token into ``{token, days, granularity, bucket_ms, buckets}``."""
    token = str(value or "").lower().strip()
    if token not in _RANGES:
        token = "7d"
    days, granularity = _RANGES[token]
    bucket_ms = _HOUR_MS if granularity == "hour" else _DAY_MS
    return {
        "token": token,
        "days": days,
        "granularity": granularity,
        "bucket_ms": bucket_ms,
        "buckets": (days * _DAY_MS) // bucket_ms,
    }


def _bucket_key(epoch_ms: int, granularity: str) -> str:
    fmt = "%Y-%m-%dT%H" if granularity == "hour" else "%Y-%m-%d"
    return time.strftime(fmt, time.gmtime(epoch_ms / 1000))


def window_records(records: list[dict[str, Any]], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """Records whose run time falls in ``[start_ms, end_ms)``."""
    return [r for r in records if start_ms <= _record_time_ms(r) < end_ms]


def bucket_series(
    records: list[dict[str, Any]],
    *,
    end_ms: int,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bucketed execution counts over the window ending at ``end_ms``.

    Buckets are pre-seeded so a quiet day renders as a zero, not a gap — a sparkline
    that silently drops empty periods misreports the shape of the trend.
    """
    bucket_ms = int(spec["bucket_ms"])
    count = int(spec["buckets"])
    granularity = str(spec["granularity"])

    buckets: dict[str, dict[str, Any]] = {}
    for i in range(count):
        at = end_ms - ((count - 1 - i) * bucket_ms)
        key = _bucket_key(at, granularity)
        buckets[key] = {
            "bucket": key,
            "at": at,
            "executions": 0,
            "successful": 0,
            "failed": 0,
            "recovered": 0,
            "duration_total": 0,
            "duration_count": 0,
        }

    for record in records:
        key = _bucket_key(_record_time_ms(record), granularity)
        entry = buckets.get(key)
        if entry is None:
            continue
        summary = record.get("summary") or {}
        entry["executions"] += 1
        if summary.get("status") == "ok":
            entry["successful"] += 1
        elif summary.get("status") == "fail":
            entry["failed"] += 1
        if _run_has_recovery(record):
            entry["recovered"] += 1
        duration = int(_number(summary.get("duration_ms")))
        if duration > 0:
            entry["duration_total"] += duration
            entry["duration_count"] += 1

    rows = []
    for entry in buckets.values():
        completed = entry["successful"] + entry["failed"]
        rows.append({
            "bucket": entry["bucket"],
            "at": entry["at"],
            "executions": entry["executions"],
            "successful": entry["successful"],
            "failed": entry["failed"],
            "recovered": entry["recovered"],
            "success_rate": round((entry["successful"] / completed) * 100, 1) if completed else None,
            "avg_duration": round(entry["duration_total"] / entry["duration_count"]) if entry["duration_count"] else 0,
        })
    return rows


# ---------------------------------------------------------------------------
# shared derivations
# ---------------------------------------------------------------------------

def _percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile. Empty input is 0, not an error."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(round((pct / 100) * len(ordered) + 0.5))))
    return int(ordered[rank - 1])


def _completed(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if (r.get("summary") or {}).get("status") in {"ok", "fail"}]


# A run stuck at status "running" for longer than this with no fresh activity is abandoned
# (its evidence never arrived), not merely slow. 10 minutes comfortably exceeds
# CONXA_EXECUTION_DEADLINE_MS's 210s default (server.js) with room for the receipt/spill
# retry path to still catch up.
_ABANDONED_GRACE_MS = 10 * 60 * 1000


def reconciliation(records: list[dict[str, Any]], *, now_ms: int | None = None) -> dict[str, Any]:
    """PROD-18 criterion 1: distinguish "still running" from "evidence never arrived" —
    today `_completed` silently excludes both from every rate, with no way to tell them
    apart. Reuses the `records` list callers already computed via `_visible_run_records`
    (the one expensive KV scan the whole dashboard shares) — never re-scans.

    Also folds each run's `_run_summary`-computed `chain` verdict (see
    `tracking._chain_state`) into fleet-wide counts, so a batch that never arrived (`gap`)
    or one that failed its own signature check (`broken`) is visible without opening every
    run individually.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    completed = 0
    abandoned: list[dict[str, Any]] = []
    in_flight = 0
    chain_counts: dict[str, int] = {"verified": 0, "gap": 0, "broken": 0, "none": 0}
    gap_runs: list[dict[str, Any]] = []
    broken_runs: list[dict[str, Any]] = []

    for record in records:
        summary = record.get("summary") or {}
        run_id = str(summary.get("run_id", ""))
        if summary.get("status") in ("ok", "fail"):
            completed += 1
        elif now - _record_time_ms(record) > _ABANDONED_GRACE_MS:
            abandoned.append({
                "run_id": run_id,
                "workflow_id": summary.get("workflow_id", ""),
                "company": record.get("company", ""),
                "last_seen": _record_time_ms(record),
            })
        else:
            in_flight += 1

        chain = summary.get("chain") or {"state": "none"}
        state = str(chain.get("state", "none"))
        chain_counts[state] = chain_counts.get(state, 0) + 1
        if state == "gap":
            gap_runs.append({"run_id": run_id, "missing_seqs": chain.get("missing_seqs", [])})
        elif state == "broken":
            broken_runs.append({"run_id": run_id})

    return {
        "runs_started": len(records),
        "runs_completed": completed,
        "runs_abandoned": len(abandoned),
        "runs_in_flight": in_flight,
        "abandoned_runs": abandoned[:50],
        "chain": {**chain_counts, "gap_runs": gap_runs[:50], "broken_runs": broken_runs[:50]},
    }


def _success_rate(records: list[dict[str, Any]]) -> float:
    completed = _completed(records)
    if not completed:
        return 0.0
    ok = sum(1 for r in completed if (r.get("summary") or {}).get("status") == "ok")
    return round((ok / len(completed)) * 100, 1)


def _workflow_key(record: dict[str, Any]) -> tuple[str, str]:
    summary = record.get("summary") or {}
    return (
        str(record.get("company") or ""),
        str(summary.get("workflow_id") or "Unknown workflow"),
    )


def step_recovery_paths(record: dict[str, Any]) -> dict[int | None, dict[str, Any]]:
    """Per-step recovery journey for one run.

    Returns ``{step_index: {"tiers": [...], "failed": bool}}`` covering only steps that
    actually entered recovery. ``tiers`` is the ordered, consecutive-deduplicated tier
    sequence observed for that step — the path a Sankey needs.

    Outcome is derived from ``step_fail`` / the run's ``failed_step_id`` rather than from
    ``step_ok``: ``run.js`` does not reliably emit a per-step success event, so treating a
    recovered step as healed unless it is positively known to have failed is the only rule
    that holds across runtime versions.
    """
    events = sorted(record.get("events") or [], key=lambda e: _number(e.get("ts")))
    summary = record.get("summary") or {}

    paths: dict[int | None, dict[str, Any]] = {}
    for evt in events:
        step_index = _event_step_index(evt)
        recovery_type = event_recovery_type(evt)
        if recovery_type:
            entry = paths.setdefault(step_index, {"tiers": [], "failed": False})
            tier = event_recovery_tier(evt, recovery_type)
            if tier in _TIER_ORDER and (not entry["tiers"] or entry["tiers"][-1] != tier):
                entry["tiers"].append(tier)
        if evt.get("e") == "step_fail" and step_index in paths:
            paths[step_index]["failed"] = True

    failed_step = summary.get("failed_step_id")
    if failed_step is not None:
        try:
            failed_index = int(failed_step)
        except (TypeError, ValueError):
            failed_index = None
        if failed_index is not None and failed_index in paths:
            paths[failed_index]["failed"] = True

    return {k: v for k, v in paths.items() if v["tiers"]}


# ---------------------------------------------------------------------------
# KPI strip
# ---------------------------------------------------------------------------

def kpi_strip(
    current: list[dict[str, Any]],
    previous: list[dict[str, Any]],
    series: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """KPIs with a prior-equal-period comparison and a sparkline series.

    ``direction`` tells the UI which way is good, so a rising failure count and a rising
    success rate don't both get painted green.
    """
    def avg_duration(records: list[dict[str, Any]]) -> int:
        durations = [
            int(_number((r.get("summary") or {}).get("duration_ms")))
            for r in _completed(records)
            if _number((r.get("summary") or {}).get("duration_ms")) > 0
        ]
        return round(sum(durations) / len(durations)) if durations else 0

    def recovery_rate(records: list[dict[str, Any]]) -> float:
        if not records:
            return 0.0
        return round((sum(1 for r in records if _run_has_recovery(r)) / len(records)) * 100, 1)

    def failed(records: list[dict[str, Any]]) -> int:
        return sum(1 for r in records if (r.get("summary") or {}).get("status") == "fail")

    specs = [
        ("executions", "Executions", "count", "up_good", len(current), len(previous),
         [b["executions"] for b in series]),
        ("success_rate", "Success rate", "percent", "up_good", _success_rate(current), _success_rate(previous),
         [b["success_rate"] if b["success_rate"] is not None else 0 for b in series]),
        ("failed_executions", "Failed runs", "count", "down_good", failed(current), failed(previous),
         [b["failed"] for b in series]),
        ("recovery_rate", "Runs self-healed", "percent", "up_good", recovery_rate(current), recovery_rate(previous),
         [b["recovered"] for b in series]),
        ("average_execution_time", "Avg duration", "duration", "down_good", avg_duration(current), avg_duration(previous),
         [b["avg_duration"] for b in series]),
    ]

    rows = []
    for key, label, unit, direction, value, prev, points in specs:
        if prev:
            delta_pct = round(((value - prev) / prev) * 100, 1)
        else:
            delta_pct = None
        rows.append({
            "key": key,
            "label": label,
            "unit": unit,
            "direction": direction,
            "value": value,
            "previous": prev,
            "delta": round(value - prev, 1),
            "delta_pct": delta_pct,
            "series": points,
        })
    return rows


# ---------------------------------------------------------------------------
# health score
# ---------------------------------------------------------------------------

def health_score(
    records: list[dict[str, Any]],
    *,
    assertion_rows: list[dict[str, Any]],
    drift_rows: list[dict[str, Any]],
    registrations: list[dict[str, Any]],
    now_ms: int,
) -> dict[str, Any]:
    """Explainable 0-100 platform health score.

    Every factor reports its own weight, raw value, and contribution so the UI can show the
    breakdown. A score nobody can decompose is a score nobody trusts — and an operator who
    can't see which factor moved can't act on it.
    """
    if not records:
        return {
            "score": None,
            "grade": "No telemetry",
            "factors": [],
            "summary": "No production executions in this period.",
        }

    success = _success_rate(records)

    assertion_total = sum(int(_number(r.get("total"))) for r in assertion_rows)
    assertion_passed = sum(int(_number(r.get("passed"))) for r in assertion_rows)
    assertion_value = round((assertion_passed / assertion_total) * 100, 1) if assertion_total else 100.0

    worst_drift = max((float(_number(r.get("occurrence_rate_pct"))) for r in drift_rows), default=0.0)
    drift_value = round(max(0.0, 100.0 - worst_drift), 1)

    tier_counts = recovery_tier_totals(records)
    total_recoveries = sum(tier_counts.values())
    agent_recoveries = tier_counts.get("Tier B", 0)
    agent_share = round((agent_recoveries / total_recoveries) * 100, 1) if total_recoveries else 0.0
    agent_value = round(100.0 - agent_share, 1)

    stale_cutoff = now_ms - (30 * _DAY_MS)
    fresh = sum(1 for reg in registrations if _epoch_ms(reg.get("last_seen")) >= stale_cutoff)
    freshness_value = round((fresh / len(registrations)) * 100, 1) if registrations else 100.0

    raw = [
        ("success_rate", "Execution success", success,
         f"{success}% of completed runs finished cleanly"),
        ("assertion_pass_rate", "Assertion pass rate", assertion_value,
         f"{assertion_passed} of {assertion_total} post-step checks passed" if assertion_total
         else "No assertion checks reported yet"),
        ("drift_pressure", "Drift resistance", drift_value,
         f"Worst step needs recovery on {worst_drift}% of runs" if drift_rows
         else "No steps showing repeat drift"),
        ("agent_dependence", "Zero-token healing", agent_value,
         f"{agent_share}% of recoveries needed an agent (Tier B)" if total_recoveries
         else "No recoveries needed in this period"),
        ("runtime_freshness", "Runtime freshness", freshness_value,
         f"{fresh} of {len(registrations)} runtimes reported in the last 30 days" if registrations
         else "No runtimes registered yet"),
    ]

    factors = []
    score = 0.0
    for key, label, value, detail in raw:
        weight = _HEALTH_WEIGHTS[key]
        clamped = max(0.0, min(100.0, float(value)))
        contribution = round((clamped / 100.0) * weight, 1)
        score += contribution
        factors.append({
            "key": key,
            "label": label,
            "weight": weight,
            "value": round(clamped, 1),
            "contribution": contribution,
            "detail": detail,
        })

    score = round(score)
    if score >= 90:
        grade, summary = "Excellent", "Platform is operating within target on every factor."
    elif score >= 75:
        grade, summary = "Healthy", "Platform is healthy. Watch the lowest-scoring factor."
    elif score >= 60:
        grade, summary = "Degraded", "Reliability is usable but slipping. Review the risk queue."
    else:
        grade, summary = "Critical", "Execution health is below target. Prioritise failures before new rollout."

    return {"score": score, "grade": grade, "factors": factors, "summary": summary}


# ---------------------------------------------------------------------------
# workflow + step analytics
# ---------------------------------------------------------------------------

def recovery_tier_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    """Total recovery events per tier across all records."""
    totals = {tier: 0 for tier in _TIER_ORDER}
    for record in records:
        for evt in record.get("events") or []:
            recovery_type = event_recovery_type(evt)
            if not recovery_type:
                continue
            tier = event_recovery_tier(evt, recovery_type)
            if tier in totals:
                totals[tier] += 1
    return totals


def workflow_analytics(
    records: list[dict[str, Any]],
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Per-skill rollups, newest-activity first, with a per-version breakdown.

    ``workflow_id`` in telemetry is the individual skill's slug (``runtime/server.js`` passes
    ``entry.slug``), so grouping by it gives genuine per-skill attribution. The nested
    version rows are what make a regression visible: "v0.3.0 is 8 points worse than v0.2.0"
    is actionable in a way that a single blended success rate never is.
    """
    prev_rates: dict[tuple[str, str], float] = {}
    prev_runs: dict[tuple[str, str], int] = {}
    for record in previous or []:
        key = _workflow_key(record)
        prev_runs[key] = prev_runs.get(key, 0) + 1
    for key in prev_runs:
        prev_rates[key] = _success_rate([r for r in (previous or []) if _workflow_key(r) == key])

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(_workflow_key(record), []).append(record)

    rows: list[dict[str, Any]] = []
    for (company, workflow), group in grouped.items():
        durations = [
            int(_number((r.get("summary") or {}).get("duration_ms")))
            for r in _completed(group)
            if _number((r.get("summary") or {}).get("duration_ms")) > 0
        ]
        recovered = sum(1 for r in group if _run_has_recovery(r))
        ok_runs = sum(1 for r in group if (r.get("summary") or {}).get("status") == "ok")
        failed_runs = sum(1 for r in group if (r.get("summary") or {}).get("status") == "fail")
        success = _success_rate(group)
        prev_success = prev_rates.get((company, workflow))

        by_version: dict[str, list[dict[str, Any]]] = {}
        for record in group:
            version = str((record.get("summary") or {}).get("workflow_ver") or "unknown")
            by_version.setdefault(version, []).append(record)

        versions = [
            {
                "version": version,
                "runs": len(bucket),
                "success_rate": _success_rate(bucket),
                "recovery_rate": round((sum(1 for r in bucket if _run_has_recovery(r)) / len(bucket)) * 100, 1),
                "last_seen": max(_record_time_ms(r) for r in bucket),
                # First run seen inside the window — not a publish time.
                "first_seen": min(_record_time_ms(r) for r in bucket),
            }
            for version, bucket in by_version.items()
        ]
        versions.sort(key=lambda v: v["last_seen"], reverse=True)

        rows.append({
            "company": company,
            "workflow": workflow,
            "runs": len(group),
            "successful": ok_runs,
            "failed": failed_runs,
            "success_rate": success,
            "previous_success_rate": prev_success,
            "success_rate_delta": round(success - prev_success, 1) if prev_success is not None else None,
            "recovery_rate": round((recovered / len(group)) * 100, 1) if group else 0.0,
            "unattended_rate": round((ok_runs / len(group)) * 100, 1) if group else 0.0,
            "p50_duration": _percentile(durations, 50),
            "p95_duration": _percentile(durations, 95),
            "last_seen": max(_record_time_ms(r) for r in group),
            "versions": versions,
        })

    rows.sort(key=lambda r: (r["runs"], r["last_seen"]), reverse=True)
    return rows


def step_analytics(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-step reliability inside one workflow's records, worst first.

    Attempts are counted from runs that reached the step (``total_steps`` on the run summary),
    which is the only step-reach signal the current event schema carries.
    """
    steps: dict[int | None, dict[str, Any]] = {}

    def entry_for(step_index: int | None) -> dict[str, Any]:
        return steps.setdefault(step_index, {
            "step_index": step_index,
            "step_label": f"Step {step_index + 1}" if step_index is not None else "Unknown step",
            "attempts": 0,
            "failures": 0,
            "recoveries": 0,
            "tier_counts": {tier: 0 for tier in _TIER_ORDER},
            "assertions_total": 0,
            "assertions_passed": 0,
            "advisory_failures": 0,
            "failure_codes": {},
            "last_seen": 0,
        })

    for record in records:
        summary = record.get("summary") or {}
        total_steps = int(_number(summary.get("total_steps")))
        reached = total_steps if total_steps > 0 else 0
        failed_step = summary.get("failed_step_id")
        try:
            failed_index = int(failed_step) if failed_step is not None else None
        except (TypeError, ValueError):
            failed_index = None
        if failed_index is not None:
            reached = max(reached, failed_index + 1)
        for index in range(reached):
            entry_for(index)["attempts"] += 1

        for evt in record.get("events") or []:
            step_index = _event_step_index(evt)
            code = str(evt.get("e") or "")
            ts = _epoch_ms(evt.get("ts")) or _record_time_ms(record)

            recovery_type = event_recovery_type(evt)
            if recovery_type:
                entry = entry_for(step_index)
                entry["recoveries"] += 1
                tier = event_recovery_tier(evt, recovery_type)
                if tier in entry["tier_counts"]:
                    entry["tier_counts"][tier] += 1
                entry["last_seen"] = max(entry["last_seen"], ts)
            elif code == "verify_result":
                entry = entry_for(step_index)
                entry["assertions_total"] += 1
                if evt.get("ok"):
                    entry["assertions_passed"] += 1
                entry["advisory_failures"] += int(_number(evt.get("advFail")))
                entry["last_seen"] = max(entry["last_seen"], ts)
            elif code == "step_fail":
                entry = entry_for(step_index)
                entry["failures"] += 1
                failure_code = str(evt.get("fc") or evt.get("code") or "unknown")
                entry["failure_codes"][failure_code] = entry["failure_codes"].get(failure_code, 0) + 1
                entry["last_seen"] = max(entry["last_seen"], ts)

        if failed_index is not None:
            entry = entry_for(failed_index)
            if entry["failures"] == 0:
                entry["failures"] += 1
                failure_code = str(summary.get("failure_code") or "unknown")
                entry["failure_codes"][failure_code] = entry["failure_codes"].get(failure_code, 0) + 1
                entry["last_seen"] = max(entry["last_seen"], _record_time_ms(record))

    rows = []
    for entry in steps.values():
        attempts = entry["attempts"]
        assertions_total = entry["assertions_total"]
        dominant = max(entry["failure_codes"].items(), key=lambda kv: kv[1], default=("", 0))
        rows.append({
            "step_index": entry["step_index"],
            "step_label": entry["step_label"],
            "attempts": attempts,
            "failures": entry["failures"],
            "recoveries": entry["recoveries"],
            "success_rate": round(((attempts - entry["failures"]) / attempts) * 100, 1) if attempts else 100.0,
            "recovery_rate": round((entry["recoveries"] / attempts) * 100, 1) if attempts else 0.0,
            "tier_counts": [
                {"tier": tier, "count": entry["tier_counts"][tier]}
                for tier in _TIER_ORDER
                if entry["tier_counts"][tier] > 0
            ],
            "assertion_pass_rate": (
                round((entry["assertions_passed"] / assertions_total) * 100, 1) if assertions_total else None
            ),
            "assertions_total": assertions_total,
            "advisory_failures": entry["advisory_failures"],
            "dominant_failure_code": dominant[0],
            "last_seen": entry["last_seen"],
        })

    rows.sort(key=lambda r: (r["success_rate"], -r["recoveries"]))
    return rows


# ---------------------------------------------------------------------------
# recovery cascade (Sankey)
# ---------------------------------------------------------------------------

def recovery_cascade(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Sankey nodes/links for the recovery ladder.

    Only steps that actually entered recovery are in the flow — including the (vastly larger)
    directly-resolved population would compress the interesting part to invisibility. That
    count is returned alongside as ``resolved_directly`` so the panel can state the ratio in
    words instead of drowning it in a diagram.

    The node order is strictly layered (Entered → Tier A → Tier B → Healed/Failed), which
    keeps the graph acyclic as any Sankey layout requires.
    """
    node_names = ["Entered recovery", *_TIER_ORDER, "Healed", "Failed"]
    index = {name: i for i, name in enumerate(node_names)}
    links: dict[tuple[int, int], int] = {}

    def add(source: str, target: str) -> None:
        key = (index[source], index[target])
        links[key] = links.get(key, 0) + 1

    entered = 0
    healed = 0
    failed = 0
    zero_token_heals = 0
    agent_assisted = 0
    tier_touch = {tier: 0 for tier in _TIER_ORDER}

    for record in records:
        for path in step_recovery_paths(record).values():
            tiers = path["tiers"]
            if not tiers:
                continue
            entered += 1
            add("Entered recovery", tiers[0])
            for earlier, later in zip(tiers, tiers[1:]):
                add(earlier, later)
            for tier in set(tiers):
                tier_touch[tier] += 1
            outcome = "Failed" if path["failed"] else "Healed"
            add(tiers[-1], outcome)

            # A step counts as a free heal only if it healed AND never reached a paid tier.
            # Summing Tier A event hits instead would double-count a step that tried two
            # in-process methods, and would credit a step that started at A and only
            # succeeded after escalating to a model — neither of which is a recovery that cost nothing.
            used_paid_tier = any(tier not in _ZERO_TOKEN_TIERS for tier in tiers)
            if used_paid_tier:
                agent_assisted += 1
            if path["failed"]:
                failed += 1
            else:
                healed += 1
                if not used_paid_tier:
                    zero_token_heals += 1

    total_steps_run = sum(
        int(_number((r.get("summary") or {}).get("total_steps"))) for r in records
    )

    return {
        "nodes": [{"name": name} for name in node_names],
        "links": [
            {"source": source, "target": target, "value": value}
            for (source, target), value in sorted(links.items())
        ],
        "entered_recovery": entered,
        "healed": healed,
        "failed": failed,
        "heal_rate": round((healed / entered) * 100, 1) if entered else 0.0,
        "resolved_directly": max(0, total_steps_run - entered),
        "tier_touch": [{"tier": tier, "steps": tier_touch[tier]} for tier in _TIER_ORDER],
        "zero_token_heals": zero_token_heals,
        "agent_assisted": agent_assisted,
    }


# ---------------------------------------------------------------------------
# heatmap + failure codes
# ---------------------------------------------------------------------------

def reliability_heatmap(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Day-of-week × hour-of-day reliability grid (UTC).

    Answers "when does automation get flaky" — a batch window that only fails at 02:00 or a
    weekend backlog spike is invisible in a daily trend line but obvious here.
    """
    cells: dict[tuple[int, int], dict[str, int]] = {}
    for record in records:
        at = _record_time_ms(record)
        if at <= 0:
            continue
        stamp = time.gmtime(at / 1000)
        key = (stamp.tm_wday, stamp.tm_hour)
        cell = cells.setdefault(key, {"runs": 0, "successful": 0, "failed": 0})
        cell["runs"] += 1
        status = (record.get("summary") or {}).get("status")
        if status == "ok":
            cell["successful"] += 1
        elif status == "fail":
            cell["failed"] += 1

    rows = []
    for (weekday, hour), cell in sorted(cells.items()):
        completed = cell["successful"] + cell["failed"]
        rows.append({
            "weekday": weekday,
            "hour": hour,
            "runs": cell["runs"],
            "successful": cell["successful"],
            "failed": cell["failed"],
            "success_rate": round((cell["successful"] / completed) * 100, 1) if completed else None,
        })
    return {"cells": rows, "max_runs": max((r["runs"] for r in rows), default=0)}


def failure_codes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failure counts by reason code, most common first.

    ``timeout`` dominating and ``url_mismatch`` dominating call for completely different
    fixes, so the blended "failed runs" number is not actionable on its own.
    """
    counts: dict[str, dict[str, Any]] = {}
    for record in records:
        summary = record.get("summary") or {}
        if summary.get("status") != "fail":
            continue
        code = str(summary.get("failure_code") or "unknown")
        entry = counts.setdefault(code, {"code": code, "count": 0, "last_seen": 0, "workflows": set()})
        entry["count"] += 1
        entry["last_seen"] = max(entry["last_seen"], _record_time_ms(record))
        entry["workflows"].add(str(summary.get("workflow_id") or ""))

    rows = [
        {
            "code": entry["code"],
            "count": entry["count"],
            "last_seen": entry["last_seen"],
            "workflow_count": len({w for w in entry["workflows"] if w}),
        }
        for entry in counts.values()
    ]
    rows.sort(key=lambda r: (r["count"], r["last_seen"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------

DEFAULT_ROI_ASSUMPTIONS: dict[str, Any] = {
    "default_minutes": 8,
    "hourly_rate": 65,
    "currency": "USD",
    "per_workflow": {},
}

# ponytail: fixed assumption, not admin-editable. Replace with a real measured figure if
# prompt-to-execute latency ever gets instrumented — out of scope for now (nothing today
# times the gap between a chat message and execute_skill landing).
HUMAN_OVERSIGHT_SECONDS = 20


def normalize_assumptions(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Merge stored ROI assumptions over the defaults, coercing bad input rather than raising."""
    merged = {**DEFAULT_ROI_ASSUMPTIONS, "per_workflow": {}}
    if isinstance(stored, dict):
        merged["default_minutes"] = max(0.0, _number(stored.get("default_minutes"), 8))
        merged["hourly_rate"] = max(0.0, _number(stored.get("hourly_rate"), 65))
        merged["currency"] = str(stored.get("currency") or "USD")[:8]
        per_workflow = stored.get("per_workflow")
        if isinstance(per_workflow, dict):
            merged["per_workflow"] = {
                str(key): max(0.0, _number(value))
                for key, value in per_workflow.items()
                if _number(value) > 0
            }
        for passthrough in ("updated_at", "updated_by"):
            if stored.get(passthrough) is not None:
                merged[passthrough] = stored[passthrough]
    return merged


def roi(records: list[dict[str, Any]], assumptions: dict[str, Any]) -> dict[str, Any]:
    """Business impact, split into measured facts and estimate-derived value.

    ``hours_saved`` depends on an admin-supplied "minutes a human used to spend" figure —
    telemetry has no such signal. Everything under ``measured`` is derived purely from
    telemetry, so the UI can present the two with different confidence and never let an
    assumption masquerade as a measurement.
    """
    settings = normalize_assumptions(assumptions)
    per_workflow = settings["per_workflow"]
    default_minutes = float(settings["default_minutes"])

    by_workflow: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if (record.get("summary") or {}).get("status") != "ok":
            continue
        company, workflow = _workflow_key(record)
        minutes = float(per_workflow.get(f"{company}/{workflow}", default_minutes))
        entry = by_workflow.setdefault((company, workflow), {
            "company": company,
            "workflow": workflow,
            "runs": 0,
            "minutes_per_run": minutes,
            "is_estimate_default": f"{company}/{workflow}" not in per_workflow,
            "minutes_saved": 0.0,
            "duration_ms_total": 0.0,
        })
        entry["runs"] += 1
        entry["minutes_saved"] += minutes
        entry["duration_ms_total"] += _number((record.get("summary") or {}).get("duration_ms"))

    # Sourced from the cascade rather than counted here, so the Impact page and the
    # Self-healing page can never quote different numbers for the same thing. Counting tier
    # events instead would report every attempt, not every step actually healed for free.
    cascade = recovery_cascade(records)

    unattended = sum(1 for r in records if (r.get("summary") or {}).get("status") == "ok")
    self_healed_runs = sum(
        1 for r in records
        if (r.get("summary") or {}).get("status") == "ok" and _run_has_recovery(r)
    )

    rows = sorted(by_workflow.values(), key=lambda r: r["minutes_saved"], reverse=True)
    for row in rows:
        # Net out the human's remaining hands-on time (they still have to kick each
        # run off) — see HUMAN_OVERSIGHT_SECONDS.
        oversight_minutes = row["runs"] * HUMAN_OVERSIGHT_SECONDS / 60
        row["minutes_saved"] = max(0.0, row["minutes_saved"] - oversight_minutes)
        row["hours_saved"] = round(row["minutes_saved"] / 60, 1)
        row["automated_minutes"] = round(row.pop("duration_ms_total") / 60000, 2)
    minutes_saved = sum(row["minutes_saved"] for row in rows)
    hours_saved = round(minutes_saved / 60, 1)

    return {
        "assumptions": settings,
        "estimated": {
            "hours_saved": hours_saved,
            "value_amount": round(hours_saved * float(settings["hourly_rate"])),
            "currency": settings["currency"],
            "by_workflow": rows[:12],
        },
        "measured": {
            "unattended_completions": unattended,
            "self_healed_runs": self_healed_runs,
            "zero_token_recoveries": cascade["zero_token_heals"],
            "agent_assisted_recoveries": cascade["agent_assisted"],
        },
    }


# ---------------------------------------------------------------------------
# deterministic insights
# ---------------------------------------------------------------------------

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def insights(
    *,
    workflows: list[dict[str, Any]],
    assertion_rows: list[dict[str, Any]],
    drift_rows: list[dict[str, Any]],
    failure_rows: list[dict[str, Any]],
    tier_totals: dict[str, int],
    stale_runtimes: int,
    metrics: dict[str, Any],
    previous_records: list[dict[str, Any]],
    current_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rule-derived operational insights, most severe first.

    Deterministic on purpose: every statement is computed from a number already on this page
    and carries the route that proves it, so an operator can click straight through to the
    evidence. No model is consulted and nothing here can assert something the data doesn't
    support.
    """
    found: list[dict[str, Any]] = []

    def add(severity: str, key: str, title: str, body: str, metric: str, evidence: str) -> None:
        found.append({
            "id": key,
            "severity": severity,
            "title": title,
            "body": body,
            "metric": metric,
            "evidence": evidence,
        })

    # Platform-level success regression.
    prev_rate = _success_rate(previous_records)
    current_rate = _success_rate(current_records)
    if previous_records and current_records and prev_rate - current_rate >= 5:
        add(
            "critical" if prev_rate - current_rate >= 15 else "warning",
            "platform_success_drop",
            "Platform success rate is falling",
            f"Overall success fell from {prev_rate}% to {current_rate}% against the previous equal period.",
            f"-{round(prev_rate - current_rate, 1)} pts",
            "/dashboard/workflows",
        )

    for row in workflows:
        delta = row.get("success_rate_delta")
        if delta is not None and delta <= -10 and row["runs"] >= 5:
            add(
                "critical" if delta <= -25 else "warning",
                f"workflow_drop:{row['company']}:{row['workflow']}",
                f"{row['workflow']} is degrading",
                f"Success rate dropped {abs(delta)} points to {row['success_rate']}% across {row['runs']} runs.",
                f"{row['success_rate']}%",
                f"/dashboard/workflows/{row['company']}/{row['workflow']}",
            )

        # Version regression: newest version materially worse than the one before it.
        versions = [v for v in row.get("versions") or [] if v["runs"] >= 5]
        if len(versions) >= 2 and versions[0]["success_rate"] - versions[1]["success_rate"] <= -5:
            add(
                "critical",
                f"version_regression:{row['company']}:{row['workflow']}",
                f"{row['workflow']} {versions[0]['version']} is worse than {versions[1]['version']}",
                (
                    f"The current version succeeds {versions[0]['success_rate']}% of the time versus "
                    f"{versions[1]['success_rate']}% on the previous one. Consider rolling back."
                ),
                f"{round(versions[0]['success_rate'] - versions[1]['success_rate'], 1)} pts",
                f"/dashboard/workflows/{row['company']}/{row['workflow']}",
            )

        if row["runs"] >= 10 and row["p95_duration"] > 0 and row["p50_duration"] > 0:
            if row["p95_duration"] >= row["p50_duration"] * 4:
                add(
                    "info",
                    f"latency_spread:{row['company']}:{row['workflow']}",
                    f"{row['workflow']} runtime is inconsistent",
                    (
                        f"The slowest runs take {round(row['p95_duration'] / row['p50_duration'], 1)}x longer than "
                        "typical ones, which usually means retries or waits on a slow page."
                    ),
                    f"p95 {row['p95_duration']}ms",
                    f"/dashboard/workflows/{row['company']}/{row['workflow']}",
                )

    for row in assertion_rows:
        if row.get("total", 0) >= 5 and row.get("pass_rate", 100) < 80:
            add(
                "warning",
                f"assertion_decay:{row.get('workflow')}:{row.get('step_index')}",
                f"Checks are failing on {row.get('step_label')}",
                (
                    f"{row.get('step_label')} in {row.get('workflow')} passes its post-step checks only "
                    f"{row.get('pass_rate')}% of the time. This usually precedes a hard failure."
                ),
                f"{row.get('pass_rate')}%",
                f"/dashboard/workflows/{row.get('company')}/{row.get('workflow')}",
            )

    for row in drift_rows[:3]:
        rate = float(_number(row.get("occurrence_rate_pct")))
        if rate >= 20:
            add(
                "warning",
                f"drift:{row.get('workflow_id')}:{row.get('step_id')}",
                f"{row.get('workflow_id')} is drifting",
                (
                    f"Step {row.get('step_id')} needs recovery on {rate}% of runs. The page has likely "
                    "changed — republishing this skill would restore a direct match."
                ),
                f"{rate}%",
                "/dashboard/healing",
            )

    total_recoveries = sum(tier_totals.values())
    agent_recoveries = tier_totals.get("Tier B", 0)
    if total_recoveries >= 10:
        share = round((agent_recoveries / total_recoveries) * 100, 1)
        if share >= 20:
            add(
                "warning",
                "agent_dependence",
                "Self-healing is escalating to the agent too often",
                (
                    f"{share}% of recoveries needed Tier B, which costs model tokens. "
                    "Republishing the affected skills would move them back to free recovery."
                ),
                f"{share}%",
                "/dashboard/healing",
            )

    if failure_rows:
        top = failure_rows[0]
        total_failures = sum(r["count"] for r in failure_rows)
        if total_failures >= 5 and top["count"] / total_failures >= 0.5:
            add(
                "info",
                f"failure_concentration:{top['code']}",
                f"Most failures share one cause: {top['code']}",
                (
                    f"{top['count']} of {total_failures} failures report {top['code']}, across "
                    f"{top['workflow_count']} workflow(s). One fix likely clears most of them."
                ),
                f"{top['count']} failures",
                "/dashboard/workflows",
            )

    if stale_runtimes > 0:
        add(
            "info",
            "stale_runtimes",
            f"{stale_runtimes} runtime(s) have gone quiet",
            "These installs have not reported in over 30 days. They may be uninstalled or offline.",
            f"{stale_runtimes} stale",
            "/dashboard",
        )

    if not found and metrics.get("total_executions"):
        add(
            "info",
            "all_clear",
            "Nothing needs attention",
            "No degrading workflows, decaying checks, or drifting steps in this period.",
            f"{metrics.get('success_rate')}%",
            "/dashboard/workflows",
        )

    found.sort(key=lambda row: _SEVERITY_RANK.get(row["severity"], 3))
    return found[:8]


# ---------------------------------------------------------------------------
# ROI assumption storage
# ---------------------------------------------------------------------------

def read_assumptions(workspace_id: str) -> dict[str, Any]:
    """Stored ROI assumptions for a workspace, defaulted where unset."""
    return normalize_assumptions(db_get(ROI_NAMESPACE, workspace_id))


def write_assumptions(workspace_id: str, payload: Any, *, user_id: str) -> dict[str, Any]:
    """Persist ROI assumptions, stamping who changed them and when.

    Input is normalized rather than trusted: this is an authenticated admin write, but the
    numbers feed a figure customers quote in business reviews, so a malformed rate silently
    becoming ``NaN`` downstream is worth one coercion pass.
    """
    stored = normalize_assumptions(payload if isinstance(payload, dict) else {})
    stored["updated_at"] = time.time()
    stored["updated_by"] = user_id
    db_set(ROI_NAMESPACE, workspace_id, stored)
    return stored


def seed_recorded_baseline(workspace_id: str, skill_slug: str, recording_duration_seconds: float | None) -> None:
    """Auto-fill a workflow's ROI baseline from its actual recording length, the first time
    it's published. Never overwrites an existing entry — an earlier auto-seed or an admin's
    manual edit both win, so a republish can never silently change a number someone is
    already relying on. Writes the raw KV row (not through write_assumptions) since this is a
    system seed, not an admin action — it must not stamp updated_by/updated_at as if a person
    edited the assumptions.
    """
    if not recording_duration_seconds or recording_duration_seconds <= 0:
        return
    key = f"{workspace_id}/{skill_slug}"
    stored = db_get(ROI_NAMESPACE, workspace_id)
    stored = dict(stored) if isinstance(stored, dict) else {}
    per_workflow = stored.get("per_workflow")
    per_workflow = dict(per_workflow) if isinstance(per_workflow, dict) else {}
    if key in per_workflow:
        return
    per_workflow[key] = round(recording_duration_seconds / 60, 2)
    stored["per_workflow"] = per_workflow
    db_set(ROI_NAMESPACE, workspace_id, stored)


# ---------------------------------------------------------------------------
# composition — one KV scan per request
# ---------------------------------------------------------------------------

def _stale_runtime_count(registrations: list[dict[str, Any]], now_ms: int) -> int:
    cutoff = now_ms - (30 * _DAY_MS)
    return sum(1 for reg in registrations if _epoch_ms(reg.get("last_seen")) < cutoff)


def _activity_row(record: dict[str, Any]) -> dict[str, Any]:
    summary = record.get("summary") or {}
    tiers = sorted({
        tier
        for path in step_recovery_paths(record).values()
        for tier in path["tiers"]
    }, key=lambda t: _TIER_ORDER.index(t) if t in _TIER_ORDER else 99)
    return {
        "run_id": summary.get("run_id", ""),
        "company": str(record.get("company") or ""),
        "workflow": str(summary.get("workflow_id") or "Unknown workflow"),
        "version": str(summary.get("workflow_ver") or ""),
        "runtime_version": str(summary.get("runtime_ver") or ""),
        "status": summary.get("status", "running"),
        "duration_ms": int(_number(summary.get("duration_ms"))),
        "total_steps": int(_number(summary.get("total_steps"))),
        "recovered_steps": int(_number(summary.get("recovered_steps"))),
        "failed_step_id": summary.get("failed_step_id"),
        "failure_code": summary.get("failure_code"),
        "recovery_tiers": tiers,
        "at": _record_time_ms(record),
    }


def run_step_flow(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-step outcome for a single run, for the execution-flow view.

    Lives here rather than in the frontend because deciding what counts as a recovery — and
    which tier it was — is the same classification the aggregates use. Reimplementing it in
    TypeScript would give the run view and the dashboard two ways to disagree about the same
    events.

    Steps after the failing one are reported as ``not_reached`` rather than silently omitted:
    a 12-step workflow that died at step 3 should still show all twelve, or the reader can't
    tell how far it got.
    """
    summary = record.get("summary") or {}
    paths = step_recovery_paths(record)

    failed_index: int | None = None
    raw_failed = summary.get("failed_step_id")
    if raw_failed is not None:
        try:
            failed_index = int(raw_failed)
        except (TypeError, ValueError):
            failed_index = None

    assertions: dict[int | None, dict[str, int]] = {}
    for evt in record.get("events") or []:
        if evt.get("e") != "verify_result":
            continue
        entry = assertions.setdefault(_event_step_index(evt), {"passed": 0, "failed": 0})
        if evt.get("ok"):
            entry["passed"] += 1
        else:
            entry["failed"] += 1

    known = [i for i in list(paths) + list(assertions) if isinstance(i, int)]
    total = int(_number(summary.get("total_steps")))
    count = max([total, (failed_index + 1) if failed_index is not None else 0, *(i + 1 for i in known)] or [0])

    rows: list[dict[str, Any]] = []
    for index in range(count):
        if failed_index is not None and index == failed_index:
            status = "failed"
        elif failed_index is not None and index > failed_index:
            status = "not_reached"
        elif index in paths:
            status = "recovered"
        else:
            status = "ok"
        counts = assertions.get(index, {"passed": 0, "failed": 0})
        rows.append({
            "index": index,
            "label": f"Step {index + 1}",
            "status": status,
            "tiers": paths.get(index, {}).get("tiers", []),
            "assertionsPassed": counts["passed"],
            "assertionsFailed": counts["failed"],
        })
    return rows


def _with_previous_pass_rate(
    rows: list[dict[str, Any]],
    previous_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach last period's pass rate to each assertion row, so decay reads as a change."""
    previous = {
        (r["company"], r["workflow"], r["step_index"]): r["pass_rate"]
        for r in _assertion_health_by_step(previous_records, limit=None)
    }
    return [
        {**row, "previous_pass_rate": previous.get((row["company"], row["workflow"], row["step_index"]))}
        for row in rows
    ]


def _with_previous_unproven_rate(
    rows: list[dict[str, Any]],
    previous_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach last period's unproven rate to each proof row, so a check getting worse reads as a change."""
    previous = {
        (r["company"], r["workflow"], r["step_index"]): r["unproven_rate"]
        for r in _proof_health_by_step(previous_records, limit=None)
    }
    return [
        {**row, "previous_unproven_rate": previous.get((row["company"], row["workflow"], row["step_index"]), 0.0)}
        for row in rows
    ]


def dashboard(principal: Principal, range_value: str) -> dict[str, Any]:
    """The full Executive Overview payload.

    Deliberately one response rather than six endpoints: ``_visible_run_records`` reads every
    run's event list out of KV, so each additional endpoint would repeat the most expensive
    thing the dashboard does. The scan happens once here and every block is derived from it.
    """
    spec = range_spec(range_value)
    now_ms = int(time.time() * 1000)
    window_ms = spec["days"] * _DAY_MS
    start_ms = now_ms - window_ms

    all_records = _visible_run_records(principal)
    current = window_records(all_records, start_ms, now_ms + 1)
    previous = window_records(all_records, start_ms - window_ms, start_ms)

    base = _dashboard_metrics(principal, spec["token"], records=all_records)
    registrations = _visible_runtime_registrations(principal)
    stale_runtimes = _stale_runtime_count(registrations, now_ms)

    series = bucket_series(current, end_ms=now_ms, spec=spec)
    assertion_rows = _with_previous_pass_rate(base.get("assertion_health_by_step") or [], previous)
    drift_rows = _drift_review_queue(current)
    workflows = workflow_analytics(current, previous)
    failure_rows = failure_codes(current)
    tier_totals = recovery_tier_totals(current)

    return {
        **base,
        "range": spec["token"],
        "granularity": spec["granularity"],
        "generated_at": now_ms,
        "series": series,
        "assertion_health_by_step": assertion_rows,
        "proof_health_by_step": _with_previous_unproven_rate(base.get("proof_health_by_step") or [], previous),
        "kpis": kpi_strip(current, previous, series),
        "health": health_score(
            current,
            assertion_rows=assertion_rows,
            drift_rows=drift_rows,
            registrations=registrations,
            now_ms=now_ms,
        ),
        "workflows": workflows[:12],
        "recovery_cascade": recovery_cascade(current),
        "reliability_heatmap": reliability_heatmap(current),
        "failure_codes": failure_rows[:8],
        "roi": roi(current, read_assumptions(principal.workspace_id)),
        "stale_runtimes": stale_runtimes,
        "reconciliation": reconciliation(current, now_ms=now_ms),
        "insights": insights(
            workflows=workflows,
            assertion_rows=assertion_rows,
            drift_rows=drift_rows,
            failure_rows=failure_rows,
            tier_totals=tier_totals,
            stale_runtimes=stale_runtimes,
            metrics=base.get("metrics") or {},
            previous_records=previous,
            current_records=current,
        ),
    }


def activity_feed(principal: Principal, *, limit: int = 50, before: int | None = None) -> dict[str, Any]:
    """Recent runs across every visible company, newest first.

    Separate from ``dashboard`` on purpose: the live feed polls far more often than the
    aggregates change, and it must not drag the whole analytics payload along with it.
    """
    records = _visible_run_records(principal)
    if before:
        records = [r for r in records if _record_time_ms(r) < before]
    page = records[: max(1, min(limit, 200))]
    rows = [_activity_row(record) for record in page]
    return {
        "runs": rows,
        "next_cursor": rows[-1]["at"] if len(page) == len(rows) and rows and len(records) > len(page) else None,
        "generated_at": int(time.time() * 1000),
    }


def workflow_detail(
    principal: Principal,
    company: str,
    slug: str,
    range_value: str,
) -> dict[str, Any]:
    """Step-level analytics for a single skill."""
    spec = range_spec(range_value)
    now_ms = int(time.time() * 1000)
    window_ms = spec["days"] * _DAY_MS
    start_ms = now_ms - window_ms

    all_records = [
        record for record in _visible_run_records(principal)
        if _workflow_key(record) == (company, slug)
    ]
    current = window_records(all_records, start_ms, now_ms + 1)
    previous = window_records(all_records, start_ms - window_ms, start_ms)

    rollups = workflow_analytics(current, previous)
    return {
        "company": company,
        "workflow": slug,
        "range": spec["token"],
        "granularity": spec["granularity"],
        "generated_at": now_ms,
        "summary": rollups[0] if rollups else None,
        "series": bucket_series(current, end_ms=now_ms, spec=spec),
        "steps": step_analytics(current),
        "recovery_cascade": recovery_cascade(current),
        "failure_codes": failure_codes(current),
        "assertion_health": _with_previous_pass_rate(_assertion_health_by_step(current), previous),
        "recent_runs": [_activity_row(record) for record in current[:20]],
    }
