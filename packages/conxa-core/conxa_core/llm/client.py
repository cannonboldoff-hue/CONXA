"""Shared OpenAI-compatible HTTP/prompt-building engine.

Builds and parses the actual HTTP requests to LLM providers: the cloud's
concrete provider router (conxa-cloud/backend/app/llm/router.py) imports the
task-prompt builder (_openai_body_dict) and response helpers here to serve
requests proxied from the Build Studio's compile pipeline. The pipeline's own
call dispatcher lives in conxa_compile/llm/client.py (Build Studio only).
"""

from __future__ import annotations

import itertools
import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any
from urllib import error, request
from urllib.parse import urlparse, urlunparse

from conxa_core.config import settings

_REQUEST_COUNTER = itertools.count(1)


def _debug_log(message: str) -> None:
    if not settings.llm_debug:
        return
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[LLM DEBUG] {ts} | {message}")


# The single source of truth for "this task needs a vision-capable model." conxa_compile's own
# call_llm() imports _is_vision_task (below) directly rather than keeping a copy, so this set is
# decisive for the normal pooled request path (which proxy endpoint Build Studio hits). The
# cloud router (conxa-cloud/backend/app/llm/router.py) imports THIS SAME set object as
# _VISION_TASK_NAMES — it is consulted only on the BYOK path (for_vision is None), where it
# picks the model within an already-selected provider entry. Both matter: a task missing here
# mis-routes the pooled path to /llm/proxy/text; present here but absent from the router's own
# usage would leave a BYOK call correctly routed but served by the wrong model. (BUILD-29: this
# used to be three separately hand-maintained copies — collapsed to one after the third copy
# silently dropped workflow_review and every compile went rules-only with no error.)
VISION_TASKS = frozenset({
    "anchor_vision", "anchor_vision_frameset", "vision_reasoning", "region_selector",
    "copilot_diagnose", "copilot_reply", "workflow_review",
})


def _is_vision_task(task: str) -> bool:
    """True for multimodal vision tasks. See VISION_TASKS above."""
    return task in VISION_TASKS


def _copilot_modality(task: str, payload: dict[str, Any]) -> str | None:
    """"text"/"multimodal" for copilot_diagnose/copilot_reply, based on whether this turn
    carries a screenshot — None for every other task (caller falls back to the flat
    _is_vision_task set as today). Unlike every other vision task, Copilot's modality isn't
    fixed by its task name: the same task sends text-only turns and screenshot-bearing turns
    (see conxa_compile/llm/copilot.py's optional image_base64), so it needs its own model slot
    per turn rather than always burning the shared vision model. In dev (settings.environment
    == "dev"), always resolves to the text_model slot — the screenshot, if any, is still
    attached to the request body (see _openai_body_dict's copilot_diagnose/copilot_reply
    branches), so the configured dev text_model must itself be multimodal-capable if screenshots
    are expected to be read."""
    if task == "execute_chat":
        # Execute is multimodal-only: every turn (with or without an image) uses the multimodal
        # model. No dev override.
        return "multimodal"
    if task not in ("copilot_diagnose", "copilot_reply"):
        return None
    if settings.environment == "dev":
        return "text"
    return "multimodal" if payload.get("image_base64") else "text"


def _safe_error_snippet(text: str, limit: int = 280) -> str:
    t = " ".join(str(text).split())
    if len(t) > limit:
        return t[: limit - 3] + "..."
    return t


def _append_llm_detail(sink: list[str] | None, msg: str, *, sink_lock: threading.Lock | None = None) -> None:
    _debug_log(msg)
    if sink is None:
        return
    if sink_lock is not None:
        with sink_lock:
            sink.append(msg)
    else:
        sink.append(msg)


def _is_openai_compatible_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    # Local/custom adapters may still accept the legacy payload shape.
    if "integrate.api.nvidia.com" in (parsed.netloc or ""):
        return True
    path = (parsed.path or "").rstrip("/")
    # A pre-built chat/completions URL (e.g. an Azure OpenAI BYOK deployment
    # URL, which has no /v1 segment at all) is accepted verbatim here — it
    # mirrors _chat_completions_url's own short-circuit for the same shape.
    # "/v1" and "/openai" are matched by suffix, not exact equality, so
    # providers whose base path carries a vendor prefix (Groq's
    # "/openai/v1", Google AI Studio's "/v1beta/openai") are still
    # recognized as OpenAI-compatible.
    return (
        path.endswith("/v1")
        or path.endswith("/openai")
        or path.endswith("/chat/completions")
    )


def _chat_completions_url(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/chat/completions"):
        return endpoint
    if path.endswith("/v1"):
        path = f"{path}/chat/completions"
    elif not path:
        path = "/v1/chat/completions"
    else:
        path = f"{path}/chat/completions"
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment))


def _legacy_payload(task: str, payload: dict[str, Any]) -> bytes:
    body = dict(payload)
    body.setdefault("task", task)
    return json.dumps(body).encode("utf-8")


def _resolved_model(task: str, payload: dict[str, Any]) -> Any:
    modality = _copilot_modality(task, payload)
    if modality is not None:
        # No standalone multimodal singleton exists (only per-provider PoolEntry.multimodal_model
        # in the router) — degrade to the vision singleton here too, same as the router falling
        # back to vision_model when multimodal_model is unset.
        default = settings.llm_text_model if modality == "text" else settings.llm_vision_model
        return payload.get("model") or default
    if _is_vision_task(task):
        return payload.get("model") or settings.llm_vision_model
    return payload.get("model") or settings.llm_text_model


_STUDIO_RULES = (
    "Rules you never break: you do not write selectors, element addresses or code for the "
    "workflow; you only explain, ask, and — when asked — propose changes that the Studio checks "
    "before anything is applied. Never claim something happened (a recording, a compile, a test, "
    "an approval) unless the context says it did. Refer to steps by their name, never only by "
    "number. Be brief and concrete; plain prose, no markdown headings. When the context has a "
    "\"Changes you are offering\" section, describe only those changes, in your own words, and say "
    "they take effect when the person presses Apply; never promise a change that is not listed. "
    "When the person asked for a change and that section says none could be prepared, say so "
    "plainly and point them to Human Edit instead of describing what you would have done."
)

_STUDIO_CHAT_SYSTEMS: dict[str, str] = {
    # Streamed prose (no JSON contract — see copilot_reply for why).
    "studio_chat_reply": (
        "You are the assistant inside Conxa Build Studio, an app where a person records a task "
        "once in a real browser and it becomes a workflow that other people's AI agents can run. "
        "The user message carries the current context (state of the workflow, decisions already "
        "made, a compact step digest, earlier conversation) followed by the person's latest "
        "message. Answer that message directly. " + _STUDIO_RULES
    ),
    # JSON: sorts an answer typed in free text onto one of a question card's allowed choices.
    "chat_map_answer": (
        "A question card is open with a fixed list of allowed choices. Read the person's free-"
        "text reply and decide which single allowed choice it means. Return ONLY a JSON object: "
        "{\"choice\": \"<one of the allowed choice ids, or \"unclear\">\", \"note\": \"<=15 words\"}. "
        "If the reply is ambiguous, off-topic, or asks a question instead of answering, choose "
        "\"unclear\". Never invent an id that is not in the list."
    ),
    # JSON: checks a compiled workflow against what the person said it is for, and sorts the
    # recorded values that are not typed text into "fixed" / "varies each run" / "unsure".
    "workflow_understanding": (
        "A person recorded a task in a browser and described what it is for. You get that "
        "description (task), the compiled steps in order (steps), what the run ends with "
        "(ends_with_checks), the inputs it already has (existing_inputs) and candidates: values "
        "taken from the recording (a page address, a record identifier, a clicked item's label, "
        "or text the person typed into a field: kind \"typed\"). Decide for each candidate whether it is specific to this "
        "recording. \"varies\": the task describes a kind of thing, or does not name this one — "
        "which repository, which file, which customer — so a future run would use a different "
        "one. \"fixed\": the task names this exact thing, or it is simply the same page every "
        "time (a settings page, a sign-in page). \"unsure\": you cannot tell; do not guess. "
        "Also judge coverage: ok is false only when the task clearly asks for an outcome the "
        "steps never reach (for example it says send and the steps stop at save draft); note is "
        "then one plain sentence, at most 25 words. Return ONLY a JSON object: "
        "{\"goal_summary\": \"one sentence\", \"coverage\": {\"ok\": true|false, \"note\": \"\"}, "
        "\"values\": [{\"ref\": \"<a candidate ref>\", \"verdict\": \"fixed|varies|unsure\", "
        "\"name\": \"short_snake_case_input_name (varies/unsure only)\", \"literal\": \"for url "
        "candidates only: the exact part of the recorded address that changes\"}]}. Never "
        "invent a ref, never write a selector, and never claim a gap you cannot see in the steps."
    ),
    # JSON: before recording, a short checklist of what the person will need to do, from their own description.
    "record_plan": (
        "A person is about to record a task in a browser and described what it is for (task), "
        "with the address they start from (start_url). Write the short checklist of actions they "
        "will most likely need to perform, in order, in plain words a non-technical person "
        "understands. 3 to 8 items, each under 12 words, each starting with a verb. Only what the "
        "description says or clearly implies: never invent a button name, field name or value. "
        "Return ONLY a JSON object: {\"steps\": [\"...\"]}."
    ),
    # JSON: is the person's message a command for one of the actions available right now?
    "chat_route_intent": (
        "The person is chatting inside a Build Studio workflow conversation. You get the "
        "actions available right now (actions: \"id: what it does\"), the workflow's input "
        "names (input_names) and the person's message. Decide whether the message asks to DO "
        "one of those actions now, or is just a question, a comment or a description of what "
        "happened (\"why did the test fail?\", \"the button moved\"). Only an instruction to act "
        "counts. Return ONLY a JSON object: {\"action\": \"<an action id from the list, or "
        "chat>\", \"slots\": {\"inputs\": {\"<input name>\": \"<value>\"}, \"workflow_id\": "
        "\"<id from workflows>\"}}. Use \"chat\" whenever you are not sure. slots.inputs is "
        "only for the test action and only for input names in input_names with a value the "
        "person actually stated; slots.workflow_id is only for open_workflow. Never invent an "
        "action id, an input name or a value."
    ),
    # JSON: folds older conversation turns into a short rolling summary.
    "thread_summarize": (
        "Summarize the older part of a conversation between a person and the Build Studio "
        "assistant so it can be dropped from the context. Keep: what was asked, what was "
        "decided, what is still open, names of workflows and steps. Drop pleasantries. Do not "
        "restate facts that appear in a PINNED FACTS block. At most 180 words. Return ONLY a "
        "JSON object: {\"summary\": \"...\"}."
    ),
    # JSON: in general Chat only — does the conversation call for drafting a workflow?
    "studio_chat_actions": (
        "You read a general-chat conversation inside Conxa Build Studio. If the person has "
        "agreed on a concrete task worth recording as a workflow, return a draft; otherwise "
        "return null. Return ONLY a JSON object: {\"draft_workflow\": null | {\"name\": str, "
        "\"group\": str (an existing group name from the context, or \"\"), \"starting_page\": "
        "str (a full https:// address, or \"\" if unknown), \"task\": str (one sentence)}}. "
        "Never draft a workflow the person has not shown intent to create."
    ),
}


def _openai_messages_for_task(task: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("input")
    prompt = payload.get("prompt")

    if task == "semantic_enrichment":
        return [
            {
                "role": "system",
                "content": (
                    "Return strict JSON with keys: intent (snake_case string), normalized_text (string), "
                    "confidence (0 to 1 number)."
                ),
            },
            {"role": "user", "content": json.dumps(data or {}, ensure_ascii=False)},
        ]
    if task == "recovery_assist":
        return [
            {
                "role": "system",
                "content": (
                    "Return strict JSON with keys: selected (candidate id string), confidence (0 to 1 number), "
                    "reason (short string)."
                ),
            },
            {"role": "user", "content": json.dumps(data or {}, ensure_ascii=False)},
        ]
    if task == "vision_reasoning":
        return [
            {
                "role": "system",
                "content": (
                    "Return strict JSON with keys: best_candidate (candidate id string), confidence (0 to 1 number), "
                    "reason (short string)."
                ),
            },
            {"role": "user", "content": json.dumps({"prompt": prompt, "input": data}, ensure_ascii=False)},
        ]
    if task == "intent_generation":
        intent_prompt = ""
        if isinstance(data, dict):
            intent_prompt = str(data.get("prompt") or "")
        return [
            {
                "role": "system",
                "content": "Return strict JSON with key: intent (single snake_case string).",
            },
            {"role": "user", "content": intent_prompt or json.dumps(data or {}, ensure_ascii=False)},
        ]
    if task == "selector_generation":
        # Compile-time: generate N selector candidates for one element.
        return [
            {
                "role": "system",
                "content": (
                    "You generate Playwright locator strings for web automation. "
                    "Return strict JSON with key 'candidates' (array of objects: "
                    "selector (string), rank (1=best), rationale (short string), intent (snake_case action description)). "
                    "Selector priority — use the first that applies:\n"
                    "1. [data-testid=\"x\"] — most stable\n"
                    "2. [aria-label=\"x\"] — when element has an explicit aria-label attribute\n"
                    "3. button:has-text(\"x\"), a:has-text(\"x\") — tag + visible text for buttons and links\n"
                    "4. input[name=\"x\"], select[name=\"x\"], textarea[name=\"x\"] — name attr for form controls only\n"
                    "5. [placeholder=\"x\"] — for text inputs with placeholder\n"
                    "6. #stable-id — non-hashed, non-generated IDs\n"
                    "7. text=\"x\" — Playwright text locator (exact, case-sensitive) as last text-based resort\n"
                    "Critical rules:\n"
                    "- NEVER write [role=\"button\"] or [role=\"x\"] — CSS [role] only matches the raw HTML attribute, "
                    "not the ARIA role; plain <button> and <a> elements do NOT have a role attribute in HTML\n"
                    "- NEVER write [name=\"x\"] on buttons, divs, spans, or links — the name attribute only exists "
                    "on form controls (input/select/textarea) and form elements\n"
                    "- When a11y_node is provided with role+name, prefer button:has-text() or [aria-label] over "
                    "any attribute selector; use role=button[name=\"x\"] (Playwright aria syntax) only as a "
                    "secondary candidate, never as rank-1\n"
                    "- Avoid: hashed classes, auto-generated IDs, nth-of-type chains, XPath\n"
                    "No markdown, no extra keys."
                ),
            },
            {"role": "user", "content": json.dumps(data or payload, ensure_ascii=False)},
        ]
    if task == "clarify_question_copy":
        # Compile-time: rewrite the follow-up questions asked after a compile into warm, plain
        # language. Copy only: ids, how many choices there are, and what each choice does are all
        # decided by code (conversation/clarify.py), which also validates this answer and keeps
        # its own text for anything that does not check out -- see llm/clarify_question_copy.py.
        return [
            {
                "role": "system",
                "content": (
                    "You rewrite short questions for someone who is NOT technical (think: an "
                    "office manager, not an engineer) who just recorded a task in a browser. "
                    "`input.questions` is a list; each has an `id`, a `title`, a `body` and "
                    "optionally `choices` (each with an `id`, `label` and `detail`) -- these are "
                    "plain drafts. Rewrite each in friendly, easy words, like explaining to a "
                    "colleague. Use short sentences. You may use more words than the draft if it "
                    "makes things clearer, e.g. one sentence on why we are asking. Never use "
                    "words like input, run, step, selector, assertion, workflow, binding, "
                    "parameter, token, field value. Say 'each time someone uses it' instead of "
                    "'each run', 'the screen' instead of 'the page'. KEEP THE MEANING EXACTLY: "
                    "a choice must still do the same thing as its draft, never swap or merge "
                    "choices, never add or drop one. Copy any text inside curly quotes (“like "
                    "this”) exactly, character for character, and keep every quoted item the "
                    "draft mentions. Never invent facts, file names, buttons or values not in "
                    "the draft. A question about a popup may also carry `popup_buttons`, the "
                    "popup's own buttons: use them only to say what the popup is about, never "
                    "name an action that is not one of them, and never add them to the answer. "
                    "Return strict JSON:{\"questions\": [{\"id\": same id, "
                    "\"title\": string, \"body\": string, \"choices\": [{\"id\": same id, "
                    "\"label\": string, \"detail\": string}]}]}. Omit `choices` when the "
                    "question had none."
                ),
            },
            {"role": "user", "content": json.dumps(data or payload, ensure_ascii=False)},
        ]
    if task == "recovery_resolve":
        # Runtime tier 3: locate one element on current DOM given semantic description.
        return [
            {
                "role": "system",
                "content": (
                    "You locate one element on a current DOM given a semantic description "
                    "and the original element's bbox/ancestors. Return strict JSON with keys: "
                    "selector (Playwright CSS string, single best match), "
                    "confidence (0 to 1 number), reason (short string)."
                ),
            },
            {"role": "user", "content": json.dumps(data or payload, ensure_ascii=False)},
        ]
    if task == "workflow_intent":
        # Compile-time: single call to infer high-level workflow goal + per-step intents.
        # Each step yields BOTH forms of intent: intent_token (machine-readable
        # snake_case, consumed by the compiler's deterministic logic — recovery
        # policy, destructive gating, validation facets) and intent (readable
        # prose shown to the human in review). One call is the single source of
        # both, replacing the old per-step intent_generation burst.
        return [
            {
                "role": "system",
                "content": (
                    "You build a workflow intent graph from a sequence of recorded actions. "
                    "Return strict JSON with keys: goal (one sentence), steps (array of "
                    "{index, intent_token, intent, verification_anchor}), decision_points (array of "
                    "{step_index, description}), expected_end_state (object with brief description). "
                    "For each step: intent_token must be one specific snake_case action token describing "
                    "the user goal for that control (verb + object, lowercase, underscores only — e.g. "
                    "click_sign_in_button, enter_email_value, navigate_to_dashboard); intent is a short "
                    "readable sentence describing what that step does."
                ),
            },
            {"role": "user", "content": json.dumps(data or payload, ensure_ascii=False)},
        ]
    if task == "workflow_semantics":
        # Compile-time (BUILD-25): single whole-workflow call producing findings
        # that compiler/second_opinion.py applies directly onto the compiled
        # steps — never selectors, never a change to *how* an element is
        # found. Human Review is the gate: a wrong finding is edited there
        # like any other compiler output.
        return [
            {
                "role": "system",
                "content": (
                    "You review a compiled browser-automation workflow and find improvements that "
                    "will be applied automatically, so only report ones you are confident about. "
                    "Return strict JSON with key: suggestions (array of {step_key, kind, current, "
                    "proposed, why}). step_key must be copied EXACTLY from the input step's own "
                    "\"key\" field — never invented, never a step number. kind must be exactly one "
                    "of: rename_binding, parameterize_literal, suggest_optional, label_phase, "
                    "suggest_assertion, flag_noise.\n"
                    "- rename_binding: this step's input_binding collides with another field's "
                    "meaning (e.g. two fields both named email_2) — proposed is a clearer "
                    "lowercase_snake_case name, current is the existing binding.\n"
                    "- parameterize_literal: this step's typed value is a literal that should "
                    "probably vary per run (a customer name, amount, reference number) — proposed "
                    "is a lowercase_snake_case name for the new input, current is the literal value.\n"
                    "- suggest_optional: ONLY for a step whose has_optional_hint is true — proposed "
                    "is \"true\" if this looks like a genuinely optional interstitial (cookie banner, "
                    "occasional dialog) or \"false\" if it looks required despite the hint.\n"
                    "- label_phase: proposed is exactly one of login, navigate, act, verify, cleanup "
                    "describing which phase of the workflow this step belongs to.\n"
                    "- suggest_assertion: a LATER step's outcome is the real proof an EARLIER step "
                    "worked (e.g. a confirmation page's text is the true sign a submit succeeded). "
                    "step_key is the EARLIER step. proposed is a JSON string "
                    "{\"type\": ..., \"target\": ...} where type is exactly one of text_present, "
                    "text_absent, url_changed, url_pattern, state_changed. target must be text or a "
                    "URL fragment that ALREADY APPEARS in this workflow's own target_text/intent/url "
                    "fields (never invent text) — empty target for state_changed and url_changed, non-empty "
                    "otherwise. Never selector_present/selector_absent/value_equals — you must never "
                    "write a page selector.\n"
                    "- flag_noise: ONLY for a step whose post_condition_effect is exactly \"none\" "
                    "(the recorder itself observed no effect) AND whose action is click, hover, "
                    "scroll, or focus AND which has no input_binding and no required assertion — "
                    "never flag a step from your own judgment alone. Exception: a navigate step may "
                    "be flagged duplicate_action when the step immediately before it is ALSO a "
                    "navigate to the identical url on the identical tab — e.g. one navigate the "
                    "recording itself did, followed by another to the exact same page. proposed is "
                    "exactly one of duplicate_action, no_op_action, orphaned_hover. This step will be "
                    "removed from the shipped skill (archived, not deleted), so only propose it when "
                    "you are sure.\n"
                    "Prefer steps marked low_confidence — the compiler was least sure about those. "
                    "Use sibling_bindings (names other workflows for the same site already use) to "
                    "pick names, not guesses. Return an EMPTY suggestions array when nothing is "
                    "clearly wrong — most workflows should get few or zero suggestions. No markdown, "
                    "no extra keys, no suggestion whose step_key you are not certain about."
                ),
            },
            {"role": "user", "content": json.dumps(data or payload, ensure_ascii=False)},
        ]
    # BUILD-29: workflow_intent and workflow_semantics above are retained-for-reference only —
    # their two calls were merged into the one multimodal call below (BUILD-26), and neither
    # task name is sent by any caller any more. Their text is the source the merged prompt was
    # built from, and their modules (workflow_intent.py::_graph_from_raw,
    # workflow_semantics.py::_validate_findings) still own response parsing/validation.
    if task == "workflow_review":
        # Compile-time: one multimodal call that replaces the two calls above — same contracts,
        # merged into one response object, plus (new) each step may carry the screenshot the
        # vision-anchor stage already chose for it. Image trimming/budgeting happens upstream
        # in compiler/build.py::_review_inputs, before this payload is built — this branch only
        # renders whatever images survive that trim; it does not decide which ones to drop.
        steps_in = list((data or {}).get("steps") or [])
        page_urls = (data or {}).get("page_urls")
        sibling_bindings = (data or {}).get("sibling_bindings")
        # Strip images out of the JSON text block — they ride along as separate image_url
        # blocks below instead. Sending them both ways would double the payload for nothing
        # and (before this branch existed at all) was silently serializing raw base64 as text.
        text_steps = [
            {k: v for k, v in s.items() if k not in ("image_base64", "image_mime")}
            for s in steps_in
            if isinstance(s, dict)
        ]
        text_payload = {"steps": text_steps, "page_urls": page_urls, "sibling_bindings": sibling_bindings}
        task_description = str((data or {}).get("task_description") or "").strip()
        if task_description:
            # What the person said the workflow is for. Context for naming and phases only: it
            # never licenses a selector, a new step, or a different goal than the recording shows.
            text_payload["task_description"] = task_description
            # BUILD-55: the "does it match what I said, and which recorded values only belong to this
            # recording?" check rides on this call instead of being a second one. Only asked when
            # there is a description to compare against.
            review_candidates = (data or {}).get("candidates")
            if review_candidates:
                text_payload["candidates"] = review_candidates
                text_payload["ends_with_checks"] = (data or {}).get("ends_with_checks") or []
        loop_candidates = (data or {}).get("loop_candidates")
        if loop_candidates:
            text_payload["loop_candidates"] = loop_candidates
        content: list[dict[str, Any]] = [
            {"type": "text", "text": json.dumps(text_payload, ensure_ascii=False)},
        ]
        for s in steps_in:
            if not isinstance(s, dict):
                continue
            image_b64 = str(s.get("image_base64") or "")
            if not image_b64:
                continue
            mime = str(s.get("image_mime") or "image/jpeg")
            content.append({"type": "text", "text": f"--- Step: {s.get('key')} ---"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_b64}"},
            })
        return [
            {
                "role": "system",
                "content": (
                    "You build a workflow intent graph from a sequence of recorded actions AND "
                    "review the same workflow for improvements that will be applied "
                    "automatically, so only report ones you are confident about. Some steps are "
                    "followed by a labelled screenshot (\"--- Step: <key> ---\") showing the "
                    "target at the moment it was recorded — use it as extra context, never as "
                    "the only evidence for a finding.\n\n"
                    "If the input has task_description, it is what the person said this workflow "
                    "is for. Use it only to pick better names, goals and phases; the recorded "
                    "steps remain the source of truth and it never justifies a step that was "
                    "not recorded.\n\n"
                    "Return strict JSON with keys:\n"
                    "goal (one sentence), steps (array of {index, intent_token, intent, "
                    "verification_anchor}), decision_points (array of {step_index, "
                    "description}), expected_end_state (object with brief description). For "
                    "each step: intent_token must be one specific snake_case action token "
                    "describing the user goal for that control (verb + object, lowercase, "
                    "underscores only — e.g. click_sign_in_button, enter_email_value, "
                    "navigate_to_dashboard); intent is a short readable sentence describing "
                    "what that step does.\n\n"
                    "Write goal and every intent in plain everyday words a non-technical person "
                    "understands, with a medium level of detail: start with a verb, at most about "
                    "12 words, and name what the person sees on screen. Describe a typed, "
                    "chosen or clicked value by what it is (\"the file\", \"the order number\"), "
                    "never by its recorded text, because it may change on every run. Avoid words like "
                    "navigate, initiate, observe, trigger, element, DOM. Examples: \"Open the "
                    "GitHub gitignore page\", \"Click Download to save the file\".\n\n"
                    "suggestions (array of {step_key, kind, current, proposed, why}). step_key "
                    "must be copied EXACTLY from the input step's own \"key\" field — never "
                    "invented, never a step number. kind must be exactly one of: "
                    "rename_binding, parameterize_literal, suggest_optional, label_phase, "
                    "suggest_assertion, flag_noise.\n"
                    "- rename_binding: this step's input_binding collides with another field's "
                    "meaning (e.g. two fields both named email_2) — proposed is a clearer "
                    "lowercase_snake_case name, current is the existing binding.\n"
                    "- parameterize_literal: this step's typed value is a literal that should "
                    "probably vary per run (a customer name, amount, reference number) — proposed "
                    "is a lowercase_snake_case name for the new input, current is the literal value.\n"
                    "- suggest_optional: ONLY for a step whose has_optional_hint is true — proposed "
                    "is \"true\" if this looks like a genuinely optional interstitial (cookie banner, "
                    "occasional dialog) or \"false\" if it looks required despite the hint.\n"
                    "- label_phase: proposed is exactly one of login, navigate, act, verify, cleanup "
                    "describing which phase of the workflow this step belongs to.\n"
                    "- suggest_assertion: a LATER step's outcome is the real proof an EARLIER step "
                    "worked (e.g. a confirmation page's text is the true sign a submit succeeded). "
                    "step_key is the EARLIER step. proposed is a JSON string "
                    "{\"type\": ..., \"target\": ...} where type is exactly one of text_present, "
                    "text_absent, url_changed, url_pattern, state_changed. target must be text or a "
                    "URL fragment that ALREADY APPEARS in this workflow's own target_text/intent/url "
                    "fields (never invent text) — empty target for state_changed and url_changed, non-empty "
                    "otherwise. Never selector_present/selector_absent/value_equals — you must never "
                    "write a page selector.\n"
                    "- flag_noise: ONLY for a step whose post_condition_effect is exactly \"none\" "
                    "(the recorder itself observed no effect) AND whose action is click, hover, "
                    "scroll, or focus AND which has no input_binding and no required assertion — "
                    "never flag a step from your own judgment alone. NEVER flag a step whose "
                    "\"causes\" field is set (file_download or file_chooser) — that step is SUPPOSED "
                    "to leave the page looking unchanged; it starts a download or opens a file "
                    "picker, which the page itself doesn't visibly react to, so a \"none\" effect "
                    "there is expected, not evidence of a no-op. Exception: a navigate step may be "
                    "flagged duplicate_action when the step immediately before it is ALSO a navigate "
                    "to the identical url on the identical tab — e.g. one navigate the recording "
                    "itself did, followed by another to the exact same page. proposed is exactly one "
                    "of duplicate_action, no_op_action, orphaned_hover. This step will be removed "
                    "from the shipped skill (archived, not deleted), so only propose it when you are "
                    "sure.\n"
                    "Prefer steps marked low_confidence — the compiler was least sure about those. "
                    "Use sibling_bindings (names other workflows for the same site already use) to "
                    "pick names, not guesses. Return an EMPTY suggestions array when nothing is "
                    "clearly wrong — most workflows should get few or zero suggestions.\n\n"
                    "ONLY IF the input has candidates, also return a key understanding: "
                    "{\"goal_summary\": \"one sentence\", \"coverage\": {\"ok\": true|false, "
                    "\"note\": \"\"}, \"values\": [{\"ref\": \"<a candidate ref>\", \"verdict\": "
                    "\"fixed|varies|unsure\", \"name\": \"short_snake_case_input_name (varies/unsure "
                    "only)\", \"literal\": \"for url candidates only: the exact part of the recorded "
                    "address that changes\"}]}. candidates are values taken from the recording "
                    "(kind \"url\": a page address, \"row\": a record identifier, \"click\": a "
                    "clicked item's label); ends_with_checks is what the run ends by checking. "
                    "Decide for each candidate whether it is specific to this recording. \"varies\": "
                    "task_description describes a kind of thing, or does not name this one — which "
                    "repository, which file, which customer — so a future run would use a different "
                    "one. \"fixed\": task_description names this exact thing, or it is simply the "
                    "same page every time (a settings page, a sign-in page). \"unsure\": you cannot "
                    "tell; do not guess. Also judge coverage: ok is false only when task_description "
                    "clearly asks for an outcome the steps never reach (for example it says send and "
                    "the steps stop at save draft); note is then one plain sentence, at most 25 "
                    "words. Never invent a ref and never claim a gap you cannot see in the steps.\n\n"
                    "ONLY IF the input has loop_candidates, also return a key loop_copy: an array of "
                    "{\"id\": the loop candidate's id, \"text\": string}. Each loop candidate is a "
                    "recording that downloads one specific file and later uploads that same file "
                    "somewhere else; its steps are tagged role \"acquire\" or \"upload\". Write 1-2 "
                    "short, friendly sentences for a non-technical reviewer, describing ONLY that "
                    "download-then-upload mechanism (never a delete, never anything the steps do not "
                    "show), ending with a question that Yes/No buttons for turning this single fixed "
                    "file into a loop over any number of caller-named files answer.\n\n"
                    "Never write or invent a page selector anywhere in this response. No markdown, "
                    "no keys other than the ones described here, no suggestion whose step_key you "
                    "are not certain about."
                ),
            },
            {"role": "user", "content": content},
        ]
    if task == "anchor_vision":
        image_b64 = str(payload.get("image_base64") or "")
        mime = str(payload.get("image_mime") or "image/jpeg")
        user_text = str(payload.get("user_text") or "")
        # NVIDIA Gemma 4 VLMs: put image before text for best multimodal behavior (NIM docs).
        return [
            {
                "role": "system",
                "content": (
                    "Return strict JSON with keys: "
                    "primary_phrase (short string describing the highlighted control), "
                    "secondary (array of objects with keys element and relation only). "
                    "relation must be one of: inside, above, below, near. "
                    "For above/below, relation is the highlighted target's position relative to the anchor. "
                    "No markdown, no extra keys."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                    {"type": "text", "text": user_text},
                ],
            },
        ]
    if task == "anchor_vision_frameset":
        # Compile-time, one step per call: 5 time-offset frames of the SAME moment
        # (T-500/-250/0/+250/+500ms around the recorded action), each with the same
        # highlight box drawn on it (the box's presence/absence/occlusion across frames
        # IS the signal). The model picks which frame best shows pre-action state and
        # writes one descriptive sentence — one call, since it has to look at all 5
        # frames to do either job. Replaces the old anchor_vision_batch (N steps, 1
        # image each, structured primary/secondary JSON) — the new unit of batching is
        # concurrency across steps (anchor_vision_llm.py), not images packed per call.
        frames = payload.get("frames")
        frames = frames if isinstance(frames, list) else []  # [{"label","image_base64","image_mime"}]
        content: list[dict[str, Any]] = []
        for item in frames:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            mime = str(item.get("image_mime") or "image/jpeg")
            image_b64 = str(item.get("image_base64") or "")
            content.append({"type": "text", "text": f"--- Frame: {label} ---"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{image_b64}"},
            })
        user_text = str(payload.get("user_text") or "")
        content.append({"type": "text", "text": user_text})
        return [
            {
                "role": "system",
                "content": (
                    "You will see 5 screenshots of the SAME UI moment, taken at different times "
                    "relative to a user action: before_far (0.5s before), before_near (0.25s "
                    "before), at (the instant of the action), after_near (0.25s after), after_far "
                    "(0.5s after). Each has a red highlighted box marking the target element's "
                    "recorded screen position — on early or late frames the box may fall on empty "
                    "space, a different element, or be partly covered (e.g. before the page "
                    "rendered it, or after a modal opened over it); that is expected and useful "
                    "signal, not an error.\n\n"
                    "Return strict JSON with exactly two keys:\n"
                    "chosen_frame: which label (before_far|before_near|at|after_near|after_far) "
                    "best shows the page's state right BEFORE the action was taken — the target "
                    "clearly visible, not yet clicked/opened/covered, closest available to the "
                    "action without being contaminated by its effect. Prefer before_near unless "
                    "another frame is clearly better (e.g. before_near is mid-animation or the "
                    "target has not rendered yet).\n"
                    "anchor_sentence: one plain-English sentence (max ~200 characters) describing "
                    "the highlighted target and where it sits on the page, written for someone who "
                    "cannot see the screenshot — e.g. \"The blue Save button in the top-right "
                    "toolbar, next to the Cancel button.\" No DOM jargon (no div/container/element). "
                    "No markdown, no extra keys, no commentary outside the JSON object."
                ),
            },
            {"role": "user", "content": content},
        ]
    if task == "region_selector":
        # Re-target wizard: user drew a fresh bbox with no stored per-element geometry to
        # resolve it against. The image (highlighted region) + DOM snippet together let the
        # model see which element the box points to and read the DOM to select it.
        image_b64 = str(payload.get("image_base64") or "")
        mime = str(payload.get("image_mime") or "image/jpeg")
        user_text = str(payload.get("user_text") or "")
        return [
            {
                "role": "system",
                "content": (
                    "You generate Playwright locator strings for web automation. The user "
                    "message contains a screenshot with a red highlighted region marking the "
                    "target element, followed by a snippet of the page's DOM HTML. Find the "
                    "element the highlighted region points to in the DOM, then return strict "
                    "JSON with key 'candidates' (array of objects: selector (string), rank "
                    "(1=best), rationale (short string), intent (snake_case action description)). "
                    "Selector priority — use the first that applies:\n"
                    "1. [data-testid=\"x\"] — most stable\n"
                    "2. [aria-label=\"x\"] — when element has an explicit aria-label attribute\n"
                    "3. button:has-text(\"x\"), a:has-text(\"x\") — tag + visible text for buttons and links\n"
                    "4. input[name=\"x\"], select[name=\"x\"], textarea[name=\"x\"] — name attr for form controls only\n"
                    "5. [placeholder=\"x\"] — for text inputs with placeholder\n"
                    "6. #stable-id — non-hashed, non-generated IDs\n"
                    "7. text=\"x\" — Playwright text locator (exact, case-sensitive) as last text-based resort\n"
                    "Critical rules:\n"
                    "- NEVER write [role=\"button\"] or [role=\"x\"] — CSS [role] only matches the raw HTML "
                    "attribute, not the ARIA role; plain <button> and <a> elements do NOT have a role "
                    "attribute in HTML\n"
                    "- NEVER write [name=\"x\"] on buttons, divs, spans, or links — the name attribute only "
                    "exists on form controls (input/select/textarea) and form elements\n"
                    "- Selectors MUST match exactly one element in the provided DOM snippet\n"
                    "- Avoid: hashed classes, auto-generated IDs, nth-of-type chains, XPath\n"
                    "No markdown, no extra keys."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                    {"type": "text", "text": user_text},
                ],
            },
        ]
    if task == "copilot_reply":
        # Streamed sibling of copilot_diagnose (BUILD-26 stage c): same evidence/transcript
        # framing, but asks for prose only — no JSON contract — because a streamed JSON object
        # would show the reviewer a raw `{"reply": "...` scrolling past. copilot_turn() still
        # runs the existing copilot_diagnose call afterward for proposals; this call never
        # proposes anything itself.
        image_b64 = str(payload.get("image_base64") or "")
        mime = str(payload.get("image_mime") or "image/jpeg")
        user_text = str(payload.get("user_text") or "")
        system = (
            "You are a diagnosis-and-repair copilot inside a browser-automation workflow "
            "reviewer (Human Review). You are shown evidence about one compiled workflow and, "
            "when relevant, why one of its steps failed a test run: the compiler's own "
            "confidence and identity signals, the recorded page's before/after DOM diff, the "
            "deterministic recovery cascade's trail, a live element inventory, and — when "
            "attached — a screenshot taken at the moment of failure. Diagnose from this "
            "evidence: cite the actual cascade stage or page state involved, never a generic "
            "restatement of the error message. Answer the reviewer's question directly, in "
            "plain English, as a short, specific paragraph. Do not return JSON, markdown, or "
            "any wrapper — plain prose only."
        )
        user_content: list[dict[str, Any]] | str
        if image_b64:
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                {"type": "text", "text": user_text},
            ]
        else:
            user_content = user_text
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    if task == "copilot_diagnose":
        # BUILD-26 stage (b), widened at stage (g): Human Review's copilot. One call per turn —
        # there is no multi-turn session on this path, so the Studio flattens the whole
        # conversation (capability manifest + workflow digest + transcript + the reviewer's
        # latest message, and any retrieval "Fetched:" blocks from a prior round of this same
        # turn) into user_text each time; this function stays stateless either way. The
        # screenshot is optional: a text-only turn (no vision provider in the pool, or no
        # screenshot available) is still a valid call — just without the image content block,
        # per the graceful-degrade contract in conxa_compile/llm/copilot.py.
        image_b64 = str(payload.get("image_base64") or "")
        mime = str(payload.get("image_mime") or "image/jpeg")
        user_text = str(payload.get("user_text") or "")
        system = (
            "You are a diagnosis-and-repair copilot inside a browser-automation workflow "
            "reviewer (Human Review). Your first message carries a CAPABILITY MANIFEST (what "
            "step kinds exist, which ones need a page target, which fields are patchable on "
            "each, and a one-line note on what the runtime actually does with each kind) and a "
            "WORKFLOW DIGEST (every step, compactly — description, intent, phase, consequence, "
            "compile confidence, whether it has assertions/a branch/a loop; NOT its full "
            "selector/identity detail). Treat the manifest as the ground truth for what is "
            "possible — never propose something it does not list as insertable or patchable for "
            "that kind, and never assume a runtime behavior the manifest's runtime_note "
            "contradicts (e.g. recovery.max_attempts and no_recovery_block are never read by the "
            "runtime — do not propose them as a fix). When the digest lacks the detail you need "
            "for a specific step (its selectors, a screenshot, its full recorded event, its full "
            "edit history) or you need the list of other workflows in this workspace, ASK for it "
            "via \"need\" rather than guessing.\n"
            "Return strict JSON with keys: reply (a short, specific plain-English answer), need "
            "(array, usually empty — see below), and proposals (array, usually empty — most "
            "turns propose nothing).\n"
            "need entries are {tool, args}, tool one of: expand_step (args: {step_key}) — full "
            "target/identity_bundle/compiled_selectors/validation/handler_hints for one step; "
            "get_step_screenshot (args: {step_key}) — that step's recorded screenshot; "
            "get_failure_evidence (args: {run_id?}) — the runtime's failure capture for a run "
            "(omit run_id for the workflow's most recent); get_edit_history (args: {step_key?}) "
            "— prior reviewer/copilot edits; list_workflows (args: {}) — every workflow in this "
            "workspace, for cross-workflow naming consistency. You will receive the results as a "
            "\"Fetched:\" block and get one more turn to answer or propose — do not repeat the "
            "same need twice.\n"
            "Three kinds of proposal exist, and EVERY proposal must carry evidence_refs (array of "
            "step_key / overlay_id / tool-name strings naming what it is derived from) — a "
            "proposal with no evidence_refs is dropped before the reviewer ever sees it:\n"
            "1. A field edit: {step_key, field, patch, why, evidence_refs}. step_key copied "
            "EXACTLY from a step's own \"step_key\" — never invented, never a step number. field "
            "is one of: value, input_binding, intent, semantic_description, "
            "validation.assertions, consequence, entity_binding.confirmed, branch.timeout_ms, "
            "for_each.max_iterations, for_each.on_row_error, handler_hints.hover_chain, or (for "
            "ai_review/handover steps only) one of the ai_review_*/handover_* fields the manifest "
            "lists for that kind. patch is the new value for that field.\n"
            "2. A conditional-branch insertion, ONLY when the reviewer asks about a popup/overlay "
            "and the digest's observed_overlays (fetch via get_failure_evidence if not already "
            "shown) lists one: {overlay_id, control_index, primitive, after_step_key, why, "
            "evidence_refs}. overlay_id copied EXACTLY from observed_overlays[].overlay_id — "
            "never invented. control_index is the index into that overlay's own \"controls\" "
            "array. primitive is \"try_dismiss\" or \"if_present\". after_step_key is the "
            "step_key to insert after, or omit to insert at the end.\n"
            "3. A structural op — insert/delete/move a step, or a workflow-level edit: "
            "{op, why, evidence_refs, ...}. op is one of:\n"
            "   - insert_step: {action_kind, after_step_key, fields?, identity_from_step_key?}. "
            "action_kind must be in the manifest's insertable_kinds. If action_kind is in the "
            "manifest's selector_kinds (it needs a page target — e.g. hover, click), you MUST "
            "set identity_from_step_key to an EXISTING step_key whose element this new step "
            "should act on — you never invent a selector for a kind that needs one; for a kind "
            "NOT in selector_kinds (ai_review, handover, wait, check, assert, navigate, scroll, "
            "screenshot) omit identity_from_step_key entirely. fields is an optional {field: "
            "value} map of the same fields listed in (1), applied right after insert.\n"
            "   - delete_step: {step_key}.\n"
            "   - move_step: {step_key, after_step_key}.\n"
            "   - parameterize: {literal, name}. Use THIS — never update_inputs or a "
            "validation.assertions edit — whenever the reviewer wants a recorded value to change "
            "per run (a clicked item's label, a page address, a row identifier, a file name). "
            "literal is the recorded value copied exactly from the digest; name is the new input "
            "id (letters, digits, underscore; starts with a letter). Code finds every place that "
            "value appears, rewrites the steps and their checks, and declares the input with the "
            "recorded value as its default.\n"
            "   - update_inputs: {inputs}. inputs is the FULL replacement input list; each row is "
            "{id, label?, type?, default?} where id is required (letters, digits, underscore) — "
            "there is no \"name\" or \"value\" key. Never use it to make a recorded value "
            "variable (use parameterize); only to edit an input that already exists.\n"
            "   - replace_literals: {find, replace}. Only for a literal value the reviewer named "
            "explicitly — never a guess at a value that merely looks similar.\n"
            "You must NEVER propose target, identity_bundle, compiled_selectors, primary_selector, "
            "fallback_selectors, recovery, no_recovery_block, or a raw for_each/branch \"steps\" "
            "array in any proposal of any kind — you never write or change a page selector or a "
            "loop/branch body directly; a human re-targeting a step in the editor does that, and a "
            "branch/structural insertion's own selectors are built deterministically from what you "
            "referenced, not by you. Never re-propose something the digest's prior_decisions "
            "already shows as rejected for the same step/field unless the reviewer asks again. "
            "Only propose a change you are confident about and can justify from evidence actually "
            "shown to you — never invent text, a selector, or a DOM fact not present above. No "
            "markdown, no extra keys."
        )
        user_content: list[dict[str, Any]] | str
        if image_b64:
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                {"type": "text", "text": user_text},
            ]
        else:
            user_content = user_text
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    if task == "ai_review":
        # EXEC-13: the Build Studio sandbox's stand-in answerer for an ai_review checkpoint when
        # no MCP agent is present to answer it (runtime/app/review_pause.js's park/resume request
        # is otherwise unanswerable under Studio's tier-2 ceiling). Deliberately NOT in
        # VISION_TASKS above — the text-model route is tried first (multimodal providers accept
        # the image on either route; only the *model selected* differs), with a caller-side
        # fallback to route_vision on a provider rejection (see
        # handlers/workflows.py::_answer_ai_review). user_text already carries the prompt, the
        # runtime's live-DOM inventory text, and the output_schema — assembled once, not
        # duplicated in the system prompt.
        image_b64 = str(payload.get("image_base64") or "")
        mime = str(payload.get("image_mime") or "image/jpeg")
        user_text = str(payload.get("user_text") or "")
        return [
            {
                "role": "system",
                "content": (
                    "You are answering one reasoning checkpoint inside a browser-automation "
                    "workflow. The user message names the question and shows the current page as "
                    "a screenshot, its live DOM elements, and a JSON Schema your answer must "
                    "match. Look at the page and answer the question. Return ONLY a JSON object "
                    "conforming to the given schema — no markdown, no extra keys, no commentary "
                    "outside the object. When no schema is given, return a JSON object with a "
                    "single \"answer\" string field."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    {"type": "text", "text": user_text},
                ],
            },
        ]
    if task in _STUDIO_CHAT_SYSTEMS:
        # Build Studio's conversation harness (BUILD-37): general Chat and each workflow's
        # thread. The Studio builds the whole context (lifecycle, pinned facts, digest, rolling
        # summary, recent turns) into user_text every turn, because this call path is stateless.
        return [
            {"role": "system", "content": _STUDIO_CHAT_SYSTEMS[task]},
            {"role": "user", "content": str(payload.get("user_text") or "")},
        ]
    if task == "execute_chat":
        # Conxa Execute's multi-turn tool-calling chat — the one task whose
        # payload already IS an OpenAI-shaped messages array (system prompt +
        # full history), built client-side by the desktop app's own turn
        # loop. Every other task above builds its own single-turn prompt
        # from payload["input"]/["prompt"]; this is the deliberate exception.
        messages = payload.get("messages")
        return list(messages) if isinstance(messages, list) else []
    return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _openai_body_dict(task: str, payload: dict[str, Any], *, json_mode: bool) -> dict[str, Any]:
    resolved_model = _resolved_model(task, payload)
    messages = _openai_messages_for_task(task, payload)
    body: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": 0.0,
    }
    if task == "anchor_vision":
        # Short JSON anchors; VLMs often expect an explicit ceiling (see NVIDIA Gemma chat examples).
        body["max_tokens"] = 1024
    if task == "anchor_vision_frameset":
        # One label + one short sentence, not primary+secondary JSON — smaller than
        # the 1024 budgeted for the old structured single-image task.
        body["max_tokens"] = 300
    if task == "region_selector":
        # A handful of selector candidates with rationale — a bit more room than anchor_vision.
        body["max_tokens"] = 1536
    if task == "workflow_review":
        # A whole-workflow intent graph AND a suggestions array in one response — the largest
        # structured JSON any task here returns, well above region_selector's handful of
        # candidates.
        body["max_tokens"] = 6144  # was 4096; the description check and loop wording ride on this call now
    if task == "copilot_diagnose":
        # BUILD-33: the cloud router (conxa-cloud/backend/app/llm/router.py) sends
        # reasoning={"enabled": False} to OpenRouter for every task, so a reasoning-capable
        # routed model shouldn't spend completion tokens on hidden chain-of-thought at all. That
        # flag is a request, not a guarantee some models keep reasoning anyway — 2048 was
        # observed live to be entirely consumed by OpenRouter's z-ai/glm-5.3-flash doing exactly
        # that, returning finish_reason="length" with an empty JSON answer. 16384 gives a full
        # chain-of-thought room to finish on its own before the answer even starts, not just a
        # bigger cap around an ignored flag.
        body["max_tokens"] = 16384
    if task == "copilot_reply":
        # Prose only, no proposals JSON riding along — the answer itself needs a fraction of
        # this. Same headroom as copilot_diagnose above and for the same reason: a stream that
        # yields zero content deltas (because a model kept reasoning despite the router's
        # reasoning={"enabled": False}) reaches the Studio as "the proxy produced no result"
        # rather than as an empty answer, which is worse than copilot_diagnose's case.
        body["max_tokens"] = 16384
    if task == "ai_review":
        # A small structured answer (yes/no + a short reason, or similar) — same order as
        # region_selector's handful of candidates.
        body["max_tokens"] = 1024
    if task == "studio_chat_reply":
        # Streamed prose: same headroom as copilot_reply, for the same hidden-reasoning reason.
        body["max_tokens"] = 16384
    if task in ("chat_map_answer", "studio_chat_actions", "chat_route_intent"):
        body["max_tokens"] = 1024
    if task in ("workflow_understanding", "record_plan"):
        body["max_tokens"] = 2048
    if task == "thread_summarize":
        body["max_tokens"] = 2048
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    # Tool-calling passthrough — only execute_chat's caller-built payload ever
    # sets these, so this is a no-op for every other task's payload shape.
    if payload.get("tools"):
        body["tools"] = payload["tools"]
        if payload.get("tool_choice") is not None:
            body["tool_choice"] = payload["tool_choice"]
    return body


def _extract_json_object_substring(raw: str) -> str | None:
    lb = raw.find("{")
    if lb < 0:
        return None
    depth = 0
    for i in range(lb, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return raw[lb : i + 1]
    return None


def _parse_json_object_content(content: str) -> dict[str, Any] | None:
    s = content.strip()
    try:
        p = json.loads(s)
        if isinstance(p, dict):
            return p
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", s, re.I)
    if fence:
        try:
            p = json.loads(fence.group(1))
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    sub = _extract_json_object_substring(s)
    if sub:
        try:
            p = json.loads(sub)
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return None


def _provider_top_level_error(data: dict[str, Any]) -> str | None:
    err = data.get("error")
    if err is None:
        return None
    if isinstance(err, dict):
        msg = err.get("message")
        typ = err.get("type") or err.get("code")
        parts = [str(p) for p in (msg, typ) if p]
        return ": ".join(parts) if parts else json.dumps(err, ensure_ascii=False)[:280]
    if isinstance(err, str):
        return err
    return json.dumps(err, ensure_ascii=False)[:280]


def _normalize_openai_response(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return data
    first = choices[0]
    if not isinstance(first, dict):
        return data
    message = first.get("message")
    content = ""
    if isinstance(message, dict):
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            # Some providers emit content as multimodal fragments.
            chunks: list[str] = []
            for part in raw_content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chunks.append(str(part.get("text") or ""))
            content = "".join(chunks).strip()
        else:
            content = str(raw_content or "").strip()
    if not content:
        content = str(first.get("text") or "").strip()
    if not content:
        return data
    parsed = _parse_json_object_content(content)
    if parsed is not None:
        return parsed
    try:
        p = json.loads(content)
        if isinstance(p, dict):
            return p
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"text": content, "output": content}


def _sse_content_to_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for part in raw:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _sse_choice_text(first: Any) -> str:
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    raw = None
    if isinstance(delta, dict):
        raw = delta.get("content")
        if raw is None:
            raw = delta.get("text")
    if not raw:
        message = first.get("message")
        if isinstance(message, dict):
            raw = message.get("content")
    return _sse_content_to_text(raw)


def _sse_choice_reasoning(first: Any) -> str:
    """Like _sse_choice_text but for the hidden chain-of-thought delta a reasoning-capable
    routed model streams before (sometimes instead of) any content — OpenRouter's
    ``delta.reasoning``, the DeepSeek/GLM-family ``delta.reasoning_content`` spelling. Used only
    to detect "this stream carried tokens but zero content" (BUILD-33), never surfaced to a
    caller as text."""
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    raw = delta.get("reasoning")
    if raw is None:
        raw = delta.get("reasoning_content")
    return _sse_content_to_text(raw)


def _iter_sse_text_deltas(response: Any, *, observed: dict[str, Any] | None = None) -> Any:
    """Parse an OpenAI-compatible ``stream: true`` response, yielding each content delta as it
    arrives. `response` is the file-like object returned by ``urlopen`` — iterating it yields
    one raw HTTP chunk-line at a time. Skips non-``data:`` lines (SSE comments/blank keep-alives)
    and non-content deltas (role markers, finish_reason); never raises on a malformed line.

    ``observed``, when given, is filled in place with ``reasoning_chars`` (running total of
    hidden chain-of-thought characters seen) and ``finish_reason`` (the last non-null one seen) —
    lets a caller tell a genuinely empty stream apart from one that spent its whole budget on
    reasoning and wrote no content (BUILD-33)."""
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            continue
        choices = obj.get("choices") if isinstance(obj, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0]
        if observed is not None:
            reasoning = _sse_choice_reasoning(first)
            if reasoning:
                observed["reasoning_chars"] = observed.get("reasoning_chars", 0) + len(reasoning)
            finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
            if finish_reason:
                observed["finish_reason"] = finish_reason
        text = _sse_choice_text(first)
        if text:
            yield text


def _sse_choice_tool_call_deltas(first: Any) -> list[dict[str, Any]]:
    if not isinstance(first, dict):
        return []
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return []
    calls = delta.get("tool_calls")
    if not isinstance(calls, list):
        return []
    out = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn_raw = call.get("function")
        fn: dict[str, Any] = fn_raw if isinstance(fn_raw, dict) else {}
        out.append({
            "index": call.get("index", 0),
            "id": call.get("id"),
            "name": fn.get("name"),
            "arguments_delta": fn.get("arguments"),
        })
    return out


def _iter_sse_deltas(response: Any, *, observed: dict[str, Any] | None = None) -> Any:
    """Tagged sibling of _iter_sse_text_deltas for tasks that need tool-call
    deltas too (currently only execute_chat) — yields
    {"type": "text", "text": ...}, {"type": "tool_call", ...}, or
    {"type": "reasoning", "text": ...} per chunk instead of a bare string, so
    a caller can tell them apart. The "reasoning" chunk carries hidden
    chain-of-thought text (see _sse_choice_reasoning) for a caller that wants
    to surface it live (Conxa Execute's chat "thinking" display) — unlike
    _iter_sse_text_deltas, which only counts these chars via `observed` and
    never yields them. Kept as a separate function rather than changing
    _iter_sse_text_deltas' return shape, so every existing caller
    (copilot_reply, etc., which never request tools or reasoning) is
    completely unaffected."""
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            continue
        choices = obj.get("choices") if isinstance(obj, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0]
        if observed is not None:
            finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
            if finish_reason:
                observed["finish_reason"] = finish_reason
        reasoning = _sse_choice_reasoning(first)
        if reasoning:
            if observed is not None:
                observed["reasoning_chars"] = observed.get("reasoning_chars", 0) + len(reasoning)
            yield {"type": "reasoning", "text": reasoning}
        text = _sse_choice_text(first)
        if text:
            yield {"type": "text", "text": text}
        for call in _sse_choice_tool_call_deltas(first):
            yield {"type": "tool_call", **call}


def _next_api_key(keys: list[str]) -> tuple[str, int, int]:
    """Get first available API key from the provided list."""
    if not keys:
        return "", 0, 0
    return keys[0], 1, len(keys)


def _decode_http_error_body(exc: error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _openai_complete_request(
    ep: str,
    raw_body: bytes,
    headers: dict[str, str],
    timeout_s: float,
    *,
    attempt_tag: str,
    req_id: int,
    task: str,
    error_detail: list[str] | None,
    sink_lock: threading.Lock | None,
) -> dict[str, Any] | None:
    req = request.Request(ep, data=raw_body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_s) as res:
            raw = res.read().decode("utf-8")
    except error.HTTPError as exc:
        bod = _decode_http_error_body(exc)
        snippet = _safe_error_snippet(bod or str(exc.reason or exc))
        _append_llm_detail(
            error_detail,
            f"HTTPError {exc.code} ({attempt_tag}): {snippet}",
            sink_lock=sink_lock,
        )
        return None
    except (error.URLError, TimeoutError, OSError) as exc:
        _append_llm_detail(
            error_detail,
            f"{type(exc).__name__} ({attempt_tag}): {exc}",
            sink_lock=sink_lock,
        )
        return None

    try:
        data_raw = json.loads(raw)
    except json.JSONDecodeError as exc:
        snippet = _safe_error_snippet(raw)
        pos = getattr(exc, "pos", None)
        loc = f"@{pos}" if pos is not None else ""
        _append_llm_detail(
            error_detail,
            f"JSONDecodeError ({attempt_tag}) body{loc}: {snippet}",
            sink_lock=sink_lock,
        )
        return None

    if not isinstance(data_raw, dict):
        _append_llm_detail(
            error_detail,
            f"unexpected_json_root ({attempt_tag}): {type(data_raw).__name__}",
            sink_lock=sink_lock,
        )
        return None

    prov_msg = _provider_top_level_error(data_raw)
    if prov_msg:
        _append_llm_detail(
            error_detail,
            f"provider_error ({attempt_tag}): {prov_msg}",
            sink_lock=sink_lock,
        )
        return None

    data = _normalize_openai_response(data_raw)

    _debug_log(
        "response_received "
        f"req_id={req_id} task={task} attempt={attempt_tag} status_ok"
    )
    return data if isinstance(data, dict) else None


def _parallel_anchor_vision_first_success(
    *,
    keys: list[str],
    ep: str,
    raw_body: bytes,
    timeout_s: float,
    attempt_tag: str,
    req_id: int,
    task: str,
    error_detail: list[str] | None,
) -> dict[str, Any] | None:
    """One HTTP POST per API key; return the first successful parsed body."""
    if len(keys) < 2:
        return None
    detail_lock = threading.Lock()
    max_workers = min(32, len(keys))
    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        futs = []
        for i, api_key in enumerate(keys):
            slot = i + 1
            hdrs: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                hdrs["Authorization"] = f"Bearer {api_key}"
            label = f"key{slot}/{len(keys)} {attempt_tag}"
            futs.append(
                ex.submit(
                    _openai_complete_request,
                    ep,
                    raw_body,
                    hdrs,
                    timeout_s,
                    attempt_tag=label,
                    req_id=req_id,
                    task=task,
                    error_detail=error_detail,
                    sink_lock=detail_lock,
                )
            )
        pending = set(futs)
        # Wall clock: allow all in-flight urllib timeouts to resolve, plus small slack.
        wall_deadline = time.monotonic() + timeout_s + 15.0
        while pending:
            now = time.monotonic()
            wait_timeout = min(1.0, max(0.05, wall_deadline - now))
            if wait_timeout <= 0:
                break
            done, pending = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
            for fut in done:
                try:
                    out = fut.result()
                except Exception as exc:  # noqa: BLE001
                    _append_llm_detail(
                        error_detail,
                        f"worker_error ({attempt_tag}): {type(exc).__name__}: {exc}",
                        sink_lock=detail_lock,
                    )
                    continue
                if out is not None:
                    try:
                        ex.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        ex.shutdown(wait=False)
                    return out
        return None
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
