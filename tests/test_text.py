"""Unit tests for the pure text-processing helpers.

These are deliberately the most-tested part of the project: unlike the
PDF/OCR/Pandoc engines (which need real books and external binaries to
exercise meaningfully), the text helpers are pure functions that every
engine's output passes through, so a regression here silently corrupts
every conversion.
"""

import tome2md.converter as converter
from tome2md.converter import (
    _resolve_dir,
    normalize_text,
    promote_chapter_lines,
    resolve_ocr_lang,
    safe_filename,
    strip_title_noise,
    t,
)


def test_resolve_dir_uses_english_defaults(tmp_path):
    assert _resolve_dir(None, "input", tmp_path) == tmp_path / "to-convert"
    assert _resolve_dir(None, "output", tmp_path) == tmp_path / "converted"
    assert _resolve_dir(None, "processed", tmp_path) == tmp_path / "originals"


def test_console_messages_default_to_english_and_support_italian(monkeypatch):
    assert t("found_files", n=2) == "Found 2 file(s).\n"
    monkeypatch.setattr(converter, "UI_LANG", "it")
    assert t("found_files", n=2) == "Trovati 2 file.\n"


def test_normalize_text_expands_ligatures():
    assert normalize_text("ﬁle") == "file"


def test_normalize_text_dehyphenates_line_wraps():
    assert "definition" in normalize_text("This is a defi-\nnition of a word.")


def test_normalize_text_strips_pandoc_leftovers():
    out = normalize_text("Real content.\n[]{#some-id}\n:::note\nMore content.")
    assert "{#some-id}" not in out
    assert ":::" not in out
    assert "Real content." in out
    assert "More content." in out


def test_normalize_text_collapses_blank_lines_without_losing_paragraphs():
    out = normalize_text("Para one.\n\n\n\n\nPara two.")
    assert out.count("\n\n\n") == 0
    assert "Para one." in out
    assert "Para two." in out


def test_strip_title_noise_removes_mirror_suffix():
    assert strip_title_noise("Some Book (z-library.sk, 1lib.sk)") == "Some Book"


def test_strip_title_noise_keeps_clean_titles_untouched():
    assert strip_title_noise("A Clean Title") == "A Clean Title"


def test_safe_filename_strips_forbidden_characters():
    out = safe_filename('Chapter: "One"? <Two>')
    for ch in '\\/*?:"<>|':
        assert ch not in out


def test_safe_filename_never_returns_empty():
    assert safe_filename("???") == "untitled"


def test_safe_filename_respects_limit():
    out = safe_filename("x" * 200, limit=10)
    assert len(out) <= 10


# --- multilingual chapter-heading promotion -------------------------------- #


def test_promote_chapter_lines_english():
    out = promote_chapter_lines("Chapter 3: The Turn\nSome body text.")
    assert out.splitlines()[0].startswith("# Chapter 3")


def test_promote_chapter_lines_supports_italian_content():
    out = promote_chapter_lines("Capitolo 4 - Il ritorno\nTesto.")
    assert out.splitlines()[0].startswith("# Capitolo 4")


def test_promote_chapter_lines_french_and_german():
    fr = promote_chapter_lines("Chapitre 1\nBonjour.")
    de = promote_chapter_lines("Kapitel 2: Der Anfang\nHallo.")
    assert fr.splitlines()[0].startswith("# Chapitre 1")
    assert de.splitlines()[0].startswith("# Kapitel 2")


def test_promote_chapter_lines_does_not_touch_existing_headings():
    """A line already starting with '#' must never be double-prefixed."""
    out = promote_chapter_lines("# Chapter 1\nBody text about chapter economics.")
    assert out.splitlines()[0] == "# Chapter 1"


def test_promote_chapter_lines_does_not_touch_unrelated_prose():
    text = "This chapter discusses habits at length, not as a heading."
    assert promote_chapter_lines(text) == text


# --- OCR language resolution ------------------------------------------------ #


def test_resolve_ocr_lang_passes_through_explicit_spec():
    assert resolve_ocr_lang("eng+fra") == "eng+fra"


def test_resolve_ocr_lang_auto_falls_back_to_eng_without_tesseract(monkeypatch):
    monkeypatch.setattr("tome2md.converter.shutil.which", lambda _: None)
    assert resolve_ocr_lang("auto") == "eng"
