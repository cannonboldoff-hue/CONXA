# CLOUD-26 — `GET /api/v1/entitlements/current` against a real database (2026-11-21)

Local throwaway Postgres 16 (`pgserver`, 127.0.0.1, scratch dir, removed afterwards). Same app, called through
FastAPI `TestClient` as `wrk_local`. Script: `bench_entitlements.py` (one process per store/scale); profiler: `prof_meta.py`.

Seeded data per workspace: 2 members, a billing row, 40 compile reservations (they are never deleted), 3 devices,
2 execute grants, 10 published workflows. "500 tenants" = 27,500 kv rows + a 394 KB `saas/metadata` document.

| Store, tenants | Code | Calls | p50 ms | p95 ms | Queries/call | Rows read/call | Writes on the GET |
|---|---|---|---|---|---|---|---|
| Postgres, 1 | before | 200 | 27.3 | 49.8 | 20 | 106 | 3 |
| Postgres, 500 | before | 200 | **751.2** | **1465.4** | 20 | **47,511** | 3 x 394 KB |
| Postgres, 500 | scoped lists | 200 | 226.7 | 324.9 | 20 | 106 | 3 x 394 KB |
| Postgres, 500 | scoped lists + `(namespace, data->>'workspace_id')` index | 200 | 232.1 | 310.4 | 20 | 106 | 3 x 394 KB |
| Postgres, 500 | after (both fixes) | 200 | **86.4** | **126.1** | 17 | 106 | 0 |
| Postgres, 1 | after | 200 | 23.0 | 38.1 | 17 | 106 | 0 |
| Filesystem, 1 | before | 200 | 103.2 | 130.1 | - | - | 3 |
| Filesystem, 1 | after | 200 | 18.8 | 29.9 | - | - | 0 |
| Filesystem, 500 | after | 30 | 7019.7 | 7929.9 | - | - | 0 |

Filesystem 500-tenant "before" was not completed (stopped: ~45k file reads per call). The "after" figure ran while the
full pytest suite was also running; the filesystem store is dev-only and still parses every file of a namespace per list.

## What the measurement showed

1. **Cross-tenant scans.** `compile_reservations`, `workspace_devices`, `execute_grants`, `entitlement_workflows` hold
   every workspace's rows. Five list calls per request fetched all of them (reservations twice) and filtered by
   `workspace_id` in Python: 47,511 rows per call at 500 workspaces, growing with the whole platform.
   Fix: the SQL store filters `data->>'workspace_id'` in the query (`entitlements.py` `_SqlKvStore.list`).
   An expression index gave no gain at this size (the namespace prefix of the primary key already narrows it), so none was added.
2. **Write on every read.** `saas.ensure_principal` ran 3 times per request and each time rewrote the whole shared
   metadata document (to Postgres and a pretty-printed disk mirror): ~127 ms each, ~80% of the remaining time.
   Fix: write only when the user, workspace, membership or billing default actually changes.

## Still open

The remaining 86 ms at 500 workspaces is eight reads of the one shared `saas/metadata` document per request
(~8 ms each at 394 KB), which grows with every workspace. Splitting that document is the CLOUD-2 scale debt.
