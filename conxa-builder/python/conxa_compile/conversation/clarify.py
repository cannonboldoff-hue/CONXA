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

from conxa_compile.display_text import clean_name
from conxa_compile.compiler.step_key import step_keys
from conxa_compile.compiler.identity_bundle import _OPTION_CONTENT_TAGS
from conxa_core.config import settings
from conxa_core.models.prose_binding import readable as _readable

MAX_QUESTIONS = 5
MAX_KNOWN_POPUP_QUESTIONS = 2
_ASSERTION_TYPES = {"text_present", "url_changed", "url_pattern", "state_changed"}
_PRIORITY = {
    "coverage": -1, "repeat_rows": -0.5, "upload": 0, "fork": 1, "fork_then": 1.2, "known_popup": 1.5, "confirm": 2, "confirm_row": 2.2, "chosen_values": 2.5,
    "dropped_value": 2.6, "test_followup": 2.7, "values": 3, "relative_date": 3.1, "skip_when_empty": 3.2, "missing_item": 3.3, "output": 3.5, "popup": 4, "done_when": 5,
}
# Ledger passes whose dropped events live on inside a merged step (their value is kept), so a
# value on one of them is not "lost" and is never asked about. "not_from_user" is the page's own
# script (recorder `origin`), which the person never typed or chose, so it is never asked about either.
_MERGE_PASSES = frozenset({"choice_collapsed", "date_collapsed", "not_from_user", "login_page"})
SourceLookup = Callable[[dict[str, Any]], "dict[str, Any] | None"]


# ── naming ──────────────────────────────────────────────────────────────────────────────────


def _action_name(step: dict[str, Any]) -> str:
    a = step.get("action")
    return str(a if isinstance(a, str) else (a or {}).get("action") or "").lower()


_LABEL_FIELDS = ("inner_text", "aria_label", "title", "alt", "placeholder", "label_text")
_LABELLED_VERBS = {
    "click": "Click", "dblclick": "Double-click", "right_click": "Right-click", "hover": "Hover over",
    "fill": "Type into", "type": "Type into", "select": "Choose in", "select_option": "Choose in",
    "set_checkbox": "Tick", "set_radio": "Choose", "extract": "Read",
}
_PLAIN_ACTIONS = {"scroll": "Scroll the page", "upload_intent": "Choose the file to upload", "upload": "Upload a file"}


def _action_title(step: dict[str, Any]) -> str:
    """This one action in words, built in code from what was recorded: the verb plus the label the
    item showed (``Click “New”``), or the page opened. Never a selector. "" when nothing fits."""
    act = _action_name(step)
    if act in _PLAIN_ACTIONS:
        return _PLAIN_ACTIONS[act]
    if act in ("navigate", "goto"):
        url = re.sub(r"^\w+://(www\.)?", "", str(step.get("url") or "")).split("?")[0].rstrip("/")
        return f"Go to {url[:60]}" if url else ""
    if act in _LABELLED_VERBS:
        fp = (step.get("identity_bundle") or {}).get("fingerprint") or {}
        # A dropdown's inner text is its whole option list ("12345 12456 …"), not its name.
        fields = _LABEL_FIELDS[1:] if {str(fp.get("tag") or "").lower(), str(fp.get("role") or "").lower()} & _OPTION_CONTENT_TAGS \
            else _LABEL_FIELDS
        label = clean_name(next((fp.get(f) for f in fields if str(fp.get(f) or "").strip()), "")).rstrip(" :")
        return f"{_LABELLED_VERBS[act]} {_quoted(label)}" if label else ""
    return ""


def step_name(step: dict[str, Any]) -> str:
    """What a person would call this step: never a number, never a selector. One action, one name:
    a step nothing described is named from its own action, not from the steps around it."""
    own = str(step.get("semantic_description") or "").strip()
    if not own or own == str(step.get("intent") or "").strip():     # the bare intent token is not a description
        own = _action_title(step)
    hints = step.get("handler_hints") if isinstance(step.get("handler_hints"), dict) else {}
    times = int(hints.get("repeat_count") or 1)
    suffix = f" ({times} times)" if times >= 3 else ""   # a click kept as "do this N times" says so
    for text in (own, str(step.get("intent") or "").strip()):
        if text:
            text = _readable(re.sub(r"^Step \d+:\s*", "", text))
            if "_" in text and " " not in text:
                text = text.replace("_", " ")
            return text[:1].upper() + text[1:80] + suffix
    return _action_name(step).replace("_", " ").capitalize() or "This step"


def _humanize(binding: str) -> str:
    return binding.replace("_", " ").strip().capitalize()


def _address_from_pattern(pattern: str) -> str:
    """A compiled address pattern (generalize_url_pattern) the way a person reads an address:
    ``^https://shop\\.test/users/[^/?#]+`` -> ``shop.test/users/…``."""
    text = re.sub(r"\[\^[^\]]*\][+*]", "…", pattern.strip().lstrip("^").rstrip("$"))
    text = re.sub(r"^https?://", "", text.replace("\\", ""))
    return _readable(text).strip()


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


def _fork_question(key: str, state: dict[str, Any], fork_copy: ForkCopy | None, fork_states: list[Any] | None = None) -> dict[str, Any]:
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
    recorded = next((o for o in options if o.get("recorded") and o.get("steps")), None)
    if recorded is not None and len([s for s in (fork_states or []) if isinstance(s, dict)]) == 1:
        label = str(recorded.get("label") or recorded["id"])
        choices.append({
            "id": "always", "label": f"It shows up every time: always click \u201c{label}\u201d",
            "detail": ("Careful: this can change or delete things. " if recorded.get("role") == "consequential" else "")
            + "It becomes an ordinary step. If the popup does not show up, the run stops there.",
        })
    choices += [
        {"id": "by_input", "label": "Let whoever uses it decide each time",
         "detail": "They get a question to answer before it starts, so it can go a different way each time."},
        {"id": "ask_human", "label": "Stop and ask a person each time", "detail": "It waits on the popup until someone tells it what to do."},
        {"id": "stop", "label": "Stop completely", "detail": "Treat this popup as a problem and end here."},
    ]
    # A box with no title of its own is named by its buttons: "a message box called “a popup”" read as nonsense (WF-4 A).
    title = (f"A message box called “{popup}” can pop up here. What should happen when it does?" if sig.get("title")
             else f"A message box{_with_buttons(sig.get('button_labels'))} can pop up here. What should happen when it does?")
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


def _fork_then_question(key: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Asked once a popup is left to the person running it (or to a person): what should happen if they
    pick something OTHER than what was recorded. The recorded choice rejoins the recorded steps by
    definition; any other choice may leave the page somewhere the rest of the steps do not fit. Choices
    are fixed facts; the answer becomes ``then`` on those options."""
    options = [o for o in state.get("options") or [] if isinstance(o, dict) and o.get("id")]
    others = [o for o in options if not o.get("recorded") and not o.get("then")]
    if not others:
        return None
    sig = state.get("signature") or {}
    popup = str(sig.get("title") or "a popup")
    recorded = next((str(o.get("label") or o["id"]) for o in options if o.get("recorded")), "")
    names = ", ".join(f"“{o.get('label') or o['id']}”" for o in others[:3])
    return _question(
        "fork_then", f"fork_then:{key}:{state['id']}",
        f"If someone picks {names} instead{f' of “{recorded}”' if recorded else ''}, do the next steps still make sense?",
        (f"The “{popup}” message box" if sig.get("title") else "This message box") + " can go different ways. You only recorded one of them, so I can\u2019t be sure the steps after it fit the others.",
        [
            {"id": "same", "label": "Yes, carry on with the same steps", "detail": "It continues exactly as recorded after any choice."},
            {"id": "stop", "label": "No, stop there", "detail": "If someone picks another choice, the choice is made and the run stops. Nothing after it runs."},
            {"id": "end", "label": "No, that finishes the job", "detail": "If someone picks another choice, the run ends there and counts as done."},
        ],
        step_keys_=[key], fork_state_id=state["id"], other_option_ids=[o["id"] for o in others],
    )


def _with_buttons(labels: Any) -> str:
    """ " with the buttons “Yes, Delete” and “No, Cancel”" for a box with no title, or "" when it has no buttons."""
    names = [f"“{str(l).strip()}”" for l in (labels or []) if str(l).strip() and str(l).strip() not in ("×", "x", "X")][:3]
    if not names:
        return ""
    return f" with the button{'s' if len(names) > 1 else ''} " + (" and ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0])


def _known_popup_question(key: str, name: str, popup: dict[str, Any]) -> dict[str, Any]:
    """A popup the app map saw after this same step in other workflows. Fixed wording: the map holds
    no model-written text, and the only things offered are adding it (asks a person by default) or not."""
    title = str(popup.get("title") or "")
    labels = [str(l) for l in popup.get("button_labels") or []][:4]
    called = f" called “{title}”" if title else _with_buttons(labels)
    return _question(
        "known_popup", f"known_popup:{key}:{popup.get('id')}",
        f"Your other workflows sometimes see a message box{called} after “{name}”. Should this one be ready for it too?",
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


def _is_irreversible(step: dict[str, Any]) -> bool:
    bundle = step.get("identity_bundle") if isinstance(step.get("identity_bundle"), dict) else {}
    return step.get("consequence") == "irreversible" or bool(step.get("destructive")) or bool(bundle.get("destructive"))


def irreversible_without_proof(doc: dict[str, Any]) -> list[str]:
    """Keys of the steps that cannot be undone and whose completion check nobody has agreed to or tested.
    The "How can we tell it worked?" question cannot be assumed or deferred while this list is not empty, and
    publishing is blocked until it is (workflows.py::_require_proof_for_irreversible). Only a pack that carries
    completion checks at all is held to this: an older compile has nothing to confirm."""
    from conxa_compile.compiler.effects import has_real_proof

    steps = _steps(doc)
    if not any(isinstance((s.get("validation") or {}).get("effect"), dict) for s in steps):
        return []
    keys = step_keys(steps)
    return [keys[i] for i, s in enumerate(steps)
            if _is_irreversible(s) and not has_real_proof((s.get("validation") or {}).get("effect"), strict=True)]


def _effect_choices(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """One tickable "this is how I know it worked" per step that starts work, from the completion checks the
    compiler built out of the recording (Proof of Done). Ticking agrees to them; it never writes one."""
    from conxa_compile.compiler.effects import _finish_checks, person_sentence

    steps = _steps(doc)
    keys = step_keys(steps)
    choices: list[dict[str, Any]] = []
    used: list[str] = []
    for i, s in enumerate(steps):
        effect = (s.get("validation") or {}).get("effect")
        live = _finish_checks(effect)
        if not live or all(c.get("confirmed") for c in live):
            continue
        sentence = person_sentence(effect)
        if not sentence:
            continue
        choices.append({
            "id": f"effect:{keys[i]}",
            "label": f"After the step “{step_name(s)[:60].rstrip(' .,;:')}”, {sentence}",
            "detail": "If this is not true afterwards, the run says it couldn\u2019t confirm it worked instead of saying it\u2019s done.",
            "selected": True,
        })
        used.append(keys[i])
    return choices, used


def _unproven_end(doc: dict[str, Any]) -> str | None:
    """Key of the last step that acts and can hold checks, when nothing proves it (no required check, no completion
    checks) although the recording saw the screen change after it (video_context). Only decides WHETHER to ask: the
    check itself is the words the person types. None otherwise."""
    steps = _steps(doc)
    for key, s in reversed(list(zip(step_keys(steps), steps))):
        if _action_name(s) == "navigate" or not _can_hold_checks(s):
            continue
        validation = s.get("validation") or {}
        effect = validation.get("effect")
        # Only a check the runtime can count as "it finished" (words on the screen that were not there before,
        # proof.js::assertionRung) proves the end. "“Toggle Me” is on the page" is true before and after the click
        # alike, so a step carrying only that still ended "couldn't confirm" and the person was never asked (WF-2 J).
        proven = any(isinstance(a, dict) and a.get("required") and a.get("type") == "text_present"
                     for a in validation.get("assertions") or []) \
            or (isinstance(effect, dict) and effect.get("checks"))
        seen = s.get("video_context") if isinstance(s.get("video_context"), dict) else {}
        after = str(seen.get("screen_after") or "").strip()
        changed = bool(after) and after != str(seen.get("screen_before") or "").strip()
        return key if changed and not proven else None
    return None


def _done_when_choices(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """The tickable "it worked" checks for the doc as it stands now: assertions the compiler already
    wrote (not yet required) plus one "this input shows up" check per run input. Text comes from
    ``target`` first -- the field the runtime checks and a "changes each run" answer rewrites."""
    steps = _steps(doc)
    keys = step_keys(steps)
    checks: list[dict[str, Any]] = []
    for i in range(max(0, len(steps) - 3), len(steps)):
        assertions = (steps[i].get("validation") or {}).get("assertions") or []
        # A step that landed on another page (its landing is a url_pattern) was last seen on the page it left: words
        # from that moment are often a search box's suggestions, gone once it works. Offered, never ticked for the
        # person (WF-4 Leg C: ticked, they failed every run). An unticked check cannot fail a run.
        landed = any(isinstance(a, dict) and a.get("type") == "url_pattern" for a in assertions)
        for j, a in enumerate(assertions):
            if isinstance(a, dict) and a.get("type") in _ASSERTION_TYPES and not a.get("required"):
                checks.append({"id": f"{keys[i]}#{j}", "step_key": keys[i], "assertion": a, "landed": landed})

    def describe(a: dict[str, Any]) -> str:
        t = a.get("type")
        if t == "text_present":
            return f"You see the words {_quoted(str(a.get('target') or a.get('text') or a.get('value') or ''))} on the screen"
        if t == "url_changed":
            return "The web address at the top of the browser changes"
        if t == "url_pattern":
            # The compiled pattern lives in `target` (build.py); it used to be read from keys it never has: a blank label.
            shown = _address_from_pattern(str(a.get("target") or a.get("pattern") or a.get("value") or ""))
            return f"The web address looks like {shown[:60]}" if shown else "The web address at the top of the browser changes"
        return "The screen changes"

    choices = [{"id": c["id"], "label": describe(c["assertion"]), "detail": "",
                "selected": c["assertion"].get("type") == "text_present" and not c["landed"]} for c in checks]
    effect_choices, effect_keys = _effect_choices(doc)
    # Offered only beside a real check, so an input-only workflow isn't pestered. A step's completion checks are one:
    # on an app that answers in the page alone (no request to watch), "what I typed shows up" is the proof left.
    if choices or effect_choices:
        referenced = " ".join(str(a.get("target") or "") for s in steps
                              for a in (s.get("validation") or {}).get("assertions") or [] if isinstance(a, dict))
        for inp in doc.get("inputs") or []:
            iid = str(inp.get("id") or "") if isinstance(inp, dict) else ""
            if iid and iid != "file_path" and "{{" + iid + "}}" not in referenced and not any(
                    c["assertion"].get("type") == "text_present" and "{{" + iid + "}}" in str(c["assertion"].get("target") or "")
                    for c in checks):
                choices.append({"id": f"input:{iid}", "label": f"You see the {_humanize(iid).lower()} on the screen",
                                "detail": "Makes sure what was typed in appears on the screen at the end.", "selected": False})
    return effect_choices + choices, sorted({c["step_key"] for c in checks} | set(effect_keys))


# Kinds whose body quotes raw typed values, and choice labels that are page text or value names:
# those are never sent to a model, never reworded. Everything else may have its wording rewritten.
_NO_REWORD_KINDS = {"dropped_value", "test_followup", "input_check"}
_QUOTED = re.compile(r"“[^”]+”")


def situation_sig(q: dict[str, Any]) -> str:
    """What a question is about, so a settled answer can tell whether it still fits after a rebuild: the kind,
    its choices and its steps, plus the wording for kinds that quote recorded values (those are never reworded)."""
    import hashlib
    import json

    parts: list[Any] = [q.get("kind"), [str(c.get("id")) for c in q.get("choices") or []], list(q.get("step_keys") or [])]
    if q.get("kind") in _NO_REWORD_KINDS:
        parts.append(q.get("body"))
    return hashlib.sha1(json.dumps(parts, default=str).encode("utf-8")).hexdigest()[:16]


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


def apply_reworded(questions: list[dict[str, Any]], reworded: Any, report: dict[str, Any] | None = None) -> None:
    """Fold a model's friendlier wording into ``questions`` (in place), field by field.

    Only title, body and the label/detail of existing single-choice options can change; ids, order,
    what each choice does and every key besides those are code's. A field is taken only when it is a
    non-empty string of sane length, has no ``{{`` placeholder and keeps every “quoted” item of the
    original (so a recorded name or value can't be dropped or invented); anything else keeps the
    template text. Never raises. ``report`` (llm/model_ledger) counts every field that kept its
    template: ``shape.malformed_reply``, ``reference.unknown_question``, ``override.reword_rejected``."""
    from conxa_compile.llm.model_ledger import log as _ledger_log  # noqa: PLC0415 -- clarify is imported very early

    if not isinstance(reworded, dict) or not isinstance(reworded.get("questions"), list):
        if reworded:
            _ledger_log(report, "shape", "malformed_reply")
        return
    by_id = {str(r.get("id")): r for r in reworded["questions"] if isinstance(r, dict)}
    allowed = {item["id"] for item in reword_payload(questions)}

    def ok(new: Any, old: str, limit: int, qid: str) -> bool:
        good = (isinstance(new, str) and 0 < len(new.strip()) <= limit and "{{" not in new
                and all(m in new for m in _QUOTED.findall(old)))
        if not good:
            _ledger_log(report, "override", "reword_rejected", qid)
        return good

    for q in questions:
        r = by_id.get(q["id"])
        if q["id"] in allowed and r is None:
            _ledger_log(report, "reference", "unknown_question", q["id"])
            continue
        if r is None or q["id"] not in allowed:
            continue
        if ok(r.get("title"), q["title"], 200, q["id"]):
            q["title"] = r["title"].strip()
        if ok(r.get("body"), q["body"], 500, q["id"]):
            q["body"] = r["body"].strip()
        if q.get("multi"):
            continue
        new_choices = {str(c.get("id")): c for c in r.get("choices") or [] if isinstance(c, dict)}
        for c in q["choices"]:
            n = new_choices.get(str(c["id"]))
            if n is None or str(c["id"]).startswith("option:"):
                continue
            if ok(n.get("label"), c["label"], 90, q["id"]):
                c["label"] = n["label"].strip()
            if (n.get("detail") == "" and not c.get("detail")) or ok(n.get("detail"), c.get("detail", ""), 220, q["id"]):
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
    kinds: frozenset[str] | set[str] | None = None,
    typed_fixed_events: frozenset[Any] | set[Any] | None = None,
    limits: dict[str, int] | None = None,
    skip_events: frozenset[Any] | set[Any] | None = None,
) -> list[dict[str, Any]]:
    """Find the places worth asking about in a compiled skill document.

    ``source_lookup(step)`` returns the step's originally recorded event (or None): it supplies
    the recorded literal a "keep it fixed" answer would restore, and the recording's own sequence
    number so a value the person already marked "Changes each run?" while recording is not asked
    about again. ``answered_by_recording`` is that hint map (``{"<sequence>": bool}``).

    ``kinds`` keeps only those question kinds (the flow builds the ones an earlier answer must be
    replayed onto first, then the rest from the document replay left). ``typed_fixed_events`` are
    recorded event ids (sequence numbers) of typed text already judged "stays as recorded": their
    ``values`` choice is left unticked. ``limits`` (out-param) counts, per kind, the places found
    after that kind's cap was reached, so a cut is said out loud instead of silently under-asking.
    ``skip_events`` are recorded event ids already asked about before the build (a dropdown / radio / date
    the understanding put to the person): their ``values`` choice is not offered a second time.
    """
    steps = _steps(doc)
    keys = step_keys(steps)
    look = source_lookup or (lambda _s: None)
    hinted = answered_by_recording or {}
    fixed_events = {str(e) for e in typed_fixed_events or ()}
    skipped_events = {str(e) for e in skip_events or ()}
    out: list[dict[str, Any]] = []

    def cut(kind: str) -> None:
        if limits is not None:
            limits[kind] = limits.get(kind, 0) + 1

    # 0a ─ the same entry made several times with different details (five records added one after another): repeat
    #      it for a list given each run, or always these same ones? Code found the repeat; the person decides; code
    #      rewrites (wrap_repeated_entries). The values inside the repeat are this question's, not the "values" one's.
    def typed(s: dict[str, Any]) -> str | None:
        v = ((look(s) or {}).get("action") or {}).get("value")
        return None if v is None or v == "{{REDACTED}}" else str(v)

    from conxa_compile.compiler.loop_suggestion import detect_repeated_entries

    repeat = detect_repeated_entries(steps, typed)
    repeat_keys: set[str] = set()
    if repeat:
        first = keys.index(repeat["start_key"])
        repeat_keys = set(keys[first:first + repeat["block_len"] * repeat["count"]])
        n = repeat["count"]
        cols = ", ".join(_humanize(c["name"]).lower() for c in repeat["columns"])
        out.append(_question(
            "repeat_rows", f"repeat_rows:{repeat['start_key']}",
            f"You entered {n} items the same way, each with a different {cols}. Each time this runs, should it enter a list it is given?",
            f"It can repeat the same steps for every row of a list. The list starts with the {n} you entered.",
            [
                {"id": "list", "label": "Yes, enter each row of a list it is given",
                 "detail": f"Whoever uses it can change the list. It starts with your {n}."},
                {"id": "same", "label": f"No, always enter these same {n}", "detail": "It repeats exactly what you recorded."},
            ],
            step_keys_=[repeat["start_key"]], suggestion=repeat,
        ))

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
    # A value the workflow still enters is not lost: typing the same words twice (a field re-reported on Enter) is
    # folded into one step that carries them, as an input's recorded value or as fixed text.
    still_used = {str(i.get("default")) for i in doc.get("inputs") or [] if isinstance(i, dict) and i.get("default") not in (None, "")}
    still_used |= {str(s.get("value")) for s in steps if s.get("value") and "{{" not in str(s.get("value"))}
    still_used |= {str(((look(s) or {}).get("action") or {}).get("value")) for s in steps
                   if ((look(s) or {}).get("action") or {}).get("value")}  # what each kept step was recorded typing
    lost = [d for d in dropped or []
            if isinstance(d, dict) and d.get("value") and d.get("pass") not in _MERGE_PASSES
            and str(d.get("value")) not in still_used]
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
        # Nobody can be asked inside a repeated step (the runtime refuses), so a repeated step that can't be undone is
        # approved once, before the repeat starts.
        body = (s.get("for_each") or {}).get("steps") if isinstance(s.get("for_each"), dict) else None
        inner = next((b for b in body or [] if isinstance(b, dict) and b.get("consequence") == "irreversible"), None)
        if (s.get("consequence") != "irreversible" and inner is None) or i == 0:
            continue
        prev = steps[i - 1]
        from conxa_compile.editor.patch_gate import is_confirm_only_span

        if _action_name(prev) == "agent_span" and is_confirm_only_span(prev):
            continue  # already guarded
        name = step_name(inner if inner is not None else s)
        if inner is not None:
            out.append(_question(
                "confirm", f"confirm:{keys[i]}",
                f"Each row of the list ends with “{name}”, which can’t be undone. Should someone approve before it starts?",
                "It will stop once before the first row, say what is about to happen, and wait for a yes or no. "
                "It can’t stop between rows.",
                [
                    {"id": "yes", "label": "Yes, ask for approval first", "detail": "One yes or no question before the list starts."},
                    {"id": "no", "label": "No, just do it", "detail": "It goes ahead without asking."},
                ],
                step_keys_=[keys[i]], step_name=name,
                prompt=f"About to go through every row of the list, each ending with “{name}”, which can’t be undone. "
                       "Should it go ahead?",
            ))
            continue
        out.append(_question(
            "confirm", f"confirm:{keys[i]}", f"“{name}” can\u2019t be undone. Should someone approve it before it happens?",
            f"It will stop just before “{name}”, show what is about to happen, and wait for a yes or no from whoever is using it.",
            [
                {"id": "yes", "label": "Yes, ask for approval first", "detail": f"A yes or no question appears before “{name}”."},
                {"id": "no", "label": "No, just do it", "detail": "It goes ahead without asking."},
            ],
            step_keys_=[keys[i]], step_name=name,
        ))

    # 2b ─ a step that can't be undone and acts on one row of a list: is it the right row? The publish gate and the
    #      editor both require this confirmation; without a question nothing in the chat ever asked for it.
    for i, s in enumerate(steps):
        eb = s.get("entity_binding")
        if (s.get("consequence") != "irreversible" or not isinstance(eb, dict) or not eb.get("container_selector")
                or eb.get("confirmed")):
            continue
        ident = str(eb.get("identifier") or "").strip()
        if not ident or ident.startswith("{{"):
            continue  # a run input names the row each time; nothing recorded to confirm
        name = step_name(s)
        out.append(_question(
            "confirm_row", f"confirm_row:{keys[i]}", f"Is the row with “{_readable(ident)[:60]}” the one “{name}” should act on?",
            "This step can’t be undone, so it only ever acts on the row with that text. If the text matches no row, or more than one, it stops instead of guessing.",
            [
                {"id": "yes", "label": "Yes, that’s the row", "detail": "Locks it to that row."},
                {"id": "no", "label": "No, it should pick a different row", "detail": "Nothing changes here. Choose the right row in the editor before this can be published."},
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
        if seq is not None and str(seq) in skipped_events:
            continue  # asked before the build
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
        if keys[i] in repeat_keys:
            continue  # asked by the repeat question
        candidates.append({"binding": str(binding), "step_key": keys[i], "recorded": recorded,
                           "fixed_by_event": seq is not None and str(seq) in fixed_events})
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
              "selected": c["binding"] not in fixed_typed and not c["fixed_by_event"]} for c in uniq],
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
            cut("output")
            continue
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
                cut("relative_date")
                continue
            when = ("today" if days == 0 else f"{abs(days)} day{'s' if abs(days) != 1 else ''} "
                    f"{'after' if days > 0 else 'before'} today")
            name = step_name(s)
            binding = str(s.get("input_binding") or "")
            recorded = str(picker.get("recorded_value") or "")[:10]
            # All three honest answers, whatever was answered before: questions are made together, so "which parts change"
            # may still turn the date into an input, or out of one, after this card was made (WF-2 E: "always this date"
            # was missing and "different every time" was the only other answer).
            out.append(_question(
                "relative_date", f"relative_date:{keys[i]}", f"Is “{name}” always {when}?",
                f"You picked {picked.strftime('%d %b %Y')}, and today is {today.strftime('%d %b %Y')}. "
                "If it is always counted from the day someone uses it, nobody has to type a date.",
                [
                    {"id": "relative", "label": f"Yes, always {when}", "detail": "I’ll work out the date on the day. Nothing to type."},
                    {"id": "fixed_date", "label": f"No, always {picked.strftime('%d %b %Y')}", "detail": "It stays exactly as you recorded it."},
                    {"id": "fixed_input", "label": "No, it is different every time", "detail": "Whoever uses it will pick the date."},
                ],
                step_keys_=[keys[i]], days=days, binding=binding, recorded_value=recorded,
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
                cut("skip_when_empty")
                continue
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
                cut("missing_item")
                continue
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
                out.append(_fork_question(keys[i], state, fork_copy, (s.get("outcome_fork") or {}).get("states")))

    # 1b' ─ a popup left to a person: the recorded choice rejoins the steps, the others may not.
    for i, s in enumerate(steps):
        if _action_name(s) != "outcome_fork":
            continue
        for state in ((s.get("outcome_fork") or {}).get("states") or []):
            if not (isinstance(state, dict) and state.get("id")) or state.get("then_reviewed"):
                continue
            policy = state.get("policy") or {}
            if policy.get("reviewed") and policy.get("mode") in ("by_input", "ask_human"):
                q = _fork_then_question(keys[i], state)
                if q is not None:
                    out.append(q)

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
    unproven_end = None if done_choices else _unproven_end(doc)
    if done_choices:
        out.append(_question(
            "done_when", "done_when", "How can we tell it worked?",
            "At the end of your recording I noticed these. Tick the ones that should be true when it works. If a ticked one is not true, it counts as failed. "
            "You can also type your own below.",
            done_choices, multi=True, step_keys_=done_keys, needs_proof=bool(irreversible_without_proof(doc)),
            # What each input was when recorded: if a later answer fixes it ("nothing changes"), a ticked
            # "you see it on the screen" checks those words, not an input that no longer exists.
            recorded={str(st["input_binding"]): v for st in steps
                      if st.get("input_binding") and (v := typed(st)) is not None},
        ))
    elif unproven_end:
        # Nothing to tick, and nothing proves the last step: its result is only on the screen (a tab's panel, a
        # message). Without asking, every test ends "couldn't confirm" and the person was never given the chance.
        out.append(_question(
            "done_when", "done_when", "How can we tell it worked?",
            "Type a few words you see on the screen once it has worked, like a heading or a message. "
            "If you leave this empty, a test can only say it ran, not that it worked.",
            [], multi=True, step_keys_=[unproven_end], needs_proof=bool(irreversible_without_proof(doc)),
        ))

    if kinds is not None:
        out = [q for q in out if q["kind"] in kinds]
    out.sort(key=lambda q: _PRIORITY[q["kind"]])
    # ``limit=None`` returns everything: the compile flow asks the first MAX_QUESTIONS and keeps the
    # rest as ``queued`` rather than dropping them (see compile_flow._flow_after_compile).
    return out if limit is None else out[:limit]


# ── answering ───────────────────────────────────────────────────────────────────────────────


def _can_hold_checks(step: dict[str, Any]) -> bool:
    """Whether the patch gate lets this kind of step carry outcome checks (the gate's own per-kind key sets)."""
    from conxa_compile.compiler.action_semantics import action_name
    from conxa_compile.editor.action_registry import action_spec
    from conxa_compile.editor.capability_manifest import editable_fields

    kind = action_name(step).lower()
    return not action_spec(kind).marker and "validation.assertions" in editable_fields(kind)


def _replace_token(value: Any, token: str, literal: str) -> Any:
    if isinstance(value, str):
        return value.replace(token, literal)
    if isinstance(value, list):
        return [_replace_token(v, token, literal) for v in value]
    if isinstance(value, dict):
        return {k: _replace_token(v, token, literal) for k, v in value.items()}
    return value


def _restore_literal_patch(step: dict[str, Any], binding: str, literal: str) -> dict[str, Any]:
    """Patch a step typing ``{{binding}}`` back to the recorded words, and every check on it that names the input:
    plain checks and completion checks alike (WF-3 A: a "the entry shows up" probe kept {{department_5}} alive)."""
    token = "{{" + binding + "}}"
    patch: dict[str, Any] = {"value": literal}
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    if token in str(validation):
        patch["validation"] = _replace_token(validation, token, literal)
    return patch


def _words_in(text: str) -> str:
    return " ".join(text.split()).casefold()


ALREADY_SHOWN = "already-shown"  # never a step key (those are a hash and #n)


def _press_time_text(ev: dict[str, Any]) -> str:
    """What the recorder saw around the element as the press began, before the action could change anything."""
    extras = ev.get("extras") if isinstance(ev.get("extras"), dict) else {}
    ctx = ev.get("context") if isinstance(ev.get("context"), dict) else {}
    target = ev.get("target") if isinstance(ev.get("target"), dict) else {}
    parts = [str(extras.get("section_heading") or ""), str(ev.get("surrounding_text") or ""), str(target.get("inner_text") or "")]
    parts += [str(a.get("element") or "") for a in ev.get("anchors") or [] if isinstance(a, dict)]
    parts += [str(x).split(":", 1)[-1] for x in ctx.get("siblings") or []]
    return " | ".join(parts)


def step_where_words_appear(doc: dict[str, Any], words: str) -> str | None:
    """The step after which ``words`` were on the screen while recording (its step key); "" when the saved pages were
    read and none shows them, None when there is no recording to read or no step brought them up, ``ALREADY_SHOWN`` when they
    were on the screen before the step that would hold them. Typed as "how we
    know it worked", they belong on the step that brought them up, not on the last step: WF-4 Leg B's "Thank you for
    your order" was checked after logging out, where it is gone.

    A saved page is taken a moment after its step, often after the next step too (one copy is reused while nothing
    changes), so it is placed by the last step that shares it. When that page has another address than the first step
    sharing it, the words came with that address: their step is the last thing done before it first showed. Otherwise
    it is the last press (click, Enter) after the last copy without the words, up to the first copy with them; with none
    there, the first press after it that still shares that copy; with none at all, the first other action there.
    shortcut: placed by copy order, not capture time (the recorder saves none); revisit if a check lands one step off."""
    from conxa_compile.compiler.navigation_effects import _INTERACTION_ACTIONS
    from conxa_core.storage.session_events import read_session_events
    from conxa_core.storage.snapshots import read_dom_snapshot
    from conxa_compile.compiler.selector_filters import _parse_soup

    session = str((doc.get("meta") or {}).get("source_session_id") or "")
    needle = _words_in(words)
    if not session or not needle:
        return None
    try:
        events = read_session_events(session)
    except Exception:  # noqa: BLE001 — no recording to read: the caller keeps its own placement
        return None

    def visible_text(html: str) -> str:
        soup = _parse_soup(html)
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        # A result panel kept hidden until the step shows it (ParaBank's "Transfer Complete!"): not on the screen yet.
        for tag in soup.find_all(lambda t: t.has_attr("hidden") or t.get("aria-hidden") == "true" or re.search(
                r"display\s*:\s*none|visibility\s*:\s*hidden", str(t.get("style") or ""), re.I)):
            if not tag.decomposed:
                tag.decompose()
        return _words_in(soup.get_text(" "))

    def action(i: int) -> str:
        return str((events[i].get("action") or {}).get("action") or "").lower()

    page = [str((e.get("page") or {}).get("url") or "").split("#", 1)[0] for e in events]
    hashes = [str((e.get("snapshot") or {}).get("dom_hash") or "") for e in events]
    shows: dict[str, bool] = {}
    for h in set(hashes) - {""}:
        html = read_dom_snapshot(session, h) or ""
        shows[h] = needle in visible_text(html) if html else False
    first = next((i for i, h in enumerate(hashes) if shows.get(h)), None)
    if first is None:
        return "" if shows else None
    last_sharing = max(i for i, h in enumerate(hashes) if h == hashes[first])
    seen_on = page[last_sharing]
    if page[first] != seen_on:
        arrived = next(i for i in range(first, len(events)) if page[i] == seen_on)
        cause = next((i for i in range(arrived - 1, -1, -1) if action(i) in _INTERACTION_ACTIONS), None)
    else:
        without = max((i for i in range(first) if hashes[i] and not shows.get(hashes[i])), default=-1)
        pressed = [i for i in range(without + 1, last_sharing + 1) if action(i) in ("click", "dblclick", "keyboard_shortcut", "submit")]
        acted = [i for i in range(without + 1, last_sharing + 1) if action(i) in _INTERACTION_ACTIONS]
        upto = [i for i in pressed if i <= first]
        # Late beats early: a check one step late still sees words that stay on the screen; one step early fails.
        cause = upto[-1] if upto else (pressed or acted or [None])[0]
    if cause is None:
        return None  # on screen before anything was done: no step brought them up
    # On the screen already when the person pressed (the recorder describes the press as the page was then: its section
    # heading, the text around it): seeing them afterwards says nothing about whether it worked. WF-5: a page heading
    # ("Five downloads") was accepted as the proof. Saved copies cannot tell: one copy may be taken after the press.
    if needle in _words_in(_press_time_text(events[cause])):
        return ALREADY_SHOWN
    seq = (events[cause].get("extras") or {}).get("sequence")
    steps = _steps(doc)
    best = None
    for key, step in zip(step_keys(steps), steps):
        src = step.get("source_event")
        if isinstance(src, int) and isinstance(seq, int) and src <= seq:
            if src == seq and not _can_hold_checks(step):
                return ""  # brought up by a step that holds no checks (a popup's answer): the caller says so
            if _can_hold_checks(step):
                best = key
    return best


def plan_answer(question: dict[str, Any], answer: dict[str, Any], doc: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Turn an answer into ops for existing commands, plus the receipt line shown once applied.

    ``answer`` is ``{"choice": id}`` for a single-choice question or ``{"selected": [ids]}`` for a
    multi-choice one. Ops (run in order by the handler):

    ``{"op": "patch", "step_key", "patch"}``        — ``cmd_patch_step`` (the gated mutation)
    ``{"op": "insert_review", "before_step_key", "prompt", "title"}`` — insert + configure a confirm-only (question-only) agent span
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

    if kind == "repeat_rows":
        suggestion = question.get("suggestion") or {}
        if answer.get("choice") == "list":
            ops.append({"op": "wrap_rows_loop", "suggestion": suggestion})
            return ops, f"Enters each row of a list it is given (starts with your {suggestion.get('count')})"
        # Keep every recorded value in the repeated steps (they are this question's, never asked as "values"), and in
        # any check elsewhere that names one (a last step's "first name appears"), or the input stays alive.
        steps = _steps(doc)
        keyed = list(zip(step_keys(steps), steps))
        start = next((i for i, (k, _) in enumerate(keyed) if k == suggestion.get("start_key")), None)
        literal_of: dict[str, str] = {}
        if start is not None:
            block_len = int(suggestion.get("block_len") or 0)
            for r, row in enumerate(suggestion.get("rows") or []):
                at = {int(c["offset"]): row.get(c["name"]) for c in suggestion.get("columns") or []}
                at.update({int(f["offset"]): f["value"] for f in suggestion.get("fixed") or []})
                for off, literal in at.items():
                    i = start + r * block_len + off
                    step = keyed[i][1] if i < len(keyed) else {}
                    binding = str(step.get("input_binding") or "")
                    if literal is not None and binding and step.get("value") == "{{" + binding + "}}":
                        literal_of[binding] = str(literal)
        for key, step in keyed:
            patch: dict[str, Any] = {}
            for binding, literal in literal_of.items():
                one = _restore_literal_patch({**step, **patch}, binding, literal)
                if not (step.get("input_binding") == binding and step.get("value") == "{{" + binding + "}}"):
                    one.pop("value")
                patch.update(one)
            if patch:
                ops.append({"op": "patch", "step_key": key, "patch": patch})
        if literal_of:
            ops.append({"op": "drop_inputs", "ids": sorted(literal_of)})
        return ops, f"Always enters these same {suggestion.get('count')}"

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
        if answer.get("choice") == "fixed_date":
            if question.get("recorded_value"):
                ops.append({"op": "patch", "step_key": keys[0], "patch": {"value": question["recorded_value"]}})
            if question.get("binding"):
                ops.append({"op": "drop_inputs", "ids": [question["binding"]]})
            return ops, "Always the date you picked"
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
                        "prompt": question.get("prompt") or f"About to run “{name}”, which can’t be undone. Should it go ahead?"})
            return ops, "Someone approves it first"
        return ops, "Goes ahead without asking"

    if kind == "confirm_row":
        if answer.get("choice") == "yes":
            ops.append({"op": "patch", "step_key": keys[0], "patch": {"entity_binding": {"confirmed": True}}})
            return ops, "Only acts on that row"
        return ops, "Needs the right row chosen in the editor"

    if kind == "fork_then":
        how = {"same": "rejoin", "stop": "stop", "end": "end"}.get(str(answer.get("choice") or ""))
        if how is None:
            return ops, "Nothing changed \u2014 that is not one of the choices"
        ops.append({"op": "set_fork_policy", "step_key": keys[0], "state_id": question.get("fork_state_id"),
                    "policy": {"then": {oid: how for oid in question.get("other_option_ids") or []}}})
        return ops, {"rejoin": "Carries on with the same steps", "stop": "Stops there", "end": "Ends there, counted as done"}[how]

    if kind == "fork":
        choice = str(answer.get("choice") or "")
        if choice not in {c["id"] for c in question.get("choices") or []}:
            return ops, "Nothing changed \u2014 that is not one of the choices"
        label = next((c["label"] for c in question.get("choices") or [] if c["id"] == choice), "")
        if choice == "always":
            ops.append({"op": "flatten_fork", "step_key": keys[0], "state_id": question.get("fork_state_id")})
            return ops, "A normal step: " + label.split(": ", 1)[-1]
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
            token = "{{" + binding + "}}"
            for key, step in keyed.items():
                # A check written from the input ("the box holds {{tags}}") keeps the input alive: drop_inputs refuses an
                # id still referenced, so the answer "nothing changes" was silently not applied (WF-2 F). It checks the
                # recorded value instead.
                patch = _restore_literal_patch(step, binding, literal)
                if not (step.get("input_binding") == binding and step.get("value") == token):
                    patch.pop("value")
                if patch:
                    ops.append({"op": "patch", "step_key": key, "patch": patch})
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
        for cid in sorted(selected):
            if cid.startswith("effect:"):
                # Agreeing to the completion checks the compiler built from the recording (Proof of Done).
                ops.append({"op": "confirm_effect", "step_key": cid[len("effect:"):]})
                continue
            if cid.startswith("input:"):
                iid = cid[len("input:"):]
                declared = {str(i.get("id")) for i in doc.get("inputs") or [] if isinstance(i, dict)}
                # Fixed by a later answer: check the recorded words. An input that no longer exists would be required
                # by the built workflow while the person is told it has none (WF-3 A: {{search_term_2}}).
                target = "{{" + iid + "}}" if iid in declared else str((question.get("recorded") or {}).get(iid) or "")
                if target:
                    extra.append({"type": "text_present", "target": target, "required": True})
                continue
            key, _, idx = cid.rpartition("#")  # step keys may contain "#" themselves
            by_step.setdefault(key, {})[int(idx)] = True
        steps = _steps(doc)
        keyed = dict(zip(step_keys(steps), steps))
        # "What you see at the end" belongs on the last step that can hold a check: a trailing scroll, a
        # download/tab marker or a decision step cannot, and the patch gate would refuse it.
        end_key = next((k for k in reversed(keyed) if _can_hold_checks(keyed[k])), None)
        # Typed words go on the step that brought them up on screen while recording; only words no saved page shows
        # are checked at the end, and the receipt says so.
        custom_key = (step_where_words_appear(doc, custom) if custom else None)
        already = custom_key == ALREADY_SHOWN
        custom_key = None if already else custom_key
        custom_unseen = custom_key == ""
        custom_key = custom_key or end_key
        # Words that show only after a popup's answer cannot be checked: a decision step holds no checks, and the step
        # before it runs before the answer (WF-4 Leg A: "Successfully Deleted" after "Yes, Delete").
        order = list(keyed)
        after_fork = bool(custom) and custom_unseen and custom_key in keyed and any(
            _action_name(keyed[k]) == "outcome_fork" for k in order[order.index(custom_key) + 1:])
        if after_fork:
            custom_key = None
        if extra and end_key:
            by_step.setdefault(end_key, {})
        if already:
            custom_key = None
        if custom and custom_key:
            by_step.setdefault(custom_key, {})
        for key, idxs in by_step.items():
            step = keyed.get(key)
            if step is None:
                continue
            assertions = [dict(a) for a in (step.get("validation") or {}).get("assertions") or []]
            for j in idxs:
                if j < len(assertions):
                    assertions[j]["required"] = True
            if key == end_key:
                assertions += extra
            if custom and key == custom_key:
                assertions.append({"type": "text_present", "target": custom, "required": True})
            ops.append({"op": "patch", "step_key": key, "patch": {"validation": {"assertions": assertions}}})
        labels = [c["label"] for c in question.get("choices") or [] if c["id"] in selected]
        if already:
            labels.append(f"I didn’t add “{custom}” as a check: it was already on the screen before that part ran, so "
                          "seeing it can’t tell whether it worked. Tell me words that only show once it’s done")
        elif after_fork:
            labels.append(f"I can’t check “{custom}” yet: it shows after the answer to the popup at the end, and a "
                          "check can’t go there. A test will show whether it appears")
        elif custom and custom_unseen:
            labels.append(f"You see “{custom}” on the screen at the very end. I didn’t see these words while you "
                          "recorded: if they show up earlier, tell me after which step")
        elif custom:
            where = step_name(keyed[custom_key]) if custom_key and custom_key != end_key else ""
            labels.append(f"You see “{custom}” on the screen" + (f" after “{where}”" if where else ""))
        return ops, "; ".join(labels) if labels else "No checks needed"

    return ops, ""


# ── summary ─────────────────────────────────────────────────────────────────────────────────


# Steps that are something an earlier action CAUSED, not something the person did: the file that
# download produced, the tab or browser dialog it raised.
_EFFECT_ACTIONS = frozenset({"download_observed", "tab_open", "dialog_appeared", "dialog_accept", "dialog_dismiss"})


def effect_parents(steps: list[dict[str, Any]]) -> list[int | None]:
    """For each step, the index of the action that caused it, or None. Display only: the steps stay
    separate everywhere they run and are edited; a screen may indent an effect under its cause.
    An effect belongs to the nearest earlier step that is not itself an effect, and only when it
    follows that step directly (or through other effects of it). With navigation folding off,
    nothing is nested: every step shows flat."""
    if not settings.compile_pass("fold_navigations"):
        return [None] * len(steps)
    out: list[int | None] = []
    cause: int | None = None
    for i, s in enumerate(steps):
        user_opened_tab = _action_name(s) == "tab_open" and (s.get("tab") or {}).get("opened_by") == "user"
        if _action_name(s) in _EFFECT_ACTIONS and cause is not None and not user_opened_tab:
            out.append(cause)
        else:
            out.append(None)
            cause = i
    return out


def _assertion_words(step: dict[str, Any]) -> list[str]:
    """A step's required checks in everyday words (selector-type checks are left out)."""
    out: list[str] = []
    for a in (step.get("validation") or {}).get("assertions") or []:
        if isinstance(a, dict) and a.get("required") and a.get("type") in _ASSERTION_TYPES:
            if a.get("type") == "text_present":
                out.append(f"“{_readable(str(a.get('target') or a.get('text') or a.get('value') or ''))[:60]}” appears")
            elif a.get("type") == "url_changed":
                out.append("the page address changes")
            elif a.get("type") == "state_changed":
                out.append("the file is uploaded" if _action_name(step) in ("upload", "upload_intent") else "the page changes")
    return out


def step_checks(step: dict[str, Any]) -> list[str]:
    """What the step is checked against, in plain words, for the summary card's sub-steps: the
    proof-of-done checks (not vetoes, not ones a test run showed dead) plus required assertions."""
    out: list[str] = []
    effect = (step.get("validation") or {}).get("effect") or {}
    for c in effect.get("checks") or []:
        if c.get("role") == "veto" or (c.get("calibrated") is False and c.get("calibration_basis")):
            continue
        if c.get("plain") and c["plain"] != "the file was chosen":
            out.append(str(c["plain"]))
    out += _assertion_words(step)
    return list(dict.fromkeys(out))[:3]


def build_summary(doc: dict[str, Any], *, recorded_actions: int, added_steps: int, name: str, uncertain: int) -> dict[str, Any]:
    """The workflow summary card's data. The counts add up across screens: actions recorded →
    steps compiled → steps now (after answers added some)."""
    steps = _steps(doc)
    names = [step_name(s) for s in steps]
    # The workflow is done when its LAST action did what it should; a check on an earlier step
    # (every page load checks its address changed) says nothing about the goal.
    last = next((s for s in reversed(steps) if _action_name(s) not in _EFFECT_ACTIONS), None)
    required_checks = _assertion_words(last or {})
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
        "step_parents": effect_parents(steps),
        "step_checks": [step_checks(s) for s in steps],
        "done_when": required_checks[:3],
    }
