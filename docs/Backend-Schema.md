# Backend Schema Document

**Status:** Current as of 2026-07-04
**Scope:** All data models, storage architecture, and API contracts

---

## Table of Contents

1. [Storage Architecture](#1-storage-architecture)
2. [Core Data Models](#2-core-data-models)
3. [Skill Package Schema](#3-skill-package-schema)
4. [Tracking / Telemetry Schema](#4-tracking--telemetry-schema)
5. [API Contracts](#5-api-contracts)
6. [Entity Relationship Diagrams](#6-entity-relationship-diagrams)
7. [KV Namespace Map](#7-kv-namespace-map)
8. [File Storage Map](#8-file-storage-map)
9. [Multi-Tenancy Design](#9-multi-tenancy-design)
10. [Security Considerations](#10-security-considerations)

---

## 1. Storage Architecture

Conxa uses a **dual-mode key-value store** abstracted in `packages/conxa-core/conxa_core/db.py`.

### 1.1 PostgreSQL Mode (Production)

Activated when `SKILL_DATABASE_URL` is set. Required in production (`SKILL_AUTH_REQUIRED=true`).

```sql
CREATE TABLE IF NOT EXISTS kv_store (
    namespace  TEXT        NOT NULL,
    key        TEXT        NOT NULL,
    data       JSONB       NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
```

**Access patterns:**
- `db_get(namespace, key)` → JSONB data or None
- `db_set(namespace, key, value)` → upsert
- `db_append(namespace, key, items)` → append items to JSON array in `data`
- `db_list_kv(namespace)` → all (key, data) pairs in namespace

### 1.2 Filesystem Mode (Local Development)

Activated when `SKILL_DATABASE_URL` is not set.

```
data/kv/{namespace}/{sha256(key)}.json
```

Key is hashed to SHA-256 to produce a filesystem-safe filename (colons, slashes, etc. are invalid on Windows).

**Fallback:** Legacy paths used pre-hashing are also checked for backward compatibility.

### 1.3 Additional Storage (Non-KV)

Large or time-series data lives in flat files, not the KV store:

| Type | Path | Format |
|---|---|---|
| Raw recorded events | `data/sessions/{id}/events.jsonl` | JSONL (append-only) |
| Per-tab recording videos | `data/sessions/{id}/recording.webm` (tab_0), `recording-{tab_id}.webm` (others), `videos.json` (`{tab_id: {file, start_wall_ms}}`) | WebM + JSON map |
| Compiled skills | `data/skills/{id}/skill.json` | JSON (SkillPackage) |
| Skill screenshots | `data/sessions/{id}/screenshots/` | PNG |
| Step thumbnails | `data/skills/{id}/assets/` | PNG |
| Reviewer edit log | `data/skills/{id}/edits.jsonl` | JSONL (§3.10) |
| Human Review Copilot failure evidence (BUILD-26) | `{CONXA_DATA_DIR}/runs/{run_id}/_evidence/{evidence.json, failure.jpg, pre_step.jpg?}` — the Studio test sandbox's own data dir, not `data/` above | JSON + JPEG |
| Human Review Copilot observed overlays (BUILD-26 stage f) | `{CONXA_DATA_DIR}/runs/{run_id}/_evidence/overlays.jsonl` — written by `runtime/app/overlay_capture.js` on ANY run that intercepted a target, including one that recovered and passed (unlike `evidence.json` above, which is failure-only) | JSONL |
| App maps (Studio only) | `data/app_graphs/{workspace}/{host}/contrib/{skill_id}.json` (one per workflow per host) and `graph.json` (their fold) — see §3.4l | JSON |
| Published skill packs | `data/skill-packs/{co}/` | Directory tree |
| Installer binaries | `data/installers/{co}/installer.exe` | Binary |
| Installer metadata | `data/installers/{co}/meta.json` | JSON |
| Blob store (planned) | External (BLOB_READ_WRITE_TOKEN) | Binary |

### 1.4 Runtime-Local Scheduler Store (PROD-5, local-only)

Schedules for the standalone runner (`docs/TRD.md` §4.8) live **only on the customer machine**
under `<CONXA_DATA_DIR>/scheduler/schedules/<sch_id>.json` — one file per schedule, atomic
tmp+rename writes. They are never synced to or read from the cloud; the Horizon-2 doctrine
(`docs/PRD.md` §14.5) keeps schedules and work items off Conxa infrastructure.

```json
{
  "id": "sch_cc826311bf02",
  "version": 1,
  "name": "Weekday reports",
  "slug": "weekly-sales-report",
  "workspace_id": "acme",
  "cron": "0 6 * * 1-5",
  "enabled": true,
  "catchup_grace_minutes": 60,
  "inputs_enc": { "iv": "…", "tag": "…", "data": "…" },
  "created_at": "2026-08-26T06:00:00.000Z",
  "updated_at": "2026-08-26T06:00:00.000Z",
  "last_slot_fired": "2026-08-26T06:00:00.000Z",
  "next_run_at": "2026-08-27T06:00:00.000Z",
  "last_run": { "at": "…", "slot": "…", "status": "completed|failed|busy|skipped|error",
                "run_id": "r_…", "message": "first 300 chars of engine response" }
}
```

- `inputs_enc` is AES-256-GCM under a dedicated machine keychain entry
  (`conxa-session` service / `scheduler-v1` account) — same scheme as browser sessions.
  Plaintext input values never touch disk and never leave `list()`; the chat-facing
  `list_schedules` tool returns metadata with a `has_inputs` flag instead.
- `last_run.status` values: `completed` / `failed` (engine ran, skill failed) /
  `busy` (engine at concurrency cap — slot stays pending) / `skipped` (past catch-up grace) /
  `error` (scheduler↔engine plumbing failure).
- Related runtime-local files: `scheduler/state.json` (status snapshot for tray/CLI),
  `daemon.lock`, `commands/*.cmd`, daily logs, and `locks/<host>.lock` (cross-process platform
  mutex) — see TRD §4.8.

---

## 2. Core Data Models

All models are Pydantic. Source: `packages/conxa-core/conxa_core/models/workflow.py`

**Cardinality: N Workflows per Workspace : 1 SkillPack per Workspace/Company**

Each workflow holds one login session + one recording. A workspace's SkillPack bundles every signed-off workflow into a single skill package; republishing a second workflow does not create a second installer or duplicate the build. This enables efficient multi-automation packaging within a single company workspace.

### 2.1 Workflow

```python
class Workflow(BaseModel):
    id: str                    # UUID-like, e.g. "wf_abc123"
    slug: str                  # URL-safe workflow name, e.g. "expense-submit"
    name: str                  # Display name
    owner_user_id: str         # Clerk user ID or "local"
    workspace_id: str          # Clerk org ID or "ws_local"
    group_id: str              # Owning WorkflowGroup — see 2.2. Every workflow has exactly one.
    target_url: str            # Entry URL for the target website
    protected_url: str         # URL captured after auth (e.g. dashboard URL)
    protected_url_marker_text: str  # Text that marks the protected area
    status: Literal["needs_auth", "ready", "error"]  # Derived from the owning group's app auth readiness
    # Single recording per workflow (inlined — no nested list).
    session_id: str | None     # Recording session ID for this workflow
    recorded_at: float | None  # Unix timestamp
    recording_status: Literal["recorded", "compiled", "error"] | None
    recording_duration_seconds: float | None  # Last event ts minus first, set at stop_recording — feeds the Cloud ROI baseline (§5.7a)
    skill_id: str | None       # "skill_{session_id}" after compilation
    edited_at: float | None    # Last edit timestamp
    last_test_at: float | None
    last_test_status: Literal["passed", "failed", "never"]
    last_test_error: str | None
    last_test_run_id: str | None  # BUILD-26 (a2) — points the Copilot's evidence bundle at
                                   # runs/{run_id}/_evidence/; set regardless of pass/fail/error,
                                   # not cleared by an edit (unlike last_test_status/error above)
    last_test_inputs: dict     # Inputs used in last test
    signed_off: bool           # Human review complete
    compile_status: Literal["ok", "review_needed", "failed"] | None
    compile_min_confidence: float | None
    compile_steps_with_warnings: int | None
    created_at: float          # Unix timestamp
    updated_at: float
```

**Status transitions:**
```
needs_auth → ready (after auth recording)
ready → error (auth lost or auth re-recording failed)
ready → (any recording_status) (during workflow recording/compilation)
```

### 2.2 WorkflowGroup / GroupApp

A business-domain folder ("Sales") that owns both a set of Workflows and the
applications those workflows sign in to. Auth is captured **once per app, at
the group level** — this replaced the old per-workflow `WorkflowAuth` field
(auth was previously captured once per workflow; now every workflow in a
group shares the group's app sessions). See `docs/TRD.md` §5.3 for the full
setup/recording/execution flow.

```python
class GroupApp(BaseModel):
    id: str                    # Stable slug, e.g. "salesforce"
    name: str
    login_url: str
    success_url: str           # Reaching this host = authenticated; "" = unset. May end in a literal
                                # "{}" wildcard ("anything after this"). Its host is also what maps a
                                # workflow / mid-run login wall to this app, so it must be the APP's host.
    captured_at: float | None  # Unix timestamp, None until authenticated
    storage_state_path: str    # Absolute path to this app's captured session (local only)
    last_error: str            # Most recent capture failure, if any
    check_note: str            # why the last probe was inconclusive ("" if it reached a verdict); never blocks recording
    checked_at: float | None   # Unix timestamp of the last actual probe verdict (any
                                # check_app_session_sync call), None if never probed since
                                # capture. Distinct from captured_at ("a session file
                                # exists") — group_auth_status derives a `verified` flag
                                # from this (set + within a 600s TTL), so a `ready` badge
                                # doesn't silently mean "never actually tested."
    detect_warning: str        # Advisory. "" when auth_definition is set (below) — a learned
                                # definition detects sign-in on its own, success_url or not. Otherwise
                                # set when the last sign-in was saved without the recorder reaching
                                # success_url (closed by hand, or none set) — the runtime's generic
                                # ladder then has less to go on. Never affects `state`.
    auth_definition: dict | None  # Learned at Connect's Done (P0: Application Authentication
                                # Recording) — a contrast between a signed-in and a genuine
                                # signed-out observation of `login_url`, proven by a 3-way self-test
                                # before being saved. None for an app connected before this feature,
                                # or whose self-test never passed (the runtime falls back to its
                                # generic detection ladder for that app). See docs/TRD.md §5.3
                                # ("Learned per-app authentication definitions") and
                                # conxa_compile.auth_learning's module docstring for the shape:
                                # {version, probe_url, signed_out: {final_path, markers,
                                # password_box}, signed_in: {final_path, endpoints}, session_keys,
                                # journey_hosts}. Never a cookie value, a response body, or a query
                                # string/fragment — cookie *names* and method+path+status shapes only.

class WorkflowGroup(BaseModel):
    id: str
    slug: str
    name: str                  # "Default" is the migration target for ungrouped workflows
    workspace_id: str
    apps: list[GroupApp]
    created_at: float
    updated_at: float
```

Storage: `data/groups/{group_id}.json` (dual DB+file, same pattern as
Workflow) via `conxa_core/storage/group_store.py`; each app's captured
session lives at `data/groups/{group_id}/auth/{app_id}.json`.

`captured_at`/`storage_state_path` aren't only set by the explicit Connect
flow (`cmd_finish_group_app_auth`) — every workflow recording that uses the
app also refreshes them. `handlers/session.py::_refresh_group_app_sessions`
runs after a recording stops or is cancelled, splits the session the
recording ended with back into each app's own file (scoped to the cookie
domains/origins that app already owned), and re-saves it via
`set_group_app_auth` — so a session stays "captured" as long as it's still
being used, not just from the moment it was first connected. See
`docs/TRD.md` §5.3 ("Per-workflow recording gate + session write-back").

**Compiled skill-pack layout:** a WorkflowGroup's `id` also decides *where a
skill's files live on disk*, but the raw `id` (an opaque UUID) is never what
appears in the path — `pack.json` carries a `skill_groups` field
(`{skill_slug: group_dir}`, distinct from the `groups` array above — `groups`
is auth-app metadata, `skill_groups` is the path index) where `group_dir` is
the group's stable, readable slug (`{name}-{id[:8]}`, e.g. `sales-257c9ff0` —
the same scheme `group_store.create_group` uses for its own local slug) and
each skill is written to `skill-packs/{company}/{group_dir}/{skill_slug}/`
(the sentinel `"_default"` when a workflow's `group_id` is empty). Build
Studio's local build resolves this from the group's own `slug` field
(`skill_package_builder.py`'s `groups_payload`); the cloud's release fold-in
(`release_routes.py::_skill_dir_group`) derives the same shape from the
`skillpack_known_groups` registry, falling back to the raw `group_id` only if
the group's name isn't known yet. Auth resolution is untouched by this —
each skill's own `manifest.json` still carries the raw `group_id` for
matching against `groups[].id`. See `docs/TRD.md` §5.3 and §11.1 for the full
on-disk layout and delta-sync wire format.

Each skill's own `manifest.json` also carries `"unclaimed_hosts": [hostname, ...]` **only when non-empty** —
hostnames the recording visited that no `GroupApp` of its group covers (`group_store.unclaimed_hosts`,
AUTH-1; see `docs/TRD.md` §5.3 "Unclaimed hosts"). Advisory only: the runtime warns, it never gates on it.

A skill's `manifest.json` carries no per-skill app list. The runtime's group-auth gate
(`runtime/app/browser.js::getGroupAuthContext`) requires **every** `GroupApp` of the skill's
group (from `pack.json`'s `groups`) to be signed in before running it, and seeds the merged
session from every app in the group that has a saved session. (`required_apps` was removed
2026-09-25, AUTH-14 - see `docs/TRD.md` §5.3 "A group is the unit of sign-in"; an older
manifest's copy is ignored.)

**Platform tags (2026-08-23):** `Workflow.visited_hosts: list[str]` records the
hostnames a saved recording navigated, extracted from session events at
"Save Workflow Now" (`cmd_stop_recording`) — before any compile; a discarded or
empty recording clears it (`clear_recording`). `cmd_get_group` returns each
workflow with `used_apps: [{id, name}]` — every group app whose
`login_url`/`success_url` hostname matches the workflow's start URLs **or** its
`visited_hosts`, via the same `apps_for_workflow` matcher the
recording gate uses. The group page renders these as platform chips
(e.g. `Render` `Vercel` on one card). At execution time the same union drives
concurrency: the runtime adds every group app's host to the run's
`host_lock` set, so runs sharing any touched platform serialize while
disjoint-platform runs execute in parallel (see `docs/TRD.md` §4.6 and
§5.2a "Platform tags").

### 2.3 SkillPack (Workspace-Level, Shared)

```python
class SkillPack(BaseModel):
    workspace_id: str          # Clerk org ID or "ws_local" (unique key)
    display_name: str = ""     # Display name for UI + installer filename; cosmetic only
    status: Literal["idle", "building", "error"]
    build: SkillPackBuild | None  # Most recent build metadata
    installer: SkillPackInstaller | None
    created_at: float          # Unix timestamp
    updated_at: float
```

**Note:** One workspace has exactly one SkillPack, forever. All workflows in a workspace that are `signed_off=true` compile together into this single skill package. The workspace is the sole identity unit — there is no secondary "company slug" ownership model.

### 2.4 SkillPackBuild

```python
class SkillPackBuild(BaseModel):
    last_built_at: float
    output_path: str           # Path to skill-package/ folder
    version: str               # Semver, e.g. "0.1.0"
```

### 2.5 SkillPackInstaller

```python
class SkillPackInstaller(BaseModel):
    built_at: float
    installer_path: str        # Local path to .exe
    filename: str              # e.g. "acme-corp-Setup.exe" or branded per plan
    version: str               # Installer version
    runtime_version: str       # Version of bundled runtime
    release_notes: str = ""    # User-provided release notes
```

### 2.6 EntitlementUsage

Stored in KV namespace `entitlement_usage`, keyed by `workspace_id:YYYY-MM`.

```python
class EntitlementUsage(TypedDict):
    workspace_id: str
    period: str                         # UTC calendar month, e.g. "2026-06"
    compile_credits_used: int
    compile_input_tokens: int
    compile_output_tokens: int
    compile_requests: int               # proxied compile LLM requests
    human_edit_input_tokens: int
    human_edit_output_tokens: int
    human_edit_requests: int            # proxied Human Edit LLM requests
    created_at: str                     # ISO-8601 UTC
    updated_at: str                     # ISO-8601 UTC
```

### 2.7 CompileReservation

Stored in KV namespace `compile_reservations`, keyed by `reservation_id`.

```python
class CompileReservation(TypedDict):
    reservation_id: str
    workspace_id: str
    period: str
    amount: int                         # currently always 1
    status: Literal["reserved", "committed", "released", "expired"]
    workflow_id: str                    # The workflow being compiled
    session_id: str
    idempotency_key: str
    created_at: str
    updated_at: str
    expires_at: float                   # Unix timestamp
```

Production enforcement uses database transactions plus a Postgres advisory transaction lock. Local development uses the file/KV fallback with a process-local lock.

---

## 3. Skill Package Schema

Source: `packages/conxa-core/conxa_core/models/skill_spec.py`

### 3.0 Contract vs. Executor Boundary (ARCH-3)

Conxa's durable position is owning the governance layer *above* whichever execution
technology actually runs a step — browser replay today, a graduated API connector (PROD-7)
or a computer-use agent later. That only holds if executor assumptions don't quietly leak
into the skill format while the schema is still cheap to change. Every field in `skill_spec.py`
is tagged in code and below as one of:

| Tag | Meaning |
|---|---|
| **contract** | Executor-independent. Describes *what* the step does and *how success is judged* — must survive a swap to a non-browser executor unchanged. |
| **executor** | Browser-replay implementation detail — Playwright selector grammar, DOM/iframe/shadow-DOM structure, hover chains. Free to change per executor backend. |
| **mixed** | The class holds both; sub-fields are tagged individually below. |

| Class | Contract fields | Executor fields |
|---|---|---|
| `SkillMeta` | `id`, `version`, `title`, `created_at`, `source_session_id`, `compiler_policy_version`, `compiler_policy_hash`, `visited_hosts`, `compensation_skill` (PROD-3-DRYRUN) | `required_runtime`, `structural_fingerprint`, `environment` (EXEC-36) |
| `SkillPolicies` | all | — |
| `RecoveryBlock` | `intent`, `final_intent`, `strategies`, `confidence_threshold`, `max_attempts`, `require_diverse_attempts` | `anchors` |
| `Assertion` | all | — |
| `ValidationBlock` | `assertions` | `wait_for`, `success_conditions` (legacy) |
| `DecisionPolicy` | all | — |
| `ElementFingerprint` | all | — |
| `IdentitySignal` | — | all (Playwright grammar) |
| `FrameFingerprint` | — | all |
| `ShadowHost` | — | all |
| `IdentityBundle` | `fingerprint`, `stable_hash`, `destructive` | `signals`, `frame_chain`, `shadow_path`, `compat_fingerprint`, `guid_like_attrs` |
| `HandlerHints` | — | all |
| `EntityBinding` (PROD-3) | `identifier`, `source`, `confirmed` | `container_selector` |
| `SkillStep` | `action`, `intent`, `url`, `value`, `input_binding`, `validation`, `recovery`, `confidence_protocol`, `decision_policy`, `semantic_description`, `phase`, `optional_hint`, `consequence` | `frame`, `tab`, `target`, `identity_bundle`\*, `handler_hints`, `signals`, `state`, `compiled_selectors`, `snapshot_ref`, `snapshot_dom_hash` |
| `SkillStep.branch` | — (mixed today; see below) | — |
| `SkillStep.entity_binding` | — (mixed today; see `EntityBinding` row) | — |
| `SkillStep.for_each` (EXEC-38) | `as`, `max_iterations`, `on_row_error` | `rows.container_selector` XOR `items` |
| `WorkflowIntentGraph` / `WorkflowIntentStep` | all | — |
| `SkillPackage` | `meta`, `inputs`, `policies`, `llm`, `intent_graph`, `compile_report` | — |

\* `identity_bundle` is itself mixed — see the `IdentityBundle` row.

**`SkillStep.branch` (EXEC-1)** is the one place today's schema still leaks executor detail
into what should be contract terms: its `if_present`/`try_dismiss`/`wait_for_one_of` payloads
carry raw selector strings and nested raw-dict steps (§3.4c). New branch primitives should
define the *condition* itself in contract terms (e.g. "an interstitial matching this identity
is present") and keep browser-specific evaluation strictly on the executor side — this is the
gate EXEC-1's schema work must pass before it ships.

**Review rule going forward:** any new field added to `skill_spec.py` must be tagged
`[contract]`/`[executor]`/`[mixed]` in its own comment at the point of introduction, and this
table updated in the same change. A field with no tag is a review-blocking omission, not an
acceptable default.

### 3.1 SkillPackage (top-level)

```python
class SkillPackage(BaseModel):
    meta: SkillMeta
    inputs: list[dict]         # Parameterizable inputs schema
    skills: list[SkillBlock]   # Currently always 1 block per workflow
    policies: SkillPolicies
    llm: dict                  # LLM config hints
    intent_graph: WorkflowIntentGraph
    compile_report: dict       # Required: {status, steps_total, min_confidence, 
                               #            llm_router_stats, steps, degraded?}
                               # `degraded` (2026-09-14): list of {pass, reason, detail} —
                               # present only when an optional pass (second opinion, vision-
                               # anchor prefetch) genuinely failed rather than being cleanly
                               # disabled or finding nothing. Its presence forces `status` to
                               # at least "review_needed", even when every step's own
                               # selector confidence would otherwise read "ok". See
                               # compiler/build.py::_build_compile_report.
```

Each entry in `inputs` is validated against `SkillInputVariable` (`conxa-builder/python/conxa_compile/editor/dto.py`):

```python
class SkillInputVariable(BaseModel):
    id: str                    # letter-led, alnum + underscore — must match {{id}} grammar
    label: str = ""
    type: Literal["text", "select", "date", "multiselect"] = "text"
                               # "date" (2026-08-30) is editor-level sugar over a plain string —
                               # the packaged input row's own `type` stays JSON-Schema "string"
                               # (every MCP client's expectation), with `format: "date"`/
                               # `"date-time"` alongside it telling the agent what shape to send
                               # (skill_package_builder_saved_skill.py). Auto-declared with this
                               # type + the recorded date as `default` for any date_pick step's
                               # binding (workflow_mutations.py; see HandlerHints §3.4b).
                               # "multiselect" (2026-08-31) is select's multi-valued sibling — a
                               # recorded checkbox group ("pick all that apply"). The packaged row
                               # emits JSON-Schema `type: "array"` with an enum'd `items` instead
                               # of `select`'s top-level `enum`; `default` is still one flat
                               # comma-joined string on disk (split back into an array only at
                               # packaging time), matching every other input type's on-disk shape.
    default: str | None = None
    options: list[str] = []    # required (non-empty) when type in {"select", "multiselect"}
    pattern: str | None = None
    sensitive: bool = False    # redacts the value from saved test history; feeds the shipped
                               # bundle's inferred auth type (password/api-key/none)
    optional: bool = False     # the skill may run without this value. A value with `default`
                               # set is always effectively optional regardless of this flag.
```

**Multiple-choice auto-declared inputs** (2026-08-31). A recorded radio/checkbox group, native
`<select>`, or ARIA radiogroup/listbox widget auto-declares as `type: "select"` (or
`"multiselect"` for a checkbox group) with `options` set to every recorded option's LABEL (never
its raw HTML `value`, which is often library-internal and meaningless to an agent) and `default`
set to whatever was picked while recording — see `workflow_mutations.py::_choice_specs` and
`compiler/choice.py`, and `docs/TRD.md`'s multiple-choice entry for the full compile/replay design
this closes (recording a gender radio group used to auto-declare `{{male}}`, named after the
recorded answer, instead of `{{gender}}`, named after the question).

`optional` is excluded from the packaged manifest's `inputs_required` (`skill_package_builder_output.py::_compute_inputs_required`), which is what the runtime's pre-execution gate and the MCP tool's `inputSchema.required` both read (`runtime/server.js`).

**Auto-declared inputs.** Any `{{placeholder}}` present in the execution steps but absent from the declared input list is appended automatically at package time. One is special-cased: **every upload step binds to `file_path`**, regardless of the picker element's label — a recording can only ever capture a file's *name*, never a path, so the real path must arrive as a runtime input. Its description is enriched with the recorded example filename (`"Path to the file to upload (e.g. invoice.pdf)"`) so an agent calling `get_skill_inputs` knows a real on-disk path is expected rather than a filename. The description also states that a **folder path** may be given when the page's upload control accepts more than one file — the runtime expands a directory into every file directly inside it, which is how a batch upload of 20 or 200 documents is driven without enumerating each file. It does not claim how many files the control accepts: that is a property of the live page (`multiple`), so the runtime asks the element at replay time and refuses a folder aimed at a single-file control with a clear message. See `docs/TRD.md` §9.4.

### 3.2 SkillMeta

```python
class SkillMeta(BaseModel):
    id: str                    # "skill_{session_id}"
    version: int               # Monotonically increasing
    title: str                 # Human-readable workflow name
    created_at: str            # ISO timestamp
    source_session_id: str | None
    compiler_policy_version: str
    compiler_policy_hash: str
    structural_fingerprint: dict  # Hash of first 3 steps' landmark selectors
                                  # Used for drift detection
    environment: dict             # EXEC-36: recording browser's locale, timezone, viewport,
                                  # device_pixel_ratio, date_format_sample, platform — captured
                                  # once at session start (recorder/session.py). Compared
                                  # against the replay environment at run start
                                  # (runtime/app/env_match.js) to WARN, never block, on a
                                  # material mismatch. Empty dict = unknown, never a mismatch.
    compensation_skill: str        # PROD-3-DRYRUN layer 5: sibling skill slug the runtime
                                  # OFFERS (never auto-runs) after a mid-run failure that
                                  # happened after a destructive step already executed. Empty
                                  # when no compensation workflow is linked.
    visited_hosts: list[str]      # Every hostname the recording actually navigated to
                                  # (main frame + any tab opened during recording), lowercase,
                                  # deduped. Feeds unclaimed_hosts (§2.2) and the group
                                  # page's platform chips, so a workflow that starts in one
                                  # group app but links into a sibling mid-recording shows
                                  # both. Empty on skills compiled
                                  # before this field existed.
```

### 3.3 SkillStep

```python
class SkillStep(BaseModel):
    action: str | dict         # Action type + params
    intent: str                # "Click the Submit button"
    url: str                   # Expected URL for this step
    frame: dict                # Iframe chain structural marker (url/url_pattern per level)
    tab: dict                  # {id, index, opened_by, opener_tab} — empty means tab_0, the
                                # initial page. Runtime resolves it per step: TRD §9.2
    target: dict               # Raw recorded target element data
    identity_bundle: IdentityBundle          # REQUIRED — single source of element identity (see §3.4a)
    handler_hints: HandlerHints              # hover_chain, virtualization (see §3.4b)
    signals: dict              # Additional DOM signals
    state: dict                # Page state at recording time
    value: Any                 # Input value (may be {{variable}})
    input_binding: str | None  # Input variable name if parameterized
    validation: ValidationBlock
    recovery: RecoveryBlock
    confidence_protocol: dict
    decision_policy: DecisionPolicy
    compiled_selectors: list[str]  # Ranked CSS/XPath selectors (T1 recovery)
    semantic_description: str      # "First Name input in Add Person dialog"
    phase: str                     # login|navigate|act|verify|cleanup, or "" — written by the
                                   # second opinion (§3.9); nothing downstream reads it yet
    snapshot_ref: str              # DOM snapshot blob reference
    snapshot_dom_hash: str         # For cross-compilation cache lookup
    branch: dict                   # Conditional/branch payload — empty for ordinary steps (§3.4c)
    optional_hint: dict | None     # Recorder-observed "might be optional" flag (§3.4d)
    for_each: dict                 # EXEC-38 iteration payload — empty for ordinary steps (§3.4i)
```

> **Single identity object (cutover):** `element_fingerprint` is no longer a top-level
> `SkillStep` field — it lives **inside** `IdentityBundle.fingerprint` (§3.4a). `identity_bundle`
> is required; the runtime resolves the primary target through `identity_bundle.signals` (no legacy
> `compiled_selectors` primary path), and frame roots are driven solely by
> `identity_bundle.frame_chain`. Packs compiled before this change must be recompiled.

### 3.4 ElementFingerprint

The stable element identity used to score DOM candidates at runtime (carried as
`IdentityBundle.fingerprint`):

```python
class ElementFingerprint(BaseModel):
    role: str          # ARIA role
    tag: str           # HTML tag
    inner_text: str    # Visible text (max 120 chars)
    aria_label: str
    name: str          # form field name attribute
    placeholder: str
    label_text: str    # Associated <label> text — a real accessible name ONLY for
                       # input/select/textarea (see accessible-name note below)
    alt: str           # <img> accessible name
    title: str         # generic accessible-name fallback
    data_testid: str   # data-testid attribute (highest stability)
    input_type: str    # for <input> elements
    css_class_tokens: list[str]   # Stable class tokens only
    anchor_phrases: list[str]     # Relational context phrases
    position_hint: dict           # {x: 0.0-1.0, y: 0.0-1.0}
```

**Accessible-name precedence.** The name used for `internal:role=…[name=…]` signals and for
candidate scoring at replay is `aria_label → name → alt → title → inner_text → placeholder`,
with `label_text` appended **only** for form controls (tag or role in
input/select/textarea/textbox/searchbox/combobox/listbox/spinbutton/checkbox/radio). For every
other element `label_text` holds the recorder's last-resort "nearest surrounding text", which
names a neighbour rather than the element — using it produced role selectors that matched
nothing at replay. An element with no accessible name gets no role signal and resolves
structurally instead. Implemented once per side and kept in lockstep:
`identity_bundle.py::_accessible_name`, `resolver.js::scoreCandidate`,
`cascade.js::a11yRecoveryName`. See `docs/TRD.md` §10.3.

### 3.4a IdentityBundle (Final Selector Architecture)

The durability-ranked, orthogonality-deduplicated element identity used by the pure runtime
resolver. Retained alongside `ElementFingerprint` for backward compatibility.

```python
class IdentitySignal(BaseModel):
    engine: str                # testid | role | aria | text | relational | css-id | css-structural | xpath
    selector: str              # Playwright native grammar (internal:role=…, internal:testid=…)
    durability: float          # 0.0–1.0, base_durability(engine) × survival_prior × stability_adj
    orthogonality_class: str   # test-contract | semantic-aria | visible-text | spatial-anchor | structural
    unique_at_compile: bool    # matched exactly 1 node in recorded DOM
    source: str                # compiler | llm | input_bound

class FrameFingerprint(BaseModel):
    signals: list[IdentitySignal]   # durability-ranked per frame level
    url: str
    url_pattern: str

class ShadowHost(BaseModel):
    host: str                  # CSS selector of the shadow host
    mode: str                  # "open" | "closed"

class IdentityBundle(BaseModel):
    signals: list[IdentitySignal]        # durability-ordered, ≤1 per orthogonality class
    fingerprint: ElementFingerprint      # the resolver's scoring oracle (recorded element attrs)
    stable_hash: str                     # SHA256(tag_path + static attrs + AX name), dynamic classes stripped
    frame_chain: list[FrameFingerprint]  # sole frame-resolution source (durability-ranked per level)
    shadow_path: list[ShadowHost]
    compat_fingerprint: str              # app-version indicators
    guid_like_attrs: list[str]
    destructive: bool
```

### 3.4b HandlerHints

```python
class HandlerHints(BaseModel):
    hover_chain: list[IdentitySignal]    # elements to hover before acting (menu reveals)
    virtualized_container: str           # scroll container selector for virtualized rows —
                                          # "" is the common case. Populated at compile time
                                          # (2026-09-12, BUILD-30) by
                                          # compiler/virtualized.py::detect_virtualized_container
                                          # from the recorded ancestor chain: a known
                                          # virtualization-library class marker, an inline
                                          # overflow+fixed-height scroll style, or (corroborating
                                          # only) an aria-rowcount/aria-setsize overcount.
                                          # Read at runtime by
                                          # resolution.js::maybeScrollForVirtualization, which
                                          # also falls back to entity_binding.container_selector
                                          # for a skill compiled before this field existed, and
                                          # to the page's dominant scrollable element for a
                                          # compiled choice/dropdown-kind control with neither —
                                          # see TRD.md §10.5.
    allow_forced_action: bool
    control_kind: str = ""               # "" = dispatch by action type alone (every action but
                                          # the two below); "date_picker" was the first populated
                                          # value (2026-08-30) — see date_picker dict, next.
                                          # "choice" (2026-08-31) is the second — see choice dict,
                                          # below date_picker.
    date_picker: dict = {}               # set only when control_kind == "date_picker" (a custom
                                          # calendar widget's click run collapsed into one
                                          # date_pick step, compiler/date_picker.py): open/grid/
                                          # header/prev/next/cell selectors, cell_attr (which DOM
                                          # attribute the day was parsed from — "" means an
                                          # aria-label/title sentence, so the runtime falls back to
                                          # an exact-day-text match), display_format (best-effort
                                          # "MM/DD/YYYY"-shaped guess), kind ("single"|"range"|
                                          # "datetime"|"text_field" -- text_field: a plain text
                                          # field a date was TYPED into, no control_kind, display_format
                                          # is exact (DD/MM/YYYY, MMM D, YYYY, ...), compiler/
                                          # date_format.py), strategy ("typed_first"|"grid_only"),
                                          # role ("range_start"|"range_end", ranges only),
                                          # year_select/month_select (selectors for a widget's
                                          # native year/month <select> dropdowns — react-datepicker
                                          # showMonthDropdown mode and equivalents, 2026-08-30 —
                                          # driven via selectOption({label}) in place of the
                                          # click-nav loop when present), recorded_value (the
                                          # picked ISO date/datetime — seeds the auto-declared
                                          # input's default, never used at replay). No compile-
                                          # time value_equals is emitted against the field
                                          # (2026-09-01 — the field's display formatting is only
                                          # knowable at replay); the runtime's format-aware
                                          # readback (handlers.js, throws on mismatch) is the
                                          # enforced post-condition instead. See docs/TRD.md
                                          # §7.1's date-picker paragraph.
    choice: dict = {}                    # set only when control_kind == "choice" (a multiple-
                                          # choice control — radio/checkbox group, native <select>,
                                          # ARIA radiogroup/listbox — compiler/choice.py, 2026-08-31):
                                          # kind ("radio"|"checkbox"|"select"|"aria_radio"|
                                          # "aria_listbox"), multi (bool — true only for a checkbox
                                          # group), group_key/group_label (the GROUP's identity —
                                          # never one option's), options (every recorded member as
                                          # {value, label, selector}), recorded_label (single-shot
                                          # kinds) or recorded_values (checkbox groups — the final
                                          # checked set, absolute not relative). See docs/TRD.md's
                                          # multiple-choice paragraph.
```

### 3.4c Conditional / Branch Steps (EXEC-1)

`SkillStep.branch` (`dict`, empty for ordinary linear steps) holds the payload for the three
conditional/branch action kinds — `if_present`, `try_dismiss`, `wait_for_one_of`. See
`docs/TRD.md` §11.1 for runtime execution semantics (best-effort, never enters recovery).

| `action` | `branch` keys | Probe target |
|---|---|---|
| `if_present` | `steps` (nested SkillStep dicts), `timeout_ms` | `SkillStep.target`/`identity_bundle` (the step's own) |
| `try_dismiss` | `candidates` (selector strings), `timeout_ms`, `fallback_escape` | each candidate, in order |
| `wait_for_one_of` | `options` (`[{selector, steps?}]`), `timeout_ms`, `required` | each option's `selector` |

Nested `steps` entries are raw dicts in the same shape as a saved `SkillStep`
(`action`/`target`/`identity_bundle`/`branch`/...) — `skill_package_builder_saved_skill.py`'s
`_saved_step_to_execution_step` recursively serializes each one into the flat runtime step
shape below.

**On-disk `execution.json` shape** (what `runtime/run.js` actually consumes — flat, `type` not
`action`):

```json
{ "type": "if_present", "selector": ".cookie-banner", "timeout_ms": 1500,
  "steps": [ { "type": "click", "selector": "#accept-cookies" } ] }

{ "type": "try_dismiss", "timeout_ms": 1000,
  "candidates": [ "#accept-cookies", ".cookie .accept", "[aria-label='Close']" ] }

{ "type": "wait_for_one_of", "timeout_ms": 8000, "required": true,
  "options": [
    { "selector": "#mfa-code", "steps": [ { "type": "fill", "selector": "#mfa-code", "value": "{{otp}}" } ] },
    { "selector": "#dashboard" }
  ] }
```

A probe may carry `identity_bundle` instead of a bare `selector`; the runtime's `probePresent`
resolves either. See `runtime/test/gate-skill/skill-pack/gate/gate-skill/execution.json` for a
working `if_present` fixture exercised by the CI execution gate.

**Editor authoring (2026-07-10):** `if_present`/`try_dismiss`/`wait_for_one_of` are now
insertable from Human Edit. `if_present`'s nested `steps` body is fully editable — structural
edits (add/remove/reorder) go through `insert_branch_step`/`delete_branch_step`/
`reorder_branch_steps` (`conxa_compile/editor/workflow_mutations.py`), while per-field edits on a
nested step reuse `cmd_patch_step` with an optional `path` parameter (e.g. `"branch.steps[1]"`)
that addresses the nested dict inside `branch.steps` instead of a top-level step. Nested steps
cannot patch `recovery`/`validation` (branch bodies never enter Tier A/B recovery, so those
blocks are meaningless there — enforced by `patch_gate.py::validate_editor_patch`'s
`in_branch_body` flag). `try_dismiss`/`wait_for_one_of` accept a normal `branch` key patch
(candidates/options, quality-gated the same way as any selector) but have no dedicated authoring
UI yet.

### 3.4d Optional-interstitial observation (recording-next-steps.md Priority 2, 2026-07-10)

`SkillStep.optional_hint` (`dict | None`) is the recorder's advisory flag that this step's target
sat inside what looked like an optional interstitial (dialog or cookie/consent banner) during
recording — carried verbatim from the recorded event's `optionality`/`branch_hint` fields
(`RecordedEvent`, `packages/conxa-core/conxa_core/models/events.py`; see `docs/TRD.md` §11.1 for
the detection heuristic). The per-step compile loop never reads it. Two consumers convert it into
a real `try_dismiss` branch (§3.4c), both through the same builder
(`compiler/second_opinion.py::build_try_dismiss_from_hint`) so the two paths cannot produce
different branch shapes: the compiler's second opinion at compile time (§3.9), and a human
confirming in Human Edit for any hint the pass left alone. Observed state is still required in
both cases — nothing invents a hint the recorder never produced; what changed on 2026-09-09 is
that the human confirmation can now come *after* the conversion, in Human Review, instead of
before it.

```python
optional_hint: dict | None   # {"kind": "try_dismiss", "container_signal": "<selector>"} or None
```

- **Set by:** `build.py` carries `RecordedEvent.optionality == "stochastic"`'s `branch_hint` onto
  the compiled step unchanged.
- **Surfaced by:** `StepEditorDTO.optional_hint` (same shape), read-only — Human Edit's
  `ReviewQuestionsDialog` asks an "Optional popup?" question when present.
- **Consumed by (compile time):** the second opinion's `suggest_optional` kind (§3.9) — converts
  the step and clears the hint before the package is ever written.
- **Consumed by (review time):** `POST confirm_optional_interstitial` (`skill_id`, `step_index`),
  for a hint the pass left alone — a structural mutation (same shape as `insert_branch_step`,
  bypasses `patch_gate.py`) that rewrites the step's `action` to `try_dismiss`, applies the shared
  builder's `intent`/`branch`/`recovery` (candidates seeded from the step's own recorded selector
  plus the hint's `container_signal`), and clears `optional_hint`.
- Either way the hint is consumed exactly once, and there is no un-confirm command: reversing a
  conversion means editing the step back by hand.

**Not to be confused with runtime-replay overlay dismissal (EXEC-30).** `optional_hint`/
`try_dismiss` above are compile-time, human-confirmed mechanisms for an interstitial *seen during
recording*. A separate, later mechanism handles an overlay the recording never saw at all: at
replay, `execute_skill`'s `step_overrides["<idx>"].dismiss` (`{ candidate_index }` / `{ selector }`
/ `{ escape: true }`) lets the Tier B agent clear an unrecognized popup before retrying the
recorded step, gated so only a close/cancel/skip-style control is ever clicked. See
`docs/TRD.md` §10.1 for the full mechanism and the `overlay_dismissed`/`overlay_dismiss_rejected`
telemetry codes above.

### 3.4e Multi-Tab Context (TabContext)

`RecordedEvent.tab` (`packages/conxa-core/conxa_core/models/events.py::TabContext`) records which
browser tab produced an event; `SkillStep.tab` (§3.3) is the compiled twin, carried through
verbatim from the event that produced the step. Since 2026-08-23 every tab — including `tab_0` —
compiles to an explicit block, so a return-to-the-initial-tab `tab_switch` marker names its
destination at replay time; only events with no tab stamp (pre-multi-tab recordings) compile to an
empty `tab` field (see `docs/TRD.md` §6.4 for how
the recorder assigns it, §7.1 for compile-time `tab_open`/`tab_switch` marker insertion, §9.1a for
runtime resolution).

```python
class TabContext(BaseModel):
    id: str = "tab_0"
    index: int = 0
    opened_by: Literal["initial", "site", "user"] = "initial"
    opener_tab: str | None = None
    url: str = ""   # editor display only — dropped from the runtime-facing execution.json copy
```

Absent on recordings made before multi-tab support existed — same read-new-fallback-old pattern as
`post_condition` (§3.4d) — so old recordings still validate and old compiled skills replay
identically (every step resolves to the initial page, exactly as before this field existed).

### 3.4f Browser History Navigation (`browser_back` / `browser_forward`, 2026-08-25)

`ActionKind` gained two recorded-only kinds: `browser_back` and `browser_forward`
(`packages/conxa-core/conxa_core/models/events.py`). They capture browser Back/Forward button
presses (and Alt+←/→), which produce no in-page DOM event the bridge could see — the recorder
detects them via per-page CDP navigation-history tracking instead (see `docs/TRD.md` §6.2).

```python
# RecordedEvent.action.value (JSON string):
{"from_url": "https://a.test/detail", "to_url": "https://a.test/list"}
```

- **Recorded by:** `session.py::_drain_nav_history_checks_sync` — synthetic events stamped with
  the tab that fired the navigation.
- **Compiles to:** a real executable `SkillStep` (not a marker): `action=browser_back/
  browser_forward`, `intent=history_back/history_forward`, `url=<to_url>` (informational only),
  `no_recovery_block`, `validation.wait_for={type: url_change, target: <to_url>}`.
- **Replayed as:** `page.goBack()`/`page.goForward()` on the step's resolved tab
  (`runtime/app/handlers.js`) — never rewritten to a URL navigation. Both types are in
  `NAVIGATION_STEP_TYPES`, so the next step waits for page load.
- **Editor:** labels "Browser back"/"Browser forward", category `flow`; not insertable, no
  selector/value fields; url field is not editable.

### 3.4g Manual address-bar navigation (`manual_navigate`, 2026-08-26)

A navigation that is neither Back/Forward nor the natural result of a recorded click/submit (the
user retyped the URL bar or picked a bookmark mid-recording) used to be silently dropped — replay
then had no step for it at all. The recorder now tells the two apart via CDP's
`Page.frameRequestedNavigation` (fires only for page-initiated navigations — link/submit/script,
never address-bar/bookmark/`goto()`; see `docs/TRD.md` §6.2) and emits a `manual_navigate`
synthetic event, same `{from_url, to_url}` value shape as `browser_back`/`browser_forward`.

- **`cause` (2026-09-30).** The value may also carry `"cause": "action"`: the navigation was a SPA route change
  or landed within 5 s of a bridge interaction, i.e. an *effect* of that action. The compiler
  (`navigation_effects.py`) drops such an event and keeps only its destination as the causing action's
  `post_condition.url_delta` -> an advisory `url_pattern` assertion; it never becomes a `navigate` step.
- **Recorded by:** `session.py::_drain_nav_history_checks_sync`, gated on
  `_nav_pending_renderer_initiated` (set by the CDP event, consumed per classification).
- **`ActionKind` member.** `manual_navigate` must be listed in the `ActionKind` Literal
  (`packages/conxa-core/conxa_core/models/events.py`). `RecordedEvent` is validated **twice** on
  the way to a compiled step — `session.py::_finalize_payload_sync` and
  `pipeline/run.py::run_pipeline` — and a kind missing from the Literal raises a
  `ValidationError` that is swallowed into `binding_errors`, so the event never reaches
  `events.jsonl` and the step is silently absent from the editor with no user-visible error.
- **Exempt from keyless dedupe.** Like `browser_back`/`browser_forward`, a `manual_navigate`
  carries an empty element target, so `step_anchors.py::clean_steps`' generic
  same-action-same-key rule would match *any* two consecutive ones and collapse them. All three
  kinds are exempted there; without it, an A→B→C address-bar sequence compiles to a single step.
- **Compiles to:** a real `action=navigate` `SkillStep` — the same step type already used for a
  recording's start page and user-opened tabs (`build.py::_navigate_step`). No runtime change
  was required; `navigate` was already fully executable.
- **Not recorded for:** any navigation that follows a page-initiated request (ordinary link
  clicks/form submits) — those are already covered by the click/submit step itself. Verified
  against real pages: an `<a href>` click reports reason `anchorClick` and a form POST reports
  `formSubmissionPost`, so both are correctly excluded.

### 3.4h Native JS dialogs (`dialog_accept` / `dialog_dismiss`, 2026-08-26; record-order + replay-deadlock fix EXEC-29, 2026-09-04)

```python
# RecordedEvent.action.value / SkillStep.value (JSON string):
{"type": "alert" | "confirm" | "prompt", "message": "I am a JS Prompt", "value": "Conxa prompt"}
```

**Recording — two mechanisms.** The primary path (`recorder/dialog_server.py`) overrides
`window.alert/confirm/prompt` themselves via an init script (`DIALOG_OVERRIDE_SCRIPT_TEMPLATE`),
before the page's own scripts ever run — a synchronous XHR to a local per-session
`DialogSyncServer` blocks the page exactly like a real dialog would, and blocks for as long as
the human takes to answer via the Studio's `alwaysOnTop` modal. This replaced an earlier
CDP-based `page.on("dialog", ...)` interception (`session.py::_on_dialog` /
`_drain_js_dialog_sync`) that raced a real Chromium-internal patience limit on a slow confirm/
prompt; that path is kept only for `beforeunload`, which cannot be overridden as a callable the
way alert/confirm/prompt can. Either path answers past a 120s timeout with a forced empty
accept so a forgotten dialog never wedges the recording. **Out of scope:** `auth_mode` (the
separate login-capture browser) still auto-accepts immediately — its pump loop never drains a
pending dialog.

- **Compiles to:** a marker `SkillStep` (`action=dialog_accept`/`dialog_dismiss`,
  `no_recovery_block`) whose `value` is the JSON above — `build.py`'s `MARKER_ACTIONS` branch
  keeps it, the same way it keeps `download_observed`'s value. A `prompt`'s typed answer is
  replaced with `{{dialog_answer}}` and the step gets `input_binding="dialog_answer"` so an
  agent replaying the skill can answer differently than the human did while recording;
  `alert`/`confirm` and an empty prompt answer stay literal. A second prompt in the same
  workflow dedupes to `dialog_answer_2` via the existing `_deduplicate_input_bindings`.
- **Record-order fix (EXEC-29):** the dialog server answers from its own request thread, while
  the triggering click's own recorded payload only reaches the recorder once the page's JS
  thread unblocks after the dialog closes — the two can land in the raw event stream in either
  order even though their timestamps are unambiguous. A confirm's answer landing directly next
  to a prompt's answer, with no click between them, used to trip `clean_steps`'s
  duplicate-consecutive-action collapse (built for real double-clicks, which carry a target;
  a marker carries none, so any two adjacent markers compared equal) and silently drop the
  second one — the prompt's typed answer, in the field repro that surfaced this.
  `pipeline/run.py::_reorder_by_timestamp` now stable-sorts every recorded event by its own
  `action.timestamp` before any other processing, and `step_anchors.py::clean_steps` no longer
  applies the duplicate-collapse rule to any `MARKER_ACTIONS` step (dialog markers,
  `download_observed`, tab markers, ...) — both defects share this one fix.
- **Replayed as:** `dialog.accept(interpolate(text, inputs))` / `dialog.dismiss()`
  (`runtime/app/handlers.js::answerDialog`, shared by the marker handlers below and the pre-arm
  described next). `_drainDialogQueue`'s wait is `DIALOG_WAIT_TIMEOUT_MS` (120s,
  `run_config.js`), and `verifyStep` (`assertions.js`) skips post-condition verification
  entirely on a step that left a dialog open.
- **Replay-deadlock fix (EXEC-29):** a native dialog blocks the very Playwright call that opens
  it — `click()`/`fill()`/etc. do not resolve until the dialog is answered, which is Chromium/
  CDP behavior, not something Playwright can be told to skip. Waiting for the *following*
  `dialog_accept`/`dialog_dismiss` step to drain `ctx.dialogQueue` — the only mechanism before
  this fix — therefore deadlocks by construction: that step can never run because the step
  before it never returns. `run.js`'s per-step loop now looks one step ahead before dispatch;
  when the next step is a dialog marker, it arms a one-shot `page.once("dialog", ...)` with that
  marker's answer *before* executing the current step, so Chromium's dialog resolves the
  instant it opens and the triggering action returns normally. The marker step's own
  `_drainDialogQueue` path is unchanged and still handles packs compiled before this fix —
  `answerDialog` tolerates being called twice on the same already-resolved dialog. See
  `docs/TRD.md`'s runtime-timeout section for the accompanying guards (recovery refuses to run
  while a dialog is pending; no unbounded `page.evaluate()` on the resolution hot path).
- **Editor:** unchanged labels ("Accepted dialog"/"Dismissed dialog" →
  `editor/describe.py` also surfaces the recorded message and typed answer in the step list).
- **Not covered:** `beforeunload` confirmations (unhandled on both record and replay) and
  dialogs shown inside the auth-capture browser — see `TODO.md` EXEC-25/EXEC-26.

### 3.4j Outcome fork: `outcome_fork` + `dialog_context`

`SkillStep.outcome_fork` (`dict`) holds the known states of an app after an action and a policy for each; empty for ordinary steps. Mechanism: `docs/TRD.md` §11.4.

```python
outcome_fork: dict   # {timeout_ms, unknown_policy, clean, states}
# clean:  {observed: bool, observed_in: [session_id]}
# states: [{ id, kind: "dialog", observed_in: [session_id],
#            signature: {title, button_labels, message_tokens, container_selector},
#            options:   [{ id, label, role: "dismiss"|"consequential"|"neutral", recorded: bool, steps: [saved-step dicts] }],
#            policy:    {mode:"auto", option_id, author_approved, reviewed?}
#                     |  {mode:"by_input", input, suggested?, reviewed?}
#                     |  {mode:"ask_human", suggested?, reviewed?}
#                     |  {mode:"stop", message, reviewed?} }]
```

- `policy.author_approved` is written **only** by `set_fork_policy`; the compiler always writes `false`. A consequential option under `auto` without it pauses at run time.
- `policy.reviewed` marks a state the author has answered (so Clarify never asks again).
- `by_input` declares a `select` input named `policy.input` (options = option labels + `ask`, default `ask`) and sets the fork step's `input_binding`/`value` to it.
- `message_tokens` are value-free words (quoted text and digits stripped); the popup's message never ships.
- The execution form (`execution.json`) is `{type:"outcome_fork", timeout_ms, unknown_policy, clean:{observed}, states:[{id, kind, signature, options:[{id,label,role,steps}], policy}]}` (`skill_package_builder_saved_skill.py::_saved_fork_step`); an option with no runnable steps, or a fork with none, is dropped with a warning. Older runtimes fail loudly on the unknown type — bump `required_runtime` for packs containing one.

`RecordedEvent.dialog_context` (optional; absent on older recordings): `{kind: "dom"|"native", container_signal, role, title, message (≤300, compile-time only), has_fields, buttons: [{tag, role, name, text, testid, id, chosen}] (≤8)}`.

`Workflow.variant_session_ids`: `[{session_id, fork_step_key, recorded_at}]` — extra takes of the same task showing a different outcome. Never the workflow's own recording; cleared when the workflow is re-recorded.

Studio RPCs: `set_fork_policy {skill_id, step_index, state_id, policy}`, `apply_variant {workflow_id, session_id, fork_step_key}`, `start_recording {…, variant_of}`, `flow_test_decision {decision: "fork:<choice>"}`.

### 3.4l App map models (`conxa_core/models/app_graph.py`)

Derived, data-free, Studio-local (cloud sync is a later phase). `Contribution` = what one compiled workflow learned on one host; `AppGraph` = `fold_contributions` over every contribution of that host and workspace. Mechanism and privacy rules: `docs/TRD.md` §11.5.

```
Screen   {id = sha16(host|route), route ("/orders/:id"), title_tokens[<=8], seen (workflows), sources[]}
Action   {id = sha16(from_screen|kind|stable_hash), from_screen, to_screen|null, kind, role,
          name|null (scrubbed label), stable_hash, intent, consequence, input_name|null, seen, sources[]}
Popup    {id, title, button_labels[], message_tokens[], popup_class (decision|dismissable|consent|form),
          container_selector, after_actions[], appeared, absent, options[{label, role, chosen}],
          field_seen, sources[]}
WorkflowPath {skill_id, name, action_ids[]}
Contribution {id = "skill:<skill_id>", app_key, workspace_id, created_at, screens, actions, popups, path}
AppGraph {app_key, workspace_id, schema_version = 1, built_at, screens, actions, popups, paths, contribution_ids}
```

`app_key` is the lowercase hostname without a leading `www.`. A step is filed under the host it **started** on; an action that leaves the host keeps a `to_screen` id that lives in the other host's graph.

`Popup.app_policy` (author's standing answer, only set by `cmd_set_app_popup_policy`): `{"mode": "auto", "option": "<button label>", "author_approved": true}` | `{"mode": "ask_human"}` | `{"mode": "stop", "message"?}` | null. Stored beside the contributions in `policies.json` and applied after every fold.

**`app_knowledge.json`** (per skill, shipped with the code files; optional): `{"schema_version": 1, "apps": {"<host>": {"popups": [{"id", "class", "signature": {"title", "button_labels", "message_tokens", "container_selector"}, "options": [{"label", "role", "chosen"}], "app_policy"}]}}}`, at most 40 popups per host, only for hosts the skill visits. Contains no screens, actions, workflow names or paths. Telemetry: `fork_decided` gains `src: "app_policy"` (state `map:<popup id>`), and `app_obs` reports unplaced popups (see the telemetry table).

**Real-run popup data** (`data/app_graphs/<ws>/<host>/field.json`, Studio only): rows from `GET /api/v1/app-graphs/{app_key}/field` — `{title_tokens[], buttons[], class, count (distinct runs), known, last_seen}`, computed on read from the workspace's own `app_obs` telemetry, ops-tier gated like the drift queue — stored as `[{match_popup_id, popup}]` and applied after every fold: a row matching a covered popup raises its `field_seen`; any other becomes a field-only popup (`sources: ["field"]`, `appeared: 0`).

### 3.4i Iteration: `for_each` (EXEC-38)

`SkillStep.for_each` (`dict`) is the "for each row matching X, do steps A-C" iteration payload —
empty for ordinary steps. Built on the entity-binding machinery (§EntityBinding) already
shipped, not new resolution logic; see `docs/TRD.md` §11.2 for the full mechanism (the
`executeOneStep` extraction that lets a loop body reuse the exact recovery/GATE/VERIFY/dry-run
path every top-level step gets) and its sibling **PROD-3-DRYRUN** section (§10.6a) for the
required dry-run/cap/entity-binding safety mechanisms it depends on.

```python
for_each: dict   # {rows: {container_selector}} XOR {items}, plus as, max_iterations, on_row_error, steps
```

- **`rows.container_selector`** `str` — `[executor]`, a plain CSS selector matching every
  candidate row. Enumerated once, up front, by `runtime/app/resolution.js::enumerateRows` — each
  row's own full trimmed text becomes its identifier. Mutually exclusive with `items` below —
  exactly one row source, never both, never neither, enforced by `_saved_for_each_step` (compiler)
  and `patch_gate.py::_validate_for_each_source` (editor, against the step's effective `for_each`).
- **`items`** `str` — `[contract]`, the name of a runtime input (typically `type: "text"`) whose
  current value drives the loop instead of a DOM scan — e.g. `items: "files"` for a caller-supplied
  comma-separated file list. `runtime/app/resolution.js::splitListInput(raw, cap)` splits on
  commas (or takes an array), trimming/dropping-empty/de-duplicating/capping the same way
  `enumerateRows` does. A body step under this source ordinarily carries no `entity_binding` —
  there is no live row to re-locate.
- **`as`** `str`, default `"row"` — `[contract]`, the name body steps read the current
  iteration's identifier/index under: `{{<as>_id}}` / `{{<as>_index}}`.
- **`max_iterations`** `int` — `[contract]`, **required**. `_saved_for_each_step`
  (`skill_package_builder_saved_skill.py`) drops the step entirely if absent or ≤ 0 — same
  "unfillable step vanishes from execution.json" contract every action uses. Clamped again at
  runtime by `CONXA_MAX_LOOP_ITERATIONS` (default 100).
- **`on_row_error`** `"stop" | "continue"`, default `"stop"` — `[contract]`. `"stop"` propagates a
  row failure as a normal run failure; `"continue"` skips the failing row and proceeds (never
  applies to a cancellation or an `ai_review` pause inside the body — those always propagate).
  **EXEC-40 (2026-09-13):** a `"continue"` loop that finishes with any row failed, or a loop of
  either mode that finds zero rows/items to process, now pushes a message into the run's own
  `warnings` array (`runtime/app/run.js::runForEachStep`) — previously only tracker-only telemetry
  (`for_each_done`), so a `"continue"` loop could complete and report plain success having silently
  skipped work. Surfaces in the runtime's final result text under the same `"Warning:\n..."` block
  every other advisory issue already uses (`server.js`'s `_allWarnings`).
- **`steps`** — nested body, same shape as `branch.steps`. Each body step's own
  `entity_binding.identifier` is author-set to `{{<as>_id}}`, so `resolution.js::entityRoots`'s
  existing narrowing does all per-row scoping — recovery still cannot substitute a different row.

**Compile.** `entity_binding` detection now runs for every step, not only irreversible ones (a
loop body's ordinary steps need row scoping too) — the confirmation *requirement* stays
irreversible-only, but `handlers/workflows.py::_require_confirmed_entity_bindings` recurses into
`for_each.steps` so a destructive body step's binding can't reach a customer install unconfirmed.

**Editor.** Insertable (`action_registry.py`, category `"iteration"`), scaffolded
(`workflow_mutations.py::_new_manual_step`), validated (`patch_gate.py::_validate_for_each_patch`
— `steps` rejected on the parent patch, same reasoning as `if_present`'s nested body — plus
`_validate_for_each_source` enforcing exactly one row source), and surfaced read-only via
`StepEditorDTO.for_each_summary`/`.for_each_steps` (same path-addressed projection pattern as
`branch_summary`/`branch_steps`, IDs like `"{skill_id}:{step_index}.for_each.steps[{j}]"`; the
summary carries `items` alongside `container_selector`). **No dedicated nested-body editor UI
yet** — see `TODO.md`.

**Two compile-time input-declaration defects fixed alongside the `items` source (2026-09-13):**
a for_each loop's own `{{<as>_id}}`/`{{<as>_index}}` used to auto-declare as user-facing inputs
the same as any other `{{var}}` a step references — `upload_binding.py::for_each_loop_variable_names`
now excludes them at every auto-declare site
(`workflow_mutations.py::reconcile_inputs_with_step_values`,
`skill_package_builder_saved_skill.py::_merge_saved_inputs_with_execution_placeholders`), the
same way the `downloaded_file*` family is already excluded. And the BUILD-25 second-opinion pass's
`parameterize_literal` finding could clobber an upload step already auto-bound to
`{{downloaded_file}}`/`{{downloaded_files_dir}}` (`docs/TRD.md`'s "A downloaded file can bind to
a later upload" section) into a hand-typed input instead —
`second_opinion.py::_apply_one` now refuses whenever the step's value already contains any
`{{placeholder}}`, not just when `input_binding` is set. See `docs/TRD.md` §11.2 for the full
account of both.

**One-click "generalize this to a loop" suggestion (EXEC-39, 2026-09-13).**
`compile_report["for_each_suggestions"]` — a new key alongside `second_opinion`/`archived_steps`
— holds `compiler/loop_suggestion.py`'s findings: a fully deterministic (no LLM) proposal to wrap
a single-file download→upload pair into a `for_each` loop, computed after the second-opinion pass
settles. Each entry: `id`, `wrap_start_key`/`wrap_end_key`/`upload_step_key` (step-key addressed,
never index), `template_literal` (the recorded filename), `suggested_input_name`, `as_name`,
`why`, `preview`. Surfaced in `WorkflowResponse.compile_health.for_each_suggestions` (read-time
filtered against a reviewer's prior rejection — see below), so `ForEachSuggestionBanner.tsx` can
show it the moment Human Edit loads. New RPCs (`handlers/copilot.py`):
- **`accept_for_each_suggestion`** `{skill_id, suggestion}` → applies
  `editor/workflow_mutations.py::apply_for_each_loop_suggestion` (one atomic document write: the
  step-range splice into a new `for_each`'s body, the literal→`{{<as>_id}}` templating, the
  upload step's rebind to `{{downloaded_files_dir}}`, the new declared input) and returns the
  same `_skill_response` shape every structural mutation RPC does.
- **`reject_for_each_suggestion`** `{skill_id, suggestion}` → logs a rejection via
  `edit_log.py::append_decision` (`field="for_each_suggestion"`, keyed on `upload_step_key`) —
  changes nothing in the document. `workflow_dto.py::_compile_health` reads this log on every
  request and filters out anything already rejected, not just at the next compile — a "Dismiss"
  must not reappear on a plain page reload.

See `docs/TRD.md` §11.2 for the full mechanism, including a `step_key`-ordinal subtlety
(`download_observed`'s fallback key hashes identically across a workflow, disambiguated only by
occurrence position) that made "clear the whole suggestion list on any apply" the correct choice
over "remove just the applied one."

**BUILD-33 (2026-09-13): `redundant_click_key` (optional).** A suggestion entry may additionally
carry `redundant_click_key` — set when the step immediately before `wrap_start_key`'s original
navigate is a hardcoded click on the recorded filename (same tab, its own recorded fingerprint
text containing the filename verbatim), made redundant once the loop navigates straight to each
item's URL. When present, `wrap_start_key` already points at that click (the detector folds it
into the wrap range), and `accept_for_each_suggestion` removes it from `steps` entirely rather
than wrapping it into the loop body, archiving it into `compile_report["archived_steps"]` under
category `superseded_by_loop_navigate` — same archive shape `flag_noise` uses, so it stays
reversible. This ships alongside a structural fix to `archive_flagged_steps` itself: it now
refuses to archive any step that triggers an observed download or opens a file-chooser upload,
independent of what a `flag_noise` finding said — see `docs/TRD.md` §11.2's BUILD-33 writeup.

**BUILD-34 (2026-09-13): `template_literal` matching is percent-encoding-aware.** Every
comparison of `template_literal` against a step's `url` field — the detector's navigate match, the
apply mutation's defensive re-check, and its actual `{{<as>_id}}` templating — goes through
`compiler/loop_suggestion.py::url_contains_literal`/`replace_url_literal`, which decode the URL's
percent-encoding before matching rather than re-encoding the filename (encoding is ambiguous,
decoding isn't). A raw substring check previously missed any filename containing a URL-reserved
character (`+`, space, non-ASCII, ...) entirely, with no error — the suggestion simply never
appeared. See `docs/TRD.md` §11.2's BUILD-34 writeup.

### 3.4j AI Review Step (EXEC-13)

An author-placed reasoning checkpoint, not a page action — no `target`/`identity_bundle`, and
outside the Tier 1-4 recovery cascade entirely (never a `recovery` block). See `docs/TRD.md` §11.3
for the full park/resume mechanism; this section is the data shape.

**Execution step** (`execution.json`, emitted by `skill_package_builder_saved_skill.py`'s
`ai_review` branch):

```python
{
  "type": "ai_review",
  "prompt": str,                       # required — dropped at build time (with a warning) if blank
  "on_failure": "abort" | "use_default" | "continue",   # default "abort"
  "output_schema": dict | omitted,     # a JSON-Schema-shaped object (required/properties/type/enum
                                        # only — see the hand-rolled validator note below), or
                                        # omitted to accept any answer
  "default_value": Any | omitted,      # present only when on_failure == "use_default"
  "reference_screenshot_ref": str | omitted,   # relative path under the skill's visuals/ dir
  "output_name": str | omitted,        # falls back to `ai_review_output_<step index>` at runtime
}
```

**Editor-side (pre-compile) fields**, top-level siblings of `action` on the saved document —
patched only through `patch_gate.py`'s dedicated `ai_review` branch, which rejects a `recovery`
patch outright and requires `ai_review_default_value` whenever `ai_review_on_failure` is
`"use_default"`:

```python
ai_review_prompt: str
ai_review_output_schema: dict | None
ai_review_on_failure: "abort" | "use_default" | "continue"
ai_review_default_value: Any
ai_review_reference_screenshot_ref: str | None
```

**`execute_skill` gains `review_results: { "<step_index>": <answer> }`** — the resume payload for
a paused `ai_review` step, parallel to `execute_skill`'s `step_overrides` param but never run
through `applyStepOverrides`: the answer is bound straight into `inputs` under the step's
`output_name`. A pause response carries `_meta: {"conxa/ai_review": {step_index, prompt,
output_schema}}` alongside its `content` blocks, for a programmatic (non-agent) caller to answer
without parsing the prose header — see `docs/TRD.md` §11.3 for the Build Studio sandbox's own use
of this.

**Answer validation** (`runtime/app/review_pause.js::validateReviewAnswer`, mirrored in Python by
`handlers/workflows.py::_validate_ai_review_answer` for the Studio sandbox path) is a hand-rolled
structural check against `output_schema` — required fields present, declared `type` matches
(`string`/`boolean`/`number`/`integer`/`object`/`array`), declared `enum` membership — not full
JSON Schema (no `$ref`, no nested `items`/`additionalProperties` validation, no string `format`).
Deliberate: the schema an author writes here is always shallow (a `yes`/`no` + reason, a single
value), and a real JSON Schema validator would be new weight in every customer's runtime bundle for
constraints nobody authors on this surface.

**Safety:** the patch gate refuses to save a destructive step that directly follows an `ai_review`
step (`destructive_step_cannot_directly_follow_ai_review` — the sibling of PROD-3's entity-binding
confirmation gate) — a review answer may gate *whether* a later step runs, never supply *what* a
destructive step acts on.

### 3.4k Hand-Over Step (EXEC-21, hand-over shape)

`ai_review`'s human sibling: a planned pause with no `target`/`identity_bundle`, outside the
Tier 1-4 recovery cascade for the same reasons as §3.4j. See `docs/TRD.md` §11.4 for the full
park/resume mechanism (three resume signals — banner, file drop, loopback HTTP — and why the host
lock is released rather than held for the pause); this section is the data shape.

**Execution step** (`execution.json`, emitted by `skill_package_builder_saved_skill.py`'s
`handover` branch):

```python
{
  "type": "handover",
  "message": str,                      # required — dropped at build time if blank
  "on_failure": "abort" | "continue",  # default "abort" — no "use_default": a hand-over produces
                                        # no answer value to fall back to
  "resume_when": dict | omitted,       # an optional probe spec (same shape wait_for_one_of's
                                        # options use) the resumed page must satisfy before the
                                        # next step runs
  "resume_when_timeout_ms": int | omitted,   # default 10000 when resume_when is set
}
```

**Editor-side (pre-compile) fields.** Unlike `ai_review`, the message shown to the person rides
the step's generic `value` field (the same one `ai_review` uses for its output binding name) —
`VALUE_LABELS["handover"]` labels it "Message shown to the person" — so no dedicated
`handover_message` field exists. Patched only through `patch_gate.py`'s `handover` branch, which
rejects a `recovery` patch outright:

```python
value: str                                   # the message — top-level, not "handover_*"
handover_on_failure: "abort" | "continue"
handover_resume_when: dict | None
handover_resume_when_timeout_ms: int | None  # must be > 0 when present
```

**No new `execute_skill` parameter.** A hand-over produces no structured answer, so unlike
`ai_review`'s `review_results` there is nothing to pass alongside `resume_from` — and in practice a
resume is normally self-driven (the runtime process itself calls `execute_skill` again the moment
a signal fires, per §10.10), not agent-initiated. A pause response carries `_meta: {"conxa/handover":
{step_index, message, run_id, resume_http: {port, token, path} | null, resume_file}}` for a
programmatic caller.

**Not built (deliberate, tracked in `TODO.md`):** a compile-time guard rejecting a `handover` step
authored inside a `for_each` loop body — unwinding the loop on the pause throws away the iteration
cursor, so the runtime propagates the pause rather than treating it as a row failure, but nothing
yet stops one from being authored there in the first place; approve/reject and supply-a-judgement,
EXEC-21's other two shapes.

### 3.5 RecoveryBlock

```python
class RecoveryBlock(BaseModel):
    intent: str                # What this step is trying to do
    final_intent: str          # Refined intent for LLM recovery
    anchors: list[dict]        # Visual anchor points for vision recovery
    strategies: list[str]      # ["semantic match", "position match", "visual match"]
    confidence_threshold: float  # 0.85 default
    max_attempts: int          # 2 default
    require_diverse_attempts: bool
```

### 3.6 Assertion

A verifiable post-action condition, checked independently of whether the action itself threw.
Every consequential action (submit click, destructive confirm, text entry/select) compiles with
exactly one **required** (enforced) assertion — the compiler's deterministic "primary signal
picker" in `build.py:_build_assertions` — plus zero or more advisory ones. `required=False`
assertions only log a warning on failure; a `required=True` failure halts the step and descends
into recovery (`runtime/run.js` `verifyStep`).

```python
class Assertion(BaseModel):
    type: str          # "url_changed" | "url_pattern" | "selector_present" | "selector_absent"
                       # | "text_present" | "text_absent" | "value_equals" | "state_changed"
    target: str        # URL prefix, regex pattern, CSS selector, or text string (empty for state_changed)
    expected: str       # value_equals only — expected field value (interpolated against runtime inputs)
    timeout_ms: int    # 5000 default
    required: bool     # True = halt on failure (and descend into recovery); False = warning only
```

- `url_changed` — the page address must differ from its value before the action. The runtime
  compares against the live pre-action URL; `target` (the recorded before-URL, may be empty) is
  only a fallback when no baseline was captured. To check *where* the page landed, use
  `url_pattern` (regex).
- `selector_present` / `selector_absent` — a selector must (not) be present.
- `text_present` / `text_absent` — visible text must (not) appear.
- `value_equals` — the target field's actual value must match `expected`. Compared normalized
  (trim/collapse-whitespace/lowercase); if the normalized-exact match fails, a "field contains
  expected" fallback is accepted, so masked/formatted fields (phone, currency) still validate.
  Compiled for `fill`/`type`/`select`/`select_option` steps that have a resolvable target
  selector and a recorded value.
- `state_changed` — no `target`; confirms the page shows *some* observable effect (URL,
  interactive-element count, or a body-text-length delta beyond a small noise tolerance) relative
  to a pre-action baseline captured by the runtime. Synthesized only for commit/destructive clicks
  that have no recorded URL/DOM evidence — the "no silent no-op" guard for evidence-less commits.

**Post-condition preference (recording-next-steps.md Priority 1, 2026-07-10):** before falling
back to the generic wait_for/success_conditions inference above, `_build_assertions` checks the
recorded event's `post_condition` (`RecordedEvent.post_condition`, optional) — a small structured
classification bridge.js computes live against the running page at action time. When
`classified_effect == "dialog_opened"`, its `dialog_signal` claims the enforced slot as a
`selector_present`. When `classified_effect == "value_set"`, a non-redacted `value_readback`
becomes `value_equals`'s `expected` instead of the recorded intent value. This live evidence is
strictly preferred because it's captured against the running page rather than reconstructed
after the fact; the runtime evaluation path is unchanged either way.

### 3.7 ValidationBlock

```python
class ValidationBlock(BaseModel):
    wait_for: dict             # Condition to wait for before asserting
    success_conditions: dict   # Legacy field
    assertions: list[Assertion]
```

### 3.8 WorkflowIntentGraph

Generated by `llm/workflow_intent.py` — one LLM call per workflow, run FIRST in
`compile_skill_package` (before the per-step loop). This single call is the
source of both each step's machine-readable `intent_token` (fed to the compiler's
deterministic logic — recovery policy, destructive gating, validation facets — in
place of the old per-step `intent_generation` calls) and its readable `intent`
prose (becomes `semantic_description`, and the Workflow plan in Human Edit).
Steps the graph leaves tokenless (or every step, if the graph call fails outright)
just compile with a blank/heuristic intent — no per-step LLM fallback call.
Cached per steps-summary+URLs hash under a v2 key namespace (v1 entries predate
`intent_token` and are never reused):

```python
class WorkflowIntentStep(BaseModel):
    index: int                  # position in the cleaned-event sequence
    intent: str = ""            # readable prose ("Enter your email address")
    verification_anchor: str = ""
    intent_token: str = ""      # snake_case machine token (click_sign_in_button)

class WorkflowIntentGraph(BaseModel):
    goal: str                          # "Submit expense report for given period"
    steps: list[WorkflowIntentStep]    # Per-step intent summary
    decision_points: list[dict]        # Points where branching may occur
    expected_end_state: dict           # What success looks like
```

### 3.9 compile_report.second_opinion (BUILD-25)

The compiler's second opinion: `llm/workflow_semantics.py::build_second_opinion` — one
whole-workflow LLM call, run LAST in `compile_skill_package` (after bindings are deduplicated and
`compile_report` itself exists, so the call can route by per-step confidence) — decides, and
`compiler/second_opinion.py::apply_second_opinion` **writes the decisions onto the compiled
steps**. This key is the audit record of what was applied, for the compile log. Nothing in Human
Edit reads it: the changes are simply part of the compiled workflow, indistinguishable from what
the fixed rules produced, and Human Review is the gate for a wrong call. Present only when at
least one finding was applied — absent, not an empty list, when the pass is disabled, fails,
returns nothing, or every finding is rejected. **`second_opinion` absent no longer means
"disabled and failed compiled identically" (2026-09-14):** the compiled steps are still
byte-identical between a disabled pass and a genuinely failed one (this key's own absence, and
every other step field, are unaffected), but a real failure — an exception, or an empty result
paired with a populated LLM `error_detail` — now adds a `second_opinion`/`llm_call_failed` entry
to `compile_report["degraded"]` (§ above) and bumps `status` to at least `"review_needed"`. A
cleanly disabled pass or one that legitimately found nothing leaves `degraded` absent too.

```python
# One entry of compile_report["second_opinion"]:
{
    "step_key": str,   # compiler/step_key.py — identity_bundle.stable_hash + occurrence ordinal,
                        # NEVER step_index (an insert/reorder renumbers every later index —
                        # BUILD-22/BUILD-23)
    "kind": str,        # "rename_binding" | "parameterize_literal" | "suggest_optional" |
                        # "label_phase" | "suggest_assertion" | "flag_noise"
    "current": str,     # the existing binding name / literal value / "" for label_phase,
                        # suggest_assertion, flag_noise
    "proposed": str,    # a valid {{placeholder}} identifier (rename_binding/parameterize_literal),
                        # "true"/"false" (suggest_optional), one of login/navigate/act/verify/
                        # cleanup (label_phase), a JSON string {"type": ..., "target": ...}
                        # (suggest_assertion), or one of duplicate_action/no_op_action/
                        # orphaned_hover (flag_noise)
    "why": str,         # human-readable rationale, capped at 280 chars
}
```

What each kind writes, and nothing else:

| kind | fields written on the `SkillStep` |
|---|---|
| `rename_binding` | `input_binding`, plus the matching `{{token}}` inside `value` |
| `parameterize_literal` | `input_binding`, `value` (only on a step with no binding yet — never re-points an existing one) |
| `label_phase` | `phase` (`login`/`navigate`/`act`/`verify`/`cleanup`; §3.4d's sibling field; read by `runtime/app/failure_response.js`'s Tier B recovery prompt, BUILD-25 stage e) |
| `suggest_optional` | `action.action`, `intent`, `branch`, `recovery`, clears `optional_hint` (§3.4d) |
| `suggest_assertion` (stage d) | additively appends one `Assertion` to `validation.assertions` — always `required=False`; restricted to `text_present`/`text_absent`/`url_changed`/`url_pattern`/`state_changed`, never a selector-bearing type. A no-op if an identical `{type, target}` assertion already exists. |
| `flag_noise` (stage d) | removes the step from `steps` (never a field-level write) — only when the recorder itself observed `post_condition.classified_effect == "none"` on a `click`/`hover`/`scroll`/`focus` step with no required assertion and no `input_binding`. See §3.9a. |

`target`, `identity_bundle`, `compiled_selectors`, `wait_for`/`success_conditions`, and the
frame/tab chain are never touched — element identity stays fully deterministic (CLAUDE.md Key
Invariants). When anything is applied or archived the whole `compile_report` is rebuilt from the
mutated/filtered steps, because a `suggest_optional` conversion or a `flag_noise` removal changes
a step's compiled shape (or the step count) after the first report was computed.

### 3.9a compile_report.archived_steps (BUILD-25 stage d, 2026-09-10)

`flag_noise` is the second-opinion pass's one destructive kind, and the only one that changes the
*shape* of `steps` rather than a field on one step. `compiler/second_opinion.py::archive_flagged_steps`
removes the matched step from what compiles into the shipped skill, but never discards it — the
full step is saved here so a person can restore one later if the compiler was wrong. `category`
`duplicate_action` also covers a `navigate` step (e.g. a user-opened tab's synthetic navigate
immediately followed by the same navigation the recorder captured) — `navigate` has no
`post_condition_effect` signal, so this one path is gated instead on the step immediately before
it being a navigate to the identical url on the identical tab
(`llm/workflow_semantics.py::_validate_findings`). Present only
when at least one `flag_noise` finding was applied; absent otherwise. Never copied into the shipped
pack (`skill_package_builder_saved_skill.py` only reads from the *returned*, filtered `steps`), and
nothing in the editor consumes it yet — same "written, no reader yet" posture `label_phase` had
before stage (e):

```python
# One entry of compile_report["archived_steps"]:
{
    "step_key": str,    # same identity as second_opinion entries — compiler/step_key.py
    "step": dict,       # the removed SkillStep, in full (model_dump(mode="json"))
    "category": str,    # "duplicate_action" | "no_op_action" | "orphaned_hover"
    "why": str,         # human-readable rationale, capped at 280 chars
}
```

A `parameterize_literal` needs no separate input declaration: `handlers/compile.py` already runs
`reconcile_inputs_with_step_values(doc)` after the package is written, which auto-declares any
`{{var}}` a step references but `inputs` lacks.

Cached per (steps + goal + page_urls + sibling_bindings) hash, sharing the versioned dual-write
cache (`llm/llm_cache.py`) that `workflow_intent.py` also uses. Gated by
`SKILL_LLM_SEMANTIC_SUGGESTIONS_ENABLED` (default on). A disabled, failed, or empty pass falls
back to the rules-only compile — byte-identical to a compile that never ran the pass; a pass that
applies something deliberately is not. See `docs/TRD.md` §7.3 for the validation gates and the
reasoning behind that trade.

### 3.10 Reviewer Edit Log (BUILD-25 stage a, 2026-09-09)

`editor/edit_log.py` appends one line per changed field to `data/skills/{skill_id}/edits.jsonl`
(mirrors `session_events.py`'s `events.jsonl`) for every mutating Human Edit command — hooked once
in `backend.py::Backend.dispatch`, so undo/redo are covered without a per-command call. It is the eval set for
§3.9's pass, scored by `conxa-cloud/scripts/eval_suggestions.py` — which joins on
`(step_key, field)` and now reports `override_rate` (a reviewer editing what the pass wrote)
rather than a precision score — and the training pair for any future fine-tune:

```python
# One line of edits.jsonl:
{
    "ts": str,             # ISO-8601 UTC
    "skill_id": str,
    "command": str,        # the cmd_* RPC name (patch_step, reorder_steps, undo_workflow,
                            #         accept_copilot_proposal, ...)
    "meta_version": int | None,  # document["meta"]["version"] after the mutation; None on a
                            #         decision-only line (append_decision — see below)
    "source": str,         # "human" (default) | "copilot" (BUILD-26 stage d)
    "proposal_id": str | None,  # the copilot proposal's id, when source == "copilot"
    "decision": str,       # "accepted" (append_edit) | "rejected" (append_decision) |
                            #         "fixed" | "still_failing" | "progressed" (append_verification, stage e)
    "step_key": str,       # same key space as compile_report.second_opinion; "overlay:<overlay_id>"
                            #         for a rejected insert_overlay_branch proposal (stage f), which
                            #         has no step_key of its own
    "field": str | None,   # one of: input_binding, value, intent, semantic_description,
                            #         action.action, optional_hint, branch,
                            #         validation.assertions, "_step" (add/remove), or None
                            #         (append_decision / append_verification lines)
    "before": Any,
    "after": Any,
    "why": str | None,     # present on a rejection (the copilot's stated reason) or a verify
                            #         line (the retest's pass/fail message)
    "run_id": str | None,  # present only on a copilot_verify line (stage e) — the retest's run id
}
```

Only this small allow-list of reviewer-editable fields is tracked — never the whole step, which
would carry DOM snapshots and identity bundles into a log meant to stay small. Writing is
failure-silent, matching every other local cache/log in the compile pipeline.

**`source` / `proposal_id` / `decision` (BUILD-26 stage d, 2026-09-10).** An accepted Human Review
Copilot proposal is otherwise indistinguishable from a manual edit in this log —
`backend.py::dispatch()` sets `source="copilot"` when the top-level command is
`accept_copilot_proposal` (which itself delegates to `cmd_patch_step`, so the mutation logic is
unduplicated) and threads the proposal's own id through. A **rejected** proposal changes no
document, so the diff-based `append_edit` hook above writes nothing on its own — `append_decision`
logs it directly into the SAME file (`decision="rejected"`, `before`/`after` both `None`, `why`
carrying the copilot's stated reason) rather than a second file, so the correction dataset never
splits into an accepted-only and a rejected-only half. `eval_suggestions.py` reports the copilot's
own accept rate from these lines (`source == "copilot"`), alongside §3.9's `override_rate`.

**`append_verification` (BUILD-26 stage e, 2026-09-10).** A THIRD writer into the same file — the
verified-retest loop's `decision` is `fixed`/`still_failing`/`progressed` (never `accepted`/
`rejected`), `field`/`before`/`after` are all `None` (this line records an outcome, not a field
diff), and `run_id` names the retest run the verdict was measured against. This is the label that
says whether an accepted proposal actually worked, not just that a reviewer liked it —
`eval_suggestions.py::copilot_verified_fix_rate` reports it separately from `accept_rate`.

---

## 4. Tracking / Telemetry Schema

### 4.1 Telemetry Batch Payload (Runtime → Cloud)

```json
{
  "rid": "run_abc123",      // run_id
  "wfid": "acme",           // workflow/company slug
  "wfv": "0.2.0",           // workflow version
  "rv": "1.0.0",            // runtime version
  "uid": "user_hash",       // anonymized user identifier
  "wid": "ws_xyz",          // workspace identifier
  "sv": 1,                  // schema version
  "evts": [                 // compact event list
    {"e": "wf_start", "ts": 1717000000, "tot": 5},
    {"e": "step_ok",  "ts": 1717000002, "si": 0, "tier": 1},
    {"e": "step_fail","ts": 1717000005, "si": 1, "code": "selector_timeout"},
    {"e": "recovery_tier2", "ts": 1717000006, "si": 1},
    {"e": "step_ok",  "ts": 1717000007, "si": 1, "tier": 2},
    {"e": "wf_ok",    "ts": 1717000020, "dur": 20000, "tot": 5, "rec": 1}
  ]
}
```

**Event codes:**

| Code | Meaning | Extra fields |
|---|---|---|
| `wf_start` | Workflow execution begins | `tot` (total steps) |
| `step_ok` | Step succeeded | `si` (step index), `tier` (1–4 wire code; 1–2 = Tier A, 3–4 = Tier B) |
| `step_fail` | Step failed | `si`, `code` (error code) |
| `recovery_tier{N}` | Recovery rung N attempted (legacy numbers; 1–2 = A, 3–4 = B) | `si` |
| `tier_ok` | A resolution/recovery tier succeeded | `si`, `tier`, `sel` |
| `verify_fail` | Post-action VERIFY failed | `si`, `ch` (assertion channel) |
| `verify_result` | Full post-action assertion audit for a step that carries assertions (emitted on the primary execution path, pass or fail — not on recovery re-verification) | `si`, `ok` (overall pass/fail), `n` (assertion count), `advFail` (count of failed advisory/non-required assertions) |
| `repair_event` | A step was recovered — drift signal for the admin flywheel queue | `step_id`, `tier` (`L1`/`L2` inside Tier A, or agent for B), `method`, `score`, `margin`, `stable_hash_match`, `stable_hash`, `drift_hint`, `app_version_fingerprint` |
| `overlay_dismissed` | (EXEC-30) A `step_overrides.dismiss` pick cleared an unrecognized overlay before the resumed step's own action | `si`, `src` (`"agent"`) |
| `overlay_dismiss_rejected` | (EXEC-30) A `dismiss` pick was refused — no match on the live page, or its label read as a commit action | `si`, `why` (`"no-match"` \| `"unsafe-label"`) |

**`recovery.json` per-step field changes (2026-09).** Added: `recorded_context` — where the target sat on the page at recording time (`parent`, `siblings` capped at 8, `index_in_parent`, `form_context`), so the agent recovery tier can compare the live page against the recorded structure instead of only describing the present. Removed: `fallback.text_variants` and `selector_context.alternatives`, which fed the two Layer 2 guessing stages deleted from the runtime cascade — nothing reads them. `selector_context.primary`, `anchors` and `visual_ref` are unchanged, as is the execution step's unrelated `target.fallback_selectors` (a *primary resolution* input, despite the similar name). Added (2026-09-10, BUILD-25 stage e): `phase` — the compiler's second-opinion pass's `label_phase` finding (§3.9), present only on a step the pass labeled; read by `failure_response.js::phaseHintBlock` as a workflow-position prior for the agent tier.

**Retired recovery signals (2026-09).** The fallback-selector walk and fuzzy-text match were removed from Layer 2 (see `docs/TRD.md` §10.1), so three values stop appearing on new runtimes: `rec_ok` with `sc: "text_variant"`, `repair_event` with `method: "fuzzy"` or `method: "fallback"`, and the `layer_recovered` recovery-log entry with `mode: "fuzzy"`. Nothing was renamed and no consumer breaks — the values simply go to zero. Historical rows keep them, so dashboards aggregating over past windows must still tolerate them. `recovery_tier{N}` and the `tier` field keep their 1–4 numbering; only the behavioural grouping changed.
| `drift_detected` | Pre-execution structural drift warning (advisory; emitted at run start, never blocks) | `total` (landmarks), `missing`, `drift_ratio`, `missing_intents` (≤5), `url` |
| `settle_retry` | (EXEC-37) A step failed, the page had visibly not finished changing, and a settle-then-retry was attempted before the recovery cascade | `si`, `waited` (ms), `phase` (`"action"` \| `"verify"`), `ok` (whether the page actually reached a stable shape, independent of whether the retry itself passed) |
| `env_mismatch` | (EXEC-36) Pre-execution environment warning: the live browser's locale/timezone/viewport differs materially from the environment the skill was recorded in (advisory; emitted at run start, never blocks) | `fields` (which of `locale`/`timezone`/`viewport` differed), `locale_rec`, `locale_live`, `vw_rec`, `vw_live` (recorded/live viewport width), `tz_delta_min` |
| `dry_run_skip` | (PROD-3-DRYRUN) A destructive step resolved but was never dispatched — `dry_run: true` withheld the committing action | `si` |
| `for_each_start` | (EXEC-38) A for_each loop began — row enumeration finished | `si`, `total` (rows found), `cap` (effective max_iterations after clamping) |
| `for_each_row_fail` | (EXEC-38) One row's loop body failed | `si`, `row_index`, `continued` (whether `on_row_error: "continue"` let the loop proceed) |
| `for_each_done` | (EXEC-38) A for_each loop finished (success or a `stop`-mode failure that halted it) | `si`, `processed`, `failed`, `total` |
| `fork_observed` | (Outcome fork) The fork step looked at the page | `si`, `st` (`clean`, `unknown`, or the matched state id) |
| `fork_clean` | (Outcome fork) No popup appeared — the clean path | `si` |
| `fork_unknown` | (Outcome fork) A popup appeared that no state recognises | `si` |
| `fork_decided` | (Outcome fork) A policy or a person chose what to do | `si`, `st`, `opt` (option id/label, when one ran), `mode` (`run` \| `stop` \| `handled_in_page`), `src` (`policy` \| `input` \| `human`) |
| `fork_paused` | (Outcome fork) The step stopped to ask a person | `si`, `st` |
| `app_obs` | (App maps, phase 6) A decision popup this run could not place — an unrecognised fork popup, or one that blocked a click and was left alone. Scrubbed to its shape by `runtime/app/scrub.js` | `si`, `host` (lowercase, no `www.`), `rt` (page route, ids as `:id`), `tt` (<=8 title words), `bt` (<=6 button labels; none that carried a file name, number or quoted text), `cl` (`decision`), `k` (1 if the shipped app map recognised it). Never the popup's message |
| `fork_answered` | (Outcome fork) A person's answer was accepted on resume | `si` |
| `park_created` | A run paused and parked its live page — Tier 3/4 agent recovery, an `ai_review` checkpoint, or (EXEC-21) a `handover` step, all sharing one park primitive (`docs/TRD.md` §10.2/§11.3/§11.4) | `si`, `kind` (`"handover"` when a hand-over parked; omitted for the other two cases) |
| `park_resumed` | A parked run resumed execution on the same live page it paused on | `si` |
| `handover_resumed` | (EXEC-21) A parked hand-over's self-driven resume completed | `si` |
| `wf_ok` | Workflow completed successfully | `dur` (ms), `tot`, `rec` (recovered steps) |
| `wf_fail` | Workflow failed | `dur`, `fsi` (failed step index), `fc` (failure code) |

**Scheduled-run identification (PROD-5):** runs fired by the standalone scheduler daemon pass
`_trigger: "scheduled"` to `execute_skill`; the value lands in the engine's LOCAL
`runtime.log` `execute_start` line (field `trigger: "scheduled"`) and on the in-memory exec
registry entry. The cloud telemetry batch schema above is unchanged — no new event code, no new
cloud field — so older clouds are unaffected and scheduled vs chat-driven attribution is
readable wherever runtime logs are readable.

**`repair_event` is ephemeral per-run telemetry** — it never mutates the signed local pack.
It aggregates into the admin drift-review queue at `GET /api/v1/tracking/{company}/drift`
(keyed by workflow/version/step). Detection is automatic and fleet-wide; a durable fix is always
an admin-reviewed, manually published re-sign — publishing is never automatic.

**`drift_detected`** is emitted by the runtime before step 0 when most of a pack's recorded
structural landmarks are no longer present on the live page (a redesign signal). It is advisory —
execution proceeds and per-step recovery still applies. `GET /api/v1/tracking/{company}/drift`
returns these separately under `pre_exec` (aggregated per workflow/version by `_pre_exec_drift_queue`),
alongside the per-step `repair_event` `queue`.

### 4.2 Stored Event Batch (Cloud)

After enrichment, stored in `kv_store` under `tracking/{company}` → `run_id`:

```json
{
  "run_id": "run_abc123",
  "company": "acme",
  "workflow_id": "acme",
  "workflow_ver": "0.2.0",
  "runtime_ver": "1.0.0",
  "uid": "user_hash",
  "wid": "ws_xyz",
  "workspace_id": "org_clerk123",
  "owner_user_id": "user_clerk456",
  "server_ts": 1717000025.3,
  "events": [...],
  "schema_v": 1,
  "chain": {"seq": 0, "prev": "", "h": "hmac_sha256_hex", "ok": true}
}
```

`chain` (PROD-18) is the per-batch evidence-chain link the runtime sends alongside the
batch (`runtime/app/tracker.js::_buildEnvelope` — top-level `seq`/`prev`/`h` on the POST
body, not nested under `chain`; the server moves it under `chain` when storing). `h` is an
HMAC-SHA256 over `{e: evts, p: prev, r: rid, s: seq}` (canonical JSON — same sorted-keys,
no-whitespace form the update manifest uses), keyed on the workspace's own tracking
token, verified server-side on the **raw, pre-truncation** events
(`app.services.tracking._verify_chain_link`) before `_cap_events` runs. `ok` is `true`/
`false` once verified, or `None`/absent for a batch from a runtime shipped before this
field existed — reported as chain state `"none"`, never as `"broken"`. `_run_summary`
folds every batch's `chain` into one per-run verdict (`app.services.tracking._chain_state`):
`"verified"` | `"gap"` (a `seq` is missing) | `"broken"` (a signature or link mismatch) |
`"none"` (pre-chain runtime).

### 4.2a Governance Policy Record

Stored in `kv_store` under `governance_policy` → `workspace_id`
(`app.services.tracking.write_governance_policy`/`read_governance_policy`):

```json
{
  "policy_version": 3,
  "workspace_id": "acme",
  "key_id": "conxa-manifest-1",
  "issued_at": 1717000000,
  "expires_at": 1717604800,
  "enforce": false,
  "require_receipt": false,
  "windows": [
    {"skills": ["payroll-*"], "tz": "Asia/Kolkata", "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "17:00"}
  ],
  "denied_hosts": ["payroll-legacy.acme.com"],
  "signature": "base64_ed25519_signature"
}
```

Signed the same way the runtime update manifest is (`app.api.manifest_signer.sign_manifest`,
canonical JSON minus `signature`), with the same keypair (`CONXA_MANIFEST_SIGNING_KEY`) —
see `docs/Security.md` SG-19 for the single-key/no-rotation tradeoff this reuse accepts.
`policy_version` increments on every write; `expires_at` (default 7 days from issue) is the
one control genuinely enforceable on a machine the customer administers, since a customer
cannot forge a longer expiry without the private key — see `docs/Audit-and-Control.md` §3.
`windows[].skills` entries support a trailing `*` glob; an empty or absent list applies the
window to every skill. `require_receipt` is carried but **not yet enforced by the
runtime** — reserved for a future hardening pass. Fetched by the runtime via
`GET /api/v1/tracking/{workspace_id}/policy` (§5.7a) and cached at
`{CONXA_DATA_DIR}/policy/{workspace_id}.json`, re-verified against the signature on every
read since that cache lives on a customer-controlled disk.

### 4.3 Tracking Token Record

Stored in `kv_store` under `tracking_tokens` → `workspace_id`:

```json
{
  "token": "random_urlsafe_32_bytes",
  "company": "workspace_dir_slug_value",
  "version": "0.2.0",
  "workspace_id": "org_clerk123",
  "owner_user_id": "user_clerk456",
  "updated_at": 1717000000.0
}
```

**`GET /api/v1/tracking/companies`** returns one row per visible workspace:
`{"company": <telemetry storage slug>, "display_name": <human label>, "workspace_id", "run_count", "last_seen"}`.
`company` MUST stay the same slug telemetry was written under (`tracking/{slug}/...`,
`workspace_dir_slug(workspace_id)`) — every reader of a row (`_visible_run_records`,
and the frontend's `/dashboard/runs/{company}/...` and `/dashboard/workflows/{company}/...`
routes) looks the run data back up by it. Put the human-readable name only in
`display_name`; conflating the two here once meant a published workspace's run
records were unreachable under their display name and the dashboard showed no data.

---

## 5. API Contracts

### 5.1 Publish Skill Pack

**Publish never deploys — it uploads a "ready" version to Conxa Cloud.** A
Cloud admin's explicit Release/Deploy action (§5.1d) is the only thing that
activates a version and starts runtimes receiving it. See `docs/App-Flow.md`
§8.

**POST /api/v1/workflows/publish**

Request:
```json
{
  "slug": "acme",
  "skill_slug": "submit_expense",
  "group_id": "expenses",
  "group_name": "Expenses",
  "workflow_name": "Submit Expense",
  "display_name": "Acme Corp",
  "target_url": "https://app.acme.com",
  "protected_url": "https://app.acme.com/dashboard",
  "skill_pack_version": "0.2.0",
  "release_notes": "Fixed export bug",
  "tests_passed": true,
  "recording_duration_seconds": 312.5,
  "files": [
    {
      "path": "execution.json",
      "content_base64": "..."
    }
  ]
}
```
`group_name`/`workflow_name` are display-only, used by Cloud's Skill Packages
→ Group → Workflow navigation (§5.1d) — an older Build Studio that hasn't
picked them up yet just falls back to the slug/group_id. `tests_passed` is
Build Studio's own local test-gate result, surfaced to Cloud admins reviewing
a "ready" version alongside its diff and artifact. `recording_duration_seconds`
is how long the human took to perform the workflow once, live, during
recording (`Workflow.recording_duration_seconds`, computed at stop_recording
from the first/last event timestamp) — `null` for an older Build Studio or a
recording made before this field existed. On a successful publish it seeds
that workflow's ROI baseline (`tracking_analytics.seed_recorded_baseline`,
§5.7a) the first time only — a later republish, or an admin's own edit, is
never overwritten.

Response (200):
```json
{
  "slug": "acme",
  "version": "0.2.0",
  "files_written": 1,
  "sync_url": "/api/v1/skill-packs/acme/delta",
  "sync_token": "...",
  "tracking": {
    "enabled": true,
    "tracking_url": "https://apis.conxa.in/api/tracking/acme/events",
    "tracking_token": "...",
    "company_id": "acme",
    "schema_version": 1
  },
  "workspace_id": "org_clerk123",
  "published_at": 1717000000.0
}
```
This response means the version is durably uploaded and **"ready"** — not
that it's live. `sync_url`/`sync_token` are minted here (Build Studio's local
`pack.json` needs them for future syncs), but nothing about the runtime-facing
mutable mirror or the stable channel moves until Release/Deploy (§5.1d).

Duplicate-version publish → **409** `skill_pack_version_exists`, unless the
existing row is still `"ready"` (or legacy `"pending"`) — retrying an
unreleased version number is safe and expected, not an overwrite.

### 5.0 App-map contributions (`/api/v1/app-graphs`)

Clerk-authenticated, scoped to the caller's workspace (the server stamps `workspace_id` on every contribution, whatever the body says). Build Studio folds the maps; the cloud only stores them so teammates share them (`conxa-builder/python/services/app_graph_sync.py`, run on publish and by `sync_app_graphs`).

```
PUT    /api/v1/app-graphs/{app_key}/contributions/{contribution_id}   body: Contribution (§3.4l)   → {"ok": true}
DELETE /api/v1/app-graphs/{app_key}/contributions/{contribution_id}                                → {"ok": true}
GET    /api/v1/app-graphs/{app_key}/contributions                                                  → {"app_key", "contributions": [...]}
GET    /api/v1/app-graphs                                                                          → {"apps": [{"app_key", "contributions"}]}
GET    /api/v1/app-graphs/{app_key}/field                                                          → {"app_key", "popups": [{"title_tokens", "buttons", "class", "count", "known", "last_seen"}]}
```

`/field` counts the runtime's `app_obs` telemetry (docs section 4.x telemetry table) per popup shape over the caller's own visible runs; the same plan gate and retention as `GET /api/v1/tracking/drift`; the event fields are re-bounded server-side (<=8 title words, <=6 buttons, 40 chars each) because a token holder can send anything.

`app_key` must match `^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$` and `contribution_id` `^skill:[A-Za-z0-9._-]{1,128}$` (422 otherwise); the body's `app_key`/`id` must equal the path (422 `contribution_mismatch`); a contribution over 64 KB is 413 `contribution_too_large`; more than 500 per app is 409 `too_many_contributions` (replacing an existing one is always allowed).

### 5.1a Versioned Endpoint Scheme (`{installer_version}`)

Skill Pack Publishing is the primary, version-controlled release surface; installers are a stable, Conxa-owned platform artifact. To let Conxa evolve the installer/runtime wire contract without ever requiring vendors to rebuild for routine skill-pack updates, Conxa-owned versions of endpoint families are nested under `{installer_version}` path segment (`publish_routes.SUPPORTED_INSTALLER_GENERATIONS`, currently `("v1", "v2")`):

**Dashboard endpoints** (Clerk-authenticated; workspace derived from authenticated principal, not a path parameter):
```
POST /api/v1/workflows/publish                                                  # legacy
POST /api/v1/workflows/{installer_version}/skill-packs/upload                   # versioned; same body/response as §5.1
GET  /api/v1/workflows/{installer_version}/skill-packs/versions                 # release history — see §5.1a below
POST /api/v1/workflows/{installer_version}/installer/upload                     # versioned; same as §5.1b below
GET  /api/v1/workflows/{installer_version}/installer/versions                   # versioned; same as §5.1b below
GET  /api/v1/workflows/generations                                              # {current, supported, deprecated}
POST /api/v1/admin/workflows/generations                                        # admin flip (Bearer CONXA_ADMIN_TOKEN)
```

**Runtime endpoints** (bearer-token authenticated; workspace scoped by `{workspace_id}` path parameter):
```
GET  /api/v1/skill-packs/{workspace_id}/delta                                   # legacy
GET  /api/v1/workflows/{installer_version}/{workspace_id}/skill-packs/delta     # versioned; same as §5.9
POST /api/v1/workflows/{installer_version}/{workspace_id}/skill-packs/{slug}/artifacts   # recovery artifacts
POST /api/v1/tracking/{workspace_id}/events                                     # legacy ingest
POST /api/v1/workflows/{installer_version}/{workspace_id}/tracking/events       # versioned ingest; same as §5.6
```

**POST …/skill-packs/{slug}/artifacts** (added 2026-09) — one zip holding exactly the recovery artifacts this machine is missing, for one skill. Request `{"have": ["<sha256>", …]}` lists the hashes already in the machine's content-addressed store; the response body is `application/zip` containing every artifact whose hash is *not* in that list, with the archive's own checksum in the `X-Artifact-Sha256` response header. An unknown hash is ignored rather than rejected — the store is shared across every skill and workspace on the machine, so it legitimately holds hashes this skill never had. Nothing missing returns a valid empty zip, not an error.

Three deliberate differences from the delta route beside it:
- **Not rate limited.** The delta allows one request per token per 5 minutes; the artifact pass runs immediately after the code files land, so that window would refuse it every time. Authentication is identical — the exemption is pacing, never access.
- **Zip bytes returned directly**, not a link to one. Render's free plan has no persistent disk (the reason `_ensure_skill_pack_on_disk` exists), so an archive written somewhere to be fetched afterwards could be gone by the time the client asked.
- **No legacy unversioned equivalent.** A pack still carrying the old `sync_endpoint` shape gets no artifacts until it is republished onto the versioned form.

The delta response gained a matching per-skill `artifacts: [{path, sha256}]` array — metadata only, never `content_base64`. It is present on `no_change` entries too, so a pack installed before artifact sync existed can backfill without waiting for an unrelated republish. Everything not in the five-file code set (`execution.json`, `recovery.json`, `inputs.json`, `manifest.json`, `validation.json`) is an artifact; today that means `visuals/`. See `docs/TRD.md` §10.2 for the two-pass client behaviour and the on-disk store.

Every versioned route validates `{installer_version}` against the allow-list (400 `unsupported_installer_version` otherwise) and delegates to the exact same shared implementation function as its legacy, unversioned counterpart — behavior is identical across generations. **`{installer_version}` is frozen into an installer at build time** (stamped into `pack.json.installer_version` at publish time by Build Studio, read from `GET /api/v1/workflows/generations`'s `current` field) and is never reassigned remotely for an already-installed runtime. "Migrating customers to a new generation" means Conxa flips the *default* generation that **new** installer builds stamp (`POST /api/v1/admin/workflows/generations`) — it does not, and cannot, change the URLs already baked into a customer's machine. The legacy, unversioned routes are kept mounted **permanently** as the implicit "v1" behavior for every already-deployed installer — never removed.

**GET /api/v1/workflows/{installer_version}/skill-packs/versions** — release history for the Skill Pack Publishing / Release Center page (version, release notes, publish timestamp, artifact hash, `is_latest`), the version/release-comment/publishing-limit surface that moved here from Build Installer:
```json
{
  "slug": "acme",
  "skill_slug": "submit_expense",
  "current_stable": {"slug": "acme", "skill_slug": "submit_expense", "version": "0.3.0", "status": "published", "is_latest": true, "...": "same row shape as below"},
  "versions": [
    {"slug": "acme", "skill_slug": "submit_expense", "version": "0.4.0", "release_notes": "WIP export tweak",
     "group_id": "expenses", "tests_passed": true,
     "workspace_id": "org_clerk123", "owner_user_id": "user_123",
     "published_by": {"user_id": "user_123", "email": "a@acme.test", "name": "Ana"},
     "published_at": 1717000200.0, "files_written": 4, "file_count": 4, "size_bytes": 3010,
     "artifact_sha256": "7e5d...12", "status": "ready", "is_latest": false},
    {"slug": "acme", "skill_slug": "submit_expense", "version": "0.3.0", "release_notes": "Fixed export bug",
     "group_id": "expenses", "tests_passed": true,
     "workspace_id": "org_clerk123", "owner_user_id": "user_123",
     "published_by": {"user_id": "user_123", "email": "a@acme.test", "name": "Ana"},
     "published_at": 1717000100.0, "files_written": 6, "file_count": 6, "size_bytes": 4821,
     "artifact_sha256": "3f2a...c9", "status": "published", "is_latest": true},
    {"slug": "acme", "skill_slug": "submit_expense", "version": "0.2.0", "release_notes": "Initial release",
     "workspace_id": "org_clerk123", "owner_user_id": "user_123", "published_at": 1717000000.0,
     "files_written": 5, "artifact_sha256": "9c1b...44", "status": "published", "is_latest": false}
  ]
}
```
**KV namespace:** `skillpack_versions__{slug}__{skill_slug}` — one row per (skill, version) (mirrors `installer_versions__{slug}`, §5.1b below). Duplicate-version publish → 409 `skill_pack_version_exists`; a byte-identical republish (same `artifact_sha256` as the current stable release) → 409 `skill_pack_artifact_unchanged` (see §5.1). `is_latest` tracks the stable channel, not "most recently published" — it moves on rollback too. `status` is one of: `"ready"` — uploaded and durably persisted, awaiting a Cloud admin's Release/Deploy decision (§5.1d); `"published"` — activated, the normal state for every row a Release or Rollback has ever pointed at; `"pending"` — legacy, a row left behind by a publish attempt that crashed before even reaching `"ready"` (safe to retry the same version number, but distinct from the new `"ready"` steady state). See §5.1d for the full release system this row shape backs.

### 5.1d Skill Pack Release System (per-skill: immutable versions, stable channel, rollback, diff)

Built on top of §5.1's publish endpoint — see `docs/Implementation-Plan.md` §3.4 for the full design writeup. Every route below is Clerk-authenticated with `require_admin` gating; workspace is derived from the authenticated principal, not a path parameter.

**Re-scoped 2026-08-19 to per skill.** 1 Workflow = 1 Skill = 1 Skill Package = 1 independent version history = 1 independent release. Every route, KV namespace, and JSON shape below is keyed by **skill slug** — publishing, testing, versioning, and rolling back one skill (e.g. "create-a-lead") never requires, checks, or touches another skill (e.g. "update-opportunity") in the same workspace. `skill_slug` travels as a required body field on `POST`s with a body (`.../releases/preview`) and as a required `?skill_slug=` query parameter on every other route below (kept off the URL path itself to avoid re-numbering the existing route segments).

```
POST /api/v1/workflows/{installer_version}/releases/preview                              # dry run for ONE skill — proposed version, diff vs. that skill's own previous version, duplicate/unchanged flags
GET  /api/v1/workflows/{installer_version}/releases/{version}?skill_slug=...              # release detail + per-file sha256, for that one skill's own version row
GET  /api/v1/workflows/{installer_version}/releases/{version}/diff?skill_slug=...          # deterministic diff vs. the preceding published version OF THIS SAME SKILL
POST /api/v1/workflows/{installer_version}/releases/{version}/release?skill_slug=...       # Cloud-only: activate a "ready" version — the only action that deploys it
POST /api/v1/workflows/{installer_version}/releases/{version}/rollback?skill_slug=...      # move ONE skill's stable channel pointer back to an already-published version
GET  /api/v1/workflows/{installer_version}/deployments?skill_slug=...                      # runtime deployment status for ONE skill
GET  /api/v1/workflows/{installer_version}/releases/events?skill_slug=...                  # release-lifecycle audit trail for ONE skill
GET  /api/v1/workflows/{installer_version}/groups                                          # Cloud's Skill Packages → Group → Workflow navigation — Studio-synced groups (possibly empty) unioned with every skill ever published
PUT  /api/v1/workflows/{installer_version}/groups/{group_id}                               # Build Studio create/rename: upsert {group_id, group_name} so the folder exists before first publish
```

**Publish vs. Release/Deploy.** `POST .../skill-packs/upload` (§5.1) only ever
writes the immutable snapshot and a version-history row with
`status: "ready"` — it never touches the mutable mirror `_build_delta`
serves, the `component_versions` entry, or the stable channel. `POST
.../releases/{version}/release` is the only thing that does all three
(mirrors `.../rollback`'s structure, just with the opposite precondition: a
`"ready"` row instead of an already-`"published"` one). This is what makes
"publish ≠ deploy" true at the data-plane level, not just a UI label — see
`docs/App-Flow.md` §8.1/§8.1a.

**Domain model:**
- **Immutable snapshot** — every publish writes its file set once, write-only, to `skillpack_release_files__{slug}__{skill_slug}__{version}` (KV) + `data_dir/skill-packs/{slug}/skills/{skill_slug}/releases/{version}/` (disk cache). Never touched again. This is what rollback restores from — no artifact is ever rebuilt, copied, or mutated. Contains only that skill's own files (`execution.json`, `recovery.json`, `inputs.json`, `manifest.json`) — never `pack.json`, which is company-level static config (target_url, tracking) a single skill's release/rollback must never mutate.
- **Mutable mirror** — the pre-existing `skillpack_files__{slug}` / `data_dir/skill-packs/{slug}/{group_id}/{skill_slug}/...` layout `_build_delta` (§5.9) has always served. Still one namespace per company (not re-keyed), but every publish/rollback call now only ever writes the one skill's own file subset into it — a sibling skill's files in the same mirror are never touched.
- **Stable channel** — `skillpack_channels` KV (still one namespace), row key is now the composite `"{slug}:{skill_slug}"`: `{"slug": "acme", "skill_slug": "create-a-lead", "stable": {"version": "1.3.0", "set_at": 1717000100.0, "set_by": "user_123", "reason": "publish"|"rollback", "from_version": "1.2.0"}}`. One channel per skill in V1. **Not** the same "channel" concept as §5.8's runtime/app self-update `dev`/`stable` channel — a separate, unrelated pointer this system doesn't read or write.
- **Release events** — `skillpack_release_events__{slug}__{skill_slug}` KV, an unbounded append-only array (`db_append`), one row per lifecycle action: `skill_version_created`, `skill_publish_started`, `skill_publish_succeeded`, `skill_publish_failed`, `release_started`, `release_succeeded`, `release_failed`, `stable_channel_changed`, `rollback_started`, `rollback_completed`. Every event is also mirrored into the existing `saas.add_audit_event` (§7's `saas` namespace) so the dashboard's Audit page needed no changes — but that log is a global 500-entry ring buffer across all workspaces, so the durable per-(slug, skill_slug) record here is the one to query for a specific skill's history. `stable_channel_changed` now fires from two different callers with a different `reason` (`"release"` vs. `"rollback"`) rather than from `publish` — publishing alone never emits it.
- **No data migration for the re-key.** These four namespaces are purely internal cloud bookkeeping — no installed runtime reads them directly (runtimes only ever see `component_versions`, `pack.json`, and the delta endpoint, all unchanged in shape — see §5.9). A pre-existing company-wide release published before this re-scoping simply won't appear in the new per-skill Release Center; that's an accepted gap, not a regression for any already-deployed runtime.
- **Known-skills registry** — `skillpack_known_skills` KV (row key `"{slug}:{skill_slug}"`, same pattern as `skillpack_channels`): `{slug, skill_slug, group_id, group_name, workflow_name, first_published_at, archived?, archived_at?}`, upserted at **publish** time (not release time). Deliberately independent of `pack.json`'s `skills`/`skill_groups` union, which only updates at Release/Deploy — this registry is what lets Cloud's Skill Packages → Group → Workflow navigation (`GET .../groups`) show a skill the moment it's published, before anyone has released it, without exposing anything runtime-facing early. `archived`/`archived_at` are set by **POST .../skills/archive** below and never touched by publish, so a republish of an archived skill's Build Studio project doesn't silently un-archive it.
- **Archive / Unarchive** — `POST .../skills/archive?skill_slug=` and `POST .../skills/unarchive?skill_slug=` (`release_routes.post_skill_archive`/`post_skill_unarchive`, `require_admin`). Archive removes the skill's slug from `pack.json`'s `skills`/`skill_groups` union (the same union `_build_delta`, §5.9, and Release/Deploy read and write) — installed runtimes drop it from `list_skills` at their next sync, but nothing already synced to a runtime's disk is deleted, and no version row, snapshot, or the stable channel is touched. Unarchive clears the flag and, if the skill has a stable release, folds it back into the `pack.json` union so runtimes pick it back up — the same union-membership step Release/Deploy uses. Both flip `archived` on the known-skills row above and record a `skill_archived`/`skill_unarchived` release-lifecycle event (§5.1d). While a skill is archived, **release** and **rollback** (both above) reject with 409 `skill_archived` — releasing a new version onto an archived skill would silently re-deploy it. `GET .../groups` includes `archived: bool` per workflow so the dashboard can show/hide the Restore action.
- **Known-groups registry** — `skillpack_known_groups` KV (row key `"{slug}:{group_id}"`): `{slug, group_id, group_name, created_at}`, upserted when Build Studio creates or renames a group (`PUT .../groups/{group_id}`), not at publish time. `GET .../groups` unions this registry with known-skills so an empty Studio group appears as a folder with `workflows: []`. A later publish of a skill with the same `group_id` attaches that workflow to the existing folder. Deleting a group in Studio does not remove the Cloud row. If the company slug has no SkillPack yet, the PUT creates a shell record from the caller's workspace name so the Skill Packages list has a company card to hang the folder on.

**PUT .../groups/{group_id}** request/response — Build Studio, `require_admin` + slug ownership (unclaimed slugs are claimed when the shell SkillPack is created):
```json
// request
{"group_name": "Sales", "company_name": "Acme"}
// response
{"slug": "acme", "group_id": "11111111-1111-1111-1111-111111111111", "group_name": "Sales"}
```

**POST .../releases/preview** request/response — one skill per call:
```json
// request
{"version": "0.4.0", "skill_slug": "submit_expense", "group_id": "expenses", "files": [{"path": "execution.json", "content_base64": "..."}]}
// response
{
  "slug": "acme", "skill_slug": "submit_expense", "previous_version": "0.3.0", "proposed_version": "0.4.0",
  "version_valid": true, "version_available": true,
  "artifact_sha256": "a1b2...", "artifact_unchanged": false,
  "diff": {
    "skills_added": [], "skills_removed": [],
    "steps_added": 2, "steps_removed": 1, "steps_modified": 3,
    "recovery_changed_skills": ["submit_expense"], "metadata_changed_skills": [],
    "summary": "+2 steps added, ~3 modified, -1 removed, ~1 recovery rule changed",
    "per_skill": {"submit_expense": {"status": "changed", "steps_added": 2, "steps_removed": 1, "steps_modified": 3, "recovery_changed": true, "metadata_changed": false}}
  }
}
```
For a brand-new skill (no previous publish), `previous_version` is `null` and the artifact-unchanged check is skipped entirely — no previous-version requirement, matching "publish a new skill's first version as v1.0.0." The diff is pure stdlib (`difflib` over a semantic per-step key — type/tab/frame/selector — excluding volatile targeting metadata like `identity_bundle`/`compiled_selectors`/`confidence`) — deterministic, no LLM, same function for the pre-publish preview and the published `.../diff` endpoint.

**POST .../releases/{version}/release?skill_slug=...** — Cloud-only, `require_admin` + slug ownership. 404 if the version doesn't exist for this skill; 400 `release_not_ready` if it isn't currently `"ready"` (already published, or still `"pending"`); 409 `release_snapshot_unavailable` if the snapshot is missing. Success response: `{"slug": "acme", "skill_slug": "create-a-lead", "released": "1.3.0", "previous_stable": "1.2.0"}`. Performs, in order: refresh the mutable mirror from the immutable snapshot; fold the skill into `pack.json`'s `skills`/`skill_groups` union (a no-op after the first release of a given skill); rewrite `component_versions`; move the stable channel; flip the row to `"published"` + `is_latest`. Every step is scoped to this one skill.

**POST .../releases/{version}/rollback?skill_slug=...** — `require_admin` + slug ownership. 404 if the version doesn't exist for this skill; 400 `release_not_published` if it isn't currently `"published"` (still `"ready"`, or a legacy `"pending"` row); 400 `already_stable` if it's already this skill's channel target; 409 `release_snapshot_unavailable` if it predates this feature. Success response: `{"slug": "acme", "skill_slug": "create-a-lead", "rolled_back_to": "1.2.0", "previous_stable": "1.3.0"}`. Every step (channel move, mirror restore, `component_versions` rewrite, `is_latest` flip) is scoped to this one skill — a sibling skill's stable version, live files, and version history are never touched by this call. **Rollback is always scoped to one workflow/skill — there is no group-level or bulk rollback anywhere in this system.**

**GET .../deployments?skill_slug=...** — reads `runtime_registrations` (§5.6/telemetry), filtered to the slug + caller's workspace, compared against THIS skill's channel stable version only:
```json
{
  "slug": "acme", "skill_slug": "submit_expense", "desired_version": "1.3.0",
  "machines": [{"machine_id": "install-abc", "platform": "win32", "runtime_version": "1.4.0",
                "installed_skill_versions": "1.3.0", "desired_skill_version": "1.3.0",
                "status": "up_to_date", "sync_error": null, "last_seen": 1717000200.0, "last_sync": 1717000200.0}],
  "summary": {"total": 1, "up_to_date": 1, "pending": 0, "failed": 0, "offline": 0, "unknown": 0}
}
```
`installed_skill_versions` is that one skill's installed version string (not the machine's full `{skill_slug: version}` map) — a machine current on every other skill but stale on this one reads `pending`, never `up_to_date`. `status` is one of, checked in this order: `offline` (stale `last_seen`, §5.6's 30-day window) → `failed` (the runtime's last phone-home reported a `sync_errors` entry for this skill AND the installed version still hasn't caught up to desired — an error that's since self-resolved never counts) → `unknown` (registration predates the runtime reporting `skill_versions`, or hasn't reported this skill yet) → `up_to_date` / `pending`. `sync_error` (`{code, at}` or `null`) is only populated when `status == "failed"`.

**Runtime telemetry extension** — `POST /api/v1/telemetry/runtime-start`'s `TelemetryBody` has two optional fields:
- `skill_versions: {company: {skill_slug: version}}`, populated by `runtime/installed_versions.js` off the same `current` junction / `version.json` §5.9/§8 already describe. Omitted entirely by a runtime that hasn't self-updated to report it yet — its registrations just read as `unknown` above rather than a fabricated `up_to_date`. Sticky: an omission never overwrites a previously-reported value (an older runtime that stops sending it doesn't regress an already-known state to `unknown`).
- `sync_errors: {company: {skill_slug: {code, at}}}`, populated by `runtime/sync_errors.js` off `pack.json.last_sync_errors` — the per-skill failures `runtime/sync.js` records on a checksum mismatch, download failure, or activation failure, and clears the moment that skill next activates successfully. **Not sticky**: always overwritten in full on every phone-home, since an empty `{}` is itself a real signal ("nothing failed this round"), unlike `skill_versions`.

**Machine Registry fields (added 2026-08-22)** — `hostname`, `username`, `os_release`, `os_arch` (strings, from Node's `os` module) and `capabilities` (`{max_recovery_tier, update_channel}`, feature flags only — not a tool list). All five are optional and sticky like `skill_versions`. `username` is the local OS account running the agent — there is no Clerk/end-user identity on an installed runtime, so this is the only available "who" signal. See §5.9a for the fleet-visibility read/revoke API these back.

**KV namespaces (re-keyed 2026-08-19 to include `skill_slug`):** `skillpack_release_files__{slug}__{skill_slug}__{version}`, `skillpack_channels` (composite row key `"{slug}:{skill_slug}"`), `skillpack_release_events__{slug}__{skill_slug}`, `skillpack_known_skills` (composite row key `"{slug}:{skill_slug}"`, publish-time index, see above) — see §7.

### 5.1b Installer Upload + History (installer becomes a secondary, advanced artifact)

**POST /api/v1/workflows/{slug}/installer/upload?filename=...&version=...&release_notes=...** (raw octet-stream body) — uploads a built installer `.exe`. No product/skill-pack-slot entitlement check here (that gate lives on skill-pack publish only, §5.1/§5.3 — installer upload is optional and unmetered, matching the requirement that a failed/skipped installer upload never fails a Build Installer run). Duplicate-version upload → 409 `installer_version_exists`. Response:
```json
{
  "slug": "acme", "version": "1.2.0", "sha256": "abc123...", "size": 20480000,
  "download_url": "/api/v1/installers/acme", "version_download_url": "/api/v1/installers/acme/versions/1.2.0"
}
```

**GET /api/v1/workflows/{slug}/installer/versions** — authenticated installer release history for the dashboard (version, release notes, sha256, size, `is_latest`, signed `download_url`).

**KV namespace:** `installer_versions__{slug}` — one row per version, binary mirrored separately in Postgres (`installer_storage.load_installer_from_db`) since Render's disk is ephemeral.

### 5.1c Plan-Aware Installer Naming + Icon (added 2026-08-09)

When `?filename=` is omitted from the upload call above, the served filename now depends on plan:
Free gets a random 10-letter unbranded name; paid plans (Starter/Pro/Enterprise) use the workspace's
stored **installer domain** if one is set, else fall back to the previous `{slug}-Setup.exe`.
The installer domain is plain text and unverified — no DNS proof of ownership yet (see `TODO.md`
PROD-6, which this field is meant to be gated on once that ships).

**GET /api/v1/entitlements/installer-domain** (owner/admin) — `{domain: string}` (empty string if unset).

**POST /api/v1/entitlements/installer-domain** (owner/admin) — `{domain: string}` → validated (protocol
stripped, basic hostname shape), stored, and returned. 400 `invalid_domain` on a malformed value.

**KV namespace:** `workspace_installer_domain` — one row per workspace, `{workspace_id, domain}`.

**Added 2026-08-22 — captured at signup, not just settings.** This same route is now also called from
a new onboarding step (`conxa-cloud/frontend/app/onboarding/company-domain/page.tsx`), shown right
after Clerk's `<CreateOrganization>` succeeds and before landing on `/dashboard`, so every workspace
has a domain on file from day one instead of only workspaces that happened to visit installer settings
later. No backend change — same route, same validation, same unverified-format-only guarantee (still
tracked as `TODO.md` PROD-6).

The installer's `.exe` icon is a separate, build-time-only concern (embedded in the binary by Build
Studio before upload, so the cloud has no upload-time hook for it): Build Studio checks
`GET /entitlements/current`'s `plan` before calling the local builder and drops any supplied
`logo_path` on the Free plan. See `docs/TRD.md` §13.6's "Installer naming and icon" note.

### 5.2 Skill Pack Delta

See §5.9 (Skill-Pack Delta Sync) — the sole current contract for this endpoint. An earlier revision of this document described a single shared pack-wide version here; that has been superseded by §5.9's per-skill version map and removed to avoid two contradictory contracts for the same endpoint.

### 5.3 Entitlements

**Rewritten 2026-08-08** for the capability ladder (`docs/PRD.md` §11, `docs/TRD.md` §13.5). The
per-slug `skill_pack_slots` meter was removed entirely — a workspace may publish under unlimited
product slugs on every tier, tracked only via `publish_owners` for the ownership-conflict check, not
for a limit. `machines` replaced it as the numeric meter.

**GET /api/v1/entitlements/current**

Response:
```json
{
  "workspace_id": "org_123",
  "plan": "starter",
  "period": "billing:1782691200",
  "reset_at": "2026-06-29T00:00:00Z",
  "trial_ends_at": null,
  "trial_expired": false,
  "wallet": {"compile_credits": 20, "human_edit_tokens": 200000, "ai_usage_credits": 200000},
  "meters": {
    "seats": {"used": 2, "limit": 3, "remaining": 1, "unlimited": false},
    "machines": {"used": 1, "limit": 3, "remaining": 2, "unlimited": false},
    "execute_seats": {"used": 4, "limit": 25, "remaining": 21, "unlimited": false},
    "compile_credits": {"used": 42, "limit": 200, "remaining": 158, "unlimited": false},
    "human_edit_tokens": {"used": 230000, "limit": 2500000, "remaining": 2270000, "unlimited": false},
    "ai_usage_credits": {"used": 230000, "limit": 2500000, "remaining": 2270000, "unlimited": false},
    "execute_chat_tokens": {"used": 12000, "limit": null, "remaining": null, "unlimited": true}
  },
  "capabilities": {
    "distribution": "external",
    "white_label": false,
    "ops_tier": "basic",
    "compile_pool": "starter",
    "byok": false,
    "vision_fallback_on_exhaustion": false
  },
  "workflow_lock": {
    "limit": 200,
    "active": 200,
    "locked": 12,
    "workflows": [
      {"workspace_id": "org_123", "workflow_id": "wf1", "created_at": "2026-06-01T00:00:00Z", "locked": true}
    ]
  }
}
```
(Free is the only plan carrying `"distribution": "internal"`, enforced by `ensure_distribution_allowed` at installer-upload time — see §5.1b below.)

**Added 2026-08-23 — `vision_fallback_on_exhaustion`.** `False` on every plan by default (unlike
`white_label`/`byok`, deliberately not plan-gated — this is an ops reliability lever Conxa can turn on
for any workspace, Free included, not a paid feature); per-workspace overridable via
`entitlement_overrides`, same mechanism as those two.
Controls whether Build Studio's local compiler degrades a step to keyword anchors instead of
hard-stopping the whole compile when the vision-anchor LLM provider pool is exhausted. Build Studio
fetches this once per compile (`handlers/compile.py::cmd_compile` calls this same endpoint via
`backend.py::_apply_vision_fallback_entitlement`) and applies it locally — replacing what used to be
a Build-Studio-local-only environment variable (`SKILL_VISION_ANCHOR_FALLBACK_ON_EXHAUSTION`) with no
cloud-side control at all. See `docs/TRD.md` §13.3.

**Added 2026-08-09 — `workflow_lock` (persistent workflow-slot ledger).** `compile_credits` above is a *monthly* meter that resets every period; it never reclaims access to workflows a workspace already published in an earlier, higher-tier period. `workflow_lock` is the separate, never-resetting answer to that gap: every distinct `(workspace_id, workflow_id)` a workspace has ever published is recorded once, on first publish, in the `entitlement_workflows` KV namespace (`app/services/entitlements.py::record_published_workflow`). On every read, `_reconcile_workflow_locks` reuses the plan's current `compile_credits` number as a standing cap on how many of those workflows may stay **active** — it keeps the `limit` most-recently-published unlocked and locks the rest, oldest first. This self-heals on every read: a downgrade locks the oldest excess automatically, an upgrade unlocks them back in the same order, with no separate migration step. `ensure_workflow_publishable` enforces the same cap at publish time (`app/api/publish_routes.py`): republishing an already-active workflow (a new version) is always allowed; republishing a **locked** one raises `workflow_locked` (402); publishing a **brand-new** workflow once the workspace is already at its cap raises `workflow_limit_exceeded` (402). Scope is deliberately company-side only — locking never touches already-installed end-customer runtimes, which keep syncing and running a workflow they already have regardless of the SaaS company's current plan (execution is local and the cloud isn't in that path, same rationale as `ensure_trial_active`). Gated by the same `entitlements_enforce_compile` flag as the monthly meter.

For paid (Cashfree-subscribed) workspaces, `period` is `billing:<current_period_end_unix>` and `reset_at` is the next monthly payment timestamp. Workspaces without a subscription timestamp use the UTC calendar-month fallback (`YYYY-MM`). `trial_ends_at`/`trial_expired` are non-null only for the `free` plan.

**Rewritten 2026-08-22 - `wallet`.** Compile add-on packs became **one-time purchases** (they were briefly monthly subscriptions earlier the same day; before that a single-scalar `addon_compile_packs`). A confirmed payment credits `wallet` - `{"compile_credits": n, "human_edit_tokens": n}` on the billing record - a never-expiring balance drawn down only after the plan's monthly allowance runs out, never reset by billing-period rollover. The plan meters themselves no longer include pack grants.

**GET /api/v1/entitlements/machines** (owner/admin) — registered build devices, `[{machine_hash, last_ip, first_seen, last_seen, revoked?}]`, newest `last_seen` first. Includes revoked devices for history.

**POST /api/v1/entitlements/machines/revoke** (owner/admin) — `{machine_hash}` → frees that slot; the same hash re-registers as a brand-new device on its next call, re-entering through the limit check.

Dashboard: the "Build Studio Devices" card on `conxa-cloud/frontend/src/SettingsPage.tsx` (added 2026-08-22, closing the frontend gap tracked as `TODO.md` CLOUD-6) — these two routes were the whole backend for it already.

**Renamed 2026-09-16 — `human_edit_tokens` → "AI Usage Credits".** Customer-visible label only; the
wire response now dual-emits `ai_usage_credits` (canonical) alongside the legacy `human_edit_tokens`
key (deprecated, kept only until already-installed Build Studio Electron builds have auto-updated
past this change — see `docs/TRD.md` §13.5). Internal storage keys and the wallet key
`human_edit_tokens` are unchanged — zero migration. **CLOUD-25 (2026-10-18)** then renamed the usage
class itself: `usage_class="ai_credits"` is canonical, `"human_edit"` stays a permanent alias for
already-installed Studios and is normalized to `ai_credits` when metered. New counters are written as
`ai_credits_{input_tokens,output_tokens,requests}`; reads still sum the legacy `human_edit_*` counters,
so no stored monthly row is migrated. An exhausted pool answers 402 `ai_credits_exceeded` to callers
that sent `ai_credits`, and the unchanged `human_edit_pool_exceeded` to legacy `human_edit` and
`execute_chat` callers. A new Studio that gets `400 invalid_usage_class` from an older cloud retries once
as `human_edit`.

**Added 2026-09-16 — `execute_seats`.** New numeric meter: people a workspace has granted Conxa
Execute access to via `POST /entitlements/execute-grants` below, independent of Build Studio org
membership. See `docs/TRD.md` §13.7 for the full grant/claim/shared-pool design.

**Added 2026-09-17 — `execute_chat_tokens`.** Line-item breakdown only, always `limit: null,
unlimited: true` — Conxa Execute's chat usage draws from the exact same pool `ai_usage_credits`
gates, it is not a second quota. `ai_usage_credits`'s own `used` figure already includes it.

**GET /api/v1/entitlements/execute-grants** (owner/admin) — `{"grants": [{grant_id, email, status,
granted_at, claimed_at, revoked_at, ...}]}`, newest first. Includes revoked/claimed grants for history.

**POST /api/v1/entitlements/execute-grants** (owner/admin) — `{"email": str}` → creates (or, for a
repeat invite to the same still-pending email, idempotently returns) a `pending` grant, checked
against the `execute_seats` cap. No `invite_url` in the response (removed 2026-09-17) — since Conxa
Execute and the Cloud Dashboard share one Clerk app, the grant auto-claims by verified email on that
person's next Conxa Execute sign-in, nothing to send them.

**POST /api/v1/entitlements/execute-grants/revoke** (owner/admin) — `{"grant_id": str}` → frees the
slot immediately and clears the pool binding if it had already been claimed.

**POST /api/v1/entitlements/execute-grants/claim** (any signed-in Cloud Dashboard user) — `{"grant_id":
str}` → claims a grant made out to the caller's own email. Phase-1 fallback path; the normal path is
automatic (`GET /api/v1/execute/contexts` below).

**GET /api/v1/execute/contexts** (any signed-in user, added 2026-09-17) — `{"contexts": [{workspace_id,
workspace_name, kind: "personal"|"member"|"grant", credits_remaining}], "active_workspace_id": str}`.
Conxa Execute's personal/team context switcher: every workspace the caller can use Execute under.
Also auto-claims any `pending` grant matching the caller's verified email as a side effect. See
`docs/TRD.md` §13.7.

**POST /api/v1/usage/compile/reserve**

Request:
```json
{
  "reservation_id": "cmp_org_123_wf_123_sess_123",
  "workflow_id": "wf_123",
  "session_id": "sess_123"
}
```
Also registers the `X-Conxa-Machine` header (§TRD 13.4a) and checks trial expiry before the reservation attempt.

Response:
```json
{
  "reservation_id": "cmp_org_123_wf_123_sess_123",
  "status": "reserved",
  "remaining_compile_credits": 157
}
```

**POST /api/v1/usage/compile/commit**

Request:
```json
{"reservation_id": "cmp_org_123_wf_123_sess_123"}
```

**POST /api/v1/usage/compile/release**

Request:
```json
{"reservation_id": "cmp_org_123_wf_123_sess_123"}
```
`reserved` → `released` only — a no-credit-ever-spent cancel. Does nothing to a `committed`
reservation (returns its current status unchanged); use refund for that case.

**POST /api/v1/usage/compile/refund** (added 2026-08-23, part of the mega-workflow 502 fix —
see `TRD.md` §13.3)

Request:
```json
{"reservation_id": "cmp_org_123_wf_123_sess_123"}
```
Response:
```json
{"reservation_id": "cmp_org_123_wf_123_sess_123", "status": "refunded"}
```
`committed` → `refunded`: gives back a credit already committed and spent when the compile then
aborted on an infrastructure failure (cloud LLM proxy unavailable, or a vision anchor call that
failed on `vision_llm_request_failed` specifically) rather than anything the user did — the
credit is committed *before* any LLM call (`handlers/compile.py`), so an infra failure otherwise
burns it for nothing. Idempotent (a second refund of the same reservation is a no-op, returns
`refunded`); `409 compile_reservation_not_committed` if called on a reservation that was never
committed (use release for that). Deliberately narrow: Build Studio calls this only for
`CloudUnreachable`/`ProxyUnavailable` and that one `VisionAnchorGenerationError` reason — never
for a content/quality failure (a bad answer, an invalid primary phrase), or repeated bad input
becomes free.

Stable entitlement error details (returned as HTTP `402` for quota-exhausted, `403`/`503` for
config/availability):
- `compile_credit_limit_exceeded` — 402, compile-credit reservation at limit (checked at `/usage/compile/reserve`)
- `human_edit_pool_exceeded` — 402, monthly Human-Edit token pool exhausted (checked at the LLM proxy)
- `seat_limit_exceeded` — 402, workspace seat limit reached (checked in `app/api/deps.py::current_principal` via `entitlements.ensure_seats_available`, added 2026-08-22 — see below)
- `machine_limit_exceeded` — 402, plan's machine limit reached for a new (never-seen) device
- `trial_expired` — 402, Free's 30-day trial window has passed; blocks LLM proxy, compile reserve, skill-pack publish, installer upload
- `distribution_not_permitted` — installer upload (402): requested `distribution=external` but the plan carries `distribution="internal"`. Skill-pack delta-sync (403, §5.9): the requesting machine isn't the Free-tier workspace's own registered device.
- `white_label_not_permitted` — 402, installer upload requested `white_label=true` without the plan's `white_label` capability
- `ops_tier_required` — 403, dashboard/audit/drift route requested above the plan's `ops_tier`
- `entitlements_unavailable` — 503, cloud could not evaluate entitlements (quota-gated actions blocked)
- `invalid_usage_class` — 400

**Enforcement is on by default**
(`entitlements_enforce_compile|_human_edit|_distribution|_machines|_seats = True` in `config.py`; the
old `entitlements_enforce_installers` flag was renamed to `entitlements_enforce_distribution` and
`entitlements_enforce_machines`/`entitlements_enforce_seats` flags added). Workspaces on the
`development` plan, or any plan whose limit resolves to `None` (e.g. an `enterprise` override), are
never blocked on the numeric meters. Enforcement points: skill-pack publish and installer upload
(`publish_routes.py`, both legacy and versioned routes), the compile-credit reserve/commit protocol,
the Human-Edit pool and machine registration at `llm_proxy_routes`, `ops_tier` gates on the
tracking/audit routes, and seat count at `app/api/deps.py::current_principal` (see below).

**Added 2026-08-22 — seat limit enforcement.** Previously, `membership_count_for` only *read* a
workspace's member count for display (Team page, `entitlements/current`'s `seats` meter); nothing
compared it to the plan's limit, so a Free workspace could carry unlimited members via Clerk's own
`<OrganizationProfile>` invite UI, entirely outside Conxa's backend. There is still no Conxa-owned
invite API or Clerk membership webhook to intercept an invite before it happens, so enforcement runs
at the earliest point Conxa's backend actually sees the new member: their first authenticated request.
`current_principal` (`app/api/deps.py`) now checks `saas.is_known_member(user_id, workspace_id)` before
calling `ensure_principal` (which would otherwise silently create that membership row); for a
not-yet-known pair it calls `entitlements.ensure_seats_available(principal)` first, which compares the
workspace's live Clerk member count (`_clerk_org_member_count`, already existed but was previously
unused for enforcement) against the plan's `seats` limit and raises `seat_limit_exceeded` (402) if the
workspace is already at or over its cap. The comparison is `count > limit`, not `>=`, because Clerk's
member count already includes the arriving member by the time they can authenticate. Existing members
are never re-checked or locked out by a later downgrade — same soft-lock shape as `workflow_lock`
above. Uses `billing_for_workspace` (read-only) rather than `billing_for`, since the latter itself
calls `ensure_principal` as a side effect. On any Clerk API failure (or local/dev auth, where there's
no Clerk org to query), the check fails open rather than lock out a workspace it can't measure.

**Slug ownership** (`services/entitlements.py`, `app/api/product_ownership.py`): unchanged as a
security boundary — a slug is claimed the first time a workspace publishes a skill pack or uploads an
installer for it, tracked in the `publish_owners` KV namespace (one row per slug,
`{slug, workspace_id, claimed_at}`). `_assert_owner`/`_assert_not_owned_by_other` still gate slug
takeover; there is no longer a *count* limit on how many slugs a workspace may claim.

### 5.4 Billing

The live payment gateway is **Cashfree** (switched from Razorpay 2026-06-30 — see `cashfree_routes.py`, mounted at `/api/v1/subscriptions`).

**POST /api/v1/subscriptions/create**

Request:
```json
{"tier": "starter", "customer_email": "...", "customer_phone": "..."}
```

`tier` accepts `starter`, `pro`, or one of the compile-credit add-on tiers (see below — `enterprise` is
sales-assisted, never self-serve checkout). Calls Cashfree's
`POST /api/v2/subscriptions/nonSeamless/subscription` server-side and returns:
```json
{
  "subscription_id": "<Cashfree subReferenceId>",
  "auth_link": "https://payments.cashfree.com/...",
  "plan_id": "<Cashfree planId>",
  "amount": 19999,
  "currency": "INR",
  "tier": "starter"
}
```

The workspace↔subscription↔tier mapping is stored server-side in the `cashfree_sub_workspace` KV namespace (keyed by `subReferenceId`) for later webhook lookup, since Cashfree webhooks only carry the subscription reference id, not the originating workspace. The frontend redirects the user to `auth_link` to complete payment.

**Compile credit add-ons** (rewritten 2026-08-22 - four self-serve SKUs, each a ONE-TIME purchase
via a Cashfree Payment Link; catalog in `entitlements.ADDON_TIERS`):

| tier | price | grants (once, never expire) |
|---|---|---|
| `credits_addon_20` | ₹3,999 | +20 compile credits + 200k Human Edit tokens |
| `credits_addon_50` | ₹9,999 | +50 compile credits + 500k Human Edit tokens |
| `credits_addon_100` | ₹19,999 | +100 compile credits + 1M Human Edit tokens |
| `credits_addon_250` | ₹49,999 | +250 compile credits + 2.5M Human Edit tokens |

(The original single `credits_addon_25` pack at ₹4,999/mo was retired in favour of this ladder.)

**POST /api/v1/subscriptions/addon/order** (owner/admin)

Request: `{"tier": "credits_addon_20"}`. Creates a Cashfree Payment Link (`POST /pg/links`) for
the pack amount and remembers the workspace/tier against the `link_id` in the
`cashfree_order_workspace` KV namespace. Returns `{"granted": false, "link_id": "...",
"payment_link": "https://...", "tier": "..."}` — the frontend redirects to `payment_link`. In
local dev (no Cashfree credentials and auth off) the wallet is credited immediately and `granted`
is `true` with an empty link. Errors with `unknown_addon_tier` (400).

**Grant path (webhook + verify, exactly once).** Payment is confirmed two ways: the PG webhook
**POST /api/v1/subscriptions/webhooks/cashfree-orders** (`PAYMENT_SUCCESS_WEBHOOK`; signature is
HMAC-SHA256 over `x-webhook-timestamp + raw body`, base64, in `x-webhook-signature`), or the
frontend calling **POST /api/v1/subscriptions/addon/verify** with the pending `link_id` after the
redirect back (belt to the webhook's braces — webhooks can't reach a locally-running backend).
Both funnel into `_grant_addon_order_once`, which credits `billing["credit_wallet"]`
(`{"compile_credits", "human_edit_tokens"}`) via `entitlements.grant_credit_wallet` exactly once,
guarded by the `cashfree_orders_granted` KV namespace so replays and double deliveries are
harmless. The wallet never expires and is drawn down only after the plan's monthly allowance is
exhausted (`reserve_compile_credit`/`commit_compile_credit` for compiles;
`record_llm_usage`/`ensure_human_edit_available` for Human Edit tokens). No Cashfree *plan IDs*
are involved for add-ons. The catalog is also served read-only at
**GET /api/v1/subscriptions/addons**.

**Removed 2026-08-22:** `POST /api/v1/subscriptions/addon/cancel` and the subscription-based
activation/cancellation branches in `/verify` and `/webhooks/cashfree` — one-time purchases have
no mandate to cancel and no credits to claw back.

**POST /api/v1/subscriptions/verify**

Request:
```json
{"subscription_id": "<Cashfree subReferenceId>"}
```

Response:
```json
{"success": true}
```

Fetches `GET /api/v2/subscriptions/{subscription_id}` from Cashfree, resolves the tier from the returned `planId`, and upserts the billing record (`plan`, `status: "active"`, `subscription_id`, `current_period_end`).

**POST /api/v1/subscriptions/webhooks/cashfree**

Cashfree webhook endpoint. When `cashfree_webhook_secret` is configured, the signature is verified by sorting all `cf_`-prefixed payload fields, concatenating `key+value` pairs, and comparing against the provided signature using the shared secret (`_cf_webhook_signature()`). Activation/charge events refresh `current_period_end`; cancellation clears it.

### 5.5 LLM Proxy Usage Class

`POST /api/v1/llm/proxy/text` and `/vision` accept:

```json
{
  "task": "anchor_vision",
  "payload": {},
  "timeout_ms": 120000,
  "usage_class": "ai_credits"
}
```

Allowed `usage_class` values are `compile`, `ai_credits` (canonical; also covers every Studio chat, Clarify, auto-fix and Human Edit call), `human_edit` (legacy alias of `ai_credits`) and `execute_chat`. Missing `usage_class` defaults to `compile`.

**`POST /api/v1/llm/proxy/text/stream` and `/vision/stream`** (BUILD-26 stage c) — same request
body as above, for a task that wants its reply streamed (currently only `copilot_reply`). Response
is `text/event-stream`: zero or more `data: {"delta": "..."}` lines as text arrives, then either
`data: {"done": true, "text": "<full text>"}` or `data: {"error": "<message>"}` if every provider
failed before producing any text. Usage is metered once, after the stream ends, from the full
accumulated text — never per chunk. Entitlement/quota/workspace-concurrency checks happen before
the stream opens, same as the blocking endpoints; once streaming has started, a downstream
entitlement-recording failure can only be logged (the reply has already reached the client).

Response when up-to-date:
```json
{
  "current_version": "0.2.0",
  "base_version": "0.2.0",
  "files": []
}
```

### 5.6 Telemetry Ingest

**POST /api/tracking/{company}/events** (also served at `/api/v1/tracking/{company}/events` — both are permanent back-compat aliases, see CLAUDE.md Key Invariants) and its versioned equivalent **POST /api/v1/workflows/{installer_version}/{company}/tracking/events** (§5.1a).
**Header: X-Tracking-Token: {token}**

Request: See §4.1 above.

Response (202):
```json
{"ok": true}
```

### 5.7 Tracking Runs Query

**GET /api/v1/tracking/{company}/runs?limit=50&offset=0**
**Header: Authorization: Bearer {clerk_jwt}**

Response:
```json
{
  "runs": [
    {
      "run_id": "run_abc123",
      "workflow_id": "acme",
      "workflow_ver": "0.2.0",
      "runtime_ver": "1.0.0",
      "uid": "...",
      "wid": "...",
      "status": "ok",
      "duration_ms": 20000,
      "total_steps": 5,
      "recovered_steps": 1,
      "failed_step_id": null,
      "failure_code": null,
      "started_at": 1717000000,
      "server_ts": 1717000025.3
    }
  ],
  "total": 1,
  "workspace_id": "org_clerk123"
}
```

**GET /api/v1/tracking/{company}/runs/{run_id}** additionally returns `summary` (the same
run-summary shape as above) and `steps` — the per-step outcome used by the run drill-down:

```json
{
  "steps": [
    {"index": 0, "label": "Step 1", "status": "ok", "tiers": [], "assertionsPassed": 1, "assertionsFailed": 0},
    {"index": 2, "label": "Step 3", "status": "recovered", "tiers": ["Tier A"], "assertionsPassed": 0, "assertionsFailed": 0},
    {"index": 3, "label": "Step 4", "status": "failed", "tiers": [], "assertionsPassed": 0, "assertionsFailed": 1},
    {"index": 4, "label": "Step 5", "status": "not_reached", "tiers": [], "assertionsPassed": 0, "assertionsFailed": 0}
  ]
}
```

`status` is derived server-side (`tracking_analytics.run_step_flow`) rather than in the
frontend, so the run view and the dashboard aggregates classify a recovery the same way.
`tiers` uses the product labels `Tier A` (in-process) and `Tier B` (agent). Runtime event codes may still say `recovery_tier{N}` (1–2 = A, 3–4 = B).
`run.js` does not reliably emit a per-step success event, so a step is treated as healed
unless it is positively known to have failed; steps after the failing one are reported as
`not_reached` rather than omitted.

### 5.7a Operations Analytics

Aggregation for the operations dashboard lives in `app/services/tracking_analytics.py` as
pure functions over the record list `tracking._visible_run_records` produces. Everything is
derived from telemetry the runtime already emits — no new event codes, no LLM calls.

**GET /api/v1/tracking/dashboard?range={24h|7d|30d|90d}** — unknown values fall back to `7d`.

Returns the pre-existing keys (`metrics`, `recovery_type_usage`, `recovery_usage_by_step`,
`recovery_usage_by_workflow`, `most_failed_workflows`, `most_failed_steps`,
`execution_trend`, `assertion_health_by_step`) plus the keys below. Each
`assertion_health_by_step[]` row also carries `previous_pass_rate` (added 2026-09-29): the same
step's pass rate over the previous equal period, or `null` if it reported no checks then.

| Key | Shape |
|---|---|
| `granularity` | `"hour"` for `24h`, `"day"` otherwise |
| `series[]` | `{bucket, at, executions, successful, failed, recovered, success_rate, avg_duration}` — pre-seeded so quiet periods are zeros, not gaps. `success_rate` is `null` when nothing completed, which is distinct from a genuine 0% |
| `kpis[]` | `{key, label, unit, direction, value, previous, delta, delta_pct, series[]}`. `direction` (`up_good`/`down_good`) tells the UI which way is an improvement per metric |
| `health` | `{score, grade, factors[], summary}`. `score` is `null` (grade `"No telemetry"`) for a workspace with no runs — never 0. Factor weights sum to 100: success rate 40, assertion pass rate 20, drift resistance 15, zero-token healing 15, runtime freshness 10 |
| `workflows[]` | Per-skill rollups grouped by `(company, workflow_id)` — runs, success rate, `success_rate_delta` vs the previous equal period, recovery/unattended rate, p50/p95 duration, and a nested `versions[]` breakdown (`{version, runs, success_rate, recovery_rate, last_seen, first_seen}`, newest `last_seen` first). `first_seen` (added 2026-09-29) is the version's first run **inside the window**, not its publish time |
| `recovery_cascade` | Sankey `{nodes, links}` over `Entered recovery → Tier A → Tier B → Healed \| Failed`, plus `entered_recovery`, `healed`, `failed`, `heal_rate`, `resolved_directly`, `tier_touch[]`, `zero_token_heals`, `agent_assisted` |
| `reliability_heatmap` | `{cells[{weekday, hour, runs, successful, failed, success_rate}], max_runs}`, UTC |
| `failure_codes[]` | `{code, count, last_seen, workflow_count}` |
| `roi` | `{assumptions, estimated{...}, measured{...}}` — see below |
| `insights[]` | `{id, severity, title, body, metric, evidence}`, most severe first, capped at 8 |
| `stale_runtimes` | Registrations with no report in 30+ days |
| `reconciliation` | PROD-18 — see §5.7c below. Computed from the same `current` records this table's own scan already produced; no extra query |

Only steps that entered recovery appear in `recovery_cascade`; the far larger directly-resolved
population is reported as `resolved_directly` instead, because folding it in would compress
every other band to a hairline. `zero_token_heals` counts **steps that healed without ever
reaching a paid tier** — not Tier A event hits, which would double-count a step that tried
both and would credit a step that only succeeded after escalating to a model.

`roi.estimated` (hours saved, value) depends on the stored assumptions; `roi.measured`
(unattended completions, self-healed runs, zero-token heals, agent-assisted heals) does not.
They are separate objects so the UI can never present an assumption as a measurement.

`roi.assumptions.per_workflow` (keyed `"{workspace_id}/{workflow_id}"`) is auto-seeded from
`Workflow.recording_duration_seconds` the first time a skill publishes successfully
(`publish_routes._publish_skill_pack_impl` → `tracking_analytics.seed_recorded_baseline`) —
seed-once: an existing key, whether from an earlier auto-seed or an admin's own edit, is never
overwritten by a later publish. Each `roi.estimated.by_workflow[]` row also nets
`HUMAN_OVERSIGHT_SECONDS` (a fixed 20s/run — the human still has to start each run) out of
`minutes_saved`/`hours_saved`, and carries a measured `automated_minutes` (summed
`summary.duration_ms` for that workflow) alongside the assumption-derived baseline, so the UI
can show recorded baseline vs. measured automated time vs. net hours saved on one row.

**GET /api/v1/tracking/activity?limit=50&before={epoch_ms}** — recent runs across every
visible company, newest first, each with `recovery_tiers[]`. Separate from the dashboard
payload because a live feed polls often and must not re-run the full aggregation. The Cloud
dashboard stopped calling it on 2026-09-29 (the Overview no longer shows a live feed); the
endpoint is kept for API clients.

**GET /api/v1/tracking/workflows/{company}/{slug}?range=** — step-level drill-down for one
skill: `summary`, `series[]`, `steps[]`, `recovery_cascade`, `failure_codes[]`,
`assertion_health[]` (with `previous_pass_rate`, as above), `recent_runs[]`.

**GET | PUT /api/v1/tracking/roi-assumptions** — read/write the `roi_assumptions` row for the
caller's workspace. `PUT` requires admin or owner (`app.services.rbac.require_admin`) and
normalizes input rather than trusting it.

> **One scan per request.** `_visible_run_records` reads every run's full event list out of
> KV, so it is the dominant cost of the dashboard. `tracking_analytics.dashboard` performs
> that scan once and derives every block from it, passing the records into
> `tracking._dashboard_metrics` via its optional `records` parameter. Adding a further
> endpoint that re-scans for one more panel would repeat the most expensive thing the
> dashboard does — extend the composed response instead.

### 5.7b Governance Policy (PROD-18)

**PUT /api/v1/tracking/{workspace_id}/policy**
**Header: Authorization: Bearer {clerk_jwt}** — admin only (`require_admin`), gated `ops_tier=full`.

Request body: `{windows: [...], denied_hosts: [...], enforce, require_receipt, ttl_s}` — see
§4.2a for the field shapes. Validates every window's `tz` against the IANA database
(`zoneinfo`), `days` (ints 1–7), and `start`/`end` (`HH:MM` 24h) — a validation failure
returns **422** with the offending field named, not a 500. Increments `policy_version`,
stamps `issued_at`/`expires_at` (`ttl_s`, default 7 days), signs, and stores. Returns the
signed document (§4.2a shape).

**GET /api/v1/tracking/{workspace_id}/policy**
**Header: X-Tracking-Token: {token}** — the same per-workspace tracking token ingest uses,
not a Clerk session. This is deliberate: a workspace with telemetry disabled has no token
and therefore cannot fetch policy either — you cannot govern what you cannot audit (see
`docs/Audit-and-Control.md` §5). Returns the stored signed document, or **404** if the
workspace has never had a policy written (a signal the runtime treats as "genuinely
ungoverned," distinct from a fetch failure — see `runtime/app/policy_gate.js::loadPolicy`).
No signing happens on this read path; signing happened once at `PUT` time, matching
`GET /api/v1/manifest.json`'s pattern (§5.8).

### 5.7c Reconciliation (PROD-18)

**GET /api/v1/tracking/{workspace_id}/reconcile?range={24h|7d|30d|90d}**
**Header: Authorization: Bearer {clerk_jwt}** — gated `ops_tier=full`, the same tier `/drift`
requires; this is an audit surface, not a basic metric.

```json
{
  "range": "30d",
  "runs_started": 1412,
  "runs_completed": 1388,
  "runs_abandoned": 19,
  "runs_in_flight": 5,
  "abandoned_runs": [{"run_id": "...", "workflow_id": "...", "company": "...", "last_seen": 1717000000}],
  "chain": {
    "verified": 1201, "gap": 2, "broken": 0, "none": 209,
    "gap_runs": [{"run_id": "...", "missing_seqs": [2]}],
    "broken_runs": [{"run_id": "..."}]
  }
}
```

`runs_abandoned` = a run whose most recent activity (`app.services.tracking._record_time_ms`)
is more than 10 minutes old and which never emitted a terminal `wf_ok`/`wf_fail` — the
"evidence never arrived" case `_completed()` silently excludes from every success-rate
number elsewhere in the dashboard (see `docs/Audit-and-Control.md` §1). `runs_in_flight` is
the same "no terminal event yet" state within the 10-minute grace, i.e. genuinely still
running. Backed by `app.services.tracking_analytics.reconciliation()`, called both here
(its own `_visible_run_records` scan, since this is human-invoked at audit cadence, not
polled) and inside `dashboard()` (§5.7a, reusing that endpoint's existing scan for free).

### 5.8 Unified Signed Runtime Manifest

**GET /api/v1/manifest.json** — the single source of truth for `runtime/manifest_manager.js`'s self-updater. Replaces the three previous manifest endpoints (still served as deprecated shims — see below). Served straight from `manifest` KV; signed once at publish time, not on the read path.

**Update channels.** An optional `?channel=dev|stable` query param selects the update channel (default `stable`). Dev builds (prerelease tags) publish to `dev`; the promotion workflow re-publishes the exact signed artifact to `stable`. Channel data is namespaced in KV — `stable` keeps the original unsuffixed `component_versions` / `manifest` namespaces (byte-identical to before), `dev` uses `:dev`-suffixed namespaces — so a dev runtime (`CONXA_UPDATE_CHANNEL=dev`) never sees a stable build and vice-versa. The same server-side Ed25519 key signs every channel; the runtime's baked-in public key verifies all of them. The response carries a `"channel"` field. `POST /admin/component-versions/{component}?channel=<channel>` targets the same dimension.

Response (`packages/conxa-core/conxa_core/models/manifest.py:UnifiedManifest`):
```json
{
  "manifest_version": 3,
  "generated_at": "2026-07-01T00:00:00Z",
  "mcp_protocol_version": "2024-11-05",
  "minimum_versions": {"conxa_runtime": "host-v1.0.0", "conxa_app": "app-v1.0.0"},
  "compatibility": {},
  "conxa_runtime": {
    "version": "host-v1.1.0",
    "released_at": "2026-07-01T00:00:00Z",
    "required": false,
    "files": [
      {"filename": "conxa-runtime.exe", "url": "https://github.com/.../conxa-runtime.exe", "sha256": "abc123..."},
      {"filename": "keytar.node", "url": "https://github.com/.../keytar.node", "sha256": "def456..."}
    ],
    "rollout": {"percentage": 100, "halted": false}
  },
  "conxa_app": {
    "version": "app-v1.5.0",
    "min_host": "host-v1.0.0",
    "files": [{"filename": "conxa-app-app-v1.5.0.zip", "url": "https://github.com/.../conxa-app-app-v1.5.0.zip", "sha256": "abc123..."}],
    "rollout": {"percentage": 100, "halted": false}
  },
  "skill_packs": {
    "acme": {
      "invoice-automation": {"version": "v1.2.0", "min_runtime": "app-v1.0.0", "files": []}
    }
  },
  "signature": "base64-encoded-ed25519-signature"
}
```

**Digest env vars are shape-validated at import time (added 2026-09-18).** Every `sha256`/`sha512`
field above the legacy `deps-manifest`/`conxa-runtime-manifest`/`conxa-app-manifest` shims serve
(`app/api/updates_routes.py`'s `CONXA_NSIS_SHA256`, `CONXA_HOST_WIN_SHA256`,
`CONXA_KEYTAR_WIN_SHA256`, `CONXA_APP_BUNDLE_SHA256`, `CONXA_STUDIO_WIN_SHA256/SHA512`,
`CONXA_EXECUTE_WIN_SHA256/SHA512`) is read straight from the environment as a module-level
constant. A live incident showed why that needs a check: one of these was set to a
release-notes-style `"<filename>  <hash>"` two-column line instead of the bare hex digest — every
client's own checksum verification then rejected the download forever, silently, with nothing
pointing back at the env var. `_validate_digest_env_vars()` now runs once at module import and
raises `RuntimeError` (refusing to boot) if any non-empty digest env var isn't exactly 64 or 128
lowercase hex characters. An unset var is still allowed — the dependent manifest entry is simply
skipped, unchanged from before.

`signature` is computed over the canonical JSON (sorted keys, no whitespace, `signature` field excluded) of every other field, using an Ed25519 private key held only as the `CONXA_MANIFEST_SIGNING_KEY` env var — never in CI. The runtime verifies it against a public key baked into the host exe at build time; a failed verification is treated exactly like a network failure (fall back to the last verified cache, or skip entirely on first run). `skill_packs[].files` is deliberately empty — skill content is delivered through the existing per-company delta-sync (§5.9) which is Bearer-token-gated per company, not broadcast in a public manifest.

**POST /api/v1/admin/component-versions/{component}** — CI (after host/app build) and `release_routes.py` (after a skill is Released/Deployed — see §5.1d; **not** at publish time) write a component's version record here; the manifest is recomposed and re-signed immediately after. `component` is `conxa_runtime`, `conxa_app`, or `skill_packs:{company}:{skill}`. Requires `Authorization: Bearer <CONXA_ADMIN_TOKEN>`.

**Deprecated shims** (kept for runtimes that haven't picked up the manifest-driven self-updater): `GET /api/v1/updates/conxa-runtime-manifest` and `GET /api/v1/updates/conxa-app-manifest` now derive their response from the same `component_versions` KV data instead of process-local globals.

**App zip URL rewrite:** `_compose_manifest` rewrites `conxa_app` zip URLs from `github.com/.../releases/download/...` to `{SKILL_API_BASE_URL}/api/v1/updates/artifact/{tag}/{filename}` before signing (no-op when `SKILL_API_BASE_URL` is unset). The route allow-lists `app-vX.Y.Z` tags and `conxa-app-app-vX.Y.Z.zip` names only. `GET /manifest.json` recomposes once if the cached manifest still holds a GitHub URL.

**KV namespaces:** `component_versions` (keys: `conxa_runtime`, `conxa_app`, `skill_packs:{company}:{skill}`) and `manifest` (keys: `current` — the composed+signed manifest; `skill_pack_index` — a list of `{company}:{skill}` identifiers, maintained because the filesystem-fallback KV store hashes keys and can't recover the original string, so skill entries can't be discovered by scanning `component_versions` directly; `minimum_versions`, `compatibility` — operator-set floors/gates).

### 5.9 Skill-Pack Delta Sync

**GET /api/v1/skill-packs/{company}/delta?since={json-map}** (legacy, permanent) and **GET /api/v1/workflows/{installer_version}/{company}/skill-packs/delta?since={json-map}** (versioned, §5.1a) — identical contract, both delegate to `_delta_impl()`/`_build_delta()`. `{installer_version}` is validated but not yet branched on — reserved for a future skill-pack wire-format generation, not dead code.

`since` is a JSON-encoded map of `{skill_slug: last_known_version}` (URL-encoded), letting each skill be compared and shipped independently instead of one shared pack-wide version — republishing one skill never triggers a redownload of the others (see `_skill_version()`/`_build_delta()` in `skillpack_update_routes.py`).

The delta response's `skills` array is the **authoritative full skill list** for the company — `runtime/sync.js` writes it back into local `pack.json.skills` on every successful fetch (whether or not anything changed), which is what makes a thin installer (shipping with `pack.skills` empty) and a skill added to a company post-install both eventually reach `skill_loader.js`'s registry. This is also the entire mechanism behind archiving a skill (§5.1d): archive removes the slug from the cloud `pack.json`, so it simply has no entry in the next delta response, and the runtime's post-sync `pack.skills = serverSkillNames` write drops it from the local list too — no separate "removed" action exists. The skill's files stay on disk under its own `{group}/{slug}/` directory; `skill_loader.js`'s `loadSkillRegistry`/`loadSkillRegistryFromCache`/`hotReloadSkill` all filter strictly against `pack.skills`, so a skill present on disk but absent from that list is never indexed, listed, or executable.

Authentication: `Authorization: Bearer <sync_token>` where `sync_token` is the per-company token minted at publish time (`publish_routes._sync_token()`) and stored in the `sync_tokens` KV namespace. The token is embedded in `pack.json` at publish and ships inside the installer — the runtime reads it directly with zero user interaction.

- Production (`SKILL_AUTH_REQUIRED=true`): 401 if token is missing or does not match stored token.
- Local dev (`SKILL_AUTH_REQUIRED=false`): validation skipped.

Response:
```json
{
  "skills": [
    {"name": "invoice-automation", "version": "v1.2.0", "action": "update", "group": "grp_a1b2c3",
     "files": [{"path": "execution.json", "sha256": "abc123...", "content_base64": "..."}]},
    {"name": "approval-workflow", "action": "no_change", "group": "_default"}
  ]
}
```

`group` is the skill's directory slug from `pack.json`'s `skill_groups` map (falling back to `"_default"`) — see "Compiled skill-pack layout" above for how that slug is derived from the group's `id`+name — telling `runtime/sync.js` which nested `skill-packs/{company}/{group}/{skill_slug}/` directory to write into (§2.2, `docs/TRD.md` §5.3) — present on both `"update"` and `"no_change"` entries since a "no_change" skill on a company that hasn't republished since group-nesting shipped still needs its group reported so the client's next `since` computation stays correct. `_build_delta()` falls back to a company's old flat `skill-packs/{company}/{skill_slug}/` cloud-storage location if the nested one doesn't exist yet (packs published before this feature), so already-published companies keep syncing without needing to republish.

Each skill's version is read from `component_versions` KV (`skill_packs:{company}:{skill}`, written at publish time), falling back to the shared `pack.json.skill_pack_version` for packs published before independent per-skill versioning existed.

The sync_token is also returned in the publish response so the Build Studio can write it into the local pack.json before staging the installer:

```json
{
  "slug": "acme",
  "version": "0.3.0",
  "sync_token": "aBcDeFgH...",
  "sync_url": "/api/v1/skill-packs/acme/delta",
  "tracking": {...},
  "workspace_id": "org_...",
  "published_at": 1717000000.0,
  "plan": "free",
  "distribution": "internal"
}
```

`plan`/`distribution` (added 2026-08-09) are informational — surfaced so Build Studio can reflect the
workspace's current plan/reach in its own UI without a separate `/entitlements/current` round trip. An
earlier revision of this field pair drove a machine-lock stamp (`pack.json.build_machine_id`); that
mechanism was removed the same day — see §5.1c and `docs/TRD.md` §13.5.

**KV namespace:** `sync_tokens` — keyed by slug, stores `{token, company, version, workspace_id, owner_user_id, updated_at}`.

### 5.9a Company Agent Fleet — Read + Revoke (Machine Registry)

**GET /api/v1/telemetry/runtimes?limit=100&offset=0** (authenticated, workspace-scoped) — paginated `runtime_registrations` for the caller's workspace. `limit` clamps to 1–500 (default 100), `offset` clamps to ≥0. `stale_count`/`version_distribution` are computed over the **full** filtered set (not just the returned page) so they never undercount past page one. Each row gets a computed `status` (`revoked` | `active` | `stale`) alongside the existing `stale` bool. Response adds `total`, `limit`, `offset` to the pre-existing `registrations`/`stale_count`/`version_distribution` shape. Pagination is an in-process slice over `db_list` — not a DB-level query — a deliberate simplification good to roughly 10k rows per workspace; revisit if that changes.

**POST /api/v1/telemetry/runtimes/revoke** (owner/admin) — `{install_id}` → sets `revoked: true` on every `runtime_registrations` row for that `install_id` within the caller's workspace. **This is a dashboard label only** — unlike CBS's `machine_limit_exceeded` enforcement (§5.3/§13.4a), Company Agent carries no machine limit and revoke never touches `_delta_impl` (skill sync), `post_telemetry_runtime_start` (phone-home), or execution. There is no un-revoke endpoint; a reinstall generates a fresh `install_id` (`runtime/install_identity.js`) that registers clean.

Dashboard: `conxa-cloud/frontend/src/FleetPage.tsx` (route `/fleet`) — search/filter table over these registrations, styled to match the CBS device list (`SettingsPage.tsx`) and the per-skill Deployment panel (`DeploymentPanel.tsx`).

### 5.9b Bug Report Submission (Manage → Report a bug, 2026-09-24)

**POST /api/v1/bug-reports** (authenticated) — `{description, page_url?, contact_email?, attachments?: [{filename, content_type?, content_base64}]}` → `{ok: true}`. Sends the report as an email via Resend's HTTPS API (`app/api/bug_report_routes.py`); no DB row is written — this is a stateless mailer, not a stored ticket. `resend_api_key`/`bug_report_to_email` unset → `503 bug_reports_not_configured`. More than 5 attachments, or their combined decoded size over `bug_report_max_bytes` (25MB default) → `413`. A Resend send failure → `502 bug_report_send_failed`.

Not routed through the Next.js `/api/v1/*` proxy: the dashboard page (`ReportBugPage.tsx`) posts straight to the backend using `NEXT_PUBLIC_API_ORIGIN` + a client-fetched Clerk bearer token, so a large screen recording never hits that proxy's ~4.5MB request cap. `app/api/security.py`'s `_body_limit_for_path` raises the body-size ceiling for this one path to 35MB (25MB raw · ~4/3 base64 overhead) rather than reusing the much larger build-artifact cap.

Chosen over SMTP because the backend runs on Render's free plan, which blocks outbound SMTP ports (25/465/587); Resend sends over plain HTTPS instead. `bug_report_from_email` defaults to `onboarding@resend.dev`, which sends without a verified domain but only to the Resend account owner's own address — see TODO.md for verifying a real sending domain before wider production use.

### 5.10 Backend JSON-RPC Protocol (Build Studio)

**Protocol:** Newline-delimited JSON over stdin/stdout.

Request:
```json
{"id": "req_abc", "type": "compile", "payload": {"session_id": "...", "workflow_id": "...", "skill_title": "Submit Expense"}}
```

Result:
```json
{"id": "req_abc", "type": "result", "result": {"skill_id": "skill_...", "version": 2, "step_count": 8}}
```

Error:
```json
{"id": "req_abc", "type": "error", "code": "compile_credit_limit_exceeded", "message": "Monthly compile credits are exhausted for this workspace."}
```

Streaming event:
```json
{"type": "event", "id": "req_abc", "phase": "compile_step", "step": "selectors", "status": "running"}
```

---

### 5.10a Human Review Copilot RPCs (BUILD-26)

Seven `cmd_*` commands on the protocol above (eight since stage g adds `copilot_load_session`),
all Build-Studio-local (§5.10) — none of them routes are added under `/api/v1`, this is stdio
JSON-RPC only.

**`get_failure_evidence`** — `{"skill_id": "skill_...", "run_id": "run_abc123"}` (run_id optional;
falls back to `Workflow.last_test_run_id`) →
```json
{"evidence": {
  "skill_id": "skill_...", "run_id": "run_abc123", "failed_step_key": "h2#1",
  "compile_status": "ok", "second_opinion": [...], "archived_steps": [...],
  "runtime_evidence": {"message": "...", "failed_at": 1, "inventory": [...], "recovery_trail": [...],
                        "failure_screenshot_path": "C:\\...\\failure.jpg", "pre_step_screenshot_path": null,
                        "observed_overlays": [{"overlay_id": "a1b2c3d4e5f6", "step_index": 1, "step_key": "h2#1",
                                                "dismissed": true, "container": {"tag": "div", "signal": "#cookie-banner"},
                                                "controls": [{"tag": "button", "role": "button", "name": "Accept", "testid": "accept-btn"}]}]},
  "steps": [{"step_key": "h1#1", "step_index": 0, "description": "...", "intent": "...",
             "compile_report": {"available": true, "confidence": 0.9, "warnings": []},
             "recorded_event": {"post_condition": {...}, "state_change": {...}},
             "edit_history": [...]}, ...]
}}
```

**`copilot_turn`** — `{"skill_id": "skill_...", "message": "why did step 2 fail?", "transcript": [{"role": "user"|"assistant", "text": "..."}]}` →
```json
{"reply": "Step 2 failed because an overlay covered the Submit button...",
 "proposals": [{"id": "6f1e...", "step_key": "h2#1", "command": "patch_step",
                "field": "validation.assertions", "patch": {"validation": {"assertions": [...]}},
                "why": "...", "evidence_refs": ["h2#1"], "preview": {"before": [...], "after": [...]}}]}
```
Errors: `invalid_input` (empty message), `skill_not_found`, `human_edit_pool_exceeded` /
`quota_exceeded` / `cloud_unreachable` (same codes `retarget_preview` uses for the same reasons).

**Context sent to the model (BUILD-26 stage g)** is now a capability manifest (what step kinds
exist and what the runtime does with each — `capability_manifest.py`) plus a compact workflow
digest (`evidence.py`'s `detail="digest"` — every step, no heavy fields), not the old always-full
evidence dump. The model may ask for detail it needs via a `need` array in its own response —
`{"tool": "expand_step"|"get_step_screenshot"|"get_failure_evidence"|"get_edit_history"|"list_workflows", "args": {...}}`
— resolved server-side and fed back for up to 2 extra rounds before `copilot_turn` returns. This
is internal to the `copilot_turn` call; the caller never sees a `need` array itself, only the
final `{reply, proposals}`.

**A second proposal kind (BUILD-26 stage f)** may appear in the same `proposals` array —
`command: "insert_overlay_branch"`, built from an overlay the runtime actually observed during a
test run (`overlays.jsonl`, below), never a selector the model invented:
```json
{"id": "9a2c...", "step_key": null, "command": "insert_overlay_branch",
 "primitive": "try_dismiss", "overlay_id": "a1b2c3d4e5f6", "after_step_key": "h1#1",
 "patch": {"intent": "try_dismiss_interstitial",
           "branch": {"candidates": ["internal:testid=[data-testid=\"accept-btn\"]", "#cookie-banner"],
                       "timeout_ms": 3000, "fallback_escape": true},
           "recovery": {...}},
 "nested_step": null,
 "why": "seen dismissing a step on run r_abc123", "preview": {"before": null, "after": "Insert try_dismiss after step h1#1"}}
```
For `primitive: "if_present"`, `patch` carries `{intent: "dismiss_if_present", target: {primary_selector: <overlay container signal>, fallback_selectors: []}}` and `nested_step` carries the one
click step's `{target, identity_bundle, intent, semantic_description}` to insert into its branch
body. `identity_bundle` in `nested_step` is built by `editor/overlay_identity.py::
bundle_from_descriptor` — see TRD §7.4 for the durability/orthogonality reuse and why
`source: "runtime"` is a new `IdentitySignal.source` value.

**Streaming (BUILD-26 stage c).** While `copilot_turn` is in flight, the connection also carries
zero or more `event` frames ahead of the final `result` — `{"type": "event", "id": "<rid>", "phase": "copilot_delta", "text": "Step 2 "}` — one per chunk of the reply as the model generates it
(see §7.2a of the TRD for the two-call streaming design behind this). A caller that ignores
`event` frames entirely is unaffected: the final `result` frame still carries the complete
`{reply, proposals}` shown above.

**`accept_copilot_proposal`** — `{"skill_id": "skill_...", "step_key": "h2#1", "patch": {...}, "proposal_id": "6f1e..."}`
→ the exact `WorkflowRevalidationResponse` shape `patch_step` returns (§5.10, `{skill_id, meta, workflow, revalidation, can_undo, can_redo}`), since this command delegates to `cmd_patch_step` internally after resolving `step_key` to the current `step_index`. Error `proposal_stale` if the step no longer exists.

**`accept_copilot_proposal` for an `insert_overlay_branch` proposal (stage f)** — same command
name, payload shaped like the proposal object above (`command: "insert_overlay_branch"`,
`primitive`, `overlay_id`, `after_step_key`, `patch`, `nested_step?`, `proposal_id`) instead of
`{step_key, patch}`. Composes `cmd_insert_step` + `cmd_patch_step` (+ `cmd_insert_branch_step` and
a second `cmd_patch_step` for `if_present`'s nested click) internally — same
`WorkflowRevalidationResponse` return shape. Error `proposal_stale` if `after_step_key` no longer
resolves.

**`reject_copilot_proposal`** — `{"skill_id": "skill_...", "step_key": "h2#1", "proposal_id": "6f1e...", "field": "validation.assertions", "why": "..."}`
→ `{"ok": true}`. Changes no document; only appends a rejection line to `edits.jsonl` (§3.10). For
an `insert_overlay_branch` proposal, `step_key` is omitted and `overlay_id` is sent instead — the
rejection is logged against `"overlay:<overlay_id>"`.

**`copilot_verify`** (stage e) — `{"skill_id": "skill_...", "step_key": "h2#1", "proposal_id":
"6f1e...", "confirmed": false}` →
```json
{"status": "confirm_required", "irreversible_count": 1,
 "irreversible_steps": [{"step_key": "h5#1", "description": "Click \"Submit payment\""}]}
```
No build, no browser on this branch. Call again with `confirmed: true` to actually rebuild
(`cmd_build_skill_package`) and retest (`cmd_test_workflow`) the workflow, streaming both
commands' own progress plus a `{"phase": "copilot_verify", "text": "..."}` event on the same `rid`
channel, then →
```json
{"status": "verified", "verdict": "fixed" | "still_failing" | "progressed", "run_id": "run_...", "message": "Done. URL: ..."}
```
or `{"status": "cancelled", "run_id": "run_..."}` if the reviewer cancelled mid-run — no verdict is
written for a cancel. Every non-cancelled verdict appends one line to `edits.jsonl` via
`edit_log.py::append_verification` (§3.10).

**`copilot_save_session`** — `{"skill_id": "skill_...", "transcript": [{"role": "user"|"assistant", "text": "..."}]}`
→ `{"ok": true}`. Called by the renderer's "new session" button before it clears the conversation
in-memory — appends one line (`{ts, skill_id, messages}`) to `{CONXA_DATA_DIR}/skills/{skill_id}/copilot_sessions.jsonl`
(mirrors `edits.jsonl`'s append-only shape, §3.10) so the outgoing conversation isn't silently
lost. A no-op for an empty transcript; fails silently on a disk error (never blocks starting a
fresh session) — see `conxa_compile/editor/copilot_sessions.py`.

**`copilot_load_session`** (stage g) — `{"skill_id": "skill_..."}` → `{"messages": [{"role": "user"|"assistant", "text": "..."}]}`.
Reads the LAST line of the same `copilot_sessions.jsonl` `copilot_save_session` writes — an
archive that was write-only before this. `[]` when there is no archive yet, or when the last line
is corrupt and no earlier valid line exists. Called once by `copilotStore.ts::ensureFor` the first
time a skill's panel opens in a session, so a reviewer resumes their last conversation instead of
always starting cold.

**A third proposal kind — typed structural ops (stage g)** may also appear in `copilot_turn`'s
`proposals` array — `command: "structural_op"`:
```json
{"id": "c4a1...", "command": "structural_op", "op": "insert_step", "action_kind": "ai_review",
 "after_step_key": "h1#1", "fields": {"ai_review_prompt": "Did the payment go through?"},
 "identity_from_step_key": null, "why": "...", "evidence_refs": ["h1#1"],
 "preview": {"before": null, "after": "Insert ai_review after step h1#1"}}
```
`op` is one of `insert_step` (`action_kind`, `after_step_key`, `fields?`,
`identity_from_step_key?` — required and re-validated whenever `action_kind` needs a page
target), `delete_step` (`step_key`), `move_step` (`step_key`, `after_step_key`), `update_inputs`
(`inputs`, the full replacement list), `replace_literals` (`find`, `replace`). Every op requires
non-empty `evidence_refs`.

**`accept_copilot_proposal` for a `structural_op` proposal (stage g)** — same command name,
payload `{"skill_id": "skill_...", "command": "structural_op", "proposal_id": "...", "ops": [<one or more op objects, same shape as above minus id/command/why/evidence_refs/preview>]}`.
Applies every op in `ops` as ONE all-or-nothing batch through the same `cmd_*` RPCs a manual edit
uses (`insert_step`, `patch_step`, `delete_step`, `reorder_steps`, `update_workflow_inputs`,
`replace_literals`) — same `WorkflowRevalidationResponse` return shape as the other two accept
paths, but collapsed to exactly ONE `can_undo` entry regardless of how many ops the batch
contained. Any op failing (most commonly `proposal_stale` — a named `step_key` no longer exists)
aborts the whole batch and restores the document to its pre-batch state; no partial batch is ever
left applied.

---

### 5.11 Legal Acceptance (Build Studio Terms & Privacy)

Build Studio blocks on these routes after sign-in: it reads the current legal version, checks whether the signed-in user has accepted it, and records the acceptance. All four are Clerk-authenticated — an acceptance is only evidence if the accepting party is identified, so there is deliberately no unauthenticated variant. Backed by the `legal_acceptances` KV namespace (§7) and mirrored into the dashboard Audit page as the `legal.accepted` action.

The document text itself is frozen in `conxa-cloud/backend/app/legal/{terms,privacy}-{version}.md` — a rendered copy of the published docs page — and its SHA-256 is what an acceptance record points at. **Changing the terms means adding a new dated pair of files and bumping `CURRENT_LEGAL_VERSION`, never editing a snapshot in place**: the version bump is what re-prompts every user, and the frozen file is what proves which words they were shown.

**`GET /api/v1/legal/current`**
```json
{
  "version": "2026-08-29",
  "documents": [
    {"id": "terms", "title": "Terms And Conditions", "url": "https://www.conxa.in/docs/terms", "sha256": "…"},
    {"id": "privacy", "title": "Privacy Policy", "url": "https://www.conxa.in/docs/privacy", "sha256": "…"}
  ]
}
```

**`GET /api/v1/legal/acceptance`** → the caller's own status:
```json
{"version": "2026-08-29", "accepted": true, "accepted_at": 1756400000.0, "record_id": "legal_1756400000000"}
```

**`POST /api/v1/legal/acceptance`**
```json
{"version": "2026-08-29", "document_hashes": {"terms": "…", "privacy": "…"}, "app_version": "1.4.2"}
```
Returns `{"accepted": true, "record": {…}}`. The client IP is taken from the first `x-forwarded-for` hop (Render fronts the app with a proxy), and the machine identifier from the `X-Conxa-Machine` header Build Studio already sends. **409 `legal_version_mismatch`** if the submitted version or either document hash does not match what the server serves — a client can never record acceptance of text this server did not hand it. Idempotent: a repeat POST returns the first row unchanged.

**`GET /api/v1/legal/acceptances`** — admin-only (`require_admin`), every acceptance row for the caller's workspace. This is the evidence export.

### 5.12 Conversation commands (BUILD-37, 2026-10-18)

Studio stdio RPC (`handlers/conversation.py`), not cloud HTTP. Thread ids are `wf:<workflow_id>` or `chat:<chat_id>`.

| Command | Payload | Result |
|---|---|---|
| `chat_list` / `chat_create{title?}` / `chat_rename{chat_id,title}` / `chat_delete{chat_id}` | | `{chats}` / `{chat}` / `{chat}` / `{deleted}` |
| `thread_load{thread_id, before?, limit?}` | `before` = index into the folded list | `{items, start, total, has_more, cursor, thread_id}` + `chat` or `{workflow, lifecycle}` |
| `thread_turn{thread_id, message?, turn_id?, anchor?, card_id?, choice?, action?}` | a message, or an answer to a card | `{turn_id, status: done or cancelled, reply, items[, result]}` |
| `thread_cancel{turn_id}` | | `{cancelled}` |
| `card_action{thread_id, card_id, action, payload?}` | `pending_change`: apply / undo / dismiss; `draft_workflow`: create / create_and_record / edit_details / dismiss; `error`: retry | `{ok, ...}`; draft: `{workflow_id, start_recording}` or `{needs, open_form}` |
| `get_lifecycle{workflow_id}` / `list_lifecycles` / `count_publishable` | | `{lifecycle}` / `{lifecycles}` / `{count}` |

Events (id = request id): `thread_item{thread_id, item}` (an appended item, or a card's new revision), `thread_delta{thread_id, turn_id, text}`, `thread_turn{thread_id, turn_id, status: started or queued}`.

`lifecycle = {stage, substate, glyph (ok, warn, fail, run or none), note, allowed_actions, rows[{label,kind,note}]}`. Item shape: `{id, ts, kind, ...}`; cards add `card_id, revision, card_type, state, refs, ...`. Card types so far: `pending_change` (`proposal`, `preview`, `why`, `undo_steps`, `applied_version`), `draft_workflow` (`draft{name,group,starting_page,task}`), `error` (`code`, `body`).

**Sign-in and recording commands (BUILD-38).** `flow_begin{workflow_id}` (sign in first if an app the workflow uses has no saved sign-in, else record; reports `already` if one is running), `flow_sign_in_done{workflow_id, card_id, force?}` (saves the sign-in for the group, then goes on to recording; an unconfirmed sign-in keeps the window open and revises the card with a `note`), `flow_sign_in_cancel`, `flow_pause_recording`, `flow_resume_recording`, `flow_finish_recording` (also “Save N actions” after a closed browser), `flow_discard_recording`, `flow_set_varies{session_id, sequence, varies: true|false|null}`, `recording_rows{session_id}` → `{rows, varies, paused}`. Each wraps the existing `start_group_app_auth` / `finish_group_app_auth` / `start_recording` / `stop_recording` / `cancel_recording` and writes its card and event row only after the wrapped command returned. Event `recording_event{session_id, workflow_id, row{sequence, kind, text, value, can_vary, tab}, count}` (not persisted). Cards: `sign_in` (`waiting`, `saved`, `cancelled`; fields `app_name`, `login_url`, `group_name`, `note`, `can_force`) and `recording` (`recording`, `paused`, `saving`, `saved`, `discarded`, `browser_closed`; fields `started_at`, `host`, `count`, `duration_seconds`, `warnings`). `create_workflow` accepts `task_description` and writes the thread's first event (“Workflow created”). New file: `data/sessions/<session_id>/hints.json`.

**Compile and Clarify (BUILD-39 / BUILD-32).** An answered `chosen_values` question runs the editor command `parameterize_literal{skill_id, literal, name, default}` (internal to the Clarify card; logged with `source="clarify"`) which rewrites the literal to `{{name}}` in navigate `url` and `entity_binding.identifier` only and declares the input; `lifecycle.allowed_actions` omits `test` while any asked question is open (`clarify_store.summary().open`). The description check is not an LLM task of its own: `workflow_review` also returns `understanding` (`{goal_summary, coverage, values[]}`, stored as `compile_report.understanding` with `for_task`) and `loop_copy`. `clarify.json` question statuses are `open`, `queued`, `applying` (an answer is being applied; carries `base_version` and `applying_since`), `answered` and `skipped`; a `building: true` marker with no questions means a compile's check was cut short and is rebuilt when the workflow opens. `flow_compile{workflow_id}` (also started in the background by `flow_finish_recording`; pass `auto_compile: false` to opt out). Cards: `compile` (`queued` with `position`, `ahead_name`, `ahead_workflow_id`; `running`/`done` with `rows[{key,label,status,detail}]`, `credits_left`, `step_count`; `out_of_credits`; `failed` with `error`, `failed_stage`, `refunded`; `deferred`; actions `retry`, `compile_later`), `clarify_question` (`question_id`, `question_kind` = coverage | chosen_values | upload | fork | known_popup | confirm | values | popup | done_when, `title`, `body`, `choices[{id,label,detail,selected}]`, `multi`, `index`, `total`; states `open`, `applying`, `answered` + `receipt`, `skipped`, `failed` + `error`; `card_action` `choose` with `payload {choice}` or `{selected: [...]}`, and `skip`), `workflow_summary` (`summary{name, version, recorded_actions, steps, added_steps, inputs, uncertain, step_names, done_when}`). `data/workflows/<id>/clarify.json`: `{skill_id, version, recorded_actions, added_steps, step_count_at_compile, questions[{id, kind, title, body, choices, multi, step_keys, status, answer, receipt, card_id}]}`. Edit-log `source` gains `"clarify"` (and, later, `"auto_fix"`). The compile sub-step event `compile_log` is forwarded to observers with `step_index`.

**Test, auto-fix and approval (BUILD-40).** `flow_test_prepare{workflow_id}` (posts or reuses the open `test_inputs` card), `flow_test_run{workflow_id, card_id, inputs, guard (default true), show_browser}` (runs in the background; refuses missing required inputs with `inputs_required` and a second concurrent test with `test_in_progress`), `flow_test_decision{workflow_id, decision: run_rest|yes|end_here}` (answers a paused test; `not_waiting` if nothing is), `flow_test_stop`, `flow_approve{workflow_id, card_id}` (needs a passing card and `last_test_status == passed`, else `not_passed` / `test_required`), `flow_autofix_stop`, `flow_autofix_resume`, `get_studio_settings` / `set_studio_settings{autofix_enabled}`. `cmd_test_workflow` gains optional `guard` plus in-process hooks (`_phase_hook`, `_guard_hook`, `_review_hook`) and a new result status `stopped_at_guard` / `previewed` (neither is a pass and neither writes `last_test_*`); `cmd_build_skill_package` gains `for_test` (skips the sign-off gate). Cards: `test_inputs` (`inputs[{id,label,type,options,required,value}]`, `irreversible[]`, `irreversible_count`, `guard`, `show_browser`, `version`, `step_count`; states `open` → `submitted`), `test_run` (`rows[{index,name,status,destructive}]`, `attempt`, `auto`, `guard`, `step_total`; states `running`, `waiting_guard` (`paused_before`, `remaining`, `ran`), `waiting_confirm` (`prompt`), `passed` (`steps_ran`, `seconds`, `irreversible_ran`, `approved`, `approved_version`), `failed` (`error`, `failed_step`, `nothing_sent`), `ended_before_irreversible`, `stopped`, `previewed`), `autofix` (`attempt`, `max_attempts`, `phase`, `changes[{card_id,title}]`, `reason`; states `running`, `passed`, `stopped`). Runtime: `execute_skill` accepts `_pause_before_irreversible` and `_guard_release` (Studio only); the pause result carries `_meta["conxa/guard"] = {step_index, step_intent, remaining_irreversible, run_id}`; the app layer's `version.json` gains `capabilities: ["test_guard"]`. Edit-log `source` `"auto_fix"`.

**Connection and states (BUILD-43).** `cloud_ping` → `{online: bool, ms?: int, detail?: str}` (never raises). `cmd_test_workflow` result status `needs_sign_in` (`message`, `run_id`, `failed_at`) when the runtime's response carries `_meta["conxa/session_expired"] = {run_id, step_index}`; the `test_run` card gains state `needs_sign_in` (`steps_ran`, `seconds`, `detail`; `actions: ["test_again"]`), the lifecycle substate `sign_in_expired` (stage `test`, ▲) while the runtime-state flag `sign_in_expired` is set, and `flow_test_run` never starts auto-fix for it. Electron IPC (not backend commands): `engine:status` → `{state: running|restarting|stopped}` (also pushed as an event), `engine:restart` → bool, `engine:diagnostics` → text; exposed as `window.conxa.engine`.

New LLM tasks (shared engine `conxa_core/llm/client.py`, so the cloud must be deployed with it): `studio_chat_reply` (streamed prose), `chat_map_answer`, `thread_summarize`, `studio_chat_actions` (JSON).

**Test resilience (gap analysis G11).** New command `flow_sign_in_then_test{workflow_id}` (workflow must be compiled; returns `{next: "sign_in" | "test"}`). `test_run` and `autofix` cards gain the state `interrupted` (action `test_again`). `patch_step` on a confirm-only `ai_review` in front of an irreversible step is refused with `confirmation_guards_a_destructive_step` unless the edit keeps it confirm-only. New in-memory flag `retest_after_sign_in`. **Segments and evidence (gap analysis G6/G10).** `execute_skill` takes the internal `_stop_before_step` (integer, Studio only) and, when it parks, returns `_meta["conxa/stopped_at"] = {step_index, step_intent, run_id}`; the app layer advertises it as capability `stop_before`. `test_workflow` accepts `stop_before_step` and returns `status: "stopped_at_step"` with `stopped_at`, `stopped_intent`, `run_id`. New command `splice_segment{skill_id, segment_skill_id, from_step_key}` returns the usual skill response plus `replaced_from`, `added`, `stale_answers`. `clarify.json` questions may carry `stale: true`. `test_run` failed cards gain `failure_image` (a `conxa-asset://local/sandbox-evidence/<run id>/_evidence/<name>.jpg|png` address). **Extract, `run_if`, `on_missing` (gap analysis G4/G5).** `SkillStep` gains `extract` (`{source: text|value|attribute, attribute?, pattern?}`; the step's `value` is its output name) and `run_if` (`{input, op: not_empty|equals|not_equals, value?}`); an entity-bound step may carry `on_missing: "skip"`. Execution step shapes: `{type: "extract", selector | identity_bundle, output_name, extract}`, any step may carry `run_if`, a bound step `on_missing`. `execute_skill`'s success result adds, only when a step extracted something, an `Outputs:` text block (`  name: value` lines) and `structuredContent: {outputs: {name: value}}`; a skipped step adds a `Skipped because ...` block and a warning. `ActionKind` gains `extract`. The app layer's `version.json` capabilities gain `outputs` and `run_if`. New command `flow_capture_value{workflow_id, card_id, on?}` (recording card must be `recording`). `test_run` cards gain `outputs{name: value}` once passed. New Clarify question kind `output` (`output_name`; answer `keep` | `remove`, remove deletes the extract step). **Compile accounting (gap analysis G7/G8).** `compile_report` gains `events_total` (recorded events handed to the compiler) and `dropped[{sequence, pass: cleanup|navigation_folded, action}]` (capped at 200). `clarify.json` questions may have `status: "queued"` (held back past the first five; `clarify_store.summary()` returns `queued`). `data/sessions/<id>/second_opinion.json` is `{key, raw}`, the model's pinned second-opinion answer for that recording. **Conversation-driven actions (gap analysis G2/G9).** New card types: `confirm_action` (`action`, `label`, `describe`, `slots`; states `open` → `running` → `done`/`failed` + `receipt`, or `dismissed`; actions `confirm`, `cancel`) and `action_link` (`action`, `target` = edit | publish | workflow | details, `workflow_id`, `title`; state `open` → `opened`; action `open`). New command `flow_do_action{workflow_id, action, slots?}` runs a `kind: "flow"` entry of `conversation/actions.ACTIONS` and returns `{ok, message}`; `thread_turn` now also returns `routed: <action id>` when a typed command was routed. New LLM task `chat_route_intent` (text; `{user_text}` = JSON `{actions[], input_names[], message, workflows?[]}` in, `{action, slots}` out, `action` must be one of `actions` or `chat`).

**Follow-ups to the conversation roadmap (BUILD-49..53).** *Execution steps* gain `source_step_key` (the document step they came from; the runtime echoes it on every `test_phase` entry so a failed row maps back to a step). *Manifest* gains `required_capabilities: [..]` (names from the app layer's `version.json` `capabilities`: `date_offset`, `ask_missing`, `templated_click`, `variant_branch`; the runtime refuses a pack whose capability it lacks — `required_runtime` compares the host exe, not the app layer). New capabilities: `outputs` schema on per-skill tools, `date_offset`, `ask_missing`, `templated_click`, `loop_guard`, `variant_branch`. *Step fields:* `handler_hints.date_picker.date_offset_days` (int; the date is today plus N and the step takes no input), `on_missing` accepts `"ask"` (pauses like a hand-over), a `wait_for_one_of` with `branch.flow: true` (options are `{selector, identity_bundle, steps}`; the chosen option's steps run as full steps and a failure fails the run), identity-bundle text signals and fingerprint text may hold `{{input}}` (clicked item that changes each run). *compile_report.dropped* entries are `{sequence, pass: cleanup|navigation_folded|choice_collapsed|date_collapsed, action, value?}`. *Clarify question kinds* added: `dropped_value`, `test_followup`, `relative_date`, `skip_when_empty`, `missing_item` (all advisory, never blocking); `missing_item` answers `skip|ask|fail`. *Cards:* `record_plan` (`steps[]`, read-only); the `recording` card's `refs` may carry `segment_from` (step key) and `segment_mode` (`replace|branch`). *Commands:* `flow_rerecord_from{skill_id|workflow_id, step_key|step_index, mode}`, `set_date_offset`, `branch_segment`, `compile{segment:true}`, `test_workflow{handoff_park:true}` (returns `handed_off_run_id`), `start_recording{adopt_run_id}` (the recording takes the panel run's id as its session id). Runtime args: `_handoff_park` (Studio only; result `_meta["conxa/handed_off"] = {run_id, step_index}`). Browser-panel control op `adopt_view{runId, kind, viewport}` → `{tabId, targetId}`. LLM task `record_plan` (`{user_text}` in, `{steps[]}` out).
New data: `Workflow.task_description`; `data/workflows/<id>/thread.jsonl`; `data/workflows/<id>/clarify.json` (BUILD-32 questions and answers, plus `understanding: {goal_summary, coverage:{ok,note}, source: model|fallback|none}` — deliberately outside the skill document; `chosen_values` questions carry `items[{ref,name,literal,recorded}]` and a `recorded{name→literal}` map the test card pre-fills from); `data/chats/<chat_id>.jsonl` and `data/chats/index.json`.

---

## 6. Entity Relationship Diagrams

### 6.1 Workflow & SkillPack Domain

```mermaid
erDiagram
    SkillPack {
        string workspace_id PK
        string display_name
        string status
        float created_at
        float updated_at
    }
    Workflow {
        string id PK
        string slug
        string name
        string workspace_id FK
        string target_url
        string protected_url
        string status
        float created_at
        float updated_at
    }
    WorkflowAuth {
        string session_id
        float captured_at
        string storage_state_path
    }
    SkillPackBuild {
        float last_built_at
        string output_path
        string version
    }
    SkillPackInstaller {
        float built_at
        string installer_path
        string filename
        string version
        string runtime_version
    }
    RecordingSession {
        string session_id PK
        string events_jsonl_path
        string screenshots_dir
    }
    SkillPackage {
        string skill_id PK
        string title
        int version
        string source_session_id FK
    }

    SkillPack ||--o| SkillPackBuild : "has one"
    SkillPack ||--o| SkillPackInstaller : "has one"
    SkillPack ||--o{ Workflow : "owns many"
    Workflow ||--o| WorkflowAuth : "has one"
    Workflow ||--o| RecordingSession : "captured from"
    Workflow ||--o| SkillPackage : "compiled to"
```

**Cardinality:** One workspace has exactly one SkillPack (identified by `workspace_id`). The SkillPack owns many Workflows. Each Workflow represents one recorded automation with one login session and one recording. When a workspace publishes, all `signed_off=true` workflows in that workspace compile together into the single SkillPack's shared build and installer.

### 6.2 Skill Package Domain

```mermaid
erDiagram
    SkillPackage {
        string id PK
        string title
        int version
        string source_session_id
        string compiler_policy_version
    }
    SkillBlock {
        string name
        int step_count
    }
    SkillStep {
        string action
        string intent
        string url
        string semantic_description
        string snapshot_ref
        float confidence
    }
    ElementFingerprint {
        string role
        string tag
        string inner_text
        string aria_label
        string data_testid
        list compiled_selectors
        dict position_hint
    }
    RecoveryBlock {
        string intent
        float confidence_threshold
        int max_attempts
        list anchors
    }
    Assertion {
        string type
        string target
        string expected
        int timeout_ms
        bool required
    }
    WorkflowIntentGraph {
        string goal
        dict expected_end_state
    }

    SkillPackage ||--|| WorkflowIntentGraph : "has one"
    SkillPackage ||--o{ SkillBlock : "has"
    SkillBlock ||--o{ SkillStep : "has many"
    SkillStep ||--|| ElementFingerprint : "has one"
    SkillStep ||--|| RecoveryBlock : "has one"
    SkillStep ||--o{ Assertion : "has many"
```

### 6.3 Cloud Platform Domain

```mermaid
erDiagram
    Workspace {
        string id PK
        string slug
        string name
        float created_at
    }
    User {
        string id PK
        string email
        string name
        string auth_provider
    }
    Membership {
        string user_id FK
        string workspace_id FK
        string role
        float created_at
    }
    PublishedSkillPack {
        string slug PK
        string workspace_id FK
        string owner_user_id FK
        string skill_pack_version
        float published_at
    }
    TrackingToken {
        string company PK
        string token
        string workspace_id FK
        string owner_user_id FK
    }
    RunRecord {
        string run_id PK
        string company FK
        string workspace_id FK
        string status
        int duration_ms
        int total_steps
        int recovered_steps
        float server_ts
    }

    Workspace ||--o{ Membership : "has"
    User ||--o{ Membership : "belongs to"
    Workspace ||--o{ PublishedSkillPack : "owns"
    PublishedSkillPack ||--|| TrackingToken : "has"
    TrackingToken ||--o{ RunRecord : "tracks"
```

---

## 7. KV Namespace Map

| Namespace | Key | Value | Used by |
|---|---|---|---|
| `workflows` | `{workflow_id}` | `Workflow` JSON | Build Studio, Cloud |
| `skill_packs_meta` | `{workspace_id}` | `SkillPack` JSON (workspace-scoped; one per workspace) | Build Studio, Cloud |
| `entitlement_usage` | `{workspace_id}:{YYYY-MM}` | Monthly compile/Human Edit usage row | Cloud entitlements |
| `compile_reservations` | `{reservation_id}` | Compile reservation row | Cloud entitlements |
| `tracking_tokens` | `{workspace_id}` | `{token, company, version, workspace_id, owner_user_id, updated_at}` | Cloud tracking, runtime telemetry auth |
| `roi_assumptions` | `{workspace_id}` | `{default_minutes, hourly_rate, currency, per_workflow: {"{company}/{skill}": minutes}, updated_at, updated_by}` | Operations dashboard — Impact page. The only input the dashboard cannot derive from telemetry: how long a task took a human before it was automated. Admin-writable, surfaced beside every figure that depends on it |
| `sync_tokens` | `{workspace_id}` | `{token, company, version, workspace_id, owner_user_id, updated_at}` | Cloud publish, runtime delta-sync auth |
| `installer_versions__{slug}` | `{version}` | Installer `meta.json` fields + `content_base64` (full .exe) | Cloud publish — durable backing for installer history; Render's free-tier disk is ephemeral, this KV namespace (Postgres in prod) is the source of truth, local disk is a rehydratable cache |
| `skillpack_files__{slug}` | `{relative_path}` (e.g. `pack.json`, `{group_id}/{skill_slug}/execution.json`) | `{path, content_base64}` | Cloud publish, skill-pack sync — the **mutable** "currently live" mirror `_build_delta` serves; still one namespace per company (not re-keyed by skill), but every publish/rollback call only ever writes one skill's own path subset — a sibling skill's entries are untouched. Same disk-wipe rationale as above; `path` is stored explicitly because the fs-fallback KV implementation keys files by a hash of the original key, not the literal string |
| `skillpack_versions__{slug}__{skill_slug}` | `{version}` | Release-history row: `{slug, skill_slug, version, release_notes, group_id, tests_passed, workspace_id, owner_user_id, published_by, published_at, file_count, size_bytes, artifact_sha256, status, is_latest}` | Cloud publish, §5.1d release system — one immutable row per published version, per skill (re-keyed 2026-08-19; every skill has its own independent history). `status` is `ready` (published, awaiting Release/Deploy), `published` (activated), or legacy `pending` |
| `skillpack_release_files__{slug}__{skill_slug}__{version}` | `{relative_path}` | `{path, content_base64}` | §5.1d release system — the **immutable** per-version snapshot for one skill, write-once, never overwritten; what Release/Deploy or rollback restores that skill's subset of `skillpack_files__{slug}` from |
| `skillpack_known_skills` | `"{slug}:{skill_slug}"` | `{slug, skill_slug, group_id, group_name, workflow_name, first_published_at, archived?, archived_at?}` | §5.1d release system — upserted at **publish** time (not release time); backs Cloud's Skill Packages → Group → Workflow navigation (`GET .../groups`) so an unreleased "ready" skill is still visible to admins. `archived`/`archived_at` set only by `POST .../skills/archive` / `.../unarchive` |
| `skillpack_known_groups` | `"{slug}:{group_id}"` | `{slug, group_id, group_name, created_at}` | §5.1d — upserted when Build Studio creates/renames a group; `GET .../groups` unions this with known-skills so empty folders appear before first publish |
| `skillpack_channels` | `"{slug}:{skill_slug}"` | `{slug, skill_slug, stable: {version, set_at, set_by, reason, from_version}}` | §5.1d release system — the per-skill stable-channel pointer (re-keyed 2026-08-19); distinct from §5.8's runtime/app self-update `dev`/`stable` channel. `reason` is `release` (first activation of a "ready" version) or `rollback` — never `publish` |
| `skillpack_release_events__{slug}__{skill_slug}` | `"events"` (single key, JSON array via `db_append`) | `[{id, workspace_id, user_id, action, resource_type, resource_id, skill_slug, metadata, created_at}, ...]` | §5.1d release system — unbounded per-(slug, skill_slug) release audit trail, mirrored into `saas.add_audit_event` |
| `tracking/{company}` | `{run_id}` | `[event_batch, ...]` | Runtime, Cloud dashboard |
| `governance_policy` | `{workspace_id}` | Signed policy document, §4.2a | Cloud policy write/read, runtime `policy_gate.js` |
| `app_graph__{workspace_id}` | `{app_key}/{contribution_id}` | One app-map `Contribution` (§3.4l) — screens/actions/popups only, no values | `app_graph_routes.py` (stores, never folds); Studio `services/app_graph_sync.py` |
| `runs` | `{workflow_id}` | `[run_record, ...]` | Cloud, Build Studio |
| `selector_cache` | `{dom_hash}:{bbox}:{model}` | Selector candidates | Compiler |
| `runtime_registrations` | `{company}:{install_id or platform}` | `{company, install_id, platform, runtime_version, workspace_id, last_seen, first_seen, revoked, skill_versions?, sync_errors?, hostname?, username?, os_release?, os_arch?, capabilities?}` | 2.1 device registration. `skill_versions` (`{skill_slug: installed_version}`) is optional and sticky (an omission never wipes a prior value), added for §5.1d's Deployment view. `sync_errors` (`{skill_slug: {code, at}}`) is optional and **not** sticky — always overwritten in full each phone-home — and is what lets Deployment show a real `failed` status. `hostname`/`username`/`os_release`/`os_arch`/`capabilities` (Machine Registry, added 2026-08-22) are optional and sticky like `skill_versions` — an older runtime that hasn't self-updated just omits them without wiping a newer value. `capabilities` is a small feature-flags object (`max_recovery_tier`, `update_channel`), not a tool list. `revoked` is admin-only (`POST /telemetry/runtimes/revoke`, §5.9a) — the runtime itself never sends it, and it never affects sync/telemetry/execution, only what the Fleet dashboard shows |
| `audit_log` | `{workspace_id}` | `[{id, user_id, action, resource_type, resource_id, metadata, created_at, ip}, ...]` | 2.3 audit trail |
| `rate_limits` | `{sha256(token)[:16]}` | `{last_ts}` | Skill-pack sync rate limiter — persisted so the 5-min window survives restarts and is shared across instances (1.5). In-memory dict fallback when no database is configured |
| `component_versions` | `conxa_runtime`, `conxa_app`, `skill_packs:{company}:{skill}` | `ComponentVersion`/`SkillVersion` dict (version, released_at, files[], rollout, min_host/min_runtime) | 5.8 unified manifest — written by CI + `publish_routes.py`, read by `_compose_manifest()` |
| `manifest` | `current` (composed+signed `UnifiedManifest`), `skill_pack_index` (list of `{company}:{skill}` identifiers), `minimum_versions`, `compatibility` | 5.8 unified manifest — `skill_pack_index` exists because the filesystem-fallback KV store hashes keys, so `component_versions` entries for skills can't be discovered by scanning keys directly |
| `workspace_devices` | `{workspace_id}:{machine_hash}` | `{workspace_id, machine_hash, last_ip, first_seen, last_seen, revoked?}` | 5.3 machine binding — `machine_hash` is SHA-256 of the Windows `MachineGuid`, never the raw ID. Added 2026-08-08 |
| `execute_grants` | `{grant_id}` | `{grant_id, workspace_id, email, status, granted_at, granted_by, claimed_at, claimed_user_id, revoked_at, revoked_by}` | §5.3 / `docs/TRD.md` §13.7 — Execute seat grants, auto-claimed by email (no invite link since 2026-09-17). Added 2026-09-16 |
| `execute_grant_by_user` | `{claimed_user_id}` | `{grant_id, workspace_id, email, claimed_at}` | §5.3 / `docs/TRD.md` §13.7 — O(1) primary-pool-binding lookup keyed by the claiming Clerk `user_id`, written on claim, cleared on revoke. Added 2026-09-16 |
| `workspace_llm_keys` | `{workspace_id}` | `{provider: "azure_openai", endpoint, deployment, api_version, nonce_b64, ciphertext_b64}` | Enterprise BYOK (§TRD 13.5) — the API key is AES-256-GCM encrypted at rest under `SKILL_BYOK_ENCRYPTION_KEY`; never stored or returned in plaintext. Added 2026-08-08 |
| `legal_acceptances` | `{user_id}:{version}` | `{id, user_id, email, name, workspace_id, workspace_slug, workspace_name, role, auth_provider, identity_source, version, document_hashes, documents[], accepted_at, accepted_at_iso, client_ip, user_agent, app_version, machine_hash}` | §5.14 Build Studio legal acceptance — **write-once**, one row per (user, terms version); a repeat acceptance returns the existing row rather than overwriting it, so the stored timestamp is always the moment the person actually agreed. Deliberately *not* stored only in `saas.audit_events`, which is a global 500-entry ring buffer — an acceptance mirrored there for the Audit page (`legal.accepted`) would be evicted long before it was needed as evidence. Added 2026-08-29 |
| `kv_store` (meta) | `{namespace}` | Admin use | Internal |

**Conxa Execute's own database and KV namespaces (`execute_wallet`, `execute_order_key`,
`execute_addon_granted`, `execute_sub_charge_granted`, `execute_cashfree_plans`, table
`execute_chat_session`) no longer exist** — deleted 2026-09-17 along with the standalone
`conxa-execute/backend` service. Execute has no wallet, no subscription, no BYOK, and no
server-side session sync; billing/access run entirely through the `execute_grants`/
`execute_grant_by_user` namespaces above, same as any other Build Studio workspace feature. See
`docs/TRD.md` §3.6.

---

## 8. File Storage Map

### Build Studio Machine

```
~/.conxa/  (SKILL_DATA_DIR or data/)
├── kv/                           ← filesystem KV fallback
│   ├── workflows/{sha256}.json
│   ├── publish_owners/{sha256}.json
│   └── tracking_tokens/{sha256}.json
├── sessions/{session_id}/
│   ├── events.jsonl              ← raw RecordedEvent stream (append-only)
│   └── screenshots/{n}.png
├── skills/{skill_id}/
│   ├── skill.json                ← SkillPackage (canonical)
│   └── assets/{step_n}.png
├── workflows/{workflow_id}.json  ← Workflow model (flat, one file per workflow)
├── workflows/{workflow_id}/
│   └── auth/
│       └── auth.json             ← Playwright storageState (LOCAL ONLY, never published)
├── skill-packs/{company}/
│   ├── pack.json                 ← manifest with sync_endpoint, tracking, skill_groups
│   └── {group_id}/                ← workflow's group_id, or "_default" (§2.2)
│       └── {skill_slug}/
│           ├── execution.json
│           ├── recovery.json
│           └── inputs.json
├── runs/{workflow_id}.jsonl
├── saas/metadata.json            ← local workspace metadata
└── deps/
    ├── nsis/makensis.exe
    └── runtime/{ver}/conxa-runtime.exe + keytar.node + runtime-app/
```

### Conxa Cloud (Render)

```
data/  (SKILL_DATA_DIR on Render, or cloud blob storage)
├── skill-packs/{company}/
│   ├── pack.json
│   └── {group_id}/                ← workflow's group_id, or "_default" (§2.2); packs
│       │                            published before group-nesting existed still have
│       │                            skills directly here instead — _build_delta() falls
│       │                            back to that flat layout when the nested one is absent
│       └── {skill_slug}/
│           ├── execution.json
│           ├── recovery.json
│           ├── inputs.json
│           └── manifest.json
└── installers/{company}/
    ├── installer.exe
    └── meta.json
```

### End-User Machine (Runtime)

Every updateable component (host, app, each skill) is a versioned directory with a
`current` directory junction — see TRD.md §4.5 for the full rationale and
`runtime/version_manager.js` for the implementation. App-layer files ship as obfuscated
plain JS, not V8 bytecode (`.jsc` was abandoned — `@yao-pkg/pkg`'s embedded Node build has
a different V8 than official nodejs.org Node, causing silent deserialization segfaults).

```
~/.conxa/  (CONXA_DIR)
├── conxa-runtime/
│   ├── v1.0.0/, v1.1.0/           ← conxa-runtime.exe + keytar.node + version.json each
│   └── current                    ← directory junction to the active version
├── conxa-app/
│   ├── v1.0.0/, v1.1.0/           ← server.js, sync.js, run.js, ... (obfuscated .js) + version.json each
│   └── current                    ← directory junction to the active version
├── manifest.json                  ← cached last Ed25519-verified signed manifest
├── chromium/                      ← Playwright browser (unversioned, external)
├── skill-packs/{company}/
│   ├── pack.json                  ← sync_endpoint + sync_token + tracking + skill_groups (no shared version)
│   └── {group_id}/                ← workflow's group_id, or "_default" (§2.2)
│       └── {skill_slug}/
│           ├── v1.0.0/, v1.1.0/   ← execution.json, recovery.json, inputs.json, manifest.json, version.json each
│           └── current             ← directory junction, independent per skill
└── logs/
    ├── runtime.log
    └── recovery.log

%APPDATA%/Conxa/  (CONXA_DATA_DIR)
└── cache/
    ├── sessions/
    │   ├── {co}__{appId}_state.json     ← AES-256-GCM encrypted storageState, one per group app
    │   └── {co}__{appId}_raw_state.json ← plaintext fallback (no Conxa token)
    └── manifests.json                  ← skill index fast-load cache
```

---

## 9. Multi-Tenancy Design

### Current State

The platform has a basic workspace model:

- **Workspace** = Clerk organization (`org_id`) or personal (`personal_{user_id}`)
- **Slug ownership** = first publisher claims the slug for their workspace. Subsequent publishes to the same slug from a different workspace return HTTP 403.
- **Tracking scoping** = telemetry events carry `workspace_id`. Dashboard queries filter by `visible_workspace_ids_for(principal)`.
- **Workflow visibility** = workflows are scoped to `workspace_id`. Local dev uses `ws_local`.

### Current Gaps

- **No team-level publishing** — only the initial slug owner can publish updates.
- **Member roles enforced on write routes** — `rbac.py`'s `require_admin` guards publish, workflow create/delete, and bundle release (403 for non-admin/owner). Not yet fine-grained (per-skill / read-only analyst roles are Phase 3).
- **No cross-workspace sharing** — a company cannot share a skill package with another workspace.
- **Runtime auth is per-company** — there is no per-user runtime token. The same skill pack serves all users on the same machine.

### Future State (Planned)

- Per-workspace slug isolation with transfer capability.
- Role-based publishing (owner/admin/member model).
- Team invitations and multi-user workspace management.
- Per-skill access controls (which team members can publish vs. view).
- Audit log (all publish, compile, delete actions attributed to users).

---

## 10. Security Considerations

### Data Classification

| Data | Classification | In Transit | At Rest |
|---|---|---|---|
| Clerk JWT | Credential | HTTPS | OS keyring (Studio) |
| Runtime company token | Credential | HTTPS | OS keychain (keytar) |
| Playwright storageState | Sensitive (session cookies) | Never transmitted | Local only; encrypted at rest (AES-256-GCM on end-user machine) |
| Skill pack content | Internal | HTTPS | Plain text on cloud; plain text on runtime machine |
| Tracking token | Semi-sensitive | HTTPS (in body) | kv_store (plain) |
| Telemetry events | Internal (anonymized) | HTTPS | kv_store |
| Run steps content | Internal | Never to cloud | Local only |

### Auth File Exclusion

**Enforced in code (`backend.py:cmd_build_installer`):**
```python
if skill_pack_dir.exists() and any(skill_pack_dir.rglob("auth.json")):
    raise _CommandError(
        "auth_file_in_build_input",
        "Refusing to build: auth.json found under the built skill pack.",
    )
```

### Slug Ownership

First publish claims the slug. This prevents a different workspace from overwriting another company's published skills. However:
- There is no slug reservation before publishing.
- Slug transfer is not implemented.
- A bad actor who publishes first can claim any slug.

### Tracking Token Security

The tracking token (`secrets.token_urlsafe(32)`) is embedded in the installer, so anyone holding an installer can extract it and submit telemetry as that company. What was closed (SG-05): ingest no longer falls back to a synthetic workspace for a company with no stored token — `_verify_token()` returns `None` (→ 401) and logs a warning whenever either `SKILL_TRACKING_HMAC_SECRET` or `SKILL_AUTH_REQUIRED` is set, and `_validate_production_config()` requires `SKILL_TRACKING_HMAC_SECRET` in production, so the permissive path survives only in true local dev. What remains open: the token is still a bearer secret shipped inside a customer-distributable binary, so a *legitimate* company's own installer can still be used to submit fabricated events for that company.

**Production config gate.** `app/main.py::_validate_production_config()` refuses to start the backend when `SKILL_AUTH_REQUIRED=true` and any of these are unset: `SKILL_DATABASE_URL`, `SKILL_CLERK_ISSUER`, `SKILL_CLERK_JWKS_URL`, `SKILL_CORS_ORIGINS`, the Cashfree credential/plan set, `SKILL_API_BASE_URL`, `SKILL_TRACKING_HMAC_SECRET`, `SKILL_INSTALLER_SIGNING_KEY`, `CONXA_MANIFEST_SIGNING_KEY`, and at least one LLM provider key. Each of these has a silent-degradation failure mode if absent (see `docs/TRD.md` §16.3), which is why they fail the boot rather than warn.
