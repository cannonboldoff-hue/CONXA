"""Convert a saved/user-edited skill's steps into an executable workflow.

Split out of skill_package_builder.py: this is the "session -> JSON payload"
conversion pipeline (step shape, input placeholders, selector/recovery
repair) that turns a compiled skill's saved steps into the execution-ready
form written into the shared skill package's skills/{workflow_slug}/ output,
plus the login-step stripping applied before that conversion runs.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from conxa_compile.compiler.action_policy import RECOVERY_ACTION_TYPES
from conxa_compile.editor.action_registry import is_marker_action, is_supported_action, normalize_action_kind
from conxa_compile.editor.assets import resolve_skill_asset
from conxa_compile.llm.anchor_vision_llm import _apply_bbox_highlight
from conxa_core.sanitize import dumps_safe, scrub_text


# ─────────────────────────────────────────────────
# Login-step detection
# ─────────────────────────────────────────────────
_LOGIN_MARKERS = frozenset(
    {
        "login", "log in", "log-in", "signin", "sign in", "sign-in",
        "username", "password", "email", "forgot password", "remember me",
    }
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
from conxa_compile.editor.placeholder_grammar import PLACEHOLDER_RE as _PLACEHOLDER_NAME_RE
_TEXT_SELECTOR_RE = re.compile(r"^text=(?P<quote>['\"]?)(?P<text>.+?)(?P=quote)$")


def _to_bundle_slug(text: str) -> str:
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"p_{slug}"
    return slug[:40]


def _is_login_step(step: dict[str, Any]) -> bool:
    """Heuristic: step touches a login page or login-related element."""
    page_url = str((step.get("page") or {}).get("url") or "").lower()
    page_title = str((step.get("page") or {}).get("title") or "").lower()
    target_text = str((step.get("target") or {}).get("inner_text") or "").lower()
    semantic_text = str((step.get("semantic") or {}).get("normalized_text") or "").lower()
    aria = str((step.get("target") or {}).get("aria_label") or "").lower()

    haystack = " ".join([page_url, page_title, target_text, semantic_text, aria])
    return any(marker in haystack for marker in _LOGIN_MARKERS)


def strip_login_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove events that belong to a login sequence from workflow recordings."""
    if not events:
        return events

    has_login = any(_is_login_step(e) for e in events)
    if not has_login:
        return events

    login_urls: set[str] = set()
    for e in events:
        if _is_login_step(e):
            url = str((e.get("page") or {}).get("url") or "")
            if url:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    login_urls.add(f"{parsed.scheme}://{parsed.netloc}")
                    login_urls.add(url.split("?")[0])
                except Exception:
                    login_urls.add(url)

    if not login_urls:
        return events

    filtered = [e for e in events if not _is_login_step(e)]
    return filtered if filtered else events


# ─────────────────────────────────────────────────
# Session → JSON payload conversion
# ─────────────────────────────────────────────────

def _events_to_json_text(events: list[dict[str, Any]], title: str) -> str:
    return json.dumps({"title": title, "steps": events}, ensure_ascii=False)


def _step_action_name(step: dict[str, Any]) -> str:
    action = step.get("action") or {}
    if isinstance(action, dict):
        return str(action.get("action") or "").strip().lower()
    return str(action or "").strip().lower()


def _step_selector(step: dict[str, Any]) -> str:
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    selector = str(target.get("primary_selector") or "").strip()
    if selector:
        return selector
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    if isinstance(action, dict):
        selector = str(action.get("selector") or action.get("css_selector") or "").strip()
        if selector:
            return selector
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    selectors = signals.get("selectors") if isinstance(signals.get("selectors"), dict) else {}
    return str(selectors.get("css") or selectors.get("text_based") or selectors.get("aria") or "").strip()


def _step_url(step: dict[str, Any]) -> str:
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    if isinstance(action, dict):
        url = str(action.get("url") or "").strip()
        if url:
            return url
    url = str(step.get("url") or "").strip()
    if url:
        return url
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    context = signals.get("context") if isinstance(signals.get("context"), dict) else {}
    return str(context.get("page_url") or "").strip()


def _sanitize_runtime_frame(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    chain = raw.get("chain")
    if not isinstance(chain, list):
        return {}
    out_chain: list[dict[str, Any]] = []
    for item in chain:
        if not isinstance(item, dict):
            continue
        selector = str(item.get("selector") or "").strip()
        if not selector:
            continue
        fallbacks = [
            str(fb).strip()
            for fb in (item.get("fallback_selectors") or [])
            if str(fb or "").strip()
        ][:5]
        out_chain.append(
            {
                "selector": selector,
                "fallback_selectors": fallbacks,
                "url": str(item.get("url") or "").strip(),
                "url_pattern": str(item.get("url_pattern") or "").strip(),
            }
        )
    return {"chain": out_chain} if out_chain else {}


def _copy_frame(step: dict[str, Any], out: dict[str, Any]) -> None:
    frame = _sanitize_runtime_frame(step.get("frame"))
    if frame:
        out["frame"] = frame


def _sanitize_runtime_tab(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    tab_id = str(raw.get("id") or "").strip()
    # tab_0 keeps an explicit block too: a return-to-the-initial-tab tab_switch marker must
    # name its destination at replay time (runtime/tabs.js), not look like a recorder mis-stamp.
    # Absent id (pre-multi-tab packs) still sanitizes to {} = "the initial page" downstream.
    if not tab_id:
        return {}
    return {
        "id": tab_id,
        "index": raw.get("index", 0),
        "opened_by": str(raw.get("opened_by") or "initial"),
        "opener_tab": raw.get("opener_tab"),
    }


def _copy_tab(step: dict[str, Any], out: dict[str, Any]) -> None:
    tab = _sanitize_runtime_tab(step.get("tab"))
    if tab:
        out["tab"] = tab


def _step_action_value(step: dict[str, Any]) -> Any:
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    if isinstance(action, dict) and action.get("value") is not None:
        return action.get("value")
    return step.get("value")


def _action_value_text(step: dict[str, Any]) -> str:
    value = _step_action_value(step)
    if value is None:
        return ""
    return str(value)


def _action_value_json(step: dict[str, Any]) -> dict[str, Any]:
    value = _step_action_value(step)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _is_recorded_file_metadata(value: str) -> bool:
    from conxa_compile.compiler.upload_binding import is_recorded_file_metadata as _is_meta

    return _is_meta(value)


def _upload_recorded_filenames(step: dict[str, Any]) -> list[str]:
    """All filenames from a recorded upload step's ``File[]`` metadata, in picker order.

    Browsers expose only ``File.name``, never a full path, so this can never be used as an
    upload target directly — it's what lets a same-run download match itself against this
    step (see ``_bind_downloads_to_uploads``) and what seeds the runtime input's description.
    """
    raw = _action_value_text(step).strip()
    if not _is_recorded_file_metadata(raw):
        return []
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    names = []
    for item in parsed:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _upload_recorded_filename(step: dict[str, Any]) -> str:
    """First filename from a recorded upload step — documentation only. See
    ``_upload_recorded_filenames`` for the full (possibly multi-file) list."""
    names = _upload_recorded_filenames(step)
    return names[0] if names else ""


def _bind_downloads_to_uploads(export_steps: list[dict[str, Any]]) -> None:
    """Rewrite upload values to same-run download placeholders when filenames/provenance match."""
    from conxa_compile.compiler.upload_binding import apply_bindings_to_export_steps

    apply_bindings_to_export_steps(export_steps)


def _copy_saved_common(step: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    _copy_frame(step, out)
    _copy_tab(step, out)
    # Final Selector Architecture: the runtime resolves the primary target from
    # identity_bundle.signals (run.js:resolveStep). Carry it (and the other fields
    # the runtime reads) through to execution.json; without it every step falls into
    # recovery using the legacy CSS selector string, which may not match.
    for key in ("identity_bundle", "confidence", "handler_hints", "compiled_selectors"):
        val = step.get(key)
        if val:
            out[key] = val
    # Phase 8 post-action VERIFY (runtime/app/assertions.js:stepAssertions) reads
    # step.validation.assertions — this was silently never copied, so no published pack's
    # compiled post-condition assertions ever reached execution.json and VERIFY was a no-op
    # fleet-wide (it always saw "no-assertions" and passed). Only `assertions` is copied, not
    # the rest of ValidationBlock (wait_for/success_conditions): those are marked legacy
    # executor detail in the model and nothing in the runtime reads them.
    validation = step.get("validation")
    assertions = validation.get("assertions") if isinstance(validation, dict) else None
    if isinstance(assertions, list) and assertions:
        out["validation"] = {"assertions": assertions}
    # PROD-3: runtime/app/cascade.js reads step.destructive (top-level) directly, and
    # runtime/app/resolution.js reads step.entity_binding (top-level) directly — neither looks
    # inside identity_bundle, even though identity_bundle.destructive is the compiler's own
    # source of truth (build.py sets both from the same classify_consequence() call).
    bundle = step.get("identity_bundle")
    if isinstance(bundle, dict) and bundle.get("destructive"):
        out["destructive"] = True
    eb = step.get("entity_binding")
    if isinstance(eb, dict) and eb.get("container_selector") and eb.get("identifier"):
        out["entity_binding"] = eb
    # run_if: runtime/app/run.js::runIfDecision reads it off the execution step.
    run_if = step.get("run_if")
    if isinstance(run_if, dict) and run_if.get("input"):
        out["run_if"] = {k: run_if[k] for k in ("input", "op", "value") if k in run_if}
    # on_missing only means something on a step bound to a record (see resolution.js::entityRoots).
    if step.get("on_missing") == "skip" and "entity_binding" in out:
        out["on_missing"] = "skip"
    return out


def _saved_check_like_step(step: dict[str, Any], action: str) -> dict[str, Any] | None:
    kind = str(step.get("check_kind") or "url").strip().lower().replace("-", "_")
    out: dict[str, Any] = {"type": action, "kind": "url_exact" if kind in {"url_must_be", "exact_url"} else kind}
    if out["kind"] == "url":
        out["pattern"] = str(step.get("check_pattern") or _step_url(step) or "")
    elif out["kind"] == "url_exact":
        out["url"] = str(step.get("check_pattern") or _step_url(step) or "")
    elif out["kind"] == "selector":
        out["selector"] = str(step.get("check_selector") or _step_selector(step) or "")
    elif out["kind"] == "text":
        out["text"] = str(step.get("check_text") or "")
    elif out["kind"] == "snapshot":
        # No visual-diff engine exists at runtime (runtime/app/handlers.js's check/assert
        # evaluator has never implemented this kind) — drop rather than ship a step that can
        # only ever fail with "unsupported check type" at replay. Caller (the None-handling
        # chain in _build_workflow_from_saved_skill) turns this into a drop-and-warn.
        return None
    if not any(k in out and out[k] for k in ("pattern", "url", "selector", "text")):
        return None
    return _copy_saved_common(step, out)


def _dedup_str_list(*groups: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group if isinstance(group, list) else []:
            text = str(item or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def _saved_branch_steps_list(raw: Any) -> list[dict[str, Any]]:
    """Recursively serialize a branch's nested saved-step dicts into runtime step shape."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        serialized = _saved_step_to_execution_step(item)
        if serialized is not None:
            out.append(serialized)
    return out


def _saved_branch_step(step: dict[str, Any], action: str) -> dict[str, Any] | None:
    """EXEC-1 conditional/branch primitives — probe(s) + nested best-effort body.

    Reads SkillStep.branch (steps/candidates/options/timeout_ms/required) and recursively
    serializes any nested steps so a conditional survives the build into execution.json —
    the format runtime/run.js's if_present/try_dismiss/wait_for_one_of handlers consume.
    """
    branch = step.get("branch") if isinstance(step.get("branch"), dict) else {}
    timeout_ms: int | None
    try:
        timeout_ms = int(branch.get("timeout_ms")) if branch.get("timeout_ms") is not None else None
    except (TypeError, ValueError):
        timeout_ms = None

    if action == "if_present":
        selector = _step_selector(step)
        nested = _saved_branch_steps_list(branch.get("steps"))
        if not selector or not nested:
            return None
        out: dict[str, Any] = {"type": "if_present", "selector": selector, "steps": nested}
        if timeout_ms is not None:
            out["timeout_ms"] = timeout_ms
        return _copy_saved_common(step, out)

    if action == "try_dismiss":
        candidates = _dedup_str_list([_step_selector(step)], branch.get("candidates"))
        if not candidates:
            return None
        out = {"type": "try_dismiss", "candidates": candidates}
        if timeout_ms is not None:
            out["timeout_ms"] = timeout_ms
        if branch.get("fallback_escape") is False:
            out["fallback_escape"] = False
        return _copy_saved_common(step, out)

    if action == "wait_for_one_of":
        options: list[dict[str, Any]] = []
        for raw_option in branch.get("options") or []:
            if not isinstance(raw_option, dict):
                continue
            option_selector = str(raw_option.get("selector") or "").strip()
            if not option_selector:
                continue
            option_out: dict[str, Any] = {"selector": option_selector}
            nested = _saved_branch_steps_list(raw_option.get("steps"))
            if nested:
                option_out["steps"] = nested
            options.append(option_out)
        if not options:
            return None
        out = {"type": "wait_for_one_of", "options": options}
        if timeout_ms is not None:
            out["timeout_ms"] = timeout_ms
        if branch.get("required"):
            out["required"] = True
        return _copy_saved_common(step, out)

    return None


def _saved_for_each_step(step: dict[str, Any]) -> dict[str, Any] | None:
    """EXEC-38 iteration primitive — reads SkillStep.for_each (rows/as/max_iterations/
    on_row_error/steps) and recursively serializes the body the same way
    _saved_branch_step does, into the flat top-level shape run.js's for_each handler
    consumes. `max_iterations` is REQUIRED — the runtime refuses an uncapped loop at
    execute time, but there's no reason to ship a pack that can't possibly run; drop the
    step here instead (same "return None => step vanishes from execution.json" contract
    every other action uses for an unfillable step).

    Two mutually exclusive row sources: `rows.container_selector` (a live DOM scan) or
    `items` (a named runtime input the runtime splits on commas — see resolution.js's
    splitListInput). Exactly one must be set; patch_gate.py enforces this at edit time,
    this is just the same rule applied to whatever the document actually holds."""
    for_each = step.get("for_each") if isinstance(step.get("for_each"), dict) else {}
    rows = for_each.get("rows") if isinstance(for_each.get("rows"), dict) else {}
    container_selector = str(rows.get("container_selector") or "").strip()
    items = str(for_each.get("items") or "").strip()
    if bool(container_selector) == bool(items):
        return None  # neither source, or both — not a runnable loop
    nested = _saved_branch_steps_list(for_each.get("steps"))
    if not nested:
        return None
    try:
        max_iterations = int(for_each.get("max_iterations"))
    except (TypeError, ValueError):
        max_iterations = 0
    if max_iterations <= 0:
        return None
    out: dict[str, Any] = {
        "type": "for_each",
        "as": str(for_each.get("as") or "row").strip() or "row",
        "max_iterations": max_iterations,
        "steps": nested,
    }
    if items:
        out["items"] = items
    else:
        out["rows"] = {"container_selector": container_selector}
    if for_each.get("on_row_error") == "continue":
        out["on_row_error"] = "continue"
    return _copy_saved_common(step, out)


_FORK_POLICY_MODES = frozenset({"auto", "by_input", "ask_human", "stop"})


def _saved_fork_step(step: dict[str, Any]) -> dict[str, Any] | None:
    """Outcome-fork step -- reads SkillStep.outcome_fork and serializes every option body the way
    _saved_branch_step does, into the shape runtime/app/fork.js + run.js::runForkStep consume.

    Safety lives here as well as in the runtime: an option with no runnable steps is dropped (a
    fork must never offer a choice it cannot carry out), a policy naming a dropped option falls
    back to ask_human rather than silently doing something else, and a fork with no runnable
    option at all vanishes from execution.json (same "None => step is dropped" contract as every
    other action). The popup's message text is never copied -- only its title, button labels and
    value-free message tokens."""
    fork = step.get("outcome_fork") if isinstance(step.get("outcome_fork"), dict) else {}
    states_out: list[dict[str, Any]] = []
    for raw_state in fork.get("states") or []:
        if not isinstance(raw_state, dict) or not raw_state.get("id"):
            continue
        options_out: list[dict[str, Any]] = []
        for raw_opt in raw_state.get("options") or []:
            if not isinstance(raw_opt, dict) or not raw_opt.get("id"):
                continue
            body = _saved_branch_steps_list(raw_opt.get("steps"))
            if not body:
                continue
            options_out.append({
                "id": str(raw_opt["id"]),
                "label": str(raw_opt.get("label") or raw_opt["id"]),
                "role": str(raw_opt.get("role") or "neutral"),
                "steps": body,
            })
        if not options_out:
            continue
        option_ids = {o["id"] for o in options_out}
        policy = dict(raw_state.get("policy") or {})
        mode = str(policy.get("mode") or "ask_human")
        if mode not in _FORK_POLICY_MODES:
            mode = "ask_human"
        if mode == "auto" and policy.get("option_id") not in option_ids:
            mode = "ask_human"
        out_policy: dict[str, Any] = {"mode": mode}
        if mode == "auto":
            out_policy["option_id"] = policy["option_id"]
            out_policy["author_approved"] = bool(policy.get("author_approved"))
        elif mode == "by_input":
            out_policy["input"] = str(policy.get("input") or "")
            if policy.get("suggested") in option_ids:
                out_policy["suggested"] = policy["suggested"]
        elif mode == "ask_human":
            if policy.get("suggested") in option_ids:
                out_policy["suggested"] = policy["suggested"]
        else:
            out_policy["message"] = str(policy.get("message") or "")
        sig = raw_state.get("signature") if isinstance(raw_state.get("signature"), dict) else {}
        states_out.append({
            "id": str(raw_state["id"]),
            "kind": str(raw_state.get("kind") or "dialog"),
            "signature": {
                "title": str(sig.get("title") or ""),
                "button_labels": [str(x) for x in sig.get("button_labels") or []],
                "message_tokens": [str(x) for x in sig.get("message_tokens") or []],
                "container_selector": str(sig.get("container_selector") or ""),
            },
            "options": options_out,
            "policy": out_policy,
        })
    if not states_out:
        return None
    clean = fork.get("clean") if isinstance(fork.get("clean"), dict) else {}
    out: dict[str, Any] = {
        "type": "outcome_fork",
        "timeout_ms": int(fork.get("timeout_ms") or 4000),
        "unknown_policy": "stop" if fork.get("unknown_policy") == "stop" else "ask_human",
        "clean": {"observed": bool(clean.get("observed"))},
        "states": states_out,
    }
    return _copy_saved_common(step, out)


def _saved_step_to_execution_step(step: dict[str, Any]) -> dict[str, Any] | None:
    action = normalize_action_kind(_step_action_name(step))
    if not is_supported_action(action):
        return None

    if is_marker_action(action):
        out: dict[str, Any] = {"type": action, "recording_marker": True}
        value = _action_value_text(step)
        if value:
            out["value"] = value
        return _copy_saved_common(step, out)

    if action == "navigate":
        url = _step_url(step)
        if not url:
            return None
        return _copy_saved_common(step, {"type": "navigate", "url": url})

    if action in {"browser_back", "browser_forward"}:
        # History navigation replays as page.goBack()/page.goForward() on the step's tab —
        # the recorded post-navigation url is informational only (editor display), never
        # navigated to. Kept even when empty so the step itself is never dropped.
        return _copy_saved_common(step, {"type": action, "url": _step_url(step)})

    if action in {"click", "focus", "hover", "dblclick", "right_click"}:
        selector = _step_selector(step)
        if not selector:
            return None
        return _copy_saved_common(step, {"type": action, "selector": selector})

    if action == "extract":
        # Reads a value off the matched element into the output named by `value` (the editor keeps the
        # name there, like ai_review). No name or no target means there is nothing to read or report,
        # so the step is dropped with the same warning the unexportable cases give.
        selector = _step_selector(step)
        name = _action_value_text(step).strip()
        if not selector or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", name):
            return None
        spec = step.get("extract") if isinstance(step.get("extract"), dict) else {}
        read = {"source": str(spec.get("source") or "text")}
        for key in ("attribute", "pattern"):
            if spec.get(key):
                read[key] = str(spec[key])
        return _copy_saved_common(step, {"type": "extract", "selector": selector, "output_name": name, "extract": read})

    if action in {"type", "fill", "date_pick"}:
        selector = _step_selector(step)
        if not selector:
            return None
        return _copy_saved_common(step, {"type": action, "selector": selector, "value": _action_value_text(step)})

    if action in {"select", "select_option", "set_checkbox", "set_radio"}:
        selector = _step_selector(step)
        if not selector:
            return None
        out = {"type": action, "selector": selector}
        value = _action_value_text(step)
        if value:
            out["value"] = value
        return _copy_saved_common(step, out)

    if action == "scroll":
        action_block = step.get("action") if isinstance(step.get("action"), dict) else {}
        selector = _step_selector(step)
        out = {"type": "scroll"}
        if selector:
            out["selector"] = selector
        else:
            delta = action_block.get("delta") if isinstance(action_block, dict) else None
            if delta is None:
                delta = step.get("scroll_amount")
            out["delta_y"] = float(delta if delta is not None else 600)
        return _copy_saved_common(step, out)

    if action in {"check", "assert"}:
        return _saved_check_like_step(step, action)

    if action == "wait":
        action_block = step.get("action") if isinstance(step.get("action"), dict) else {}
        raw_ms = action_block.get("ms") if isinstance(action_block, dict) else None
        raw_ms = raw_ms if raw_ms is not None else _action_value_text(step)
        try:
            ms = int(raw_ms)
        except (TypeError, ValueError):
            # An unparseable duration means this step has no real wait time to run — drop
            # it (same "return None => step vanishes from execution.json" contract as
            # every other unfillable step, e.g. keyboard_shortcut below) rather than
            # inventing an arbitrary 1000ms that silently changes the automation's timing.
            return None
        return _copy_saved_common(step, {"type": "wait", "ms": max(ms, 0)})

    if action == "screenshot":
        return _copy_saved_common(step, {"type": "screenshot"})

    if action == "keyboard_shortcut":
        value = _action_value_text(step)
        if not value:
            return None
        return _copy_saved_common(step, {"type": "keyboard_shortcut", "value": value})

    if action == "drag_drop":
        payload = _action_value_json(step)
        selector = _step_selector(step)
        src = str(payload.get("src_selector") or payload.get("src_css") or "").strip()
        dst = str(payload.get("dst_selector") or payload.get("dst_css") or selector or "").strip()
        if not src or not dst:
            return None
        return _copy_saved_common(step, {"type": "drag_drop", "src_selector": src, "dst_selector": dst})

    if action in {"upload", "upload_intent"}:
        selector = _step_selector(step)
        # Uploads are always parameterised. A recording can only ever yield file metadata --
        # browsers never expose a full path -- so nothing the recorder captured is a valid
        # upload target on the machine that will replay the skill. The real path arrives as a
        # runtime input; {{file_path}} is auto-declared and gated by
        # _merge_saved_inputs_with_execution_placeholders below. A literal path or a custom
        # placeholder typed by hand in Human Edit is preserved as authored.
        value = _action_value_text(step).strip()
        if not value or _is_recorded_file_metadata(value):
            value = "{{file_path}}"
        out = {"type": "upload", "value": value}
        if selector:
            out["selector"] = selector
        return _copy_saved_common(step, out)

    if action == "ai_review":
        # EXEC-13: a planned pause, not a page action — no selector to resolve. Config lives in
        # top-level `ai_review_*` fields (the same shape check/assert's check_kind/check_pattern/
        # etc. use, per _saved_check_like_step above) rather than nested under `action` — the
        # saved-skill export normalizes `action` down to a plain string before this function
        # ever runs, so scroll/drag_drop's action-dict nesting isn't available here.
        # `reference_screenshot_ref` is author-set at edit time (there is no recorded screenshot
        # to auto-associate — ai_review is inserted during Human Edit, not captured during
        # recording), pointing at wherever the editor already saved it under `visuals/`.
        # `output_name` is omitted when blank; run.js falls back to `ai_review_output_<step
        # index>` at execution time using the same index space.
        prompt = str(step.get("ai_review_prompt") or "").strip()
        if not prompt:
            return None
        out: dict[str, Any] = {
            "type": "ai_review",
            "prompt": prompt,
            "on_failure": str(step.get("ai_review_on_failure") or "abort").strip().lower(),
        }
        output_schema = step.get("ai_review_output_schema")
        if isinstance(output_schema, dict) and output_schema:
            out["output_schema"] = output_schema
        reference_ref = str(step.get("ai_review_reference_screenshot_ref") or "").strip()
        if reference_ref:
            out["reference_screenshot_ref"] = reference_ref
        if out["on_failure"] == "use_default":
            out["default_value"] = step.get("ai_review_default_value")
        output_name = _action_value_text(step).strip()
        if output_name:
            out["output_name"] = output_name
        return _copy_saved_common(step, out)

    if action == "handover":
        # EXEC-21 (hand-over shape): a planned pause, no selector — the human sibling of
        # ai_review immediately above. The message shown to the person rides the generic value
        # field (see action_registry.py's VALUE_LABELS) rather than a dedicated
        # `handover_message` field; everything else lives in top-level `handover_*` fields, same
        # convention as ai_review's `ai_review_*` fields.
        message = _action_value_text(step).strip()
        if not message:
            return None
        out: dict[str, Any] = {
            "type": "handover",
            "message": message,
            # No use_default here (unlike ai_review) — a hand-over produces no answer value to
            # fall back to, only abort (default) or continue past it are meaningful.
            "on_failure": str(step.get("handover_on_failure") or "abort").strip().lower(),
        }
        resume_when = step.get("handover_resume_when")
        if isinstance(resume_when, dict) and resume_when:
            out["resume_when"] = resume_when
            timeout_ms = step.get("handover_resume_when_timeout_ms")
            if isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
                out["resume_when_timeout_ms"] = int(timeout_ms)
        return _copy_saved_common(step, out)

    if action in {"if_present", "try_dismiss", "wait_for_one_of"}:
        return _saved_branch_step(step, action)

    if action == "for_each":
        return _saved_for_each_step(step)

    if action == "outcome_fork":
        return _saved_fork_step(step)

    return None


def _normalize_saved_skill_inputs(inputs: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in inputs:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("id") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        row: dict[str, Any] = {
            "name": name,
            "type": "string",
            "description": str(raw.get("description") or raw.get("label") or f"Enter {name.replace('_', ' ')}"),
        }
        # "date" is an editor-level convenience type (see editor/dto.py SkillInputVariable); the
        # packaged input row's own `type` stays the JSON-Schema "string" every MCP client expects,
        # with `format` telling the agent what shape of string to send (ISO 8601 date, or
        # date+time when the recorded value carried a T-separated time component).
        if str(raw.get("type") or "") == "date":
            default_str = str(raw.get("default") or "")
            row["format"] = "date-time" if "T" in default_str else "date"
        if raw.get("sensitive"):
            row["sensitive"] = True
        if raw.get("optional"):
            row["optional"] = True
        options = raw.get("options")
        if isinstance(options, list) and options:
            option_strs = [str(item) for item in options if str(item)]
            if str(raw.get("type") or "") == "multiselect":
                # A checkbox-group answer is a set of picks, not one string -- JSON-Schema array
                # with an enum'd `items` (not a top-level `enum`) is what tells an MCP client the
                # value must be a list of these exact strings, not a comma-joined single string.
                row["type"] = "array"
                row["items"] = {"type": "string", "enum": option_strs}
            else:
                row["enum"] = option_strs
        default = raw.get("default")
        if default is not None and str(default).strip():
            if row.get("type") == "array":
                # SkillInputVariable.default is comma-joined labels for a multiselect (see
                # editor/dto.py) -- split it back out so the default matches the array type
                # declared above instead of shipping a comma-joined string an agent would then
                # have to parse itself.
                row["default"] = [p.strip() for p in str(default).split(",") if p.strip()]
            else:
                row["default"] = default
        out.append(row)
    return out


def _placeholder_names_from_payload(payload: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            for match in _PLACEHOLDER_NAME_RE.finditer(value):
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    names.append(name)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item)

    visit(payload)
    return names


def _upload_input_descriptions(source_steps: list[dict[str, Any]]) -> dict[str, str]:
    """Describe the auto-declared ``file_path`` input using the recorded example filename.

    Without this the generic derivation yields "Enter file path", which does not tell an agent
    calling ``get_skill_inputs`` that a real file on disk is expected.

    Also states that a *folder* path is accepted, because that is the only practical way to
    drive a batch upload: the runtime expands a directory into every file inside it
    (``run.js::resolveUploadPaths``), so an agent moving 20 or 200 documents passes one
    location string rather than enumerating every file.

    Deliberately does **not** claim how many files this particular control accepts. Whether it
    takes one file or many is a property of the live page (the ``multiple`` attribute), not of
    how many files happened to be picked while recording — a single-file recording against a
    multi-select control is perfectly normal. The runtime asks the element itself at replay
    time and refuses a folder with a clear message if the control only takes one, so the
    description stays honest rather than guessing from the recording (EXEC-15).
    """
    for step in source_steps:
        if _step_action_name(step) not in {"upload", "upload_intent"}:
            continue
        example = _upload_recorded_filename(step)
        hint = f" (e.g. {example})" if example else ""
        return {"file_path": (
            f"Path to the file to upload{hint}. "
            f"If this page's upload control accepts more than one file, a folder path may be "
            f"given instead and every file directly inside it is uploaded, in name order."
        )}
    return {}


def _dialog_input_descriptions(source_steps: list[dict[str, Any]]) -> dict[str, str]:
    """Describe the auto-declared ``dialog_answer`` input using the recorded prompt's message.

    Without this the generic derivation yields "Enter dialog answer", which doesn't tell an
    agent what the website actually asked. See compiler/build.py's dialog_accept marker branch,
    which is the sole producer of a ``dialog_answer``-bound step.
    """
    for step in source_steps:
        if step.get("input_binding") != "dialog_answer":
            continue
        try:
            dialog_payload = json.loads(step.get("value") or "")
        except (TypeError, ValueError):
            dialog_payload = None
        message = dialog_payload.get("message") if isinstance(dialog_payload, dict) else None
        if message:
            return {"dialog_answer": f'Text to type into the browser prompt "{message}".'}
        return {"dialog_answer": "Text to type into the browser's prompt dialog."}
    return {}


def _merge_saved_inputs_with_execution_placeholders(
    declared_inputs: list[Any],
    execution_steps: list[dict[str, Any]],
    descriptions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    from conxa_compile.compiler.upload_binding import (
        _RUNTIME_ONLY_PLACEHOLDER_RE,
        for_each_loop_variable_names,
    )

    inputs = _normalize_saved_skill_inputs(declared_inputs)
    seen = {str(item.get("name") or "") for item in inputs}
    overrides = descriptions or {}
    # A for_each loop's own {{<as>_id}}/{{<as>_index}} are populated by run.js at replay time,
    # never something a user/agent supplies — same reason downloaded_file* is excluded below.
    loop_vars = for_each_loop_variable_names(execution_steps)
    # The primary record->compile path (compiler/build.py) already declares file_path with a
    # generic humanized label ("File Path") before this function ever sees it, so the "not yet
    # seen" branch below never fires for it. Overwrite it here too, since the upload-derived
    # description is always more specific than a generic label-cased binding name.
    for item in inputs:
        override = overrides.get(str(item.get("name") or ""))
        if override:
            item["description"] = override
    for name in _placeholder_names_from_payload(execution_steps):
        if name in seen:
            continue
        if _RUNTIME_ONLY_PLACEHOLDER_RE.match(name):
            # downloaded_file / downloaded_file_N — populated automatically at replay time by
            # run.js's download_observed handler from an earlier download in the same run
            # (see _bind_downloads_to_uploads), never something a user/agent supplies.
            continue
        if name in loop_vars:
            continue
        seen.add(name)
        inputs.append(
            {
                "name": name,
                "type": "string",
                "description": overrides.get(name) or f"Enter {name.replace('_', ' ')}",
            }
        )
    return inputs


def _drop_unreferenced_inputs(
    inputs: list[dict[str, Any]],
    execution_steps: list[dict[str, Any]],
    on_warning: Any = None,
) -> list[dict[str, Any]]:
    """Remove a declared input that nothing in the shipped steps mentions (BUILD-24).

    A step the packager dropped (an unexportable one, a react-select the collapse lost) leaves its
    input behind, and every caller is then asked for a value nothing uses. "Mentions" is a plain
    substring test over the serialized steps, deliberately generous: a loop's ``items`` name and a
    fork policy's ``input`` are bare names, not ``{{placeholders}}``, and must survive. The removal
    is reported, never silent.
    """
    if not inputs:
        return inputs
    blob = json.dumps(execution_steps, ensure_ascii=False, default=str)
    kept: list[dict[str, Any]] = []
    for item in inputs:
        name = str(item.get("name") or "")
        if name and name not in blob:
            if on_warning:
                on_warning(f"The input \u201c{name}\u201d isn\u2019t used by any step that was kept, so it was removed from the skill.")
            continue
        kept.append(item)
    return kept


def _render_saved_skill_markdown(title: str, inputs: list[dict[str, Any]], execution_steps: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", "", "## Inputs"]
    if inputs:
        for item in inputs:
            lines.append(f"- `{{{{{item['name']}}}}}`: {item.get('description') or 'Runtime input'}.")
    else:
        lines.append("- No runtime inputs are required.")
    lines.extend(["", "## Steps"])
    for index, step in enumerate(execution_steps, start=1):
        step_type = step.get("type")
        if step_type == "navigate":
            label = f"Open {step.get('url')}"
        elif step_type in {"fill", "type"}:
            label = f"Enter {step.get('value', '')}"
        elif step_type == "click":
            label = f"Click {step.get('selector')}"
        elif step_type == "check":
            label = f"Check {step.get('kind', 'url')}"
        else:
            label = str(step_type or "Run step")
        lines.append(f"{index}. {label}")
    return "\n".join(lines).strip() + "\n"


def _text_selector_value(selector: Any) -> str:
    text = str(selector or "").strip()
    match = _TEXT_SELECTOR_RE.match(text)
    if not match:
        return ""
    return match.group("text").strip()


def _normalize_recovery_name(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text or "action"


def _target_text_from_selector(selector: str) -> str:
    text_value = _text_selector_value(selector)
    if text_value:
        return " ".join(text_value.split())
    match = re.search(r"input\s*\[\s*name\s*=\s*['\"]([^'\"]+)['\"]\s*\]", selector or "", re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _saved_recovery_anchors(step: dict[str, Any], target_text: str) -> list[dict[str, Any]]:
    recovery = step.get("recovery") if isinstance(step.get("recovery"), dict) else {}
    raw_anchors = recovery.get("anchors") if isinstance(recovery.get("anchors"), list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_anchors:
        if not isinstance(raw, dict):
            continue
        relation = str(raw.get("relation") or "").strip().lower()
        if relation == "target_sentence":
            # The full vision-anchor sentence — surfaced separately via
            # _saved_recovery_anchor_sentence's dedicated field, not as one of
            # these short anchor phrases.
            continue
        text = str(raw.get("text") or raw.get("element") or "").strip()
        if "{{" in target_text and "{{" not in text:
            continue
        if not text or text in seen:
            continue
        priority = 2 if relation == "target" or text == target_text else 1
        out.append({"text": text, "priority": priority})
        seen.add(text)
    if target_text and target_text not in seen:
        out.append({"text": target_text, "priority": 2})
    return out[:4]


def _saved_recovery_anchor_sentence(step: dict[str, Any]) -> str:
    """The single vision-chosen prose description of the target — a plain-English
    caption for Tier B recovery, separate from the short-phrase anchors list."""
    recovery = step.get("recovery") if isinstance(step.get("recovery"), dict) else {}
    raw_anchors = recovery.get("anchors") if isinstance(recovery.get("anchors"), list) else []
    for raw in raw_anchors:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("relation") or "").strip().lower() == "target_sentence":
            text = str(raw.get("element") or raw.get("text") or "").strip()
            if text:
                return text
    return ""


# How many sibling summaries to carry into recovery.json. The recorder already caps each one's
# text at 40 chars (bridge.js siblingSummaries), so this bounds the whole block to a few hundred
# bytes per step — small enough to ride the existing sync and to sit alongside the live ranked
# digest without eating into its 40k-char budget (candidate_digest.js).
_RECORDED_CONTEXT_SIBLING_MAX = 8


def _recorded_context_for_saved_step(step: dict[str, Any]) -> dict[str, Any]:
    """Where this element sat on the page AT RECORDING TIME — the structural before-picture.

    The agent recovery tier is handed a ranked list of what is on the page *now*. That describes
    the present but not the change, and drift is a change: "it used to sit in the toolbar next to
    Export" is what identifies a moved button, and nothing in a live inventory can say it.

    Everything here was already captured by the recorder (bridge.js's `context`) and already
    survives into the saved skill (build.py's `signals["context"]`) — this only selects the
    structural fields and drops the rest. `page_url`/`page_title`/`timing` are merged into the
    same dict by the compiler and are deliberately excluded: they describe the visit, not the
    element's position in the page.
    """
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    context = signals.get("context") if isinstance(signals.get("context"), dict) else {}
    if not context:
        return {}

    out: dict[str, Any] = {}
    parent = str(context.get("parent") or "").strip()
    if parent:
        out["parent"] = parent

    raw_siblings = context.get("siblings")
    if isinstance(raw_siblings, list):
        siblings = [" ".join(str(s or "").split()) for s in raw_siblings if str(s or "").strip()]
        if siblings:
            out["siblings"] = siblings[:_RECORDED_CONTEXT_SIBLING_MAX]

    index_in_parent = context.get("index_in_parent")
    if isinstance(index_in_parent, int):
        out["index_in_parent"] = index_in_parent

    form_context = str(context.get("form_context") or "").strip()
    if form_context:
        out["form_context"] = form_context

    return out


def _saved_step_visual_ref(step_id: int, visuals_dir: Path | None) -> str | None:
    if visuals_dir is None:
        return None
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = visuals_dir / f"Image_{step_id}{suffix}"
        if candidate.is_file():
            return f"visuals/{candidate.name}"
    return None


def _saved_visual_asset_path(step: dict[str, Any], source_session_id: str) -> str:
    """The frame used for this step's recovery reference image (visual_ref).

    Prefers the vision LLM's chosen_frame (signals.visual.default_frame_label,
    resolved against signals.visual.frames) — the frame it judged best shows the
    page's state right before the action — falling back to full_screenshot
    (before_near) when no chosen frame is recorded or the labeled file is
    missing (legacy recordings, or the per-step vision call never ran).
    """
    signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
    visual = signals.get("visual") if isinstance(signals.get("visual"), dict) else {}
    frames = visual.get("frames") if isinstance(visual.get("frames"), dict) else {}
    label = str(visual.get("default_frame_label") or "").strip()
    rel = ""
    if label and label in frames:
        rel = str(frames.get(label) or "").strip().replace("\\", "/")
    if not rel:
        rel = str(visual.get("full_screenshot") or "").strip().replace("\\", "/")
    if not rel or ".." in rel:
        return ""
    if rel.startswith("sessions/"):
        return rel
    if source_session_id and rel.startswith("images/"):
        return f"sessions/{source_session_id}/{rel}"
    return rel


def _write_saved_visual_assets(
    *,
    source_steps: list[dict[str, Any]],
    skill_dir: Path,
    source_session_id: str,
) -> Path | None:
    visuals_dir = skill_dir / "visuals"
    wrote = False
    for index, step in enumerate(source_steps, start=1):
        if not isinstance(step, dict):
            continue
        rel = _saved_visual_asset_path(step, source_session_id)
        if not rel:
            continue
        try:
            source_path = resolve_skill_asset(rel)
        except ValueError:
            continue
        if not source_path.is_file():
            continue
        suffix = source_path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue

        signals = step.get("signals") if isinstance(step.get("signals"), dict) else {}
        visual = signals.get("visual") if isinstance(signals.get("visual"), dict) else {}
        bbox = visual.get("bbox") if isinstance(visual.get("bbox"), dict) else {}
        viewport = str(visual.get("viewport") or "")
        image_bytes = source_path.read_bytes()
        if bbox:
            image_bytes = _apply_bbox_highlight(image_bytes, bbox, viewport, highlight_alpha=0.35)
            suffix = ".jpg"

        visuals_dir.mkdir(parents=True, exist_ok=True)
        (visuals_dir / f"Image_{index}{suffix}").write_bytes(image_bytes)
        wrote = True
    return visuals_dir if wrote else None


def _build_saved_skill_recovery(
    source_steps: list[dict[str, Any]],
    step_ids: list[int],
    visuals_dir: Path | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for step, step_id in zip(source_steps, step_ids):
        action = _step_action_name(step)
        if action == "input":
            action = "type"
        if action not in RECOVERY_ACTION_TYPES:
            continue
        selector = str(_step_selector(step) or "").strip()
        if not selector:
            continue
        target_text = _target_text_from_selector(selector)
        target = step.get("target") if isinstance(step.get("target"), dict) else {}
        target_role = "textbox" if action in {"type", "fill"} else str(target.get("role") or "")
        raw_intent = str(step.get("intent") or "").strip()
        intent = raw_intent or f"{action}_{_normalize_recovery_name(target_text or target_role)}"
        entry: dict[str, Any] = {
            "step_id": step_id,
            "intent": intent,
            "target": {
                "text": target_text,
                "role": target_role,
            },
            "anchors": _saved_recovery_anchors(step, target_text),
            # `text_variants` and `selector_context.alternatives` used to live here. They fed the
            # runtime's fallback-selector walk and fuzzy text match — two Layer 2 stages deleted in
            # 2026-09 because they clicked `.first()` on a text-derived selector with no uniqueness
            # gate. The variant generator was the worst of it: it expanded "Save changes" into
            # "Save"/"Update" and "Delete" into "Remove", which is exactly how a step could act on
            # the wrong control. Nothing reads either field now, so they are no longer emitted.
            # `target.fallback_selectors` on the EXECUTION step is unrelated and still live — it
            # feeds primary resolution via selector_grammar.py.
            "fallback": {"role": target_role},
            "selector_context": {"primary": selector},
        }
        recorded_context = _recorded_context_for_saved_step(step)
        if recorded_context:
            entry["recorded_context"] = recorded_context
        visual_ref = _saved_step_visual_ref(step_id, visuals_dir)
        if visual_ref:
            entry["visual_ref"] = visual_ref
        anchor_sentence = _saved_recovery_anchor_sentence(step)
        if anchor_sentence:
            entry["anchor_sentence"] = anchor_sentence
        # BUILD-25 stage e: the compiler's second-opinion pass's label_phase
        # finding. Read by runtime/app/failure_response.js's Tier B recovery
        # prompt as a workflow-position prior. Absent on any step the pass
        # never ran on or didn't label.
        phase = str(step.get("phase") or "").strip()
        if phase:
            entry["phase"] = phase
        entries.append(entry)
    return {"steps": entries}


def _build_workflow_from_saved_skill(
    *,
    bundle_root: Path,
    workflow_slug: str,
    saved_skill: dict[str, Any],
    on_warning: Callable[[str], None] | None = None,
) -> None:
    meta = saved_skill.get("meta") if isinstance(saved_skill.get("meta"), dict) else {}
    title = str(meta.get("title") or workflow_slug).strip() or workflow_slug
    skills = saved_skill.get("skills") if isinstance(saved_skill.get("skills"), list) else []
    block = skills[0] if skills and isinstance(skills[0], dict) else {}
    raw_steps = block.get("steps") if isinstance(block.get("steps"), list) else []
    source_session_id = str(meta.get("source_session_id") or "").strip()
    execution_steps: list[dict[str, Any]] = []
    source_steps: list[dict[str, Any]] = []
    step_ids: list[int] = []
    export_steps = raw_steps
    # Wire a same-run download -> upload handoff before conversion (EXEC-10/W-2), so a matched
    # upload step's value becomes {{downloaded_file...}} instead of the generic {{file_path}}
    # _saved_step_to_execution_step would otherwise fall back to.
    _bind_downloads_to_uploads(export_steps)
    for raw_index, raw in enumerate(export_steps, start=1):
        if not isinstance(raw, dict):
            continue
        converted = _saved_step_to_execution_step(raw)
        if converted is None:
            action = _step_action_name(raw) or "unknown"
            if normalize_action_kind(action) == "drag_drop":
                # Drag-and-drop can't be reliably replayed — drop the step instead of
                # failing the whole build.
                if on_warning:
                    on_warning(f"Step {raw_index}: drag-and-drop isn't supported and was removed from the skill.")
                continue
            if normalize_action_kind(action) == "extract":
                if on_warning:
                    on_warning(f"Step {raw_index}: a value to read needs both a target element and an output name, so it was removed from the skill. Set them in Human Edit, then rebuild.")
                continue
            if normalize_action_kind(action) == "outcome_fork":
                # A fork none of whose options can run (every body step was unexportable) has
                # nothing safe to offer. Same drop-and-warn shape as the cases around it.
                if on_warning:
                    on_warning(f"Step {raw_index}: the popup decision has no runnable choices and was removed from the skill.")
                continue
            if normalize_action_kind(action) == "ai_review":
                # EXEC-13: an inserted-but-unconfigured AI Review step (blank prompt — see
                # _new_manual_step) has nothing to ask. Same drop-and-warn shape as drag_drop
                # above, rather than failing the whole build over one unfinished checkpoint.
                if on_warning:
                    on_warning(f"Step {raw_index}: AI Review has no prompt yet and was removed from the skill. Add a prompt in Human Edit, then rebuild.")
                continue
            if normalize_action_kind(action) in {"check", "assert"} and \
                    str(raw.get("check_kind") or "").strip().lower().replace("-", "_") == "snapshot":
                # Same drop-and-warn shape as drag_drop above — the runtime has no visual-diff
                # engine, so this kind can only ever fail at replay.
                if on_warning:
                    on_warning(f"Step {raw_index}: snapshot checks aren't supported and were removed from the skill.")
                continue
            raise ValueError(f"Saved skill step {raw_index} action {action!r} is not exportable.")
        execution_steps.append(converted)
        source_steps.append(raw)
        step_ids.append(len(execution_steps))
    if not execution_steps:
        raise ValueError(f"Saved skill {meta.get('id') or workflow_slug!r} has no executable steps.")

    inputs = _merge_saved_inputs_with_execution_placeholders(
        list(saved_skill.get("inputs") or []),
        execution_steps,
        {**_upload_input_descriptions(source_steps), **_dialog_input_descriptions(source_steps)},
    )
    inputs = _drop_unreferenced_inputs(inputs, execution_steps, on_warning)
    skill_dir = bundle_root / "skills" / workflow_slug
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    visuals_dir = _write_saved_visual_assets(
        source_steps=source_steps,
        skill_dir=skill_dir,
        source_session_id=source_session_id,
    )
    recovery = _build_saved_skill_recovery(source_steps, step_ids, visuals_dir)
    (skill_dir / "execution.json").write_text(dumps_safe(execution_steps, indent=2, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "recovery.json").write_text(dumps_safe(recovery, indent=2, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "input.json").write_text(dumps_safe({"inputs": inputs}, indent=2, ensure_ascii=False), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(scrub_text(_render_saved_skill_markdown(title, inputs, execution_steps)), encoding="utf-8")

    # Carry the compiled structural fingerprint through to the runtime pack so the
    # runtime can run a pre-execution drift check. It rides on SkillMeta and would
    # otherwise be dropped by _write_skill_packs_format (which reads from disk).
    structural_fp = meta.get("structural_fingerprint")
    if isinstance(structural_fp, dict) and structural_fp.get("landmarks"):
        (skill_dir / "structural_fingerprint.json").write_text(
            dumps_safe(structural_fp, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # EXEC-36: same carry-through pattern as structural_fingerprint above — the recording
    # environment rides on SkillMeta and would otherwise be dropped by _write_skill_packs_format.
    environment = meta.get("environment")
    if isinstance(environment, dict) and environment:
        (skill_dir / "environment.json").write_text(
            dumps_safe(environment, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # PROD-3-DRYRUN layer 5: same carry-through pattern — the compensation-workflow link (set in
    # the Human Edit editor) rides on SkillMeta and would otherwise be dropped.
    compensation_skill = str(meta.get("compensation_skill") or "").strip()
    if compensation_skill:
        (skill_dir / "compensation_skill.json").write_text(
            dumps_safe({"compensation_skill": compensation_skill}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


