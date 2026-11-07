# Refactor Audit — `conxa-builder`

**Date:** 2026-08-22
**Scope:** All of `conxa-builder/` — Python stdio backend (`backend.py`, `handlers/`, `services/`), compiler (`conxa_compile/compiler/`), builders/storage/editor (`conxa_compile/storage|editor|llm|...`), and the Electron app (`electron/main.js`, `renderer/src/`).
**Method:** 4 parallel audit agents (research-only, no code changed). Every finding below was verified against current file contents; line numbers are exact as of this date. Cross-cutting claims were re-checked independently.

---

## Executive Summary

The codebase is functional and in places genuinely well-built (Electron state management, IPC isolation, TypeScript hygiene, the patch-gate editor design are all strong). The dominant structural problems are:

1. **A god-object backend.** `Backend` composes 8 mixins into one class with ~106 methods, dispatched by string via `getattr`. Mixins silently depend on core methods they don't define — the coupling is invisible to tooling.
2. **A monolithic compiler.** `compiler/build.py` is 1,631 lines / 37 functions / zero classes, with seven interleaved responsibilities. Natural split boundaries already exist (the codebase did this before with `step_anchors.py`).
3. **Copy-paste instead of helpers.** The same error-mapping ladder appears 7×, skill-load boilerplate ~21×, editor mutation scaffolding ~10×, LLM cache logic 6×, and six identical save blocks inside one React function.
4. **One real concurrency/billing bug.** A process-global LLM-router singleton is swapped per request while every command runs on its own thread — overlapping commands can mis-route each other's LLM calls (compile credits vs Human Edit pool).
5. **~1,400+ lines of verified-dead code** across Python and renderer, including stale `.pyc` bytecode for modules deleted long ago.

Nothing here requires a big-bang rewrite. The recommended path is incremental extraction following boundaries that already exist.

---

## Priority Matrix

| # | Finding | Severity | Subsystem | Effort |
|---|---------|----------|-----------|--------|
| 1 | Global LLM-router singleton mutated per request → cross-thread usage-class race (billing/correctness) | **HIGH** | Python backend | Small |
| 2 | Unsynchronized undo/redo dicts + over-broad recording lock on per-request threads | **HIGH** | Python backend | Medium |
| 3 | `build.py` monolith (1,631 lines) | **HIGH** | Compiler | Large |
| 4 | Silent dual primary-selector election in `_build_target`; empty IdentityBundle falls back silently, no warning | **HIGH** | Compiler | Small |
| 5 | Bare `except Exception: pass` on the sanctioned LLM selector-regeneration path (`patch.py`) | **HIGH** | Compiler | Trivial |
| 6 | Dead legacy bundle-index writer producing false `npm install` / `executor.js` README content users can hit today | **HIGH** | Storage/builders | Small |
| 7 | `workflowApi.ts` vs `workflowsApi.ts`: confusing near-duplicate API layers, drifted response types, same-name-different-semantics exports | **HIGH** | Electron | Medium |
| 8 | God components: `HumanEditPage.tsx` (1,083 ln), `StepConfigForm.tsx` (887 ln) | **HIGH** | Electron | Medium |
| 9 | `Backend` god object: 8 mixins, ~106 methods, hidden mixin→core contract | **HIGH** | Python backend | Large |
| 10 | Proxy-exception → `_CommandError` mapping duplicated 7× | MEDIUM | Python backend | Trivial |
| 11 | Skill load-and-check boilerplate ×21, workspace resolution ×11 | MEDIUM | Python backend | Trivial |
| 12 | Auth.json build-input invariant (Key Invariant) enforced by byte-identical copy-pasted code in two handlers | MEDIUM | Python backend | Trivial |
| 13 | Editor mutation scaffolding (snapshot→undo→write→respond) repeated in 10 handlers; inconsistent ValueError handling | MEDIUM | Python backend | Medium |
| 14 | `cmd_list_groups` performs cloud writes on a read path | MEDIUM | Python backend | Small |
| 15 | Stringly-typed event/step payloads end-to-end; pydantic validation result discarded at compile entry | MEDIUM | Compiler | Large |
| 16 | Confidence formula maintained twice (build.py vs selector_score.py); testid regex duplicated 4+× | MEDIUM | Compiler | Small |
| 17 | Selector-quality law spread across 3 files incl. the LLM regeneration path's own ranking scheme | MEDIUM | Compiler + LLM | Medium |
| 18 | Six copies of LLM cache plumbing with inconsistent TTL semantics ("transient outage cached forever") | MEDIUM | LLM clients | Medium |
| 19 | Editor reaches into compiler privates (`_build_assertions`, `_merge_compile_warnings`, …) — layering inversion | MEDIUM | Editor ↔ Compiler | Small |
| 20 | Reflected XSS in OAuth callback page (`auth_service.py:282-287`); tracebacks shipped to renderer; unvalidated `logo_path` | MEDIUM* | Python backend/security | Trivial |
| 21 | Dead code cluster (Python): `metadata_reporter.py`, `skill_package_templates.py`, `prefilter_selector_candidate`, `rank_selectors`, no-op `cmd_set_skill_pack_bundle_root`, etc. | MEDIUM | Multiple | Trivial |
| 22 | Dead components (renderer): 8 files, ~700 lines, all zero importers | MEDIUM | Electron | Trivial |
| 23 | Three competing backend-event-streaming idioms in renderer; triplicated `{logs,busy,error,done,result}` task state | MEDIUM | Electron | Medium |
| 24 | Long functions across builders/editor (6 functions >100 lines, worst 230) | LOW | Builders/editor | Medium |
| 25 | Template rendering implemented 3 different ways; NSIS template header omits 4 real placeholders | LOW | Builders | Small |
| 26 | Misc: `time.sleep(15)` blocking mid-compile; full-file run-log scans; hardcoded install path; unbounded thread-per-request dispatch | LOW | Multiple | Small |

\* security items are small diffs but should be treated as urgent regardless of refactor sequencing.

---

## 1. Python stdio backend (`backend.py`, `handlers/`, `services/`)

### H-9. God object assembled from 8 mixins (~106 methods)

`backend.py:102-111` composes `SessionMixin, CompileMixin, WorkflowsMixin, GroupsMixin, WorkflowEditorMixin, VisualMixin, SkillPackagesMixin, RunsMixin` into a single class dispatched purely via `getattr(self, f"cmd_{cmd}")` (`backend.py:615`). Method census:

| Module | defs | cmd_* handlers |
|---|---|---|
| workflows.py | 20 | 17 |
| workflow_editor.py | 17 | 16 |
| groups.py | 16 | 13 |
| session.py | 13 | 12 |
| skill_packages.py | 10 | 10 |
| visual.py | 6 | 6 |
| runs.py | 3 | 3 |
| compile.py | 4 | 2 |

The dangerous part is the *hidden contract*: mixins call core-only methods they never declare — `groups.py:127-135` calls `self._installer_generation()`/`self._cloud_json()`; `visual.py:57,87,134` calls `self._install_proxy_router()`. Satisfied only through MRO against `backend.py`; a typo surfaces at runtime as `internal_error`. Handlers are also misplaced: `cmd_compile_updated` lives in VisualMixin despite being a title/version mutation (`visual.py:10-25`), and WorkflowsMixin mixes CRUD, release-center RPCs, installer builds, test execution, and recording status.

**Refactor:** introduce an explicit `BackendContext` protocol injected into each domain class; extract `_publish_skill_pack`, `_upload_installer_for_download`, `_cloud_json`, credit reservation into `services/cloud_client.py`; split `workflows.py` into crud/release/installer modules.

### H-1. Global LLM-router singleton race (billing correctness)

`backend.py:159-180` installs a router via `core_llm.set_router(client)` — a process-global. It is invoked *inside individual handlers*: `compile.py:127` with `usage_class="compile"`, `workflow_editor.py:406,437` and `visual.py:57,87,134` with `"human_edit"`. Meanwhile `serve()` spawns one thread per request (`backend.py:645`). Two overlapping commands replace the same global; the compile's in-flight LLM calls can be routed through the other request's proxy client with the wrong usage class — silent credit mis-metering.

**Fix:** pass the router explicitly through task clients, or use a `contextvars.ContextVar` read by `call_llm()`. Never mutate the global from a handler.

### H-2. Thread-safety of shared mutable state

- Undo/redo stacks (`backend.py:124-137`, mutated in `workflow_editor.py:350-387`) have **no lock**, on per-request threads.
- `_rec_lock` is held across the entire browser launch + N-app auth probing in `cmd_start_recording` (`session.py:96-293`), blocking unrelated commands for tens of seconds.
- No per-skill serialization for concurrent editor writes; stacks never evicted per skill (unbounded growth).

### H-3. God functions

| Function | Location | Length |
|---|---|---|
| `cmd_start_recording` | session.py:91-293 | ~203 ln |
| `_publish_skill_pack` | backend.py:351-537 | ~187 ln |
| `cmd_compile` | compile.py:23-208 | ~186 ln (credit release handled at 4 separate sites — easy to miss one) |
| `cmd_test_workflow` | workflows.py:397-533 | ~137 ln |
| `cmd_build_installer` | workflows.py:290-395 | ~106 ln |
| `AuthService.login` | auth_service.py:248-337 | ~90 ln |

**Fix highlights:** wrap compile in a `with compile_credit_reservation(...)` context manager owning commit/release.

### Duplication inventory (MEDIUM)

- Proxy-exception → `_CommandError` ladder verbatim **7×**: compile.py:131-138,165-172; workflow_editor.py:411-416,447-452; visual.py:60-65,90-95,137-142. Entitlement codes also duplicated between `backend.py:252-263` and `llm_proxy_client.py:138-145`.
- Skill load-and-check block **~21×** across workflow_editor/visual/skill_packages/workflows; workspace-id resolution **×11**.
- **Key Invariant risk:** the "auth.json never enters build output" guard exists as byte-identical copies at `workflows.py:191-198` and `workflows.py:308-317` — centralize it so future entry points inherit it.
- Editor mutation scaffolding (deepcopy snapshot → push_undo → write → respond → history flags) in 10 handlers; branch mutations catch `ValueError → _CommandError` but `cmd_reorder_steps`/`cmd_insert_step`/`cmd_delete_step` don't, so a mutation `ValueError` surfaces as `internal_error` with traceback.
- Recording-session management duplicated between session.py and groups.py (stale-lock recovery, cancel semantics, probe-TTL constant defined twice as `_VERIFIED_TTL_S = 600` / `_PROBE_TTL_S = 600`).

### Other notable

- **Side-effectful read:** `cmd_list_groups` (`groups.py:140-151`) issues a cloud PUT per group on every list — move sync to create/rename or background.
- **Env-var coupling:** `bootstrap.py` communicates results via `os.environ` (`MAKENSIS_PATH`, `CONXA_RUNTIME_LOCAL_DIR`, …) consumed implicitly downstream.
- **Error hygiene:** `AuthService` raises bare `RuntimeError` with string codes that reach the renderer as `internal_error` + traceback; ~25 broad `except Exception:` sites, several unjustified.
- **Dead code:** `protocol.py::_is_rejected_protected_url`, entire `services/metadata_reporter.py`, both branches identical at `workflows.py:554-556`, and `cmd_set_skill_pack_bundle_root` validates then echoes without persisting (looks functional to UI, is a no-op).

---

## 2. Compiler (`conxa_compile/compiler/`)

### H-3. `build.py` map and split plan

1,631 lines, 37 top-level functions, zero classes. Verified decomposition table:

| Extract to | Functions | ~Lines |
|---|---|---|
| `navigation_context.py` | tab/frame/navigate synthesis (8 pure fns, build.py:134-298,420-440) | ~230 |
| `element_identity.py` | fingerprint/snapshot/bundle/hover-chain/confidence builders | ~250 |
| `assertion_planner.py` | `_build_assertions` (658-803) | ~146 |
| `target_builder.py` | `_build_target`, `_build_signals`, input binding, validation | ~250 |
| `vision_fallback.py` | reason sets + fallback/warning/log fns (71-93, 323-397) | ~120 |
| `reporting.py` | compile log + `_build_compile_report` | ~140 |
| `intent_graph.py` | `_build_intent_graph` | ~75 |

All boundaries acyclic; extract in table order (navigation_context first — zero coupling). `build.py` retains orchestration (~350 lines). Precedent: `step_anchors.py` docstring says it was split out of former `v3.py`.

### H-4. Dual primary-selector election vs the IdentityBundle invariant

`_build_target` (`build.py:846-998`) always runs the legacy score-election first (861-908), then overrides with the bundle's top non-relational/xpath signal (926-937). If bundle signals are empty or all relational/xpath, the legacy primary silently becomes authoritative — **no compile warning distinguishes "bundle weak" from "bundle absent."** Invariant is currently held but enforced by after-the-fact override rather than construction.

**Fix:** gate legacy election behind empty-bundle; emit `identity_bundle_empty` compile warning when promotion is skipped. Also make `selector_source` honest (`"legacy_fallback"` when applicable — it's currently a hardcoded `"deterministic"` at build.py:912).

### H-5. Silent swallows on the sanctioned LLM path

- `patch.py:102-103` — `_regenerate_compiled_selectors` body wrapped in `except Exception: pass`. Snapshot-hash mismatch, missing bbox, cache failures all vanish at the exact boundary where LLM-written selectors enter artifacts.
- `patch.py:164-165` — same pattern in vision-anchor enrichment.
- Cost note: `_enhance_step_with_llm` fires vision enrichment on any assist patch even when recovery anchors aren't stale.

**Fix:** log reason + step index; stay non-fatal.

### M-15. Untyped pipeline

`compile_skill_package` validates events with `RecordedEvent.model_validate(e)` (`build.py:1476-1477`) **and discards the result** — everything downstream is `dict[str, Any]` (`Step = dict[str, Any]` aliases at step_anchors.py:18, validation_planner.py:27), with 100+ defensive `.get(...) or {}` guards restating shape assumptions. Three separate action-kind normalizers exist (`action_policy.normalize_action_kind`, `action_policy.action_kind_from_step`, `upload_binding._step_action_str`). Several post-stages mutate the step list in place relying on positional zip-alignment documented only in prose (`build.py:260,285-287,1504`; `upload_binding.py:287-300`).

### M-16/M-17. Duplicated heuristics

- Confidence formula maintained twice: `build.py:827-843` (pydantic) vs `selector_score.py:156-172` (dicts) — identical constants (×0.7 ortho, ×0.6 non-unique, cap 1.0); docstring admits it.
- Testid regex duplicated 4+× (`build.py:473,591`; identity_bundle.py:121; selector_grammar.py:57/95/270).
- Anchor-quality token lists diverge across 4 modules (selector_filters.py:371-422, step_anchors.py:21-75, decision_layer.py:106-117, build.py:352).
- The LLM regeneration module ships its own third ranking/filtering scheme (`llm/selector_regeneration.py:45-123`) partially contradicting `selector_score._KIND_PRIORITY`; `editor/retarget.py:266,341` imports its `validate_selector`, widening blast radius.

### Dead code (verified)

`selector_filters.prefilter_selector_candidate` (:166-168, alias, 0 callers), `step_anchors.rank_selectors` (:378-390, 0 callers), permanently-`None` `confidence_breakdown` / permanently-empty `selector_rationale` (`build.py:910-911,994-997,1359,1395`), identical branches in `selector_grammar.display_to_signal` (:142-147). Stale `.pyc` bytecode for deleted modules still present under `__pycache__/` (plugin_builder*, v3, llm_selector_generator*, workflow_service…).

---

## 3. Builders, storage, editor, LLM clients

### H-6. Legacy bundle-index writer still live

`storage/skill_packages_build.py::_write_bundle_index` (+ `format_plugin_index_json` / readme_text formatters) writes a legacy bundle layout whose README instructs `npm install` / references `executor.js` — colliding with current templates users can actually hit today. The four `_refresh_bundle_artifacts_*` callers keep it alive. **Kill it:** make refresh a no-op or regenerate via current templates, then delete the writer + formatter wrappers.

Also confirmed dead: `storage/skill_package_templates.py` (zero importers repo-wide — grep-verified during this audit).

### H-18. Six copies of LLM cache plumbing

Cache read/write logic is copy-pasted across intent, vision-anchor, recovery, semantic, workflow-intent-graph, and selector-regeneration clients — with **inconsistent TTL semantics**: some cache entries survive transient provider outages forever (error results cached as if valid). Extract one `llm/cache.py` dual-store helper (~200 lines removed) and fix TTL-on-failure uniformly.

### Builder/editor findings

- **God functions:** `build_installer` (installer_builder.py:105-334, ~230 ln — validate/stage/run-makensis-and-sign are separable), `_write_skill_packs_format` (skill_package_builder_output.py:51-274, ~224 ln — secretly resolves env + mints HMAC tracking tokens + DB-writes mid-"file write"; extract `_resolve_api_base`/`_mint_tracking_token`), `preview_retarget` (retarget.py:273-414, 141 ln + 8 deferred imports), two 130-155 ln functions in `recording_visual.py`.
- **Version-bump + steps-extraction boilerplate repeated 7+ sites** across workflow_mutations.py/retarget.py (e.g. :42-47,:63-68,:90-96,:110-114,:176-183,:325-330,:545-549; retarget :449-453). One `_bump_version(doc)` + steps-accessor removes a whole drift-bug class.
- **DTO redundancy:** `workflow_dto.step_to_dto` sets `human_readable_description` and `semantic_description` to the *same* computed value (:548,:552).
- **Layering inversion:** editor imports compiler privates — recording_visual.py:8 (three `_`-names from compiler.build), retarget.py:282,424-425, saved_skill.py:21 & region_selector_vision.py:33 (LLM-module privates), saved_skill :627 (upload_binding regex). Rename them public or move to shared modules.
- **Templates done three ways:** `.tmpl` + str.replace loops (implemented twice — output.py:301-305 and installer_builder.py:448-460), f-string builders, and pure-python index writers. One `render_template(path, **subs)` helper covers both .tmpl sites. `setup.nsi.tmpl` header comment omits 4 placeholders actually substituted (`ICON_DIRECTIVE`, `INSTALL_SUBDIR`, `CONXA_ENV`, `CONXA_UPDATE_CHANNEL`). `MAKENSIS_PATH` captured at import time AND re-read at runtime — stale-value error message possible.
- **Dead/test-only helpers:** `_to_bundle_slug`, `_events_to_json_text`, `strip_login_steps`/`_is_login_step` (tests only — module docstring claim is aspirational), `is_valid_placeholder_id`, `skill_pack_json_metrics`, unused params (`version=`, `bundle_slug`, `company_slug`).
- **Silent swallow:** installer_builder.py:315-325 wraps `set_installer(...)` in bare try/pass — failed install-record write desyncs Studio from disk silently.

---

## 4. Electron app

### H-7. `workflowsApi.ts` vs `workflowApi.ts`

Complementary, not duplicates — but the naming hides it and there are five concrete overlaps:

- Same-file duplicate wrappers for `delete_workflow`: `deleteWorkflowEntity` (workflowsApi.ts:296) vs `deleteWorkflow` (:369).
- Both wrap `start_recording` (:314 / workflowApi.ts:394), `stop_recording` (:335 / :409), and `get_recording_status` — with **drifted response types** (workflowsApi's carries `reached_wait_url?`, `auth_captured?`).
- **Same exported name, different semantics:** `fetchWorkflow(id)` fetches a Workflow entity (workflowsApi.ts:282); `fetchWorkflow(skillId)` fetches a step-editor DTO payload (workflowApi.ts:146). HumanEditPage imports one, App.tsx the other.
- Three competing event-stream idioms: `withKindLog`/`withKindLogAndStage` (workflowsApi.ts:383,398), `runSkillPackStream` (workflowApi.ts:462), and `compileStore.applyEvent` (store/compileStore.ts:161).

**Refactor:** rename/split into `api/workflowEntity.ts`, `api/skillEditor.ts`, `api/releases.ts`; delete the four duplicate wrappers; unify recording-status typing; collapse streaming into one utility.

### H-8. God components

**HumanEditPage.tsx (1,083 ln)** — hook census: 7 useState / 6 useEffect / 10 useCallback / 8 zustand selectors / 3 useQuery. It is three pages in one function. Verified extractions:

| Unit | Lines |
|---|---|
| `SkillPickerLanding` component | 494-661 |
| `usePaneResize` hook (localStorage-persisted split pane) | 182-218 |
| `useUndoRedoShortcuts` hook | 220-265 |
| `useStepMutations(skillId)` hook — 7 handlers repeat the same epilogue (`onWorkflowUpdated` + history flags) at lines **273, 297, 323, 347, 407, 438, 454** | 267-463 |
| `EditorToolbar` | 815-944 |
| `ToolPanesDialog` | 989-1080 |

**StepConfigForm.tsx (887 ln)** — 0 useState (react-hook-form owns state); the problem is one ~230-line `persistStepValues` (229-457) containing **six copy-pasted try/catch save sequences** (navigate 263-276, wait 297-310, screenshot 320-333, check 352-365, scroll 394-407, generic 442-454). One `savePatch(patch)` helper shrinks it ~150 lines and gives save semantics a single home. Selectors list (691-718) and anchors list (832-859) are structurally identical → one generic `StringListField`.

### IPC / typing / state (healthy overall)

- Context-isolated preload bridge, typed `cmd<T>()`, centralized streaming — good. Gaps: command names are bare strings (typo fails only at runtime against the Python dispatcher); no runtime validation of responses; open-index-signature `BackendEvent` forces re-casts at 7 sites.
- TanStack Query used consistently; Zustand stores narrowly scoped and justified. One duplication: `{logs, busy, error, done, result}` hand-rolled 3× (PublishPage.tsx:49-56, BuildInstallerPage.tsx:48-55, WorkflowTests) → one `useStreamedTask(fn, {kind})`.
- **Zero `any` annotations** in renderer/src. 17 non-null assertions, concentrated where routing guarantees presence.

### Dead renderer files (all verified zero-importer)

`ActionBadge.tsx`, `ConfidenceBanner.tsx`, `HowClaudeSeesThisPanel.tsx`, `StatusDot.tsx`, `ValidationEditor.tsx` (superseded by `validation/AssertionEditor`), `types/waitValidation.ts` (transitively dead), `skillPackages/StatsStrip.tsx`, `ui/tabs.tsx` — **~700 lines deletable outright.**

Duplicated UI patterns worth extracting: dialog-with-form pair in GroupPage.tsx:42-98 / 100-147; recurring red-callout class combo (HumanEditPage.tsx:599, PublishPage.tsx:161/245/355/368, GroupPage variants); log-panel-with-autoscroll triplicated. Cosmetic: CompileProgress.tsx uses inline `style={{}}` objects while everything else is Tailwind.

---

## Recommended Sequencing

**Wave 0 — safety & hygiene (≤ 1 day total, near-zero risk)**
1. Escape OAuth callback HTML; stop shipping tracebacks to renderer; validate `logo_path`.
2. Fix the LLM-router global race (context-var or explicit router arg) + add locks around undo/redo.
3. Log (don't swallow) the two `patch.py` bare excepts.
4. Delete all verified-dead code: 8 renderer files, `metadata_reporter.py`, `skill_package_templates.py`, stale `.pyc`s, misc Python helpers.
5. Extract the 7× proxy-error ladder, 21× skill-load, workspace-resolution helpers; centralize the auth.json invariant check.

**Wave 1 — de-monolithing (1–2 weeks, mechanical)**
6. Extract `navigation_context.py` from build.py, then element_identity / assertion_planner / target_builder / reporting per the table.
7. Merge/rename the renderer API layer into three domain modules; unify recording-status types.
8. Extract `savePatch`, `applyMutationResponse`, `usePaneResize`, `SkillPickerLanding` from the two god components.
9. Extract `llm/cache.py`; kill the legacy bundle-index writer.

**Wave 2 — structural (planned work)**
10. `BackendContext` injection replacing mixin self-sharing; split workflows.py.
11. Typed step models replacing dict-passing in the compile pipeline.
12. Unify confidence formula + selector-quality law behind one module each.

---

## Verification Notes

- Line counts and locations were read from current sources by four independent agents; suspected-dead symbols were checked repo-wide for importers before being listed here.
- `storage/skill_package_templates.py` orphan status was independently re-verified during report compilation.
- The dead-code clusters in §3 corroborate the historical account in `docs/archive/refactors/PHASE_4_REFACTOR_REPORT.md`.
- Nothing was modified; this document is the only artifact produced.
