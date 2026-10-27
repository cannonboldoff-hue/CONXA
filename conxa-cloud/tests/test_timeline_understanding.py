"""Pre-compile understanding: candidates, code-side validation, questions, decisions."""

from __future__ import annotations

import json

from conxa_compile.conversation import decisions
from conxa_compile.conversation import timeline_understanding as TU


def event(n, action, target="", value=None, url="https://app.test/", seg=None):
    return {"kind": "event", "event_id": n, "row_ref": f"r{n}", "action": action, "target": target,
            "value": value, "url": url, "tab": "tab_0", "t_ms": n * 1000, "segment": seg, "flags": []}


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
        "phases": [{"name": "Find it", "from_row": "r2", "to_row": "r3"}],
        "values": [{"ref": "url:2", "verdict": "varies", "name": "Repo Name!", "literal": "acme/repo-one"},
                   {"ref": "typed:3", "verdict": "varies", "name": "invoice_number"},
                   {"ref": "click:5", "verdict": "fixed"}],
        "success_check": "the download bar shows the file",
        "gaps": [{"row": "r6", "note": "A download bar appeared that the browser did not record."}],
        "detailed_summary": [{"row": "r3", "text": "Types the invoice number."}],
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
    assert rows["r3"]["seen"]["intent"] == "find the invoice" and rows["r2"]["seen"] is None
    assert rows["r6"]["kind"] == "observation"


def test_validate_drops_unknown_refs_rows_and_unsafe_guesses():
    cands = TU.candidates_from_timeline(MERGED)
    raw = raw_answer(
        values=raw_answer()["values"] + [{"ref": "url:999", "verdict": "varies", "name": "x"}],
        phases=[{"name": "bad", "from_row": "r9", "to_row": "r1"}, {"name": "back", "from_row": "r3", "to_row": "r1"}],
        gaps=[{"row": "r99", "note": "ghost"}],
        questions=raw_answer()["questions"] + [{"kind": "launch_missiles", "row": "r1", "prompt": "?"},
                                               {"kind": "upload", "row": "r42", "prompt": "?"}])
    out = TU.validate(raw, MERGED, cands)
    assert set(out["values"]) == {"url:2", "typed:3", "click:5"}
    assert out["values"]["url:2"] == {"verdict": "varies", "name": "repo_name", "literal": "acme/repo-one"}
    assert out["values"]["click:5"]["name"] == ""                # fixed values get no input name
    assert out["phases"] == [] and out["gaps"] == []
    kinds = [q["kind"] for q in out["questions"]]
    assert kinds == ["confirm_before", "upload", "fork"]
    by_kind = {q["kind"]: q for q in out["questions"]}
    assert by_kind["confirm_before"]["best_guess"] == ""        # "just do it" is never pre-filled
    assert by_kind["upload"]["best_guess"] == "each_run"
    assert by_kind["fork"]["best_guess"] == ""                   # a decision popup is never pre-filled


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
    assert down["detailed_summary"][0]["text"] == "find the invoice"      # built from the timeline
    assert TU.understand_timeline([], "x", call=boom)["source"] == "none"


def test_questions_are_ranked_prefilled_and_capped_at_five_open():
    und = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: raw_answer())
    qs = TU.questions_from_understanding(und, MERGED)
    assert [q["kind"] for q in qs][:3] == ["confirm_summary", "coverage", "chosen_values"]
    assert all(q["phase"] == "pre_compile" for q in qs)
    by_id = {q["id"]: q for q in qs}
    assert by_id["confirm_summary"]["best_guess"] == "yes"
    assert by_id["upload:r3"]["best_guess"] == "each_run" and by_id["fork:r5"]["best_guess"] == ""
    assert by_id["confirm_before:r5"]["best_guess"] == ""
    assert [q["status"] for q in qs] == ["open"] * 5 + ["queued"] * (len(qs) - 5)
    chosen = by_id["chosen_values"]
    assert {c["id"] for c in chosen["choices"] if c.get("selected")} == {"url:2"}   # typed text is not asked here
    assert chosen["items"][0]["event_ids"] == [2]


def test_decisions_are_keyed_by_event_and_survive_a_replaced_answer(tmp_path):
    und = TU.understand_timeline(MERGED, "download invoices", call=lambda t, p, ms: raw_answer())
    qs = {q["id"]: q for q in TU.questions_from_understanding(und, MERGED)}
    decisions.record(tmp_path, qs["chosen_values"], {"selected": ["url:2"]})
    decisions.record(tmp_path, qs["confirm_summary"], {"choice": "yes"})
    stored = {d["qid"]: d for d in decisions.load(tmp_path)}
    assert stored["chosen_values"]["values"] == [
        {"ref": "url:2", "name": "repo_name", "literal": "acme/repo-one", "kind": "url", "event_ids": [2]}]
    assert stored["confirm_summary"]["description"] == "Finds an invoice and downloads the PDF."

    decisions.record(tmp_path, qs["chosen_values"], {"selected": []})            # re-answered
    again = decisions.load(tmp_path)
    assert len(again) == 2 and next(d for d in again if d["qid"] == "chosen_values")["skipped"] is True
