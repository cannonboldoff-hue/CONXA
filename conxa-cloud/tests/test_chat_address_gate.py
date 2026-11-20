"""WF-12 12.5: chat never re-points a step, page address included. One allow-list (`chat_may_write`) decides
what a chat change may write, when its card is made and again when it is applied; an address under any
spelling or nesting is refused, and the apply says so in plain words. Code may still turn part of a page
address into a run input (`parameterize`). Also WF-12 12.3: "make the typed text an input" gets a card on
every wording, and cards name fields in plain words."""

from __future__ import annotations

import copy
import importlib.util
import os
import sys

import pytest

from conxa_compile.compiler.step_key import step_keys
from conxa_compile.editor.capability_manifest import ADDRESS_REFUSAL, chat_may_write, editable_fields
from conxa_compile.editor.copilot_proposals import gate_proposals, gate_structural_proposals, proposal_summary

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

HOME = "https://a.test/home"
ELSEWHERE = "https://evil.test/"


def _navigate(url=HOME):
    return {"action": {"action": "navigate", "url": url}, "url": url, "intent": "navigate", "value": "",
            "signals": {"context": {"page_url": url}}}


def _click():
    return {"action": {"action": "click"}, "url": HOME, "intent": "click_submit", "value": "",
            "identity_bundle": {"signals": [{"engine": "testid", "selector": "#s", "durability": 0.9}]},
            "target": {"primary_selector": "#s"}}


def _type(text="quarterly report"):
    return {"action": {"action": "type"}, "url": HOME, "intent": "enter_note", "value": text,
            "identity_bundle": {"signals": [{"engine": "testid", "selector": "#note", "durability": 0.9}]},
            "target": {"primary_selector": "#note"}}


def _doc(steps):
    return {"meta": {"id": "s", "title": "t", "version": 1}, "skills": [{"id": "s", "steps": steps}]}


ADDRESS_PATCHES = [
    {"url": ELSEWHERE},
    {"URL": ELSEWHERE},
    {" url": ELSEWHERE},
    {"Url": ELSEWHERE},
    {"action.url": ELSEWHERE},
    {"action": {"url": ELSEWHERE}},
    {"action": {"action": "navigate", "url": ELSEWHERE}},
    {"signals": {"context": {"page_url": ELSEWHERE}}},
    {"signals.context.page_url": ELSEWHERE},
    {"page_url": ELSEWHERE},
    {"start_url": ELSEWHERE},
    {"href": ELSEWHERE},
    {"target": {"primary_selector": "#x"}},
    {"target.primary_selector": "#x"},
    {"identity_bundle": {"signals": []}},
    {"compiled_selectors": ["#x"]},
    {"selector": "#x"},
    {"frame": {"chain": []}},
    {"recovery": {"intent": "x"}},
    {"intent": "ok", "url": ELSEWHERE},                       # a meaning field cannot carry an address along
    {"validation": {"assertions": [], "url": ELSEWHERE}},     # nor can a nested meaning object
    {},
    "url",
    None,
]


@pytest.mark.parametrize("kind", ["navigate", "click", "type", "if_present", "for_each"])
@pytest.mark.parametrize("patch", ADDRESS_PATCHES, ids=repr)
def test_no_spelling_or_nesting_of_an_address_passes_the_chat_gate(kind, patch):
    assert not chat_may_write(kind, patch)


def test_meaning_fields_still_pass_and_url_is_not_editable():
    assert "url" not in editable_fields("navigate")
    assert chat_may_write("navigate", {"intent": "open_home"})
    assert chat_may_write("navigate", {"semantic_description": "Open the home page"})
    assert chat_may_write("click", {"validation": {"assertions": []}})
    assert chat_may_write("if_present", {"branch": {"timeout_ms": 3000}})
    assert not chat_may_write("if_present", {"branch": {"steps": []}})
    assert chat_may_write("for_each", {"for_each": {"max_iterations": 5}})


@pytest.mark.parametrize("field,value", [
    ("url", ELSEWHERE), ("URL", ELSEWHERE), ("action.url", ELSEWHERE), ("action", {"url": ELSEWHERE}),
    ("signals.context.page_url", ELSEWHERE), ("signals", {"context": {"page_url": ELSEWHERE}}),
    ("page_url", ELSEWHERE), ("start_url", ELSEWHERE),
])
def test_no_card_offers_a_page_address(field, value):
    doc = _doc([_navigate(), _click()])
    key = step_keys(doc["skills"][0]["steps"])[0]
    assert gate_proposals(doc, [{"step_key": key, "field": field, "patch": value, "evidence_refs": [key]}]) == []
    ok = gate_proposals(doc, [{"step_key": key, "field": "semantic_description", "patch": "Open home", "evidence_refs": [key]}])
    assert len(ok) == 1


@pytest.mark.parametrize("fields", [{"url": ELSEWHERE}, {"start_url": ELSEWHERE}, {"action.url": ELSEWHERE},
                                    {"target": {"primary_selector": "#x"}}, {"intent": "go", "page_url": ELSEWHERE}])
def test_a_new_step_that_asks_for_an_address_gets_no_card(fields):
    doc = _doc([_navigate(), _click()])
    keys = step_keys(doc["skills"][0]["steps"])
    raw = {"op": "insert_step", "action_kind": "navigate", "after_step_key": keys[1], "fields": fields, "evidence_refs": [keys[1]]}
    assert gate_structural_proposals(doc, [raw]) == []


# ── apply time ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def backend(monkeypatch, tmp_path):
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    spec = importlib.util.spec_from_file_location("cbackend_chat_address", os.path.join(_PY_DIR, "backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out: list[dict] = []
    from handlers import protocol as _protocol

    mod._write = lambda obj: out.append(obj)
    _protocol._write = lambda obj: out.append(obj)
    return mod.Backend(), out


@pytest.mark.parametrize("patch", [
    {"url": ELSEWHERE}, {"action": {"action": "navigate", "url": ELSEWHERE}},
    {"signals": {"context": {"page_url": ELSEWHERE}}}, {"intent": "x", "url": ELSEWHERE},
])
def test_applying_a_page_address_change_is_refused_in_plain_words(backend, patch):
    from conxa_core.storage.json_store import read_skill, write_skill

    b, out = backend
    write_skill("skill_addr", _doc([_navigate(), _click()]))
    before = read_skill("skill_addr")
    key = step_keys(before["skills"][0]["steps"])[0]
    b.dispatch({"id": "1", "type": "accept_copilot_proposal",
                "payload": {"skill_id": "skill_addr", "step_key": key, "patch": patch, "proposal_id": "p"}})
    assert out[-1]["type"] == "error" and out[-1]["code"] == "chat_cannot_repoint"
    assert out[-1]["message"] == ADDRESS_REFUSAL and "Human Edit" in ADDRESS_REFUSAL
    assert read_skill("skill_addr")["skills"] == before["skills"]


def test_inserting_a_step_with_an_address_is_refused_at_apply(backend):
    from conxa_core.storage.json_store import read_skill, write_skill

    b, out = backend
    write_skill("skill_addr2", _doc([_navigate(), _click()]))
    before = read_skill("skill_addr2")
    keys = step_keys(before["skills"][0]["steps"])
    b.dispatch({"id": "1", "type": "accept_copilot_proposal", "payload": {
        "skill_id": "skill_addr2", "command": "structural_op", "proposal_id": "p",
        "ops": [{"op": "insert_step", "action_kind": "navigate", "after_step_key": keys[1], "fields": {"url": ELSEWHERE}}]}})
    assert out[-1]["type"] == "error" and out[-1]["code"] == "chat_cannot_repoint"
    assert read_skill("skill_addr2")["skills"] == before["skills"]


def test_a_meaning_edit_on_a_navigate_step_still_applies(backend):
    from conxa_core.storage.json_store import read_skill, write_skill

    b, out = backend
    write_skill("skill_addr3", _doc([_navigate(), _click()]))
    key = step_keys(read_skill("skill_addr3")["skills"][0]["steps"])[0]
    b.dispatch({"id": "1", "type": "accept_copilot_proposal",
                "payload": {"skill_id": "skill_addr3", "step_key": key, "patch": {"semantic_description": "Open home"}, "proposal_id": "p"}})
    assert out[-1]["type"] == "result", out[-1]
    step = read_skill("skill_addr3")["skills"][0]["steps"][0]
    assert step["semantic_description"] == "Open home" and step["url"] == HOME and step["action"]["url"] == HOME


def test_the_after_apply_guard_puts_a_page_address_back_but_keeps_an_input():
    from handlers.copilot import _keep_address_keys

    before = _doc([_navigate(), _click()])
    moved = copy.deepcopy(before)
    s = moved["skills"][0]["steps"][0]
    s["url"], s["action"]["url"], s["signals"]["context"]["page_url"] = ELSEWHERE, ELSEWHERE, ELSEWHERE
    guarded = _keep_address_keys(before, moved)
    assert guarded is not None
    g = guarded["skills"][0]["steps"][0]
    assert (g["url"], g["action"]["url"], g["signals"]["context"]["page_url"]) == (HOME, HOME, HOME)

    templated = copy.deepcopy(before)
    templated["skills"][0]["steps"][0]["url"] = "https://a.test/{{page}}"
    kept = _keep_address_keys(before, templated) or templated
    assert kept["skills"][0]["steps"][0]["url"] == "https://a.test/{{page}}"
    # A "template" that does not reproduce the recorded address is a different page, not an input.
    other = copy.deepcopy(before)
    other["skills"][0]["steps"][0]["url"] = "https://evil.test/{{page}}"
    assert _keep_address_keys(before, other)["skills"][0]["steps"][0]["url"] == HOME


# ── 12.3 typed text and plain card words ────────────────────────────────────────────────────


def test_parameterize_on_typed_text_becomes_a_value_card():
    from conxa_compile.conversation.harness import _proposal_title

    doc = _doc([_navigate(), _click(), _type("quarterly report")])
    keys = step_keys(doc["skills"][0]["steps"])
    out = gate_structural_proposals(doc, [{"op": "parameterize", "literal": "quarterly report", "name": "note_text",
                                           "why": "asked", "evidence_refs": [keys[2]]}])
    assert len(out) == 1
    p = out[0]
    assert p["command"] == "patch_step" and p["step_key"] == keys[2] and p["patch"] == {"value": "{{note_text}}"}
    assert "run input" in proposal_summary(p) and "“value”" not in proposal_summary(p)
    assert _proposal_title(p) == "Change the text or value it uses"


def test_card_titles_use_plain_words():
    from conxa_compile.conversation.harness import _proposal_title

    assert _proposal_title({"command": "patch_step", "field": "semantic_description"}) == "Change the step's description"
    assert _proposal_title({"command": "patch_step", "field": "wait_until_kind"}) == "Change the wait until kind"
    doc = _doc([_navigate(), _click()])
    key = step_keys(doc["skills"][0]["steps"])[1]
    p = gate_proposals(doc, [{"step_key": key, "field": "semantic_description", "patch": "Press submit", "evidence_refs": [key]}])[0]
    assert proposal_summary(p).startswith("Change the step's description on ")
