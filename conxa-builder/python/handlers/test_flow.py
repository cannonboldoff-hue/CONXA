"""Test, auto-fix and approval, driven from a workflow's conversation (BUILD-40).

``cmd_flow_test_prepare`` posts the inputs card; ``cmd_flow_test_run`` builds a *test* package
(approval is not needed to test — testing comes first), runs ``cmd_test_workflow`` with the RT-9
guard and mirrors the runtime's own step events onto one ``test_run`` card. A test **never**
crosses a step that can't be undone without a person: the guard pauses before it ("Run the last N
steps for real" / "End the test here"), a confirm-only review is answered by the person, and an
engine that predates the guard is previewed (dry run) instead of run for real.

When a test fails and "find the cause" is on, ``_tf_investigate`` explains the root cause in plain
words and proposes **at most one change** — the same proposal the copilot makes, through the same
gates — as an ordinary change card. Nothing is applied until the person presses Apply; Apply (on this
card or on a change card from the person's own chat message) re-tests at once with the guard on
(``_tf_retest_after_fix``). A test that fails again investigates again: there is no attempt cap, and
every fix needs its own approval. Approving the workflow stays a separate human action.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from conxa_core.redact import MASK, is_secret_input
from handlers.protocol import _CommandError, _event_sink, _safe_id


def _is_confirm_only_span(step: dict) -> bool:
    from conxa_compile.editor.patch_gate import is_confirm_only_span

    return is_confirm_only_span(step)

_DECISION_TIMEOUT_S = 870  # just under the 15 minute park window a test run is given (workflows.py::_TEST_RUN_ENV)

_waiters: dict[str, dict[str, Any]] = {}
_waiters_lock = threading.Lock()
_autofix_stop: dict[str, threading.Event] = {}
# A password or token typed into a test card, kept only in memory for this Studio session so "test again" can reuse
# it; it is never saved (the workflow keeps a mask, conxa_core.redact). After a restart the card asks again.
_secret_test_inputs: dict[str, dict[str, Any]] = {}


def last_test_inputs(workflow: Any) -> dict[str, Any]:
    """The inputs of the workflow's last test, with any secret ones this session still holds."""
    return {**(getattr(workflow, "last_test_inputs", None) or {}), **_secret_test_inputs.get(getattr(workflow, "id", ""), {})}


def _thread(workflow_id: str) -> str:
    return f"wf:{workflow_id}"


def _pick_failing_row(rows: list[dict[str, Any]], failed_key: str | None, failed_at: Any,
                      names: dict[str, tuple[str, bool]] | None = None) -> dict[str, Any] | None:
    """The row for the step the runtime says failed. Rows come from progress lines that travel on a different
    channel than the result, so the "running" row can be the step before the one that failed. The runtime's own
    answer (step key, else plan index) wins; the running row is only the fallback when the runtime gave none."""
    match = None
    if failed_key:
        match = next((r for r in rows if r.get("step_key") == failed_key), None)
    if match is None and isinstance(failed_at, int) and not isinstance(failed_at, bool):
        match = next((r for r in rows if r.get("index") == failed_at), None)
    if match is None and failed_key and names and failed_key in names:
        name, nested = names[failed_key]
        match = {"index": failed_at if isinstance(failed_at, int) else len(rows), "name": name, "nested": nested,
                 "status": "running", "destructive": False, "step_key": failed_key}
        rows.append(match)
    if match is None:
        return next((r for r in reversed(rows) if r["status"] == "running"), None)
    for r in rows:
        if r is not match and r["status"] == "running":
            r["status"] = "done"
    return match


def _built_package_has(output_path: str, slug: str) -> bool:
    """True when the built package holds this workflow's runnable skill (skills/<slug>/execution.json)."""
    try:
        from pathlib import Path

        return bool(slug) and (Path(output_path) / "skills" / slug / "execution.json").is_file()
    except Exception:  # noqa: BLE001 -- when in doubt, build: a needless build is cheap, "Skill not found" is not
        return False


def _sign_in_changed_since_build(workflow: Any) -> bool:
    """True when the built pack's sign-in setup for this workflow's group differs from the live group (the same
    rule the test applies before it runs, handlers.protocol._sign_in_setup_drift). Unreadable means build."""
    import json
    from pathlib import Path

    from conxa_core.config import settings
    from conxa_core.workspace import LOCAL_WORKSPACE_ID, workspace_dir_slug
    from handlers.protocol import _sign_in_setup_drift

    if not getattr(workflow, "group_id", None):
        return False
    try:
        pack_json = json.loads((Path(settings.data_dir) / "skill-packs" / workspace_dir_slug(LOCAL_WORKSPACE_ID) / "pack.json")
                               .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return bool(_sign_in_setup_drift(workflow, pack_json))


def _humanize(intent: str) -> str:
    text = str(intent or "").replace("_", " ").strip()
    return (text[:1].upper() + text[1:]) if text else "A step"


_DELIBERATE_KINDS = frozenset({"decision_needed", "left_open_unclassified", "couldnt_confirm", "bad_input", "tab_not_found"})


def rows_to_text(value: Any, columns: list[str]) -> str:
    """A table value as the lines the test card shows: one row per line, columns separated by " | ".
    Text that is already lines is kept; a saved default (the rows as JSON text) is shown as lines too (WF-11)."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if not isinstance(parsed, list):
            return value
        value = parsed
    if not isinstance(value, list):
        return ""
    return "\n".join(" | ".join(str(r.get(c, "")) for c in columns) for r in value if isinstance(r, dict))


def rows_from_text(text: Any, columns: list[str], label: str) -> list[dict[str, str]]:
    """The lines of a table input ("a@x.com | Hello", one row per line) as rows. A line with the wrong number of
    columns is refused with its line number: a shifted column would send the wrong value to the wrong place."""
    out: list[dict[str, str]] = []
    for n, line in enumerate(str(text or "").splitlines(), 1):
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) != len(columns) or any(not c for c in cells):
            raise _CommandError("inputs_invalid", f"{label}, line {n}: give {len(columns)} values separated by | ({', '.join(c.replace('_', ' ') for c in columns)}).")
        out.append(dict(zip(columns, cells)))
    return out


def _agent_span_notice(steps: list[Any]) -> str:
    """What the person must know before a test about the parts the AI agent does (WF-13): an unfinished one is left
    out (the build only logged it), and a run stops before the free one over the limit. Empty when nothing to say."""
    from conxa_compile.editor.patch_gate import MAX_AGENT_SPANS_PER_RUN
    from conxa_compile.skill_package_builder_saved_skill import agent_span_counts

    dropped, kept = agent_span_counts(steps)
    parts = []
    if dropped == 1:
        parts.append("One part the AI agent does on its own has no goal yet, so it is left out of this test. "
                     "Open it in Human Edit and say what the agent should get done to include it.")
    elif dropped:
        parts.append(f"{dropped} parts the AI agent does on its own have no goal yet, so they are left out of this test. "
                     "Open them in Human Edit and say what the agent should get done to include them.")
    if kept > MAX_AGENT_SPANS_PER_RUN:
        parts.append(f"This workflow uses the AI agent {kept} times, and a run stops before the "
                     f"{MAX_AGENT_SPANS_PER_RUN + 1}th time so it can’t run away. "
                     "Remove or merge some of those parts so the whole workflow can run.")
    return " ".join(parts)


def _is_deliberate_stop(kind: str | None) -> bool:
    return bool(kind) and (kind in _DELIBERATE_KINDS or str(kind).startswith("fault:"))


def _deliberate_stop_message(facts: dict[str, Any]) -> str:
    """One plain message for a stop the runtime chose on purpose, from the recorded kind, surface and the runtime's
    own sentence. Only a popup the workflow has no answer for ends by asking the person what should happen."""
    from conxa_compile.conversation.failure_summary import summarize

    kind = str(facts.get("kind") or "")
    surface = facts.get("surface") or {}
    title = str(surface.get("title") or "").strip()
    buttons = [str(b) for b in (surface.get("buttons") or []) if b][:4]
    if kind == "decision_needed" and title:
        shown = f"“{title}”" + (f", with the buttons {', '.join(buttons)}," if buttons else ",")
        text = (f"The test stopped on purpose. A popup {shown} was open, and this workflow has no recorded answer for it, "
                "so it did not guess.")
    else:
        text = summarize(str(facts.get("message") or ""), kind)
    if facts.get("took_effect"):
        text += " The step before it had already run, so nothing was repeated."
    if kind in ("decision_needed", "left_open_unclassified"):
        text += " Tell me here what should happen when this shows up, or fix it yourself in Human Edit."
    return text


class TestFlowMixin:
    __test__ = False  # not a pytest class, despite the name

    # ── inputs card ─────────────────────────────────────────────────────────────────────────

    def cmd_flow_test_prepare(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import clarify, clarify_store, harness, thread_store as ts
        from conxa_compile.editor.source_event import recorded_input_values
        from conxa_compile.editor.workflow_mutations import inputs_a_run_needs, run_produced_names
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.skill_id:
            raise _CommandError("workflow_not_compiled", "Compile this workflow before testing it.")
        self._require_allowed(workflow_id, "test")
        doc = read_skill(workflow.skill_id) or {}
        sink = _event_sink(rid)
        existing = next((c for c in reversed(ts.fold(ts.read_raw(_thread(workflow_id))))
                         if c.get("card_type") == "test_inputs" and c.get("state") == "open"), None)
        if existing is not None:
            # Test changes (Human Edit) already posted one, further up the thread. Pressing Test it again
            # must visibly do something, so the old one is folded away and a fresh one lands at the bottom.
            harness.update_card(_thread(workflow_id), existing["card_id"], sink=sink, state="replaced", actions=[])

        steps = ((doc.get("skills") or [{}])[0] or {}).get("steps") or []
        recorded = {}
        for q in (clarify_store.load(workflow_id) or {}).get("questions") or []:
            if q.get("kind") in ("values", "chosen_values"):
                recorded = {**recorded, **(q.get("recorded") or {})}
        last = workflow.last_test_inputs or {}
        suspects = {str(s) for s in payload.get("suspect_inputs") or []}
        # A value the run fills in itself (an agent's answer, a value read off the page) is never asked for (WF-13).
        produced = run_produced_names(steps)
        from_recording = recorded_input_values(doc)
        inputs = []
        for row in inputs_a_run_needs(doc):
            if str(row["id"]).lower() in produced:
                continue
            rid_ = str(row["id"])
            value = last.get(rid_, recorded.get(rid_, row.get("default") or from_recording.get(rid_, "")))
            columns = [str(c) for c in row.get("columns") or []] if row.get("type") == "rows" else []
            if columns:
                value = rows_to_text(value, columns)
            secret = is_secret_input(rid_) and (row.get("type") or "text") == "text"
            if secret:
                value = ""   # never pre-filled: the card is saved in the thread
            inputs.append({
                "id": rid_, "label": _humanize(rid_), "type": "password" if secret else (row.get("type") or "text"),
                **({"secret": True} if secret else {}),
                "options": row.get("options") or [], "required": not row.get("optional", False),
                **({"columns": columns} if columns else {}),
                "value": value if isinstance(value, (str, int, float, bool)) else "",
                **({"suspect": True} if rid_ in suspects else {}),
            })
        irreversible = [clarify.step_name(s) for s in steps if isinstance(s, dict) and s.get("consequence") == "irreversible"]
        if not irreversible:
            # A step can also be destructive without a consequence label (older compiles).
            irreversible = [clarify.step_name(s) for s in steps if isinstance(s, dict)
                            and isinstance(s.get("identity_bundle"), dict) and s["identity_bundle"].get("destructive")]
        go_keys = self._flow_go_ahead_keys(workflow_id)
        go_ahead = [clarify.step_name(s) for k, s in zip(clarify.step_keys(steps), steps) if k in go_keys]
        harness.emit_item(sink, _thread(workflow_id), ts.append(
            _thread(workflow_id), "assistant_message",
            text="Let’s test it. Fill in the inputs the way a real request would — I’ve put your recorded values in to start."))
        notice = _agent_span_notice(steps)
        if notice:
            harness.emit_item(sink, _thread(workflow_id), ts.append(_thread(workflow_id), "assistant_message", text=notice))
        card = harness.post_card(
            _thread(workflow_id), "test_inputs", "open", sink=sink, inputs=inputs, irreversible=irreversible[-3:],
            irreversible_count=len(irreversible), guard=True, show_browser=True, use_ai=False,
            go_ahead=go_ahead,
            agent_step_count=sum(1 for s in steps if isinstance(s, dict) and clarify._action_name(s) == "agent_span"
                                 and not _is_confirm_only_span(s)),
            version=int((doc.get("meta") or {}).get("version") or 1), step_count=len(steps),
            actions=["run"],
        )
        return {"ok": True, "card_id": card["card_id"]}

    def cmd_flow_test_run(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card = ts.get_card(_thread(workflow.id), str(payload.get("card_id") or ""))
        if card is None or card.get("state") != "open":
            raise _CommandError("card_not_open", "This test was already started.")
        values = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        missing = [i["label"] for i in card.get("inputs") or []
                   if i.get("required") and str(values.get(i["id"], "")).strip() == ""]
        if missing:
            raise _CommandError("inputs_required", f"Fill in: {', '.join(missing)}.")
        # A table input is typed as lines on the card; the run gets real rows.
        run_values = dict(values)
        for spec in card.get("inputs") or []:
            if spec.get("columns"):
                run_values[spec["id"]] = rows_from_text(values.get(spec["id"], ""), list(spec["columns"]), str(spec.get("label") or spec["id"]))
        if runtime_state.get_flags(workflow.id).get("testing"):
            raise _CommandError("test_in_progress", "A test of this workflow is already running.")
        self._require_allowed(workflow.id, "test")  # the card may predate a question asked since
        guard = bool(payload.get("guard", True))
        show_browser = bool(payload.get("show_browser", True))
        use_ai = bool(payload.get("use_ai", False))
        # Keep what was actually run on the card, so a reload shows those values, not the pre-filled ones.
        ran = [{**i, "value": MASK if i.get("secret") and values.get(i["id"]) else values.get(i["id"], i.get("value"))}
               for i in card.get("inputs") or []]
        _secret_test_inputs[workflow.id] = {i["id"]: values[i["id"]] for i in card.get("inputs") or []
                                            if i.get("secret") and values.get(i["id"])}
        harness.update_card(_thread(workflow.id), card["card_id"], sink=sink, state="submitted", actions=[],
                            guard=guard, show_browser=show_browser, use_ai=use_ai, inputs=ran)
        threading.Thread(
            target=self._tf_background, args=(workflow.id, run_values, guard, show_browser, use_ai), daemon=True,
        ).start()
        return {"ok": True}

    def _tf_background(self, workflow_id: str, inputs: dict[str, Any], guard: bool, show_browser: bool,
                       use_ai: bool = False) -> None:
        try:
            outcome = self._tf_run_test(workflow_id, inputs, guard=guard, show_browser=show_browser, attempt=1, auto=False,
                                        use_ai=use_ai)
            if outcome == "failed":
                from conxa_compile.conversation import studio_settings

                # A value the person typed is the first suspect for a step that couldn't find its
                # element: ask before proposing to rewrite steps that may be fine.
                if self._tf_input_check(workflow_id, inputs):
                    return
                if studio_settings.load().get("autofix_enabled"):
                    self._tf_investigate(workflow_id)
        except Exception:  # noqa: BLE001 — never take the engine down from a background test
            import traceback

            traceback.print_exc()

    # ── one test run ────────────────────────────────────────────────────────────────────────

    def _tf_ensure_built(self, workflow: Any, rid: str) -> None:
        from conxa_core.storage.skill_pack_store import get_skill_pack
        from conxa_core.workspace import LOCAL_WORKSPACE_ID

        pack = get_skill_pack(LOCAL_WORKSPACE_ID)
        stale = pack is None or pack.build is None or (
            workflow.edited_at is not None and workflow.edited_at > pack.build.last_built_at)
        # The build record is one timestamp for the whole pack, so "built" never meant "this workflow is in it": a
        # second, never-edited workflow looked fresh while another workflow's build existed, and its test ended in
        # "Skill not found". Ask the built package itself.
        if not stale and not _built_package_has(pack.build.output_path, workflow.slug):
            stale = True
        # The test refuses a pack whose group sign-in setup changed since it was built (_sign_in_setup_drift: a
        # re-connected app, a learned sign-in). "Run test — builds a fresh test copy first" must then build, or a
        # person who just signed in again is told to "rebuild" with no way to do it (WF-1 Leg B).
        if not stale and _sign_in_changed_since_build(workflow):
            stale = True
        if stale:
            self.cmd_build_skill_package({"skill_slug": workflow.slug, "for_test": True}, rid)

    def _tf_run_test(self, workflow_id: str, inputs: dict[str, Any], *, guard: bool, show_browser: bool, use_ai: bool = False,
                     attempt: int, auto: bool) -> str:
        """Run one test and mirror it onto a card. Returns passed | failed | stopped | ended | previewed."""
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts
        from conxa_core.storage.workflow_store import get_workflow, list_workflows  # noqa: F401

        workflow = get_workflow(workflow_id)
        thread = _thread(workflow_id)
        rid = f"test_{uuid.uuid4().hex[:10]}"
        sink = _event_sink(rid)
        rows: list[dict[str, Any]] = []
        started = time.time()
        # Name each row the way the workflow's own step list does ("Open github.com", "Click “Drive”"),
        # not by the bare action kind the runtime reports. A step with no recorded key keeps the old name.
        names: dict[str, tuple[str, bool]] = {}
        try:
            from conxa_compile.conversation import clarify
            from conxa_core.storage.json_store import read_skill

            name_doc = (read_skill(workflow.skill_id) or {}) if workflow and workflow.skill_id else {}
            steps = clarify._steps(name_doc)
            for key, step, parent in zip(clarify.step_keys(steps), steps, clarify.effect_parents(steps)):
                names[key] = (clarify.step_name(step), parent is not None)
        except Exception:  # noqa: BLE001 -- names are cosmetic; a test must still run without them
            name_doc = {}

        def name_at(index: Any, intent: Any) -> str:
            """The step the runtime stopped before, by the workflow's own name for it ("Click Submit to send the
            form"), not its bare kind ("Click", WF-5). Falls back to the runtime's words."""
            try:
                from conxa_compile.skill_package_builder_saved_skill import runtime_step_index

                hit = next((n for key, (n, _) in names.items() if runtime_step_index(name_doc, key) == index), None)
                if hit:
                    return hit
            except Exception:  # noqa: BLE001 -- cosmetic
                pass
            return _humanize(intent or "")
        card = harness.post_card(thread, "test_run", "running", sink=sink, rows=rows, attempt=attempt, auto=auto,
                                 guard=guard, started_at=started, actions=["stop"])
        card_id = card["card_id"]
        runtime_state.set_flags(workflow_id, testing=True, recording_discarded=False, sign_in_expired=False)
        released = {"v": False}
        # What the run last stopped to ask. A test the person ends is reported by THAT cause, never by
        # a guess: ending at a popup is not "ending before a step that can't be undone".
        pause: dict[str, Any] = {}

        def record_outcome(kind: str, title: str = "", **extra: Any) -> None:
            """Keep how an unfinished test ended on the workflow, so the lifecycle can say "ended partway"."""
            try:
                from conxa_core.storage.workflow_store import set_workflow_test_outcome

                idx = extra.get("step_index")
                key = next((r.get("step_key") for r in rows if r.get("index") == idx), "") if idx is not None else ""
                set_workflow_test_outcome(workflow_id, {"outcome": kind, "title": title, "step_key": key or "", **extra})
            except Exception:  # noqa: BLE001 -- a record must never turn an ended test into an error
                pass

        def push(**extra: Any) -> None:
            harness.update_card(thread, card_id, sink=sink, rows=[dict(r) for r in rows], **extra)

        def phase_hook(entry: dict[str, Any]) -> None:
            phase = str(entry.get("phase", ""))
            # `inner_*` marks a step inside a popup answer or loop: its row is the plan step that holds it.
            if entry.get("step") is None or not phase.endswith("_start") or phase.startswith("inner_"):
                return
            for r in rows:
                if r["status"] == "running":
                    r["status"] = "done"
            if rows and rows[-1]["index"] == int(entry["step"]):
                # A resume (each agent round, a released guard) starts the same step again: one row, not two (WF-13).
                rows[-1]["status"] = "running"
                push(step_total=entry.get("step_total"))
                return
            parts = str(entry.get("phase", "")).split("_")  # step_<i>_<type>_start
            kind = parts[2] if len(parts) >= 4 else "step"
            key = entry.get("source_step_key") or ""
            name, nested = names.get(key) or (_humanize(entry.get("intent") or kind), False)
            rows.append({"index": int(entry["step"]), "name": name, "nested": nested,
                         "status": "running", "destructive": bool(entry.get("destructive")),
                         "step_key": key})
            push(step_total=entry.get("step_total"))

        def wait_decision(kind: str) -> str:
            ev = threading.Event()
            with _waiters_lock:
                _waiters[workflow_id] = {"event": ev, "decision": None, "kind": kind}
            ev.wait(_DECISION_TIMEOUT_S)
            with _waiters_lock:
                decision = (_waiters.pop(workflow_id, {}) or {}).get("decision")
            return decision or "end_here"

        def settle() -> None:
            """A step is only known to have finished when the next one starts or the run pauses."""
            for r in rows:
                if r["status"] == "running":
                    r["status"] = "done"

        def guard_hook(info: dict[str, Any]) -> str:
            settle()
            pause.clear()
            pause.update(kind="guard")
            ran = sum(1 for r in rows if r["status"] == "done")
            push(state="waiting_guard", paused_before=name_at(info.get("step_index"), info.get("step_intent")), remaining=info.get("remaining"),
                 ran=ran, actions=["run_rest", "end_here"])
            harness.log_event(thread, "Paused before a step that can’t be undone", glyph="warn", sink=sink,
                              subject=_humanize(info.get("step_intent")))
            decision = wait_decision("guard")
            if decision == "run_rest":
                released["v"] = True
                push(state="running", released=True, actions=["stop"])
            return decision

        def review_hook(review: dict[str, Any]) -> bool:
            settle()
            pause.clear()
            pause.update(kind="confirm")
            push(state="waiting_confirm", prompt=str(review.get("prompt") or ""), actions=["yes", "end_here"])
            decision = wait_decision("confirm")
            if decision in ("yes", "run_rest"):
                released["v"] = True
                push(state="running", released=True, actions=["stop"])
                return True
            return False

        def fork_hook(fork: dict[str, Any]) -> str:
            """A popup that needs a decision: show the choices and wait for the person. Never answered
            by a model. Returns a choice id, or "__stop__" when they end the test."""
            settle()
            pause.clear()
            pause.update(kind="popup", title=str(fork.get("title") or ""), step_index=fork.get("step_index"),
                         reason=str(fork.get("reason") or ""))
            push(state="waiting_fork", fork={"title": fork.get("title"), "message": fork.get("message"),
                                             "choices": fork.get("choices"), "suggested": fork.get("suggested"),
                                             "unknown": fork.get("unknown"), "step_index": fork.get("step_index"),
                                             "reason": fork.get("reason")},
                 actions=["fork_choose", "end_here"])
            harness.log_event(thread, "Paused — a popup needs your decision", glyph="warn", sink=sink,
                              subject=str(fork.get("title") or "a popup"))
            decision = wait_decision("fork")
            if decision.startswith("fork:"):
                push(state="running", fork=None, actions=["stop"])
                return decision[len("fork:"):]
            return "__stop__"

        try:
            try:
                self._tf_ensure_built(workflow, rid)
            except Exception as exc:  # noqa: BLE001 -- the test never started: this is not a test result
                return self._tf_failed(workflow_id, card_id, rows, str(exc), sink, started, inputs, outcome="package_build", names=names)
            result = self.cmd_test_workflow(
                {"workflow_id": workflow_id, "inputs": inputs, "headless": not show_browser, "guard": guard, "use_ai": use_ai,
                 "_phase_hook": phase_hook, "_guard_hook": guard_hook, "_review_hook": review_hook,
                 "guard_skip": sorted(self._flow_go_ahead_keys(workflow_id)),
                 "_fork_hook": fork_hook}, rid)
        except _CommandError as exc:
            return self._tf_failed(workflow_id, card_id, rows, exc.message or exc.code, sink, started, inputs,
                                   outcome=getattr(exc, "outcome", None), names=names)
        except Exception as exc:  # noqa: BLE001
            return self._tf_failed(workflow_id, card_id, rows, str(exc), sink, started, inputs, names=names)
        finally:
            runtime_state.set_flags(workflow_id, testing=False)

        status = result.get("status")
        for r in rows:
            if r["status"] == "running":
                r["status"] = "done"
        elapsed = int(time.time() - started)
        if status == "passed":
            ran_destructive = any(r.get("destructive") and r["status"] == "done" for r in rows)
            outcome = result.get("outcome") or "done"
            if outcome == "done" and workflow is not None and workflow.skill_id:
                self._tf_calibrate(workflow.skill_id, result.get("proof_steps") or [])
            notes = [str(n) for n in (result.get("notes") or [])][:10]
            # An approval step's yes is the run's own bookkeeping, not something the workflow read from the app.
            # Said in the card's own words: the person never sees a step's token or an output's internal name.
            outputs = {k.replace("_", " "): v for k, v in (result.get("outputs") or {}).items() if k != "confirmed_to_continue"}
            proof_steps = [{**s, "intent": (names.get(str(s.get("source_step_key") or "")) or (_humanize(str(s.get("intent") or "")),))[0]}
                           for s in result.get("proof_steps") or []]
            from handlers.workflows import unproven_steps

            push(state="passed", seconds=elapsed, steps_ran=len(rows), irreversible_ran=ran_destructive,
                 outputs=outputs, actions=["approve", "test_again"],
                 outcome=outcome, notes=notes, proof_steps=proof_steps,
                 unproven_steps=unproven_steps(proof_steps) if outcome in ("submitted", "done_please_check") else [])
            if outcome == "done_please_check":
                text = ("It ran to the end, but I couldn’t confirm everything" + (": " + " ".join(notes) if notes else ".")
                        + " Look at the result in the app. If it’s right, press “I checked — it worked” on the test card; "
                        "otherwise test it again. Approving doesn’t publish anything on its own.")
            elif outcome == "submitted":
                text = ("It ran and the app took the work, but I couldn’t confirm that it finished. Look at the result in the "
                        "app. If it’s there, press “I checked — it worked” on the test card; otherwise test it again. "
                        "Approving doesn’t publish anything on its own.")
            else:
                text = ("It works. Approve it when you’re happy — that adds it to the next skill package release. "
                        "Approving doesn’t publish anything on its own.")
            harness.emit_item(sink, thread, ts.append(thread, "assistant_message", text=text))
            return "passed"
        if status == "needs_sign_in":
            runtime_state.set_flags(workflow_id, sign_in_expired=True)
            # "Sign in and test again" re-runs with the last test's inputs; a first test that paused here had saved none.
            from conxa_core.storage.workflow_store import save_workflow

            paused = get_workflow(workflow_id)
            if paused is not None:
                paused.last_test_inputs = dict(inputs)
                save_workflow(paused)
                self._tf_mark_session_expired(paused)
            push(state="needs_sign_in", steps_ran=len(rows), seconds=elapsed, detail=str(result.get("message") or "")[:300],
                 actions=["test_again"])
            harness.log_event(thread, "Test paused — sign-in expired", glyph="warn", sink=sink,
                              subject="Nothing failed in the workflow")
            return "needs_sign_in"
        if status == "couldnt_run":
            # Not a test result: the last real result stays, and neither the input check nor auto-fix runs.
            from conxa_compile.conversation.failure_summary import summarize

            push(state="failed", outcome="couldnt_run", seconds=elapsed,
                 summary=summarize(str(result.get("message") or "")), error=str(result.get("message") or "")[:4000],
                 failed_step="", nothing_sent=False, actions=[])
            harness.log_event(thread, "Test couldn’t run", glyph="warn", sink=sink, subject="Nothing failed in the workflow")
            return "blocked"
        if status == "previewed":
            push(state="previewed", seconds=elapsed, actions=["test_again"])
            harness.log_event(thread, "Preview finished", glyph="warn", sink=sink,
                              subject="Steps that can’t be undone were checked, not run")
            record_outcome("previewed")
            return "previewed"
        if status == "stopped_at_guard" and pause.get("kind") == "popup":
            # The person ended the test at a popup they were asked about. Recorded with the step, the
            # popup's own words and why it asked, so the ending is data and not just a line in the chat.
            push(state="ended_at_popup", popup=pause.get("title") or "a popup", step_index=pause.get("step_index"),
                 reason=pause.get("reason") or "", steps_ran=len(rows), actions=["test_again"])
            harness.log_event(thread, "Test ended at a popup", glyph="none", sink=sink,
                              subject=str(pause.get("title") or "a popup"))
            record_outcome("ended_at_popup", str(pause.get("title") or "a popup"), step_index=pause.get("step_index"),
                           reason=pause.get("reason") or "")
            return "ended"
        if status == "stopped_at_guard":
            ran = [r["name"] for r in rows if r["status"] == "done"]
            push(state="ended_before_irreversible", cause=pause.get("kind") or "guard", stopped_before=name_at(result.get("stopped_at"), result.get("stopped_intent")),
                 declined=bool(result.get("declined")), after_confirmed=bool(result.get("after_confirmed")),
                 steps_ran=len(rows), actions=["test_again"])
            harness.log_event(thread, "Test ended before a step that can’t be undone", glyph="none", sink=sink,
                              subject="Nothing more was sent" if result.get("after_confirmed") else "Nothing was sent")
            record_outcome("ended_before_irreversible", name_at(result.get("stopped_at"), result.get("stopped_intent")),
                           step_index=result.get("stopped_at"), reason=pause.get("kind") or "guard")
            return "ended"
        # cancelled by the person
        push(state="stopped", steps_ran=len(rows), actions=["test_again"], last_step=rows[-1]["name"] if rows else "")
        harness.log_event(thread, "Test stopped by you", glyph="none", sink=sink)
        record_outcome("stopped", rows[-1]["name"] if rows else "")
        return "stopped"

    @staticmethod
    def _flow_go_ahead_keys(workflow_id: str) -> set[str]:
        """Step keys of steps that can't be undone where the person answered "No, just do it" to "should someone approve
        first". The test's own pause before such steps honours that answer. Only a step still marked irreversible counts:
        an answer about a step that has since become something else settles nothing."""
        try:
            from conxa_compile.conversation import clarify, clarify_store
            from conxa_core.storage.json_store import read_skill
            from conxa_core.storage.workflow_store import get_workflow

            wf = get_workflow(workflow_id)
            doc = read_skill(wf.skill_id) if wf and wf.skill_id else None
            if not doc:
                return set()
            live = {k for k, s in zip(clarify.step_keys(clarify._steps(doc)), clarify._steps(doc))
                    if isinstance(s, dict) and s.get("consequence") == "irreversible"}
            return {k for q in (clarify_store.load(workflow_id) or {}).get("questions") or []
                    if q.get("kind") == "confirm" and q.get("status") == "answered" and (q.get("answer") or {}).get("choice") == "no"
                    for k in q.get("step_keys") or [] if k in live}
        except Exception:  # noqa: BLE001 -- without it the test simply pauses, which is the safe side
            return set()

    def _tf_run_facts(self, workflow_id: str) -> dict[str, Any]:
        """What the runtime recorded about the latest failure, as data: its kind, whether the failing step's action may
        already have happened, and the surface a deliberate stop was about. Nothing here is read from message text.
        Empty-handed ({"kind": None, ...}) when no evidence was kept; callers then fall back to the old behaviour."""
        facts: dict[str, Any] = {"kind": None, "took_effect": False, "surface": None, "message": "",
                                 "failed_step_key": None, "failed_at": None, "candidates": []}
        try:
            from conxa_compile.editor.evidence import build_evidence_bundle
            from conxa_core.storage.workflow_store import get_workflow

            wf = get_workflow(workflow_id)
            if wf is None or not wf.skill_id or not wf.last_test_run_id:
                return facts
            bundle = build_evidence_bundle(wf.skill_id, run_id=wf.last_test_run_id, detail="digest")
            ev = bundle.get("runtime_evidence") or {}
            facts["failed_step_key"] = ev.get("failed_source_step_key") or bundle.get("failed_step_key")
            facts["failed_at"] = ev.get("failed_at")
            facts["kind"] = ev.get("failure_kind") if ev.get("failure_kind") not in (None, "other") else None
            facts["took_effect"] = bool(ev.get("action_may_have_taken_effect"))
            facts["message"] = str(ev.get("message") or "")
            surface = ev.get("left_open_surface")
            facts["surface"] = surface if isinstance(surface, dict) else None
            cands = ev.get("ambiguous_candidates")
            facts["candidates"] = [str(c.get("name") or "") for c in cands if isinstance(c, dict)] if isinstance(cands, list) else []
        except Exception:  # noqa: BLE001 -- facts are a bonus; a failed test must still report
            pass
        return facts

    def _tf_failure_image(self, workflow_id: str) -> str | None:
        """``conxa-asset://`` address of the failure screenshot the runtime kept for the latest test, or
        None. Only the run id and file name leave the sandbox path; the renderer cannot ask for anything else."""
        try:
            from pathlib import Path

            from conxa_compile.editor.evidence import build_evidence_bundle
            from conxa_core.storage.workflow_store import get_workflow

            wf = get_workflow(workflow_id)
            if wf is None or not wf.skill_id or not wf.last_test_run_id:
                return None
            shot = (build_evidence_bundle(wf.skill_id, run_id=wf.last_test_run_id, detail="digest").get("runtime_evidence") or {}).get("failure_screenshot_path")
            parts = Path(str(shot or "")).parts
            if "_evidence" not in parts:
                return None
            i = parts.index("_evidence")
            return f"conxa-asset://local/sandbox-evidence/{parts[i - 1]}/_evidence/{parts[-1]}" if i >= 1 else None
        except Exception:  # noqa: BLE001 -- a picture is a bonus; a failed test must still report
            return None

    def _tf_followup(self, workflow_id: str, step_key: str, inputs: dict[str, Any], sink: Any) -> None:
        """A failed test where the person's test value differs from the recorded one becomes a
        Clarify question. Never raises: a missing question must not hide the failure."""
        try:
            from conxa_compile.conversation import clarify, clarify_store
            from conxa_core.storage.json_store import read_skill
            from conxa_core.storage.workflow_store import get_workflow

            workflow = get_workflow(workflow_id)
            doc = read_skill(workflow.skill_id) if workflow and workflow.skill_id else None
            q = clarify.test_followup_question(doc, step_key, inputs) if doc else None
            if q is None:
                return
            data = clarify_store.load(workflow_id) or {"skill_id": workflow.skill_id, "questions": []}
            if any(x.get("id") == q["id"] for x in data.get("questions") or []):
                return  # asked (or answered) already
            data["questions"] = [*(data.get("questions") or []), q]
            clarify_store.save(workflow_id, data)
            self._flow_post_next_question(workflow_id, sink)
        except Exception:  # noqa: BLE001
            pass

    def _tf_input_check(self, workflow_id: str, inputs: dict[str, Any]) -> bool:
        """True when the failed test is put to the person as "is this value right?" instead of going
        to Auto-fix. Never raises: a failed check must not hide the failure or stop Auto-fix."""
        try:
            from conxa_compile.conversation import clarify, clarify_store
            from conxa_compile.editor.evidence import build_evidence_bundle
            from conxa_compile.editor.input_suspects import find_input_suspects
            from conxa_core.storage.json_store import read_skill
            from conxa_core.storage.workflow_store import get_workflow

            workflow = get_workflow(workflow_id)
            if workflow is None or not workflow.skill_id or not workflow.last_test_run_id:
                return False
            data = clarify_store.load(workflow_id) or {"skill_id": workflow.skill_id, "questions": []}
            if any(q.get("status") == "open" for q in data.get("questions") or []):
                return False  # a question already asked about this failure (the typed-step follow-up)
            doc = read_skill(workflow.skill_id) or {}
            # The recorded value of each input: the same source (and precedence) the test card prefills from.
            recorded: dict[str, Any] = {}
            for x in data.get("questions") or []:
                if x.get("kind") in ("values", "chosen_values"):
                    recorded = {**recorded, **(x.get("recorded") or {})}
            defs = [{**d, "default": recorded.get(str(d.get("id"))) or d.get("default")}
                    for d in doc.get("inputs") or [] if isinstance(d, dict)]
            evidence = build_evidence_bundle(workflow.skill_id, run_id=workflow.last_test_run_id, detail="digest")
            suspects = find_input_suspects(evidence.get("runtime_evidence"), inputs, defs)
            q = clarify.input_check_question(suspects, defs, inputs, workflow.last_test_run_id)
            if q is None or any(x.get("id") == q["id"] for x in data.get("questions") or []):
                return False
            data["questions"] = [*(data.get("questions") or []), q]
            clarify_store.save(workflow_id, data)
            self._flow_post_next_question(workflow_id, _event_sink(f"inputcheck_{uuid.uuid4().hex[:10]}"))
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _tf_calibrate(skill_id: str, proof_steps: list[dict[str, Any]]) -> int:
        """Proof of Done: a clean test run shows which of each step's completion checks really fire on this site.
        Checks that never answered "yes" are marked dead (they no longer count), and the number of separate witnesses
        needed rises to the ones that did answer. Only a fully clean pass calibrates; best effort and silent, because a
        test that passed must never turn into an error over bookkeeping. Returns how many steps changed."""
        try:
            from conxa_compile.editor.workflow_mutations import calibrate_effects
            from conxa_core.storage.json_store import read_skill, write_skill

            observed: dict[str, dict[str, str]] = {}
            for s in proof_steps:
                key = str(s.get("source_step_key") or "")
                checks = {str(c["id"]): str(c["answer"]) for c in (s.get("checks") or []) if c.get("id") and c.get("answer")}
                if key and checks:
                    observed[key] = checks
            if not observed:
                return 0
            doc = read_skill(skill_id)
            if not doc:
                return 0
            doc, changed = calibrate_effects(doc, observed)
            if changed:
                write_skill(skill_id, doc, keep_test=True)   # learned from this very pass: it still stands
            return changed
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _tf_mark_session_expired(workflow: Any) -> None:
        """The runtime found the saved sign-in dead. Record it on the group's apps, so "Sign in and test again" opens
        the sign-in window instead of trusting a check made minutes ago and re-running with the same dead session
        (AUTH-24 (3)). The app whose address the workflow starts on is marked; with no match, every connected one."""
        from urllib.parse import urlsplit

        from conxa_core.storage.group_store import get_group, set_group_app_checked

        try:
            group = get_group(workflow.group_id) if workflow.group_id else None
            if group is None:
                return
            host = urlsplit(workflow.target_url or "").hostname or ""

            def on_host(app: Any) -> bool:
                for known in (app.login_url, app.success_url):
                    h = urlsplit(known or "").hostname
                    if h and host and (host == h or host.endswith("." + h) or h.endswith("." + host)):
                        return True
                return False

            connected = [a for a in group.apps if a.captured_at and a.storage_state_path]
            for app in [a for a in connected if on_host(a)] or connected:
                set_group_app_checked(group.id, app.id, "expired")
        except Exception:  # noqa: BLE001 — a note about the session must never fail the test card
            pass

    def _tf_failed(self, workflow_id: str, card_id: str, rows: list[dict[str, Any]], message: str, sink: Any, started: float,
                   inputs: dict[str, Any] | None = None, outcome: str | None = None,
                   names: dict[str, tuple[str, bool]] | None = None) -> str:
        from conxa_compile.conversation import harness
        from conxa_compile.conversation.failure_summary import summarize

        facts = self._tf_run_facts(workflow_id)
        failing = _pick_failing_row(rows, facts.get("failed_step_key"), facts.get("failed_at"), names)
        if failing is not None:
            failing["status"] = "failed"
        # Only claim "nothing was sent" when no step that can't be undone even started AND the runtime does not say
        # the failing step's action may already have happened: a failure AT such a step may have half-happened.
        nothing_sent = not any(r.get("destructive") for r in rows) and not facts["took_effect"]
        # "Couldn't confirm" / "Partly done" already read in plain words and say something an ordinary
        # failure summary would flatten: the work may have happened. Keep their own sentences.
        own = outcome in ("couldnt_confirm", "partly_done")
        not_a_result = outcome == "package_build"   # the test never started: no step ran, so the run's evidence is not this failure's
        if not_a_result:
            summary = summarize(message, "package_build")
        else:
            summary = message.split(".", 1)[1].strip()[:400] if own and "." in message else summarize(message, facts["kind"], facts.get("candidates"))
        harness.update_card(
            _thread(workflow_id), card_id, sink=sink, state="failed", rows=[dict(r) for r in rows], error=message[:4000],
            summary=summary, failed_step=failing["name"] if failing else "", nothing_sent=(nothing_sent and not own) or not_a_result,
            seconds=int(time.time() - started), outcome=outcome or "failed",
            failure_image=None if not_a_result else self._tf_failure_image(workflow_id), actions=[],
        )
        if not_a_result:
            return "blocked"
        if failing is not None and failing.get("step_key"):
            from conxa_compile.conversation import clarify_events

            clarify_events.log(workflow_id, "test_failed", step_key=failing["step_key"])
            self._tf_followup(workflow_id, failing["step_key"], inputs or {}, sink)
        return "failed"

    # ── decisions, stop, approve ────────────────────────────────────────────────────────────

    def cmd_flow_test_decision(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """The person's answer while a test is paused: run_rest / yes / end_here, or fork:<choice id>
        for a popup decision."""
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        decision = str(payload.get("decision") or "")
        if decision not in ("run_rest", "yes", "end_here") and not (decision.startswith("fork:") and len(decision) > 5):
            raise _CommandError("invalid_input", "decision must be run_rest, yes, end_here or fork:<choice>")
        with _waiters_lock:
            w = _waiters.get(workflow_id)
            if w is None:
                raise _CommandError("not_waiting", "This test isn’t waiting for you any more.")
            w["decision"] = decision
            w["event"].set()
        return {"ok": True}

    def cmd_flow_test_stop(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        with _waiters_lock:
            w = _waiters.get(workflow_id)
            if w is not None:
                w["decision"] = "end_here"
                w["event"].set()
        stop = _autofix_stop.get(workflow_id)
        if stop is not None:
            stop.set()
        self.cmd_cancel_test_workflow({"workflow_id": workflow_id}, rid)
        return {"ok": True}

    def cmd_flow_approve(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Approve for packaging. A separate human action after a pass; never publishes."""
        from conxa_compile.conversation import harness
        from conxa_core.storage.workflow_store import get_workflow

        workflow = self._flow_workflow(payload)
        sink = _event_sink(rid)
        card_id = str(payload.get("card_id") or "")
        card = self._flow_card(workflow.id, card_id)
        if card.get("state") != "passed":
            raise _CommandError("not_passed", "Only a passing test can be approved.")
        stored = (workflow.last_test_outcome or {}).get("outcome")
        unconfirmed = card.get("outcome") in ("submitted", "done_please_check") and stored == card.get("outcome")
        if unconfirmed and payload.get("checked") is not True:
            raise _CommandError("check_required", "Look at the result in the app first, then press “I checked — it worked”.")
        if unconfirmed and workflow.last_test_status == "never":
            # The run ended without proving every step (the app took the work, or a step's result could not be confirmed).
            # The person looked and says it is right: their word is the proof, and only this button gives it (never a
            # chat message).
            from conxa_core.storage.workflow_store import set_workflow_test_result

            set_workflow_test_result(workflow.id, status="passed", inputs=workflow.last_test_inputs or {}, run_id=workflow.last_test_run_id)
            workflow = get_workflow(workflow.id) or workflow
        if workflow.last_test_status != "passed":
            raise _CommandError("test_required", "The workflow changed since that test. Test it again, then approve.")
        self._require_allowed(workflow.id, "approve")   # an unanswered question blocks approval
        result = self.cmd_sign_off_workflow({"skill_id": workflow.skill_id}, rid)
        if not result.get("signed_off"):
            raise _CommandError("approve_failed", "Couldn’t approve this workflow.")
        version = None
        try:
            from conxa_core.storage.json_store import read_skill

            version = int(((read_skill(workflow.skill_id) or {}).get("meta") or {}).get("version") or 0) or None
        except Exception:  # noqa: BLE001
            pass
        harness.update_card(_thread(workflow.id), card_id, sink=sink, approved=True, approved_version=version,
                            actions=["test_again"], build_error=result.get("build_error"))
        from conxa_compile.conversation import thread_store as ts

        from conxa_core.storage.workflow_store import list_workflows

        n = sum(1 for w in list_workflows() if w.signed_off)
        harness.emit_item(sink, _thread(workflow.id), ts.append(
            _thread(workflow.id), "assistant_message",
            text=f"Approved. You now have {n} approved workflow{'s' if n != 1 else ''} waiting in the next release. "
                 "Publish when the whole group is ready."))
        return {"ok": True, "approved": True, "built": bool(result.get("built"))}

    # ── settings ────────────────────────────────────────────────────────────────────────────

    def cmd_get_studio_settings(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import studio_settings

        return {"settings": studio_settings.load()}

    def cmd_set_studio_settings(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import studio_settings

        return {"settings": studio_settings.update(**{k: v for k, v in payload.items() if k != "type"})}

    # ── auto-fix ────────────────────────────────────────────────────────────────────────────

    def cmd_flow_autofix_stop(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        return self.cmd_flow_test_stop(payload, rid)

    def cmd_flow_investigate(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        workflow = self._flow_workflow(payload)
        threading.Thread(target=self._tf_investigate, args=(workflow.id,), daemon=True).start()
        return {"ok": True}

    def _tf_propose_fix(self, workflow_id: str, sink: Any = None) -> tuple[str, list[dict[str, Any]], Any]:
        """The copilot's plain-words reading of the latest failure, and at most one gated fix proposal —
        its own call and gates. A change only a person can draw (a new element) becomes the same "Open Human Edit"
        link card the chat turn makes; the third item posts it when called (None when none is needed). ("", [], None) when there is
        nothing to read."""
        from conxa_compile.conversation.context_pack import build_context_pack
        from conxa_compile.editor.evidence import EvidenceError, build_evidence_bundle
        from conxa_compile.llm.copilot import _capability_manifest_text, copilot_turn
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.skill_id:
            return "", [], None
        try:
            digest = build_evidence_bundle(workflow.skill_id, run_id=workflow.last_test_run_id, detail="digest")
        except EvidenceError:
            return "", [], None
        facts = self._tf_run_facts(workflow_id)
        known = (f" The runtime recorded the kind of failure as \"{facts['kind']}\"." if facts["kind"] else "") + (
            " The failing step's action may already have taken effect, so it must not be repeated." if facts["took_effect"] else "")
        message = ("The last test failed. In plain, everyday words for a non-technical person, say what went wrong "
                   "(the root cause, from the evidence) and what you would change to fix it. Then propose at most ONE "
                   "change that is within the allowed change types and that you are confident fixes it. If you are "
                   "unsure, propose nothing: say what you saw and ask how the person would like it fixed. "
                   "State only causes the evidence shows; do not guess at causes it does not." + known)
        pack = build_context_pack(message=message, budget_tokens=12_000, digest=digest,
                                  failure={"last_test": digest.get("last_test"), "runtime_evidence": digest.get("runtime_evidence")})
        user_text = ("Capability manifest — what you may propose and what the runtime does with each step kind:\n"
                     f"{_capability_manifest_text()}\n\n{pack.text}")
        # The page as it was when the step failed: a vision model reads "the button moved" off the screenshot
        # that a text description of the failure can only hint at. None when no failure image was kept.
        shot = (digest.get("runtime_evidence") or {}).get("failure_screenshot_path")
        result = copilot_turn(skill_id=workflow.skill_id, evidence=digest, transcript=[], message=message,
                              on_delta=None, user_text=user_text, screenshot_path=shot)
        ctx = self._thread_context("wf", workflow_id, None)
        proposals = ctx["gate_proposals"]([p for p in result.get("proposals") or [] if isinstance(p, dict)])
        hints = [n for n in result.get("needs_editor") or [] if isinstance(n, dict)]
        post_hint = None
        if hints and sink is not None:
            def post_hint() -> None:  # called by the caller after its Stop check, so Stop leaves nothing behind
                from conxa_compile.conversation import harness

                harness.post_editor_hint_card(_thread(workflow_id), sink, hints[0], digest)
        return str(result.get("reply") or "").strip(), proposals[:1], post_hint

    def _tf_investigate(self, workflow_id: str) -> None:
        """Find why the latest test failed and put a fix to the person. Changes nothing itself: the fix is an
        ordinary change card, and pressing Apply on it (or on one made from the person's own words) re-tests."""
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        thread = _thread(workflow_id)
        rid = f"investigate_{uuid.uuid4().hex[:10]}"
        sink = _event_sink(rid)
        stop = _autofix_stop.setdefault(workflow_id, threading.Event())
        stop.clear()

        def say(text: str) -> None:
            harness.emit_item(sink, thread, ts.append(thread, "assistant_message", text=text))

        # A deliberate stop (a popup with no recorded answer, a refusal the app showed, a bad input...) is already
        # fully explained by the runtime's own record. There is nothing for a model to guess at, so none is asked:
        # one plain message, and the route comes from the kind, not from model text.
        facts = self._tf_run_facts(workflow_id)
        if _is_deliberate_stop(facts["kind"]):
            harness.post_card(thread, "autofix", "needs_answer", sink=sink, reason=_deliberate_stop_message(facts), actions=[])
            return

        runtime_state.set_flags(workflow_id, investigating=True)
        card = harness.post_card(thread, "autofix", "running", sink=sink, phase="Finding the cause", actions=["stop"])
        card_id = card["card_id"]

        def end(state: str, **extra: Any) -> None:
            harness.update_card(thread, card_id, sink=sink, state=state, actions=extra.pop("actions", []), **extra)

        try:
            self._install_proxy_router(sink=sink, usage_class="ai_credits")
            try:
                reply, proposals, post_hint = self._tf_propose_fix(workflow_id, sink)
            except Exception as exc:  # noqa: BLE001 — entitlement/quota/cloud errors end the round cleanly
                return end("failed", reason=str(exc)[:160] or "I couldn’t reach the AI service.", actions=["investigate", "human_edit"])
            if stop.is_set():  # pressed while it was thinking: nothing may be posted after Stop
                return end("stopped", actions=["investigate", "human_edit"])
            # Exactly one thing is said: the model's own reading, or (when it gave none) the plain fallback. Never both.
            if post_hint:
                post_hint()
            if reply:
                say(reply)
            elif not proposals and not post_hint:
                say("I’m not sure how to fix this. Tell me how you’d like it fixed here in the chat, or open Human Edit.")
            if not proposals:
                return end("no_fix", actions=["investigate", "human_edit"])
            proposal = proposals[0]
            harness.post_card(
                thread, "pending_change", "open", sink=sink,
                refs={"skill_id": self._flow_workflow({"workflow_id": workflow_id}).skill_id, "proposal_id": proposal.get("id"),
                      "step_key": proposal.get("step_key")},
                title=_change_title(proposal), why=proposal.get("why") or "", proposal=proposal, preview=proposal.get("preview"),
                actions=["apply", "dismiss"],
            )
            end("done")
        finally:
            runtime_state.set_flags(workflow_id, investigating=False)

    def _tf_retest_after_fix(self, workflow_id: str, sink: Any) -> bool:
        """After a change card is applied: re-test at once, with the guard on, when the workflow's latest test
        failed. Any edit resets ``last_test_status``, so "failed" is read from the newest test card in the thread.
        Returns True when a re-test started; False (and nothing happens) when it did not apply or may not start now."""
        from conxa_compile.conversation import harness, runtime_state, thread_store as ts
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        if workflow is None or not workflow.skill_id or not last_test_inputs(workflow):
            return False
        newest = next((c for c in reversed(ts.fold(ts.read_raw(_thread(workflow_id))))
                       if c.get("kind") == "card" and c.get("card_type") == "test_run"), None)
        if newest is None or newest.get("state") != "failed" or runtime_state.get_flags(workflow_id).get("testing"):
            return False
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow

        if lifecycle_for_workflow(workflow).get("substate") == "change_pending":
            return False  # the person may press Test themselves, but an automatic re-test waits for the other change
        try:
            self._require_allowed(workflow_id, "test")  # an open question holds it back
        except _CommandError:
            return False
        harness.log_event(_thread(workflow_id), "Testing again with your change", glyph="none", sink=sink)
        threading.Thread(target=self._tf_background, args=(workflow_id, last_test_inputs(workflow), True, True), daemon=True).start()
        return True


def _change_title(proposal: dict[str, Any]) -> str:
    if proposal.get("command") == "insert_overlay_branch":
        return "Add a step to dismiss a pop-up"
    if proposal.get("command") == "structural_op":
        return "Change the steps"
    from conxa_compile.editor.copilot_proposals import field_words

    return f"Change {field_words(proposal.get('field'))}"
