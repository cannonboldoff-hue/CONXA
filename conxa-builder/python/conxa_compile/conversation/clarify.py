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

import datetime
import os
import re
from typing import Any, Callable

from conxa_compile.compiler.step_key import step_keys
from conxa_core.models.prose_binding import readable as _readable

MAX_QUESTIONS = 5
MAX_KNOWN_POPUP_QUESTIONS = 2
_ASSERTION_TYPES = {"text_present", "url_changed", "url_pattern", "state_changed"}
_PRIORITY = {
    "coverage": -1, "upload": 0, "fork": 1, "known_popup": 1.5, "confirm": 2, "chosen_values": 2.5,
    "dropped_value": 2.6, "test_followup": 2.7, "values": 3, "relative_date": 3.1, "skip_when_empty": 3.2, "missing_item": 3.3, "output": 3.5, "popup": 4, "done_when": 5,
}
# Ledger passes whose dropped events live on inside a merged step (their value is kept), so a
# value on one of them is not "lost" and is never asked about.
_MERGE_PASSES = frozenset({"choice_collapsed", "date_collapsed"})
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
            text = _readable(re.sub(r"^Step \d+:\s*", "", text))
            if "_" in text and " " not in text:
                text = text.replace("_", " ")
            return text[:1].upper() + text[1:80]
    return _action_name(step).replace("_", " ").capitalize() or "This step"


def _humanize(binding: str) -> str:
    return binding.replace("_", " ").strip().capitalize()


def _quoted(text: str) -> str:
    """``“text”`` for a person, without doubling the quotes when ``readable`` already added them
    (a target that is only a ``{{token}}`` comes back already quoted)."""
    shown = _readable(text)[:60].strip()
    return shown if shown.startswith("“") and shown.endswith("”") and shown.count("“") == 1 else f"“{shown}”"


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
            detail.append("This is what you clicked while recording.")
        if o.get("role") == "consequential":
            detail.append("Careful: this can change or delete things. If you pick it, it will happen on its own, without asking anyone.")
        choices.append({"id": f"option:{o['id']}", "label": str(o.get("label") or o["id"]), "detail": " ".join(detail)})
    choices += [
        {"id": "by_input", "label": "Let whoever uses it decide each time",
         "detail": "They get a question to answer before it starts, so it can go a different way each time."},
        {"id": "ask_human", "label": "Stop and ask a person each time", "detail": "It waits on the popup until someone tells it what to do."},
        {"id": "stop", "label": "Stop completely", "detail": "Treat this popup as a problem and end here."},
    ]
    title = f"A message box called “{popup}” can pop up here. What should happen when it does?"
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
        "You saw this message box while recording, but it might not always show up. Tell me what to do when it does. If it doesn\u2019t show up, I\u2019ll just carry on.",
        choices, step_keys_=[key], fork_state_id=state["id"],
        input_name="on_" + (re.sub(r"[^a-z0-9]+", "_", popup.lower()).strip("_") or "popup")[:40],
        # Context for the wording call only (the popup's own buttons); nothing reads it back as a choice.
        popup_buttons=[str(b) for b in sig.get("button_labels") or []][:6],
    )


def _known_popup_question(key: str, name: str, popup: dict[str, Any]) -> dict[str, Any]:
    """A popup the app map saw after this same step in other workflows. Fixed wording: the map holds
    no model-written text, and the only things offered are adding it (asks a person by default) or not."""
    title = str(popup.get("title") or "a popup")
    labels = [str(l) for l in popup.get("button_labels") or []][:4]
    return _question(
        "known_popup", f"known_popup:{key}:{popup.get('id')}",
        f"Your other workflows sometimes see a message box called “{title}” after “{name}”. Should this one be ready for it too?",
        f"It has these buttons: {', '.join(labels)}. It didn’t appear while you recorded this one, but it could show up when someone really uses it.",
        [
            {"id": "add", "label": "Yes, be ready for it", "detail": "If it shows up, a person gets asked what to do. If not, it carries on. This adds a short pause at this step."},
            {"id": "skip", "label": "No, skip it", "detail": "If it ever shows up, it will stop and ask a person for help."},
        ],
        step_keys_=[key], popup=popup,
    )


def _coverage_question(note: str) -> dict[str, Any]:
    return _question(
        "coverage", "coverage", "Does this cover everything you wanted?",
        f"{note} If a part is missing, add it now, before you try it out. That way the try-out checks the whole job.",
        [
            {"id": "complete", "label": "Yes, that’s everything", "detail": "The recording shows what I wanted."},
            {"id": "incomplete", "label": "No, a part is missing", "detail": "I\u2019ll add or record the missing part in the editor."},
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
              if isinstance(c, dict) and c.get("kind") == "click" and not c.get("supported")
              and c.get("verdict") in ("varies", "unsure")]
    body = "Tick the ones that are different every time. Whoever uses this will type them in. Anything you leave unticked stays exactly as you recorded it."
    if limits:
        body += " I can\u2019t make a clicked item change yet, so these stay the same as you recorded: " + ", ".join(f"“{l}”" for l in limits[:3]) + "."
    kinds = {"url": "Web address", "row": "Item in a list", "click": "Something you clicked"}
    return _question(
        "chosen_values", "chosen_values", "Which of these are different every time?", body,
        [{"id": c["ref"], "label": _humanize(c["name"]),
          "detail": f"{kinds.get(c['kind'], 'Value')}: {str(c.get('literal') or c.get('recorded'))[:60]}",
          "selected": c["verdict"] == "varies"} for c in cands],
        multi=True, step_keys_=sorted({k for c in cands for k in c.get("step_keys") or []}),
        items=[{"ref": c["ref"], "name": c["name"], "literal": c["literal"], "recorded": c["recorded"],
                **({"kind": "click"} if c["kind"] == "click" else {})} for c in cands],
        recorded={c["name"]: c["literal"] for c in cands},
    )


def test_followup_question(doc: dict[str, Any], step_key: str, tested_inputs: dict[str, Any]) -> dict[str, Any] | None:
    """After a failed test: the failing step typed an input whose test value differs from the
    recorded one. Asks whether it really changes each run (keep it an input) or was only ever the
    recorded value (pin it back). None when the step is not that shape. Pure."""
    steps = _steps(doc)
    keyed = dict(zip(step_keys(steps), steps))
    step = keyed.get(step_key)
    binding = str((step or {}).get("input_binding") or "")
    if not step or not binding or binding == "file_path" or str(step.get("value") or "") != "{{" + binding + "}}":
        return None
    recorded = next((str(i.get("default") or "") for i in doc.get("inputs") or []
                     if isinstance(i, dict) and i.get("id") == binding), "")
    tested = str((tested_inputs or {}).get(binding) or "")
    if not recorded.strip() or recorded == "{{REDACTED}}" or not tested.strip() or tested == recorded:
        return None
    name = step_name(step)
    return _question(
        "test_followup", f"test_followup:{step_key}:{binding}",
        f"The try-out got stuck at “{name}”. Is the “{_humanize(binding).lower()}” really different every time?",
        f"In the try-out you typed “{tested[:60]}”, but while recording you typed “{recorded[:60]}”. If it is always “{recorded[:60]}”, I\u2019ll stop asking for it.",
        [
            {"id": "varies", "label": "Yes, it is different every time", "detail": "Whoever uses it will still type it in. The problem may be something else."},
            {"id": "fixed", "label": f"No, always “{recorded[:40]}”", "detail": "I\u2019ll always use what you recorded and stop asking."},
        ],
        step_keys_=[step_key], binding=binding, recorded_value=recorded,
    )


_INPUT_CHECK_WHY = {
    "differs_from_recorded": "In the try-out you typed “{tested}”, but while recording you typed “{recorded}”.",
    "not_an_option": "“{tested}” is not one of the choices allowed here.",
    "pattern_mismatch": "“{tested}” is not written the way it needs to be.",
    "empty": "It was left blank.",
}


def input_check_question(suspects: list[dict[str, Any]], input_defs: list[dict[str, Any]],
                         tested_inputs: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    """After a test that could not find an element: the value typed for an input the failing step
    reads looks wrong (``editor/input_suspects.py``). Asks before Auto-fix rewrites the steps. None
    when nothing is suspect. Pure; the values shown are never a sensitive input's (those are only
    ever suspect when empty)."""
    defs = {str(d["id"]): d for d in input_defs or [] if isinstance(d, dict) and d.get("id")}
    sentences, ids = [], []
    for s in suspects:
        defn = defs.get(s["id"]) or {}
        label = _humanize(s["id"])
        why = _INPUT_CHECK_WHY.get(s["reason"], "").format(
            tested=str((tested_inputs or {}).get(s["id"]) or "")[:40], recorded=str(defn.get("default") or "")[:40])
        sentences.append(f"{label}: {why}" if len(suspects) > 1 else why)
        ids.append(s["id"])
    if not ids:
        return None
    names = ", ".join(_humanize(i).lower() for i in ids)
    return _question(
        "input_check", f"input_check:{run_id}",
        f"The try-out couldn\u2019t find the {names} on the page. Did you type the right thing?",
        " ".join(sentences) + " The workflow itself may be fine.",
        [
            {"id": "wrong", "label": "No, I typed it wrong. Let me try again", "detail": "I\u2019ll reopen the try-out and point to the box to fix."},
            {"id": "right", "label": "Yes, it was right. Fix the workflow instead", "detail": "I\u2019ll look for the problem in the steps."},
        ],
        input_ids=ids,
    )


def _done_when_choices(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """The tickable "it worked" checks for the doc as it stands now: assertions the compiler already
    wrote (not yet required) plus one "this input shows up" check per run input. Text comes from
    ``target`` first -- the field the runtime checks and a "changes each run" answer rewrites."""
    steps = _steps(doc)
    keys = step_keys(steps)
    checks: list[dict[str, Any]] = []
    for i in range(max(0, len(steps) - 3), len(steps)):
        for j, a in enumerate((steps[i].get("validation") or {}).get("assertions") or []):
            if isinstance(a, dict) and a.get("type") in _ASSERTION_TYPES and not a.get("required"):
                checks.append({"id": f"{keys[i]}#{j}", "step_key": keys[i], "assertion": a})

    def describe(a: dict[str, Any]) -> str:
        t = a.get("type")
        if t == "text_present":
            return f"You see the words {_quoted(str(a.get('target') or a.get('text') or a.get('value') or ''))} on the screen"
        if t == "url_changed":
            return "The web address at the top of the browser changes"
        if t == "url_pattern":
            return f"The web address looks like {_readable(str(a.get('pattern') or a.get('value') or ''))[:60]}"
        return "The screen changes"

    choices = [{"id": c["id"], "label": describe(c["assertion"]), "detail": "",
                "selected": c["assertion"].get("type") == "text_present"} for c in checks]
    if choices:  # offered only beside a real check, so an input-only workflow isn't pestered
        referenced = " ".join(str(a.get("target") or "") for s in steps
                              for a in (s.get("validation") or {}).get("assertions") or [] if isinstance(a, dict))
        for inp in doc.get("inputs") or []:
            iid = str(inp.get("id") or "") if isinstance(inp, dict) else ""
            if iid and iid != "file_path" and "{{" + iid + "}}" not in referenced and not any(
                    c["assertion"].get("type") == "text_present" and "{{" + iid + "}}" in str(c["assertion"].get("target") or "")
                    for c in checks):
                choices.append({"id": f"input:{iid}", "label": f"You see the {_humanize(iid).lower()} on the screen",
                                "detail": "Makes sure what was typed in appears on the screen at the end.", "selected": False})
    return choices, sorted({c["step_key"] for c in checks})


# Kinds whose body quotes raw typed values, and choice labels that are page text or value names:
# those are never sent to a model, never reworded. Everything else may have its wording rewritten.
_NO_REWORD_KINDS = {"dropped_value", "test_followup", "input_check"}
_QUOTED = re.compile(r"“[^”]+”")


def reword_payload(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a model may see to reword: title, body, and (single-choice only) each fixed choice.
    Multi-choice labels are value names and fork ``option:`` labels are the popup's own buttons, so
    they are left out and stay exactly as built."""
    out = []
    for q in questions:
        if q.get("kind") in _NO_REWORD_KINDS or q.get("status") not in ("open", "queued"):
            continue
        item: dict[str, Any] = {"id": q["id"], "title": q["title"], "body": q["body"]}
        if q.get("popup_buttons"):
            item["popup_buttons"] = list(q["popup_buttons"])
        if not q.get("multi"):
            item["choices"] = [{"id": c["id"], "label": c["label"], "detail": c.get("detail", "")}
                               for c in q["choices"] if not str(c["id"]).startswith("option:")]
        out.append(item)
    return out


def apply_reworded(questions: list[dict[str, Any]], reworded: Any) -> None:
    """Fold a model's friendlier wording into ``questions`` (in place), field by field.

    Only title, body and the label/detail of existing single-choice options can change; ids, order,
    what each choice does and every key besides those are code's. A field is taken only when it is a
    non-empty string of sane length, has no ``{{`` placeholder and keeps every “quoted” item of the
    original (so a recorded name or value can't be dropped or invented); anything else keeps the
    template text. Never raises."""
    if not isinstance(reworded, dict) or not isinstance(reworded.get("questions"), list):
        return
    by_id = {str(r.get("id")): r for r in reworded["questions"] if isinstance(r, dict)}
    allowed = {item["id"] for item in reword_payload(questions)}

    def ok(new: Any, old: str, limit: int) -> bool:
        return (isinstance(new, str) and 0 < len(new.strip()) <= limit and "{{" not in new
                and all(m in new for m in _QUOTED.findall(old)))

    for q in questions:
        r = by_id.get(q["id"])
        if r is None or q["id"] not in allowed:
            continue
        if ok(r.get("title"), q["title"], 200):
            q["title"] = r["title"].strip()
        if ok(r.get("body"), q["body"], 500):
            q["body"] = r["body"].strip()
        if q.get("multi"):
            continue
        new_choices = {str(c.get("id")): c for c in r.get("choices") or [] if isinstance(c, dict)}
        for c in q["choices"]:
            n = new_choices.get(str(c["id"]))
            if n is None or str(c["id"]).startswith("option:"):
                continue
            if ok(n.get("label"), c["label"], 90):
                c["label"] = n["label"].strip()
            if ok(n.get("detail"), c.get("detail", ""), 220) or (n.get("detail") == "" and not c.get("detail")):
                c["detail"] = str(n.get("detail") or "").strip()


def refresh_question(question: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a ``done_when`` question from the doc as it is now, so its labels reflect answers given
    after the compile (a value that became an input). Other kinds are returned as they were."""
    if question.get("kind") != "done_when":
        return question
    choices, keys = _done_when_choices(doc)
    return {**question, "choices": choices, "step_keys": keys} if choices else question


def generate_questions(
    doc: dict[str, Any],
    *,
    source_lookup: SourceLookup | None = None,
    answered_by_recording: dict[str, bool] | None = None,
    fork_copy: ForkCopy | None = None,
    known_popups: list[dict[str, Any]] | None = None,
    understanding: dict[str, Any] | None = None,
    dropped: list[dict[str, Any]] | None = None,
    today: datetime.date | None = None,
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

    # 0b ─ steps the compile left out that carried a value the person entered: say so, so a
    # real omission is not silent. Advisory: the answer applies nothing (re-record to add it back).
    lost = [d for d in dropped or []
            if isinstance(d, dict) and d.get("value") and d.get("pass") not in _MERGE_PASSES]
    if lost:
        shown = ", ".join(f"“{str(d['value'])[:40]}”" for d in lost[:3])
        out.append(_question(
            "dropped_value", "dropped_value", "A few things you typed are not in the workflow. Is that okay?",
            f"You typed these while recording, but they are not in the workflow: {shown}. "
            "That is normal if you fixed a typo or typed over something while recording.",
            [
                {"id": "ok", "label": "Yes, leave them out", "detail": "They were mistakes or typed twice."},
                {"id": "missing", "label": "No, they should be there", "detail": "I\u2019ll add them in the editor, or you can record again."},
            ],
        ))

    # 1 ─ file uploads: the recorded path lives on this computer and won't exist on a customer's.
    for i, s in enumerate(steps):
        if _action_name(s) in ("upload", "upload_intent") and s.get("input_binding") == "file_path":
            ev = look(s) or {}
            recorded = str((ev.get("action") or {}).get("value") or "")
            fname = os.path.basename(recorded.replace("\\", "/")) if recorded else ""
            label = f"“{fname}”" if fname else "the file"
            out.append(_question(
                "upload", f"upload:{keys[i]}", f"Always send {label}, or let whoever uses this pick the file?",
                f"You chose {label} from your computer while recording. Your customers won\u2019t have that file on their computers.",
                [
                    {"id": "input", "label": "Let them pick the file each time", "detail": "Best choice. Whoever uses this picks their own file."},
                    {"id": "fixed", "label": f"Always send {label}", "detail": "Only works on a computer that has this exact file in the same place."},
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
            "confirm", f"confirm:{keys[i]}", f"“{name}” can\u2019t be undone. Should someone approve it before it happens?",
            f"It will stop just before “{name}”, show what is about to happen, and wait for a yes or no from whoever is using it.",
            [
                {"id": "yes", "label": "Yes, ask for approval first", "detail": f"A yes or no question appears before “{name}”."},
                {"id": "no", "label": "No, just do it", "detail": "It goes ahead without asking."},
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
    # The person's own description said this exact value is the point of the task: leave it unticked.
    fixed_typed = {str(c.get("name")) for c in (understanding or {}).get("candidates") or []
                   if isinstance(c, dict) and c.get("kind") == "typed" and c.get("verdict") == "fixed"}
    seen: set[str] = set()
    uniq = [c for c in candidates if not (c["binding"] in seen or seen.add(c["binding"]))]
    if uniq:
        out.append(_question(
            "values", "values", "Which of these are different every time?",
            "Tick the ones that are different every time. Whoever uses this will type them in. Anything you leave unticked stays exactly as you recorded it.",
            [{"id": c["binding"], "label": _humanize(c["binding"]), "detail": c["recorded"][:60] or "Text",
              "selected": c["binding"] not in fixed_typed} for c in uniq],
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
            "output", f"output:{keys[i]}", f"Show \u201c{s['value']}\u201d to the user when it finishes?",
            f"While recording you chose to read \u201c{label}\u201d from the screen. Whoever uses this workflow can get that text back when it is done.",
            [
                {"id": "keep", "label": "Yes, show it", "detail": "It is handed back with a name, and later steps can use it too."},
                {"id": "remove", "label": "No, don\u2019t show it", "detail": "I\u2019ll remove the step that reads it."},
            ],
            step_keys_=[keys[i]], output_name=str(s["value"]),
        ))

    # 3b2 ─ a picked date that may only ever have meant "N days from today". Needs today's date (passed in,
    #       so this stays pure); the label states the offset it infers so a wrong guess is easy to refuse.
    if today is not None:
        rel_seen = 0
        for i, s in enumerate(steps):
            hh = s.get("handler_hints") if isinstance(s.get("handler_hints"), dict) else {}
            picker = hh.get("date_picker") if isinstance(hh.get("date_picker"), dict) else {}
            try:
                picked = datetime.date.fromisoformat(str(picker.get("recorded_value") or "")[:10])
            except ValueError:
                continue
            days = (picked - today).days
            if _action_name(s) != "date_pick" or picker.get("date_offset_days") is not None or not -31 <= days <= 366:
                continue
            rel_seen += 1
            if rel_seen > 2:
                break
            when = ("today" if days == 0 else f"{abs(days)} day{'s' if abs(days) != 1 else ''} "
                    f"{'after' if days > 0 else 'before'} today")
            name = step_name(s)
            out.append(_question(
                "relative_date", f"relative_date:{keys[i]}", f"Is “{name}” always {when}?",
                f"You picked {picked.strftime('%d %b %Y')}, and today is {today.strftime('%d %b %Y')}. "
                "If it is always counted from the day someone uses it, nobody has to type a date.",
                [
                    {"id": "relative", "label": f"Yes, always {when}", "detail": "I\u2019ll work out the date on the day. Nothing to type."},
                    {"id": "fixed_input", "label": "No, it is different every time", "detail": "Whoever uses it will pick the date."},
                ],
                step_keys_=[keys[i]], days=days, binding=str(s.get("input_binding") or ""),
            ))

    # 3c ─ a typed field the person may leave empty: skip the step then, instead of typing nothing.
    #      Advisory; the answer is one gated patch (run_if reads the run's inputs, never the page).
    declared = {str(i.get("id")): i for i in doc.get("inputs") or [] if isinstance(i, dict)}
    skip_seen = 0
    for i, s in enumerate(steps):
        binding = str(s.get("input_binding") or "")
        row = declared.get(binding) or {}
        # "optional-looking": marked optional, or recorded empty (an empty default alone is not proof)
        ev = look(s)
        recorded_empty = bool(ev) and not str((ev.get("action") or {}).get("value") or "").strip()
        if (row and (row.get("optional") or (recorded_empty and not str(row.get("default") or "").strip()))
                and binding != "file_path" and _action_name(s) in ("type", "fill")
                and str(s.get("value") or "") == "{{" + binding + "}}" and not s.get("run_if")):
            skip_seen += 1
            if skip_seen > 2:
                break
            name = step_name(s)
            out.append(_question(
                "skip_when_empty", f"skip_when_empty:{keys[i]}", f"If the “{_humanize(binding).lower()}” is left blank, should we skip “{name}”?",
                "Sometimes whoever uses this may have nothing to put here. Skipping stops it from typing nothing into the box.",
                [
                    {"id": "skip", "label": "Yes, skip it if blank", "detail": f"“{name}” only happens when something is typed in."},
                    {"id": "run", "label": "No, always do it", "detail": "It happens even if the box is blank."},
                ],
                step_keys_=[keys[i]], binding=binding,
            ))

    # 3d ─ a step acting on a record the run names: what if that record is not on the page?
    missing_seen = 0
    for i, s in enumerate(steps):
        eb = s.get("entity_binding")
        if (isinstance(eb, dict) and eb.get("container_selector") and str(eb.get("identifier") or "").startswith("{{")
                and not s.get("on_missing")):
            missing_seen += 1
            if missing_seen > 2:
                break
            name = step_name(s)
            out.append(_question(
                "missing_item", f"missing_item:{keys[i]}", f"What should “{name}” do if the item can\u2019t be found on the page?",
                "This step works on one particular item. It might be gone, for example if someone already dealt with it.",
                [
                    {"id": "skip", "label": "Skip it and keep going", "detail": "Only when the list is showing and the item is clearly not in it."},
                    {"id": "ask", "label": "Stop and ask a person", "detail": "It waits with the browser open until someone sorts it out."},
                    {"id": "fail", "label": "Stop and show an error", "detail": "Treat a missing item as a problem."},
                ],
                step_keys_=[keys[i]],
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
                "popup", f"popup:{keys[i]}", f"A small window popped up at “{name}”. Should it be closed whenever it shows up?",
                "You saw it while recording. Only you know if it appears every time or only sometimes.",
                [
                    {"id": "dismiss", "label": "Yes, close it if it shows up", "detail": "If it’s there it gets closed. If not, it carries on."},
                    {"id": "once", "label": "No, that only happened once", "detail": "Keep it as a normal step."},
                ],
                step_keys_=[keys[i]],
            ))

    # 5 ─ how do we know it worked? Only checks the compiler already wrote, never new selectors.
    done_choices, done_keys = _done_when_choices(doc)
    if done_choices:
        out.append(_question(
            "done_when", "done_when", "How can we tell it worked?",
            "At the end of your recording I noticed these. Tick the ones that should be true when it works. If a ticked one is not true, it counts as failed. "
            "You can also type your own below.",
            done_choices, multi=True, step_keys_=done_keys,
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
            return ops, "Always sends the file you recorded"
        return ops, "Whoever uses it picks the file"

    if kind == "coverage":
        if answer.get("choice") == "incomplete":
            return ops, "A part is missing — add it in the editor before you rely on this"
        return ops, "Covers everything you wanted"

    if kind == "relative_date":
        if answer.get("choice") == "relative":
            ops.append({"op": "set_date_offset", "step_key": keys[0], "days": int(question["days"])})
            if question.get("binding"):
                ops.append({"op": "drop_inputs", "ids": [question["binding"]]})
            return ops, "Worked out from the day it is used"
        return ops, "Whoever uses it still fills it in"

    if kind == "skip_when_empty":
        if answer.get("choice") == "skip":
            ops.append({"op": "patch", "step_key": keys[0], "patch": {"run_if": {"input": question["binding"], "op": "not_empty"}}})
            return ops, "Skipped if left blank"
        return ops, "Always happens"

    if kind == "missing_item":
        if answer.get("choice") in ("skip", "ask"):
            ops.append({"op": "patch", "step_key": keys[0], "patch": {"on_missing": answer["choice"]}})
            return ops, ("Skipped if the item isn’t there" if answer["choice"] == "skip"
                         else "Asks a person if the item isn’t there")
        return ops, "Stops and shows an error"

    if kind == "test_followup":
        if answer.get("choice") == "fixed":
            ops.append({"op": "patch", "step_key": keys[0], "patch": {"value": question["recorded_value"]}})
            ops.append({"op": "drop_inputs", "ids": [question["binding"]]})
            return ops, "Always what you recorded"
        return ops, "Whoever uses it still fills it in"

    if kind == "input_check":
        # No edit to the workflow either way; the handler acts on the choice (re-test or Auto-fix).
        return ops, ("Trying again with a corrected value" if answer.get("choice") == "wrong" else "Keeping the value — looking at the steps")

    if kind == "dropped_value":
        if answer.get("choice") == "missing":
            return ops, "A part is missing — add it in the editor before you rely on this"
        return ops, "Left out"

    if kind == "chosen_values":
        selected = set(answer.get("selected") or [])
        names: list[str] = []
        for item in question.get("items") or []:
            if item.get("ref") in selected:
                ops.append({"op": "parameterize", "literal": item["literal"], "name": item["name"], "default": item["literal"],
                            **({"kind": "click"} if item.get("kind") == "click" else {})})
                names.append(_humanize(item["name"]))
        return ops, (", ".join(names) if names else "Nothing changes — everything stays as you recorded")

    if kind == "output":
        if answer.get("choice") == "remove":
            ops.append({"op": "delete_step", "step_key": keys[0]})
            return ops, "Not shown to the user"
        return ops, f"Shown to the user as \u201c{question.get('output_name') or 'the result'}\u201d"

    if kind == "confirm":
        if answer.get("choice") == "yes":
            name = question.get("step_name") or "this step"
            ops.append({"op": "insert_review", "before_step_key": keys[0], "title": f"Confirm before “{name}”",
                        "prompt": f"About to run “{name}”, which can’t be undone. Should it go ahead?"})
            return ops, "Someone approves it first"
        return ops, "Goes ahead without asking"

    if kind == "fork":
        choice = str(answer.get("choice") or "")
        label = next((c["label"] for c in question.get("choices") or [] if c["id"] == choice), "")
        if choice.startswith("option:"):
            policy = {"mode": "auto", "option_id": choice[len("option:"):]}
            receipt = f"Always: {label}"
        elif choice == "by_input":
            policy = {"mode": "by_input", "input": question.get("input_name") or "on_popup"}
            receipt = "Whoever uses it decides each time"
        elif choice == "ask_human":
            policy = {"mode": "ask_human"}
            receipt = "Stops and asks a person"
        else:
            policy = {"mode": "stop"}
            receipt = "Stops completely"
        ops.append({"op": "set_fork_policy", "step_key": keys[0], "state_id": question.get("fork_state_id"), "policy": policy})
        return ops, receipt

    if kind == "known_popup":
        if answer.get("choice") == "add":
            title = str((question.get("popup") or {}).get("title") or "the popup")
            ops.append({"op": "add_known_popup_fork", "step_key": keys[0], "popup": question["popup"]})
            return ops, f"Added: a person decides what to do when “{title}” appears"
        return ops, "Left out"

    if kind == "popup":
        once = answer.get("choice") == "once"
        ops.append({"op": "confirm_optional", "step_key": keys[0], "keep_as_step": once})
        return ops, "Kept as a normal step" if once else "Closed whenever it shows up"

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
        return ops, (", ".join(kept) if kept else "Nothing changes — everything stays as you recorded")

    if kind == "done_when":
        selected = set(answer.get("selected") or [])
        custom = str(answer.get("custom_text") or "").strip()[:120]
        by_step: dict[str, dict[int, bool]] = {}
        extra: list[dict[str, Any]] = []  # new text checks, added to the last step
        for cid in selected:
            if cid.startswith("input:"):
                extra.append({"type": "text_present", "target": "{{" + cid[len("input:"):] + "}}", "required": True})
                continue
            key, _, idx = cid.rpartition("#")  # step keys may contain "#" themselves
            by_step.setdefault(key, {})[int(idx)] = True
        if custom:
            extra.append({"type": "text_present", "target": custom, "required": True})
        steps = _steps(doc)
        keyed = dict(zip(step_keys(steps), steps))
        if extra and keyed:
            by_step.setdefault(list(keyed)[-1], {})
        for key, idxs in by_step.items():
            step = keyed.get(key)
            if step is None:
                continue
            assertions = [dict(a) for a in (step.get("validation") or {}).get("assertions") or []]
            for j in idxs:
                if j < len(assertions):
                    assertions[j]["required"] = True
            if key == list(keyed)[-1]:
                assertions += extra
            ops.append({"op": "patch", "step_key": key, "patch": {"validation": {"assertions": assertions}}})
        labels = [c["label"] for c in question.get("choices") or [] if c["id"] in selected]
        if custom:
            labels.append(f"You see “{custom}” on the screen")
        return ops, "; ".join(labels) if labels else "No checks needed"

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
                    required_checks.append(f"“{_readable(str(a.get('target') or a.get('text') or a.get('value') or ''))[:60]}” appears")
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
