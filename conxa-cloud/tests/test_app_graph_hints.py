"""App maps (P4): a freshly compiled workflow is told about popups that other workflows met after the
same action, and can add a fork for one (asks a person by default)."""

from __future__ import annotations

import copy
import os
import sys

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_compile.app_graph import install_hooks  # noqa: E402
from conxa_compile.app_graph.hints import known_popups  # noqa: E402
from conxa_compile.conversation import clarify  # noqa: E402
from conxa_compile.editor.workflow_mutations import add_known_popup_fork  # noqa: E402
from conxa_core.config import settings  # noqa: E402
from conxa_core.storage import app_graph_store  # noqa: E402
from conxa_core.storage.json_store import write_skill  # noqa: E402
from conxa_core.storage.skill_hooks import clear_hooks  # noqa: E402
from conxa_core.workspace import LOCAL_WORKSPACE_ID  # noqa: E402

DRIVE = "https://drive.example.com"


def step(action, *, name="", stable="h", intent=""):
    return {
        "action": {"action": action},
        "intent": intent or f"{action}_{stable}",
        "semantic_description": f"{action} {name}".strip(),
        "state": {
            "before": {"url": f"{DRIVE}/drive/my-drive", "page_title": "My Drive"},
            "after": {"url": f"{DRIVE}/drive/my-drive", "page_title": "My Drive"},
        },
        "identity_bundle": {"stable_hash": stable, "fingerprint": {"role": "button", "aria_label": name}},
        "consequence": "reversible",
    }


def fork(*, title="Upload options"):
    return {
        "action": {"action": "outcome_fork"},
        "intent": "handle_popup_decision",
        "outcome_fork": {
            "clean": {"observed": False, "observed_in": []},
            "states": [{
                "id": "dialog_upload_options",
                "signature": {"title": title, "button_labels": ["Replace", "Keep both", "Cancel"],
                              "message_tokens": ["already", "exists"], "container_selector": "[role=dialog]"},
                "options": [
                    {"id": "replace", "label": "Replace", "role": "consequential", "recorded": True, "steps": []},
                    {"id": "keep_both", "label": "Keep both", "role": "neutral", "recorded": False, "steps": []},
                    {"id": "cancel", "label": "Cancel", "role": "dismiss", "recorded": False, "steps": []},
                ],
                "observed_in": ["s1"],
            }],
        },
    }


def doc(*steps, title="wf"):
    return {"meta": {"id": "x", "title": title, "version": 1}, "skills": [{"steps": list(steps)}], "inputs": []}


def core_steps():
    return [step("click", name="Upload", stable="upload_btn"), step("upload", name="File input", stable="file_in")]


@pytest.fixture()
def hooked(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clear_hooks()
    install_hooks()
    yield
    clear_hooks()


def hints_for(d, skill_id):
    return known_popups(d, skill_id=skill_id, workspace_id=LOCAL_WORKSPACE_ID)


def test_a_workflow_is_told_about_a_popup_other_workflows_met(hooked):
    write_skill("skill_a", doc(*core_steps(), fork()))
    b = doc(*core_steps())
    write_skill("skill_b", b)
    (hit,) = hints_for(b, "skill_b")
    assert hit["step_index"] == 1                       # right after the upload step
    assert hit["popup"]["title"] == "Upload options"
    assert [o["label"] for o in hit["popup"]["options"]] == ["Replace", "Keep both", "Cancel"]


def test_no_hint_for_the_workflow_that_met_it_itself(hooked):
    a = doc(*core_steps(), fork())
    write_skill("skill_a", a)
    assert hints_for(a, "skill_a") == []


def test_no_hint_when_this_workflow_already_has_a_fork_there(hooked):
    write_skill("skill_a", doc(*core_steps(), fork()))
    b = doc(*core_steps(), fork())
    write_skill("skill_b", b)
    assert hints_for(b, "skill_b") == []


def test_no_hint_on_an_unrelated_step(hooked):
    write_skill("skill_a", doc(*core_steps(), fork()))
    other = doc(step("click", name="Share", stable="share_btn"))
    write_skill("skill_c", other)
    assert hints_for(other, "skill_c") == []


def test_no_hint_when_no_map_exists(hooked):
    assert hints_for(doc(*core_steps()), "skill_z") == []


def test_clarify_asks_and_both_answers_map_to_the_right_ops(hooked):
    write_skill("skill_a", doc(*core_steps(), fork()))
    b = doc(*core_steps())
    write_skill("skill_b", b)
    hints = hints_for(b, "skill_b")
    qs = clarify.generate_questions(b, known_popups=hints)
    (q,) = [q for q in qs if q["kind"] == "known_popup"]
    assert "Upload options" in q["title"] and "Replace" in q["body"]
    assert [c["id"] for c in q["choices"]] == ["add", "skip"]

    ops, receipt = clarify.plan_answer(q, {"choice": "add"}, b)
    assert ops == [{"op": "add_known_popup_fork", "step_key": q["step_keys"][0], "popup": q["popup"]}]
    assert "Upload options" in receipt
    assert clarify.plan_answer(q, {"choice": "skip"}, b)[0] == []


def test_without_hints_nothing_changes(hooked):
    b = doc(*core_steps())
    assert not [q for q in clarify.generate_questions(b) if q["kind"] == "known_popup"]


def test_at_most_two_known_popup_questions():
    d = doc(*core_steps())
    popup = {"id": "p", "title": "T", "button_labels": ["A", "B"], "options": [{"label": "A"}, {"label": "B"}]}
    qs = clarify.generate_questions(d, known_popups=[{"step_index": 0, "popup": {**popup, "id": f"p{i}"}} for i in range(5)])
    assert len([q for q in qs if q["kind"] == "known_popup"]) == clarify.MAX_KNOWN_POPUP_QUESTIONS


# ─── the mutation ───────────────────────────────────────────────────────────────────────────

def popup_from_map(hooked_fixture=None):
    write_skill("skill_a", doc(*core_steps(), fork()))
    return app_graph_store.get_graph(LOCAL_WORKSPACE_ID, "drive.example.com").popups[0].model_dump(mode="json")


def test_add_known_popup_fork_inserts_an_ask_a_person_fork(hooked):
    popup = popup_from_map()
    b = doc(*core_steps(), step("click", name="Done", stable="done_btn"))
    out = add_known_popup_fork(copy.deepcopy(b), 1, popup)
    steps = out["skills"][0]["steps"]
    assert len(steps) == 4 and steps[2]["action"] == "outcome_fork"
    state = steps[2]["outcome_fork"]["states"][0]
    assert steps[2]["outcome_fork"]["clean"]["observed"] is True
    assert state["from_map"] is True and state["observed_in"] == []
    assert state["policy"]["mode"] == "ask_human" and "author_approved" not in state["policy"]
    assert state["policy"]["suggested"] == "replace"        # the one recording that met it chose Replace
    assert [o["label"] for o in state["options"]] == ["Replace", "Keep both", "Cancel"]
    assert all(o["recorded"] is False and len(o["steps"]) == 1 for o in state["options"])
    replace = state["options"][0]
    assert replace["role"] == "consequential" and replace["steps"][0]["identity_bundle"]["destructive"] is True
    assert out["meta"]["version"] == 2


def test_the_new_options_are_scoped_to_the_dialog(hooked):
    popup = popup_from_map()
    out = add_known_popup_fork(doc(*core_steps()), 1, popup)
    sels = [s["selector"] for s in out["skills"][0]["steps"][2]["outcome_fork"]["states"][0]["options"][1]["steps"][0]["identity_bundle"]["signals"]]
    assert sels and all(s.startswith("[role=dialog] >> ") for s in sels)


def test_add_known_popup_fork_refuses_a_second_one_or_a_bad_popup(hooked):
    popup = popup_from_map()
    once = add_known_popup_fork(doc(*core_steps()), 1, popup)
    with pytest.raises(ValueError, match="fork_already_here"):
        add_known_popup_fork(once, 1, popup)
    with pytest.raises(ValueError, match="popup_not_addable"):
        add_known_popup_fork(doc(*core_steps()), 1, {**popup, "options": [{"label": "Only"}]})
    with pytest.raises(ValueError, match="step_index_out_of_range"):
        add_known_popup_fork(doc(*core_steps()), 9, popup)


def test_a_workflow_that_never_saw_the_popup_does_not_count_as_an_appearance(hooked):
    popup = popup_from_map()
    b = add_known_popup_fork(doc(*core_steps()), 1, popup)
    write_skill("skill_b", b)
    graph = app_graph_store.get_graph(LOCAL_WORKSPACE_ID, "drive.example.com")
    (p,) = graph.popups
    assert p.appeared == 1 and p.absent == 1                 # skill_a saw it; skill_b ran without it
    assert {o.label: o.chosen for o in p.options}["Replace"] == 1


def test_a_fork_added_from_the_map_exports_to_a_runnable_execution_step(hooked, tmp_path):
    import json

    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill
    from conxa_core.models.skill_spec import IdentityBundle, IdentitySignal, SkillStep

    popup = popup_from_map()
    trigger = SkillStep(
        action="click", intent="click_upload", target={"primary_selector": "#up"},
        identity_bundle=IdentityBundle(
            signals=[IdentitySignal(engine="role", selector='internal:role=button[name="Upload"i]', durability=0.9,
                                    orthogonality_class="semantic")],
            stable_hash="upload_btn",
        ),
    ).model_dump(mode="json")
    b = add_known_popup_fork(doc(trigger), 0, popup)
    warnings: list[str] = []
    _build_workflow_from_saved_skill(bundle_root=tmp_path, workflow_slug="wf", saved_skill=b, on_warning=warnings.append)
    execution = json.loads((tmp_path / "skills" / "wf" / "execution.json").read_text())
    exported = next(s for s in execution if s["type"] == "outcome_fork")
    state = exported["states"][0]
    assert state["policy"]["mode"] == "ask_human"
    assert [o["id"] for o in state["options"]] == ["replace", "keep_both", "cancel"]
    assert all(o["steps"] and o["steps"][0]["type"] == "click" for o in state["options"])
    assert not [w for w in warnings if "fork" in w.lower()]


# ─── live hint while recording ──────────────────────────────────────────────────────────────

def _event(label="Upload", url=f"{DRIVE}/drive/my-drive"):
    return {"page": {"url": url}, "target": {"aria_label": label, "inner_text": label}}


def _upload_graph():
    write_skill("skill_a", doc(step("click", name="Upload", stable="upload_btn"), step("upload", name="File input", stable="file_in"), fork()))
    return app_graph_store.get_graph(LOCAL_WORKSPACE_ID, "drive.example.com")


def test_recording_hint_fires_for_the_action_a_popup_followed(hooked):
    from conxa_compile.app_graph.hints import RecordingHinter

    graph = _upload_graph()
    # The popup followed the second action ("File input"); recording that control triggers the hint.
    hint = RecordingHinter(LOCAL_WORKSPACE_ID, graph_for=lambda _h: graph)(_event("File input"))
    assert hint and "Upload options" in hint and "drive.example.com" in hint and "Replace" in hint


def test_recording_hint_is_said_once_and_only_for_a_known_control(hooked):
    from conxa_compile.app_graph.hints import RecordingHinter

    graph = _upload_graph()
    h = RecordingHinter(LOCAL_WORKSPACE_ID, graph_for=lambda _h: graph)
    assert h(_event("Upload")) is None                        # a control no popup followed
    assert h(_event("File input")) is not None
    assert h(_event("File input")) is None                    # already said this recording
    assert h(_event("File input", url=f"{DRIVE}/other/page")) is None   # a different screen


def test_recording_hint_is_silent_without_a_map_or_a_label(hooked):
    from conxa_compile.app_graph.hints import RecordingHinter

    assert RecordingHinter(LOCAL_WORKSPACE_ID, graph_for=lambda _h: None)(_event("File input")) is None
    graph = _upload_graph()
    h = RecordingHinter(LOCAL_WORKSPACE_ID, graph_for=lambda _h: graph)
    assert h({"page": {"url": "about:blank"}, "target": {}}) is None
    assert h({"page": {"url": f"{DRIVE}/drive/my-drive"}, "target": {}}) is None


def test_every_exported_step_names_the_document_step_it_came_from(hooked, tmp_path):
    import json

    from conxa_compile.compiler.step_key import step_keys
    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill
    from conxa_core.models.skill_spec import SkillStep

    steps = [SkillStep(action="click", intent=f"click_{n}", target={"primary_selector": f"#b{n}"}).model_dump(mode="json")
             for n in range(2)]
    saved = doc(*steps)
    _build_workflow_from_saved_skill(bundle_root=tmp_path, workflow_slug="wf", saved_skill=saved)
    execution = json.loads((tmp_path / "skills" / "wf" / "execution.json").read_text())
    expected = step_keys(saved["skills"][0]["steps"])
    assert [s["source_step_key"] for s in execution] == expected[: len(execution)] and len(set(expected)) == len(expected)
