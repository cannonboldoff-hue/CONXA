"""Parsing for the workflow-intent section of the merged workflow-review call.

`_graph_from_raw` used to belong to a standalone workflow-intent LLM call that
ran before the per-step compile loop. It's now reused by workflow_review.py,
which merges that call with the second-opinion pass into one whole-workflow
multimodal call that runs after every step (and its vision anchor) is
compiled — see workflow_review.py's module docstring for why.

This is one of the few LLM-driven passes the compile pipeline still runs — see
the "LLM does not write selector strings" invariant; it produces intents and
annotations, never selectors.
"""

from __future__ import annotations

from typing import Any

from conxa_core.models.skill_spec import WorkflowIntentGraph, WorkflowIntentStep


def _graph_from_raw(raw: dict[str, Any]) -> WorkflowIntentGraph:
    intent_steps: list[WorkflowIntentStep] = []
    for item in raw.get("steps") or []:
        if not isinstance(item, dict):
            continue
        # A missing index must skip the item: defaulting it to 0 would put its prose on step 0.
        if item.get("index") is None:
            continue
        try:
            intent_steps.append(
                WorkflowIntentStep(
                    index=int(item["index"]),
                    intent=str(item.get("intent") or ""),
                    verification_anchor=str(item.get("verification_anchor") or ""),
                    intent_token=str(item.get("intent_token") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return WorkflowIntentGraph(
        goal=str(raw.get("goal") or ""),
        steps=intent_steps,
        decision_points=list(raw.get("decision_points") or []),
        expected_end_state=dict(raw.get("expected_end_state") or {}),
    )
