"""BUILD-39 / BUILD-32: compile progress, the compile queue, Clarify questions and their answers."""

from __future__ import annotations

from unittest.mock import patch

from conxa_core.config import settings

import copy
import threading
import time

import pytest

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.conversation import compile_queue
from conxa_compile.conversation.clarify import MAX_QUESTIONS, build_summary, generate_questions, plan_answer, step_name
from conxa_compile.conversation.compile_progress import CompileProgress


# ── compile progress ────────────────────────────────────────────────────────────────────────


def _run(events):
    p = CompileProgress()
    changed = [p.observe(e) for e in events]
    return p, changed


def test_stages_only_move_when_the_compiler_says_so():
    p, _ = _run([{"phase": "pipeline_start"}])
    assert [r["status"] for r in p.rows()] == ["running", "pending", "pending", "pending"]
    p.observe({"phase": "pipeline_done", "event_count": 14})
    assert p.rows()[0] == {"key": "clean", "label": "Cleaned up the recording", "status": "done", "detail": "14 recorded actions"}
    p.observe({"phase": "compiler_start"})
    p.observe({"phase": "compile_log", "message": "Compiled step 9.", "step_index": 8})
    find = p.rows()[1]
    assert find["status"] == "running" and find["detail"] == "9 of 14"
    p.observe({"phase": "compile_log", "message": "Asking for a second opinion on the whole workflow."})
    assert [r["status"] for r in p.rows()] == ["done", "done", "running", "pending"]
    p.observe({"phase": "compile_log", "message": "Second opinion finished."})
    p.observe({"phase": "compiler_done", "step_count": 13})
    assert p.rows()[1]["detail"] == "13 of 13" and p.rows()[3]["status"] == "running"
    p.observe({"phase": "compile_done", "step_count": 13})
    assert p.done and all(r["status"] == "done" for r in p.rows())


def test_progress_never_goes_backwards_and_repeats_are_not_changes():
    p = CompileProgress()
    p.observe({"phase": "pipeline_done", "event_count": 10})
    assert p.observe({"phase": "compile_log", "message": "Compiled step 5.", "step_index": 4}) is True
    assert p.observe({"phase": "compile_log", "message": "Compiled step 3.", "step_index": 2}) is False
    assert p.detail["find"] == "5 of 10"
    assert p.observe({"phase": "compile_log", "message": "Something unrelated"}) is False


def test_an_error_marks_the_running_stage_failed_and_keeps_the_message():
    p, _ = _run([{"phase": "pipeline_start"}, {"phase": "compiler_start"}, {"phase": "compile_error", "message": "Cloud stopped responding"}])
    assert p.rows()[1]["status"] == "failed" and p.error == "Cloud stopped responding" and not p.done


def test_credits_left_only_from_a_committed_quota_event():
    p = CompileProgress()
    assert p.observe({"phase": "quota", "status": "reserved", "remaining": 38}) is False
    assert p.observe({"phase": "quota", "status": "committed", "remaining": 37}) is True
    assert p.credits_left == 37


# ── queue ───────────────────────────────────────────────────────────────────────────────────


def test_compiles_run_one_at_a_time_in_order_with_real_positions():
    compile_queue.reset()
    order: list[str] = []
    positions: dict[str, list[int]] = {"b": [], "c": []}
    compile_queue.acquire("a")
    order.append("a start")

    def worker(wid):
        compile_queue.acquire(wid, lambda ahead, running: positions[wid].append(ahead))
        order.append(f"{wid} start")
        time.sleep(0.05)
        order.append(f"{wid} end")
        compile_queue.release(wid)

    tb = threading.Thread(target=worker, args=("b",)); tb.start(); time.sleep(0.1)
    tc = threading.Thread(target=worker, args=("c",)); tc.start(); time.sleep(0.1)
    assert compile_queue.running() == "a" and compile_queue.queued() == ["b", "c"]
    assert positions["b"][0] == 1 and positions["c"][0] == 2  # real positions, no fake bar
    compile_queue.release("a")
    tb.join(5); tc.join(5)
    assert order == ["a start", "b start", "b end", "c start", "c end"]
    assert compile_queue.running() is None


def test_a_queued_compile_can_leave_the_line():
    compile_queue.reset()
    compile_queue.acquire("a")
    outcome: list[str] = []

    def waiter():
        try:
            compile_queue.acquire("b")
            outcome.append("ran")
        except compile_queue.WaitCancelled:
            outcome.append("left")

    t = threading.Thread(target=waiter); t.start(); time.sleep(0.1)
    compile_queue.cancel_wait("b")
    t.join(3)
    assert outcome == ["left"] and compile_queue.queued() == []
    compile_queue.release("a")
    assert compile_queue.running() is None
    compile_queue.reset()


# ── clarify ─────────────────────────────────────────────────────────────────────────────────


def _step(action, intent, **kw):
    s = {"action": {"action": action}, "intent": intent, "semantic_description": "", "validation": {"assertions": []}, "value": None, "input_binding": None}
    s.update(kw)
    return s


def _doc(steps, inputs=None):
    return {"meta": {"version": 3}, "skills": [{"steps": steps}], "inputs": inputs or []}


def _lookup(table):
    def look(step):
        return table.get(step.get("intent"))
    return look


def _invoice_doc():
    steps = [
        _step("navigate", "open_new_invoice"),
        _step("type", "enter_po_number", value="{{po_number}}", input_binding="po_number"),
        _step("type", "enter_description", value="{{description}}", input_binding="description"),
        _step("upload", "attach_pdf", value="{{file_path}}", input_binding="file_path"),
        _step("click", "close_cookie_banner", optional_hint={"container_signal": "#cookie"}),
        _step("click", "send_invoice", consequence="irreversible",
              validation={"assertions": [{"type": "text_present", "text": "Invoice sent", "required": False},
                                         {"type": "url_changed", "required": False}]}),
    ]
    return _doc(steps, [{"id": "po_number", "type": "text"}, {"id": "description", "type": "text"}, {"id": "file_path", "type": "file"}])


_SOURCES = {
    "enter_po_number": {"action": {"value": "PO-44821"}, "extras": {"sequence": 4}},
    "enter_description": {"action": {"value": "Annual license"}, "extras": {"sequence": 5}},
    "attach_pdf": {"action": {"value": "C:\\Users\\me\\Desktop\\invoice.pdf"}, "extras": {"sequence": 6}},
}


def test_questions_cover_the_places_only_the_recorder_knows_and_are_capped_and_ordered():
    qs = generate_questions(_invoice_doc(), source_lookup=_lookup(_SOURCES))
    assert [q["kind"] for q in qs] == ["upload", "confirm", "values", "popup", "done_when"]
    assert len(qs) <= MAX_QUESTIONS
    upload = qs[0]
    assert "invoice.pdf" in upload["title"] and upload["choices"][0]["id"] == "input"  # the safe default first
    assert qs[1]["title"] == "“Send invoice” can’t be undone. Should someone approve it before it happens?"
    values = qs[2]
    assert values["multi"] and [c["id"] for c in values["choices"]] == ["po_number", "description"]
    assert values["choices"][0]["detail"] == "PO-44821"
    done = qs[4]
    # the message is pre-ticked; the address and the two "input shows up" offers are not
    assert [c["selected"] for c in done["choices"]] == [True, False, False, False]


def test_words_seen_as_a_step_landed_elsewhere_are_offered_but_not_ticked():
    # WF-4 Leg C: Enter in a search box landed on the article; the words were the box's suggestions on the page it left.
    doc = _doc([_step("keyboard_shortcut", "submit_search", validation={"assertions": [
        {"type": "url_changed", "required": True},
        {"type": "text_present", "target": "T-Shirt (Migos song)", "required": False},
        {"type": "url_pattern", "target": r"^https://en\.wikipedia\.org/wiki/T\-shirt", "required": False}]})])
    done = next(q for q in generate_questions(doc) if q["kind"] == "done_when")
    words = next(c for c in done["choices"] if "Migos" in c["label"])
    assert words["selected"] is False


def test_a_workflow_with_nothing_uncertain_asks_nothing():
    doc = _doc([_step("navigate", "open_page"), _step("click", "save_changes")])
    assert generate_questions(doc) == []


def test_a_value_marked_while_recording_is_not_asked_again():
    qs = generate_questions(_invoice_doc(), source_lookup=_lookup(_SOURCES), answered_by_recording={"4": True})
    values = next(q for q in qs if q["kind"] == "values")
    assert [c["id"] for c in values["choices"]] == ["description"]


def test_hidden_fields_and_choice_controls_are_never_offered():
    doc = _doc([
        _step("type", "enter_password", value="{{password}}", input_binding="password"),
        _step("type", "pick_gender", value="{{gender}}", input_binding="gender", handler_hints={"control_kind": "choice"}),
    ])
    look = _lookup({"enter_password": {"action": {"value": "{{REDACTED}}"}, "extras": {"sequence": 1}}})
    assert generate_questions(doc, source_lookup=look) == []


def test_an_already_guarded_destructive_step_is_not_asked_about():
    doc = _doc([
        _step("click", "open"),
        {**_step("agent_span", "confirm"), "agent_span_allowed": [], "agent_span_answer_schema": {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean", "enum": [True]}}}, "agent_span_on_failure": "abort", "action": "agent_span"},
        _step("click", "send_invoice", consequence="irreversible"),
    ])
    assert generate_questions(doc) == []


def test_steps_are_named_not_numbered():
    assert step_name(_step("click", "send_invoice")) == "Send invoice"
    assert step_name(_step("click", "x", semantic_description="Step 4: Send invoice button")) == "Send invoice button"
    assert step_name({"action": "scroll"}) == "Scroll the page"


def test_a_menu_items_shortcut_hint_is_not_part_of_its_name():
    item = {"action": "click", "identity_bundle": {"fingerprint": {"inner_text": "File upload Alt+C then U"}}}
    assert step_name(item) == "Click “File upload”"


def test_done_when_is_the_last_actions_check_not_every_page_load():
    nav = {"action": "navigate", "url": "https://a.com", "validation": {"assertions": [{"type": "url_changed", "required": True}]}}
    up = {"action": "upload", "validation": {"assertions": [{"type": "state_changed", "required": True}]}}
    doc = {"skills": [{"steps": [nav, up]}]}
    assert build_summary(doc, recorded_actions=2, added_steps=0, name="x", uncertain=0)["done_when"] == ["the file is uploaded"]


def test_step_checks_list_live_checks_and_required_assertions_per_step():
    checks = [
        {"role": "support", "plain": "the file arrives on this computer"},
        {"role": "veto", "plain": "an error shows"},
        {"role": "support", "plain": "a dead one", "calibrated": False, "calibration_basis": ["x"]},
    ]
    dl = {"action": "click", "validation": {"effect": {"checks": checks}, "assertions": [{"type": "text_present", "target": "Downloaded", "required": True}]}}
    bare = {"action": "click"}
    s = build_summary({"skills": [{"steps": [dl, bare]}]}, recorded_actions=2, added_steps=0, name="x", uncertain=0)
    assert s["step_checks"] == [["the file arrives on this computer", "“Downloaded” appears"], []]


def test_compile_done_settles_rows_whose_end_event_never_came():
    p = CompileProgress()
    p.observe({"phase": "pipeline_start"})
    p.observe({"phase": "compile_done", "step_count": 3})
    assert {r["status"] for r in p.rows()} == {"done"}


def test_a_step_nothing_described_is_named_from_its_own_action():
    click = {"action": "click", "intent": "activate_control_new",
             "identity_bundle": {"fingerprint": {"inner_text": "", "aria_label": "New"}}}
    assert step_name(click) == "Click “New”"
    assert step_name({**click, "semantic_description": "activate_control_new"}) == "Click “New”"   # bare token
    assert step_name({"action": "navigate", "url": "https://www.github.com/a/{{file}}?x=1"}) == "Go to github.com/a/“file”"
    assert step_name({**click, "identity_bundle": {}}) == "Activate control new"                   # nothing recorded


def _q(doc, kind):
    return next(q for q in generate_questions(doc, source_lookup=_lookup(_SOURCES)) if q["kind"] == kind)


def test_answers_become_only_existing_mutations():
    doc = _invoice_doc()
    keys = step_keys(doc["skills"][0]["steps"])

    # values: unticked ones are restored to the recorded literal through the gated patch; nothing else is touched
    ops, receipt = plan_answer(_q(doc, "values"), {"selected": ["po_number"]}, doc)
    assert ops == [
        {"op": "patch", "step_key": keys[2], "patch": {"value": "Annual license"}},
        {"op": "drop_inputs", "ids": ["description"]},
    ]
    assert receipt == "Po number"
    assert all(set(o) <= {"op", "step_key", "patch", "ids"} for o in ops)  # no target / selector keys anywhere
    assert all("target" not in (o.get("patch") or {}) and "identity_bundle" not in (o.get("patch") or {}) for o in ops)

    # keeping everything changes nothing
    assert plan_answer(_q(doc, "values"), {"selected": ["po_number", "description"]}, doc)[0] == []

    # upload: the default asks each run (no op at all); fixed restores the recorded path
    assert plan_answer(_q(doc, "upload"), {"choice": "input"}, doc) == ([], "Whoever uses it picks the file")
    ops, _ = plan_answer(_q(doc, "upload"), {"choice": "fixed"}, doc)
    assert ops[0] == {"op": "patch", "step_key": keys[3], "patch": {"value": "C:\\Users\\me\\Desktop\\invoice.pdf"}}

    # pop-up: both answers are the existing confirm mutation; "one-off" keeps it a step
    assert plan_answer(_q(doc, "popup"), {"choice": "dismiss"}, doc)[0] == [{"op": "confirm_optional", "step_key": keys[4], "keep_as_step": False}]
    assert plan_answer(_q(doc, "popup"), {"choice": "once"}, doc)[0][0]["keep_as_step"] is True

    # confirm: yes inserts a review before the step; no does nothing
    ops, receipt = plan_answer(_q(doc, "confirm"), {"choice": "yes"}, doc)
    assert ops[0]["op"] == "insert_review" and ops[0]["before_step_key"] == keys[5] and receipt == "Someone approves it first"
    assert plan_answer(_q(doc, "confirm"), {"choice": "no"}, doc)[0] == []


def test_done_when_only_ever_makes_an_existing_text_url_or_state_check_required():
    doc = _invoice_doc()
    keys = step_keys(doc["skills"][0]["steps"])
    q = _q(doc, "done_when")
    ops, receipt = plan_answer(q, {"selected": [q["choices"][0]["id"]]}, doc)
    (op,) = ops
    assert op["step_key"] == keys[5]
    asserts = op["patch"]["validation"]["assertions"]
    assert asserts[0]["required"] is True and asserts[1]["required"] is False
    assert {a["type"] for a in asserts} <= {"text_present", "url_changed", "url_pattern", "state_changed"}
    assert receipt == "You see the words “Invoice sent” on the screen"


def test_done_when_offers_inputs_and_takes_a_typed_check_and_reads_the_current_doc():
    from conxa_compile.conversation.clarify import refresh_question

    doc = _invoice_doc()
    keys = step_keys(doc["skills"][0]["steps"])
    q = _q(doc, "done_when")
    assert {"input:po_number", "input:description"} <= {c["id"] for c in q["choices"]}
    assert "input:file_path" not in {c["id"] for c in q["choices"]}
    ops, receipt = plan_answer(q, {"selected": ["input:po_number"], "custom_text": "Saved"}, doc)
    (op,) = ops
    assert op["step_key"] == keys[5]
    added = op["patch"]["validation"]["assertions"][-2:]
    assert added == [{"type": "text_present", "target": "{{po_number}}", "required": True},
                     {"type": "text_present", "target": "Saved", "required": True}]
    # a value turned into an input after the compile: the label shows the input, not the recorded text
    doc["skills"][0]["steps"][5]["validation"]["assertions"][0]["target"] = "Invoice {{po_number}} sent"
    fresh = refresh_question(q, doc)
    assert fresh["choices"][0]["label"] == "You see the words “Invoice “po number” sent” on the screen"
    assert "input:po_number" not in {c["id"] for c in fresh["choices"]}


def test_a_typed_check_skips_trailing_steps_that_cannot_hold_one():
    """A person often scrolls to look at the result after the last real action: the typed check goes on the last
    step that can carry a check, not on the scroll (the gate would refuse it)."""
    from conxa_compile.editor.patch_gate import validate_editor_patch
    from conxa_compile.policy.bundle import get_policy_bundle

    doc = _invoice_doc()
    steps = doc["skills"][0]["steps"]
    steps.append({"action": {"action": "scroll", "delta_y": 400}, "intent": "scroll_down"})
    keys = step_keys(steps)
    ops, _ = plan_answer(_q(doc, "done_when"), {"selected": [], "custom_text": "Saved"}, doc)
    (op,) = ops
    assert op["step_key"] == keys[5]
    validate_editor_patch(steps[5], op["patch"], get_policy_bundle().data)  # the gate accepts it


def test_a_step_with_completion_checks_also_offers_the_typed_values_as_proof():
    """A form that answers in the page alone (no request): "the app accepted the request" can never hold there, so the
    person must be able to say "I see what I typed" even when the compiler wrote no text check of its own."""
    from conxa_compile.conversation.clarify import _done_when_choices

    doc = {"inputs": [{"id": "full_name"}], "skills": [{"steps": [
        {"action": {"action": "type"}, "value": "{{full_name}}", "input_binding": "full_name"},
        {"action": {"action": "click"}, "intent": "submit_form", "validation": {"assertions": [], "effect": {
            "kind": "send", "required_rung": "completed",
            "checks": [{"id": "c1", "rung": "completed", "witness": "transport", "role": "support",
                        "probe": {"type": "request_chain", "method": "mutating", "status": "2xx"},
                        "plain": "the app accepted the request"}]}}},
    ]}]}
    choices, _ = _done_when_choices(doc)
    ids = {c["id"] for c in choices}
    assert "input:full_name" in ids and any(i.startswith("effect:") for i in ids), ids


def test_planning_never_mutates_the_document():
    doc = _invoice_doc()
    before = copy.deepcopy(doc)
    plan_answer(_q(doc, "values"), {"selected": []}, doc)
    assert doc == before


def test_the_summary_counts_add_up():
    doc = _invoice_doc()
    s = build_summary(doc, recorded_actions=14, added_steps=2, name="Create invoice", uncertain=2)
    assert (s["recorded_actions"], s["steps"], s["added_steps"], s["inputs"], s["version"]) == (14, 6, 2, 3, 3)
    assert s["step_names"][0] == "Open new invoice"


# ── choice / date controls, and no silently dropped questions (gap analysis G7) ─────────────


def test_a_single_choice_and_a_date_can_be_pinned_to_what_was_recorded():
    doc = _doc([
        _step("select_option", "pick_plan", value="{{plan}}", input_binding="plan",
              handler_hints={"control_kind": "choice", "choice": {"multi": False, "recorded_label": "Pro", "options": [{"label": "Pro"}, {"label": "Free"}]}}),
        _step("date_pick", "pick_date", value="{{start_date}}", input_binding="start_date",
              handler_hints={"control_kind": "date_picker", "date_picker": {"recorded_value": "2026-09-14"}}),
    ])
    q = next(q for q in generate_questions(doc) if q["kind"] == "values")
    assert [(c["id"], c["detail"]) for c in q["choices"]] == [("plan", "Pro"), ("start_date", "2026-09-14")]
    ops, receipt = plan_answer(q, {"selected": []}, doc)       # nothing ticked: both stay as recorded
    patches = {o["patch"]["value"] for o in ops if o["op"] == "patch"}
    assert patches == {"Pro", "2026-09-14"} and ops[-1] == {"op": "drop_inputs", "ids": ["plan", "start_date"]}
    ops, _ = plan_answer(q, {"selected": ["plan", "start_date"]}, doc)
    assert ops == []                                             # both stay inputs


def test_a_multi_select_has_no_single_value_to_restore_so_it_is_not_offered():
    doc = _doc([_step("click", "tick_extras", value="{{extras}}", input_binding="extras",
                      handler_hints={"control_kind": "choice", "choice": {"multi": True, "recorded_values": ["a"], "options": [{"label": "A", "value": "a"}]}})])
    assert generate_questions(doc) == []


def test_every_question_is_kept_and_only_the_cap_is_asked_first():
    doc = _doc([_step("click", f"send_{i}", consequence="irreversible") for i in range(1, 9)] )
    # a first step so each irreversible one has a predecessor
    doc["skills"][0]["steps"].insert(0, _step("navigate", "open"))
    capped = generate_questions(doc)
    everything = generate_questions(doc, limit=None)
    assert len(capped) == MAX_QUESTIONS and len(everything) == 8
    assert [q["id"] for q in everything[:MAX_QUESTIONS]] == [q["id"] for q in capped]


# ── test failure -> Clarify ─────────────────────────────────────────────────────────────────


def _typed_doc():
    return {"inputs": [{"id": "company", "default": "Acme Ltd"}],
            "skills": [{"steps": [{"action": "type", "input_binding": "company", "value": "{{company}}", "intent": "Type company"}]}]}


def test_a_failed_test_with_a_different_value_asks_whether_it_varies_and_pins_it_on_no():
    from conxa_compile.conversation.clarify import plan_answer, test_followup_question

    doc = _typed_doc()
    key = step_keys(doc["skills"][0]["steps"])[0]
    q = test_followup_question(doc, key, {"company": "Globex"})
    assert q and q["kind"] == "test_followup" and "Globex" in q["body"] and "Acme Ltd" in q["body"]
    ops, receipt = plan_answer(q, {"choice": "fixed"}, doc)
    assert ops == [{"op": "patch", "step_key": key, "patch": {"value": "Acme Ltd"}}, {"op": "drop_inputs", "ids": ["company"]}]
    assert plan_answer(q, {"choice": "varies"}, doc)[0] == []


def test_no_followup_when_the_test_used_the_recorded_value_or_the_step_is_not_a_typed_input():
    from conxa_compile.conversation.clarify import test_followup_question

    doc = _typed_doc()
    key = step_keys(doc["skills"][0]["steps"])[0]
    assert test_followup_question(doc, key, {"company": "Acme Ltd"}) is None
    assert test_followup_question(doc, key, {}) is None
    assert test_followup_question(doc, "nope", {"company": "Globex"}) is None


def test_input_check_names_the_doubtful_values_in_plain_words_and_edits_nothing():
    from conxa_compile.conversation.clarify import input_check_question, plan_answer

    defs = [{"id": "gitignore_file", "default": "Ada.gitignore"}, {"id": "region", "type": "select", "options": ["eu"]}]
    suspects = [{"id": "gitignore_file", "reason": "differs_from_recorded"}, {"id": "region", "reason": "not_an_option"}]
    q = input_check_question(suspects, defs, {"gitignore_file": "gitignore", "region": "asia"}, "r_1")
    assert q["kind"] == "input_check" and q["id"] == "input_check:r_1" and q["input_ids"] == ["gitignore_file", "region"]
    assert "“gitignore”" in q["body"] and "“Ada.gitignore”" in q["body"] and "“asia”" in q["body"]
    assert [c["id"] for c in q["choices"]] == ["wrong", "right"]
    assert plan_answer(q, {"choice": "wrong"}, {})[0] == [] and plan_answer(q, {"choice": "right"}, {})[0] == []
    assert input_check_question([], defs, {}, "r_1") is None


def test_clarify_offers_skip_when_empty_and_missing_item_and_each_answer_is_one_gated_patch():
    from conxa_compile.conversation.clarify import plan_answer

    doc = {"inputs": [{"id": "note", "default": "", "optional": True}],
           "skills": [{"steps": [
               {"action": "type", "input_binding": "note", "value": "{{note}}", "intent": "Type note"},
               {"action": "click", "intent": "Delete row", "entity_binding": {"container_selector": "tr", "identifier": "{{row}}"}},
           ]}]}
    qs = {q["kind"]: q for q in generate_questions(doc, limit=None)}
    keys = step_keys(doc["skills"][0]["steps"])
    ops, _ = plan_answer(qs["skip_when_empty"], {"choice": "skip"}, doc)
    assert ops == [{"op": "patch", "step_key": keys[0], "patch": {"run_if": {"input": "note", "op": "not_empty"}}}]
    ops, _ = plan_answer(qs["missing_item"], {"choice": "skip"}, doc)
    assert ops == [{"op": "patch", "step_key": keys[1], "patch": {"on_missing": "skip"}}]
    assert plan_answer(qs["missing_item"], {"choice": "fail"}, doc)[0] == []
    # a step that already says what to do is not asked about
    doc["skills"][0]["steps"][0]["run_if"] = {"input": "note", "op": "not_empty"}
    doc["skills"][0]["steps"][1]["on_missing"] = "skip"
    assert not {"skip_when_empty", "missing_item"} & {q["kind"] for q in generate_questions(doc, limit=None)}



def test_the_steps_on_one_row_share_one_missing_item_question_and_its_answer():
    """WF-7: Edit, the email box and Save all act on the searched row. A skip that covered only Edit would fail on the
    next step, and asking three times is noise."""
    from conxa_compile.conversation.clarify import plan_answer

    row = {"container_selector": "tbody > tr", "identifier": "{{search_by_name}}"}
    doc = {"inputs": [], "skills": [{"steps": [
        {"action": "click", "intent": "Edit row", "entity_binding": dict(row)},
        {"action": "type", "intent": "Type email", "value": "new@example.com", "entity_binding": dict(row)},
        {"action": "click", "intent": "Save row", "entity_binding": dict(row)},
    ]}]}
    keys = step_keys(doc["skills"][0]["steps"])
    qs = [q for q in generate_questions(doc, limit=None) if q["kind"] == "missing_item"]
    assert len(qs) == 1 and qs[0]["step_keys"] == keys
    ops, _ = plan_answer(qs[0], {"choice": "skip"}, doc)
    assert ops == [{"op": "patch", "step_key": k, "patch": {"on_missing": "skip"}} for k in keys]


def test_a_value_that_stops_changing_names_its_row_by_the_recorded_text_again():
    """A row bound to {{search_by_name}} kept the input alive after it was unticked (drop_inputs refuses an id still
    referenced), so every run still asked for it."""
    from conxa_compile.conversation.clarify import plan_answer

    doc = _doc([
        _step("type", "type_name", value="{{search_by_name}}", input_binding="search_by_name"),
        _step("click", "edit_row", entity_binding={"container_selector": "tbody > tr", "identifier": "{{search_by_name}}"}),
    ])
    q = {"kind": "values", "choices": [{"id": "search_by_name"}], "recorded": {"search_by_name": "Carol"}}
    ops, _ = plan_answer(q, {"selected": []}, doc)
    assert ops[-1] == {"op": "drop_inputs", "ids": ["search_by_name"], "literals": {"search_by_name": "Carol"}}

# ── relative dates ──────────────────────────────────────────────────────────────────────────


def _date_doc():
    step = {"action": "date_pick", "intent": "Pick due date", "input_binding": "due_date", "value": "{{due_date}}",
            "handler_hints": {"date_picker": {"kind": "text_field", "display_format": "YYYY-MM-DD", "recorded_value": "2026-10-31"}}}
    return {"inputs": [{"id": "due_date", "type": "date", "default": "2026-10-31"}], "skills": [{"steps": [step]}]}


def test_a_picked_date_near_today_is_offered_as_relative_and_the_answer_unbinds_it():
    import datetime

    from conxa_compile.conversation.clarify import plan_answer
    from conxa_compile.editor.workflow_mutations import set_date_offset

    doc = _date_doc()
    today = datetime.date(2026, 10, 24)
    q = next(q for q in generate_questions(doc, today=today, limit=None) if q["kind"] == "relative_date")
    assert "7 days after today" in q["title"] and q["days"] == 7
    key = step_keys(doc["skills"][0]["steps"])[0]
    ops, _ = plan_answer(q, {"choice": "relative"}, doc)
    assert ops == [{"op": "set_date_offset", "step_key": key, "days": 7}, {"op": "drop_inputs", "ids": ["due_date"]}]
    assert plan_answer(q, {"choice": "fixed_input"}, doc)[0] == []
    out = set_date_offset(doc, 0, 7)
    step = out["skills"][0]["steps"][0]
    assert step["handler_hints"]["date_picker"]["date_offset_days"] == 7 and step["value"] == "" and step["input_binding"] is None
    # no question without a "today", for a far-away date, or once the step is already relative
    assert not [q for q in generate_questions(doc, limit=None) if q["kind"] == "relative_date"]
    assert not [q for q in generate_questions(doc, today=datetime.date(2024, 1, 1), limit=None) if q["kind"] == "relative_date"]
    assert not [q for q in generate_questions(out, today=today, limit=None) if q["kind"] == "relative_date"]
    with pytest.raises(ValueError, match="not_a_date_step"):
        set_date_offset({"skills": [{"steps": [{"action": "click"}]}]}, 0, 1)

    with pytest.raises(ValueError, match="date_offset_invalid"):
        set_date_offset(doc, 0, 99999)


def test_capabilities_needed_names_the_new_engine_features():
    from handlers.workflows import capabilities_needed

    steps = [
        {"action": {"action": "date_pick"}, "handler_hints": {"date_picker": {"date_offset_days": 3}}},
        {"action": {"action": "click"}, "on_missing": "ask"},
        {"action": {"action": "click"}, "identity_bundle": {"signals": [{"selector": 'role=link[name="{{file}}"]'}], "fingerprint": {}}},
    ]
    assert capabilities_needed(steps) == {"date_offset", "ask_missing", "templated_click"}
    assert capabilities_needed([{"action": {"action": "click"}, "identity_bundle": {"signals": [], "fingerprint": {"inner_text": "Save"}}}]) == set()


def test_reworded_questions_are_validated_field_by_field():
    from conxa_compile.conversation.clarify import apply_reworded, reword_payload

    qs = [
        {"id": "c", "kind": "confirm", "status": "open", "multi": False, "title": "“Send invoice” can’t be undone.", "body": "Draft body.",
         "choices": [{"id": "yes", "label": "Yes", "detail": "Asks first."}, {"id": "no", "label": "No", "detail": ""}]},
        {"id": "v", "kind": "values", "status": "open", "multi": True, "title": "Which change?", "body": "Tick them.",
         "choices": [{"id": "po", "label": "Po number", "detail": "PO-1"}]},
        {"id": "d", "kind": "dropped_value", "status": "open", "multi": False, "title": "T", "body": "typed “secret”", "choices": []},
    ]
    assert [i["id"] for i in reword_payload(qs)] == ["c", "v"]          # typed values never leave the machine
    assert "choices" not in reword_payload(qs)[1]                         # multi-choice labels are value names
    apply_reworded(qs, {"questions": [
        {"id": "c", "title": "Heads up: “Send invoice” can’t be taken back.", "body": "{{x}}",         # placeholder: rejected
         "choices": [{"id": "yes", "label": "Yes, ask someone first", "detail": "A yes/no box appears."},
                     {"id": "no", "label": "", "detail": ""}, {"id": "ghost", "label": "Invented", "detail": "x"}]},
        {"id": "v", "title": "Which of these change?", "body": "Tick them.", "choices": [{"id": "po", "label": "HACK", "detail": "HACK"}]},
        {"id": "d", "title": "HACK", "body": "HACK"},
    ]})
    assert qs[0]["title"].startswith("Heads up") and qs[0]["body"] == "Draft body."
    assert [c["label"] for c in qs[0]["choices"]] == ["Yes, ask someone first", "No"] and len(qs[0]["choices"]) == 2
    assert qs[1]["title"] == "Which of these change?" and qs[1]["choices"][0]["label"] == "Po number"
    assert qs[2]["title"] == "T"
    apply_reworded(qs, {"questions": [{"id": "c", "title": "Dropped the quoted name"}]})  # a quoted item missing: rejected
    assert qs[0]["title"].startswith("Heads up")
    apply_reworded(qs, None)  # a failed call changes nothing


@patch.object(settings, "compile_keep_every_action", False)


def test_a_download_or_new_tab_is_shown_under_the_action_that_caused_it_but_stays_its_own_step():
    from conxa_compile.conversation.clarify import effect_parents

    steps = [{"action": a} for a in ("navigate", "click", "download_observed", "navigate", "click", "tab_open", "dialog_accept", "click")]
    assert effect_parents(steps) == [None, None, 1, None, None, 4, 4, None]
    assert effect_parents([{"action": "download_observed"}]) == [None]          # nothing before it to belong to


# ── Phase 0: kinds, fixed typed events, counted cuts ────────────────────────────────────────


def test_kinds_restrict_what_is_built_and_a_fixed_typed_event_is_left_unticked():
    doc = _invoice_doc()
    only = generate_questions(doc, source_lookup=_lookup(_SOURCES), limit=None, kinds={"values"})
    assert [q["kind"] for q in only] == ["values"]
    assert {c["id"]: c["selected"] for c in only[0]["choices"]} == {"po_number": True, "description": True}
    # event 5 (the description) was judged "stays as recorded": offered, but not pre-ticked
    fixed = generate_questions(doc, source_lookup=_lookup(_SOURCES), limit=None, kinds={"values"}, typed_fixed_events={5})
    assert {c["id"]: c["selected"] for c in fixed[0]["choices"]} == {"po_number": True, "description": False}


def test_places_past_a_per_kind_cap_are_counted_and_not_just_dropped():
    steps = [_step("extract", f"read_{i}", value=f"total_{i}") for i in range(5)]
    limits: dict[str, int] = {}
    qs = generate_questions(_doc(steps), limit=None, limits=limits)
    assert [q["kind"] for q in qs].count("output") == 2 and limits == {"output": 3}


# ── Phase 3: a dropdown fixed before the build ──


def test_an_always_this_option_answer_for_a_dropdown_pins_it_through_the_values_answer():
    from conxa_compile.conversation import decisions as dec, replay

    step = _step("select", "pick_country", value="{{country}}", input_binding="country",
                 handler_hints={"control_kind": "choice", "choice": {"multi": False, "recorded_label": "Germany",
                                                                     "options": [{"label": "Germany", "value": "de"}]}})
    doc = _doc([step], [{"id": "country", "type": "text"}])
    key = step_keys(doc["skills"][0]["steps"])[0]
    q = {"id": "value:control:5", "kind": "value", "event_ids": [5], "choices": [{"id": "varies"}, {"id": "fixed"}],
         "items": [{"ref": "control:5", "name": "country", "literal": "Germany", "kind": "control", "event_ids": [5]}]}
    d = dec.decision_from_answer(q, {"choice": "fixed"})
    assert d["values"][0]["kind"] == "control" and dec.decision_from_answer(q, {"choice": "varies"})["values"] == []
    plan = replay.plan([d], doc, [], {key: 5}.get)
    assert [g["ops"] for g in plan["groups"]] == [[{"op": "patch", "step_key": key, "patch": {"value": "Germany"}},
                                                    {"op": "drop_inputs", "ids": ["country"]}]]
    assert plan["unapplied"] == []
    missing = replay.plan([d], doc, [], {key: 99}.get)                                  # the step is gone: said, not dropped
    assert missing["groups"] == [] and missing["unapplied"] == [{"kind": "value", "choice": "fixed"}]


# ── W3: a step that can't be undone and acts on one row asks which row, once ──────────────────────────────────


def _row_doc(identifier="Bike Light", confirmed=False):
    steps = [
        _step("navigate", "open_cart"),
        _step("click", "activate_control_remove", consequence="irreversible",
              entity_binding={"container_selector": "#cart > li", "identifier": identifier, "source": "literal", "confirmed": confirmed}),
    ]
    return _doc(steps)


def test_an_unconfirmed_row_on_a_step_that_cant_be_undone_is_asked_about():
    doc = _row_doc()
    keys = step_keys(doc["skills"][0]["steps"])
    q = next(q for q in generate_questions(doc, limit=None) if q["kind"] == "confirm_row")
    assert "Bike Light" in q["title"] and q["step_keys"] == [keys[1]]
    ops, receipt = plan_answer(q, {"choice": "yes"}, doc)
    assert ops == [{"op": "patch", "step_key": keys[1], "patch": {"entity_binding": {"confirmed": True}}}]
    assert plan_answer(q, {"choice": "no"}, doc)[0] == []  # chat never re-points a step: that is Human Edit


def test_a_confirmed_row_or_a_row_named_by_the_run_is_not_asked_again():
    assert not any(q["kind"] == "confirm_row" for q in generate_questions(_row_doc(confirmed=True), limit=None))
    assert not any(q["kind"] == "confirm_row" for q in generate_questions(_row_doc(identifier="{{item}}"), limit=None))


def test_an_address_check_is_offered_with_the_address_a_person_can_read():
    """It was offered as "The web address looks like " with nothing after it: the pattern is kept in `target`."""
    from conxa_compile.conversation.clarify import _address_from_pattern

    assert _address_from_pattern(r"^https://the\-internet\.herokuapp\.com/users/[^/?#]+") == "the-internet.herokuapp.com/users/…"
    assert _address_from_pattern(r"^https://the\-internet\.herokuapp\.com/login") == "the-internet.herokuapp.com/login"
    assert _address_from_pattern("") == ""


def test_a_last_step_with_no_proof_still_gets_asked_how_to_tell_it_worked():
    """WF-2 Segment D: a tab click whose result only shows on the screen had nothing to tick, so the question was never
    asked and every test ended "couldn't confirm". Now it is asked with only the typing box, and the words become the
    last step's check."""
    seen = {"screen_before": "the first tab is showing", "screen_after": "the third tab's panel is showing"}
    doc = _doc([_step("navigate", "open_page"), _step("click", "open_third_tab", video_context=seen)])
    keys = step_keys(doc["skills"][0]["steps"])
    q = next(q for q in generate_questions(doc) if q["kind"] == "done_when")
    assert q["choices"] == []
    ops, receipt = plan_answer(q, {"selected": [], "custom_text": "Mauris eleifend"}, doc)
    (op,) = ops
    assert op["step_key"] == keys[1]
    assert op["patch"]["validation"]["assertions"] == [{"type": "text_present", "target": "Mauris eleifend", "required": True}]
    assert receipt == "You see “Mauris eleifend” on the screen"
    # Already proven: not asked again.
    proven = _doc([_step("navigate", "open_page"),
                   _step("click", "open_third_tab", video_context=seen,
                         validation={"assertions": [{"type": "text_present", "target": "x", "required": True}]})])
    assert not any(q["kind"] == "done_when" for q in generate_questions(proven))
    # Nothing seen changing: not asked (a workflow with nothing uncertain asks nothing).
    assert not any(q["kind"] == "done_when" for q in generate_questions(_doc([_step("navigate", "open_page"), _step("click", "x")])))


def test_a_picked_date_can_always_be_kept_exactly_as_recorded():
    """WF-2 E: the person wanted the date kept as recorded; the card offered only "1 day before today" or "different every
    time". All three answers are offered now, and keeping it pins the recorded date and drops its input."""
    import datetime

    doc = _date_doc()
    key = step_keys(doc["skills"][0]["steps"])[0]
    q = next(q for q in generate_questions(doc, today=datetime.date(2026, 10, 24), limit=None) if q["kind"] == "relative_date")
    assert [c["id"] for c in q["choices"]] == ["relative", "fixed_date", "fixed_input"]
    assert next(c for c in q["choices"] if c["id"] == "fixed_date")["label"] == "No, always 31 Oct 2026"
    ops, receipt = plan_answer(q, {"choice": "fixed_date"}, doc)
    assert ops == [{"op": "patch", "step_key": key, "patch": {"value": "2026-10-31"}}, {"op": "drop_inputs", "ids": ["due_date"]}]
    assert receipt == "Always the date you picked"


def test_keeping_a_typed_value_also_rewrites_the_check_written_from_it():
    """WF-2 F: "Nothing changes" left the input, because the typing step's check said "the box holds {{tags}}" and an input
    still referenced is never dropped. The check now checks the recorded value, so the input can go."""
    step = _step("type", "type_tag", value="{{tags}}", input_binding="tags",
                 validation={"assertions": [{"type": "value_equals", "target": "#tags", "expected": "{{tags}}", "required": True}]})
    doc = _doc([_step("navigate", "open_page"), step], inputs=[{"id": "tags", "default": "Ja"}])
    key = step_keys(doc["skills"][0]["steps"])[1]
    q = {"kind": "values", "choices": [{"id": "tags", "label": "Tags"}], "recorded": {"tags": "Ja"}, "step_keys": [key]}
    ops, receipt = plan_answer(q, {"selected": []}, doc)
    assert ops == [
        {"op": "patch", "step_key": key, "patch": {"value": "Ja", "validation": {"assertions": [
            {"type": "value_equals", "target": "#tags", "expected": "Ja", "required": True}]}}},
        {"op": "drop_inputs", "ids": ["tags"]},
    ]
    assert receipt == "Nothing changes — everything stays as you recorded"


def test_a_check_that_holds_before_and_after_is_not_proof_so_the_person_is_asked():
    """WF-2 J: the click's only check was "“Toggle Me” is on the page", true whether the panel opened or not."""
    seen = {"screen_before": "the panel is closed", "screen_after": "the panel is open"}
    weak = {"assertions": [{"type": "selector_present", "target": 'text="Toggle Me"', "required": True}]}
    doc = _doc([_step("navigate", "open_page"), _step("click", "open_panel", video_context=seen, validation=weak)])
    assert any(q["kind"] == "done_when" for q in generate_questions(doc))


def test_a_dropdown_step_is_named_by_its_caption_not_its_option_list():
    # WF-3 D r2: "Choose in “12345 12456 12567 …”" when no description was written.
    from conxa_compile.conversation.clarify import step_name

    fp = {"tag": "select", "role": "combobox", "inner_text": "12345 12456 12567", "label_text": "to account #"}
    assert step_name({"action": "select", "identity_bundle": {"fingerprint": fp}}) == "Choose in “to account #”"
    box = {"tag": "input", "role": "textbox", "label_text": "Amount:"}
    assert step_name({"action": "type", "identity_bundle": {"fingerprint": box}}) == "Type into “Amount”"


def test_typed_words_go_on_the_step_that_brought_them_up(monkeypatch):
    """WF-4 Leg B: "Thank you for your order" was put on the last step (Log out), where it is gone. The saved pages show
    it first on a copy taken late (its event's address is still the step-two page), so the copy is placed by the last
    event that shares it: the order-complete page, which Finish opened."""
    import conxa_core.storage.session_events as se
    import conxa_core.storage.snapshots as sn

    def ev(seq, action, url, snap):
        return {"action": {"action": action}, "page": {"url": url}, "extras": {"sequence": seq}, "snapshot": {"dom_hash": snap}}

    two, done, home = "https://shop.test/step-two", "https://shop.test/complete", "https://shop.test/inventory"
    events = [ev(1, "scroll", two, "late"), ev(2, "click", two, "late"), ev(3, "manual_navigate", done, "late"),
              ev(4, "click", done, "late"), ev(5, "click", home, "inv"), ev(6, "click", home, "inv")]
    pages = {"late": "<h2>Thank you   for your order!</h2>", "inv": "<button>Logout</button>"}
    monkeypatch.setattr(se, "read_session_events", lambda _s: events)
    monkeypatch.setattr(sn, "read_dom_snapshot", lambda _s, h: pages.get(h))
    steps = [_step("click", "finish", source_event=2, semantic_description="Finish the order"),
             _step("click", "back_home", source_event=4), _step("click", "open_menu", source_event=5),
             _step("click", "logout", source_event=6)]
    doc = _doc(steps)
    doc["meta"]["source_session_id"] = "s1"
    keys = step_keys(steps)
    q = {"kind": "done_when", "choices": [], "step_keys": []}
    ops, receipt = plan_answer(q, {"selected": [], "custom_text": "Thank you for your order"}, doc)
    assert [o["step_key"] for o in ops] == [keys[0]]
    assert ops[0]["patch"]["validation"]["assertions"] == [
        {"type": "text_present", "target": "Thank you for your order", "required": True}]
    # Words no saved page shows stay at the end, and the receipt says they were not seen.
    ops, receipt = plan_answer(q, {"selected": [], "custom_text": "See you soon"}, doc)
    assert [o["step_key"] for o in ops] == [keys[3]] and "didn’t see these words" in receipt


@pytest.mark.parametrize("copies, want", [
    # Leg B's shape on one address: the scroll's copy was taken after Finish; Finish is the next press sharing it.
    ({"0": "before", "1": "late", "2": "late", "3": "late"}, 2),
    # A saved message: the Save click's own copy has the words; the click on the box before it is not the cause.
    ({"0": "before", "1": "before", "2": "late", "3": "late"}, 2),
])
def test_typed_words_on_a_page_that_keeps_its_address(monkeypatch, copies, want):
    import conxa_core.storage.session_events as se
    import conxa_core.storage.snapshots as sn

    acts = ["click", "scroll", "click", "click"]
    events = [{"action": {"action": a}, "page": {"url": "https://shop.test/a"}, "extras": {"sequence": i + 1},
               "snapshot": {"dom_hash": copies[str(i)]}} for i, a in enumerate(acts)]
    pages = {"before": "<p>Order</p><script>var t='Saved it'</script>", "late": "<p>Saved it</p>"}
    monkeypatch.setattr(se, "read_session_events", lambda _s: events)
    monkeypatch.setattr(sn, "read_dom_snapshot", lambda _s, h: pages.get(h))
    from conxa_compile.conversation.clarify import step_where_words_appear

    steps = [_step(a, f"s{i}", source_event=i + 1) for i, a in enumerate(acts)]
    doc = _doc(steps)
    doc["meta"]["source_session_id"] = "s1"
    assert step_where_words_appear(doc, "saved it") == step_keys(steps)[want]


def test_typed_words_already_on_the_screen_when_the_step_was_pressed_are_not_a_check(monkeypatch):
    """WF-5: the page heading "Five downloads" was accepted as the proof of the downloads. It was on the screen before
    the first click, so seeing it afterwards says nothing. The recorder describes each press as the page was then."""
    import conxa_core.storage.session_events as se
    import conxa_core.storage.snapshots as sn

    events = [{"action": {"action": a}, "page": {"url": "https://app.test/dupes"}, "extras": {"sequence": i + 1,
               "section_heading": "Five downloads"}, "snapshot": {"dom_hash": "same"}}
              for i, a in enumerate(["click", "download_observed", "click"])]
    monkeypatch.setattr(se, "read_session_events", lambda _s: events)
    monkeypatch.setattr(sn, "read_dom_snapshot", lambda _s, h: "<h1>Five downloads</h1><a>Get file</a>")
    steps = [_step("click", "get_1", source_event=1), _step("click", "get_2", source_event=3)]
    doc = _doc(steps)
    doc["meta"]["source_session_id"] = "s1"
    q = {"kind": "done_when", "choices": [], "step_keys": []}
    ops, receipt = plan_answer(q, {"selected": [], "custom_text": "Five downloads"}, doc)
    assert ops == [] and "already on the screen" in receipt


def test_check_first_before_a_delete_is_applied_even_when_the_built_step_is_not_irreversible():
    """WF-4 Leg A: "Check first" was asked about the bin icon (it only opens "Are you sure?", so it was built as
    reversible and no confirm question existed). The answer was reported as matching nothing, and the delete would
    have run without asking. A person's own request to be asked always lands."""
    from conxa_compile.conversation import replay

    steps = [_step("click", "search", source_event=25), _step("click", "delete_user", source_event=27,
                                                           semantic_description="Delete the user"),
             _step("outcome_fork", "handle_popup", source_event=28)]
    doc = _doc(steps)
    keys = step_keys(steps)
    d = {"kind": "confirm_before", "event_ids": [27], "choice": "confirm"}
    plan = replay.plan([d], doc, [], dict(zip(keys, (25, 27, 28))).get)
    assert plan["unapplied"] == []
    (g,) = plan["groups"]
    assert [o["op"] for o in g["ops"]] == ["insert_review"] and g["ops"][0]["before_step_key"] == keys[1]
    # The event's step is gone: asked before the last step before it, still never dropped.
    plan = replay.plan([d], doc, [], dict(zip(keys, (25, 99, 98))).get)
    assert plan["groups"][0]["ops"][0]["before_step_key"] == keys[0]
    # "Delete without checking" still changes nothing.
    assert replay.plan([{**d, "choice": "no_confirm"}], doc, [], dict(zip(keys, (25, 27, 28))).get) == {"groups": [], "unapplied": []}


def test_a_popup_without_a_title_is_named_by_its_buttons():
    from conxa_compile.conversation.clarify import _fork_question

    q = _fork_question("k#1", {"id": "dialog_popup", "signature": {"button_labels": ["×", "No, Cancel", "Yes, Delete"]},
                               "options": [{"id": "yes", "label": "Yes, Delete", "recorded": True}]}, None, None)
    assert q["title"].startswith("A message box with the buttons “No, Cancel” and “Yes, Delete” can pop up here")
    assert "a popup" not in q["title"]


def test_typed_words_are_placed_by_the_clock_when_the_recording_has_one(monkeypatch):
    """The first copy that shows the words was taken after the LAST click (4500 ms), so that click brought them up,
    although copy order alone would name the click before it."""
    import conxa_core.storage.session_events as se
    import conxa_core.storage.snapshots as sn

    acts = ["click", "scroll", "click", "click"]
    press = [1000, 2000, 3000, 4000]
    looked = [1500, 4500, 4500, 4500]
    copies = ["before", "late", "late", "late"]
    events = [{"action": {"action": a, "timestamp": f"1970-01-01T00:00:{p // 1000:02d}Z"},
               "page": {"url": "https://shop.test/a"}, "extras": {"sequence": i + 1},
               "snapshot": {"dom_hash": copies[i], "captured_ms": looked[i]}}
              for i, (a, p) in enumerate(zip(acts, press))]
    pages = {"before": "<p>Order</p>", "late": "<p>Saved it</p>"}
    monkeypatch.setattr(se, "read_session_events", lambda _s: events)
    monkeypatch.setattr(sn, "read_dom_snapshot", lambda _s, h: pages.get(h))
    from conxa_compile.conversation.clarify import step_where_words_appear

    steps = [_step(a, f"s{i}", source_event=i + 1) for i, a in enumerate(acts)]
    doc = _doc(steps)
    doc["meta"]["source_session_id"] = "s1"
    assert step_where_words_appear(doc, "saved it") == step_keys(steps)[3]
