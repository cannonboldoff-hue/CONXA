# App Flow Document

**Status:** Current as of 2026-08-08  
**Scope:** All major user flows across Build Studio, Conxa Cloud, and Runtime

---

## Table of Contents

1. [User Onboarding](#1-user-onboarding)
2. [Build Studio Login](#2-build-studio-login)
3. [Create a Workflow](#3-create-a-workflow)
3a. [Conversation-led workflow (Build Studio, 2026-10)](#3a-conversation-led-workflow-build-studio-2026-10)
4. [Group Page — Workflow Lifecycle Rail (Record, Compile, Review, Test)](#4-group-page--workflow-lifecycle-rail)
5. [Pipeline & Compilation](#5-pipeline--compilation)
6. [Workflow Editing (HumanEdit)](#6-workflow-editing-humanedit)
7. [Build Skill Package](#7-build-skill-package)
8. [Publish & Build Installer](#8-publish--build-installer)
10. [End-User Installation](#10-end-user-installation)
11. [Runtime Registration & First Sync](#11-runtime-registration--first-sync)
12. [MCP Skill Execution](#12-mcp-skill-execution)
13. [Execution with Recovery](#13-execution-with-recovery)
13a. [Scheduled Execution (Standalone Runner)](#12a-scheduled-execution-standalone-runner-prod-5)
14. [Skill Pack Update (Company Side)](#14-skill-pack-update-company-side)
15. [Skill Sync (Runtime Side)](#15-skill-sync-runtime-side)
16. [Runtime Self-Update](#16-runtime-self-update)
17. [Failure Recovery (End User)](#17-failure-recovery-end-user)
18. [Entitlement Gates (Trial, Machines, Distribution, BYOK)](#18-entitlement-gates-trial-machines-distribution-byok)

---

## 1. User Onboarding

```mermaid
flowchart TD
    A[Company signs up at app.conxa.in] --> B[Clerk sign-up flow]
    B --> C{First-time login?}
    C -->|Yes| D[Download Build Studio installer]
    C -->|No| E[Record]
    D --> F[Run conxa-build-studio-setup.exe]
    F --> G[Build Studio launches]
    G --> H[First-run bootstrap starts]
    H --> I[Fetch deps-manifest from Cloud]
    I --> J[Download NSIS]
    I --> K[Download runtime-win.exe]
    I --> L[Install Playwright Chromium]
    J & K & L --> M[Bootstrap complete]
    M --> U{Update available?}
    U -->|Yes| V[Update Required screen blocks app]
    V --> W[User clicks Update now]
    W --> X[electron-updater downloads update]
    X --> Y[quitAndInstall — silent restart]
    U -->|No or check error| N[Build Studio login prompt]
    N --> O[User clicks Sign In]
    O --> P[Browser opens to Clerk]
    P --> Q[User authenticates]
    Q --> R[Token stored in OS keyring]
    R --> S[Build Studio shows Record]
```

**Notes:**
- Bootstrap (`services/bootstrap.py`) is idempotent. Re-running skips already-present dependencies.
- All downloads are SHA-256 verified against values from the cloud manifest.
- If on a corporate network, the bootstrap surfaces the exact URLs for IT whitelisting.
- The update check (step U) is fail-open: if GitHub Releases is unreachable, the app proceeds normally. Updates are mandatory — the app cannot advance past the Update Required screen without installing.
- On subsequent (non-first-time) launches the same gate applies: deps check is skipped (already installed), update check runs, then login or Workflow list.
- The Record page was removed (2026-08): the Workflow List (`/workflows`) is now the primary landing page. Its group cards drill into the Group Page (`/groups/:id`), which contains every recording/auth/compile/review/test control for that group's workflows inline — see §4. Root `/` redirects to `/workflows`; the old `/workflows/:id` per-workflow route (removed 2026-08-13) now redirects to the workflow's owning group.
- **Added 2026-08-22 — company domain step.** In the Cloud dashboard's own onboarding (`app/onboarding/`, "Clerk sign-up flow" step B above), Clerk's `<CreateOrganization>` now redirects to a new `/onboarding/company-domain` page before `/dashboard`, asking for the company's domain (used to name built installers, see `docs/Backend-Schema.md` §5.1c). Same free-text field, format-only validated as before — real ownership verification is still open, tracked as `TODO.md` PROD-6.

---

## 2. Build Studio Login

```mermaid
sequenceDiagram
    participant User
    participant Renderer as Build Studio Renderer
    participant Backend as Python Backend
    participant Clerk as clerk.conxa.in

    User->>Renderer: Click "Sign In"
    Renderer->>Backend: {type: "login"}
    Backend->>Backend: generate PKCE verifier+challenge
    Backend->>Backend: start HTTP server on port 52741
    Backend->>User: open system browser to Clerk authorize URL
    User->>Clerk: authenticate (email+password or OAuth)
    Clerk->>Backend: GET /cb?code=...&state=...
    Backend->>Clerk: POST /oauth/token (code + verifier)
    Clerk-->>Backend: access_token + refresh_token
    Backend->>Clerk: GET /oauth/userinfo
    Clerk-->>Backend: {sub, email, name, org_id}
    Backend->>Backend: store in OS keyring
    Backend-->>Renderer: {type: "result", result: {user_id, org_id, name, email}}
    Renderer->>Renderer: set auth state → show Record
```

**Refresh:** `auth_service.get_token()` checks expiry on every outbound API call. If within 60s of expiry, uses `refresh_token` to get a new `access_token` transparently.

---

## 3. Create a Workflow

```mermaid
flowchart LR
    A[User on Record] --> B[Click 'New Workflow']
    B --> C[Enter workflow name + target URL]
    C --> D[Backend: cmd_create_workflow]
    D --> E[Create Workflow record in workflow_store]
    E --> F[Workflow ID assigned]
    F --> G[Workflow appears in list with status: needs_auth]
```

**Data created:** `Workflow` model with `status="needs_auth"`, `auth=null`, `workflows=[]`.  
**Storage:** `data/workflows/{id}/workflow.json`

---

## 3a. Conversation-led workflow (Build Studio, 2026-10)

Each workflow has one continuous conversation (`/w/<id>`); the group page and Human Edit remain as places to work, but the thread is where the person is told what happened and what to do next. Source: `docs/Build-Studio-Conversation-Plan.md`; mockups: `docs/artifacts/build-studio-redesign/`.

```mermaid
flowchart LR
    N[New workflow<br/>name · group · start page · task] --> S{App signed in<br/>for the group?}
    S -- no --> SI[Sign-in card<br/>Chromium window]
    S -- yes --> R
    SI --> R[Recording card<br/>live rows · Pause · Changes each run?]
    R -- Finish --> C[Compile card<br/>real stages · queued · out of credits]
    R -- Discard / browser closed --> R2[Record again · Save N actions]
    C --> Q[Clarify questions<br/>one at a time, up to 5]
    Q --> SUM[Summary card]
    SUM --> T[Test inputs]
    SUM --> E[Human Edit<br/>Test changes]
    E --> T
    T --> TR[Test running<br/>guard pauses before steps that can't be undone]
    TR -- sign-in expired --> SX[Paused: sign in again, test again]
    TR -- failed --> AF[Auto-fix ≤ 3 attempts<br/>one gated change + Undo]
    AF --> TR
    AF -- stops --> E
    TR -- passed --> A[Approve<br/>never publishes]
    A --> P[Publish page]
```

**What stays the same:** recording and sign-in still use their own Chromium window (only tests draw into Studio's browser panel); compiling and testing run on this computer; the cloud is used for the model proxy, credits and publishing. **Who decides:** the person approves; edits revoke approval; no step that can't be undone runs in a test unless a person allows it; Clarify answers and auto-fix changes go through the same gated edit commands as Human Edit. **When something is unavailable:** offline pauses compile/chat/publish, a stopped engine shows a restart notice, an expired sign-in pauses the test instead of failing it (see TRD §7.4b).

---

## 4. Group Page — Workflow Lifecycle Rail

The Group Page (`/groups/:groupId`) is the primary interface for recording and managing every workflow in a group — there is no separate per-workflow page (removed 2026-08-13; see `docs/UI-UX-Brief.md` §2.3a). Each workflow's row carries a five-node `WorkflowStageRail` — Record → Compile → Review → Test → Ready to Package — where each node is the action for that stage, gated on the previous one's completion. Creating or renaming the group in Build Studio also creates/renames the matching folder under Conxa Cloud Skill Packages (same id and name), even if no workflow in it has been published yet. Cloud is not a second place to create groups.

### 4.1 Authentication is set up once per group, not per workflow

As of the Workflow Groups change (see `docs/TRD.md` §5.3), a workflow no longer records its own login — it inherits its group's app sessions. The same Group Page that hosts the workflow rows also hosts the group's own Applications panel (add/edit/remove apps, connect sessions); a workflow's Record node stays disabled with an explanatory tooltip until every app in its group is authenticated.

```mermaid
flowchart TD
    A[Group has apps with missing/expired sessions] --> B["Group page shows 'Connect' per app"]
    B --> C[User clicks Connect on an app]
    C --> D[Backend: cmd_start_group_app_auth]
    D --> E[Playwright launches Chromium at app.login_url]
    E --> F[User logs into the target website]
    F --> G["reached_wait_url is a passive hint only — the browser stays open"]
    G --> H[User clicks Done — the ONLY thing that ends the session]
    H --> I[Backend: cmd_finish_group_app_auth]
    I --> J["learn_auth observes LIVE / OUT / RELOAD, then self-tests the result"]
    J --> K{Self-test passed?}
    K -->|No| L["Window stays open — 'not confirmed yet, keep signing in' (or Save anyway)"]
    L --> H
    K -->|Yes| M[storageState + auth_definition saved to data/groups/group_id/auth/app_id.json]
    M --> N[Every workflow in the group flips to status=ready once all its apps are authenticated]
    N --> O{More unauthenticated apps in the group?}
    O -->|Yes| B
    O -->|No| P[Group ready — every workflow in it can record/run immediately]
```

**Key invariant:** each app's captured session lives at `data/groups/{group_id}/auth/{app_id}.json`. It is NEVER copied into the skill pack build output.

**Learning the sign-in signals (P0: Application Authentication Recording, 2026-09-25).** Step J above — `RecordingSession.learn_auth` — observes the app's `login_url` three ways (signed in, a fresh cookie-less context, and the just-saved session reloaded fresh) and keeps only the signals that differ between "signed in" and "genuinely signed out." That learned definition is what the runtime later evaluates directly, instead of guessing from a generic page-shape heuristic — see `docs/TRD.md` §5.3 ("Learned per-app authentication definitions") for the full mechanism, the definition's shape, and how a failed self-test surfaces in the wizard.

### 4.1a Recording a workflow starts pre-authenticated to every app in its group

`cmd_start_recording` merges every authenticated app's storageState in the workflow's group into one seeded Playwright context (`conxa_core.storage.storage_state.merge_storage_states`) before opening the recorder — so a recording that crosses several of the group's apps starts signed in to all of them, with no per-site login step during the recording itself. Only the apps the workflow's own URLs actually resolve to (by hostname) have to be connected before recording can start — an app elsewhere in the group that this workflow never visits doesn't block it, (the runtime is stricter and gates every run on the whole group — §4.1b below). Because recording seeds every captured app, not just the required ones, the gate also runs a bounded freshness check against every captured app (skipping any checked in the last 10 minutes) before recording opens: an expired *required* app blocks recording outright, while an expired *sibling* app only surfaces a warning in the response — the user is right there in the recorder window and can sign back in inline if that app actually comes up.

Recording no longer discards what happens to the session while it's open: it autosaves into the same merged file it was seeded from, and when the recording stops (or is cancelled), each app's slice of that file — cookies and local storage the app already owned, nothing a sibling app in the same group picked up — gets written back to that app's own saved session. Signing in once now actually holds: routine cookie rotation, a refreshed token, or the user re-authenticating mid-recording because the seeded session had gone stale all stick, instead of being thrown away the moment the recording window closes.

### 4.1b Testing/running a workflow needs every app in its group signed in — and never fails mid-run for a gap recording papered over

A workflow runs only when **every** app in its group is signed in (`docs/TRD.md` §5.3 "A group is the unit of sign-in", AUTH-14) — there is no per-workflow subset, so a compiled skill carries no `required_apps`. Recording is looser (§4.1a: only the apps the workflow's own URLs touch must be connected before it starts); the runtime is stricter, so a group with an app nobody has connected yet fails at pre-flight, not mid-run. Pre-flight validates every app's saved session (a recent check is remembered for a few hours), seeds the browser from all of them, and if more than one needs signing in, every login window opens together with one message naming all of them, rather than one window per run attempt. Build Studio's Run Test also refuses to run a pack built before the group's sign-in setup last changed (an app re-Connected under a new id, a sign-in definition learned) — rebuild the skill package first (`sign_in_setup_stale`, AUTH-15).

A session that expires **mid-run** normally fails the run outright and asks the person to call the skill again — re-authentication happens on that next call's own pre-flight check. An app whose sign-in was learned (has a saved `auth_definition`) is the one exception: the run instead opens a sign-in window automatically and resumes itself once the person finishes signing in, with no second call needed. See `docs/TRD.md` §5.3 ("Mid-run expiry now auto-retries").

### 4.2 Record a Workflow (inline action)

```mermaid
flowchart TD
    A["Group page, workflow row, group auth ready"] --> B["Record rail node is enabled (also for re-record when a recording already exists)"]
    B --> C[User clicks the Record node — a recording already exists triggers a re-record confirm first]
    C --> D[Backend: cmd_start_recording with workflow_id + auth_mode=false]
    D --> E[Load auth session from auth/auth.json]
    E --> F[Playwright launches with storageState]
    F --> G[Navigate to workflow.protected_url]
    G --> H[bridge.js injected into all frames]
    H --> I[User performs workflow steps in browser]
    I --> J[Events captured: click, fill, select, navigate, etc.]
    J --> K[User closes the browser, clicks 'Save Workflow Now']
    K --> L[Backend: cmd_stop_recording]
    L --> L2[Playwright context closes; each tab's recording.webm/recording-tab_N.webm renamed into place]
    L2 --> M[Events saved to sessions/session_id/events.jsonl]
    M --> N[Workflow updated with status=recorded]
    N --> O[Recording dialog closes, the row's rail advances to an enabled Compile node]
```

**Re-recording a workflow** is allowed at any time: clicking the already-recorded row's Record node opens a confirm ("your previous recording will be backed up"), and `cmd_start_recording` copies the previous session's folder (`data/sessions/<old-session-id>/` — events, screenshots, snapshots) to `data/backups/recordings/<workflow-id>/<timestamp>-<old-session-id>/` before the new recording replaces it. The copy is additive — the original session directory stays in place, because compiled skill packages and editor state may still reference blobs under it. The new session becomes the workflow's single active recording (`workflow.session_id`), so compile, recompile, and tests all use the latest take automatically. A failed backup never blocks a re-record.

**Event types captured by bridge.js:**
`click`, `dblclick`, `right_click`, `type`, `fill`, `focus`, `select`, `select_option`, `set_checkbox`, `set_radio`, `date_pick`, `drag_drop`, `keyboard_shortcut`, `upload`, `navigate`, `scroll`.
`tab_open`/`tab_switch`/`popup`/`frame_enter`/`frame_exit` come from the compiler/session, not the bridge. `dialog_accept`/`dialog_dismiss` come from Playwright's `page.on("dialog", ...)` in `session.py` (see below); `dialog_appeared` is declared but never produced.

**A native `alert`/`confirm`/`prompt` pauses the recording.** Chromium still shows the real dialog, but the recorder holds it open (never auto-answers it) and a Studio modal — pinned in front of the browser so it's never hidden behind it — shows the same message and asks for the answer (OK/Cancel, plus a text box for a prompt) the human would have given the real browser. Their answer is what gets recorded, replayed, and used to close the real dialog; a prompt's typed answer becomes a named input on the compiled skill so it can be answered differently at run time. Because the real dialog closes while its window is in the background, the browser window is briefly brought back to the front right after to clear any stale leftover image — never minimized. A dialog left unanswered for 2 minutes is auto-accepted so it can never freeze a recording.

Stop-recording no longer waits on video frame extraction — it only renames Playwright's raw `.webm`
files into place and returns. Frame extraction (for vision anchors) now runs at compile time, see §5.

**Recording across multiple tabs** works whether the site opens the new tab (a link or button
that opens `target="_blank"`) or the user does (Ctrl+T): every tab the browser context opens is
captured, not just the first. Open a new tab, do work there, switch back to the original tab and
keep going — all of it is recorded, in order, with each event tagged to the tab it happened on.
Compiling inserts a small marker step ("switched to a new tab" / "switched back to tab 1") at
each crossing, and at replay time the runtime follows the same tabs the recording did — waiting
for a tab the site is expected to open, or opening one itself for a tab the user opened manually.
A file downloaded in one tab can also be picked up and uploaded again in a later tab, in the same
run, with no extra step required. See `docs/TRD.md` §6.4, §7.1, §9.2.

**Recording a file upload** works exactly like any other step from the user's point of view: click the
page's upload control, pick a file, done — except the dialog that opens is the Studio's own, not
Chromium's native one, and it opens already pointed at the folder the most recent download landed
in (or its extracted folder, for a downloaded zip). Three earlier attempts to make Chromium's own
picker remember that folder never worked reliably, so recording now intercepts the picker request
and shows its own dialog instead — see `docs/TRD.md` §7.1. What gets recorded is the file's *name*,
never its path (browsers do not expose paths), so the compiled skill turns the upload into a required
`file_path` input the calling agent must supply at run time. See `docs/TRD.md` §9.4.

---

## 5. Pipeline & Compilation

```mermaid
flowchart TD
    A[User selects workflow, clicks Compile] --> B[Backend: cmd_compile]
    B --> C[Install LLM proxy router]
    C --> C2[Extract video frames from recording.webm]
    C2 --> D[Load raw events from events.jsonl]
    D --> E[pipeline/normalize.py]
    E --> F[pipeline/dedupe.py]
    F --> G[pipeline/enrich.py]
    G --> H[Normalized event list]
    H --> I[compiler/build.py: compile_skill_package]
    
    I --> J[workflow_intent: one text call for the whole workflow]
    J --> K[For each step:]
    K --> L[IdentityBundle: deterministic selectors]
    L --> N[validation_planner: Assertion list]
    N --> O[recovery_policy: RecoveryBlock]
    O --> P[confidence/layered: confidence score]
    P --> Q{More steps?}
    Q -->|Yes| K
    Q -->|No| R[Assemble SkillPackage]
    R --> S[Save to data/skills/skill_id/skill.json]
    S --> T[Update WorkflowWorkflow: status=compiled, skill_id set]
    T --> U[Compile complete — step count shown to user]
```

**Frame extraction (step C2):** runs once per compile against `recording.webm`, cutting 5 PNGs per
recorded event (`before_far/near`, `at`, `after_near/far`) via ffmpeg for the compiler's vision-anchor
step. It is idempotent — a frame already on disk from a prior compile attempt is not re-cut — and
isolated per event, so one event's extraction failure (e.g. a slow/timed-out ffmpeg call) does not cost
any other event its frames. A failed event falls back to deterministic, DOM-derived anchors instead of
vision-model anchors for that step only (`Vision anchors fell back for step N` in the compile log);
primary element selection is unaffected, since the `IdentityBundle` never reads vision output. Recompile
re-attempts only the events still missing frames.

**Fresh compile quota:** Before local compile starts, Build Studio reserves 1 compile credit through `POST /api/v1/usage/compile/reserve`. The reservation is committed before the first LLM-assisted pipeline/compiler stage. If compile fails before commit, Build Studio releases the reservation; after commit, the credit remains consumed.

**Recompile quota:** Existing workflows with `skill_id` skip compile-credit reservation. Their proxied LLM calls use `usage_class="human_edit"` and draw from the monthly Human Edit pool.

**LLM calls** route through `conxa_core.llm.get_router()`, which is replaced at compile time with `LLMProxyClient` forwarding to `POST /api/v1/llm/proxy/{text,vision}` with `usage_class` set to `compile` or `human_edit`.

**Real-time events** stream from backend to renderer during compilation:
- `pipeline_start`, `pipeline_done`
- `compile_step` with `step` and `status` fields
- `compiler_start`, `compiler_done`
- `api_call` — each LLM call
- `compile_error` — on failure

---

## 6. Workflow Editing (HumanEdit)

```mermaid
flowchart TD
    A[Compiled workflow in Build Studio] --> B[User opens HumanEdit screen]
    B --> C[Load workflow via cmd_get_workflow]
    C --> D[Render step list with thumbnails]
    D --> E{User action}
    E --> F[Edit step field] --> G[cmd_patch_step → update + revalidate]
    E --> H[Reorder steps] --> I[cmd_reorder_steps]
    E --> J[Insert step] --> K[cmd_insert_step]
    E --> L[Delete step] --> M[cmd_delete_step]
    E --> N[Update input variables] --> O[cmd_update_workflow_inputs]
    E --> P[Replace literal with variable] --> Q[cmd_replace_literals]
    E --> R[Apply recording screenshot to step] --> S[cmd_apply_recording_visual]
    E --> T[Re-target element wizard] --> T1[Phase 1: draw region] --> T2[cmd_retarget_preview: candidates + validation diff] --> T3[Phase 3: Validation — review/edit the enforced post-condition] --> T4[cmd_retarget_apply → bbox + target + identity_bundle + validation, one undo entry]
    E --> U1[Add/remove/reorder if_present body step] --> U2[cmd_insert_branch_step / cmd_delete_branch_step / cmd_reorder_branch_steps]
    E --> U3[Edit nested branch-body step field] --> U4[cmd_patch_step with path='branch.steps N ']
    E --> U5["Answer 'Optional popup?' in the review-questions dialog"] --> U6[cmd_confirm_optional_interstitial → step becomes try_dismiss branch]
    E --> V[Sign off workflow] --> W[cmd_sign_off_workflow → signed_off=true, edited_at=now]
    W --> Z[Auto-invoke skill_package_builder.build_skill_package, only_workflow_id=this workflow — see §7] --> AA[Return built=true; only this workflow's skill built — a sibling workflow that isn't compiled/edited yet is never required or touched]
```

**Patch gate:** Each edit increments the skill version. `revalidate_step()` checks that selector and intent remain coherent after the patch.

**Three-tier review surface (2026-07-10 redesign):** the workflow list, per-step panel, and a new
Diagnostics dialog now separate what an *approver* needs to see (branch-body sub-steps, safety
badges, a workflow-level compile-health banner, the compiled "Workflow plan" tool pane showing
`intent_graph`) from what a *skill engineer* needs (a per-step "Reliability" section: recovery
behavior, element fingerprint, current identity signals) from what *support* needs (integrity
hashes, LLM router stats). All of it is read-only projection through the existing
`cmd_get_workflow`/`_skill_response` chokepoint — see `docs/Implementation-Plan.md` §1.11 and
`research-analysis/Human-Edit-vs-Skill-Package.md` for the full audit and design rationale.

**Branch-step authoring:** `if_present`/`try_dismiss`/`wait_for_one_of` (§10.7 of `docs/TRD.md`)
are now insertable from the Add-action menu. `if_present`'s nested body gets a dedicated editor
(add/remove/reorder + per-step field edits via the new `path`-addressed `cmd_patch_step`); nested
steps cannot patch `recovery`/`validation` since branch bodies are best-effort and never enter
recovery. `try_dismiss`/`wait_for_one_of` show read-only summary badges only — no authoring UI
for their candidate/option lists yet (`TODO.md` BUILD-6).

**AI Review step authoring (EXEC-13, 2026-09-14):** `ai_review` (§10.9 of `docs/TRD.md`) is now
insertable from the Add-action menu alongside the branch primitives above. It has no selector to
pick and no post-condition to assert, so its config block replaces the usual selector/Validation
panels entirely: a required question, an answer-shape preset that fills an editable JSON schema
textarea, an on-failure policy (abort / use a default / continue), and — like `wait`/`screenshot`
before it — the generic Value field, here labelled "Output binding name" and doubling as the name
later steps read the answer back from. Testing a workflow containing one in the Build Studio no
longer fails outright: the sandbox has no MCP agent to answer the paused checkpoint, so it answers
the checkpoint itself through the metered cloud LLM proxy and resumes, the same way a real agent
would over MCP on a customer's machine (at zero cost there, since that path runs on the customer's
own Claude subscription).

**Hand-over step authoring (EXEC-21, hand-over shape, 2026-09-15):** `handover` (§10.10 of
`docs/TRD.md`) is `ai_review`'s human sibling in the Add-action menu — same "no selector, no
Validation panel" shape, but the generic Value field (labelled "Message shown to the person" here,
rather than an output binding name) is the message a person sees on the pause banner, since that's
the one thing every hand-over must have and it's the field every action-kind editor already
renders. On-failure is narrower than `ai_review`'s (abort / continue — no "use a default", since a
hand-over produces no answer value). Testing one in the Build Studio requires an actual person: the
sandbox forces a visible browser and shows the same in-page banner a production run would, and the
tester clicks it themselves to resume — there is no cloud-proxy stand-in the way `ai_review`'s
sandbox answerer provides, because nothing but a person can do what a hand-over step exists for.

**"Treat as optional?" suggestion (recording-next-steps.md Priority 2, 2026-07-10):** the recorder
now observes (never probes) whether a step's target sat inside what looked like an optional
interstitial — a dialog or cookie/consent banner — during recording, and flags it advisory-only;
the step still compiles and executes as a normal required step regardless. When flagged, Human Edit
asks an unskippable "Optional popup?" question in `ReviewQuestionsDialog.tsx` (the same dialog that
asks the for-each loop questions). Answering Yes
calls `cmd_confirm_optional_interstitial`, which rewrites the step into a real `try_dismiss` branch
(candidates seeded from the step's own recorded selector plus the recorder's observed container) —
the same executor path `try_dismiss` steps already use (§10.7 of `docs/TRD.md`). This is the only
way a branch step gets created from a live recording without hand-editing JSON: the compiler never
converts a flagged step on its own, honoring "branch steps compile only from observed states +
human confirmation."

**Auto-build on sign-off (2026-07 workflow redesign, Phase 1):** sign-off no longer just flips a flag. `cmd_sign_off_workflow` re-checks, after persisting `signed_off`/`edited_at`, whether every workflow belonging to the workflow is compiled and signed off — the same condition `workflow_builder.build_workflow` already gates on (§8). If so, it calls `build_workflow` itself, so the package exists the moment the gate is satisfied instead of requiring a separate visit to a build page (there is no such page anymore — see §8). The gate itself is unchanged; this only moves *when* the already-existing build call happens to fire.

**Re-target wizard (replaces the old direct "update visual bbox" flow):** `cmd_update_visual_bbox` still exists (bbox + vision-anchor regeneration only, no selector change) but the primary re-target entry point is the 3-phase wizard — Pick element → Review selectors → **Validation**. `cmd_retarget_preview` is read-only (generates and scores candidates + a validation diff without persisting); `cmd_retarget_apply` is the only command that writes, composing bbox + `target.primary_selector`/`fallback_selectors` + rebuilt `identity_bundle` + (optionally) regenerated `validation.wait_for`/`assertions` into a single document mutation and a single undo entry. See `docs/UI-UX-Brief.md` §2.8 for the UI.

**Phase 2 candidate generation when the element is re-picked** (drawing a new region) uses a vision LLM, not text: there is no stored per-element geometry anywhere in a recording (only the originally-clicked element carries a bbox, and the DOM snapshot is plain HTML with no inline coordinates), so a text prompt has no way to relate a drawn region to a DOM node. `conxa_compile/llm/region_selector_vision.py` (task `region_selector`) sends the step's full-page screenshot — with the drawn region highlighted, the same technique `anchor_vision_llm.py` uses for anchors — together with the recorded DOM snippet, so the model can see which element the box points to and read the DOM to select it. Candidates then go through the same validate/prune pipeline as every other selector source (§7.2). Continuing without re-picking the element (`regenerate=False`) still reads back the already-compiled selectors with no LLM call.

**Validation phase (was "Confirm & apply"):** Phase 3 is a true post-condition review, not just a
confirm-and-go screen. It surfaces the step's enforced (`required=True`) assertion — the single
deterministic post-condition the compiler picked for this action (`docs/Backend-Schema.md` §3.6)
— alongside any advisory checks, and lets the user edit the flat assertion list (type, target,
expected value, timeout, required) before applying. An edit sends `edited_assertions` in the
`cmd_retarget_apply` payload, which takes precedence over the previewed `proposed_assertions`;
omitting any edit falls back to today's keep/replace behavior unchanged. A step with no enforced
assertion is flagged in the UI ("this step will pass even if the action had no effect") rather
than silently accepted.

Deterministic Human Edit actions are available without quota: patch, reorder, delete, input edits, validation edits, sign-off, and reviewing a step's already-compiled selectors in the re-target wizard (continuing without re-picking the element). LLM-assisted actions such as selector regeneration (including the re-target wizard's Phase 2 candidate generation **when the element is re-picked**), visual re-anchor, screenshot/bbox anchor regeneration, semantic repair, and raw-recording recompile require remaining Human Edit pool.

**Human Review Copilot (BUILD-26, 2026-09-10):** Human Edit gains a conversation. A floating
launcher (bottom-right by default, freely draggable anywhere on screen, position persisted)
opens a chat card without leaving the step list — deliberately not the Tools rail, which opens
as a shared modal and would hide the step being discussed. A "new session" button archives the
outgoing conversation to disk (`copilot_sessions.jsonl`, §5.10a of `docs/Backend-Schema.md`)
before clearing it, so switching topics never silently loses the prior thread. A reviewer types
a question ("why did step 3 fail?",
"give me a better assertion for step 5") and the copilot answers grounded in real evidence: the
compile report, the recovery cascade's own log of what it tried, a live element inventory, and —
when the most recent Studio test run failed — the screenshot taken at that moment. It may propose
a change as an accept/reject diff (never applying anything itself), restricted to a step's
`value`/`input_binding`/`intent`/`semantic_description`/`validation.assertions` — never a
selector, same boundary as every other LLM path in this product. Accepting one runs through the
exact same `cmd_patch_step` a manual edit uses (its own undo entry, its own quota draw from the
Human Edit pool); rejecting logs the decision without touching the compiled skill. Switching to a
different step while a proposal is pending prompts the same "you'll lose this" confirmation the
re-target wizard already shows for an unsaved edit — discarding one this way still counts as a
rejection, not a silent drop. See `docs/UI-UX-Brief.md` §2.7 and `docs/TRD.md` §7.4.

**Verify fix (BUILD-26 stage e, 2026-09-10):** after accepting a proposal, a **Verify fix** button
appears — clicking it first shows a count of the workflow's non-reversible steps ("this run
performs N steps that commit or destroy data") with **Run anyway** / **Cancel**; nothing runs
against the real system until the reviewer clicks through that confirmation. Confirming rebuilds
the skill package and retests the workflow, streaming the same progress lines a manual Run Test
does into the chat, then shows a verdict: **Fixed** (the retest passed), **Still failing** (the
same step still fails), or **Progressed** (a different step now fails). A reviewer-initiated
cancel mid-retest shows no verdict at all — it is neither a pass nor a failure worth recording.

**Overlay-aware proposals (BUILD-26 stage f, 2026-09-10):** when a Studio test run hits an
unexpected overlay (a popup, a banner) that blocks a step — whether or not it ultimately recovers
— the runtime remembers what it saw. A reviewer can then ask "add a condition for that popup" and
the copilot proposes inserting a conditional branch (dismiss it, or check for it before a step)
built from that actual observation — never a guessed selector. The proposal renders the same
accept/reject diff as any other, just describing an inserted step instead of a field change.

---

## 7. Build Skill Package

**Trigger (revised 2026-08, re-scoped 2026-08-19):** there is no longer a "Build Skill Package" page or button. `cmd_build_skill_package` (and the `skill_package_builder.build_skill_package` call it wraps) fires automatically the moment sign-off's gate check passes for **that one workflow** — a sibling workflow that isn't compiled/edited yet is never required or touched — or manually via the Inspector drawer's "Rebuild package" action (also single-workflow scoped) or the Publish page's per-skill "Rebuild" action. 1 Workflow = 1 Skill = 1 Skill Package = 1 independent version history = 1 independent release: `build_skill_package(workspace_id, only_workflow_id=...)` compiles just that workflow into its own subtree of the shared local package directory, merging into (never replacing) whatever sibling skills were already built there. An explicit full-workspace rebuild (`only_workflow_id=None`) still exists as a maintenance action but is no longer the path sign-off or Publish use.

```mermaid
flowchart TD
    A[Sign-off completes for one workflow, or user rebuilds that one skill from the Inspector/Publish page] --> B[Backend: cmd_build_skill_package, skill_slug=this workflow]
    B --> C[Read only this ONE compiled + signed-off workflow]
    C --> D[skill_package_builder.build_skill_package, only_workflow_id=this workflow]
    D --> E["Create/reuse output/{workspace_dir_slug}-skill-package/ folder"]
    E --> F[Merge this workflow's entry into skill_package.json — sibling skills' entries untouched]
    E --> I["Write {skill_slug}/execution.json, recovery.json, inputs.json"]
    I --> M["Copy to data/skill-packs/{workspace_dir_slug}/{group_id}/{skill_slug}/ — sibling skills' directories untouched"]
    M --> N[Merge skills/skill_groups into pack.json — union, not replace]
    N --> O[Skill package build record saved]
    O --> P[Build complete — this skill ready to publish]
```

**Security check:** Build output directory is scanned for `auth.json`. If found, the build is **refused** with `auth_file_in_build_input` error.

---

## 8. Publish & Build Installer

**Publish is the primary, mandatory release action — and publishing is not
the same as deploying.** Build Studio only ever gets a version to Conxa
Cloud as an immutable, versioned artifact; a Cloud admin's explicit
Release/Deploy action is what actually activates it and starts customer
machines receiving it. Build Installer is a secondary, optional action for
distributing an already-published skill package as a standalone `.exe`.

```mermaid
flowchart LR
    subgraph Studio["Build Studio (BUILD/PUBLISH plane)"]
        S1[Workflow] --> S2[Compile] --> S3[Test] --> S4[Publish]
    end
    subgraph Cloud["Conxa Cloud (RELEASE/DEPLOYMENT control plane)"]
        C1["Ready for Release"] --> C2[Review: diff, test status, artifact] --> C3["Release / Deploy"] --> C4["Desired Version"]
    end
    subgraph Runtime["Runtime (customer machine)"]
        R1[Sync] --> R2[Verify] --> R3[Install] --> R4[Execute] --> R5["Report Status"]
    end
    S4 --> C1
    C4 --> R1
```

### 8.1 Publish Skill Package (Build Studio, Primary)

The Publish page is scoped to ONE selected skill (workflow) via a skill
picker: release candidate → what will change (deterministic diff against
that skill's own previous version) → version + release notes → publish. Every
skill has its own independent version number (a brand-new skill's first
publish is v1.0.0, no previous-version requirement) and its own publish/test
gate — publishing "Create a Lead" never requires, checks, or touches "Update
Opportunity," even if "Update Opportunity" is failing its tests. Full
mechanism in `docs/TRD.md` §5.5; API contracts in `docs/Backend-Schema.md`
§5.1d.

If the selected skill hasn't passed its test, the Publish page offers **Run test** inline (step 1) — the same test runner as the Group Page — and keeps the release form locked until it passes.

```mermaid
flowchart TD
    A[Skill package built; user visits Publish, picks a skill] --> A2["Studio previews: proposed version, diff vs. THIS skill's current stable, artifact hash"]
    A2 --> B[User enters version + release notes + clicks Publish]
    B --> C[Backend: cmd_publish_skill_pack, skill_slug=selected skill]
    C --> D["Read only this skill's own files from data/skill-packs/{workspace_dir_slug}/{group_id}/{skill_slug}/"]
    D --> E["POST .../skills/{skill_slug}/skill-packs/upload to Cloud"]
    E --> F{Cloud: duplicate version or unchanged artifact — checked against THIS skill's own history?}
    F -->|Yes| F2[409 — reject, Studio shows why, Publish button stays enabled to retry]
    F -->|No| G["Cloud: write this skill's own immutable per-version snapshot"]
    G --> H["Cloud: version-history row status=ready; register this skill+group in Cloud's Groups/Workflows index"]
    H --> I["Cloud: return {tracking_token, sync_url, version} — the stable channel is NOT moved"]
    I --> J[Rewrite local pack.json with tracking + sync_endpoint]
    J --> K["Studio shows a bare confirmation: 'v{version} is Ready for Release in Conxa Cloud' — no deployment/audit UI rendered here"]
```

A failed publish (step F2, or a failure inside G-H) never creates a "ready"
row — the UI states plainly that nothing was uploaded and what version failed.
Because every step above is scoped to one skill, a failed publish (or a
failing test gate) for one skill never blocks, retests, or republishes any
other skill. Build Studio never renders release history, deployment status,
or an audit log — those, along with Release/Deploy and Rollback, are
Cloud-only (§8.1a).

### 8.1a Release, Deploy, Rollback, and Deployment/Audit — Conxa Cloud (Primary)

Everything downstream of "uploaded to Cloud" lives in the Cloud dashboard
under Skill Packages → Group → Workflow, never in Build Studio:

- **Ready for Release**: every "ready" version for the selected workflow,
  each with its diff against the previous release, its test status
  (`tests_passed`, reported by Build Studio at publish time), and its
  artifact hash.
- **Release / Deploy**: a Cloud admin picks a "ready" version and confirms
  (current stable, target, effect shown explicitly). This is the *only*
  action anywhere in the product that moves a skill's stable channel forward
  — it refreshes that skill's files in the mutable mirror runtimes actually
  sync from, records its `component_versions` entry, and flips the version
  row to "published." Admin/owner only, enforced server-side
  (`require_admin`), same gate as publish and rollback.
- **Rollback**: from Release History, an admin/owner picks a previous
  *already-published* version of the same skill and confirms. No artifact is
  rebuilt or re-uploaded — that skill's stable channel pointer moves back to
  the already-immutable snapshot, and only that skill's files in the mutable
  mirror plus its own `component_versions` entry are refreshed from it; no
  other skill's channel, files, or version history is touched, and the
  company-level `pack.json` (target_url, tracking) is left as-is. **Rollback
  is always scoped to one workflow/skill — never the whole group.**
- **Deployment dashboard**: desired (stable) version vs. what each registered
  machine last reported, derived only from data the runtime actually reports
  (`runtime_registrations`) — never fabricated. Per-machine status is one of
  `up_to_date`, `pending` (hasn't synced to the desired version yet),
  `failed` (the runtime reported a sync error — checksum mismatch or
  download/activation failure — that it hasn't since recovered from),
  `offline` (not seen in 30 days), or `unknown` (a registration that predates
  per-skill version reporting).
- **Audit log**: every publish, release, rollback, and channel-change event
  for the selected skill, attributed to the user who did it — a durable,
  unbounded per-skill event log (mirrored into the global audit ring buffer
  the rest of the dashboard already uses).
- **Archive / Restore**: this is "delete a workflow" in Skill Packages, and it
  is deliberately non-destructive. An admin/owner archives a workflow from its
  page header; the workflow drops out of every installed customer's AI
  assistant (`list_skills`) at their next sync, but nothing already on a
  customer's machine is deleted, and the workflow's version history and
  release stay exactly as they were. Restore reverses it in one click — no
  republish needed. While archived, Release and Rollback are unavailable for
  that workflow (the server rejects both), and the workflow shows dimmed with
  an "Archived" badge instead of its usual status everywhere it's listed.

Runtimes pick up a Release or a Rollback at their next regular delta-sync —
the sync mechanism itself doesn't know the difference between the two, only
that the stable channel moved. Archiving works the same way: the workflow
simply stops appearing in what the server reports, and the runtime's regular
sync brings its own list back in line — no separate "delete" message is ever
sent to installed machines.

### 8.2 Build Installer (Secondary, Optional)

```mermaid
flowchart TD
    A[Skill package published; user visits Build Installer page] --> B[User enters release notes + logo path + clicks Build]
    B --> C[Backend: cmd_build_installer]
    C --> D[Validate skill pack published]
    D --> E[build_installer via NSIS]
    E --> F[".exe created at output/{workspace_dir_slug}-Setup.exe"]
    
    F --> G{User clicks Upload to Cloud?}
    G -->|Yes| H["POST /api/v1/workflows/{installer_version}/installer/upload"]
    G -->|No| I[Save installer locally, optionally skip cloud upload]
    H --> J[Cloud stores installer.exe + meta.json]
    J --> K[Cloud returns download_url]
    K --> L[Show installer path + cloud download URL to user]
```

**Installer contents:**
- `skill-packs/{workspace_dir_slug}/` (pack.json with tracking config embedded)
- `runtime.exe` + `keytar.node` + `version.json`
- Chromium browser (fetched at install time via `runtime.exe --install-playwright`, not bundled)

**Customer-visible meters shown during this flow:**
- Settings/Billing: seats, machines, compile credits, Human Edit pool.
- Publish: compile credits (paid monthly tier only).
- Build Installer: optional cloud upload; no metering (upload can be skipped entirely).

Workflow recording and local workflow creation remain unlimited.

---

## 9. End-User Installation

```mermaid
flowchart TD
    A[Customer receives Company-Agent-Setup.exe] --> B[Run installer - no UAC]
    B --> C["NSIS installs conxa-runtime.exe + keytar.node into conxa-runtime\<version>\, creates conxa-runtime\current junction"]
    C --> D[NSIS installs app layer into conxa-app\<version>\, creates conxa-app\current junction]
    D --> E[NSIS runs conxa-runtime\current\conxa-runtime.exe --install-playwright with CONXA_DIR=$PROFILE\.conxa]
    E --> F["NSIS installs skill-packs to $PROFILE\.conxa\skill-packs\company\{skill}\<version>\, creates a current junction per skill"]
    F --> G[NSIS generates a PowerShell script that merges a conxa entry into claude_desktop_config.json]
    G --> H[Same entry merged into ~/.claude.json for Claude Code, if it already exists]
    H --> I[Customer restarts Claude Desktop]
    I --> J[Claude Desktop starts conxa-runtime\current\conxa-runtime.exe via MCP stdio with CONXA_DIR env var]
    J --> K[Runtime finds Chromium + skill packs via CONXA_DIR]
    K --> L[list_skills tool available in Claude]
```

**Install scope:** Per-user (`RequestExecutionLevel user`), installs to `$PROFILE\.conxa` (i.e. `%USERPROFILE%\.conxa`). No admin elevation required. Correctly resolves to the logged-in user's profile (avoids the elevated-admin-wrong-profile bug).

**Versioned layout:** Every component the installer lays down (host exe, app layer, each skill) is its own versioned directory with a `current` directory junction pointing at it — see TRD.md §4.5. Junctions are used (rather than a plain flat copy) because this is the exact same on-disk convention the runtime's self-updater writes into later; the installer's initial install and every subsequent update speak the same layout from day one. Directory junctions don't require admin rights or Developer Mode, unlike true NTFS symlinks.

**MCP registration:** The installer writes directly into `claude_desktop_config.json` via a generated PowerShell script that does a non-destructive JSON merge (preserves any existing `mcpServers` entries). The registered `command` points at `conxa-runtime\current\conxa-runtime.exe` — a stable path that is written **once**, at install time, and never rewritten; every future self-update simply flips the `current` junction to a new version directory, so Claude Desktop's config never needs to change again. The script auto-detects the Microsoft Store/MSIX install path (`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`) and falls back to `%APPDATA%\Claude\` otherwise. If `~/.claude.json` (Claude Code) already exists, the same entry is merged there too; it is never created if absent.

**CONXA_DIR wiring:** The MCP entry written into `claude_desktop_config.json` (and `~/.claude.json`) sets `env.CONXA_DIR = $PROFILE\.conxa` — the same `INSTALL_DIR` used during install. `server.js` derives `PLAYWRIGHT_BROWSERS_PATH` and `SKILL_PACKS_DIR` from `CONXA_DIR`, so the runtime always finds the `.exe`-installed Chromium and skill packs.

**Uninstall asymmetry:** The `.exe` uninstaller removes `$PROFILE\.conxa` (when no other companies' skill packs remain) and the HKCU uninstall registry key, and also removes the `conxa` entry from `claude_desktop_config.json` and `~/.claude.json` via the same PowerShell-merge approach — no manual cleanup needed in Claude Desktop's UI. `RMDir /r` on a tree containing directory junctions removes each junction as a single reparse-point entry without recursing into its target (standard Win32 behaviour); since every `current` junction points at a version directory that is itself a normal subdirectory of the same tree being removed, nothing survives and nothing is ever deleted through an unintended path.

---

## 10. Runtime Registration & First Sync

```mermaid
flowchart TD
    A[conxa-runtime.exe starts via conxa-runtime/current junction] --> B[Resolve CONXA_DIR + CONXA_DATA_DIR]
    B --> B2[bootstrap.js: GET manifest.json signature-verified, update conxa-app if newer — see §16]
    B2 --> C[bootstrap.js: version_manager.resolveCurrent for conxa-app, min_host check, load server.js]
    C --> D[Load skill index from skill-packs/ cache]
    D --> E[Connect MCP to Claude Desktop]
    E --> F[Async: POST /telemetry/runtime-start fire-and-forget]
    E --> G[Async: manifest_manager.checkForUpdates for conxa_runtime — reuses the manifest bootstrap already fetched]
    E --> H[Async: syncSkillPacks — per-skill delta, 4s timeout]
    H --> I[For each company in skill-packs/:]
    I --> J[getToken from OS keychain]
    J -->|No token| K[Skip — log warning]
    J -->|Has token| L["GET /skill-packs/{co}/delta?since={per-skill version map}"]
    L -->|All skills no_change| M[Up to date]
    L -->|Some skills changed| N[Per changed skill: write to <skill>/<version>/, activate — others untouched]
    N --> O[Update pack.json last_synced]
    O --> P[Reload skill index]
    P --> Q[Sync complete]
```

**No token-acquisition step needed:** the runtime authenticates to Conxa Cloud using the **installer-embedded sync token** — a `secrets.token_urlsafe(32)` string minted at publish time and written into `pack.json` (see `docs/TRD.md` §5.4, `docs/Auth-and-Updater.md` §1.3). It's sent as `Authorization: Bearer` on every delta-sync request straight out of the installer, with no keytar lookup and no user login required. This superseded an earlier design (a Clerk-token/`setup_company` challenge-URL flow) that would have required an in-app completion step — that flow was replaced before shipping, so there is no first-sync gap to close here.

---

## 11. MCP Skill Execution

```mermaid
sequenceDiagram
    participant User
    participant Claude as Claude Desktop
    participant RT as Runtime (MCP)
    participant Browser as Playwright Browser
    participant Cloud as Conxa Cloud

    User->>Claude: "Submit my expense report for last week"
    Claude->>RT: list_skills
    RT-->>Claude: [{company: "acme", slug: "submit_expense", inputs: [...]}]
    Claude->>RT: get_skill_inputs(skill="submit_expense", company="acme")
    RT-->>Claude: {inputs: [{name: "period", type: "string", required: true}]}
    Claude->>User: "What period? (e.g. 'last week', '2025-05')"
    User->>Claude: "last week"
    Claude->>RT: execute_skill(skill="submit_expense", company="acme", inputs={period: "last week"})
    RT->>RT: load execution.json + recovery.json
    RT->>RT: load storageState from cache/sessions/, validate against protected_url
    alt session missing or expired
        RT->>Browser: open ONE Chromium, a sign-in tab per missing app (valid apps untouched)
        RT-->>Claude: returns at once: "Authentication required — sign in to <apps>" + run_id
        User->>Browser: signs in (SSO / MFA / CAPTCHA — take as long as needed)
        Browser-->>RT: detected automatically, tab closes, session saved
        Note over RT,Claude: the workflow then starts BY ITSELF in the same Chromium —<br/>Claude does not run it again, it polls get_execution_status(run_id)<br/>for the result. Closed tab / failed sign-in is reported by name, and can be retried.
    else session valid
        RT->>Browser: launch Chromium (headed by default)
        loop For each step
            RT->>Browser: executeStep() with resolveElement()
            Browser-->>RT: success or failure
            RT->>RT: verifyAssertions()
            RT->>RT: writeCheckpoint()
            RT->>Cloud: POST /tracking/{co}/events (async)
        end
        Browser-->>RT: workflow complete
        RT->>Browser: close
        RT-->>Claude: {content: [{type: "text", text: "Done. Expense report submitted."}]}
        Claude-->>User: "Done. Expense report submitted."
    end
```

A step failing mid-execution with a login redirect (session expired) follows the same
pre-flight-only pattern: `isAuthFailure()` detects it and `execute_skill` fails immediately, telling
Claude to call it again — that next call runs the pre-flight gate above, which opens the login
window and waits — then resume with `resume_from`. See `docs/Auth-and-Updater.md` §1.3.

**Dry-run (PROD-3-DRYRUN).** When a skill's last step is a hard-to-undo action (delete a record,
send a payment), the user can ask to preview it first: `execute_skill(..., dry_run: true)` runs
every ordinary step normally but, on reaching the destructive one, only confirms its target is
still findable — it never clicks/submits it. The response names exactly what it held back
("Dry run: 1 committing step skipped — Click 'Delete invoice'"), and the user decides whether to
re-run for real. See `docs/TRD.md` §9.7.

---

## 12. Execution with Recovery

Before step 0, the runtime runs an **advisory pre-execution drift check** (`runtime/drift.js`):
it looks for the pack's recorded structural landmarks on the live page and, if most are gone
(a redesign signal), emits a `drift_detected` telemetry event that surfaces on the vendor
dashboard's `/drift` queue. This **never blocks** — execution proceeds straight into the per-step
loop below and normal recovery still applies. See TRD §9.7.

```mermaid
flowchart TD
    A[executeStep for step N] --> B[resolveStep: IdentityBundle over live DOM]
    B --> C{Tier A: timing and obstruction — same element, never a guess}
    C -->|Resolved| D[withLocator: perform action]
    C -->|Fail| F{Recovery ceiling ≥ 3?}
    F -->|No — Build Studio| N[Deterministic failure: report, no agent handoff]
    F -->|Yes — Claude/MCP| G0[Tier B armed round: ranked digest + screenshots + recorded context]
    G0 --> J[Claude resumes: execute_skill resume_from + step_overrides candidate_index/selector]
    J --> P{Park still live and fingerprint matches?}
    P -->|No| Q[Refuse resume: ask agent to restart the skill]
    P -->|Yes| K[Adopt parked page; resolve nominated index → derived selector; validate against fingerprint]
    K --> R{Unique match above margin?}
    R -->|No| S{Page fingerprint unchanged across 2 consecutive rounds?}
    S -->|Yes| T[Stagnation stop: fail deterministically, no further paid rounds]
    S -->|No| G0
    R -->|Yes| D
    D --> L[verifyStep: check the step's post-condition assertions]
    L -->|All required pass| M[tracker.emit tier_ok + continue]
    L -->|Required fails| G0
```

**Two behavioural tiers, not four.** Tier A is in-process and free: exception ladder, a11y re-probe,
transient retry, re-hover, dialog-scope. It may change *when* or *where* it looks; it never settles
for a different element — with one qualification: the **a11y re-probe** does re-resolve by
accessible name, so it can land on a different node (through the same fingerprint scoring and
uniqueness margin as primary resolution). That distinction is what gates a destructive step: it
runs every Tier A stage EXCEPT the a11y re-probe. See "Destructive steps get Tier A minus
re-identification" below. Tier B is one armed agent payload — ranked digest plus screenshots plus
recording-time context — not a text-only round followed by a later vision round. A failed step gets
at most two Tier B rounds; round two carries what round one nominated. The runtime never calls
Claude Desktop; each round is a *response* the client answers with its own `execute_skill` resume.
Studio sandbox sets `CONXA_MAX_RECOVERY_TIER=2` so a step that survives Tier A fails deterministically.
See `docs/TRD.md` §10.1.

**Destructive steps get Tier A minus re-identification (PROD-3, revised 2026-09-02).** The line for
an irreversible step (pay / delete / submit — `identity_bundle.destructive`, set at compile time) is
*re-identification*, not tier. It may retry the **same** element under different timing or a
narrower scope; it may never be re-resolved to a different one:

| Tier A stage | re-tries | destructive step |
|---|---|---|
| L1 exception ladder | `primarySelector` after one targeted remedy | runs |
| a11y re-probe | a node matched by **accessible name** | **skipped** — can land elsewhere |
| transient (+250 ms) | `primarySelector` | runs |
| re-hover → retry | `primarySelector` | runs |
| dialog scope | `primarySelector`, scoped into a dialog | runs |

This replaced a blanket halt before all of Layer 2. That halt was written when Layer 2 still
contained a fallback-selector walk and a fuzzy text match — both picked a different element by text
affinity and clicked `.first()` with no uniqueness gate. Once those were deleted, blocking the
remaining same-selector stages while still allowing L1's identical same-selector retry was an
accident of where the gate sat, not a safety property. Double-dispatch protection is unaffected and
orthogonal: the EXEC-24 action guard still refuses any re-dispatch whose first attempt may already
have landed.

An unhealed destructive step is still terminal — `run.js` marks it `destructiveHalt` regardless of
which stage it stopped at, so `server.js`'s `parkable` predicate keeps it out of the agent park. A
`step_overrides` candidate pick is re-identification by another name.

**Validated closing edge:** the agent's `step_overrides` pick (a `candidate_index` nomination from
the ranked digest, or an explicit selector) is never applied blind. A nominated index is resolved to
a derived selector captured when the digest was built; it is then scored against the step's recorded
fingerprint the same way `resolver.js` scores a compiled signal — a unique match is accepted, a
multi-match must clear the uniqueness margin, and a no-match/ambiguous pick is rejected back into a
fresh recovery request rather than silently acting on the first match. Separately, the parked page
itself is only trusted if a cheap page-state fingerprint (url + interactive-element count + a
body-text hash) still matches what was captured at park time — a page that has drifted while the
agent was reasoning is discarded, and the resume is refused outright rather than silently continuing
mid-plan on a fresh, different page. And if the page stops changing entirely across consecutive
recovery rounds, the stagnation hard cap refuses to spend further tokens. See `docs/TRD.md` §10.1.

**Re-verified recovery:** a "resolved" in Tier A above isn't the end of the story for a
consequential step — every remedy that re-runs the action re-checks the post-condition
(`verifyStep`) before being counted as recovered (`runtime/run.js` `recoverWithSelector`). A
verify-fail skips the exception-ladder single-remedy retry (re-running the same action against the
same, already-checked DOM can't fix it) and continues the rest of Tier A (a11y / re-hover / dialog).
See `docs/TRD.md` §9.5/§10.4 for the assertion vocabulary and the re-verify wiring.

---

## 12a. Scheduled Execution (Standalone Runner, PROD-5)

Skills can fire on a schedule with **no chat application open**. Schedules live only on the
customer machine (Horizon-2 doctrine — the cloud holds neither schedules nor work items); the
scheduler daemon drives the same runtime engine a chat host would. Full architecture:
`docs/TRD.md` §4.8.

**Creating a schedule (two doors, one store):**

```mermaid
flowchart TD
    A[User: run this every weekday at 6am] --> B{Where?}
    B -->|Chat app open| C[Agent calls create_schedule]
    C --> D[server.js validates skill + cron,<br/>encrypts inputs, writes local record]
    B -->|No chat needed| E[schedule add --skill S --cron 0 6 * * 1-5]
    E --> D
    D --> F[next_run_at computed - done]
```

**Firing (the part that works while you sleep):**

```mermaid
flowchart TD
    A[Autostart .lnk at sign-in] --> B[conxa-runtime.exe schedule daemon<br/>tray icon appears]
    B --> C[Tick every 30s]
    C --> D{Due slot?}
    D -->|none| C
    D -->|due within grace| E[Spawn engine if idle<br/>execute_skill watch:false _trigger:scheduled]
    D -->|past grace| F[Record ONE skip,<br/>fast-forward to next firable slot]
    D -->|engine full| G[Defer - retry next tick]
    E --> H[Engine: auth pre-flight, platform lock,<br/>run steps, recovery cascade - all unchanged]
    H --> I[Record last_run + advance slot bookkeeping]
    G --> D
    F --> D
```

Guarantees worth knowing as a user:

- Two schedules touching the **same target app never run at the same time** — even when one was
  started from a chat and the other by the scheduler (cross-process file locks). Different apps
  run fully in parallel, up to five at once.
- Missed slots after the runner was off are caught up **only within `catchup_grace_minutes`**
  (default 60) — an always-on runner waking after a week skips instead of burst-firing.
- The tray icon can pause/resume everything, run a schedule now, or quit; every click becomes a
  command file the daemon consumes — the tray is view-only by construction.
- `runner setup` registers autostart and prints the power settings a runner machine wants
  (never sleep on AC); `runner doctor` health-checks the whole chain.

---

## 13. Skill Pack Update (Company Side)

```mermaid
flowchart TD
    A[Company re-records or edits a workflow] --> B[Compile new version]
    B --> C[Build workflow with new version string]
    C --> D[Build Studio: publish only]
    D --> E[POST /api/v1/workflows/publish]
    E --> F["Cloud writes this skill's immutable snapshot + version row, status=ready"]
    F --> G["Cloud admin reviews in Skill Packages → Group → Workflow, clicks Release"]
    G --> H["Cloud: mutable mirror + component_versions updated; stable channel moves — see §8.1a"]
    H --> I[Customer runtimes detect version change on next sync]
    I --> J[Delta delivered, files updated atomically]
    J --> K[New skill version active on next execution]
```

**No re-installer needed** for content-only updates — once released, the runtime's delta sync
handles delivery automatically. **Publishing alone never reaches step H** — see §8.1/§8.1a for why
that split exists and who does each half.

---

## 14. Skill Sync (Runtime Side)

```mermaid
flowchart TD
    A[Runtime cold start or refresh_skills tool call] --> B[Iterate skill-packs/ directories]
    B --> C[Read pack.json — get sync_endpoint; read each skill's OWN version from its current/version.json]
    C --> D[Get company token from keytar]
    D --> E["GET {sync_endpoint}?since={JSON map of skill:version}"]
    E --> F{Per-skill delta response}
    F -->|all skills no_change| G[Up to date — skip]
    F -->|some skills action=update| H[Download all changed skills' files in parallel first]
    H --> I[For each changed skill:]
    I --> J[Write files to skill-packs/co/skill/<version>/, SHA-256 verify each]
    J --> K[version_manager.activate — flip that skill's current junction, prune old versions]
    K --> L{Activation OK?}
    L -->|Yes| M[Add to activated list; clear that skill's last_sync_errors entry]
    L -->|No| N["Discard the partial version dir — that skill's current is untouched; record last_sync_errors[skill]={code, at}"]
    M --> O[Update pack.json last_synced + last_sync_errors]
    N --> O
    O --> P[Reload skill index]
    P --> Q["Next phone-home reports last_sync_errors alongside skill_versions — feeds Cloud's Deployment dashboard 'failed' status (§8.1a)"]
```

Each skill is compared and activated **independently** — republishing one skill never redownloads or re-touches the others (see TRD.md §5.8). A checksum mismatch or a download/activation failure for one skill is recorded (`code`: `checksum_mismatch` | `download_failed` | `activation_failed`) and cleared the moment that skill next activates successfully — it never blocks or delays any other skill's sync.

---

## 15. Runtime Self-Update

```mermaid
flowchart TD
    A[Runtime cold start — bootstrap.js, before the app layer is loaded] --> B[Fetch GET /api/v1/manifest.json — no local TTL, every launch fetches]
    B --> C[Verify Ed25519 signature against baked-in public key]
    C -->|Invalid| D[Discard — fall back to last verified cache, or skip check]
    C -->|Valid| E[decideUpdate conxa_app: semver + min_host floor + minimum_versions floor + rollout bucket]

    E -->|update decided| L[Download zip on a tight budget — 2 retries x 5s, launch-blocking]
    L --> M[SHA-256 verify, extract to conxa-app/<version>/, validate server.js present]
    M --> N[version_manager.activate — flip conxa-app/current, prune old versions]
    N --> O[Live on THIS launch — server.js has not been require'd yet]
    E -->|no update, or any failure| P[Fall through — current junction left untouched]

    O --> Q[require conxa-app/current/server.js]
    P --> Q
    Q --> R[startupSync: decideUpdate conxa_runtime, reusing the manifest bootstrap already fetched]

    R -->|update decided| F[Download exe + keytar.node into conxa-runtime/<version>/]
    F --> G[SHA-256 verify each file]
    G --> H[Spawn new exe --selfcheck with its own CONXA_DIR]
    H -->|Fails| I[Abort — current untouched, old host keeps running]
    H -->|Passes| J[version_manager.activate — flip conxa-runtime/current, prune old versions]
    J --> K[Takes effect on the NEXT process spawn — a process cannot replace its own running binary]
```

Because each new version lands in its own directory rather than overwriting whatever file the *currently running* process loaded from, activation never needs to wait for a "safe restart" — there's nothing running that could be disrupted by it.

The two legs are timed differently on purpose. The app layer is checked *before* anything loads it, so a new app version takes effect on the same launch that downloaded it — which is why its download budget is deliberately small and every failure is swallowed rather than surfaced. The host exe can't possibly apply until the next spawn, so it keeps a generous retry budget and runs in the background alongside skill sync. See `docs/TRD.md` §5.7.

---

## 16. Failure Recovery (End User)

```mermaid
flowchart TD
    A[Skill execution fails at step N] --> B{Recovery tiers exhausted?}
    B -->|No| C[Recovery attempted - see §13]
    B -->|Yes| D[Runtime sends wf_fail telemetry event]
    D --> E[Cloud records failure: fsi=step N, fc=error code]
    E --> F[Runtime returns failure result to Claude]
    F --> G[Claude reports to user: what failed + why]
    G --> H{User action}
    H --> I[Fix manually then say continue] --> J[execute_skill with resume_from=N]
    H --> K[Cancel] --> L[Execution state cleared]
    H --> M[Ask Claude for help] --> N[Claude diagnoses + suggests action]
```

**Execution state:** `data/executions/{id}/checkpoint.json` records the last successfully completed step. On resume, execution starts from `resume_from` step index with the same browser session if still open.

---

## 17. Entitlement Gates (Trial, Machines, Distribution, BYOK)

Added 2026-08-08 for the capability ladder (`docs/PRD.md` §11, `docs/TRD.md` §13.5). These gates sit in
front of steps 6 (Pipeline & Compilation) and 9 (Build Installer & Publish) above — they don't replace
those flows, they can block entry into them.

```mermaid
flowchart TD
    A[Build Studio calls a cloud-gated action] --> B{Which action?}
    B -->|Compile / LLM proxy call| C[Send X-Conxa-Machine header]
    C --> D{Known machine, or under machines limit?}
    D -->|No, new device at limit| E[402 machine_limit_exceeded]
    D -->|Yes| F{Free plan, trial expired?}
    F -->|Yes| G[402 trial_expired]
    F -->|No| H[Proceed: compile reserve / LLM proxy call]
    H --> I{compile_pool = premium and BYOK configured?}
    I -->|Yes| J[Route to workspace's Azure OpenAI deployment]
    I -->|No| K[Route to shared pool: free, starter, or pro tier]

    B -->|Installer upload| L{distribution=external requested?}
    L -->|Yes| M{Plan distribution = external?}
    M -->|No| N[402 distribution_not_permitted]
    M -->|Yes| O{white_label requested?}
    L -->|No| O
    O -->|Yes| P{Plan white_label = true?}
    P -->|No| Q[402 white_label_not_permitted]
    P -->|Yes| R[Upload accepted, branded]
    O -->|No| S[Upload accepted, Conxa-branded]
    R --> AE{filename given explicitly?}
    S --> AE
    AE -->|Yes| AF[Use given filename]
    AE -->|No, plan = free| AG[Random unbranded filename]
    AE -->|No, plan != free| AH{Installer domain set?}
    AH -->|Yes| AI[Use domain-based filename]
    AH -->|No| AJ["Fallback: slug-Workflow-Setup.exe"]

    B -->|Dashboard / audit / drift route| T{Workspace ops_tier vs. route's requirement}
    T -->|Below requirement| U[403 ops_tier_required]
    T -->|Meets requirement| V[Route returns data]

    B -->|Build installer, before staging icon| AK{logo_path supplied?}
    AK -->|No| AL[Build without custom icon]
    AK -->|Yes| AM{Plan = free, or entitlements check fails?}
    AM -->|Yes| AL
    AM -->|No| AN[Build with supplied icon]
```

**First-time machine registration.** A brand-new device registers itself on its first gated call — there
is no separate "register this machine" step in the Studio UI. Settings shows the resulting device list
(`GET /entitlements/machines`) and lets an admin revoke one (`POST /entitlements/machines/revoke`); a
revoked machine re-enters through the limit check on its next call, it doesn't silently reappear.

**Trial banner.** While `trial_expired` is false but `trial_ends_at` is set, Studio and the dashboard
show a countdown; once expired, the same building actions above 402, with a plain-language upgrade
prompt (`docs/UI-UX-Brief.md`).

**BYOK setup** (Enterprise only): Settings → configure Azure OpenAI endpoint/deployment/API key via
`PUT /api/v1/workspace/llm-key`. Once configured, every subsequent compile and LLM proxy call for that
workspace routes to the customer's own deployment instead of the shared pool — silently, with no
per-call toggle. `GET` on the same endpoint never returns the key, only whether one is configured.

**Plan-aware installer naming and icon**, added 2026-08-09 (`docs/TRD.md` §13.6, `docs/Backend-Schema.md`
§5.1c): a Free-tier installer that predates this feature would run and update on any machine — an
earlier same-day attempt at hard-locking that (machine-hash stamped into `pack.json`, checked by the
runtime and the delta-sync endpoint) was reverted for blocking legitimate reinstalls, so no such lock
exists today (see `TODO.md` CLOUD-3, reopened). What ships instead is cosmetic, not restrictive: Free
gets a randomly-named, unbranded `.exe` with no custom icon; paid plans get a filename derived from a
workspace-set (but not yet ownership-verified — see `TODO.md` PROD-6) installer domain, and may embed a
custom icon.

---

## Flow Summary

| Flow | Trigger | Systems Involved | Duration |
|---|---|---|---|
| Onboarding | First Build Studio launch | Build Studio, Cloud | ~5 min |
| Login | User clicks Sign In | Build Studio, Clerk | <30s |
| Record auth | Workflow setup | Build Studio, Target website | 2–5 min |
| Record workflow | Workflow setup | Build Studio, Target website | 5–30 min |
| Compile | After recording | Build Studio, Cloud LLM proxy | 1–10 min |
| Build installer | After compile | Build Studio, Cloud | 1–5 min |
| Customer install | .exe runs | Runtime, Claude Desktop | 2–5 min |
| Skill execution | Claude tool call | Runtime, Target website | 10s–5 min |
| Recovery | Step failure | Runtime, Cloud (LLM at T3+) | +2–30s |
| Skill update | Cloud admin releases a "ready" version | Build Studio (publish), Cloud (release), Runtime (next sync) | <15s sync after release |
| Runtime update | Cold start check | Runtime, Cloud | Background |
| Entitlement gate | Every compile/LLM/installer call | Build Studio, Cloud | <200ms |
