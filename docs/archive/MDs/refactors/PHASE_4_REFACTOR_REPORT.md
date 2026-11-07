# Phase 4 — Shared Package Separation: Refactor Report

## Summary

`conxa_core` — the shared Python package installed by both the Conxa Cloud backend and the
Conxa Builder (Build Studio) — carried a mix of genuinely-shared foundation code and code that
actually belonged to one app or the other. This refactor moved the app-specific pieces back into
their owning app and shrank `conxa_core` to what both apps truly need, while keeping it a single
pip package (not splitting it into multiple distributions). Four of five planned steps completed
cleanly with zero regressions; the fifth (splitting the `config.py` settings class) was attempted,
hit a real architectural blocker, and was fully reverted — documented below with a recommendation.

**Validation used throughout:** `cd conxa-cloud && pytest -q tests` (covers `conxa_core` +
`conxa_compile` + the cloud `app` in one suite) and a cloud import smoke test
(`SKILL_ALLOW_NO_PROVIDERS=1 python -c "import app.main"`). Baseline going in: 373 passed, 18
skipped, 18 pre-existing failures unrelated to this work (auth/RBAC, runtime staging paths,
fingerprint tests — see `git log` for the areas). Every step below reproduced that exact baseline
after landing.

## What Moved

### 1. Installer + plugin templates, and their generation code → Builder
- `conxa_core/storage/installer_templates/setup.nsi.tmpl` → `conxa_compile/installer_templates/`
- `conxa_core/storage/plugin_templates/` (whole dir) → `conxa_compile/plugin_templates/`
- `conxa_core/storage/skill_package_templates.py` → `conxa_compile/storage/`
- `conxa_core/storage/skill_package_formatters.py` → `conxa_compile/storage/` (moved together with
  the `skill_packages.py` split below, since it's only used by bundle generation)
- `conxa_core/skill_pack_build_log.py` → `conxa_compile/` (only consumer is bundle generation)

None of these were ever imported by the cloud. The PyInstaller spec (`conxa-builder/pyinstaller.spec`)
already globs `*.tmpl`/`*.gitignore`/`*.json` out of `conxa_compile` for the frozen build, so no
packaging changes were needed there. `packages/conxa-core/pyproject.toml`'s `package-data` no longer
declares the two template globs.

### 2. `llm/client.py` split — narrower than originally scoped
The initial plan assumed the compile-pipeline's LLM task prompts were Build-Studio-only, based on
the cloud importing only 8 "low-level HTTP helper" symbols from this module. Tracing the actual call
graph disproved that: the cloud's own concrete provider router
(`conxa-cloud/backend/app/llm/router.py`) calls `_openai_body_dict(task, ...)` directly to build the
real request body sent to LLM providers, and `_openai_body_dict` internally calls
`_openai_messages_for_task` — the function holding every task's prompt text. The prompts are live
cloud execution logic (the cloud is what actually calls providers on the pipeline's behalf via the
metered proxy), not dead Builder-only code.

**What actually moved to `conxa_compile/llm/client.py`** (Builder-only, verified by a whole-repo
grep for callers): `call_llm()` — the pipeline's dispatch entry point — plus two functions with zero
live callers anywhere (`supports_multimodal_chat`, `_selected_endpoint_and_keys`), kept for exact
behavioral parity in case anything ever calls them.

**What stayed in `conxa_core/llm/client.py`:** the entire OpenAI-compatible HTTP/prompt-building
engine — transport, JSON parsing, response normalization, and all task prompts — because the cloud
needs it directly or transitively. The module docstring now describes it accurately as shared
infrastructure the cloud's router depends on, not Build-Studio-only.

### 3. `storage/skill_packages.py` split — read/list stays core, generation moves to Builder
Computed the exact call-graph closure (script-assisted, not manual guessing) from the real external
entrypoints: the cloud needs `bundle_root_dir` + `list_skill_bundle_summaries`; the Build Studio's
own handler additionally needs `list_skill_package_summaries`, `skill_package_root_dir`,
`read_skill_package_bundle_files` — all read-only, with zero dependency on the formatter/template
code. `delete_skill_package_bundle`/`rename_skill_package_bundle` — used only by the Build Studio,
never the cloud — turned out to transitively need the write/formatter machinery, so they moved to
the generation module too rather than staying in core.

- **Core kept** (`conxa_core/storage/skill_packages.py`, ~370 lines, down from 924): path/slug
  resolution, workflow discovery, visual-asset reads, listing helpers, and the three API-facing
  read functions. Zero imports from `conxa_compile` — verified.
- **Builder got** (new `conxa_compile/storage/skill_packages_build.py`, ~440 lines): scaffold
  creation, all writes, delete/rename for both bundles and workflows, and the legacy
  `package_bundle_root_name`/`_persisted_package_bundle_root_slug` group (confirmed dead — the
  bundle root is hardcoded, and `rename_package_bundle_root` unconditionally raises). Several
  functions in this module (`write_skill_package_files*`, `skill_package_dir`, `resolve_workflow_dir`,
  `delete_skill_package_workflow`, `rename_skill_package_workflow`, `read_skill_package_files`,
  `read_skill_package_visual_asset_bytes`) have no callers anywhere in the repo today — preserved
  verbatim rather than deleted, since removing unreachable code was out of scope for a
  zero-behavior-change refactor.

Two Build Studio call sites (`handlers/skill_packages.py`'s delete/rename commands,
`plugin_builder_output.py`'s `ensure_bundle_scaffold` call) now import from the new generation
module; everything else is unchanged.

## What Was Investigated and Deliberately Left Alone

- **`models/events.py`, `models/skill_spec.py`, `models/manifest.py`, `models/plugin.py`** — pure
  Pydantic schema contracts. Cloud tests import `events`/`skill_spec` directly; moving them into
  `conxa_compile` would force a cross-app test dependency for zero decoupling benefit, since they
  carry no app-specific behavior.
- **`db.py`, `metrics/store.py`, `progress.py`, `workspace.py`, `sanitize.py`,
  `storage/{json_store,session_events,selector_cache,snapshots,snapshots_gc,plugin_store}.py`** —
  genuine shared infrastructure (the dual Postgres/filesystem store, the shared metrics singleton,
  the sink-injection seam for job progress). No changes.
- **`conxa_core.llm.__init__`'s `RouterProtocol`/`set_router`/`get_router`** — the injection seam
  itself stays in core; only the concrete Build-Studio-only dispatcher (`call_llm`) moved out.

## What Was Attempted and Reverted: `config.py`

The plan called for splitting the single 581-line `Settings` class into a shared `CoreSettings` base
plus `CloudSettings`/`StudioSettings` subclasses, each with its own singleton, so the Build Studio
would no longer need to defeat cloud-shaped boot validators via `SKILL_ALLOW_NO_PROVIDERS=1` /
`SKILL_ALLOW_ENV_MISMATCH=1`.

**Investigation before writing code** turned up two corrections to the original assumptions:
`installer_signing_key`/`installer_signing_window` are cloud-only (used to sign installer download
links), not Build-Studio-only as first assumed; and `conxa_core/db.py` reads `settings.database_url`
directly, meaning that field can never be cleanly cloud-only since a core module depends on it. A
full field-by-field usage audit (~105 fields, cross-checked against every `settings.X` read in both
apps and in `packages/conxa-core` itself) produced a complete, verified categorization, including a
decision (confirmed with the user) to rewrite `conxa_core/llm/client.py`'s `_resolved_model` to drop
its `settings`-derived fallback, since tracing its one real caller showed that branch was never
actually exercised.

**The split was implemented and then failed at the full test suite**, surfacing 15 new failures on
top of the 18 pre-existing ones. Root cause: **pydantic model inheritance does not share a runtime
instance.** `conxa_core.config.settings = CoreSettings()`, `app.config.settings = CloudSettings()`,
and `conxa_compile.config.settings = StudioSettings()` become three independently-instantiated
objects. For any field both a core module and an app-level module read — `data_dir` above all — a
test (or any runtime code) that mutates one instance has zero effect on the others. This wasn't a
subtle edge case: `test_skill_pack_fingerprint.py`'s failure showed it concretely — the test patched
`conxa_core.config.settings.data_dir`, but the rewritten `plugin_builder_output.py` read
`conxa_compile.config.settings.data_dir` (a different, unpatched instance) and wrote its output
files to the wrong directory.

This codebase's test suite (and potentially production runtime paths) depends on **one process-wide
mutable settings singleton**. That pattern is fundamentally incompatible with splitting `Settings`
into separately-instantiated subclasses for any field with cross-cutting readers, short of either
rewriting how every module resolves settings (a much larger, invasive change of its own) or giving
up genuine instance separation. Given the "no new features, zero behavior change" constraint, the
right call was to stop and revert rather than ship a design that breaks test isolation.

**Revert verification:** `packages/conxa-core/conxa_core/config.py` restored to the original
monolithic `Settings`/`settings` (confirmed via `git diff` showing an empty diff); the
`_resolved_model` fix reverted back to referencing `settings.llm_vision_model`/`llm_text_model`; all
~14 cloud backend files' and ~20 Build Studio files' imports restored to
`from conxa_core.config import settings`; the 6 touched test files reverted via `git checkout`; the
two new files (`app/config.py`, `conxa_compile/config.py`) deleted. Full suite re-verified back to
the exact baseline (373 passed, 18 skipped, same 18 pre-existing failures); import smoke passed.
Steps 1–4 above are unaffected — none of their changes touch `config.py` or its consumers' imports.

**`config.py` remains monolithic.** `SKILL_ALLOW_NO_PROVIDERS`/`SKILL_ALLOW_ENV_MISMATCH` are still
required by the Build Studio launcher, unchanged.

## The Lean, Shared `conxa_core` — Current State

After Steps 1–4, `conxa_core` contains:
- `config.py` — the monolithic `Settings`/`settings` (unsplit; see above).
- `db.py` — the dual Postgres/filesystem KV store.
- `sanitize.py`, `workspace.py`, `progress.py` — small shared utilities and the job-progress
  sink-injection seam.
- `metrics/store.py` — the shared in-process metrics singleton.
- `llm/__init__.py` — the router-injection protocol (`RouterProtocol`, `set_router`, `get_router`).
- `llm/client.py` — the shared OpenAI-compatible HTTP/prompt-building engine (transport + every task
  prompt), the cloud's provider router's direct dependency.
- `models/{events,skill_spec,manifest,plugin}.py` — shared Pydantic schema contracts.
- `storage/{json_store,session_events,selector_cache,snapshots,snapshots_gc,plugin_store}.py` — full
  modules, unchanged.
- `storage/skill_packages.py` — read/list/resolve only (~370 lines, down from 924).

Removed to the Build Studio's `conxa_compile/`: installer/plugin templates, the formatter and
template-data modules, the build-log module, bundle generation/write/delete/rename, and the
`call_llm()` pipeline dispatcher.

## Remaining Technical Debt

1. **`config.py` is still one 581-line class mixing cloud, Build-Studio, and shared fields.** The
   field-by-field audit from the aborted split is preserved in the plan history and gives a ready
   starting point for a future attempt — but any future attempt must first solve the
   single-instance-vs-inheritance problem (see recommendation below), not just re-attempt the same
   class-splitting approach.
2. **`conxa_core.llm.client`'s dead code** (`_openai_complete_request`, `_parallel_anchor_vision_first_success`,
   `_next_api_key`, `_legacy_payload`, `_append_llm_detail`, and core's own copy of
   `_decode_http_error_body`, which the cloud's router duplicates locally rather than importing) has
   no live callers anywhere in the repo. Left in place, out of scope for this refactor.
3. **Underscore-prefixed cross-package imports.** The cloud imports 7 `_`-prefixed helpers from
   `conxa_core.llm.client`; the Build Studio's new `conxa_compile/llm/client.py` imports 2
   `_`-prefixed helpers back from core. Both predate this refactor in spirit (the cloud's imports
   already looked like this) — promoting a small public API surface would be a natural follow-up.
4. **`json_store → plugin_store` lazy in-function import** (a pre-existing cycle-breaker) remains;
   proper one-directional layering is future work, untouched by this refactor.
5. **Two "legacy adapter" functions in `conxa_compile/llm/client.py`** (`supports_multimodal_chat`,
   `_selected_endpoint_and_keys`) have zero callers anywhere in the codebase, confirmed by a
   whole-repo grep. They were preserved rather than deleted (out of scope for this refactor) but are
   a clean deletion candidate whenever someone next touches that file.
6. **`storage/skill_packages_build.py`'s several zero-caller functions** (`write_skill_package_files*`,
   `skill_package_dir`, `resolve_workflow_dir`, `delete_skill_package_workflow`,
   `rename_skill_package_workflow`, `read_skill_package_files`,
   `read_skill_package_visual_asset_bytes`) — same story as above, preserved verbatim.

## Recommendation for a Future `config.py` Split

Splitting `Settings` by subclassing with separate instantiation will hit the same wall again for any
field read by both a core module and app-level code. A safe path would need one of:
- **A single settings instance, organized (not split) by field-group mixins** composed into one
  class in `conxa_core/config.py` — achieves readability/documentation value but zero actual import
  decoupling (every consumer still imports from `conxa_core.config`). Lowest risk, lowest reward.
- **Convert every settings consumer from `from X import settings` (module-level, cached at import
  time) to a `get_settings()` accessor called at use-site** — this would let a genuinely-separate
  `CloudSettings`/`StudioSettings` instance be swapped in without the "three independent singletons"
  problem, since core code would resolve through a single indirection point that the app sets once
  at startup. This is a real, invasive change to how ~50 modules access configuration and should be
  scoped as its own project, not folded into a "no new features" refactor.

## Verification Performed

- Full suite after every landed step: `cd conxa-cloud && pytest -q tests` — 373 passed, 18 skipped,
  same 18 pre-existing failures throughout.
- Cloud import smoke after every landed step: `SKILL_ALLOW_NO_PROVIDERS=1 python -c "import app.main"`.
- Targeted re-runs: `pytest tests/test_plugin_builder.py tests/test_installer_builder.py
  tests/test_product_routes.py tests/test_build_studio_backend.py` — all green except the one
  pre-existing baseline failure.
- `git diff` review confirming the `config.py` revert left zero residue on every touched file.
