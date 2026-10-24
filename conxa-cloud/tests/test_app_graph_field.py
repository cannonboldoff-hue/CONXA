"""App maps (P6, Studio side): what real runs saw (the cloud's ``/field`` rows) folded into the map."""

from __future__ import annotations

import os
import sys

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_compile.app_graph import install_hooks  # noqa: E402
from conxa_compile.app_graph.field import ingest_field  # noqa: E402
from conxa_compile.app_graph.knowledge import build_app_knowledge  # noqa: E402
from conxa_core.config import settings  # noqa: E402
from conxa_core.storage import app_graph_store  # noqa: E402
from conxa_core.storage.json_store import write_skill  # noqa: E402
from conxa_core.storage.skill_hooks import clear_hooks  # noqa: E402
from conxa_core.workspace import LOCAL_WORKSPACE_ID  # noqa: E402
from handlers.app_graph import AppGraphMixin  # noqa: E402
from handlers.protocol import _CommandError  # noqa: E402
from services.app_graph_sync import pull_field  # noqa: E402

HOST = "drive.example.com"
WS = LOCAL_WORKSPACE_ID


def step(action, *, name="", stable="h"):
    url = f"https://{HOST}/drive/my-drive"
    return {
        "action": {"action": action}, "intent": f"{action}_{stable}",
        "state": {"before": {"url": url, "page_title": "My Drive"}, "after": {"url": url, "page_title": "My Drive"}},
        "identity_bundle": {"stable_hash": stable, "fingerprint": {"role": "button", "aria_label": name}},
    }


def fork():
    labels = ["Replace", "Keep both", "Cancel"]
    return {
        "action": {"action": "outcome_fork"},
        "outcome_fork": {"clean": {"observed": False, "observed_in": []}, "states": [{
            "id": "d",
            "signature": {"title": "Upload options", "button_labels": labels, "message_tokens": [], "container_selector": "[role=dialog]"},
            "options": [{"id": l.lower(), "label": l, "role": "neutral", "recorded": l == "Replace", "steps": []} for l in labels],
            "observed_in": ["s1"],
        }]},
    }


def doc(*steps):
    return {"meta": {"id": "x", "title": "wf"}, "skills": [{"steps": list(steps)}], "inputs": []}


COVERED = {"title_tokens": ["upload", "options"], "buttons": ["Replace", "Keep both", "Cancel"], "count": 4}
UNCOVERED = {"title_tokens": ["discard", "changes"], "buttons": ["Discard", "Keep editing"], "count": 2}


@pytest.fixture()
def hooked(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clear_hooks()
    install_hooks()
    write_skill("skill_a", doc(step("click", name="Upload", stable="up"), fork()))
    yield
    clear_hooks()


def graph():
    return app_graph_store.get_graph(WS, HOST)


def test_a_covered_popup_gains_a_real_run_count(hooked):
    assert ingest_field(WS, HOST, [COVERED]) == 1
    (p,) = graph().popups
    assert p.title == "Upload options" and p.field_seen == 4 and p.appeared == 1


def test_an_uncovered_popup_is_kept_as_field_only_with_risk_marked(hooked):
    ingest_field(WS, HOST, [COVERED, {**UNCOVERED, "buttons": ["Delete", "Keep editing"]}])
    g = graph()
    assert len(g.popups) == 2
    extra = next(p for p in g.popups if p.sources == ["field"])
    assert extra.title == "discard changes" and extra.appeared == 0 and extra.field_seen == 2
    assert {o.label: o.role for o in extra.options} == {"Delete": "consequential", "Keep editing": "neutral"}


def test_ingesting_again_replaces_instead_of_adding(hooked):
    ingest_field(WS, HOST, [COVERED, UNCOVERED])
    ingest_field(WS, HOST, [COVERED])
    g = graph()
    assert [p.field_seen for p in g.popups] == [4] and len(g.popups) == 1


def test_a_workflow_that_later_covers_the_popup_absorbs_the_field_entry(hooked):
    ingest_field(WS, HOST, [UNCOVERED])
    assert len(graph().popups) == 2
    other = fork()
    other["outcome_fork"]["states"][0]["signature"].update({"title": "Discard changes?", "button_labels": ["Discard", "Keep editing"]})
    other["outcome_fork"]["states"][0]["options"] = [
        {"id": "discard", "label": "Discard", "role": "consequential", "recorded": True, "steps": []},
        {"id": "keep", "label": "Keep editing", "role": "neutral", "recorded": False, "steps": []},
    ]
    write_skill("skill_b", doc(step("click", name="Close", stable="close"), other))
    ingest_field(WS, HOST, [UNCOVERED])                    # the next sync re-matches it
    g = graph()
    merged = next(p for p in g.popups if p.title == "Discard changes?")
    assert merged.field_seen == 2 and merged.appeared == 1
    assert not [p for p in g.popups if p.sources == ["field"]]


def test_unusable_rows_are_dropped(hooked):
    rows = [
        {"title_tokens": ["x"], "buttons": ["Only"], "count": 5},
        {"title_tokens": ["x"], "buttons": ["report.pdf", "q3.xlsx"], "count": 5},     # labels that are content
        {"title_tokens": ["x"], "buttons": ["A", "B"], "count": 0},
    ]
    assert ingest_field(WS, HOST, rows) == 0
    assert len(graph().popups) == 1


def test_an_app_without_a_map_is_ignored(hooked):
    assert ingest_field(WS, "unknown.example.com", [UNCOVERED]) == 0
    assert app_graph_store.get_graph(WS, "unknown.example.com") is None


def test_a_field_only_popup_ships_to_runtimes_and_can_get_a_policy(hooked):
    ingest_field(WS, HOST, [{**UNCOVERED, "buttons": ["Delete", "Keep editing"]}])
    shipped = build_app_knowledge(WS, [HOST])["apps"][HOST]["popups"]
    extra = next(p for p in shipped if p["signature"]["title"] == "discard changes")
    assert {o["label"]: o["role"] for o in extra["options"]}["Delete"] == "consequential"

    h = AppGraphMixin()
    with pytest.raises(_CommandError) as e:                # a risky button needs the explicit confirmation here too
        h.cmd_set_app_popup_policy({"app_key": HOST, "popup_id": extra["id"], "policy": {"mode": "auto", "option": "Delete"}}, "r")
    assert e.value.code == "confirmation_required"
    h.cmd_set_app_popup_policy({"app_key": HOST, "popup_id": extra["id"], "policy": {"mode": "auto", "option": "Keep editing"}}, "r")
    assert next(p for p in graph().popups if p.id == extra["id"]).app_policy["option"] == "Keep editing"


def test_pull_field_asks_the_cloud_per_app_and_survives_failures(hooked):
    calls = []

    def cloud(path, **_kw):
        calls.append(path)
        return {"popups": [UNCOVERED]}

    out = pull_field(WS, cloud)
    assert calls == [f"/api/v1/app-graphs/{HOST}/field"] and out["field_popups"] == 1 and out["errors"] == []

    def broken(path, **_kw):
        raise RuntimeError("402")

    out = pull_field(WS, broken)
    assert out["field_popups"] == 0 and out["errors"] and graph() is not None
