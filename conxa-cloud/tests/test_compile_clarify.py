"""BUILD-39 / BUILD-32: compile progress, the compile queue, Clarify questions and their answers."""

from __future__ import annotations

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
    assert p.rows()[0] == {"key": "clean", "label": "Cleaned up the recording", "status": "done", "detail": "14 actions kept"}
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
    assert qs[1]["title"] == "“Send invoice” can’t be undone. Should a person confirm first?"
    values = qs[2]
    assert values["multi"] and [c["id"] for c in values["choices"]] == ["po_number", "description"]
    assert values["choices"][0]["detail"] == "PO-44821"
    done = qs[4]
    # the message is pre-ticked; the address and the two "input shows up" offers are not
    assert [c["selected"] for c in done["choices"]] == [True, False, False, False]


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
        {**_step("ai_review", "confirm"), "ai_review_output_schema": {"type": "object", "required": ["confirmed"], "properties": {"confirmed": {"type": "boolean", "enum": [True]}}}, "ai_review_on_failure": "abort", "action": "ai_review"},
        _step("click", "send_invoice", consequence="irreversible"),
    ])
    assert generate_questions(doc) == []


def test_steps_are_named_not_numbered():
    assert step_name(_step("click", "send_invoice")) == "Send invoice"
    assert step_name(_step("click", "x", semantic_description="Step 4: Send invoice button")) == "Send invoice button"
    assert step_name({"action": "scroll"}) == "Scroll"


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
    assert plan_answer(_q(doc, "upload"), {"choice": "input"}, doc) == ([], "Asks for the file each run")
    ops, _ = plan_answer(_q(doc, "upload"), {"choice": "fixed"}, doc)
    assert ops[0] == {"op": "patch", "step_key": keys[3], "patch": {"value": "C:\\Users\\me\\Desktop\\invoice.pdf"}}

    # pop-up: both answers are the existing confirm mutation; "one-off" keeps it a step
    assert plan_answer(_q(doc, "popup"), {"choice": "dismiss"}, doc)[0] == [{"op": "confirm_optional", "step_key": keys[4], "keep_as_step": False}]
    assert plan_answer(_q(doc, "popup"), {"choice": "once"}, doc)[0][0]["keep_as_step"] is True

    # confirm: yes inserts a review before the step; no does nothing
    ops, receipt = plan_answer(_q(doc, "confirm"), {"choice": "yes"}, doc)
    assert ops[0]["op"] == "insert_review" and ops[0]["before_step_key"] == keys[5] and receipt == "A person confirms"
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
    assert receipt == "The message “Invoice sent” appears"


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
    assert fresh["choices"][0]["label"] == "The message “Invoice “po number” sent” appears"
    assert "input:po_number" not in {c["id"] for c in fresh["choices"]}


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


def test_record_plan_keeps_a_short_readable_checklist_and_never_raises():
    from conxa_compile.conversation import record_plan

    assert record_plan.generate("") == []
    good = {"steps": ["Open the invoices page", " Open the  invoices page ", "Click New invoice", "x" * 300]}
    steps = record_plan.generate("Create an invoice", call=lambda *_: good)
    assert steps[:2] == ["Open the invoices page", "Click New invoice"] and len(steps[2]) == 100
    assert record_plan.generate("t", call=lambda *_: {"steps": ["only one"]}) == []     # too thin to be a plan
    assert record_plan.generate("t", call=lambda *_: None) == []

    def boom(*_):
        raise RuntimeError("no providers")

    assert record_plan.generate("t", call=boom) == []


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
