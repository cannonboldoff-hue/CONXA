"""Studio-side client for the Human Review Copilot's diagnosis-and-repair task (BUILD-26 stage b).

One call per conversation turn — there is no multi-turn *session* on this call path
(`ProxyBody` carries no `messages` field — every task builds a fresh `[system, user]` pair per
call), so the whole conversation lives in the renderer and is flattened into one payload each
turn; this module stays stateless either way. Two LLM calls make up one turn (BUILD-26 stage c):
a streamed `copilot_reply` call for the prose the reviewer reads live, and the existing
`copilot_diagnose` call for proposals — kept as two calls, not one, because `copilot_diagnose`'s
JSON-object contract can't be streamed cleanly (see `conxa_core.llm.client`'s `copilot_reply` and
`copilot_diagnose` branches for both prompts).

BUILD-26 stage g adds two things to the `copilot_diagnose` side only (never to the streamed
`copilot_reply`, which stays prose-only so the reviewer sees text immediately):
  - the capability manifest (`editor/capability_manifest.py`) and a workflow DIGEST
    (`evidence.py`'s `detail="digest"`) replace the old always-full evidence dump, so the model
    knows what the runtime can execute and sees every step, but not every step's heavy fields;
  - a bounded `need[]` retrieval loop — the model asks for detail (`editor/copilot_retrieval.py`)
    instead of receiving it all up front, capped at `MAX_RETRIEVAL_ROUNDS` extra calls per turn.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from conxa_core.config import settings

from conxa_compile.editor.capability_manifest import build_capability_manifest
from conxa_compile.editor.copilot_retrieval import MAX_RETRIEVAL_ROUNDS, resolve_need
from conxa_compile.llm.anchor_vision_llm import _bounded_jpeg_bytes
from conxa_compile.llm.client import call_llm, stream_llm
from services.llm_proxy_client import (
    CloudUnreachable,
    EntitlementBlocked,
    ProxyUnavailable,
    QuotaExceeded,
)

_CAPABILITY_MANIFEST_TEXT: str | None = None


def _capability_manifest_text() -> str:
    """Cached module-level (BUILD-26 stage g's L0) — the manifest only changes when the Studio
    build does, so there is no reason to rebuild or re-serialize it every turn."""
    global _CAPABILITY_MANIFEST_TEXT
    if _CAPABILITY_MANIFEST_TEXT is None:
        _CAPABILITY_MANIFEST_TEXT = json.dumps(build_capability_manifest(), ensure_ascii=False, default=str)
    return _CAPABILITY_MANIFEST_TEXT


def _format_transcript(transcript: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for turn in transcript:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "user").strip() or "user"
        text = str(turn.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _llm_reply_text(data: dict[str, Any] | None) -> str:
    """Pull the reviewer's prose out of whatever envelope the model/router handed back.

    Streamed `copilot_reply` is `{text, output}`. JSON `copilot_diagnose` is `{reply, proposals}`.
    Providers that ignore json_object mode fall through `_normalize_openai_response` as
    `{text, output}` with no `reply` key — that is still the answer, not a failed turn."""
    if not isinstance(data, dict):
        return ""
    for key in ("reply", "text", "output"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _encode_screenshot(path: str | None) -> str | None:
    """Read + downscale a screenshot for the vision call, matching
    region_selector_vision.py's own read → downscale → base64 shape exactly. Missing/unreadable
    is the same as "no screenshot" — the graceful-degrade case, never an error."""
    if not path:
        return None
    try:
        import base64
        from pathlib import Path

        raw = Path(path).read_bytes()
        return base64.standard_b64encode(_bounded_jpeg_bytes(raw)).decode("ascii")
    except Exception:  # noqa: BLE001 — an unreadable image degrades to text-only, never an error
        return None


def _base_user_text(digest: dict[str, Any], transcript: list[dict[str, Any]], message: str) -> str:
    user_text = (
        "Capability manifest — what you may propose and what the runtime does with each step "
        "kind (this never changes mid-conversation, no need to ask about it):\n"
        f"{_capability_manifest_text()}\n\n"
        "Workflow digest — every step, compact (ask for expand_step/get_step_screenshot/"
        "get_failure_evidence/get_edit_history/list_workflows via \"need\" for anything not "
        "shown here):\n"
        f"{json.dumps(digest, ensure_ascii=False, default=str)}\n\n"
    )
    # BUILD-26 stage h: a null failed_step_key / empty runtime_evidence means EITHER the run
    # passed OR it failed but left no evidence.json (stale app layer, retention sweep). Those
    # look identical in the digest otherwise — without this line the model has inferred a step
    # failure from unrelated fields (a stale compile report, an advanced-only step kind) rather
    # than say it couldn't see the page. last_test.error is the one fact that always survives.
    absent_reason = digest.get("runtime_evidence_absent")
    if absent_reason:
        last_test = digest.get("last_test") or {}
        user_text += (
            f"No runtime evidence is available for this turn: {absent_reason}. "
            "Treat last_test.error (if present) as the authoritative account of what failed — "
            f"last_test: {json.dumps(last_test, ensure_ascii=False, default=str)}. "
            "Do not infer a different failing step or cause from compile_status, step kinds, or "
            "anything else in the digest; say plainly that the failing page could not be "
            "inspected for this turn.\n\n"
        )
    history = _format_transcript(transcript)
    if history:
        user_text += f"Conversation so far:\n{history}\n\n"
    user_text += f"Reviewer's message: {message}"
    return user_text


def copilot_turn(
    *,
    skill_id: str = "",
    evidence: dict[str, Any],
    transcript: list[dict[str, Any]],
    message: str,
    screenshot_path: str | None = None,
    model: str | None = None,
    on_delta: Callable[[str], None] | None = None,
    user_text: str | None = None,
) -> dict[str, Any]:
    """One turn of the Human Review copilot conversation.

    ``user_text`` (BUILD-37): a caller that already assembled the whole context under its own
    token budget — the conversation harness — passes it here and ``evidence`` / ``transcript`` /
    ``message`` are not re-flattened. Everything after that (retrieval rounds, the screenshot,
    gating by the caller) is unchanged.

    Two calls, not one (BUILD-26 stage c): a streamed ``copilot_reply`` call produces the prose
    the reviewer reads live via ``on_delta`` (skipped — falls back to the non-streamed reply
    below — when ``on_delta`` is None, e.g. an older caller that hasn't wired streaming yet), and
    the existing ``copilot_diagnose`` call still runs for proposals (its own ``reply`` field is
    ignored once a streamed reply exists, since it's the same answer generated twice — kept
    only as the reply's fallback source when streaming is unavailable or came back empty).

    BUILD-26 stage g: ``evidence`` is now the compact DIGEST (`evidence.py`'s
    ``detail="digest"``), not the old always-full bundle — every step, no heavy fields. The
    ``copilot_diagnose`` call (proposals) may come back with a ``need[]`` array asking for detail
    on specific steps/screenshots/history; that is resolved via `editor/copilot_retrieval.py` and
    fed back for up to ``MAX_RETRIEVAL_ROUNDS`` extra rounds before the final proposals are
    returned. The streamed ``copilot_reply`` call never asks for or waits on retrieval — the
    reviewer's prose answer must not stall on it.

    Returns ``{"reply": str, "proposals": list[dict]}``. Graceful-empty on any failure —
    matching `workflow_intent.py` / `workflow_semantics.py` — **except** QuotaExceeded /
    EntitlementBlocked / CloudUnreachable, which propagate so the caller
    (`handlers/copilot.py`) can surface an actionable message ("your AI Usage Credits are
    exhausted") instead of the panel going silently mute — the same carve-out
    `region_selector_vision.py` makes.
    """
    base_user_text = user_text if user_text is not None else _base_user_text(evidence, transcript, message)

    payload: dict[str, Any] = {"user_text": base_user_text}
    image_b64 = _encode_screenshot(screenshot_path)
    if image_b64:
        payload["image_base64"] = image_b64
        payload["image_mime"] = "image/jpeg"
    else:
        # No screenshot on disk, or no vision provider will ever see it either way — say so
        # explicitly rather than silently sending a bare question, per the plan's graceful-
        # degrade note ("diagnosis quality drops; nothing breaks").
        base_user_text += "\n\n(No failure screenshot is available for this turn.)"
        payload["user_text"] = base_user_text
    if model:
        payload["model"] = model

    streamed_parts: list[str] = []
    if on_delta is not None:
        def _forward_delta(chunk: str) -> None:
            if chunk:
                streamed_parts.append(chunk)
            on_delta(chunk)

        stream_err_lines: list[str] = []
        try:
            stream_result = stream_llm(
                "copilot_reply", payload, settings.llm_vision_timeout_ms,
                on_delta=_forward_delta, error_detail=stream_err_lines,
            )
        except ProxyUnavailable:
            # Must sit ABOVE the infra tuple — ProxyUnavailable subclasses CloudUnreachable, and
            # here it means something different: the proxy answered, the streaming endpoint just
            # produced no text. A reasoning model that spends its whole completion budget on hidden
            # chain-of-thought before writing content emits zero content deltas and lands here,
            # even though the provider recorded a complete generation. `copilot_diagnose` below is
            # a separate, non-streamed request with its own budget that routinely succeeds when
            # this one didn't, so degrade to it instead of killing the turn — otherwise the
            # reviewer's question sits in the panel with no answer and no explanation. A genuinely
            # unreachable cloud still propagates: the diagnose call raises it moments later.
            stream_result = None
        except (QuotaExceeded, EntitlementBlocked, CloudUnreachable):
            raise
        except Exception:  # noqa: BLE001 — streaming failure falls back to the non-streamed reply below
            stream_result = None
        streamed_reply = "".join(streamed_parts).strip() or _llm_reply_text(
            stream_result if isinstance(stream_result, dict) else None
        )
    else:
        streamed_reply = ""

    diagnose_payload = dict(payload)
    fetched_blocks: list[str] = []
    data: dict[str, Any] | None = None
    for round_num in range(MAX_RETRIEVAL_ROUNDS + 1):
        err_lines: list[str] = []
        try:
            data = call_llm(
                "copilot_diagnose", diagnose_payload, settings.llm_vision_timeout_ms, error_detail=err_lines
            )
        except (QuotaExceeded, EntitlementBlocked, CloudUnreachable):
            raise
        except Exception:  # noqa: BLE001 — any other failure degrades to an empty turn, never a crash
            data = None

        if not isinstance(data, dict):
            break
        needs = data.get("need")
        needs = [n for n in needs if isinstance(n, dict)] if isinstance(needs, list) else []
        if not needs or round_num == MAX_RETRIEVAL_ROUNDS:
            break
        # BUILD-26 stage g: resolve every requested tool call and feed the results back as one
        # more "Fetched:" block on the SAME base text — never a new conversation turn, never
        # touching the transcript the reviewer sees.
        fetched = {json.dumps(n, ensure_ascii=False, default=str): resolve_need(skill_id, n) for n in needs}
        fetched_blocks.append(
            "Fetched (in response to your need[]):\n" + json.dumps(fetched, ensure_ascii=False, default=str)
        )
        diagnose_payload = dict(diagnose_payload)
        diagnose_payload["user_text"] = str(payload.get("user_text") or "") + "\n\n" + "\n\n".join(fetched_blocks)

    if not isinstance(data, dict):
        return {"reply": streamed_reply, "proposals": []}

    reply = streamed_reply or _llm_reply_text(data)
    raw_proposals = data.get("proposals")
    proposals = [p for p in raw_proposals if isinstance(p, dict)] if isinstance(raw_proposals, list) else []
    return {"reply": reply, "proposals": proposals}
