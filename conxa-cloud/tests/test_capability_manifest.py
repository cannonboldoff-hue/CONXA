"""BUILD-26 stage g: the capability manifest must never claim something the patch gate doesn't
actually allow — that mismatch is the whole failure mode this manifest exists to prevent (the
model proposing something the gate then silently drops). This is the same anti-drift check as
`capability_manifest.py`'s own `__main__` self-check, formalized as a real test.
"""

from __future__ import annotations

import copy
import os
import sys

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

from conxa_compile.editor.capability_manifest import build_capability_manifest  # noqa: E402
from conxa_compile.editor.patch_gate import validate_editor_patch  # noqa: E402
from conxa_compile.editor.workflow_mutations import _new_manual_step  # noqa: E402
from conxa_compile.policy.bundle import get_policy_bundle  # noqa: E402


def test_every_insertable_kind_has_a_runtime_note():
    manifest = build_capability_manifest()
    for row in manifest["kinds"]:
        if row["insertable"]:
            assert row["runtime_note"], f"{row['kind']} is insertable but carries no runtime_note"


def test_manifest_never_claims_a_key_the_gate_rejects():
    """For every kind the manifest lists a patchable field for, patching a fresh scaffold with
    just that field must never fail with an "unsupported key" style error — a value-shape
    rejection is fine (the manifest promises the FIELD is allowed, not that any value passes)."""
    manifest = build_capability_manifest()
    policy = get_policy_bundle().data
    unsupported_key_markers = ("_allows_only_", "_not_patchable_here", "recording_marker_steps_are_read_only")

    for row in manifest["kinds"]:
        kind = row["kind"]
        if kind in ("navigate", "scroll", "for_each", "if_present", "try_dismiss", "wait_for_one_of"):
            continue  # structural keys (action/target/branch/for_each) need real shaped values, not exercised here
        try:
            scaffold = _new_manual_step(kind, "")
        except ValueError:
            continue  # not insertable via the manual scaffolder (e.g. upload_intent)
        for field in row["patchable_fields"]:
            if field in ("action", "target", "frame", "signals"):
                continue
            probe = copy.deepcopy(scaffold)
            top = field.split(".")[0]  # a dotted field (validation.assertions) is patched through its top-level key
            value = probe.get(top, "x")
            if kind == "extract" and field == "entity_binding.confirmed":
                # Only a binding code already wrote can be confirmed, and only its flag (BUILD-117 (2)).
                probe["entity_binding"] = {"container_selector": "li", "identifier": ""}
                value = {"confirmed": True}
            try:
                validate_editor_patch(probe, {top: value}, policy)
            except ValueError as exc:
                assert not any(m in str(exc) for m in unsupported_key_markers), (
                    f"manifest claims {kind}.{field} is patchable but the gate rejects the KEY itself: {exc}"
                )


def test_marker_kinds_are_never_listed_as_insertable_or_patchable():
    manifest = build_capability_manifest()
    marker_kinds = set(manifest["marker_kinds"])
    for row in manifest["kinds"]:
        assert row["kind"] not in marker_kinds
    assert not (set(manifest["insertable_kinds"]) & marker_kinds)
