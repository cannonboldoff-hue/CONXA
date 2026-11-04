"""RecordedEvent shape check. (classify_login_flow was removed 2026-11-05: nothing called it.)"""

from __future__ import annotations

from conxa_core.models.events import (
    ActionMeta, DomContext, PageContext, RecordedEvent, SnapshotRef,
    SemanticFeatures, Selectors, StateChange, TargetDom, Timing, VisualFeatures,
)


# ─────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────

def _make_event(
    url: str = "https://app.example.com",
    title: str = "",
    input_type: str | None = None,
    target_name: str | None = None,
) -> RecordedEvent:
    return RecordedEvent(
        action=ActionMeta(action="click", timestamp="2026-01-01T00:00:00Z"),
        target=TargetDom(tag="input", name=target_name),
        selectors=Selectors(css="input", xpath="//input", text_based="", aria=""),
        context=DomContext(parent="form", siblings=[], index_in_parent=0),
        semantic=SemanticFeatures(normalized_text="", role="input", input_type=input_type, intent_hint="provide_input"),
        visual=VisualFeatures(bbox={"x": 0, "y": 0, "w": 0, "h": 0}, viewport="1280x800", scroll_position="0,0", timestamp_ms=0),
        page=PageContext(url=url, title=title),
        state_change=StateChange(before="", after=""),
        timing=Timing(),
        ancestors=[],
        surrounding_text="",
        snapshot=SnapshotRef(ref="", dom_hash=""),
    )


def test_recorded_event_omits_url_state_field():
    event = _make_event()
    assert "url_state" not in event.model_dump()
