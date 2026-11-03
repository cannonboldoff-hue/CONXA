"""Provenance gate: an event the page's own script fired never becomes a step."""
from unittest.mock import patch

import pytest

from conxa_compile.compiler.build import prepare_compile_events
from conxa_core.config import settings
from conxa_core.models.events import RecordedEvent


@pytest.fixture(autouse=True)
def _keep_every_action():
    # The provenance gate runs before (and independently of) the repeat cleanup; keep that out of the way.
    with patch.object(settings, "compile_keep_every_action", True):
        yield


def _ev(seq: int, origin=None, label="Save"):
    ev = {
        "action": {"action": "click", "timestamp": "2026-01-01T00:00:0%dZ" % seq, "value": None},
        "target": {"aria_label": label, "tag": "button"},
        "page": {"url": "https://app.example.com/"},
        "tab": {"id": "t1"},
        "extras": {"sequence": seq},
    }
    if origin:
        ev["origin"] = origin
    return ev


def test_script_event_is_dropped_and_ledgered():
    ledger: list = []
    out = prepare_compile_events([_ev(1), _ev(2, "script", "Google Account"), _ev(3)], {}, ledger)
    kept = [e["extras"]["sequence"] for e in out if e.get("extras", {}).get("sequence")]
    assert kept == [1, 3]
    assert [d["sequence"] for d in ledger if d["pass"] == "not_from_user"] == [2]
    assert ledger[0]["target"] == "Google Account"


def test_upload_filled_by_studio_picker_is_kept():
    """Studio's picker fills the input via set_files (untrusted change), but the person chose it."""
    up = _ev(2, "script", "Upload")
    up["action"]["action"] = "upload_intent"
    up["extras"]["file_pick"] = {"source": "download", "names": ["a.txt"]}
    ledger: list = []
    out = prepare_compile_events([_ev(1), up, _ev(3, "script")], {}, ledger)
    kept = [e["extras"]["sequence"] for e in out if e.get("extras", {}).get("sequence")]
    assert kept == [1, 2]
    assert [d["sequence"] for d in ledger] == [3]


def test_missing_origin_is_human_for_old_recordings():
    out = prepare_compile_events([_ev(1)], {}, [])
    assert any(e.get("extras", {}).get("sequence") == 1 for e in out)


def test_origin_field_is_on_the_model():
    assert "origin" in RecordedEvent.model_fields
