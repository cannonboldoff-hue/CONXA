"""build.py::_rewrite_loop_suggestions_for_review builds role-tagged, selector-free step
context from the actual compiled steps — regression guard for the bug where the LLM copy
call got only a bare filename + boolean and hallucinated a wrong mechanism (e.g. a delete)
for what is always a download-then-upload pattern."""

from __future__ import annotations

import sys

sys.path.insert(0, "../conxa-builder/python")

from conxa_core.models.skill_spec import SkillStep  # noqa: E402

from conxa_compile.compiler.build import _rewrite_loop_suggestions_for_review  # noqa: E402
from conxa_compile.compiler.loop_suggestion import detect_download_upload_loop_candidates  # noqa: E402


def _step(**kw) -> SkillStep:
    base = dict(action="click", intent="x", url="", value=None, input_binding=None)
    base.update(kw)
    return SkillStep(**base)


def _download_value(filename: str) -> str:
    return f'{{"url": "blob:x", "suggested_filename": "{filename}"}}'


def _matching_steps() -> list[SkillStep]:
    return [
        _step(action="navigate", url="https://example.com/repo"),
        _step(
            action="navigate",
            url="https://example.com/blob/main/Actionscript.gitignore",
            intent="Open the Actionscript.gitignore page",
        ),
        _step(action="click", intent="click_download"),
        _step(
            action="download_observed",
            value=_download_value("Actionscript.gitignore"),
            intent="Download Actionscript.gitignore",
        ),
        _step(action="navigate", url="https://drive.example.com"),
        _step(
            action="upload_intent",
            value="{{downloaded_file}}",
            input_binding=None,
            intent="Upload the file to Drive",
        ),
    ]


def test_rewrite_never_touches_structural_fields():
    steps = _matching_steps()
    suggestions = detect_download_upload_loop_candidates(steps)
    before = {k: v for k, v in suggestions[0].items() if k != "why"}

    _rewrite_loop_suggestions_for_review(suggestions, steps)

    after = {k: v for k, v in suggestions[0].items() if k != "why"}
    assert before == after  # only `why` may ever change


def test_the_review_is_asked_about_the_loop_with_a_deterministic_id_and_role_tagged_steps():
    from conxa_compile.compiler import build as build_module

    steps = _matching_steps()
    first = build_module._review_loop_candidates(steps)
    second = build_module._review_loop_candidates(steps)
    assert len(first) == 1 and first[0]["id"] == second[0]["id"]      # not the suggestion's random uuid: it keys a cache
    assert {s["role"] for s in first[0]["steps"]} == {"acquire", "upload"}


def test_the_reviews_sentence_replaces_why_only_when_usable():
    steps = _matching_steps()
    for copy, expect_changed in (
        ({"__PAIR__": "Do you want this to run for any file you name?"}, True),
        ({"__PAIR__": ""}, False),
        ({"__PAIR__": "x" * 401}, False),
        ({"__PAIR__": "Use {{file}} here"}, False),
        ({"some|other": "Not this loop"}, False),
        (None, False),
    ):
        suggestions = detect_download_upload_loop_candidates(steps)
        original = suggestions[0]["why"]
        pair = "|".join((suggestions[0]["wrap_start_key"], suggestions[0]["upload_step_key"]))
        _rewrite_loop_suggestions_for_review(suggestions, steps, {(pair if k == "__PAIR__" else k): v for k, v in (copy or {}).items()} or copy)
        assert (suggestions[0]["why"] != original) is expect_changed


def test_the_loop_questions_own_context_never_sees_the_redundant_click():
    # The click removal is a SEPARATE Yes/No question (ReviewQuestionsDialog.tsx), asked only
    # after the loop question is answered Yes, with its own deterministic (no LLM) copy
    # (loop_suggestion.py's `redundant_click_why`) — the loop question's own LLM context must
    # never even see this step, let alone tag it with a role.
    from conxa_compile.compiler import build as build_module

    steps = _matching_steps()
    steps[1] = _step(
        action="navigate",
        url="https://example.com/blob/main/Actionscript.gitignore",
        tab={"id": "tab_0"},
    )
    redundant = _step(
        action="click",
        intent="click_the_file_link",
        tab={"id": "tab_0"},
        identity_bundle={"fingerprint": {"inner_text": "Actionscript.gitignore"}},
    )
    steps = steps[:1] + [redundant] + steps[1:]
    suggestions = detect_download_upload_loop_candidates(steps)
    assert "redundant_click_key" in suggestions[0]
    assert "leftover click" in suggestions[0]["redundant_click_why"]

    candidates = build_module._review_loop_candidates(steps)

    roles = [s["role"] for s in candidates[0]["steps"]]
    assert "redundant_click" not in roles
    assert set(roles) == {"acquire", "upload"}
    assert not any(s.get("description") == "click_the_file_link" for s in candidates[0]["steps"])
