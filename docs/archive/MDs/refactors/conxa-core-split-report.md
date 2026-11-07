# Report: `packages/conxa-core` — How Important Is It, And Should It Be Split?

**Date:** 2026-08-22
**Scope:** Read-only analysis. No code was changed.
**Question asked:** Is this package important? Why is it shared? Shouldn't it be split so each app (`conxa-builder`, `conxa-cloud`, `runtime`) owns its own copy?

---

## 1. The Short Answer

`packages/conxa-core` is **very important** — it's the "contract" layer between the two Python apps in the system. But the premise that all three apps share it is **wrong**: only two apps use it (`conxa-builder` and `conxa-cloud`). The Node.js `runtime/` never imports it at all.

And no, it should **not** be fully split today. It was already slimmed down once (a previous refactor moved ~600 lines of Build-Studio-only code out), and an attempt to split its biggest file (`config.py`) was tried, failed for real architectural reasons, and had to be reverted. What's left in the package is mostly *genuinely shared* code. There are a few things that could still move out, and one or two things that *could* be split but would take a lot of work and carry real risk.

---

## 2. What This Package Actually Is

It's a small pip-installable Python package (~130 KB of actual code across 27 files). Think of it as the **shared foundation box**: settings loading, database access, data models, file storage helpers, and the LLM plumbing that both Python apps plug into.

```
packages/conxa-core/
  conxa_core/
    config.py            28 KB   ← biggest file; all env/settings for BOTH apps
    db.py                 8 KB   ← dual database: Postgres (cloud) / files (studio)
    progress.py           2 KB   ← job progress event sink
    sanitize.py           1 KB   ← safe JSON dumps / text scrubbing
    slugs.py              1 KB   ← URL-safe name generation
    workspace.py          1 KB   ← workspace ID helpers
    llm/
      __init__.py         2 KB   ← router injection seam (set_router/get_router)
      client.py          22 KB   ← OpenAI-compatible HTTP engine + ALL task prompts
    metrics/store.py      2 KB   ← in-process metrics singleton
    models/                      ← the Pydantic data contracts
      events.py           9 KB   ← RecordedEvent (what the recorder captured)
      skill_spec.py      17 KB   ← SkillStep, IdentitySignal etc. (compiled skill format)
      workflow.py         4 KB   ← Workflow, SkillPack, GroupApp
      manifest.py         2 KB   ← manifest schemas
    storage/
      json_store.py       4 KB   ← read/write individual skills as JSON
      workflow_store.py  10 KB   ← workflow CRUD on disk
      group_store.py     10 KB   ← app-group CRUD on disk
      session_events.py   1 KB   ← recorded-event file reader/writer
      snapshots.py        4 KB   ← DOM snapshot store
      snapshots_gc.py     2 KB   ← old-snapshot cleanup
      selector_cache.py   8 KB   ← LLM selector cache (+ expiry cleanup)
      skill_packages.py  15 KB   ← READ/list side of built skill bundles
      skill_pack_store.py 4 KB   ← skill-pack metadata store
      storage_state.py    3 KB   ← Playwright storageState merge
```

It also has a `data/` folder inside it — that's **runtime state** (KV entries, skill packs, tracking data) that leaked into the package directory during local runs. That's a hygiene problem worth noting (see §8), not something to ship.

---

## 3. Who Actually Uses It

| Consumer | Uses conxa-core? | How |
|---|---|---|
| `conxa-builder` (Build Studio Python backend) | **Yes — heavily** | Recorder, compiler, editor handlers, bundle builder — ~40+ import sites |
| `conxa-cloud` backend | **Yes — moderately** | Settings, DB, models, selector-cache/snapshot GC at startup, its own LLM provider router builds on core's prompt engine |
| `conxa-cloud` tests | **Yes — heavily** | Tests drive the Studio pipeline through the same suite |
| `runtime/` (Node.js MCP server) | **No — zero usage** | Only one comment in `browser.js` mentioning it ported `merge_storage_states` logic from Python |

So the honest framing: **it's a two-consumer package**, plus the Node runtime consumes the *output shape* of its models (the compiled skill JSON), without ever importing them.

---

## 4. Why Sharing Exists In The First Place

The whole product depends on one idea: **a workflow recorded and compiled on the Studio machine must mean exactly the same thing when the Cloud hosts it and when the Runtime executes it.**

That agreement lives here:

- `models/skill_spec.py` defines what a compiled step *is*. Studio writes it. Cloud validates/serves it. Runtime reads the JSON produced from it.
- `models/events.py` defines what a recording *is*. Same deal.
- `llm/__init__.py`'s `set_router`/`get_router` is the seam where Studio plugs the *cloud's metered proxy* in as its LLM provider. Both sides must agree on that interface or compiles break.
- `db.py`'s dual store means the same `db_get/db_set` calls work against Postgres in the cloud and plain files in the Studio. That's why the cloud can reuse test fixtures against a filesystem DB.
- `config.py` gives both apps one way to read `SKILL_*` env vars.

If you duplicate these instead of sharing them, every schema change becomes a "did you remember to update the other copy?" bug waiting to happen. That's the classic reason this kind of code gets extracted into a shared package.

---

## 5. Already Tried, Already Done: The Phase 4 Slim-Down

A previous refactor (documented in `docs/archive/refactors/PHASE_4_REFACTOR_REPORT.md`) already did the easy splitting. What moved OUT of core into the Builder:

- Installer/plugin templates and their generation code
- `call_llm()` — the compile-pipeline dispatcher
- Bundle **write/generate/delete/rename** side → now `conxa_compile/storage/skill_packages_build.py`
- Core kept only the read/list side of `skill_packages.py` (924 → ~370 lines)

That report also documents a **failed attempt** to split `config.py` into separate Cloud/Studio settings classes. It failed because tests (and some production paths) depend on there being **exactly one mutable settings object per process**. Split classes = three independent objects = patching one doesn't affect the others = broken behavior. That lesson directly shapes what's realistic below.

---

## 6. What MUST Stay Shared (Don't Touch)

| Item | Why it must stay |
|---|---|
| `models/*` (all four files) | The data contract between Studio-compile and Cloud-host/Runtime-execute. Pure schemas, no behavior. Splitting = version-drift bugs where the cloud serves JSON the studio's compiler no longer produces (or vice versa). |
| `llm/__init__.py` (router protocol + set/get) | The dependency-injection seam itself. Studio registers the proxy router; cloud provides its concrete pool router. Tiny, stable, load-bearing. |
| `db.py` | Genuinely used by both sides in production code (cloud services do `db_get/db_set`; Studio uses it for LLM caches, intent graphs, vision anchors). The dual Postgres/filesystem design is the point. |
| `sanitize.py`, `slugs.py`, `workspace.py`, `progress.py`, `metrics/store.py` | Small, dependency-free utilities used by both. Moving them buys nothing and costs churn. |
| `storage/json_store.py`, `session_events.py`, `selector_cache.py`, `snapshots.py`, `snapshots_gc.py` | Read-side infrastructure used by both apps (cloud startup even runs selector-cache/snapshot GC). Shared on purpose. |

---

## 7. What CAN Be Split (Reasonable Effort)

These are used by the **cloud backend only in tests**, not by any live cloud route — their real home is arguably the Builder:

| Item | Evidence | Where it would go |
|---|---|---|
| `storage/workflow_store.py` | Only cloud *tests* touch it; live cloud routes don't | `conxa_compile/storage/` |
| `storage/group_store.py` | Same pattern | `conxa_compile/storage/` |
| `storage/session_events.py` write side (reader is used by both-ish; check before moving) | Mostly Studio recorder/editor | `conxa_compile/storage/` |
| `storage/skill_pack_store.py` | Used by Studio bundle builder; cloud tests only | `conxa_compile/storage/` |
| `storage/storage_state.py` | Studio auth/session handling only | `conxa_compile/storage/` |

**But here's the catch:** those cloud tests exist precisely to exercise the Studio pipeline through the shared suite. Move the modules and you must repoint dozens of test imports — mechanical work, low risk, near-zero payoff. The Phase 4 report already concluded the same about sibling modules. Do it only if/when the test suite splits into per-app suites anyway.

Also trivially splittable: dead code flagged in the Phase 4 report (unused functions in `llm/client.py` and `skill_packages_build.py`) — deletion candidates next time someone touches those files.

---

## 8. What COULD Be Split, But Takes A Hell Lot Of Stuff

### A. `config.py` — the big one
One 28 KB settings class mixing cloud-only fields (Cashfree, Clerk JWKS, installer signing), Studio-only fields, and shared ones (data dirs, LLM keys/models).

**Why it's hard:** the single-mutable-singleton problem from §5. The known-safe path (documented in the Phase 4 report's recommendation):
1. Convert every consumer from `from conxa_core.config import settings` (cached at import time) to a `get_settings()` accessor called at use-site — **~50 modules across both apps**.
2. Then introduce `CoreSettings` + `CloudSettings` + `StudioSettings`, with each app installing its instance into core once at startup.

This is a genuine multi-day project touching nearly every Python file, and until it's done, splitting config buys you nothing functional — the Studio will still need its `SKILL_ALLOW_NO_PROVIDERS=1` escape hatch because the boot validators are cloud-shaped.

**Verdict:** possible, expensive, and currently the highest-value item on the list *if* the env-validator annoyance hurts you in daily dev. Otherwise leave it.

### B. `llm/client.py` — the prompt engine
Looks like an obvious "that's compile logic, move it to the Studio" candidate. It isn't: the cloud's own provider router (`conxa-cloud/backend/app/llm/router.py`) calls `_openai_body_dict()` from this module, which pulls in `_openai_messages_for_task()` — i.e., **every task's prompt text is live cloud execution logic**. The cloud literally builds provider requests from these prompts when it meters-and-forwards the pipeline's LLM calls.

Splitting it means either duplicating prompts in two places (drift risk on every prompt tweak) or inventing a third "prompts" micro-package — more structure than a ~22 KB module deserves.

**Verdict:** keep shared. The cost/benefit already killed this once in Phase 4.

### C. Hygiene: get `data/` out of the package directory
Not a split, but the loudest real problem found: `packages/conxa-core/data/` contains KV stores, skill packs, and tracking tokens — runtime state sitting inside source. It works because defaults resolve relative to the repo, but it pollutes the package folder and risks accidental commits of customer-ish state. Pointing local dev state at the existing repo-level `data/` dir would fix it.

---

## 9. Final Verdict

| Question | Answer |
|---|---|
| Is `packages/conxa-core` important? | Yes — it's the contract layer; break it and Studio↔Cloud↔Runtime drift apart silently |
| Should it be split three ways? | No. The runtime doesn't use it, and the remaining contents are genuinely shared between the two Python apps |
| Anything worth splitting now? | Nothing urgent. The §7 modules are movable but pointless until test suites split |
| Anything worth splitting eventually? | `config.py` via the `get_settings()` conversion — big, planned, real payoff (kills the `SKILL_ALLOW_*` escape hatches) |
| Best quick win? | Get `data/` runtime state out of the package directory |

The package is already leaner than it looks. The previous refactor took the easy wins; what remains shared is shared for defensible reasons, and the one painful leftover (`config.py`) has a documented, known-risky path forward that shouldn't be attempted casually.
