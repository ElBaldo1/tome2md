# Contributing to tome2md

Thanks for considering a contribution. This project is intentionally small —
one module, few dependencies — so it stays easy to read end to end before you
change it.

## Quick setup

```bash
git clone https://github.com/ElBaldo1/tome2md
cd tome2md
uv sync --all-extras   # or: pip install -e ".[all,dev]"
uv run pytest          # or: pytest
```

External tools used by some engines (optional, install what you need):
`pandoc`, `tesseract` (+ language packs), `ebook-convert` (Calibre).

## Good first contributions

- **A new chapter-heading language.** `_CHAPTER_WORDS` in
  `src/tome2md/converter.py` lists the words the plain-text engine looks for
  ("Chapter", "Capitolo", "Chapitre", ...). If your language isn't there yet,
  add it and a short test in `tests/test_text.py` proving it matches.
- **A test book that breaks the chapter splitter.** If `tome2md` merges or
  mis-splits a real book you own, open an issue describing the structure
  (or, even better, a minimal reproduction) rather than just the output —
  chapter detection is heuristic and edge cases are the most useful bug
  reports.
- **A new Pandoc-backed format.** If Pandoc already supports reading a format
  that isn't in `_PANDOC_FORMATS`, adding it is usually a one-line change
  plus a mention in the README's format table.

## Pull requests

- Keep the diff focused on one change.
- Run `pytest` before opening the PR.
- Update `README.md` if you change a default, a flag, or the supported-format
  table.
- Describe what you tested it on (which book, which format) in the PR
  description — this project has no large regression corpus, so a concrete
  "I converted X and checked Y" note is the most useful review signal.

## Reporting a bug

Please include: the file format, whether the PDF is scanned or born-digital,
the exact command you ran, and the first few lines of the error. If you can
share a minimal (non-copyrighted) reproduction file, that speeds things up a
lot.
