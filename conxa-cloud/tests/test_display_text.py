from conxa_compile.display_text import clean_name, display_label, has_machine_words, person_text


def test_clean_name_drops_the_pages_extras():
    assert clean_name("New folder Alt+C then F") == "New folder"
    assert clean_name("ExpressionEngine.gitignore, (File)") == "ExpressionEngine.gitignore"
    assert clean_name("Save ⌘S") == "Save"
    assert clean_name("  Two \n lines ") == "Two lines"


def test_a_real_qualifier_is_kept():
    assert clean_name("Delete (permanent)") == "Delete (permanent)"


def test_display_label_cuts_at_a_word():
    out = display_label("word " * 30)
    assert out.endswith("…") and len(out) <= 61 and not out.endswith("wor…")


def test_person_text_is_one_line_without_an_email():
    assert "\n" not in person_text("a\nb", 20)
    assert "@" not in person_text("Signed in as ada@example.com", 60)


def test_machine_words():
    for bad in ("the ?pli=1 bit is a leftover", "Opens https://x.io/a", "uses invoice_number", "a {{token}}", "run `ls`"):
        assert has_machine_words(bad), bad
    for ok in ("You always start on the invoices page.", "Downloads a template file and uploads it to Drive."):
        assert not has_machine_words(ok), ok
