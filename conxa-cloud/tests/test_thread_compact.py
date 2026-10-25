"""One item per moment: the failure sentence and the one-time cleanup of old threads."""

from __future__ import annotations

import json

import pytest

from conxa_compile.conversation import thread_store as ts
from conxa_compile.conversation.failure_summary import summarize


def test_summarize_is_plain_and_never_the_raw_text():
    raw = ("Execution failed at step 2: Step 2 (click) failed: Something unexpected went wrong. "
           "(Ambiguous element resolution (no signal cleared uniqueness gate)) Recovery ceiling Tier 2")
    out = summarize(raw)
    assert "uniqueness" not in out and "Tier" not in out and "several matching" in out
    assert summarize("???") == "Something went wrong at this step."
    assert "smaller" in summarize("Element not found (resolve miss) recorded at 1280x720; this browser is 922x624")
    assert summarize("???", "verify_fail").startswith("The step ran")


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    from conxa_core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return "wf:w1"


def _seed(thread):
    ts.append(thread, "event", text="Recording started", glyph="run")
    ts.append(thread, "event", text="Recording saved", glyph="ok")
    ts.append_card(thread, "test_run", "failed", card_id="c1", error="Ambiguous element resolution (x)")
    ts.append(thread, "event", text="Test failed", glyph="fail")
    ts.append(thread, "event", text="Applied: Change the steps", glyph="ok")
    ts.append(thread, "event", text="Test passed", glyph="ok")


def test_compact_drops_duplicates_adds_summary_and_is_idempotent(thread):
    _seed(thread)
    items = ts.load(thread)["items"]
    assert [i["text"] for i in items if i["kind"] == "event"] == ["Recording started", "Test passed"]
    card = next(i for i in items if i["kind"] == "card")
    assert "several matching" in card["summary"]
    path = ts.thread_path(thread)
    assert path.with_suffix(".jsonl.bak").is_file()
    before = path.read_text(encoding="utf-8")
    ts.load(thread)
    assert path.read_text(encoding="utf-8") == before  # second load changes nothing
    assert all(json.loads(l).get("text") not in ("Recording saved", "Test failed") for l in before.splitlines())


def test_compact_leaves_an_empty_thread_alone(thread):
    assert ts.load(thread)["items"] == []
    assert not ts.thread_path(thread).exists()
