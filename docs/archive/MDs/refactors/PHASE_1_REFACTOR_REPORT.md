# Phase 1 Refactor Report — Conxa Builder (Electron)

**Scope:** `conxa-builder/` (Electron main process, React renderer, Python stdio backend and compile pipeline).
**Goal:** Remove AI-generated technical debt and make the codebase maintainable by a senior engineering team, with zero behavior change.
**Branch:** `refactor/phase-1-conxa-builder` (13 commits, one per stage — see `git log` for the full trail).

## How to verify this report's claims

```bash
# Python (from conxa-cloud/, per pytest.ini's pythonpath)
cd conxa-cloud && pytest -q                      # 362 passed, 27 pre-existing failures (see below), 18 skipped
ruff check ../conxa-builder/python                # 0 findings

# TypeScript (from conxa-builder/electron/)
npm run typecheck && npm run lint && npm run build:renderer && npm run test:bridge
```

Every commit on the branch was validated against this same harness before moving to the next change. The 27 pytest failures are **pre-existing on `main`** (verified before any change was made — see Baseline below) and are identical, byte-for-byte, before and after every commit in this phase.

---

## Baseline: what "no regression" was measured against

CI (`build-studio.yml`) only packages the app — it never ran tests, lint, or typecheck before this phase, so `main` had accumulated undetected breakage. Establishing ground truth was step one:

| Check | Baseline result |
|---|---|
| `pytest` (from `conxa-cloud/`) | **362 passed, 27 failed, 18 skipped** |
| `tsc --noEmit` | 0 errors |
| `npm run build:renderer` | succeeds |
| `npm run test:bridge` | passes |

The 27 pre-existing pytest failures break down as:
- **`test_conxa_runtime.py` (11)** — environmental: the sandbox has a real `runtime/` directory that shadows the empty-directory precondition these tests assume.
- **`test_element_fingerprint.py` (3)** — a real, pre-existing bug: the `data-testid` regex in `compiler/build.py` (`data-test(?:-id)?`) requires a literal hyphen before `id` and so never matches the literal string `data-testid` (only `data-test` or `data-test-id`). Confirmed via direct regex testing. **Not fixed in this phase** — behavior preservation was the mandate; flagged below as a real bug for a follow-up.
- **`test_razorpay_routes.py` (9), `test_product_routes.py` (1), `test_llm_proxy_and_publish.py` (3)** — cloud-app tests (billing/config), outside `conxa-builder/` scope, failing on missing credentials/environment config.

This set was captured as `baseline_failures.txt` and diffed against after every single commit. It never changed.

---

## Summary of changes by stage

### Stage 0 — Quality gates (the root-cause fix)

The absence of any linter, type-checker, or formatter is why debt accumulated undetected. Added:
- **`npm run typecheck`** (`tsc --noEmit`) — flipped `noUnusedLocals`/`noUnusedParameters` from `false` to `true`, removed the deprecated `baseUrl` config. Fixed the 2 dead bindings this surfaced (`recordingShotsQ` in `HumanEditPage.tsx`, `onCompiled` prop in `PluginDetailPage.tsx`).
- **`npm run lint`** (ESLint, flat config, `eslint.config.mjs`) — TypeScript + React Hooks rules. No Prettier — deliberately, to keep this phase's diffs reviewable instead of burying substantive changes under whitespace churn.
- **`ruff check`** (root `pyproject.toml`) — pyflakes (`F`) + flake8-bugbear (`B`) only. Deliberately excludes import-sorting/pyupgrade/style rules (formatting-adjacent) and `PLC0415` (import-outside-top-level is an established, intentional pattern in this codebase — lazy imports avoid circular refs and speed up stdio startup). Cleared 18 findings: 12 unused imports, 2 dead assignments, 2 missing `raise ... from`, 1 duplicate set literal, 1 forward-ref false positive (resolved naturally by Stage 2).
- **CI gate** — `build-studio.yml` now runs `ruff check`, `pytest`, `tsc`, `eslint`, and the bridge test in a `test` job that gates the (slow, Windows-only) packaging job.

### Stage 1 — Dead code removal

All removals verified via repo-wide reference search (zero importers) before deletion:
- **Renderer:** 6 files deleted outright — `CompilePage.tsx`, `SetupWizard.tsx` (unrouted pages), `designTokens.ts` (unused function), `ValidationReportPanel.tsx`, `build/StatCard.tsx` (unused components), `styles.css` (never imported). Plus 9 dead exports: `installerDownloadUrl`/`downloadPlugin` (no-op pass-throughs returning their own input), `fetchTrackingRuns`/`fetchTrackingRun`/`fetchJob`/`streamJobEvents`/`enqueueCompileJob`/`enqueueRecompileSkillJob`/`enqueuePackageBuildJob` (reject-only stubs left over from the cloud app this code was forked from).
- **Python:** `conxa_compile/compiler/stub.py` deleted — `not_implemented_package` was defined and re-exported via `__all__` but never called anywhere.

### Stage 2 — Eliminated the selector-generator fork

`llm_selector_generator.py` and `llm_selector_generator_v2.py` had a stalled v1/v2 fork with a circular import (v2 importing `validate_selector` back from v1) and ~200 lines of fully dead code (`generate_selector_with_objective_confidence`, `compute_dom_uniqueness_signal`, `compute_self_consistency_signal`, `rank_candidates`, and friends — zero external callers). Deleted both files. Relocated the 5 functions that were actually live:
- `to_playwright_grammar` → `compiler/selector_grammar.py` (its natural home — that file's own comment already said it inlined helpers "to avoid llm_selector_generator_v2's" heavy import chain; that avoidance is no longer needed).
- `build_workflow_intent_graph` → new `llm/workflow_intent.py`.
- `SelectorCompileTask`, `compile_selectors_for_task`, `task_from_recorded_event` → new `llm/selector_regeneration.py` (the 1-click-fix re-compile path used by `patch.py`).

Also renamed `input_binding_v2.py` → `input_binding.py` (no `v1` sibling ever existed) and `derive_input_binding_v2` → `derive_input_binding`.

**Documentation fix:** `CLAUDE.md`'s invariant said "LLM does not write selector strings" without qualification, but `patch.py::_regenerate_compiled_selectors` genuinely does re-run LLM-assisted selector generation when a user manually re-targets a step in the editor. Reworded the invariant to state the real scope: the *primary compile path* never LLM-writes selectors; the 1-click fix API is a narrow, documented, user-initiated exception.

### Stage 3 — De-duplication

- Added `WorkflowRevalidationResponse` / `WorkflowStepMutationResponse` / `WorkflowUndoRedoResponse` types (`types/workflow.ts`), replacing 10 hand-repeated inline response shapes in `workflowApi.ts`.
- Extracted `runSkillPackStream()` — unified two identical `pack_log`/`pack_done`/`pack_error` event listeners.
- Extracted `withKindLog()` — unified three copy-pasted "subscribe to backend events, run a command, unsubscribe in `finally`" blocks.
- Added `lib/format.ts` with a shared `formatBytes()`, used by `BootstrapScreen` and `UpdateRequiredScreen` (byte-identical implementations before this).

**Investigated and deliberately left alone** (documented, not silently skipped):
- `BuildPage.logLineLevel`, `BuildInstallerPage.getLogLineStyle`, `CompileProgress`'s `LogRow`/`ApiCallRow` looked like duplicate log-styling logic by name, but use different classification schemes, different color palettes, and different input shapes (raw log lines vs. structured entries). Unifying them would risk visible UI drift for no real simplification — not true duplication, just convergent naming.
- `SkillPackagesPage`'s local `formatBytes` was **not** merged into the shared one — it caps at MB (no GB tier) and rounds differently. A genuine behavioral divergence; merging would silently change its output.
- The two remaining in-component `window.conxa.onEvent` call sites (`BootstrapScreen`, `App.tsx`) each couple the subscription to an immediate `cmd(...)` side effect on the same lifecycle. `useBackendEvents` doesn't support that shape without a riskier two-effect restructure, for a purely cosmetic win on code with no automated tests.
- `services/installer_builder.py`'s apparent pass-through wrapper does real setup work (dependency bootstrap, `MAKENSIS_PATH` resolution) before delegating — not a pass-through.

### Stage 4 — Decomposing oversized files

**Judgment call, stated up front:** every split below was preceded by a cross-call analysis (which function calls which, in which direction) before any code moved. Where that analysis showed tight, bidirectional coupling — the functions form one genuinely single-pass pipeline, not several independent concerns — the file was **left as one file** and the reasoning documented in its commit. Splitting those anyway would trade one kind of complexity (a long file) for another (fake abstraction boundaries with circular-feeling imports) without reducing real risk, in the single most invariant-sensitive part of this codebase.

**Python — decomposed (8 files → 24 new modules):**

| Original file | Before | After | New files |
|---|---|---|---|
| `backend.py` | 1968 | 475 | `handlers/{protocol,session,compile,plugins,workflow_editor,visual,skill_packages,runs}.py` |
| `plugin_builder.py` | 1338 | 209 | `plugin_builder_saved_skill.py` (785), `plugin_builder_output.py` (391) |
| `recorder/session.py` | 1168 | 914 | `recorder/frame_utils.py` (286) |
| `compiler/v3.py` | 686 | *(retired)* | `compiler/step_anchors.py` (542), `compiler/state_validation.py` (167) |
| `editor/workflow_service.py` | 636 | *(retired)* | `editor/workflow_dto.py` (424), `editor/workflow_mutations.py` (230) |
| `conxa_runtime.py` | 630 | 422 | `runtime_tool.py` (226) |

`backend.py`'s 56 `cmd_*` RPC handlers were split into 7 domain mixins (`SessionMixin`, `CompileMixin`, `PluginsMixin`, `WorkflowEditorMixin`, `VisualMixin`, `SkillPackagesMixin`, `RunsMixin`) composed via `class Backend(SessionMixin, CompileMixin, ...)`. `dispatch()`'s `getattr(self, f"cmd_{cmd}")` lookup is unchanged and resolves through the MRO regardless of which mixin defines a handler — this is a pure structural move.

**A real bug this surfaced and fixed:** `conxa-cloud/tests/test_build_studio_backend.py`'s fixture monkeypatches `_write` on the dynamically-loaded `backend` module to capture emitted events. Once `_write`/`_event_sink` moved into `handlers/protocol.py`, the test's patch stopped intercepting events emitted by the (now-separate) command handlers — they still wrote to real stdout, just not into the test's capture list. One test failed with the event genuinely present in captured stdout but absent from the test's assertion list. Root-caused and fixed by patching `handlers.protocol._write` too, not by loosening the assertion.

**Python — evaluated, deliberately left as one file** (with the specific evidence in each case):
- **`compiler/build.py`** (1191 lines) — cross-call analysis showed every candidate boundary (fingerprint/identity building, vision-anchor handling, step assembly, orchestration) calling back into every other one. A genuinely single-pass, tightly-coupled compile pipeline — also the most invariant-bound file in the codebase (selector resolution, `IdentityBundle` scoring). Not split.
- **`recorder/session.py`'s `RecordingSession` class** (the ~825-line remainder after extracting `frame_utils.py`) — a stateful object driven by live async Playwright callbacks (frame-ready, dialog, download, disconnect). Automated coverage for the class itself is thin (existing tests mostly exercise the pure-function helpers that *were* extracted); this sandbox has no cached Chromium binary to manually verify a live recording session end-to-end. Deferred to a pass with either better test coverage or a real browser available.
- **`services/bootstrap.py`** (552 lines) — cross-call analysis showed genuine bidirectional coupling between manifest/version tracking, download/install mechanics, and status orchestration (each group calls into the other two).
- **`editor/recording_visual.py`** (505 lines) — 4-5 already-large (100–130 line), already-independent, already-cohesive functions. Splitting would mean one function per file — over-fragmentation, not simplification.

**TypeScript — decomposed (2 of 11 candidate files):**

| Original file | Before | After | New files |
|---|---|---|---|
| `SkillPackagesPage.tsx` | 1242 | 901 | `lib/skillPackageTree.ts` (148), `components/skillPackages/{PanelChrome,StructureTree,StatsStrip}.tsx` |
| `WorkflowViewer.tsx` | 456 | 110 | `lib/workflowViewerHelpers.ts` (80), `components/workflowViewer/{WorkflowHeader,WorkflowStepItem,DeleteStepDialog}.tsx` |

**TypeScript — surveyed, not yet split (9 files, ~6,000 lines):** `HumanEditPage.tsx` (1010), `StepEditorPanel.tsx` (949), `BuildInstallerPage.tsx` (878), `PluginDetailPage.tsx` (875), `ScreenshotViewer.tsx` (608), `CompileProgress.tsx` (594), `PluginWorkflowTests.tsx` (581), `ParameterizationDrawer.tsx` (549), `ValidationEditor.tsx` (505).

These split into two real categories, confirmed by inspection (see the per-file function listing captured during this phase):
- **`BuildInstallerPage.tsx`, `PluginDetailPage.tsx`, `ScreenshotViewer.tsx`, `CompileProgress.tsx`, `PluginWorkflowTests.tsx`, `ParameterizationDrawer.tsx`, `ValidationEditor.tsx`** already contain multiple independently-named sub-components in one file (the same shape `WorkflowViewer.tsx` and `SkillPackagesPage.tsx`'s presentational layer had). **Mechanically identical, low-risk follow-up** — move each named component to its own file the same way, verify each component's actual usage sites first (don't assume; some sub-components are used by more than one caller, some aren't used by the page's siblings at all), then run the same `tsc`/`eslint`/`build:renderer` gate.
- **`HumanEditPage.tsx`'s and `StepEditorPanel.tsx`'s main components** are shaped like `SkillPackagesPage.tsx`'s core (which was *not* split): one large function with a dozen-plus interdependent `useState`/`useCallback` hooks, not a cluster of independent sub-components. Splitting these safely needs custom-hook extraction (e.g. `useStepEditor()`) with careful state-lifting, verified by hand since there is no automated renderer test coverage — a different, higher-risk kind of work than the mechanical moves above.

### Stage 5 — Pattern standardization

- Removed the redundant `async` keyword from 6 functions in `workflowApi.ts`/`pluginApi.ts` that only `return`ed a promise-returning call with no direct `await` — now consistent with the other 48 functions in those files.
- Confirmed zero remaining `_v2`/`_V2` suffixes anywhere in the builder (Stage 2 cleared the last ones).
- Investigated the two "parallel" recording APIs (`pluginApi.startWorkflowRecord` vs `workflowApi.postStartRecording`) — they call the same backend commands but with genuinely different payload shapes for different flows (plugin-attached workflow recording vs. standalone recording). Not duplicates; left alone.

---

## Remaining technical debt (not touched this phase, in priority order)

1. **`compiler/build.py`'s `_build_element_fingerprint` `data-testid` regex bug.** `data-test(?:-id)?` never matches the literal string `data-testid` (missing an optional hyphen before `id`; the correct pattern, `data-test(?:-?id)?`, already exists correctly in `selector_grammar.py`'s equivalent regex). This is the root cause of 3 of the 27 baseline pytest failures. **Not fixed** — out of scope for a behavior-preserving refactor, but it's a real bug affecting selector durability scoring and should be a Phase 2 priority.
2. **9 renderer files still over 500 lines**, split into the two categories above — 7 are cheap mechanical follow-ups; `HumanEditPage.tsx` and `StepEditorPanel.tsx` need custom-hook extraction.
3. **`RecordingSession` class** (~825 lines) — needs either better automated test coverage or manual verification in an environment with a cached Chromium binary before it can be safely decomposed.
4. **`compiler/build.py`, `services/bootstrap.py`** — genuinely tightly coupled; a real decomposition (not a mechanical file-move) would need someone with deep domain knowledge of the compile pipeline / dependency-bootstrap flow to redesign the boundaries, not just relocate code.
5. **`conxa_compile/plugin_builder.py`'s `zip_plugin` function** appears to have zero callers anywhere in the repo (found while mapping `plugin_builder.py`'s structure in Stage 4b). Not removed — dead-code hunting was Stage 1's job, and this surfaced afterward; flag for a follow-up Stage-1-style sweep.
6. **The `skills/` vs `skill-packs/{company}/` dual-write in `plugin_builder_output.py`.** `_write_skill_packs_format`'s docstring says it writes the newer layout "alongside the legacy `skills/` layout... kept untouched for backward compatibility" — and the new layout actually *reads from* the legacy one on disk. This coupling was preserved as-is (untangling it is a functional question, not a structural one) but is worth a dedicated look.
7. **No Prettier / Ruff-format.** Deliberate for this phase (see Stage 0) — recommended as a single, isolated, whitespace-only commit *after* this phase lands, so it doesn't get conflated with the substantive changes here.
8. **No automated tests for the renderer.** Confirmed zero component/unit tests anywhere under `renderer/src/`; the only renderer test is `test/bridge.test.js` (the main-process IPC layer). All renderer decomposition work in this phase was validated by `tsc` + `eslint` + `build:renderer` (which catch wiring/type errors) and manual code review — not by exercising the actual UI. Recommend `/verify`-style manual smoke testing (or component tests) before Phase 2 touches the renderer further.
9. **Python compile-pipeline tests live in `conxa-cloud/tests/`, not beside the code they test.** This is a pre-existing repo convention (`pytest.ini` wires `conxa-builder/python` onto the cloud repo's pythonpath) rather than something introduced or fixed in this phase — noting it because it's the reason `test_conxa_runtime.py`'s 11 failures couldn't be used as a regression signal for the `conxa_runtime.py` split (Stage 4f); that split was verified by hand-tracing every `patch()` target instead.

## Recommended next steps before Phase 2

1. Fix the `data-testid` regex bug (item 1 above) — small, isolated, high-value.
2. Finish the mechanical renderer splits (item 2's first category) — same pattern as `WorkflowViewer.tsx`/`SkillPackagesPage.tsx`, low risk, ~1 hour per file.
3. Add component tests for at least the largest/most-edited renderer pages before attempting the harder `HumanEditPage.tsx`/`StepEditorPanel.tsx` decomposition — untested interactive state is the main reason those two (and `RecordingSession`) were deferred.
4. Land Prettier + `ruff format` as one isolated commit.
5. Run a second Stage-1-style dead-code sweep now that Stage 4's restructuring has surfaced a couple of new candidates (`zip_plugin`, possibly others uncovered once the renderer splits finish).
6. Decide, with product/domain input, whether the `skills/`/`skill-packs/` dual-write (item 6) should be collapsed to a single format now that the newer layout has been live for a while.
