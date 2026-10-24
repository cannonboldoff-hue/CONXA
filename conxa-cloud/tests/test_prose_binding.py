"""bind_prose: one rule that keeps prose in line with inputs, applied wherever a skill is saved."""

from __future__ import annotations

import copy

from conxa_core.models.prose_binding import bind_prose, readable
from conxa_core.storage.json_store import read_skill, write_skill


def _doc():
    nested = {"action": {"action": "click"}, "semantic_description": "Click Angular.gitignore again"}
    return {
        "meta": {"version": 1, "description": "Download Angular.gitignore and upload it"},
        "intent_graph": {"goal": "Get the Angular.gitignore file", "steps": [{"index": 0, "intent": "Open Angular.gitignore"}]},
        "inputs": [{"id": "github_file", "type": "text", "default": "Angular.gitignore"},
                   {"id": "n", "type": "text", "default": "1"}],
        "skills": [{"steps": [
            {"action": {"action": "click"}, "semantic_description": "Trigger the download of the Angular.gitignore file.",
             "target": {"primary_selector": "text=Angular.gitignore"}, "value": "{{github_file}}",
             "url": "https://x.test/Angular.gitignore",
             "validation": {"assertions": [{"type": "text_present", "target": "Angular.gitignore"}]}},
            {"action": {"action": "wait_for_one_of"}, "semantic_description": "",
             "branch": {"options": [{"steps": [nested]}]}},
            {"action": {"action": "click"}, "semantic_description": "Press 1 time"},
        ]}],
    }


def test_every_sentence_naming_the_value_is_rewritten_and_addresses_are_not_touched():
    doc = _doc()
    out = bind_prose(doc)
    steps = out["skills"][0]["steps"]
    assert steps[0]["semantic_description"] == "Trigger the download of the {{github_file}} file."
    assert steps[1]["branch"]["options"][0]["steps"][0]["semantic_description"] == "Click {{github_file}} again"
    assert out["intent_graph"]["goal"] == "Get the {{github_file}} file"
    assert out["intent_graph"]["steps"][0]["intent"] == "Open {{github_file}}"
    assert out["meta"]["description"] == "Download {{github_file}} and upload it"
    for key in ("target", "value", "url", "validation"):
        assert steps[0][key] == doc["skills"][0]["steps"][0][key]


def test_short_defaults_and_unused_inputs_are_left_alone_and_the_pass_is_idempotent():
    doc = _doc()
    out = bind_prose(doc)
    assert out["skills"][0]["steps"][2]["semantic_description"] == "Press 1 time"
    assert bind_prose(out) == out
    unused = copy.deepcopy(doc)
    unused["skills"][0]["steps"][0]["value"] = "x"
    assert bind_prose(unused) is unused


def test_an_input_named_like_its_default_never_nests_tokens():
    doc = {"inputs": [{"id": "invoice", "type": "text", "default": "invoice"}],
           "skills": [{"steps": [{"value": "{{invoice}}", "semantic_description": "Type {{invoice}} for the invoice"}]}]}
    assert bind_prose(doc)["skills"][0]["steps"][0]["semantic_description"] == "Type {{invoice}} for the {{invoice}}"


def test_saving_a_skill_applies_it_for_any_caller(tmp_path, monkeypatch):
    from conxa_core.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    write_skill("s1", _doc())
    assert read_skill("s1")["skills"][0]["steps"][0]["semantic_description"] == "Trigger the download of the {{github_file}} file."


def test_readable_drops_the_article_and_never_doubles_it():
    assert readable("Click on the {{github_file_name}} file link") == "Click on “github file name” file link"
    assert readable("{{order_id}} is sent") == "“order id” is sent"
    assert "the the" not in readable("Open the {{x}} then the {{y}}")
