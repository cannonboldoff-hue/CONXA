"""A step's own id is written once and never recomputed (BUILD-104 (1), plan W-A)."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_core.models.step_identity import assign_compile_ids, ensure_step_ids, step_keys  # noqa: E402


def _step(h="", action="click", url="https://x", **kw):
    return {"action": {"action": action}, "url": url, "identity_bundle": {"stable_hash": h}, **kw}


def _doc(steps):
    return {"skills": [{"name": "recorded", "steps": steps}]}


def test_legacy_skill_keeps_every_old_key_byte_for_byte():
    steps = [_step("h1"), _step("", action="scroll"), _step("h1"), _step("h2")]
    old = step_keys(copy.deepcopy(steps))
    doc = _doc(steps)
    assert ensure_step_ids(doc) is True
    assert [s["step_id"] for s in doc["skills"][0]["steps"]] == old
    assert step_keys(doc["skills"][0]["steps"]) == old


def test_changing_the_element_hash_after_freezing_moves_nothing():
    doc = _doc([_step("h1"), _step("h1")])
    ensure_step_ids(doc)
    before = step_keys(doc["skills"][0]["steps"])
    for s in doc["skills"][0]["steps"]:
        s["identity_bundle"]["stable_hash"] = "v2-hash"
    assert step_keys(doc["skills"][0]["steps"]) == before


def test_inserting_a_step_with_the_same_element_keeps_old_keys_and_gets_a_unique_new_one():
    doc = _doc([_step("h1"), _step("h1")])
    ensure_step_ids(doc)
    steps = doc["skills"][0]["steps"]
    old = step_keys(steps)
    steps.insert(0, _step("h1"))  # same element, in front
    ensure_step_ids(doc)
    keys = step_keys(steps)
    assert keys[1:] == old
    assert len(set(keys)) == 3


def test_freezing_is_idempotent():
    doc = _doc([_step("h1"), _step("h2")])
    ensure_step_ids(doc)
    snapshot = copy.deepcopy(doc)
    assert ensure_step_ids(doc) is False
    assert doc == snapshot


def test_bodies_get_ids_and_unique_ones():
    loop = _step("h9", action="for_each", for_each={"steps": [_step("a"), _step("a")]})
    fork = _step("f", action="outcome_fork", outcome_fork={"states": [{"options": [{"steps": [_step("o")]}]}]})
    doc = _doc([loop, fork])
    ensure_step_ids(doc)
    ids = [loop["for_each"]["steps"][0]["step_id"], loop["for_each"]["steps"][1]["step_id"],
           fork["outcome_fork"]["states"][0]["options"][0]["steps"][0]["step_id"]]
    assert len(set(ids)) == 3 and all(ids)


def test_compile_ids_come_from_the_event_number_and_ignore_the_hash():
    steps = [SimpleNamespace(step_id="", source_event=7, action="click", url="u", identity_bundle=None),
             SimpleNamespace(step_id="", source_event=7, action="click", url="u", identity_bundle=None),
             SimpleNamespace(step_id="", source_event=None, action="navigate", url="u", identity_bundle=None)]
    assign_compile_ids(steps)
    assert steps[0].step_id == "rec7" and steps[1].step_id == "rec7.2"
    assert steps[2].step_id and steps[2].step_id not in {"rec7", "rec7.2"}
    assert step_keys(steps) == [s.step_id for s in steps]
