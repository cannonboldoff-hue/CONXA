"""Free text → one of the actions the lifecycle allows right now (gap analysis G2).

Deterministic first: a message that is exactly one action's phrase needs no model. Otherwise, and
only when the message looks like a command, one short constrained ``chat_route_intent`` call
decides. The model can only choose an id that is in ``allowed`` (anything else is "chat"), and any
slot it fills is checked against what code knows — an input name from the workflow, a workflow id
from the person's own list. A wrong guess therefore degrades to the ordinary conversational reply,
never to an action nobody could take.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from conxa_compile.conversation.actions import ACTIONS, routable

# A message with none of these is conversation, so it never costs a routing call.
_COMMANDISH = re.compile(
    r"\b(record|compile|test|run|try|approve|stop|cancel|sign|log|publish|edit|again|redo|finish|"
    r"done|discard|fix|start|ask|question|retry|open|go|show|take)\b", re.I)
_OPEN_WORDS = re.compile(r"\b(open|go to|take me|show me|switch to|jump to|work on)\b", re.I)
MAX_COMMAND_CHARS = 200

Caller = Callable[[str, dict[str, Any], int], "dict[str, Any] | None"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", str(text or "").lower())).strip()


def _by_name(text: str, workflows: list[dict[str, Any]]) -> dict[str, Any] | None:
    needle = normalize(text)
    hits = [w for w in workflows if normalize(w.get("name") or "") and normalize(w["name"]) in needle]
    return hits[0] if len(hits) == 1 else None


def route_intent(
    text: str,
    allowed: list[str],
    *,
    known_inputs: Callable[[], list[str]] | None = None,
    workflows: list[dict[str, Any]] | None = None,
    call: Caller | None = None,
    timeout_ms: int = 15_000,
) -> dict[str, Any] | None:
    """``{"action", "slots", "source"}`` or None (= just talk). Never raises."""
    options = routable(allowed)
    if not options or not text.strip():
        return None
    norm = normalize(text)

    if "open_workflow" in allowed and workflows and _OPEN_WORDS.search(text):
        hit = _by_name(text, workflows)
        if hit is not None:
            return {"action": "open_workflow", "slots": {"workflow_id": hit["id"], "name": hit.get("name") or ""}, "source": "name"}

    phrased = [o.id for o in options if norm in ACTIONS[o.id].phrases]
    if len(phrased) == 1:
        return {"action": phrased[0], "slots": {}, "source": "phrase"}
    if len(norm) > MAX_COMMAND_CHARS or not _COMMANDISH.search(norm):
        return None

    inputs: list[str] = []
    if known_inputs is not None and "test" in allowed:
        try:
            inputs = [str(i) for i in known_inputs()][:20]
        except Exception:  # noqa: BLE001 -- a hint only
            inputs = []
    listing = [f"{o.id}: {o.describe}" for o in options]
    body: dict[str, Any] = {"actions": listing, "input_names": inputs, "message": text.strip()[:MAX_COMMAND_CHARS]}
    if workflows and "open_workflow" in allowed:
        body["workflows"] = [{"id": w["id"], "name": w.get("name")} for w in workflows[:50]]
    try:
        if call is None:
            from conxa_compile.llm.client import call_llm as call  # noqa: PLC0415
        data = call("chat_route_intent", {"user_text": json.dumps(body, ensure_ascii=False)}, timeout_ms)
    except Exception:  # noqa: BLE001 -- routing is a convenience; the conversational reply still works
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip()
    if action not in {o.id for o in options}:
        return None  # "chat", unclear, or an id that is not allowed right now
    slots: dict[str, Any] = {}
    raw = data.get("slots") if isinstance(data.get("slots"), dict) else {}
    if action == "test" and isinstance(raw.get("inputs"), dict):
        slots["inputs"] = {k: str(v) for k, v in raw["inputs"].items() if k in inputs and isinstance(v, (str, int, float))}
    if action == "open_workflow":
        hit = next((w for w in workflows or [] if w["id"] == raw.get("workflow_id")), None)
        if hit is None:
            return None
        slots = {"workflow_id": hit["id"], "name": hit.get("name") or ""}
    return {"action": action, "slots": slots, "source": "model"}
