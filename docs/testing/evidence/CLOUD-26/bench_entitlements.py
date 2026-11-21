"""Benchmark GET /api/v1/entitlements/current on the filesystem store vs a local Postgres.

usage: python bench_entitlements.py <fs|pg> <tenants> <data_dir> [database_url]
Env must be set before conxa_core imports, so this is one process per (store, scale).
"""
from __future__ import annotations

import json, os, statistics, sys, time

store, tenants, data_dir = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.environ["SKILL_DATA_DIR"] = data_dir
os.environ["SKILL_DATABASE_URL"] = sys.argv[4] if store == "pg" else ""
os.environ["SKILL_AUTH_REQUIRED"] = "false"
os.environ["SKILL_ALLOW_NO_PROVIDERS"] = "1"
root = r"C:\Users\kiran\Desktop\Conxa"
sys.path[:0] = [rf"{root}\conxa-cloud\backend", rf"{root}\packages\conxa-core"]

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
from conxa_core import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import entitlements as ent  # noqa: E402
from app.services import saas  # noqa: E402

db.init_db()
engine = db.get_engine()
if engine is not None:
    with engine.begin() as c:
        c.execute(text("TRUNCATE kv_store"))
        c.execute(text("DROP INDEX IF EXISTS kv_store_ns_workspace_idx"))
        if os.environ.get("BENCH_INDEX") == "1":
            c.execute(text("CREATE INDEX kv_store_ns_workspace_idx ON kv_store (namespace, (data->>'workspace_id'))"))

# ---- seed: realistic multi-tenant volume -------------------------------------------------------
# Per tenant: 2 members, billing, ~40 compile reservations (they are never deleted, so they pile up
# over months), 3 devices, 2 execute grants, 10 published workflows.
now = time.time()
period = ent.current_period()
rows: list[tuple[str, str, dict]] = []
state = saas._read_state()
for t in range(tenants):
    ws = "wrk_local" if t == 0 else f"org_bench_{t:05d}"
    for u in range(2):
        uid = f"user_{t}_{u}"
        state["users"][uid] = {"id": uid, "email": f"{uid}@example.test", "name": uid, "auth_provider": "clerk", "updated_at": now}
        state["memberships"].append({"user_id": uid, "workspace_id": ws, "role": "owner", "created_at": now})
    state["workspaces"][ws] = {"id": ws, "slug": ws, "name": ws, "updated_at": now}
    state["billing"].setdefault(ws, {"plan": "pro", "status": "active", "trial_started_at": now, "updated_at": now})
    for r in range(40):
        rid = f"res_{t}_{r}"
        rows.append((ent.RESERVATION_NS, rid, {
            "reservation_id": rid, "workspace_id": ws, "period": period if r < 10 else f"2026-{(r % 9) + 1:02d}",
            "amount": 1, "status": "reserved" if r < 2 else "committed", "workflow_id": f"wf_{r}",
            "session_id": f"sess_{r}", "idempotency_key": rid, "created_at": "2026-11-01T00:00:00Z",
            "updated_at": "2026-11-01T00:00:00Z", "expires_at": now + 3600}))
    for m in range(3):
        rows.append((ent.DEVICE_NS, f"{ws}:mh{m}", {"workspace_id": ws, "machine_hash": f"mh{m}", "last_ip": "10.0.0.1", "first_seen": "x", "last_seen": "x"}))
    for g in range(2):
        rows.append((ent.EXECUTE_GRANT_NS, f"g_{t}_{g}", {"grant_id": f"g_{t}_{g}", "workspace_id": ws, "email": f"e{g}@example.test", "status": "claimed", "granted_at": "x"}))
    for w in range(10):
        rows.append((ent.WORKFLOW_NS, f"{ws}:wf{w}", {"workspace_id": ws, "workflow_id": f"wf{w}", "created_at": f"2026-10-{w + 1:02d}T00:00:00Z", "created_at_ns": w, "locked": False}))
saas._write_state(state)

t0 = time.perf_counter()
if engine is not None:
    with engine.begin() as c:
        c.execute(text("INSERT INTO kv_store (namespace, key, data) VALUES (:ns, :key, CAST(:data AS jsonb))"),
                  [{"ns": ns, "key": k, "data": json.dumps(d)} for ns, k, d in rows])
        c.execute(text("ANALYZE kv_store"))
else:
    for ns, k, d in rows:
        db.db_set(ns, k, d)
seed_s = time.perf_counter() - t0
meta_bytes = len(json.dumps(saas._read_state()))

# ---- measure -----------------------------------------------------------------------------------
stats = {"queries": 0, "rows": 0, "kinds": {}}
if engine is not None:
    @event.listens_for(engine, "after_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        stats["queries"] += 1
        stats["rows"] += max(cursor.rowcount, 0) if statement.lstrip().upper().startswith("SELECT") else 0
        kind = " ".join(statement.split())[:60]
        stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1

client = TestClient(app)
N = int(os.environ.get("BENCH_N", "200"))
for _ in range(min(10, N)):
    assert client.get("/api/v1/entitlements/current").status_code == 200
stats.update(queries=0, rows=0, kinds={})
N = int(os.environ.get("BENCH_N", "200"))
times = []
for _ in range(N):
    t = time.perf_counter()
    r = client.get("/api/v1/entitlements/current")
    times.append((time.perf_counter() - t) * 1000)
    assert r.status_code == 200, r.text
body = r.json()
times.sort()
out = {
    "store": store, "tenants": tenants, "kv_rows_seeded": len(rows), "saas_metadata_bytes": meta_bytes,
    "seed_s": round(seed_s, 1), "calls": N,
    "p50_ms": round(statistics.median(times), 1), "p95_ms": round(times[int(N * 0.95) - 1], 1),
    "mean_ms": round(statistics.fmean(times), 1),
    "queries_per_call": round(stats["queries"] / N, 1) if engine is not None else None,
    "rows_read_per_call": round(stats["rows"] / N, 1) if engine is not None else None,
    "query_kinds_per_call": {k: v / N for k, v in stats["kinds"].items()} if engine is not None else None,
    "sanity": {"machines": body["meters"]["machines"]["used"], "execute_seats": body["meters"]["execute_seats"]["used"],
               "compile_reserved_remaining": body["meters"]["compile_credits"]["remaining"],
               "workflows_active": body["workflow_lock"]["active"], "seats": body["meters"]["seats"]["used"]},
}
print(json.dumps(out, indent=2))
