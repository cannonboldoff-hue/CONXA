"""Engine v2 C3: the action contract.

packages/conxa-core/conxa_core/models/action_contract.json says what every recorded action kind becomes
and how the runtime runs it. This is the Python half; runtime/test/unit/test_action_contract.js is the Node
half. Together they fail the build when the recorder model, the editor, the packager, the recovery policy
and the runtime handler table stop agreeing, instead of a customer finding out when a step type is unknown."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

import conxa_core
from conxa_compile.compiler.action_policy import recovery_enabled_for_action
from conxa_compile.editor import action_registry as registry
from conxa_compile.skill_package_builder_saved_skill import _saved_step_to_execution_step
from conxa_core.models.events import ActionKind

CONTRACT = json.loads((Path(conxa_core.__file__).parent / "models" / "action_contract.json").read_text("utf-8"))
KINDS = CONTRACT["kinds"]
STEP_ONLY = CONTRACT["step_only"]


def test_every_recorded_kind_has_an_entry_and_nothing_else_does():
    assert set(KINDS) == set(get_args(ActionKind))


def test_step_only_types_are_exactly_the_editor_kinds_the_recorder_cannot_emit():
    # Editor-authored step types (ai_review, handover, for_each, ...) are not recorder kinds.
    assert set(STEP_ONLY) == set(registry.ACTION_KIND_ORDER) - set(get_args(ActionKind))


def test_editor_knows_every_kind_except_the_one_the_compiler_rewrites():
    missing = {k for k in KINDS if not registry.is_supported_action(k)}
    assert missing == {k for k, v in KINDS.items() if v["via"] == "compiler"}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_recovery_flag_matches_the_policy(kind):
    assert recovery_enabled_for_action(kind) is KINDS[kind]["recovery"], kind


@pytest.mark.parametrize("kind", sorted(STEP_ONLY))
def test_step_only_recovery_flag_matches_the_policy(kind):
    assert recovery_enabled_for_action(kind) is STEP_ONLY[kind]["recovery"], kind


def _minimal_saved_step(kind: str) -> dict:
    """The smallest saved step the packager can turn into something runnable for ``kind``."""
    step = {"action": {"action": kind, "value": "x"}, "target": {"primary_selector": "#a"},
            "url": "https://example.test/x"}
    if kind == "drag_drop":
        step["action"]["value"] = json.dumps({"src_selector": "#a", "dst_selector": "#b"})
    if kind == "extract":
        step["action"]["value"] = "invoice_total"
    if kind == "wait":
        step["action"]["ms"] = 100
    return step


@pytest.mark.parametrize("kind", sorted(k for k, v in KINDS.items() if v["via"] == "packager"))
def test_the_packager_turns_the_kind_into_the_step_the_contract_names(kind):
    built = _saved_step_to_execution_step(_minimal_saved_step(kind))
    assert built is not None, f"{kind}: the packager drops it, so a recorded step would vanish from execution.json"
    assert built["type"] == KINDS[kind]["step"]
