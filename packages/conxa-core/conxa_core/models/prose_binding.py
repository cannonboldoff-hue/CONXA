"""Keep a workflow's prose in line with its inputs.

A recorded value ("Angular.gitignore") gets written into free-text prose by the compiler's model.
When the person later makes that value a run input, every sentence that names it has to say
``{{input}}`` instead, or the workflow reads as if it were hard-coded. That used to be patched
per step inside whichever mutation made the input; ``bind_prose`` is the one rule, applied wherever
a skill document is saved (``storage.json_store.write_skill``).

Only prose is touched: a step's ``semantic_description``, the intent graph's goal and step intents,
and ``meta.description``. Addresses (``target``, ``identity_bundle``, ``compiled_selectors``,
``url``, ``value``, assertions) belong to their own mutators and are never read here.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

MIN_LITERAL = 4          # a shorter recorded value ("1", "yes") is too likely to be an ordinary word
_TOKEN = re.compile(r"(\{\{[^}]*\}\})")
_TOKEN_IN_PROSE = re.compile(r"(\b[Tt]he\s+)?\{\{\s*(\w+)\s*\}\}")


def _bindings(doc: dict[str, Any]) -> list[tuple[re.Pattern[str], str]]:
    """(pattern for the recorded literal, ``{{id}}``) for every input a step actually uses."""
    used = json.dumps(doc.get("skills") or [], ensure_ascii=False)
    out: list[tuple[str, str]] = []
    for row in doc.get("inputs") or []:
        if not isinstance(row, dict):
            continue
        iid, default = str(row.get("id") or ""), row.get("default")
        if (not iid or iid == "file_path" or row.get("type") == "file" or not isinstance(default, str)
                or default == "{{REDACTED}}" or "{{" + iid + "}}" not in used):
            continue
        literal = " ".join(default.split())
        if len(literal) >= MIN_LITERAL:
            out.append((literal, "{{" + iid + "}}"))
    out.sort(key=lambda p: -len(p[0]))
    return [(re.compile(r"(?<!\w)" + re.escape(lit) + r"(?!\w)", re.IGNORECASE), tok) for lit, tok in out]


def _bind(text: str, pairs: list[tuple[re.Pattern[str], str]]) -> str:
    # existing {{tokens}} are left alone: an input named like its own default must not nest
    parts = _TOKEN.split(text)
    for n in range(0, len(parts), 2):
        for pat, tok in pairs:
            parts[n] = pat.sub(lambda _m, t=tok: t, parts[n])
    return "".join(parts)


def _walk_descriptions(node: Any, pairs: list[tuple[re.Pattern[str], str]]) -> None:
    """Every ``semantic_description`` anywhere under the steps, whatever shape the nesting takes
    (branch options, loop bodies, outcome-fork states)."""
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "semantic_description" and isinstance(val, str):
                node[key] = _bind(val, pairs)
            else:
                _walk_descriptions(val, pairs)
    elif isinstance(node, list):
        for item in node:
            _walk_descriptions(item, pairs)


def bind_prose(doc: dict[str, Any]) -> dict[str, Any]:
    """The document with each input's recorded value replaced by its ``{{id}}`` in prose.
    Pure and idempotent; returns ``doc`` itself when there is nothing to do."""
    if not isinstance(doc, dict):
        return doc
    pairs = _bindings(doc)
    if not pairs:
        return doc
    out = copy.deepcopy(doc)
    _walk_descriptions(out.get("skills"), pairs)
    graph = out.get("intent_graph")
    if isinstance(graph, dict):
        if isinstance(graph.get("goal"), str):
            graph["goal"] = _bind(graph["goal"], pairs)
        for s in graph.get("steps") or []:
            if isinstance(s, dict) and isinstance(s.get("intent"), str):
                s["intent"] = _bind(s["intent"], pairs)
    meta = out.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("description"), str):
        meta["description"] = _bind(meta["description"], pairs)
    return out


def readable(text: str) -> str:
    """``{{github_file_name}}`` -> ``“github file name”`` for a person; a leading "the" is dropped so
    "the {{x}} file" does not read "the the x file". Every surface that shows prose uses this."""
    return _TOKEN_IN_PROSE.sub(lambda m: f"“{m.group(2).replace('_', ' ').lower()}”", text)
