"""Scroll optimization, DOM state snapshots/diffs, and validation-from-diff.

Split out of the former compiler/v3.py (a versioned filename with no v1/v2
siblings — retired). This half of that module compares before/after page
state to infer validation blocks and wait conditions.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from conxa_compile.compiler.action_semantics import action_name
from conxa_compile.compiler.decision_layer import infer_compiled_validation
from conxa_compile.compiler.validation_planner import infer_wait_for_shape
from conxa_compile.compiler.wait_for_shape import is_wait_group, leaf_wait_type
from conxa_compile.policy.bundle import get_policy_bundle

Step = dict[str, Any]


def optimize_scroll(step: Step) -> dict[str, Any] | str:
    if action_name(step) != "scroll":
        return action_name(step)
    return "scroll"


def scroll_payload(step: Step, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    if action_name(step) != "scroll":
        return {}
    pol = policy or get_policy_bundle().data
    sd = pol.get("scroll_defaults") if isinstance(pol.get("scroll_defaults"), dict) else {}
    extras = step.get("extras") if isinstance(step.get("extras"), dict) else {}
    try:
        delta = int(extras.get("scroll_amount")) if extras.get("scroll_amount") is not None else int(sd.get("delta", 150))
    except (TypeError, ValueError):
        delta = int(sd.get("delta", 150))
    return {
        "action": "scroll",
        "delta": delta,
    }


def _fingerprint(state: dict[str, Any]) -> str:
    payload = {
        "url": state.get("url") or "",
        "title": state.get("page_title") or "",
        "elements": state.get("visible_key_elements") or [],
        "texts": state.get("important_text_blocks") or [],
    }
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def capture_state_snapshot(step: Step, *, before: bool) -> dict[str, Any]:
    page = step.get("page") or {}
    target = step.get("target") or {}
    context = step.get("context") or {}
    selectors = step.get("selectors") or {}
    visible = [
        str(selectors.get("aria") or ""),
        str(selectors.get("text_based") or ""),
        str(selectors.get("css") or ""),
        str(target.get("tag") or ""),
    ] + [str(s) for s in (context.get("siblings") or [])[:6]]
    target_text = str(target.get("inner_text") or "").strip()
    if action_name(step) == "scroll":
        target_text = ""
    url = str(page.get("url") or "")
    if not before:
        # The recorded page is where the action STARTED; where it landed lives on the post-condition.
        url_delta = (step.get("post_condition") or {}).get("url_delta") or {}
        url = str(url_delta.get("after") or url)
    state = {
        "url": url,
        "page_title": str(page.get("title") or ""),
        "visible_key_elements": [x for x in visible if x],
        # `state_change.before/after` (bridge.js's pageFingerprint(), format
        # "url|title|hash") used to be folded in here too — it's a compact identity
        # hash, not real page text, and leaked straight into text_present assertions
        # ("the text 'https://.../|Render Dashboard|391b9b69' appears on the page")
        # whenever the hash segment changed between before/after.
        "important_text_blocks": [x for x in [target_text[:240]] if x][:4],
    }
    state["dom_fingerprint"] = _fingerprint(state)
    return state


def _evidence_strength(new_elements: list, removed_elements: list, text_change: list) -> float:
    return min(1.0, (len(new_elements) + len(removed_elements) + len(text_change)) / 20.0)


def compare_state(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b_elements = set(before.get("visible_key_elements") or [])
    a_elements = set(after.get("visible_key_elements") or [])
    b_text = set(before.get("important_text_blocks") or [])
    a_text = set(after.get("important_text_blocks") or [])
    n_new = sorted(a_elements - b_elements)
    n_rem = sorted(b_elements - a_elements)
    n_txt = sorted(a_text - b_text)
    return {
        "url_changed": str(before.get("url") or "") != str(after.get("url") or ""),
        "dom_changed": str(before.get("dom_fingerprint") or "") != str(after.get("dom_fingerprint") or ""),
        "new_elements": n_new,
        "removed_elements": n_rem,
        "text_change": n_txt,
        "evidence_strength": _evidence_strength(n_new, n_rem, n_txt),
    }


def document_after(events: list[Step], i: int) -> str:
    """Whether the page document of step ``i`` was still the one there afterwards: "same", "left" or "" (can't tell).

    Read from the next step the person took on the same tab, outside any frame: each page document carries its own id
    (bridge.js DOC_ID), so the same id means the step stayed on that document (a route change inside one page, saucedemo's
    Finish) and another id means it left it, whatever the address says. A saved page copy's id cannot tell: the recorder
    replaces a copy taken after the page was left with the one the page sent as it was left (session.py). A navigation
    the person made themselves in between ends the search (Back can bring the same document back)."""
    from conxa_compile.compiler.navigation_effects import _navigation_value

    ev = events[i]
    doc = str(ev.get("doc_id") or "")
    if not doc or (ev.get("frame") or {}).get("chain"):
        return ""
    tab = str((ev.get("tab") or {}).get("id") or "")
    for later in events[i + 1:]:
        if str((later.get("tab") or {}).get("id") or "") != tab:
            continue
        if action_name(later) == "manual_navigate":
            if _navigation_value(later).get("cause") == "action":
                continue  # what a step did itself (folded into it at compile)
            return ""
        other = "" if (later.get("frame") or {}).get("chain") else str(later.get("doc_id") or "")
        if other:
            return "same" if other == doc else "left"
    return ""


def mark_document_changes(events: list[Step]) -> list[Step]:
    """Stamp each step with ``post_condition.document`` ("same" / "left") where the recording can tell (``document_after``),
    so a step's own evidence says whether its page diff is the page it left. Returns the same list."""
    for i, ev in enumerate(events):
        found = document_after(events, i)
        if found:
            ev["post_condition"] = {**(ev.get("post_condition") or {}), "document": found}
    return events


def _left_the_page(ev: Step) -> bool:
    """True when this step's page diff is the page it left: the recorder saw the next page arrive (``result_diff.next_page``),
    the next step ran on another document (``post_condition.document``), or, when that cannot be told, the address changed.
    A route change inside the same document is not leaving: the diff is the page after the step."""
    pc = ev.get("post_condition") or {}
    if pc.get("document") == "left" or (((ev.get("state_change") or {}).get("result_diff")) or {}).get("next_page"):
        return True
    if pc.get("document") == "same":
        return False
    ud = pc.get("url_delta") or {}
    before, after = str(ud.get("before") or "").split("#", 1)[0], str(ud.get("after") or "").split("#", 1)[0]
    return bool(before and after and before != after)


def merge_dom_diff_evidence(ev: Step, state_diff: dict[str, Any]) -> dict[str, Any]:
    """Fold bridge.js's already-recorded page-wide before/after element diff into state_diff.

    `compare_state` above only compares the step's OWN recorded target/selectors/context — a
    single element's own descriptors, not the page-wide interactive-element scan the recorder
    already did (`bridge.js::_computeDomDiff`, stored as `state_change.dom_diff.added/removed`,
    lines like "tag|testid|aria_label|text"). That real, already-computed evidence was validated
    onto the model (Priority 1) but never consumed here — recompiling with it gives the
    text_present/selector_present picker something real to work with instead of falling straight
    to an intent-derived guess when the step's own before/after descriptors show no change.
    """
    sc = ev.get("state_change") or {}
    dom_diff = sc.get("dom_diff") or {}
    result_diff = sc.get("result_diff") or {}
    # A step that left the page was last seen on the page it left: what "appeared" there is that page's closing
    # moment (a search box's suggestions), never a result of the step. Nothing is derived from it. The one exception is
    # what the recorder saw on the page the step led to, once it settled (``next_page``: words the page left did not show).
    left = _left_the_page(ev)
    if left and not result_diff.get("next_page"):
        return state_diff
    added_lines = [] if left else [str(x) for x in (dom_diff.get("added") or [])][:20]
    # Words the page showed after the action (bridge.js resultSignature, never a control's label): the real results.
    # A message's own close button ("You logged out of the secure area! ×") is not part of its words (BUILD-117 (11)).
    result_words = [w for w in (str(x).strip().rstrip("×✕✖").rstrip() for x in (result_diff.get("added") or []) if isinstance(x, str))
                    if len(w) >= 3][:6]
    if not added_lines and not result_words:
        return state_diff

    extra_text: list[str] = list(result_words)
    control_text: list[str] = []
    extra_selectors: list[str] = []
    for line in added_lines:
        parts = line.split("|")
        if len(parts) < 4:
            continue
        testid, text = parts[1].strip(), parts[3].strip()
        # A form control's line carries its value, not words on the screen (an opacity slider's "100" was offered as
        # "You see the words “100”", WF-8 8.12); a page-text check can never see it.
        if parts[0].strip().lower() in ("input", "select", "textarea"):
            text = ""
        # W-E (owner decision 2026-11-21): a button or link that appears after a click appears whether or not the click
        # worked (an "Edit" beside a saved and an unsaved form alike), so it is never an "it is there" check that could
        # prove the click. Its label is still offered, unticked (control_text below).
        if testid and parts[0].strip().lower() not in ("button", "a"):
            extra_selectors.append(f'[data-testid="{testid}"]')
        if text and len(text) >= 3:
            extra_text.append(text)
            control_text.append(text)  # a control's label: weak proof of a result, offered but never pre-ticked

    def _dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    new_elements = _dedup(list(state_diff.get("new_elements") or []) + extra_selectors)[:24]
    text_change = _dedup(list(state_diff.get("text_change") or []) + extra_text)[:8]
    merged = dict(state_diff)
    merged["new_elements"] = new_elements
    merged["text_change"] = text_change
    merged["control_text"] = [t for t in _dedup(control_text) if t.lower() not in {w.lower() for w in result_words}]
    merged["evidence_strength"] = _evidence_strength(new_elements, state_diff.get("removed_elements") or [], text_change)
    return merged


def validation_from_diff(
    action: str,
    intent: str,
    state_diff: dict[str, Any],
    timeout: int,
    *,
    page_url: str = "",
    source_step: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pol = policy or get_policy_bundle().data
    validation_step = source_step or {
        "action": {"action": action},
        "semantic": {"llm_intent": intent},
        "timing": {"timeout": timeout},
    }
    return infer_compiled_validation(validation_step, state_diff, page_url, pol)


def fix_validation(
    step: dict[str, Any],
    state_diff: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer wait shape from action + observed diff (event-driven)."""
    pol = policy or get_policy_bundle().data
    sd = state_diff if isinstance(state_diff, dict) else {}
    return infer_wait_for_shape(step, sd, pol)


def _placeholder_leaf_ok(node: dict[str, Any]) -> bool:
    t = leaf_wait_type(node)
    if t in {"", "none"}:
        return True
    return t in {"url_change", "element_appear", "element_disappear", "intent_outcome", "dom_change"}


def _placeholder_tree_ok(node: dict[str, Any]) -> bool:
    """Recursive AND/OR placeholder (real executors should replace with browser checks)."""
    if is_wait_group(node):
        op = str(node.get("op") or "").strip().lower()
        kids = [c for c in (node.get("conditions") or []) if isinstance(c, dict)]
        if not kids:
            return False
        if op == "and":
            return all(_placeholder_tree_ok(c) for c in kids)
        if op == "or":
            return any(_placeholder_tree_ok(c) for c in kids)
        return False
    return _placeholder_leaf_ok(node)


def wait_for_condition(step: dict[str, Any], timeout: int = 8000) -> bool:
    """Deterministic polling helper for execution-layer validators."""
    raw = ((step.get("validation") or {}).get("wait_for") or {})
    wf = dict(raw) if isinstance(raw, dict) else {}
    if not wf:
        return True
    deadline = time.monotonic() + max(100, int(timeout)) / 1000.0
    while time.monotonic() < deadline:
        time.sleep(0.05)
        if _placeholder_tree_ok(wf):
            return True
    return False
