"""W5-i: a click nobody classified is withheld in a dry run (a hand-added Send click used to escape it)."""

from __future__ import annotations

from conxa_compile.skill_package_builder_saved_skill import _mark_unclassified_click


def _out(raw):
    converted = {"type": str(raw["action"]["action"])}
    _mark_unclassified_click(raw, converted)
    if (raw.get("identity_bundle") or {}).get("destructive"):
        converted["destructive"] = True
    return converted


def test_a_hand_added_click_has_an_unknown_consequence():
    assert _out({"action": {"action": "click"}, "intent": ""}).get("consequence_unknown") is True


def test_a_classified_click_is_not_unknown():
    assert "consequence_unknown" not in _out({"action": {"action": "click"}, "identity_bundle": {"destructive": False}})
    assert "consequence_unknown" not in _out({"action": {"action": "click"}, "identity_bundle": {"destructive": True}})
    assert "consequence_unknown" not in _out({"action": {"action": "click"}, "consequence": "reversible"})


def test_other_kinds_are_not_marked():
    assert "consequence_unknown" not in _out({"action": {"action": "type"}})
