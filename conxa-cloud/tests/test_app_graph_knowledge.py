"""App maps (P5, Studio side): the author's standing answers for a popup, and the data-free
``app_knowledge.json`` that ships with a skill so a runtime can recognise a popup nobody recorded."""

from __future__ import annotations

import json
import os
import sys

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_compile import skill_package_builder_output  # noqa: E402
from conxa_compile.app_graph import install_hooks  # noqa: E402
from conxa_compile.app_graph.knowledge import FILE_NAME, MAX_POPUPS_PER_HOST, build_app_knowledge, write_app_knowledge  # noqa: E402
from conxa_core.config import settings  # noqa: E402
from conxa_core.storage import app_graph_store  # noqa: E402
from conxa_core.storage.json_store import write_skill  # noqa: E402
from conxa_core.storage.skill_hooks import clear_hooks  # noqa: E402
from conxa_core.workspace import LOCAL_WORKSPACE_ID  # noqa: E402
from handlers.app_graph import AppGraphMixin  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402

DRIVE = "https://drive.example.com"
HOST = "drive.example.com"


def step(action, *, name="", stable="h", url=f"{DRIVE}/drive/my-drive"):
    return {
        "action": {"action": action},
        "intent": f"{action}_{stable}",
        "state": {"before": {"url": url, "page_title": "My Drive"}, "after": {"url": url, "page_title": "My Drive"}},
        "identity_bundle": {"stable_hash": stable, "fingerprint": {"role": "button", "aria_label": name}},
    }


def fork(*, title="Upload options", labels=("Replace", "Keep both", "Cancel"), recorded="Replace"):
    return {
        "action": {"action": "outcome_fork"},
        "outcome_fork": {
            "clean": {"observed": False, "observed_in": []},
            "states": [{
                "id": "d",
                "signature": {"title": title, "button_labels": list(labels), "message_tokens": ["already", "exists"],
                              "container_selector": "[role=dialog]"},
                "options": [{"id": l.lower().replace(" ", "_"), "label": l,
                             "role": "consequential" if l == "Replace" else ("dismiss" if l == "Cancel" else "neutral"),
                             "recorded": l == recorded, "steps": []} for l in labels],
                "observed_in": ["s1"],
            }],
        },
    }


def doc(*steps):
    return {"meta": {"id": "x", "title": "wf"}, "skills": [{"steps": list(steps)}], "inputs": []}


@pytest.fixture()
def hooked(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clear_hooks()
    install_hooks()
    write_skill("skill_a", doc(step("click", name="Upload", stable="up"), fork()))
    yield tmp_path
    clear_hooks()


def popup_id():
    return app_graph_store.get_graph(LOCAL_WORKSPACE_ID, HOST).popups[0].id


def set_policy(policy, **extra):
    return AppGraphMixin().cmd_set_app_popup_policy(
        {"app_key": HOST, "popup_id": popup_id(), "policy": policy, **extra}, "r")


# ─── the author's policy ────────────────────────────────────────────────────────────────────

def test_a_harmless_choice_becomes_an_approved_policy(hooked):
    out = set_policy({"mode": "auto", "option": "Keep both"})
    assert out["app_policy"] == {"mode": "auto", "option": "Keep both", "author_approved": True}
    assert app_graph_store.get_graph(LOCAL_WORKSPACE_ID, HOST).popups[0].app_policy == out["app_policy"]


def test_a_risky_choice_needs_an_explicit_confirmation(hooked):
    with pytest.raises(_CommandError) as e:
        set_policy({"mode": "auto", "option": "Replace"})
    assert e.value.code == "confirmation_required"
    assert app_graph_store.get_graph(LOCAL_WORKSPACE_ID, HOST).popups[0].app_policy is None
    assert set_policy({"mode": "auto", "option": "Replace"}, confirm_risky=True)["app_policy"]["author_approved"] is True
    with pytest.raises(_CommandError):
        set_policy({"mode": "auto", "option": "Replace"}, confirm_risky="yes")      # only a real True counts


def test_bad_requests_are_refused(hooked):
    with pytest.raises(_CommandError) as e:
        set_policy({"mode": "auto", "option": "Nuke it"})
    assert e.value.code == "option_not_found"
    with pytest.raises(_CommandError) as e:
        set_policy({"mode": "whatever"})
    assert e.value.code == "invalid_policy"
    with pytest.raises(_CommandError) as e:
        AppGraphMixin().cmd_set_app_popup_policy({"app_key": HOST, "popup_id": "nope", "policy": {"mode": "ask_human"}}, "r")
    assert e.value.code == "popup_not_found"


def test_ask_stop_and_clear(hooked):
    assert set_policy({"mode": "ask_human"})["app_policy"] == {"mode": "ask_human"}
    assert set_policy({"mode": "stop"})["app_policy"]["mode"] == "stop"
    assert set_policy({"mode": "clear"})["app_policy"] is None
    assert app_graph_store.get_graph(LOCAL_WORKSPACE_ID, HOST).popups[0].app_policy is None


def test_a_policy_survives_every_re_fold(hooked):
    set_policy({"mode": "auto", "option": "Keep both"})
    write_skill("skill_b", doc(step("click", name="Upload", stable="up"), fork()))     # another workflow joins
    write_skill("skill_a", doc(step("click", name="Upload", stable="up"), fork()))     # and one is re-compiled
    assert app_graph_store.get_graph(LOCAL_WORKSPACE_ID, HOST).popups[0].app_policy["option"] == "Keep both"


# ─── app_knowledge.json ─────────────────────────────────────────────────────────────────────

def test_the_knowledge_holds_only_popups_of_the_hosts_asked_for(hooked):
    set_policy({"mode": "auto", "option": "Keep both"})
    k = build_app_knowledge(LOCAL_WORKSPACE_ID, ["https://www.drive.example.com/start", "other.example.com"])
    assert k["schema_version"] == 1 and list(k["apps"]) == [HOST]
    (p,) = k["apps"][HOST]["popups"]
    assert p["signature"] == {"title": "Upload options", "button_labels": ["Replace", "Keep both", "Cancel"],
                              "message_tokens": ["already", "exists"], "container_selector": "[role=dialog]"}
    assert {o["label"]: o["role"] for o in p["options"]} == {"Replace": "consequential", "Keep both": "neutral", "Cancel": "dismiss"}
    assert p["app_policy"]["author_approved"] is True


def test_the_knowledge_leaks_no_screens_actions_or_workflow_names(hooked):
    blob = json.dumps(build_app_knowledge(LOCAL_WORKSPACE_ID, [HOST]))
    for forbidden in ("my-drive", "action_ids", "\"paths\"", "wf", "skill_a", "sources", "upload_up"):
        assert forbidden not in blob, forbidden


def test_no_popups_means_no_knowledge(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clear_hooks()
    install_hooks()
    write_skill("skill_a", doc(step("click", name="Upload", stable="up")))
    assert build_app_knowledge(LOCAL_WORKSPACE_ID, [HOST]) is None
    clear_hooks()


def test_write_app_knowledge_writes_and_removes_a_stale_file(hooked, tmp_path):
    skill_dir = tmp_path / "skills" / "wf"
    skill_dir.mkdir(parents=True)
    assert write_app_knowledge(skill_dir, LOCAL_WORKSPACE_ID, [HOST]) is True
    assert json.loads((skill_dir / FILE_NAME).read_text())["apps"][HOST]
    assert write_app_knowledge(skill_dir, LOCAL_WORKSPACE_ID, ["nothing.example.com"]) is False
    assert not (skill_dir / FILE_NAME).exists()


def test_the_file_is_capped_per_host(hooked):
    graph = app_graph_store.get_graph(LOCAL_WORKSPACE_ID, HOST)
    many = [graph.popups[0].model_copy(update={"id": f"p{i}", "appeared": i + 1}) for i in range(MAX_POPUPS_PER_HOST + 10)]
    k = build_app_knowledge(LOCAL_WORKSPACE_ID, [HOST], graph_for=lambda _h: graph.model_copy(update={"popups": many}))
    ids = [p["id"] for p in k["apps"][HOST]["popups"]]
    assert len(ids) == MAX_POPUPS_PER_HOST and ids[0] == f"p{MAX_POPUPS_PER_HOST + 9}"   # most-seen first


def test_the_pack_writer_ships_the_file_and_drops_a_stale_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bundle = tmp_path / "bundle"
    skill = bundle / "skills" / "wf"
    skill.mkdir(parents=True)
    (skill / "execution.json").write_text(json.dumps([{"type": "click", "selector": "#a"}]), encoding="utf-8")
    (skill / "recovery.json").write_text("{}", encoding="utf-8")
    (skill / "input.json").write_text(json.dumps({"inputs": []}), encoding="utf-8")
    (skill / FILE_NAME).write_text('{"schema_version": 1, "apps": {}}', encoding="utf-8")
    kwargs = dict(bundle_root=bundle, workspace_id="wrk_acme", display_name="Acme", target_url="https://acme.test",
                  protected_url="", skill_slugs=["wf"], skill_target_urls={"wf": "https://acme.test"}, version="1.0.0")
    skill_package_builder_output._write_skill_packs_format(**kwargs)
    shipped = tmp_path / "skill-packs" / "wrk_acme" / "_default" / "wf" / FILE_NAME
    assert shipped.is_file()
    (skill / FILE_NAME).unlink()                       # the next build has no popup knowledge
    skill_package_builder_output._write_skill_packs_format(**kwargs)
    assert not shipped.exists()


def test_the_cloud_syncs_the_file_with_the_skill_code_not_as_a_recovery_artifact(tmp_path):
    from app.api.skillpack_update_routes import _CODE_FILES, _artifact_entries

    skill = tmp_path / "wf"
    (skill / "visuals").mkdir(parents=True)
    (skill / "execution.json").write_text("[]", encoding="utf-8")
    (skill / FILE_NAME).write_text('{"schema_version": 1, "apps": {}}', encoding="utf-8")
    (skill / "visuals" / "a.png").write_bytes(b"png")
    assert FILE_NAME in _CODE_FILES
    assert [a["path"] for a in _artifact_entries(skill)] == ["visuals/a.png"]
