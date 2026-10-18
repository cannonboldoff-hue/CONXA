"""Phase 3 — compile normalized events into a SkillPackage (no runtime execution)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from conxa_compile.compiler.action_policy import no_recovery_block, recovery_enabled_for_action
from conxa_compile.editor.action_registry import MARKER_ACTIONS
from conxa_compile.compiler.decision_layer import rank_merged_anchors
from conxa_compile.compiler.action_semantics import commit_intent_hit, is_editable_field_click
from conxa_compile.compiler.destructive_semantics import classify_consequence, destructive_compiler_step
from conxa_compile.compiler.commit_ordering import lint_commit_ordering
from conxa_compile.compiler.entity_binding import (
    collect_input_literal_values,
    detect_entity_binding,
    upgrade_entity_binding_identifiers,
)
from conxa_compile.compiler.input_binding import derive_input_binding
from conxa_compile.compiler.choice import collapse_choice_group_runs, derive_choice
from conxa_compile.compiler.date_picker import collapse_date_picker_runs, convert_typed_date_steps
from conxa_compile.compiler.navigation_effects import fold_action_caused_navigations, generalize_url_pattern
from conxa_compile.compiler.upload_binding import apply_bindings_to_compiled_steps
from conxa_compile.compiler.loop_suggestion import detect_download_upload_loop_candidates
from conxa_compile.compiler.virtualized import detect_virtualized_container
from conxa_compile.compiler.recovery_policy import (
    default_recovery_block,
    merge_recovery_strategies_for_wait_shape,
)
from conxa_compile.compiler.selector_filters import (
    filter_selectors_dict,
    is_dynamic_id,
    is_low_quality_anchor,
    selector_passes_filters,
    uniqueness_gate,
)
from conxa_compile.compiler.selector_score import (
    durability_score,
    rank_selectors_scored,
    score_selector_row,
)
from conxa_compile.compiler.stable_hash import compute_stable_hash
from conxa_compile.compiler.second_opinion import (
    apply_second_opinion,
    archive_flagged_steps,
    rewrite_placeholder,
)
from conxa_compile.compiler.step_key import step_key_index_map, step_keys
from conxa_compile.compiler.identity_bundle import generate_deterministic_signals
from conxa_compile.compiler.selector_grammar import display_to_signal, signal_to_display
from conxa_compile.compiler.state_validation import capture_state_snapshot, compare_state, merge_dom_diff_evidence, optimize_scroll, scroll_payload, validation_from_diff
from conxa_compile.compiler.step_anchors import clean_anchors, clean_steps, fix_step_order, generate_stable_selector
from conxa_core.config import settings
# anchor_vision_llm pulls in PIL -> numpy, whose native-extension import is slow on some
# machines (multi-second to tens of seconds the first time a process touches it). Deferred
# to the two call sites below (both compile-only) so read paths like the workflow editor
# (which import this package for lightweight helpers, never compile) don't pay that cost.
if TYPE_CHECKING:
    from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError
from conxa_core.models.events import RecordedEvent
from conxa_core.models.skill_spec import (
    Assertion,
    DecisionPolicy,
    ElementFingerprint,
    FrameFingerprint,
    HandlerHints,
    IdentityBundle,
    IdentitySignal,
    RecoveryBlock,
    ShadowHost,
    SkillBlock,
    SkillMeta,
    SkillPackage,
    SkillPolicies,
    SkillStep,
    ValidationBlock,
    WorkflowIntentGraph,
)
from conxa_compile.policy.bundle import PolicyBundle, get_policy_bundle
from conxa_compile.policy.intent_ontology import intent_specificity_score, normalize_compiler_intent
from conxa_core.storage import snapshots
from conxa_core.progress import append_current_job_event


_RECOVERABLE_VISION_ANCHOR_REASONS = frozenset({
    "vision_anchors_disabled_in_policy",
    "llm_disabled",
    "llm_anchor_vision_disabled",
    "llm_endpoint_unset",
    "llm_endpoint_not_multimodal_capable",
    "vision_llm_invalid_anchor_sentence",
    # Frame extraction deferred to session shutdown; in-memory events won't have
    # full_screenshot set if extraction hasn't completed yet (e.g. stop() timeout).
    "full_screenshot_path_missing",
})
# vision_llm_request_failed / vision_llm_empty_response mean every provider is genuinely
# unavailable — the router already retries across the whole pool and waits out a 429
# cooldown (bounded by llm_router_wait_ceiling_secs) before giving up. Whether that then
# hard-stops the compile or degrades to keyword anchors is an operator choice: see
# settings.vision_anchor_fallback_on_exhaustion. This is a per-workspace cloud
# entitlement (capabilities.vision_fallback_on_exhaustion, fetched via
# GET /api/v1/entitlements/current and applied for the scope of one compile in
# handlers/compile.py::cmd_compile, right alongside _install_proxy_router) — NOT
# a cloud/Render env var; setting SKILL_VISION_ANCHOR_FALLBACK_ON_EXHAUSTION on
# the Render backend has zero effect, since this compiler only ever runs inside
# Build Studio's local process. The local env var still works as a fallback for
# dev / when the entitlement fetch itself fails.
_EXHAUSTION_VISION_ANCHOR_REASONS = frozenset({
    "vision_llm_request_failed",
    "vision_llm_empty_response",
})

_RECOVERABLE_VISION_ANCHOR_REASON_PREFIXES = ("screenshot_file_missing:",)


def _compile_log(event: str, message: str, data: dict[str, Any] | None = None) -> None:
    append_current_job_event(event, message, data or {})


def _default_confidence_protocol(bundle: PolicyBundle) -> dict[str, Any]:
    return bundle.as_confidence_protocol_fragment()


def _infer_selector_kind(selector: str) -> str:
    """Infer selector kind from its string pattern for confidence scoring."""
    s = selector.strip()
    if s.startswith("label:has-text("):
        return "label"
    if s.startswith("[aria-label="):
        return "aria"
    if s.startswith("role=") or s.startswith("internal:role="):
        # Executable Playwright role engine — matches by accessible name, not HTML name attribute.
        # public:   role=button[name="New"]        → passes through root.locator() in fallback path
        # internal: internal:role=button[name="Y"] → pre-parsed by resolve_adapter.js into getByRole
        return "aria"
    if re.match(r"\[role=", s):
        # CSS [role=...][name=...] selectors look semantic but aren't: CSS [name=...] matches the
        # HTML `name` attribute only, not an element's accessible name. A <button>New</button> has
        # no `name` HTML attribute so [role="button"][name="New"] can never match it. Score as "css"
        # (70) rather than "aria" (100) so these brittle CSS attribute selectors don't win the
        # primary slot over proper Playwright internal:role= signals.
        return "css"
    if re.match(r"input\[name=", s):
        return "name"
    if s.lower().startswith("text="):
        return "text_based"
    if s.startswith("/") or s.startswith("(//"):
        return "xpath"
    if re.match(r"#[a-zA-Z][\w-]*$", s):
        return "name"  # ID selectors are as stable as name attrs — score 95, not generic css 70
    return "css"


def _build_frame_context(ev: dict[str, Any]) -> dict[str, Any]:
    """Structural iframe-chain marker (url/url_pattern per level).

    Frame element resolution at runtime is driven by `identity_bundle.frame_chain`; this carries
    only the chain depth + url markers needed for frame_enter/frame_exit and diagnostics.
    """
    frame = ev.get("frame")
    if not isinstance(frame, dict):
        return {}
    chain = frame.get("chain")
    if not isinstance(chain, list):
        return {}
    out_chain: list[dict[str, Any]] = []
    for raw in chain:
        if not isinstance(raw, dict):
            continue
        if not (raw.get("fingerprint") or {}).get("signals"):
            continue
        out_chain.append(
            {
                "url": str(raw.get("url") or "").strip(),
                "url_pattern": str(raw.get("url_pattern") or "").strip(),
            }
        )
    return {"chain": out_chain} if out_chain else {}


def _build_tab_context(ev: dict[str, Any]) -> dict[str, Any]:
    """Which browser tab this event was recorded on (RecordedEvent.tab). The runtime resolves
    each step's page from this at replay time — see runtime/tabs.js::resolveStepPage.

    Every tab gets an explicit block, including tab_0. The earlier convention ("empty block
    means tab_0") made a return-to-the-initial-tab `tab_switch` marker destination-less, so the
    runtime's mis-stamp guard (tabs.js::stepInheritsPage) swallowed it and the switch back to
    tab_0 replayed as a no-op — see FIX.md 2026-08-23. Absent/blank tab (recordings made before
    multi-tab support) still means "tab_0" at runtime, so old skills replay unchanged."""
    tab = ev.get("tab")
    if not isinstance(tab, dict):
        return {}
    tab_id = str(tab.get("id") or "").strip()
    if not tab_id:
        return {}
    return {
        "id": tab_id,
        "index": tab.get("index", 0),
        "opened_by": str(tab.get("opened_by") or "initial"),
        "opener_tab": tab.get("opener_tab"),
        # Editor display only (WorkflowViewer.tsx's tab-boundary divider) — the runtime-facing
        # copy in execution.json (see _sanitize_runtime_tab) deliberately drops this.
        "url": str(tab.get("url") or "").strip(),
    }


def _make_tab_marker_event(action: str, tab: dict[str, Any], url: str) -> dict[str, Any]:
    """A synthetic event for a tab_open/tab_switch marker step — same shape a real recorded
    event has, but only the fields _build_step's MARKER_ACTIONS branch reads (action, frame,
    tab) are populated. Not written through RecordedEvent.model_validate (that already ran on
    the real recorded events before this function is called), so no need to fill every field
    that model requires."""
    return {
        "action": {"action": action, "timestamp": "", "value": url},
        "target": {},
        "frame": {},
        "tab": tab,
        "page": {"url": url, "title": ""},
    }


def _make_tab_navigate_event(tab: dict[str, Any], url: str) -> dict[str, Any]:
    """A synthetic `manual_navigate` event immediately following a user-opened tab's `tab_open`
    marker — _build_step's existing manual_navigate branch (not a MARKER_ACTIONS no-op) turns
    this into a real `navigate` step. Same minimal-fields shape as _make_tab_marker_event, for
    the same reason (never round-tripped through RecordedEvent.model_validate).

    Inserted here, at the EVENT level, rather than as a step spliced in after the fact
    (the old _insert_user_tab_navigate_steps): every later pass that walks `steps` and `events`
    in lockstep (hover-chain population, choice-group collapse, date-picker collapse) depends on
    steps[i] mapping 1:1 onto events[i], and a step inserted without a matching event silently
    breaks that for everything downstream. Producing the extra event here instead keeps the
    invariant true by construction all the way through the step-building loop.
    """
    return {
        "action": {"action": "manual_navigate", "timestamp": "", "value": json.dumps({"to_url": url})},
        "target": {},
        "frame": {},
        "tab": tab,
        "page": {"url": url, "title": ""},
    }


def _insert_tab_markers(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert a tab_open/tab_switch marker event immediately before the first event recorded
    on a different tab than the previous event — the tab-context twin of frame_enter/
    frame_exit, and compiled through the same no-op MARKER_ACTIONS path. tab_open the first
    time a tab id is seen, tab_switch when returning to one already seen (including back to
    the initial tab). The very first event never gets a marker — that's just the recording's
    starting tab, not something that "opened" mid-workflow.

    A `tab_open` for a tab the *user* opened (Ctrl+T or similar — nothing on the recorded page
    ever opens it) is immediately followed by a synthetic `manual_navigate` event to the tab's
    destination URL (see _make_tab_navigate_event). runtime/tabs.js::resolveStepPage creates a
    blank page for opened_by="user" tabs and relies on "the recorded first action on this tab"
    being a navigate; without this, a user-opened tab replays as a permanently blank page and
    every subsequent step on it fails with "Element not found". opened_by="site" tabs (a
    link/window.open) get no such event — the click that triggers them is itself replayed
    normally and the browser navigates them as a natural side effect, exactly like it did at
    record time."""
    if not events:
        return events
    out: list[dict[str, Any]] = []
    first_tab = events[0].get("tab") if isinstance(events[0].get("tab"), dict) else {}
    prev_tab_id = str(first_tab.get("id") or "tab_0")
    seen_tabs = {prev_tab_id}
    for i, ev in enumerate(events):
        tab = ev.get("tab") if isinstance(ev.get("tab"), dict) else {}
        tab_id = str(tab.get("id") or "tab_0")
        if i > 0 and tab_id != prev_tab_id:
            marker_action = "tab_switch" if tab_id in seen_tabs else "tab_open"
            url = str((ev.get("page") or {}).get("url") or "")
            out.append(_make_tab_marker_event(marker_action, tab, url))
            if marker_action == "tab_open" and str(tab.get("opened_by") or "") == "user" and url:
                out.append(_make_tab_navigate_event(tab, url))
            seen_tabs.add(tab_id)
        out.append(ev)
        prev_tab_id = tab_id
    return out


def _navigate_step(url: str, tab: dict[str, Any]) -> SkillStep:
    """A synthetic `navigate` step to `url` — used everywhere the compiler has to insert a page
    load the recording itself never performed as a user action (a tab open, the recording's own
    starting page)."""
    return SkillStep(
        action="navigate",
        intent="navigate_to_page",
        url=url,
        tab=dict(tab),
        recovery=RecoveryBlock(**no_recovery_block("navigate_to_page")),
        validation=ValidationBlock(
            wait_for={"type": "url_change", "target": url, "timeout": 60000},
            success_conditions={"url": url},
        ),
    )


def _insert_start_navigate_step(
    steps: list[SkillStep], cleaned_events: list[dict[str, Any]]
) -> list[SkillStep]:
    """Give the recording's own starting tab the same leading `navigate` step every
    user-opened tab already gets (via the synthetic manual_navigate event
    _insert_tab_markers inserts for it — see _make_tab_navigate_event).

    _insert_tab_markers deliberately emits no tab_open marker for the recording's initial tab —
    "that's just the recording's starting tab, not something that 'opened' mid-workflow." That's
    correct for markers, but it means tab_0 is the one tab with no recorded navigation: nothing
    in the compiled skill says where step 1 is supposed to run. The runtime used to guess (the
    group app's landing page), which fails whenever that's a different site than the recording —
    see FIX.md. This makes tab_0 consistent with every other tab: the skill's own first step is
    a `navigate` to wherever the recording actually started.

    Must run AFTER _populate_hover_chains and the choice/date-picker collapses (it prepends,
    which would desync their steps[i]-against-events[i] indexing if run first).
    """
    if not steps or not cleaned_events:
        return steps
    if steps[0].action == "navigate":
        return steps  # the recording already opens with a real navigation
    url = str((cleaned_events[0].get("page") or {}).get("url") or "").strip()
    if not url or url.split(":", 1)[0] == "about":
        return steps
    return [_navigate_step(url, steps[0].tab)] + steps


def _merge_compile_warnings(
    protocol: dict[str, Any],
    ev_with_intent: dict[str, Any],
    merged_anchors: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    vision_anchor_warning: dict[str, Any] | None = None,
    weak_fingerprint: bool = False,
) -> dict[str, Any]:
    out = dict(protocol)
    cw = dict(out.get("compile_warnings") or {})
    if vision_anchor_warning:
        cw["vision_anchor_fallback"] = vision_anchor_warning
    if weak_fingerprint:
        cw["weak_fingerprint"] = True
    unc = policy.get("uncertainty") if isinstance(policy.get("uncertainty"), dict) else {}
    min_a = int(unc.get("destructive_min_anchors_warn", 2))
    # The full-sentence entry (relation == "target_sentence") isn't a discriminative
    # short anchor — exclude it so it can't silently mask a genuinely low anchor count.
    discriminative_count = sum(1 for a in merged_anchors if a.get("relation") != "target_sentence")
    if destructive_compiler_step(ev_with_intent, policy) and discriminative_count < min_a:
        cw["destructive_low_anchor_count"] = True
    if cw:
        out["compile_warnings"] = cw
    return out


def _vision_anchor_failure_is_recoverable(exc: VisionAnchorGenerationError) -> bool:
    reason = str(exc.reason or "")
    if reason in _RECOVERABLE_VISION_ANCHOR_REASONS or any(
        reason.startswith(p) for p in _RECOVERABLE_VISION_ANCHOR_REASON_PREFIXES
    ):
        return True
    if reason in _EXHAUSTION_VISION_ANCHOR_REASONS:
        return bool(settings.vision_anchor_fallback_on_exhaustion)
    return False


def _fallback_anchors_from_event(ev_with_intent: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    anchors = clean_anchors(
        ev_with_intent.get("anchors") or [],
        ev_with_intent.get("context") or {},
        policy,
        target=dict(ev_with_intent.get("target") or {}),
        semantic=dict(ev_with_intent.get("semantic") or {}),
    )
    target = ev_with_intent.get("target") if isinstance(ev_with_intent.get("target"), dict) else {}
    semantic = ev_with_intent.get("semantic") if isinstance(ev_with_intent.get("semantic"), dict) else {}
    direct = ""
    for key in ("inner_text", "aria_label", "name", "placeholder"):
        direct = str(target.get(key) or "").strip()
        if direct:
            break
    if not direct:
        direct = str(semantic.get("normalized_text") or "").strip()
    direct = " ".join(direct.lower().split())[:96]
    if direct and direct not in {"button", "input", "link", "element"}:
        target_anchor = {"element": direct, "relation": "target"}
        anchors = [target_anchor, *[a for a in anchors if str(a.get("element") or "").lower() != direct]]
    return anchors


def _vision_anchor_warning(exc: VisionAnchorGenerationError, *, step_index: int) -> dict[str, Any]:
    warning: dict[str, Any] = {
        "reason": str(exc.reason or ""),
        "step_index": exc.step_index if exc.step_index is not None else step_index,
        "fallback": "deterministic_anchors",
    }
    if exc.hint:
        warning["hint"] = exc.hint
    return warning


def _log_vision_anchor_fallback_summary(steps: list[SkillStep]) -> None:
    """Emit one aggregate event when steps fell back to keyword anchors.

    Per-step `vision_anchor_fallback` warnings are easy to miss buried in a long compile
    log — a provider cooldown degrading 30 of 41 steps should be obvious at a glance, not
    something you notice only after runtime recovery starts flaking.
    """
    fallbacks = [
        step.confidence_protocol.get("compile_warnings", {}).get("vision_anchor_fallback")
        for step in steps
        if isinstance(step.confidence_protocol, dict)
        and (step.confidence_protocol.get("compile_warnings") or {}).get("vision_anchor_fallback")
    ]
    if not fallbacks:
        return
    first = fallbacks[0] if isinstance(fallbacks[0], dict) else {}
    _compile_log(
        "compile_phase",
        f"Vision anchors fell back to keyword anchors for {len(fallbacks)} of {len(steps)} step(s).",
        {
            "phase": "vision_anchor_fallback_summary",
            "fallback_count": len(fallbacks),
            "step_count": len(steps),
            "first_reason": first.get("reason"),
            "first_hint": first.get("hint"),
        },
    )


def _persisted_visual_asset_path(
    ev: dict[str, Any],
    rel: str | None,
    *,
    session_id_fallback: str = "",
) -> str:
    """Turn recorder-relative paths (files under sessions/<id>/) into paths under data_dir."""
    if not rel or not isinstance(rel, str):
        return ""
    r = rel.strip().replace("\\", "/")
    if not r or ".." in r:
        return ""
    if r.startswith("sessions/"):
        return r
    session_id = str((ev.get("extras") or {}).get("session_id") or "").strip()
    if not session_id:
        session_id = str(session_id_fallback or "").strip()
    if session_id:
        return f"sessions/{session_id}/{r}"
    return r


def _extract_visited_hosts(cleaned_events: list[dict[str, Any]]) -> list[str]:
    """Every hostname this recording actually navigated to — main frame
    (ev.page.url) and any tab opened during the recording (ev.tab.url) — so
    SkillMeta.visited_hosts can flag unclaimed_hosts from everywhere the workflow
    goes, not just where it starts. See group_store.unclaimed_hosts."""
    from conxa_compile.recorder.session import extract_visited_hosts

    return extract_visited_hosts(cleaned_events)


def build_signal_reference(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_kind": ev.get("action", {}).get("action"),
        "target": ev.get("target") or {},
        "selectors": ev.get("selectors") or {},
        "semantic": ev.get("semantic") or {},
        "context": ev.get("context") or {},
        "anchors": ev.get("anchors") or [],
        "visual": {
            "bbox": (ev.get("visual") or {}).get("bbox") or {},
            "viewport": (ev.get("visual") or {}).get("viewport") or "",
            "scroll_position": (ev.get("visual") or {}).get("scroll_position") or "",
        },
        "state_after": (ev.get("state_change") or {}).get("after") or "",
        "page_url": (ev.get("page") or {}).get("url") or "",
        "page_title": (ev.get("page") or {}).get("title") or "",
    }


def _build_element_fingerprint(ev: dict[str, Any]) -> ElementFingerprint:
    """Extract stable element identity from recorded event signals."""
    target = ev.get("target") or {}
    semantic = ev.get("semantic") or {}
    selectors = ev.get("selectors") or {}
    anchors = ev.get("anchors") or []
    visual = ev.get("visual") or {}

    # Extract data-testid / data-test-id from CSS selector — highest-stability attribute
    data_testid = ""
    # `(?:-id)?` alone only matched the hyphenated "data-test-id" convention and missed
    # the far more common unhyphenated "data-testid" (React/testing-library) attribute
    # entirely — `(?:-?id)?` covers "data-test", "data-testid", and "data-test-id" alike.
    _TESTID_RE = re.compile(r'data-test(?:-?id)?=["\']?([^"\'>\s\]]+)')
    css = str(selectors.get("css") or "")
    m = _TESTID_RE.search(css)
    if m:
        data_testid = m.group(1)
    if not data_testid:
        aria = str(selectors.get("aria") or "")
        m2 = _TESTID_RE.search(aria)
        if m2:
            data_testid = m2.group(1)

    inner_text = str(target.get("inner_text") or semantic.get("normalized_text") or "").strip()[:120]

    # Only keep class tokens that look stable (no hash-like sequences, min length 3)
    raw_classes = " ".join(target.get("classes") or []) if isinstance(target.get("classes"), list) else str(target.get("classes") or "")
    class_tokens = [
        c for c in raw_classes.split()
        if len(c) >= 3 and not re.search(r"[0-9]{4,}|[a-f0-9]{6,}", c)
    ][:8]

    anchor_phrases = [
        str(a.get("element") or "").strip()
        for a in anchors
        if a.get("element") and str(a.get("element")).strip()
        and not is_low_quality_anchor(str(a.get("element") or ""))
    ][:6]

    bbox = visual.get("bbox") or {}
    vw = max(int(bbox.get("vw", 0)) or 1280, 1)
    vh = max(int(bbox.get("vh", 0)) or 800, 1)

    return ElementFingerprint(
        role=str(semantic.get("role") or target.get("role") or ""),
        tag=str(target.get("tag") or ""),
        inner_text=inner_text,
        aria_label=str(target.get("aria_label") or ""),
        name=str(target.get("name") or ""),
        placeholder=str(target.get("placeholder") or ""),
        label_text=str(target.get("label_text") or ""),
        alt=str(target.get("alt") or ""),
        title=str(target.get("title") or ""),
        data_testid=data_testid,
        input_type=str(semantic.get("input_type") or ""),
        css_class_tokens=class_tokens,
        anchor_phrases=anchor_phrases,
        position_hint={
            "x_pct": round(int(bbox.get("x") or 0) / vw, 3),
            "y_pct": round(int(bbox.get("y") or 0) / vh, 3),
        },
    )


def _fingerprint_is_weak(fp: ElementFingerprint) -> bool:
    """No strong identity signal recorded — a future runtime recovery case waiting to happen.

    Mirrors the three fields runtime/resolver.js::scoreCandidate weights most heavily
    (testid, aria_label/name, inner_text). A fingerprint carrying none of them can only
    ever be rescued by the resolver's trusted-contract escape hatch or full recovery.
    """
    return not (fp.data_testid or fp.aria_label or fp.name or fp.inner_text)


def _load_step_snapshot(ev: dict[str, Any], session_id: str) -> tuple[str | None, dict[str, Any] | None]:
    """Load the recorded page's HTML + accessibility tree for compile-time uniqueness checks.

    The event only carries a `dom_hash` pointer (see session.py) — the actual blobs live in
    the session's content-addressable snapshot store and must be read from disk.
    """
    if not session_id:
        return None, None
    dom_hash = str((ev.get("snapshot") or {}).get("dom_hash") or "").strip()
    if not dom_hash:
        return None, None
    html = snapshots.read_dom_snapshot(session_id, dom_hash)
    a11y_tree = snapshots.read_a11y_snapshot(session_id, dom_hash)
    return html, a11y_tree


def _build_identity_bundle(
    ev: dict[str, Any],
    dom_html: str | None = None,
    a11y_tree: dict[str, Any] | None = None,
) -> IdentityBundle:
    """Build durability-ranked, orthogonality-deduplicated IdentityBundle from a recorded event.

    Uses the deterministic-floor generator (Playwright grammar, zero-LLM) as the signal base.
    The bundle also carries the scoring `fingerprint` (the resolver's oracle) — single source
    of truth for element identity.
    """
    target = ev.get("target") or {}
    selectors = ev.get("selectors") or {}

    # The scoring oracle the runtime resolver ranks candidates against.
    fingerprint = _build_element_fingerprint(ev)

    # Phase 4: use deterministic-floor generator (Playwright grammar)
    signals = generate_deterministic_signals(ev, dom_html=dom_html, a11y_tree=a11y_tree)

    distinct_classes = {s.orthogonality_class for s in signals}
    if len(distinct_classes) < 2:
        # Log only — single orthogonality class means the recorded data lacks role/text fields.
        # Capture richer data in the recorder (bridge.js) to improve signal diversity.
        _compile_log(
            "compile_step",
            f"Deterministic floor has {len(distinct_classes)} orthogonality class(es): {distinct_classes or set()} — consider enriching recorder capture.",
            {"phase": "identity_bundle_low_orthogonality", "classes": list(distinct_classes)},
        )

    element_data = {
        "tag": str(target.get("tag") or ""),
        "parent_tag": "",
        "aria_label": str(target.get("aria_label") or ""),
        "name": str(target.get("name") or ""),
        "inner_text": str(target.get("inner_text") or "")[:80],
        "attributes": target.get("attributes") or {},
    }
    s_hash = compute_stable_hash(element_data)

    # Extract testid for guid_like_attrs check
    data_testid = ""
    _TESTID_RE2 = re.compile(r'data-test(?:-?id)?=["\']?([^"\'>\s\]]+)')
    for key in ("css", "aria"):
        m = _TESTID_RE2.search(str(selectors.get(key) or ""))
        if m:
            data_testid = m.group(1)
            break
    guid_like_attrs = [data_testid] if data_testid and is_dynamic_id(f"#{data_testid}") else []

    # Phase 6: detect and populate shadow_path
    shadow_path_raw = ev.get("shadow_path") or []
    if not shadow_path_raw:
        for sel_key in ("css", "aria"):
            sel_val = str((ev.get("selectors") or {}).get(sel_key) or "")
            if "::part(" in sel_val or ":host(" in sel_val:
                shadow_path_raw = [{"host": "unknown", "mode": "open"}]
                break
    shadow_path = [
        ShadowHost(**h) if isinstance(h, dict) else h
        for h in shadow_path_raw
        if isinstance(h, (dict, ShadowHost))
    ]
    # Apply XPath guard: drop XPath signals when element is inside a shadow root
    if shadow_path:
        from conxa_compile.compiler.selector_filters import css_shadow_unverifiable, xpath_shadow_guard
        signals = [s for s in signals if xpath_shadow_guard(s.engine, shadow_path)]
        # A css-structural/css-id signal's unique_at_compile=True is meaningless for a
        # shadow-sourced element (see css_shadow_unverifiable) — correct the stamp rather than
        # drop the signal, since it can still serve as a fallback below role/text signals.
        signals = [
            s.model_copy(update={"unique_at_compile": False})
            if css_shadow_unverifiable(s.engine, shadow_path) and s.unique_at_compile
            else s
            for s in signals
        ]

    # Phase 5: build frame_chain from per-frame fingerprints (the sole frame identity source).
    frame_chain: list[FrameFingerprint] = []
    for spec in ((ev.get("frame") or {}).get("chain") or []):
        fp_dict = spec.get("fingerprint") or {}
        fp_signals = [
            IdentitySignal(**sig_dict)
            for sig_dict in (fp_dict.get("signals") or [])
            if isinstance(sig_dict, dict)
        ]
        if fp_signals:
            frame_chain.append(FrameFingerprint(
                signals=fp_signals,
                url=str(spec.get("url") or fp_dict.get("url") or ""),
                url_pattern=str(spec.get("url_pattern") or fp_dict.get("url_pattern") or ""),
            ))

    return IdentityBundle(
        signals=signals,
        fingerprint=fingerprint,
        stable_hash=s_hash,
        frame_chain=frame_chain,
        shadow_path=shadow_path,
        compat_fingerprint="",
        guid_like_attrs=guid_like_attrs,
        destructive=bool(ev.get("destructive", False)),
    )


def _populate_hover_chains(steps: list[SkillStep], events: list[dict[str, Any]], session_id: str = "") -> None:
    """Set handler_hints.hover_chain on steps whose recorded predecessor was a hover (Phase 7).

    Requires steps[i] to map 1:1 onto events[i] — same invariant collapse_choice_group_runs and
    collapse_date_picker_runs depend on. A caller that violates it (an earlier pass inserted a
    step with no matching event, or vice versa) gets a loud failure here instead of hints
    silently landing on the wrong steps."""
    if len(steps) != len(events):
        raise ValueError(
            f"_populate_hover_chains: steps ({len(steps)}) and events ({len(events)}) "
            "must be the same length — an earlier pass desynced them"
        )
    from conxa_compile.compiler.action_semantics import detect_hover_precondition
    for i, ev in enumerate(events):
        prev_ev = events[i - 1] if i > 0 else None
        if not detect_hover_precondition(ev, prev_ev):
            continue
        dom_html, a11y_tree = _load_step_snapshot(prev_ev, session_id)
        hover_signals = generate_deterministic_signals(prev_ev, dom_html=dom_html, a11y_tree=a11y_tree)
        if hover_signals:
            steps[i].handler_hints.hover_chain = hover_signals


def _build_assertions(
    ev: dict[str, Any],
    validation: ValidationBlock,
    policy: dict[str, Any],
    target: dict[str, Any],
    value: Any = None,
) -> list[Assertion]:
    """Compile verifiable post-action assertions from all available evidence.

    Deterministic "primary signal picker": every consequential action gets exactly one
    REQUIRED (enforced) assertion — the single post-condition the runtime must re-confirm
    after the action and after every recovery remedy. All other evidence stays advisory
    (required=False). focus/scroll have no observable post-action outcome and get nothing.
    """
    assertions: list[Assertion] = []
    action = str((ev.get("action") or {}).get("action") or "").lower()

    if action in {"focus", "scroll"}:
        return []

    wf = validation.wait_for
    wf_type = str(wf.get("type") or "")
    wf_target = str(wf.get("target") or "")
    wf_timeout = int(wf.get("timeout") or 5000)
    primary_selector = str(target.get("primary_selector") or "")

    # Post-condition distillation (recording-next-steps.md Priority 1): the recorder already
    # classified the observed delta in-page (bridge.js::buildPostCondition). Prefer that specific
    # evidence for the enforced assertion over the generic wait_for/success_conditions inference
    # below — it is captured live against the running page, not reconstructed after the fact.
    post_condition = ev.get("post_condition") if isinstance(ev.get("post_condition"), dict) else {}
    classified_effect = str(post_condition.get("classified_effect") or "")
    value_readback = post_condition.get("value_readback")
    dialog_signal = post_condition.get("dialog_signal")

    if classified_effect == "dialog_opened" and dialog_signal:
        assertions.append(Assertion(
            type="selector_present",
            target=str(dialog_signal),
            timeout_ms=wf_timeout,
            required=True,
        ))

    # Text entry / selection: the enforced post-condition is that the field actually holds the
    # value we typed/selected — this is the action's own direct effect, independent of wait_for.
    # Prefer the live readback (catches framework normalization/combobox commits the recorded
    # intent value wouldn't show) over the recorded value; redacted/absent readback falls back.
    # Never override a templated {{binding}} value, though — the whole point of a bound input is
    # that a later run supplies a DIFFERENT value than what was recorded, and runtime's
    # assertions.js interpolates `expected` against that run's inputs. Overwriting it with the
    # literal recorded readback bakes in this recording's answer forever, so the assertion
    # fails on every subsequent run with different input (audit finding — see FIX.md).
    raw_value = (ev.get("action") or {}).get("value")
    is_key_event = isinstance(raw_value, str) and raw_value.strip().startswith("{")
    is_bound_value = isinstance(value, str) and value.startswith("{{")
    effective_value = value
    if (
        classified_effect == "value_set"
        and isinstance(value_readback, str)
        and value_readback
        and value_readback != "{{REDACTED}}"
        and not is_bound_value
    ):
        effective_value = value_readback
    if (
        not assertions
        and action in {"fill", "type", "select", "select_option", "date_pick"}
        and primary_selector
        and effective_value
        and not is_key_event
    ):
        assertions.append(Assertion(
            type="value_equals",
            target=primary_selector,
            expected=str(effective_value),
            timeout_ms=wf_timeout,
            required=True,
        ))

    required_assigned = bool(assertions)  # dialog/value_equals already claimed the enforced slot

    # Primary wait_for assertion — becomes the enforced post-condition for actions that didn't
    # already claim one above (navigation / commit / destructive clicks with DOM evidence).
    if wf_type == "url_change":
        before_url = str((ev.get("page") or {}).get("url") or "")
        # URL must change but we don't know to what — assert it differs from current
        assertions.append(Assertion(
            type="url_changed",
            target=before_url,
            timeout_ms=wf_timeout,
            required=not required_assigned,
        ))
        required_assigned = True
    elif wf_type == "element_appear" and wf_target:
        assertions.append(Assertion(
            type="selector_present",
            target=wf_target,
            timeout_ms=wf_timeout,
            required=not required_assigned,
        ))
        required_assigned = True

    # A click only counts as "consequential" (submit/commit a form, destructive confirm) when
    # the deterministic action-semantics helpers say so — this gates BOTH the required_elements
    # promotion below and the state_changed synthesis, so an incidental click (menu toggle,
    # tooltip reveal) with unrelated DOM evidence never gets forced into a required assertion.
    is_consequential_click = (
        action == "click"
        and not is_editable_field_click(ev)
        and (commit_intent_hit(ev, policy) or destructive_compiler_step(ev, policy))
    )
    # Uploads never get a wait_for/success_conditions match above (a file input rarely moves the
    # URL or visible DOM on its own — most sites don't react until a later, separate submit), so
    # without this they'd compile with zero assertions. Folded into the same evidence-less-commit
    # fallback below rather than its own branch.
    is_upload_action = action in {"upload", "upload_intent"}

    # success_conditions evidence: for a consequential click, the first required_element is
    # promoted to the enforced post-condition if nothing has claimed that slot yet; everything
    # else (and all required_elements on non-consequential steps) stays advisory.
    sc = validation.success_conditions
    required_elements = [el for el in (sc.get("required_elements") or [])[:3] if el and isinstance(el, str)]
    expected_tokens = [tok for tok in (sc.get("expected_text_tokens") or [])[:3] if tok and isinstance(tok, str)]

    for i, el in enumerate(required_elements):
        promote = not required_assigned and i == 0 and is_consequential_click
        assertions.append(Assertion(
            type="selector_present",
            target=el,
            timeout_ms=wf_timeout,
            required=promote,
        ))
        if promote:
            required_assigned = True

    for tok in expected_tokens:
        assertions.append(Assertion(
            type="text_present",
            target=tok,
            timeout_ms=min(wf_timeout, 5000),
            required=False,
        ))

    # Consequential clicks (and uploads — see is_upload_action above) that still have no enforced
    # signal (no URL change, no DOM evidence recorded) get a synthesized state_changed check:
    # confirms the action produced SOME observable effect instead of silently no-oping.
    # Deliberately stricter than leaving these advisory — accepted tradeoff for evidence-less
    # commits (see plan).
    if not required_assigned and (is_consequential_click or is_upload_action):
        assertions.append(Assertion(
            type="state_changed",
            timeout_ms=wf_timeout,
            required=True,
        ))
        required_assigned = True

    # Where an action's own navigation landed (a click that redirected, or one folded in from a
    # standalone navigation by navigation_effects.py) is evidence, never a target: advisory only,
    # with per-run ids/tokens generalized away, so the run follows wherever the app sends it.
    if classified_effect == "navigation" and not any(a.type == "url_pattern" for a in assertions):
        landed = generalize_url_pattern(str(((post_condition.get("url_delta") or {}).get("after")) or ""))
        if landed:
            assertions.append(Assertion(type="url_pattern", target=landed, timeout_ms=wf_timeout, required=False))

    return assertions


def _build_structural_fingerprint(steps: list[SkillStep]) -> dict[str, Any]:
    """Fingerprint the first 3 interactive steps for pre-execution drift detection."""
    landmarks: list[dict[str, Any]] = []
    for step in steps[:5]:
        action = step.action if isinstance(step.action, str) else (step.action or {}).get("action", "")
        if action in {"navigate", "scroll", "browser_back", "browser_forward"}:
            # History navigations are non-interactive landmarks too — never fingerprinted.
            continue
        fp = step.identity_bundle.fingerprint
        primary = step.target.get("primary_selector", "")
        if primary or fp.data_testid or fp.aria_label or fp.inner_text:
            landmarks.append({
                "intent": step.intent,
                "primary_selector": primary,
                "data_testid": fp.data_testid,
                "aria_label": fp.aria_label,
                "inner_text": fp.inner_text[:60],
                "tag": fp.tag,
            })
        if len(landmarks) >= 3:
            break
    return {"landmarks": landmarks, "landmark_count": len(landmarks)}


def _read_environment_sidecar(session_root: Path) -> dict[str, Any]:
    """EXEC-36: read the recording environment sidecar written by
    recorder/session.py::_write_environment_sync. Best-effort — a missing sidecar (a session
    recorded before this field existed, or a page that never finished loading) compiles cleanly
    with an empty dict, which the runtime treats as "unknown," never as a mismatch."""
    path = session_root / "environment.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _confidence_from_identity_bundle(bundle: Any) -> float:
    """Derive selector confidence from IdentityBundle signal quality (the runtime's resolver oracle).

    The deterministic floor is the sole selector generator; confidence reflects how strong that
    floor is for this element — more orthogonal classes and a unique top signal mean higher
    confidence. No LLM agreement is consulted.
    """
    signals = list(getattr(bundle, "signals", None) or [])
    if not signals:
        return 0.0
    base = max((float(getattr(s, "durability", 0.0) or 0.0) for s in signals), default=0.0)
    classes = {str(getattr(s, "orthogonality_class", "") or "") for s in signals}
    classes.discard("")
    ortho_factor = 1.0 if len(classes) >= 2 else 0.7
    has_unique = any(bool(getattr(s, "unique_at_compile", False)) for s in signals)
    unique_factor = 1.0 if has_unique else 0.6
    return round(min(1.0, base * ortho_factor * unique_factor), 3)


def _build_target(
    ev: dict[str, Any],
    policy: dict[str, Any],
    identity_bundle: IdentityBundle,
    dom_html: str | None = None,
) -> dict[str, Any]:
    if str((ev.get("action") or {}).get("action") or "").lower() in MARKER_ACTIONS:
        return {}
    raw_selectors = ev.get("selectors") or {}
    selectors = filter_selectors_dict(raw_selectors)
    target = ev.get("target") or {}
    semantic = ev.get("semantic") or {}
    stable = generate_stable_selector(
        {"target": target, "selectors": selectors, "semantic": semantic}, policy
    )
    ranked_scored = rank_selectors_scored(
        {
            "aria": selectors.get("aria"),
            "name": target.get("name"),
            "text_based": selectors.get("text_based"),
            "css": selectors.get("css"),
            "xpath": selectors.get("xpath"),
        },
        policy,
    )
    ranked = [v for _, _, v in ranked_scored]
    top_score = ranked_scored[0][0] if ranked_scored else 0.0
    # Score the stable synthesized primary.
    stable_primary = str(stable.get("primary_selector") or "").strip()
    stable_score = 0.0
    if stable_primary:
        stable_kind = _infer_selector_kind(stable_primary)
        stable_score = max(0.0, score_selector_row(stable_kind, stable_primary, policy))
        top_score = max(top_score, stable_score)
    selector_confidence = round(top_score / 100.0, 3)

    # Elect primary: whichever source scores highest wins.
    # If a recorded selector beats the stable synthesized primary, promote it and demote
    # stable_primary into the fallback pool — prevents e.g. a label selector (93) from
    # shadowing an aria selector (100) that came from the recorded selectors dict.
    ranked_top_score = ranked_scored[0][0] if ranked_scored else 0.0
    if ranked_scored and ranked_top_score > stable_score and selector_passes_filters(ranked[0]):
        primary = ranked[0]
        stable_pool = ([stable_primary] if stable_primary else []) + list(stable.get("fallback_selectors") or [])
        fallback_raw = stable_pool + [s for s in ranked[1:] if s not in set(stable_pool)]
    else:
        primary = stable_primary or (ranked[0] if ranked else str(selectors.get("css") or ""))
        ranked_extra = [s for s in ranked if s not in {stable_primary, *(stable.get("fallback_selectors") or [])}]
        fallback_raw = list(stable.get("fallback_selectors") or []) + ranked_extra

    _is_bare_tag = bool(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]*", primary.strip()))
    if not selector_passes_filters(primary) or _is_bare_tag:
        primary = next((r for r in ranked if selector_passes_filters(str(r))), "")
        if not primary:
            # Last resort: use the best raw (unfiltered) selector — brittle > bare tag
            primary = next(
                (str(v).strip() for v in [
                    raw_selectors.get("css"), raw_selectors.get("aria"),
                    raw_selectors.get("text_based"), raw_selectors.get("xpath"),
                ] if v and str(v).strip()),
                str(target.get("tag") or "input"),
            )
            selector_confidence = 0.0

    # Selector generation is fully deterministic — the LLM is never asked to write a selector
    # (SeeAct Finding 3: ~30% hallucination). `target.primary_selector`/`fallback_selectors` are
    # used only by the recovery cascade (layer 2+); the runtime hot path resolves off
    # `identity_bundle.signals`. Confidence therefore reflects the IdentityBundle's strength.
    selector_confidence = _confidence_from_identity_bundle(identity_bundle)

    # Promote the IdentityBundle's top signal as the authoritative primary selector so
    # skill_package_builder._step_selector writes the same durable selector the editor displays.
    # The rank_selectors_scored/stable-selector election above is demoted into the fallback
    # pool so it remains available to the recovery cascade but does not pollute execution.json.
    # Never promote relational or xpath signals as primary — they are recovery-tier only. When
    # the bundle has no promotable top signal (an element with only a relational/xpath
    # identity, or none at all), the election above stands as `primary` unchanged — tagged
    # "legacy_fallback" below so a reviewer can tell this step skipped IdentityBundle promotion
    # instead of reading a hardcoded "deterministic" that claims otherwise (REFACTOR-AUDIT H-4).
    selector_source = "legacy_fallback"
    _bundle_signals = identity_bundle.signals or []
    if _bundle_signals:
        _top = _bundle_signals[0]
        _top_engine = str(getattr(_top, "engine", "") or "")
        if _top_engine not in ("relational", "xpath"):
            _top_display = signal_to_display(_top_engine, str(getattr(_top, "selector", "") or ""))
            if _top_display:
                # Demote the elected primary into the fallback pool (if distinct).
                if primary and primary != _top_display and primary not in fallback_raw:
                    fallback_raw = [primary] + list(fallback_raw)
                primary = _top_display
                selector_source = "deterministic"

    # Re-rank the merged fallback pool: durability-descending → xpath always last.
    # Keep all admissible candidates (no over-aggressive orthogonality collapse against the
    # bundle — that's already done inside the IdentityBundle itself). Only collapse
    # near-identical structural strings that differ solely by CSS combinator (+/~).
    _seen_fb: set[str] = set()
    # (durability, engine, selector)
    _dur_fb: list[tuple[float, str, str]] = []
    for _s in fallback_raw:
        _s = str(_s).strip()
        if not _s or _s == primary or _s in _seen_fb:
            continue
        _engine, _stored = display_to_signal(_s)
        if not selector_passes_filters(_s) and _engine not in ("testid", "role", "text_based", "relational"):
            continue
        # Drop non-unique structural selectors when a DOM snapshot is available.
        if _engine in ("css-structural", "css", "css-id", "xpath") and dom_html is not None:
            if not uniqueness_gate(_s, dom_html):
                continue
        _seen_fb.add(_s)
        _dur = durability_score(_engine, _s)
        _dur_fb.append((_dur, _engine, _s))
    # Sort durability-descending (xpath ≈0.01 lands last by construction).
    _dur_fb.sort(key=lambda r: r[0], reverse=True)
    # Collapse near-duplicate CSS structural strings that differ only by combinator (+/~).
    # E.g. "label:has-text('X') + button" and "label:has-text('X') ~ button" → keep first seen.
    _seen_combinator_base: set[str] = set()
    fallback: list[str] = []
    for _dur, _engine, _s in _dur_fb:
        if _engine in ("css-structural", "css"):
            _base = re.sub(r"\s*[+~]\s*", " * ", _s)
            if _base in _seen_combinator_base:
                continue
            _seen_combinator_base.add(_base)
        fallback.append(_s)
    input_type = semantic.get("input_type")
    target_type = "input" if input_type else str(target.get("tag") or "")
    if target_type == "button" or semantic.get("role") == "button":
        target_type = "button"
    elif target_type not in {"button", "input"}:
        target_type = "input" if target_type in {"textarea", "select"} else target_type
    # Plain CSS/text selector for the recovery cascade (`run.js` baseSelector reads target.css)
    # and the drag_drop destination — both need a raw locator string, not Playwright internal:
    # grammar. Prefer the recorded css; fall back to the deterministic primary.
    css_for_recovery = str(selectors.get("css") or "").strip()
    if not css_for_recovery or not selector_passes_filters(css_for_recovery):
        css_for_recovery = primary
    return {
        "primary_selector": primary,
        "fallback_selectors": fallback,
        "css": css_for_recovery,
        "role": str(semantic.get("role") or target.get("role") or ""),
        "type": target_type or "input",
        "selector_confidence": selector_confidence,
        "selector_source": selector_source,
    }


def _build_signals(
    ev: dict[str, Any],
    *,
    resolved_intent: str,
    policy: dict[str, Any],
    anchors_override: list[dict[str, Any]] | None = None,
    asset_session_id: str = "",
    default_frame_label: str | None = None,
) -> dict[str, Any]:
    visual = ev.get("visual") or {}
    target = dict(ev.get("target") or {})
    selectors = filter_selectors_dict(dict(ev.get("selectors") or {}))
    semantic = dict(ev.get("semantic") or {})
    semantic.pop("llm_confidence", None)
    semantic.pop("llm_source", None)
    semantic["final_intent"] = resolved_intent
    semantic["llm_intent"] = resolved_intent
    semantic.pop("intent_hint", None)
    is_scroll = str((ev.get("action") or {}).get("action") or "") == "scroll"
    sig_cfg = policy.get("signals") if isinstance(policy.get("signals"), dict) else {}
    text_max = int(sig_cfg.get("build_inner_text_max", 240))
    sib_max = int(sig_cfg.get("pipeline_siblings_max", 4))
    if is_scroll:
        target.pop("inner_text", None)
    else:
        target["inner_text"] = str(target.get("inner_text") or "")[:text_max]
    compact_context = dict(ev.get("context") or {})
    compact_context["siblings"] = list(compact_context.get("siblings") or [])[:sib_max]
    post_condition = ev.get("post_condition") if isinstance(ev.get("post_condition"), dict) else {}
    signals = {
        "dom": target,
        # BUILD-25 stage d: stashed here (compile-internal only, never copied
        # into the shipped pack by skill_package_builder_saved_skill.py) so
        # _review_inputs can read it back later, once steps no longer
        # align 1:1 with cleaned_events. Mirrors _build_assertions's own
        # classified_effect read from the same field.
        "post_condition_effect": str(post_condition.get("classified_effect") or ""),
        "selectors": {
            "aria": selectors.get("aria"),
            "text_based": selectors.get("text_based"),
            "css": selectors.get("css"),
            "xpath": selectors.get("xpath"),
        },
        "semantic": semantic,
        "context": {
            **compact_context,
            "page_url": (ev.get("page") or {}).get("url") or "",
            "page_title": (ev.get("page") or {}).get("title") or "",
            "timing": ev.get("timing") or {},
        },
        "anchors": (
            anchors_override
            if anchors_override is not None
            else clean_anchors(
                ev.get("anchors") or [],
                ev.get("context") or {},
                policy,
                target=dict(ev.get("target") or {}),
                semantic=dict(ev.get("semantic") or {}),
            )
        ),
        "visual": {
            "bbox": visual.get("bbox") or {},
            "viewport": visual.get("viewport") or "",
            "scroll_position": visual.get("scroll_position") or "",
            "full_screenshot": _persisted_visual_asset_path(
                ev, visual.get("full_screenshot"), session_id_fallback=asset_session_id
            ),
            "element_snapshot": _persisted_visual_asset_path(
                ev, visual.get("element_snapshot"), session_id_fallback=asset_session_id
            ),
            "frames": {
                label: _persisted_visual_asset_path(ev, path, session_id_fallback=asset_session_id)
                for label, path in (visual.get("frames") or {}).items()
                if path and isinstance(path, str)
            },
            "default_frame_label": default_frame_label or "before_near",
        },
    }
    if is_scroll:
        return {"visual": {"scroll_position": visual.get("scroll_position") or ""}}
    return signals


def _derive_input_binding(ev: dict[str, Any], policy: dict[str, Any]) -> tuple[Any, str | None]:
    """Delegate to derive_input_binding with priority signals (label_text → placeholder → aria_label → value pattern → input_type)."""
    value, binding = derive_input_binding(ev, policy)
    # BUILD-38: the person said, while recording, that this typed value stays exactly as recorded.
    # That is their meaning, not a selector — it keeps the literal and drops the input. Absent a
    # hint (the usual case) nothing changes.
    if binding and (ev.get("extras") or {}).get("user_varies") is False:
        raw = (ev.get("action") or {}).get("value")
        if isinstance(raw, str) and raw:
            return raw, None
    return value, binding


def _build_validation(ev: dict[str, Any], state_diff: dict[str, Any], policy: dict[str, Any]) -> ValidationBlock:
    action = str((ev.get("action") or {}).get("action") or "")
    intent = str((ev.get("semantic") or {}).get("llm_intent") or "")
    timeout = int((ev.get("timing") or {}).get("timeout") or 5000)
    page_url = str((ev.get("page") or {}).get("url") or "")
    dynamic = validation_from_diff(
        action, intent, state_diff, timeout, page_url=page_url, source_step=ev, policy=policy
    )
    return ValidationBlock(
        wait_for=dynamic.get("wait_for") or {},
        success_conditions=dynamic.get("success_conditions") or {},
    )

_PREFETCH_VISION_ANCHORS_DEADLINE_SECS = 60.0


def _prefetch_vision_anchors(
    cleaned_events: list[dict[str, Any]],
    *,
    session_root: Path,
    policy: dict[str, Any],
    graph_intent_tokens: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Best-effort parallel prefetch of every step's vision anchor request before
    the main per-step loop runs (see prefetch_vision_anchors_parallel's docstring).
    Mirrors _build_step's own
    skip conditions (scroll / MARKER_ACTIONS never call vision) and intent
    resolution (workflow-intent graph token, else blank -> normalize_compiler_intent)
    exactly, so the cache key this computes matches what _build_step computes later.

    graph_intent_tokens holds the tokens the single workflow-intent LLM call
    (run before this) produced. A step it left tokenless just gets a blank
    intent here — no per-step LLM call, no network round trip.

    Never raises: prefetch failing outright just means _build_step's per-step vision
    call does the normal single-image work it always has — a step that needs vision
    still gets its anchor, just without the parallel speedup. Returns a list (usually
    empty) of degradation entries for the compile report's `degraded` channel, so a
    systematic prefetch failure is visible to a reviewer instead of only showing up as
    "this compile took longer than usual" with no explanation."""
    from conxa_compile.llm.anchor_vision_llm import prefetch_vision_anchors_parallel

    candidates: list[tuple[int, dict[str, Any]]] = []
    for i, ev in enumerate(cleaned_events):
        action_payload = optimize_scroll(ev)
        # manual_navigate (retyped address bar, or the synthetic event
        # _make_tab_navigate_event inserts for a user-opened tab) compiles straight to a
        # `navigate` step with no element target — same as scroll and the MARKER_ACTIONS
        # no-ops, _build_step never reads a vision anchor for it, so prefetching one here is
        # pure waste.
        if action_payload in ("scroll", "manual_navigate") or action_payload in MARKER_ACTIONS:
            continue
        candidates.append((i, ev))
    if not candidates:
        return []

    _compile_log(
        "compile_phase",
        f"Prefetching vision anchors for {len(candidates)} step(s)…",
        {"phase": "vision_anchor_prefetch_start", "step_count": len(candidates)},
    )

    deadline = time.monotonic() + _PREFETCH_VISION_ANCHORS_DEADLINE_SECS
    items: list[tuple[int, dict[str, Any], str]] = []
    try:
        for i, ev in candidates:
            if time.monotonic() >= deadline:
                _compile_log(
                    "compile_phase",
                    "Vision anchor prefetch deadline reached — continuing without it.",
                    {"phase": "vision_anchor_prefetch_deadline", "level": "warn"},
                )
                return [{
                    "pass": "vision_anchor_prefetch",
                    "reason": "deadline_reached",
                    "detail": f"Prefetch exceeded {_PREFETCH_VISION_ANCHORS_DEADLINE_SECS:.0f}s "
                              f"with {len(candidates) - len(items)} of {len(candidates)} step(s) "
                              "still unqueued; each falls back to its own per-step vision call.",
                }]
            # The workflow-intent graph call already resolved this step's intent
            # token when it could. A step it left tokenless just gets a blank
            # intent — no per-step LLM call.
            llm_raw = (graph_intent_tokens or {}).get(i) or ""
            intent = normalize_compiler_intent(ev, llm_raw, policy)
            items.append((i, ev, intent))
        prefetch_vision_anchors_parallel(items, session_root=session_root, policy=policy)
    except Exception as exc:  # noqa: BLE001 — prefetch is pure optimization, never fatal
        return [{
            "pass": "vision_anchor_prefetch",
            "reason": "exception",
            "detail": f"{type(exc).__name__}: {exc}",
        }]
    return []


def _build_step(
    ev: dict[str, Any],
    bundle: PolicyBundle,
    *,
    session_root: Path,
    step_index: int,
    graph_intent_token: str | None = None,
) -> SkillStep:
    policy = bundle.data
    action_payload = optimize_scroll(ev)
    started = time.perf_counter()
    _compile_log(
        "compile_step",
        f"Compiling step {step_index + 1}.",
        {"phase": "step_start", "step_index": step_index, "action": action_payload},
    )
    if action_payload == "scroll":
        scroll_action = scroll_payload(ev, policy)
        visual = ev.get("visual") or {}
        scroll_rel = visual.get("full_screenshot") or visual.get("element_snapshot")
        scroll_screenshot = _persisted_visual_asset_path(
            ev,
            scroll_rel if isinstance(scroll_rel, str) else None,
            session_id_fallback=session_root.name,
        )
        scroll_position = visual.get("scroll_position") or ""
        visual_signals: dict[str, Any] = {"scroll_position": scroll_position}
        if scroll_screenshot:
            visual_signals["scroll_screenshot"] = scroll_screenshot
        step = SkillStep(
            action=scroll_action,
            intent="scroll_viewport",
            frame=_build_frame_context(ev),
            tab=_build_tab_context(ev),
            signals={
                "visual": visual_signals,
            },
            recovery=RecoveryBlock(**no_recovery_block("scroll_viewport")),
        )
        _compile_log(
            "compile_step",
            f"Compiled step {step_index + 1}.",
            {
                "phase": "step_done",
                "step_index": step_index,
                "action": action_payload,
                "intent": step.intent,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return step
    if action_payload in MARKER_ACTIONS:
        # download_observed's recorded value ({"url", "suggested_filename"}) must survive to
        # execution.json — skill_package_builder_saved_skill.py::_bind_downloads_to_uploads
        # matches it against a later upload step's recorded filename to wire a same-run
        # download->upload handoff (EXEC-10/W-2). dialog_accept/dialog_dismiss's recorded value
        # ({"type","message","value"}, see session.py::_drain_js_dialog_sync) is kept the same
        # way so runtime/app/handlers.js can replay the human's actual answer instead of always
        # accepting with empty text. Other markers carry no payload worth keeping.
        marker_value = None
        marker_input_binding = None
        if action_payload == "download_observed":
            raw_value = (ev.get("action") or {}).get("value")
            if raw_value:
                marker_value = str(raw_value)
        elif action_payload in ("dialog_accept", "dialog_dismiss"):
            raw_value = (ev.get("action") or {}).get("value")
            if raw_value:
                try:
                    dialog_payload = json.loads(raw_value)
                except (TypeError, ValueError):
                    dialog_payload = None
                # A prompt's typed answer becomes a named skill input rather than a literal —
                # the agent replaying this skill may need to answer differently than the human
                # did while recording. alert/confirm carry no free text, and an empty prompt
                # answer has nothing worth parameterizing.
                if (
                    isinstance(dialog_payload, dict)
                    and dialog_payload.get("type") == "prompt"
                    and dialog_payload.get("value")
                ):
                    marker_input_binding = "dialog_answer"
                    dialog_payload["value"] = "{{dialog_answer}}"
                    marker_value = json.dumps(dialog_payload)
                else:
                    marker_value = str(raw_value)
        step = SkillStep(
            action=action_payload,
            intent=str(action_payload),
            frame=_build_frame_context(ev),
            tab=_build_tab_context(ev),
            value=marker_value,
            input_binding=marker_input_binding,
            recovery=RecoveryBlock(**no_recovery_block(str(action_payload))),
        )
        _compile_log(
            "compile_step",
            f"Compiled step {step_index + 1}.",
            {
                "phase": "step_done",
                "step_index": step_index,
                "action": action_payload,
                "intent": str(action_payload),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return step
    if action_payload in {"browser_back", "browser_forward"}:
        # Real executable navigation step (NOT a marker): replay calls page.goBack()/
        # page.goForward() on the recorded tab, so the browser's own history machinery
        # reproduces the navigation exactly as the user performed it — never a guessed
        # URL. No element target, no LLM intent, no vision anchors (same reasoning as
        # scroll: nothing to select). The recorded value carries {from_url, to_url}; to_url
        # feeds the wait_for validation so the next step starts on the settled page.
        raw_value = str((ev.get("action") or {}).get("value") or "")
        to_url = ""
        try:
            parsed = json.loads(raw_value) if raw_value else {}
            if isinstance(parsed, dict):
                to_url = str(parsed.get("to_url") or "").strip()
        except Exception:  # noqa: BLE001
            to_url = ""
        intent = "history_back" if action_payload == "browser_back" else "history_forward"
        step = SkillStep(
            action=action_payload,
            intent=intent,
            url=to_url,
            frame=_build_frame_context(ev),
            tab=_build_tab_context(ev),
            recovery=RecoveryBlock(**no_recovery_block(intent)),
            validation=ValidationBlock(
                wait_for={"type": "url_change", "target": to_url, "timeout": 60000} if to_url else {},
                success_conditions={"url": to_url} if to_url else {},
            ),
        )
        _compile_log(
            "compile_step",
            f"Compiled step {step_index + 1}.",
            {
                "phase": "step_done",
                "step_index": step_index,
                "action": action_payload,
                "intent": intent,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return step
    if action_payload == "manual_navigate":
        # The user retyped the address bar (or used a bookmark) mid-recording — CDP's
        # Page.frameRequestedNavigation told the recorder no click/submit/script on the page
        # asked for this, so no other recorded step will ever reproduce it. Compile straight to
        # the same `navigate` step type _insert_start_navigate_step and the synthetic
        # manual_navigate event _make_tab_navigate_event inserts for user-opened tabs already
        # use for tab-open/start navigations — page.goto(to_url) on replay, no element target,
        # no LLM intent, no vision anchors.
        raw_value = str((ev.get("action") or {}).get("value") or "")
        to_url = ""
        try:
            parsed = json.loads(raw_value) if raw_value else {}
            if isinstance(parsed, dict):
                to_url = str(parsed.get("to_url") or "").strip()
        except Exception:  # noqa: BLE001
            to_url = ""
        step = _navigate_step(to_url, _build_tab_context(ev))
        _compile_log(
            "compile_step",
            f"Compiled step {step_index + 1}.",
            {
                "phase": "step_done",
                "step_index": step_index,
                "action": "navigate",
                "intent": step.intent,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return step
    # Single-source intent: resolved by the one workflow-intent LLM call that ran
    # before the step loop. A step it left tokenless gets a blank intent — no
    # per-step LLM call. normalize_compiler_intent still validates/normalizes below.
    llm_raw = graph_intent_token or ""
    intent = normalize_compiler_intent(ev, llm_raw, policy)
    state_before = capture_state_snapshot(ev, before=True)
    state_after = capture_state_snapshot(ev, before=False)
    state_diff = compare_state(state_before, state_after)
    state_diff = merge_dom_diff_evidence(ev, state_diff)
    ev_with_intent = dict(ev)
    semantic = dict(ev_with_intent.get("semantic") or {})
    pipeline_candidate = str(semantic.get("llm_intent") or "").strip()
    if pipeline_candidate and pipeline_candidate != intent:
        semantic["intent_candidate"] = pipeline_candidate
    semantic["final_intent"] = intent
    semantic["llm_intent"] = intent
    semantic["intent_specificity_score"] = intent_specificity_score(intent, policy)
    ev_with_intent["semantic"] = semantic
    from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError, generate_anchors_for_step_or_raise

    vision_anchor_warning: dict[str, Any] | None = None
    chosen_frame: str | None = None
    try:
        _compile_log(
            "compile_step",
            f"Generating vision anchors for step {step_index + 1}.",
            {"phase": "vision_anchor_start", "step_index": step_index, "intent": intent},
        )
        vision_result = generate_anchors_for_step_or_raise(
            ev_with_intent,
            session_root=session_root,
            final_intent=intent,
            policy=policy,
            step_index=step_index,
        )
        chosen_frame = str(vision_result.get("chosen_frame") or "") or None
        # anchor_phrases: short strings extracted from the vision LLM's sentence, for the
        # existing anchor-consuming paths (recovery.json's short-anchor list). One extra
        # entry carries the FULL sentence, tagged so it's never mistaken for a short
        # phrase downstream — see skill_package_builder_saved_skill.py::
        # _saved_recovery_anchor_sentence, the only reader of this tag.
        # The first extracted phrase is the primary/most-discriminative one (the sentence's
        # own leading noun phrase) — tagged "target" so priority-aware consumers (e.g.
        # _saved_recovery_anchors) keep treating it as the best anchor, matching the old
        # primary_phrase convention.
        phrases = list(vision_result.get("anchor_phrases") or [])
        merged_anchors: list[dict[str, Any]] = [
            {"element": p, "relation": "target" if i == 0 else "near"} for i, p in enumerate(phrases)
        ]
        anchor_sentence = str(vision_result.get("anchor_sentence") or "")
        if anchor_sentence:
            merged_anchors.append({"element": anchor_sentence, "relation": "target_sentence"})
        _compile_log(
            "compile_step",
            f"Vision anchors generated for step {step_index + 1}.",
            {"phase": "vision_anchor_done", "step_index": step_index, "anchor_count": len(merged_anchors)},
        )
    except VisionAnchorGenerationError as exc:
        if not _vision_anchor_failure_is_recoverable(exc):
            raise
        merged_anchors = _fallback_anchors_from_event(ev_with_intent, policy)
        vision_anchor_warning = _vision_anchor_warning(exc, step_index=step_index)
        _compile_log(
            "compile_step",
            f"Vision anchors fell back for step {step_index + 1}.",
            {
                "phase": "vision_anchor_fallback",
                "step_index": step_index,
                "reason": exc.reason,
                "anchor_count": len(merged_anchors),
            },
        )
    merged_anchors = rank_merged_anchors(merged_anchors, ev_with_intent, intent, policy)
    validation = _build_validation(ev_with_intent, state_diff, policy)
    if recovery_enabled_for_action(action_payload):
        recovery_dict = default_recovery_block(intent, merged_anchors, policy)
        recovery_dict = merge_recovery_strategies_for_wait_shape(
            recovery_dict,
            dict(validation.wait_for) if validation.wait_for else {},
            policy,
        )
    else:
        recovery_dict = no_recovery_block(intent)
    recovery = RecoveryBlock(**recovery_dict)
    dom_html, a11y_tree = _load_step_snapshot(ev_with_intent, session_root.name)
    identity_bundle = _build_identity_bundle(ev_with_intent, dom_html=dom_html, a11y_tree=a11y_tree)
    target = _build_target(ev, policy, identity_bundle=identity_bundle, dom_html=dom_html)
    # PROD-3: replaces the dead `ev.get("destructive", False)` default — RecordedEvent has no
    # such field, so this line was the only writer of identity_bundle.destructive and it never
    # fired. classify_consequence is the real, policy-driven classification.
    consequence = classify_consequence(ev_with_intent, policy)
    identity_bundle.destructive = consequence == "irreversible"
    # EXEC-38: entity binding used to be detected only for irreversible steps (the only case
    # PROD-3's publish gate/runtime enforcement cared about). A for_each loop body needs row
    # scoping on its ORDINARY steps too — the whole point of {{row_id}} entity binding inside a
    # loop is that every step in the body targets the current row, not just its destructive one
    # — so detection now always runs; only the PUBLISH-GATE CONFIRMATION requirement stays
    # irreversible-only (see patch_gate.py::irreversible_step_requires_confirmed_entity_binding).
    entity_binding = detect_entity_binding(ev_with_intent)
    signals = _build_signals(
        ev,
        resolved_intent=intent,
        policy=policy,
        anchors_override=merged_anchors,
        asset_session_id=session_root.name,
        default_frame_label=chosen_frame,
    )
    value, input_binding = _derive_input_binding(ev, policy)
    # Multiple-choice controls (radio/select/ARIA radiogroup/listbox -- checkbox groups are
    # handled separately by collapse_choice_group_runs, a post-pass below since one group spans
    # several events): override the label-derived binding above with one named after the
    # QUESTION ("gender"), never the recorded ANSWER ("male"), and stash the full option set for
    # the runtime to validate a caller's value against. Takes priority over
    # _derive_input_binding's label/placeholder chain, which is what produced {{male}} before
    # this existed -- see CLAUDE.md's multiple-choice plan.
    choice_hints = derive_choice(ev)
    # BUILD-30: a step whose target sits inside a scroll container that virtualizes its rows
    # (AG Grid, react-window, TanStack Virtual, ...) needs this at replay — see
    # runtime/app/resolution.js, which scrolls the container and re-gathers before giving up on
    # a step whose row isn't in the DOM yet. "" (the common case) means no virtualization hint.
    virtualized_container = detect_virtualized_container(ev)
    handler_hints = HandlerHints(virtualized_container=virtualized_container)
    if choice_hints is not None:
        value = choice_hints["value"]
        input_binding = choice_hints["input_binding"]
        handler_hints = HandlerHints(
            control_kind="choice",
            choice=choice_hints["choice"],
            virtualized_container=virtualized_container,
        )
    confidence_protocol = _merge_compile_warnings(
        _default_confidence_protocol(bundle),
        ev_with_intent,
        merged_anchors,
        policy,
        vision_anchor_warning=vision_anchor_warning,
        weak_fingerprint=_fingerprint_is_weak(identity_bundle.fingerprint),
    )
    sel_conf = target.get("selector_confidence", 1.0)
    if sel_conf <= 0.5:
        cw = dict(confidence_protocol.get("compile_warnings") or {})
        cw["selector_confidence"] = sel_conf
        confidence_protocol = {**confidence_protocol, "compile_warnings": cw}
    assertions = _build_assertions(ev_with_intent, validation, policy, target, value)
    # A synthesized state_changed assertion (build.py's own fallback for a consequential click
    # with zero other evidence — no URL change, no post_condition, no recorded DOM diff) means the
    # compiler had nothing real to verify against and fell back to "something happened". That was
    # previously silent; surface it the same way weak_fingerprint/selector_confidence already are,
    # so a human can see it in the compile report instead of only finding out at runtime.
    if any(a.type == "state_changed" for a in assertions):
        cw = dict(confidence_protocol.get("compile_warnings") or {})
        cw["weak_evidence"] = True
        confidence_protocol = {**confidence_protocol, "compile_warnings": cw}
    # A recorded frame level lacking fingerprint.signals is silently dropped from
    # identity_bundle.frame_chain (see _build_identity_bundle) — without this warning the runtime
    # then treats the whole chain as absent (rootCandidates -> [page], see runtime/run.js) with no
    # diagnostic trail, silently mis-scoping or resolve-missing at runtime for a step that was
    # actually recorded inside an iframe.
    raw_frame_chain = (ev_with_intent.get("frame") or {}).get("chain") or []
    if isinstance(raw_frame_chain, list) and len(raw_frame_chain) > len(identity_bundle.frame_chain):
        cw = dict(confidence_protocol.get("compile_warnings") or {})
        cw["frame_chain_incomplete"] = True
        confidence_protocol = {**confidence_protocol, "compile_warnings": cw}
    if assertions:
        validation = ValidationBlock(
            wait_for=validation.wait_for,
            success_conditions=validation.success_conditions,
            assertions=assertions,
        )
    snapshot = ev.get("snapshot") or {}
    # Conditional-state observation (Priority 2): carry the recorder's optional-interstitial hint
    # onto the step verbatim. It changes nothing in this per-step loop (see _build_assertions
    # above, which never reads it). Downstream, the second-opinion pass may convert the step into
    # a try_dismiss branch; any hint it leaves alone stays advisory until a human confirms it in
    # Human Edit (workflow_mutations.confirm_optional_interstitial).
    optional_hint = None
    if ev.get("optionality") == "stochastic" and isinstance(ev.get("branch_hint"), dict):
        optional_hint = ev["branch_hint"]
    step = SkillStep(
        action=action_payload,
        intent=intent,
        frame=_build_frame_context(ev),
        tab=_build_tab_context(ev),
        target=target,
        identity_bundle=identity_bundle,
        handler_hints=handler_hints,
        signals=signals,
        state={"before": state_before, "after": state_after},
        value=value,
        input_binding=input_binding,
        validation=validation,
        recovery=recovery,
        confidence_protocol=confidence_protocol,
        decision_policy=DecisionPolicy(),
        snapshot_ref=str(snapshot.get("ref") or ""),
        snapshot_dom_hash=str(snapshot.get("dom_hash") or ""),
        optional_hint=optional_hint,
        consequence=consequence,
        entity_binding=entity_binding,
    )
    _compile_log(
        "compile_step",
        f"Compiled step {step_index + 1}.",
        {
            "phase": "step_done",
            "step_index": step_index,
            "action": action_payload,
            "intent": step.intent,
            "selector": (step.target or {}).get("primary_selector"),
            "selector_confidence": (step.target or {}).get("selector_confidence"),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return step


def _build_compile_report(
    steps: list[SkillStep], *, degraded: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Aggregate per-step selector confidence + LLM router stats into a single report.

    Status:
    - "ok" if all confidence >= 0.90 and no warnings, and nothing degraded
    - "review_needed" if any confidence 0.75–0.89, or an optional pass genuinely failed
      (see `degraded`) — a reviewer should look, but nothing here blocks the compile
    - "failed" if any confidence < 0.75

    `degraded`: entries for an optional, non-primary pass (second opinion, vision-anchor
    prefetch, …) that failed outright rather than being cleanly disabled or finding
    nothing — each `{"pass": str, "reason": str, "detail": str}`. This is metadata about
    the report only; it never changes a compiled step's target/selectors/identity_bundle,
    so the Key Invariant that the LLM never writes selector strings on the primary path is
    unaffected. Its only effect is that a genuine failure is no longer indistinguishable
    from a disabled pass in the report a reviewer sees.
    """
    step_reports: list[dict[str, Any]] = []
    min_confidence = 1.0
    has_warnings = False

    for i, step in enumerate(steps):
        target = step.target or {}
        selector = str(target.get("primary_selector") or "")

        if not recovery_enabled_for_action(step.action):
            # Steps like scroll/navigate/wait/frame_enter/frame_exit have no element to select —
            # there's nothing here to score. Without this, an empty target reads as
            # selector_confidence=0.0 and both drags min_confidence to 0% and raises a spurious
            # "low_selector_confidence"/"empty_selector" warning on a step that was never
            # supposed to have a selector in the first place.
            step_reports.append({
                "index": i,
                "intent": step.intent,
                "selector": selector,
                "confidence": None,
                "source": str(target.get("selector_source") or "heuristic"),
                "reasoning": "",
                "input_binding": step.input_binding,
                "warnings": [],
            })
            continue

        confidence = float(target.get("selector_confidence") or 0.0)
        source = str(target.get("selector_source") or "heuristic")
        rationale = str(target.get("selector_rationale") or "")

        warnings: list[dict[str, str]] = []
        if confidence < 0.50:
            warnings.append({
                "code": "low_selector_confidence",
                "message": f"Selector confidence {confidence:.2f} is below 0.50 — runtime may need to fall back to vision recovery",
            })
            has_warnings = True

        if not selector:
            warnings.append({
                "code": "empty_selector",
                "message": "No selector could be generated — step may fail at runtime",
            })
            has_warnings = True

        if confidence < min_confidence:
            min_confidence = confidence

        step_reports.append({
            "index": i,
            "intent": step.intent,
            "selector": selector,
            "confidence": round(confidence, 3),
            "source": source,
            "reasoning": rationale,
            "input_binding": step.input_binding,
            "warnings": warnings,
        })

    degraded_list = list(degraded or [])

    if min_confidence >= 0.90 and not has_warnings and not degraded_list:
        status = "ok"
    elif min_confidence >= 0.75:
        status = "review_needed"
    else:
        status = "failed"

    from conxa_core.llm import get_router
    router = get_router()
    if hasattr(router, "pool"):
        if not router.pool:
            raise RuntimeError(
                "LLM router has no providers configured. Compile cannot produce a "
                "trustworthy report without LLM verification. Set at least one "
                "*_API_KEYS + *_ENABLED=true in .env."
            )
        router_stats = router.stats()
    else:
        # Proxy client (Build Studio) — no local provider pool; stats not applicable.
        router_stats = {"provider": "cloud_proxy"}

    report: dict[str, Any] = {
        "status": status,
        "steps_total": len(steps),
        "steps_with_warnings": sum(1 for sr in step_reports if sr["warnings"]),
        "min_confidence": round(min_confidence, 3),
        "steps": step_reports,
        "llm_router_stats": router_stats,
    }
    if degraded_list:
        report["degraded"] = degraded_list
    return report


def _deduplicate_input_bindings(steps: list[SkillStep]) -> None:
    """Ensure no two type/keyboard_shortcut steps share the same input_binding name.

    If two steps both resolve to {{name}}, the second becomes {{name_2}}, third {{name_3}}, etc.
    Keyboard shortcut steps (with input_binding=None) are left alone.
    """
    seen_counts: dict[str, int] = {}
    for step in steps:
        binding = step.input_binding
        if not binding:
            continue
        if binding not in seen_counts:
            seen_counts[binding] = 1
            continue
        seen_counts[binding] += 1
        new_binding = f"{binding}_{seen_counts[binding]}"
        step.input_binding = new_binding
        # Rewrite the {{binding}} token wherever it appears in the value, not only when the
        # value is exactly "{{binding}}" — see rewrite_placeholder, shared with the
        # second-opinion applier so the two renaming paths cannot drift.
        step.value = rewrite_placeholder(step.value, binding, new_binding)


def compile_skill_package(
    events: list[dict[str, Any]],
    *,
    skill_id: str,
    source_session_id: str | None,
    title: str,
    version: int,
    policy_bundle: PolicyBundle | None = None,
) -> SkillPackage:
    """Build a package from already pipeline-normalized event dicts."""
    from conxa_compile.llm.anchor_vision_llm import VisionAnchorGenerationError

    bundle = policy_bundle or get_policy_bundle()
    pol = bundle.data
    for e in events:
        RecordedEvent.model_validate(e)
    sid = str(source_session_id or "").strip()
    if not sid:
        raise VisionAnchorGenerationError("source_session_id_required")
    session_root = (settings.data_dir / "sessions" / sid).resolve()
    _compile_log(
        "compile_phase",
        "Preparing compiler inputs.",
        {"phase": "compiler_prepare", "event_count": len(events), "session_id": sid},
    )
    cleaned_events = fix_step_order(clean_steps(events, pol), pol)
    # A navigation an action caused (click Submit -> redirect to /orders/847291) is that action's
    # effect, not a step: drop it and keep only a generalized post-condition on the action.
    # Before _insert_tab_markers so the deliberate navigate it adds for a user-opened tab (and
    # _insert_start_navigate_step's start URL) are never candidates.
    cleaned_events = fold_action_caused_navigations(cleaned_events)
    # Insert tab_open/tab_switch markers at tab-boundary crossings — after clean_steps/
    # fix_step_order so the synthetic marker events never have to satisfy those functions'
    # assumptions about real recorded event shape.
    cleaned_events = _insert_tab_markers(cleaned_events)
    _compile_log(
        "compile_phase",
        "Compiler inputs prepared.",
        {"phase": "compiler_prepare_done", "cleaned_event_count": len(cleaned_events)},
    )
    # The workflow-intent LLM call used to run here, ahead of the step loop, to
    # seed every step's intent token before vision-anchor selection and
    # _build_step ran. It's now merged with the second-opinion call (BUILD-26,
    # see llm/workflow_review.py) and runs once, after every step below is
    # fully compiled, so it can see the compiled context and each step's
    # chosen anchor image. Vision-anchor selection and _build_step fall back to
    # the recorder's own heuristic intent (normalize_compiler_intent's
    # content-derived path) instead of an LLM token — see workflow_review.py's
    # module docstring for the quality trade this accepts.
    page_urls = sorted({(ev.get("page") or {}).get("url") or "" for ev in cleaned_events} - {""})
    prefetch_degraded = _prefetch_vision_anchors(cleaned_events, session_root=session_root, policy=pol)
    steps = [
        _build_step(e, bundle, session_root=session_root, step_index=i)
        for i, e in enumerate(cleaned_events)
    ]
    # Must build this while steps[i] still maps 1:1 onto cleaned_events[i] (the
    # leading-navigate insert below prepends, which shifts positions) — the
    # per-step screenshot the vision-anchor stage chose lives on the event
    # (ev["visual"]), not on the compiled SkillStep. Keyed by step_key so the
    # merged review call, which runs after every reshuffling pass below, can
    # still look an image up for any step that survived unchanged; a step a
    # collapse pass merged or reordered just loses its image and gets text
    # only, same as any step with no vision anchor.
    image_by_step_key = _build_image_by_step_key(steps, cleaned_events, session_root=session_root, policy=pol)
    _log_vision_anchor_fallback_summary(steps)

    # Phase 7: populate hover_chain handler hints from hover-then-act sequences. Must run before
    # _insert_start_navigate_step below: it indexes steps[i] against cleaned_events[i], and the
    # leading navigate that function prepends has no matching cleaned_event.
    _populate_hover_chains(steps, cleaned_events, session_id=sid)
    # Checkbox-group collapse: must run here too, while steps[i] still maps 1:1 onto
    # cleaned_events[i] — same alignment requirement as _populate_hover_chains above. Collapses a
    # "pick all that apply" run of individual set_checkbox toggles into one multi-valued step;
    # see conxa_compile/compiler/choice.py. Runs BEFORE the date-picker collapse below and hands
    # it a synced events view rather than cleaned_events directly — collapse_date_picker_runs
    # also assumes 1:1 alignment with whatever `steps` it's given, which this pass may have
    # already shrunk.
    steps, date_pass_events = collapse_choice_group_runs(steps, cleaned_events, pol)
    # Custom date-picker collapse: same alignment requirement, against date_pass_events (see
    # above) rather than cleaned_events. Collapses (never regresses) an open->nav->day-cell click
    # run into one parameterized date_pick step per bridge.js's date_context tagging; see
    # conxa_compile/compiler/date_picker.py.
    # Typed-text dates ("25/09/2026" into a plain field) become date_pick steps that remember the
    # field's format. Before the collapse (same 1:1 alignment, and it never changes the count);
    # an ambiguous day/month order falls back to the recording browser's own locale sample.
    environment = _read_environment_sidecar(session_root)
    convert_typed_date_steps(steps, date_pass_events, pol, str(environment.get("date_format_sample") or ""))
    steps = collapse_date_picker_runs(steps, date_pass_events, pol)
    steps = _insert_start_navigate_step(steps, cleaned_events)

    # The workflow-level intent graph was already built before the step loop —
    # it is the single source of per-step intent tokens and review prose, so no
    # further LLM work happens here.

    _deduplicate_input_bindings(steps)
    apply_bindings_to_compiled_steps(steps, cleaned_events)
    # PROD-3: a literal entity-binding identifier detected per-step (build.py's per-step loop
    # above) can now be upgraded to a run-input reference — this needs the full, deduplicated
    # set of declared inputs across the workflow, which only exists after the two calls above.
    upgrade_entity_binding_identifiers(steps, collect_input_literal_values(cleaned_events, pol))

    _compile_log(
        "compile_phase",
        "Building compile confidence report.",
        {"phase": "compile_report_start", "step_count": len(steps)},
    )
    compile_report = _build_compile_report(steps, degraded=prefetch_degraded)
    _compile_log(
        "compile_phase",
        "Compile confidence report finished.",
        {
            "phase": "compile_report_done",
            "status": compile_report.get("status"),
            "min_confidence": compile_report.get("min_confidence"),
            "steps_with_warnings": compile_report.get("steps_with_warnings"),
        },
    )

    # BUILD-25: the compiler's second opinion — one whole-workflow LLM call that
    # decides the four things no per-step rule can decide correctly, and writes
    # them onto the steps. Sited after bindings are deduplicated (naming needs
    # them to exist) and after the report itself (routing needs per-step
    # confidence). Disabled/failed/empty all leave the SkillPackage below
    # byte-identical to a compile that never ran this pass; a pass that finds
    # something deliberately does not — Human Review is the gate.
    visited_hosts = _extract_visited_hosts(cleaned_events)
    _compile_log(
        "compile_phase",
        "Asking for a second opinion on the whole workflow.",
        {"phase": "second_opinion_start", "step_count": len(steps)},
    )
    steps, compile_report, intent_graph = _apply_second_opinion(
        compile_report,
        steps,
        skill_id=skill_id,
        page_urls=page_urls,
        visited_hosts=visited_hosts,
        image_by_step_key=image_by_step_key,
    )
    _compile_log(
        "compile_phase",
        "Second opinion finished.",
        {
            "phase": "second_opinion_done",
            "applied": len(compile_report.get("second_opinion") or []),
        },
    )

    # PROD-3-DRYRUN layer 4: advisory stage-then-commit lint — never blocks compile/save/publish.
    commit_ordering_warnings = lint_commit_ordering(steps)
    if commit_ordering_warnings:
        compile_report["commit_ordering_warnings"] = commit_ordering_warnings

    # A one-click "generalize this to a loop" suggestion for a single-file download->upload
    # workflow — a pure pattern match over facts already computed above (no LLM), run against
    # the FINAL steps (second opinion above may have renamed bindings or archived steps, both of
    # which would shift step_keys). Advisory only; a reviewer already-rejected suggestion is
    # filtered out later by skill_id-aware code in handlers/compile.py, same layering
    # filter_runtime_only_inputs already uses there.
    loop_suggestions = detect_download_upload_loop_candidates(steps)
    _rewrite_loop_suggestions_for_review(loop_suggestions, steps)
    compile_report["for_each_suggestions"] = loop_suggestions

    now = datetime.now(timezone.utc).isoformat()
    structural_fp = _build_structural_fingerprint(steps)
    meta = SkillMeta(
        id=skill_id,
        version=version,
        title=title or skill_id,
        created_at=now,
        source_session_id=source_session_id,
        compiler_policy_version=bundle.version,
        compiler_policy_hash=bundle.content_hash,
        structural_fingerprint=structural_fp,
        environment=environment,
        visited_hosts=visited_hosts,
    )
    return SkillPackage(
        meta=meta,
        inputs=[],
        skills=[SkillBlock(name="recorded", steps=steps)],
        policies=SkillPolicies(),
        llm={
            "max_calls_per_step": settings.llm_max_calls_per_step,
            "timeout_ms": settings.llm_pack_timeout_ms,
        },
        intent_graph=intent_graph,
        compile_report=compile_report,
    )


def _build_image_by_step_key(
    steps: list[SkillStep],
    cleaned_events: list[dict[str, Any]],
    *,
    session_root: Path,
    policy: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Re-derive the vision-anchor stage's already-chosen frame for each step,
    keyed by step_key, for the merged workflow-review call (BUILD-26). Must run
    while steps[i] still maps 1:1 onto cleaned_events[i] — see the call site.

    Recomputes the same `intent` _build_step/_prefetch_vision_anchors used
    (normalize_compiler_intent with no LLM token, since no graph_intent_token
    exists at this point in the pipeline) so get_chosen_frame_image's cache
    lookup hits without re-running the vision LLM. A step with no cached
    choice (vision anchors disabled/failed, or a scroll/marker/navigate step
    that never requested one) is simply absent from the returned map — the
    review call just sends text for it, same as any step with no anchor."""
    from conxa_compile.llm.anchor_vision_llm import get_chosen_frame_image

    keys = step_keys(steps)
    out: dict[str, tuple[str, str]] = {}
    for i, ev in enumerate(cleaned_events):
        action_payload = optimize_scroll(ev)
        if action_payload in ("scroll", "manual_navigate") or action_payload in MARKER_ACTIONS:
            continue
        intent = normalize_compiler_intent(ev, "", policy)
        try:
            chosen = get_chosen_frame_image(
                ev, session_root=session_root, final_intent=intent, policy=policy, step_index=i
            )
        except Exception:  # noqa: BLE001 — best-effort, matches get_chosen_frame_image's own contract
            chosen = None
        if chosen:
            out[keys[i]] = chosen
    return out


def _apply_workflow_review_to_steps(steps: list[SkillStep], graph: WorkflowIntentGraph) -> int:
    """Write the merged review call's per-step prose onto the already-compiled,
    already-collapsed `steps` list, and backfill `step.intent` from
    `intent_token` wherever the recorder's own heuristic left it blank.

    Unlike the old pre-loop intent graph (indexed by *event* position, applied
    before any collapse/reorder pass touched `steps`), this call runs after
    every collapse/insert pass has already run — see the call site in
    _apply_second_opinion. `graph.steps[i].index` is therefore just this call's
    own request-payload position (0..len(steps)-1 at request time), which still
    lines up 1:1 against `steps[i]` because nothing reorders `steps` between
    building that request and applying this response.

    Returns the number of steps whose `intent` was backfilled, so the caller
    knows whether the compile report (built before this ran) needs rebuilding."""
    prose_by_index = {s.index: s for s in graph.steps if s.intent}
    backfilled = 0
    for i, step in enumerate(steps):
        entry = prose_by_index.get(i)
        prose = entry.intent if entry else None
        if prose:
            step.semantic_description = prose
        elif step.intent and not step.semantic_description:
            step.semantic_description = step.intent
        if entry and entry.intent_token and not step.intent:
            step.intent = entry.intent_token
            backfilled += 1
    return backfilled


# BUILD-29: images are already individually bounded (anchor_vision_llm.py downscales every
# frame to ~1024px JPEG before it's ever cached), so this only has to bound the *count* on a
# long workflow — router.py treats an oversized request as a 400/413 with no retry/failover,
# which would silently reproduce the exact no-op this budget exists to prevent.
# ponytail: a flat byte budget across all attached base64 images, not provider-aware; raise if
# a real provider's multipart limit turns out smaller/larger than this guess.
_REVIEW_IMAGE_BUDGET_BYTES = 6_000_000


def _trim_review_images(items: list[dict[str, Any]]) -> None:
    """In place: drop image_base64/image_mime from the highest-confidence steps first until
    the total base64 size is under budget. A low_confidence step's picture survives longest —
    that's the step the second opinion most needs a picture for."""
    with_images = [it for it in items if it.get("image_base64")]
    total = sum(len(it["image_base64"]) for it in with_images)
    if total <= _REVIEW_IMAGE_BUDGET_BYTES:
        return
    with_images.sort(key=lambda it: it.get("low_confidence") is True)  # high-confidence first
    for it in with_images:
        if total <= _REVIEW_IMAGE_BUDGET_BYTES:
            break
        total -= len(it["image_base64"])
        it.pop("image_base64", None)
        it.pop("image_mime", None)


def _step_action_name(step: SkillStep) -> str | None:
    action = step.action
    return action.get("action") if isinstance(action, dict) else action


def _next_effect_cause(steps: list[SkillStep], i: int) -> str | None:
    """What the step at index i's click sets in motion, looking past pure
    navigation markers (tab_switch, frame_enter/exit) to the first real thing
    that follows it — a download, or a file-chooser upload. A click that only
    starts one of these is EXPECTED to leave post_condition_effect at "none"
    (the page itself doesn't change), which is exactly the signal flag_noise
    otherwise reads as "did nothing" — see compiler/second_opinion.py and
    llm/workflow_semantics.py, which both refuse to flag a step this
    identifies. Purely structural: no LLM, no new per-step signal, just a scan
    over the step list this function already has."""
    for j in range(i + 1, len(steps)):
        name = _step_action_name(steps[j])
        if name in {"tab_switch", "frame_enter", "frame_exit"}:
            continue
        if name == "download_observed":
            return "file_download"
        if name in {"upload", "upload_intent"}:
            return "file_chooser"
        return None
    return None


def _review_inputs(
    steps: list[SkillStep],
    compile_report: dict[str, Any],
    image_by_step_key: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Compact per-step payload for the merged workflow-review call (BUILD-25's
    second opinion + BUILD-26's intent-graph merge) — action, text, url,
    binding, and (when the vision-anchor stage chose one) the step's target
    screenshot. No DOM. Keyed on step_key (never step_index) so a finding, or
    the goal/intent graph's own per-step entry, survives the reorders/inserts
    that already happened by the time this call runs."""
    keys = step_keys(steps)
    confidence_by_index = {
        sr.get("index"): sr.get("confidence")
        for sr in (compile_report.get("steps") or [])
        if isinstance(sr, dict)
    }
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        action_name = _step_action_name(step)
        confidence = confidence_by_index.get(i)
        value = step.value if isinstance(step.value, str) else None
        item: dict[str, Any] = {
            "key": keys[i],
            "action": action_name,
            "target_text": (step.identity_bundle.fingerprint.inner_text or "")[:120],
            "url": step.url,
            # Compared by flag_noise's navigate path to confirm two navigates are truly the
            # same replay location, not just the same URL string on different tabs.
            "tab_id": str((step.tab or {}).get("id") or ""),
            "intent": step.intent,
            "input_binding": step.input_binding,
            "value": value,
            "has_optional_hint": bool(step.optional_hint),
            "low_confidence": bool(confidence is not None and confidence < 0.75),
            # BUILD-25 stage d — recorder-observed evidence for flag_noise, and
            # a required-assertion check so it never flags a step something
            # downstream already relies on.
            "post_condition_effect": str(step.signals.get("post_condition_effect") or ""),
            "has_required_assertion": any(a.required for a in step.validation.assertions),
        }
        causes = _next_effect_cause(steps, i)
        if causes:
            item["causes"] = causes
        image = image_by_step_key.get(keys[i])
        if image:
            item["image_base64"], item["image_mime"] = image
        out.append(item)
    _trim_review_images(out)
    return out


# ponytail: full-document reads over the 20 most recent local skills, per
# compile — fine at Build Studio's single-vendor local scale; index binding
# names by host if this ever needs to scale past that.
_SIBLING_LOOKUP_LIMIT = 20


def _sibling_bindings(visited_hosts: list[str], exclude_skill_id: str) -> dict[str, list[str]]:
    """Binding names other local workflows for the same host(s) already use —
    turns naming from a guess into a lookup (BUILD-25 stage b2). Returns {}
    when this workflow visits no hosts (nothing to match against)."""
    if not visited_hosts:
        return {}
    from conxa_core.storage.json_store import list_skill_summaries, read_skill  # noqa: PLC0415

    host_set = set(visited_hosts)
    out: dict[str, set[str]] = {}
    for summary in list_skill_summaries()[:_SIBLING_LOOKUP_LIMIT]:
        sid = str(summary.get("skill_id") or "")
        if not sid or sid == exclude_skill_id:
            continue
        doc = read_skill(sid)
        if not doc:
            continue
        sib_hosts = set((doc.get("meta") or {}).get("visited_hosts") or []) & host_set
        if not sib_hosts:
            continue
        sib_steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        names = {
            str(s.get("input_binding") or "").strip()
            for s in sib_steps
            if isinstance(s, dict) and s.get("input_binding")
        }
        if not names:
            continue
        for host in sib_hosts:
            out.setdefault(host, set()).update(names)
    return {host: sorted(names) for host, names in out.items()}


def _apply_second_opinion(
    compile_report: dict[str, Any],
    steps: list[SkillStep],
    *,
    skill_id: str,
    page_urls: list[str],
    visited_hosts: list[str],
    image_by_step_key: dict[str, tuple[str, str]],
) -> tuple[list[SkillStep], dict[str, Any], WorkflowIntentGraph]:
    """Run the merged workflow-review call (BUILD-25's second opinion merged
    with the workflow-intent graph, BUILD-26 — see llm/workflow_review.py) and
    write what it finds onto `steps`. Returns (steps, compile_report, graph):
    steps/compile_report are the ones passed in when nothing was applied,
    archived, or backfilled, freshly rebuilt/filtered ones otherwise, since a
    suggest_optional conversion, a flag_noise archive, or an intent backfill
    changes what the report handed in here was built against. `graph` is
    always returned (WorkflowIntentGraph() on any failure) — the caller needs
    its `goal` for SkillPackage.intent_graph regardless of whether any
    second-opinion finding applied.

    Applied field-rewrites are recorded under compile_report["second_opinion"];
    archived (flag_noise) steps are recorded in full under
    compile_report["archived_steps"] and removed from the returned `steps` —
    see compiler/second_opinion.py::archive_flagged_steps for why that's an
    archive, not a delete. Nothing in Human Edit reads either key yet: the
    field-rewrites are simply part of the compiled workflow now,
    indistinguishable from what the fixed rules produced, and a reviewer edits
    a wrong one the same way they edit any other compiler output.

    Never raises: any exception degrades the STEPS to the rules-only compile,
    exactly like a disabled pass or a drained provider pool — the Key Invariant
    that the LLM never writes selector strings on the primary path holds either
    way. What differs now: a genuine failure (an exception here, or an empty
    result with a populated error_detail — build_workflow_review's own contract
    is to never raise, so this is the signal a real call failed rather than
    finding nothing to say) is recorded into compile_report["degraded"], which
    _build_compile_report folds into `status`. A disabled pass or one that
    legitimately found nothing still reports clean."""
    from conxa_compile.llm.workflow_review import build_workflow_review  # noqa: PLC0415

    error_detail: list[str] = []
    try:
        graph, findings = build_workflow_review(
            _review_inputs(steps, compile_report, image_by_step_key),
            page_urls=page_urls,
            sibling_bindings=_sibling_bindings(visited_hosts, skill_id),
            error_detail=error_detail,
        )
    except Exception as exc:  # noqa: BLE001 — a second, non-primary LLM pass must never fail compile
        graph, findings = WorkflowIntentGraph(), []
        error_detail.append(f"{type(exc).__name__}: {exc}")

    degraded = list(compile_report.get("degraded") or [])
    if error_detail and not findings and not graph.goal:
        degraded.append({
            "pass": "second_opinion",
            "reason": "llm_call_failed",
            "detail": "; ".join(error_detail),
        })

    # Always applied, even when the call above failed — mirrors the old
    # pre-loop intent graph, which called _apply_intent_graph_to_steps
    # unconditionally, outside whatever try/except guarded its own LLM call.
    # Keeping this outside the try below (unlike the findings application,
    # which depends on `findings` existing) is what keeps a disabled and a
    # failed compile's STEPS byte-identical: both end up applying an equally-
    # empty graph instead of one applying it and the other skipping it. Only
    # the report's `degraded`/`status` fields now tell the two apart.
    backfilled = _apply_workflow_review_to_steps(steps, graph)

    try:
        noise_findings = [f for f in findings if f.get("kind") == "flag_noise"]
        rewrite_findings = [f for f in findings if f.get("kind") != "flag_noise"]
        applied = apply_second_opinion(steps, rewrite_findings)
        steps, archived = archive_flagged_steps(steps, noise_findings)
    except Exception as exc:  # noqa: BLE001 — a second, non-primary LLM pass must never fail compile
        applied, archived = [], []
        degraded.append({
            "pass": "second_opinion_apply",
            "reason": "exception",
            "detail": f"{type(exc).__name__}: {exc}",
        })
    if not applied and not archived and not backfilled and not degraded:
        return steps, compile_report, graph
    # Rebuilt, not patched: _build_compile_report is a pure local function over
    # `steps`, so re-running it is cheaper than reasoning about which of its
    # aggregates a conversion/archive/backfill invalidated.
    fresh = _build_compile_report(steps, degraded=degraded)
    if applied:
        fresh["second_opinion"] = applied
    if archived:
        fresh["archived_steps"] = archived
    return steps, fresh, graph


def _rewrite_loop_suggestions_for_review(suggestions: list[dict[str, Any]], steps: list[Any]) -> None:
    """Best-effort in-place rewrite of each suggestion's `why` into plain, friendly language
    for the mandatory Human Edit review popup (ReviewQuestionsDialog.tsx) — see
    llm/loop_suggestion_copy.py. Copy-only: never touches `wrap_start_key`/`wrap_end_key`/
    `upload_step_key`/`template_literal`/`redundant_click_key`, so a failed or skipped call
    changes nothing but the sentence a reviewer reads; the deterministic `why` already there
    is the fallback, not a placeholder that needs a working provider to be usable. Doesn't
    touch compile_report["degraded"]/status — unlike second_opinion, a failure here is a
    worse sentence, never a worse compile.

    Passes the LLM the actual steps involved (role-tagged: acquiring the file vs. uploading
    it, each with its own compile-time intent/semantic_description, never a selector, via
    `compiler/step_description.py::describe_step`) rather than a bare filename + boolean —
    the earlier bare-facts version of this prompt had the model guessing at what the workflow
    even does and inventing a wrong mechanism (e.g. describing a delete when the pattern is
    download-then-upload). The key→step lookup (`step_key_index_map`) and the description
    shape (`describe_step`) are both generic, reusable by any future step that needs to hand
    an LLM copy-rewrite real grounding — this function is just the loop-suggestion-specific
    glue: which steps play which role.

    Deliberately excludes the redundant click (when present) from this context: whether to
    remove it is a SEPARATE Yes/No question (`redundant_click_why`, built deterministically in
    `compiler/loop_suggestion.py` — no LLM rewrite, it's one factual sentence) that
    ReviewQuestionsDialog.tsx asks as its own popup, only after this loop question is answered
    Yes. Rewriting the loop's own `why` has no reason to mention it at all."""
    from conxa_compile.compiler.step_description import describe_step  # noqa: PLC0415
    from conxa_compile.llm.loop_suggestion_copy import friendly_loop_suggestion_text  # noqa: PLC0415

    key_to_index = step_key_index_map(steps)

    for suggestion in suggestions:
        error_detail: list[str] = []
        try:
            steps_context: list[dict[str, Any]] = []
            redundant_key = str(suggestion.get("redundant_click_key") or "")
            wrap_start_idx = key_to_index.get(str(suggestion.get("wrap_start_key") or ""))
            wrap_end_idx = key_to_index.get(str(suggestion.get("wrap_end_key") or ""))
            if wrap_start_idx is not None and wrap_end_idx is not None:
                for idx in range(wrap_start_idx, wrap_end_idx + 1):
                    if key_to_index.get(redundant_key) == idx:
                        continue  # the redundant click, if absorbed into the wrap range — its
                        # own separate question, not part of this one's context (see above)
                    steps_context.append(describe_step(steps[idx], role="acquire"))
            upload_idx = key_to_index.get(str(suggestion.get("upload_step_key") or ""))
            if upload_idx is not None:
                steps_context.append(describe_step(steps[upload_idx], role="upload"))

            suggestion["why"] = friendly_loop_suggestion_text(
                str(suggestion.get("template_literal") or ""),
                steps_context=steps_context,
                fallback=str(suggestion.get("why") or ""),
                error_detail=error_detail,
            )
        except Exception as exc:  # noqa: BLE001 — advisory copy only, never fail compile over this
            error_detail.append(f"{type(exc).__name__}: {exc}")
        if error_detail:
            _compile_log(
                "compile_phase",
                "Loop suggestion copy rewrite failed — kept the deterministic wording.",
                {"phase": "loop_suggestion_copy_failed", "detail": "; ".join(error_detail)},
            )
