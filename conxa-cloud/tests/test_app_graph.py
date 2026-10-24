"""App maps (P1): a data-free map of each web app, folded from every compiled workflow.

Covers the scrubber (nothing a person typed or saw may enter a map), the extractor (a compiled skill
document -> per-host contributions), the fold (order-independent, no double counting) and the store
plus the skill write/delete hooks (re-compiling replaces, deleting removes).
"""

from __future__ import annotations

import json
import os
import sys

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_compile.app_graph import install_hooks, refresh_skill  # noqa: E402
from conxa_compile.app_graph.extract import app_key_of, contributions_from_doc  # noqa: E402
from conxa_compile.app_graph.scrub import safe_element_name, scrub_label, scrub_text, scrub_tokens  # noqa: E402
from conxa_core.config import settings  # noqa: E402
from conxa_core.models.app_graph import fold_contributions  # noqa: E402
from conxa_core.storage import app_graph_store  # noqa: E402
from conxa_core.storage.json_store import delete_skill, write_skill  # noqa: E402
from conxa_core.storage.skill_hooks import clear_hooks  # noqa: E402
from conxa_core.workspace import LOCAL_WORKSPACE_ID  # noqa: E402

DRIVE = "https://drive.example.com"


# ─── helpers ────────────────────────────────────────────────────────────────────────────────

def step(action, *, url_before, url_after=None, title="Home", name="", stable="h", value=None,
         input_binding=None, entity_binding=None, intent="", inner_text=None):
    fp = {"role": "button", "aria_label": name}
    if inner_text is not None:
        fp["inner_text"] = inner_text
    return {
        "action": {"action": action},
        "intent": intent or f"{action}_{stable}",
        "state": {
            "before": {"url": url_before, "page_title": title},
            "after": {"url": url_after or url_before, "page_title": title},
        },
        "identity_bundle": {"stable_hash": stable, "fingerprint": fp},
        "value": value,
        "input_binding": input_binding,
        "entity_binding": entity_binding,
        "consequence": "reversible",
    }


def fork_step(*, observed_in=("s1",), clean_in=(), title="Upload options"):
    return {
        "action": {"action": "outcome_fork"},
        "intent": "handle_popup_decision",
        "outcome_fork": {
            "clean": {"observed": bool(clean_in), "observed_in": list(clean_in)},
            "states": [{
                "id": "dialog_upload_options",
                "signature": {
                    "title": title,
                    "button_labels": ["Replace", "Keep both", "Cancel"],
                    "message_tokens": ["already", "exists"],
                    "container_selector": "[role=dialog]",
                },
                "options": [
                    {"id": "replace", "label": "Replace", "role": "consequential", "recorded": True, "steps": []},
                    {"id": "keep_both", "label": "Keep both", "role": "neutral", "recorded": False, "steps": []},
                    {"id": "cancel", "label": "Cancel", "role": "dismiss", "recorded": False, "steps": []},
                ],
                "observed_in": list(observed_in),
            }],
        },
    }


def doc(*steps, title="Upload a file"):
    return {"meta": {"id": "skill_a", "title": title}, "skills": [{"steps": list(steps)}]}


def upload_doc(*, title="Upload a file", with_fork=True, file_name="q3-report.pdf"):
    steps = [
        step("navigate", url_before=f"{DRIVE}/drive/my-drive", title="My Drive", stable="nav", intent="open_drive"),
        step("click", url_before=f"{DRIVE}/drive/my-drive", title="My Drive", name="Upload", stable="upload_btn", intent="click_upload"),
        step("upload", url_before=f"{DRIVE}/drive/my-drive", title="My Drive", name="File input", stable="file_in",
             value=file_name, input_binding="file_path", intent="upload_file"),
    ]
    if with_fork:
        steps.append(fork_step())
    return doc(*steps, title=title)


# ─── scrubber ───────────────────────────────────────────────────────────────────────────────

def test_scrub_text_removes_everything_that_could_be_data():
    s = scrub_text('Replace "q3-report.pdf" for jane@acme.com at https://x.test/a?b=1 order 12345 now')
    assert s == "Replace for at order now"
    assert "q3" not in s and "jane" not in s and "12345" not in s


def test_scrub_text_keeps_apostrophes_in_ordinary_words():
    assert scrub_text("Don't save") == "Don't save"
    assert scrub_text("Can't upload 'secret plan' today") == "Can't upload today"


def test_scrub_label_drops_long_text_and_empties():
    assert scrub_label("Keep both") == "Keep both"
    assert scrub_label("Quarterly revenue forecast for the northern region team") is None
    assert scrub_label("report.pdf") is None


def test_scrub_tokens_for_titles():
    assert scrub_tokens("Inbox (3) - jane@acme.com - Mail") == ["inbox", "mail"]


def test_safe_element_name_rules():
    base = {"identity_bundle": {"fingerprint": {"aria_label": "Upload", "inner_text": "Upload"}}}
    assert safe_element_name(base) == "Upload"
    # a table-row step: the row text IS the data
    assert safe_element_name({**base, "entity_binding": {"identifier": "{{customer}}"}}) is None
    # bound to an input: visible text may be the chosen value, only labels count
    bound = {"input_binding": "role", "identity_bundle": {"fingerprint": {"inner_text": "Administrator"}}}
    assert safe_element_name(bound) is None
    labelled = {"input_binding": "role", "identity_bundle": {"fingerprint": {"label_text": "Role", "inner_text": "Administrator"}}}
    assert safe_element_name(labelled) == "Role"
    # the literal typed value never survives as a name
    typed = {"value": "Acme Corp", "identity_bundle": {"fingerprint": {"inner_text": "Acme Corp"}}}
    assert safe_element_name(typed) is None


def test_app_key_strips_www_and_rejects_non_urls():
    assert app_key_of("https://WWW.Drive.Example.com/x") == "drive.example.com"
    assert app_key_of("about:blank") == ""


# ─── extractor ──────────────────────────────────────────────────────────────────────────────

def test_extract_builds_screens_actions_and_a_path():
    out = contributions_from_doc(upload_doc(), skill_id="skill_a", workspace_id="wrk_x")
    assert set(out) == {"drive.example.com"}
    c = out["drive.example.com"]
    assert c.id == "skill:skill_a" and c.workspace_id == "wrk_x"
    assert [s.route for s in c.screens] == ["/drive/my-drive"]
    assert len(c.actions) == 3 and len(c.path.action_ids) == 3
    upload = next(a for a in c.actions if a.intent == "click_upload")
    assert upload.name == "Upload" and upload.kind == "click"
    typed = next(a for a in c.actions if a.intent == "upload_file")
    assert typed.input_name == "file_path"


def test_extract_never_puts_a_typed_value_or_file_name_in_the_map():
    c = contributions_from_doc(upload_doc(file_name="q3-secret-report.pdf"), skill_id="skill_a")["drive.example.com"]
    blob = c.model_dump_json()
    assert "q3-secret" not in blob and "secret-report" not in blob


def test_dynamic_url_segments_are_one_screen():
    d = doc(
        step("click", url_before=f"{DRIVE}/orders/1001", title="Order 1001", stable="a"),
        step("click", url_before=f"{DRIVE}/orders/2002", title="Order 2002", stable="a"),
    )
    c = contributions_from_doc(d, skill_id="s")["drive.example.com"]
    assert [s.route for s in c.screens] == ["/orders/:id"]
    assert c.screens[0].title_tokens == ["order"]


def test_a_fork_becomes_a_popup_attached_to_the_action_before_it():
    c = contributions_from_doc(upload_doc(), skill_id="skill_a")["drive.example.com"]
    assert len(c.popups) == 1
    p = c.popups[0]
    assert p.title == "Upload options" and p.button_labels == ["Replace", "Keep both", "Cancel"]
    assert p.appeared == 1 and p.absent == 0
    assert p.after_actions == [c.path.action_ids[-1]]
    chosen = {o.label: o.chosen for o in p.options}
    assert chosen == {"Replace": 1, "Keep both": 0, "Cancel": 0}


def test_a_fork_with_a_clean_variant_counts_the_absent_case():
    d = upload_doc(with_fork=False)
    d["skills"][0]["steps"].append(fork_step(clean_in=("variant1",)))
    p = contributions_from_doc(d, skill_id="skill_a")["drive.example.com"].popups[0]
    assert p.appeared == 1 and p.absent == 1


def test_a_cross_host_step_is_filed_under_the_host_it_started_on():
    d = doc(
        step("click", url_before=f"{DRIVE}/login", url_after="https://accounts.example.org/signin", title="Sign in", stable="go"),
        step("click", url_before="https://accounts.example.org/signin", title="Sign in", stable="pw"),
    )
    out = contributions_from_doc(d, skill_id="s")
    assert set(out) == {"drive.example.com", "accounts.example.org"}
    edge = out["drive.example.com"].actions[0]
    assert edge.to_screen and edge.to_screen in {s.id for s in out["accounts.example.org"].screens}


def test_markers_and_blank_pages_are_skipped():
    d = doc(
        {"action": {"action": "frame_enter"}, "state": {}},
        step("click", url_before="about:blank", stable="x"),
    )
    assert contributions_from_doc(d, skill_id="s") == {}


# ─── fold ───────────────────────────────────────────────────────────────────────────────────

def _two_workflows():
    a = contributions_from_doc(upload_doc(title="Upload"), skill_id="skill_a")["drive.example.com"]
    other = doc(
        step("navigate", url_before=f"{DRIVE}/drive/my-drive", title="My Drive", stable="nav", intent="open_drive"),
        step("click", url_before=f"{DRIVE}/drive/my-drive", title="My Drive", name="Upload", stable="upload_btn", intent="click_upload"),
        step("click", url_before=f"{DRIVE}/drive/my-drive", title="My Drive", name="New folder", stable="nf", intent="new_folder"),
        title="Make a folder",
    )
    b = contributions_from_doc(other, skill_id="skill_b")["drive.example.com"]
    return a, b


def test_two_workflows_on_one_host_share_screens_and_actions():
    a, b = _two_workflows()
    g = fold_contributions("drive.example.com", "wrk", [a, b])
    assert len(g.screens) == 1 and g.screens[0].seen == 2  # workflows that visit it
    by_intent = {x.intent: x for x in g.actions}
    assert by_intent["click_upload"].seen == 2 and set(by_intent["click_upload"].sources) == {"skill:skill_a", "skill:skill_b"}
    assert len(g.actions) == 4  # navigate, upload, file input, new folder
    assert {p.skill_id for p in g.paths} == {"skill_a", "skill_b"}
    assert g.popups[0].appeared == 1


def test_fold_is_order_independent():
    a, b = _two_workflows()
    g1 = fold_contributions("h", "w", [a, b], built_at=0).model_dump()
    g2 = fold_contributions("h", "w", [b, a], built_at=0).model_dump()
    assert g1 == g2


def test_popups_from_two_recordings_add_up():
    d1 = upload_doc()
    d2 = upload_doc()
    d2["skills"][0]["steps"][-1] = fork_step(observed_in=("s9",))
    c1 = contributions_from_doc(d1, skill_id="a")["drive.example.com"]
    c2 = contributions_from_doc(d2, skill_id="b")["drive.example.com"]
    p = fold_contributions("h", "w", [c1, c2]).popups[0]
    assert p.appeared == 2
    assert {o.label: o.chosen for o in p.options}["Replace"] == 2


# ─── store + hooks ──────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def hooked(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clear_hooks()
    install_hooks()
    yield tmp_path
    clear_hooks()


def _graph(app="drive.example.com"):
    return app_graph_store.get_graph(LOCAL_WORKSPACE_ID, app)


def test_writing_a_skill_builds_the_map_and_recompiling_replaces_not_doubles(hooked):
    write_skill("skill_a", upload_doc())
    g = _graph()
    assert g is not None and len(g.paths) == 1 and g.popups[0].appeared == 1
    seen_before = {a.id: a.seen for a in g.actions}
    write_skill("skill_a", upload_doc())          # same skill compiled again
    again = _graph()
    assert {a.id: a.seen for a in again.actions} == seen_before
    assert again.popups[0].appeared == 1 and len(again.paths) == 1


def test_a_second_skill_joins_the_same_map_and_deleting_it_leaves_the_first(hooked):
    write_skill("skill_a", upload_doc())
    write_skill("skill_b", upload_doc(title="Other", with_fork=False))
    assert len(_graph().paths) == 2
    delete_skill("skill_b")
    g = _graph()
    assert [p.skill_id for p in g.paths] == ["skill_a"]
    delete_skill("skill_a")
    assert _graph() is None
    assert app_graph_store.list_apps(LOCAL_WORKSPACE_ID) == []


def test_a_skill_that_stops_touching_a_host_loses_its_part_of_that_host(hooked):
    two_hosts = doc(
        step("click", url_before=f"{DRIVE}/login", url_after="https://accounts.example.org/signin", stable="go"),
        step("click", url_before="https://accounts.example.org/signin", stable="pw"),
    )
    write_skill("skill_a", two_hosts)
    assert _graph("accounts.example.org") is not None
    write_skill("skill_a", doc(step("click", url_before=f"{DRIVE}/x", stable="only")))
    assert _graph("accounts.example.org") is None and _graph("drive.example.com") is not None


def test_a_broken_document_never_fails_the_write(hooked):
    write_skill("skill_x", {"skills": "not-a-list"})   # must not raise
    assert app_graph_store.list_apps(LOCAL_WORKSPACE_ID) == []


def test_list_apps_summarises(hooked):
    write_skill("skill_a", upload_doc())
    (row,) = app_graph_store.list_apps(LOCAL_WORKSPACE_ID)
    assert row["app_key"] == "drive.example.com" and row["workflows"] == 1 and row["popups"] == 1


def test_refresh_files_the_skill_under_its_workflows_workspace(hooked):
    from conxa_core.storage.workflow_store import create_workflow, get_workflow, save_workflow

    wf = create_workflow("Upload a file", f"{DRIVE}/drive", workspace_id="wrk_acme")
    write_skill("skill_a", upload_doc())              # compile writes before the workflow points at it
    assert _graph() is not None
    wf = get_workflow(wf.id)
    wf.skill_id = "skill_a"
    save_workflow(wf)
    refresh_skill("skill_a")                           # compile.py's final refresh
    assert _graph() is None                            # no longer filed as unassigned
    moved = app_graph_store.get_graph("wrk_acme", "drive.example.com")
    assert moved is not None and moved.paths[0].name == "Upload a file"


def test_graph_json_holds_no_typed_value(hooked):
    write_skill("skill_a", upload_doc(file_name="q3-secret-report.pdf"))
    for f in (hooked / "app_graphs").rglob("*.json"):
        assert "q3-secret" not in f.read_text(encoding="utf-8")


# ─── Studio commands the viewer calls ───────────────────────────────────────────────────────

def test_list_and_get_commands(hooked):
    from handlers.app_graph import AppGraphMixin
    from handlers.protocol import _CommandError

    write_skill("skill_a", upload_doc())
    h = AppGraphMixin()
    (row,) = h.cmd_list_app_graphs({}, "r")["apps"]
    assert row["app_key"] == "drive.example.com" and row["workspace_id"] == LOCAL_WORKSPACE_ID
    graph = h.cmd_get_app_graph({"workspace_id": row["workspace_id"], "app_key": row["app_key"]}, "r")["graph"]
    assert graph["popups"][0]["title"] == "Upload options" and len(graph["screens"]) == 1
    assert h.cmd_list_app_graphs({"workspace_id": "wrk_other"}, "r") == {"apps": []}
    with pytest.raises(_CommandError):
        h.cmd_get_app_graph({"workspace_id": row["workspace_id"], "app_key": "nope.test"}, "r")


# ─── the runtime scrubber (runtime/app/scrub.js) must agree with this one ──────────────────────

def test_python_scrubber_agrees_with_the_shared_cases_the_runtime_also_runs():
    from conxa_compile.compiler.navigation_effects import route_template

    path = os.path.join(os.path.dirname(__file__), "..", "..", "runtime", "test", "fixtures", "scrub_cases.json")
    cases = json.load(open(path, encoding="utf-8"))
    fns = {"text": scrub_text, "label": scrub_label, "tokens": scrub_tokens, "route": route_template}
    assert len(cases) > 20
    for c in cases:
        assert fns[c["fn"]](c["in"]) == c["out"], (c["fn"], c["in"])
