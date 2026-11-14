"""A word the intent names ("download", "file") that is only the clicked control's own label was on the page before the
action, so it cannot show the action worked. Same rule as for new page text (validation_planner._drop_echoes)."""
from conxa_compile.compiler.validation_planner import infer_success_conditions

POLICY = {"decision_layer": {"intent_primary_validation": True, "success_add_intent_tokens": True}}


def _tokens(step, intent, text_change=()):
    out = infer_success_conditions({"type": "none"}, {"text_change": list(text_change)}, "https://x/", POLICY,
                                   final_intent=intent, source_step=step)
    return out["expected_text_tokens"]


def test_words_of_the_clicked_control_are_not_outcome_checks():
    step = {"action": {"action": "click"}, "target": {"inner_text": "Download raw file", "aria_label": "Download raw file"},
            "page": {"url": "https://x/"}}
    assert _tokens(step, "click_download_raw_file") == []


def test_a_menu_entry_with_a_shortcut_hint_is_still_an_echo():
    step = {"action": {"action": "click"}, "target": {"inner_text": "File upload Alt+C then U"}, "page": {"url": "https://x/"}}
    assert _tokens(step, "click_file_upload_option") == []


def test_a_word_the_action_really_brought_in_is_kept():
    step = {"action": {"action": "click"}, "target": {"inner_text": "Save"}, "page": {"url": "https://x/"}}
    assert _tokens(step, "save_changes", text_change=["Changes saved"]) == ["Changes saved"]
