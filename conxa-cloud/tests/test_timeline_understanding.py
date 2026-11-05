"""Pre-compile understanding: candidates, code-side validation, questions, decisions."""

from __future__ import annotations

import json

from conxa_compile.conversation import decisions
from conxa_compile.conversation import timeline_understanding as TU


def event(n, action, target="", value=None, url="https://app.test/", seg=None, dialog=None):
    return {"kind": "event", "event_id": n, "row_ref": f"r{n}", "action": action, "target": target,
            "value": value, "url": url, "tab": "tab_0", "t_ms": n * 1000, "segment": seg, "flags": [],
            **({"dialog": dialog} if dialog else {})}


MERGED = [
    event(1, "navigate", url="https://app.test/", value="https://app.test/"),
    event(2, "manual_navigate", value=json.dumps({"to_url": "https://app.test/acme/repo-one/settings"})),
    event(3, "type", "Search", value="INV-20931", seg={"intent": "find the invoice", "screen_after": "one row left"}),
    event(4, "type", "Password", value="{{REDACTED}}"),
    event(5, "click", "Download PDF", seg={"intent": "download the invoice"}),
    {"kind": "observation", "type": "off_page", "row_ref": "r6", "t_ms": 6000, "segment": {"intent": "download bar shows the file"}},
]


def raw_answer(**over):
    base = {
        "goal": "Download an invoice as a PDF", "short_summary": "Finds an invoice and downloads the PDF.",
        "values": [{"ref": "url:2", "verdict": "varies", "name": "Repo Name!", "literal": "acme/repo-one"},
                   {"ref": "typed:3", "verdict": "varies", "name": "invoice_number"},
                   {"ref": "click:5", "verdict": "fixed"}],
        "gaps": [{"row": "r6", "note": "A download bar appeared that the browser did not record."}],
        "detailed_summary": [{"row": "r3", "text": "Types the invoice number."}, {"row": "r99", "text": "ghost row"}],
        "questions": [
            {"kind": "confirm_before", "row": "r5", "prompt": "Download without asking?", "best_guess": "no_confirm", "cost_rank": 1},
            {"kind": "upload", "row": "r3", "prompt": "Same file each time?", "best_guess": "each_run", "cost_rank": 2},
            {"kind": "fork", "row": "r5", "prompt": "A popup asked something.", "best_guess": "by_input", "cost_rank": 3},
        ],
    }
    return {**base, **over}


def test_candidates_cover_urls_clicks_and_typed_but_never_hidden_values():
    cands = {c["ref"]: c for c in TU.candidates_from_timeline(MERGED)}
    assert set(cands) == {"url:2", "click:5", "typed:3"}        # site root and the password are skipped
    assert cands["url:2"]["literal"] == "acme/repo-one/settings"
    assert cands["typed:3"]["row_refs"] == ["r3"]


def test_model_input_hides_redacted_values_and_carries_what_video_saw():
    payload = TU.build_input(MERGED, "download invoices", "", TU.candidates_from_timeline(MERGED))
    rows = {r["ref"]: r for r in payload["rows"]}
    assert rows["r4"]["value"] == "(hidden)"
    assert rows["r3"]["seen"]["intent"] == "find the invoice" and "seen" not in rows["r2"]
    assert rows["r6"]["kind"] == "observation"


def test_validate_drops_unknown_refs_rows_and_unsafe_guesses():
    cands = TU.candidates_from_timeline(MERGED)
    raw = raw_answer(
        values=raw_answer()["values"] + [{"ref": "url:999", "verdict": "varies", "name": "x"}],
        gaps=[{"row": "r99", "note": "ghost"}],
        questions=raw_answer()["questions"] + [{"kind": "launch_missiles", "row": "r1", "prompt": "?"},
                                               {"kind": "upload", "row": "r42", "prompt": "?"}])
    out = TU.validate(raw, MERGED, cands)
    assert set(out["values"]) == {"url:2", "typed:3", "click:5"}
    assert out["values"]["url:2"] == {"verdict": "varies", "name": "repo_name", "literal": "acme/repo-one"}
    assert out["values"]["click:5"]["name"] == ""                # fixed values get no input name
    assert out["gaps"] == []
    kinds = [q["kind"] for q in out["questions"]]
    assert kinds == ["confirm_before", "upload", "value"]       # the fork on a plain click is dropped
    by_kind = {q["kind"]: q for q in out["questions"]}
    assert by_kind["confirm_before"]["best_guess"] == ""        # "just do it" is never pre-filled
    assert by_kind["upload"]["best_guess"] == "each_run"
    assert by_kind["value"]["ref"] == "url:2" and by_kind["value"]["best_guess"] == "varies"   # added back, code-worded


def test_validate_literal_must_sit_inside_the_recorded_value_and_names_are_unique():
    cands = TU.candidates_from_timeline(MERGED)
    out = TU.validate({"values": [{"ref": "url:2", "verdict": "varies", "name": "item", "literal": "somewhere/else"},
                                  {"ref": "typed:3", "verdict": "varies", "name": "item"}]}, MERGED, cands)
    assert out["values"]["url:2"]["literal"] == "acme/repo-one/settings"
    assert {out["values"]["url:2"]["name"], out["values"]["typed:3"]["name"]} == {"item", "item_2"}


def test_understand_timeline_uses_the_model_and_never_raises():
    out = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: raw_answer())
    assert out["source"] == "model" and out["goal"].startswith("Download")
    cands = {c["ref"]: c for c in out["candidates"]}
    assert cands["url:2"]["verdict"] == "varies" and cands["click:5"]["verdict"] == "fixed"

    def boom(*a):
        raise RuntimeError("proxy down")

    down = TU.understand_timeline(MERGED, "download invoices", call=boom)
    assert down["source"] == "fallback" and down["values"]["url:2"]["verdict"] == "unsure"
    assert out["detailed_summary"] == [{"row": "r3", "text": "Types the invoice number."}]     # the model's walkthrough, unknown rows dropped
    assert down["detailed_summary"] == [] and "phases" not in down      # no model answer, nothing invented
    assert TU.understand_timeline([], "x", call=boom)["source"] == "none"


def test_questions_are_the_models_own_ranked_prefilled_and_capped_at_five_open():
    und = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: raw_answer())
    qs = TU.questions_from_understanding(und, MERGED)
    # the model's own questions only: its gap (r6) is never asked
    assert [q["kind"] for q in qs] == ["confirm_before", "upload", "value"]
    assert all(q["phase"] == "pre_compile" for q in qs)
    by_id = {q["id"]: q for q in qs}
    assert by_id["upload:r3"]["best_guess"] == "each_run" and by_id["confirm_before:r5"]["best_guess"] == ""
    assert by_id["upload:r3"]["title"] == "Same file each time?"                  # the model's sentence
    assert by_id["value:url:2"]["items"][0]["event_ids"] == [2]
    assert "note:r6" not in by_id
    assert [q["status"] for q in qs] == ["open"] * 3
    many = {**und, "questions": [dict(und["questions"][0], row=f"r{i}") for i in range(1, 7)]}
    assert [q["status"] for q in TU.questions_from_understanding(many, MERGED)] == ["open"] * 5 + ["queued"]


def test_a_decision_popup_is_always_a_fork_and_a_plain_click_never_is():
    merged = MERGED + [event(7, "click", "Cancel", dialog="decision"), event(8, "click", "Close", dialog="dismissable")]
    out = TU.validate(raw_answer(
        values=[], questions=[
            {"effect": "popup_dismiss", "row": "r7", "prompt": "Close the popup?", "best_guess": "dismiss", "cost_rank": 1},
            {"effect": "fork", "row": "r8", "prompt": "Decide?", "cost_rank": 2}]),
        merged, TU.candidates_from_timeline(merged))
    # r5, the click right before the popup, is also asked about: the page itself asked "are you sure?".
    assert sorted((q["kind"], q["row"], q["best_guess"]) for q in out["questions"] if q["kind"] != "note") == [
        ("confirm_before", "r5", ""), ("fork", "r7", "")]


def _popup_event(n, buttons):
    return {**event(n, "click", "Replace", dialog="decision"), "dialog_buttons": buttons}


def test_a_popup_question_offers_one_choice_per_button_then_the_generic_ones_and_never_a_guess():
    merged = MERGED + [_popup_event(7, ["Replace", "Keep both", "Cancel"])]
    out = TU.validate(raw_answer(values=[], questions=[
        {"effect": "fork", "row": "r7", "prompt": "Decide?", "best_guess": "btn:Replace", "choices": [{"id": "stop", "label": "Halt"}]}]),
        merged, TU.candidates_from_timeline(merged))
    q = next(q for q in out["questions"] if q["kind"] == "fork")
    assert [c["id"] for c in q["choices"]] == ["btn:Replace", "btn:Keep both", "btn:Cancel", "by_input", "ask_human", "stop"]
    assert "Careful" in q["choices"][0]["detail"] and "Careful" not in q["choices"][1]["detail"]
    assert q["best_guess"] == "", "a popup choice is never pre-filled"
    assert next(c for c in q["choices"] if c["id"] == "stop")["label"] == "Halt", "the model may still reword the generic labels"


def test_a_popup_the_model_left_out_comes_back_with_its_buttons():
    merged = MERGED + [_popup_event(7, ["Keep editing", "Discard"])]
    out = TU.validate(raw_answer(values=[], questions=[]), merged, TU.candidates_from_timeline(merged))
    q = next(q for q in out["questions"] if q["kind"] == "fork")
    assert [c["id"] for c in q["choices"]][:2] == ["btn:Keep editing", "btn:Discard"]


def test_a_popup_with_no_recorded_buttons_keeps_only_the_generic_choices():
    merged = MERGED + [event(7, "click", "Replace", dialog="decision")]
    out = TU.validate(raw_answer(values=[], questions=[]), merged, TU.candidates_from_timeline(merged))
    q = next(q for q in out["questions"] if q["kind"] == "fork")
    assert [c["id"] for c in q["choices"]] == ["by_input", "ask_human", "stop"]


def test_a_decision_popup_the_model_left_out_is_put_back_and_so_is_an_upload():
    merged = MERGED + [event(7, "click", "Replace", dialog="decision"), event(8, "upload_intent", "file")]
    out = TU.validate(raw_answer(values=[], questions=[]), merged, TU.candidates_from_timeline(merged))
    assert sorted((q["kind"], q["row"]) for q in out["questions"]) == [("confirm_before", "r5"), ("fork", "r7"), ("upload", "r8")]
    assert {m["effect"] for m in TU.build_input(merged, "", "", [])["must_ask"]} == {"confirm_before", "fork", "upload"}


def test_one_question_per_value_and_per_row_and_a_note_needs_choices():
    ask = {"effect": "value", "row": "r2", "ref": "url:2", "prompt": "Same repo every time?", "cost_rank": 2}
    note = {"effect": "note", "row": "r5", "prompt": "If nothing matches?", "cost_rank": 1,
            "choices": [{"label": "Stop and tell me"}, {"label": "Skip it"}]}
    out = TU.validate(raw_answer(questions=[ask, dict(ask, prompt="Again?"), note,
                                            dict(note, row="r3", choices=[{"label": "only one"}])]),
                      MERGED, TU.candidates_from_timeline(MERGED))
    assert [(q["kind"], q["row"]) for q in out["questions"]] == [("note", "r5"), ("value", "r2")]
    assert [c["id"] for c in out["questions"][0]["choices"]] == ["c1", "c2"]
    assert out["questions"][1]["prompt"] == "Same repo every time?"


def test_a_value_question_names_an_address_by_its_last_part_and_hides_emails():
    long = {"kind": "url", "label": "", "recorded": "https://github.com/github/gitignore/blob/main/FuelPHP.gitignore",
            "literal": "github/gitignore/blob/main/FuelPHP.gitignore"}
    assert TU._short_label(long) == "FuelPHP.gitignore"
    words = {"kind": "click", "label": "word " * 20, "recorded": "x"}
    assert TU._short_label(words).endswith("word…") and len(TU._short_label(words)) <= 61
    assert TU._text("Google Account Ada ada@example.com", 200) == "Google Account Ada"


def test_a_page_load_a_click_caused_is_not_a_value_to_ask_about():
    nav = event(9, "manual_navigate", "", value=json.dumps({"from_url": "https://a.com/", "to_url": "https://a.com/x/1", "cause": "action"}))
    assert not [c for c in TU.candidates_from_timeline([nav]) if c["kind"] == "url"]


def test_no_summary_or_coverage_question_is_ever_asked():
    und = TU.understand_timeline(MERGED, "download invoices",
                                 call=lambda t, p, ms: raw_answer(questions=[], gaps=[{"row": "r6", "note": "gap"}]))
    qs = TU.questions_from_understanding(und, MERGED)
    assert not {q["kind"] for q in qs} & {"confirm_summary", "coverage", "chosen_values"}
    assert not [q for q in qs if q["id"].endswith(":r6")]   # a gap is never asked


def test_a_note_on_a_row_the_model_called_a_gap_is_dropped_in_code():
    cands = TU.candidates_from_timeline(MERGED)
    note = {"effect": "note", "row": "r6", "prompt": "Was the download bar fine?", "cost_rank": 1,
            "choices": [{"label": "Yes"}, {"label": "No"}]}
    out = TU.validate(raw_answer(questions=[note]), MERGED, cands)           # r6 is the gap row
    assert not [q for q in out["questions"] if q["row"] == "r6"]
    out = TU.validate(raw_answer(questions=[dict(note, row="r5")]), MERGED, cands)   # r5 is not a gap
    assert [(q["kind"], q["row"]) for q in out["questions"] if q["kind"] == "note"] == [("note", "r5")]


def test_the_one_gap_that_is_asked_survives_the_gap_rule():
    pick = {"kind": "observation", "type": "off_page", "row_ref": "r7", "t_ms": 7000,
            "segment": {"intent": "selects a file in File Explorer", "visible_target": "Open dialog"}}
    merged = MERGED + [pick]
    out = TU.validate(raw_answer(questions=[], gaps=[{"row": "r7", "note": "File Explorer picked a file."}]),
                      merged, TU.candidates_from_timeline(merged))
    assert [(q["kind"], q["row"]) for q in out["questions"] if q["row"] == "r7"] == [("note", "r7")]


def test_a_file_picked_outside_the_browser_is_asked_unless_the_browser_recorded_an_upload():
    pick = {"kind": "observation", "type": "off_page", "row_ref": "r7", "t_ms": 7000,
            "segment": {"intent": "selects a file in File Explorer", "visible_target": "Open dialog"}}
    merged = MERGED + [pick]
    qs = TU.questions_from_understanding(TU.understand_timeline(merged, "x", call=lambda t, p, ms: raw_answer(questions=[])), merged)
    asked = [q for q in qs if q["id"].endswith(":r7")]
    assert len(asked) == 1 and "outside the browser" in asked[0]["title"]
    assert [c["id"] for c in asked[0]["choices"]] == ["c1", "c2"]
    # an upload the browser did record close by means the pick was captured
    assert TU.missed_file_picks(merged + [event(8, "upload_intent", "file", value="[]")]) == []
    # an idle wait or a download bar is never asked
    assert TU.missed_file_picks(MERGED) == []


def test_decisions_are_keyed_by_event_and_survive_a_replaced_answer(tmp_path):
    und = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: raw_answer())
    qs = {q["id"]: q for q in TU.questions_from_understanding(und, MERGED)}
    decisions.record(tmp_path, qs["value:url:2"], {"choice": "varies"})
    stored = {d["qid"]: d for d in decisions.load(tmp_path)}
    assert stored["value:url:2"]["values"] == [
        {"ref": "url:2", "name": "repo_name", "literal": "acme/repo-one", "kind": "url", "event_ids": [2]}]

    decisions.record(tmp_path, qs["value:url:2"], {"choice": "fixed"})            # re-answered
    again = decisions.load(tmp_path)
    assert len(again) == 1 and again[0]["values"] == [] and again[0]["choice"] == "fixed"


def test_a_note_answer_is_stored_as_text_and_changes_nothing_else(tmp_path):
    q = {"id": "note:r5", "kind": "note", "title": "If nothing matches?", "event_ids": [5],
         "choices": [{"id": "c1", "label": "Stop and tell me"}, {"id": "c2", "label": "Skip it"}]}
    d = decisions.record(tmp_path, q, {"choice": "c2"})[0]
    assert d["note"] == "If nothing matches? Skip it" and "values" not in d


def test_an_answer_the_question_never_offered_is_remembered_as_skipped(tmp_path):
    q = {"id": "fork:r5", "kind": "fork", "title": "Popup?", "event_ids": [5],
         "choices": [{"id": "by_input", "label": "Let whoever uses it decide"}, {"id": "stop", "label": "Stop"}]}
    d = decisions.record(tmp_path, q, {"choice": "option:replace"})[0]
    assert d["skipped"] is True and "choice" not in d
    d = decisions.record(tmp_path, q, {"choice": "by_input"})[0]
    assert d["skipped"] is False and d["choice"] == "by_input"


# ─── choosing one item out of a list is not a silent "fixed" ────────────────────────────────

def _row(tag, parent_tag, n_rows, cap=False):
    rows = "".join(f"<{tag} class='r'>x</{tag}>" for _ in range(n_rows))
    html = f"<{parent_tag}>{rows}</{parent_tag}>"
    return {"tag": tag, "id": "", "classes": ["r"], "outer_html": "<tr class='r'>x</tr>"}, \
           {"tag": parent_tag, "id": "", "classes": [], "outer_html": html + ("x" * 2000 if cap else "")}


def _ev(role, *anc):
    chain = []
    for a in anc:
        chain.extend(a)
    return {"semantic": {"role": role}, "ancestors": chain}


def test_repeating_peers_tells_an_item_in_a_list_from_a_control():
    from conxa_compile.compiler.entity_binding import repeating_peers

    div = {"tag": "div", "id": "", "classes": ["cell"], "outer_html": "<div/>"}
    row, body = _row("tr", "tbody", 4)
    assert repeating_peers(_ev("a", [div], [row, body])) == 4                    # a link in a table row
    assert repeating_peers(_ev("button", [div], [row, body])) == 0               # a per-row button is a control
    assert repeating_peers(_ev("a", [div, {**div, "classes": ["g"]}, div])) == 0  # no row anywhere
    head_row, head = _row("tr", "thead", 4)
    assert repeating_peers(_ev("a", [head_row, head])) == 0                      # a header row is not a record
    one_row, only = _row("tr", "tbody", 1)
    assert repeating_peers(_ev("a", [one_row, only])) == 0                       # a lone row repeats nothing
    big_row, big = _row("tr", "tbody", 1, cap=True)                              # parent cut off by the recorder
    assert repeating_peers(_ev("a", [big_row, big])) >= 3


LISTED = [
    event(1, "navigate", url="https://app.test/", value="https://app.test/"),
    {**event(2, "click", "Ada.gitignore"), "peers": 5},
    event(3, "click", "Download raw file"),
    event(4, "download_observed", value=json.dumps({"suggested_filename": "Ada.gitignore"})),
    event(5, "upload_intent", "Upload", value=json.dumps([{"name": "Ada.gitignore", "size": 51}])),
]


def test_an_item_picked_from_a_list_is_a_candidate_even_after_many_control_clicks():
    controls = [event(10 + i, "click", f"Menu {i}") for i in range(8)]
    cands = {c["ref"]: c for c in TU.candidates_from_timeline(controls + [{**event(30, "click", "Pick me"), "peers": 4}])}
    assert cands["click:30"]["in_set"] is True and cands["click:10"]["in_set"] is False
    assert "click:30" in cands                                                    # not crowded out by the control cap


def test_an_unmentioned_list_item_is_asked_not_assumed_fixed_whether_or_not_the_model_answered():
    for call in (lambda t, p, ms: raw_answer(values=[], questions=[]), lambda t, p, ms: None):
        out = TU.understand_timeline(LISTED, "", call=call)
        assert out["values"]["click:2"]["verdict"] == "unsure" and out["values"]["click:2"]["name"]
        value_qs = [q for q in out["questions"] if q["kind"] == "value"]
        assert [q["ref"] for q in value_qs] == ["click:2"] and value_qs[0]["best_guess"] == ""   # asked, nothing pre-ticked


def test_a_model_that_explicitly_says_fixed_for_a_list_item_is_respected():
    out = TU.understand_timeline(LISTED, "download Ada.gitignore", call=lambda t, p, ms: raw_answer(
        values=[{"ref": "click:2", "verdict": "fixed"}], questions=[]))
    assert out["values"]["click:2"]["verdict"] == "fixed"
    assert not [q for q in out["questions"] if q["kind"] == "value"]


def test_the_model_is_told_which_clicks_were_list_items():
    payload = TU.build_input(LISTED, "", "", TU.candidates_from_timeline(LISTED))
    assert {r["ref"]: r.get("peers") for r in payload["rows"]}["r2"] == 5
    assert [c["ref"] for c in payload["candidates"] if c.get("in_set")] == ["click:2"]


def test_a_file_the_workflow_downloaded_itself_is_not_asked_about_again():
    assert TU.must_ask(LISTED) == []                                              # the upload follows the download
    other = LISTED[:3] + [event(5, "upload_intent", "Upload", value=json.dumps([{"name": "report.pdf"}]))]
    assert TU.must_ask(other) == [{"row": "r5", "effect": "upload"}]              # a file from elsewhere still is


def test_a_model_upload_question_on_a_self_downloaded_file_is_dropped():
    out = TU.validate({"questions": [{"effect": "upload", "row": "r5", "prompt": "Same file?", "best_guess": "each_run"}]},
                      LISTED, TU.candidates_from_timeline(LISTED))
    assert not [q for q in out["questions"] if q["kind"] == "upload"]


def test_an_answer_that_asks_for_a_change_but_matches_nothing_is_reported_not_dropped():
    from conxa_compile.conversation import replay

    stored = [{"qid": "upload:r5", "kind": "upload", "choice": "each_run", "skipped": False, "event_ids": [5]},
              {"qid": "upload:r6", "kind": "upload", "choice": "fixed_path", "skipped": False, "event_ids": [6]}]
    out = replay.plan(stored, {"skills": [{"steps": []}]}, [], lambda key: None)
    assert out["groups"] == [] and out["unapplied"] == [{"kind": "upload", "choice": "each_run"}]    # "leave it" owes no report


def test_a_div_grid_row_counts_only_when_it_says_it_is_a_row():
    from conxa_compile.compiler.entity_binding import repeating_peers

    def grid(cls, role="", n=4, rid=""):
        attrs = (f' role="{role}"' if role else "")
        row = {"tag": "div", "id": rid, "classes": cls, "outer_html": f"<div{attrs}>x</div>"}
        kids = "".join(f"<div class='{' '.join(cls)}'>x</div>" for _ in range(n))
        return _ev("a", [row, {"tag": "div", "id": "", "classes": [], "outer_html": f"<div>{kids}</div>"}])

    assert repeating_peers(grid(["file-card"])) == 4                 # a class that names a record
    assert repeating_peers(grid(["x"], role="listitem")) == 4        # an ARIA row-like role
    assert repeating_peers(grid(["x"], rid="item-3")) == 4           # an id that names one
    assert repeating_peers(grid(["row"])) == 0                       # bare layout .row is not a record
    assert repeating_peers(grid(["wrapper"])) == 0                   # repeated layout proves nothing


def test_a_download_and_the_upload_that_uses_it_are_linked_by_explicit_slots():
    from conxa_compile.compiler.upload_binding import apply_bindings_to_compiled_steps
    from conxa_core.models.skill_spec import SkillStep

    dl = SkillStep(action="download_observed", value=json.dumps({"url": "blob:x", "suggested_filename": "Ada.gitignore"}))
    up = SkillStep(action="upload_intent", value=json.dumps([{"name": "Ada.gitignore", "size": 51}]))
    event = {"action": {"action": "upload_intent", "value": up.value}, "extras": {}}
    apply_bindings_to_compiled_steps([dl, up], [event])
    assert up.value == "{{downloaded_file}}" and up.consumes == ["downloaded_file"]
    assert dl.produces == ["downloaded_file_1", "downloaded_file"]          # the only download also answers to the bare name


def test_a_reply_that_was_cut_off_is_recorded_with_its_shape_in_the_fallback_reason():
    cut = {"text": '{"goal": "Download a file", "short_summary": "Downloads', "output": "x"}
    out = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: cut)
    assert out["source"] == "fallback"
    assert "answer keys: ['output', 'text']" in out["fallback_reason"] and '"goal": "Download a file"' in out["fallback_reason"]


def test_an_answer_nested_one_level_down_is_the_same_answer():
    nested = {"understanding": raw_answer()}
    out = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: nested)
    assert out["source"] == "model" and out["goal"].startswith("Download")
    assert "fallback_reason" not in out
    ambiguous = {"a": raw_answer(), "b": raw_answer()}                      # two candidates: nothing is guessed
    assert TU.understand_timeline(MERGED, "x", call=lambda t, p, ms: ambiguous)["source"] == "fallback"


def test_no_answer_at_all_names_the_router_detail_when_there_is_some():
    def silent(task, payload, ms):
        return None

    out = TU.understand_timeline(MERGED, "download invoices", call=silent)
    assert out["fallback_reason"] == "the model returned no answer"


def test_model_input_is_compact():
    seg = lambda **k: k      # noqa: E731
    merged = [
        event(1, "click", "A", url="https://app.test/a", seg=seg(intent="open it", purpose="to read it", screen_before="home", screen_after="page A")),
        event(2, "click", "B", url="https://app.test/a", seg=seg(intent="go on", screen_before="page A", screen_after="page B")),
        event(3, "click", "C", url="https://app.test/c"),
        event(4, "click", "D", url="https://app.test/c", seg=seg(intent="again", screen_before="page C")),
    ]
    rows = TU.build_input(merged, "", "", [])["rows"]
    assert "kind" not in rows[0] and "role" not in rows[0] and "value" not in rows[0]
    assert rows[0]["seen"]["intent"] == "open it — to read it" and "purpose" not in rows[0]["seen"]
    assert rows[0]["seen"]["screen_before"] == "home"                 # nothing came before it
    assert "screen_before" not in rows[1]["seen"]                     # r1 already said how its screen ended
    assert rows[0]["url"] and "url" not in rows[1] and rows[2]["url"].endswith("/c") and "url" not in rows[3]
    assert rows[3]["seen"]["screen_before"] == "page C"               # r3 had no video segment, so this one is kept
    assert not any(v is None for r in rows for v in r.values())


def test_rows_beyond_the_old_cap_are_still_sent():
    merged = [event(n, "click", f"Item {n}") for n in range(1, 201)]
    assert len(TU.build_input(merged, "", "", [])["rows"]) == 200


def test_what_the_person_said_while_recording_is_passed_on_and_wins():
    seen = {}

    def call(task, payload, ms):
        seen["payload"] = payload
        return raw_answer(values=[{"ref": "typed:3", "verdict": "varies", "name": "invoice_number"}],
                          questions=[{"effect": "value", "row": "r3", "ref": "typed:3", "prompt": "Does it change?", "cost_rank": 1}])

    out = TU.understand_timeline(MERGED, "download invoices", hints={"3": False}, existing_inputs={"tenant"}, call=call)
    assert seen["payload"]["told"] == {"typed:3": "fixed"} and seen["payload"]["existing_inputs"] == ["tenant"]
    assert out["values"]["typed:3"] == {"verdict": "fixed", "name": "", "literal": "INV-20931"}     # the person's word beats the model's
    assert not [q for q in out["questions"] if q.get("ref") == "typed:3"]                         # and nothing is asked about it

    said_varies = TU.understand_timeline(MERGED, "download invoices", hints={"3": True},
                                         call=lambda t, p, ms: raw_answer(values=[]))
    assert said_varies["values"]["typed:3"]["verdict"] == "varies" and said_varies["values"]["typed:3"]["name"]
    assert "told" not in TU.build_input(MERGED, "", "", TU.candidates_from_timeline(MERGED))


def test_an_unreadable_reply_is_asked_for_once_more_and_a_missing_one_is_not():
    calls = []

    def flaky(task, payload, ms):
        calls.append(payload)
        return {"text": "cut off mid-sent"} if len(calls) == 1 else raw_answer()

    out = TU.understand_timeline(MERGED, "download invoices", call=flaky)
    assert len(calls) == 2 and "retry" not in calls[0] and "retry" in calls[1]
    assert out["source"] == "model" and out["retried"] is True

    calls.clear()
    hopeless = TU.understand_timeline(MERGED, "download invoices", hints={"3": True},
                                      call=lambda t, p, ms: calls.append(p) or {"text": "nope"})
    assert len(calls) == 2 and hopeless["source"] == "fallback"      # a recording-time answer never fakes a model answer

    calls.clear()
    assert TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: calls.append(p)) ["source"] == "fallback"
    assert len(calls) == 1                                           # no reply at all (proxy down): do not wait twice



# --- grading and the rules table --------------------------------------------------------------

def _graded(raw, hints=None):
    cands = TU.candidates_from_timeline(MERGED)
    told = TU.told_from_hints(cands, hints)
    return TU._grade(TU.validate(raw, MERGED, cands, told=told), cands, told)


def test_grade_thresholds():
    assert _graded(raw_answer()) == ("ok", [])
    assert _graded({})[0] == "unusable"
    assert _graded(raw_answer(goal=""))[1] == ["no goal"]
    # 2 of the 3 candidates unjudged is more than half; the person's own answer takes one candidate off what the model owes
    assert _graded(raw_answer(values=raw_answer()["values"][:1]))[0] == "weak"
    assert _graded(raw_answer(values=raw_answer()["values"][:1]), hints={"3": True, "5": False})[0] == "ok"
    ghosts = [{"ref": f"url:9{i}", "verdict": "varies"} for i in range(TU.WEAK_REF_DROPS)]
    assert _graded(raw_answer(values=raw_answer()["values"] + ghosts, detailed_summary=[]))[0] == "weak"
    assert _graded(raw_answer(values=raw_answer()["values"] + ghosts[:-1], detailed_summary=[]))[0] == "ok"


def test_validation_report_counts_each_drop_with_its_first_example():
    out = TU.validate(raw_answer(values=[{"ref": "url:9", "verdict": "varies"}, {"ref": "url:8", "verdict": "varies"}]),
                      MERGED, TU.candidates_from_timeline(MERGED))
    assert out["validation_report"]["reference"]["unknown_ref"] == {"count": 2, "example": "url:9"}
    assert set(out["validation_report"]) <= set(TU.STAGES)


def test_unhashable_junk_in_a_model_answer_is_dropped_not_raised():
    out = TU.validate({"values": [{"ref": ["x"]}], "gaps": [{"row": ["r1"], "note": "n"}], "questions": [{"effect": ["fork"], "row": ["r1"]}]},
                      MERGED, TU.candidates_from_timeline(MERGED))
    assert out["gaps"] == [] and out["validation_report"]["reference"]["unknown_ref"]["count"] == 1


def test_the_rules_table_matches_the_effect_table_in_the_workflow_graph():
    import re
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "docs" / "artifacts" / "conxa-workflow-engine" / "conxa-workflow-engine.html").read_text("utf-8")
    rows = re.findall(r'<tr><td class="k">(\w+)</td><td>(.*?)</td><td>(.*?)</td></tr>', html.split("Question effects", 1)[1].split("</table>", 1)[0])
    assert {r[0] for r in rows} == set(TU.RULES)
    for effect, ids, guess in rows:
        rule = TU.RULES[effect]
        if rule["choices"]:
            assert re.findall(r"<code>(\w+)</code>", ids) == [c[0] for c in rule["choices"]], effect
        assert guess == ("never" if not rule["guesses"] else "confirm only" if rule["unsafe"] else "yes"), effect


# --- quality: what the model is shown, and what is never left to a silent default -------------------


def test_model_input_carries_title_popup_buttons_what_the_page_said_and_video_agreement():
    a = {**event(1, "click", "Save", url="https://app.test/a"), "title": "Settings",
         "said": [{"kind": "alert", "words": ["name", "taken"]}]}
    b = {**event(2, "type", "Name", value="x" * 300, url="https://app.test/a"), "title": "Settings", "flags": ["value_seen_in_both"]}
    c = {**event(3, "click", "Delete", url="https://app.test/a", dialog="decision"), "title": "Confirm", "dialog_buttons": ["Delete", "Cancel"]}
    rows = TU.build_input([a, b, c], "", "", [])["rows"]
    assert rows[0]["title"] == "Settings" and "title" not in rows[1] and rows[2]["title"] == "Confirm"
    assert rows[0]["said"] == ["alert: name taken"]
    assert rows[1]["video"] == "confirmed" and len(rows[1]["value"]) == TU.VALUE_CHARS
    assert rows[2]["buttons"] == ["Delete", "Cancel"]
    assert "video" not in rows[0] and "buttons" not in rows[0]


def test_a_long_recording_drops_scrolls_first_and_never_a_row_a_candidate_rests_on(monkeypatch):
    monkeypatch.setattr(TU, "MAX_ROWS", 10)
    merged = [event(n, "scroll") for n in range(1, 9)] + [
        event(9, "type", "Search", value="INV-1"), event(10, "click", "Download PDF"),
        event(11, "click", "Other"), event(12, "click", "More")]
    cands = TU.candidates_from_timeline(merged)
    payload = TU.build_input(merged, "", "", cands)
    refs = [r["ref"] for r in payload["rows"]]
    assert len(refs) == 10 and {"r9", "r10", "r11", "r12"} <= set(refs) and refs[0] == "r3"
    assert payload["rows_omitted"] == 2 and not any(c["unsent"] for c in cands)


def test_a_candidate_whose_rows_are_cut_off_is_left_out_of_the_input_and_asked_instead(monkeypatch):
    monkeypatch.setattr(TU, "MAX_ROWS", 4)
    merged = [event(n, "click", f"Item {n}", url=f"https://app.test/{n}") for n in range(1, 13)]
    cands = TU.candidates_from_timeline(merged)
    payload = TU.build_input(merged, "pick items", "", cands)
    assert len(payload["rows"]) == 4 and payload["rows_omitted"] == 8
    sent = {c["ref"] for c in payload["candidates"]}
    assert sent and any(c["unsent"] for c in cands) and all(c["unsent"] == (c["ref"] not in sent) for c in cands)

    out = TU.understand_timeline(merged, "pick items", call=lambda t, p, ms: raw_answer(values=[], questions=[]))
    assert out["rows_omitted"] == 8
    unsent_urls = [c["ref"] for c in out["candidates"] if c["unsent"] and c["kind"] == "url"]
    assert all(out["values"][ref]["verdict"] == "unsure" for ref in unsent_urls)       # asked, never assumed fixed


def test_a_page_address_the_model_left_unjudged_is_asked_again_then_asked_of_the_person():
    calls = []

    def model(task, payload, ms):
        calls.append(payload)
        return raw_answer(values=[{"ref": "typed:3", "verdict": "varies", "name": "invoice_number"},
                                  {"ref": "click:5", "verdict": "fixed"}], questions=[])

    out = TU.understand_timeline(MERGED, "download invoices", call=model)
    assert len(calls) == 2 and "url:2" in calls[1]["retry"]
    assert out["values"]["url:2"]["verdict"] == "unsure"
    asked = [q for q in out["questions"] if q.get("ref") == "url:2"]
    assert asked and asked[0]["best_guess"] == ""                       # nothing pre-ticked

    calls.clear()
    no_task = TU.understand_timeline(MERGED, "", call=model)
    assert "url:2" not in no_task["values"]                             # nothing to compare it against: not asked


def test_review_context_reads_the_understanding_and_the_persons_answer_wins(tmp_path):
    assert TU.review_context(tmp_path) is None                          # never went through the understanding
    (tmp_path / "understanding.json").write_text(json.dumps({
        "goal": "Download an invoice.",
        "detailed_summary": [{"row": "r3", "text": "Types the invoice number."}, {"row": "r99", "text": "ghost"}],
        "decided": [{"row": "r1", "text": "Same page each run.", "reason": "x"}],
        "candidates": [{"ref": "typed:3", "event_ids": [3], "literal": "INV-20931", "verdict": "fixed"},
                       {"ref": "click:5", "event_ids": [5], "literal": "Download PDF", "verdict": "unsure"}]}), encoding="utf-8")
    (tmp_path / "merged_timeline.json").write_text(json.dumps(MERGED), encoding="utf-8")
    decisions.record(tmp_path, {"id": "q1", "kind": "value", "event_ids": [3]}, {"choice": "varies"})
    ctx = TU.review_context(tmp_path)
    assert ctx["goal"] == "Download an invoice."
    assert ctx["summary_by_event"] == {"3": "Types the invoice number."}      # a row the timeline does not have is dropped
    assert ctx["settled"] == [{"literal": "INV-20931", "verdict": "varies"}]  # the person's answer beat the model; "unsure" is not settled
    assert ctx["decided"] == ["Same page each run."]


# ── Phase 0: what the caps hid is counted ───────────────────────────────────────────────────


def test_candidates_past_a_bucket_cap_are_counted_and_reach_the_saved_understanding():
    rows = [{"kind": "event", "row_ref": f"r{i}", "event_id": i, "action": "click", "target": f"Item number {i}"} for i in range(1, 10)]
    cuts: dict[str, int] = {}
    assert len(TU.candidates_from_timeline(rows, cuts)) == TU.MAX_CLICK_CANDIDATES and cuts == {"click": 3}
    out = TU.understand_timeline(rows, "x", call=lambda t, p, ms: None)
    assert out["limits"] == {"candidates": {"click": 3}}
    assert "limits" not in TU.understand_timeline(rows[:2], "x", call=lambda t, p, ms: None)


# ── Phase 3: a dropdown / radio / date is asked before the build ────────────────────────────


def _control_timeline():
    return [{"kind": "event", "row_ref": "r1", "event_id": 5, "action": "select", "target": "Country", "value": "de",
             "control": {"kind": "choice", "recorded": "Germany"}}]


def test_a_dropdown_becomes_a_control_candidate_named_by_its_label_not_its_value():
    cands = TU.candidates_from_timeline(_control_timeline())
    assert [(c["kind"], c["recorded"], c["event_ids"]) for c in cands] == [("control", "Germany", [5])]


def test_a_control_the_model_calls_varies_needs_no_question_and_one_it_calls_fixed_is_asked_unticked_by_code():
    merged = _control_timeline()
    varies = TU.understand_timeline(merged, "x", call=lambda t, p, ms: {"goal": "g", "short_summary": "s",
                                    "values": [{"ref": "control:5", "verdict": "varies", "name": "country"}], "questions": []})
    assert TU.questions_from_understanding(varies, merged) == []                  # the compiler already made it an input
    fixed = TU.understand_timeline(merged, "x", call=lambda t, p, ms: {"goal": "g", "short_summary": "s",
                                   "values": [{"ref": "control:5", "verdict": "fixed"}], "questions": []})
    qs = TU.questions_from_understanding(fixed, merged)
    assert [(q["kind"], q["best_guess"]) for q in qs] == [("value", "fixed")]       # pinning it is the person's call
    assert qs[0]["items"][0]["kind"] == "control"


# ── Phase 3: the second read ────────────────────────────────────────────────────────────────


def _audit_call(items):
    def call(task, payload, ms):
        return {"questions": items} if task == "clarify_audit" else raw_answer(questions=[])
    return call


def test_the_second_read_is_off_by_default_shadow_saves_and_on_asks_at_most_two(monkeypatch):
    from conxa_core.config import settings

    good = [{"row": "r4", "facet": "alternatives", "prompt": "What should happen if no invoice matches?", "why": "The recording only shows a match.",
             "choices": [{"label": "Stop and report it"}, {"label": "Skip it and finish"}]}]
    calls = []

    def call(task, payload, ms):
        calls.append(task)
        return {"questions": good} if task == "clarify_audit" else raw_answer(questions=[])

    monkeypatch.setattr(settings, "clarify_audit", "off")
    off = TU.understand_timeline(MERGED, "download invoices", call=call)
    assert "audit" not in off and calls == ["timeline_understanding"]
    monkeypatch.setattr(settings, "clarify_audit", "shadow")
    shadow = TU.understand_timeline(MERGED, "download invoices", call=call)
    assert [q["facet"] for q in shadow["audit"]["questions"]] == ["alternatives"] and shadow["questions"] == off["questions"]
    monkeypatch.setattr(settings, "clarify_audit", "on")
    on = TU.understand_timeline(MERGED, "download invoices", call=call)
    note = next(q for q in on["questions"] if q["kind"] == "note")
    assert note["facet"] == "alternatives" and note["best_guess"] == "" and [c["label"] for c in note["choices"]] == ["Stop and report it", "Skip it and finish"]


def test_the_second_read_drops_what_cannot_be_acted_on_and_counts_it(monkeypatch):
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "clarify_audit", "shadow")
    items = [{"row": "r4", "facet": "made_up", "prompt": "p", "choices": [{"label": "a"}, {"label": "b"}]},
             {"row": "r999", "facet": "scope", "prompt": "p", "choices": [{"label": "a"}, {"label": "b"}]},
             {"row": "r4", "facet": "scope", "prompt": "Open https://x.test/a?", "choices": [{"label": "a"}, {"label": "b"}]},
             {"row": "r4", "facet": "scope", "prompt": "Only one choice?", "choices": [{"label": "a"}]},
             "not a dict"]
    out = TU.understand_timeline(MERGED, "download invoices", call=_audit_call(items))
    report = out["audit"]["validation_report"]
    assert out["audit"]["questions"] == []
    assert {"unknown_facet", "audit_selector_like", "malformed_item"} <= {r for stage in report.values() for r in stage}
