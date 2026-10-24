# TODO — Conxa Project Backlog

This is the single prioritized backlog for Conxa, spanning documentation, architecture, and every
subsystem. It was assembled from a full 2026-07 documentation audit that cross-checked every doc in
`docs/` and `research-analysis/` (~85 files) against the actual codebase, then extended in a second
pass to pull in the business-strategy, go-to-market, ops, and edge-case-reliability documents that
the first pass had only summarized without mining for concrete action items.

**This file is organized by priority (P0 → P4), not by subsystem.** Each item still carries a
**Category:** field showing which part of the platform it belongs to (Product Strategy, Documentation,
Architecture, Builder, Runtime, Cloud, MCP, Execution & Recovery, Auto Updates, Testing & Cleanup,
Advanced) — use that to find everything in one subsystem, or just read top-to-bottom for "what's most
important." Within each priority tier, items are grouped by category in that same order, so related
items still sit near each other. Item IDs (`PROD-`, `DOC-`, `ARCH-`, `BUILD-`, `RT-`, `CLOUD-`, `MCP-`,
`EXEC-`, `UPD-`, `TEST-`, `ADV-`) are unchanged from earlier versions of this file, so cross-references
between items ("depends on EXEC-1") still resolve regardless of which priority section an item is in.
Enterprise-readiness work (RBAC/SSO/tenant isolation, the IT admin console, SOC 2/ISO 27001) doesn't
get its own category — it's tracked under **Cloud** (`CLOUD-1`) and **Product Strategy** (`PROD-9`,
`PROD-10`).

**How this relates to other planning docs:**
- [`docs/Implementation-Plan.md`](docs/Implementation-Plan.md) is the engineering-detail record of what's *already shipped* (Phase 1/2, with file-level "what was built" write-ups). This file is what's still *open*.
- [`docs/Sales-Blockers.md`](docs/Sales-Blockers.md) is the sales-framed subset of the near-term items here that block enterprise contracts.
- [`docs/Security.md`](docs/Security.md) is the numbered security-gap tracker (SG-01…); several items below are drawn from its still-open gaps.
- [`research-analysis/06-execution-plan/master-recommendations.md`](research-analysis/06-execution-plan/master-recommendations.md) is the deeper research backing (Source/Problem/Gains/ROI) for the architecture-level items in the Execution & Recovery, Builder, Cloud, and MCP categories below — read it for the *why*, this file for the *what's left and in what order*.
- [`research-analysis/conxa-critical-analysis.md`](research-analysis/conxa-critical-analysis.md) and its companion [`research-analysis/conxa-solutions-by-problem.md`](research-analysis/conxa-solutions-by-problem.md) are the deeper backing for the Product Strategy category below.
- [`research-analysis/05-reliability/top-50-improvements.md`](research-analysis/05-reliability/top-50-improvements.md) and [`research-analysis/06-execution-plan/build-order.md`](research-analysis/06-execution-plan/build-order.md) are the deeper backing for the added Execution & Recovery items.

**How to use this file:** when you complete an item, mark it done in place — strikethrough the title, add a resolution date and a one-line note — matching the pattern used in `docs/Sales-Blockers.md`/`docs/Security.md`. Don't delete completed items; a backlog with no history of what's been closed is less useful than one that shows velocity. When you discover new open work during a task, add it here rather than leaving it undocumented.

**Priority legend:** P0 = critical and time-sensitive, supersedes every other priority, do immediately. P1 = blocking or foundational, do first. P2 = high value, do soon. P3 = valuable, sequence around other work. P4 = low urgency, opportunistic.
**Complexity legend:** S = hours. M = ~1-3 days. L = ~1-2 weeks. XL = multi-week, likely needs its own sub-plan.
**`[DECISION]`** marks items that need a maintainer/founder answer before engineering work can be scoped — don't treat these as already-scoped tasks.
**`[UNVERIFIED]`** marks items sourced from the research corpus that were *not* individually confirmed against current code (unlike the rest of this file, where every "still open" claim was grep/read-verified) — do a quick check before starting in case it's already been shipped since the source doc was written.

---

## Progress Dashboard

Counts are of still-open items only. Resolved items live in [`Done.md`](Done.md) — move an item there (cut it from this file) when it is done, and adjust the count here.

| Priority | Remaining |
|---|---|
| P0 — Critical / Time-Sensitive | 15 |
| P1 — Blocking / Foundational | 3 |
| P2 — High Value, Do Soon (incl. Discovered Items) | 44 |
| P3 — Valuable, Sequence Around Other Work (incl. Discovered Items) | 20 |
| P4 — Low Urgency, Opportunistic | 37 |
| **Total** | **119** |

---

## P0 — Critical / Time-Sensitive (15 remaining)

**Added 2026-08-09, direct from the founders — supersedes every previously-tracked priority in this file.** The four items below are the reason the rest of this backlog shifted down one tier (old P0 → P1, old P1 → P2, old P2 → P3, old P3 → P4); no other item content changed, only the section labels. See the "Fourth pass" note at the bottom of this file.

### RECOV-2 — Deliver recovery artifacts to customer machines (phases 4–6 of the recovery redesign)

Phases 1–3 shipped 2026-09-02 (see `FIX.md`, `docs/TRD.md` §10.1): the two ungated guessing stages
are deleted, the agent tier is armed from round one, and the prompt no longer describes a reference
image that isn't attached. What remains is making the artifacts actually arrive, so Tier B has a
real before-picture instead of a suppressed sentence.

- **Phase 4 — recorded page structure.** The recorder already captures a 24-level ancestor chain
  (`bridge.js::captureAncestors`, carried through `session.py`), and `recovery.json` already syncs.
  Emit a trimmed `recorded_context` per step at build time (~6 ancestors: tag / id / classes / a
  short text excerpt — **drop `outer_html`**, it is 2000 chars each and would blow the 40k-char
  digest budget) and add it to the Tier B payload. No recorder work, no re-recording, no sync-list
  change.
- **Phase 5 — artifact archive endpoint.** Publish already uploads `visuals/` (`backend.py`'s
  `rglob("*")`) and the cloud already stores it; only the delta route's hardcoded five-JSON
  allow-list (`skillpack_update_routes.py`) filters it out. List artifacts as `{path, sha256}`
  metadata in the delta response, and add an authenticated route where the machine sends the
  hashes it is missing and receives one archive of exactly those, per skill. Read from the live
  mirror so artifacts match the code files just downloaded.
- **Phase 6 — two-pass sync + hash store.** Code files stay on the blocking path; once they
  activate the gate opens and workflows run. The artifact pass then runs unwaited, one skill at a
  time. Store by content hash **outside** the per-version directories so the three retained
  versions share one copy and identical screenshots across skills are stored once. `sync.js`
  already has an unused fetch-by-link branch (`content_url` → `_downloadBuffer`) and an
  `atomicWrite` with SHA-256 verification — both reusable as-is.

Blocked on nothing. Full plan: `~/.claude/plans/create-a-end-to-merry-parrot.md`; design note:
https://claude.ai/code/artifact/eb3fdd7c-d73e-46fa-b146-18c8e491829c

### TEST-11 — Run the full manual testing suite in `docs/testing/` (re-invoke & reconfigure LLM keys first)
- **Category:** Testing & Cleanup
- **Description:** Execute every test plan currently living in [`docs/testing/`](docs/testing/) — consolidated since 2026-08-26 into [`01-WORKFLOWS-TO-TEST.md`](docs/testing/01-WORKFLOWS-TO-TEST.md) (pending work, sorted easy→hard; WF-1…WF-12) and [`02-WORKFLOWS-PASSED.md`](docs/testing/02-WORKFLOWS-PASSED.md) (manually-passed workflows + capability dashboard) — end to end, and fix whatever they surface. **Before any testing begins**, the LLM provider keys must be re-invoked (regenerated/refreshed) and re-configured: existing keys may be expired, rotated, or rate-limited, and a compile- or recovery-tier test failing on auth would waste the run and mask real defects. Re-run the router setup per `conxa-cloud/backend/ROUTER_SETUP.md` and verify each configured provider responds before starting the suites.
- **Why required:** the testing docs encode the verification plans for recently shipped work (long-chain multi-tab workflows, stress behavior, production readiness, tier limits) but none of them have been executed as a full pass; running them is the only way to confirm the platform actually holds up.
- **Business value:** catches customer-facing failures now, on our own machines, instead of in front of an enterprise buyer.
- **Technical value:** a full pass across all four docs exercises compile, runtime execution/recovery, cloud billing/tier limits, and stress paths in one sweep — the same coverage the individual fixes were unit-tested against, now verified end to end.
- **Dependencies:** LLM keys re-invoked and re-configured *first* (see above) — several suites depend on working LLM calls through the proxy.
- **Suggested order:** immediate — top of the P0 queue.
- **Complexity:** L — four separate suites plus whatever fixes they surface.
- **Success criteria:** all four docs' test plans executed with results recorded; every failure either fixed or filed as its own TODO item with a repro; LLM providers confirmed healthy via the reconfigured keys before the first suite ran.
- **Found via:** direct founder instruction (2026-08-22).

### TEST-12 — Execute the hard-mode mega-workflow gauntlet (now WF-9 in `01-WORKFLOWS-TO-TEST.md`)
- **Category:** Testing & Cleanup
- **Description:** Run the 2026-08-25 hard-mode test plan — now WF-9 in [`docs/testing/01-WORKFLOWS-TO-TEST.md`](docs/testing/01-WORKFLOWS-TO-TEST.md) — end to end: one ~60-step recording across six domains and four tabs (self-mutating local page, scroll-load, late-render, iframe ping-pong, entity-specific cart removal, parameterized search), followed by its replay gauntlet R1–R8 (parameterized replays, mid-run session sabotage, same/different-platform concurrent runs with cancel, canvas + CAPTCHA boundary refusals) and the overnight ×20 endurance loop. File every failure per the doc's exit-criteria routing.
- **Why required:** this suite covers the axes EXEC-10 never touches — DOM that mutates between record and replay, data variance across replays, RT-3 concurrency, and honest-failure boundaries — which is exactly the gap between "works on demo sites" and "survives a customer's first week." Passing it is the evidence base for the reliability claims made to buyers; Segment D is also PROD-3's concrete success criterion.
- **Business value:** same rationale as TEST-11, extended to the failure classes most likely to surface in front of an enterprise buyer rather than here.
- **Dependencies:** same LLM-keys-healthy precondition as TEST-11; the mutator fixture must be built first (Phase 0 of the doc).
- **Suggested order:** immediately after (or alongside) TEST-11.
- **Complexity:** L — one ~60-step recording plus the replay gauntlet and the overnight run.
- **Success criteria:** the recording compiles to ≥50 steps untruncated; R1/R2/R5/R6 pass outright; R2's zero-result probe and R4 fail loudly; R7 produces clean refusals; R8 shows flat memory and run 8 ≈ run 1; every miss filed as its own TODO item with events.jsonl / recovery-log evidence attached; passing shapes promoted into `runtime/test/gate-skill/` after three consecutive clean passes.
- **Found via:** hard-mode robustness review (2026-08-25).

### RT-3-RETRY-BUDGET — Per-skill retry budget shared across concurrent same-skill runs
- **Category:** Runtime / Execution & Recovery
- **Description:** `retry_budget.js` keys attempts by `${slug}:${stepIndex}` only (see its
  `ponytail:` comment, added alongside RT-3). Two concurrent runs of the *same* skill share one
  counter: a successful sibling's `clearRetryBudget(slug)` resets a still-struggling sibling's
  count, and a struggling sibling can burn a shared budget faster than solo. Not fixed as part of
  RT-3 because keying by `runId` would silently remove the budget's actual purpose — persisting
  *across* MCP calls so an agent can't retry a broken step forever by always starting a fresh run.
- **Why required:** low severity today (bounded — a few extra retries for a sibling, never
  corruption) but worth a deliberate fix once same-skill parallel runs are common in practice.
- **Suggested fix:** key the in-run attempt count by `${runId}:${slug}:${stepIndex}`, and keep a
  separate `${slug}:${stepIndex}` counter that only clears on cross-call resume — same-skill
  siblings stop sharing attempts without losing the cross-call ceiling.
- **Dependencies:** none blocking.
- **Suggested order:** low priority — revisit if fleet telemetry shows same-skill parallel runs are
  common.
- **Complexity:** S.
- **Found via:** RT-3 audit (2026-08-22).

### RT-3-CAP-SIZING — Derive the concurrency cap from machine specs instead of a flat number
- **Category:** Runtime / MCP / Execution & Recovery
- **Description:** `CONXA_MAX_CONCURRENT_RUNS` defaults to a flat `5` regardless of the host
  machine's specs — same number on an 8GB laptop and a 64GB workstation. Considered and
  deliberately deferred during RT-3 follow-up: a *default* derived from `os.totalmem()` at process
  startup (e.g. roughly one run per 1.5–2GB of total RAM, clamped to a sane min/max like 2–8),
  still overridable via the env var for IT-managed fleets. A **live** per-call resource check
  (e.g. `os.freemem()` at admission time) was explicitly ruled out, not just deferred: it makes
  admission non-deterministic (the same call can pass or fail depending on what else is open on
  the machine at that instant) and doesn't protect against a run's memory footprint growing after
  admission; `os.loadavg()` is also unusable for this on Windows (always returns `[0,0,0]` —
  a known Node.js limitation), so there is no reliable live CPU signal to gate on at all.
- **Why required:** a flat cap either wastes headroom on a well-specced machine or risks OOM on a
  modest one; each concurrent run is a full Chromium instance (~300–500MB+).
- **Suggested fix:** compute the default at startup from `os.totalmem()` (one-time, not
  per-call — keeps admission deterministic), clamp to a min/max range, keep
  `CONXA_MAX_CONCURRENT_RUNS` as the override for anyone who wants to set it explicitly.
- **Dependencies:** none blocking.
- **Suggested order:** low priority — revisit if fleet telemetry shows the flat default is a real
  bottleneck (refused-past-cap events) or a real risk (OOM reports) on either end of the spectrum.
- **Complexity:** S.
- **Found via:** direct founder question (2026-08-22) — "can we cap concurrency to whatever the
  laptop supports instead of a fixed number?"

### BUILD-14 — Record/Compile page: workflow-centric counting + folder grouping
- **Category:** Builder / Cloud
- **Description:** Build Studio's record/compile screens (`PluginListSidebar.tsx`, `PluginSwitcher.tsx`) and the surrounding UI are built around counting and switching between "plugins" — but a single published plugin/skill pack can bundle many individually recorded workflows, and the cloud's entitlement layer already models that distinction internally (`entitlements.py::ensure_workflow_publishable(principal, plugin_id, workflow_ids)` treats workflows as the countable sub-unit of a plugin). Redesign the Record + Compile page to show and count **workflows**, not plugins, and add folder-style grouping (e.g., "Marketing", "Sales") so a vendor with a growing catalog can organize workflows into named groups. Add the backend support needed for a workflow→folder association as **pure organizational metadata** — a label attached to a workflow record, not a structural change to the compiled `SkillPackage`, its `IdentityBundle`, or anything the runtime reads. Grouping must never touch the skill contract (see `CLAUDE.md`'s Key Invariants and ARCH-3's contract/executor boundary) — moving a workflow between folders must be indistinguishable, from the runtime's perspective, from not moving it at all.
- **Why required:** the current plugin-centric counting doesn't match how vendors actually think about or are billed on their catalog (by workflow, per the entitlement model's own `workflow_ids` field), and there is no way today to organize a growing list of recorded workflows.
- **Business value:** keeps Build Studio usable at the scale a vendor with dozens of workflows will actually reach, and surfaces the same unit (workflows) that entitlements already count and gate on, instead of a different unit (plugins) that can undercount or overcount what a customer is actually paying for.
- **Technical value:** reuses the entitlement model's existing plugin_id/workflow_ids distinction rather than inventing a new counting unit; folder metadata is additive and stored out-of-band from the compiled skill package, which keeps the change low-risk to execution as long as that boundary is respected.
- **Dependencies:** should land together with an explicit check (a test, per this repo's non-negotiable-invariant convention) proving folder assignment is never read by `runtime/run.js` or by any step of the compiler's build path — this is the same class of contract-boundary discipline ARCH-3 is asking for more broadly.
- **Suggested order:** immediate, alongside the other three P0 items.
- **Complexity:** M–L — UI rework of the Record/Compile screens plus a new backend field/endpoint for folder assignment; no change to the skill contract itself.
- **Success criteria:** the Record/Compile page shows and counts workflows, not plugins, matching what's actually entitled/billed; a workflow can be assigned to a named folder and the list can be filtered by folder; compiling and executing the same workflow before and after a folder move produces byte-identical compiled output and identical runtime behavior.

</details>

### Workflow Groups CI gate coverage
- **Category:** Runtime / CI
- **Description:** `runtime/test/gate-skill/skill-pack/gate/pack.json` (the fixture `gate_replay.js` replays against a real host exe in CI) predates Workflow Groups and has no `groups` block, so the group-aware path in `runtime/browser.js` (`_resolveGroup`, `getGroupAuthContext`) is exercised by `runtime/test/test_group_auth.js`'s unit tests but never by the execution gate. Add a second gate fixture (or extend the existing one) with a one-app group and a matching `{company}__{appId}_raw_state.json`, and confirm `gate_replay.js` passes against it before the app-layer CI gate is considered to cover Workflow Groups.
- **Why required:** the execution gate is what actually blocks a bad app-layer release; a code path it doesn't exercise isn't really protected by CI.
- **Dependencies:** none — the underlying group-auth code already ships.
- **Complexity:** S.
- **Update (2026-08-12):** skill-pack directories now nest under `{group_id}/` on disk (`skill-packs/{company}/{group_id}/{skill_slug}/`, see `docs/TRD.md` §5.3/§5.6) — `skill_loader.js` requires this layout unconditionally, so `gate_replay.js`'s fixture-staging loop was updated to stage `gate-skill` under a `_default/` folder (matching the fallback `skill_loader.js` uses when a pack has no `skill_groups` map), keeping the gate green. That's the minimum fix to not break the gate — this item's original, broader scope (a real `groups` array + group-scoped app auth in the fixture, so `_resolveGroup`/`getGroupAuthContext` get exercised too, not just path resolution) is still open.

### ARCH-4 — Migrate installer generation from Build Studio to Conxa Cloud (CDN-hosted, one per workspace, swappable)
- **Category:** Architecture / Cloud
- **Description:** The 2026-07-09 publish/installer architecture redesign (tracked informally, not previously in this file) already reframed installers as static, Conxa-owned platform artifacts and explicitly named "migrate installer generation to a cloud build pipeline" as its long-term half, deferred until Conxa had paying customers — that deferral ends now. Move installer *generation* out of Build Studio's local NSIS build (`conxa_compile/installer_builder.py`) into a cloud build pipeline. Each workspace gets exactly **one** installer build, produced automatically either at workspace creation or on first skill-pack publish (whichever happens first), stored in Conxa's CDN for distribution. The installer's content (company identity, branding, endpoints, `{installer_version}`) stays swappable at any time via config inputs — swapping is a re-generate-from-the-same-inputs operation, not a new local build process, consistent with the already-documented design that installers carry only static config and never a skill file tree.
- **Why required:** vendors currently still run a local, Windows-only NSIS build inside Build Studio for every installer, which the 2026-07-09 redesign always intended to centralize once there were real customers to justify the infrastructure investment — that trigger condition is now met.
- **Business value:** removes a local build dependency from every vendor's workflow, gives Conxa consistent control over signing/versioning across every installer in the fleet, and turns "update this workspace's branding" into a config change instead of a vendor re-running a local build tool.
- **Technical value:** builds directly on already-shipped machinery — the versioned `{installer_version}` endpoint scheme, Ed25519 manifest signing, and the installer cloud-upload path that already exists today as optional/best-effort from the local build becomes the only path, no new upload mechanism needed. Directly overlaps CLOUD-2's blob/CDN storage item, since a cloud-built installer needs the same durable object storage CLOUD-2 already flags as a real (not just scalability) requirement.
- **Dependencies:** should land together with, or immediately ahead of, CLOUD-2's CDN/object-storage work, since the built installer needs a durable home from day one; must preserve every static-config-only invariant already documented in `CLAUDE.md`'s Deployment section (installer never bundles skill file trees, only `pack.json`).
- **Suggested order:** immediate, alongside the other three P0 items.
- **Complexity:** L–XL — a real cloud build pipeline (NSIS build + Authenticode signing are currently local-machine-only steps) is significant new cloud infrastructure, not a refactor of existing code.
- **Success criteria:** creating a workspace, or publishing its first skill pack, produces exactly one installer — built and signed in the cloud and served from Conxa's CDN — with no local Build Studio NSIS step involved; changing a workspace's branding/config produces an updated installer at the same distribution URL without a full local rebuild.

### EXEC-10 — Long-workflow stress test: 30–40 steps, multiple tabs, cross-domain file transfer
- **Category:** Execution & Recovery / Testing & Cleanup
- **Description:** Build and pass a real test workflow of 30–40 steps that opens multiple tabs across different domains and moves a file between them end to end — e.g., export/download a file from a page on tab A, switch to tab B on a different domain, and upload that file there. Confirm the runtime actually supports this workflow shape today — sustained execution at this step count, correct cross-tab context switching, and a real file handoff between tabs — and fix whatever breaks. EXEC-5's #43 ("new-tab/new-window landed-context verification so an action can't silently execute in the wrong window") already names part of this gap in the abstract; this item is the concrete stress test that proves it one way or the other instead of leaving it as an inferred risk.
- **Why required:** this exact shape — long, multi-tab, cross-domain, with a file passed between tabs — is a realistic enterprise pattern (pull a report from one system, upload it into another) that nothing in `runtime/test/` or the CI execution gate (`gate_replay.js`) currently exercises at this length or tab-complexity.
- **Business value:** this is precisely the kind of workflow that would otherwise fail silently in front of a customer on day one; proving it works — or finding and fixing what doesn't — before a customer discovers it protects the core reliability pitch directly.
- **Technical value:** likely exercises several already-tracked but unverified gaps at once — multi-tab/window context handling, download/upload verification (EXEC-5 #31/#32), and long-run stability (the recovery-tier ceiling, `RETRY_BUDGET_MAX`) — so it doubles as an integration test for multiple existing backlog items, not just new coverage.
- **Dependencies:** benefits from EXEC-5's download/upload verification handlers landing first, though this stress test can also be the forcing function that gets them built.
- **Suggested order:** immediate, alongside the other three P0 items; a natural companion to EXEC-5.
- **Complexity:** M for the test itself; likely M–L once it surfaces real multi-tab/cross-domain gaps that need runtime fixes — expect it will, since nothing today explicitly exercises this path.
- **Success criteria:** a recorded 30–40 step workflow spanning at least two tabs on different domains, including a file downloaded in one tab and uploaded in another, compiles and replays successfully end to end; a passing `runtime/test/` fixture (or `gate_replay.js` fixture) captures this scenario going forward so it can't silently regress.
- **Update (2026-08-15):** a test plan for this item now lives at `docs/testing/01-WORKFLOWS-TO-TEST.md` — six happy-path workflows on free public sites plus eight adversarial "break tests". Writing it surfaced four code-verified gaps that make the item's success criteria **currently unreachable as written**, two of which are now tracked separately below (EXEC-11, EXEC-12). The other two were EXEC-10's own scope: (a) `tab_open`/`tab_switch`/`popup` were registered as **no-op handlers** in `run.js` (`NOOP_STEP_TYPES`, ~L39-43 + L872-874), so the runtime never switched which page it acted on and every post-new-tab step replayed against the original tab; (b) there was **no in-workflow binding from a download to a later upload** — `upload` took its path from `step.value`/`inputs` while downloaded paths were only surfaced to the agent as result *text*, so a single compiled skill couldn't hand a file from tab A to tab B without an LLM round-trip per file.
- **Update (2026-08-15, gaps (a)/(b) + the W-5 download-listener gap closed):** the recorder now assigns every page a stable tab id + `opened_by` classification (`site`/`user`/`initial`) as it's discovered (`session.py::_register_new_pages_sync`), stamped onto every event (`RecordedEvent.tab`); the compiler inserts `tab_open`/`tab_switch` markers at tab-boundary crossings and carries `tab` on every step (`build.py::_insert_tab_markers`/`_build_tab_context`); the runtime resolves each step's live page fresh per step (`runtime/tabs.js::resolveStepPage` — waits for a site-opened tab, creates a user-opened one itself, never falls back to the wrong tab on a miss); and `server.js` now attaches its download/diagnostics listeners to every tab opened during a run, not just the initial one, closing W-5. A downloaded file can also bind to a later matching upload in the same run with no LLM round-trip (`skill_package_builder_saved_skill.py::_bind_downloads_to_uploads`), closing (b) directly. Unit-covered: `runtime/test/test_tabs.js`, `conxa-cloud/tests/test_recorder_tab_identity.py`, `test_compile_tab_markers.py`, `test_download_upload_binding.py`. **Still open:** the actual 30–40 step, multi-tab, cross-domain recording this item asks for, and a `runtime/test/gate-skill/` fixture that exercises a tab switch in the CI execution gate (`gate_replay.js`) so it can't silently regress — neither was built in this pass; both need a real browser/host-exe environment this session didn't have to record and verify against.
- **Update (2026-08-16, real-workflow replay found two more gaps in the above, now fixed):** replaying an actual recorded 3-tab workflow ("Create a Lead": Render dashboard → a Ctrl+T-opened Vercel tab → a `target="_blank"` login popup off that tab) surfaced two defects the unit coverage above didn't catch, because both need a *second* tab-opening step to trigger. (1) `tabs.js`'s `pendingPages` queue was a bare FIFO with no notion of "already spoken for" — since `context.newPage()` (used for `opened_by: "user"` tabs) fires the same `"page"` event a real popup does, the blank page created for the Ctrl+T tab sat in the queue and was handed back out as the *next* site-opened tab, pointing two different `tab.id`s at one page; fixed with a `bound` Set the registry checks before handing anything out. (2) the recorder's synthetic `popup` event was enqueued with no source page, so it fell back to whatever tab was `_active_page_sync()` — usually `tab_0` — even when the popup was opened from a background tab; the compiler then inserted a spurious `tab_switch` back to `tab_0` right before the real `tab_open`, and replay followed it. Fixed by threading the actual listener-owning page through `_attach_page_listeners`/`_enqueue_synthetic` (recorder-side, affects only newly recorded sessions) and by making `run.js` treat a tab-marker step with no `tab` block as "stay on the current page" rather than "go to tab_0" (runtime-side, fixes already-compiled packs too). Also raised the page-load budget from 15s/8s hardcoded values to a consistent 60s (`PAGE_LOAD_TIMEOUT_MS`, `CONXA_PAGE_LOAD_TIMEOUT_MS`) across the `navigate` handler, the new-tab settle, and URL-type assertion timeouts — the immediate trigger was Vercel loading slower than 15s. New unit coverage: `runtime/test/test_tabs.js` (the exact user-tab/site-tab collision, an about:blank popup settle, `stepInheritsPage`), `conxa-cloud/tests/test_recorder_tab_identity.py` (popup stamped with the opener tab, not the active tab). **Still open, low-severity:** `resolveStepPage`'s `tab_0` fast path returns before `_settle` runs, so switching back to the initial tab never calls `bringToFront()` in headed/`watch: true` mode — cosmetic only (headless replay, the default, is unaffected), not fixed in this pass.
- **Update (2026-08-16, same-day follow-up: the about:blank wait above had its own bug):** replaying the fix above surfaced that `_settle`'s new "wait for the page to leave about:blank" logic applied unconditionally — including to a **user-opened** (Ctrl+T) tab, which this same registry creates blank on purpose and which stays blank until the compiler's own synthesized `navigate` step runs against it. Nothing external ever navigates that page on its own, so the wait ran to its full `loadTimeoutMs` (60s) with no chance of succeeding — once resolving the `tab_open` marker step, again resolving the `navigate` step immediately after it on the same tab, roughly doubling the stall. Fixed by gating the wait on `step.tab.opened_by !== "user"`, so it only ever runs for a real site-opened popup, where the site's own click handler is actually expected to navigate it a beat after creation. Covered by two new `runtime/test/test_tabs.js` cases asserting `waitForURL` is never called for a user-opened tab and is still called for a site-opened one.
- **Update (2026-08-25, first full-shape run passes):** the item's core scenario now has a real passing run behind it. A single recorded workflow ("mega-workflow", session `bd8b2048`) spans **42 steps / 6 tabs / 6 hosts** (`filebin.net`, `demoqa.com`, `the-internet.herokuapp.com`, `dashboard.render.com`, `vercel.com`, `search-engine-5nfe.vercel.app`) with two in-run download→upload handoffs that compiled to real W-2 bindings (`{{downloaded_file_2}}`, `{{downloaded_file_3}}` — no `file_path` input supplied), including a return-to-initial-tab switch and a site-opened popup sign-in (the B-1 shape). Compiled clean and replayed end to end via Build Studio Run Test (2026-08-25 05:33 local); two smaller published skills had already proven the halves separately on 2026-08-23 (see `docs/testing/02-WORKFLOWS-PASSED.md` → P-1 and `docs/archive/sessions/session-ses_fd4e.md`). **Still open:** promoting a CI-runnable distillation into `runtime/test/gate-skill/` (the full run needs Render/Vercel auth, so the fixture should keep the no-login filebin→demoqa/the-internet segments plus a tab round-trip), the standalone single-domain 30+ step chain (Workflow 2 as written), Workflow 5, Workflow 6's cross-run consistency re-run, and the 20-file identity check.

- **Update (2026-09-02, the tab round-trip this item asks for was silently broken — now fixed):** replaying a second mega-workflow (session `9d19890f`, 108 steps / 6 tabs) reproduced the exact "new-tab/new-window landed-context verification" risk EXEC-5 #43 names in the abstract. Recorded shape: on `the-internet.herokuapp.com/windows`, click a `target="_blank"` link (step 28), the browser opens a new tab, then switch straight back to the original tab and keep working (step 30 onward) — the new tab is **never interacted with**. Three defects compounded. (1) **Recorder:** `_on_popup` runs inside Playwright's own event callback, one pump tick before `_register_new_pages_sync` names the new page, so `_tab_id_for_page(popup)` returned `None` and the popup event was written with the *opener's* tab (`tab_3`) and a dead `"tab_id": null` payload — even though `recorder_diag.json` correctly recorded `tab_4 {opened_by: "site", opener_tab: "tab_3"}` and a `recording-tab_4.webm`. `events.jsonl` contained zero `tab_4` events. (2) **Compiler:** `_insert_tab_markers` derives markers purely from a tab-id *transition* between consecutive events, so an opener-stamped popup produced no transition, no `tab_open`, no `tab_switch` back — the compiled pack never mentioned the second tab, with no compile warning. (3) **Runtime:** `tabs.js::_settleIfSwitched` skipped `_settle` (and therefore `bringToFront()`) whenever `prevPage === page`, so nothing reclaimed the foreground Chromium had handed to the popup; steps 30-107 drove the original tab from the **background** for the rest of the run — invisible under watch, and with rAF/timers throttled, which stalls Playwright's actionability `stable` wait. Fixed: the recorder defers the popup's tab-id lookup to the pump-loop drain via `_enqueue_synthetic(..., tab_key=id(popup))` so the event carries the popup's own tab (the compiler then emits `tab_open{tab_4, site, opener=tab_3}` + `tab_switch{tab_3}` on its own, no compiler change needed); and the runtime's tab registry sets `foregroundStale` from its existing `context.on("page")` listener, which suppresses the same-page early return for exactly one step so the target page is brought back to front (`forceFront`, independent of `watch`, emitting a `foreground_reclaimed` phase marker). Verified against the real 120-event stream, not just fixtures. New coverage: two `runtime/test/unit/test_tabs.js` cases (foreground reclaim with no page switch; reclaim in headless), two `conxa-cloud/tests/test_recorder_tab_identity.py` cases (deferred popup tab id; popup that closes before registration), two `conxa-cloud/tests/test_compile_tab_markers.py` cases. **Note:** the recorder half only affects **newly recorded** sessions — session `9d19890f`'s event stream permanently lost `tab_4` — but the runtime half heals already-compiled packs. **Still open:** the `runtime/test/gate-skill/` CI fixture exercising a tab round-trip, still not built.

- **Update (2026-09-02, H-1 was unwinnable by construction — fixture fixed, capability gap logged):** with the tab round-trip above fixed, the same mega-workflow got as far as **step 15** and failed with `destructive_recovery_halted`. Not a timeout: `ACTION_TIMEOUT_MS` is 2500 ms and `locators.js::withLocator` already re-resolves every 120 ms inside it (~20 attempts); the run ended at 2.8 s because `cascade.js` saw `step.destructive === true` and gave the step Layer 1 and nothing more. Root cause: `docs/testing/fixtures/mutator.html` labelled its drifting button **`Submit`**, so the compiled intent was `click_submit_button`, `action_semantics.py::commit_intent_hit` matched the default `commit_intent_substrings: ["submit","confirm"]`, and `classify_consequence` returned `irreversible`. H-1 therefore paired a name-drifting element with a commit-intent target — the very PROD-3 rule that protects real Submit buttons made the drift test permanently unpassable. **Fixed in the fixture, not the policy** (PROD-3 deliberately untouched): the button's label set is now `Go`/`Send`/`Do It`/`Proceed` and its id is `tgt-go`, chosen to clear both `commit_intent_substrings` and `submit_text_tokens`; all drift behaviour (id/class rewrite, random relabel, `appendChild` to end of body every 700 ms) is unchanged. `conxa-cloud/tests/test_fixture_intent_classification.py` now reads the label array straight out of the fixture and asserts against the **live** policy file, so both a fixture edit and a policy-vocabulary change fail loudly; it includes a guard-the-guard case proving the old `Submit` shape still classifies irreversible. Docs updated with the standing constraint (`01-WORKFLOWS-TO-TEST.md`, `03-ONE-WORKFLOW-RUNBOOK.md`), plus the stale `npx serve -l 3000` on the Phase 0 line corrected to the :8099 server every other fixture uses. **Still open:** segment A must be **re-recorded** — `destructive: true` is baked into the existing `execution.json`, so the fixture fix does nothing for pack `mega-workflow-fc8031f2`. That same re-record is already required for the popup `tab_4` fix above, so one recording validates both.

### EXEC-28 — No Tier 1/2 recovery stage can heal an element whose accessible name changed
- **Category:** Execution & Recovery
- **Description:** Every Tier 1/2 path keys off the **recorded** accessible name. The compiled identity bundle's signals embed it (`internal:role=button[name="Submit"]`, `internal:text="Submit"`), and the a11y stage rebuilds the same thing — `cascade.js::a11yRecoveryName()` returns `fingerprint.inner_text` for any `NAME_FROM_CONTENT_ROLES` element, so `recoverWithA11y` re-probes with the identical selector that just failed. Meanwhile `recovery.json` already carries per-step **anchors** (`{"text": "shapeshifter", "priority": 1}` — stable page landmarks written at compile time precisely because they survive UI drift) and `fingerprint.position_hint`, and **no Tier 1/2 stage reads either**: `grep` for `step.anchors` in `cascade.js`/`resolution.js`/`resolver.js`/`recovery.js` returns nothing. They are consumed only by the Tier 3/4 agent payload (`failure_response.js::stepRecoveryContext`). Net effect: a button that gets relabelled — the single most common real-world UI drift — is unhealable without spending LLM tokens, and in Build Studio Run Test (`CONXA_MAX_RECOVERY_TIER=2`, `runtime_tool.py:320`) it is unhealable at all.
- **Why required:** "self-healing recovery" is the core product claim, and renaming is the most ordinary form of drift there is. Today the deterministic tiers can heal a *moved* or *reclassed* element but not a *renamed* one, even though the compiler already wrote down exactly the drift-resistant signals needed to do it.
- **Business value:** every rename currently escalates to a paid LLM round-trip (or, in Studio, to a hard failure). Healing renames at Tier 2 is a direct cost reduction and a direct reliability gain on the most common drift class.
- **Technical value:** the data is already compiled and shipped in every pack — this is a consumer for `recovery.json` anchors and `position_hint`, not a new compile-time signal. Nothing needs re-compiling.
- **Careful:** this must NOT become the "find something close" guessing that was deliberately removed on 2026-09-02. An anchor/role/position stage has to resolve through the same pure matcher and uniqueness-margin gate as primary resolution (the pattern `recoverWithA11y` already follows with its synthetic bundle), and it must stay behind the PROD-3 destructive halt — a renamed *Delete* button is exactly the case that should still fail closed.
- **Dependencies:** none. Reproduces deterministically against `docs/testing/fixtures/mutator.html`.
- **Suggested order:** after the mega-workflow re-record confirms the rest of the chain is green.
- **Complexity:** M.
- **Success criteria:** the mutator fixture's relabelled button heals at Tier 2 with zero LLM tokens (`tier2_*` in `recovery.log`, not `Element not found`); a destructive renamed element still halts; a unit test covers both.


### EXEC-29a — Hover-reveal steps still fail at live replay despite recorder + handler support
- **Category:** Execution & Recovery
- **Description:** Found 2026-09-03 running the `03-ONE-WORKFLOW-RUNBOOK.md` mega-workflow (Segment D, `the-internet.herokuapp.com` `/hovers`). The hover-then-click step did not replay reliably — even though the recorder has captured hover reveals correctly since the 2026-08-26 fixes (see `FIX.md`) and the runtime has real handlers (`runtime/app/handlers.js`'s `hover`; `runtime/app/locators.js::walkHoverChain`; `cascade.js` Layer 2 re-hover). The specific failure mode wasn't captured with `events.jsonl`/`recovery.log` evidence this session — that's the immediate next step before root-causing. `research-analysis/05-reliability/architectures/hover.md` documents theoretical hover-then-click gaps (hover context lost on recovery, no re-hover-then-retry) as a starting point, though `walkHoverChain`/`cascade.js`'s Layer 2 already appear to implement a re-hover retry, so that doc may be stale relative to current code and worth re-checking against `git log` before trusting its gap list. (This item used to be filed jointly with the native-JS-dialog half of the same repro — see the resolved dialog entry below, split out 2026-09-04 once the dialog half's root cause turned out to be unrelated: a record-time event-ordering race plus a replay-time deadlock, neither of which touches hovering.)
- **Why required:** hover-revealed menus are an ordinary UI pattern, not an edge case — the-internet's `/hovers` is literally the reference example — so any real workflow touching one can't currently be trusted to replay clean.
- **Business value:** hover-revealed menu items are common in admin/back-office UIs, a core target segment.
- **Technical value:** closes a live-verified gap between "the plumbing exists" (recorder, compiler, runtime handlers) and "it actually works end to end."
- **Dependencies:** needs a fresh minimal repro (record + replay against `/hovers` alone, not buried in a 100-step take) with `events.jsonl`/`recovery.log` attached before root-causing.
- **Suggested order:** next mega-workflow re-run — reproduce in isolation first.
- **Complexity:** unknown until reproduced — S if it's a timing/pacing issue, M if it's the hover-chain logic itself.
- **Success criteria:** `the-internet.herokuapp.com`'s `/hovers` (hover avatar → click "View profile") replays clean at zero LLM tokens, both in isolation and inside the full mega-workflow take.

### EXEC-31 — WF-7's Tier 2 fixture variant no longer exercises the a11y recovery cascade
- **Category:** Execution & Recovery
- **Description:** Found running `docs/testing/04-COVERAGE-BUMP-RUNBOOK.md`'s R1 row (`?v=t2`) against skill `wf-7-dfe37300`, 2026-09-03. `resolver.js` walks every compiled `identity_bundle.signals` entry durability-ranked *before* `cascade.js`'s Tier 2 ever engages — the cascade only fires on a full resolver miss. The fixture's `t2` variant (`docs/testing/fixtures/recovery-fixture.html`) strips `data-testid`/`aria-label`/text but leaves `id="checkout-btn"` intact, and that id is already compiled in as identity signal #5 (`css-id`, durability 0.4275) in `execution.json`'s `identity_bundle.signals` — so the step resolves at Tier 0/primary and never reaches `cascade.js` at all. `recovery.log` showed zero recovery events for that row.
- **Why required:** WF-7's stated purpose is proving "the deterministic zero-token recovery tier (Tier A) actually heals real drift" — but under the current architecture, any drift that preserves even one compiled orthogonal signal is absorbed by primary resolution, not recovery. There is currently no fixture variant that exercises Tier 2 a11y recovery specifically: the only full-miss variants (`t3`/`gone`) skip straight past Tier 2 to the agent tier.
- **Business value:** same as the general "self-healing recovery" claim — if the Tier 2 a11y-only path has no fixture exercising it, a real regression there has no way to be caught before it reaches a customer.
- **Technical value:** closes a real gap between what the runbook claims to test and what it actually exercises.
- **Careful:** don't "fix" this by weakening the compiler's structural signal — the `css-id` survival is intentional resilience, working as designed. The fix is a *new* fixture variant that fails everything the compiler bakes in as a primary signal (including structural/css-id) while still leaving an a11y-derivable identity behind — not just relabeling the existing `t2` row.
- **Dependencies:** `docs/testing/fixtures/recovery-fixture.html`, `docs/testing/04-COVERAGE-BUMP-RUNBOOK.md`.
- **Suggested order:** whenever recovery-cascade coverage work is revisited; not blocking anything else.
- **Complexity:** S — one new fixture variant plus a runbook row update, once the right "Tier-2-only" drift shape is designed.
- **Success criteria:** a fixture variant produces a genuine `tier2_*` entry in `recovery.log` with zero agent tokens, distinct from a primary-resolution success.
- **Found via:** manual R0–R5 pass of `04-COVERAGE-BUMP-RUNBOOK.md`'s WF-7 recovery drill, 2026-09-03.

### EXEC-33 — `tracking.py`'s recovery-type dashboard mapping doesn't classify EXEC-30's overlay-dismissal events
- **Category:** Execution & Recovery
- **Description:** EXEC-30 added two new runtime telemetry codes, `overlay_dismissed` and `overlay_dismiss_rejected` (`docs/Backend-Schema.md` §4.1). `conxa-cloud/backend/app/services/tracking.py`'s `_event_recovery_type`/`_event_recovery_tier` (and the four-entry `_RECOVERY_TIERS` table it's built from) only recognize the pre-existing Selector/Text Anchor/Text Variant/Vision method codes — an overlay dismissal ingests and stores fine, but falls through `_event_recovery_type` returning `""` and never counts toward a step's `tier_counts` in the per-step recovery dashboard, so the dashboard undercounts recovery activity on any run that dismissed an overlay.
- **Why required:** deliberately deferred at EXEC-30 ship time rather than adding a fifth recovery *type* under time pressure — an overlay dismissal genuinely isn't an element-recovery *method* (it doesn't change which element the step targets), so it may deserve its own dashboard column rather than folding into the Selector/Text Anchor/Text Variant/Vision taxonomy. Needs a product call, not just a code change.
- **Dependencies:** `docs/TRD.md` §10.1 (EXEC-30 mechanism), `conxa-cloud/backend/app/services/tracking.py`.
- **Suggested order:** low priority — cosmetic dashboard gap, not a correctness issue (the runtime-side gating and learned-store behavior are unaffected).
- **Complexity:** S once the taxonomy question is settled.
- **Success criteria:** a run containing an `overlay_dismissed` event shows up in the per-step recovery dashboard, however it's ultimately classified.

### BUILD-22 — Templatizer rewrote a step's selector with an unrelated input placeholder
- **Category:** Builder
- **Description:** In the 2026-09-02 mega-workflow compile (session `9d19890f`), step 35 is `the-internet.herokuapp.com`'s **Login** submit button. The master skill records it correctly (`primary_selector: text="Login"`, `intent: click_login_button`), and the compiled `identity_bundle` still says `internal:text="Login"` — but the packaged `execution.json` step carries `selector: text="{{user_password}}"`, and `recovery.json` step 35 carries `intent: "click_user_password"`. The preceding step is a `type` into the password field, so the templatizer appears to have cross-contaminated the Login click with the value and intent of the step before it. At replay the top-level selector can never match; the step falls straight through to the recovery cascade on every run.
- **Why required:** a selector that interpolates a *secret* is worse than a broken one — the compiled skill now embeds the shape of a password into a text matcher, and any recovery payload built from that step's selector context carries it too. It is also silently wrong: nothing in the compile report flags a selector that no longer resembles its own identity bundle.
- **Business value:** this is a "compiled skill is quietly wrong" failure the customer only discovers at replay, on a login step — the single worst place to lose reliability.
- **Technical value:** a cheap invariant falls out of it — a compiled step's top-level `selector` should be derivable from its own `identity_bundle`, so a mismatch is a compile-time assertion, not a runtime surprise.
- **Dependencies:** none. Reproduces deterministically from session `9d19890f`. **Update 2026-09-02 (unconfirmed):** likely the same root cause as the `mega-workflow` `date_pick` desync just fixed (see the top of `FIX.md`, and `build.py::_insert_tab_markers`/`_make_tab_navigate_event`) — `_insert_user_tab_navigate_steps` spliced bare steps into `steps` without a matching event, which desynced every later index-based pass. This recording has multiple user-opened tabs before the Login step, matching that shift pattern. Not empirically re-verified against session `9d19890f` (a backup recording distinct from the session that fix was verified against) — re-run this session through the fixed compiler before closing this item.
- **Suggested order:** soon — it is a correctness bug in the primary compile path, and it touches credentials. Cheap first step now: recompile session `9d19890f` and check whether it's already fixed.
- **Complexity:** S to find (the templatizer's step-to-step value carry-over), S–M to fix plus the invariant check — likely $0 if the desync fix already covers it.
- **Success criteria:** recompiling session `9d19890f` yields step 35 with a selector consistent with its `identity_bundle` (`text="Login"`) and intent `click_login_button`; a compiler assertion (or compile warning) fires whenever a step's top-level selector interpolates an input the step's own identity bundle has no trace of.

</details>

### BUILD-22b — Reintroduce search-result-follows-input rebinding, gated correctly
- **Category:** Builder
- **Description:** BUILD-22's fix deleted the packager-time rewrite that bound a search-result
  click's selector to the immediately preceding typed input, because it fired unconditionally with
  no relatedness check. The underlying feature was real: a workflow that searches for something
  and clicks the top/matching result should still work when replayed with a different search term.
  Rebuild it at compile time (where the recorded literal value still exists, unlike in the
  packager), gated on the click's own recorded text actually equaling the typed literal.
- **Why required:** without this, "search then click the result" skills only work for the exact
  value recorded; every other input falls through to the recovery cascade.
- **Dependencies:** BUILD-22 (resolved 2026-09-04).
- **Complexity:** S–M.
- **Success criteria:** a search->click-result skill compiled with a real relatedness check
  rebinds the click selector to the input when (and only when) the click's own recorded text
  matches the typed value; an unrelated click (e.g. a Login button after a password field) is
  never touched.

### BUILD-24 — react-select `select_option` dropped silently while its input stays required
- **Partly done (2026-10-24):** the packager now removes a declared input that no kept step mentions and names it as a build warning (`skill_package_builder_saved_skill.py::_drop_unreferenced_inputs`). Still open: the react-select step itself is dropped, and the removal is a build warning, not an entry in `compile_report`.
- **Category:** Builder
- **Description:** Found while investigating BUILD-22 on the WF-2-S-F recording (demoqa.com
  `/select-menu`): the raw recording picks "Group 1, option 1" from the `react-select-2` widget
  (`select_option` on `#react-select-2-option-0-0`), and the compiler still declares
  `react_select_2_listbox` as a required input in `manifest.json` / `input.json` — but the
  compiled `execution.json` never contains a step that acts on it. The input exists with nothing
  to bind it to; the action it describes silently never runs.
- **Why required:** same family as BUILD-23 (a compile that drops steps with no warning naming
  them) — the customer sees a required input asking for a value that the compiled skill will
  never use.
- **Dependencies:** none. Reproduces from session `3651c833-918d-4e54-97bc-514a6356503d`
  (`WF-2-S-F`).
- **Complexity:** S–M to find which pass drops it; likely needs the same fix as BUILD-23's dropped
  steps.
- **Success criteria:** every `inputs_required` entry a compiled skill declares has at least one
  step in `execution.json` that consumes it, or the compile report names the input it dropped.

### BUILD-23 — A failed compile drops steps silently and reports no warning naming them
- **Category:** Builder
- **Description:** The same 2026-09-02 compile finished with `compile_status: "failed"`, `compile_min_confidence: 0.007`, and `compile_steps_with_warnings: 9` — yet it still produced and published a runnable 108-step pack from a `compile_report.steps_total` of **113**. Five steps were dropped with nothing naming which, and all nine warnings are `low_selector_confidence` on unrelated `demoqa` steps; none of them mention the dropped steps, the mangled step-35 selector (BUILD-22), or the lost popup tab (EXEC-10's 2026-09-02 update). A compile that reports `failed` should not silently yield an artifact the runtime happily executes.
- **Why required:** the compile report is the only place a Build Studio user can see that their recording did not survive compilation. Right now `failed` is indistinguishable from `succeeded` in every way that matters — the pack builds, publishes and runs — so the signal is dead.
- **Business value:** the customer records a workflow, sees it compile, ships it, and finds out at replay that five steps are missing. Surfacing this at compile time is the difference between a 2-minute re-record and a failed demo.
- **Technical value:** forces the compile report to account for every input event — emitted, merged, or dropped-with-a-reason — which is also the data needed to make `compile_status` mean something.
- **Dependencies:** none, though it pairs naturally with BUILD-22 (the invariant there is one of the warnings this item should surface). **Update 2026-09-02 (unconfirmed):** "five steps dropped" matches exactly the count this recording's user-opened tabs would have shifted by under the desync bug just fixed (see BUILD-22's update note) — `collapse_choice_group_runs`/`collapse_date_picker_runs` used to silently truncate `steps` to `min(len(steps), len(events))`, which is precisely a tail-drop of N steps where N is the extra step count the splice introduced. The desync fix also replaces that silent truncation with a hard failure, so even if some other cause remains, a future occurrence would now surface as a compile error instead of a silent drop. Re-verify against session `9d19890f` before closing.
- **Suggested order:** alongside BUILD-22 — same compile, same report. Re-check both together once BUILD-22 is re-verified.
- **Complexity:** M — likely much smaller if the desync fix already accounts for the dropped steps; the compile-report accounting behavior (naming what was dropped/merged) is still a separate piece of work regardless.
- **Success criteria:** every input event is accounted for in the compile report (emitted / merged into step N / dropped because X); `compile_status: "failed"` either blocks the publish path or surfaces an explicit, dismissable warning in Build Studio naming exactly what was lost.

---

## P1 — Blocking / Foundational (4 remaining)

### SHIP-1 — Build Studio's dependency manifest is hand-maintained and can silently disagree with what CI actually published

- **Symptom seen 2026-09-20:** an app layer published as `app-v3.2.1` never reached Build Studio. Studio checked on every launch, downloaded the zip, failed the checksum, discarded it, and stayed on `app-v3.1.12` — silently, forever.
- **Cause:** `GET /api/v1/updates/deps-manifest` (`conxa-cloud/backend/app/api/updates_routes.py:169`) — the only update source Build Studio reads — is composed from import-time env vars (`CONXA_APP_VERSION`, `CONXA_APP_BUNDLE_SHA256`, `CONXA_HOST_VERSION`, …) that a human sets on Render. CI never writes them; CI POSTs the real version + digest to `/admin/component-versions/conxa_app`, which feeds the KV store behind `GET /api/v1/manifest.json` and the two deprecated shims. Two sources of truth for one artifact, only one machine-written, and nothing checks that they agree. The version had been copied across; the digest was from an earlier build of the same tag (the workflow re-uploads with `--clobber`, and JS obfuscation is not byte-reproducible).
- **Fix (~20 lines, not done):** have `deps_manifest()` read `_component("conxa_app")` / `_component("conxa_runtime")` through the existing `_component()` / `_file_url()` / `_file_sha256()` helpers that `runtime_app_manifest()` (`updates_routes.py:371`) already uses, falling back to the env vars only when KV is empty. Then publishing a release updates Studio automatically, the same way it already updates the runtime self-updater.
- **Also:** `SHIP-GUIDE.md:356` reads "Nothing promotes automatically the way conxa-app/conxa-runtime do" — true for the runtime self-updater, false for Build Studio. Correct it in the same pass.
- **Partially mitigated 2026-09-20:** the Studio no longer hides the failure (`App.tsx` renders the dep error instead of dismissing the banner), so a future mismatch is at least visible. The drift itself is untouched.


### BUILD-15 — Recording-window flicker: move the mid-recording session-refresh save off the visible hot path
- **Category:** Builder
- **Description:** Recording any workflow that belongs to a Group makes the Chromium recording window visibly flicker while the user interacts with the page. Root cause confirmed by direct comparison against the last known-good commit (`b445dab`, where `handlers/session.py`'s group-recording branch set `storage_state_autosave = ""`) plus a controlled test (disabling the wiring eliminates the flicker): an in-progress, not-yet-committed feature — `_refresh_group_app_sessions` in `handlers/session.py`, which carries a rotated cookie or a mid-recording re-login back to each app's saved session after a group workflow recording ends — needs a live, up-to-date copy of the session while recording is happening, so it wired `storage_state_autosave_path` on for ordinary (non-auth) workflow recording. That turns on `_autosave_storage_state_sync`'s periodic `context.storage_state()` call (`conxa_compile/recorder/session.py`) every 6 seconds during recording — the same call whose own code comment already blames it for "visible window flicker during active login" in auth-mode recording, where it was always meant to be confined. Two earlier, wrong hypotheses (generic per-tick timer checks; Playwright's built-in video screencast) were investigated and ruled out first — both are documented, along with the full trace, in the plan file this investigation produced.
- **Currently patched, not fixed:** `storage_state_autosave` is force-disabled again in `handlers/session.py` (marked `PONYTAIL TEMP DEBUG (2026-08-17)`) so recording is flicker-free, but this also disables the rotated-cookie-refresh feature it was wired up for. Playwright's `record_video_dir`/`record_video_size` in `session.py` is also still temporarily disabled from the earlier (ruled-out) video hypothesis and needs re-enabling once the real fix lands. Both temp-debug blocks are clearly marked in the diff and must not ship as-is.
- **Why required:** blocks clean recording for any workflow that belongs to a Group — the primary/expected shape of most real customer workflows — and the temporary fix trades away a legitimate feature (session refresh) to get there.
- **Business value:** recording a clean browser session is the single core action of the entire product; a visibly broken/flickering recorder is a first-five-minutes trust problem, and it's currently live in the working tree, not yet shipped.
- **Technical value:** the same investigation also confirmed (but did not yet fix) two amplifying issues worth fixing alongside this: `_rewrite_events_jsonl` writes a full diagnostics dump (`_write_diagnostics_sync`, a `frame.evaluate()` per frame) on every single recorded event instead of once at session end, and `_capture_a11y_async` spawns a thread per event to call `page.accessibility.snapshot()`, an API removed in the installed Playwright version (1.58.0) — every call silently fails, wasting a thread per event for nothing. Neither is the flicker's root cause, but both are real, independently-confirmed cleanups on the same hot path.
- **What's left:** design and implement a version of the session-refresh save that doesn't block/interrupt the browser's compositor while recording — options include (a) saving only once, right before recording ends, instead of on a 6s timer (loses the "browser crashed mid-recording" safety net that timer also serves — see the other autosave comment in `session.py`); (b) moving the `storage_state()` read off the synchronous per-tick pump loop onto a separate thread/process (needs confirming Playwright's sync API tolerates a concurrent call from another thread — the existing "reentrancy deadlock" comment on `_binding_sink_sync` suggests it may not, without care); (c) triggering the save on a real signal (e.g. a cookie-set event) instead of a blind timer, so it only fires when something actually changed. A plan file with the full investigation, Phase 0 confirmation steps, and a first-draft implementation plan already exists at `.claude/plans/created-workflow-testing-download-staged-cray.md` (session-local, not yet committed anywhere durable) — read it before restarting this investigation from scratch.
- **Dependencies:** none blocking, but must land before (or alongside) the uncommitted `_refresh_group_app_sessions` feature is committed — do not ship that feature with the flicker-causing wiring left live.
- **Suggested order:** immediate — it's an active regression sitting in the working tree, not yet shipped to any customer.
- **Complexity:** M.
- **Success criteria:** recording a group workflow with 2+ tabs shows zero visible browser flicker while interacting, **and** a rotated cookie / mid-recording re-login is still correctly carried back to each app's saved session after recording ends — the fix must not just re-disable the feature to make the symptom go away.

</details>

### BUILD-21 — Human Edit step form should show/edit the workflow-plan prose intent, not the machine token
- **Category:** Builder
- **Description:** Since 2026-08-25 the single workflow-intent compile call produces both a snake_case `intent_token` (machine logic) and readable prose per step (`semantic_description` — same source as the Workflow plan panel). But `StepConfigForm.tsx` still shows/edits the machine token (`step.intent || step.final_intent`) and sends it back through a patch gate that enforces `sanitize_intent_token`, so the human-friendly sentence never appears where users edit steps.
- **Why required:** the plan panel and the step editor tell two different stories about the same step; users editing the token field must guess slug conventions instead of plain English.
- **Business value:** consistency between review and editing surfaces; editing text a human can actually read.
- **Technical value:** form defaults prefer `semantic_description`; patches send a new patchable `semantic_description` key (plain string, no token validation) instead of `intent`; patch-gate allowlists gain that key; editor DTO prefers the stored description over `describe_step`'s recomputed generic label; machine token shown as read-only mono hint under the field.
- **Dependencies:** none (compile-side single-source intent already shipped).
- **Suggested order:** immediate follow-up to the 2026-08-25 compiler change.
- **Complexity:** S.
- **Success criteria:** opening any compiled step in Human Edit shows the plan's sentence for it; saving edits persists that sentence and it survives reload + appears on re-fetch; the machine token stays untouched by prose edits.

</details>

### BUILD-19 (original) — `build-app-local.ps1` still reads app-layer files from the pre-split flat `runtime/` layout, so it fails on every file
- **Category:** Builder / Runtime (dev tooling)
- **Description:** Found while removing the dead `bootstrap.js` copy from the app-layer build lists (2026-08-22). Commit `8de4394` moved the app-layer modules into `runtime/app/` and the exe-only ones into `runtime/host/`, and updated `.github/workflows/build-runtime-app.yml` (`app/$f`, `host/bootstrap.js`) — but `scripts/build-app-local.ps1` still resolves every source as `$RuntimeDir\$f` (e.g. `runtime\server.js`), where none of those files exist anymore (the runtime root only holds `check_host_manifest.js`/`check_pkg_stubs.js`). Every obfuscator invocation in the script fails with ENOENT, so the local app-layer build has been broken since the split.
- **Why required:** the script is the documented way to test local app-layer edits through the real host exe path ("Run this after editing server.js, run.js, resolver.js…" — README's local-build table points to it); right now any local app change can only be tested by pushing an `app-v*` tag through CI.
- **Business value:** restores the fast local dev loop for runtime work; without it every runtime iteration costs a CI round-trip.
- **Technical value:** mechanical fix — mirror the workflow's mapping: `$src = "app/$f"` for all entries (bootstrap.js was removed from the list in 2026-08-22), plus re-syncing the file list against the workflow's current one (the script is also missing newer seam files such as `host_bridge.js`, `cli_installer.js`, `recovery_park.js`, `failure_response.js`, `tool_defs.js`, `run_config.js`, `recovery_log.js`, `interpolate.js` if those were added after its list froze).
- **Dependencies:** none.
- **Suggested order:** next time anyone needs a local app-layer build — until then CI remains the only working builder.
- **Complexity:** S.
- **Success criteria:** running `.\scripts\build-app-local.ps1` after editing e.g. `runtime/app/run.js` completes, stages the result under `<studio-home>\deps\conxa-app\<version>\`, and Test Skill replays it successfully.

</details>

### PROD-17 — Build Studio Terms & Conditions + Privacy Policy (anti-modification, anti-resale)
- **Status:** Drafting + wiring shipped 2026-08-29 — **still open pending founder/legal counsel sign-off on the drafted wording.** Founder decisions taken this session: licence unit is **per machine/device install**; the reverse-engineering ban carries a **statutory carve-out** ("except to the extent prohibited by applicable law"); licensees may **freely use, distribute, and sell the skill packages and installers they build** (subject to plan capability) but never Build Studio itself; acceptance is a **blocking first-run click-through**. Shipped: three new sections in the public Terms (`build-studio-license`, `build-studio-restrictions`, `ownership-and-outputs`) and a new Privacy section (`build-studio-local-data`) in `conxa-cloud/frontend/src/content/publicDocs.ts`; a blocking first-run gate in Build Studio (`conxa-builder/electron/renderer/src/pages/LegalGateScreen.tsx`, mounted in `App.tsx` ahead of the sign-in gate, acceptance recorded per `LEGAL_VERSION` in local storage); persistent links in the Studio Settings → About card and in the cloud dashboard sidebar. **Acceptance is now recorded server-side (same day):** the gate moved to *after* Clerk sign-in, and accepting writes an immutable, per-(user, version) evidence record to the cloud (`legal_acceptances` KV namespace — identity, workspace, document content hashes, server timestamp, client IP, Studio version, machine id) via four new Clerk-authenticated routes under `/api/v1/legal/` (`conxa-cloud/backend/app/api/legal_routes.py`, `app/services/legal.py`), with frozen document snapshots in `conxa-cloud/backend/app/legal/`. The Studio is server-authoritative and **fail-closed** — it blocks on a retry screen if the cloud is unreachable. Covered by `conxa-cloud/tests/test_legal_acceptance.py`. Not done: legal review of the wording, a dashboard screen for the acceptance export (the rows are reachable only via the admin endpoint and the Audit page), and the NSIS installer licence page (deliberately skipped — the in-app click-through is the assent record).
- **Category:** Product Strategy & Business-Risk Mitigation / Legal
- **Description:** Draft and publish Terms & Conditions and a Privacy Policy covering the Build Studio (Electron desktop app) distributed to SaaS company customers. The terms must explicitly prohibit modifying, reverse-engineering, or redistributing the Build Studio binary, and prohibit any commercial use outside the licensed relationship (e.g., white-labeling or reselling Build Studio itself, as opposed to the skill packages/installers a licensee is entitled to build and ship to their own end customers). Needs sign-off from a founder/legal counsel on the exact license grant boundaries before wording is finalized; this item is drafting + legal review + publishing (linking from the Build Studio installer/first-run screen and the cloud dashboard), not an engineering build.
- **Why required:** Build Studio is a Windows desktop binary that customers install locally — without an enforceable license agreement, there is currently no documented restriction stopping a licensee from modifying the app or using it commercially beyond the intended per-seat/per-workspace license.
- **Business value:** closes an open legal gap before wider commercial distribution; protects the company's IP in the compiler/recorder/identity-bundle logic that ships inside the Build Studio binary.
- **Technical value:** none directly — this is a legal/business document, though it may motivate future engineering work (e.g., license-key enforcement) tracked separately once the terms exist.
- **Dependencies:** `[DECISION]` — needs a founder/legal decision on license scope before drafting can finalize.
- **Suggested order:** alongside other P1 items, before any significant expansion of Build Studio's customer base.
- **Complexity:** S–M (drafting + review + adding links into the app and dashboard).
- **Success criteria:** a published Terms & Conditions and Privacy Policy exist, are linked from the Build Studio first-run/installer flow and the cloud dashboard, and explicitly bar unauthorized modification, reverse-engineering, and commercial resale of the Build Studio application itself.

### PROD-3-UI — Studio UI for entity-binding confirmation, Strict Mode, and the safety score
- **Category:** Product Strategy & Business-Risk Mitigation / Builder
- **Description:** The safety-core mechanisms shipped 2026-08-29 have no dedicated editor UI yet: confirming a detected entity binding, or setting a workflow's Strict Mode ceiling, both work only through the generic step-patch mechanism / a `CONXA_STRICT_MODE_MAX_TIER` build-time env var — not a purpose-built control. Also open: publishing a measured per-skill safety score ("0 wrong actions in 12,400 runs") and before/after screenshots on every consequential step (Answer 12's evidence layer). **Update (2026-09-12, PROD-3-DRYRUN):** same gap now also applies to `SkillMeta.compensation_skill` — the backend/runtime half (linking + offering it after a failure) is done, but linking one is patch-only, no editor control. **Update (2026-09-12, EXEC-38):** and to `for_each` loop bodies — tracked separately as `EXEC-38-UI` since it's a distinct, larger control (a `BranchBodyEditor.tsx` variant) rather than a form field, but the same underlying gap.
- **Why required:** the backend/runtime guarantee already exists and is tested; without an editor surface a vendor cannot practically use it at scale (confirming bindings one JSON patch at a time), and without a published score the safety claim stays a marketing line rather than "measured and published."
- **Business value:** makes the safety mechanism actually usable day-to-day, and the published score is described as a sales weapon in its own right ("measured safety is a sales weapon; claimed safety is just marketing").
- **Technical value:** the data already exists on `StepEditorDTO.entity_binding` and per-skill telemetry (`rec_halt`, the new `entity_binding_not_found` recovery-log event) — this is UI + aggregation, not new core logic. `conxa-cloud/backend/app/services/tracking_analytics.py`'s `health_score` (line ~294) is the natural home for the score, alongside its existing weighted-factor pattern.
- **Dependencies:** PROD-3 safety core (done).
- **Suggested order:** alongside or shortly after PROD-3-DRYRUN — this is what makes the shipped mechanism sellable rather than just correct.
- **Complexity:** M — an Entity Binding confirmation card in `StepConfigForm.tsx`, a Strict Mode selector in `PublishPage.tsx`, a safety-score aggregation + dashboard card, and screenshot capture on consequential steps (the runtime already has JPEG pre-step capture machinery — `maybeCapturePreStep` — to extend).
- **Success criteria:** a vendor can confirm an entity binding and set Strict Mode from the Human Edit / Publish pages without hand-crafting a patch; a per-skill safety score is computed and shown on the dashboard; before/after screenshots are captured and viewable for a consequential step's run.

---

### EXEC-21 — Human review points: record where a person is genuinely needed, and pause there
- **Status (2026-09-15):** the **hand-over** shape shipped — a first-class `handover` step type
  (`runtime/app/handover.js`, `run.js`'s pre-dispatch interception, `server.js`'s park/resume,
  authoring via `action_registry.py`/`patch_gate.py`/`skill_package_builder_saved_skill.py`, see
  `docs/TRD.md` §11.4 and `docs/Backend-Schema.md` §3.4k for the full mechanism). Reused EXEC-13's
  park-and-resume primitive as specified, with the two differences this entry called out in
  advance: the host lock is released (not held) for the pause — `PARK_TTL_MS` stayed the wrong
  lifetime for a person, so hand-over gets its own person-scale `HANDOVER_PARK_TTL_MS` (30 min
  default) instead — and the resume is now driven by three signal sources with no EXEC-13
  equivalent (an in-page banner via `context.exposeBinding`, a file drop mirroring the scheduler
  daemon's own command pattern plus a `conxa-runtime.exe resume` CLI subcommand, and a token-gated
  loopback HTTP listener — the runtime's first-ever inbound network surface, armed only while a
  hand-over is pending). The resume is self-driven: the long-lived runtime process calls its own
  `execute_skill` handler the instant any signal fires, with no agent polling required. **Deferred,
  not built:** approve/reject and supply-a-judgement, the other two shapes — see the unchanged
  description below for what each still needs. **Known gap carried forward:** a `handover` step
  authored inside a `for_each` loop body has no compile-time guard yet (unwinding the loop on the
  pause loses the iteration cursor — the runtime propagates rather than silently corrupting, but
  nothing stops the authoring). 725 new/updated runtime tests, 13 new cloud pytest cases, all green.
- **Category:** Execution & Recovery / Builder / Product Strategy
- **Description:** A recorded, compiled, first-class step type marking a point in a workflow where a **human** — not a model — has to be involved, plus the runtime behaviour that pauses there and resumes afterwards. Three shapes, all authored rather than discovered at run time: **approve/reject** (run pauses, a person confirms, run continues or aborts); **supply a judgement** (run pauses, a person provides a value later steps consume — which category, which record, is this a duplicate); and **hand over** (a person takes over the live browser, does the part only they can do, and hands control back). The review is presented **in the Chromium instance the runtime is already driving on the customer's own machine** — the person sees the real page in the state the run left it in and answers there. No review UI in the cloud, no run state leaving the machine.
- **Why required:** `docs/PRD.md` §14.1 makes this current Horizon 1 scope, and §8 (*Human review points*) describes it as product capability — flagged there as being built, not shipped. It also reverses a previous product position: the Workflow Qualification Checklist used to treat a mid-flow human decision as a shape problem to be engineered around by splitting the workflow in two. That checklist has been revised, so the capability now has to exist to match what the PRD promises.
- **Business value:** opens the large class of real business processes that are automatable *except* for one approval or one judgement call — today those either get rejected during scoping or get split into two half-workflows with a manual gap between them that nobody owns. It is also the Horizon 2 prerequisite: a scalable human-review queue can only route review points that Horizon 1 recorded in the first place.
- **Technical value — build it as the human sibling of EXEC-13, not as a second mechanism:** EXEC-13 (AI Review Step) specifies exactly this pause → ask → consume-structured-answer → resume loop with Claude as the answerer, and documents in detail that the machinery already exists (the parked page in `runtime/server.js`, `PARK_TTL_MS`, the park-divergence fingerprint check, `resume_from` + `step_overrides` in `execute_skill`, and Human Edit's recorded-literal → named-runtime-input plumbing). A human review step is the same shape with a person as the answerer instead of a model. **Read EXEC-13 in full before scoping this** — its failure-handling section (timeout, invalid output, refusal, page drift), its bounded-retry requirement, its interaction with EXEC-12's retry budget, and its `on_failure` policy (`abort` default / `use_default` / `continue`) all apply here essentially unchanged. Two things differ and need their own design: the answer arrives from a person interacting with the live page rather than from a structured model response, so the "wait" is unbounded in a way a model call is not (`PARK_TTL_MS` as it stands is the wrong lifetime for a person who has gone to lunch); and the hand-over shape has no EXEC-13 equivalent at all, since it means deliberately yielding control of a page the runtime is mid-way through driving and then re-establishing that the page is still where the workflow expects it.
- **Interaction with EXEC-8 / PROD-3:** EXEC-8 already specifies a first-class "pause and hand to a human" state, triggered by rule or by recovery exhaustion, and defers to PROD-3 as the primary tracked item. That is the *unplanned* human handoff — something went wrong. This item is the *planned* one — the recording said a person belongs here. They should share one park/hand-off mechanism and one audit trail, and must stay distinguishable in telemetry, because "a person was needed" and "a person was needed because we failed" are different numbers and `docs/PRD.md` §12 now measures both.
- **Dependencies:** sequence after EXEC-12 (retry budget) for the same reason EXEC-13 does. Design alongside EXEC-13 and PROD-3/EXEC-8 rather than after them. Durable resumable state (EXEC-22) is not blocking for the attended case — a person at the machine can answer inside the park lifetime — but is required before a review can outlive the browser session.
- **Suggested order:** immediate. The PRD now describes it as current scope, which makes the gap between doc and product a live accuracy problem, not just a backlog item.
- **Complexity:** L — approve/reject is the small half and reuses EXEC-13's machinery almost wholesale; judgement input needs the typed input contract and schema validation; hand-over is the genuinely new part and carries the real risk (yielding and reclaiming a live page mid-run).
- **Success criteria:** a workflow authored with each of the three review shapes compiles, publishes, and replays on a customer machine; at each review point the run pauses and the person is prompted in the browser the runtime is driving, with the page in the state the run left it; a judgement value supplied by the person is bound as a named input and demonstrably changes a later step's behaviour; a rejected approval aborts cleanly with an audit record; a hand-over returns control to the run with the page re-validated before the next step acts; a review that is never answered times out per its configured `on_failure` policy and leaks no browser; the workflow remains testable in the Build Studio sandbox; and telemetry distinguishes a planned review from a recovery-triggered handoff.

## P2 — High Value, Do Soon (37 remaining)

### PROD-1 — Per-tenant reliability: first-run calibration + persistent repair memory
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** Two combined mechanisms for the "a skill recorded on one account may not work on another customer's account" risk. (1) **First-run calibration:** before a skill's first real run on a new customer's account, quietly walk through it in a safe, no-changes way — confirm each target actually exists, learn local-language labels, take fresh screenshots, and flag likely plan/permission differences *before* the customer hits a failure. (2) **Persistent repair memory:** after a validated repair, remember it in a local, per-account adjustment layer (the signed pack itself is never modified) instead of re-paying the same LLM repair cost on every subsequent run.
- **Why required:** a skill compiled against the vendor's own demo account routinely meets different labels, permissions, and available screens at each customer — described as "genuinely works but needs the real-world proof" in the source doc, and flagged there as the #1 priority test in the whole business-risk review.
- **Business value:** first-run success rate is described in the source doc as "the number the thesis lives on" — this is what makes that number controllable rather than a roll of the dice per customer.
- **Technical value:** the repair-memory half directly attacks recovery cost: the source doc estimates a workflow with 2 weak steps drops from ~7,200 tokens *every run* to ~1,200 after the first repair, meaningfully increasing how many runs fit in a customer's Claude usage session.
- **Dependencies:** benefits from EXEC-1 (conditional steps, for handling permission-gated UI differences) and the resolver/scoring machinery already shipped (`resolver.js`, `selector_score.py`), which the source doc notes "already exists" for most of this.
- **Suggested order:** high priority — the source doc calls the real-world cross-account test "the #1 priority in the main report."
- **Complexity:** M — mostly wiring existing scoring/resolution machinery into a new pre-flight pass and a small local overlay store, not new core algorithms.
- **Success criteria:** a skill's first run on a new customer's account either succeeds cleanly or surfaces a specific, actionable pre-flight warning (not a mid-run failure); by the Nth run on that account, repair costs measurably drop toward zero.

### PROD-6 — Domain ownership verification at publish
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** Vendors can only publish skills for domains they've proven they own (a DNS TXT record or Search-Console-style verification), plus a signed "automation lane" identifier sent with every request so a vendor's own security team can recognize, rate-limit, and audit Conxa's traffic as their own labeled automation channel rather than unidentified bot traffic. **Design note (2026-07-07):** design the verification model with *delegation* in mind — one day the verifying entity won't always be the vendor itself (a platform vouching for its marketplace ISVs' domains, or an enterprise IT org vouching for a batch of internal domains — see PROD-13). Don't hard-wire a one-vendor-one-DNS-record assumption so deep that the delegated form needs a rework; the v1 flow can still ship as simple DNS verification. A second verification *mode* (proving admin control of a SaaS tenant rather than DNS control of a domain) is a separate, explicitly-gated founder decision — see PROD-16; do not implement it as part of this item. **Second consumer (2026-08-09):** paid-plan installer filenames now use a workspace-supplied "installer domain" (`entitlements.get_installer_domain`/`set_installer_domain`, unverified plain text — see `docs/Backend-Schema.md`) purely for naming, with no ownership check. Once this item ships, gate that field's write path on the same DNS-verification proof so a workspace can't claim a domain-based installer name for a domain it doesn't control. **Capture point moved (2026-08-22):** this same field is now also collected at signup (`/onboarding/company-domain`, right after workspace creation) rather than only being discoverable later in installer/settings flows — still the same unverified plain-text field, so this item's scope (gating the write path on real proof) is unchanged, just now applies to one more call site.
- **Why required:** addresses the "automating other companies' sites can violate ToS and trigger bot-blockers" risk. The source doc explicitly recommends shipping this *early*, before vendor sign-ups scale, since it's also what makes the "we only automate what the vendor owns" claim enforceable rather than just a policy statement — and it protects Conxa's own platform from being used against sites nobody in the transaction owns.
- **Business value:** removes the bot-blocker cat-and-mouse problem almost entirely by construction, and is a defensible answer to any legal/ToS question a prospect raises.
- **Technical value:** small, self-contained addition to the publish flow — a DNS-verification step plus a signed request header.
- **Dependencies:** none.
- **Suggested order:** early — small effort, and the source doc flags it as something that should gate publishing "before vendor sign-ups scale," implying urgency independent of its small size.
- **Complexity:** S.
- **Success criteria:** publishing a skill for a domain requires proof of ownership; every runtime request carries a signed automation-identification header a vendor's security team can recognize.

### PROD-11 — Skill health dashboard + fast re-record + "skill CI"
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** A live per-skill health score with drift alerts and a fast diff-based re-record/republish flow (target: under 15 vendor-minutes per republish), plus "skill CI" — since the vendor owns the target app, let them add a `conxa test` step to their own release pipeline that dry-runs all published skills against their staging environment on every deploy, so a redesign that would break a skill fails the *vendor's* build before it ever reaches customers.
- **Why required:** answers "teach once, run forever quietly becomes re-record after every redesign" — the maintenance cost that "less hassle than a connector" depends on actually being small. Skill CI specifically is described as something no traditional automation vendor can offer, since their customers don't own the target app and Conxa's do — it's called out as possibly Conxa's single most differentiated feature, "falling out of the vendor-automates-their-own-product model almost for free."
- **Business value:** makes the "maintenance is genuinely small" claim provably true rather than asserted, which is central to the whole "cheaper than building/maintaining a connector" pitch; skill CI in particular could make most redesign-breaks invisible to customers entirely.
- **Technical value:** the health-dashboard half overlaps with the already-shipped drift-detection queue (`GET /api/v1/tracking/{company}/drift`, Implementation-Plan §2.2) and with EXEC-2's fleet-flywheel work — this item is the vendor-facing UX and CI-integration layer on top of that existing detection mechanism, not a duplicate of it. Also overlaps `docs/UI-UX-Brief.md`'s existing "fast re-record" UI backlog item (see BUILD-2).
- **Dependencies:** builds on the existing drift-detection queue; benefits from EXEC-2's automation work for the "fast re-record" half to eventually become semi-automatic.
- **Suggested order:** high value — sequence alongside EXEC-2, since both work from the same underlying drift-detection signal.
- **Complexity:** L — the dashboard/re-record UX is moderate; the "skill CI" integration (a CLI tool the vendor's own CI calls) is a new, self-contained deliverable.
- **Success criteria:** a vendor can see per-skill health and drift status in the dashboard; a `conxa test` CLI step exists that a vendor can wire into their own build pipeline and that fails the build when a staging-environment change would break a published skill.
- **Partial progress (2026-08-07):** the *health-dashboard half* of the first success criterion is done — the operations-dashboard redesign ships per-skill rollups with success rate, period-over-period delta, p50/p95 duration and a per-version breakdown (`/dashboard/workflows`), a step-level drill-down, and drift status on `/dashboard/healing`. Still open: the fast diff-based re-record/republish flow and the `conxa test` skill-CI integration, which are the differentiated parts of this item.

### AUTH-12 — Built-in app catalog with one-click "Connect" (known-good sign-in config for popular apps)
- **Category:** Execution & Recovery / Authentication (spans Build Studio, Cloud, Runtime)
- **Description:** Ship a Conxa-maintained catalog of popular SaaS apps (GitHub, Google, Render, Vercel, Slack, Notion, HubSpot, Stripe, …). Each entry carries verified sign-in facts: display name + logo, `login_url`, an optional `success_url` hint, sign-in-provider quirks (e.g. "redirects `www.` → bare host", "SSO via accounts.google.com"), and — the important part — an **authenticated marker** that is known to be true only when signed in (a URL pattern, a selector, or an account-menu probe like AUTH-8's `accountNameProbe`). The customer sees "Connect GitHub" once, signs in once, and is done; the company no longer hand-types `login_url`/`success_url` per app in the Group Auth Wizard, and the runtime no longer has to *guess* whether sign-in finished for catalog apps.
- **Why this is browser-based, not OAuth:** Conxa skills replay clicks in a real Chromium, so they need a signed-in *browser session* (cookies/localStorage), not an API token. An OAuth "Connect" like Claude's connectors would give a token the browser can't use. True API connectors are a different execution engine (see "Out of scope" below).
- **Why required:** sign-in is the most fragile step in the customer flow. The generic detection ladder (`login_signals.js`, `browser.js::_signedOutBaseline`, AUTH-5/6/7/8/9) exists because each app's sign-in shape was unknown; GitHub's `www.` redirect broke it twice. For known apps we can replace guesswork with facts, and for unknown apps the generic ladder stays as the fallback. It also removes the per-app setup a company does before recording.
- **Proposed design (to confirm before building):**
  1. **Catalog data + schema.** New `CatalogApp` model in `packages/conxa-core/conxa_core/models/` (`slug`, `name`, `logo`, `login_url`, `success_url?`, `signed_in_check`, `notes`, `catalog_version`). Stored as a versioned JSON file owned by Conxa (start bundled in the repo; later served from Cloud so entries can be fixed without shipping a new Studio/runtime).
  2. **Cloud endpoint.** `GET /api/v1/catalog/apps` (list, ETag/version) under the existing `/api/v1` prefix; documented in `docs/Backend-Schema.md` and `docs/TRD.md`. Read-only, no per-tenant data.
  3. **Build Studio.** In the Group Auth Wizard, "Add app" offers the catalog first ("Pick from popular apps"), with "Custom app" as the existing manual path. Picking an entry pre-fills `GroupApp` (name, `login_url`, `success_url`) and stores a `catalog_slug` on it. Update `docs/UI-UX-Brief.md`.
  4. **Pack contract.** `pack.json.groups[].apps[]` gains optional `catalog_slug` + a frozen copy of the entry's `signed_in_check`, so the runtime works offline and is not affected by a later catalog edit until the next publish.
  5. **Runtime.** In `login_signals.js` / `browser.js`, when an app has a `signed_in_check`, use it as the primary "signed in?" answer for pre-flight validation, the prover, and login-completion. No `signed_in_check` (custom app) → the current baseline-compare ladder, unchanged. The catalog check must never *replace* the prover — it is one more signal, and the human "I'm done" (AUTH-6) still overrides.
  6. **Customer-side "Connect" UX.** A single "Connect apps" panel in the runtime/Execute showing each required app as a card with Connect / Connected ✓ / Reconnect, opening one sign-in tab per tap (reuses the existing per-app sign-in-tab machinery, `authenticate` tool, and Execute's per-tab Done button). No new auth primitive.
  7. **Catalog maintenance.** A small CI check that loads each entry's `login_url` in a signed-out context and asserts it still looks like a login page and the `signed_in_check` is false (a cheap "catalog CI"), so a site redesign is caught by us, not by a customer. Start with 5–8 apps and grow from real demand.
- **Invariants to respect:** auth files/storageState still never enter build output; sessions stay local and encrypted (AES-256-GCM, per-app keys); the catalog holds only public, non-secret facts; no LLM in the sign-in decision path; all routes under `/api/v1`; cloud still does not execute or record.
- **Open questions:** (a) is the catalog bundled or Cloud-served in v1? (recommend bundled first, Cloud-served once it changes more than monthly); (b) what shape is `signed_in_check` — URL pattern only, or also selector/probe? (recommend "URL pattern or account-probe", never a bare host match, given AUTH-9's lessons); (c) does a catalog entry ever replace a company's own `login_url` override? (recommend: company override wins, catalog is a default).
- **Out of scope (separate items if wanted):** (1) True OAuth/API connectors that call an app's official API instead of replaying browser steps — a second execution engine with its own recovery/telemetry/billing model, would need its own `[DECISION]` item and design. (2) Importing the customer's existing Chrome/Edge sessions — Windows encrypts those cookies (app-bound encryption) and it raises privacy concerns; at most a time-boxed feasibility spike, not a build item.
- **Dependencies:** builds on AUTH-1…AUTH-9 (all in `Done.md`); relates to AUTH-10 (a catalog `signed_in_check` is the natural fix for its "public success page" edge case for catalog apps) and the `login-desk.html` design artifact (AUTH-11, done — in `Done.md`; re-check it if this lands); no blocker.
- **Suggested order:** first slice = schema + 5 hand-verified apps + Studio picker + runtime use of `signed_in_check`, bundled (no Cloud endpoint yet). Then Cloud-served catalog + catalog CI, then the customer "Connect apps" panel polish.
- **Complexity:** L overall (S for schema + picker, M for runtime `signed_in_check`, M for Cloud endpoint + catalog CI, M for the customer Connect panel).
- **Success criteria:** a company adds GitHub to a group by picking it from the catalog (no URLs typed); a customer connects it with one tap and one sign-in; a valid GitHub session passes pre-flight without a false "expired"; a catalog app whose sign-in page changes is caught by catalog CI before a customer hits it; custom (non-catalog) apps behave exactly as today.

### ~~PROD-18~~ — Governance *enforcement* layer: tamper-resistant audit + run-time policy gates — **Pieces 1 & 3 done, piece 2's windows/deny-lists done, require-approval deferred — 2026-08-29**
- **Resolution:** Piece 1 shipped: every telemetry batch now carries an HMAC-linked `seq`/`prev`/`h` chain (`runtime/app/tracker.js::_buildEnvelope`, keyed on the tracking token, verified server-side on the RAW pre-truncation events by `app.services.tracking._verify_chain_link`/`_chain_state`); `wf_start` is flushed synchronously (bounded 2s) before any browser work (`server.js`'s `_receiptFlush`, awaited at the seam every surviving path crosses); a failed POST spills to `{CONXA_DATA_DIR}/logs/telemetry-spill.jsonl` (drop-newest, opposite of the in-memory ring) and drains at next startup (`tracker.js::drainSpill`); `_run_summary` carries a `chain` verdict; a new `GET /api/v1/tracking/{workspace_id}/reconcile` route (gated `ops_tier=full`, reusing `_visible_run_records`, no added scan) reports started/completed/abandoned/in-flight counts plus chain gap/broken runs — this is what makes a run whose local logs were deleted still leave a verifiable, gap-detectable server-side trace (criterion 1, met). Piece 2's windows + deny-lists shipped as a signed governance document (`GET`/`PUT /api/v1/tracking/{workspace_id}/policy`, reusing `manifest_signer`'s existing Ed25519 keypair — zero new crypto) fetched, verified, and cached by a new `runtime/app/policy_gate.js` (pure `evaluate()` + I/O `loadPolicy()`, mirroring the `resolver.js`/`resolve_adapter.js` split), gated in `server.js` before host-lock acquisition and any browser work; ships `enforce:false` (audit-only) by default per the recommended rollout in `research-analysis/04-architecture/subsystems/enterprise.md` §1. A policy whose own `expires_at` has passed and can't be refreshed refuses every skill in that workspace — the one bypass genuinely closed by local enforcement alone, since a customer can't forge a longer `expires_at` without the private key (criterion 3, met — see the "outside_window"/"denied_host"/"policy_expired" refusal messages). Piece 3 shipped as `docs/Audit-and-Control.md` (criterion 4, met). **Deferred, not built:** require-approval-before-step (criterion 2) — it needs a human answerer at the pause, which only EXEC-21 supplies, and EXEC-21 remains unshipped (only EXEC-13's `ai_review`, whose answerer is the MCP agent itself — self-approval, not evidence an auditor accepts); `require_receipt` (the schema field exists on the signed policy document but the runtime does not yet enforce it); a policy-authoring UI (API + docs only); signing-key rotation (tracked as new gap SG-19 in `docs/Security.md` — one Ed25519 keypair now covers both the update manifest and the governance policy, no key-id-based rotation). 41 new tests: 4 runtime (`test_evidence_chain.js`), 19 runtime (`test_policy_gate.js`), 18 cloud pytest (`test_tracking_service.py`/`test_tracking_analytics.py`/`test_tracking_routes.py` additions) — full runtime suite (496) and cloud suite (1029) both green.
- **Category:** Product Strategy & Business-Risk Mitigation / Cloud / Runtime
- **Description:** Conxa already has strong audit *visibility* — per-run telemetry with `run_id` bound into every event (`runtime/app/tracker.js`), the append-only recovery log (`recovery_log.js`), per-step assertion audits (`assertions.js`), build-side request-scoped build logs, and RBAC/entitlements on the cloud. What it lacks is enforcement and evidentiary integrity, which is what enterprise procurement actually gates on. Three pieces:
  1. **Tamper-resistant audit trail.** Recovery logs and session data live on the customer machine where the executor itself can delete them; cloud telemetry is batched and fire-and-forget (a run can finish with its evidence never arriving). Make run evidence durable and verifiable: per-run evidence receipt (hash chain or signed summary posted at run start AND end so a gap is detectable), server-side reconciliation of runs-started vs. results-received, and a documented retention policy.
  2. **Pre-action policy gates (not post-hoc recording).** Compile-time step attributes (danger class already sketched in PROD-3 layer 1) that the runtime must honor before acting: require-approval-before-step (rides EXEC-21's park/resume mechanism rather than adding a second one), time/policy windows ("never execute payroll skills outside 9–5"), and platform deny-lists enforced by `host_lock.js`'s existing host keying. The runtime refuses to start (or pauses) when policy says so — today nothing between the audit trail and the click can stop it.
  3. **Compliance posture packaging.** A short "audit & control" doc mapping the above to what SOC2/finance/HR buyers ask: who ran what, which skill version, what evidence exists, who can delete it, what happens on an unanswered approval. Most of this is documenting shipped machinery honestly (including its limits), not building.
- **Why required:** surfaced while assessing what still blocks enterprise sales even if all execution-reliability tests pass: auditors ask "can the executor delete its own evidence?" (currently yes) and "what stops a run mid-flight?" (currently nothing). Visibility ≠ control; this item is the control half. Deliberately scoped to NOT duplicate PROD-3's compensation/cleanup workflows or EXEC-21's human review points — those provide the approval UI and the undo; this item provides the policy that *forces* them to be used and the evidence that they were.
- **Business value:** converts "we have audit of every run and build" from an engineering claim into a procurement-passing answer; directly unblocks the finance/HR/payroll verticals PROD-3 targets, since those buyers gate on evidence integrity and pre-action control before any wrong-row risk matters.
- **Technical value:** mostly composition of existing primitives — `run_id` is already bound into every telemetry event; EXEC-21 supplies pause/resume; PROD-3 supplies danger labeling; `host_lock.js` already keys hosts. The genuinely new engineering is the evidence receipt/hash-chain and the server-side reconciliation query.
- **Dependencies:** design alongside EXEC-21 (approval gates share one park/resume mechanism) and PROD-3 layer 1 (danger classes feed policy rules); the tracking-ingest route inconsistency noted in TODO.md's Key Invariants discussion should be resolved first so evidence posting has one stable endpoint.
- **Suggested order:** P2 — sequence after EXEC-21 lands its approve/reject shape, but write the compliance-posture doc early since it is cheap and sales-facing now.
- **Complexity:** M–L — piece 1 (evidence receipts + reconciliation) and piece 2 (policy attributes + runtime checks) are each moderate; piece 3 is S.
- **Success criteria:** a run executed with its local logs deleted afterwards still leaves a verifiable, gap-detectable trace server-side; a skill step marked require-approval demonstrably cannot execute without the approval event; a policy window prevents out-of-hours execution with a clear refusal message; the audit-and-control doc exists and matches shipped behavior.

### PROD-19 — Conxa as a standalone execution platform: own agent harness (OpenCode) + hosted model (Kimi K3) + in-app credits
- **Category:** Product Strategy
- **See also:** [`docs/Execution-Platform-Session.md`](docs/Execution-Platform-Session.md); **v0.1 (2026-09-04):** `conxa-execute/` is form + a **vendored OpenCode loop** (pinned commit in `conxa-execute/NOTICE`), not a binary-only pin of their desktop. Hosted Kimi, credits, review cards still open. **2026-09-05:** `conxa-execute/backend/` shipped a first cut of steps 5 and 7 — see their notes below and `docs/TRD.md` §3.6. **2026-09-05 (later same day):** Execute Key auth replaced with real Clerk accounts, top-up/subscription/BYOK made three explicit modes, subscriptions became real quota-reset plans (not recurring top-up), the proxy got a standalone LLM config with model-fallback, and chat sessions now persist — locally via a ported opencode storage/compaction service (`conxa-execute/app/vendor/opencode/{storage,session}/`, `NOTICE`) plus backend sync for signed-in modes (`app/routes_sessions.py`). See step notes below.
- **Description:** Today a customer cannot execute a skill without also owning an MCP client — in practice a paid Claude or Codex subscription. That makes a third-party subscription a hard prerequisite for our product, caps the addressable market at people who already pay someone else, and hands the most valuable part of the loop to a competitor. Direction agreed 2026-09-01: build Conxa into a self-contained execution platform. Four parts: (1) an agent harness of our own, using the open-source **OpenCode** rather than writing a loop from scratch; (2) a **hosted model tier** served through the existing cloud LLM proxy, starting with **Kimi K3** and later fine-tuned on our own recovery data; (3) **bring-your-own-API-key**, so customers who already pay Anthropic/OpenAI/anyone can point the harness at their own key and spend nothing with us on tokens; (4) **in-app credit purchase with QR-code payment**, so a customer can top up from inside the app without leaving for a billing portal.
- **Why required:** removes the single largest adoption blocker (needing someone else's paid product first), and moves us from "a skill format that runs inside Claude Desktop" to "a platform that executes."
- **Business value:** opens the entire market of companies with no AI subscription; makes execution itself billable instead of free-riding on the customer's Claude plan; BYO-key keeps the price-sensitive and the enterprise-procurement segments on the same product; QR top-up removes the checkout drop-off that a redirect to a billing page always costs.
- **Technical value:** most of the plumbing exists. `conxa-cloud/backend/app/llm/router.py` is already an OpenAI-compatible multi-provider pool with per-provider `text_model`/`vision_model` read from env, so adding Kimi is configuration, not code; `services/llm_metering.py` already meters; Cashfree is already wired for payment. What is genuinely new is the harness, the credit ledger, and the model-choice plumbing.
- **The constraint that decides the design:** the runtime **has no LLM client of its own** and never calls a model directly. Tier B recovery is performed by *the calling agent's model*, which reasons about the failure and hands back `_agent_override` for the runtime to apply (`runtime/app/server.js:963`, `sc: "agent_override"`). So whichever model sits behind our harness is not a chat convenience — it becomes the self-healing brain that today is Claude Opus running free on the customer's own subscription. A weaker model there makes skills measurably less durable, which is the entire product promise. Model choice is a reliability decision, not a cost decision.
- **What's left:**
  1. ~~**Direct-run front door (do first, needs no model).**~~ **Started 2026-09-04** in Conxa Execute (pick skill, fill declared inputs, run over MCP). ~~Needs an installer~~ **Shipped 2026-09-17**: `conxa-execute/app/electron-builder.yml` + `.github/workflows/build-execute.yml` produce a per-user NSIS installer on an `execute-v*` tag, published to a GitHub Release, with a silent (non-blocking) auto-updater mirroring the Studio's feed topology minus its mandatory-update UI — see `docs/Auth-and-Updater.md` §2.1a. Still needs general polish; not a localhost page on the runtime.
  2. **Recovery-data capture (do second, cheap, compounds).** The supervised pair we would need to fine-tune on — broken page state → the correction that worked → whether the retry passed — exists in memory today and is discarded. `runtime/app/server.js:963` emits only `si`/`l`/`sc` short codes; the winning `_agent_override` never leaves the machine, and `capturePageFingerprint`'s parked DOM state is dropped after use. Emitting the full pair is a few lines in a path that already runs, and every month it is not on is a month of training data lost. **Requires explicit opt-in and a retention policy first** — this payload is customer DOM and selectors, materially more sensitive than the current short codes. That is the same consent the hosted model will need anyway.
  3. ~~**Harness on OpenCode.**~~ **Started 2026-09-04** — hand-ported agent loop + stdio MCP client under `conxa-execute/app/vendor/opencode/` (MIT, pinned commit; see `app/vendor/opencode/README.md`). Chat is locked to `list_skills` / `get_skill_inputs` / `execute_skill`. Not their TUI/desktop; upgrades are diffed against a fresh clone kept outside this repo. **Update (cleanup pass):** the upstream `packages/llm` reference tree that used to sit alongside the four live files was deleted — it was never imported, never compiled, already excluded from the installer, and its own dependencies (`aws4fetch`, `@smithy/*`, `@opencode-ai/schema`) were never installed; it existed purely as an upgrade-diffing aid that an external clone already serves.
  4. **Hosted model tier.** Add Kimi K3 as a router entry. Two things to verify on the model card before committing: **vision support** (`router.py:260-261` skips providers with no `vision_model`, and recovery's relational-anchor and drawn-region-retarget paths need it — if K3 is text-only, keep a vision provider enabled alongside or those tasks fall through to nothing), and **multi-turn tool-calling reliability**, since the loop calls `execute_skill`/`get_skill_inputs` repeatedly and feeds failures back in.
  5. **Auth.** ~~The runtime already holds a per-workspace bearer token; either extend `/api/v1/llm/proxy` to accept it or bake an LLM token into `pack.json` beside the sync token.~~ ~~Shipped differently, 2026-09-05: rather than a per-workspace bearer token, `conxa-execute` has no login system at all, so a purchase mints an opaque **Execute Key**...~~ **Superseded 2026-09-05 (later same day):** the opaque Execute Key model was replaced with real Clerk sign-in (`conxa-execute/backend/app/auth.py`, a Clerk application deliberately separate from conxa-cloud's — its JWT claims are shaped around org_id/workspace, foreign to a single-consumer-user wallet). Wallet/subscription/session data now key off the Clerk `user_id`; `keys.py` was deleted as dead code once nothing referenced it. `conxa-execute/app/electron/auth_service.js` is a Node port of Build Studio's Clerk PKCE flow (`conxa-builder/python/services/auth_service.py`) for the desktop app's login. **Still open:** a real Clerk application needs registering (domain + client id) before login is testable end-to-end — nothing here can do that from this environment.
  6. **BYO key.** Per-workspace stored credential, used instead of our pool; metering records usage but bills nothing. Decide storage (keyring vs. cloud) and whether BYO-key runs still report recovery telemetry. *(Unaffected by the 2026-09-05 work below — that's the managed-chat side; BYOK in `conxa-execute`'s Settings modal already worked before and still does, unchanged.)*
  7. **Credits + QR.** ~~Ledger on top of `llm_metering`, QR checkout via the existing Cashfree integration, low-balance warning, and defined behavior when credits hit zero mid-run (finish the run, then block — never abandon a half-completed workflow).~~ **First cut shipped 2026-09-05** as a standalone service (`conxa-execute/backend/`, own `render.yaml`/Postgres, not `llm_metering`): 6 token tiers (250k tokens/₹500 up to 10M/₹15,000) as both one-time packs and monthly auto-refill subscriptions, checkout via Cashfree Payment Links/subscriptions (Cashfree's own hosted page offers UPI QR among its payment methods — no separate custom QR flow was built), wallet floors at 0 rather than blocking mid-call (see `docs/TRD.md` §3.6 for the exact behavior). **Reworked 2026-09-05 (later same day):** "subscription" is now a real tiered plan with a monthly quota that resets each period (`app/subscription.py`, `app/plans.py`) — not a recurring top-up of the same forever-stacking balance — while one-time packs still work exactly as before via `app/wallet.py`. The proxy checks subscription quota before top-up balance. Checkout moved off the old anonymous form (`existing_key` field) onto an authenticated `POST /v1/checkout/{tier}` the signed-in Electron app calls, opening Cashfree's hosted page in the OS browser. **Still open:** the model tier is the existing free-compile-pool providers (Groq/Google AI Studio/NVIDIA NIM) under dedicated keys (now resolved independently in `app/llm_config.py`, with model-fallback wired via `app/llm_client.py` — see step 4 below for Kimi K3 specifically); low-balance warning UI in the Electron app itself; **a mode picker** — `SettingsModal.tsx` can currently only *display* which mode is active (`chatMode`, driven by saved settings or a redeemed workspace-pool grant), there is no control for a person to actually choose Top-up or Subscription, so `POST /v1/checkout/{tier}` and the `plansUrl` the entitlement IPC already returns have no UI entry point yet; confirming Cashfree v2's exact per-charge webhook field for subscription-renewal idempotency against a real sandbox payload (flagged in code, `routes_checkout.py::webhook_cashfree`); free-tier upstream rate limits are a real ceiling at paid volume — launched deliberately on free tier per founder call, monitor and upgrade if usage hits it; and end-to-end checkout/webhook testing needs real Cashfree sandbox credentials, which this environment doesn't have.
  8. **Fine-tune.** Only once (2) has produced real volume.
- **Update (2026-09-17 — steps 5/6/7's standalone-backend design reversed):** the separate `conxa-execute/backend/` Render service, its separate Clerk application, BYOK, the personal wallet/subscription, and the dedicated LLM provider keys described in steps 5-7 above are all **deleted**. Conxa Execute now shares conxa-cloud's backend, Clerk app, and managed provider pool directly — access and billing are entirely the existing Build Studio team plan (Execute Seat grants, §13.4c) rather than a personal purchase flow. This closes step 5's "still open: register a real Clerk application" (done — a new OAuth client under the existing `clerk.conxa.in`, not a new app) and makes steps 6 (BYO key) and 7 (Credits + QR, as a personal purchase) moot as written: there is no BYOK option in Execute anymore, and there is no personal credit purchase — a workspace's existing Cashfree subscription is what funds Execute now, same pool as Human Edit. The founder-level reasoning: give a person Execute access without necessarily giving them Build Studio access (an Execute Seat grant, unchanged), bill it as part of the team plan (now true), and let a signed-in person switch between their personal context and any team they have Execute access to (`GET /api/v1/execute/contexts`, new). See `docs/TRD.md` §3.6/§13.7 for the current design. Steps 1-4 and 8 (front door, recovery-data capture, OpenCode harness, hosted model tier, fine-tune) are unaffected by this reversal.
- **The number to watch:** recovery success rate, split by front door — of the steps that fail Tier A, what fraction get healed at Tier B by Claude Desktop versus by our hosted model, on the same skills and sites. Durability is the whole pitch; if the hosted tier heals meaningfully worse we will have shipped a cheaper product under the same promise without noticing. This cannot be judged by feel, and step (2) produces the data for free.
- **`[DECISION]` — open questions for the founders:** does the hosted tier get priced into a seat price or sold purely as credits? Is BYO-key available on every plan or only above a tier? Does the direct-run front door ship to everyone (including existing Claude Desktop customers, as a faster path for repeat runs) or only to the no-subscription segment? Is recovery-data capture opt-in per workspace, or a condition of the hosted tier?
- **Dependencies:** none blocking. Step (2) should land before the fine-tune is scoped, and ideally before the hosted tier ships, so the two front doors can be compared on the same data. Overlaps PROD-11 (skill health dashboard — the same recovery telemetry feeds it) and CLOUD-1 (BYO-key credential storage is tenant-isolation-shaped).
- **Suggested order:** step 1 immediately (a week, unblocks the no-subscription market on its own), step 2 right behind it (cheap, and its value is time-sensitive — data not captured is data lost), steps 3-7 as the platform push, step 8 last.
- **Complexity:** XL — needs its own sub-plan. Steps 1 and 2 are each S–M and are worth doing regardless of whether the rest proceeds.
- **Success criteria:** a customer with no Claude or Codex subscription can install the pack, run a skill from the app, and heal a Tier B failure; the same customer can instead paste their own API key and run with zero credit spend; credits can be bought inside the app via QR without a redirect; and the recovery-success-rate comparison above is visible on a dashboard, not inferred.

### DOC-1 — Keep the 2026-07 documentation audit's fixes from re-drifting
- **Category:** Documentation & Process
- **Description:** This audit fixed a large number of stale claims across `docs/`, root-level files, and `research-analysis/` (Razorpay→Cashfree references, contradictory delta-sync contracts, a broken `docs/agentic-discovery-strategy.md` path, a rewritten `Auth-and-Updater.md` §1.3 that no longer describes a nonexistent keytar-token-refresh mechanism, and more). None of this is self-enforcing — it can drift again exactly as it did before.
- **Why required:** the audit found docs that were stale for a month or more without anyone noticing, including internal self-contradictions within single files (`docs/TRD.md` said a gap was both open and resolved in two different sections).
- **Business value:** stale docs cost engineer-hours and erode trust in the doc set as a source of truth — several docs were found to describe removed files (`worker.py`, `Aptfile`) or reverted designs (V8 bytecode app layer) as if current.
- **Technical value:** a documentation set that's actually trustworthy is a precondition for AI coding agents (which read `CLAUDE.md`/`AGENTS.md` before every task) to make correct decisions without re-deriving context from code archaeology.
- **Dependencies:** none — this is a process item, not a code change.
- **Suggested order:** ongoing discipline, not a one-time task. Revisit at the next major milestone (e.g., after DOC-4 below ships, or at the next audit boundary).
- **Complexity:** S per touch-point, ongoing.
- **Success criteria:** `CLAUDE.md`'s "Maintaining the Docs" table continues to be consulted after significant changes (already-existing policy, now with FIX.md rotation and TODO.md maintenance rules added to it).

### BUILD-1 — Compiler IR (CIR) + reproducible, pinned compiles
- **Category:** Builder
- **Description:** Introduce a diffable intermediate representation between recorded events and the final `SkillPackage` — a `.cir` artifact with a `cir_root_hash`, produced by a compile pinned to a specific model/prompt version so the same recording reliably produces the same package. Confirmed unbuilt via grep across `conxa_compile/` (no `.cir`, no `cir_root_hash`, no model-pinning anywhere).
- **Why required:** without a diffable IR, there's no way to do partial recompilation of just the changed steps, or a diff-driven repair-suggestion UX when a skill needs a targeted fix rather than a full recompile. (Rollback-to-identical-bytes itself no longer needs this — the 2026-08-19 release system added immutable per-version artifact snapshots + a stable-channel pointer, so rollback is a pointer move against already-published bytes with no CIR involved. `release_diff.py`'s deterministic diff also covers "what changed between two releases" at the compiled-output level, coarser-grained than a true CIR but sufficient for the Release Center's diff view — see `docs/TRD.md` §5.5.)
- **Business value:** unlocks safer, faster iteration on published skills — a customer-visible reliability and turnaround-time improvement once the durability flywheel (EXEC-2) can act on it.
- **Technical value:** this is the substrate multiple other items depend on — most notably EXEC-2 (fleet durability automation), which needs a diffable representation to generate targeted CIR patches rather than full recompiles.
- **Dependencies:** none upstream, but EXEC-2 depends on this.
- **Suggested order:** sequence before EXEC-2; it's foundational infrastructure, not a user-facing feature, so it can proceed in parallel with other execution/recovery items that don't depend on it (EXEC-1, EXEC-3).
- **Complexity:** XL — touches the compiler's core event→package pipeline, requires a new artifact format, versioning strategy, and model-pinning across the LLM router.
- **Success criteria:** compiling the same recording twice (same model/prompt version) produces byte-identical `.cir` output; a `cir_root_hash` mismatch is detectable and drives a defined resolution path (recompile vs. flag).

### BUILD-18 — Learned classifier for pipeline judgment calls (dedupe/variable-detection/hover-vs-click), fed by human-correction telemetry
- **Category:** Builder / Compiler
- **Description:** Keep selector/target generation deterministic (`IdentityBundle` + `selector_grammar.py` — this is the non-negotiable invariant in `CLAUDE.md` and stays). But several *other* pipeline judgment calls in `conxa_compile/pipeline/` (`dedupe.py`'s `drop_superseded_focus_events`/`collapse_select_interaction_noise`/`dedupe_scroll_events`, and `run.py`'s stage sequencing) are currently rule-based heuristics over recorded event shape, deciding things like: is this event noise vs. signal ("wasted moment"), should this field be promoted to a variable, should this interaction be a hover vs. a click. These are classification problems, not identity lookups, and heuristics will keep misfiring on cases outside the rules' assumptions no matter how many are added. Longer-term, replace/augment these specific decision points with a learned classifier or LLM pass that runs *before* the deterministic compiler, not instead of it.
- **Why required:** the deterministic rules can't generalize to recording patterns they weren't written for, and there's currently no mechanism to detect or learn from the misses — every wrong dedupe/variable/hover call is silently wrong until a human catches it in the editor. Training data doesn't exist yet, so this can't be built today; it needs an instrumentation step first.
- **Business value:** fewer manual corrections per compiled skill as recording variety grows, without touching the auditable selector-generation path that self-healing and recovery depend on.
- **Technical value:** narrow, well-scoped ML surface (classification over event sequences) instead of a full "replace the compiler with an LLM" rewrite, which would break reproducibility guarantees the recovery cascade relies on.
- **Dependencies:** needs a labeling hook first — log every human correction made in the workflow editor (collapsed/uncollapsed steps, retargeted hover, promoted-to-variable fields) as labeled examples tied to the original recorded event sequence. Complements EXEC-9's telemetry work (that's for signal-durability priors; this is for pipeline-stage judgment priors) but is a separate data stream.
- **Suggested order:** instrumentation (the labeling hook) can start any time and should start early so data accumulates; the classifier/LLM pass itself waits until there's enough labeled volume to beat the current heuristics.
- **Complexity:** M for the labeling hook; L+ for the eventual model/prompt work once data exists.
- **Success criteria:** every editor correction to a dedupe/variable/hover decision is captured as a labeled example with its source event sequence; before any model is trained, someone can query "how often does the current heuristic get corrected, and on what event shapes."
- **Found via:** user discussion (2026-08-30) on whether the compiler should move toward ML/LLM-driven output.

### BUILD-20 — Workflows page: parallel workflow checks instead of cancel-on-new-check
- **Category:** Builder / Build Studio
- **Description:** On the Build Studio Workflows page, starting a check (test run) on one workflow and then starting a check on another cancels the first one — checks currently run sequentially through a single slot, so the newest click always kills the in-flight check. Make checks parallel: clicking check on a second workflow should start it alongside the first, not replace it.
- **Why required:** a vendor verifying several workflows after recording has to babysit the page — wait for each check to finish before daring to click the next one, or silently lose the running check. This is exactly the sequential-execution pain point already solved at the runtime level by RT-3's per-run registry (`runtime/app/run_registry.js`, `CONXA_MAX_CONCURRENT_RUNS` default 5) and host-level serialization via `host_lock.js`; the Studio's test path should behave the same way.
- **Business value:** vendors verify whole batches of workflows after recording/publishing; serial checking multiplies that wall-clock time and surprises users when their running check vanishes.
- **Technical value:** likely a Studio-side single-active-test slot (renderer state and/or backend handler) needs to become a per-run keyed map mirroring RT-3's shape; note two runs touching the SAME external app still serialize via `host_lock.js` semantics, which is correct and should be surfaced as "queued behind" rather than "cancelled".
- **Dependencies:** none blocking — runtime-side parallel execution already ships (RT-3).
- **Suggested order:** soon — direct user-reported friction on the primary authoring surface.
- **Complexity:** M — depends on how much of the serialization lives in the renderer vs. the Python backend's test/sandbox runner (`conxa_runtime.py` stages a sandbox; its staging may itself assume a single active test).
- **Success criteria:** with two workflows checked back-to-back, both checks run to completion concurrently (or the second visibly queues with status, never cancelling the first); results for each check stay correctly attributed to their own workflow.
- **Found via:** direct user report (2026-08-23).

### BUILD-31 — Durability scores are hand-set priors, never measured against real page mutations
- **Category:** Builder / Build Studio
- **Description:** `selector_score.py::durability_score` is a hardcoded table (testid=0.99, role+name=0.95, text=0.85, ...) multiplied by whether the signal was unique on the one page the recorder saw. The compiler never sees the page twice, so every number is a guess about websites in general — it can't tell a stable `data-testid` a vendor's test suite depends on from one a developer will delete next week, or a role+name that's uniquely stable from one that's unique only because today's list has three rows. Fix: mutate the recorded DOM snapshot the way a real redesign would (strip all `data-testid`, rename every CSS class, wrap the target in a new `<div>`, reorder/shift siblings, drop the `id` attribute, translate/reword visible text and `aria-label`) and re-resolve each `IdentityBundle` signal against each mutant using Playwright's real engine (`page.setContent(mutated_html)` in headless Chromium, ~50–200ms per mutation per step, fully parallelizable on the existing vision-prefetch thread pool). A signal surviving 7 of 8 mutations is durable; one surviving 2 of 8 is a liability regardless of what the static table says.
- **Why required:** the whole recovery/durability story rests on these numbers meaning something. Right now `0.95` means "role selectors are usually fine," not "this specific selector survives realistic page changes" — and the orthogonality classing (independent signal groups so one change can't kill all of them) is itself an unverified assumption; two "independent" signals often secretly share the same underlying attribute, and mutation testing is the only way to catch that per element.
- **Business value:** turns an opaque confidence number into an actionable human-review signal ("this step has one way to find its button and dies the moment anyone renames a CSS class") instead of a score nobody can act on; also makes **BUILD-18**'s learned classifier and **PROD-1**'s repair memory train against ground truth instead of the same hardcoded priors they're trying to improve on.
- **Technical value:** replaces a guess with a measurement using tooling the compiler already has (Playwright, the thread pool, the recorded DOM snapshot) — no new infrastructure, no LLM tokens, fully offline and deterministic.
- **Dependencies:** none blocking. Longer-term the mutation set itself should be learned from field drift rather than hand-written — natural pairing with **EXEC-9** (dataset-grade telemetry) once that data exists.
- **Suggested order:** before leaning further on `durability_score` for anything user-facing (recovery ranking, publish-time warnings) — the measurement should exist before more decisions are built on top of the guess.
- **Complexity:** L — a mutation harness (8+ mutation kinds × every step's DOM snapshot), integrating it into the compile pipeline without materially slowing compiles, and reworking `durability_score`'s output shape to carry per-mutation survival instead of a single static multiplier.
- **Success criteria:** each compiled step's identity signals carry a measured survival rate against the mutation suite instead of (or alongside) the static table value; Human Review surfaces "single point of failure" steps whose only signal dies under a realistic mutation.
- **Found via:** advisor review of the reliability architecture (2026-09-12).

### CLOUD-1 — RBAC / SSO / tenant isolation (enterprise plumbing)
- **Category:** Cloud
- **Description:** Extend the current partial RBAC (`require_admin` enforced only on publish, plugin create/delete, and bundle-release routes) to per-skill ACLs and a read-only analyst role; add SSO/SAML (currently unbuilt — Clerk JWT only); replace `workspace_id` string-filtering in shared KV namespaces with real tenant isolation (Postgres row-level security or equivalent). This is the item that carries Conxa's enterprise-readiness work more broadly, alongside PROD-9/PROD-10.
- **Why required:** this is explicitly Phase 3 "Enterprise Readiness" scope per `docs/Implementation-Plan.md`, and enterprise buyers routinely require SSO and demonstrable tenant isolation as contractual/compliance gates, not nice-to-haves.
- **Business value:** gates enterprise revenue directly — this is named as a revenue-gating item in the research corpus's `master-recommendations.md` (R8) and in `docs/UI-UX-Brief.md`'s own UX audit (§8.5).
- **Technical value:** closes the gap between "RBAC exists" and "RBAC is actually granular and enforced everywhere it needs to be," and moves tenant isolation from a convention (string-filtering) to an enforced guarantee.
- **Dependencies:** none blocking, but MCP-1 (entitlement-filtered `list_skills`) depends on a more complete entitlements/RBAC model than exists today. Also related to PROD-9's IT admin console and PROD-10's SOC 2 work.
- **Suggested order:** can run in parallel with execution/recovery work — it's revenue-gated, not reliability-gated, per the research corpus's own sequencing recommendation.
- **Complexity:** XL — SSO/SAML integration, per-skill ACL model, and a real tenant-isolation migration are each substantial on their own.
- **Success criteria:** a read-only analyst role exists and is enforced; SSO/SAML login works end-to-end for at least one major IdP; tenant data isolation is enforced at the data layer, not just by application-level filtering convention.

### CLOUD-16 — Group-level access control: who can see/edit which Workflow Group in Build Studio
- **Category:** Cloud / Build Studio
- **Description:** Today any employee signed into Build Studio can see and edit every Workflow Group in the workspace. Add per-group membership so, for example, only Sales employees are added to (and can open/record/compile into) the "Sales" group, while a Support employee can't see it at all. Needs a group-membership model on the cloud side (which user belongs to which group(s)) plus enforcement in Build Studio's group list/open/save calls — not just hiding the group in the UI.
- **Why required:** companies onboarding multiple departments onto Conxa need department-scoped editing today, not just department-scoped execution (see CLOUD-17) — an org where Sales and Support share one workspace currently has no way to keep each team out of the other's workflows.
- **Business value:** removes a blocker for any customer with more than one department building workflows; today the workaround is "give every team the whole workspace," which larger customers won't accept.
- **Technical value:** this is the natural extension of CLOUD-1's per-skill ACL work, scoped to groups instead of individual skills — likely shares the same permission model and should be designed together with CLOUD-1 rather than as a second, parallel ACL system.
- **Dependencies:** CLOUD-1 (RBAC model) — build the group-membership check on top of whatever role/permission primitive that item establishes, don't build a second one.
- **Suggested order:** after or alongside CLOUD-1; before CLOUD-17, since execution-time enforcement needs the same membership data this item creates.
- **Complexity:** L — group-membership schema + enforcement in every Build Studio group read/write path.
- **Success criteria:** a user assigned only to the Sales group cannot list, open, or edit any other group's workflows via Build Studio, enforced server-side (not just hidden client-side).

### CLOUD-17 — Employee-scoped execution: who can run which workflow group at runtime, gated by installer login
- **Category:** Cloud / Runtime
- **Description:** Right now the installer identifies a *company*, not an *employee* — anyone with the installer and a machine can execute every skill pack it downloads. Add an employee-identity layer: the installer/runtime authenticates the individual (email + password, or SSO once CLOUD-1 lands) before it will sync or execute skill packs, and execution is filtered to the workflow groups that employee's account is a member of (same membership model as CLOUD-16) — a Sales employee's runtime can execute Sales-group skills but not Support-group ones, and skill packs outside their groups are never even synced to their machine.
- **Why required:** without this, any customer employee (or anyone who gets hold of a company's installer) can execute every workflow the company has ever published, regardless of role — a real problem once CLOUD-16 lets companies scope *editing* by group, since execution would still be wide open.
- **Business value:** this is likely a hard requirement for the same enterprise buyers CLOUD-1 targets — least-privilege execution, not just least-privilege editing, is a standard ask once a company has more than one department on the platform.
- **Technical value:** changes what the installer authenticates as (today: company-level static token in `pack.json`) to what it authenticates as *plus who's logged in on this machine* — touches `runtime/auth_manager.js` (per-company token today, needs a per-employee session on top) and `runtime/sync.js` (delta-sync needs to filter by the logged-in employee's group membership, not just company).
- **Dependencies:** CLOUD-1 (identity/RBAC model) and CLOUD-16 (group-membership data) — this is the runtime-enforcement half of the same access model, not a separate one.
- **Suggested order:** after CLOUD-16, since it reuses that item's group-membership data; both should be designed in the same pass as CLOUD-1 to avoid ending up with three divergent permission models (cloud dashboard, Build Studio, runtime).
- **Complexity:** XL — new employee-identity auth flow in the installer/runtime (login UI or CLI prompt, credential storage, session refresh), plus sync/execution filtering by group membership, plus the "Auth files never enter build output" invariant (see CLAUDE.md) needs revisiting for what *is* allowed to persist locally for a logged-in employee session.
- **Success criteria:** a fresh install of the installer requires an employee to log in before any skill pack executes; an employee's runtime only ever syncs and can only ever execute skills belonging to workflow groups they're a member of; revoking a group membership on the cloud side stops that employee's local runtime from executing that group's skills on next sync.

### CLOUD-18 — New Build Studio install: authenticate as employee, auto-import (read-only) the groups they belong to
- **Category:** Cloud / Build Studio
- **Description:** When a new employee installs Build Studio and logs in (same employee auth as CLOUD-17), Build Studio should look up which Workflow Groups the cloud says they belong to and, for any group that already has published workflows, pull those workflows' *published* skill packages down into the local Build Studio so the employee can see and test-run them immediately — without waiting for someone to hand them a file or re-record from scratch. The catch: the underlying recording/session data (`data/sessions/<id>/events.jsonl`, screenshots) only ever lived on the machine that recorded it and was never uploaded to the cloud (recording/compiling is local-only, per this repo's core architecture). So the import is necessarily the *compiled, published skill package* — enough to view, test-run, and understand the workflow — not the original recording, so it can't be re-compiled or edited on the new machine. Editing still requires either the original recorder's machine or a fresh re-recording.
- **Why required:** today a new hire on an existing team (e.g. the second Sales employee) starts from zero in Build Studio even though their team's workflows already exist and are published — they have to ask around for context or re-record something that's already built. This is a rough first-week experience and duplicates work.
- **Business value:** faster ramp for new team members, less duplicated recording effort across a team, and reinforces that Workflow Groups (CLOUD-16) are a real shared-team boundary rather than just an access filter — joining a group should visibly "hand you" what your team already built.
- **Technical value:** this is a read path on top of CLOUD-16's group membership and the existing Publish flow's already-cloud-hosted skill packages (`skillpack_storage`/`publish_routes.py`) — no new storage is needed, just a Build Studio import step that pulls what's already on the cloud for the groups the logged-in employee belongs to. Must be clearly surfaced as read-only/imported in the UI (distinct from "your local recordings") so nobody tries to edit a package with no local session data behind it and gets confused when it fails.
- **Dependencies:** CLOUD-16 (group membership data) and CLOUD-17 (employee login in the Build Studio / installer context) — this is the "welcome import" that group membership makes possible, not a separate access-control mechanism itself.
- **Suggested order:** after CLOUD-16 and CLOUD-17 land, since it's a convenience feature layered on their identity + membership plumbing, not a blocker for either.
- **Complexity:** M — mostly a Build Studio-side "fetch published packages for my groups on login" call plus clear read-only UI treatment; no new compiler or storage work.
- **Success criteria:** a new employee, after logging in, sees their group's already-published workflows appear in Build Studio automatically, can open and test-run them, and gets a clear "imported, not editable here" indicator rather than a confusing edit failure if they try to modify one.

### EXEC-2 — Fleet durability flywheel automation
- **Category:** Execution & Recovery
- **Description:** Complete the durability pipeline beyond its current detection-only state: automatic classification of a detected drift, a CIR-based patch suggestion (depends on BUILD-1), regression-gating the patch against a golden-corpus test suite, and canary rollout with auto-rollback on regression. Today, only the detection + admin-review half exists (`GET /api/v1/tracking/{company}/drift`, an admin-gated queue) — an admin still manually reviews and republishes every drift.
- **Why required:** the fleet-level drift-detection flywheel is identified across the research corpus (`research-analysis/03-insights/master-insights.md` L1/#1, `research-analysis/06-execution-plan/master-recommendations.md` R3, `research-analysis/06-execution-plan/cto-report.md`) as Conxa's single durable, structurally-uncopyable competitive moat — no single-tenant/local automation tool can replicate cross-customer drift intelligence. Right now that moat is only half-built: detection compounds with scale, but manual republishing doesn't.
- **Business value:** this is the mechanism that would let "workflows survive for years with no manual maintenance" become a real, delivered promise rather than a partially-automated one — directly load-bearing for retention and for differentiating against both legacy RPA (which requires manual maintenance) and agent-based competitors (which can't do fleet learning at all). Directly overlaps PROD-11's "fleet durability dataset" framing of the same mechanism from a business-value angle.
- **Technical value:** this is the compounding asset — more customers → faster drift detection → fresher packages → higher reliability → more customers — that the rest of the architecture is, per the research corpus, built in service of.
- **Dependencies:** depends on BUILD-1 (Compiler IR) for diffable, targeted patches rather than full recompiles; the admin-approval step (publishing is never automatic, per the existing design) should remain even once the rest is automated.
- **Suggested order:** after BUILD-1 lands; this is a large, multi-stage effort likely worth its own dedicated sub-plan once BUILD-1 is in place.
- **Complexity:** XL — classification logic, a CIR-patch-generation step, a golden-corpus regression suite, and canary-rollout infrastructure are each substantial; combined, this is likely Conxa's largest single remaining engineering investment.
- **Success criteria:** a drift detected on one customer's runtime is automatically classified, a patch is generated and regression-tested against a golden corpus, and — pending the existing admin approval gate — pushed fleet-wide, measurably faster than today's fully-manual review-and-republish cycle.

### EXEC-4 — `[DECISION]` Autonomous Tier-3 recovery design
- **Category:** Execution & Recovery
- **Description:** The originally-specced design for Tier B recovery was an invisible round trip: the runtime asks the host model directly via MCP sampling, the model describes-then-matches against the recorded intent, and the fix is applied with no human-visible interruption. What actually shipped is different and, in some ways, more elaborate: a host-delegated handoff with page-parking (`server.js:_parkedRecovery`) and `step_overrides` — the browser session survives the round trip to Claude Desktop and back, and the agent still reasons at the top level, but this is a visible resume-with-a-corrected-selector flow, not an invisible one.
- **Why required:** this is real, meaningful progress over the "screenshot and hope" model the original research corpus describes as the pre-existing state — but it's a materially different shape than the target design, and nobody has explicitly decided whether the current design is the accepted long-term approach or an interim step toward the original spec.
- **Business value:** affects how "self-healing" gets described to customers and prospects — the current mechanism is real and working, but oversells as "autonomous" if the shipped mechanism is actually agent-visible-and-resumable rather than invisible.
- **Technical value:** the answer changes how much further engineering investment (if any) goes into building the original invisible-sampling design versus polishing and formalizing the current page-parking approach.
- **Dependencies:** none technically; this is a scoping decision that should happen before further investment in either direction.
- **Suggested order:** early — resolving the decision avoids wasted effort in either direction.
- **Complexity:** M for the decision itself; XL if a full redesign toward the original invisible-sampling spec is chosen.
- **Success criteria:** a written decision exists (in `docs/TRD.md` §10 or a dedicated design note) stating which design is the accepted target, and any customer/marketing-facing description of "self-healing" is checked against that decision for accuracy.

### EXEC-5 — `[UNVERIFIED]` Action-correct interaction handler library
- **Category:** Execution & Recovery
- **Update (2026-08-25):** the **dismiss-known-pattern slice shipped early** as part of the never-recorded-popup work (see Workflow 8 AV-5 in `docs/testing/01-WORKFLOWS-TO-TEST.md`). On an INTERCEPTED Tier A failure, after the historical Escape press, `cascade.js::layer1Ladder` now walks a bounded list of ubiquitous consent-toolkit accept/close selectors (`app/dismiss_patterns.js` — OneTrust, Cookiebot, TrustArc, Iubenda, Quantcast, Complianz, CookieYes + generic scoped accept/close phrasing; never decline buttons, never bare modal-footer buttons) and a host-scoped learned store (`app/learned_dismissals.js`, `{CONXA_DATA_DIR}/learned_overlays.json`, 30-day TTL, per-host/global caps, runtime state only — packs are never mutated). Successes are recorded to the audit log (`tier1_dismiss_pattern`) and refresh the host entry so repeat visits skip straight to the winning selector. Unit-tested in `runtime/test/unit/test_dismiss_patterns.js` (13 tests). **Still open from this slice:** (a) hooking Tier B agent-recovery wins on INTERCEPTED steps into `record()` — that is what makes a paid first fix free forever; (b) surfacing repeated learned wins as a one-click "insert try_dismiss here?" suggestion in Human Edit (republish re-signs properly); (c) an optional proactive post-navigation sweep for high-confidence banner tokens only.
- ~~**Update (2026-08-30):** the **date-picker handler (#33) shipped.**~~ Custom-calendar widgets (MUI, react-datepicker, flatpickr, Ant Design, jQuery UI, ...) now compile and replay correctly, not just native `<input type=date>`. Record: `bridge.js::buildDateContext` tags a click landing inside a detected calendar grid (day cell / nav button / time option) with `date_context`, carrying which DOM attribute the date was parsed from so a DIFFERENT target date can be re-queried later instead of reusing the recording's own (often locale-sentence) cell selector. Compile: `compiler/date_picker.py::collapse_date_picker_runs` (called from `build.py` right after `_populate_hover_chains`, same steps↔events alignment requirement) collapses the open→nav→day-cell click run into ONE `date_pick` step with `handler_hints.control_kind="date_picker"` — binds to a run input (`{{due_date}}`, named off the field's label/placeholder when found) whose declared `default` is the recorded date, supports date ranges (two steps, `{{start_date}}`/`{{end_date}}`) and datetime pickers. Also fixed in passing: native `date_pick` steps never got a `value_equals` post-condition (excluded from `_build_assertions`'s value-set action set) — added, matching every other value-set action; and `derive_input_binding`'s phone-number pattern matched ISO date strings, misclassifying `{{end_date}}`-shaped bindings as `{{phone}}` — added an ISO-date check ahead of it. Replay: `runtime/app/date_picker.js` (pure month/header/selector math, unit-tested standalone) + `handlers.js`'s `date_pick` adapter try typed-first (fill the field's own display format, verify readback) and fall back to driving the grid (bounded, boundary-aware month navigation; day-cell click via a machine-readable attribute rebuilt for the target date, or a locale-agnostic exact-text match excluding disabled/adjacent-month cells) only for a compiled custom-picker step whose typed attempt didn't stick — zero LLM tokens throughout, Tier A invariant holds. Native `<input type=month|week|time>` behavior is untouched (their values aren't full ISO dates, so the new parsing simply doesn't apply and the original fill/click path runs). 22 new unit tests (`runtime/test/unit/test_date_picker.js`, pure — no browser); full 518-test runtime suite green.
- ~~**Follow-up (2026-08-30, same day):** select-based month/year navigation.~~ A real demoqa.com Practice Form recording (react-datepicker's `showMonthDropdown`/`showYearDropdown` mode — its own recommended pattern for a date of birth) exposed three bugs: `buildDateContext` never fired on a `<select>`'s own `"select"` action at all (widened to include it, plus new `year_select`/`month_select` `date_context` roles, detected by class/aria-label matching `/year|month/i`; `collapse_date_picker_runs` treats them as run-starters/continuations alongside day/nav/time, and the runtime drives them via `selectOption({label})` — by label, never by the option's raw value, since libraries index month values inconsistently); `pipeline/dedupe.py` had no dedup for click/type noise bracketing a `select` event, so one dropdown pick compiled into 4 steps (new `collapse_select_interaction_noise`, general-purpose — every `<select>` in every recording benefits, not just calendars); and `captureAssociatedLabel`'s last-resort fallback mistook a calendar's own live "current month" readout for a field's label (`"August 2026"` → binding `august_2026`), fixed with an anchored month-year exclusion. A run with select-driven month/year but no day cell ever clicked (the real recording's actual shape) is deliberately left uncollapsed — two clean `select` steps, not an invented date. Both bridge.js fixes are record-time only; recompiling an old session gets the dedup fix but not clean labels/collapsing — needs a fresh recording. 2 more unit tests; full 520-test runtime suite green; verified directly against the real recorded session's raw events (8 steps → 3 after dedup).
- ~~**Update (2026-08-31):** multiple-choice controls (radio/checkbox groups, native `<select>`, ARIA radiogroup/listbox widgets) shipped.~~ Recording a gender radio group used to compile to `role=radio[name="gender"]` (the group's shared HTML `name`, not the picked option's accessible name — Playwright's role selector matches accessible name, not `name`, so this either matched every option or none) plus a duplicate phantom `Focus` step, and the declared input was named after the recorded ANSWER (`{{male}}`) instead of the QUESTION (`{{gender}}`), so a different value at execution had no effect on which radio got clicked. Record: `bridge.js::buildChoiceContext` tags a radio/checkbox/select/ARIA-radiogroup-or-listbox event with the group's kind, key, label, and every member's `{value, label, selector, checked}` (requires ≥2 members — a lone checkbox stays ordinary); an ARIA custom widget's click is reclassified as `set_radio`/`select_option` since it never fires a native `change`. Root-cause fixes: `identity_bundle.py::_accessible_name` no longer treats the HTML `name` attribute as an accessible-name source (it never was one, for any element); `action_semantics.py::is_editable_target` excludes radio/checkbox (a click commits in one gesture, it doesn't acquire a caret), and `clean_steps`'s prep-click merge now covers every value-setting action, not just `type`/`upload`. Compile: `compiler/choice.py::derive_choice` names the input from the group's label/key (never the answer) for single-shot kinds; `collapse_choice_group_runs` collapses a "pick all that apply" checkbox run into one multi-valued step. Both feed `editor/workflow_mutations.py::_choice_specs`, which auto-declares a real `select`/`multiselect` input with the full recorded option set as its `enum` — reaching both `input.json` and the MCP tool schema, so an agent literally cannot send an out-of-range value. Replay: `runtime/app/choice.js` (pure, unit-tested standalone) resolves a caller's value ("male", "M", "Male ") against the recorded options through an exact→normalized→unique-prefix ladder, returning no match at all rather than ever guessing; `handlers.js`'s `set_radio`/`select`/`select_option`/`set_checkbox` act on the matched option's own selector and fail closed (marked `badInput`, skipping Tier 3+ recovery) naming every valid option on a mismatch. Additive on `handler_hints.control_kind` — a skill compiled before this needs no recompile. 14 new runtime unit tests + 21 new compiler-side tests (14 in `test_choice_compile.py`, 2 in `test_nameless_element_identity.py`, 5 new bridge.js contract tests); full suites green (534 runtime, 1052 cloud). **Still open:** `runtime/resolver.js:95`'s `scoreCandidate` uses `fingerprint.name` as an accessible-name proxy the same wrong way `identity_bundle.py::_accessible_name` did before this fix — same root cause, on the resolver's scoring side instead of the compiler's signal-generation side; not fixed here since it changes resolver scoring behavior for every already-compiled skill, not just newly-compiled multiple-choice ones. Checkbox-group multi-select was originally scoped as a follow-up but shipped in the same pass once the group-collapse plumbing was already in place for radios.
- **Description:** A cohesive batch of specific-interaction handlers identified in `research-analysis/05-reliability/top-50-improvements.md` (Tier 1–2) and grouped together in `build-order.md`'s own build sequence: a typeahead/autocomplete handler (fill→wait-for-options→select-exact, #10); a custom-dropdown handler (open→wait→click-by-text, distinct from native `selectOption`, #15); a contenteditable/rich-text handler (focus+key events, since `fill()` silently no-ops on Quill/Slate/TinyMCE, #23); scroll-until-found for virtualized lists addressed by stable id, not position (#12, ag-grid/react-window rows that aren't in the DOM until scrolled to); a hover-gated action group with re-hover recovery for menus that close on blur (#13); a post-navigation stale-DOM guard that aborts if URL/focus changed mid-action (#14); a lazy-load bounded scroll-to-load loop (#25); re-resolving moving/reordering rows by stable id rather than position (#26); download verification (file exists/size/type, #31) and upload verification (input populated/preview shown, #32); a date-picker handler (typed input vs. day-cell navigation, #33); and new-tab/new-window landed-context verification so an action can't silently execute in the wrong window (#43).
- **Why required:** these are exactly the "the recorder captured it as a generic click/fill, but the real interaction needs specific handling" cases that WorkArena-style enterprise SaaS workflows are full of — build-order.md groups these as the second wave of work (right after conditional steps land), since they're each individually well-understood, mostly zero-token, ports of proven mechanisms rather than open research questions.
- **Business value:** each handler individually closes a specific, previously-identified failure mode that would otherwise either silently no-op (contenteditable, downloads, uploads) or escalate unnecessarily to paid LLM recovery.
- **Technical value:** these are the kind of "boring, deterministic engineering" fixes the research corpus repeatedly emphasizes as higher-leverage than any AI-based recovery improvement — 11 of these 12 items are zero-token.
- **Dependencies:** should follow EXEC-1 (some handlers, e.g. dismissing an interstitial before typeahead search, compose naturally with conditional steps); benefits from RT-2's recording-depth work to capture which handler a given step needs at compile time rather than inferring it at runtime.
- **Suggested order:** immediately after EXEC-1, per `build-order.md`'s own sequencing ("build second — depends on the verified floor").
- **Complexity:** L — each handler is individually small-to-medium, but there are 12 of them; treat as a batch of related PRs rather than one large one.
- **Success criteria:** each handler has a passing `runtime/test/` fixture demonstrating the specific failure mode it fixes (e.g., a Quill editor step that would previously no-op now succeeds); none of these require an LLM call.

### EXEC-9 — Dataset-grade telemetry: structure resolution/drift/repair events for future compile-time priors
- **Category:** Execution & Recovery
- **Description:** Redesign the runtime→cloud telemetry events (`runtime/tracker.js` → `tracking_routes.py`) so every element-resolution outcome is captured in a form that can later *train* the fleet durability dataset, not just alert on drift. Per resolved step, that means recording: which `IdentityBundle` signal engine won (testid / role / text / relational / structural), its `orthogonality_class`, how deep the fallback walk went before success, whether `stable_hash` matched, the app's `compat_fingerprint`, and — on repair — which signal class the *replacement* selector came from. Today's telemetry supports the detection/alerting half (the drift queue exists); this item is about making the events rich enough that EXEC-2's flywheel can learn "which signal classes survive which drift types, per app/framework" as compile-time durability priors.
- **Why required:** the durability dataset (EXEC-2, and `research-analysis/conxa-critical-analysis.md` item 9) is identified across the research corpus as Conxa's only structurally-uncopyable moat — it requires a fleet to exist, so neither a connector vendor nor a frontier lab can backfill it. But the dataset is only as good as the events feeding it, and **every run that executes before the event schema is dataset-grade is training data lost forever**. This is why the schema work is P2 even though the learning pipeline that consumes it (EXEC-2) is sequenced later behind BUILD-1.
- **Business value:** protects the primary compounding asset — and the primary acquisition asset (see PROD-13) — from the silent failure mode of "we had thousands of runs but only logged pass/fail."
- **Technical value:** small, well-bounded schema work now versus an unfixable data gap later; also directly improves the existing drift queue and PROD-11's health dashboard, which get richer signal for free from the same events.
- **Dependencies:** none upstream (deliberately decoupled from EXEC-2/BUILD-1 so capture starts before the learning pipeline exists). New event codes must be documented in `docs/Backend-Schema.md` per the doc-maintenance rules.
- **Suggested order:** early — ideally before meaningful customer run volume accumulates; sequence alongside or immediately after EXEC-1, since the new branch primitives will also need outcome events.
- **Complexity:** M — event schema design, `tracker.js` emission points at the resolver/recovery seams, ingest-side validation, and doc updates; no new learning code.
- **Success criteria:** every resolution outcome event carries winning signal engine, orthogonality class, fallback depth, stable-hash match, and compat fingerprint; a sample export can answer "which signal classes survived last month's drift events, per app" with a query rather than a code change; `docs/Backend-Schema.md` documents the new event codes.

### EXEC-38-UI — Studio UI for authoring a for_each loop body
- **Category:** Execution & Recovery / Builder
- **Description:** EXEC-38 shipped the `for_each` iteration primitive fully in the compiler and runtime, with read-only visibility in Human Edit (`StepEditorDTO.for_each_summary`/`.for_each_steps`), but no dedicated authoring control — a vendor can insert a `for_each` step and see its body, but can only edit `rows.container_selector`/`items`/`as`/`max_iterations`/`on_row_error` and the nested body through the generic patch mechanism, not a purpose-built UI. This is the same "backend done, editor control pending" gap `PROD-3-UI` already tracks for Strict Mode, entity-binding confirmation, and `compensation_skill`.
- **Why required:** without an editor surface, a vendor cannot practically build or adjust a loop body (wrap N steps, set the row selector, confirm the destructive body step's entity binding) without hand-crafting patches — the primitive is correct but not usable at scale.
- **Business value:** makes the shipped loop mechanism actually usable day-to-day for the bulk-processing workflows EXEC-38's business case targets.
- **Technical value:** `BranchBodyEditor.tsx` already solves most of this shape for `if_present` — a `for_each` variant needs a row-selector field, `max_iterations`/`on_row_error` controls, and the same nested-step insert/delete/reorder UX, reusing `insert_branch_step`/`delete_branch_step`/`reorder_branch_steps`'s pattern (parallel `for_each` RPCs, not yet built) rather than inventing new interaction design.
- **Dependencies:** EXEC-38 (done).
- **Complexity:** M — a for_each variant of `BranchBodyEditor.tsx`, matching structural-mutation RPCs (`insert_for_each_step`/`delete_for_each_step`/`reorder_for_each_step` in `workflow_mutations.py` + `handlers/workflow_editor.py`), and a row-selector/cap/on-row-error control panel.
- **Success criteria:** a vendor can build and edit a `for_each` loop — row selector, cap, on-row-error, and its nested body — from the Human Edit page without hand-crafting a patch.

### EXEC-43 — Finish the Conxa Execute → conxa-cloud merge: new Clerk client + decommission old infra
- **Category:** Execution & Recovery / Conxa Execute / Auth
- **Description:** The code side of folding Conxa Execute into conxa-cloud is done (2026-09-17): `conxa-execute/backend/` deleted, `execute_bridge_routes.py` deleted, BYOK removed from the desktop app, the `execute_chat` usage class + personal/team context switcher (`GET /api/v1/execute/contexts`) shipped, grant auto-claim by email replaces the invite-code UI, and the LLM proxy gained tool-calling passthrough for a real multi-turn chat client. See `docs/TRD.md` §3.6/§13.7 and `TODO.md` PROD-19's matching update for the full design.
- **Why required:** three manual, dashboard-only steps remain that no engineering session can do from this environment, and until they're done the app can't actually be tested end to end.
- **What's left:**
  1. ~~**Create a new Clerk OAuth client** under the existing `clerk.conxa.in` instance...~~ **Done 2026-09-17**: OAuth client `Pas7EoTMro1jNvR0` created under `clerk.conxa.in` (PKCE, no secret), redirect URIs `http://127.0.0.1:52841-52850/cb`, scope confirmed via that instance's own discovery document to already support `user:org:read` (and `org_id` in `claims_supported`) — no surprises. Wired into `electron-builder.yml`'s `extraMetadata` (`conxaExecuteClerkDomain: https://clerk.conxa.in`, `conxaExecuteClerkClientId: Pas7EoTMro1jNvR0`). **Still needed:** a real packaged build + sign-in test (step 4) — this has only been wired, not exercised end to end.
  2. **Decommission the abandoned `clerk.apis.conxa.in` Clerk instance** and its OAuth client (`pCbQVP1K15tVmQKP`) — created during EXEC-42's now-reversed fix, unused going forward.
  3. **Decommission the `conxa-execute-api` Render service and its `conxa-execute-db` Postgres instance** — the repo-side `render.yaml` is already deleted; the live Render resources still need deleting in the dashboard. No real production data exists to migrate (the first working-sign-in installer shipped the same day this was built).
  4. **End-to-end verification once (1) is done:** sign in as a full workspace member (should just work, no grant needed) and as a grant-only email (should auto-bind on first sign-in and never appear as a Build Studio member); confirm the personal/team switcher lists the right contexts; confirm tool-calling chat (`execute_skill` via the chat composer, not just the Form) actually runs a skill through the new proxy path — this hasn't been tested against a real Clerk token or a real LLM provider yet, only against unit tests with mocked transports.
- **Dependencies:** step 1 blocks step 4 entirely (nothing can sign in without it).
- **Suggested order:** step 1 first (blocking), 2 and 3 whenever convenient (cleanup, no urgency), 4 right after 1.
- **Complexity:** S — dashboard clicks plus one manual verification pass, no further code expected unless verification surfaces a bug.
- **Success criteria:** a real person can sign into a packaged Conxa Execute build, see their correct personal/team contexts, and successfully run a skill through the chat composer with the cost landing on the right workspace's AI Usage Credits.

### EXEC-44 — Outcome forks: follow-ups after the first release (popups that need a decision)
- **Category:** Execution & Recovery / Builder
- **Description:** The first release (2026-10-24, `docs/TRD.md` §11.4) turns a recorded decision popup ("file already exists: Replace / Keep both / Cancel") into an `outcome_fork` step with a per-state policy, asks the author about it in Clarify, can compare a second "no popup" recording with the first, pauses for a person on a risky or unrecognised popup, and refuses to auto-dismiss decision popups. Not built yet:
  1. **Native `alert`/`confirm`/`prompt` forks.** They need the dialog pre-armed before the triggering click (EXEC-29), so a native dialog still compiles to its own `dialog_accept`/`dialog_dismiss` step and is never forked.
  2. **Late popups.** A popup that opens after the fork's watch window (1.5–8 s) is only caught by the INTERCEPTED path on a later step, which now refuses to dismiss it. Keeping the fork's signature armed for the next two steps would catch it as a known state.
  3. **Insertable forks in Human Edit.** Forks only come from a recording; a vendor can't add one by hand or edit an option's steps in the UI (only `set_fork_policy`).
  4. **Learn forks from field data.** `fork_unknown` / `fork_paused` telemetry could suggest "this popup keeps appearing for customers — add it as a state" in the dashboard.
  5. **Popup screenshot on the Studio test card** (`waiting_fork`) and a second `fork_variant` offer when a workflow has several forks.
  6. **Server-level pause/resume test.** `_resumeForkStep` / the fork park in `runtime/app/server.js` is covered by `fork_pause.js` + `runForkStep` unit tests and the Studio driver tests, but not by an in-process `server.js` test (none exists for `_resumeReviewStep` either).
  7. **Real-app check.** Re-record a Google Drive upload with an existing file and run it through Claude Desktop with the file present and absent (the manual verification in the plan).
- **Why required:** the first release deliberately keeps the safe default (fail closed to a person) everywhere it is incomplete, which costs interruptions rather than correctness.
- **Dependencies:** item 1 depends on EXEC-29's dialog pre-arm; item 4 on the tracking/drift dashboards.
- **Complexity:** M overall; each item is independent.
- **Success criteria:** a workflow recorded against a popup that sometimes appears runs unattended on the paths the author approved, and every other path ends with a person's decision — never a silent Replace, Delete or Cancel.

### APP-1 — App maps: finish the learned map of each web app (phase 8)
- **Category:** Builder / Execution & Recovery / Cloud
- **Description:** Phases 1-7 shipped (2026-10-24, `docs/TRD.md` §11.5): every compiled workflow adds its screens, actions and decision popups to a data-free, per-host, per-workspace map kept in Build Studio, teammates share contributions through the cloud (`/api/v1/app-graphs`, no folding there), an **App maps** page in Studio draws them, Studio warns about popups other workflows met (a `known_popup` question after compile; a live "Heads up" line while recording), and a popups-only `app_knowledge.json` ships with each skill so a runtime recognises a popup nobody recorded and follows the author's standing policy (or asks a better question; a decision popup is still only ever answered by a person unless the author approved it). Runtimes also report scrubbed `app_obs` telemetry for popups they could not place, which comes back as "seen in real runs" counts and field-only popups. Agents also see which web app each workflow works in (`list_skills.app`, the discoverability note). Still to build (phase 8, gated): an opt-in, k-anonymous shared popup map across customers. Design: a workspace setting `share_app_maps` (default off, with legal copy); a periodic cloud fold of opted-in workspaces' **popup shapes only** (never screens, paths or workflow names) keeping a shape only when >=3 distinct workspaces report it, into KV `app_graph_shared`; Studio shows these as read-only "common in this app" hints that never become a fork or a policy on their own. Blocked on: a `docs/Security.md` re-review of SG-20, customer consent wording, and a decision on whether pooling needs a `docs/PRD.md` change (the PRD already calls the telemetry loop pooled execution metadata). Known limits so far: `app_obs` has no per-pack off switch yet (SG-20); the cascade-path report only fires when a decision popup blocked a click; consent/dismissable popups are not mapped, so `cascade.js` gets no known close button from the map (it still refuses to dismiss decision dialogs); the post-compile `known_popup` question cannot also offer "record the other case" (the variant flow needs an existing fork); the shipped file changes only when a skill is published again; phase 1: single-page apps that keep the URL while the view changes collapse into one screen (the compiled steps carry no page landmarks); `try_dismiss` and consent popups are not mapped yet; branch steps other than forks are skipped; one host = one app (no aliases such as docs.google.com ↔ drive.google.com).
- **Why required:** the knowledge in every recording is thrown away after the compile. The map is what lets a new workflow, a run, and an agent benefit from what earlier ones saw.
- **Dependencies:** phase 5 builds on EXEC-44's fork matcher; phase 8 needs a privacy review.
- **Complexity:** L overall; each phase is S-M.
- **Success criteria:** a second workflow recorded on the same app is warned about the popups the first one met, and a customer run that meets a known popup asks a better question than "unknown dialog".

### UPD-1 — True skill-pack cryptographic signing
- **Category:** Auto Updates
- **Description:** Today, only the runtime/app *update manifest* is Ed25519-signed (`manifest_signer.py`, verified by `runtime/manifest_manager.js`). Individual skill packs still rely on the bearer `sync_token` model (per-company, minted at publish time, embedded in `pack.json`) rather than a publisher signature over a per-pack Merkle manifest, as originally specced in the research corpus's `04-architecture/07-skill-pack.md` Part 2.
- **Why required:** the sync token is a read-only, single-company-scoped bearer secret — reasonable as a low-severity accepted tradeoff (see `docs/Security.md` SG-08, `docs/TRD.md` §17), but it's not a cryptographic signature, meaning skill-pack authenticity/integrity relies on transport auth rather than a verifiable publisher signature the way the update manifest now does.
- **Business value:** strengthens the supply-chain story for the distributable skill-pack artifact, which the research corpus identifies as a real differentiator (a signed, versioned, licensable artifact competitors built around live agents don't have an equivalent to).
- **Technical value:** brings skill-pack integrity guarantees up to the same standard already established for the runtime/app update manifest, closing an inconsistency between the two.
- **Dependencies:** benefits from BUILD-1 (Compiler IR) for a natural per-pack content hash to sign over, though it isn't strictly blocked by it.
- **Suggested order:** can proceed independently of BUILD-1, but is more natural once a CIR-based content hash exists.
- **Complexity:** L — signing key management, a per-pack Merkle manifest format, and updating both the publish flow and the runtime's verification path.
- **Success criteria:** a published skill pack carries a publisher signature the runtime verifies before installing, independent of (in addition to) the existing bearer sync-token auth.

### CLOUD-2 — Materialize dashboard aggregates instead of re-scanning every run per request
- **Category:** Cloud / Performance
- **Description:** `app.services.tracking._visible_run_records` loads **every** run's full event list out of KV on every dashboard request, and every aggregate is derived from that list in-process. The 2026-08-07 operations-dashboard redesign was built to pay this cost exactly once per request (`tracking_analytics.dashboard` performs the single scan and threads the records into `_dashboard_metrics` via its `records` parameter), so it did not regress today's latency — but the cost is O(all telemetry ever ingested) and grows without bound. The fix is a daily rollup written at ingest time (or on a schedule) that the dashboard reads instead, falling back to the live scan for the current partial day.
- **Why required:** the redesign puts far more on the dashboard than the old page did, and the whole surface is now the post-login landing page, so this scan runs on every session. At a few thousand runs it is fine; at a few hundred thousand it becomes the slowest thing in the product, on the screen customers see first.
- **Explicitly not the fix:** a short-TTL in-process memo over `(workspace_id, range)`. It was considered and rejected — the dashboard's Refresh control invalidates the query and refetches, so a TTL cache would hand back byte-identical data and make an explicit user action silently do nothing. Any cache here has to be invalidated by ingest, not by a timer, or bypassed on explicit refresh.
- **Business value:** keeps the first screen after login fast as customer run volume grows, which is precisely when the dashboard matters most.
- **Technical value:** removes the only unbounded-cost read path in the cloud; also makes longer ranges (90d, and eventually year-over-year) viable, which the current scan makes progressively more expensive.
- **Dependencies:** none. Benefits from EXEC-9's richer events landing first so the rollup schema is designed once.
- **Suggested order:** before the first customer with sustained daily run volume; not urgent at pilot scale.
- **Complexity:** M — rollup table/namespace, write path at ingest, read path with partial-day fallback, and a backfill for existing telemetry.
- **Success criteria:** dashboard response time is flat with respect to total ingested runs; the live scan is used only for the current day's partial bucket; Refresh still returns genuinely fresh data.

---

### CLOUD-3 — Real enforcement for Free's 1-install cap — Reopened 2026-08-09
- **Category:** Cloud / Billing
- **Status:** the 2026-08-09 fix (stamping `pack.json.build_machine_id` and having `runtime/skill_loader.js` + `entitlements.ensure_delta_sync_allowed` refuse to run/update a Free-tier install anywhere but its original build machine) was removed the same day — it was blocking legitimate Free-tier use (e.g. reinstalling on a repaired machine, moving to a new laptop). No install-destination enforcement exists again; this is back to open. The separate per-workspace *build*-machine quota (`ensure_machine_slot`, Free=1) is unaffected and still enforced on the LLM proxy / compile-credit path — that limits how many machines can build for a workspace, not how many machines a built installer can run on, so it doesn't substitute for this item.
- **Original description (for context):** `docs/PRD.md` §11 says Free is capped at 1 install; nothing enforces it. The obvious hook, `skillpack_update_routes.py::post_telemetry_runtime_start`, is deliberately public and unauthenticated (its own docstring: "spoofing inflates counts but leaks nothing") — hard-blocking installs there on a spoofable `install_id` would be a false security control, not a real one (see `docs/Security.md` SG-15 for the related machine-fingerprint discussion). Any future fix here needs a way to enforce the cap without punishing legitimate reinstalls/machine swaps — the naive machine-hash-lock approach tried and reverted on 2026-08-09 didn't distinguish the two.

### CLOUD-4 — Write-side telemetry retention pruning
- **Category:** Cloud / Storage
- **Description:** Analytics retention (`analytics_retention_days` per plan) is enforced read-side only (`app/services/tracking.py`, filtering `_visible_run_records`/`_visible_runtime_registrations`) — see `docs/Security.md` SG-16. Raw telemetry past a workspace's retention window still exists in `tracking/{company}` KV; nothing deletes it. Add an amortised prune (on ingest, or a periodic sweep) that deletes rows past the longest contractually-possible retention window.
- **Why required:** read-side filtering is correctness-complete for the customer-facing "you can't see data older than N days" promise, but not a real deletion guarantee, and storage grows unbounded.
- **Dependencies:** ~~the prune needs the *publishing workspace's* plan, and there's no cheap workspace-id-keyed billing read today (`billing_for()` takes a `Principal`, not a bare workspace_id) — solve that lookup first, or accept a periodic batch job that resolves it per company.~~ Unblocked 2026-08-09: `app/services/saas.py::billing_for_workspace(workspace_id)` now exists (added for the delta-sync distribution gate, see resolved CLOUD-3) — reuse it here instead of adding a second lookup.
- **Complexity:** M.

### CLOUD-5 — Bedrock and Vertex BYOK
- **Category:** Cloud / LLM
- **Description:** Enterprise BYOK currently supports only Azure OpenAI (`app/services/byok.py`, `docs/TRD.md` §13.9), chosen first because it's OpenAI-compatible and reuses the existing router call path (`_is_openai_compatible_endpoint`/`_chat_completions_url`, extended to accept a pre-built `.../chat/completions` URL). AWS Bedrock (SigV4 auth, Converse API) and GCP Vertex AI (Google auth, `generateContent`) each need their own request/response translation layer and auth scheme — a materially bigger lift than Azure was.
- **Why required:** AWS-first and GCP-first enterprise accounts can't use BYOK today.
- **Dependencies:** none, but should follow the same `PoolEntry`/`call_entry_directly` pattern Azure established rather than inventing a parallel path.
- **Complexity:** L each (Bedrock and Vertex), mostly in the provider-specific request signing/translation.

### CLOUD-8 — Update pricing page copy for Starter's external distribution
- **Category:** Cloud / Frontend
- **Description:** `PLAN_LIMITS["starter"].distribution` flipped from `"internal"` to `"external"` on 2026-08-09 (see resolved CLOUD-3) — Starter can now ship to unlimited outside customer machines, throttled only by its existing 200 compile-credit ceiling. `conxa-cloud/frontend/src/components/marketing/sections/PricingTable.tsx` still badges only Pro as "Distribution channel" (`TIER_SUB`/highlight logic, lines ~10-42) and Starter's sub-copy ("One product team") doesn't mention it at all — the pricing page now undersells what Starter actually includes.
- **Why required:** pricing copy that undersells a paid tier's real capability is a lost upsell/retention argument, not just a cosmetic gap.
- **Dependencies:** none — copy-only change, the entitlement is already live.
- **Complexity:** S.

### CLOUD-7 — Server-side build-queue priority
- **Category:** Cloud / Architecture
- **Description:** The pricing sheet promises a tiered build queue (Standard/Priority/Highest/SLA-backed). `app/services/jobs.py::enqueue_job` is the only place that could host a priority queue, but it has zero callers today — nothing in the codebase actually queues a server-side build job; compiles happen client-side in Build Studio, and the cloud's LLM proxy answers each request synchronously. Adding a priority queue to `jobs.py` as-is would be dead code with no observable effect. This item is really "design and wire an actual server-side job producer" — priority ordering is trivial once that exists.
- **Why required:** the pricing page's "Build speed: Standard / Priority / Highest / SLA-backed" row is currently aspirational, not enforced.
- **Dependencies:** needs a real decision about what, if anything, should move server-side (see CLOUD-21 for related queue/job-infrastructure thinking already in this backlog).
- **Complexity:** L — mostly architectural decision-making, not the priority-queue mechanics themselves.

---

---

### EXEC-22 — Durable, resumable execution state
- **Category:** Execution & Recovery / Architecture
- **Description:** Run state that survives beyond the lifetime of a single MCP call and a single parked browser — enough to pause a run at a human review point, persist what has happened so far, and resume it later (potentially after the browser has closed, potentially on a different invocation) with the workflow's inputs, outputs, step results, and position intact.
- **Why required:** `docs/PRD.md` §14.4 lists execution state as a current Horizon 1 foundation. Today the runtime has per-run status and the park-and-resume machinery that keeps a live page alive across two MCP calls — which is sufficient for a recovery round-trip measured in seconds, and insufficient for a human review that waits for someone to come back from a meeting. It is also the thing that makes a work item (ARCH-5) reportable rather than just dispatchable.
- **Business value:** without it, "a person approves this step" is capped at "a person approves this step right now, while the browser is open." That is a materially weaker promise and it is the one an ops team will test first.
- **Technical value:** replaces the implicit assumption that a run is an in-memory object with an explicit, inspectable record. Prerequisite for reporting on in-flight work, and for any Horizon 2 queue, which cannot dispatch or retry what it cannot describe.
- **Dependencies:** EXEC-21 is the first real consumer. `PARK_TTL_MS` and the park-divergence check define the boundary of what today's mechanism can do and should be read first. Note the hard constraint that a durable run cannot durably hold a live browser page — resuming after a long pause means re-establishing page state, not restoring it.
- **Suggested order:** alongside or immediately after EXEC-21's attended case; required before its unattended one.
- **Complexity:** L.
- **Success criteria:** a run pauses at a review point, its state persists across the browser closing, and it resumes to completion afterwards with all prior step outputs intact; an abandoned run is reaped on a defined policy rather than accumulating; in-flight runs are enumerable and inspectable.

### ARCH-5 — Work-item abstraction: make a unit of work addressable, not just a workflow invocation
- **Category:** Architecture / Product Strategy
- **Description:** Model the thing a run is *about* — one invoice, one claim, one onboarding — as an addressable unit with its own identity, state, history, and outcome, distinct from the workflow invocation that happens to be processing it. Today a run is a workflow plus inputs; there is no concept that survives across retries, spans more than one skill, or can be counted as a business outcome rather than a technical execution.
- **Why required:** `docs/PRD.md` §14.4 lists it as a current Horizon 1 foundation, on the grounds that retrofitting it later means rewriting telemetry, reporting, and the human-review model simultaneously. It is the unit a Horizon 2 queue distributes, the unit a reviewer acts on, and the unit Horizon 3 counts.
- **Business value:** it is the difference between reporting "we executed 4,000 steps" and "we processed 300 claims, 12 of which needed a person." The second is the sentence an operations buyer renews on; the first is telemetry.
- **Technical value:** gives telemetry, review routing, and future queueing one shared identity to key off, instead of three subsystems each inventing their own correlation id later.
- **Dependencies:** pairs with EXEC-22 (a work item needs durable state to have a history) and EXEC-9 (telemetry has to carry the identity for it to be worth anything). Do not build a queue as part of this — the queue's placement is an open decision (PROD-20); the abstraction is what makes a later queue possible without a rewrite.
- **Suggested order:** after EXEC-21/EXEC-22 have established what a run's state actually looks like — modelling the unit before the state is settled risks modelling it wrong.
- **Complexity:** L — mostly data-model and telemetry surface rather than new execution machinery, but it touches every subsystem that currently reasons in terms of runs.
- **Success criteria:** a single business unit of work is identifiable end to end across retries and across more than one skill; the dashboard can report outcomes per work item, not only per execution; the identity is present in telemetry, in review routing, and in the audit trail.

### ARCH-6 — Process-level metadata and the workflow dependency graph
- **Category:** Architecture / Cloud / Product Strategy
- **Description:** Two coupled foundations from `docs/PRD.md` §14.4, tracked as one item because building either alone is half-useful. First, a **process** concept sitting above skills: a business activity that may span several skills, several people, and several systems, carrying its own name, owner, and description — as distinct from a skill, which is an artifact. Second, **recorded relationships** between workflows: which feeds which, which blocks which, which must complete before which. Today `execute_sequence` can run skills in order, but the order encodes no dependency the system can reason about, so no graph can be reconstructed from it afterwards.
- **Why required:** Horizon 3 (`docs/PRD.md` §14.3) reasons about processes, bottlenecks, ownership and dependencies. Every one of those is a graph question over relationships that are either recorded at the time or lost. Bottleneck and impact analysis in particular cannot be built retrospectively from execution logs that never captured what depends on what.
- **Business value:** "which process is our bottleneck", "what breaks if this one fails", and "who owns this" are the questions that make an operational-intelligence layer worth buying. None are answerable from skill-level data alone.
- **Technical value:** low cost now, unrecoverable if skipped — this is metadata capture, not new execution behaviour, but it can only be captured while someone still knows the answer.
- **Dependencies:** ownership and versioning already exist (publishing, release channels, RBAC, audit log) and should be reused rather than duplicated. Relates to ARCH-5 — a work item flows through a process, which is what makes throughput measurable.
- **Suggested order:** the process/ownership metadata half is cheap and can land early; the dependency graph should follow ARCH-5 so that dependencies are expressed between things that have stable identities.
- **Complexity:** M for the metadata half, L for the dependency graph and its authoring surface.
- **Success criteria:** a workspace can group skills under a named process with a stated owner; dependencies between workflows are recorded explicitly rather than implied by execution order; the recorded graph is queryable and demonstrably reconstructs an ordering the system was not told at run time.

### PROD-20 — `[DECISION]` Horizon 2 / Horizon 3 open questions — **4 of 6 decided 2026-08-21; 2 remain**
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** Six unresolved decisions recorded in `docs/PRD.md` §14.5, each blocking real design work and none answerable by engineering alone. (1) **Where Horizon 2's execution workers run** — customer-owned workers preserve the three properties the business rests on (the cloud never executes, execution costs us nothing, credentials never leave the machine); Conxa-hosted execution breaks all three. (2) **Where the work queue lives and what a work item may contain** — cloud-hosted with business payloads is easier to operate and richer for Horizon 3, and is the same question as whether the data-locality promise survives. (3) **Whether "pay for reach, not for runs" survives Horizon 2** — a queue at scale would be the first thing in this business with a genuine marginal cost. (4) **What data the Horizon 3 intelligence layer receives** — execution metadata plus company-stated context, versus work-item content, versus a customer-hosted deployment. (5) **Unattended session lifetime** — see PROD-4, which is the engineering half of this. (6) **Whose operations Horizon 3 describes in a Rung 3 relationship**, when a vendor or consultancy distributes skills into thirty other companies.
- **Why required:** these are recorded as open in the PRD precisely so nobody resolves them by inference while building something else. Question 1 in particular would be decided *de facto* by the first engineer who builds a scheduler in the wrong place.
- **Business value:** questions 3 and 6 are commercial, not technical, and both affect what can be sold and to whom. Question 6 has contractual consequences at the highest-leverage rung in the ICP.
- **Technical value:** questions 1, 2 and 4 each determine whether the current security answer — the one that clears bank and insurer security reviews — survives into the next horizon. That is the property the whole product currently rests on, so deciding these accidentally is the expensive outcome.
- **Dependencies:** PROD-4 (session keeper / long-lived sessions) and PROD-5 (standalone launcher, scheduler, parallel execution, runner-machine profile) are the two existing backlog items that are already Horizon 2 foundations, written before the horizon framing existed. Question 5 is largely PROD-4's scope; question 1 is largely PROD-5's. Neither should be scoped further without an answer to the corresponding decision here.
- **Update 2026-08-21 — questions 1, 2, 3 and 4 are decided, by a single doctrine:** *Conxa ships capability to where the work and the data already are; the cloud holds neither.* Concretely: Horizon 2's **execution workers and work queue run on customer-owned infrastructure** and Conxa does not hold work items; **"pay for reach, not for runs" therefore survives every horizon** (`docs/PRD.md` §11 now carries the reach-generalises table); and the **Horizon 3 intelligence layer deploys on the customer's own infrastructure** — staged so stage one is a structured operational data model queried by a general model on ordinary server hardware, with a fine-tuned per-company model as stage two, across three customer-owned deployment targets (ordinary hardware, the customer's own cloud tenancy, on-premise GPU). Retraining is orchestrated by the Conxa platform and executed on the customer's infrastructure. Recorded in `docs/PRD.md` §14.5 under *Decided*. **Engineering consequence: do not design a Conxa-hosted scheduler, queue, or analytics store.** PROD-5's runner-machine profile is now the sanctioned direction rather than one option among several.
- **Still open (2 of 6):** question 5, **unattended session lifetime** — the single hardest unsolved problem between Horizon 1 and Horizon 2, and largely PROD-4's scope; and question 6, **whose operations Horizon 3 describes in a Rung 3 reseller relationship**, which the local-deployment decision narrows (the data now sits inside each end customer's infrastructure, so "the distributor sees all thirty" would have to be deliberately built) without answering.
- **Suggested order:** question 5 next — it gates PROD-4 and PROD-5 and is now the only technical blocker left between the two horizons. Question 6 can wait until a Rung 3 account actually asks.
- **Complexity:** n/a — founder/maintainer decisions, not engineering tasks. Each one should end with a written position in `docs/PRD.md` §14.5 replacing the open question.
- **Success criteria:** each of the six questions has a recorded answer (or an explicit, dated deferral with a reason) in `docs/PRD.md` §14.5, and any backlog item gated on one names which.

### BUILD-25 — Compile-time semantic pass: one model layer for the edge-case families hand-written rules keep losing to
- **Category:** Builder
- **Status (2026-09-10):** stages (a), (a2), (b), (b2), (c), (d) and (e) are done; only (f) is open.
  - **Shipped —** the reviewer edit log (`editor/edit_log.py`, one JSON line per changed field under
    `data/skills/{id}/edits.jsonl`, keyed on the new `compiler/step_key.py` identity key, never
    `step_index`); the eval harness (`conxa-cloud/scripts/eval_suggestions.py`); the second
    whole-workflow LLM call (`llm/workflow_semantics.py`, sited after `_deduplicate_input_bindings`
    and `_build_compile_report`, gated by `SKILL_LLM_SEMANTIC_SUGGESTIONS_ENABLED`, sharing the new
    `llm/llm_cache.py` that `workflow_intent.py` was migrated onto) deciding `rename_binding`,
    `parameterize_literal`, `suggest_optional`, `label_phase`, and now (stage d) `suggest_assertion`
    and `flag_noise`; the payload improvements of stage (b2) — uncertainty routing off the per-step
    `confidence` the report already computes, plus sibling-workflow binding-name context; and, in
    place of (c), the applier (`compiler/second_opinion.py`) that **writes those findings onto the
    compiled steps**. Shapes are documented in `docs/TRD.md` §7.1/§7.3 and `docs/Backend-Schema.md`
    §3.9/§3.9a/§3.10.
  - ~~**(c) Human Edit accept/reject UI**~~ — **dropped 2026-09-09.** The pass now applies its
    findings instead of offering them, and Human Review is itself the gate: a reviewer opens the
    workflow, sees `sender_email` rather than a chip offering to rename `email_2`, and edits a wrong
    call the same way they edit any other compiler output. Nothing in the renderer marks a
    second-opinion change — no badge, no pane, no provenance. **Zero renderer files changed.** What
    this cost is written down in "Risk / the line that moved" below, because it is not free.
  - ~~**(d) `suggest_assertion` and `flag_noise`**~~ — **done 2026-09-10.** `suggest_assertion`
    additively appends one advisory (`required=False`) assertion, restricted to
    `text_present`/`text_absent`/`url_changed`/`url_pattern`/`state_changed` (never a selector-bearing
    type — that `target` is a raw Playwright selector this pass may never write) and grounded in text
    that already appears somewhere in the workflow's own payload, never invented. `flag_noise`'s
    destructive-kind question resolved as **archive, not delete**: the step is removed from what ships
    but its full data is saved under `compile_report["archived_steps"]` (`compiler/second_opinion.py::
    archive_flagged_steps`) so it can be restored later — never a silent, unrecoverable loss of a
    step's IdentityBundle. Gated on the recorder's own `post_condition.classified_effect == "none"`,
    never the model's opinion alone.
  - ~~**(e) a reader for `label_phase`**~~ — **done 2026-09-10.** Wired into the Tier B recovery
    prompt only (`runtime/app/failure_response.js::phaseHintBlock`, fed by `handlers.js::
    enrichStepsWithRecovery`'s `_phase`) — the smallest real consumer, since the agent-recovery
    payload already carries full step data. The other three listed consumers (BUILD-27 skill
    descriptions, BUILD-26 review triage, EXEC-32 `resume_from`) remain separate, untouched backlog
    items.
  - **Open —** (f) `group_steps`, deliberately last because it carries the steps↔events alignment
    risk described below.

- **Description:** several compiler passes have converged on one failure shape — a hand-written rule
  with hand-tuned constants that works on the recordings it was written against, breaks on the next
  unfamiliar widget or page, and leaves the difference for a human to fix in Human Review. Adding
  another constant or another branch has stopped paying, because in every case the rule is not too
  *simple*: it is looking at **too little of the recording to decide correctly**. The fix is one
  compile-time model pass that sees the whole workflow at once and emits *suggestions*, leaving every
  existing deterministic pass exactly where it is.

  **The six problem families.** The first four were found together; families 5 and 6 were added
  2026-09-09 and are the same shape, so this list is explicitly not assumed complete.

  1. **De-duplication and leftover noise.** `conxa_compile/pipeline/dedupe.py` has three rules — drop
     a `focus` superseded by an action on the same element within `_FOCUS_LOOKAHEAD = 3` events;
     collapse the click/type noise bracketing a `select` within `_SELECT_NOISE_LOOKAROUND = 2` events
     either side; drop consecutive identical scrolls. Both lookaround constants were derived from a
     single react-datepicker recording (that file's own docstring says so). Anything outside those
     three shapes — a stray click that opened nothing, a mis-click and its correction, an accidental
     scroll-then-scroll-back — survives into the compiled workflow for a reviewer to delete by hand.
     The recorder already captures the evidence needed to judge this (`state_change.dom_diff`,
     `post_condition.classified_effect`, including the literal value `"none"`) and no pass currently
     reads it for noise removal.
  2. **Hardcoded values that should be variables.** `conxa_compile/compiler/input_binding.py` walks a
     fixed ladder — `label_text` → `placeholder` → `aria_label` → value regex (email, phone, URL, ISO
     date, digits) → `semantic.input_type`. Every signal on that ladder is **local to a single
     field**, so at the moment it names a field it cannot see any other field in the workflow. Two
     email inputs therefore cannot become `sender_email` and `recipient_email`; they collide, and
     `build.py::_deduplicate_input_bindings` papers over it with `_2`, `_3`. Separately, a typed value
     the ladder finds no name for stays frozen as the literal recorded text — a customer name, an
     amount, a reference number — and the reviewer must notice and bind each one by hand. Deciding
     *whether* a value should vary at all requires the workflow's purpose, which no field-local signal
     carries. **This is the clearest case of the six: no additional local rule can fix it, because the
     required information is workflow-global.**
  3. **Date pickers, and too many of them.** `conxa_compile/compiler/date_picker.py` is 378 lines
     handling one widget family, and its docstring enumerates the vendors it was built against (MUI,
     react-datepicker, flatpickr, Ant Design, jQuery UI). That enumeration is the tell: widget #6
     needs a code change and a release, and until then a reviewer stitches raw day-cell clicks
     together by hand. Two live instances are already tracked as **COMPILE-1** (an orphaned raw
     day-click when the same field is picked twice) and **COMPILE-2** (`_findAnchoredField` misses
     react-datepicker's DOB field, silently degrading `typed_first` to `grid_only`). Both are real
     bugs; neither is the last of its kind.
  4. **Hover-step noise.** Hover capture is gated three ways and still over-produces: `bridge.js`'s
     smart-hover path is opt-in per recording, waits a `hover_dwell_ms = 400` dwell, and diffs a
     before/after actionable-element signature (limits 160 / 60 / 80) to decide whether the hover
     revealed anything; `pipeline/run.py::_drop_non_actionable_hover_events` then drops any hover
     whose bounding box is under 2px. What survives is still noisy, and the consumer makes it worse —
     `compiler/action_semantics.py::detect_hover_precondition` inspects only the **immediately
     preceding** event, so a mouse traverse that fires several hovers before a click contributes one
     hover-chain hint and leaves the rest as standalone steps. Related to but distinct from
     **EXEC-29a**: that is hover-reveal steps failing at live replay, this is a recording that
     contains steps which were never intentional.
  5. **Optional and stochastic steps — the cheapest family, and the one shipped first.** The recorder
     already classifies steps it saw behave non-deterministically: `build.py` (~line 1545) carries
     `optionality == "stochastic"` and the recorder's `branch_hint` onto the step **advisory only** —
     it changes no compiled behaviour, and only a human confirming in Human Edit
     (`editor/workflow_mutations.py::confirm_optional_interstitial`) turns it into a real
     `try_dismiss` branch. So a hint stream already exists with no automated judgment behind it, and
     the human approval gate already exists too, which makes this the one suggestion kind needing no
     new approval UI. The decision is workflow-global by nature: *"this consent banner appeared in 1
     of your 3 recordings of this app and never after login — almost certainly optional"* is not
     something a field-local or single-recording rule can conclude.
  6. **Assertions whose real post-condition lives downstream.** `compiler/validation_planner.py` plans
     a step's success condition from FINAL_INTENT plus policy plus state diff, all evaluated at that
     step. But a step's true post-condition is frequently only visible later: if step 13 clicks
     something that exists only after step 12 saved, then step 12's real assertion is *that element
     appearing*. A per-step planner structurally cannot see it. Overlaps **BUILD-26**'s
     assertion-coverage audit, which is the review-time half of the same gap; this is the
     compile-time half, and the two must share one suggestion format.

- **What the pass writes.** `llm/workflow_semantics.py` decides; `compiler/second_opinion.py`
  applies. Each finding is `{step_key, kind, current, proposed, why}`, and the ones actually applied
  are recorded in `compile_report["second_opinion"]` as an audit trail for the compile log — nothing
  in Human Edit reads it. The four live kinds map onto exactly four surfaces of a compiled step:
  `input_binding`, `{{placeholder}}` tokens inside `value`, `phase`, and the `try_dismiss`
  `branch`/`intent`/`recovery`. Never `target`, `identity_bundle`, `compiled_selectors`,
  `validation`, or the frame/tab chain.

  | Kind | Solves | Risk if wrong | Status |
  |---|---|---|---|
  | `suggest_optional` | family 5 | a required step becomes a best-effort branch; no un-confirm command, so undoing means editing the step back by hand | applied (b/c) |
  | `rename_binding` | family 2 | a worse name, edited at review | applied (b/c) |
  | `parameterize_literal` | family 2 | a spurious input the shipped skill demands of the end customer, edited at review | applied (b/c) |
  | `label_phase` | cross-cutting (see below) | none — advisory only | written (b/c); read by the Tier B recovery prompt (e) |
  | `suggest_assertion` | family 6 | none — always advisory (`required=False`), never a selector type | applied (d) |
  | `flag_noise` | families 1 and 4 | none — archived, never a silent unrecoverable delete | applied (d), archive-not-delete |
  | `group_steps` | family 3 | **steps↔events desync** | open (f), deliberately last |

  Two of these need their reasoning stated rather than inferred:

  - **`group_steps` is last because grouping implies collapsing.** It *labels* a run of steps as one
    semantic unit so the existing `collapse_date_picker_runs` executor knows where to look, instead of
    teaching the collapse pass about a sixth vendor. But collapsing touches the steps↔events 1:1
    alignment that `date_picker.py` and `upload_binding.py` both depend on, and that has already
    produced one silent desync bug (**BUILD-22** / **BUILD-23**).
  - **`label_phase` is worth its own kind for what it unlocks elsewhere.** Every step in a compiled
    package is currently equal. A model with the whole workflow in view can label runs of steps as
    *login / navigate / act / verify / cleanup*, and that one annotation feeds four things already
    wanted independently: skill and input descriptions (**BUILD-27**), Human Review triage
    (**BUILD-26**), sensible resume points (`resume_from`, **EXEC-32**), and a materially better
    recovery prompt — "this step is in the login phase" is a strong prior for the agent tier. **The
    recovery-prompt reader shipped 2026-09-10** (stage e, `failure_response.js::phaseHintBlock`);
    BUILD-27/BUILD-26/EXEC-32 remain separate, unimplemented consumers of the same label. Labelling
    only, and deliberately distinct from `group_steps`: nothing downstream *acts* on a phase
    label, so it carries none of the alignment risk.

- **Which kind of model — and what is explicitly not proposed:**
  - **Prompted LLM pass — the chosen vehicle, and it already existed.**
    `llm/workflow_intent.py::build_workflow_intent_graph` was already one call per compile with a
    whole-workflow view, a compact payload (`build.py::_intent_graph_inputs` — action, target text,
    URL, heuristic hint; no DOM, no screenshots), local caching keyed on a content hash, and a
    graceful empty-on-failure path back to the rules-only route. Bindings do not exist at the point
    that call runs (it fires *before* the per-step loop), so the semantic pass is a second call sited
    after `build.py::_deduplicate_input_bindings`, reusing the same payload shape. No new prompt
    infrastructure, no new cost line, no new failure mode.
  - **Fine-tuned model — not yet, and blocked on data.** The supervised pair is (compiled package →
    human-corrected package), and that before/after diff was logged nowhere until stage (a). Capture
    it regardless of whether a fine-tune ever happens: it is the eval set for the prompt above, and
    without it there is no way to tell whether a prompt change made suggestions better or worse. This
    is the compile-time twin of the runtime recovery-data capture tracked as step 2 of **PROD-19**,
    with the same property — a month not captured is a month of training data permanently lost.
  - **Classical ML or a neural net over hand-built features — not recommended.** The features that
    matter here are exactly the semantic ones (what is this workflow *for*; is this value a constant
    or a parameter) that a feature-engineered model cannot see and a language model reads natively.
    Revisit only if the LLM pass proves too slow or too expensive per compile.

- **How the pass is fed** — two cheap changes, both shipped in (b2), both of which raise its ceiling:
  - **Sibling-workflow context.** `_intent_graph_inputs` described one workflow. Adding a compact
    summary of what the workspace's *other* workflows for the same domain named their bindings turns
    family 2 from a guess into a lookup — `customer_email` because four other workflows already call
    it that, not `email_2`. This is the compile-time twin of **BUILD-26**'s cross-workflow consistency
    check, and the compile-time version is strictly better because it fixes the name before a reviewer
    ever sees it.
  - **Routing by the compiler's own uncertainty.** `confidence/uncertainty.py` already computes
    `is_top_two_ambiguous` and `is_score_below`, so the compiler already knows which steps it was
    least sure about. Handing the model that ranked set as "look here first", rather than an
    undifferentiated step list, makes the call cheaper, faster and more precise — the difference
    between a pass that scales to a 40-step workflow and one that does not.

- **Multi-model routing per suggestion kind (open; sequenced after a precision baseline exists).**
  `workflow_semantics.py`'s call is text-only — no screenshots, no DOM — which makes this a
  cost-routing problem, not a capability problem, and the kinds do not all need the same model.
  - **Cheap tier:** `suggest_optional` and `flag_noise` are the highest-volume, lowest-risk kinds.
    They fire on ordinary shape (a stochastic step, a no-op action) and the cost of a wrong suggestion
    is a reviewer clicking reject once. Route them to the cheapest text model in the pool (Groq's
    Llama tier already in `router.py`, or a Kimi text-only call once added). This is most of the call
    volume at compile time and the one place a cheaper model directly moves the cost-per-compile
    number in `docs/cost_model.md`.
  - **Strong tier:** `rename_binding`, `parameterize_literal` and `group_steps` need the harder
    judgment — workflow-global naming, and for `group_steps` the alignment call flagged above. Keep
    these on the strongest available text model regardless of cost. A wrong `group_steps` proposal is
    the one that can desync steps↔events, which is exactly where "optimise for precision, not recall"
    must not be traded away for a cheaper model.
  - **Mechanism, not new infrastructure.** `conxa_core.llm`'s router is already keyed by task name
    (see `llm/client.py`'s task clients); this needs only a model override per suggestion kind at the
    call site in `workflow_semantics.py`, the same pattern `router.py` uses to pool Groq, Google AI
    Studio and NVIDIA NIM. Adding a provider is a router registration plus a model-name string.
  - **Sequencing.** Do this only once the eval harness has a baseline from the single-model pass.
    Splitting models before there is a precision number to compare against makes a prompt regression
    impossible to attribute.

- **Three design requirements that are cheap now and unfixable later:**
  - **Suggestions must be keyed on something stable, not `step_index`.** Inserting one step renumbers
    every step after it — the positional fragility that already produced one silent desync bug
    (**BUILD-22** / **BUILD-23**). Accept a suggestion, recompile, and a `step_index`-keyed log can no
    longer say which step it applied to. Key on the step's identity fingerprint instead (this is what
    `compiler/step_key.py` exists for). Getting this wrong does not break the pass; it quietly ruins
    the dataset the pass exists to produce.
  - **Ship the eval harness alongside the log, not after it.** The before/after log is the dataset; a
    replayable set of N recordings with known human corrections is the *scoreboard*. Without the
    second, "did that prompt change help?" has no answer and iteration becomes guesswork.
  - **Failure, cost and silence behaviour must be stated, not inferred.** This is a second LLM call
    per compile and follows `llm/workflow_intent.py`'s pattern exactly: local cache keyed on a content
    hash, graceful empty-on-failure back to the rules-only route, compile completes byte-identically
    when the provider pool is drained. Equally, **"no findings" must be a first-class and common
    outcome.** A pass that always finds something to write is worse than one that usually finds
    nothing — now literally so, since what it finds is applied rather than offered. Optimise for
    precision, not recall; `_validate_findings`'s six gates are the trust boundary and every one is
    load-bearing.

- **Explicitly out of scope — decided, so nobody adds them by analogy:**
  - **Waits and timing** (`compiler/intent_validation_rules.py`, `compiler/wait_for_shape.py`). These
    look like the same shape of problem and are not. Timing is where a policy beats a guess, and a
    model proposing wait conditions produces intermittently-failing workflows — the worst failure mode
    available, because it presents as a flaky site rather than a bad compile.
  - **Step reordering** (`compiler/dependencies.py`). Ordering is execution semantics, not meaning. A
    wrong reorder also fails in a way that looks like a site problem rather than a compiler problem,
    and the premise of this item is that the model annotates meaning and never writes the program.

- **Why required:** Human Review is the only thing standing between a compiled workflow and these six
  defects, and it is manual, unmeasured, and repeated on every single recording. It is the largest
  remaining per-workflow labour cost in the product, and it scales linearly with customers.
- **Business value:** the vendor's time-to-first-working-skill is the whole onboarding funnel. Every
  minute a person spends renaming `email_2` or deleting stray hover steps is a minute where the
  product looks like a recording tool with homework attached rather than a compiler. It is also the
  single most visible quality signal in a demo.
- **Technical value:** stops the "add another constant per widget" treadmill in four places at once,
  and — because each suggestion is a diff a human accepts or rejects — produces the labelled
  correction data that every later version of this (including a fine-tune) needs, as a side effect of
  normal use.
- **Risk / the line that moved (2026-09-09).** The original line was "the model **proposes**, the
  reviewer **disposes**" — the package byte-identical whether the call succeeded, failed, or was
  disabled. That line was deliberately moved: the model now writes, and **Human Review disposes**.
  Four things this costs, stated so nobody rediscovers them as surprises:
  1. **Two compiles of the same recording can differ.** The content-hash cache keeps repeat compiles
     of the same recording stable, but a drained provider pool produces the rules-only package, not
     the same one. Only the routes that apply *nothing* are still byte-identical.
  2. **`suggest_optional` retires "branch steps compile only from observed states + human
     confirmation."** The observed state is still required — the pass can only judge a hint the
     recorder already produced, never invent one — but the confirmation now happens after the fact.
     There is no un-confirm command in the editor.
  3. **A wrong `parameterize_literal` reaches the customer** as an input the shipped skill demands.
     Visible and fixable in Human Review, but it is real behaviour, not annotation.
  4. **`suggest_optional` can shift `structural_fingerprint`**, since it rewrites `intent` and the
     fingerprint covers the first ≤5 interactive steps' intents. Drift detection sees a different
     landmark set.

  What did **not** move: element addresses, identity bundles, assertions and the collapse machinery
  all stay fully deterministic. The *"LLM does not write selector strings on the primary compile
  path"* invariant holds literally — see CLAUDE.md, where the carve-out is now written down rather
  than left for the letter of the rule to cover.
- **Dependencies:** none blocking. Shares the LLM proxy and cost model with the existing compile
  calls. Related: **COMPILE-1** and **COMPILE-2** (two live instances of family 3), **EXEC-29a** (the
  replay-side half of family 4), **BUILD-22** / **BUILD-23** (the alignment fragility that makes
  `group_steps` is the risky output), **BUILD-26** (the review-time twin — note it still proposes an
  accept/reject diff, which is right for a conversational agent acting on a reviewer's request and
  deliberately unlike this pass), **PROD-19** step 2 (the runtime-side twin of the data capture).
- **Suggested order:** (a) log the Human Review before/after diff, keyed on step identity — *done*;
  (a2) the eval harness beside it — *done*; (b) the second LLM call into `compile_report`, dark,
  emitting `suggest_optional` first then `rename_binding` / `parameterize_literal` — *done*; (b2)
  uncertainty routing and sibling-workflow context in the payload — *done*; (c) ~~accept/reject chips
  in Human Edit~~ — *dropped; the findings are applied and Human Review is the gate*; (d)
  `suggest_assertion` and `flag_noise` — *done, `flag_noise` resolved as archive-not-delete*; (e)
  consume `label_phase` — *done, in the Tier B recovery prompt*; **(f) only now consider
  `group_steps`.** Per-kind model routing sits after (f)'s eval-harness baseline is real.
- **Complexity:** L overall, staged — (a) S, (a2) S, (b) S–M, (b2) S, (c) S as built (an applier
  plus docs; the M-sized editor UI it replaced was never written), (d) S, (e) S, (f) M–L with the
  alignment risk above. Each stage is worth doing on its own merits.
- **Success criteria:** a workflow with two same-typed fields arrives in Human Review already
  carrying distinguishing names derived from workflow context rather than `_2` suffixes; what the
  pass wrote is confined to `input_binding`, `value` placeholders, `phase`, the `try_dismiss` branch,
  an advisory text/URL/state assertion, and (archived, never deleted outright) a flagged no-op step —
  a diff against the same compile with the pass disabled shows no selector or identity-bundle
  difference; a recorder-flagged stochastic step arrives as a `try_dismiss` branch, and a hint the
  pass left alone can still be confirmed through the existing gate; every reviewer edit is logged
  with the before/after pair, keyed on a stable identity rather than a step position that renumbers;
  the eval harness reports an override rate so a prompt change can be shown to help or hurt; a compile
  with the pass disabled or the provider pool drained produces a package identical to one that never
  ran it; and the median number of manual edits per compiled workflow is measurable and demonstrably
  lower than the pre-pass baseline.

### BUILD-27 — Nothing reviews the text that decides whether an agent calls the right skill at all
- **Category:** Builder
- **Description:** Skills reach the customer as MCP tools exposed by `runtime/app/server.js`
  (`execute_skill`, `get_skill_inputs`, `list_skills`). Whether Claude picks the **right** skill out
  of a pack of twenty, and whether it fills the inputs correctly, is decided almost entirely by three
  pieces of prose: the skill's name, its description, and its per-input descriptions. Today those are
  whatever the compiler happened to emit from the recording — and **no screen, pass, or check in the
  product ever reviews them.** Human Review scrutinises selectors, assertions and step order, all of
  which only matter *after* the right skill has already been chosen; the text that determines whether
  it gets chosen is the one thing nobody looks at. Two skills in the same pack can end up with near
  identical descriptions and neither the compiler nor the reviewer will notice.
- **Scope:** surface name / description / input descriptions in Human Edit as editable, first-class
  fields with the same weight the step list gets; add a deterministic lint (empty or default-looking
  description, two skills in a pack whose descriptions are near-duplicates, an input whose
  description does not say what a valid value looks like); and — once **BUILD-26** exists — let the
  copilot draft and critique them with the whole workflow in view. Prose only: this touches no
  selector, no assertion, and no execution path, so it is safe by construction.
- **Why required:** a workflow that executes flawlessly and is never selected is worth exactly as
  much as one that fails. This is the only remaining part of the skill contract with no review step.
- **Business value:** in a demo and in production alike, "the agent picked the wrong skill" reads as
  the product being unreliable, and it is currently the failure mode with the least instrumentation
  behind it. Also the cheapest quality lever available: it is editing text.
- **Technical value:** independent of every other item — no new data, no new capture, no execution
  change. The lint is deterministic and testable.
- **Dependencies:** none. **BUILD-26** makes it better (LLM drafting with workflow context) but is
  not required — the fields and the lint stand alone and should ship first.
- **Complexity:** S for the editable fields + lint; S–M for the copilot-drafted variant.
- **Success criteria:** name, description and every input description are editable in Human Edit and
  round-trip into the published pack; a pack containing two near-duplicate skill descriptions is
  flagged before publish; and an input with no description of its valid values cannot be signed off
  silently.

### BUILD-28 — `drift_detected` is emitted, reaches the dashboard, and nothing ever acts on it
- **Category:** Builder
- **Description:** `runtime/app/drift.js` already does the hard part: before step 0 it checks the
  compiled `structural_fingerprint`'s landmarks against the live page and, when more than half have
  vanished, emits a `drift_detected` telemetry signal — pure resolver scoring, no LLM, advisory and
  never blocking. The signal reaches the fleet dashboard and **stops there.** Nobody is told, and
  there is no path from "this app was redesigned" to "here is what to do about it." The vendor finds
  out when the failures start.
- **Scope:** (a) route the signal to the workspace that owns the pack — a real notification, not a
  chart; (b) show it in the Build Studio against the affected workflow, with which landmarks are
  missing; (c) offer the two real remedies, re-record or re-target step by step, rather than leaving
  the reviewer to work out what a drift number means. Once **BUILD-26** exists this becomes a copilot
  turn: "6 of 9 landmarks are gone — this app was redesigned; want to walk the affected steps?"
- **Why required:** drift is the single highest-value early warning the runtime produces, because it
  fires *before* a run fails rather than after, and it is currently thrown away. The detection is
  already built and paid for; only the response is missing.
- **Business value:** "we told you your vendor's app changed before your automation broke" is a
  qualitatively different product from "your automation broke." It is also the clearest proof that
  the fleet telemetry is worth having.
- **Technical value:** closes the loop on machinery that already exists and is already correct.
  Related: **EXEC-2** (fleet durability flywheel), **PROD-11** (skill health dashboard),
  **EXEC-33** (an adjacent case of a recovery signal the dashboard does not classify).
- **Dependencies:** none blocking. The detector, the telemetry path and the dashboard all exist.
- **Complexity:** M — routing and UI, no new detection logic.
- **Success criteria:** a workspace whose target app has drifted is notified without opening a
  dashboard; the affected workflow shows which landmarks are missing; and the notification offers a
  concrete next action rather than a number.
- **Update (2026-09-12, EXEC-36):** `drift_detected` now also reaches the END USER running the
  skill — `run.js` surfaces it as a plain-language warning in `execute_skill`'s response text
  (the same channel EXEC-36 built for its own environment-mismatch check). This is orthogonal to
  scope (a) above, which is about notifying the *workspace/vendor* — still open. Scopes (b) and
  (c) are also still open.

### BUILD-32 — Compile asks the reviewer a few clarifying questions instead of silently guessing (remaining slice)
- **Category:** Builder
- **Status (2026-10-18):** the machinery and the first five question kinds shipped with BUILD-39 (`conversation/clarify.py`, `handlers/compile_flow.py`): file upload (ask each run vs. fixed path), a step that can't be undone (confirm first), typed values (fixed vs. input), recorder-flagged pop-ups (always dismiss vs. one-off), and "how do we know it worked" (makes an existing compiler-written text/URL/state check required). Cap 5, one at a time, never blocking, answers only through existing gated mutations, questions stored in `clarify.json` so an unanswered compile is byte-identical.
- **Still open:** (a) the **row-style** question ("always the 3rd row, or the row matching its text?") — its answer changes the step's target, so the choices must be selector options `IdentityBundle`/`selector_grammar.py` already produced; needs an API that lists them per step; (b) the **date** question (fixed date / today / ask each run); (c) "**if the item is missing**" (stop and report vs. pause and ask) — needs a step kind that fails or branches on an absent item, which doesn't exist; an `ai_review` after the failing step can never run; (d) free-text answers only map onto single-choice questions (a tick-list asks you to tick); (e) "Change" on an answered question opens Human Edit rather than re-asking.
- **Complexity:** M for (a)+(b); (c) needs runtime design.

### BUILD-45 — Follow-ups from the conversation redesign's last phase
- **Category:** Builder
- **Items:** (a) a test paused by an expired sign-in should *resume by itself* after the person signs in (the mockup's “Show browser / continues on its own”); today Studio keeps `wait_for_auth` false and asks for a re-test, and the group does not get its own ▲; (b) offline the composer is disabled rather than holding a message to send later (“replies will wait”); (c) the legacy Approve button in Human Edit and the old group page's record/compile/test rows still exist beside the conversation — retire them once the conversation has been used on a real workflow; (d) the sidebar's empty-workspace variant (mockup `FirstLaunch`) is the ordinary sidebar with its hint text; (e) engine-stop and offline were never exercised in a running, signed-in Studio (the shell needs a Clerk sign-in) — only through unit tests, type checks and a real-Electron check of the panel seam.
- **Complexity:** S each; (a) M.

### BUILD-44 — Small items found while mapping the conversation redesign
- **Category:** Builder
- **Items:** `get_step_screenshot` (copilot retrieval) returns a path that is pasted into the prompt but the image is never attached; one `insert_overlay_branch` accept leaves several undo entries (structural ops collapse to one); possible double bounding-box offset for relayed child-frame events (`bridge.js` plus `session.py::_finalize_payload_sync`, **unverified**); `Done.md` reuses ids BUILD-31 and BUILD-33.
  Found in Phase 5: the failure card can't show the page at the moment of failure (the evidence screenshot lives under the sandbox folder, which the `conxa-asset://` protocol doesn't serve); the compile card's "N compile credits left" assumes the `quota` event's `remaining` key (not confirmed against `/usage/compile/commit`); a confirm-only review can be edited into a free-form one afterwards without the gate re-checking the destructive step after it; the guard cannot pause inside a `for_each` body (it fails the test instead); the stop/decision of a paused test lives only in memory (a Studio restart mid-pause leaves the parked browser to time out).
- **Complexity:** S each.

### RT-10 — The hand-over banner is installed on the whole host browser, not just the run's pages
- **Category:** Runtime / Isolation
- **Description:** `runtime/app/handover.js` arms its "Done" banner with `context.exposeBinding("__conxaHandoverDone")` + `context.addInitScript(_bannerScript(...))`. When the runtime borrows a view from Conxa Execute or Build Studio's test panel (`host_browser.js`), Playwright's one context is the host app's default CDP context, which holds the app's own window and other runs' views — so the banner script and binding reach every page in the host process, including the app UI, until the runtime disconnects. Found while building BUILD-46 (`Done.md`; the recorder's embedded mode installs its bridge per run page for exactly this reason, TRD §4.7a).
- **Suggested fix:** in host-browser mode, install the binding and init script on the run's own pages (`page.exposeBinding` / `page.addInitScript`, plus pages that open later), as `recorder/session.py::_adopt_run_page_sync` does.
- **Complexity:** S–M.

### BUILD-49 — Understanding check: follow-ups (clicked items, test → Clarify, pre-record plan)
- **Category:** Builder / Conversation
- **Description:** The understanding check (`conversation/understanding.py`, `Done.md` once filed; gap analysis G3) asks which recorded page addresses and record identifiers change each run, but three parts of the plan are not built. (1) **A clicked item with no row scope cannot change each run.** Its visible text lives in `identity_bundle` signals and `compiled_selectors`; the runtime does interpolate selector strings, but nothing proves that templating a literal there keeps the resolver's fingerprint scoring and drift checks coherent, and Clarify answers never touch `target`/`identity_bundle`. Today such a click is reported in the question body and stays as recorded — for "download file X from a list" this is the case that matters. Needs a deliberate design (probably a signal-aware, text-only template with a runtime test) before it is built. (2) **No path from a failed test back to a Clarify question** (e.g. "the test used X instead of the recorded Y and failed at this step — should it vary?"); needs the failing step's compiled-doc index, which the runtime's index space does not always match when the packager drops a step. (3) **No pre-record plan card** (`record_plan`, advisory) built from the task description. Also: the understanding result is not yet used to pre-decide `values` typed-text questions, and `url_pattern`/`text_present` assertions that contain a now-templated literal are not rewritten (the runtime interpolates assertion targets, so they would fail on a run that supplies a different value).
- **Why required:** (1) is the half of "which file?" the check still cannot act on; (2)–(3) close the loop the conversation plan describes.
- **Complexity:** (1) L, (2) M, (3) M.

### BUILD-50 — Remaining pieces of the compile-determinism and Clarify-coverage work
- **Category:** Builder / Compiler
- **Description:** (1) BUILD-47's *popup* race is still open: `session.py::_on_popup` queues `popup` with `tab_key=id(popup)` and the drain only resolves it if the page is already registered; otherwise it falls back to the opener's tab, so `tab_open`/`popup` can be misordered. Needs a real-browser repro before changing. (2) BUILD-32(b), relative dates ("today", "today + 7 days"): the runtime's `interpolate.js` has no expressions by design (its grammar mirrors `placeholder_grammar.py`), so this needs a `handler_hints.date_offset_days` honoured by `runtime/app/date_picker.js` plus a capability flag, and an `app-v` release. (3) The drop ledger lists cleanup passes only; the choice/date collapses and the packager's drop-and-warn cases are not yet in `compile_report["dropped"]`, and a dropped step that carried a value does not yet raise a Clarify question. (4) The second-opinion cache key still lacks the model and prompt version (the new session pin makes recompiles stable, but a fresh recording after a prompt change can still be served an older shared-cache answer).
- **Complexity:** (1) M, (2) M, (3) S, (4) S.

### BUILD-51 — Extract / run_if / on_missing: what the first cut leaves out
- **Category:** Builder / Runtime
- **Description:** (1) *Values made during a run are lost across a pause.* Anything a step writes into `inputs` (an extract's output, an `ai_review` answer) is not stored on the park object (`server.js` ~2154-2260), and a resume rebuilds `inputs` from the caller's arguments; an extract before an `ai_review`/fork pause therefore disappears from the final `outputs`. (2) *No Human Edit form* for `extract` (source / attribute / pattern / output name), `run_if` or `on_missing`: the gate, packager and copilot manifest accept them, but `StepConfigForm.tsx` has no fields, so today they are set by capture, by the copilot or by patch. (3) *No Clarify question for `run_if`/`on_missing`* ("skip this field when it's empty?", "what if the item isn't there?" — BUILD-32(c)). (4) *`outputSchema` on the per-skill MCP tools* is not declared (the SDK then requires structured content on every success); outputs are in the text and `structuredContent` only. (5) *`on_missing: "ask"`* (hand over to a person) is not built; only `skip` and the default fail-closed. (6) An extract inside a `for_each` body keeps only the last row's value. (7) A captured click is recorded through the top frame's bridge listener only if capture mode reached that frame; a frame created after the button press misses it (press again).
- **Complexity:** (1) M, (2) M, (3) S, (4) S, (5) M, (6) M, (7) S.

### BUILD-52 — Re-record from a step: hand the parked page to the recorder
- **Category:** Builder / Recorder
- **Description:** Everything around it exists and is tested (`_stop_before_step`, `compile_skill_package(segment=True)`, `replace_step_range`, `cmd_splice_segment`, `clarify_store.mark_stale`); the one missing piece needs a live Electron window. (1) `browser_control.js`/`browser_panel.js` need an `adopt_view` op that re-labels the test run's `WebContentsView` for the recorder and makes `_dropFinishedRuns` skip it (today `newView` closes every finished `test` run, and each recording gets its own storage partition, so a fresh view has none of the replayed state). (2) `host_view.acquire(existing_run_id)` and a `RecordingSession` start path that calls `_adopt_run_page_sync` on the already-open page instead of creating a view. (3) A `flow_rerecord_from{workflow_id, step_key}` command and a conversation action that string replay-to-N → record → compile(segment) → splice together, with the runtime's tab ids mapped onto the recorder's (unverified: the runtime assigns `tab_0..` while replaying, the recorder numbers tabs itself). (4) The parked run holds a host lock and a 180 s TTL (`PARK_TTL_MS`); the recorder must claim the page inside it or the park is discarded.
- **Why required:** the natural refinement in a conversation is "step 7 is wrong, let me show you"; today that re-records everything.
- **Complexity:** L.

### BUILD-53 — Branching and loop-safe testing: the parts of the conversation roadmap not built
- **Category:** Builder / Runtime
- **Description:** (1) *The guard cannot pause inside a `for_each` body.* `run.js::executeOneStep` fails the test with `guard_inside_loop` because `runForEachStep` keeps no row cursor, so a resume would restart from row 0; persisting `{row_index}` on the park (and restoring it in `runForEachStep`) would let the guard, a review and a handover pause mid-loop (also `server.js`'s park objects). (2) *General variant branches.* `compiler/variant_align.py::compare_variant` works on events and returns `unexplained` strings, never merged; a general learner needs it to return the unmatched spans `(k, m)` and a map from events to compiled steps, then build a `wait_for_one_of` whose option probes come from the first diverging step's IdentityBundle and whose bodies are the two recorded segments, confirmed by the person through `BRANCH_ALLOWED_KEYS`. Known limit to settle first: branch bodies run through `runBranchBody` with no recovery cascade (`handlers.js`), so a long variant body would be less robust than a top-level step. (3) *The conversation plan's pre-record plan card* and *test → Clarify follow-up* are BUILD-49. (4) The P2 residuals the gap analysis lists (EXEC-44 #1-7, EXEC-25/26, RT-10, EXEC-27/28/29a, EXEC-38-UI, BUILD-6, BUILD-27/28, BUILD-45(b-d), BUILD-32(e)) are unchanged and still tracked under their own IDs.
- **Complexity:** (1) M, (2) L.

### BUILD-48 — In-app recording: follow-ups after BUILD-46
- **Category:** Builder
- **Items:** (1) a manual end-to-end pass in a running, signed-in Studio on a real site needing sign-in (sign-in in the panel, record across a site-opened tab, pause/resume, widen, Finish → compile, check `frames/`, test replay, Group page Connect) — the pieces are tested separately and in a real-Electron harness, not together in the packaged app. (2) `localStorage` of an origin whose sign-in tab already closed keeps the previous save's copy (same limit as AUTH-20). (3) Playwright's own mouse input misses on a shrunk (scale < 1) page — only human input is used while recording today, but anything that drives the record view programmatically must account for the scale. (4) A popup adopted after its first document loads gets the dialog override only from its next navigation (alerts on that first document take the CDP dialog path). (5) If the person leaves the Group page mid-Connect, the sign-in view has nowhere to be drawn until they come back. (6) A slow download hand-off blocks the recorder loop: in 1 of 3 Gate B re-runs the panel took ~6 s to hand over the file, the popup opened meanwhile was adopted late, and the first click inside it was not recorded. Claim the download off the pump thread (or keep pumping while waiting, as `_save_panel_download_sync` already does for its first 0.75 s).
- **Complexity:** S each; (1) M.

### BUILD-47 — Two ordering races in the recorder's event stream (launched and embedded alike)
- **Category:** Builder / Recorder Correctness
- **Description:** Found running the same scripted session four times with today's launched recorder (Gate B of BUILD-46, now in `Done.md`): (1) `download_observed` was recorded after the *next* click in 2 of 4 runs — `bridge.js` holds a click's report until the DOM settles (up to `click_settle_max_ms`), and the download event can overtake it; the compiler reads "the step after this click is `download_observed`" as "this click downloads", so the click loses that meaning and a later, unrelated click gains it. The embedded path now waits `_DOWNLOAD_CLICK_GRACE_S` for the click; the launched path does not. (2) The synthetic `popup` event is stamped with the popup's own tab only when the pump has registered that tab before the event is drained (`_tab_key`); otherwise it falls back to the opener — 1 of 4 runs got the popup's tab, and the compiled step order around `tab_open`/`popup` changes with it.
- **Why required:** recordings of the same actions compile differently from run to run; (1) can attach a download to the wrong click.
- **Suggested fix:** order synthetic events against bridge payloads by their own timestamps (or hold a synthetic until the page's pending click reports have drained), and resolve the popup's tab deterministically before the `popup` event is materialized.
- **Complexity:** M.

### COMPILE-1 — `collapse_date_picker_runs` leaves an orphaned raw day-click when the same date field is date-picked twice in one recording
- **Category:** Compiler Correctness
- **Description:** Discovered live-testing `mega-workflow` in dev mode. Recording the same date-of-birth field twice in one session (once via calendar prev/next arrows, once via the month/year `<select>` dropdowns), with an unrelated field typed in between the two, produces two clean `date_pick` steps as expected — but also leaves one extra, un-collapsed raw `click` step for the FIRST picked day-cell (`text="6"`, aria-label "Choose Thursday, September 6th, 2007"), positioned *after* the second `date_pick` in execution order. By the time this orphan step runs, the calendar has already been reopened and renavigated by the second `date_pick`, so the cell it targets no longer exists on the currently-displayed month — the runtime correctly reports "Element not found" and requests self-healing recovery. `conxa_compile/compiler/date_picker.py::collapse_date_picker_runs` scans linearly for consecutive events sharing a `date_context.grid` selector; the interleaved unrelated field (no `date_context`) correctly closes the first run, and a second run for the second date-pick opens correctly — but the raw day-click event for the *first* pick, verified present in the raw session recording (`identity_bundle.fingerprint.aria_label` = "Choose Thursday, September 6th, 2007", role `gridcell`, `react-datepicker__day` classes — unambiguously a real day-cell), was not classified with `date_context.role == "day"` at the point the pass scanned it and so fell through to `out.append(steps[i])` (an ordinary click) instead of being absorbed into the first `date_pick`. Root cause of *why* that one event's `date_context` didn't classify as a day-cell (a `bridge.js::buildDateContext`/`_cellIsoDate` question) was not run down — worth tracing with a fresh, minimal repro (two date-picks on one field, unrelated field typed in between) before attempting a fix.
- **Why required:** any recorded workflow that touches the same date field twice in one session (a natural thing to do while getting a demo recording right, or a legitimate two-step DOB-entry UI) silently compiles a step that will fail on replay, well downstream of the actual date logic — exactly the kind of "genuinely works but needs the real-world proof" gap PROD-1 (above) is about, except this one is a straightforward compiler bug, not an environment-drift question.
- **Business value:** medium — a workflow that fails deep into a run (72+ steps in, in this case) burns much more of a customer's trust and debugging time than one that fails early or at compile time.
- **Technical value:** high — `collapse_date_picker_runs` is the sole date-picker collapsing pass; a leftover raw click from it can appear in any workflow with this recording pattern, not just this one.
- **Dependencies:** none blocking; self-contained to the compiler's date-picker pass and (likely) `bridge.js`'s date-context classification.
- **Suggested order:** next time compiler/date-picker code is touched, or before this specific mega-workflow session is relied on again — re-recording without the double date-pick pattern is today's practical workaround.
- **Complexity:** M — requires tracing `buildDateContext`/`_cellIsoDate` against the actual raw event (available in this repo's dev studio data dir, session `9d19890f-554c-49e8-90dd-f1b41e942f9e`, `events.jsonl` line 33) to find why that specific click's date-context classification didn't stick, then either fixing the classification or making `collapse_date_picker_runs` defensively drop/absorb an orphaned day-click whose `aria_label`/`cell` selector duplicates one already consumed by an earlier `date_pick` step in the same compile.
- **Success criteria:** recording the same date field twice in one session, with an unrelated field typed in between, compiles to exactly two `date_pick` steps and zero leftover raw clicks; a regression test in `conxa-cloud/tests/test_date_picker_collapse.py` covers this exact "field picked twice, interleaved by an unrelated field" pattern.

### COMPILE-2 — `_findAnchoredField` misses react-datepicker's Date of Birth field, forcing `grid_only` instead of `typed_first`
- **Category:** Compiler Correctness
- **Description:** Discovered fixing the `mega-workflow` `date_pick` desync bug (2026-09-02, resolved — see the top of `FIX.md`). Once the steps↔events alignment bug was fixed, the Date of Birth field's compiled `date_pick` step still carries `handler_hints.date_picker.open: ""` and `strategy: "grid_only"`, even though `#dateOfBirthInput` is a real anchored `<input>` sitting immediately before it in the step list. `bridge.js::_findAnchoredField` (recorder/bridge.js:987) is supposed to resolve the calendar grid back to its owning field via `aria-owns`/`aria-controls`, falling back to `_lastFocusedEditableForDate`. react-datepicker sets neither ARIA attribute on this widget, and the fallback apparently doesn't survive the recording's intervening month/year `<select>` interactions (see COMPILE-1, same field, same session) — so `_findAnchoredField` returns `null` and the compiler falls back to the weaker `grid_only` replay strategy (drive the grid directly) instead of `typed_first` (type into the field, which is more robust against grid re-render timing).
- **Why required:** `grid_only` still works (the runtime's grid drive in `runtime/app/date_picker.js` handles it), but it's the less durable of the two strategies documented in `date_picker.py` — losing the anchored-field signal on a field that genuinely has one degrades replay robustness for no reason, on what is likely a common calendar-library pattern (react-datepicker), not a one-off.
- **Business value:** low-medium — this doesn't fail replay today; it's a quiet reliability regression on a specific (but common) date-picker library shape.
- **Technical value:** medium — `_lastFocusedEditableForDate`'s tracking lifetime through intermediate select-driven navigation is exactly the kind of DOM-focus-tracking edge case that's easy to get subtly wrong and hard to notice without a live recording.
- **Dependencies:** none blocking. Independent of COMPILE-1 (different symptom, same field/session) and of the desync fix (that fix is a prerequisite for seeing this clearly — before it, the `date_pick` step was in the wrong place entirely).
- **Suggested order:** next time `bridge.js`'s date-context detection is touched; not urgent since `grid_only` is a safe fallback.
- **Complexity:** M — needs a fresh recording of a react-datepicker (or similar) field with intervening month/year `<select>` use to confirm whether `_lastFocusedEditableForDate` is being cleared/overwritten by the select interactions, or never set for this widget shape at all.
- **Success criteria:** recompiling a react-datepicker Date of Birth field recorded via the month/year `<select>` path yields `handler_hints.date_picker.strategy: "typed_first"` with `open` naming the real anchored input.

## P3 — Valuable, Sequence Around Other Work (17 remaining; +4 more in "P3 Discovered Items" below)

### PROD-2 — Recordability pre-check (green/yellow/red compile-time score)
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** A score shown during/after recording indicating how automatable the target product actually is, based on how dynamic/generalizable its screens are across accounts.
- **Why required:** turns "products that are too dynamic per-customer are out of scope" from an informal policy into a measurable, shown-to-the-vendor gate — the source doc frames this as preventing "the worst early-company event: a paying customer whose product was never going to work."
- **Business value:** avoids selling into accounts that will produce bad first impressions no matter how good the runtime is — protects both revenue (churn) and reputation.
- **Technical value:** small, self-contained addition on top of existing compile-time signal quality checks (`selector_filters.py`'s anchor-quality gates already compute something adjacent).
- **Dependencies:** none blocking.
- **Suggested order:** early, alongside PROD-1 — cheap and directly protects the same risk.
- **Complexity:** S.
- **Success criteria:** recording a workflow against a known-dynamic product surfaces a visible yellow/red warning before the vendor invests further in it.

### PROD-4 — Session keeper + vendor-controlled long-lived sessions
- **Category:** Product Strategy & Business-Risk Mitigation
- **2026-09-21 (unattended half done):** a scheduled run with an expired login no longer opens a visible sign-in window on a runner nobody is sitting at — pre-flight (`noPrompt`) closes the session and fails with a message naming the app(s), which the scheduler stores as the schedule's `last_run.message` (visible through `list_schedules`; the tray/CLI state file carries status only). The vendor-controlled-session half is now written up as vendor guidance in `docs/Runner-Machine.md` §4a; it stays **open** on its success criterion — at least one design-partner vendor actually configuring a longer-lived session for their own automation traffic — which no code can satisfy.
- **2026-09-21:** the up-front half is now also complete for interactive use — pre-flight probes and sign-in happen inside one browser session, the wait for the human no longer depends on the MCP request timeout (the run detaches and starts by itself), and a run never starts on an invalid session. The unattended case and the vendor-controlled half are covered by the entry above.
- **Session-keeper half resolved 2026-08-02:** the pre-flight dead-login check this item asked for already existed (`browser.js::_validateSession`, called before step 0) but was undermined by two bugs — the interactive login window discarded any existing session instead of reusing it, and the "did the user finish signing in?" check flagged a real OAuth login (e.g. through Google) as "still on the login page" because it matched substrings like `auth`/`oauth`/`signin` anywhere in the URL, including on the identity provider's own domain. Fixed: the capture check is now scoped to the target site's own hostname, the login window seeds from whatever session is already on disk, and the whole interactive-login path (first-run and mid-run) is now non-blocking — it returns immediately with "a login window is open, sign in and re-run" instead of holding the MCP call open past the client's response budget. See `docs/Auth-and-Updater.md` §1.3.
- **Description:** Detect a dead login *before* a skill's first step runs (not mid-run), and prompt for re-login up front rather than failing partway through. Separately, since Conxa's customers own the software being automated, let a vendor configure longer-lived, device-locked sessions specifically for their own automation traffic — no MFA circumvention, just the app owner setting a sensible policy for their own runtime.
- **Why required:** expired logins and MFA are named as the biggest practical interruption to scheduled/overnight runs; the source doc frames this as "an accepted design, not a gap to engineer around" for attended use, but a real gap for unattended runs specifically.
- **Business value:** directly extends how "hands-off" the overnight/unattended pitch can honestly be — today it's capped by session lifetime.
- **Technical value:** the session-keeper half is a small, self-contained pre-flight check; the vendor-controlled-session half requires the vendor's own cooperation (a config choice on their end), not new Conxa infrastructure.
- **Dependencies:** none blocking.
- **Suggested order:** moderate priority — meaningfully improves the unattended/overnight-run story without being a blocker to any current sale.
- **Complexity:** M.
- **Success criteria:** a dead session is caught and surfaced before a scheduled run begins, not mid-run; at least one design-partner vendor has configured a longer-lived session for their own automation traffic.

### PROD-9 — Trust & GTM: open-source safety-critical runtime code + security whitepaper + IT admin console
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** Publish a short security whitepaper making explicit that customer data never touches Conxa's cloud during execution; open-source the safety-critical parts of the runtime (element-finding, recovery, session encryption) so a security team can read the actual code instead of trusting a claim; and give enterprise IT an admin console showing what's installed where, with a remote kill switch and a proper enterprise install package.
- **Why required:** answers "company IT departments will distrust the installer" beyond what code signing alone fixes (signing answers "who made this," not "what does it do," and security teams judge both).
- **Business value:** flips the IT security conversation from "prove you're not malware" to "here's the source code and here's your off-switch" — described as a much stronger position for enterprise deals specifically.
- **Technical value:** Conxa's actual differentiated value lives in the compiler, the fleet data, and distribution — not in the runtime's element-finding/recovery code — so open-sourcing that part is framed as low-cost from a competitive-moat perspective.
- **Dependencies:** the admin console/kill switch overlaps with CLOUD-1's enterprise plumbing (RBAC/tenant management) — sequence together if convenient.
- **Suggested order:** alongside CLOUD-1 and UPD-2/UPD-3 (the broader enterprise-trust cluster).
- **Complexity:** L — open-sourcing requires care about what's actually safe to publish; the admin console with a kill switch is a real new feature.
- **Success criteria:** the safety-critical runtime modules are published as open source with a whitepaper explaining the security architecture; an IT admin can see installed machines and remotely disable one.

### PROD-10 — SOC 2 / ISO 27001 + enterprise packaging
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** SOC 2 Type I then Type II (or ISO 27001 for non-US buyers, ~80% overlap with SOC 2, same compliance platforms), plus MSI/Intune-style enterprise install packaging, SSO/per-device identity with remote revocation, and replacing the shared per-company sync token with per-device identity (today, one leaked installer is a company-wide token leak — flagged as the first finding any serious security review will surface).
- **Why required:** gates the enterprise segment specifically; the source material notes Conxa has a real scoping advantage here since the cloud is deliberately thin (customer execution/data never touch it), making the audit boundary — and cost — smaller than a typical SaaS company's.
- **Business value:** directly opens the enterprise segment; a known, bounded cost and timeline (~$15–40k first year, SOC 2 Type II ~6–12 months) rather than an open-ended unknown.
- **Technical value:** the per-device-identity replacement for the shared sync token is a real security improvement independent of the compliance certification itself, and relates to CLOUD-1's tenant-isolation work.
- **Dependencies:** benefits from CLOUD-1 (RBAC/tenant isolation) landing first, since SOC 2 audits typically examine exactly that kind of access control.
- **Suggested order:** start now in parallel with engineering work — the source material's explicit warning is "everything lands in about a year, and the only way to lose is to start late."
- **Complexity:** L — mostly compliance process and per-device identity engineering, not deep architecture work, but with an unavoidable multi-month observation window for Type II.
- **Success criteria:** SOC 2 Type I (or ISO 27001) report in hand; per-device identity replaces the shared installer-wide sync token as the primary trust anchor.

### PROD-12 — Customer onboarding & support: guided walkthrough, troubleshooting runbook, "request a fix" path
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** A guided "connect your app" walkthrough for the auth/record-session step, a troubleshooting runbook a non-technical user can act on when a run fails (today they just get a raw recovery payload and a `resume_from` token), and an explicit "request a fix" escalation path to the vendor/Conxa team.
- **Why required:** identified in the go-to-market readiness research as the one non-self-serve step in an otherwise-strong onboarding flow (install, connect, record, and compile all work well; troubleshooting a failure is "the hole"). For an initial small cohort of customers this is acceptable as a deliberately-staffed white-glove model, but it should be a planned staffing decision, not a surprise support load.
- **Business value:** reduces support burden per customer and improves the credibility of the "self-serve" pitch as the customer base grows beyond a hand-held first cohort.
- **Technical value:** builds on the already-shipped error-code UX mapping (Implementation-Plan §2.8) — this item is the next layer up (a runbook and an escalation path), not a duplicate of that work.
- **Dependencies:** benefits from PROD-11's health dashboard and drift alerts, which feed directly into what a troubleshooting runbook would need to explain.
- **Suggested order:** moderate priority — relevant as soon as the customer base grows past a small, founder-supported cohort.
- **Complexity:** M.
- **Success criteria:** a non-technical user who hits a failed run can follow a runbook to a resolution, or has a clear "request a fix" path, without needing to interpret a raw recovery payload themselves.

### DOC-2 — `[DECISION]` `conxa-cloud/scripts/PLUGIN_TEST_README.md` legacy-format call
- **Category:** Documentation & Process
- **Description:** This doc and the `test_plugin.py` tool it documents describe a bundle schema (`manifest.json`, `orchestration/`, `SKILL.md`, a 4-layer recovery model) that doesn't match the current authoritative schema (`pack.json`, `skills/{slug}/{execution,recovery,inputs}.json`, `CLAUDE.md`+`index.md`, the 4-tier T1–T4 cascade). It also references `app/services/skill_pack_builder.py`, which doesn't exist anywhere in the current backend. A flag note was added to the file during this audit (see `docs/archive`-style annotation at its top) but no decision was made about what to do with it.
- **Why required:** a contributor following this README today will hit an immediate dead end at a nonexistent file. It's unclear whether this is a legacy/parallel test harness that predates the current schema (safe to archive) or an accidentally-orphaned tool that should be updated to match reality.
- **Business value:** low direct value, but prevents future engineer time loss and a second, contradictory source of truth for the bundle schema.
- **Technical value:** removes a second description of the skill-pack format, consistent with the "one canonical description" principle applied to the recovery-tier table elsewhere in this audit.
- **Dependencies:** none.
- **Suggested order:** quick — this is a single question to whoever built `test_plugin.py`, not an engineering task.
- **Complexity:** S if the answer is "archive"; M if the answer is "rewrite to match the current schema" (requires actually updating the test harness, not just the doc).
- **Success criteria:** either the file + its scripts move to an archive location with a "superseded" note, or both are rewritten to reference real files and the current schema.

### DOC-4 — Build the CLAUDE.md/AGENTS.md/README.md sync mechanism
- **Category:** Documentation & Process
- **Description:** These three root-level files triplicate the shared "Repository Layout," "Architecture," "Key Invariants," "Where to Look First," and "Deployment" sections for three different audiences (Claude Code, Codex, human/GitHub visitors). This audit found `AGENTS.md` had already drifted behind `CLAUDE.md` (missing 3 of its newest invariants, plus a stale single-layer runtime description) before being manually re-synced as part of this pass. Build `scripts/sync-docs.py` to extract the shared sections from `CLAUDE.md` and write them into `AGENTS.md`/`README.md` between marker comments, plus a CI check (new lightweight workflow or a step in an existing one) that fails if they've drifted.
- **Why required:** the manual-sync approach was implicitly already relied on and already failed within about a month — this is a proven, recurring failure mode, not a hypothetical one.
- **Business value:** low direct value, but prevents an AI coding agent from making a wrong decision because it read the stale copy of an invariant in `AGENTS.md` instead of the current one in `CLAUDE.md`.
- **Technical value:** converts a manual-discipline problem into a structurally-enforced one (CI fails instead of "someone forgot").
- **Dependencies:** none.
- **Suggested order:** do once the three files have been re-synced by hand (done as part of this audit) so the script has a clean starting point to codify, not a gap to first close.
- **Complexity:** M — a small extraction/injection script plus one CI step.
- **Success criteria:** editing a shared section in `CLAUDE.md` and running the sync script updates `AGENTS.md`/`README.md` identically; a CI run against an intentionally-desynced `AGENTS.md` fails.

### RT-1 — macOS runtime support
- **Category:** Runtime
- **Description:** `.github/workflows/build-runtime-host.yml` already has a full `build-conxa-runtime-macos` job scaffolded and gated `if: false`, with an explicit `TODO(2.4)` comment listing the exact remaining work: stage arm64/x64 `keytar.node`, codesign + notarize via `notarytool`, compute SHA-256, upload the release asset, and POST the mac entry into the signed runtime manifest. `installer_builder.py` has no macOS installer generation (PKG/DMG) yet.
- **Why required:** currently Windows-only; this is the only remaining Phase 2 item besides code signing per `docs/Sales-Blockers.md`, and it's explicitly framed there as an upsell (expands addressable market to Mac-based teams) rather than a blocker.
- **Business value:** opens the Mac-team segment of the market; not required for the current Windows-first enterprise sales motion.
- **Technical value:** the runtime code itself is already platform-aware (`CONXA_DIR` resolves correctly on non-Windows); this is packaging/signing/distribution work, not core logic.
- **Dependencies:** none.
- **Suggested order:** after the first Windows enterprise sale closes, per `docs/Sales-Blockers.md`'s recommended sequence.
- **Complexity:** L — CI scaffolding exists, but macOS installer generation, codesigning/notarization, and a tested distribution path are all genuinely unbuilt.
- **Success criteria:** a signed, notarized `.pkg`/`.dmg` installs and runs the full record→compile→execute loop on macOS; the `build-conxa-runtime-macos` CI job is un-gated and green.

### RT-2 — Recording capture depth
- **Category:** Runtime
- **Description:** Add first-class recorder support for typeahead/autocomplete, dynamic-table operations (sort/filter/paginate), and multi-step wizard composites — the interaction classes WorkArena research identifies as most commonly mis-captured. Also add per-signal confidence scoring at capture time (rather than reconstructing it later at compile time) and ~~conditional-state observation (recording whether an element was present/absent when the recording happened, feeding EXEC-1's conditional steps)~~ — **conditional-state observation done 2026-07-10** (`bridge.js::detectOptionalContainer` flags dialog/consent-banner interstitials as `optionality: "stochastic"` + `branch_hint`; human-gated confirm in Human Edit converts the step to a real `try_dismiss` branch via `confirm_optional_interstitial` — see `research-analysis/04-architecture/subsystems/recording-next-steps.md` Priority 2, `docs/TRD.md` §11.1). Typeahead/autocomplete, table ops, wizard composites, and capture-time confidence scoring remain unbuilt — confirmed via grep across `conxa_compile/recorder/`.
- **Why required:** these interaction types are exactly the ones enterprise SaaS workflows lean on heavily and where linear, naive event capture is weakest — under-capturing them pushes more of the burden onto compile-time LLM reconstruction and runtime recovery, both more expensive and less reliable than capturing the right signal in the first place.
- **Business value:** directly improves out-of-the-box reliability for the enterprise-SaaS workflows Conxa targets, without needing a customer to hit a failure first.
- **Technical value:** reduces compile-time LLM dependence (cheaper, more deterministic compiles) and gives EXEC-1's conditional steps something concrete to be compiled from. Also a prerequisite for PROD-7's connector-graduation idea, which needs the recorder to observe network calls behind each step.
- **Dependencies:** should follow EXEC-1 (conditional/branch steps) — capturing "this element was sometimes present" is only useful once the compiler/runtime have a conditional-step representation to compile it into.
- **Suggested order:** after EXEC-1 lands.
- **Complexity:** L — new recorder event types, new `bridge.js` capture logic, compiler-side handling for the new signal types.
- **Success criteria:** a recorded workflow with an autocomplete field, a sortable table, and a multi-step wizard compiles into steps that replay correctly without falling back to LLM reconstruction for basic structure; capture-time confidence scores are present in the compiled `IdentityBundle`.
- **Note (2026-07-10, from the recording.md design-vs-code review):** one already-captured signal is currently thrown away — `bridge.js::finalizeStateWithAfter` computes a `dom_diff` (interactive elements added/removed by each action) but nothing in Python consumes it (the `StateChange` model only keeps `before`/`after`, so the diff is dropped on parse; confirmed via grep). When RT-2's capture work touches `finalizeState`, either wire `dom_diff` into the post-condition distillation (`compare_state`/`validation_planner` currently diff only the coarse page fingerprints) or stop computing it. Full section-by-section status of the recorder design is in `research-analysis/04-architecture/subsystems/recording.md`.

### MCP-1 — `ServerBackend` seam + entitlement-filtered `list_skills`
- **Category:** MCP
- **Description:** Refactor `runtime/server.js` (currently a large single file) into the harness/registry/backend-seam architecture described in the research corpus (`04-architecture/subsystems/mcp.md`), and extend the existing capability-filtering pattern into true entitlement filtering — `list_skills` should advertise only skills the calling company is actually licensed for.
- **Why required:** confirmed unbuilt via grep (no `ServerBackend` references anywhere in `runtime/`). As the runtime's MCP surface grows (more tools, more skill types), an undifferentiated single file becomes harder to evolve safely, and licensing-by-convention (rather than by enforced filtering) is a governance gap for a paid product.
- **Business value:** entitlement filtering is a direct commercial/governance enabler — customers should not be able to see (even if not execute) skills outside their license tier.
- **Technical value:** a cleaner separation of transport/registry/backend concerns makes the runtime safer and faster to evolve; this is explicitly hygiene work, not a reliability fix. This refactor is also the natural place to add MCP-2/MCP-3's multi-agent HTTP surface without further bloating `server.js`.
- **Dependencies:** benefits from CLOUD-1's more complete entitlements model landing first, though a first pass of entitlement filtering could use the current partial RBAC.
- **Suggested order:** after CLOUD-1, or in parallel if entitlement data is already sufficient for a first cut.
- **Complexity:** L — a real refactor of `server.js`'s architecture, plus new entitlement-checking logic in the `list_skills` path.
- **Success criteria:** `server.js` is decomposed into harness/registry/backend layers; `list_skills` for a company only returns skills that company's plan/license actually entitles them to.

### EXEC-3 — Vision grounding as an actionable Tier B mechanism
- **Category:** Execution & Recovery
- **Description:** Build out Tier B vision into a real, bounded recovery mechanism: a grounder that maps `(screenshot, description) → normalized bounding box`, scaled by device pixel ratio at execution time, with Set-of-Marks-style annotation shipped to telemetry as a drift signal (never as success evidence — top-50-improvements.md #42). Today, per `docs/TRD.md` §10.1, every Tier B round already ships screenshots with the ranked digest — the agent receives an image but there's no grounder, bbox re-derivation, or scale normalization on the runtime side.
- **Why required:** without a real grounder, the pictures in a Tier B round are more of a "give the model a picture and hope" fallback than a working extraction — vision is the identity half of recovery, but should still produce a structured pick the runtime can verify. Note per `build-order.md`: don't build this until CDP-AX recovery (EXEC-4/EXEC-6) is exhausted in practice, since most "DOM-opaque" cases are reachable via CDP accessibility-tree access first — vision is deliberately the rarest, lowest-ROI mechanism (ROI 4 of 14 in `master-recommendations.md`).
- **Business value:** improves recovery success on DOM-hostile surfaces (canvas-based UIs, custom widgets that don't expose a normal accessibility tree) that no other tier can handle at all today.
- **Technical value:** completeness for a deliberately rare, bounded tier — this is explicitly not meant to become a primary execution path (vision-as-primary is a rejected anti-pattern per the research corpus).
- **Dependencies:** benefits from EXEC-4's resolution (the shape of the agent-mediated recovery handoff affects how a vision grounder's output flows back into the runtime); should follow EXEC-6's closed-shadow CDP escape hatch, per `build-order.md`'s explicit sequencing.
- **Suggested order:** after EXEC-1/EXEC-2/EXEC-6, since it's lower strategic priority per the research corpus's own ranking.
- **Complexity:** L — a real grounder model/pipeline, bbox normalization logic, and telemetry wiring for SoM annotations.
- **Success criteria:** a Tier B recovery on a canvas-based or custom-widget UI produces a normalized, scale-correct bounding box that the runtime can act on, not just a screenshot handed to the agent with no structured extraction.

### EXEC-23 — Recorder coverage checklist from browser-use's ClickableElementDetector
- **Category:** Execution & Recovery
- **Description:** Mine browser-use's `ClickableElementDetector.is_interactive` heuristics (`research-analysis/05-reliability/per-tool/browser-use.md` §"What Conxa should adopt", item 3) into the Build Studio recorder/compiler as *recording-coverage assertions* — a checklist of interactable shapes the recorder must recognize so it never silently fails to capture a target: JS click listeners with no DOM signal (CDP-detected `@click`/`onClick`/`(click)` handlers — the single most valuable heuristic, covering div-buttons and icon-only targets), `label`/`span` wrappers containing form controls ≤2 levels deep (Ant-Design-style radio/checkbox wrappers), AX state properties (`focusable`/`editable`/`checked`/`expanded`, with `disabled`/`hidden` as negative signals), icon-sized elements (10–50px box with class/role/aria-label), and a `cursor:pointer` fallback. Use strictly as a *completeness checklist at record/compile time*, never as resolution logic or element identity.
- **Why required:** with Tier B now grounding on a ranked indexed digest of interactive elements (`runtime/app/candidate_digest.js`, 2026-08-25), anything the recorder failed to capture as an interactable is also missing from that digest — recording gaps now directly shrink the recovery candidate pool, not just the primary path.
- **Business value:** fewer silently-missed clicks on custom-widget sites; stronger compiled packs.
- **Dependencies:** none hard; pairs naturally with future EXEC-3 vision work (the same detector informs what belongs in a digest vs. needs pixels).
- **Complexity:** M — CDP listener detection in the recorder bridge + compile-time warning when a clicked target has only positional/class identity (compile report signal).
- **Success criteria:** recording a click on a div-button that has only a JS click listener (no role/text/testid) produces either a captured step with a usable identity bundle or an explicit compile-report warning — never silence.

### EXEC-25 — `beforeunload` confirmations unhandled on record and replay
- **Category:** Execution & Recovery
- **Description:** Discovered while fixing the alert/confirm/prompt auto-close bug (see FIX.md, 2026-08-26). `session.py::_on_dialog` now holds `alert`/`confirm`/`prompt` open and asks the Studio, but a `beforeunload` "Leave site?" confirmation is a different Playwright surface entirely (there is no `page.on("dialog")` event for it in the recorder's current wiring — Playwright just auto-dismisses it) and was left untouched. A recording that navigates away from a page with unsaved-changes protection will silently lose that confirmation step, and replay has no handler for it either.
- **Why required:** any site with a "you have unsaved changes" guard (most form-heavy SaaS admin pages) can silently drop a step during recording, with no error surfaced anywhere.
- **Business value:** correctness for a category of sites (anything with an unsaved-changes guard) that's common in the SaaS admin tools Conxa targets.
- **Dependencies:** none hard; would likely reuse the same "hold + ask the Studio" plumbing this fix introduced (`on_js_dialog_request`/`resolve_js_dialog`/`_drain_js_dialog_sync` in `session.py`) if Playwright exposes enough control over the navigation to make that safe.
- **Complexity:** M — requires confirming what control Playwright actually gives over a `beforeunload` prompt (it may not be interceptable the same way as `dialog`) before any implementation can start.
- **Success criteria:** recording a navigation off a page with an unsaved-changes guard either records an explicit step for the choice made, or the gap is documented as a known, intentional limitation with a clear reason why.

### EXEC-26 — Native JS dialogs unhandled in the Build Studio auth-capture browser
- **Category:** Execution & Recovery
- **Description:** Discovered in the same fix as EXEC-25. The separate login-capture browser (`auth_mode=True` recording sessions, used to seed stored auth) still auto-accepts every native `alert`/`confirm`/`prompt` immediately (`session.py::_on_dialog`'s `auth_mode` branch), deliberately left unchanged because that browser's pump loop never drains a pending dialog (`_run_sync_recorder`'s `if not self.auth_mode:` gate) — holding a dialog there today would hang the login page forever with no way to answer it.
- **Why required:** a login flow that shows a dialog (e.g. a "you're already signed in elsewhere" confirm) is silently auto-accepted with no chance for the human doing the auth capture to choose otherwise.
- **Business value:** low-to-medium — affects only sites whose login flow itself triggers a native dialog, but a silent wrong answer there can leave stored auth in an unexpected state.
- **Dependencies:** would need the auth-capture browser's pump loop to gain the same dialog-draining path as the main recording loop, plus a Studio UI surface for the auth-capture window (which today has no equivalent of `RecordWorkflowDialog.tsx`'s live event stream).
- **Complexity:** M — mostly plumbing an existing pattern into a second, currently-simpler pump loop.
- **Success criteria:** a login flow's native dialog is surfaced to the human doing auth capture instead of being auto-accepted, or the gap is documented as a known, intentional limitation.

### EXEC-27 — `probePresent`/branch-primitive polling has the same unbounded-timeout gap `pollPositive`/`pollNegative` had
- **Category:** Execution & Recovery
- **Description:** Discovered while fixing replay deadlocking on a native JS dialog (FIX.md, 2026-08-26): `runtime/app/assertions.js`'s `pollPositive`/`pollNegative` used to `await checkFn()` unconditionally and only check the deadline afterward, so a `checkFn` that never resolves (e.g. a Playwright call against a renderer blocked by an open dialog) hung the whole poll forever regardless of its `timeoutMs`. That's now fixed there (`withDeadline` races each check against the remaining deadline). `runtime/app/handlers.js`'s `probePresent` — used by the `if_present`/`try_dismiss`/`wait_for_one_of` branch primitives — does its own separate polling with the same shape and the same gap, just not yet exercised by a known-reachable hang the way `verifyStep` was.
- **Why required:** any future scenario where a branch-primitive's predicate hangs (a native dialog mid-branch-check, a hung selector query, a slow/wedged page) would deadlock the run exactly the way the dialog bug did, with no timeout protecting it.
- **Business value:** reliability — prevents a whole class of "run hangs forever with no error" failures in conditional/branch workflows specifically.
- **Technical value:** small, mechanical fix once done — likely the same `withDeadline` helper, exported from `assertions.js` and reused in `handlers.js`.
- **Dependencies:** none; the `withDeadline` helper this would reuse already exists in `assertions.js`.
- **Suggested order:** opportunistic — no known live repro yet, unlike the dialog case that forced the first fix.
- **Complexity:** S.
- **Success criteria:** `probePresent` gives up at its own timeout even when its predicate never resolves, verified by a unit test mirroring `pollPositive`'s "gives up at the deadline instead of hanging forever" test in `runtime/test/unit/test_verify.js`.

### EXEC-6 — `[UNVERIFIED]` Frame/shadow recovery hardening
- **Category:** Execution & Recovery
- **Description:** A second batch from the same top-50 list, specifically about iframe and shadow-DOM edge cases: forbid XPath for shadow targets at compile time and record the shadow host-path instead (#17); a multi-signal `FrameFingerprint` plus a frame-level recovery sub-tier for when a frame's id drifts (#18); a closed-shadow escape hatch (AX role+name → CDP pierce → vision, for closed shadow roots that hard-fail today, #24); frame/shadow-aware verification so post-condition checks read *inside* the correct frame/shadow boundary instead of false-passing or false-failing across it (#27); a wait-for-frame-attached gate for dynamically-injected iframes (#39); and a wait-for-shadow-upgrade gate for web components not yet upgraded when first queried (#40).
- **Why required:** iframe- and shadow-DOM-heavy enterprise products (Salesforce, ServiceNow, and similar) are explicitly called out in the research corpus as the hardest part of enterprise automation and a determinism-*and*-enterprise-moat item (`research-analysis/03-insights/master-insights.md` D3) — but today only the basic "iframe chain preserved verbatim" invariant is enforced; these finer-grained edge cases aren't yet handled.
- **Business value:** directly improves reliability on exactly the enterprise SaaS products (heavy iframe/shadow use) that are Conxa's target market.
- **Technical value:** builds on the already-enforced "iframe chain preserved verbatim" invariant (`session.py`) rather than replacing it — this is the next layer of edge-case hardening on top of an already-solid foundation.
- **Dependencies:** benefits from EXEC-1 landing first (the same conditional-step machinery is useful for "wait for frame attached"-style gating).
- **Suggested order:** per `build-order.md`'s sequencing, this is "build third" — after the verified floor and the action-correct handlers (EXEC-5), alongside the autonomous-recovery work (EXEC-4).
- **Complexity:** L — six related but individually-scoped fixes across the compiler (shadow host-path recording), the resolver (`FrameFingerprint`), and the recovery cascade (CDP pierce escape hatch).
- **Success criteria:** a recorded workflow targeting an open-shadow-DOM component (e.g., a Salesforce Lightning Web Component) survives a shadow-host id change without falling back to XPath; a closed-shadow target that hard-fails today is recoverable via the CDP escape hatch.
- **Progress (2026-09-04):** the recording half of #17 landed and is confirmed end-to-end — `bridge.js` now actually populates `shadow_path` by walking `getRootNode().host` chains (previously only the fragile `::part(`/`:host(` string-sniff fallback in `build.py` populated it, so real shadow-DOM clicks recorded `shadow_path: []`), and it falls back to the shadow host's own text/aria-label when the shadow-internal element being clicked has neither (found via a real `shoelace.style` `<sl-button>` repro — see `docs/testing/02-WORKFLOWS-PASSED.md` **P-8**). The compiler no longer trusts a shadow-sourced `css-structural`/`css-id` signal's `unique_at_compile` stamp either (its 0-match count from the shadow-blind HTML snapshot was being masked as "verified unique" instead of "unverifiable"). A fresh manual record → compile → replay against `shoelace.style/components/button` passed, compiling the shadow-DOM button click to a unique `role=button[name="Primary"]` selector at 0.95 confidence. #18 (`FrameFingerprint`/frame-level recovery sub-tier), #24 (closed-shadow CDP pierce), #27 (frame/shadow-aware verification), #39 (wait-for-frame-attached), and #40 (wait-for-shadow-upgrade) remain open.

### EXEC-8 — Structured Tier-5 human handoff + rule-triggered destructive escalation
- **Status (2026-08-29):** the destructive-escalation half is resolved — PROD-3's safety core now makes rule-triggered destructive escalation real (`identity_bundle.destructive` is actually set and reaches the runtime; `cascade.js` fails closed rather than falling through to a confident guess; the halt is excluded from agent-mediated park/resume). **Still open, and still this entry's own scope:** the general CAPTCHA/2FA/ambiguous-step "pause and hand to a human" Tier-5 state (`CALL_USER`) — PROD-3 did not build a human-handoff mechanism, only the refuse-to-guess half. See EXEC-21 for the closely related, still-open planned-human-review-point work.
- **Category:** Execution & Recovery
- **Description:** A first-class, designed "pause and hand to a human" state (top-50-improvements.md #28, matching the research corpus's UI-TARS-inspired `CALL_USER` pattern) for CAPTCHA/2FA/ambiguous/sensitive steps, triggered both by rules (a step tagged as sensitive/destructive) and by recovery exhaustion — plus rule-triggered escalation specifically for destructive actions (pay/delete/submit, #29) so an irreversible step never falls through to a "confident guess" recovery path.
- **Why required:** this is the same underlying need as PROD-3's safe-action system (danger-classified steps, refuse-to-guess on irreversible actions) approached from the reliability-engineering side of the research corpus rather than the business-risk side — **treat PROD-3 as the primary tracked item for this work and this entry as confirmation that the reliability research independently arrived at the same requirement**, not a second thing to build.
- **Business value:** see PROD-3.
- **Technical value:** see PROD-3; the "CALL_USER" framing here specifically emphasizes making the human-handoff state a first-class, designed state (not just falling through to a generic failure) with a clear audit trail of why it triggered.
- **Dependencies:** should be scoped and built together with PROD-3, not separately.
- **Suggested order:** alongside PROD-3.
- **Complexity:** see PROD-3.
- **Success criteria:** see PROD-3's success criteria — this entry exists to prevent someone independently re-scoping the same feature twice from two different source documents.

### UPD-3 — Authenticode-sign the runtime self-update binary
- **Category:** Auto Updates
- **Description:** Distinct from UPD-2 (which covers the Build Studio installer `.exe`), the runtime's self-update binary (`conxa-runtime.exe`, downloaded via the signed manifest) is itself still not Authenticode-signed — only its SHA-256 (sourced from the now-signed manifest) is verified. Per `docs/Security.md` SG-09, if the manifest-signing key were ever compromised, an attacker could still ship an arbitrary signed manifest entry pointing at a malicious binary, since there's no second, independent trust check on the binary itself.
- **Why required:** the manifest-signing half of this gap was already fixed (2026-07-01, Enterprise-Grade Auto-Update Architecture) — this is explicitly the "still open" half of a partially-fixed High-severity security gap, not a new finding.
- **Business value:** closes a real (if currently unexploited) supply-chain risk on the auto-update path that customers' security teams may ask about during enterprise due diligence.
- **Technical value:** adds a second, independent trust check beyond the manifest's own signature, so a compromised manifest-signing key alone is insufficient to install a malicious binary.
- **Dependencies:** none; independent of UPD-1/UPD-2 (different binary, different signing mechanism).
- **Suggested order:** can proceed in parallel with UPD-1; lower urgency than UPD-2 since it requires a compromised signing key to actually be exploitable.
- **Complexity:** M — add a `signtool` step for `conxa-runtime.exe` in `build-runtime-host.yml` (parallel to the Studio installer's signing step) and a verification check in `manifest_manager.js` before `--selfcheck`/activation.
- **Success criteria:** `conxa-runtime.exe` is Authenticode-signed as part of the build; `manifest_manager.js` verifies this signature independently of the manifest's own Ed25519 signature before activating a downloaded binary.

### UPD-4 — Move release-artifact hosting off GitHub Releases before the source repo goes private
- **Category:** Auto Updates
- **Description:** Per `research-analysis/ops/private-repo-migration.md`: the source repo can be made private, but release-artifact downloads (host exe, `keytar.node`, app-layer zip, Build Studio installer) currently default to public GitHub Release URLs consumed by unauthenticated customer clients — `bootstrap.py`'s deps-manifest fetch, the runtime's Ed25519-signed `manifest.json` self-updater, and the Cloud frontend's Studio-installer/electron-updater feed all resolve to `github.com/.../releases/download/...` URLs built from `CONXA_GITHUB_REPO`. Making the repo private without changing this breaks every one of those unauthenticated download paths. `build-runtime-host.yml`/`build-runtime-app.yml` construct the same GitHub Release URL inline in their manifest-publish steps, not from a shared env default, so they're the pieces most likely to be missed. (`promote-release.yml`, previously the trickiest spot, was removed 2026-09-17 — see UPD-5.)
- **Why required:** this is a real, actionable, already-fully-scoped migration plan sitting unimplemented in the research corpus — not a hypothetical concern. If the repo visibility is ever changed without this work landing first, every currently-installed runtime's self-update and every fresh Build Studio bootstrap breaks simultaneously.
- **Business value:** removes a blocker to making the source repo private (a reasonable IP-protection step for a company with paying customers) without breaking any existing customer installation's ability to update.
- **Technical value:** the source doc already specifies the target shape precisely: CI keeps building the same artifacts, but uploads them to Conxa-owned public artifact storage in addition to (or instead of) a GitHub Release, and `updates_routes.py`'s `_release_url()` plus the two build workflows' inline URL construction both point at the new base instead of GitHub. Signing/verification logic is unaffected — only the URL host changes.
- **Dependencies:** none blocking; can proceed independently of the actual repo-visibility change (do this first, then flip visibility once validated). Loosely related to CLOUD-2's blob/CDN storage work — both are "stop relying on ad-hoc storage for distributed artifacts" efforts and could share infrastructure decisions.
- **Suggested order:** before any decision to make the repo private, not after — do this whenever that decision is made, not urgently otherwise.
- **Complexity:** M — the source doc's own validation checklist (logged-out downloads of every manifest-referenced URL) is the acceptance test; the actual code change is concentrated in `updates_routes.py` and the two build workflow files.
- **Success criteria:** every item in `research-analysis/ops/private-repo-migration.md`'s own validation checklist passes from a logged-out machine, including a fresh tagged release that posts a stable manifest pointing at Conxa-owned artifact URLs, not GitHub Release URLs.

### UPD-5 — Set `CLOUD_API_URL`/`CLOUD_ADMIN_TOKEN` repo config so runtime releases actually publish
- **Category:** Auto Updates
- **Description:** Investigated 2026-09-17 while debugging "the runtime never auto-updates." Root cause: `build-runtime-host.yml`/`build-runtime-app.yml`'s manifest-publish step silently `exit 0`s when its target cloud API URL is unset (`if (-not $api) { ...; exit 0 }`) — and neither `CLOUD_API_URL` nor `CLOUD_ADMIN_TOKEN` (nor their `_DEV` counterparts) has ever existed as a GitHub repo variable/secret (`gh variable list`/`gh secret list` confirm this). So every CI build has shown green while never actually publishing a manifest record, on any channel, ever. Separately, the dev→stable promotion pipeline these workflows used to feed (`promote-release.yml`) has been removed — the hosted Dev cloud it promoted from was already deleted in commit `91eebe8`, so builds now publish straight to stable and only need the one (non-`_DEV`) pair of values.
- **Why required:** without these two values, no CI-built host or app release ever reaches any customer install, no matter how many releases get tagged.
- **Business value:** unblocks the entire runtime auto-update pipeline — currently every customer is stuck on whatever version their installer shipped.
- **Technical value:** none — this is pure configuration, not a code gap.
- **Dependencies:** none.
- **Suggested order:** immediate — this is the single blocker on the whole update pipeline.
- **Complexity:** S — set `CLOUD_API_URL` = `https://apis.conxa.in` and `CLOUD_ADMIN_TOKEN` = the prod Render service's own `CONXA_ADMIN_TOKEN` value as a repo variable/secret, then either re-tag or manually POST the already-built `host-v3.2.0`/`app-v3.2.0` artifacts (URLs/SHA-256 already known) to seed stable without a rebuild.
- **Success criteria:** `curl https://apis.conxa.in/api/v1/manifest.json` shows `conxa_runtime`/`conxa_app` at the latest tagged versions, and a subsequent tag+push updates it automatically with no manual step.

### TEST-1 — Remove or fix orphaned `conxa-builder/python` test files
- **Category:** Testing & Cleanup
- **Description:** `conxa-builder/python/test_installer_builder.py` and `test_bootstrap.py` are written as standalone `unittest`-style scripts with manual `sys.path` insertion. They're on `pythonpath` (per `conxa-cloud/pytest.ini`) but outside `testpaths` (which only points at `conxa-cloud/tests`), so they're importable but never actually collected/run by CI. They also diverge in content from same-named files that do run under `conxa-cloud/tests/` (e.g., differing line counts), suggesting they're stale duplicates rather than intentionally-separate coverage.
- **Why required:** dead, unrun test files create false confidence — someone reading the file tree might assume these provide coverage that CI is not actually exercising.
- **Business value:** none directly; this is test-hygiene housekeeping.
- **Technical value:** removes a source of confusion for future engineers and prevents dead code from silently accumulating further drift from the tests that do run.
- **Dependencies:** none.
- **Suggested order:** opportunistic — low complexity, do whenever convenient, e.g. bundled with other `conxa-builder/python` work.
- **Complexity:** S.
- **Success criteria:** either the two files are deleted (if `conxa-cloud/tests/`'s versions are confirmed to be strict supersets), or they're moved into `testpaths` and reconciled with the versions that do run.

### TEST-6 — Remove dead `refreshSession()` from `runtime/auth_manager.js`
- **Category:** Testing & Cleanup
- **Description:** `auth_manager.js::refreshSession()` (a blocking, in-process target-site re-login) has no callers left in `server.js` — it was superseded by the non-blocking interactive-login flow added 2026-08-02 (`browser.js::beginInteractiveAuth`/`captureReAuth`, see `docs/Auth-and-Updater.md` §1.3). The only remaining caller is `runtime/test/test_auth_recovery.js`, which exercises it directly.
- **Why required:** dead code that still has "real" test coverage reads as load-bearing when it isn't — a future change to the auth flow could be tempted to route through it instead of the actual (non-blocking) path.
- **Business value:** none directly; hygiene.
- **Technical value:** removes a second, divergent auth-failure-URL regex (duplicated from `run.js`) and a dead attempt-counter that no longer does anything useful once the real path is fixed.
- **Dependencies:** none.
- **Suggested order:** opportunistic.
- **Complexity:** S.
- **Success criteria:** `refreshSession()` and its dedicated tests are removed (or the tests are repointed at the real `beginInteractiveAuth`/`captureReAuth` path if the coverage is still wanted).

</details>

### TEST-10 — Move per-event diagnostics off the recorder's hot path; remove dead a11y-capture thread
- **Category:** Testing & Cleanup
- **Description:** Found during BUILD-15's flicker investigation (see its resolution note), neither is the flicker's cause, both still open. (1) `_rewrite_events_jsonl` (`conxa_compile/recorder/session.py`) calls `_write_diagnostics_sync` on **every recorded event** — a `frame.evaluate()` per frame on the page plus `frame_element()` + 2 more evaluates per subframe, and a full `events.jsonl` rewrite from scratch — when it's a diagnostics dump that only needs writing once, at session end (which already happens separately in the shutdown path). (2) `_capture_a11y_async` (same file) spawns a daemon thread per recorded event to call `page.accessibility.snapshot()` — an API Playwright removed; installed version is 1.58.0, confirmed via `hasattr(Page, 'accessibility')` returning `False`. Every call raises `AttributeError` instantly, swallowed by a bare `except`, so `settings.snapshot_capture_a11y` does nothing and every event pays for a wasted thread.
- **Why required:** both scale with recording length and page complexity for zero benefit — (1) is O(n²) CDP traffic over a session, (2) is pure waste.
- **Business value:** none directly; performance/hygiene.
- **Technical value:** removing (1) also reduces per-event latency (fewer synchronous CDP round-trips on the path that decides how quickly a click gets recorded); removing (2) deletes a permanently-broken code path (and its `_last_a11y_capture_time`/`_a11y_skip_count` state) before some future compatibility issue makes its failure mode less obviously silent.
- **Dependencies:** none.
- **Suggested order:** opportunistic.
- **Complexity:** S.
- **Success criteria:** `_write_diagnostics_sync` runs once per recording session, not once per event; `_capture_a11y_async` and its call site are removed (or, if a11y capture is still wanted, reimplemented against whatever the current Playwright version's actual accessibility API is called).
- **Partially resolved 2026-09-01:** item (2) — `_capture_a11y_async` is fixed, not removed, per the "reimplemented" branch above; see `~~BUILD-10~~` for the implementation. Still one thread per event (unchanged scope — the thread+2s-timeout structure is what isolates a slow/hung call, not incidental), but it now does real work instead of instantly raising and being swallowed. Item (1), the per-event diagnostics dump, is untouched — still open.

---

## P4 — Low Urgency, Opportunistic (37 remaining)

### AUTH-10 — A same-host app whose success page is public could pass the signed-out-baseline check while actually signed out
- **Category:** Execution & Recovery / Authentication
- **Description:** `isSignedInAgainstBaseline` (see AUTH-9 in `Done.md`) treats "differs from the genuine signed-out baseline, and isn't itself login-shaped" as signed in. For an app whose sign-in and success pages share a host — and whose success page (or some other page the probe happens to land on) is ALSO publicly viewable when signed out, with a URL that doesn't match `/login`-style patterns and shows no password box — a signed-out probe could read as "signed in." Narrow in practice: GitHub's own `/login` always shows a password box when signed out, so this doesn't currently bite anyone; flagged because the mechanism makes it theoretically possible for some future app.
- **Why required:** correctness edge case in the new baseline-compare auth mechanism, not urgent — no known app currently triggers it.
- **Complexity:** S–M (would need a stronger baseline signal — e.g. an authenticated marker check similar to AUTH-8's `accountNameProbe`, run against the app's actual landing page — if a real site is ever shown to need it).
- **Partially mitigated by AUTH-13 (`Done.md`):** an app whose sign-in was *learned* (has a saved `auth_definition`) is not exposed to this gap by construction — the definition is built from a genuine LIVE-vs-OUT contrast and self-tested (OUT must not evaluate "yes") before it's ever saved. This item stays open for the app that has no learned definition yet (never connected under the new flow, or force-saved past a failed self-test) and still falls back to the plain baseline compare.
- **Also narrowed by AUTH-15 (`Done.md`):** the generic check now treats a login segment at ANY path depth as login-shaped (`LOGIN_PATH_RE`), which closes the case where a signed-out probe lands on a login-*shaped* page that merely differs from the baseline (Google's `/v3/signin/accountchooser`). What remains open is only the truly public, non-login-shaped success page described above.

### AUTH-17 — A signed-out Google Drive lands on a marketing page that `isAuthFailure` doesn't recognise
- **Category:** Execution & Recovery / Authentication
- **Description:** Observed 2026-09-23 on the Github→Drive Run Test: with no usable Google session, `drive.google.com` redirected to `workspace.google.com/intl/en-US/products/drive/` — a public marketing page with no `/login` or `/signin` segment and no "sign in" title — so step 7 failed as "Ambiguous element resolution (no signal cleared uniqueness gate)" on the "New" button instead of as a session expiry (see `sandbox/data/runs/<id>/_evidence/evidence.json`). A dead remembered-account session lands on `/v3/signin/accountchooser` and IS recognised (AUTH-15). Pre-flight (group-level gating, AUTH-14, plus learned definitions, AUTH-13) should now stop a signed-out Google reaching the step at all; this is the mid-run backstop for an app where it doesn't.
- **Why required:** mislabels an auth failure as a selector problem and sends the person to edit a healthy workflow.
- **Complexity:** S — needs a generic signal (e.g. when the app has an `auth_definition`, evaluate the failing page against its signed-out markers/path) rather than another URL pattern.

### TEST-13 — `integration_host_lock.js` and `integration_auth_detached.js` fail on a clean checkout since AUTH-9
- **Category:** Testing
- **Description:** Both real-server e2e files (run by hand — `npm test` covers only `test/unit`) fail 4 checks each on clean HEAD (verified 2026-09-25 in a throwaway worktree). Their fixtures assume the pre-AUTH-9 detection ("an empty storage state counts as signed-in when the app lands on a non-login URL", per `integration_host_lock.js`'s own comment); the local fixture server answers every path with the same page, so the genuine signed-out baseline equals the probe and nothing ever validates. Make a pre-seeded session distinguishable from the cookie-less baseline (e.g. the server redirects `/login` → `/home` only when a session cookie is present, and the fixture seeds that cookie) or give the fixture apps a learned `auth_definition`. `integration_group_auth_error_surfacing.js` is unaffected.
- **Why required:** they guard host-lock keying and the detached sign-in flow, which are currently untested.
- **Complexity:** S–M.

### RT-5 — Sync/telemetry keys skill-pack files by the installer's on-disk folder name, not `workspace_id`
- **Category:** Runtime / Testing & Cleanup
- **Description:** Found during a 2026-09-25 legacy/error-handling audit. `runtime/app/sync.js`, `sync_errors.js`, `installed_versions.js`, and `durable_context.js` all use the skill-packs directory name as if it were the workspace id. That folder name is `workspace_dir_slug(installer_name)` (the company domain the installer was built with — `installer_builder.py`'s deliberate, cosmetic-only exception to the `workspace_id`-is-the-identity-key rule, see CLAUDE.md Key Invariants) and only falls back to the real `workspace_id` slug when no domain was given. The real cloud workspace id lives at `pack.published.workspace_id` / `pack.tracking.workspace_id`. When a domain was set, `sync_errors.collectSyncErrors()`'s per-workspace keys, `installed_versions`, and the generated SKILL.md's `execute_skill` hint all carry the domain slug instead.
- **Why required:** cosmetic today (the folder name still round-trips correctly for sync/tracking, which use the bearer token, not the folder name, to authorize), but any future cloud-side lookup by folder-name-as-workspace-id would silently resolve the wrong workspace.
- **Complexity:** S — read `pack.published.workspace_id` (falling back to the folder name only if absent) at the handful of call sites instead of the folder name directly.

### RT-6 — `file_lock.js`'s claim phase is check-then-write, not atomic (no `O_EXCL`)
- **Category:** Runtime
- **Description:** Found during the same 2026-09-25 audit. `acquireFileLocks`'s claim phase (`_writeAll`) reads every lock file to confirm it's free, then writes all of them — two processes can both pass the free-check in the same window and both believe they hold the platform. The module's own `ponytail:`-style comment already flags this as a known, deliberate simplification (defense-in-depth behind the in-process `host_lock.js` map, not the sole guard).
- **Why required:** low severity today (the in-process map already prevents same-engine double-acquire; this only matters across two separate engine processes racing within the same poll window), but worth a real fix before cross-process concurrency is relied on for anything higher-stakes.
- **Complexity:** S/M — write with `wx` (fail-if-exists) instead of `w`, treating an `EEXIST` as "someone else just claimed it" rather than success.

### RT-7 — `canonical_json.js` doesn't provably match the cloud's Python `json.dumps(sort_keys=True)`
- **Category:** Runtime
- **Description:** Found during the same 2026-09-25 audit. The runtime's `canonicalJSON` (used for both telemetry evidence-chain hashing and manifest-signature verification) has no cross-language test vector against the cloud's Python signer. Known JS/Python divergences that could silently break signature verification fleet-wide: an integral float serializes as `50.0` in Python vs `50` in JS, exponent notation differs (`1e-07` vs `1e-7`), and non-BMP-character key sort order differs (Python compares by code point, JS string comparison is UTF-16-code-unit-based).
- **Why required:** a single float slipping into a signed manifest's `rollout`/`minimum_versions` from an admin form would make every client's signature check fail silently (see `manifest_manager.js`'s already-fixed string-percentage bug, this same audit, for how an unexpected type gets into that payload).
- **Complexity:** S — add a shared cross-language test vector (same input dict, assert identical output string in both `canonical_json.js` and the cloud's Python signer), and coerce numeric manifest fields to `int` server-side before signing as a belt-and-suspenders fix.

### RT-8 — Remaining items from the 2026-09-25 runtime legacy/error-handling audit
- **Category:** Runtime / Testing & Cleanup
- **Description:** A full audit of `runtime/` (this session, see `FIX.md` 2026-09-25 "Cleaned up old, unused code…") found substantially more silent-fallback and dead-legacy-shim instances than fit in one session's scope. Fixed: the highest-risk bugs (retry budget, `.first()`-of-many-matches clicking, check/assert step evaluation, closed-tab rebinding, update-gate/file-lock fail-open, scheduler input-decrypt/stale-command handling, telemetry spill, a missing `errors.js` shaping helper) plus dead-code removal in `locators.js`/`resolution.js`/`recovery.js`/`resolve_adapter.js`/`assertions.js`, plus the compiler-side `validation.assertions` gap (RT-5/RT-6/RT-7 above are the three follow-ups that got their own items). Still open, not yet fixed: `browser.js`'s corrupt-pack-fallback wording and plaintext-session-on-encryption-failure path, `skill_loader.js`'s silent corrupt-manifest drops, `policy_gate.js`'s unlogged signature-verification failures, `bootstrap.js`/`min_host_gate.js`'s missing per-reason messages, `host/cli_sync.js`'s always-exit-0 behavior, `version_manager.js`'s real-directory-at-`current` handling, `server.js`'s six duplicated early-return teardown blocks and four duplicated exit-teardown blocks (one already missing an `await`, per its own comment), dead legacy branches in `handlers.js` (`upload_intent`, `recovery.alternatives`) and `server.js` (the old `AppData\Local\Conxa\chromium` path, `input.json`, `published.workspace_id`/`tracking.company_id`), and `browser.js`'s unused `CONXA_LOGIN_SETTLE_MS`/`_successPrefix`/`_loginEntryUrl`/non-group `captureReAuth` leftovers.
- **Why required:** none of these are known-exploitable or currently reported as breaking a real run (unlike the ones fixed this session) — they're maintainability debt and lower-probability silent-fallback risk, not active bugs.
- **Complexity:** M — mostly mechanical per-file cleanup following the same pattern already established this session (see the four `fix(runtime)`/`refactor(runtime)`/`fix(compiler)` commits on this session's branch for the exact shape of each fix category).

### PROD-7 — Connector graduation path
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** When a mature skill has run enough times to prove its value, offer to generate a draft *official* API connector from the recording plus the network calls Conxa's recorder observed behind each step, and keep running the same governed skill on top of the faster backend once it exists.
- **Why required:** answers the "market squeezed from both sides" risk on the "vendors eventually build their own connector" side — turning what would otherwise be lost business (a vendor graduating away from browser automation) into a paid upsell instead of churn.
- **Business value:** converts Conxa's biggest long-term structural threat (vendors building official connectors) into expansion revenue rather than attrition.
- **Technical value:** depends on the recorder already capturing network-call context per step (a capability that would need to be added — see RT-2) and is described in the source material as strategic/large, not a quick win.
- **Dependencies:** benefits from RT-2's recording-depth work and, conceptually, from BUILD-1's compiler IR (a clean intermediate representation makes "generate a connector from this" a more tractable transform).
- **Suggested order:** later — explicitly framed as the "endgame" item in the source doc's own build-plan ordering, not a near-term priority.
- **Complexity:** XL — this is closer to a new product surface than a feature.
- **Success criteria:** at least one mature, high-volume skill has a generated draft connector that a vendor can inspect and choose to adopt as a paid upgrade.

### PROD-8 — Trust & GTM: self-serve AI-operability scanner + "Works with Claude" badge
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** A free, self-serve tool where a vendor pastes their product's URL, and in minutes gets an "AI-operability score" plus a "Works with Claude" badge they can display on their own site.
- **Why required:** answers "big software companies don't need Conxa" for the *small/mid-market* segment Conxa actually targets — the source material frames this as a low-cost way to generate warm, pre-qualified leads (every vendor who runs the scanner has already seen their own good score) instead of paying salespeople to argue the case cold.
- **Business value:** a marketing/lead-gen mechanism, not core product — small vendors are described as buying "marketing wins" faster than they buy tools, and the badge is exactly that.
- **Technical value:** low — this is closer to a lightweight product-marketing tool than core infrastructure, though it does quietly generate data on which kinds of products fit Conxa best.
- **Dependencies:** none.
- **Suggested order:** opportunistic — a marketing investment, not a reliability or architecture item; sequence whenever GTM capacity allows.
- **Complexity:** M.
- **Success criteria:** the scanner is live and self-serve; at least one vendor converts from "ran the scanner" to "became a customer" without a sales call in between.

### PROD-13 — `[DECISION]` Platform-giant strategy: ecosystem partner channel + acquisition-asset posture
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** The recorded answer (2026-07-07 founder discussion) to "how do enterprise giants like Salesforce ever pay us, work with us, or buy us." A giant will never pay Conxa to automate its own product — it owns the product, the API, and an agent team, so that pitch loses structurally (`conxa-critical-analysis.md` P1/§4). The four paths that *do* work, all reached by winning SMB first, not by pivoting: **(1) Ecosystem partner channel** — the pitch to a platform giant is "we make your ISV marketplace agent-ready": its thousands of small marketplace vendors are exactly Conxa's ICP, and a sanctioned partner program makes the *platform* more valuable in the agent era — the one framing where a giant promotes or pays for Conxa directly. **(2) Their internal long tail** — giants' own API-less internal/acquired/legacy tools; already a named paying segment (critical-analysis §7), gated purely by the trust package (PROD-10, CLOUD-1), no new product work. **(3) The fleet durability dataset** as the asset a giant cannot backfill without a fleet — the primary acquisition asset (capture protected by EXEC-9; learning built by EXEC-2). **(4) The skill contract as the standard above the executor** (ARCH-3) — own the governed-workflow format and whichever execution technology wins still needs the layer above it. The `[DECISION]` half: founders must decide *when* to open the partner-program conversation (it needs Proof A/Proof B numbers in hand first) and which platform to approach first. **Extension (same 2026-07-07 discussion, second pass):** the follow-up brainstorm reframed the search as "where does the browser beat APIs *even for a giant with infinite engineers*" — two zones: where integration payback math never arrives (temporary/long-tail work) and where the screen itself is the ground truth (what a human sees is the thing being verified). That produced three further giant-compatible plays now tracked separately — compliance evidence packs (PROD-14), M&A/legacy-sunset bridge automation (PROD-15), and the tenant-ownership verification decision that unlocks SaaS-tenant use cases (PROD-16) — plus two folded here as sub-paths: the **"MCP gateway for the legacy estate"** framing (path 2 sold as agent-enablement infrastructure to the CIO with a make-AI-useful mandate, not as automation) and **executable runbooks / enterprise tenant regression testing** (recordings as living SOPs and as release-retest suites for heavily customized SaaS tenants — the latter blocked on PROD-16).
- **Why required:** without this on record, the natural instinct — adding features aimed at "automating Salesforce for its customers" — would violate the domain-verification policy (PROD-6), re-enter the bot-blocker arms race the Answer 11 decision deliberately exited, and compete with native APIs exactly where they always win.
- **Business value:** defines the only credible route to giant-scale revenue/partnership/acquisition, and — equally important — what *not* to build or sell in the meantime.
- **Technical value:** mostly a GTM/strategy item, but it has three concrete engineering echoes already tracked elsewhere: ARCH-3 (contract boundary), EXEC-9 (dataset capture), and a delegation-aware design for PROD-6's domain verification (a platform vouching for its ISVs' domains, an enterprise IT org vouching for internal domains — don't hard-wire "one vendor, one DNS record").
- **Dependencies:** Proof A (paying SMB vendors retained through a redesign) and Proof B (cross-account numbers) are hard prerequisites for any giant conversation; PROD-5's multi-host registration must be demo-true (a platform partnership runs through the partner's agent surface, not Claude Desktop); PROD-6, PROD-10, ARCH-3, EXEC-9 as above.
- **Suggested order:** the engineering echoes are sequenced under their own items; the partner-program conversation itself waits for the proofs — revisit at the first quarterly review after Proof B numbers exist.
- **Complexity:** S for the decision; the BD motion itself is not an engineering item.
- **Success criteria:** a written founder decision on partner-program timing and first target platform; no roadmap item exists that automates third-party products the customer doesn't own; PROD-6's verification design review confirms delegation is possible without rework.

### PROD-14 — Compliance evidence collection as code ("audit evidence packs")
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** Package Conxa's existing machinery — deterministic replay, Strict Mode (recovery ceiling capped, zero AI in the loop), step-by-step logs, and PROD-3's before/after screenshots — as a compliance-evidence product: a scheduled "evidence pack" skill that walks each in-scope system to its access-review/config screen and produces a timestamped, screenshotted, replayable evidence bundle for SOX/SOC 2/ISO/internal-audit cycles. Enterprises currently do this with armies of analysts every audit season, and auditors specifically require *screen-level* evidence of what a user can see and do — an API dump is not acceptable evidence under their own rules.
- **Why required:** this is one of the two zones (2026-07-07 discussion, see PROD-13) where browser automation beats native APIs *even for a giant with unlimited engineers* — here because the screen itself is the ground truth being attested. It's a route to Fortune-500 revenue that competes with **zero** native APIs and requires no new core architecture.
- **Business value:** a recurring (quarterly/annual, audit-calendar-driven) enterprise purchase; "deterministic, zero-AI-in-the-loop, fully screenshotted" is a sentence auditors and compliance officers respond to, and Strict Mode makes it literally true.
- **Technical value:** almost entirely packaging and skill templates on top of shipped or already-tracked machinery (Strict Mode productization and before/after screenshots are PROD-3; scheduling is PROD-5); the new work is an evidence-bundle output format (signed, timestamped archive) and template skills.
- **Dependencies:** PROD-3 (Strict Mode + screenshots) and PROD-5 (scheduler) for the full unattended version. Scope note: evidence collection on the enterprise's *own internal* systems passes today's domain-verification policy; extending packs to their vendor-SaaS tenants (the "40 systems" version) is gated on the PROD-16 tenant-ownership decision.
- **Suggested order:** opportunistic — becomes concretely sellable once PROD-3 and PROD-5 land; the template/bundle-format work is small enough to pilot with one design partner's internal systems before that.
- **Complexity:** M — evidence-bundle format, template skills, and audit-calendar scheduling glue; no new core capability.
- **Success criteria:** one pilot enterprise replaces a manual screenshot-collection cycle for at least one audit with a Conxa evidence pack, and their auditor accepts the bundle as evidence.

### PROD-15 — M&A / legacy-sunset "bridge automation" + shadow-run migration validation
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** A GTM motion plus one feature. The motion: sell Conxa for **deliberately temporary** automation — systems inherited in an acquisition or scheduled for retirement, where no one will ever build an API integration because the payback never arrives before the system dies, so the work is done by hand for the entire 12–24-month transition. A business analyst records the workflow on the dying system in an afternoon; it runs until switch-off. The feature: **shadow-run validation** — during a migration, execute the same recorded workflow against the old and the new system and diff the outcomes (assertions, extracted values, end-state screenshots) to prove the new system behaves equivalently before cutover.
- **Why required:** this is the second "browser beats APIs even for giants" zone from the 2026-07-07 discussion (see PROD-13) — the *payback math*, not engineering capability, is what rules the API out, so the giant's infinite engineers are irrelevant. Every large enterprise is perpetually mid-migration somewhere; acquired systems' domains transfer to the acquirer, so this passes the existing domain-verification policy with no policy change at all.
- **Business value:** a wedge into exactly the enterprises that "would just build an API" everywhere else; M&A integration budgets are large, deadline-driven, and pre-approved, and "temporary" removes the biggest enterprise-automation objection (long-term maintenance commitment).
- **Technical value:** the motion needs nothing new; shadow-run validation builds naturally on the existing assertion machinery (`ValidationBlock`) plus a run-comparison report, and doubles as a general regression tool (also useful to PROD-11's skill CI).
- **Dependencies:** none for the GTM motion. Shadow-run benefits from PROD-1 (calibration, for the new system's account) and composes with PROD-14's evidence-bundle output format for the cutover-signoff report.
- **Suggested order:** the motion is available as soon as there's enterprise-grade trust packaging (PROD-10); shadow-run is opportunistic feature work after the assertion/report machinery stabilizes.
- **Complexity:** S for the GTM positioning; M for shadow-run (paired execution + outcome-diff report).
- **Success criteria:** one enterprise uses Conxa skills as the bridge for an acquired or sunsetting system through to its retirement; shadow-run produces a side-by-side outcome report a migration team signs off on.

### PROD-16 — `[DECISION]` Tenant-ownership verification (extend "own the domain" to "own the tenant")
- **Category:** Product Strategy & Business-Risk Mitigation
- **Description:** Decide whether — and under what rules — the domain-verification policy (PROD-6) gets a second, explicitly consented verification mode: proving **admin control of a SaaS tenant** (a verification token placed inside the tenant, Search-Console-style, e.g. for `myorg.lightning.force.com`) rather than DNS control of the domain. An enterprise doesn't own `salesforce.com`, but it does own its tenant, its data, and its configuration. This single decision gates a cluster of enterprise use cases: vendor-SaaS coverage in PROD-14's evidence packs, enterprise tenant regression testing (retesting heavily customized SaaS orgs after vendor seasonal releases — see PROD-13), and long-tail IT ops on SaaS without SCIM/APIs (e.g., offboarding).
- **Why required:** the critical analysis (Answer 11) explicitly warned that any loosening of "only automate what you own" must be a deliberate, explicitly consented mode — "not something to drift into." Several recorded enterprise plays now genuinely need the extension, so the decision needs to be made on purpose, with its own rules, or those plays stay out of scope on purpose. Either answer is acceptable; drifting is not.
- **Business value:** unlocks (or consciously forgoes) the largest slice of the enterprise use-case cluster from the 2026-07-07 discussion; also keeps the clean "you cannot violate your own terms" legal posture intact, since a tenant admin operating their own tenant is inside their contract with the vendor in the ordinary case.
- **Technical value:** if approved, implementation is moderate and self-contained (a tenant-token verification flow beside the DNS flow in the publish gate); the harder work is the policy text — what tenant verification does and does not permit (e.g., respect for the SaaS vendor's automation/ToS terms remains the customer's responsibility, stated explicitly).
- **Dependencies:** PROD-6 must land first (this is a second mode beside it, and PROD-6's delegation-aware design note already anticipates non-DNS verifiers); the consuming use cases are PROD-14 (SaaS scope) and the tenant-regression play in PROD-13.
- **Suggested order:** decide when the first design partner asks for a SaaS-tenant use case — before then it's speculative; after then it blocks revenue.
- **Complexity:** S for the decision; M for the verification-flow implementation if approved.
- **Success criteria:** a written founder decision (yes with rules, or explicit out-of-scope) recorded here and in `docs/Security.md`'s policy section; if yes, the publish gate supports tenant-token verification as a distinct, consented mode with its own policy text.

### DOC-3 — Verify the 3 flagged papers in `unverified-papers.md`
- **Category:** Documentation & Process
- **Description:** `research-analysis/01-external-research/papers/unverified-papers.md` flags three arXiv IDs (`2402.10157v1`, `2501.09903v3`, `2501.12988v1`) whose metadata suggests they're outside the web-automation domain (control theory, quantum computing, semantic communications respectively). This was re-confirmed via metadata lookup during this audit (2026-07-04 stamp added) but the underlying PDFs were never actually opened.
- **Why required:** if these are genuinely mislabeled/irrelevant, they're dead weight in the research corpus; if they're actually relevant (unlikely given the metadata signal), the corpus is missing their analysis.
- **Business value:** minimal — this is research-corpus hygiene, not a product concern.
- **Technical value:** keeps the `01-external-research/` corpus internally consistent with its own priority-ranking claims.
- **Dependencies:** none.
- **Suggested order:** opportunistic — whenever someone has the PDFs handy (see the `pdftotext` snippet already in the file).
- **Complexity:** S.
- **Success criteria:** each of the 3 papers gets either a full dossier (if relevant) or a one-line "confirmed off-topic, removed from corpus" resolution in the same file.

### BUILD-2 — Build Studio UX backlog (from `docs/UI-UX-Brief.md` §9)
- **Category:** Builder
- **Description:** A set of smaller Build Studio UI improvements identified in the UI/UX audit that were never carried into an engineering backlog: a guided HumanEdit review checklist before sign-off; parameterization auto-suggest (detect email/name/date-shaped values during HumanEdit and suggest `{{variable}}` templating); ~~a workflow pipeline visualization (`Recorded → Compiled → Reviewed → Signed Off → Built → Deployed`) replacing plain status text~~ **Resolved 2026-07-07** — `handlers/status.py::derive_workflow_stage` + `StagePath`/`WorkflowStageBadge` (see `docs/Implementation-Plan.md` §1.9, the Build Studio workflow redesign); persisted, prominently-shown test results with a "test required before publish" gate; bulk compile (select multiple recordings, compile in sequence with per-item progress); installer version history (version/date/sha256 list on the Build Installer page); publish-without-installer-rebuild for content-only updates (the delta sync already supports this — this is a UI path to trigger it directly — tracked as Phase 4 of the workflow redesign); and an execution-dashboard widget embedded in Build Studio showing the last 10 runs across deployed plugins.
- **Why required:** these are all still-open items from the UX audit's own "Priority 2/3" improvement lists that were never converted into tracked engineering work.
- **Business value:** each is a small usability improvement rather than a blocker; collectively they reduce friction for the people actually using Build Studio day-to-day (the "guided checklist" and "test-required gate" items also reduce the risk of a badly-reviewed skill reaching customers).
- **Technical value:** low-to-moderate individually; "publish without installer rebuild" in particular is mostly UI work since the underlying delta-sync capability already exists.
- **Dependencies:** none blocking; overlaps PROD-11's "fast re-record" dashboard item — sequence together if convenient.
- **Suggested order:** opportunistic, low urgency — pick off individually as Build Studio UI work comes up.
- **Complexity:** S–M per item; M overall if batched.
- **Success criteria:** each listed item ships as a discrete, independently-shippable UI change; track completion per sub-item rather than requiring the whole list to land at once.

### CLOUD-21 — Blob/CDN storage + durable job queue
- **Category:** Cloud
- **Description:** Skill-pack files persist as base64 blobs in a Postgres KV namespace (`skillpack_files__{slug}`) — durable, but not built for scale, and `blob_read_write_token` config exists but is unwired. **Installer binaries no longer do:** commit `a4dabc6` cut the `content_base64` copy out of `installer_versions__{slug}` (a ~20 MB blob doesn't belong in a JSONB field), so `installer_versions__{slug}` now holds metadata only and the `.exe` itself lives on the Render local disk alone. `load_installer_from_db()`'s disk-wipe fallback therefore only rehydrates installers uploaded *before* that commit; anything newer 404s (`installer_not_published`) after a disk wipe until the vendor re-uploads. **This makes object storage a durability requirement for installers, not just a scale one — verify the Render service has a persistent disk mounted before relying on installer download links in front of a customer.** Separately, `worker.py` (a queue scaffold referenced by older docs) doesn't exist anywhere in the current repo — the durable job queue it implied was never actually built.
- **Why required:** installer download links break after any Render disk wipe (the free plan has no persistent disk and idles out), which is customer-facing; and base64-in-Postgres doesn't scale indefinitely for the skill-pack files that still use it — a real problem if uploads approach the `build_artifact_upload_max_bytes` (250 MB) limit regularly, or if DB storage cost/limits become an issue at higher customer volume.
- **Business value:** avoids a future scaling wall, but isn't blocking anything today — flagged as a scalability concern to watch, not an active fire.
- **Technical value:** moves large-blob storage to infrastructure built for it (CDN/object storage) and gives the platform a real durable queue for anything that needs one (e.g., the fleet-flywheel automation in EXEC-2, if it ends up needing asynchronous job processing).
- **Dependencies:** none blocking; EXEC-2 may want a durable queue depending on how its automation pipeline is built. Overlaps UPD-4's release-artifact hosting move — both are "get off ad-hoc storage" items and could share infrastructure decisions.
- **Suggested order:** revisit when installer sizes or DB storage costs actually approach a limit, or when EXEC-2's design calls for asynchronous job processing — not urgent before either trigger.
- **Complexity:** M/L — wiring `blob_read_write_token` to a real object-storage backend is more contained than standing up a full durable queue from scratch.
- **Success criteria:** installer/skill-pack blobs above a defined size threshold are served from object storage/CDN rather than Postgres; a real job queue exists if/when something in the platform needs asynchronous processing.

### CLOUD-23 — Verify a Resend sending domain for bug report emails (shipped 2026-09-24)
- **Category:** Cloud
- **Description:** The new dashboard "Report a bug" page (`docs/Backend-Schema.md` §5.9b) sends via Resend, using the default `onboarding@resend.dev` sender. That address only delivers to the Resend account owner's own inbox — fine for one internal `bug_report_to_email`, but it will silently start failing (or need a different from-address) the moment reports should reach a shared team inbox or a different domain.
- **Why required:** avoids reports quietly not arriving once the team inbox changes from the Resend account owner's address.
- **Business value:** keeps a customer-facing feedback channel actually working.
- **Technical value:** small — verify a domain in Resend, set `SKILL_BUG_REPORT_FROM_EMAIL` to an address on it.
- **Dependencies:** none.
- **Suggested order:** before `SKILL_BUG_REPORT_TO_EMAIL` is set to anything other than the Resend account's own address.
- **Complexity:** S.
- **Success criteria:** bug report emails deliver reliably to the real team inbox, not just the Resend account owner's address.

### MCP-2 — Skill discovery manifests + Cloud discovery endpoint
- **Category:** MCP
- **Description:** Per `research-analysis/07-go-to-market/agentic-discovery-strategy.md`'s roadmap: add `skill.json` manifest generation to every published skill pack (`plugin_builder.py`), a `GET /api/v1/discover/skills` Cloud endpoint plus a per-skill `GET /api/v1/discover/skills/:id`, with the manifest response including the full `execution` block (endpoint + `runtime_bootstrap`), and a Cloud dashboard surface showing the "ARD Discovery URL" per published skill pack. Confirmed unbuilt — the only `skill.json` references in the current codebase are in an `OBSOLETE_WORKFLOW_FILENAMES` list (an old, unrelated schema artifact), not this manifest.
- **Why required:** positions Conxa's skill packages to be discoverable by any agentic-resource-discovery-compliant AI agent, not just Claude Desktop — the source doc frames MCP-native execution as Conxa's moat regardless of which agent discovers/calls it, so a standard discovery manifest is a distribution-channel expansion, not an architecture change.
- **Business value:** the source doc's own framing: "record once in Build Studio → every AI agent your customer uses discovers and runs it reliably" — an emerging distribution channel, positioned early rather than reactively once a discovery standard consolidates.
- **Technical value:** low-risk, additive — a new manifest format and two new read-only discovery endpoints, no changes to existing execution paths.
- **Dependencies:** none blocking; naturally sequenced with MCP-1's `ServerBackend` refactor and MCP-3's local HTTP surface, since all three touch the same "what can external callers see and call" boundary.
- **Suggested order:** speculative/early-market-positioning — lower urgency than the reliability-phase items, but cheap enough to fit in alongside MCP-1.
- **Complexity:** M.
- **Success criteria:** every published skill pack has a `skill.json` manifest; `GET /api/v1/discover/skills` returns discoverable metadata for a company's published skills.

### MCP-3 — `[UNVERIFIED]` Local HTTP server for multi-agent runtime access
- **Category:** MCP
- **Description:** Per the same roadmap, add a local HTTP server to `runtime/server.js` (bound to `127.0.0.1`, e.g. port 7823) exposing `/health`, `/skills`, `/skills/:id`, `/execute`, `/status/:exec_id`, authenticated with the existing per-company token from `auth_manager.js` as a Bearer header — so any agent framework that can make an HTTP call (OpenAI Agents SDK, etc.), not just MCP-speaking clients, can discover and execute Conxa skills.
- **Why required:** this is the mechanism that makes PROD-5's "Conxa doesn't need Claude specifically" thesis concretely true for non-MCP agent frameworks, not just other MCP hosts (which already work via the existing MCP stdio server).
- **Business value:** removes the last architectural tie to MCP-speaking clients specifically, widening the addressable set of "AI agents that can call a Conxa skill" beyond Claude Desktop, ChatGPT desktop, and Cursor.
- **Technical value:** additive to the existing `server.js` — a new transport alongside the existing MCP stdio interface, reusing the same skill-execution machinery and auth model.
- **Dependencies:** natural to build alongside MCP-1's `ServerBackend` refactor, since a second transport is exactly the kind of thing that seam is meant to make cheap to add.
- **Suggested order:** after MCP-1, or bundled with it.
- **Complexity:** M — mostly wiring an existing execution path behind a new local HTTP listener with the same auth model already in place.
- **Success criteria:** a non-MCP agent framework can call `POST http://127.0.0.1:7823/execute` directly and get the same result as calling `execute_skill` over MCP.

### EXEC-7 — Selector-scoring & misc reliability refinements
- **Category:** Execution & Recovery
- **Description:** A smaller set of standalone refinements from the same top-50 list that don't need their own dedicated epic: penalize GUID-like/volatile ids in live scoring so a stable signal is preferred over a volatile one even when the volatile one currently matches (#36); de-rank `position_hint` specifically for content confirmed to be dynamic, since position breaks first on reflow (#37); an anchor/relational re-find tier that locates a text-drifted target via its recorded neighboring elements (#38, builds on the existing `anchors/` module); a content-based stall/loop fingerprint (URL + element-count + DOM-text hash) that hard-caps recovery retries when the page genuinely hasn't changed between attempts — distinct from and complementary to the existing simple attempt-count cap (`RETRY_BUDGET_MAX = 3` in `run.js`, which is already shipped and doesn't inspect page content, #22); reflection-in-output for the Tier-3 prompt, paired with the already-shipped independent post-condition check since reflection is the model's belief, not verified truth (#44); an AX-tree rank-and-cap digest for Tier-3 recovery input so the intended element is never the one silently truncated away on a large page (#45); an optional CDP-based engine for bot-detection-heavy targets (#48); and role/text-based identity for context-menu items that render at the document body root rather than near their trigger (#49); plus deferred/soft post-condition batch reporting — collecting non-fatal assertion failures across a run and reporting them all at the end instead of failing on the first one (#50).
- **Why required:** each closes a specific, narrower edge case than the Tier-1 items already shipped; individually lower-impact than EXEC-5/EXEC-6 but still concrete, previously-identified gaps.
- **Business value:** incremental reliability improvement across a long tail of specific failure modes rather than one headline fix.
- **Technical value:** several of these (GUID penalty, position_hint de-rank, stall fingerprint) are refinements to selector scoring and recovery-retry logic that's already shipped and working, not net-new subsystems.
- **Dependencies:** none blocking each other; can be picked off independently.
- **Suggested order:** opportunistic, after the higher-priority items in this category (EXEC-1 through EXEC-6) — this is explicitly the "Tier 3: valuable, complete the coverage" tier of the source list, not the critical path.
- **Complexity:** S–M per item.
- **Success criteria:** each item ships as an independent, individually-testable refinement; no single success criterion covers the whole set.
- **Discovered 2026-07-09 (post-condition validation research), updated 2026-07-09:** #50's per-step half now ships — `runtime/run.js::verifyStep` evaluates every assertion on a step (not just up to the first required failure), polls (Playwright-style web-first assertions) instead of sampling once, and emits a full `results` audit as a `verify_result` telemetry event per step. Several of the same research pass's follow-ons have since landed too: ephemeral elements (toasts/cookie banners) are now filtered out of the REQUIRED promotion slot at compile time (`validation_planner.py` + `selector_filters.is_ephemeral_anchor`); a per-step Validation panel now exists in the Human Edit screen (`StepConfigForm.tsx`, shared `components/validation/AssertionEditor.tsx`); and `verify_result` is now aggregated fleet-wide into `assertion_health_by_step` on `GET /api/v1/tracking/dashboard`, rendered as the Dashboard's Assertion health card — the EXEC-9 tie-in above. **Still open for #50:** batching non-fatal failures *across* an entire run into one end-of-run report (today each step's audit is independent). **Deliberately not attempted:** entity-scoped assertions for destructive/commit clicks on repeating rows (ties into PROD-3) — there is no repeating-container/row detection anywhere in the recorder or compiler today; building one from scratch is real, unbuilt P1/XL infra (exactly PROD-3's scope), not a surgical addition to this item, and a heuristic shim would be unsound for destructive-action safety.

### UPD-2 — `[DECISION]` Installer code-signing certificate procurement
- **Category:** Auto Updates
- **Description:** The Windows EV code-signing integration for the Build Studio installer is fully implemented in code — `installer_builder.py` runs a conditional `signtool.exe sign /sha1 ... /fd SHA256 /tr http://timestamp.digicert.com` step after the NSIS build, gated on the `CONXA_SIGN_CERT_SHA1` env var (a certificate thumbprint) and `CONXA_SIGNTOOL_PATH`. As of this audit (2026-07-04), no certificate has been procured, so builds still ship unsigned and trigger Windows SmartScreen's "Unknown Publisher" warning.
- **Why required:** per `docs/Sales-Blockers.md`, this is the one remaining hard blocker for enterprise contract signature — many enterprises block unsigned executables via GPO policy, making this a fleet-deployability blocker, not a cosmetic one.
- **Business value:** directly gates the ability to close the first enterprise Windows customer.
- **Technical value:** none remaining — the engineering work is done; this is purely a procurement/ops action.
- **Dependencies:** none.
- **Suggested order:** immediate — per `docs/Sales-Blockers.md`'s "Now" recommendation, this is the last thing between the current build and a fleet-deployable, GPO-safe installer. (Its listing under P4 here reflects that no *engineering* work remains, not that it's unimportant — see `docs/Sales-Blockers.md` for the business-priority framing.)
- **Complexity:** S — procure a Windows EV code-signing certificate (~$200/yr), install it in the build machine's certificate store, and set the env var. No code changes needed.
- **Success criteria:** a build produced with the certificate installed passes Windows SmartScreen without an "Unknown Publisher" warning.

### TEST-2 — Add dedicated test coverage for `packages/conxa-core`
- **Category:** Testing & Cleanup
- **Description:** `packages/conxa-core` has no test directory of its own — it's only covered indirectly, through `conxa-cloud/tests/` via the shared `pythonpath` set in `pytest.ini`. This works today because `conxa-core` is always installed alongside the cloud backend and Build Studio in the current monorepo layout.
- **Why required:** if `conxa-core` is ever versioned or published independently (a natural evolution for a shared foundation package installed by two separate systems), indirect-only coverage becomes a real gap — there'd be no way to verify the package works correctly in isolation.
- **Business value:** none today; this is forward-looking technical debt, not an active problem.
- **Technical value:** protects against a future refactor (splitting `conxa-core` out for independent versioning) silently losing test coverage in the process.
- **Dependencies:** none.
- **Suggested order:** low urgency — revisit if/when `conxa-core` independent versioning is ever seriously considered.
- **Complexity:** S/M depending on how much of the existing indirect coverage needs to be duplicated versus how much can just be relocated.
- **Success criteria:** `packages/conxa-core` has its own `tests/` directory with meaningful direct coverage of its public API, independent of the cloud test suite's `pythonpath` trick.

### TEST-3 — Residual low-severity security hardening (SG-08, SG-12, SG-13)
- **Category:** Testing & Cleanup
- **Description:** Three low-severity, currently-accepted gaps remain open in `docs/Security.md`: SG-08 (sync token is a shared installer-scoped secret, not rotatable per-installer — see also PROD-10's per-device-identity replacement plan), SG-12 (company name used in file paths without re-validation in `auth_manager.js`/`sync.js`), and SG-13 (no per-user identity at runtime — `uid` is spoofable, only per-company auth exists). All three are already documented as accepted low-severity tradeoffs, not urgent fixes.
- **Why required:** listed for completeness so they don't fall off the radar entirely — each is individually low-impact, but worth a look if runtime auth is ever revisited for other reasons (e.g., alongside CLOUD-1's RBAC work, since "no per-user identity at runtime" (SG-13) is conceptually related to enterprise RBAC granularity).
- **Business value:** low — these are defense-in-depth items, not known-exploitable gaps.
- **Technical value:** closes out the security-gap tracker's remaining open items, keeping `docs/Security.md` accurately reflecting "everything genuinely resolved vs. still open" rather than a slow accumulation of low-priority items.
- **Dependencies:** SG-13 (per-user runtime identity) is naturally related to CLOUD-1's RBAC work — consider bundling if CLOUD-1 is ever scoped in detail. SG-08 is directly superseded if PROD-10's per-device identity work ships.
- **Suggested order:** opportunistic; bundle with CLOUD-1 or PROD-10 if convenient.
- **Complexity:** S each.
- **Success criteria:** each gap either gets a real fix (e.g., path re-validation for SG-12) or an explicit, documented "accepted risk, will not fix" decision in `docs/Security.md` rather than sitting in an ambiguous "Low, not urgent" state indefinitely.

### TEST-4 — Studio deps-cache version picker can select a stale runtime/app-layer build
- **Category:** Testing & Cleanup / Builder
- **Description:** `_bootstrap_runtime_dir()`/`_bootstrap_app_dir()` in `conxa_compile/conxa_runtime.py` pick the highest-versioned directory under `~/.conxa-build-studio(-dev)/deps/conxa-runtime|conxa-app/` via `_runtime_version_sort_key()`, which tuple-compares the digit groups parsed out of the directory name. This breaks when a real tagged build (e.g. `host-v1.2.3` → `(1,2,3)`) and a local dev build (e.g. `host-v0.0.0-local.20260721135338` → `(0,0,0,20260721135338)`) coexist in the same deps folder: Python tuple comparison stops at the first differing element, so `(1,2,3) > (0,0,0,…)` regardless of the local build's later timestamp — the stale/unrelated `v1.2.3` build silently wins over a just-built local one. Reproduced directly: a `host-v1.2.3` directory (whose bundled `bootstrap.js` predated the `register-mcp`/`unregister-mcp` subcommand) was picked by `_stage_runtime_binary()` for a customer installer build over a same-day local build that had the subcommand, so the resulting install's `conxa-runtime.exe register-mcp` silently did nothing (fell through to launching the full MCP server instead) instead of registering into any AI agent host.
- **Why required:** silently picking the wrong cached build produces installers/test-sandboxes built from stale runtime code with no error or warning — the failure only surfaces later as a confusing runtime behavior mismatch, as it did here.
- **Business value:** avoids shipping (or locally testing against) a build that doesn't reflect current `runtime/` source, without the developer realizing it.
- **Technical value:** narrow, well-isolated fix — `_runtime_version_sort_key()` needs either directory mtime as a tiebreaker/primary key, or to stop mixing tagged semver-style names with the `-local.<timestamp>` naming scheme in the same comparison.
- **Dependencies:** none.
- **Suggested order:** opportunistic — low urgency since `build-runtime-local.ps1`/`build-app-local.ps1` already delete every other version dir after a local build, which mostly avoids the collision going forward; only bites when something else (e.g. a real download from "Build Installer") repopulates a second, differently-named version dir alongside a local build.
- **Complexity:** S.
- **Success criteria:** `_bootstrap_runtime_dir()`/`_bootstrap_app_dir()` always resolve to the most-recently-built candidate (by mtime, not by name-parsed digit tuple) when multiple version directories are present.

### TEST-5 — Studio test-sandbox staging: unguarded rmtree, unreaped kill, no request serialization
- **Category:** Testing & Cleanup / Builder
- **Description:** Found while root-causing the "Failed to point conxa-app/current" wedge (fixed 2026-08-01 — `_is_link()` was blind to junctions under the shipped Python 3.11 build, see `FIX.md`). Three adjacent weaknesses in the same code path were out of scope for that fix and left open:
  1. `stage_runtime_payload()`'s `shutil.rmtree(version_dest)` (`conxa_compile/conxa_runtime.py`, right before the `_ensure_junction` call) and the equivalent in `sync_skill_pack()` have no retry/backoff, unlike `_ensure_junction` immediately after them — a lingering handle on the *old* version dir raises a bare, unguarded `OSError` instead of the same defense-in-depth retry the junction repoint gets.
  2. `runtime_tool.py`'s `finally` block calls `proc.kill()` on the timeout path with no following `wait()` — the child is killed but never reaped, and the function returns before Windows confirms the handle is released; stdout/stderr pipes are also never explicitly closed.
  3. `backend.py`'s JSON-RPC dispatcher runs every request (including `cmd_test_workflow`) on its own daemon thread with no per-command lock or in-flight registry, and the renderer's "already running" guard (`PluginWorkflowTests.tsx`) is component-local — two workflow-test panels can both target the one singleton sandbox concurrently.
- **Why required:** none of these caused the wedge that was just fixed, but all three are latent causes of the *next* confusing sandbox failure — the kind of thing the last three (wrong-diagnosis) fix attempts were chasing.
- **Business value:** fewer "reinstall/retry" false alarms during workflow testing, which otherwise erode confidence in Build Studio during demos and onboarding.
- **Technical value:** small, well-isolated hardening — a backoff loop on the two `rmtree` call sites, a `wait()` after `proc.kill()` (+ explicit pipe close), and either a lock around `cmd_test_workflow` or a sandbox-directory-scoped mutex.
- **Dependencies:** none.
- **Suggested order:** opportunistic — pick up if sandbox flakiness resurfaces after the 2026-08-01 fix, since that fix removes the only known trigger.
- **Complexity:** S.
- **Success criteria:** a killed-mid-test runtime process is confirmed reaped before the next `stage_runtime_payload()` call; both `rmtree` call sites retry past a transient lock the same way `_ensure_junction` does; two concurrent `test_workflow` calls against the same sandbox no longer race.

### TEST-8 — `check_app_session_sync` leaks its probe thread/browser on timeout
- **Category:** Testing & Cleanup / Builder
- **Description:** Found while root-causing a compile that hung forever with no error (fixed 2026-08-15 — see `FIX.md`). The freshness probe in `conxa_compile/recorder/session.py` used to run unbounded on every Group Page open and could wedge the whole backend; it's now bounded with `thread.join(timeout_s)` and only called from the recording gate. But a timed-out probe thread — and the Playwright driver + headless browser it launched — is not cancelled, only abandoned (`daemon=True` just stops it blocking process shutdown). A backend that repeatedly hits this timeout (e.g. a saved session whose target site is consistently unreachable) accumulates leaked driver processes for the life of the backend.
- **Why required:** the bounding fix (this session) closes the reported freeze; this item tracks the residual resource leak on the timeout path, which is real but strictly less severe — it degrades over many repeated timeouts rather than hanging on the first one.
- **Business value:** avoids a slow memory/process-handle creep in long-running Studio sessions against a flaky or unreachable auth target.
- **Technical value:** the fix is kill-able subprocess isolation for the probe instead of an in-process thread — `sys.executable` is the backend exe in frozen builds, so it's not a one-liner; needs a small helper script or a `multiprocessing.Process` that can be `.terminate()`d on timeout.
- **Dependencies:** none.
- **Suggested order:** opportunistic — pick up if leaked-process reports surface from a long-running Studio install.
- **Complexity:** S.
- **Success criteria:** a probe that times out leaves no Playwright driver or headless browser process still running a few seconds later.

### TEST-9 — Cloud frontend's Dialog/AlertDialog/Sheet overlays have the same missing-forwardRef bug
- **Category:** Testing & Cleanup / Cloud Frontend
- **Description:** Found while fixing the identical bug in Build Studio (this session, 2026-08-15 — see `FIX.md`): `conxa-cloud/frontend/src/components/ui/{dialog,alert-dialog,sheet}.tsx` define their `*Overlay` component as a plain function, not `React.forwardRef`. Confirmed by grep (not yet fully read) to be byte-for-byte the same shadcn-template shape as the Build Studio files before this session's fix, and `conxa-cloud/frontend/package.json` pins the same `radix-ui@^1.4.3` whose `DialogPortal` clones + attaches a ref to every direct Portal child. Every `*Content` component in that app (`DialogContent`, `AlertDialogContent`, `SheetContent`) passes its `*Overlay` sibling into `*Portal` the same way Build Studio's did, so opening any of them will log "Function components cannot be given refs" the same way.
- **Why required:** same root cause, same fix, different app — not touched in this session because it wasn't the app that logged the reported warning and this session's scope was Build Studio only.
- **Business value:** none directly (console-only warning, not a visible bug) — cosmetic/hygiene, but a very cheap fix.
- **Technical value:** removes a recurring console warning and restores the ref Radix's Presence needs to track each overlay's exit-animation completion.
- **Dependencies:** none — the fix is the exact same 3-file pattern already applied in `conxa-builder/electron/renderer/src/components/ui/{alert-dialog,dialog,sheet}.tsx`, just mirrored onto the cloud frontend's copies.
- **Suggested order:** opportunistic — bundle with any other cloud-frontend UI pass.
- **Complexity:** S.
- **Success criteria:** all three `*Overlay` components in `conxa-cloud/frontend/src/components/ui/` are `React.forwardRef`; opening a Dialog, AlertDialog, and Sheet in that app logs no ref warning.

### ADV-1 — TwelveLabs video-understanding integration
- **Category:** Advanced / Research Integrations
- **Description:** Per `research-analysis/07-go-to-market/twelvelabs-video-strategy.md`: Conxa already captures a `recording.webm` for every recorded workflow but doesn't currently use video-understanding models on it. Four integration points are laid out in detail: (1) compile-time intent enrichment (Pegasus model — richer per-step intent descriptions than can be inferred from screenshots/DOM alone); (2) semantic skill discovery and dedup (Marengo model — detect that two separately-recorded workflows are actually the same underlying task); (3) auto-generated assertions (Pegasus — infer expected post-conditions from what visibly changed in the recording); (4) recovery describe-then-match using Marengo at Tier B only, consistent with the zero-token-hot-path invariant.
- **Why required:** this is a genuinely unbuilt, well-specified opportunity to extract more value from data Conxa already collects (the recording itself) without changing what customers do — but it's speculative product investment, not a response to an identified gap or risk the way most of the rest of this file is.
- **Business value:** each integration point could meaningfully improve compile quality or discovery, but none is validated yet — this is an R&D bet, not a committed roadmap item.
- **Technical value:** the source doc includes a specific cost model (cost per compile) and explicitly checks the integration against existing invariants (e.g., recovery describe-then-match is scoped to Tier B only, preserving the zero-token hot-path guarantee) — the design work is largely done; what's missing is the build and a validation pass on real recordings.
- **Dependencies:** none blocking; independent of the rest of the roadmap.
- **Suggested order:** last — explicitly speculative and not gating anything else in this file.
- **Complexity:** L per integration point; the source doc scopes each of the four independently, so they don't need to land together.
- **Success criteria:** at least one integration point (most likely compile-time intent enrichment, the simplest of the four) is validated against a sample of real recordings and shown to measurably improve compiled-skill quality before investing in the remaining three.

### BUILD-3 — Stale-validation badge for manual selector edits
- **Category:** Builder
- **Description:** The new 3-phase re-target wizard (`RetargetWizardDialog.tsx`) shows a plain-language current-vs-proposed validation diff whenever the user re-targets a step's element through it. But a user can still hand-edit `target.primary_selector`/`fallback_selectors` directly in `StepEditorPanel`'s selector fields via `cmd_patch_step`, which does not surface any "the wait-for/assertions may now be stale" signal — the editor has no dependency awareness outside the wizard.
- **Why required:** identified during the re-target wizard design as explicitly out of scope for that task (kept surgical); the gap is real but narrower than the wizard itself, since manual selector edits are the less common path (the wizard is now the primary re-target entry point).
- **Business value:** small — reduces the chance a manually-edited step silently ships with assertions that no longer match its new target.
- **Technical value:** the plumbing already exists (`infer_wait_for_shape` + `_build_assertions`, already reused by `conxa_compile/editor/retarget.py::preview_retarget`); this is mostly a `cmd_patch_step` response addition plus a UI badge, not new compiler logic.
- **Dependencies:** none blocking; natural follow-up to the re-target wizard.
- **Suggested order:** opportunistic — pick up next time `StepEditorPanel.tsx` or `cmd_patch_step` is touched.
- **Complexity:** S — a "re-check validation" badge with a one-click regenerate action reusing `preview_retarget`'s validation-diff computation.
- **Success criteria:** hand-editing a step's target selector in `StepEditorPanel` (outside the wizard) shows a visible warning when the current `validation` block no longer matches what `infer_wait_for_shape`/`_build_assertions` would produce for the new target, with a one-click way to regenerate it.
- ~~**Discovered 2026-07-09 (post-condition validation work):** `conxa_compile/editor/patch_gate.py::validate_editor_patch` — including its destructive-wait_for invariant and the new "consequential action must retain a required assertion" invariant added alongside `docs/Implementation-Plan.md` 1.10 — is not actually called from `cmd_patch_step` (or anywhere else); it's covered by unit tests (`conxa-cloud/tests/test_patch_gate.py`) but not wired into the live patch path, so a manual `StepEditorPanel` edit today can silently violate either invariant. Wiring `validate_editor_patch` into `cmd_patch_step` would close this gap and is a natural companion to the stale-validation badge above.~~ **Resolved 2026-07-09:** `cmd_patch_step` (`conxa-builder/python/handlers/workflow_editor.py`) now calls `validate_editor_patch` before merging/persisting any patch, raising a rejecting `_CommandError` on violation. This closes the invariant-violation gap specifically; the broader "stale but not invariant-violating" badge-and-regenerate UX described above is still open.

### BUILD-6 — Authoring UI for try_dismiss candidates and wait_for_one_of options
- **Category:** Builder
- **Description:** BUILD-5 shipped full authoring for `if_present`'s nested body (`BranchBodyEditor.tsx`) but deliberately left `try_dismiss`'s `branch.candidates` (an ordered list of selector strings) and `wait_for_one_of`'s `branch.options` (a list of `{selector, steps}` alternatives) with read-only summary badges only. The backend patch-gate validation for both already exists (`conxa_compile/editor/patch_gate.py::_validate_branch_patch`) — a normal `patch_step` call with a `branch: {candidates: [...]}` or `branch: {options: [{selector: ...}]}` payload is already accepted and quality-gated; there is just no UI to compose that payload. Per-option nested `steps` bodies for `wait_for_one_of` are explicitly out of scope even for this follow-up (see `_validate_branch_patch`'s rejection of a `steps` key inside any option) — that would need its own path-addressing scheme (`branch.options[i].steps[j]`) beyond what `cmd_patch_step`'s current `path` parameter supports.
- **Why required:** identified while scoping BUILD-5 — `if_present` covers the most common case (optional interstitials with a body to run), but `try_dismiss` and `wait_for_one_of` are equally real primitives with no authoring path today.
- **Business value:** small — completes branch-step authoring parity; most real-world interstitial handling likely uses `if_present`.
- **Technical value:** low-risk, additive; the write path is already validated and tested (`tests/test_patch_gate.py::TestBranchStepPatches`), this is UI-only.
- **Dependencies:** builds on BUILD-5's branch-authoring foundation.
- **Suggested order:** opportunistic — next time branch-step authoring is touched.
- **Complexity:** S for candidates (a simple ordered string-list editor); M for options (needs the new path-addressing scheme if per-option bodies are also wanted).
- **Success criteria:** a user can add/remove/reorder `try_dismiss` candidate selectors and `wait_for_one_of` options from Human Edit without hand-crafting a patch payload.

### BUILD-7 — Decide the fate of `validation.success_conditions`
- **Category:** Builder
- **Description:** Flagged by the Human Edit vs. Skill Package audit (§4 item 7) as "half-exposed" — the field is typed in `types/workflow.ts` and read by one path in `StepConfigForm.tsx` (setting a URL into it for navigate steps) but has no dedicated editor next to `validation.assertions`. Explicitly deferred out of BUILD-5's scope per user direction (2026-07-10) rather than addressed inline. Needs a decision: give it a real editor, or migrate its one remaining use into `assertions` and delete the field (the more invasive option — touches the compiler + runtime contract).
- **Why required:** a half-exposed field is worse than either a fully-exposed or fully-hidden one — it reads as "nothing to see here" when there's actually a gap.
- **Business value:** small — mostly a consistency/debt item.
- **Technical value:** low if the "editor" path is chosen (mirrors the existing assertion editor); moderate if the "delete and migrate" path is chosen.
- **Dependencies:** none blocking.
- **Suggested order:** opportunistic.
- **Complexity:** S (add an editor) or M (migrate and delete, since it touches `compiler/patch.py` and the runtime's `verifyAssertions()` contract).
- **Success criteria:** `success_conditions` is either editable through a real UI or removed from the schema — no third state.

### BUILD-9 — Dev deps manifest can pin an app layer older than the runtime code requires
- **Category:** Builder
- **Description:** Discovered while diagnosing the BUILD-8-adjacent stale-app-layer bug (2026-07-30, see `FIX.md`): the dev deps manifest pinned `app-v1.3.4`, built 2026-07-04, which was missing five files (`http_client.js`, `page_scripts.js`, `durable_context.js`, `config_edit.js`, `mcp_hosts.js`) that the current `server.js`/`sync.js` require to even start. A fresh dev machine that bootstraps deps without ever running `build-app-local.ps1` would download this pinned version and hit the same runtime failure the http/https fix was meant to close.
- **Why required:** the deps manifest is a silent trust boundary — nothing currently checks that a downloaded/pinned app layer actually contains the files the checked-out runtime source expects.
- **Business value:** low-moderate — mainly protects new-developer onboarding and CI-adjacent dev flows from a confusing "works on my machine" gap.
- **Technical value:** moderate — either bump the pinned dev manifest version, or add a build-time staleness check in `installer_builder.py::_stage_runtime_binary()` that diffs the staged app layer's `version.json` file list against a required-files constant and fails loudly instead of shipping an incomplete bundle.
- **Dependencies:** none.
- **Suggested order:** opportunistic; do before the next round of onboarding a new developer to Build Studio.
- **Complexity:** S (bump the pin) to M (add the staleness-check guard).
- **Success criteria:** either the pinned dev app-layer version is kept current, or a build-time check catches a staged app layer missing files the current runtime source requires, before it reaches an installer.

---

### BUILD-11 — Batch the 5 ffmpeg calls per event into one invocation
- **Category:** Builder
- **Description:** Found 2026-08-04 while fixing a production compile where one hung ffmpeg process (extracting `evt_0008_before_far.png` at `-ss 34.414`) discarded frames for the whole session — see `FIX.md`. That fix moved frame extraction from recorder shutdown into compile (`handlers/compile.py:cmd_compile`) and made it idempotent + per-event isolated, but each event still spawns 5 separate `ffmpeg` processes (`before_far/near`, `at`, `after_near/far`), one per offset. Measured cost is only ~0.2s/frame, so this is not currently a real bottleneck — it's a follow-up optimization, not a fix.
- **Why required:** not required today. Would matter for very long recordings (hundreds of events → thousands of ffmpeg spawns) or if the acceptable compile-time budget tightens.
- **Business value:** low now; faster compiles at scale later.
- **Technical value:** one ffmpeg invocation per event instead of 5 (`-ss <T-0.5> -i recording.webm -vf fps=4 -frames:v 5 ...`) cuts process-spawn overhead ~5x. Needs verification that `fps` resampling on Playwright's variable-frame-rate webm lands the 5 outputs on the intended offsets (before_far/near, at, after_near/far) rather than approximate ones — the current per-offset `-ss` seek is exact, a batched filter isn't guaranteed to be without checking.
- **Dependencies:** none blocking.
- **Suggested order:** opportunistic — only worth doing if compile time for long recordings becomes a complaint.
- **Complexity:** S.
- **Success criteria:** frame extraction for an N-event session uses N ffmpeg processes instead of 5N, and the 5 frames produced for a sample event are pixel-verified to match the current per-offset `-ss` output within tolerance.

### BUILD-13 — A cancelled file-picker click, recorded with no following upload, can still hang a run
- **Category:** Builder
- **Description:** Found 2026-08-06 while fixing the upload-workflow hang documented in `FIX.md` (compiler now drops a recorded click on a file input when it's immediately followed by the `upload_intent`/`upload` step that supersedes it — `step_anchors.py::clean_steps`). That fix is a merge: it requires the upload to actually be present in the recording to know the click was superseded. If a user opens the native file picker while recording and cancels it without choosing a file, the browser's `change` event never fires, `bridge.js` never emits `upload_intent`, and the lone `click` on the file input survives compilation untouched — including past `is_editable_target`'s file-input exclusion (also shipped 2026-08-06), which stops it being mislabeled `focus` but does not remove it. A literal `click` step on a file input still opens an undismissable native OS dialog at runtime, with nothing to drive it, hanging the run exactly as the original bug did.
- **Why required:** not urgent — it only affects a recording where the user opened and then abandoned the file picker, a deliberately incomplete recording action a user would normally redo. But it's a real, reachable hang with no current guard, in the same class of bug as the one just fixed.
- **Business value:** low-moderate — a confusing, silent hang during recording review or a customer's first run is a bad experience even if the recording that caused it was itself accidental.
- **Technical value:** two options, both deliberately out of scope for the 2026-08-06 fix (which was scoped compiler-only, no runtime change): (1) compiler-side — in `clean_steps`, drop any `click`/`focus` step on a `semantic.input_type == "file"` target that has no following `upload`/`upload_intent` anywhere later in the same recording, since such a click can never be a valid runtime step on its own; (2) runtime-side — a `page.on("filechooser", ...)` guard in `browser.js` that auto-cancels any dialog a stray click opens during a run, as defense-in-depth against this and any structurally similar case (e.g. a custom "Attach" button that wraps a hidden file input in a way the compiler can't statically detect). Option 2 is more robust (catches cases the compiler can't reason about at all) but was explicitly scoped out of the immediate fix per user direction.
- **Dependencies:** none blocking; independent of BUILD-12.
- **Suggested order:** opportunistic — pick up if this hang is ever actually hit in practice, or bundled with any other runtime `browser.js` work.
- **Complexity:** S (compiler-side drop) or S (runtime-side guard) — either alone is small; doing both is still small.
- **Success criteria:** a recording containing a cancelled file-picker click (a lone click on a file input, no following upload) either never reaches the compiled skill, or is safely absorbed at runtime without hanging.

### CLOUD-12 — `GET /api/v1/workflows/generations` is unreachable (route shadowed)
- **Category:** Cloud
- **Description:** Found 2026-08-19 while building the release system. `main.py` registers `workflow_router` (prefix `/workflows`) before `publish_router` (also prefix `/workflows`). `workflow_routes.py`'s `GET /{workflow_id}` matches first, so `GET /api/v1/workflows/generations` resolves to `get_workflow_detail("generations")` → 404 `"Workflow not found."`, never `publish_routes.get_installer_generations()`.
- **Why required:** currently harmless — `backend.py::_installer_generation()` catches the failure and falls back to `"v2"`, which happens to be the correct current default, so nothing user-visible is broken today. But the fallback silently masks the route being dead, and the intended behavior (admin-flippable default generation via `POST /admin/workflows/generations`) has no working read path to verify against.
- **Business value:** low today (masked by the fallback); would become a real bug the moment `current` is ever flipped away from `"v2"` — every Studio install would keep stamping the stale default with no visible error.
- **Technical value:** one-line fix (reorder router registration, or move `publish_router` before `workflow_router` in `main.py`), high value for closing a silent-failure gap.
- **Dependencies:** none.
- **Suggested order:** opportunistic — bundle with any other `main.py` router-registration change.
- **Complexity:** S.
- **Success criteria:** `GET /api/v1/workflows/generations` returns the generations payload, not a 404; a regression test asserts the route resolves correctly regardless of router registration order.

### CLOUD-14 — No backfill for release history published before per-skill versioning (2026-08-19)
- **Category:** Cloud
- **Description:** The 2026-08-19 per-skill publishing fix re-keys `skillpack_versions__{slug}`, `skillpack_channels`, and `skillpack_release_events__{slug}` from `{slug}` to `{slug}__{skill_slug}` / `"{slug}:{skill_slug}"`. This was a clean re-key with no migration script, on the reasoning that these three namespaces are purely internal Release Center bookkeeping — no installed runtime reads them (runtimes only see `component_versions`, `pack.json`, and the delta endpoint, all left untouched and already per-skill). Any company that published under the old company-wide model before this date simply has no version history under the new per-skill keys — their Release Center for that company will show "no releases published yet" for every skill until they publish again, even though their runtimes are still correctly running whatever was last live.
- **Why required:** currently cosmetic (no functional break — `write_pack_json_mirror`/`component_versions`/delta sync are unaffected), but any pre-2026-08-19 published company loses its dashboard-visible release history, release notes, and rollback targets until it republishes each skill once.
- **Business value:** low today (no paying customers predate this change per `docs/PRD.md`'s "long-term once Conxa has paying customers" framing); would become real the first time a company with real pre-existing publishes needs to see or roll back to a release from before 2026-08-19.
- **Technical value:** a one-time backfill script reading the legacy `skillpack_versions__{slug}` / `skillpack_channels` rows (row keyed by slug alone) and writing them forward into the new per-skill keys, inferring `skill_slug` from each legacy row's `skills`/`skill_versions` list, would close this.
- **Dependencies:** none — read-only against legacy data, purely additive.
- **Suggested order:** opportunistic — only worth doing once real pre-2026-08-19 publishes exist that someone actually needs history for.
- **Complexity:** S — one script, no schema change, no runtime involvement.
- **Success criteria:** a company that published under the old company-wide model before 2026-08-19 sees its historical versions/rollback targets in the Release Center for each of its skills, without needing to republish first.

### CLOUD-16 — Untested cloud-backend routers
- **Category:** Cloud
- **Description:** Discovered during the 2026-09-17 conxa-cloud streamlining pass. Four routers/modules have zero test coverage: `app/api/job_routes.py` (4 endpoints), `app/services/jobs.py` (149 lines, never imported by a test), `app/api/workflow_routes.py` (6 endpoints — list/create/delete workflows, SkillPack list/detail), `app/api/machine_binding.py`, `app/api/product_ownership.py`, and the `POST /entitlements/admin/plan` branch of `entitlement_routes.py`.
- **Why required:** these are real, reachable routes with no regression net — a change to any of them (including an unrelated refactor that happens to import through the same module) can silently break behavior with no test to catch it, unlike almost everything else in the 1550+-test suite.
- **Technical value:** closing this gap is mechanical (the existing `TestClient(app)` pattern used throughout `tests/test_*.py` covers all of these), not a design question.
- **Dependencies:** none.
- **Suggested order:** opportunistic — pick up alongside the next change that actually touches one of these files, rather than a dedicated sweep.
- **Complexity:** S per router.
- **Success criteria:** each listed router has at least one characterization test per endpoint (happy path + one auth/validation failure), matching the style already used for every other router in `tests/`.

### CLOUD-24 — Impact page shows "Change hourly rate" to people who cannot save
- **Category:** Cloud
- **Description:** Discovered during the 2026-09-29 Operations dashboard redesign. The Impact tab's "Change hourly rate" control is shown to every signed-in member, but `PUT /api/v1/tracking/roi-assumptions` requires `require_admin`, so a non-admin fills in the form and only then sees "Could not save. Updating these values requires an admin or owner role." `workflowsApi.ts`'s comment already claims "the UI hides the control for everyone else" — it doesn't.
- **Why required:** a control that always fails for a role is worse than no control; it also makes the comment in the API client wrong.
- **Technical value:** read the member's role (as Team/Settings already do) and hide the control for non-admins.
- **Dependencies:** none.
- **Suggested order:** opportunistic — next time the Impact tab or role plumbing is touched.
- **Complexity:** S.
- **Success criteria:** a non-admin sees the hourly rate as text with no "Change hourly rate" control; an admin's flow is unchanged.

---

## P3 Discovered Items (4 remaining) (2026-08-12 Plugin→Workflow/SkillPack refactor)

### TEST-7 — Dev-script renaming cleanup: test_plugin.py, rebuild_plugin.py, plugin_test/ directory
- **Category:** Testing & Cleanup
- **Update (2026-09-14):** `rebuild_now.py` and `rebuild_plugin.py` were not just stale-named — both imported `conxa_compile.plugin_builder`, a module that no longer exists (superseded by `conxa_compile.compiler.build.compile_skill_package`, which `recompile_session.py` already uses correctly). Both were confirmed broken and deleted rather than renamed. `test_plugin.py`, `scripts/plugin_test/`, and `PLUGIN_TEST_README.md` are untouched and still need the naming pass described below.
- **Description:** Internal dev/debug tooling scripts (`conxa-cloud/scripts/test_plugin.py`, `scripts/plugin_test/` directory, `PLUGIN_TEST_README.md`) still carry "plugin" terminology from the pre-refactor entity model. These scripts are confirmed NOT functionally broken (they operate on on-disk bundle-slug lookups, not the Plugin model/API), but their naming is stale and confuses future maintainers. Rename all references to align with post-refactor terminology (plugin → workflow, where applicable; plugin_test/ folder → maybe workflow_test_suite/ or keep generic).
- **Why required:** stale naming in developer tooling creates confusion and maintenance debt — future engineers reading the codebase will wonder if these scripts are handling the old Plugin entity or the new Workflow model.
- **Business value:** low — purely developer-facing cleanup.
- **Technical value:** low — no functional changes, pure naming/documentation alignment.
- **Dependencies:** none; safe to do anytime.
- **Suggested order:** opportunistic — next time these scripts are touched, or bundled with the next developer-docs pass.
- **Complexity:** S — find-and-replace in filenames/content + docstring updates.
- **Success criteria:** all dev scripts in `conxa-cloud/scripts/` use consistent terminology (workflows, not plugins) in their names and documentation.

### ADV-2 — Orphaned cloud-frontend jobsApi.ts and associated job-queue infrastructure
- **Category:** Advanced
- **Description:** `conxa-cloud/frontend/src/api/jobsApi.ts` is a fully-typed fetch wrapper over a cloud-side job-queue API (job status, polling, cancellation) that exists in the type definitions but has zero actual consumers in the current codebase. Discovered during Plugin→Workflow refactor (`grep -r "jobsApi"` found only test stubs and the file itself). This suggests either: (1) the job-queue infrastructure was built but never wired into the cloud backend's `main.py` or any routers, or (2) it was removed from the backend but the frontend stub was left behind. Either way, the client is dead code and should be removed unless it's a stub awaiting future implementation.
- **Why required:** dead code is a maintenance hazard and obscures the actual API surface the cloud exposes.
- **Business value:** zero — purely internal cleanup.
- **Technical value:** low — just deleting unused code. If the job-queue is actually planned, this becomes a different task (implement the backend route), not a cleanup.
- **Dependencies:** run `grep -r "jobsApi\|job.*queue" conxa-cloud/` to determine if the backend work actually exists (check `app/api/*.py` and `app/services/*.py` for any route or service using Job/queue terminology).
- **Suggested order:** opportunistic — low-risk cleanup, doesn't block anything.
- **Complexity:** S — delete the file and any imports; verify no breakage.
- **Success criteria:** `jobsApi.ts` is removed; `npm run build` in `conxa-cloud/frontend` succeeds with no errors; a search for "job" in the frontend code returns zero API-related results.

### DOC-5 — Comprehensive review and updates to Backend-Schema.md for Workflow/SkillPack split
- **Category:** Documentation
- **Description:** Backend-Schema.md underwent routing-level updates during the Plugin→Workflow refactor (all `/api/v1/plugins/` → `/api/v1/workflows/`), but the data models section (§2 Plugin/PluginWorkflow/PluginAuth/PluginBuild/PluginInstaller → Workflow/SkillPack split) and ERD diagrams (§6) still describe the old Plugin entity structure with nested PluginWorkflow lists. The doc needs a comprehensive rewrite of the models section to show: Workflow (flat, 1 recording each), SkillPack (workspace-level, shared build/installer), and the KV namespace updates (`plugins` → `workflows`, new `skill_packs_meta` namespace). The ERD should be redrawn to show the new cardinality (N workflows : 1 SkillPack per workspace).
- **Why required:** Backend-Schema.md is the source-of-truth doc for the data contract; a stale doc creates confusion during implementation and review.
- **Business value:** high — prevents future bugs from misunderstanding the current data model.
- **Technical value:** high — keeps the contract doc accurate.
- **Dependencies:** depends on this refactor being complete (it is).
- **Suggested order:** soon — within the next doc-maintenance pass.
- **Complexity:** M — requires rewriting §2 (models), updating §6 (ERD), updating §7 (KV namespace map), and spot-checking §5 (API examples) to ensure all request/response bodies use updated field names.
- **Success criteria:** Backend-Schema.md §2–7 accurately describe Workflow/SkillPack models, KV namespaces, and the API contract with no references to the old Plugin entity structure.

### DOC-6 — `docs/cost_model.md` still narrates the business/pricing model in "plugin" terms
- **Category:** Documentation
- **Description:** `docs/cost_model.md` uses "plugin" throughout as the business/pricing unit (e.g. "1 plugin, 50 workflows", "Plugin Compilation", "live plugins", per-plugin telemetry, plugin adoption metrics) — this predates the 2026-08-12 Plugin→Workflow/SkillPack entity rename and the term now maps most closely to the new `SkillPack` concept (one company's shared build+installer, containing many workflows). Not fixed as part of that refactor because this doc isn't in CLAUDE.md's mandatory-update table for entity/API changes (it's pricing narrative, not architecture), and because it has ~30+ mentions including dated historical changelog entries (§ version history, e.g. "v14: Replaced visible workflow/plugin caps...") that must NOT be rewritten — those are a historical record of past pricing decisions, not current-state documentation.
- **Why required:** stale business terminology in the doc that governs LLM unit-economics decisions risks confusing future pricing/product discussions about what a "plugin" actually is post-rename.
- **Business value:** medium — this doc directly informs pricing/tier decisions.
- **Technical value:** low — pure terminology, no code changes.
- **Dependencies:** none.
- **Suggested order:** opportunistic — next time cost_model.md is substantively revised (it already has a "TODO" version-history habit of batching several changes per revision).
- **Complexity:** M — requires reading each occurrence in context to distinguish current-state prose (rewrite: "plugin" → "skill package") from historical changelog entries (leave untouched).
- **Success criteria:** all forward-looking prose in cost_model.md uses "skill package" terminology consistent with the rest of the docs; the version-history log at the bottom is left as an untouched historical record.

---

## Final Review Notes (from the audits that produced this file)

- Every row in `docs/Security.md`'s security-gaps table (SG-01 through SG-13) has a corresponding item above or is confirmed already fully resolved with no residual work.
- Every "done" claim corrected during the first audit pass (installer code signing, the `/api/v1` gap, the gate_replay.js CI gate status) was verified directly against code, not assumed from prior docs.
- **Second pass:** the first pass under-mined the research corpus — it built this file almost entirely from the engineering-gap documents (`gap-analysis.md`, `master-insights.md`, `master-recommendations.md`, `Sales-Blockers.md`) and direct code checks, while only *summarizing* (not extracting action items from) `conxa-critical-analysis.md`, `conxa-solutions-by-problem.md`, `top-50-improvements.md`, `build-order.md`, `agentic-discovery-strategy.md`, the go-to-market folder, `ops/private-repo-migration.md`, the `twelvelabs-video-strategy.md`, and `docs/UI-UX-Brief.md`'s own Priority 2/3 backlog. That pass added the Product Strategy items, BUILD-2, MCP-2/MCP-3, EXEC-5 through EXEC-8, UPD-4, and ADV-1 to close that gap. Items marked `[UNVERIFIED]` above were sourced from the research corpus without an individual code-level check (unlike the rest of this file) — a couple of candidate items from the same source list (top-50 #9 "consume compile-time confidence at runtime" and #21 "wait-enabled/aria-disabled gate") were spot-checked and found to already be shipped, so they were deliberately excluded rather than re-listed as open.
- **Third pass:** re-sorted the whole file by priority tier (P0–P3) instead of by subsystem category, per user request. No item content changed — each item gained a **Category:** field so the previous category grouping isn't lost, just no longer the primary sort key. The old "Phase 10 — Enterprise Features" section (a pointer-only section with no items of its own) was folded into the file's intro instead of kept as a structural element.
- **Fourth pass (2026-08-09, this update):** the whole priority ladder shifted down one tier (old P0 → P1, P1 → P2, P2 → P3, P3 → P4) to make room for a new P0 tier of four founder-directed items: `CLOUD-10` (homepage positioning + pricing), `BUILD-14` (workflow-centric Record/Compile page + folder grouping), `ARCH-4` (installer generation migrating from Build Studio to Conxa Cloud), and `EXEC-10` (30–40 step multi-tab cross-domain stress test). No existing item's content changed beyond its section label and the handful of in-body cross-references that named a specific tier number (`PROD-3`'s and `EXEC-7`'s references to PROD-3 being "P0", `EXEC-9`'s reference to its own old-P1 tier, `UPD-2`'s reference to being listed under old-P3) — those were updated to match the new numbering so the file stays internally consistent.
- **Fifth pass (2026-08-21, this update):** added five items (`EXEC-21`, `EXEC-22`, `ARCH-5`, `ARCH-6`, `PROD-20`) arising from the Horizon 1/2/3 long-term direction added to `docs/PRD.md` §14. No existing item's content changed; `EXEC-13`, `EXEC-8`/`PROD-3`, `EXEC-9`, `PROD-4` and `PROD-5` are cross-referenced by the new items rather than duplicated — in particular `EXEC-21` (human review) is deliberately scoped as the human sibling of `EXEC-13` (AI review) sharing one park/resume mechanism, and `PROD-4`/`PROD-5` are identified as pre-existing Horizon 2 foundations written before the framing existed.
- `CLAUDE.md`'s documentation table was checked against `ls docs/` post-archival and is current as of the first pass — recheck this after any future archival/rename to avoid the exact drift that pass found and fixed.
- `docs/Sales-Blockers.md`, `docs/Implementation-Plan.md`, and this file agree on wording for the installer-code-signing item (UPD-2) — all three were updated together in the first pass.
- New-file discovery: `SHIP-GUIDE.md` was found during the first audit's verification pass despite not appearing in the original per-file research — it was confirmed accurate and just needed one broken cross-reference fixed and a doc-table listing added. Any `.md` file created after this audit began should get the same treatment (read, verify, cross-reference) before being assumed current.
- **Still not individually mined for action items:** the `research-analysis/04-architecture/00-master-architecture.md` through `09-implementation-blueprint.md` series and its `subsystems/*.md` files (used only as cited backing for existing R-numbered items, not read cover-to-cover for finer-grained specifics), and `research-analysis/05-reliability/framework.md`, `inventory.md`, `matrix.md`, `recovery-patterns.md` (the deeper EC-xx/RP-xx reference material behind `top-50-improvements.md`, sampled lightly rather than fully mined). If a future pass has time, these are the next places to check for anything still missing.

- [ ] **UPD-GATE-1 — scheduled runs never pick up a staged host update.** The scheduler daemon spawns its own exe, so the runtime's update gate (`runtime/app/update_gate.js`) deliberately exempts `_trigger: "scheduled"` runs; the daemon needs its own respawn-on-staged path.

- [ ] **EXEC-CHAT-1 — decide whether `list_skills`' "ALWAYS call this first when the user mentions Conxa" wording should soften.** Found 2026-09-20: it (`runtime/app/tool_defs.js`) is what nudged Execute's chat into running a skill on a bare "hello conxa". Execute now guards itself (conditional system prompt + a confirm card before any chat-started run), so this is left as-is, because the same description drives discovery in Claude Desktop and every other MCP host — changing it is a product call, not an Execute fix.

- [ ] **AUTH-1 — verify the sign-in tab comes to the front in a real Conxa Execute.** Added 2026-09-20 with the `authenticate` tool / waiting auth gate: Execute now sends a `focus` flag on a login's `new_view` so the browser panel selects that run even when another already holds the selection (`browser_panel.js`, `BrowserPanel.tsx`). Unit-tested (`browser_panel.test.js`) and typechecked, but never seen in a live Electron window — a run whose end notification is lost (`host_browser.js::release` is best-effort) was the suspected way a login could stay at 0x0. Check by running `Compliance-Report-Handoff` in Execute with neither GitHub nor Google Drive signed in.
- [ ] **AUTH-2 — `get_runtime_status` has no per-app sign-in state.** Left out of the 2026-09-20 `authenticate` work on purpose: reporting which group apps hold a valid session means the same live network validation `getGroupAuthContext` does (up to 30s per app), which is wrong for a "non-mutating diagnostics" call. `authenticate` with a skill already answers "is everything signed in" (instant `signed_in` when so). Add a cheap stored-session-only check here if a dashboard needs it.
- [ ] **RT-CHROMIUM-1 — decide whether the runtime should auto-download Chromium when none exists.** Found 2026-09-20: `~/.conxa/chromium` did not exist on a working Execute install (Execute borrows its own browser, so it never needed it), and the `.revision`-marker self-heal in `server.js` only fires once Chromium was installed at least once. The runtime now also falls back to `%LOCALAPPDATA%\ms-playwright`, but does not download, because doing so would pull ~150 MB for every Execute-only user. Any non-Execute client (Claude Desktop) on a machine with no Chromium anywhere still gets a launch error — now surfaced honestly — rather than an install.
