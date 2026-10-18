# UI/UX Brief

**Status:** Current as of 2026-08-15 (multi-tab recording: `WorkflowViewer.tsx` now renders a tab-boundary divider wherever a step's tab changes — see §2.7; Group Page absorbed the Workflow Detail page — every workflow's record/compile/review/test/ready-to-package lifecycle now runs from its group's page via `WorkflowStageRail`, and the standalone per-workflow route was removed; Plugin→Workflow/SkillPack refactor 2026-08-12: Record page removed, Cloud dashboard's Plugins screen renamed to Skill Packages at `/packages`; capability-ladder pricing restructure from 2026-08-08: new `/pricing` page, removed skill-pack-slot meter — see `docs/PRD.md` §11)
**Scope:** Build Studio (Electron) + Cloud Dashboard (Next.js)

---

## Table of Contents

1. [UI Systems Overview](#1-ui-systems-overview)
2. [Build Studio Screens](#2-build-studio-screens)
3. [Cloud Dashboard Screens](#3-cloud-dashboard-screens)
4. [Navigation Structure](#4-navigation-structure)
5. [User Journeys](#5-user-journeys)
6. [UX Bottlenecks & Friction Points](#6-ux-bottlenecks--friction-points)
7. [Missing Experiences](#7-missing-experiences)
8. [Enterprise UX Considerations](#8-enterprise-ux-considerations)
9. [Recommended Improvements](#9-recommended-improvements)
10. [Runner Tray](#10-runner-tray-prod-5-2026-08-26)
11. [Conxa Execute](#11-conxa-execute-redesign-2026-10-01)

---

## 1. UI Systems Overview

Conxa has two distinct UIs:

| UI | Technology | Audience | Deployment |
|---|---|---|---|
| **Build Studio** | Electron + React (Vite) + TypeScript | Company engineers who build and publish skills | Windows desktop app |
| **Cloud Dashboard** | Next.js 16 + Tailwind 4 + shadcn/ui | Companies monitoring execution + managing billing | web (app.conxa.in on Vercel) |

Both use:
- Tailwind CSS for styling
- shadcn/ui components (Radix UI primitives)
- Clerk for auth (different SDK: Electron uses custom PKCE; web uses `@clerk/nextjs`)
- TanStack Query (cloud dashboard) or direct IPC calls (Build Studio)

The production source of truth is:
- Build Studio: `conxa-builder/electron/renderer/src/`
- Cloud Dashboard: `conxa-cloud/frontend/`

(A `research/frontend/` prototype directory was referenced in earlier drafts of this doc — it does not exist in the repo; see `docs/Implementation-Plan.md` §1.8, marked MOOT.)

---

## 2. Build Studio Screens

### 2.1 Setup Wizard (`SetupWizard.tsx`)

**Purpose:** First-run onboarding when dependencies are not yet installed.  
**Inputs:** None (reads bootstrap state from backend).  
**Outputs:** Progress events from `cmd_bootstrap`.  
**User goal:** Get dependencies installed without manual intervention.

**UX issues:**
- No estimate of download time per dependency.
- Failures show a URL to whitelist but no retry button — user must re-launch.
- No "already installed, skip" fast path feedback.
- Progress is event-driven but the UI doesn't persist across restarts (progress resets if app is closed mid-bootstrap).

---

### 2.1a Legal Gate (`LegalGateScreen.tsx`) — added 2026-08-29

**Purpose:** Blocking click-through acceptance of the Terms and Conditions and Privacy Policy before any route is mounted (PROD-17).
**Where it sits in the gate chain:** deps bootstrap → mandatory update → **sign-in** → **legal gate** → routes. It sits *after* sign-in because an acceptance is only evidence if it names the person accepting.
**Inputs:** The current version and document hashes fetched from the cloud, a summary of the key licence terms, links that open the hosted `/docs/terms` and `/docs/privacy` pages in the system browser, and a required "I agree" checkbox.
**Outputs:** An acceptance record written on Conxa servers against the signed-in user — identity, workspace, version, document hashes, server timestamp, IP, Build Studio version, machine identifier. Nothing is stored locally.
**User goal:** Understand what the licence permits, accept once, never be asked again.

**Behaviour:**
- Rendered inside `AppChrome` so the frameless window's title-bar controls still work while it blocks.
- **Fail-closed:** if the cloud cannot be reached, a blocking "Can't reach Conxa" screen with a Retry button appears instead — the app never assumes acceptance.
- Dev builds (`!window.conxa.isPackaged`) skip the gate entirely, the same escape the deps gate uses.
- Bumping `CURRENT_LEGAL_VERSION` on the cloud re-prompts every user, because acceptance is recorded per (user, version).

**UX issues:**
- A Conxa outage blocks Build Studio at launch even though recording and compiling are entirely local. This was a deliberate trade for enforceability; the retreat, if it bites, is to admit a user whose acceptance the server previously confirmed.
- There is no dashboard screen for the acceptance export yet — the rows are readable only through `GET /api/v1/legal/acceptances` and, in summary, on the Audit page.

---

### 2.2 Login Overlay (`LoginOverlay.tsx`)

**Purpose:** Prompt sign-in before showing the main app.  
**Inputs:** User clicks "Sign In" button.  
**Outputs:** Auth state set in `AuthContext`.  
**User goal:** Authenticate once and be remembered.

**UX issues:**
- No visual feedback that the browser has opened and is waiting.
- No timeout indicator (5-minute wait; user may not know the window expired).
- Error messages from Clerk (e.g. `clerk_token_error`) are displayed raw without user-friendly translation.

---

### 2.3 Workflow List Page (`/workflows`)

**Purpose:** Primary landing page: browse Workflow Groups (business-domain folders — "Sales", "Marketing"), create a new group. Workflow-level create/search/delete moved to each group's own page (§2.3a).
**Inputs:** Workspace context; no selection required to load this page.
**Outputs:** A created group, or navigation to a specific group's page.
**User goal:** See the business domains at a glance and how far along each one's app logins are.

**Layout (2026-08-15 redesign):**
- Action strip: the **Compile credits** and **Human Edit pool** usage cards on the left (`UsageCards`, §2.3b), "+ New Group" on the right — the one clay-accented control on the page
- Empty-state panel (if no groups yet), otherwise a **folder grid**: a two-column grid whose rows are sized against the scroll container's own height (`.folder-grid`, `grid-auto-rows: minmax(260px, calc((100cqh − gap − padding) / 2))`), so **exactly four folders fill the viewport** and the rest are reached by scrolling. One column below the `sm` breakpoint.
- Each group renders as an actual **folder silhouette**, not a rectangle: a tab notch with an angled shoulder is cut out of the card via `clip-path` (`.folder-card`), with a second layer inset 1px (`.folder-card-inner`) supplying the hairline outline that follows the folder edge — `border` cannot follow a clip-path. Flat, no shadow, per `DESIGN.md` §4.
- Folder contents: the workflow count on the tab; group name; up to **three workflow names** with a shape-plus-color stage glyph and stage label, plus a "+N more" line (`workflow_preview`, capped server-side by `WORKFLOW_PREVIEW_LIMIT`); a one-line lifecycle summary covering *all* the group's workflows ("7 ready · 3 need review · 2 failed", from the `stages` map); and the auth readiness line ("● 5 of 5 apps connected" / "▲ 3 of 5 apps connected", per `DESIGN.md` §6's StatusDot convention).
- Hover lifts the folder 2px and brightens its outline; focus thickens the outline to a 2px clay trace (a clipped card cannot take a ring). Both have `prefers-reduced-motion` fallbacks.
- Clicking a folder navigates to that group's page.

**Components:** `WorkflowListPage`, `NewGroupDialog`, `FolderCard`, `FolderContents`, `StageBreakdown`, `UsageCards`.

**UX issues:**
- The three previewed workflows are the first three in storage order, not the three most in need of attention. The lifecycle summary line covers the remainder, and the group page has the full list.

---


### 2.3a Group Page (`/groups/:groupId`) — absorbed the Workflow Detail page, 2026-08-13

**Purpose:** A group's home and the sole per-workflow surface: manage its apps and their auth state, and run every stage of each workflow's lifecycle (record, compile, review, test, ready-to-package) without leaving the page.
**Inputs:** Group ID from route params.
**Outputs:** Added/removed/renamed apps, connected sessions, created/deleted workflows, renamed/deleted group, new recordings, compiled skills, sign-offs, test runs.
**User goal:** Get every app in the group signed in once, then take a workflow through its whole lifecycle from one screen.

**Layout (2026-08-13 redesign; refreshed 2026-08-15):**
- Action strip: the **Compile credits** and **Human Edit pool** usage cards on the left (§2.3b — these replaced the old hover-only meter pills), Rename, Delete (hidden for the `Default` group) and "+ New Workflow" on the right
- Top-bar subtitle: "N of N apps connected · N workflows", plus "sign in to start recording" while the group is not fully authenticated
- Two-column body: **Applications** column (left, hugs its content) and **Workflows** column (right, flexes to fill)
- Applications column: a connection summary line ("All apps connected" / "2 of 4 still need signing in") above `GroupAuthWizard` in `editable` mode — one row per app (Connect walks unauthenticated apps in sequence), plus a pencil (edit name/login URL/success URL) and trash (confirm-then-remove) icon pair per row; a "+" icon button opens `AddAppDialog` (name + login URL only — no Success URL field; see below). Success URLs (edit-only now) support a literal `{}` wildcard (e.g. `vercel.com/{}` matches any path reached after login)
- **Learned sign-in (P0: Application Authentication Recording, 2026-09-25):** Connect no longer closes itself on reaching a configured success URL — **Done is the only way a Connect session ends.** Clicking Done tries to learn a structured sign-in definition from the session and self-tests it before saving; a definition that doesn't pass keeps the window open with an amber "Couldn't confirm you're signed in yet — finish signing in and click Done again" caption plus a **Save anyway** button (force-saves the session with no definition, same as before this feature existed). A row whose app has a learned definition shows no `detect_warning` regardless of whether a Success URL is set — see `docs/TRD.md` §5.3.
- Session freshness (2026-08-15): opening the group page does **not** re-verify saved sessions — an earlier version fired a headless-Chrome recheck of every captured app on every page open, but that background probe had no timeout on most of its own steps and could wedge the whole backend (see `FIX.md`, same date). Freshness is now checked at the points it actually gates something: `cmd_start_recording` bounds the same probe to a hard 20s deadline per app and runs every captured app's check concurrently before a recording starts (not just the required ones — recording seeds every captured app's session). An app the probe finds expired gets `last_error` set, which flips its row from the ready-green treatment to a red "Session expired — sign in again" state with a **Reconnect** button (same re-auth flow as Connect); the group summary line and `groupReady` gate both count an expired app as not-ready, same as a never-connected one.
- **Verified vs. assumed (2026-08-16):** a green "ready" row means a saved session file exists — it does not by itself mean that session was recently confirmed to still work; `checked_at`/`verified` (`GroupApp`, `group_auth_status`) track that separately. `GroupAuthWizard` renders a ready-but-unverified app with an amber (not emerald) status dot, an "Not verified recently — may have expired" caption, and a **Check now** button in place of the empty action slot — a bounded, user-initiated probe of that one app (`cmd_check_group_app_auth`). The Applications panel header also gained a small **Check all** action next to the group's ready/blocked summary line, which probes every captured app in the group at once. A fresh Connect/Reconnect always marks the app verified immediately (a just-completed login is inherently confirmed); verification otherwise decays after 10 minutes, matching the recording gate's own probe TTL.
- Workflows column: **two lines per workflow** — name, stage badge, small pill badges naming the app(s) the workflow targets (matched by hostname against the group's apps — replaced the old raw target-URL text), Inspector and Delete icon buttons on the first; the full-width `WorkflowStageRail` on the second. (Sharing one line is what made the rail read as five loose buttons instead of a pipeline.) **Platform tags (2026-08-23):** the pills now come from the backend's per-workflow `used_apps` — every group app whose `login_url`/`success_url` matches the workflow's start URLs *or* any hostname its recording visited (`Workflow.visited_hosts`, captured at "Save Workflow Now"). A workflow that starts in Render and links into Vercel mid-recording shows both `Render` and `Vercel` chips immediately after saving, before any compile; these tags are the same set the runtime uses to decide which concurrent runs must serialize against each other. The renderer keeps a start-URL-only fallback for payloads from before this field existed.
- `WorkflowStageRail` — five nodes joined by connector segments that fill with state: **Record → Compile → Human Edit → Test → Ready to Package**. Each node *is* the action for that stage, enabled only once its prerequisite is met (disabled nodes carry a tooltip explaining what's missing, and use `aria-disabled` rather than `disabled` so that tooltip stays reachable by mouse and keyboard). The single node that is actionable right now carries the clay accent — the one place clay is spent on the row:
  - **Record** — enabled once the group is fully authenticated and the workflow has no recording yet; opens `RecordWorkflowDialog` inline
  - **Compile** — enabled once a recording exists; opens the inline compile panel (`CompilePanel`) under the row. Once a skill already exists, the same node instead opens a "Recompile?" confirm (uses the Human Edit pool) before opening the panel in recompile mode. While another workflow is compiling the click is refused with a toast, since only one compile is tracked at a time (§2.8)
  - **Human Edit** — enabled once a skill exists; navigates to `/edit/:skillId?from=/groups/:groupId`. Renamed from "Review" on 2026-08-15 so the rail, the sidebar and the usage card all use one word for the same thing
  - **Test** — enabled once a skill exists, the shared skill package has been built, and the workflow isn't stale (edited since the last build); toggles an inline panel under the row that mounts `WorkflowTestRow` wholesale (same input dialog, group-auth gate, live log, and pass/fail badge as before — this is now the *only* place a workflow is tested, see §2.10 note below)
  - **Ready to Package** — enabled once the workflow's stage is `ready`; navigates to Publish Skill Package

**Components:** `GroupPage`, `GroupAuthWizard` (now with row-level edit/remove), `AddAppDialog`, `NewWorkflowDialog` (group-scoped), `WorkflowStageRail`, `RecordWorkflowDialog`, `DeleteWorkflowButton`, `WorkflowTestRow` (reused inline), `InspectorPage`, `UsageCards`.

**UX issues:**
- None known.

---

### 2.3b Usage cards (`UsageCards` / `MeterCard`) — added 2026-08-15

**Purpose:** Make the two metered resources the product bills on — **compile credits** and the **Human Edit token pool** — visible wherever workflow work happens, instead of only as hover-only pills on the group page.
**Inputs:** `get_usage` (`fetchEntitlements`), cached 30 s.
**Outputs:** None — read-only.
**User goal:** Answer "how much do I have left?" without hovering anything.

**Layout:** Two cards side by side in the action strip of the Workflows page and the Group page. Each leads with **remaining** as the largest figure ("20 left"), with used/limit secondary ("30 of 50"), a usage bar, and a "Resets 1 Sep · Team plan" footer. Unlimited meters read "Unlimited" with no bar.

**Behaviour:**
- Bar fill is neutral while healthy, **Caution Amber ≥ 75%**, **Alert Red ≥ 90%**; at ≥ 90% the footer is replaced by a plain-language warning ("Running low — 5 compiles left"). Clay is deliberately *not* used — it stays reserved for the page's primary action (`DESIGN.md` §2, The One Accent Rule).
- The cards refresh the moment a credit is spent: `AppChrome`'s compile event bridge invalidates the `entitlements` query on every backend `quota` event.
- When entitlements are unavailable the card says "Usage unavailable" rather than disappearing, so the strip does not collapse. (The compact `MeterBadge` pill, still used in the Human Edit toolbar, keeps its old vanish-on-unavailable behaviour.)

**Components:** `UsageCards`, `MeterCard`, `MeterCardShell` (all in `EntitlementMeters.tsx`).

---

### 2.5 Recording Feed (`RecordingFeed.tsx`)

**Purpose:** Live view of captured events during recording.  
**Inputs:** Active recording session.  
**Outputs:** Real-time event stream display.  
**User goal:** Confirm the recorder is capturing events correctly.

**UX issues:**
- Events are shown as raw JSON — not user-friendly (e.g. `{"action":"click","target":{"role":"button","inner_text":"Submit"}}`).
- No screenshot preview during recording (screenshots are captured but not shown live).
- No "pause recording" capability — only start/stop.
- No indication of iframe depth (user can't tell if the recorder is inside a frame).

---

### 2.6 Human Edit list (`HumanEditListPage.tsx`)

**Purpose:** Entry point for reviewing the selected automation's compiled workflows, needs-review-first.
**Inputs:** The shared workflow selection.
**Outputs:** A row per compiled workflow with its confidence summary (`compile_status`/`compile_min_confidence`, computed at compile time by `_build_compile_report` and persisted onto the workflow in `handlers/compile.py`) and a "Review" link into the per-skill editor below.
**User goal:** See which workflows most need a human look, in one glance, without opening each one.

New in the 2026-07 redesign (Phase 1) — previously the only way to reach a compiled workflow was via the workflow page or a direct `/edit/:skillId` link.

---

### 2.7 Human Edit editor (`HumanEditPage.tsx`)

**Purpose:** Review and edit compiled workflow steps before signing off.  
**Inputs:** Compiled skill ID.  
**Outputs:** Patched skill document.  
**User goal:** Verify each step is correct and parameterize inputs.

**Step Editor Redesign (2026-09-29) — the "one plain question per step" pass.** Built from the design boards in `docs/artifacts/step-editor-redesign/`. Several things below are now stale in ways worth flagging up front rather than line-editing every paragraph:
- `CompileHealthBanner.tsx` was **deleted**. Its "N steps below threshold" jump-chips became a quiet warn dot on the matching row in `WorkflowStepItem.tsx`; its "Details" button is now Diagnostics, reached via the header's **More** menu. **Header decluttered further (2026-09-29):** the subtitle's "needs N% confidence" and "Recompile to refresh" text were both dropped — the subtitle now only shows the step count and, when present, `statusWord` (compile issues / review recommended).
- The header's Tools rail (Suggestions / Input variables / Workflow plan / Diagnostics, each an always-visible segmented button) collapsed to: **Suggestions** as its own button (it carries a live count) plus a **More** popover holding the other three — quieter, and the AI-usage-credits badge moved inside that same popover instead of sitting in the toolbar. **Swapped back (2026-09-29):** **Workflow plan** and **Input variables** are now the two standalone header buttons (Suggestions' old spot); **Suggestions** moved into the **More** popover alongside Diagnostics, keeping its live count as a trailing number on that menu row.
- Alignment pass (2026-09-29, board in `docs/artifacts/human-edit-page-polish/`): that toolbar (Suggestions / undo-redo / More / Approve) now sits in AppChrome's single 64px header row after the title via `PageHeader`'s `inlineActions` prop, instead of a second strip below it. Step rows truncate their label to one line (full text on hover) with badges on one wrapping row underneath. The wizard's Element phase stacks copy → full-width screenshot → one action row (box size · Draw a new box · View recording screenshots · Yes, continue), and every step layout uses a 32px side padding.
- Tool-dialog pass (2026-09-29, boards `Variables`/`Plan`/`Diagnostics`/`Selectors` in the same folder; the `Loop` board was deliberately **not** implemented — `patch_gate.py` doesn't allow validation on a for_each step): Input variables, Workflow plan and Diagnostics open as a 760px auto-height modal with a clay icon tile; Plan and Diagnostics get a Close footer. **Input variables** shows spotted `{{ids}}` as clay pills, each variable as a one-line summary card (type / default / optional / sensitive, plus "Not used in any step" in amber) that expands in place to the full editor on click; JSON editing is still under "Advanced". **Workflow plan** is flat numbered rows with "Verifies: …". **Diagnostics** is flat label/value rows with a coloured status; skill id + copy, compat fingerprint and frame/shadow depth moved behind "Advanced". **Review selectors** is a flat list of radio rows ("Primary · Test ID" eyebrow, mono selector, durability dot) — **clicking a row makes it the primary**; edit/remove appear on hover, uniqueness/source badges moved into the durability tooltip, drag and arrow-key reorder still work. `RecoveryAnchorsCard.tsx` lost its card chrome and reads "Landmark to search near, if every signal above stops matching", with Save shown only when edited.
- `HowClaudeSeesThisPanel.tsx` — already dead code per the 2026-07-10 note below (confirmed still unmounted, then deleted 2026-09-29 along with four other unimported components: `ValidationEditor.tsx`, `ConfidenceBanner.tsx`, `ActionBadge.tsx`, `StatusDot.tsx`).
- `InlineRetargetFlow.tsx` now opens every step layout with a `StepEditorHeading`: a "Step N · <type>" eyebrow plus a plain-language question ("What should this step do?", "Is this the right element?", "Will this keep finding the right element?", "How do we know this <verb> worked?" for the wizard's three phases). The 3-phase wizard's stepper is a local numbered-circle `WizardStepper` (brand-filled current phase), not the old label-below-icon `BuildPipelineStepper` (which is now unused outside this file's history — nothing else referenced it).
- `StepConfigForm.tsx`'s old "Action: X" card is gone — its Intent field is now a bare "Description" label (no card chrome), and Navigate's "URL" field is now "Page address". The raw selector list, selector-channel (CSS/ARIA/Text/XPath) card, frame-context card, anchors card and `ElementFingerprintCard` all moved behind a single closed-by-default "Advanced" `Collapsible` — nothing was removed, it's just out of the way until opened. The non-wizard step layouts (navigate/scroll/upload, if_present) wrap `RecoveryAnchorsCard` in its own closed-by-default "Recovery hints" disclosure the same way.
- `components/validation/AssertionEditor.tsx`'s check rows now show a hollow status circle + plain description + mono `type: "target"` + a **Blocks step** pill (replacing the old numbered-square + checkbox pattern) and an × remove button. The old single "+ Add check" button is now four one-tap quick-add chips (Text appears / Text disappears / Page address changes / Element state changes) plus a **More check types** popover for the rest of `ASSERTION_TYPE_GROUPS`. A newly-added check now defaults to `required: true` ("blocks step") instead of `false`, matching the redesign's framing that an added check is meant to actually verify something.
- `WorkflowHeader.tsx`'s left pane header is now a plain "STEPS" eyebrow + a brand text "+ Add step" button (no bordered button chrome); `TabDivider.tsx` now reads "New tab · host" instead of "Tab N — host".

**Components:**
- `WorkflowViewer.tsx` — step list with action/intent display; each row also renders a `BranchSummaryBadge` and safety badges (`allow_forced_action`/hover-chain — 2026-07-10) via `StepBadges`, and an indented collapsible `BranchSubList.tsx` under any `if_present` step, previewing its nested body (clicking a nested row selects the parent step and focuses that nested index — `editorStore.focusedBranchIndex`). **Tab-boundary dividers (2026-08-15, multi-tab recording):** `workflowViewer/TabDivider.tsx` renders a labeled rule ("Tab 2 — example.com") wherever consecutive steps' `StepEditorDTO.tab.id` differs, so a workflow that opens a new tab mid-recording reads as visibly crossing tabs instead of looking like one continuous page. Backend-only otherwise: `tab_open`/`tab_switch` markers already had labels in `describe.py`, unchanged.
- `InlineRetargetFlow.tsx` — center pane: embeds the re-target wizard and `StepConfigForm.tsx` together (retired the standalone `StepEditorPanel.tsx`). Branch steps (`if_present`/`try_dismiss`/`wait_for_one_of`) skip the wizard entirely — their bbox-driven model doesn't fit a candidate list or option set — and render `StepConfigForm` plus, for `if_present` only, `components/branch/BranchBodyEditor.tsx` (2026-07-10).
- `StepConfigForm.tsx` — edit intent, selectors, assertions for a step. **Intent field shows the workflow-plan prose (2026-08-25, BUILD-21):** the editable "Intent" input holds the workflow-intent graph's readable sentence (`StepEditorDTO.semantic_description` — the same source the Workflow plan panel renders), falling back to the machine token for steps compiled before prose existed; saves go through a new patchable `semantic_description` key (plain non-empty string) rather than the slug-validated `intent` key, and the machine token is shown as a read-only mono "Machine intent: …" hint under the field when it differs. **Validation card (2026-07, post-condition validation):** a self-contained "Validation" card (read-only wait-for description + the editable assertion list, via the shared `components/validation/AssertionEditor.tsx` — the same row editor `RetargetPhaseValidation.tsx` uses) with its own "Save validation" action, independent of the rest of the step form's single "Save step" submit. Hidden for scroll/marker steps (`editable_fields.validation`). Backed by `StepEditorDTO.validation.assertions`, newly surfaced by `step_to_dto` (previously only `wait_for`/`success_conditions` reached the client). Saved edits are gated by `conxa_compile/editor/patch_gate.py::validate_editor_patch`, now wired into `cmd_patch_step` — a manual edit that would drop a consequential step's only required assertion is rejected before it's persisted, not just flagged after the fact. **Reliability collapsible (2026-07-10):** a "Show reliability details" section (hidden by default, hidden entirely during the wizard's Phase 2 / for marker steps) mounting `StepIdentitySummary.tsx` (closes the `BUILD-4` gap — was built but never rendered), `RecoveryBehaviorCard.tsx` (plain-language recovery ladder from `StepEditorDTO.recovery_view`), and `ElementFingerprintCard.tsx` (`StepEditorDTO.fingerprint` — role/text/labels/testid/position + frame/shadow depth). **AI Review config block (2026-09-14, EXEC-13):** selected via the Add-action menu's new "AI Review" entry (category Validation), the form swaps in a dedicated field group in place of the selector/value fields every other action shows — a required **Question** textarea, an **Expected answer** preset select (yes/no + reason, a single text value, or no schema) that fills an editable raw-JSON schema textarea beneath it (the same "pick a preset, then hand-edit" pattern `ParameterizationDrawer.tsx`'s form↔JSON toggle uses), an **If a usable answer never arrives** select (Abort the run / Use a default value / Continue anyway) that reveals a **Default value** field only in the "use a default" case, and the existing generic **Output binding name** field (`action_spec.value_label`) unchanged below it. The Validation card and Selector channels card are both hidden for this step — a review checkpoint has no DOM target and no post-condition to assert. Saving validates the schema textarea as JSON client-side before it ever reaches `patch_gate.py`.
- `branch/BranchBodyEditor.tsx` (2026-07-10) — add/remove/reorder + per-step inline editing for an `if_present` step's nested body. Deliberately not built on `StepConfigForm` (whose `patchStep` calls are hardcoded to the top-level `step_index`); a standalone, path-aware editor instead, visually consistent with `StepConfigForm`'s card idiom. Reorder is up/down buttons, not drag-and-drop.
- `ConfidenceBanner.tsx` — a suggestions-lint rollup (errors/warnings/"looks solid", from `wf.suggestions`) described in the redesign doc as spanning the pane grid, but **not currently mounted anywhere** (confirmed via repo-wide search, 2026-07-10) — this description is stale; the component exists but is dead code. Distinct from `CompileHealthBanner.tsx` below, which *is* mounted and reads a different signal (`compile_report`, not lint suggestions).
- `CompileHealthBanner.tsx` (2026-07-10, mounted under the page header) — workflow-level status pill (`compile_report.status`) + min-confidence + clickable "N steps below threshold" chips (jump to step) + required-runtime, with a "Details" button opening the new Diagnostics tool tab.
- `WorkflowPlanPanel.tsx` (2026-07-10, new "Workflow plan" Tools-rail tab) — read-only `intent_graph`: goal, per-step intent + verification anchor, decision points, expected end state.
- `DiagnosticsPanel.tsx` (2026-07-10, new "Diagnostics" Tools-rail tab) — compile report detail, LLM router stats, compiler policy version, required runtime, structural-fingerprint presence, and the selected step's `stable_hash`/`compat_fingerprint`. Distinct from the landing page's "Diagnostics" card (raw backend metrics JSON, unrelated concept, still only on the no-skill-selected state).
- `HowClaudeSeesThisPanel.tsx` — read-only agent-contract preview (Tools rail)
- `ParameterizationDrawer.tsx` (redesigned 2026-07-31) — exports only `ParameterizationInlinePanel` now; the unused side-`Sheet` export was deleted. Declared variables render as a single-line-per-row table (name/label/type/default/optional/sensitive) instead of a six-field card, with the `{{`/`}}` braces built into the name input's frame rather than shown as a helper line below it — the same field is reused in "Turn a recorded value into a variable" (renamed from "Find & replace") so its second input can no longer be mistaken for free text. The header-level "+ Add" button is gone; a full-width dashed "New variable" strip appends a row instead, leaving "Save variables" as the tool's only primary action. A new "Optional" checkbox (auto-checked and disabled once a Default is set) marks a variable as not required at execution — see `docs/Backend-Schema.md` §3.1's `SkillInputVariable.optional`. The footer sits below the panel's own bounded scroll region (not the shared dialog `ScrollArea`), so it stays in view instead of scrolling out of reach — see `HumanEditPage.tsx`'s per-tool branch around the `variables` case.
- `RecordingScreenshotsPanel.tsx` — match steps to recording screenshots
- `SuggestionsPanel.tsx` — AI-suggested improvements
- `EntitlementMeters.tsx` — shows Human Edit pool for LLM-assisted edits
- `components/copilot/CopilotLauncher.tsx` + `CopilotPanel.tsx` (2026-09-10, BUILD-26) — the Human Review Conxa Copilot: a circular launcher (bottom-right by default, freely draggable anywhere on screen via native Pointer Events — `hooks/useDraggable.ts` — position persisted via browser storage) that opens a floating chat card above it, mounted as a sibling of the pane grid rather than a Tools-rail entry — that rail is a shared modal dialog and would hide the step list mid-conversation. The panel header's "new session" button archives the outgoing conversation to disk (`copilot_save_session` RPC) before clearing it. Per `DESIGN.md`'s One Accent Rule the launcher itself is a flat charcoal surface (clay is already spent on this page's Approve CTA); a small clay dot appears only when a proposal is pending, and clay returns on `ProposalCard.tsx`'s Accept button, because at that moment it genuinely is the current primary action. A pending proposal renders inline in the chat (before/after preview, Accept/Reject) and is the same kind of "you'll lose this" state the re-target wizard already guards — switching steps with one pending reuses `WorkflowViewer`'s existing discard-confirm `AlertDialog`, and discarding this way still logs a rejection rather than silently dropping it.
  - **Streaming, thinking indicator, edit-and-resend (BUILD-26 stage c).** The reply now types itself in word by word (`copilotStore.ts`'s `streamingText`, filled from `copilot_delta` backend events) instead of appearing all at once after a silent wait. Before the first chunk arrives there is nothing to show yet, so the panel renders `CopilotThinkingBubble` — three softly pulsing dots (`animate-pulse`, matching `skeleton.tsx`'s existing pulse convention; deliberately not `animate-bounce`, which reads as dated/tacky) — and swaps to the live streaming bubble on the first delta. Hovering any past question of yours reveals a small pencil icon (`CopilotMessage.tsx`); clicking it truncates the conversation back to that point, clears any pending proposal (it would otherwise reference a turn no longer in the transcript), and re-seeds the composer with the original text so you can correct it and resend, rather than piling a correction on top of an unrelated tangent.
  - **`components/copilot/VerifyCard.tsx` (BUILD-26 stage e).** Renders where `ProposalCard` was, right after a proposal is accepted. First shows a **Verify fix** button (`status-warn` outline, matching `DESIGN.md`'s existing warning token — no new color introduced); clicking it counts the workflow's non-reversible steps (no build, no browser yet) and swaps to a `status-warn`-toned confirm card naming the count with **Cancel** / **Run anyway**. Confirming streams rebuild+retest log lines into a small scrollable tail, then resolves to a **Fixed** (`status-ok`, `CheckCircle2`) or **Still failing**/**Progressed** (`status-warn`, `AlertTriangle`) verdict card with a Dismiss action. Nothing here auto-runs — every state transition out of idle is a deliberate click.
  - **Overlay-insertion proposals (BUILD-26 stage f).** `ProposalCard.tsx` gained a second render branch for `command: "insert_overlay_branch"` proposals — same Accept/Reject buttons, but the before/after preview describes an inserted branch step ("Insert try_dismiss after step N") rather than a field diff, since there is no existing field being changed.

**Re-target wizard — STALE, superseded by inline embedding (flagged, not yet reconciled):** the three-separate-routes description below was accurate for an earlier iteration; the wizard has since been merged into `InlineRetargetFlow.tsx`, embedded directly in the center pane next to the step list (see the Layout paragraph above) rather than living on its own routes/pages. The phase-by-phase behavior (Pick element → Review selectors → Validation, the pruning rules, the `regenerate` LLM gating) is unchanged — only *where* it renders changed. This subsection needs a follow-up rewrite to describe `InlineRetargetFlow.tsx` instead of the retired `RetargetPickPage.tsx`/`RetargetSelectorsPage.tsx`/`RetargetConfirmPage.tsx` route trio; left as-is below to avoid rewriting under time pressure with unverified route-removal details. **Phase 3 note (2026-07, post-condition validation):** Phase 3 was renamed "Confirm & apply" → "Validation" and is no longer just a keep/replace diff — it now surfaces the step's enforced (`required=True`) assertion (the compiler's single deterministic post-condition for the action) plus any advisory checks, and lets the user edit the flat assertion list (type, target, expected value, timeout, required) via `RetargetPhaseValidation.tsx` before applying; a step with no enforced check is flagged rather than silently accepted. Edits round-trip through `cmd_retarget_apply`'s new `edited_assertions` payload field.
1. **Pick element** — `/edit/:skillId/retarget/:stepIndex` (`RetargetPickPage.tsx`) — the step's current target is preselected, so drawing a new region is optional. Continue runs `cmd_retarget_preview` with `regenerate` set to whether a new region was actually drawn, then navigates to the selectors route (`RetargetPhasePick.tsx`, reuses `ScreenshotViewer`'s draw mode via `autoActivateDraw`).
2. **Review selectors** — `/edit/:skillId/retarget/:stepIndex/selectors` (`RetargetSelectorsPage.tsx`) — shows each candidate's actual selector string (monospace, always visible — not hidden behind a toggle) alongside an engine badge, a verification badge, and a labelled durability bar; the collapsible now holds only the manual-selector override. On the review path the candidates are built from the step's `identity_bundle` signals, so the verification badge reflects the compiler's own `unique_at_compile` verdict (computed at compile time against the recorded DOM *and* accessibility tree) rather than a weaker offline CSS re-check — role=/text= selectors that the offline checker can't evaluate therefore read "Unique match" instead of an unverified state, and the engine/durability come from the compile. Badge states: **Unique match**, **Not unique** / **Matches N elements**, and **Checked at run time** (the offline-unverifiable case, e.g. an older skill with no identity_bundle). The review list is pruned (`_prune_review_candidates`) so it doesn't offer options that make bad targets — non-unique matches (could resolve to the wrong element) and any selector below a hard durability floor (`_MIN_OFFERED_DURABILITY`, 30%; e.g. an absolute XPath or a fragile structural selector) are dropped. The prune can empty the list — if nothing clears the bar, the wizard shows the "re-pick" prompt rather than offering a too-weak selector. Because apply rebuilds `fallback_selectors` from the shown candidates, applying after a review also drops the pruned selectors from the skill, keeping them out of the runtime. Ambiguous or zero-candidate results are flagged with a way back to Phase 1 (`RetargetPhaseSelectors.tsx`). Only when the user re-picked the element (`regenerate=true`) does `cmd_retarget_preview` re-run selector generation against the original recorded DOM snapshot (LLM-assisted, the sanctioned 1-click-fix exception — see `CLAUDE.md` Key Invariants); continuing on an **unchanged** element instead reads back the selectors already produced at compile time — no LLM call, no Human Edit pool consumed, and no dependency on the recording session still existing.
3. **Validation** (was "Confirm & apply") — `/edit/:skillId/retarget/:stepIndex/confirm` (`RetargetConfirmPage.tsx`) — shows the step's current vs. proposed wait-for/assertions in plain language with a "keep existing" checkbox, and the enforced post-condition plus advisory checks as an editable flat list (type, target, expected value, timeout, required); a strong, unambiguous pick with unchanged validation collapses this phase to a single confirm (`RetargetPhaseValidation.tsx`). **Expected outcome recap (2026-07-12):** below the editable "Outcome checks" list and above the Back/Apply row, a read-only `ExpectedOutcomeSummary` card (`components/validation/AssertionEditor.tsx`) recaps the step's currently-compiled post-condition (wait-for description + each assertion, or an explicit "no check configured" warning) — separate from whatever's being proposed/edited above it in this same phase. Never writes anything, so it can't conflict with the editable list.

**Identity-signal transparency (2026-07-10):** Phase 2's candidate rows also carry an **orthogonality class** badge (test-contract / semantic-aria / visible-text / spatial-anchor / structural — the independent "axis of identity" a selector depends on, from `IdentitySignal.orthogonality_class`) and a **source** badge (compiler / AI-assisted / manual edit), plus a step-level **compile confidence** percentage in the Selectors card header — all previously computed and persisted in `identity_bundle` but not shown. Manually editing or adding a selector now marks it `source: user`. A read-only **"Current identity"** card (`StepIdentitySummary.tsx`) showing the same badge set for the step's already-compiled signals was built for Phase 1 (Pick element) but was not wired into `RetargetPhasePick.tsx` at the time — **mounted 2026-07-10**, though in `StepConfigForm.tsx`'s new "Reliability" collapsible rather than specifically in Phase 1 (see the Components list above); the literal Phase-1 wizard placement remains open if still wanted (`TODO.md` `BUILD-4`, now marked resolved-with-caveat).

Because each phase is a separate route that unmounts on navigation, the cross-phase state (drawn bbox, fetched preview, chosen selector, keep-validation) lives in a small Zustand store (`store/retargetStore.ts`); the shared page frame, phase stepper, and workflow-loading hook live in `components/retarget/retargetFlow.tsx`. The selectors and confirm routes redirect back to Pick if their preview state is missing (e.g. a hard reload or a pasted deep link), so a phase can't render without its prerequisites. Each page carries its own `PageHeader` (title, step description, "Back to editor") and the same phase stepper.

Nothing persists until Apply (on the confirm route), which calls `cmd_retarget_apply` once — bbox, target selectors, `identity_bundle`, and (optionally) validation land as a single undo entry, updates the same `['workflow', skillId]` react-query cache `HumanEditPage` reads, resets the wizard store, and navigates back to `/edit/:skillId`. If the original recording session is gone, Phase 1 offers an "apply position only" fallback that updates the bbox without touching selectors.

**Layout (3-zone editor):** the redesign doc's plan called for a confidence banner (`ConfidenceBanner.tsx`) spanning the full width above the pane grid — a headline rollup of `wf.suggestions` — but that component was never actually wired in (confirmed 2026-07-10; see the Components list above). What **is** mounted there today, as of the 2026-07-10 redesign, is `CompileHealthBanner.tsx` — a different signal (`compile_report.status`/`min_confidence`, not lint suggestions), also serving as the page's first-seen trust indicator. Below it: a top toolbar (skill title + id/copy, version, undo/redo, Back, and the brand-clay **Approve** CTA — a `BadgeCheck`-iconed `variant="brand"` button, renamed from "Finish editing" — with a live "N unsaved" indicator driven by the editor store's `dirtySteps`), a slim entitlement-meter strip, then a resizable three-pane body: left **Workflow** step list (`WorkflowViewer`, now also showing branch sub-lists — see above), center **step editor + re-target wizard** (`InlineRetargetFlow.tsx`, embedding `StepConfigForm.tsx` — see below), right **Tools** rail, opened as a shared modal dialog keyed off a single `openTool` state rather than separate always-visible panes. The Tools rail is a vertical segmented control — Suggestions / Input variables / Recording screenshots / How Claude sees this / **Workflow plan** / **Diagnostics** (the last two added 2026-07-10) — with a framer-motion active indicator and cross-faded panels; each tool sits beside its own info affordance. **How Claude sees this** (`HowClaudeSeesThisPanel.tsx`) is a read-only preview of the compiled skill's "agent contract" (§10.1) — name (`package_meta.title`), a synthesized plain-language description built client-side from the steps' `human_readable_description` (via `compactStepLabel`, honestly framed as a summary, not the verbatim generated text), and required inputs (`wf.inputs`, reusing `rowsFromServerInputs`) — all from data already fetched for this page, no new backend calls. The "no skill" state is a guided landing with a Record → Compile → Edit → **Approve** explainer, a primary **Resume a skill** card, and a **Diagnostics** card whose raw metrics JSON is collapsed by default — a distinct, older concept from the new per-skill "Diagnostics" Tools-rail tab (raw backend metrics vs. this workflow's compile telemetry).

All three pane columns (`WorkflowViewer`'s aside, `InlineRetargetFlow`'s panel, the Tools `<aside>`) share one gradient-fill depth treatment (`linear-gradient(180deg,rgba(17,24,39,0.9),rgba(7,10,16,0.95))` + `ring-1 ring-inset ring-white/[0.03]`) applied inline rather than via the reusable `components/ui/panel-chrome.tsx` `PanelChrome` component, because these are flush grid columns against the pane resizer, not floating/inset panels — `PanelChrome`'s rounded corners + outer shadow are reserved for panels with margin around them (e.g. `StepConfigForm.tsx`'s cards use `PanelChrome`-equivalent styling via a shared `PANEL_CARD_CLASS`). Status colors (`--status-ok/warn/error`, `globals.css`) replace what were previously hardcoded emerald/amber/red/sky classes in `BuildPipelineStepper.tsx`, `RetargetPhaseSelectors.tsx`'s uniqueness badges, and `SuggestionsPanel.tsx`'s severity badges — surfaced as `Badge`'s new `success`/`warning` variants (`destructive` already existed) and a new `Button` `brand` variant, both in `components/ui/`.

**Sign-off behavior (revised 2026-09):** **Approve** (renamed from "Finish editing," redesign doc §12 Phase 3) awaits `sign_off_workflow` and surfaces failure as a toast instead of silently swallowing it. If signing off completes the workspace's build gate — every workflow compiled and signed off — `cmd_sign_off_workflow` auto-builds the shared skill package (no separate Build Skill Package page visit); either way the editor navigates to the workflow's own group page (`GroupPage.tsx`), where its row's **Test** toggle is the way to run it — there is no standalone Test Skill page anymore (see §2.10).

**Compile → Human Edit is now a direct hop.** `CompilePanel.tsx` no longer waits for a "Review steps →" click once a compile finishes — it navigates straight into Human Edit (`history.replace`, so Back from the editor doesn't return to a finished compile) — but only when the panel watched the run finish; re-opening the panel on an already-finished compile just shows its logs. The moment Human Edit opens, a mandatory `ReviewQuestionsDialog` (one Yes/No question at a time, no close button, Esc and outside-click both no-op) walks the reviewer through every compiler-detected "generalize to a loop" suggestion and every recorder-flagged optional-popup hint still pending on this skill, before the editor becomes interactive. Answering everything makes the dialog disappear on its own (it recomputes its pending list from the live workflow after each answer); a workflow with nothing pending never shows it. A recompile regenerates fresh hints/suggestions, which is what "answered once per compile" comes from — there's no separate persisted "already asked" flag.

**Two-tier contextual help (`InfoHint` + `Tooltip`):** Every "i" affordance is a themed click-to-open popover (`components/ui/info-hint.tsx`) showing a plain-language **summary** for non-technical users plus an expandable **"Technical details"** section for power users. Help copy is centralized in `lib/editorHelp.tsx`. Short icon-button labels use a themed `Tooltip` (`components/ui/tooltip.tsx`) instead of native `title=`. Both build on Radix (`components/ui/popover.tsx`) and animate via the `.anim-pop` CSS layer in `globals.css`, degrading to instant under `prefers-reduced-motion`. The clay brand accent is the `--brand*` token set in `globals.css`.

**Resolved UX issues:**
- Tool affordances now carry first-class, themed help (summary + technical detail) rather than terse native tooltips.
- Validation/Suggestions/Variables/Screenshots/Selectors are switchable in a single animated rail; selected step uses the brand-clay highlight.
- Unsaved edits are now surfaced explicitly in the toolbar; the empty workflow shows an "Add first action" state.

**Remaining UX issues:**
- Saves are still per-step and implicit beyond the unsaved-count badge — no inline per-field "saved" confirmation.
- The screenshot panel still requires manual matching of recording screenshots to steps — should be auto-matched.
- No diff view for a manual edit (can't see what changed from the compiled original) — a
  copilot-proposed change is the one exception (2026-09-10, BUILD-26): `ProposalCard.tsx` shows
  a before/after preview before it's applied. A manual `StepConfigForm.tsx` save still has none.
- No guided sign-off checklist before Finish.

---

### 2.8 Compile (`CompilePanel.tsx`) — inline panel under the workflow card (2026-09-28)

Compile/Re-compile opens a log panel under the workflow's row on the Group Page (same slot as the Test panel; only one of the two is open at a time). Clicking the card shows/hides whichever panel it has; hidden panels stay mounted so logs survive. Finishing a compile jumps to Human Edit. The old `/workflows/:id/compile/:sessionId` route was removed.

**Purpose:** Turn a recording into a skill — the user decides when to spend a compile credit.
**Inputs:** The Group Page's own Compile/Recompile rail node (`WorkflowStageRail` — see §2.3a); per-workflow session ID.
**Outputs:** Compiled skill ID + step count + the compile-confidence summary (see §2.7).
**User goal:** Compile (or recompile) a specific workflow without hunting for it.

`CompilePage.tsx` and its sidebar entry were removed as part of the Workflow Groups redesign — it only ever duplicated the Compile/Recompile buttons that already lived on the per-workflow page. Those buttons themselves were folded into the Group Page's `WorkflowStageRail` on 2026-08-13 when the standalone Workflow Detail page was removed. Triggering Compile or Recompile opens the inline `CompilePanel.tsx` (7-phase progress) under the workflow's row.

**Run ownership (2026-08-15):** the run itself lives in `store/compileStore.ts`, not in the page. `CompilePanel.tsx` is a *view* over it, and `AppChrome` holds the single app-wide subscription to compile events. Consequences:
- Navigating away no longer loses the run. The backend already ran each command on its own thread (`backend.py`), so the compile kept going; now the UI keeps up with it and reattaches on return.
- `start()` is idempotent on `workflowId:sessionId:mode`. Before this, remounting the page re-fired `cmd('compile')` — a second concurrent compile on the same recording, and a **second compile credit reserved**.
- Only one run is tracked at a time. `window.conxa.cmd` never exposes the backend request id, so streamed events carry nothing the renderer could correlate to a specific run; rather than plumb an id through main + preload, a second, different compile is refused while one is in flight (the rail toasts, and the panel shows an inline "Another workflow is compiling" message).
- Every backend `quota` event invalidates the `entitlements` query, so the usage cards (§2.3b) move as credits are spent.

**Meter behavior:**
- First compile consumes 1 compile credit.
- Recompile uses the Human Edit pool.

**UX issues:**
- Progress steps (normalize → dedupe → enrich → selectors → assertions → recovery → package) are shown but LLM sub-steps are hidden.
- No persistent compile history (only the most recent run is retained, and only for the session).
- Concurrent compiles are refused rather than queued — see the request-id note above for what would need to change to support them.

---

### 2.9 Build Installer Page (`BuildInstallerPage.tsx`)

**Purpose:** Advanced/secondary action — package an already-published skill pack release into a distributable NSIS installer. Most routine updates never need this at all; **Publish Skill Package** (§2.12) is the primary release action.
**Inputs:** Company slug, logo — no per-workflow selector; the page acts on the workspace's one shared skill pack, fetched via `fetchSkillPack()`. Version and release notes are no longer collected here — they're read from the workspace's latest published skill-pack release (via `fetchSkillPackVersions`).
**Outputs:** Installer path, cloud download URL (when the optional cloud upload succeeds).
**User goal:** Produce a distributable `.exe` for a release that's already shipped via Publish Skill Package.

**Gating (redesigned 2026-07, Phase 4):** the Build button is disabled — with an inline banner linking to Publish Skill Package — until a skill-pack release exists for the workspace (`cmd_build_installer` raises `skill_pack_not_published` server-side if `pack.json` has no `sync_token`, i.e. nothing has ever been published). This replaced the old flow where clicking "Build Installer" silently published the skill pack as a side effect via `Backend._publish_skill_pack_for_installer`.

**Cloud upload is optional (2026-07 redesign):** `cmd_build_installer` now catches any installer-upload failure and returns it as a non-fatal `cloud_upload_error`/`cloud_upload_error_message` field on the result — the page renders it as an amber warning banner, not a build failure, since the local installer was already built successfully and installer hosting will eventually move to Conxa's own cloud build pipeline.

**Meter behavior (removed 2026-07):** the installer-slot meter pill was removed from this page — slot gating now happens entirely at skill-pack publish time (§2.12), since installer upload is unmetered.

**Pipeline stepper:** now 2 stages ("Build Installer" / "Upload to Cloud") — the old 3rd "Publish Release" stage was removed since publish no longer happens here.

**UX issues:**
- `cloud_upload_error: installer_upload_too_large` still shows via the page's local `humanizeError()` map rather than the shared `errorMessages.ts` — pre-existing duplication, not touched by the 2026-07 redesign.

---

### 2.10 Testing a workflow (no standalone page — `GroupPage.tsx`)

**Purpose:** Run a compiled workflow against the local runtime for validation.
**Where:** There is no dedicated Test Skill page (`TestSkillPage.tsx`, `WorkflowTestList`, and `workflowTestSummary()` were removed 2026-09-27, together with the `/test` route and sidebar entry). Testing happens from a workflow's own row on its group page: the row's **Test** toggle (§2.4/§2.5's group-page workflow row) mounts `WorkflowTestRow` inline — same input dialog, group-auth gate (`RunGateDialog`), live log, and pass/fail badge the old page offered, just scoped to one workflow instead of listing the whole workspace.
**Inputs:** Test inputs for that one workflow, entered inline.
**Outputs:** Pass/fail result, runtime output text.
**User goal:** Confirm the workflow works end-to-end before shipping to customers — reached immediately after approving it in Human Edit, with no intermediate stop.

**Cancel + elapsed time (EXEC-35, 2026-09-04):** each running test's "Testing…" label shows live elapsed time, and a Cancel button appears beside it — calls the runtime's `cancel_execution` MCP tool (via a `cancel_test_workflow` backend command that tracks the run's `run_id`) so a stuck or unwanted test returns to idle in a few seconds instead of only ever ending via the runtime's own 900s subprocess timeout.

**UX issues:**
- Runtime must be installed locally for testing — there's no inline message when it's not found (just `runtime_not_found` error code).
- No visual step-by-step progress during test execution.
- No "passed without AI recovery" caveat yet (Studio only exercises Tier A recovery, `CONXA_MAX_RECOVERY_TIER=2` — see `docs/App-Flow.md`); adding it is Phase 3 of the redesign.

---

### 2.11 Publish Skill Package — the Release Center (`PublishPage.tsx`)

**Purpose:** The primary, mandatory, version-controlled release-management action — ship a skill-pack update to customers who already have Conxa installed, via the runtime's delta-sync, with zero installer rebuild required. As of the release system (2026-08-19) this is a full enterprise release workflow, not a bare upload form: release candidate → what will change → where it will go → publish → release history → deployment status → audit trail.
**Inputs:** Version (semver), release notes — no per-workflow selector; the page acts on the workspace's one shared skill pack, fetched via `fetchSkillPack()`.
**Outputs:** Deterministic diff preview against the current stable release, immutable release history (version, status, artifact hash, publisher, `is_latest` — now tracking the *stable channel*, not "most recently published"), runtime deployment status, per-slug release audit trail, sync endpoint, tracking URL, workspace ID.
**User goal:** Ship a skill-pack change to customers as fast as possible, see exactly what's about to change before doing it, and undo a bad release without waiting on a rebuild.

**Shipped 2026-07 (Phase 4)**, replacing the Phase-1 stub. `Backend._publish_skill_pack_for_installer` was renamed to `_publish_skill_pack` and extracted from Build Installer's call chain into the new mandatory `cmd_publish_skill_pack` RPC, which this page calls via `publishSkillPack()`. Skill-pack upload is **mandatory** — publish fails the whole action if the cloud upload fails (`_CommandError("cloud_publish_failed", ...)`), by design (per the versioned-installer-architecture requirement).

**Release system (2026-08-19):** new pure-logic module `lib/releaseState.ts` (dependency-free, tested by `test/releaseState.test.mjs`) drives every derived UI state — candidate readiness, the publish button's compound gate, the idle/publishing/success/failure states, release badges (`stable`/`superseded`/`pending`/`failed`), and rollback eligibility. New components under `components/release/`: `DiffPanel` (the deterministic "what will change" summary + progressive-disclosure technical detail), `ReleaseHistoryTable` (with inline `RollbackDialog` — an explicit confirm showing current stable, target, and effect, gated to only render for a valid rollback target), `DeploymentPanel`, `ReleaseAuditLog`. `fetchSkillPackVersions()`'s response gained `current_stable`; new calls: `previewRelease()`, `fetchReleaseDetail()`, `fetchReleaseDiff()`, `rollbackRelease()`, `fetchDeployments()`, `fetchReleaseEvents()`.

**Two-step layout (2026-09-29):** the middle pane is now **1 Run the test → 2 Release details**. Step 1 is an amber card that embeds the same `WorkflowTestRow` used on the Group Page (inputs dialog, auth gate, cancel), so the test runs without leaving Publish; it needs the full Workflow record, fetched with `fetchWorkflow()` only while the test hasn't passed. When the test passes (`['skill-pack']` is invalidated on completion) it collapses to a green "Test passed" row and unlocks step 2. While locked, the form is disabled and the footer reads "Locked until the test passes"; Publish turns the brand color only when `canPublish` is true. The skill list uses shape+color glyphs (check = test passed, dashed circle = not passed — test status only, not published status), and "Publishing does not deploy" is a three-node flow strip (Publish from Studio → Ready for Release → Admin releases in Conxa Cloud). Mockup: `docs/artifacts/publish-skill-package-redesign/`.

**Publishing progress is honest, not animated.** The publish transaction is one opaque cloud call from the Studio's point of view — `stageChecklist()` only marks a checklist item done once a real `stage` event (`validated`/`uploading`/`published`/`failed`) has actually been observed from the backend, never guessing intermediate cloud-side progress it can't see (persist artifact / create release / move channel / write audit all happen server-side inside the single "uploading" step).

**Version history:** `fetchSkillPackVersions()` → `GET /api/v1/workflows/{installer_version}/skill-packs/versions` (workspace derived from Clerk session) — the version/release-comment/publishing-limit surface that moved here from Build Installer, per the original design brief. Republishing an already-used version number is rejected with `skill_pack_version_exists` (409); republishing a byte-identical artifact under a *new* version number is rejected with `skill_pack_artifact_unchanged` (409) rather than silently minting a no-op release.

**Deployment status is deliberately partial.** Only `up_to_date`/`pending`/`offline`/`unknown` are shown — `updating`/`failed`/`rolled_back` aren't derivable from what the runtime currently reports (state at phone-home, not sync outcomes), so the UI never fabricates them. A registration with no `skill_versions` field (an already-deployed runtime that hasn't self-updated to report it yet) reads as `unknown`, never a guessed `up_to_date`.

**Read-only cloud mirror:** the Cloud dashboard's Skill Packages detail page (`SkillPackageVersionsPage.tsx`) shows the same release history + deployment sections for visibility (e.g. a support engineer checking release state without opening Build Studio) — no publish or rollback controls there; those stay Studio-only.

**Meter behavior:** the header pill was repurposed 2026-08-08 from the now-removed skill-pack-slot meter
to compile credits remaining — there is no longer any limit on how many distinct product slugs a
workspace may publish under (`docs/PRD.md` §11). Trial-expired, machine-limit, and distribution/
white-label errors from a publish or installer-upload attempt surface through the same error-message
map as every other entitlement code (`lib/errorMessages.ts`) — no dedicated UI state per code yet.

**Shared components:** the log/result-card UI (`components/BuildLogUi.tsx`) is shared with Build Installer (§2.9) to prevent the two pages' visual language drifting apart. The "Built Packages" sidebar list (`components/PluginListSidebar.tsx`) this section used to reference was removed in the 2026-08-12 refactor — both pages became workspace-scoped with no per-automation selector, so there's nothing left to switch between.

**Known gap:** Build Studio has no client-visible role concept today (a local desktop tool, typically single-operator), so `canManageReleases()` in `releaseState.ts` is defined but not wired to a real "am I admin" check on this page — publish/rollback controls always render, and `require_admin` enforcement is entirely server-side. Not a security gap (the server never trusts the client), but a non-admin user would only discover they can't publish when the 403 comes back.

---

### 2.12 Inspector (`InspectorPage.tsx`, route `/workflows/:workflowId/inspector`)

**Purpose:** On-demand package-file browser and internals viewer for the selected automation — the demoted home for what used to be the top-level Packages page.
**Inputs:** The workflow id from the route, with `?from=` as the back target (opened from the Group Page's per-row "Inspector" icon button — see §2.3a). A full page, no longer a right-hand drawer; matches it to the built skill package by comparing the workflow's own slug against each packaged skill's `workflow_slug` (the link `list_skill_packages`/`list_skill_package_files` expose between a workflow and the shared package).
**Outputs:** Read-only file tree + preview; "Open in Explorer"; a "Rebuild package" action (calls `buildSkillPackage()`, workspace-scoped — the manual escape hatch now that building has no page of its own, since sign-off auto-builds in the normal case).
**User goal:** Audit built package contents when something needs a closer look — not part of the everyday flow.

Replaces `SkillPackagesPage.tsx` (2026-07, Phase 1), reusing its `PanelChrome`/`StructureTrieRows` tree components and `lib/skillPackageTree` helpers unchanged. Deliberately scoped down from the original page: no rename/delete, no resizable panes, no cross-workflow package list — those were package-*management* features for engineers auditing the whole `data/skill-packages/` tree, not part of what an Inspector needs to do for a single workflow. The bundle_root path and per-file paths now live only on this page, never on a default surface.

**UX issues (carried over):**
- Rebuild has no confirmation step (it's cheap and idempotent, but a stray click could surprise a user mid-review).
- No connection back to which workflow in the workspace produced which file — the tree is package-wide (shared across every workflow that compiles into it), not workflow-scoped.

---

### 2.13 Settings Page (`SettingsPage.tsx`)

**Purpose:** Show the signed-in account, workspace usage, and app/version info; sign out; check for app updates.  
**Layout:** Left section nav (Account, Usage, About; scrolls to the section) beside a content column capped at `max-w-3xl`. The nav is hidden below `md`.  
**Sections:**
- **Account** — initials avatar, name, email, organisation, Sign out.
- **Usage** — 2×2 grid of meters (seats, machines, compile credits, AI usage credits) with the used count large; metered meters also show a thin usage bar, unlimited ones do not.
- **About** — product, description, version, and software update (check / update now / download progress), plus Terms and Privacy links.

**Inputs:** None (read-only, plus Sign out and update actions).  
**User goal:** Confirm who is signed in, see what has been used, and keep the app current.

**UX issues:**
- The section nav highlights on click only (no scroll-spy).

---

## 3. Cloud Dashboard Screens

Source: `conxa-cloud/frontend/`

### 3.1 Marketing/Landing Page

**Path:** `app/(marketing)/page.tsx`  
**Purpose:** Public landing page for Conxa. Rebuilt 2026-08-06 as a **communication-first scroll story** for enterprise buyers (CEO/CTO/CIO/VP Eng/Ops) — the prior version was feature-led and never explained the problem before listing capabilities. Each section lands one idea and answers the question the previous one raised, at plain-language reading level.

**Rebuilt again 2026-08-12** around the pricing ladder (TODO.md CLOUD-10). The 2026-08-09 ladder rewrite
(`e7939b2`) had updated `/pricing`, the nav, the FAQ and the docs, but left this page carrying the old
positioning and no price at all. Two changes: the hero now leads with the business outcome and names the
four rungs, and `PricingTable` is mounted on the page itself. Twelve sections became ten — `Problem` +
`WhyAiNeedsIt` were one argument told twice, as were `OldVsNew` + `Outcomes`.

**Homepage v2 (2026-09-29).** The homepage was rebuilt so each section has its own visual world instead of one repeated card template. Design source: `docs/artifacts/homepage-v2/`. Components live in `src/components/marketing/home/` (plus the untouched `sections/DemoStory.tsx`); animation is CSS-only (`.hm-*` keyframes in `src/index.css`) with one `prefers-reduced-motion` block that stops them.

**Section order (`app/(marketing)/page.tsx`):**

| # | id | Answers | Component |
|---|----|---------|-----------|
| 1 | — | What is this, in one line? | `home/HomeHero.tsx` (headline, "Book a demo" / "Read the docs", animated cursor trace across three app windows) |
| 2 | `demo` | What does using it look like? | `sections/DemoStory.tsx` (click-to-play YouTube embed, unchanged) |
| 3 | — | Why do the alternatives fail? | `home/Problem.tsx` (light band: scripts / integrations / guessing agents) |
| 4 | `how-it-works` | How does it work? | `home/Steps.tsx` (Record / Compile / Publish / Run, each with a drawn mockup) |
| 5 | — | What happens when the screen changes? | `home/SelfRepair.tsx` (full-cyan band, before/after redesign + clue ladder) |
| 6 | `examples` | Does it work on my software? | `home/Processes.tsx` (process names with app chains) |
| 7 | `security` | Where does it run, can I trust it? | `home/LocalFirst.tsx` (machine / cloud diagram) |
| 8 | `pricing` | What does it cost? | `home/HomePricing.tsx` (rows; reads `STATIC_PLANS` / `TIER_CTA` exported from `sections/PricingTable.tsx`) |
| 9 | `faq` | Everything else pre-demo | `home/HomeFaq.tsx` (native `<details>`) |
| 10 | — | What now? | `home/HomeCta.tsx` |

`/pricing` still uses the original `PricingTable`, `Faq` and `FinalCta`. The homepage no longer uses the competitor matrix, the telemetry visuals or the product screenshots described below; the paragraphs are kept as history and for the copy constraints, which still apply. The "any agent" wording (MCP-compatible agents, including Claude) is intentional.

**Competitor matrix (`comparison`, added 2026-08-12 by request; removed from the homepage 2026-09-29).** `DESIGN.md` §6 lists dense
feature-matrix tables as a Don't — the legacy-RPA anti-reference. This section is a deliberate,
requested exception, kept narrow to avoid what that rule guards against: six capability columns, not
twenty, and every row carries a **"Beats us at:" concession** taken from `docs/PRD.md` §10. Keep both
properties. A matrix where one column wins every row reads as marketing and gets discounted wholesale;
the concessions are what make the rest of the table credible to a technical buyer, and they are also
what the sales material is built on ("a salesperson who can name a competitor's strength is trusted on
everything else they say"). Cells accept `Partly` / `n/a` / prose — do not force every cell into a tick
or a cross, because that overstates the claim. The PRD's separate rows for live-UI agents and browser
assistants are collapsed into one; that distinction matters in a sales conversation, not here. It
replaced the three "what breaks" cards that used to close `Reliability.tsx`, which made the same
argument in weaker form.

Two layout constraints on that table, both load-bearing: the scroll container must stay
`relative`, because the `sr-only` cells are `position: absolute` and would otherwise resolve their
containing block to the `relative` `<section>`, escape the scroller at their static position, and widen
the entire page on mobile; and the first column narrows below `sm` so a phone shows more than one data
column. A visible "scroll sideways" hint appears below `lg`.

**Telemetry visuals (`reliability`, removed from the homepage 2026-09-29):** this section reuses the **real dashboard viz components** rather
than drawing marketing approximations of them — `viz/ExecutionFlow.tsx` for a run whose certificate-upload
step self-heals at Tier A, and `viz/TierLadder.tsx` for where recovery finishes. It replaces three
hand-drawn static SVGs (`MaintenanceChart` / `LockMark` / `CostChart`) that used to live in `Outcomes.tsx`.
Two rules apply and must not regress: (1) the figures are **explicitly labelled illustrative** in visible
body text — there is no customer proof yet (`PRODUCT.md`; `docs/PRD.md` §8, "this is a diagram, not a
moat"), so they may never be captioned as measured results; (2) the dashboard components are used
**as-is**, wrapped for scale, never edited for marketing — the one change made was an additive
`showFootnote` prop on `TierLadder`, because its built-in footnote is `text-zinc-600` on Panel Black,
which is below the 4.5:1 body-text floor for this surface.

**Copy constraints (do not regress):** no SOC 2 / HIPAA / GDPR / ITAR / certification claims; recovery Tier B is described as *assisted*, never "fully autonomous" or "never breaks"; no invented metrics, customer logos, or testimonials; Windows-only runtime, macOS as roadmap. Allowed claims are the shipped ones — local execution, AES-256-GCM sessions in the OS keychain, credentials never in published skills, Ed25519-signed updates, admin-only publishing plus audit log, telemetry limited to event codes, unlimited free executions.

**Assets:** `DemoStory.tsx` embeds the product demo video (YouTube, click-to-play — the iframe is only injected on click, so it costs nothing at page load). Real product screenshots live in `public/marketing/screenshots/`, wired through `primitives/ShotFrame.tsx`: the four `shot_*.png` files are **cropped derivatives** of the originals — cropped both to stay legible at column width and, in the case of `shot_execution.png`, to remove the personal chat-history rail from the Claude Desktop capture. Keep the originals; re-crop rather than swapping in a raw full-window capture. `public/marketing/examples-stack.png` is an AI-generated illustration (Codex `image_gen`) whose background is exactly `#06080b`, so it composites seamlessly on the Void Black canvas — match that background on any replacement.

**Deliberately not used:** the dashboard capture (`dashboard.png`) shows a red "Attention needed" state, a 36% success rate, and single-digit workspace counts from a dev workspace. It is not on the page; the Reliability section renders the live viz components with illustrative data instead. Only put a dashboard screenshot on the homepage once its numbers represent real, healthy usage.

**Status:** Implemented with Framer Motion + Lenis. The Three.js `OrchestrationScene` was removed from the page (decorative only, no communication value); `3d/SplineScene.tsx` remains unused.

---

### 3.1.1 Public Docs (`app/(marketing)/docs/...`)

**Paths:** `/docs`, `/docs/[slug]`, `/docs/claude-automation`
**Purpose:** Public documentation and customer-facing policy pages for product behavior, security, privacy, terms, cookies, billing, acceptable use, data processing, and support.
**Inputs:** Static typed content from `src/content/publicDocs.ts`.
**Outputs:** Docs index, sidebar navigation, mobile docs navigation, page table of contents, related docs, drafting-reference links for policy pages, and crawler-facing `/robots.txt`, `/sitemap.xml`, and `/llms.txt` endpoints.
**User goal:** Understand how Conxa works, what data moves where, what policies govern use, and how to contact support before signing in.

**Status:** Public marketing route group; does not require Clerk auth. Includes a Claude automation docs page for LLM/search discoverability around Claude Desktop, MCP, local execution, and browser workflow automation.

---

### 3.1.2 Pricing Page (`app/(marketing)/pricing/page.tsx`)

**Path:** `/pricing`  
**Purpose:** Added 2026-08-08 for the capability-ladder repositioning (`docs/PRD.md` §11). Public,
unauthenticated. Renders `PricingTable.tsx`, which fetches `GET /api/v1/subscriptions/plans` client-side
(TanStack Query) and shows the four tiers as cards — Pro carries a "Distribution channel" ribbon,
matching the pricing sheet the restructuring was based on. Feature lists come straight from the API
response, itself derived from `PLAN_LIMITS`, so the page cannot show a number that isn't actually
enforced.

**One component, two hosts (2026-08-12).** `PricingTable` takes a `compact` flag. `/pricing` renders it
without: full cards plus the four explainer columns (compile credit, unlimited runs, model access by tier,
Human Edit credit). The homepage renders it with `compact`: the same four live tiers, but the explainer
columns are replaced by a "Compare every tier in full" link back here, so the homepage doesn't carry the
whole billing FAQ. **Do not fork the component** — the prices and feature strings are server-generated,
and a second copy drifts from the backend the day either changes. Both hosts share the TanStack query key
`['public-plans']`, so a visitor moving from `/` to `/pricing` costs no extra request.

**Two bugs fixed 2026-08-12** while verifying the homepage rebuild, both of which had shipped with the
original pricing page:

- `/pricing` was never added to `proxy.ts`'s `isPublic` route matcher, so Clerk middleware redirected
  every anonymous visitor to `/sign-in` — from the nav link, the footer, and the `sitemap.xml` entry the
  same commit added. The page was public in intent and in its static build output, but not in routing.
- `_plan_features` rendered Enterprise's numeric limits literally. Enterprise carries `0` in `PLAN_LIMITS`
  as a sentinel meaning "agreed per contract" (the real values live in billing metadata as contractual
  overrides), so the public card advertised "0 seats", "0 machines" and "0K Human Edit tokens". `0` now
  renders as contract language. Anything that formats a plan limit for display must handle this sentinel.

Also 2026-08-12: a feature the tier *withholds* ("No ops dashboard", "No analytics retention") now renders
with a muted dash instead of a cyan tick — a tick beside a negative statement read as "included".

**Status:** Implemented and linked from the marketing nav and the homepage. Loading/error states covered
(skeleton cards, inline error banner). Not yet built: a dedicated Enterprise "contact us" form (routes to
`/docs/support` instead) and inline plan-comparison tooltips explaining each capability row.

---

### 3.2 Operations Dashboard (`app/(protected)/dashboard/`)

**Purpose:** The enterprise AI-operations control center — the landing surface after login.
**Inputs:** Clerk auth context; `?range=` in the URL (`24h` / `7d` / `30d` / `90d`).
**User goal:** Answer, in order — is the platform healthy, what needs attention, how much is running and what is failing, which workflows are slipping, is self-healing keeping up, and what is this worth.

**Structure (2026-08 redesign).** One sidebar entry, five routes behind a sub-navigation tab row. The range picker and refresh live in the shared shell (`src/dashboard/DashboardShell.tsx`) and the selected range is held in the URL so a link shows the recipient the same window.

| Route | Answers |
|---|---|
| `/dashboard` | One-sentence summary + health dial and status pills, KPI strip, reach line, [health factors \| insights], [execution trend \| what is failing most], self-healing outcome bar |
| `/dashboard/workflows` | Status-mix summary, per-skill list with last-period → this-period success dot-plot, share-of-runs bar, failure reasons in plain words |
| `/dashboard/workflows/[company]/[slug]` | Verdict sentence + "Fix in Studio", stat strip, trend with "vX first ran" marker, version comparison, step list, recent runs, recovery outcome bar |
| `/dashboard/healing` | Summary sentence + 4 stats, repair-method ladder (Tier A → Tier B), flaky-hours heatmap, drift queue, check pass rates vs last period |
| `/dashboard/impact` | Hours/value sentence, one-line method (recorded Build Studio time × unattended runs × hourly rate) with a "Change hourly rate" control, Estimate/Measured stats, hours by workflow, measured reliability cost |
| `/dashboard/runs/[company]/[runId]` | "Run X stopped at step N" + plain-language cause, step timeline with the recovery path each repaired/failed step took, raw events collapsed |

**Plain-language redesign (2026-09-29).** Every section keeps its question and adds a large *answer* sentence under it, computed by fixed rules in `src/dashboard/narrative.ts` (tested in `frontend/test/narrative.test.mjs`) — the reader gets the conclusion before the chart. Sections are unboxed (question, answer, visual, separated by space and hairlines), laid out on one two-column grid (`minmax(0,1fr) 25rem`). Colour is reserved for what needs attention: healthy data is neutral ink (`INK` in `chartTheme.ts`), slipping is amber, failing is red, and cyan marks AI (Tier B) or the one signal per screen. Runtime failure codes are shown in plain words (`failureLabel`) with the raw code as a subline. The Overview no longer carries the live-activity feed or the ROI summary (Impact covers value); the Sankey and fleet-topology charts were replaced by proportional `SplitBar`s, and per-skill success uses the `RateShift` dot-plot. Workflow status thresholds: failing below 80% success, slipping when down 3+ points on the previous period.

**Prior state.** The dashboard was a single 631-line page (`src/DashboardPage.tsx`, now removed) with six metric tiles, a hand-drawn bar chart, and four ranked lists. It answered "what broke" but not whether the platform was healthy, which workflows were degrading, or what any of it was worth. Its sections were re-homed: risk queue → Overview, recovery/assertion/drift panels → Self-healing, trend → the shared `TrendChart`.

**Design decisions worth keeping.**
- **KPIs are one divided strip, not a tile grid.** Five boxed hero-metric cards is the marketing reflex; a strip with hairline dividers reads as one instrument panel. Each cell carries value, sparkline, and a delta against the prior equal period followed by its meaning in words ("fewer failures"), with per-KPI `direction` so a rising failure count is never painted green.
- **Health score is decomposed on screen.** The arc is the headline; the five weighted factors (success rate, assertion pass rate, drift resistance, zero-token healing, runtime freshness) are listed beside it with their own values and contributions. A score nobody can decompose is a score nobody acts on. No telemetry yields `score: null` / grade "No telemetry" — never a red zero for a workspace that has done nothing wrong.
- **Section headings are questions.** "Where does recovery spend its budget?" rather than "Recovery" — each panel states the job it does.
- **Estimates are visibly separated from measurements.** Hours saved uses each workflow's recorded time in Build Studio (seeded into the ROI baseline on first publish) as the manual-time baseline — nobody types minutes per run (2026-09-29: the minutes field was removed; workflows with no recording fall back to the workspace default and say so). The only input a person supplies is the hourly rate, editable inline. Estimates are tagged "Estimate"; counts derived purely from telemetry are tagged "Measured".
- **Insights are rule-derived, never model-generated.** Each carries the metric behind it and links to the evidence.
- **Chart palette is separate from the status palette** (`--chart-1..4` plus an "Other" slot and `--tier-1..4` in `src/index.css`; the heatmap now uses neutral ink for volume and red for flaky hours). A series painted emerald would read as "healthy" when it only means "workflow 4". Four categorical hues, not six — six do not survive the all-pairs colour-vision check on this surface.

**Visualizations** live in `src/components/viz/`: `Sparkline`, `TrendChart` (stacked outcome bars, optional version marker), `HealthArc`, `SplitBar` (one whole split into parts), `RateShift` (last period → this period dot-plot), `TierLadder` (repair methods grouped by tier), `Heatmap` (hour-of-week, red = more than 5% of runs failed), `ExecutionFlow` (step timeline with recovery path). d3 supplies scale/arc maths only; all markup is local so theming stays in the design system.

**Assertion health (2026-07, post-condition validation):** sourced from `assertion_health_by_step` (`app.services.tracking._assertion_health_by_step`, aggregating the runtime's `verify_result` event). Steps worst-pass-rate-first with this period's pass-rate bar (neutral ≥95%, amber ≥80%, red below) above last period's (`previous_pass_rate`, grey), check count, and advisory-failure count — the fleet-wide early warning for an assertion decaying before it becomes a hard step failure. Now lives on `/dashboard/healing`.

---

### 3.3 Skill Packages Page (`app/(protected)/packages/page.tsx`)

**Purpose:** List the workspace's groups (folders of published skills), not the workspace itself as one card.
**Inputs:** Clerk auth; `GET /workflows/skill-packs` then `GET .../groups` per company slug.
**Outputs:** Group cards (name, workflow count, ready count). Click opens `/packages/{slug}/groups/{groupId}`. Header **Installer** opens `/packages/{slug}` (company-level installer downloads). If the workspace has more than one company slug, each group card shows the company name as a subtitle and each slug gets its own Installer control.
**User goal:** Open a group and review its workflows / release status.

**Note:** A skill package is still company-scoped (installer + sync live at the slug). Groups are the listing unit because one workspace holds many groups. Empty folders appear as soon as they are created in Build Studio.

---

### 3.4 Skill Package Versions Page (`app/(protected)/packages/[slug]/page.tsx`)

**Purpose:** Version history and installer release management for one skill package.
**Inputs:** Company slug (not workspace ID — derived from the authenticated workspace context).
**Outputs:** Skill package versions (release notes, publish timestamp, skill list, file count), installer versions (if any) with download buttons, version-specific metadata, and a Groups grid. Groups appear as soon as they are created in Build Studio (empty folders with zero workflows); publishing a workflow fills the matching folder. Click a group to see its workflows, click a workflow to reach its release/deployment page.
**User goal:** Audit release history, download previous versions, compare versions, and open a group's workflows.

**UX issues:**
- No filter by status (ok/fail).
- No drill-down from run summary to individual step events.
- No time range filter.

**Archive / Restore (shipped 2026-09-24, per-workflow page header):** an Archive button (Restore once archived) next to Back, backed by `POST .../skills/archive` / `.../unarchive`. Archiving is Cloud's "delete a workflow" action — it pulls the workflow out of every installed customer's next sync (`list_skills` stops showing it) while leaving its files on their machines and its version history/release intact, so Restore brings it straight back. While archived, the Release and Rollback buttons are hidden on this page, and the workflow's card on the Group page (§ groups grid) sorts to the end, dims, and shows an "Archived" badge instead of "Ready for Release". Archived workflows are also left out of the folder preview and workflow counts on the Skill Packages page (§3.3).

---

### 3.5 Compile Page (Cloud) — removed

**Purpose (historical):** Trigger re-compilation of a published workflow. This route (`app/(protected)/plugins/[id]/workflows/[workflowId]/compile/page.tsx`) never had working functionality behind it — cloud compilation is **not implemented** (cloud has no compiler, see CLAUDE.md's "cloud does not compile or execute" invariant) — and the placeholder file itself no longer exists on disk after the 2026-08-12 `/plugins` → `/packages` rename. Recompiling a workflow remains a Build Studio–only action (§2.8).

---

### 3.6 Billing Page (`app/(protected)/billing/page.tsx`)

**Purpose:** Subscription management via Cashfree.  
**Inputs:** Plan selection.  
**Outputs:** Checkout readiness, plan tier, and workspace usage meters.
**User goal:** Upgrade or manage subscription.

**Meter behavior:** Shows all four customer meters first: seats, machines, compile credits, and Human Edit pool (`installer slots` renamed/removed 2026-08-08 — see `docs/PRD.md` §11). Account timing and checkout state live in the Billing Operations panel rather than top summary cards. The panel shows active plan and Usage reset only; Usage reset uses the Cashfree monthly payment/renewal timestamp, and the separate Billing period end row is not shown.

**UX issues:**
- No invoice history.
- **Compile Credit Add-Ons panel (shipped 2026-08-22, four-SKU ladder same day):** a card between the usage meters and the plan grid lists every add-on from `GET /subscriptions/addons` — ₹3,999/+20 compiles+200k tokens, ₹9,999/+50+500k, ₹19,999/+100+1M, ₹49,999/+250+2.5M (Human Edit tokens ride along at ~10k per compile). Each row shows its active-pack badge (`addons` on `GET /entitlements/current`), a Buy button running the standard Cashfree checkout flow (`POST /subscriptions/create` with that tier), and a Cancel button (`POST /subscriptions/addon/cancel` with `{tier}`) that cancels the mandate at Cashfree; the webhook then decrements the pack count.
- **Free-trial banner (shipped 2026-08-22):** a global banner under the dashboard topbar (not page-local) consumes `trial_ends_at`/`trial_expired` from `GET /entitlements/current`. Amber countdown with days remaining while the free trial runs, red expiry notice once `trial_expired` is set; both deep-link to `/billing`. Hidden for paid plans.

---

### 3.7 Team Page (`app/(protected)/team/page.tsx`)

**Purpose:** Manage workspace members, roles, and seats.
**Outputs:** Workspace/team summary, seat usage, current role, last team activity, role guide, billing/audit links, organization member controls, and — below the member directory — a Conxa Execute Seats panel (shipped 2026-09-16, CLOUD-22).
**Meter behavior:** Shows seat usage before member controls. Hard enforcement still requires Conxa-owned invites or Clerk webhook cleanup; raw `OrganizationProfile` alone is metered/audited, not a complete hard gate.
**Status:** Company-facing team UI is implemented. Member operations remain handled by Clerk organization controls.

**Conxa Execute Seats panel (shipped 2026-09-16; claim flow simplified 2026-09-17):** an
admin-facing table backed by `GET/POST /api/v1/entitlements/execute-grants` and `POST .../revoke`
(`docs/TRD.md` §13.7) for granting Conxa Execute access to people independent of Build Studio
membership, billed against the workspace's shared AI Usage Credits pool. An email form creates a
`pending` grant; the table shows every grant's status (pending/claimed/revoked) with a
confirm-guarded Revoke on any non-revoked row. Seat usage is read from the already-fetched
`meters.execute_seats`. Since Conxa Execute moved onto the same Clerk application as the dashboard
(2026-09-17, `docs/TRD.md` §3.6), a grant needs no invite link or code at all — it auto-claims the
next time that email signs into Conxa Execute. The old "Copy link" action and the public
`app/claim/[grantId]/page.tsx` landing page were removed as part of that change.

---

### 3.8 Settings Page (`app/(protected)/settings/page.tsx`)

**Purpose:** Compact workspace settings and administration hub.
**Outputs:** Workspace identity, current user role, auth/session verification status, signed-in user context, and shortcuts to Team, Billing, and Audit.
**User goal:** Confirm they are in the right workspace and quickly reach the admin areas that change company state.
**Status:** Implemented as a read-oriented settings page backed by `/me`; real mutations remain in Team, Billing, and Audit instead of being implied by inactive settings controls.

**Bring Your Own LLM Key panel (shipped 2026-08-22):** a card at the bottom of the page surfaces the Enterprise BYOK configuration (`PUT/GET/DELETE /api/v1/workspace/llm-key`, `docs/TRD.md` §13.9). Owner/admin only — members see a note instead of the controls. Shows provider/endpoint/deployment/API-version when configured (the key itself is never returned), with a form to set or replace the Azure OpenAI key and a confirm-guarded remove that falls compiles back to the shared managed pool.

---

### 3.8a Report a Bug Page (`app/(protected)/report-bug/page.tsx`, shipped 2026-09-24)

**Purpose:** Let any workspace member send a bug report — description, the page it happened on, a contact email, and file attachments (screenshots, a screen recording, logs) — straight to the team's inbox, without leaving the dashboard.
**Inputs:** A free-text description (required), an optional page URL, a contact email (prefilled from `/me`, editable), and up to 5 files (client-enforced 25MB combined size cap; the picker accepts images, video, `.log/.txt/.har/.json/.pdf/.zip`).
**Outputs:** `POST /api/v1/bug-reports` (`docs/Backend-Schema.md` §5.9b) sent directly to the backend (bypassing the dashboard's own `/api/v1/*` proxy, whose request-size limit is far too small for a screen recording). A success toast clears the form; a failed or oversized submission shows the error inline via the existing toast/error styling.
**User goal:** Report a problem with enough detail and evidence (a recording beats a description) without opening email or a separate tool.
**Status:** Implemented. Sidebar entry sits in the **Manage** group, directly below Settings. No stored ticket or inbox in the dashboard — this is a stateless mailer; see TODO.md for a possible future in-app report list.

---

### 3.9 Audit Page (`app/(protected)/audit/page.tsx`)

**Purpose:** Dedicated enterprise audit trail for workspace activity.
**Inputs:** Clerk auth context and `GET /api/v1/audit-events`.
**Outputs:** Summary counters, actor/resource coverage, latest event status, searchable and action-filtered audit table, metadata preview, and CSV export of the filtered result set.
**User goal:** Review who performed operational actions, when they happened, and which workspace resources were affected.
**Status:** Implemented as a protected route with a sidebar entry directly below Skill Packages.

---

### 3.10 Sign-In / Sign-Up

**Paths:** `app/sign-in/[[...sign-in]]/page.tsx`, `app/sign-up/[[...sign-up]]/page.tsx`  
**Purpose:** Clerk-hosted auth UI embedded in Next.js.  
**Status:** Standard Clerk Next.js integration.

---

## 4. Navigation Structure

### Build Studio

One button per stage of the Workflows -> Human Edit -> Publish Skill Package ->
Build Installer flow (2026-08 Workflow Groups redesign), replacing the earlier
Record-centric sidebar (Record/Dashboard/Plugins/etc). The Workflows page (a
grid of Workflow Groups) is the primary landing page and entry point for all
recording/editing. **Compile has no sidebar entry or standalone page** — it
lives only on the `WorkflowStageRail` on each workflow's row on its group's
page. **There is no per-workflow detail page** (removed 2026-08-13) — every
action a workflow needs lives on its group's page. **There is no Test Skill
sidebar entry or standalone page either** (removed 2026-09-27) — testing is
a workflow row's **Test** toggle on its group page (§2.10), reached
immediately after Human Edit's Approve with no intermediate stop.

```
AppChrome (layout)
├── Sidebar
│   ├── Workflows (WorkflowListPage.tsx — app home and default route: browse/
│   │   create groups; §2.3)
│   │   └── [Group ID] (GroupPage.tsx — group's apps + auth, and every workflow's
│   │       full lifecycle rail (record/compile/review/test/ready-to-package)
│   │       inline per row — compile log (CompilePanel) and test panel open
│   │       under the row; create/search/delete workflows here; §2.3a)
│   ├── Human Edit (HumanEditListPage.tsx — compiled workflows, needs-review-first,
│   │   → /edit/[skillId] HumanEditPage.tsx, the per-skill editor — see §2.7 — opens
│   │       a mandatory ReviewQuestionsDialog first if the skill has pending loop/
│   │       optional-popup questions; Approve returns to the workflow's group page)
│   ├── Publish Skill Package (PublishPage.tsx — primary release action: workspace-scoped)
│   ├── Build Installer (BuildInstallerPage.tsx — secondary/optional, requires a published release)
│   └── Settings
└── WindowTitleBar (custom Electron title bar)
```

`/`, `/record`, `/plugins`, `/build`, and unknown routes all redirect to `/workflows`:
the Record page was removed (2026-08); all its controls are inline on the Group Page's
workflow rows. `/workflows/:workflowId` (the old per-workflow detail route) now redirects
to the workflow's owning group — kept only so existing deep links, the compile page's
"← Back", and Human Edit's `?from=` still resolve. The Workflow List page is the home for
group management. Build Installer is secondary and only offered after a skill package is
published.

### Cloud Dashboard

```
(marketing)/
├── / (landing page)
├── /docs
│   └── /docs/[slug]

(protected)/  [requires Clerk auth]
├── /dashboard
├── /packages
│   └── /packages/[slug]
├── /audit
├── /billing
├── /team
└── /settings

/sign-in
/sign-up
/onboarding
```

---

## 5. User Journeys

### Journey 1: First-time company engineer (Build Studio)

1. Downloads Build Studio. Runs setup wizard.
2. Signs in with Clerk (browser pop-up).
3. Creates first workflow (name + URL).
4. Records auth session (1–3 min).
5. Records first workflow (5–15 min).
6. Compiles (2–5 min — waits on LLM).
7. Reviews in HumanEdit — signs off.
8. Builds installer.
9. Distributes to first test customer.

**Time to value:** ~30 minutes from download to working installer.

**Current friction points:**
- Bootstrap progress is opaque (unclear if 2 or 20 minutes).
- Compile step has no time estimate.
- HumanEdit is the longest/hardest step — no guided review.

### Journey 2: Returning company engineer (update a workflow)

1. Opens Build Studio.
2. Navigates to existing workflow.
3. Re-records a workflow (auth is still valid).
4. Compiles new version.
5. Signs off.
6. Builds new installer version.
7. Publishes. Customers auto-update on next runtime start.

**Time to value:** ~15 minutes for a content update.

**Current friction:** No way to compare old vs. new compiled steps. Must rebuild installer even for content-only updates.

### Journey 3: Customer runs a skill

1. Installs the .exe.
2. Restarts Claude Desktop.
3. Asks Claude to run the skill.
4. Watches browser execute (headed mode default).
5. Claude confirms completion.

**Time to value:** Immediate after install.

**Current friction:** If the runtime token is not set (first-time), the skill sync silently skips. User may not know their skills need a token.

---

## 6. UX Bottlenecks & Friction Points

| Area | Friction | Impact |
|---|---|---|
| Compile step | No time estimate; LLM progress hidden | Users cancel thinking it's stuck |
| HumanEdit | No guided review checklist | Steps signed off incorrectly |
| Parameterization | Not discoverable | Workflows hardcoded; break on different users |
| ~~Runtime token~~ | ~~No in-app acquisition flow~~ | **Resolved** — installer-embedded sync token (`docs/Auth-and-Updater.md` §1.3, `docs/TRD.md` §5.4) needs no acquisition step at all |
| Error codes | Raw codes shown to user | Confusing (e.g. `cloud_unreachable` for quota exceeded) |
| Recording | No live screenshot preview | Can't confirm recorder is capturing correctly |
| Build → installer | Two steps not explained | Users confused about why two actions needed |
| Test workflow | Requires local runtime installed | No feedback when runtime missing |
| Compile quota | Quota exceeded shows as unreachable | Misleading error |
| ~~Workflow status~~ | ~~No pipeline visualization~~ | **Resolved 2026-07** — `handlers/status.py::derive_workflow_stage` + `StagePath`/`WorkflowStageBadge` replaced the three separate, inconsistent status fields with one derived stage rendered consistently across the workflow page and every stage page |

---

## 7. Missing Experiences

### 7.1 Runtime Token Acquisition — Resolved

This section previously described a critical gap: no in-app flow for the end customer to acquire a runtime auth token, with skill sync silently failing on new installations. That design (a Clerk-token/`setup_company` challenge-URL flow via `getAuthChallengeUrl()`) was superseded before shipping by the installer-embedded sync-token model — the token is minted at publish time, written into `pack.json`, and used directly by the runtime with zero user interaction. See `docs/Auth-and-Updater.md` §1.3 and `docs/TRD.md` §5.4 for the current model. There is no first-sync gap to close here.

### 7.2 Execution Visibility in Build Studio

The Build Studio has no view of execution history from deployed customers. Engineers must open the Cloud Dashboard separately to see telemetry. There's no "how are my customers doing?" view within the tool where the engineer lives.

### 7.3 Drift Alerts — Resolved

This section previously described the `structural_fingerprint` comparison as designed but not implemented in the runtime. It's now implemented: `runtime/drift.js` runs an advisory pre-execution drift check (see `docs/TRD.md` §9.7) and emits a `drift_detected` telemetry event that surfaces on the vendor dashboard's `/drift` review queue (Implementation-Plan §2.2) when it fires. This never blocks execution — it's a signal, not a gate.

### 7.4 Workflow Diff / Version History

No way to see what changed between two compiled versions of a workflow. Engineers cannot review the diff before shipping an update.

### 7.5 Onboarding Progress State

The Setup Wizard has no persistent state. If the app is closed mid-bootstrap, it restarts from scratch. Should save progress and resume.

### 7.6 Installer Version History

**Partially resolved 2026-07:** Skill Pack Publishing (§2.11) now shows a full release history (version, release notes, publish timestamp, `is_latest`) — that's the primary changelog surface now, per the redesign making skill packs the version-controlled artifact. Pure installer-build history (the backend has always tracked `installer_versions__{slug}` and served it at `GET /api/v1/workflows/{slug}/installer/versions`) is still not surfaced as a UI list on the Build Installer page itself — see Recommended Improvement #10.

### 7.7 Multi-SkillPack Build

A workspace can publish under many distinct company slugs (e.g. an agency running several clients), each with its own skill pack — there is no batch build/publish flow across them. Each company's skill pack must still be built and published individually.

### 7.8 Test Results Persistence

Test results (`last_test_status`, `last_test_error`) are saved to the workflow model but not surfaced prominently in the workflow list or dashboard.

### 7.9 Claude Dashboard Integration

No way to see runtime status from within Claude Desktop (other than calling `get_runtime_status`). A user-visible status summary would help diagnose issues.

---

## 8. Enterprise UX Considerations

### 8.1 Team Publishing (Missing)

Multi-engineer teams cannot currently:
- Share draft workflows before sign-off.
- Assign review tasks.
- See who compiled or built what.

### 8.2 Audit Trail

The Cloud Dashboard now has a dedicated `/audit` page with search, action filtering, summary counters, metadata preview, and CSV export over `GET /api/v1/audit-events`. Remaining enterprise hardening work is deeper event taxonomy, actor display names, and backend-level export pagination beyond the current loaded result set.

**Release events (2026-08-19):** every release-lifecycle action (publish start/succeed/fail, channel move, rollback start/complete) is mirrored into this same global audit log, so the `/audit` page needed no changes. But that log is a 500-entry ring buffer shared across every workspace — a busy tenant can evict another tenant's history. The Release Center's own Audit section (§2.11) reads from a separate, unbounded, per-slug source (`GET .../releases/events`) instead, which is the one to trust for a specific skill pack's full release history.

### 8.3 Offline Mode

The Build Studio can record and partially edit offline (events are local). Compilation requires the LLM proxy (cloud). There is no offline-first messaging to tell users what they can and cannot do offline.

### 8.4 Proxy / Corporate Network

Bootstrap surfaces download URLs for IT whitelisting, which is good. However, the Clerk auth flow opens a system browser and uses a fixed port range (52741–52750). Corporate proxy environments may block this. There's no proxy configuration UI.

### 8.5 Role-Based Access in Dashboard — Partially Resolved

`require_admin` is now enforced on publish, workflow create/delete, and bundle-release routes (`app/services/rbac.py`; Implementation-Plan §1.6). Fine-grained per-skill ACLs and a read-only analyst role for enterprise customers are still open — Phase 3, tracked in `TODO.md`.

---

## 9. Recommended Improvements

### Priority 1: Fix critical UX gaps (blockers for reliable use)

1. ~~**Translate error codes to human messages.**~~ **Resolved** (Implementation-Plan §2.8) — `errorMessages.ts` now maps `_CommandError` codes to user-friendly strings in the renderer.

2. ~~**Runtime token acquisition flow.**~~ **Resolved** — no acquisition flow is needed; see §7.1 above.

3. **Compile time estimate.** Before compilation starts, show an estimate based on event count: `~N steps → approximately M minutes`. Update the estimate as steps complete.

4. **Recording screenshots live preview.** During recording, show the most recently captured screenshot in a sidebar panel so engineers can confirm the recorder is working.

### Priority 2: Improve workflow quality (reduce errors in production)

5. **Guided HumanEdit checklist.** Add a checklist panel in HumanEdit: "Have you verified the selector for each step? Have you parameterized all user-specific values? Have you signed off?" Require checklist completion before sign-off.

6. **Parameterization auto-suggest.** During HumanEdit, analyze step values for user-specific patterns (email addresses, names, dates) and suggest parameterization. `{{email}}`, `{{date}}` pre-populated.

7. **Workflow pipeline visualization.** Replace the status text labels with a clear pipeline diagram: `Recorded → Compiled → Reviewed → Signed Off → Built → Deployed`.

8. **Test result persistence and surface.** Show `last_test_status` prominently per workflow. Add a "Test required before publish" gate.

### Priority 3: Operational improvements (efficiency for returning users)

9. **Bulk compile.** Allow selecting multiple recorded workflows and compiling them all in sequence with a progress bar for each.

10. **Installer version history.** Store installer builds with version, date, sha256 and show them in a list on the Build Installer page. (The backend has carried `GET /api/v1/workflows/{slug}/installer/versions` — now also `.../{installer_version}/{slug}/installer/versions` — since before this note was written; still not surfaced as a UI list on Build Installer. Still open.)

11. ~~**Publish without installer rebuild.**~~ **Resolved 2026-07.** Publish Skill Package (§2.11) publishes the updated skill pack directly via `cmd_publish_skill_pack`; Build Installer is now a fully separate, optional, secondary action gated on a release already existing. The runtime's delta sync handles delivery with zero installer rebuild.

12. **Execution dashboard widget in Build Studio.** Embed a mini execution dashboard in the Build Studio showing the last 10 runs across every workflow in the workspace, fetched from the Cloud API.

---

## 10. Runner Tray (PROD-5, 2026-08-26)

A native Windows system-tray icon for the standalone scheduler daemon
(`runtime/app/tray_windows.ps1` — PowerShell `NotifyIcon`, no Electron, no npm dependency).
It is deliberately **view-only**: every click writes a command file the daemon consumes, so
there is exactly one writer of scheduler state.

**Right-click menu** (rebuilt each time it opens):

| Item | Behavior |
|---|---|
| Status line (disabled) | "Conxa Runner: running" / "PAUSED" |
| Run now: `<schedule name> (<next run>)` | One per enabled schedule, max 8; fires an ad-hoc run that never disturbs slot bookkeeping |
| Pause / Resume scheduling | Global pause — in-flight runs finish, nothing new starts |
| Open logs folder | `explorer` on `<CONXA_DATA_DIR>/scheduler/logs` |
| Open schedules folder | `explorer` on the schedules directory |
| Quit Conxa Runner | Graceful shutdown via command file |

Double-click opens the logs folder. The tray exits itself when the daemon lock disappears or
goes stale, so it can never outlive its owner. Icon: a blue disc with a white "C", drawn in
code — no .ico asset to ship.

Known gaps (tracked in TODO.md): no per-schedule enable/disable from the menu yet; the status
line does not show the next fire time (state.json carries it; menu text kept short); macOS has
no tray.

---

## 11. Conxa Execute (redesign, 2026-10-01)

The Execute desktop app (`conxa-execute/app/renderer/src/`) is built from the three boards in `docs/artifacts/conxa-execute-redesign/` (Main, Home, Attach). The palette is the app's warm charcoal with the `#d97757` accent. The typeface is Geist, with Geist Mono for shortcuts and durations. The light theme is kept.

| Screen | What it shows |
|---|---|
| Title bar | Sidebar toggle, back/forward, the current chat's name (menu: Rename, Delete), the **Browser** show/hide toggle (disabled until a run opens the panel), window controls |
| Sidebar | Conxa mark, **New chat** (Ctrl N), **Search / Skills / Runs**, Recents (a green dot while a chat's turn is running), account footer (name + active workspace, opens Settings) |
| Home (empty chat) | "Good {morning/afternoon/evening}, {first name}", the composer, chips for three skills (prefill "Run …") and **Browse all skills** |
| Thread | User bubbles (attached images as tiles), assistant prose, one **run card** per skill run, confirm card in "Ask me first" mode, **Run again** under the latest reply |
| Run card | Skill name, status (done / didn't finish / waiting for sign-in / busy / cancelled), time taken, downloaded files, an amber strip for runtime warnings with **Open page**. There is no per-step list, because the run result carries none. Status and duration come from the run history, so a run parked on sign-in updates when it ends |
| Composer | Attachment tiles, the **+** menu (Add files or photos Ctrl U, Take a screenshot, Capture the browser panel, "Before a skill runs": Ask me first / Auto-approve), Chat/Form switch (Form opens Skills), permission chip, workspace switcher, Send |
| Drag and drop | Dropping files on the chat column shows a dashed overlay. Images and text files up to 10 MB each; no PDFs |
| Search | Finds chats by name or by anything the user or Conxa said in them |
| Skills | A card per installed skill with **Run with form** (the fill-in form) and **Ask in chat** |
| Runs | The latest 50 runs from chat and forms: status filter, time, duration, source. A row expands to show the result text, **Open chat** and **Remove from list** |

Not built on purpose: the Attach board's editable "Run N times" review table (the AI still proposes batches in plain text), PDF reading, and a per-step run timeline.

## 12. Build Studio shell (conversation redesign, Phase 1, 2026-10-18)

The page-per-feature layout is replaced by a three-column shell (design source: `docs/artifacts/build-studio-redesign/`, plan: [`Build-Studio-Conversation-Plan.md`](Build-Studio-Conversation-Plan.md)). The conversation, recording, Clarify, test and system-state cards are described in §12–14 below.

- **Navigation (264 px, 220-320, folds to a 56 px rail):** Chat, New workflow (Ctrl N), Publish with a neutral count badge, Search (Ctrl K), Chats, then Groups. Every workflow appears in its group from the moment it is created, with a status glyph and a short note for unfinished ones. Footer: Settings, workspace, compile credits and AI credits used.
- **Inspector (320 px, 280-400, folds to a 44 px strip; workflow screens only):** lifecycle rows (Sign in, Record, Compile, Review, Test, Approve), details, and Human Edit. Clarify and Edit rows join when their backend exists.
- **Folding order:** inspector to strip, then navigation to rail, then the inspector becomes a sheet over the workspace; the workspace never drops below 520 px (`lib/panels.ts`). Dividers are focusable (arrow keys move 16 px) and double-click resets. Widths and open state are remembered per computer.
- **Keyboard:** Ctrl K search, Ctrl N new workflow, Ctrl \ fold navigation, Ctrl . fold inspector, Ctrl E Human Edit for the open workflow. Esc closes dialogs and never stops a run.
- **Status vocabulary:** `●` ready, `▲` needs you, `✕` failed, `◐` in progress, `○` not started, always shape plus colour (`lib/status.ts`). Until the lifecycle derivation lands (BUILD-37) the glyph comes from the existing workflow stage.
- **Interim pieces:** `/w/:workflowId` is a placeholder that links to the group page and Human Edit; `/chat` is a placeholder; New workflow is a small dialog. Known UX gap: the Publish badge counts signed-off workflows, not yet "approved and unpublished".

### 12.1 Test browser panel (BUILD-42)

When a test runs with **Show browser** on, the inspector folds to a strip and a browser column opens beside the conversation: a tab strip, back / forward / reload, an address field, a **live** / **finished** marker, and a close button. The page stays on the final screen after the run so the person can check the result; closing the panel (or starting another test) frees it. Hiding the panel folds it to a strip and does not stop the run. While a dialog or sheet is open the page is hidden (a native view cannot be drawn over). Recording and sign-in still open a separate Chromium window.

## 13. Human Edit inside the conversation shell (conversation redesign, Phase 6, 2026-10-18)

Human Edit keeps its job and its panes; it adopts the shell and the conversation.

- **Header:** "Back to conversation" (a labelled button, not an icon) when Human Edit was opened from a workflow's conversation (`?from=/w/<id>`); the primary button there is **Test changes** — it saves the open step, posts the test card into the thread and goes back. There is no Approve here: approval follows a passing test, in the conversation. Reached from the old group page instead, the legacy **Approve** remains until that flow is retired (BUILD-45).
- **Wizard:** Element → **How it's found** → **Validation** (was Element → Selectors → Check). The raw selector editors live under the Advanced disclosure; "Selectors" no longer names a step of the primary flow.
- **Step rows:** the step's name leads (the number stays as a small secondary badge) and each carries a type chip in the action's colour — pink for click/select, blue for type, purple for navigate, grey for scroll/hover; clay is never a step-type colour.
- **Conversation pane (replaces the floating copilot):** the workflow's own thread, 340 px (300–440, drag or arrow keys, double-click resets), hideable to a 44 px strip, scoped to the selected step (the composer says which). A proposed change is a card with the exact change and Apply / Undo, through the same gates as before; applying or undoing one reloads the editor. The old copilot launcher, panel and verify card were removed; their history is imported into the thread once.


## 14. System states, Settings and first launch (conversation redesign, Phase 8, 2026-10-18)

Every state ends with a way forward. Built before this phase: loading skeletons (never a spinner over a blank page), recording discarded, browser closed mid-recording, compile failed, out of credits, waiting for another compile, test stopped by you, and creating a group inline in the sidebar. Added in this phase:

- **Offline.** A slim banner under the header: “You’re offline — compiling, chat and publishing wait until Conxa Cloud is back”, with **Try again** and **What still works**. The sidebar footer shows `✕ Offline`; Publish says “waiting to connect”; the composer is disabled with its reason; compile cards' **Try again** / **Compile now** are disabled. Recording, Human Edit, local tests and Approve keep working.
- **Engine stopped.** The workspace is replaced by “Build Studio’s engine stopped” (workflows are saved on this computer) with **Restart engine** and **Copy diagnostics**; navigation stays visible but dimmed and inert, the inspector is hidden. During the automatic restart the same panel reads “Restarting…”.
- **Sign-in expired during a test.** The test card pauses (▲ “A sign-in expired during the test”): nothing failed in the workflow and no automatic fix is tried; the person signs in again from the group page and tests again. (The mockup resumes by itself after sign-in; Studio does not — see TRD §7.4b.)
- **Leaving Human Edit with an unsaved change.** A small confirm: “Save the change to step N?” — **Save and leave**, **Discard**, **Keep editing** (Esc).
- **Settings** is a 800×560 popup over the current screen (left list: Account, Usage, Testing, About). Usage shows the meters with the single **AI credits** name; Testing holds the auto-fix switch. The old full page is gone; `/settings` opens the popup.
- **First launch** (`/welcome`, shown when the workspace has no groups): headline, the three steps (Record it once / Answer a few questions / Test, then approve), **New workflow** and **Plan one in chat first**, and a “This computer” list — signed in, recording browser (with **Install now** when missing), local test runner. A failing check explains itself and never blocks the page.
