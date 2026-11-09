"""A long workflow must not lose its per-step meaning to the LLM output limit: the limit grows with the
step count, a reply stopped at the limit is noticed, and only then is the review sent in windows
(halved when a window is still cut off). No model, no network."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")))

from conxa_core.config import settings  # noqa: E402
from conxa_core.llm.client import OUTPUT_CEILING, TRUNCATED_MARKER, _openai_body_dict, mark_if_cut_off  # noqa: E402
from conxa_compile.llm import openapi_client, workflow_review  # noqa: E402


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _steps(n):
    return [{"key": f"k{i}", "action": "click"} for i in range(n)]


# ── the output limit grows with the input ───────────────────────────────────────────────────


def _max_tokens(task, key, n):
    return _openai_body_dict(task, {"input": {key: [{}] * n}}, json_mode=True).get("max_tokens")


def test_the_output_limit_grows_with_the_steps_and_stops_at_the_ceiling():
    assert _max_tokens("workflow_review", "steps", 0) == 12288               # the old fixed limit is the floor
    assert _max_tokens("workflow_review", "steps", 100) == 4096 + 1000 * 100
    assert _max_tokens("workflow_review", "steps", 10_000) == OUTPUT_CEILING
    assert _max_tokens("timeline_understanding", "rows", 50) == 4096 + 1200 * 50
    assert _max_tokens("timeline_understanding", "rows", 1000) == OUTPUT_CEILING
    assert _max_tokens("compile_recap", "steps", 500) == 1024                 # a fixed-size reply never grows


# ── the cut-off marker ──────────────────────────────────────────────────────────────────────


def test_a_reply_stopped_at_the_limit_is_tagged_but_a_finished_one_is_not():
    cut = {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}
    done = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    assert mark_if_cut_off(cut, {"goal": "g"}) == {"goal": "g", "_truncated": True}
    assert mark_if_cut_off(done, {"goal": "g"}) == {"goal": "g"}


def test_the_marker_reaches_error_detail_and_leaves_the_data_clean(monkeypatch):
    monkeypatch.setattr(openapi_client, "call_llm", lambda *a, **k: {"goal": "g", "_truncated": True})
    detail: list[str] = []
    out = openapi_client._call_llm_or_none("workflow_review", {}, 1000, error_detail=detail)
    assert out == {"goal": "g"} and detail == [TRUNCATED_MARKER]


# ── the review: one call, windows only when that is cut off ─────────────────────────────────


def _fake(calls, *, cut_over=None, cut_key=None):
    """A stand-in model: one intent per step it was sent. `cut_over`: a reply for more steps than this is
    'cut off'. `cut_key`: any call holding this step is always cut off."""
    def infer(**k):
        chunk, window = k["steps"], k.get("window")
        calls.append((len(chunk), window))
        if (cut_over is not None and len(chunk) > cut_over) or (cut_key and any(s["key"] == cut_key for s in chunk)):
            k["error_detail"].append(TRUNCATED_MARKER)
            return None
        raw = {"steps": [{"index": i, "intent": f"do {s['key']}", "intent_token": "x"} for i, s in enumerate(chunk)],
               "suggestions": []}
        if window is None or window["first"]:
            raw["goal"] = "The goal"
        if window is None or window["last"]:
            raw["expected_end_state"] = {"description": "done"}
        return raw
    return infer


def _run(monkeypatch, steps, infer):
    writes: list = []
    monkeypatch.setattr(workflow_review, "infer_workflow_review", infer)
    monkeypatch.setattr(workflow_review, "read_cached", lambda *a, **k: None)
    monkeypatch.setattr(workflow_review, "write_cached", lambda *a, **k: writes.append(1))
    detail: list[str] = []
    graph, _ = workflow_review.build_workflow_review(steps, page_urls=[], error_detail=detail)
    return graph, detail, writes


def test_a_long_workflow_is_still_one_call_when_the_reply_fits(monkeypatch):
    calls: list = []
    graph, _, writes = _run(monkeypatch, _steps(300), _fake(calls))
    assert [c[0] for c in calls] == [300] and calls[0][1] is None
    assert len(graph.steps) == 300 and writes == [1]


def test_a_reply_that_is_cut_off_is_redone_in_windows_and_joined(monkeypatch):
    calls: list = []
    graph, detail, writes = _run(monkeypatch, _steps(250), _fake(calls, cut_over=100))
    assert [c[0] for c in calls] == [250, 100, 100, 50]                       # the whole, then three windows
    assert [s.index for s in graph.steps] == list(range(250))                 # indexes are whole-workflow, not per window
    assert graph.goal == "The goal" and graph.expected_end_state == {"description": "done"}
    assert detail == [TRUNCATED_MARKER] and writes == [1]
    assert calls[2][1]["earlier"][-1] == {"key": "k99", "intent": "do k99"}   # later windows see what came before


def test_a_window_still_cut_off_is_halved_and_every_step_is_covered(monkeypatch):
    calls: list = []
    graph, _, _ = _run(monkeypatch, _steps(250), _fake(calls, cut_over=30))
    assert [s.index for s in graph.steps] == list(range(250)) and graph.goal == "The goal"
    assert [c[0] for c in calls][:5] == [250, 100, 50, 25, 25]               # 100 cut, so 50 and 50; 50 cut, so 25 and 25


def test_a_step_that_can_never_be_reviewed_is_reported_and_not_cached(monkeypatch):
    calls: list = []
    graph, detail, writes = _run(monkeypatch, _steps(250), _fake(calls, cut_key="k7"))
    assert "steps_not_reviewed: 1" in detail and writes == []
    assert len(graph.steps) == 249 and 7 not in [s.index for s in graph.steps]
