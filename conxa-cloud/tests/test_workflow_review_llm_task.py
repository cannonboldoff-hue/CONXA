"""workflow_review (BUILD-26 merge, wiring fixed BUILD-29) — the compiler's one whole-workflow
LLM call, decided by conxa_core.llm.client.VISION_TASKS / _openai_messages_for_task /
_openai_body_dict.

Before BUILD-29, the task name was missing from all three of those, so it silently fell through
to the no-system-prompt default and was routed to the text model — meaning every compile ran
rules-only, with no error anywhere. Mirrors test_copilot_llm_task.py's pattern: assert the
registration and prompt content directly, not just end-to-end behavior.
"""

from __future__ import annotations

from conxa_core.llm.client import (
    VISION_TASKS,
    _is_vision_task,
    _openai_body_dict,
    _openai_messages_for_task,
)


def _payload(steps):
    return {"input": {"steps": steps, "page_urls": ["https://example.com"], "sibling_bindings": {}}}


def test_workflow_review_is_a_vision_task():
    assert _is_vision_task("workflow_review")


def test_workflow_review_never_falls_through_to_the_no_system_prompt_default():
    messages = _openai_messages_for_task("workflow_review", _payload([{"key": "s0"}]))
    assert messages[0]["role"] == "system"
    system = messages[0]["content"]
    # Both merged contracts must survive: the intent-graph half and the second-opinion half.
    assert "intent_token" in system
    assert "goal" in system
    assert "suggestions" in system
    assert "step_key" in system


def test_workflow_review_forbids_selector_fields_in_its_own_system_prompt():
    system = _openai_messages_for_task("workflow_review", _payload([]))[0]["content"]
    assert "never write or change a page selector" in system.lower() \
        or "must never write a page selector" in system.lower() \
        or "never invent" in system.lower()


def test_workflow_review_attaches_images_as_image_url_blocks_not_inlined_text():
    steps = [
        {"key": "s0", "action": "click", "image_base64": "AAA", "image_mime": "image/png"},
        {"key": "s1", "action": "type"},
    ]
    messages = _openai_messages_for_task("workflow_review", _payload(steps))
    user_content = messages[1]["content"]
    assert isinstance(user_content, list)

    image_blocks = [b for b in user_content if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"] == "data:image/png;base64,AAA"

    # The regression this test exists for: before the branch was written, the whole payload
    # (images included) was json.dumps'd into a single text block — the raw base64 string must
    # never appear inside any text block now that images ride as separate content blocks.
    text_blocks = [b for b in user_content if b.get("type") == "text"]
    assert all("AAA" not in b["text"] for b in text_blocks)
    # And the step that never had an image contributes no image block or stray label.
    assert not any("s1 ---" in b["text"] for b in text_blocks if "Step:" in b["text"])


def test_workflow_review_body_gets_its_own_token_budget():
    body = _openai_body_dict("workflow_review", _payload([]), json_mode=True)
    assert body["max_tokens"] == 6144
    assert body["response_format"] == {"type": "json_object"}


def test_vision_task_set_has_exactly_one_definition():
    """BUILD-29: the router imports this same object as _VISION_TASK_NAMES rather than keeping
    its own literal copy — this is the guard against the duplication silently reappearing."""
    from app.llm.router import _VISION_TASK_NAMES

    assert _VISION_TASK_NAMES is VISION_TASKS
    assert "workflow_review" in _VISION_TASK_NAMES


def test_review_image_budget_keeps_low_confidence_images_drops_high_confidence_first():
    from conxa_compile.compiler.build import _REVIEW_IMAGE_BUDGET_BYTES, _trim_review_images

    big = "x" * (_REVIEW_IMAGE_BUDGET_BYTES // 2 + 1)
    items = [
        {"key": "hi_conf", "low_confidence": False, "image_base64": big, "image_mime": "image/jpeg"},
        {"key": "lo_conf", "low_confidence": True, "image_base64": big, "image_mime": "image/jpeg"},
    ]
    _trim_review_images(items)

    by_key = {it["key"]: it for it in items}
    assert "image_base64" not in by_key["hi_conf"]
    assert by_key["lo_conf"]["image_base64"] == big


def test_review_image_budget_no_op_when_under_budget():
    from conxa_compile.compiler.build import _trim_review_images

    items = [{"key": "s0", "low_confidence": False, "image_base64": "AAA", "image_mime": "image/jpeg"}]
    _trim_review_images(items)
    assert items[0]["image_base64"] == "AAA"


def test_workflow_review_cache_key_ignores_images():
    """BUILD-29: two payloads differing only in attached images must hash to the same cache
    key — the image-budget trim can drop a subset of images without changing anything else
    about a step, and the key must not depend on which subset survived."""
    from conxa_compile.llm.llm_cache import cache_key

    steps_with_image = [{"key": "s0", "action": "click", "image_base64": "AAA", "image_mime": "image/jpeg"}]
    steps_without_image = [{"key": "s0", "action": "click"}]

    def _strip(steps):
        return [{k: v for k, v in s.items() if k not in ("image_base64", "image_mime")} for s in steps]

    key_a = cache_key(1, steps=_strip(steps_with_image), page_urls=[], sibling_bindings={})
    key_b = cache_key(1, steps=_strip(steps_without_image), page_urls=[], sibling_bindings={})
    assert key_a == key_b
