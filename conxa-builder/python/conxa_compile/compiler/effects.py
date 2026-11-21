"""Proof of Done at build time: what "done" means for a step that starts work.

A step that uploads, downloads, deletes, sends, saves or starts a long job is not done when its button was pressed.
This module gives such a step an ``effect`` (conxa_core ``Effect``): a short checklist of questions the runtime asks
afterwards, each from a separate source of evidence, with the strongest claim a "yes" proves. Everything is built by
code from what the recording really showed (the recorder's post-action watch, the words the app said, the kind of
action); the LLM never writes a check, a selector or a rung. ``second_opinion.py`` may only choose a ``kind`` from the
enumerated list.

What it deliberately does not do:

* name an element: a check never carries a selector, so there is nothing for chat to re-point;
* ask for a claim the runtime cannot prove yet. ``required_rung`` is capped at the strongest rung the probes of this
  version can answer, and ``target_rung`` keeps what the design wants, so a recompile raises it when "the right item is
  really there" becomes provable (docs/Backend-Schema.md section 3.4z);
* demand more than one witness up front. ``quorum.witnesses`` starts at 1 and a passing test run raises it to the
  number of separate witnesses that really answered "yes" (``calibrate_effect``), so a site that never exposes a
  signal cannot make a working workflow stop with "couldn't confirm".
"""

from __future__ import annotations

import re
from typing import Any

from conxa_core.models.skill_spec import (
    EFFECT_RUNGS,
    Effect,
    EffectCheck,
    EffectOn,
    EffectQuorum,
    EffectTime,
)

# Probe types whose answer the runtime of this version can give (runtime/app/effect_probes.js). The others exist in
# the model so a pack never has to change shape when they are built; today they answer "cant_tell".
RUNTIME_PROBES = frozenset({"input_has_files", "request_chain", "failure_signal", "new_text", "download_file", "fresh_look",
                             "url_stays", "row_with_identity", "value_equals", "later_step"})
# A probe the runtime can answer but that has not yet been shown to work on THIS site. It is mined into the checklist,
# but it does not raise required_rung: only a clean test run that saw it answer "yes" does (calibrate_effect). Until then
# a site that never lists the new item cannot make a working workflow stop with "couldn't confirm".
# The four the proof picker may add (compiler/proof_gates.py) are untested on every site until a clean run saw them answer "yes".
_UNTESTED_PROBES = frozenset({"fresh_look", "url_stays", "row_with_identity", "value_equals", "later_step"})

_DELETE_RE = re.compile(r"\b(delete|remove|erase|discard|trash|revoke|cancel subscription|unsubscribe|close account)\b", re.I)
_SEND_RE = re.compile(r"\b(send|publish|pay|purchase|buy|order|checkout|submit|post|share|transfer|invite|email)\b", re.I)
_LONG_RE = re.compile(r"\b(import|export|sync|backup|generate|build|convert|process|run report|download all)\b", re.I)

# Words that say work is finished ("upload complete", "saved", "sent"). Value-free words from the recording are
# compared against these; a status line without one of them is progress or chatter, not a completion message.
_DONE_WORDS = frozenset({
    "complete", "completed", "done", "finished", "saved", "sent", "uploaded", "created", "deleted", "removed",
    "published", "imported", "exported", "added", "updated", "submitted", "success", "successful", "successfully",
    "ready", "shared", "attached", "applied",
})

# What the design wants for each kind: the strongest rung, and how many separate witnesses. The compiler caps the rung
# at what the runtime can prove today; the quorum starts at one and is calibrated by a test run.
_DESIRED: dict[str, tuple[str, int]] = {
    "upload": ("result_exists", 2),
    "download": ("result_exists", 1),
    "delete": ("result_exists", 2),
    "send": ("result_exists", 2),
    "save": ("completed", 1),
    "long_job": ("completed", 1),
    # Picking files on a page that sends them only when a button is pressed: the box holding the files is all it does.
    "choose_files": ("accepted", 1),
}

_RUNG_INDEX = {r: i for i, r in enumerate(EFFECT_RUNGS)}


def _label(ev: dict[str, Any]) -> str:
    t = ev.get("target") or {}
    return " ".join(str(t.get(k) or "") for k in ("inner_text", "aria_label", "name", "label_text"))


def classify_kind(ev: dict[str, Any], *, consequence: str = "", is_commit: bool = False, destructive: bool = False) -> str | None:
    """The kind of work an action starts, or None when it starts none (a typed value, an opened menu, a
    navigation: their direct effect is already their assertion). Deterministic: the action type first, then the
    button's own words, then what the recorder saw (a progress window) and the compiler's consequence label."""
    action = str((ev.get("action") or {}).get("action") or "").lower()
    extras = ev.get("extras") if isinstance(ev.get("extras"), dict) else {}
    if extras.get("choose_files"):
        return "choose_files"
    if extras.get("completes_upload"):
        return "upload"   # the press that sent the files chosen just before (choose_file_presses)
    if action in {"upload", "upload_intent"}:
        return "upload"
    if action == "download_observed":
        return "download"
    if action != "click":
        return None
    label = _label(ev)
    watch = ev.get("post_watch") if isinstance(ev.get("post_watch"), dict) else {}
    irreversible = destructive or consequence == "irreversible"
    if irreversible or is_commit:
        if _DELETE_RE.search(label):
            return "delete"
        if _SEND_RE.search(label):
            return "send"
        if _LONG_RE.search(label) or watch.get("progress_seen"):
            return "long_job"
        return "delete" if irreversible and destructive else ("send" if irreversible else "save")
    if watch.get("progress_seen"):
        return "long_job"
    return None


def _final_words(ev: dict[str, Any]) -> list[list[str]]:
    """Value-free words of what the app said when the work ended, at most two phrases. From the post-action watch when
    a progress window was seen (anything the page said afterwards that was not progress), otherwise from a status line
    that carries a completion word. Alerts and invalid-field messages are failures, never completions."""
    out: list[list[str]] = []
    watch = ev.get("post_watch") if isinstance(ev.get("post_watch"), dict) else {}
    for item in watch.get("final_status") or []:
        words = [str(w) for w in (item.get("words") or []) if isinstance(w, str)]
        if words and item.get("kind") != "alert":
            out.append(words[:6])
    if not out:
        ctx = ev.get("surface_context") if isinstance(ev.get("surface_context"), dict) else {}
        for item in ctx.get("items") or []:
            words = [str(w) for w in (item.get("words") or []) if isinstance(w, str)]
            if item.get("kind") == "status" and any(w in _DONE_WORDS for w in words):
                out.append(words[:6])
    seen: list[list[str]] = []
    for w in out:
        if w not in seen:
            seen.append(w)
    # A page says other things after the work too ("maximized", "more actions alt": a window and a tooltip). A phrase
    # that says the work is finished is the one worth checking for, so those come first and, when there is one, the
    # rest are left out. Only when no phrase has a known completion word (an app in another language) are the others kept.
    done = [w for w in seen if any(x in _DONE_WORDS for x in w)]
    return (done or seen)[:2]


# Words too common to say anything about an app (and short ones). A learned word must be distinctive to be used.
_STOP = frozenset({"about", "after", "again", "being", "could", "there", "these", "those", "which", "while", "would", "their", "other"})


def _usable_word(w: Any) -> bool:
    return isinstance(w, str) and len(w) >= 5 and w.isalpha() and w not in _STOP


def _own_data_words(ev: dict[str, Any]) -> set[str]:
    """Words that came from the person's own data in this step (a typed value, a chosen file's name). The app's
    screens echo them back ("Maven.gitignore" in an upload tray); they say nothing about how the app words things and
    must never be kept in a shipped package or an app map."""
    action = ev.get("action") if isinstance(ev.get("action"), dict) else {}
    parts = [str(action.get("value") or "")]
    extras = ev.get("extras") if isinstance(ev.get("extras"), dict) else {}
    pick = extras.get("file_pick") if isinstance(extras.get("file_pick"), dict) else {}
    parts += [str(n) for n in (pick.get("names") or [])]
    parts.append(str(pick.get("download_filename") or ""))
    return {w for w in re.findall(r"[a-z]+", " ".join(parts).lower()) if len(w) >= 3}


def app_words(ev: dict[str, Any]) -> dict[str, list[str]]:
    """The app's own progress / success / failure words around this step, as value-free single words (the recorder
    already dropped digits, quotes, addresses). Kept on the effect so the app map can learn how THIS app words them,
    in its own language. A word that appears in two groups says nothing and is dropped from both. A word that says
    work is finished is never a progress word, and a word from the person's own data is never kept."""
    watch = ev.get("post_watch") if isinstance(ev.get("post_watch"), dict) else {}
    ctx = ev.get("surface_context") if isinstance(ev.get("surface_context"), dict) else {}
    mine = _own_data_words(ev)
    progress = [w for w in (watch.get("progress_words") or []) if _usable_word(w) and w not in _DONE_WORDS and w not in mine]
    success: list[str] = []
    for words in _final_words(ev):
        success += [w for w in words if _usable_word(w) and w not in mine]
    failure: list[str] = []
    # A step that showed the work finishing teaches no failure words: a loud announcement beside a success ("Maximized"
    # on Google Drive's upload tray) is the app's chrome, not its wording for failure.
    if not success:
        for item in ctx.get("items") or []:
            if item.get("kind") in {"alert", "invalid"}:
                failure += [w for w in (item.get("words") or []) if _usable_word(w) and w not in mine]
    groups = {"progress": progress, "success": success, "failure": failure}
    clash = {w for g in groups.values() for w in g if sum(w in other for other in groups.values()) > 1}
    return {k: sorted({w for w in v if w not in clash})[:12] for k, v in groups.items() if any(w not in clash for w in v)}


def _mine_checks(ev: dict[str, Any], kind: str) -> list[EffectCheck]:
    checks: list[EffectCheck] = []

    def add(**kw: Any) -> None:
        checks.append(EffectCheck(id=f"c{len(checks) + 1}", **kw))

    picked = str((ev.get("action") or {}).get("action") or "").lower() in {"upload", "upload_intent"}
    if kind == "choose_files":
        # Read back from the file box after the pick (runtime handlers.js): never "the step ran".
        add(rung="accepted", witness="target", probe={"type": "input_has_files"}, plain="the files were chosen")
        return checks
    if kind == "upload" and picked:
        add(rung="performed", witness="target", probe={"type": "input_has_files"},
            plain="the file was chosen")
    if kind == "download":
        add(rung="completed", witness="filesystem", probe={"type": "download_file"},
            plain="the file arrived on this computer")
    if kind in {"upload", "delete", "send", "save", "long_job"} and (ev.get("extras") or {}).get("sent_mutating_request") is not False:
        # Layer 1 of the design, true on any website: the app was sent a data-changing request and answered it. Only
        # offered for a step the recorder saw one after (a page that sends nothing never gets this check); a recording
        # with no request log leaves the flag unset and keeps it.
        add(rung="completed", witness="transport", probe={"type": "request_chain", "method": "mutating", "status": "2xx"},
            plain="the app accepted the request")
    mine = _own_data_words(ev)
    for words in _final_words(ev):
        words = [w for w in words if w not in mine]  # a chosen file's name is the person's data, not the app's wording
        if not words:
            continue
        add(rung="completed", witness="renderer", probe={"type": "new_text", "words": words},
            plain=f"the app says “{' '.join(words)}”")
    if kind == "upload" and picked:
        # Go and look: open the same page again in a separate tab and see whether the uploaded file's name is there,
        # and more times than before the upload. Read-only (a GET of the page already open); needs a single named file.
        add(rung="result_exists", witness="fresh_look", probe={"type": "fresh_look", "expect": "file_name"},
            plain="the file shows up when the page is opened again")
    pick_seq = (ev.get("extras") or {}).get("completes_upload")
    if kind == "upload" and not picked and isinstance(pick_seq, int) and not isinstance(pick_seq, bool):
        # A press that sends the files an earlier step chose: the same look, for the file that step picked. The pick is
        # named by its event here; build.py::link_upload_picks turns that into its step key once steps have ids.
        add(rung="result_exists", witness="fresh_look",
            probe={"type": "fresh_look", "expect": "file_name", "file_from_event": pick_seq},
            plain="the file shows up when the page is opened again")
    # Always last: the failure signal. A veto, so an error or refusal beats any amount of good news.
    add(rung="completed", witness="renderer", role="veto", probe={"type": "failure_signal", "scope": "new_since_baseline"},
        plain="the app shows no error or refusal")
    return checks


def build_effect(
    ev: dict[str, Any],
    *,
    consequence: str = "",
    is_commit: bool = False,
    destructive: bool = False,
    kind: str | None = None,
) -> Effect | None:
    """The effect for one compiled step, or None for a step that starts no work. ``kind`` overrides the deterministic
    classification (used when the second opinion chose from the enumerated list)."""
    chosen = kind or classify_kind(ev, consequence=consequence, is_commit=is_commit, destructive=destructive)
    if chosen not in _DESIRED:
        return None
    checks = _mine_checks(ev, chosen)
    desired_rung, _desired_witnesses = _DESIRED[chosen]
    supported = [c for c in checks if c.role == "support" and c.probe.get("type") in RUNTIME_PROBES
                 and c.probe.get("type") not in _UNTESTED_PROBES]
    best = max((_RUNG_INDEX[c.rung] for c in supported), default=_RUNG_INDEX["performed"])
    required = EFFECT_RUNGS[min(_RUNG_INDEX[desired_rung], best)]
    long = chosen in {"upload", "long_job", "download"}
    return Effect(
        kind=chosen,
        required_rung=required,
        target_rung=desired_rung,
        checks=checks,
        quorum=EffectQuorum(witnesses=1),
        time=EffectTime(stall_after_s=45 if long else 20, max_s=900 if long else 120, long_wait="park" if long else "fail"),
        on=EffectOn(),
        words=app_words(ev),
    )


_PRESS_ACTIONS = frozenset({"click", "dblclick", "submit", "keyboard_shortcut"})


def _ms(ev: dict[str, Any]) -> float | None:
    from conxa_compile.compiler.navigation_effects import _timestamp
    t = _timestamp(ev)
    return None if t is None else t * 1000


def _app_reacted(ev: dict[str, Any]) -> bool:
    """The app visibly did something after this action: a new address, a dialog, progress, or a message of its own.
    A page echoing the chosen file names is not the app taking them."""
    post = ev.get("post_condition") if isinstance(ev.get("post_condition"), dict) else {}
    return (str(post.get("classified_effect") or "none") not in {"none", "value_set"}
            or bool(ev.get("post_watch")) or bool(ev.get("surface_context")))


def request_evidence(events: list[dict[str, Any]], requests: dict[str, Any] | None):
    """``None`` without the recorder's request log (a recording made before it, or none kept): nothing can be said.
    Otherwise ``f(i)``: did a data-changing request go out in the 10 s after event ``i`` (until the next event)?
    ``None`` for an event with no time. One reading of the log for both the file-pick pairing and the "app accepted the
    request" check, so a check about a request is only ever made for a step after which one was seen."""
    if not isinstance(requests, dict) or not isinstance(requests.get("mutating"), list):
        return None
    sent = sorted(float(r["t"]) for r in requests["mutating"] if isinstance(r, dict) and isinstance(r.get("t"), (int, float)))

    def window(i: int) -> tuple[float, float] | None:
        t0 = _ms(events[i])
        if t0 is None:
            return None
        nxt = next((t for t in (_ms(e) for e in events[i + 1:]) if t is not None and t > t0), t0 + 10_000)
        return t0, min(nxt, t0 + 10_000)

    def sent_in(i: int) -> bool | None:
        w = window(i)
        return None if w is None else any(w[0] <= t < w[1] for t in sent)

    return sent_in


def choose_file_presses(events: list[dict[str, Any]], requests: dict[str, Any] | None) -> dict[int, int]:
    """``{upload sequence: press sequence}`` for each file pick that sent nothing, whose files a later press on the same
    page sent (a "choose files, then press Upload" page). Such a pick is built as ``choose_files`` (proven by the file box
    holding the files) and the press carries the upload's checklist and is gated like an upload.

    Needs the recorder's request log (``requests.json``): without it nobody can say the pick sent nothing, so a recording
    made before 2026-11-17 keeps the upload as the step that does the work. A pick that sent a request, or after which
    the app reacted, is the upload itself. Only presses on the same tab and address before the next pick count, and only
    the first of them that sent a data-changing request."""
    sent_in = request_evidence(events, requests)
    if sent_in is None:
        return {}

    def where(ev: dict[str, Any]) -> tuple[str, str]:
        tab = ev.get("tab") if isinstance(ev.get("tab"), dict) else {}
        return str(tab.get("id") or "tab_0"), str((ev.get("page") or {}).get("url") or "").split("#", 1)[0]

    def action(ev: dict[str, Any]) -> str:
        return str((ev.get("action") or {}).get("action") or "").lower()

    out: dict[int, int] = {}
    for i, ev in enumerate(events):
        if action(ev) not in {"upload", "upload_intent"} or _app_reacted(ev) or sent_in(i) is not False:
            continue
        for j in range(i + 1, len(events)):
            later = events[j]
            if where(later) != where(ev) or action(later) in {"upload", "upload_intent"}:
                break
            if action(later) in _PRESS_ACTIONS and sent_in(j):
                seq_u, seq_p = (ev.get("extras") or {}).get("sequence"), (later.get("extras") or {}).get("sequence")
                if isinstance(seq_u, int) and isinstance(seq_p, int):
                    out[seq_u] = seq_p
                break
    return out


def desired_witnesses(kind: str) -> int:
    return _DESIRED.get(kind, ("completed", 1))[1]


def plain_sentence(effect: dict[str, Any] | Effect) -> str:
    """The checklist in everyday words, for the "How can we tell it worked?" question and the test card."""
    data = effect.model_dump() if isinstance(effect, Effect) else effect
    parts = [str(c.get("plain") or "") for c in (data.get("checks") or []) if c.get("role") != "veto" and c.get("plain")]
    parts = [p for p in parts if p not in {"the file was chosen"}] or parts
    return "; ".join(parts[:3])


def _from_recording(check: dict[str, Any]) -> bool:
    """A check only the author can retire: the author agreed to it, or it is the words the recording itself saw
    (a ``new_text`` check is built from nothing else)."""
    return check.get("confirmed") is True or (check.get("probe") or {}).get("type") == "new_text"


def _dead(check: dict[str, Any]) -> bool:
    """Shown to be dead by a run that really proved the step. A bare ``calibrated: false`` (no basis) is ignored."""
    return check.get("calibrated") is False and bool(check.get("calibration_basis"))


def calibrate_effect(effect: dict[str, Any], observed: dict[str, str]) -> dict[str, Any]:
    """Apply what a PASSING test run showed to a step's effect dict and return the new dict.

    ``observed`` maps a check id to its answer in that run ("yes" / "no" / "not_yet" / "cant_tell").

    A run teaches nothing unless a check that proves the work FINISHED (rung ``completed`` or higher) answered "yes"
    in it: a pass that rested on nothing but "the step ran" may be a false pass, and learning from it would retire the
    very checks that would have caught it. Such a run changes nothing. Otherwise a support check that answered "yes"
    is ``calibrated: true``; one that answered "no" is ``calibrated: false`` (the site does not show that signal) and
    no longer counts, unless the recording itself saw that result or the author confirmed it (those are retired only
    by the author). "I could not tell" and "not yet" never retire anything. Every mark carries ``calibration_basis``,
    the checks that proved the run; a mark without one is ignored. The quorum rises to the number of separate
    witnesses that really answered "yes", up to what the design wants. A veto check cannot be proven by a run that had
    no failure, so it stays as it is. Pure: the caller stores the result."""
    out = dict(effect)
    checks = [dict(c) for c in effect.get("checks") or []]
    basis = [
        str(c.get("id") or "") for c in checks
        if c.get("role") != "veto" and observed.get(str(c.get("id") or "")) == "yes"
        and _RUNG_INDEX.get(str(c.get("rung")), -1) >= _RUNG_INDEX["completed"]
        # A later step working is the weakest finish witness (it may only prove the file existed), so it never
        # teaches the checklist: it must not be what retires the checks that answered "no".
        and c.get("witness") != "later_step"
    ]
    if not basis:
        return out
    live_witnesses: set[str] = set()
    for c in checks:
        if c.get("role") == "veto":
            continue
        answer = observed.get(str(c.get("id") or ""))
        if answer == "yes":
            c["calibrated"] = True
            c["calibration_basis"] = basis
            live_witnesses.add(str(c.get("witness") or "renderer"))
        elif answer == "no" and not _from_recording(c):
            c["calibrated"] = False
            c["calibration_basis"] = basis
    out["checks"] = checks
    quorum = dict(effect.get("quorum") or {})
    quorum["witnesses"] = max(1, min(desired_witnesses(str(effect.get("kind") or "")), len(live_witnesses) or 1))
    out["quorum"] = quorum
    # A check at a higher rung that really answered "yes" on this site is now trustworthy: raise what the step requires
    # to it (never above what the design wants). Until a run proves it, the requirement stays where it was.
    best = max((_RUNG_INDEX.get(str(c.get("rung")), -1) for c in checks if c.get("calibrated") is True), default=-1)
    target = _RUNG_INDEX.get(str(effect.get("target_rung")), -1)
    current = _RUNG_INDEX.get(str(effect.get("required_rung")), -1)
    if best > current and target >= 0:
        out["required_rung"] = EFFECT_RUNGS[min(best, target)]
    return out


def _finish_checks(effect: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The live support checks that prove the work FINISHED (rung completed or higher) with a probe the runtime can
    answer, leaving out any a test run showed to be dead."""
    if not isinstance(effect, dict):
        return []
    out = []
    for c in effect.get("checks") or []:
        if c.get("role") == "veto" or _dead(c):
            continue
        probe = (c.get("probe") or {}).get("type")
        if probe in RUNTIME_PROBES and _RUNG_INDEX.get(str(c.get("rung")), -1) >= _RUNG_INDEX["completed"]:
            out.append(c)
    return out


def has_real_proof(effect: dict[str, Any] | None, *, strict: bool = False) -> bool:
    """Does this effect hold a live support check that proves at least that the work finished?

    ``strict`` is the publish rule for a step that cannot be undone: the check must also have been *shown* to work,
    either fired in a passing test run (``calibrated``) or accepted by the author in chat (``confirmed``). A check
    nobody has tested or agreed to is a guess, and a guess is not enough for a delete or a send."""
    for c in _finish_checks(effect):
        if not strict or (c.get("calibrated") is True and c.get("calibration_basis")) or c.get("confirmed") is True:
            return True
    return False


def person_sentence(effect: dict[str, Any] | None) -> str:
    """The checks that prove the work finished, as one phrase a non-technical person can agree to
    ("you see “upload complete” on the screen and the app confirms it received it")."""
    parts: list[str] = []
    for c in _finish_checks(effect):
        probe = c.get("probe") or {}
        kind = probe.get("type")
        if kind == "new_text":
            words = " ".join(str(w) for w in (probe.get("words") or []))
            parts.append(f"you see \u201c{words}\u201d on the screen")
        elif kind == "request_chain":
            parts.append("the app confirms it received it")
        elif kind == "download_file":
            parts.append("the file arrives on this computer")
        elif kind == "fresh_look":
            parts.append("the file shows up when the page is opened again")
        elif c.get("plain"):
            parts.append(str(c["plain"]))
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return " and ".join(seen[:3])


if __name__ == "__main__":
    up = {
        "action": {"action": "upload_intent"},
        "post_watch": {"progress_seen": True, "waited_ms": 3000, "final_status": [{"kind": "status", "words": ["upload", "complete"]}]},
    }
    eff = build_effect(up)
    assert eff is not None and eff.kind == "upload" and eff.required_rung == "completed" and eff.target_rung == "result_exists"
    assert [c.probe["type"] for c in eff.checks] == ["input_has_files", "request_chain", "new_text", "failure_signal"]
    assert has_real_proof(eff.model_dump())
    assert build_effect({"action": {"action": "type"}}) is None
    print("effects ok")
