"""The understanding check: recorded values that aren't typed text, and whether the workflow covers
what the person said (the "9 steps, 0 inputs, nothing to ask" defect)."""

from __future__ import annotations

import pytest

from conxa_compile.conversation import understanding
from conxa_compile.conversation.clarify import BLOCKING_KINDS, generate_questions, plan_answer
from conxa_compile.conversation.lifecycle import derive_lifecycle
from conxa_compile.editor.workflow_mutations import parameterize_literal


def _step(action, intent, **kw):
    s = {"action": {"action": action}, "intent": intent, "semantic_description": "", "validation": {"assertions": []},
         "value": None, "input_binding": None, "identity_bundle": {"stable_hash": intent}}
    s.update(kw)
    return s


def _github_to_drive():
    """Navigate to a repo, click a file, download it, open Drive: nothing is typed anywhere."""
    steps = [
        _step("navigate", "open_repo", url="https://github.com/github/gitignore"),
        _step("click", "open_file", identity_bundle={"stable_hash": "open_file", "fingerprint": {"inner_text": "Android.gitignore"}}),
        _step("click", "download_file", identity_bundle={"stable_hash": "download_file", "fingerprint": {"inner_text": "Download raw file"}}),
        _step("navigate", "open_drive", url="https://drive.google.com/"),
    ]
    return {"meta": {"version": 1}, "skills": [{"steps": steps}], "inputs": []}


# ── candidates ──────────────────────────────────────────────────────────────────────────────


def test_candidates_are_what_the_recording_chose_not_what_it_typed():
    cands = understanding.extract_candidates(_github_to_drive())
    by_kind = {c["kind"]: c for c in cands if c["kind"] != "click"}
    assert by_kind["url"]["literal"] == "github/gitignore"          # the site root (Drive) is not a candidate
    assert [c["literal"] for c in cands if c["kind"] == "url"] == ["github/gitignore"]
    clicks = {c["literal"]: c for c in cands if c["kind"] == "click"}
    assert "Android.gitignore" in clicks and clicks["Android.gitignore"]["supported"] is False
    assert by_kind["url"]["supported"] is True


def test_a_bound_row_is_a_candidate_and_an_already_templated_one_is_not():
    doc = _github_to_drive()
    steps = doc["skills"][0]["steps"]
    steps[1]["entity_binding"] = {"container_selector": "tr", "identifier": "Android.gitignore", "source": "literal"}
    steps[2]["entity_binding"] = {"container_selector": "tr", "identifier": "{{file}}", "source": "input"}
    rows = [c for c in understanding.extract_candidates(doc) if c["kind"] == "row"]
    assert [r["literal"] for r in rows] == ["Android.gitignore"]


def test_the_same_value_on_two_steps_is_one_candidate():
    doc = _github_to_drive()
    steps = doc["skills"][0]["steps"]
    steps.insert(2, _step("navigate", "again", url="https://github.com/github/gitignore"))
    urls = [c for c in understanding.extract_candidates(doc) if c["kind"] == "url"]
    assert len(urls) == 1 and len(urls[0]["step_keys"]) == 2


# ── the model's answer ──────────────────────────────────────────────────────────────────────


def _ask(answer, task="Download a gitignore file from GitHub and upload it to Drive"):
    return understanding.understand(_github_to_drive(), task, call=lambda *_a, **_k: answer)


def test_a_usable_answer_labels_candidates_and_names_inputs():
    res = _ask({"goal_summary": "Copy a file from a GitHub repo to Drive.", "coverage": {"ok": True},
                "values": [{"ref": r["ref"], "verdict": "varies", "name": "Repository" if r["kind"] == "url" else "File Name",
                            "literal": "github/gitignore"} for r in understanding.extract_candidates(_github_to_drive())
                           if r["kind"] != "click"]})
    assert res["source"] == "model" and res["goal_summary"].startswith("Copy a file")
    url = next(c for c in res["candidates"] if c["kind"] == "url")
    assert (url["verdict"], url["name"], url["literal"]) == ("varies", "repository", "github/gitignore")


def test_nothing_the_model_says_is_trusted_blindly():
    cands = understanding.extract_candidates(_github_to_drive())
    url_ref = next(c["ref"] for c in cands if c["kind"] == "url")
    res = _ask({"goal_summary": "x", "values": [
        {"ref": "url:made-up", "verdict": "varies", "name": "x"},                            # unknown ref
        {"ref": url_ref, "verdict": "varies", "name": "1 bad name!!", "literal": "not-in-the-url"},  # bad literal
    ]})
    url = next(c for c in res["candidates"] if c["kind"] == "url")
    assert url["literal"] == "github/gitignore"          # the unrelated literal was dropped
    assert url["name"] == "bad_name"                      # renamed to something the runtime can interpolate


def test_a_new_input_name_never_collides_with_an_existing_one():
    doc = _github_to_drive()
    doc["inputs"] = [{"id": "repository"}]
    cands = understanding.extract_candidates(doc)
    ref = next(c["ref"] for c in cands if c["kind"] == "url")
    res = understanding.understand(doc, "t", call=lambda *_a, **_k: {"values": [{"ref": ref, "verdict": "varies", "name": "repository"}]})
    assert next(c for c in res["candidates"] if c["kind"] == "url")["name"] == "repository_2"


@pytest.mark.parametrize("answer", [None, {}, "nonsense", {"values": "no"}])
def test_an_unusable_answer_with_a_description_still_asks_unticked(answer):
    res = _ask(answer)
    assert res["source"] == "fallback"
    url = next(c for c in res["candidates"] if c["kind"] == "url")
    assert url["verdict"] == "unsure" and url["name"]
    click = next(c for c in res["candidates"] if c["kind"] == "click")
    assert click["verdict"] == "fixed"  # clicks can't be varied yet, so they are never offered


def test_a_failing_model_call_never_raises_and_without_a_description_asks_nothing():
    def boom(*_a, **_k):
        raise RuntimeError("cloud down")

    res = understanding.understand(_github_to_drive(), "", call=boom)
    assert res["source"] == "fallback" and all(c["verdict"] == "fixed" for c in res["candidates"])
    assert understanding.understand({"skills": [{"steps": []}]}, "", call=boom)["source"] == "none"


# ── the questions ───────────────────────────────────────────────────────────────────────────


def _understood(verdict="varies", coverage=None):
    res = _ask({"goal_summary": "g", "coverage": coverage or {"ok": True}, "values": [
        {"ref": c["ref"], "verdict": verdict, "name": "repository"} for c in understanding.extract_candidates(_github_to_drive())]})
    return res


def test_the_github_to_drive_recording_now_asks_which_repository_and_says_what_it_cannot_vary():
    qs = generate_questions(_github_to_drive(), understanding=_understood())
    q = next(q for q in qs if q["kind"] == "chosen_values")
    assert q["multi"] and [c["selected"] for c in q["choices"]] == [True]
    assert q["choices"][0]["detail"] == "Page address: github/gitignore"
    assert "these stay as recorded: “Android.gitignore”" in q["body"]   # honest about what can't change yet
    assert q["kind"] in BLOCKING_KINDS


def test_unsure_is_listed_unticked_and_fixed_is_not_asked():
    qs = generate_questions(_github_to_drive(), understanding=_understood("unsure"))
    assert [c["selected"] for c in next(q for q in qs if q["kind"] == "chosen_values")["choices"]] == [False]
    assert not [q for q in generate_questions(_github_to_drive(), understanding=_understood("fixed")) if q["kind"] == "chosen_values"]


def test_a_coverage_gap_is_asked_first():
    qs = generate_questions(_github_to_drive(), understanding=_understood(coverage={"ok": False, "note": "You said upload, but the recording stops at Drive's home page."}))
    assert qs[0]["kind"] == "coverage" and "stops at Drive" in qs[0]["body"]
    assert not [q for q in generate_questions(_github_to_drive(), understanding=_understood()) if q["kind"] == "coverage"]


def test_answers_become_a_parameterize_op_only_for_the_ticked_values():
    doc = _github_to_drive()
    q = next(q for q in generate_questions(doc, understanding=_understood()) if q["kind"] == "chosen_values")
    ref = q["choices"][0]["id"]
    ops, receipt = plan_answer(q, {"selected": [ref]}, doc)
    assert ops == [{"op": "parameterize", "literal": "github/gitignore", "name": "repository", "default": "github/gitignore"}]
    assert receipt == "Repository"
    ops, receipt = plan_answer(q, {"selected": []}, doc)
    assert ops == [] and receipt.startswith("Nothing changes")
    assert plan_answer({"kind": "coverage"}, {"choice": "incomplete"}, doc)[1].startswith("Something is missing")


# ── the mutation ────────────────────────────────────────────────────────────────────────────


def test_parameterizing_a_page_address_declares_an_input_with_the_recorded_default():
    doc, changed = parameterize_literal(_github_to_drive(), "github/gitignore", "repository", default="github/gitignore")
    assert changed == 1
    assert doc["skills"][0]["steps"][0]["url"] == "https://github.com/{{repository}}"
    assert doc["inputs"] == [{"id": "repository", "type": "text", "default": "github/gitignore", "options": []}]
    assert doc["skills"][0]["steps"][3]["url"] == "https://drive.google.com/"      # nothing else moved
    assert doc["skills"][0]["steps"][1]["identity_bundle"]["fingerprint"]["inner_text"] == "Android.gitignore"  # never the target


def test_parameterizing_a_row_rewrites_the_identifier_the_runtime_re_reads():
    doc = _github_to_drive()
    doc["skills"][0]["steps"][1]["entity_binding"] = {"container_selector": "tr", "identifier": "Android.gitignore", "source": "literal", "confirmed": True}
    out, changed = parameterize_literal(doc, "Android.gitignore", "file_name")
    eb = out["skills"][0]["steps"][1]["entity_binding"]
    assert (eb["identifier"], eb["source"], eb["confirmed"]) == ("{{file_name}}", "input", True)
    assert changed == 1 and [i["id"] for i in out["inputs"]] == ["file_name"]


def test_parameterizing_something_that_is_not_there_or_a_bad_name_is_refused():
    with pytest.raises(ValueError, match="literal_not_found"):
        parameterize_literal(_github_to_drive(), "nowhere", "x")
    with pytest.raises(ValueError, match="invalid_input_name"):
        parameterize_literal(_github_to_drive(), "github/gitignore", "1 bad")
    with pytest.raises(ValueError, match="literal_must_be_nonempty"):
        parameterize_literal(_github_to_drive(), " ", "x")


# ── the gate ────────────────────────────────────────────────────────────────────────────────

_WF = {"session_id": "s", "recording_status": "compiled", "skill_id": "k", "target_url": "https://x", "last_test_status": "never"}


def test_testing_waits_for_a_question_that_decides_what_the_workflow_is():
    blocked = derive_lifecycle(_WF, clarify={"open": 1, "blocking_open": 1})
    assert blocked["stage"] == "clarify" and "test" not in blocked["allowed_actions"]
    advisory = derive_lifecycle(_WF, clarify={"open": 1, "blocking_open": 0})
    assert "test" in advisory["allowed_actions"]
    assert "test" in derive_lifecycle(_WF, clarify={"open": 1})["allowed_actions"]   # older summaries are unchanged
