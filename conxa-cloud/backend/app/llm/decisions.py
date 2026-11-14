"""Decision-model client for the Proof-of-Done picker (the cloud half of ``POST /llm/proxy/decide``).

One request carries shared evidence plus several yes/no questions; each comes back with a probability that
the answer is yes. The model writes no text. Request shape is OpenRouter's alpha Decisions API: ``model``,
``state`` (a list of text parts) and ``questions`` (``{name: {type: "noul", instructions, criteria}}``); the
response holds ``answers`` as ``{name: {noul: probability}}``. Studio sends only value-free step facts (see
``conxa_compile/compiler/proof_picker.py``); this module never trusts the shape it is handed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from conxa_core.config import settings

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 40
MAX_INPUT_CHARS = 8000
MAX_INSTRUCTION_CHARS = 400
MAX_NAME_CHARS = 64


class DecisionsUnavailable(RuntimeError):
    """No key is configured: the picker is off."""


def clean_questions(raw: Any) -> list[dict[str, str]]:
    """Keep well-formed ``{name, instructions}`` pairs, bounded; drop the rest. Every question is sent as a ``noul``."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        name = str(q.get("name") or "").strip()[:MAX_NAME_CHARS]
        text = str(q.get("instructions") or "").strip()[:MAX_INSTRUCTION_CHARS]
        if not name or not text or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "instructions": text})
        if len(out) >= MAX_QUESTIONS:
            break
    return out


def parse_probabilities(data: Any, asked: set[str]) -> dict[str, float]:
    """``{name: probability}`` for every asked question the model answered. A refusal, an unknown name or a
    probability outside 0..1 is dropped, never guessed."""
    answers = data.get("answers") if isinstance(data, dict) else None
    out: dict[str, float] = {}
    for name, a in (answers.items() if isinstance(answers, dict) else []):
        p = a.get("noul") if isinstance(a, dict) else None
        if name in asked and isinstance(p, (int, float)) and not isinstance(p, bool) and 0.0 <= float(p) <= 1.0:
            out[str(name)] = float(p)
    return out


def decide(payload: dict[str, Any], timeout_s: float = 30.0) -> dict[str, Any] | None:
    """``{"probabilities": {name: p}}``, or None when the provider failed. Raises DecisionsUnavailable with no key."""
    key = settings.llm_decisions_api_key.strip()
    if not key:
        raise DecisionsUnavailable("decisions_not_configured")
    questions = clean_questions(payload.get("questions"))
    text = str(payload.get("input") or "")[:MAX_INPUT_CHARS]
    if not questions or not text:
        return {"probabilities": {}}
    url = settings.llm_decisions_endpoint.rstrip("/") + "/" + settings.llm_decisions_path.lstrip("/")
    body = {
        "model": settings.llm_decisions_model,
        "state": [text],
        "questions": {
            q["name"]: {"type": "noul", "instructions": q["instructions"], "criteria": {"true": "Yes", "false": "No"}}
            for q in questions
        },
    }
    try:
        resp = httpx.post(url, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("decisions_call_failed error=%s", exc)
        return None
    return {"probabilities": parse_probabilities(data, {q["name"] for q in questions})}
