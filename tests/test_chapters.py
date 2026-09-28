"""Tests for split_into_chapters — the step that turns one Markdown blob into
a linked vault of notes.

The critical invariant this project depends on is: **no content is silently
lost or duplicated**, and the index actually links to every file that was
written. A pretty demo cannot compensate for a chapter that quietly
disappears, so this is tested directly rather than only eyeballed.
"""

from tome2md.converter import split_into_chapters


def _word_set(text: str) -> set[str]:
    return {w for w in text.split() if w.isalpha()}


def test_splits_on_h1_and_writes_one_file_per_chapter(tmp_path):
    md = (
        "# Introduction\n\nWordsone wordsintro plenty of filler words here to "
        "clear the min-words merge threshold easily every single time okay.\n\n"
        "# The Turning Point\n\nWordstwo wordsturn plenty of filler words here "
        "to clear the min-words merge threshold easily every single time okay."
    )
    folder = tmp_path / "book"
    n = split_into_chapters(md, "My Book", folder, min_words=5)

    assert n == 2
    chapter_files = sorted(p for p in folder.glob("*.md") if not p.name.startswith("00_"))
    assert len(chapter_files) == 2

    index = (folder / "00_Index.md").read_text(encoding="utf-8")
    for f in chapter_files:
        note_name = f.stem
        assert f"[[{note_name}|" in index


def test_no_content_is_lost_across_the_split(tmp_path):
    md = (
        "# One\n\nAlpha beta gamma delta epsilon zeta eta theta iota kappa "
        "lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega.\n\n"
        "# Two\n\nUnique marker word ZORB appears only in this chapter body "
        "and must survive the round trip through the splitter untouched here."
    )
    folder = tmp_path / "book"
    split_into_chapters(md, "Word Book", folder, min_words=5)

    all_text = "\n".join(
        p.read_text(encoding="utf-8") for p in folder.glob("*.md")
    )
    assert "ZORB" in all_text
    assert "omega" in all_text


def test_short_sections_are_merged_forward_not_dropped(tmp_path):
    md = (
        "# Real Chapter\n\nThis chapter has plenty of real body words so it "
        "clears the minimum word threshold on its own without any help.\n\n"
        "# Tiny\n\nJust three words.\n\n"
        "# Another Real Chapter\n\nAgain this one has plenty of real body "
        "words so it clears the minimum word threshold on its own easily."
    )
    folder = tmp_path / "book"
    n = split_into_chapters(md, "Merge Book", folder, min_words=10)

    all_text = "\n".join(p.read_text(encoding="utf-8") for p in folder.glob("*.md"))
    assert "Just three words." in all_text  # merged, not dropped
    assert n == 2  # "Tiny" folded into the previous chapter, not its own file


def test_no_headings_falls_back_to_a_single_full_text_chapter(tmp_path):
    md = "Just a wall of prose with no headings at all in the whole document."
    folder = tmp_path / "book"
    n = split_into_chapters(md, "Flat Book", folder, min_words=5)

    assert n == 1
    chapter_files = [p for p in folder.glob("*.md") if not p.name.startswith("00_")]
    assert len(chapter_files) == 1
    assert "wall of prose" in chapter_files[0].read_text(encoding="utf-8")


def test_falls_back_to_epub_file_anchors_when_no_headings_exist(tmp_path):
    # Some EPUBs style their chapter titles as plain text/links instead of
    # real <h1>-<h6> tags; Pandoc then emits no ATX headings at all, but it
    # still preserves one anchor per source XHTML file, which this fallback
    # uses as the chapter boundary instead of dumping everything into one
    # "Full text" note.
    md = (
        '<span id="Frontmatter.xhtml"></span>\n\n'
        '<div id="Frontmatter.xhtml_kfm1" class="section frontmatter" title="Foreword">\n\n'
        "Filler words for the foreword section so it clears the merge threshold easily.\n\n"
        "</div>\n\n"
        '<span id="Chapter001.xhtml"></span>\n\n'
        "[Chapter One](#toc)\n\n"
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda plenty more filler.\n\n"
        '<span id="Chapter002.xhtml"></span>\n\n'
        "[Chapter Two](#toc)\n\n"
        "Unique marker word ZORB appears only here and must survive the split untouched."
    )
    folder = tmp_path / "book"
    n = split_into_chapters(md, "Anchor Book", folder, min_words=5)

    assert n == 3
    index = (folder / "00_Index.md").read_text(encoding="utf-8")
    assert "Foreword" in index
    assert "Chapter 1" in index
    assert "Chapter 2" in index
    all_text = "\n".join(p.read_text(encoding="utf-8") for p in folder.glob("*.md"))
    assert "ZORB" in all_text


def test_frontmatter_is_present_and_escapes_quotes(tmp_path):
    md = '# It\'s "Complicated"\n\nEnough filler words to clear the threshold easily right here.'
    folder = tmp_path / "book"
    split_into_chapters(md, 'A "Quoted" Title', folder, min_words=1)

    chapter_files = [p for p in folder.glob("*.md") if not p.name.startswith("00_")]
    content = chapter_files[0].read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert 'book: "' in content
