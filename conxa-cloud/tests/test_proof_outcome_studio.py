"""Build Studio reads how the runtime says a run ended (Proof of Done): structured outcome first, the
result's first words as the fallback for an older engine."""

from handlers.workflows import notes_from_message, runtime_outcome


def test_structured_outcome_wins_over_the_text():
    result = {"structuredContent": {"outcome": "done_please_check", "steps": [
        {"index": 2, "intent": "Upload the report", "verdict": "couldnt_confirm", "rung": "accepted", "checks": [{"x": 1}]},
        "junk",
    ]}}
    outcome, steps = runtime_outcome(result, "Done. URL: x")
    assert outcome == "done_please_check"
    assert steps == [{"index": 2, "intent": "Upload the report", "verdict": "couldnt_confirm", "rung": "accepted"}]


def test_an_older_engine_is_read_from_the_first_words():
    for text, expected in [
        ("Done. URL: https://x.test/", "done"),
        ("Done, please check. URL: x\n\nPlease check:\n  - note", "done_please_check"),
        ("Submitted. The upload goes on.", "submitted"),
        ("Partly done. 2 of 3 files.", "partly_done"),
        ("Couldn't confirm. I couldn't confirm that \"Send\" finished.", "couldnt_confirm"),
    ]:
        assert runtime_outcome({}, text)[0] == expected, text


def test_any_other_message_is_an_ordinary_failure():
    for text in ["Execution cancelled.", "Step 3 (click) failed: nothing found", "", "Dry run complete. URL: x"]:
        assert runtime_outcome({}, text)[0] is None, text
    # an unknown structured outcome is ignored, not trusted
    assert runtime_outcome({"structuredContent": {"outcome": "bogus"}}, "Done. x")[0] == "done"


def test_notes_are_read_from_the_please_check_block():
    text = (
        "Done, please check. URL: https://x.test/\n\nPlease check:\n"
        "  - I couldn't confirm that \"Rename the file\" finished.\n"
        "  - I couldn't confirm that step 7 finished.\n\nWarning:\n  other\n(run_id: r1)"
    )
    assert notes_from_message(text) == [
        "I couldn't confirm that \"Rename the file\" finished.",
        "I couldn't confirm that step 7 finished.",
    ]
    assert notes_from_message("Done. URL: x\n(run_id: r)") == []
