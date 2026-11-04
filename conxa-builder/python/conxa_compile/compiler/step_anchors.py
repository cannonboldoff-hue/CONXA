"""Anchor candidate selection, step cleanup/ordering, and selector ranking.

Split out of the former compiler/v3.py (a versioned filename with no v1/v2
siblings — retired). This half of that module builds semantic anchor
candidates, cleans/reorders/sanitizes a step list, ranks and finalizes
selector/vision-anchor choices.
"""

from __future__ import annotations

import re
from typing import Any

from conxa_compile.compiler.action_semantics import action_name, is_editable_target, looks_like_submit
from conxa_compile.compiler.selector_filters import is_dynamic_id, selector_passes_filters
from conxa_compile.compiler.selector_score import ordered_selector_strings, rank_labeled_selector_candidates
from conxa_compile.editor.action_registry import MARKER_ACTIONS
from conxa_compile.policy.bundle import get_policy_bundle

Step = dict[str, Any]

# Every action that commits a field's value in one gesture. A recorded click/focus immediately
# preceding one of these on the SAME target is prep noise, not a separate user intent — mirrors
# bridge.js's own _VALUE_SET_ACTIONS list (kept in sync by hand; bridge.js additionally includes
# "fill", which the recorder itself never emits but a hand-authored/legacy event might).
_VALUE_SET_ACTIONS = frozenset({
    "type", "fill", "select", "select_option", "set_checkbox", "set_radio", "date_pick",
    "upload", "upload_intent",
})


def _generic_anchors(policy: dict[str, Any] | None = None) -> set[str]:
    data = policy or get_policy_bundle().data
    sec = data.get("anchors") if isinstance(data.get("anchors"), dict) else {}
    raw = sec.get("generic_skip") or ["header", "h1", "h2", "h3", "title", "page"]
    return {str(x).strip().lower() for x in raw}


def _anchors_section(policy: dict[str, Any] | None) -> dict[str, Any]:
    pol = policy or get_policy_bundle().data
    sec = pol.get("anchors")
    return sec if isinstance(sec, dict) else {}


def _semantic_anchor_config(policy: dict[str, Any] | None) -> dict[str, Any]:
    sec = _anchors_section(policy)
    raw = sec.get("semantic_anchors")
    return raw if isinstance(raw, dict) else {}


def _default_phrase_stopwords() -> set[str]:
    return {
        "submit",
        "cancel",
        "close",
        "ok",
        "next",
        "continue",
        "button",
        "input",
        "click",
        "loading",
        "save",
        "reset",
        "apply",
        "search",
    }


def _phrase_is_discriminative(phrase: str, sem_cfg: dict[str, Any], generic: set[str]) -> bool:
    p = " ".join(str(phrase).lower().split()).strip()
    if len(p) < int(sem_cfg.get("min_phrase_len", 3)):
        return False
    if p in generic:
        return False
    stops = _default_phrase_stopwords() | {str(x).lower() for x in (sem_cfg.get("phrase_stopwords") or []) if x}
    if p in stops:
        return False
    min_chars = int(sem_cfg.get("min_unlisted_phrase_chars", 6))
    if len(p) >= min_chars:
        return True
    if " " in p and len(p.replace(" ", "")) >= int(sem_cfg.get("min_multiword_body_chars", 4)):
        return True
    if any(ch in p for ch in "#.[]:@/"):
        return True
    return False


def semantic_context_anchor_candidates(
    context: dict[str, Any],
    semantic: dict[str, Any] | None,
    target: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Short, deterministic relational phrases from DOM context + target (not generic buckets)."""
    sem_cfg = _semantic_anchor_config(policy)
    if not bool(sem_cfg.get("enabled", True)):
        return []
    pol = policy or get_policy_bundle().data
    generic = _generic_anchors(pol)
    max_len = int(sem_cfg.get("max_phrase_len", 56))
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(element: str, relation: str) -> None:
        el = " ".join(str(element).lower().split()).strip()[:max_len]
        rel = str(relation or "near").strip().lower()
        if rel not in {"inside", "above", "below", "near"}:
            rel = "near"
        if not _phrase_is_discriminative(el, sem_cfg, generic):
            return
        key = (el, rel)
        if key in seen:
            return
        seen.add(key)
        out.append({"element": el, "relation": rel})

    ctx = context if isinstance(context, dict) else {}
    tgt = target if isinstance(target, dict) else {}
    sem = semantic if isinstance(semantic, dict) else {}

    parent = str(ctx.get("parent") or "").strip()
    if parent:
        _add(parent, "inside")

    fc = str(ctx.get("form_context") or "").strip()
    if fc and fc.lower() != parent.lower():
        _add(fc, "near")

    for key in ("aria_label", "placeholder", "name"):
        raw = str(tgt.get(key) or "").strip()
        if raw:
            _add(raw, "near")

    inner = str(tgt.get("inner_text") or "").strip()
    if inner:
        _add(inner[:max_len], "near")

    norm = str(sem.get("normalized_text") or "").strip()
    if norm and norm.lower() != inner.lower():
        _add(norm[:max_len], "near")

    sib_max = int(sem_cfg.get("max_sibling_anchor_candidates", 2))
    for sib in (ctx.get("siblings") or [])[: max(0, sib_max)]:
        sx = str(sib).strip()
        if sx and len(sx) <= max_len + 20:
            _add(sx[:max_len], "near")

    return out


def semantic_anchor_phrase_kept(element: str, policy: dict[str, Any] | None = None) -> bool:
    """True when element is an allowlisted unlisted semantic phrase (for recovery / policy filters)."""
    pol = policy or get_policy_bundle().data
    sem_cfg = _semantic_anchor_config(pol)
    if not bool(sem_cfg.get("allow_unlisted_phrases", True)):
        return False
    return _phrase_is_discriminative(str(element or ""), sem_cfg, _generic_anchors(pol))


def _frame_signature(step: Step) -> str:
    """Stable per-frame identity so steps in different iframes never share a target key.

    HubSpot-style micro-frontends routinely embed multiple iframes whose fields carry
    identical (often empty) name/id/aria/css signals — without this, _target_key()
    treats "the first empty <input>" in every frame as the same target, and the merge
    logic below silently collapses distinct steps recorded in different iframes.
    """
    frame = step.get("frame") or {}
    chain = frame.get("chain") if isinstance(frame, dict) else None
    if not isinstance(chain, list):
        return ""
    return ">".join(
        str(level.get("url_pattern") or level.get("url") or "")
        for level in chain
        if isinstance(level, dict)
    )


def _tab_signature(step: Step) -> str:
    """Stable per-tab identity — the multi-tab twin of _frame_signature. Two steps recorded on
    different tabs must never share a target key even if every DOM signal happens to match
    (e.g. two tabs on the same site with identical layout)."""
    tab = step.get("tab") or {}
    return str(tab.get("id") or "tab_0") if isinstance(tab, dict) else "tab_0"


def _target_key(step: Step) -> str:
    target = step.get("target") or {}
    selectors = step.get("selectors") or {}
    return "|".join(
        [
            _tab_signature(step),
            _frame_signature(step),
            str(selectors.get("aria") or ""),
            str(selectors.get("text_based") or ""),
            str(selectors.get("css") or ""),
            str(target.get("name") or ""),
            str(target.get("id") or ""),
            str(target.get("tag") or ""),
            str(target.get("placeholder") or ""),
        ]
    )


def _event_value(step: Step) -> str:
    action = step.get("action") or {}
    if isinstance(action, dict):
        return str(action.get("value") or "")
    return ""


def _normalize_prep_click_to_focus(step: Step, policy: dict[str, Any]) -> Step:
    """Clicks on editable fields that are not submit behave like focus acquisition (optional via policy)."""
    wf = policy.get("workflow") if isinstance(policy.get("workflow"), dict) else {}
    if not bool(wf.get("normalize_editable_click_to_focus", True)):
        return step
    if action_name(step).lower() != "click":
        return step
    if looks_like_submit(step, policy):
        return step
    if not is_editable_target(step):
        return step
    out = dict(step)
    ap = dict(out.get("action") or {})
    ap["action"] = "focus"
    out["action"] = ap
    return out


def _navigation_detected(prev_submit: Step, current: Step) -> bool:
    prev_url = str((prev_submit.get("page") or {}).get("url") or "")
    current_url = str((current.get("page") or {}).get("url") or "")
    if prev_url and current_url and prev_url != current_url:
        return True
    state_change = current.get("state_change") or {}
    before = str(state_change.get("before") or "")
    after = str(state_change.get("after") or "")
    return bool(before and after and before != after)


def _truncate_after_terminal_submit(policy: dict[str, Any]) -> bool:
    wf = policy.get("workflow") if isinstance(policy.get("workflow"), dict) else {}
    return bool(wf.get("truncate_after_terminal_submit", False))


def clean_steps(steps: list[Step], policy: dict[str, Any] | None = None) -> list[Step]:
    """Strict step cleanup for recorded workflows."""
    pol = policy or get_policy_bundle().data
    if not steps:
        return []
    cleaned: list[Step | None] = []
    last_type_index: dict[str, int] = {}
    pending_submit: Step | None = None
    use_submit_truncate = _truncate_after_terminal_submit(pol)
    for raw in steps:
        step = dict(raw)
        step = _normalize_prep_click_to_focus(step, pol)
        action = action_name(step)
        key = _target_key(step)

        # Optional legacy: drop anything after terminal submit unless navigation signal appears.
        if use_submit_truncate and pending_submit is not None:
            if _navigation_detected(pending_submit, step):
                pending_submit = None
            else:
                continue

        # Remove duplicate consecutive action.
        if cleaned and cleaned[-1] is not None:
            prev = cleaned[-1]
            prev_action = action_name(prev or {})
            prev_key = _target_key(prev or {})
            if action == prev_action and key == prev_key and prev is not None:
                # Navigation actions carry no element key (empty target), so the generic
                # same-action-same-key test above matches ANY two consecutive ones. Two real
                # consecutive Backs — or two address-bar edits to different URLs — must never
                # collapse into one replayed step.
                # Markers (dialog_accept/dialog_dismiss, download_observed, tab_open, ...) carry
                # no element target, so `key` is the same empty value for every one of them —
                # two REAL, distinct marker events (e.g. confirm's answer followed by prompt's)
                # would otherwise always look like one duplicated action and lose the second.
                # Unlike browser_back/forward/manual_navigate above, a marker's `value` also
                # differs event to event, so there is no same-key-different-content case this
                # would wrongly preserve.
                if action in {"browser_back", "browser_forward", "manual_navigate"} or action in MARKER_ACTIONS:
                    cleaned.append(step)
                    continue
                if action == "type":
                    prev_payload = dict(prev.get("action") or {})
                    curr_payload = dict(step.get("action") or {})
                    if curr_payload.get("value") not in {None, ""}:
                        prev_payload["value"] = curr_payload.get("value")
                        prev["action"] = prev_payload
                continue

        # Merge noisy click/focus + the field's real value-setting action. An upload_intent
        # (or an already-normalized upload) on a file input supersedes the picker-invoking click
        # exactly the way `type` supersedes a text field's prep click — replay must never see
        # that click, since clicking a file input reopens an OS dialog nothing can drive. Same
        # reasoning for select/set_checkbox/set_radio/date_pick: the prep click/focus that opens a
        # dropdown or lands on a radio option is not a distinct user intent once the committing
        # "change" event exists — without this widening (originally `{"type", "upload",
        # "upload_intent"}` only), a recorded radio pick compiled into two steps: a phantom
        # `focus`/`click` PLUS the real `set_radio`.
        #
        # Uploads look back past intervening steps: Drive (and similar) programmatically
        # activate a hidden <input type=file> when the user clicks a menu item, so the
        # recorder emits click(file), click(menu), upload_intent(file). The file click is
        # still prep noise even though it is not cleaned[-1]. Other value-set actions stay
        # immediate-predecessor only — looking through a later click on a different control
        # would drop a real open-the-dropdown gesture.
        if action in {"upload", "upload_intent"}:
            for i in range(len(cleaned) - 1, -1, -1):
                prev = cleaned[i]
                if (
                    prev is not None
                    and action_name(prev) in {"click", "focus"}
                    and _target_key(prev) == key
                ):
                    cleaned.pop(i)
                    for k, idx in list(last_type_index.items()):
                        if idx > i:
                            last_type_index[k] = idx - 1
                        elif idx == i:
                            last_type_index.pop(k, None)
        elif action in _VALUE_SET_ACTIONS and cleaned and cleaned[-1] is not None:
            prev = cleaned[-1]
            if prev is not None and action_name(prev) in {"click", "focus"} and _target_key(prev) == key:
                cleaned.pop()

        # Later type on same field: merge value into the earlier step (preserve chronology).
        if action == "type" and key in last_type_index:
            old_idx = last_type_index[key]
            if 0 <= old_idx < len(cleaned) and cleaned[old_idx] is not None:
                old_step = cleaned[old_idx]
                if action_name(old_step) == "type" and _target_key(old_step) == key:
                    old_payload = dict(old_step.get("action") or {})
                    new_payload = dict(step.get("action") or {})
                    if new_payload.get("value") not in {None, ""}:
                        old_payload["value"] = new_payload.get("value")
                    old_step["action"] = old_payload
                    last_type_index[key] = old_idx
                    continue

        cleaned.append(step)
        if action == "type":
            last_type_index[key] = len(cleaned) - 1

        if use_submit_truncate and action == "click" and looks_like_submit(step, pol):
            pending_submit = step

    cleaned = [s for s in cleaned if s is not None]
    # Drop redundant post-entry clicks on the same editable target (focus already implied by typing).
    deduped: list[Step] = []
    for step in cleaned:
        prev_action = action_name(deduped[-1]).lower() if deduped else ""
        cur_action = action_name(step).lower()
        if deduped and cur_action in {"click", "focus"} and not looks_like_submit(step, pol):
            prev = deduped[-1]
            if (
                is_editable_target(step)
                and prev_action in _VALUE_SET_ACTIONS
                and _target_key(prev) == _target_key(step)
            ):
                continue
        deduped.append(step)
    cleaned = deduped

    # Optional legacy: remove trailing type steps after terminal submit if no navigation happened.
    if use_submit_truncate and pending_submit is not None:
        out: list[Step] = []
        for s in cleaned:
            out.append(s)
            if s is pending_submit:
                break
        return out
    return cleaned


def _synthetic_focus_before_type(type_step: Step) -> Step:
    """Minimal focus acquisition for the same target as a type (recording order preserved)."""
    out = dict(type_step)
    ap = dict(out.get("action") or {})
    ap["action"] = "focus"
    ap.pop("value", None)
    out["action"] = ap
    return out


def _same_target_focus_prereq(prev: Step | None, step: Step, policy: dict[str, Any]) -> bool:
    """True when the previous step already focuses/clicks the same field (non-submit)."""
    if prev is None:
        return False
    if _target_key(prev) != _target_key(step):
        return False
    pa = action_name(prev).lower()
    if pa == "focus":
        return True
    if pa == "click" and not looks_like_submit(prev, policy):
        return True
    return False


def sanitize_steps_preserving_order(steps: list[Step], policy: dict[str, Any] | None = None) -> list[Step]:
    """Keep compile order aligned with recording; insert focus only when type lacks same-target prep."""
    if not steps:
        return []
    pol = policy or get_policy_bundle().data
    wf = pol.get("workflow") if isinstance(pol.get("workflow"), dict) else {}
    insert_focus = bool(wf.get("synthetic_focus_before_type", True))
    out: list[Step] = []
    for step in steps:
        st = dict(step)
        action = action_name(st).lower()
        if insert_focus and action == "type" and is_editable_target(st):
            prev = out[-1] if out else None
            if not _same_target_focus_prereq(prev, st, pol):
                out.append(_synthetic_focus_before_type(st))
        out.append(st)
    return out


def fix_step_order(steps: list[Step], policy: dict[str, Any] | None = None) -> list[Step]:
    """Preserve recorded step order; only minimal focus-before-type inserts (no cross-field reorder)."""
    return sanitize_steps_preserving_order(steps, policy)


def rank_selectors(selectors: dict[str, Any], policy: dict[str, Any] | None = None) -> list[str]:
    pol = policy or get_policy_bundle().data
    return ordered_selector_strings(
        {
            "aria": selectors.get("aria"),
            "name": selectors.get("name"),
            "text_based": selectors.get("text_based"),
            "role": selectors.get("role"),
            "css": selectors.get("css"),
            "xpath": selectors.get("xpath"),
        },
        pol,
    )


def generate_stable_selector(element: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build stable selector set: policy-scored candidates (not a fixed name-before-css order)."""
    pol = policy or get_policy_bundle().data
    selectors = element.get("selectors") or {}
    target = element.get("target") or {}

    name = str(target.get("name") or "").strip()
    input_type = str((element.get("semantic") or {}).get("input_type") or target.get("type") or "").strip().lower()
    aria_label = str(target.get("aria_label") or "").strip()
    placeholder = str(target.get("placeholder") or "").strip()
    label_text = str(target.get("label_text") or "").strip()
    text_based = str(selectors.get("text_based") or "").strip()
    css = str(selectors.get("css") or "").strip()
    raw_id = str(target.get("id") or "").strip()
    id_selector = f"#{raw_id}" if raw_id else ""
    tag_hint = str(target.get("tag") or "input").strip() or "input"

    def _escape_attr(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    rows: list[tuple[str, str]] = []
    if name:
        rows.append(("name", f'input[name="{_escape_attr(name)}"]'))
    if aria_label:
        rows.append(("aria", f'[aria-label="{_escape_attr(aria_label)}"]'))
    if label_text:
        escaped_label = _escape_attr(label_text)
        rows.append(("label", f'label:has-text("{escaped_label}") + {tag_hint}'))
        rows.append(("label", f'label:has-text("{escaped_label}") ~ {tag_hint}'))
    if placeholder:
        rows.append(("css", f'[placeholder="{_escape_attr(placeholder)}"]'))
    if input_type:
        rows.append(("css", f'input[type="{_escape_attr(input_type)}"]'))
    if text_based and selector_passes_filters(text_based):
        rows.append(("text_based", text_based))
    if css and not is_dynamic_id(css) and selector_passes_filters(css):
        rows.append(("css", css))
    if id_selector and not is_dynamic_id(id_selector) and selector_passes_filters(id_selector):
        rows.append(("css", id_selector))

    unique = rank_labeled_selector_candidates(rows, pol)

    if not unique:
        fallback_name = str(target.get("tag") or "input").strip() or "input"
        unique = [fallback_name]
    return {
        "primary_selector": unique[0],
        "fallback_selectors": unique[1:],
    }


def _vision_anchor_config(policy: dict[str, Any] | None) -> dict[str, Any]:
    pol = policy or get_policy_bundle().data
    sec = pol.get("anchors") if isinstance(pol.get("anchors"), dict) else {}
    raw = sec.get("vision")
    return raw if isinstance(raw, dict) else {}


_REL_ALLOWED_VISION_SECONDARY = frozenset({"inside", "above", "below", "near"})


def finalize_vision_anchors(
    primary_phrase: str,
    secondary_raw: list[dict[str, Any]] | None,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Post-process Vision LLM output only: sanitize, dedupe, cap count. No DOM imports."""
    pol = policy or get_policy_bundle().data
    cfg = _vision_anchor_config(pol)
    max_total = max(1, min(8, int(cfg.get("max_total_anchors", 4))))
    max_len = int(cfg.get("max_phrase_len", 96))
    min_chars = max(1, int(cfg.get("min_primary_chars", 4)))
    generic_extra = [str(x).strip().lower() for x in (cfg.get("cleanup_generic_terms") or []) if str(x).strip()]
    generic = _generic_anchors(pol) | set(generic_extra)

    def _scrub_phrase(text: str) -> str:
        t = " ".join(str(text).lower().split()).strip()[:max_len]
        if not t:
            return ""
        parts = []
        for w in t.replace(":", " ").split():
            wl = w.strip().lower()
            if wl in generic or not wl:
                continue
            parts.append(wl)
        return " ".join(parts).strip()[:max_len]

    primary = _scrub_phrase(primary_phrase)
    if len(primary) < min_chars:
        return []

    out: list[dict[str, Any]] = [{"element": primary, "relation": "target"}]
    seen: set[tuple[str, str]] = {(primary, "target")}

    for raw in secondary_raw or []:
        if not isinstance(raw, dict):
            continue
        if len(out) >= max_total:
            break
        el = _scrub_phrase(str(raw.get("element") or ""))
        rel = str(raw.get("relation") or "near").strip().lower()
        if rel not in _REL_ALLOWED_VISION_SECONDARY:
            rel = "near"
        if not el or len(el) < min_chars:
            continue
        if el == primary:
            continue
        key = (el, rel)
        if key in seen:
            continue
        seen.add(key)
        out.append({"element": el, "relation": rel})

    return out[:max_total]


_KNOWN_FRAME_LABELS = ("before_far", "before_near", "at", "after_near", "after_far")


def finalize_vision_frameset(
    chosen_frame: str | None,
    anchor_sentence: str | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Post-process anchor_vision_frameset LLM output: validate chosen_frame,
    scrub/cap the sentence, and derive short keyword phrases from it for
    consumers that need short strings (recovery.json's short-anchor list).
    No DOM imports — pure string processing.
    """
    pol = policy or get_policy_bundle().data
    cfg = _vision_anchor_config(pol)
    max_sentence_len = int(cfg.get("max_sentence_len", 220))
    min_chars = max(1, int(cfg.get("min_primary_chars", 4)))

    frame = str(chosen_frame or "").strip()
    if frame not in _KNOWN_FRAME_LABELS:
        frame = "before_near"  # safe default: the deterministic pre-action frame

    sentence = " ".join(str(anchor_sentence or "").split()).strip()[:max_sentence_len]
    if len(sentence) < min_chars:
        return {"chosen_frame": frame, "anchor_sentence": "", "anchor_phrases": []}

    return {"chosen_frame": frame, "anchor_sentence": sentence, "anchor_phrases": _extract_short_phrases(sentence, cfg, pol)}


def _extract_short_phrases(sentence: str, cfg: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Deterministic phrase extraction: split the sentence on stopwords/punctuation,
    keep the surviving word-runs (up to 4 words) as short candidate anchors. Reuses
    finalize_vision_anchors's generic-term vocabulary so the two paths never drift
    on what counts as "generic"."""
    max_total = max(1, min(8, int(cfg.get("max_total_anchors", 4))))
    max_len = int(cfg.get("max_phrase_len", 96))
    generic_extra = [str(x).strip().lower() for x in (cfg.get("cleanup_generic_terms") or []) if str(x).strip()]
    generic = _generic_anchors(policy) | set(generic_extra) | _default_phrase_stopwords()

    words = re.findall(r"[a-z0-9][a-z0-9'-]*", sentence.lower())
    runs: list[list[str]] = []
    current: list[str] = []
    for w in words:
        if w in generic or len(w) < 2:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(w)
        if len(current) == 4:  # cap run length so phrases stay short/discriminative
            runs.append(current)
            current = []
    if current:
        runs.append(current)

    seen: set[str] = set()
    out: list[str] = []
    for run in runs:
        phrase = " ".join(run)[:max_len].strip()
        if not phrase or phrase in seen:
            continue
        seen.add(phrase)
        out.append(phrase)
        if len(out) >= max_total:
            break
    return out


def clean_anchors(
    anchors: list[dict[str, Any]],
    context: dict[str, Any],
    policy: dict[str, Any] | None = None,
    *,
    target: dict[str, Any] | None = None,
    semantic: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    pol = policy or get_policy_bundle().data
    sec = _anchors_section(pol)
    allowed = {str(x).strip().lower() for x in (sec.get("allowed_elements") or [])}
    generic = _generic_anchors(pol)
    sem_cfg = _semantic_anchor_config(pol)
    allow_unlisted = bool(sem_cfg.get("allow_unlisted_phrases", True))
    allow_sibling_fallback = bool(sem_cfg.get("allow_sibling_input_fallback", True))
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _push(element: str, relation: str, *, bypass_allowlist: bool = False) -> None:
        el = str(element or "").strip().lower()
        rel = str(relation or "inside").strip().lower()
        if rel not in {"inside", "above", "below", "near", "target"}:
            rel = "inside"
        if not el or el in generic:
            return
        if not bypass_allowlist:
            if allowed and el not in allowed:
                return
        elif allow_unlisted:
            if allowed and el not in allowed and not _phrase_is_discriminative(el, sem_cfg, generic):
                return
        else:
            if allowed and el not in allowed:
                return
        key = (el, rel)
        if key in seen:
            return
        seen.add(key)
        out.append({"element": el, "relation": rel})

    for raw in anchors or []:
        if (raw or {}).get("ephemeral"):
            continue
        _push((raw or {}).get("element") or "", (raw or {}).get("relation") or "inside")

    tgt = target if isinstance(target, dict) else {}
    sem = semantic if isinstance(semantic, dict) else {}
    for cand in semantic_context_anchor_candidates(context, sem, tgt, pol):
        _push(str(cand.get("element") or ""), str(cand.get("relation") or "near"), bypass_allowlist=True)

    if not out:
        parent = str(context.get("parent") or "").strip().lower()
        siblings = " ".join(str(s).strip().lower() for s in (context.get("siblings") or []))
        blob = f"{parent} {siblings}".strip()
        if blob:
            for element in sorted(allowed):
                if element in {"input", "sibling_input"}:
                    continue
                if element in blob:
                    _push(element, "inside")
            if any(k in blob for k in ("label", "placeholder", "for=")):
                _push("label", "near")

    if not out and context.get("siblings") and allow_sibling_fallback:
        _push("sibling_input", "near")
    return out


