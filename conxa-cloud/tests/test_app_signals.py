"""Proof of Done stage 6: the app map learns how THIS app words progress, success and failure, in its own language."""

from conxa_compile.app_graph.extract import contributions_from_doc
from conxa_compile.app_graph.knowledge import build_app_knowledge
from conxa_compile.compiler.effects import app_words, build_effect
from conxa_core.models.app_graph import AppGraph, AppSignals, Contribution, fold_contributions


def _german_upload_event():
    return {
        "action": {"action": "upload_intent"},
        "post_watch": {
            "progress_seen": True,
            "waited_ms": 4000,
            "progress_words": ["wird", "hochgeladen", "datei"],
            "final_status": [{"kind": "status", "words": ["upload", "abgeschlossen"]}],
        },
        "surface_context": {"items": [{"kind": "alert", "words": ["fehlgeschlagen", "speicher", "voll"]}]},
    }


# ── what is learned from one recording ──────────────────────────────────────────────────────────


def test_a_german_upload_teaches_the_apps_own_words_and_drops_the_short_and_the_ambiguous():
    words = app_words(_german_upload_event())
    assert words["progress"] == ["datei", "hochgeladen"], "'wird' is too short to say anything"
    assert words["success"] == ["abgeschlossen", "upload"]
    assert words["failure"] == ["fehlgeschlagen", "speicher"]


def test_a_word_used_for_both_progress_and_success_says_nothing_and_is_dropped():
    ev = _german_upload_event()
    ev["post_watch"]["progress_words"] = ["upload", "hochgeladen"]
    words = app_words(ev)
    assert "upload" not in words.get("progress", []) and "upload" not in words.get("success", [])
    assert "hochgeladen" in words["progress"]


def test_the_words_ride_on_the_effect_and_never_on_a_check():
    eff = build_effect(_german_upload_event())
    assert eff.words["success"] == ["abgeschlossen", "upload"]
    assert "hochgeladen" not in eff.model_dump_json().split('"words"')[0], "no check holds a learned word"


# ── the map ─────────────────────────────────────────────────────────────────────────────────────


def _doc_with(effect):
    step = {
        "action": {"action": "upload"},
        "intent": "Upload the file",
        "url": "https://drive.example.de/ordner",
        "state": {"before": {"url": "https://drive.example.de/ordner"}, "after": {"url": "https://drive.example.de/ordner"}},
        "identity_bundle": {"stable_hash": "h1"},
        "validation": {"assertions": [], "effect": effect.model_dump()},
    }
    return {"skills": [{"steps": [step]}], "meta": {"title": "Upload"}}


def test_a_compiled_workflow_contributes_the_words_of_the_app_it_ran_on():
    contribs = contributions_from_doc(_doc_with(build_effect(_german_upload_event())), skill_id="s1")
    c = contribs["drive.example.de"]
    assert c.signals.progress == ["datei", "hochgeladen"] and c.signals.success == ["abgeschlossen", "upload"]


def test_the_fold_unions_recordings_and_drops_a_word_two_of_them_disagree_about():
    a = Contribution(id="skill:a", app_key="x.de", signals=AppSignals(progress=["hochgeladen"], success=["abgeschlossen"]))
    b = Contribution(id="skill:b", app_key="x.de", signals=AppSignals(progress=["laeuft", "abgeschlossen"], failure=["fehler"]))
    g = fold_contributions("x.de", "", [a, b])
    assert g.signals.progress == ["hochgeladen", "laeuft"]
    assert g.signals.success == [], "'abgeschlossen' is progress in one recording and success in another"
    assert g.signals.failure == ["fehler"]
    # order independent, like the rest of the fold
    assert fold_contributions("x.de", "", [b, a], built_at=g.built_at).signals == g.signals


# ── what ships ──────────────────────────────────────────────────────────────────────────────────


def test_the_words_ship_in_app_knowledge_even_when_the_app_has_no_popups():
    graph = AppGraph(app_key="drive.example.de", signals=AppSignals(progress=["hochgeladen"], success=["abgeschlossen"]))
    knowledge = build_app_knowledge("w", ["https://drive.example.de/x"], graph_for=lambda host: graph)
    assert knowledge["apps"]["drive.example.de"] == {"signals": {"progress": ["hochgeladen"], "success": ["abgeschlossen"]}}


def test_an_app_that_taught_nothing_ships_nothing():
    graph = AppGraph(app_key="drive.example.de")
    assert build_app_knowledge("w", ["https://drive.example.de/x"], graph_for=lambda host: graph) is None
