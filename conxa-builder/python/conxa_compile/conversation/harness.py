"""The chat harness: one turn loop for general Chat and every workflow's thread (BUILD-37).

A turn:

1. **Route deterministically first.** An answer to an open card (a click, or free text mapped
   to one of the card's allowed choices by one short model call) is applied by the same code
   path either way, and never needs the conversational model. Only a free-form message falls
   through to it.
2. **Build the context** (``context_pack.py``) under an explicit token budget — the proxy is
   stateless, so every turn rebuilds it.
3. **Propose, then stream the reply** (``studio_chat_reply``). When the turn may produce edits,
   a separate proposal call runs first (the Human Review copilot's ``copilot_diagnose``) and
   **every proposal goes through the existing gates** before it can be offered. The model
   never writes a selector, and a proposal that fails a gate is silently dropped. The reply is
   then written knowing exactly which gated changes are on offer, and they become ONE
   pending-change card (one reply, one change set, one Apply).
4. Append everything to the thread. When the verbatim window overflows, fold the oldest turns
   into a stored rolling summary.

Turns are serialized per thread: a second message while one streams is queued, never
interleaved. Cancelling stops the stream and appends nothing half-finished.

This module knows nothing about the Backend — card actions (apply a change, create a workflow)
are executed by a callback the command handler supplies.
"""

from __future__ import annotations

import re
import threading
import uuid
from typing import Any, Callable

from conxa_core.config import settings

from conxa_compile.conversation import thread_store as ts
from conxa_compile.conversation.context_pack import (
    DEFAULT_CHAT_BUDGET,
    DEFAULT_WORKFLOW_BUDGET,
    build_context_pack,
)

Sink = Callable[[dict[str, Any]], None]
CardExecutor = Callable[[dict[str, Any], str, dict[str, Any]], dict[str, Any]]
# (action id, slots) -> {"ok": bool, "message": str}. Runs one of actions.ACTIONS through the same
# backend commands the card buttons use; never raises.
FlowExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


class TurnCancelled(Exception):
    """Raised inside the stream callback to stop a turn the person cancelled."""


# ── per-thread serialization and cancellation ───────────────────────────────────────────────

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_cancel: dict[str, threading.Event] = {}


def _thread_lock(thread_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(thread_id, threading.Lock())


def cancel_turn(turn_id: str) -> bool:
    """Ask a queued or streaming turn to stop. Returns False for an unknown turn id."""
    with _locks_guard:
        ev = _cancel.get(turn_id)
    if ev is None:
        return False
    ev.set()
    return True


# ── emitting ────────────────────────────────────────────────────────────────────────────────


def emit_item(sink: Sink | None, thread_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Tell the renderer a thread item appeared or a card was revised."""
    if sink is not None:
        sink({"phase": "thread_item", "thread_id": thread_id, "item": item})
    return item


def log_event(thread_id: str, text: str, *, glyph: str = "ok", sink: Sink | None = None, **fields: Any) -> dict[str, Any]:
    """The system's one-line voice: what actually happened, with a glyph and a timestamp."""
    return emit_item(sink, thread_id, ts.append(thread_id, "event", text=text, glyph=glyph, **fields))


def post_card(thread_id: str, card_type: str, state: str, *, sink: Sink | None = None, **kw: Any) -> dict[str, Any]:
    card = ts.append_card(thread_id, card_type, state, **kw)
    return emit_item(sink, thread_id, _as_folded(card))


def update_card(thread_id: str, card_id: str, *, sink: Sink | None = None, **kw: Any) -> dict[str, Any] | None:
    card = ts.revise_card(thread_id, card_id, **kw)
    if card is not None:
        emit_item(sink, thread_id, card)
    return card


def _as_folded(card: dict[str, Any]) -> dict[str, Any]:
    out = dict(card)
    out["created_ts"] = card.get("ts")
    out["updated_ts"] = card.get("ts")
    return out


# ── pinned facts ────────────────────────────────────────────────────────────────────────────

_WS = re.compile(r"\s+")


def _step_name(row: dict[str, Any]) -> str:
    name = str(row.get("description") or row.get("intent") or row.get("step_key") or "this step")
    return _WS.sub(" ", name).strip()[:80]


def pinned_facts(digest: dict[str, Any] | None, clarify: dict[str, Any] | None, task: str = "") -> list[str]:
    """Everything the person decided or the workflow depends on, as short lines. These ride in
    every context pack and are never summarized away. ``task`` is what the person said the
    workflow is for (``Workflow.task_description``): the chat model needs the goal, not just the steps."""
    facts: list[str] = []
    if task.strip():
        facts.append(f"What the person said this workflow is for: {task.strip()[:300]}")
    for q in (clarify or {}).get("questions") or []:
        if isinstance(q, dict) and q.get("status") == "answered" and q.get("receipt"):
            facts.append(f"{q.get('title')}: {q.get('receipt')}")
    if digest:
        irreversible = [_step_name(s) for s in digest.get("steps") or [] if isinstance(s, dict) and s.get("consequence") == "irreversible"]
        if irreversible:
            facts.append("Steps that can't be undone: " + ", ".join(f"“{n}”" for n in irreversible))
        reviews = [_step_name(s) for s in digest.get("steps") or [] if isinstance(s, dict) and s.get("action_kind") == "ai_review"]
        if reviews:
            facts.append("Confirmation / review points: " + ", ".join(f"“{n}”" for n in reviews))
        inputs = digest.get("inputs")
        if isinstance(inputs, list) and inputs:
            names = [str(i.get("name") or i.get("id")) for i in inputs if isinstance(i, dict)]
            if names:
                facts.append("Run inputs: " + ", ".join(names))
    return facts


# ── helpers ─────────────────────────────────────────────────────────────────────────────────

_CHANGE_WORDS = re.compile(
    r"\b(fix|change|edit|make|set|add|remove|delete|rename|wait|retry|update|replace|instead|"
    r"wrong|doesn'?t|isn'?t|fails?|failed|should|shouldn'?t|skip|insert|move|reorder|input)\b", re.I,
)


def wants_proposals(message: str, *, has_skill: bool, anchor: str | None, last_test_failed: bool) -> bool:
    """Run the (extra, metered) proposal call only when the message could plausibly ask for a
    change. A plain question gets the streamed reply alone."""
    if not has_skill:
        return False
    return bool(anchor) or last_test_failed or bool(_CHANGE_WORDS.search(message or ""))


def thread_turns(items: list[dict[str, Any]], *, after_id: str | None = None) -> list[dict[str, Any]]:
    """User/assistant messages as ``{id, role, text}``, oldest first, skipping anything the latest
    summary already covers."""
    started = after_id is None
    out: list[dict[str, Any]] = []
    for it in items:
        if not started:
            if it.get("id") == after_id:
                started = True
            continue
        kind = it.get("kind")
        if kind in ("user_message", "assistant_message") and str(it.get("text") or "").strip():
            out.append({"id": it.get("id"), "role": "user" if kind == "user_message" else "assistant", "text": it["text"]})
    return out


def _timeout() -> int:
    return int(getattr(settings, "llm_vision_timeout_ms", 120_000))


# ── the turn ────────────────────────────────────────────────────────────────────────────────


def run_turn(
    *,
    thread_id: str,
    message: str,
    sink: Sink | None,
    turn_id: str | None = None,
    anchor: str | None = None,
    card_id: str | None = None,
    choice: str | None = None,
    action: str | None = None,
    workflow: dict[str, Any] | None = None,
    skill_id: str | None = None,
    lifecycle: dict[str, Any] | None = None,
    group_names: list[str] | None = None,
    card_executor: CardExecutor | None = None,
    gate_proposals: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    load_digest: Callable[[], dict[str, Any] | None] | None = None,
    load_clarify: Callable[[], dict[str, Any] | None] | None = None,
    last_test_failed: bool = False,
    flow_executor: FlowExecutor | None = None,
    workflows: list[dict[str, Any]] | None = None,
    context_sections: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Run one turn. Returns ``{"turn_id", "status": "done"|"cancelled", "reply", "items"}``.

    Raises the proxy's entitlement/quota/cloud errors unchanged — the command handler maps them
    to the error cards in ``States``. A turn that raises appends nothing but the person's own
    message (already shown), so Try again is always safe.
    """
    kind, _ident = ts.parse_thread_id(thread_id)
    turn_id = turn_id or f"turn_{uuid.uuid4().hex[:10]}"
    cancel = threading.Event()
    with _locks_guard:
        _cancel[turn_id] = cancel
    lock = _thread_lock(thread_id)
    new_items: list[dict[str, Any]] = []

    def push(item: dict[str, Any]) -> dict[str, Any]:
        new_items.append(item)
        return item

    try:
        if not lock.acquire(blocking=False):
            if sink is not None:
                sink({"phase": "thread_turn", "thread_id": thread_id, "turn_id": turn_id, "status": "queued"})
            lock.acquire()
        try:
            if cancel.is_set():
                return {"turn_id": turn_id, "status": "cancelled", "reply": "", "items": []}
            return _run_locked(
                thread_id=thread_id, kind=kind, message=message, sink=sink, turn_id=turn_id, cancel=cancel,
                anchor=anchor, card_id=card_id, choice=choice, action=action, workflow=workflow, skill_id=skill_id,
                lifecycle=lifecycle, group_names=group_names, card_executor=card_executor,
                gate_proposals=gate_proposals, load_digest=load_digest, load_clarify=load_clarify,
                last_test_failed=last_test_failed, push=push, new_items=new_items,
                flow_executor=flow_executor, workflows=workflows, context_sections=context_sections,
            )
        finally:
            lock.release()
    finally:
        with _locks_guard:
            _cancel.pop(turn_id, None)


def _run_locked(*, thread_id: str, kind: str, message: str, sink: Sink | None, turn_id: str,
                cancel: threading.Event, anchor: str | None, card_id: str | None, choice: str | None,
                action: str | None, workflow: dict[str, Any] | None, skill_id: str | None,
                lifecycle: dict[str, Any] | None, group_names: list[str] | None,
                card_executor: CardExecutor | None, gate_proposals: Any, load_digest: Any, load_clarify: Any,
                last_test_failed: bool, push: Callable[[dict[str, Any]], dict[str, Any]],
                new_items: list[dict[str, Any]], flow_executor: FlowExecutor | None = None,
                workflows: list[dict[str, Any]] | None = None,
                context_sections: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    if sink is not None:
        sink({"phase": "thread_turn", "thread_id": thread_id, "turn_id": turn_id, "status": "started"})

    message = (message or "").strip()
    raw = ts.read_raw(thread_id)
    items = ts.fold(raw)

    # 1 ─ deterministic routing: an answer to an open card never needs the conversational model.
    if card_id and (choice or action):
        card = next((i for i in items if i.get("card_id") == card_id), None)
        if card is None:
            raise ValueError(f"No card {card_id}")
        if message:
            push(emit_item(sink, thread_id, ts.append(thread_id, "user_message", text=message)))
        result = (card_executor or _no_executor)(card, action or "choose", {"choice": choice, "message": message})
        return {"turn_id": turn_id, "status": "done", "reply": "", "items": new_items, "result": result}

    if not message:
        raise ValueError("message is required")

    # The person's own message is on the page the moment it is sent, even if the model fails.
    user_item = push(emit_item(sink, thread_id, ts.append(thread_id, "user_message", text=message, anchor=anchor)))
    items = items + [user_item]

    open_question = next((c for c in reversed(items) if c.get("kind") == "card" and c.get("state") == "open" and c.get("choices")), None)
    if open_question is not None and card_executor is not None and not anchor and open_question.get("multi"):
        # A tick-list can't be answered by one mapped choice (reading "yes" as "tick nothing" would
        # silently change the workflow), so a typed reply only points back at the card.
        item = push(emit_item(sink, thread_id, ts.append(
            thread_id, "assistant_message",
            text="That question has several options — tick the ones you want on the card above, then press its button.")))
        return {"turn_id": turn_id, "status": "done", "reply": item["text"], "items": new_items}
    if open_question is not None and card_executor is not None and not anchor:
        mapped = map_free_text_to_choice(open_question, message)
        if mapped is not None:
            result = card_executor(open_question, "choose", {"choice": mapped, "message": message})
            return {"turn_id": turn_id, "status": "done", "reply": "", "items": new_items, "result": result}
        # Not an answer. It may still be a command ("what are my options" is not, "test it" is) —
        # the lifecycle decides what is allowed, so a blocked action simply isn't routed.
        if flow_executor is not None:
            routed = _route_command(kind, message, lifecycle, load_digest, workflows)
            if routed is not None:
                return _perform_routed(routed, thread_id=thread_id, sink=sink, turn_id=turn_id, push=push,
                                       new_items=new_items, flow_executor=flow_executor)
        # Unclear: say so, keep the question open, and fall through to nothing else — a guess
        # would apply the wrong answer to the workflow.
        item = push(emit_item(sink, thread_id, ts.append(
            thread_id, "assistant_message",
            text="I wasn’t sure which of the options you meant. Pick one on the card above, or say it another way.")))
        return {"turn_id": turn_id, "status": "done", "reply": item["text"], "items": new_items}

    # 1.5 ─ a command in free text ("test it", "approve", "record that again"): one of the actions the
    # lifecycle allows right now, never anything else. Side effects wait for a confirm card.
    if flow_executor is not None and not anchor:
        routed = _route_command(kind, message, lifecycle, load_digest, workflows)
        if routed is not None:
            return _perform_routed(routed, thread_id=thread_id, sink=sink, turn_id=turn_id, push=push,
                                   new_items=new_items, flow_executor=flow_executor)

    # 2 ─ context under a budget.
    digest = load_digest() if load_digest else None
    clarify = load_clarify() if load_clarify else None
    summary_item = ts.latest_summary(items)
    turns = thread_turns(items[:-1], after_id=(summary_item or {}).get("covers_through"))
    budget = DEFAULT_WORKFLOW_BUDGET if kind == "wf" else DEFAULT_CHAT_BUDGET
    extra: list[tuple[str, str]] = []
    if kind == "chat" and group_names is not None:
        extra.append(("Groups in this workspace (read-only)", ", ".join(group_names) or "(none yet)"))
        extra.extend(context_sections or [])
    def make_pack() -> Any:
        return build_context_pack(
            message=message,
            budget_tokens=budget,
            lifecycle=lifecycle,
            open_cards=ts.open_cards(items),
            pinned_facts=pinned_facts(digest, clarify, str((workflow or {}).get("task_description") or "")),
            digest=digest,
            summary=(summary_item or {}).get("text"),
            turns=turns,
            extra_sections=extra,
            anchor=anchor,
        )

    pack = make_pack()

    # 2b ─ proposals, gated, never applied here — and made BEFORE the reply, so the reply describes
    # exactly the changes that are on offer instead of promising ones no card carries.
    proposals: list[dict[str, Any]] = []
    if kind == "wf" and skill_id and gate_proposals is not None and wants_proposals(
        message, has_skill=True, anchor=anchor, last_test_failed=last_test_failed
    ):
        from conxa_compile.editor.copilot_proposals import proposal_summary

        proposals = _propose(message=message, base_pack=pack, digest=digest, skill_id=skill_id,
                             gate_proposals=gate_proposals, cancel=cancel, with_failure_image=last_test_failed)
        if cancel.is_set():
            return {"turn_id": turn_id, "status": "cancelled", "reply": "", "items": new_items}
        offered = "\n".join(f"- {proposal_summary(p)}" for p in proposals) or "(none could be prepared)"
        extra.append(("Changes you are offering — each takes effect only when the person presses Apply", offered))
        pack = make_pack()

    # 3 ─ stream the reply.
    from conxa_compile.llm.client import stream_llm

    parts: list[str] = []

    def on_delta(chunk: str) -> None:
        if cancel.is_set():
            raise TurnCancelled()
        if not chunk:
            return
        parts.append(chunk)
        if sink is not None:
            sink({"phase": "thread_delta", "thread_id": thread_id, "turn_id": turn_id, "text": chunk})

    try:
        result = stream_llm("studio_chat_reply", {"user_text": pack.text}, _timeout(), on_delta=on_delta)
    except TurnCancelled:
        return {"turn_id": turn_id, "status": "cancelled", "reply": "", "items": new_items}
    reply = "".join(parts).strip() or _reply_text(result)
    if cancel.is_set():
        return {"turn_id": turn_id, "status": "cancelled", "reply": "", "items": new_items}

    # 3c ─ general Chat: may the conversation become a draft workflow?
    draft = None
    if kind == "chat" and not cancel.is_set():
        draft = _maybe_draft(pack.text, reply, group_names or [])

    if cancel.is_set():
        return {"turn_id": turn_id, "status": "cancelled", "reply": "", "items": new_items}

    # 4 ─ append. Only now: nothing half-finished is ever written.
    if reply:
        push(emit_item(sink, thread_id, ts.append(thread_id, "assistant_message", text=reply)))
    if proposals:
        # One reply, one change set, one Apply: the changes are applied all together or not at all.
        from conxa_compile.editor.copilot_proposals import proposal_summary

        single = proposals[0] if len(proposals) == 1 else None
        push(post_card(
            thread_id, "pending_change", "open", sink=sink,
            refs={"skill_id": skill_id, "proposal_ids": [p.get("id") for p in proposals]},
            title=_proposal_title(single) if single else "Proposed changes",
            why=(single or {}).get("why") or "", proposals=proposals,
            summaries=[proposal_summary(p) for p in proposals],
            preview=(single or {}).get("preview"), actions=["apply", "dismiss"],
        ))
    if draft:
        push(post_card(thread_id, "draft_workflow", "open", sink=sink, draft=draft, actions=["create_and_record", "edit_details", "dismiss"]))

    if pack.needs_summary:
        _summarize(thread_id, pack.dropped_turns, (summary_item or {}).get("text"), sink)
    return {"turn_id": turn_id, "status": "done", "reply": reply, "items": new_items}


def _route_command(kind: str, message: str, lifecycle: dict[str, Any] | None, load_digest: Any,
                   workflows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    from conxa_compile.conversation.intent_router import route_intent

    if kind == "chat":
        return route_intent(message, ["open_workflow"], workflows=workflows) if workflows else None
    allowed = [str(a) for a in (lifecycle or {}).get("allowed_actions") or []]

    def inputs() -> list[str]:
        rows = ((load_digest() if load_digest else None) or {}).get("inputs")
        return [str(i.get("name") or i.get("id")) for i in rows or [] if isinstance(i, dict) and (i.get("name") or i.get("id"))]

    return route_intent(message, allowed, known_inputs=inputs)


def _perform_routed(routed: dict[str, Any], *, thread_id: str, sink: Sink | None, turn_id: str,
                    push: Callable[[dict[str, Any]], dict[str, Any]], new_items: list[dict[str, Any]],
                    flow_executor: FlowExecutor) -> dict[str, Any]:
    """Act on a routed command. Opening something and answering a question need no confirmation;
    anything with a side effect becomes a ``confirm_action`` card and nothing runs until the
    person presses it."""
    from conxa_compile.conversation.actions import ACTIONS

    spec = ACTIONS[routed["action"]]
    slots = routed.get("slots") or {}

    def say(text: str) -> dict[str, Any]:
        item = push(emit_item(sink, thread_id, ts.append(thread_id, "assistant_message", text=text)))
        return {"turn_id": turn_id, "status": "done", "reply": item["text"], "items": new_items, "routed": spec.id}

    if spec.kind == "nav":
        name = str(slots.get("name") or "")
        push(post_card(thread_id, "action_link", "open", sink=sink, action=spec.id, target=spec.target,
                       title=f"Open “{name}”" if name else spec.label, workflow_id=slots.get("workflow_id") or "",
                       actions=["open"]))
        return {"turn_id": turn_id, "status": "done", "reply": "", "items": new_items, "routed": spec.id}
    if spec.side_effect:
        push(post_card(thread_id, "confirm_action", "open", sink=sink, action=spec.id, label=spec.label,
                       describe=spec.describe, slots=slots, actions=["confirm", "cancel"]))
        return say(f"I can do that: {spec.label.lower()}. Press Confirm on the card to go ahead — nothing has happened yet.")
    result = flow_executor(spec.id, slots)
    return say(str(result.get("message") or ("Done." if result.get("ok", True) else "That didn’t work.")))


def _no_executor(card: dict[str, Any], action: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise ValueError("This card can't be answered right now.")


def _reply_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("reply", "text", "output"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _proposal_title(p: dict[str, Any]) -> str:
    cmd = p.get("command")
    if cmd == "insert_overlay_branch":
        return "Add a step to dismiss a popup"
    if cmd == "structural_op":
        return "Make it a run input" if p.get("op") == "parameterize" else "Change the steps"
    return f"Change “{p.get('field') or 'a step'}”"


# ── the extra model calls ───────────────────────────────────────────────────────────────────


def map_free_text_to_choice(card: dict[str, Any], text: str) -> str | None:
    """Free text → one of the card's allowed choice ids, or None for "unclear". An exact id or
    label match needs no model; otherwise one short ``chat_map_answer`` call decides, and any id
    not on the card is treated as unclear."""
    choices = [c for c in card.get("choices") or [] if isinstance(c, dict) and c.get("id")]
    allowed = {str(c["id"]) for c in choices}
    needle = text.strip().lower()
    for c in choices:
        if needle in (str(c["id"]).lower(), str(c.get("label") or "").strip().lower()):
            return str(c["id"])
    from conxa_compile.llm.client import call_llm

    payload = {"user_text": (
        f"Question: {card.get('title')}\n"
        f"Allowed choices: {[{'id': c['id'], 'label': c.get('label'), 'detail': c.get('detail')} for c in choices]}\n"
        f"The person replied: {text}"
    )}
    data = call_llm("chat_map_answer", payload, min(30_000, _timeout()))
    pick = str((data or {}).get("choice") or "").strip() if isinstance(data, dict) else ""
    return pick if pick in allowed else None


def _propose(*, message: str, base_pack: Any, digest: dict[str, Any] | None, skill_id: str,
             gate_proposals: Any, cancel: threading.Event, with_failure_image: bool = False) -> list[dict[str, Any]]:
    """One ``copilot_diagnose`` call (with the existing bounded need[] retrieval), then every
    proposal through the gates. The reply it also returns is ignored — the streamed one is shown."""
    from conxa_compile.llm.copilot import _capability_manifest_text, copilot_turn

    if cancel.is_set():
        return []
    user_text = (
        "Capability manifest — what you may propose and what the runtime does with each step kind:\n"
        f"{_capability_manifest_text()}\n\n{base_pack.text}"
    )
    # After a failed test the diagnosis gets the screenshot of the failure, not only its description.
    shot = ((digest or {}).get("runtime_evidence") or {}).get("failure_screenshot_path") if with_failure_image else None
    result = copilot_turn(
        skill_id=skill_id, evidence=digest or {}, transcript=[], message=message, on_delta=None, user_text=user_text,
        screenshot_path=shot,
    )
    raw = [p for p in result.get("proposals") or [] if isinstance(p, dict)]
    return gate_proposals(raw)


_URL = re.compile(r"^https?://[^\s/]+\.[^\s/]+", re.I)


def _maybe_draft(context_text: str, reply: str, group_names: list[str]) -> dict[str, Any] | None:
    from conxa_compile.llm.client import call_llm

    data = call_llm(
        "studio_chat_actions",
        {"user_text": f"{context_text}\n\nAssistant just replied: {reply}\nExisting groups: {group_names}"},
        min(30_000, _timeout()),
    )
    draft = (data or {}).get("draft_workflow") if isinstance(data, dict) else None
    if not isinstance(draft, dict):
        return None
    name = str(draft.get("name") or "").strip()
    if not name:
        return None
    group = str(draft.get("group") or "").strip()
    url = str(draft.get("starting_page") or "").strip()
    return {
        "name": name[:80],
        "group": group if group in group_names else "",
        "starting_page": url if _URL.match(url) else "",
        "task": str(draft.get("task") or "").strip()[:300],
    }


def _summarize(thread_id: str, dropped: list[dict[str, Any]], previous: str | None, sink: Sink | None) -> None:
    """Fold the oldest turns into the stored rolling summary. Best effort: a failure here must
    not fail the turn that triggered it."""
    from conxa_compile.llm.client import call_llm

    if not dropped:
        return
    lines = "\n".join(f"{t['role']}: {t['text']}" for t in dropped)
    text = (f"Existing summary:\n{previous}\n\n" if previous else "") + f"Older turns to fold in:\n{lines}"
    try:
        data = call_llm("thread_summarize", {"user_text": text}, min(60_000, _timeout()))
    except Exception:  # noqa: BLE001 — summarizing is housekeeping
        return
    summary = str((data or {}).get("summary") or "").strip() if isinstance(data, dict) else ""
    if summary:
        emit_item(sink, thread_id, ts.append(thread_id, "summary", text=summary, covers_through=dropped[-1].get("id")))
