"""Skill package schema (Phase 3+). Compiler fills these at compile time.

ARCH-3 contract/executor boundary. Every field below is tagged:
  [contract]  executor-independent — what the skill does and how success is judged.
              Must survive a swap to a non-browser executor (API connector, computer-use
              agent) unchanged. This is the layer Conxa's durable position is built on.
  [executor]  browser-replay implementation detail — Playwright selector grammar, DOM/iframe/
              shadow-DOM structure, hover chains. Free to change per executor backend.
  [mixed]     the class/field holds both; sub-fields are tagged individually.
New fields (e.g. EXEC-1's branch primitives) must pick one on introduction — see
docs/Backend-Schema.md §3.0 and docs/TRD.md §7.7 for the full design note.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class SkillMeta(BaseModel):
    id: str                                                        # [contract]
    version: int = 1                                               # [contract]
    title: str = ""                                                # [contract]
    created_at: str = ""                                           # [contract]
    source_session_id: str | None = None                           # [contract]
    compiler_policy_version: str = ""                              # [contract]
    compiler_policy_hash: str = ""                                 # [contract]
    # [executor] Minimum runtime version required to execute this skill. Set by the compiler
    # when features are used that require a specific runtime capability. The packager
    # reads this and writes it into manifest.json / pack.json; the runtime enforces it
    # at execute time via semver.satisfies(RUNTIME_VERSION, required_runtime).
    required_runtime: str = ">=1.0.0"
    # [executor] Structural fingerprint of the first 3 steps' landmark selectors — used by
    # drift detection to detect site redesigns before execution begins. Browser-DOM specific.
    structural_fingerprint: dict[str, Any] = Field(default_factory=dict)
    # [executor] EXEC-36: the recording browser's environment (locale, timezone, viewport,
    # device_pixel_ratio, date_format_sample, platform), captured once at session start
    # (recorder/session.py::_write_environment_sync). Compared against the replay browser's own
    # environment at run start (runtime/app/env_match.js) to WARN — never block — when a
    # workflow is replayed in a materially different locale/timezone/viewport than it was
    # recorded in, a failure mode that otherwise burns the recovery ladder and Tier B tokens
    # looking exactly like drift. Empty on skills compiled before this field existed, or when
    # the recording page never finished loading — the runtime treats an empty dict as "unknown,"
    # never as a mismatch. Browser-DOM specific, not part of the executor-independent contract.
    environment: dict[str, Any] = Field(default_factory=dict)
    # [contract] PROD-3-DRYRUN layer 5: the slug of a sibling skill in the same pack the runtime
    # should OFFER (never auto-run) after a mid-run failure that happened after a destructive
    # step already executed — e.g. "cancel the draft invoice" as the companion to "create
    # invoice." Empty when no compensation workflow is linked. Set in the Human Edit editor;
    # surfaced by failure_response.js as a named next step, never executed automatically — an
    # unattended compensating write after an unknown failure is a second uncontrolled action.
    compensation_skill: str = ""
    # [contract] Every hostname the recording actually navigated to (main frame + any tab
    # opened during the recording), lowercase, deduped. Used to compute a workflow's
    # unclaimed_hosts (conxa_core.storage.group_store.unclaimed_hosts) from everywhere the
    # workflow goes, not just where it starts — see CLAUDE.md's group-auth "rough edges" and
    # docs/TRD.md's Workflow Groups section. Empty on skills compiled before this field
    # existed; callers must fall back to target_url/protected_url-only matching for those.
    visited_hosts: list[str] = Field(default_factory=list)


class SkillPolicies(BaseModel):
    failure_first: bool = True                                     # [contract]
    stop_on_low_confidence: bool = True                             # [contract]


class RecoveryBlock(BaseModel):
    intent: str = ""                                                # [contract]
    final_intent: str = ""                                          # [contract]
    anchors: list[dict[str, Any]] = Field(default_factory=list)     # [executor] visual/DOM anchor points
    strategies: list[str] = Field(                                  # [contract] named strategy policy
        default_factory=lambda: ["semantic match", "position match", "visual match"]
    )
    confidence_threshold: float = 0.85                              # [contract]
    max_attempts: int = 2                                           # [contract]
    require_diverse_attempts: bool = True                           # [contract]


class Assertion(BaseModel):
    """A verifiable post-action condition checked after each step. [contract] — the
    executor-independent definition of what "success" means for a step; every field here
    must be answerable by any executor backend, not just the browser one."""
    # url_changed | url_pattern | selector_present | selector_absent | text_present |
    # text_absent | value_equals | state_changed
    # (url_pattern is a regex-matching alias of url_changed; url_changed is canonical.)
    type: str
    target: str = ""
    # Expected value for value_equals (interpolated against runtime inputs). Compared
    # normalized (trim/collapse-whitespace/lowercase) with a "contains" fallback so masked/
    # formatted fields (phone, currency) still validate.
    expected: str = ""
    timeout_ms: int = 5000
    # If True, assertion failure halts execution. If False, records a warning only.
    required: bool = True


class ValidationBlock(BaseModel):
    """[mixed] — assertions are contract; wait_for/success_conditions are today's
    browser-executor wait-condition detail (legacy, largely superseded by Assertion)."""
    wait_for: dict[str, Any] = Field(default_factory=dict)          # [executor]
    success_conditions: dict[str, Any] = Field(default_factory=dict)  # [executor] legacy
    # [contract] Multi-assertion outcome verification — runtime checks all assertions after action.
    assertions: list[Assertion] = Field(default_factory=list)


class DecisionPolicy(BaseModel):
    ask_if_ambiguous: bool = True                                   # [contract]
    stop_if_low_confidence: bool = True                             # [contract]
    max_retries: int = 2                                            # [contract]


class IdentitySignal(BaseModel):
    """A single scored, classified selector signal within an IdentityBundle. [executor] —
    `selector` is Playwright grammar; the whole signal is how the browser executor locates
    an element, not part of the durable contract."""
    engine: str                      # testid | attr | role | aria | text | relational | css-id | css-structural | xpath
    selector: str                    # selector string (Playwright grammar preferred)
    durability: float                # 0.0–1.0 from durability_score()
    orthogonality_class: str         # test-contract | named-attr | semantic-aria | visible-text | spatial-anchor | structural
    unique_at_compile: bool = False  # matched exactly 1 node in recorded DOM
    # compiler | llm | input_bound | user (manually edited in editor) | runtime (BUILD-26 stage
    # f: synthesized from a runtime-observed overlay's descriptor, editor/overlay_identity.py —
    # deterministic, not LLM-authored; distinct from "compiler" because it was never seen at
    # compile time and carries no DOM-snapshot uniqueness verification)
    source: str = "compiler"


class ElementFingerprint(BaseModel):
    """[contract] — stable, executor-independent element identity (what the element *is*,
    not how a browser finds it): role/text/label semantics any executor could reason about."""
    role: str = ""
    tag: str = ""
    inner_text: str = ""       # visible text, max 120 chars
    aria_label: str = ""
    name: str = ""
    placeholder: str = ""
    label_text: str = ""
    alt: str = ""              # <img> accessible name — the only name a bare image has
    title: str = ""            # generic accessible-name fallback for any element
    data_testid: str = ""      # data-testid attribute value — highest-stability signal
    input_type: str = ""       # for <input> elements
    css_class_tokens: list[str] = Field(default_factory=list)   # stable class tokens only
    anchor_phrases: list[str] = Field(default_factory=list)     # relational context phrases
    position_hint: dict[str, Any] = Field(default_factory=dict) # normalized x/y as 0.0–1.0


class ShadowHost(BaseModel):
    """[executor] A shadow DOM host in the element's containment path — DOM/browser structure."""
    host: str          # CSS selector of the shadow host element
    mode: str = "open"  # "open" | "closed"


class FrameFingerprint(BaseModel):
    """[executor] Multi-signal frame identity for one level in an iframe chain — browser
    document/frame structure with no non-browser equivalent."""
    signals: list[IdentitySignal] = Field(default_factory=list)  # durability-ranked
    url: str = ""
    url_pattern: str = ""


class IdentityBundle(BaseModel):
    """[mixed] Compiled, signed element identity: durability-ordered signals + stable fingerprint.

    `fingerprint` (ElementFingerprint) is [contract] — the resolver's scoring oracle, stated in
    executor-independent terms. `signals`/`frame_chain`/`shadow_path` are [executor] — the
    Playwright-grammar candidates and DOM structure a browser executor resolves against.
    `stable_hash`/`destructive` are [contract]: semantic identity and consequence flags any
    executor needs. `compat_fingerprint`/`guid_like_attrs` are [executor]: DOM-attribute detail.
    """
    signals: list[IdentitySignal] = Field(default_factory=list)               # [executor]
    fingerprint: ElementFingerprint = Field(default_factory=ElementFingerprint)  # [contract]
    stable_hash: str = ""                  # [contract] SHA256 of (tag_path + static_attrs + AX_name)
    frame_chain: list[FrameFingerprint] = Field(default_factory=list)         # [executor]
    shadow_path: list[ShadowHost] = Field(default_factory=list)               # [executor]
    compat_fingerprint: str = ""           # [executor] SHA256 of app-version indicators
    guid_like_attrs: list[str] = Field(default_factory=list)                  # [executor]
    destructive: bool = False              # [contract]


class EntityBinding(BaseModel):
    """[mixed] PROD-3 entity binding — scopes a step's target to the ONE record it must act on,
    so recovery can never substitute a same-looking element from a different row. Detected
    deterministically at compile time from the recorded ancestor chain (never LLM-authored, per
    the primary-compile-path invariant); `confirmed` only ever becomes True via an explicit
    vendor confirmation in the editor (see patch_gate.py's
    irreversible_step_requires_confirmed_entity_binding)."""
    container_selector: str = ""   # [executor] matches every sibling row, e.g. "table#invoices tr"
    identifier: str = ""           # [contract] text the right row must contain; may be "{{input_name}}"
    source: str = "literal"        # [contract] "input" | "literal"
    confirmed: bool = False        # [contract] vendor confirmed this binding in the editor


class HandlerHints(BaseModel):
    """[executor] Precompiled runtime hints for a *browser* action handler (hover
    preconditions, virtualized-list scrolling) — has no meaning outside a browser executor."""
    hover_chain: list[IdentitySignal] = Field(default_factory=list)  # elements to hover before acting
    virtualized_container: str = ""   # selector of a scroll container that virtualizes rows
    allow_forced_action: bool = False
    # Dispatch-by-control-kind seam (03-replay-algorithm.md §5): "" means the handler dispatches
    # purely by action type, as every action does today. "date_picker" was the first populated
    # value — set by compiler/date_picker.py when a click run collapses into a date_pick step
    # targeting a custom calendar grid (as opposed to a native <input type=date>, which never
    # gets this set). runtime/app/date_picker.js reads `date_picker` below when this is set.
    # "choice" is the second: set by compiler/choice.py for a multiple-choice control (radio/
    # select/ARIA radiogroup-listbox/checkbox-group). runtime/app/choice.js + handlers.js's
    # set_radio/select/select_option/set_checkbox choice branches read `choice` below.
    control_kind: str = ""
    date_picker: dict[str, Any] = Field(default_factory=dict)
    choice: dict[str, Any] = Field(default_factory=dict)


class SkillStep(BaseModel):
    """[mixed] — see field tags below. `action`/`intent`/`url`/`value`/`input_binding`/
    `validation`/`recovery`/`decision_policy`/`optional_hint`/`consequence` are [contract]: what
    this step means and how its success is judged, independent of executor. `frame`/`tab`/`target`/
    `identity_bundle`/`handler_hints`/`signals`/`state`/`compiled_selectors`/`snapshot_ref`/
    `snapshot_dom_hash` are [executor]: browser-DOM/Playwright implementation detail. `branch`
    is [mixed] today (holds executor probe detail) and is EXEC-1's next target for a clean
    contract-terms definition (a condition + nested steps, no browser assumptions).
    `entity_binding` is [mixed] — see EntityBinding."""
    action: str | dict[str, Any]                                              # [contract]
    intent: str = ""                                                          # [contract]
    url: str = ""                                                             # [contract]
    # [contract] PROD-3 danger classification: "read_only" | "reversible" | "irreversible" |
    # "" (compiled before this field existed). Derived at compile time
    # (destructive_semantics.classify_consequence); vendor-editable in Human Edit. Drives
    # skill_package_builder_saved_skill.py's execution-step `destructive` flag and the runtime's
    # fail-closed recovery halt (runtime/app/cascade.js).
    consequence: str = ""
    frame: dict[str, Any] = Field(default_factory=dict)                       # [executor] iframe chain marker
    # [executor] Which tab/page this step runs on ({id, index, opened_by, opener_tab}). Empty
    # means "tab_0" (the initial page) — the same page every step already ran on before
    # multi-tab support existed, so old compiled skills are unaffected. Runtime resolution:
    # runtime/tabs.js::resolveStepPage.
    tab: dict[str, Any] = Field(default_factory=dict)                         # [executor]
    target: dict[str, Any] = Field(default_factory=dict)                      # [executor] raw recorded DOM target
    # [executor] Durability-ranked, orthogonality-deduplicated identity bundle — the single
    # source of truth for element identity (signals + scoring fingerprint). Runtime resolves
    # against it. (`identity_bundle.fingerprint` alone is [contract]; see IdentityBundle.)
    identity_bundle: IdentityBundle = Field(default_factory=IdentityBundle)
    # [executor] Phase 7: precompiled handler hints (hover chain, virtualization) — browser only.
    handler_hints: HandlerHints = Field(default_factory=HandlerHints)
    signals: dict[str, Any] = Field(default_factory=dict)                     # [executor] additional DOM signals
    state: dict[str, Any] = Field(default_factory=dict)                       # [executor] page state at recording
    value: Any = None                                                         # [contract]
    input_binding: str | None = None                                         # [contract]
    validation: ValidationBlock = Field(default_factory=ValidationBlock)     # [contract] (see ValidationBlock)
    recovery: RecoveryBlock = Field(default_factory=RecoveryBlock)           # [contract] (see RecoveryBlock)
    confidence_protocol: dict[str, Any] = Field(default_factory=dict)        # [contract]
    decision_policy: DecisionPolicy = Field(default_factory=DecisionPolicy)  # [contract]

    # [executor] Phase 3: LLM-compiled selector candidates (ranked, validated against snapshot).
    # Runtime Tier 1 tries these in order; runtime never calls LLM unless all fail.
    compiled_selectors: list[str] = Field(default_factory=list)
    semantic_description: str = ""        # [contract] "First Name input in Add Person dialog"
    # [contract] BUILD-25: which part of the workflow this step belongs to —
    # "login" | "navigate" | "act" | "verify" | "cleanup", or "" when the second-opinion
    # pass didn't run or didn't label this step. Written by the compiler's second-opinion
    # pass (compiler/second_opinion.py); read by runtime/app/failure_response.js's Tier B
    # recovery prompt (stage e) as a workflow-position prior for the agent tier.
    phase: str = ""
    snapshot_ref: str = ""                # [executor] which recorded DOM blob this step compiled against
    snapshot_dom_hash: str = ""           # [executor] for cross-compilation cache lookup

    # [mixed — EXEC-1 target] Conditional/branch primitives (if_present, try_dismiss,
    # wait_for_one_of): probe(s) + nested step bodies for optional interstitials (cookie
    # banners, session-expired screens, optional MFA, A/B variants). Empty for ordinary linear
    # steps. Holds `steps` (if_present), `candidates` (try_dismiss), `options`
    # (wait_for_one_of, each {selector, steps}), `timeout_ms`, `required`. Nested step entries
    # are raw dicts in the same shape as a saved SkillStep (action/target/identity_bundle/
    # branch/...); skill_package_builder_saved_skill.py recursively serializes each one into the flat
    # runtime step shape run.js consumes. Branch bodies run best-effort and never enter Tier
    # 1-4 recovery — CLAUDE.md Key Invariants. ARCH-3: today's probe/candidate shape leaks
    # executor detail (raw selector strings); new branch primitives must define the condition
    # itself in contract terms (e.g. "an interstitial matching this identity is present") and
    # keep browser-specific evaluation on the executor side.
    branch: dict[str, Any] = Field(default_factory=dict)

    # [contract] Conditional-state observation (recording-next-steps.md Priority 2): carried
    # verbatim from the recorded event's optionality/branch_hint (see
    # conxa_core.models.events.RecordedEvent) when the step's target sat inside an optional
    # interstitial. Never read by the compiler's own assertion/branch logic. Two consumers
    # convert it into a real try_dismiss branch, both through the same builder
    # (compiler/second_opinion.py::build_try_dismiss_from_hint): the compiler's
    # second-opinion pass at compile time, and editor/workflow_mutations.py's
    # confirm_optional_interstitial when a human confirms one the pass left alone. Cleared to
    # None by whichever one consumes it. None for ordinary steps.
    optional_hint: dict[str, Any] | None = None

    # [mixed] PROD-3: entity binding — see EntityBinding. None when the compiler found no
    # repeating ancestor container for this step's target (nothing to bind against).
    entity_binding: EntityBinding | None = None

    # [mixed] EXEC-38: the "for each row, do steps A-C" iteration primitive. Empty for ordinary
    # steps. Holds a row source (see below), `as` (the {{name}} body steps read the current
    # row's identifier/index under — {{as}}_id / {{as}}_index), `max_iterations`
    # (REQUIRED at runtime — see runtime/app/run.js; a loop with no cap refuses to load),
    # `on_row_error` ("stop" default | "continue"), and `steps` (the loop body, same nested
    # saved-step-dict shape `branch` uses; skill_package_builder_saved_skill.py serializes it the
    # same way). Author-first, not inferred from a recording: authored in the Human Edit editor
    # by wrapping N existing steps into a body, one level deep only (same constraint as
    # `branch`).
    #
    # Exactly one row source, never both, never neither (enforced by
    # skill_package_builder_saved_skill.py::_saved_for_each_step at package time and
    # editor/patch_gate.py::_validate_for_each_source at edit time):
    #   `rows` ({container_selector} — the [executor] half: a live DOM scan, every sibling row
    #   matching this selector is a candidate iteration). Entity-bound to `{{<as>_id}}` inside
    #   the body is what gives recovery the same "never substitute a different row" guarantee
    #   entity_binding already provides elsewhere — see EntityBinding and
    #   runtime/app/resolution.js::entityRoots.
    #   `items` (a named runtime input — e.g. a plain `text` input the caller filled with a
    #   comma-separated list — split via runtime/app/resolution.js::splitListInput; no DOM scan,
    #   no entity_binding, since there is no live row to re-locate).
    for_each: dict[str, Any] = Field(default_factory=dict)

    # [mixed] Outcome fork: "after the previous action the app is in one of several known states"
    # (no popup; a "file already exists: Replace / Keep both / Cancel" popup; ...), each with a
    # policy -- auto (run a named option), by_input (the caller picks via a runtime input),
    # ask_human (a person decides), stop. Empty for ordinary steps. Built by
    # compiler/outcome_fork.py from a recorded decision dialog; the only writer of a state's
    # `policy.author_approved` is editor/workflow_mutations.py::set_fork_policy. Unlike `branch`
    # this is NOT best-effort: the chosen option's steps run with full GATE/VERIFY/recovery
    # (runtime/app/run.js::runForkStep), and an unrecognised popup is never auto-answered.
    # Shape: {timeout_ms, unknown_policy, clean: {observed, observed_in}, states: [{id, kind,
    # signature: {title, button_labels, message_tokens, container_selector}, options: [{id,
    # label, role, recorded, steps}], policy, observed_in}]}. Option `steps` are nested
    # saved-step dicts, serialized like `branch`/`for_each` bodies.
    outcome_fork: dict[str, Any] = Field(default_factory=dict)


class WorkflowIntentStep(BaseModel):
    index: int                                                      # [contract]
    intent: str = ""                                                # [contract]
    verification_anchor: str = ""                                   # [contract]
    # Snake_case action token for this step, emitted by the same single
    # workflow-intent LLM call that produces the prose. The compiler uses it as
    # the step's machine-readable intent (after normalize_compiler_intent),
    # making the graph call the single source of both display prose and logic
    # tokens. Empty on graphs produced before this field existed (cache v1).
    intent_token: str = ""                                          # [contract]


class WorkflowIntentGraph(BaseModel):
    """[contract] Compile-time semantic understanding of the workflow (Claude Browser-style) —
    stated entirely in goal/decision-point terms, no executor detail."""

    goal: str = ""
    steps: list[WorkflowIntentStep] = Field(default_factory=list)
    decision_points: list[dict[str, Any]] = Field(default_factory=list)
    expected_end_state: dict[str, Any] = Field(default_factory=dict)


class SkillBlock(BaseModel):
    name: str = "default"                                           # [contract]
    steps: list[SkillStep] = Field(default_factory=list)             # [mixed] (see SkillStep)


class SkillPackage(BaseModel):
    meta: SkillMeta                                                 # [contract] (see SkillMeta)
    inputs: list[dict[str, Any]] = Field(default_factory=list)      # [contract] parameterizable inputs
    skills: list[SkillBlock] = Field(default_factory=list)          # [mixed] (see SkillBlock)
    policies: SkillPolicies = Field(default_factory=SkillPolicies)  # [contract]
    llm: dict[str, Any] = Field(default_factory=dict)                # [contract] LLM router config hints

    # [contract] Phase 3: workflow-level semantic understanding (one LLM call per workflow).
    intent_graph: WorkflowIntentGraph = Field(default_factory=WorkflowIntentGraph)

    # [contract] Per-step confidence report + LLM router statistics from compile — build-time
    # metadata, not tied to any executor.
    # Required: must contain status, steps_total, min_confidence, llm_router_stats, steps.
    compile_report: dict[str, Any]

    @model_validator(mode="after")
    def _validate_compile_report(self) -> "SkillPackage":
        required = {"status", "steps_total", "min_confidence", "llm_router_stats", "steps"}
        missing = required - set(self.compile_report.keys())
        if missing:
            raise ValueError(
                f"SkillPackage.compile_report missing required keys: {sorted(missing)}"
            )
        return self
