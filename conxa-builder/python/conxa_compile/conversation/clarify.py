"""Clarify: a few questions after compile, answered through existing mutations (BUILD-32).

The compiler decides, on its own, things only the person who recorded the task knows: is a typed
value fixed or an input, is a pop-up always dismissable or a one-off, may a file upload ship with a
path that exists only on this computer, should a person confirm before something that can't be
undone. This module finds those places in a *compiled* skill document and asks — at most
``MAX_QUESTIONS``, one at a time, never blocking: an unanswered question keeps the compiler's
default.

Two rules this module exists to keep:

- **Questions are not part of the skill.** They live in ``clarify.json`` beside it, so a compile
  nobody answers is byte-identical to one that never asked (the BUILD-25 rule).
- **An answer only ever becomes an existing mutation.** ``plan_answer`` returns a list of ops —
  ``patch`` (the gated ``cmd_patch_step``), ``insert_review``, ``confirm_optional``
  (``confirm_optional_interstitial`` → the shared ``build_try_dismiss_from_hint`` builder),
  ``drop_inputs`` — that the command handler runs through the commands a reviewer would use. The
  model never writes a selector, and no answer reaches ``target`` or ``identity_bundle``.

Everything here is pure (the recorded-event lookup is injected), so it is unit-testable.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from conxa_compile.compiler.step_key import step_keys

MAX_QUESTIONS = 5
MAX_KNOWN_POPUP_QUESTIONS = 2
_ASSERTION_TYPES = {"text_present", "url_changed", "url_pattern", "state_changed"}
_PRIORITY = {
    "coverage": -1, "upload": 0, "fork": 1, "known_popup": 1.5, "confirm": 2, "chosen_values": 2.5,
    "values": 3, "output": 3.5, "popup": 4, "done_when": 5,
}
# Questions whose answer decides what the workflow *is*, so testing waits for them: a test of a
# workflow that might still be incomplete, or that still carries a value from the recording as if
# it were fixed, proves little. Everything else keeps the compiler's default and never blocks.
BLOCKING_KINDS = frozenset({"coverage", "chosen_values", "upload", "fork"})

SourceLookup = Callable[[dict[str, Any]], "dict[str, Any] | None"]


# ── naming ──────────────────────────────────────────────────────────────────────────────────


def _action_name(step: dict[str, Any]) -> str:
    a = step.get("action")
    return str(a if isinstance(a, str) else (a or {}).get("action") or "").lower()


def step_name(step: dict[str, Any]) -> str:
    """What a person would call this step: never a number, never a selector."""
    for key in ("semantic_description", "intent"):
        text = str(step.get(key) or "").strip()
        if text:
            text = re.sub(r"^Step \d+:\s*", "", text)
            if "_" in text and " " not in text:
                text = text.replace("_", " ")
            return text[:1].upper() + text[1:80]
    return _action_name(step).replace("_", " ").capitalize() or "This step"


def _humanize(binding: str) -> str:
    return binding.replace("_", " ").strip().capitalize()


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    block = ((doc.get("skills") or [{}])[0]) or {}
    return [s for s in block.get("steps") or [] if isinstance(s, dict)]


# ── generation ──────────────────────────────────────────────────────────────────────────────


def _question(kind: str, qid: str, title: str, body: str, choices: list[dict[str, Any]], *, multi: bool = False,
              step_keys_: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": qid, "kind": kind, "title": title, "body": body, "choices": choices, "multi": multi,
        "step_keys": step_keys_ or [], "status": "open", "answer": None, "receipt": "", **extra,
    }


ForkCopy = Callable[[dict[str, Any]], "str | None"]


def _fork_question(key: str, state: dict[str, Any], fork_copy: ForkCopy | None) -> dict[str, Any]:
    """The question about one popup state of an outcome fork. Every choice is a fixed fact from the
    compiled fork; ``fork_copy`` (a model, optional) may only reword the title line."""
    sig = state.get("signature") or {}
    popup = str(sig.get("title") or "a popup")
    options = [o for o in state.get("options") or [] if isinstance(o, dict)]
    choices: list[dict[str, Any]] = []
    for o in options:
        detail = []
        if o.get("recorded"):
            detail.append("You chose this while recording.")
        if o.get("role") == "consequential":
            detail.append("This can change or remove data. Picking it here means it runs without asking.")
        choices.append({"id": f"option:{o['id']}", "label": str(o.get("label") or o["id"]), "detail": " ".join(detail)})
    choices += [
        {"id": "by_input", "label": "Let the person running it choose each time",
         "detail": "Adds a setting they fill in, so each run can go a different way."},
        {"id": "ask_human", "label": "Pause and ask me each time", "detail": "The run stops on the popup and waits for a person."},
        {"id": "stop", "label": "Stop the run", "detail": "Treat this popup as a problem and end the run."},
    ]
    title = f"When “{popup}” appears, what should this workflow do?"
    if fork_copy is not None:
        try:
            reworded = fork_copy({
                "popup_title": popup,
                "buttons": [str(b) for b in sig.get("button_labels") or []],
                "message_hint": " ".join(sig.get("message_tokens") or [])[:120],
                "fallback": title,
            })
        except Exception:  # noqa: BLE001 -- wording only, never fail a compile over it
            reworded = None
        if reworded and str(reworded).strip():
            title = str(reworded).strip()
    return _question(
        "fork", f"fork:{key}:{state['id']}", title,
        "This popup can show up on a later run. Pick what happens when it does, and I\u2019ll also handle the case where it doesn\u2019t appear.",
        choices, step_keys_=[key], fork_state_id=state["id"],
        input_name="on_" + (re.sub(r"[^a-z0-9]+", "_", popup.lower()).strip("_") or "popup")[:40],
    )


def _known_popup_question(key: str, name: str, popup: dict[str, Any]) -> dict[str, Any]:
    """A popup the app map saw after this same step in other workflows. Fixed wording: the map holds
    no model-written text, and the only things offered are adding it (asks a person by default) or not."""
    title = str(popup.get("title") or "a popup")
    labels = [str(l) for l in popup.get("button_labels") or []][:4]
    return _question(
        "known_popup", f"known_popup:{key}:{popup.get('id')}",
        f"Other workflows sometimes get “{title}” after “{name}”. Cover it here too?",
        f"It offers {', '.join(labels)}. You didn’t see it while recording this one, but it can still appear on a real run.",
        [
            {"id": "add", "label": "Yes, handle it", "detail": "A person is asked what to do whenever it appears, and the run carries on if it doesn’t. Adds a short wait at this step."},
            {"id": "skip", "label": "No, leave it out", "detail": "A run that meets it will stop and ask instead."},
        ],
        step_keys_=[key], popup=popup,
    )


def _coverage_question(note: str) -> dict[str, Any]:
    return _question(
        "coverage", "coverage", "Does this cover everything you wanted?",
        f"{note} If something is missing, add it before testing, so the test proves the whole task.",
        [
            {"id": "complete", "label": "Yes, that’s everything", "detail": "The recording is what I meant."},
            {"id": "incomplete", "label": "No, something is missing", "detail": "I’ll record or add the missing part in Human Edit."},
        ],
    )


def _chosen_values_question(understanding: dict[str, Any]) -> dict[str, Any] | None:
    """Recorded values that aren't typed text (a page address, a record identifier) and that the
    person's own description suggests change each run. ``varies`` is pre-ticked, ``unsure`` is listed
    unticked, and anything judged ``fixed`` is not asked — infer what is clear, ask only what isn't."""
    cands = [c for c in understanding.get("candidates") or []
             if isinstance(c, dict) and c.get("supported") and c.get("verdict") in ("varies", "unsure") and c.get("name")]
    if not cands:
        return None
    limits = [str(c.get("recorded") or "") for c in understanding.get("candidates") or []
              if isinstance(c, dict) and not c.get("supported") and c.get("verdict") in ("varies", "unsure")]
    body = "Ticked values become inputs the person running it fills in. Unticked ones stay exactly as recorded."
    if limits:
        body += " I can’t make a clicked item change each run yet, so these stay as recorded: " + ", ".join(f"“{l}”" for l in limits[:3]) + "."
    kinds = {"url": "Page address", "row": "Record"}
    return _question(
        "chosen_values", "chosen_values", "Which of these change each run?", body,
        [{"id": c["ref"], "label": _humanize(c["name"]),
          "detail": f"{kinds.get(c['kind'], 'Value')}: {str(c.get('literal') or c.get('recorded'))[:60]}",
          "selected": c["verdict"] == "varies"} for c in cands],
        multi=True, step_keys_=sorted({k for c in cands for k in c.get("step_keys") or []}),
        items=[{"ref": c["ref"], "name": c["name"], "literal": c["literal"], "recorded": c["recorded"]} for c in cands],
        recorded={c["name"]: c["literal"] for c in cands},
    )


def generate_questions(
    doc: dict[str, Any],
    *,
    source_lookup: SourceLookup | None = None,
    answered_by_recording: dict[str, bool] | None = None,
    fork_copy: ForkCopy | None = None,
    known_popups: list[dict[str, Any]] | None = None,
    understanding: dict[str, Any] | None = None,
    limit: int | None = MAX_QUESTIONS,
) -> list[dict[str, Any]]:
    """Find the places worth asking about in a compiled skill document.

    ``source_lookup(step)`` returns the step's originally recorded event (or None): it supplies
    the recorded literal a "keep it fixed" answer would restore, and the recording's own sequence
    number so a value the person already marked "Changes each run?" while recording is not asked
    about again. ``answered_by_recording`` is that hint map (``{"<sequence>": bool}``).
    """
    steps = _steps(doc)
    keys = step_keys(steps)
    look = source_lookup or (lambda _s: None)
    hinted = answered_by_recording or {}
    out: list[dict[str, Any]] = []

    # 0 ─ understanding (conversation/understanding.py): the recording against what the person
    # said, and recorded values that aren't typed text. A fallback answer carries no coverage verdict.
    if understanding:
        cov = understanding.get("coverage") or {}
        if cov.get("ok") is False and cov.get("note"):
            out.append(_coverage_question(str(cov["note"])))
        chosen = _chosen_values_question(understanding)
        if chosen is not None:
            out.append(chosen)

    # 1 ─ file uploads: the recorded path lives on this computer and won't exist on a customer's.
    for i, s in enumerate(steps):
        if _action_name(s) in ("upload", "upload_intent") and s.get("input_binding") == "file_path":
            ev = look(s) or {}
            recorded = str((ev.get("action") or {}).get("value") or "")
            fname = os.path.basename(recorded.replace("\\", "/")) if recorded else ""
            label = f"“{fname}”" if fname else "the file"
            out.append(_question(
                "upload", f"upload:{keys[i]}", f"Upload {label} every time, or ask for the file each run?",
                f"You picked {label} while recording. A path on this computer won’t exist on your customers’ computers.",
                [
                    {"id": "input", "label": "Ask for the file each run", "detail": "Recommended — whoever runs it chooses the file."},
                    {"id": "fixed", "label": f"Always upload {label}", "detail": "Only works where that exact path exists."},
                ],
                step_keys_=[keys[i]], recorded_value=recorded,
            ))

    # 2 ─ a step that can't be undone: should a person confirm first?
    for i, s in enumerate(steps):
        if s.get("consequence") != "irreversible" or i == 0:
            continue
        prev = steps[i - 1]
        from conxa_compile.editor.patch_gate import is_confirm_only_review

        if _action_name(prev) == "ai_review" and is_confirm_only_review(prev):
            continue  # already guarded
        name = step_name(s)
        out.append(_question(
            "confirm", f"confirm:{keys[i]}", f"“{name}” can’t be undone. Should a person confirm first?",
            f"The run pauses before “{name}” and shows what’s about to happen. Whoever runs it — the agent’s user — answers.",
            [
                {"id": "yes", "label": "Yes, confirm first", "detail": f"Adds a confirmation step before “{name}”."},
                {"id": "no", "label": "No, go straight ahead", "detail": "Runs without asking."},
            ],
            step_keys_=[keys[i]], step_name=name,
        ))

    # 3 ─ typed values: fixed, or an input the person running it fills in?
    candidates: list[dict[str, Any]] = []
    for i, s in enumerate(steps):
        binding = s.get("input_binding")
        hh = s.get("handler_hints") if isinstance(s.get("handler_hints"), dict) else {}
        if not binding or binding == "file_path" or str(s.get("value") or "") != "{{" + str(binding) + "}}":
            continue
        control = str(hh.get("control_kind") or "")
        ev = look(s) or {}
        seq = (ev.get("extras") or {}).get("sequence")
        if seq is not None and str(seq) in hinted:
            continue  # the person already said, while recording
        if control == "choice":
            # A single choice can be pinned to its recorded option (the runtime matches step.value
            # against the option labels). A multi-select has no single literal to restore.
            choice = hh.get("choice") if isinstance(hh.get("choice"), dict) else {}
            recorded = "" if choice.get("multi") else str(choice.get("recorded_label") or "")
            if not recorded:
                continue
        elif control == "date_picker":
            picker = hh.get("date_picker") if isinstance(hh.get("date_picker"), dict) else {}
            recorded = str(picker.get("recorded_value") or "")
            if not recorded:
                continue
        elif control or _action_name(s) not in ("type", "fill"):
            continue
        else:
            recorded = str((ev.get("action") or {}).get("value") or "")
            if recorded == "{{REDACTED}}":
                continue
        candidates.append({"binding": str(binding), "step_key": keys[i], "recorded": recorded})
    seen: set[str] = set()
    uniq = [c for c in candidates if not (c["binding"] in seen or seen.add(c["binding"]))]
    if uniq:
        out.append(_question(
            "values", "values", "Which values change each run?",
            "Ticked values become inputs the person running it fills in. Unticked ones stay exactly as recorded.",
            [{"id": c["binding"], "label": _humanize(c["binding"]), "detail": c["recorded"][:60] or "Text", "selected": True} for c in uniq],
            multi=True, step_keys_=[c["step_key"] for c in uniq], recorded={c["binding"]: c["recorded"] for c in uniq},
        ))

    # 3b ─ a value the person marked "read this" while recording: confirm it is meant to be reported back.
    #      Advisory (a wrong yes is harmless; a wrong no just removes one read step they can re-record).
    extract_seen = 0
    for i, s in enumerate(steps):
        if _action_name(s) != "extract" or not s.get("value"):
            continue
        extract_seen += 1
        if extract_seen > 2:
            break
        label = step_name(s)
        out.append(_question(
            "output", f"output:{keys[i]}", f"Report \u201c{s['value']}\u201d back when the workflow finishes?",
            f"You marked \u201c{label}\u201d while recording. Whoever runs the workflow gets this value in its result.",
            [
                {"id": "keep", "label": "Yes, report it", "detail": "It comes back as a named result, and later steps can use it."},
                {"id": "remove", "label": "No, leave it out", "detail": "Removes the read step."},
            ],
            step_keys_=[keys[i]], output_name=str(s["value"]),
        ))

    # 1b ─ popups that need a decision (outcome forks): never answered silently.
    for i, s in enumerate(steps):
        if _action_name(s) != "outcome_fork":
            continue
        for state in ((s.get("outcome_fork") or {}).get("states") or []):
            if isinstance(state, dict) and state.get("id") and not (state.get("policy") or {}).get("reviewed"):
                out.append(_fork_question(keys[i], state, fork_copy))

    # 1c ─ popups other workflows on this app met right after the same step (the app map).
    for kp in (known_popups or [])[:MAX_KNOWN_POPUP_QUESTIONS]:
        i = int(kp.get("step_index", -1))
        if 0 <= i < len(steps) and isinstance(kp.get("popup"), dict):
            out.append(_known_popup_question(keys[i], step_name(steps[i]), kp["popup"]))

    # 4 ─ pop-ups the recorder saw but only a person can classify.
    for i, s in enumerate(steps):
        if isinstance(s.get("optional_hint"), dict) and s["optional_hint"]:
            name = step_name(s)
            out.append(_question(
                "popup", f"popup:{keys[i]}", f"A pop-up appeared at “{name}”. Always close it if it appears?",
                "You saw it while recording. Only you know whether it shows up every time.",
                [
                    {"id": "dismiss", "label": "Close it whenever it appears", "detail": "The run dismisses it if it’s there, and carries on if not."},
                    {"id": "once", "label": "It was a one-off", "detail": "Keep it as an ordinary step."},
                ],
                step_keys_=[keys[i]],
            ))

    # 5 ─ how do we know it worked? Only checks the compiler already wrote, never new selectors.
    checks: list[dict[str, Any]] = []
    for i in range(max(0, len(steps) - 3), len(steps)):
        for j, a in enumerate((steps[i].get("validation") or {}).get("assertions") or []):
            if isinstance(a, dict) and a.get("type") in _ASSERTION_TYPES and not a.get("required"):
                checks.append({"id": f"{keys[i]}#{j}", "step_key": keys[i], "index": j, "assertion": a})
    if checks:
        def describe(a: dict[str, Any]) -> str:
            t = a.get("type")
            if t == "text_present":
                return f"The message “{str(a.get('text') or a.get('value') or a.get('target') or '')[:60]}” appears"
            if t == "url_changed":
                return "The page address changes"
            if t == "url_pattern":
                return f"The page address matches {str(a.get('pattern') or a.get('value') or '')[:60]}"
            return "The page changes"

        out.append(_question(
            "done_when", "done_when", "How do we know it worked?",
            "I saw these at the end of your recording. Ticked checks must pass for a run to count as done.",
            [{"id": c["id"], "label": describe(c["assertion"]), "detail": "", "selected": c["assertion"].get("type") == "text_present"} for c in checks],
            multi=True, step_keys_=sorted({c["step_key"] for c in checks}),
        ))

    out.sort(key=lambda q: _PRIORITY[q["kind"]])
    # ``limit=None`` returns everything: the compile flow asks the first MAX_QUESTIONS and keeps the
    # rest as ``queued`` rather than dropping them (see compile_flow._flow_after_compile).
    return out if limit is None else out[:limit]


# ── answering ───────────────────────────────────────────────────────────────────────────────


def plan_answer(question: dict[str, Any], answer: dict[str, Any], doc: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Turn an answer into ops for existing commands, plus the receipt line shown once applied.

    ``answer`` is ``{"choice": id}`` for a single-choice question or ``{"selected": [ids]}`` for a
    multi-choice one. Ops (run in order by the handler):

    ``{"op": "patch", "step_key", "patch"}``        — ``cmd_patch_step`` (the gated mutation)
    ``{"op": "insert_review", "before_step_key", "prompt", "title"}`` — insert + configure a confirm-only review
    ``{"op": "confirm_optional", "step_key", "keep_as_step"}``
    ``{"op": "drop_inputs", "ids"}``                — remove inputs nothing references any more
    ``{"op": "delete_step", "step_key"}``           — ``cmd_delete_step`` (an extract the person decided not to report)
    ``{"op": "parameterize", "literal", "name", "default"}`` — ``cmd_parameterize_literal``: a recorded
        page address / record identifier becomes a run input
    """
    kind = question["kind"]
    ops: list[dict[str, Any]] = []
    keys = question.get("step_keys") or []

    if kind == "upload":
        if answer.get("choice") == "fixed" and question.get("recorded_value"):
            ops.append({"op": "patch", "step_key": keys[0], "patch": {"value": question["recorded_value"]}})
            ops.append({"op": "drop_inputs", "ids": ["file_path"]})
            return ops, "Always uploads the recorded file"
        return ops, "Asks for the file each run"

    if kind == "coverage":
        if answer.get("choice") == "incomplete":
            return ops, "Something is missing — add it in Human Edit before relying on this"
        return ops, "Covers everything"

    if kind == "chosen_values":
        selected = set(answer.get("selected") or [])
        names: list[str] = []
        for item in question.get("items") or []:
            if item.get("ref") in selected:
                ops.append({"op": "parameterize", "literal": item["literal"], "name": item["name"], "default": item["literal"]})
                names.append(_humanize(item["name"]))
        return ops, (", ".join(names) if names else "Nothing changes — all kept as recorded")

    if kind == "output":
        if answer.get("choice") == "remove":
            ops.append({"op": "delete_step", "step_key": keys[0]})
            return ops, "Not reported"
        return ops, f"Reported as \u201c{question.get('output_name') or 'the result'}\u201d"

    if kind == "confirm":
        if answer.get("choice") == "yes":
            name = question.get("step_name") or "this step"
            ops.append({"op": "insert_review", "before_step_key": keys[0], "title": f"Confirm before “{name}”",
                        "prompt": f"About to run “{name}”, which can’t be undone. Should it go ahead?"})
            return ops, "A person confirms"
        return ops, "Runs without confirmation"

    if kind == "fork":
        choice = str(answer.get("choice") or "")
        label = next((c["label"] for c in question.get("choices") or [] if c["id"] == choice), "")
        if choice.startswith("option:"):
            policy = {"mode": "auto", "option_id": choice[len("option:"):]}
            receipt = f"Always: {label}"
        elif choice == "by_input":
            policy = {"mode": "by_input", "input": question.get("input_name") or "on_popup"}
            receipt = "The person running it chooses each time"
        elif choice == "ask_human":
            policy = {"mode": "ask_human"}
            receipt = "Pauses and asks a person"
        else:
            policy = {"mode": "stop"}
            receipt = "Stops the run"
        ops.append({"op": "set_fork_policy", "step_key": keys[0], "state_id": question.get("fork_state_id"), "policy": policy})
        return ops, receipt

    if kind == "known_popup":
        if answer.get("choice") == "add":
            title = str((question.get("popup") or {}).get("title") or "the popup")
            ops.append({"op": "add_known_popup_fork", "step_key": keys[0], "popup": question["popup"]})
            return ops, f"Added: a person decides when “{title}” appears"
        return ops, "Left out"

    if kind == "popup":
        once = answer.get("choice") == "once"
        ops.append({"op": "confirm_optional", "step_key": keys[0], "keep_as_step": once})
        return ops, "Kept as an ordinary step" if once else "Close it whenever it appears"

    if kind == "values":
        selected = set(answer.get("selected") or [])
        recorded: dict[str, str] = question.get("recorded") or {}
        fixed: list[str] = []
        steps = _steps(doc)
        keyed = dict(zip(step_keys(steps), steps))
        for choice in question.get("choices") or []:
            binding = choice["id"]
            if binding in selected:
                continue
            literal = recorded.get(binding)
            if not literal:
                continue  # nothing to restore: leave it an input rather than guess a value
            for key, step in keyed.items():
                if step.get("input_binding") == binding and step.get("value") == "{{" + binding + "}}":
                    ops.append({"op": "patch", "step_key": key, "patch": {"value": literal}})
            fixed.append(binding)
        if fixed:
            ops.append({"op": "drop_inputs", "ids": fixed})
        kept = [c["label"] for c in question.get("choices") or [] if c["id"] in selected]
        return ops, (", ".join(kept) if kept else "Nothing changes — all kept as recorded")

    if kind == "done_when":
        selected = set(answer.get("selected") or [])
        by_step: dict[str, dict[int, bool]] = {}
        for cid in selected:
            key, _, idx = cid.rpartition("#")  # step keys may contain "#" themselves
            by_step.setdefault(key, {})[int(idx)] = True
        steps = _steps(doc)
        keyed = dict(zip(step_keys(steps), steps))
        for key, idxs in by_step.items():
            step = keyed.get(key)
            if step is None:
                continue
            assertions = [dict(a) for a in (step.get("validation") or {}).get("assertions") or []]
            for j in idxs:
                if j < len(assertions):
                    assertions[j]["required"] = True
            ops.append({"op": "patch", "step_key": key, "patch": {"validation": {"assertions": assertions}}})
        labels = [c["label"] for c in question.get("choices") or [] if c["id"] in selected]
        return ops, "; ".join(labels) if labels else "No required checks"

    return ops, ""


# ── summary ─────────────────────────────────────────────────────────────────────────────────


def build_summary(doc: dict[str, Any], *, recorded_actions: int, added_steps: int, name: str, uncertain: int) -> dict[str, Any]:
    """The workflow summary card's data. The counts add up across screens: actions recorded →
    steps compiled → steps now (after answers added some)."""
    steps = _steps(doc)
    names = [step_name(s) for s in steps]
    required_checks: list[str] = []
    for s in steps:
        for a in (s.get("validation") or {}).get("assertions") or []:
            if isinstance(a, dict) and a.get("required") and a.get("type") in _ASSERTION_TYPES:
                if a.get("type") == "text_present":
                    required_checks.append(f"“{str(a.get('text') or a.get('value') or a.get('target') or '')[:60]}” appears")
                elif a.get("type") == "url_changed":
                    required_checks.append("the page address changes")
    inputs = [i for i in doc.get("inputs") or [] if isinstance(i, dict)]
    return {
        "name": name,
        "version": int((doc.get("meta") or {}).get("version") or 1),
        "recorded_actions": recorded_actions,
        "steps": len(steps),
        "added_steps": added_steps,
        "inputs": len(inputs),
        "uncertain": uncertain,
        "step_names": names,
        "done_when": required_checks[:3],
    }
