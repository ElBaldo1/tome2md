"""Batch converter: EPUB/PDF books -> Obsidian-friendly Markdown vaults.

Flow
----
libri-da-convertire/<book>.(pdf|epub)
    -> libri-convertiti/<book>/            one note per chapter + 00_Indice.md (+ assets/)
    -> file-originali-convertiti/<book>.*  original archived on success

Engines (auto-selected, override with --engine):
  * pdf-text : born-digital PDF. Chapter structure comes from the PDF bookmarks
               (or font size, as fallback); spacing is rebuilt from glyph
               geometry so tightly-justified lines don't lose their spaces.
  * pdf-ocr  : scanned PDF -> Tesseract (ita+eng).
  * epub     : Pandoc -> Markdown (real ATX headings, media extracted).
  * epub-ocr : image-only "fake" EPUB -> Tesseract.
  * docling  : opt-in (--engine docling). Best quality (LaTeX formulas, tables,
               layout) but very slow on CPU: budget ~30-60 s per page.

Run with no arguments to process everything in libri-da-convertire/.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pymupdf
from PIL import Image
from tqdm import tqdm

try:
    import pytesseract
except ImportError:  # optional, only needed for the OCR fallbacks
    pytesseract = None

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "libri-da-convertire"
OUTPUT_DIR = BASE_DIR / "libri-convertiti"
PROCESSED_DIR = BASE_DIR / "file-originali-convertiti"

for _d in (INPUT_DIR, OUTPUT_DIR, PROCESSED_DIR):
    _d.mkdir(exist_ok=True)

SUPPORTED_EXT = {".pdf", ".epub"}

# --------------------------------------------------------------------------- #
# Text normalization
# --------------------------------------------------------------------------- #

_LIGATURES = str.maketrans(
    {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}
)


def normalize_text(text: str) -> str:
    """Repair the usual PDF/Pandoc artefacts without deleting any content."""
    text = text.translate(_LIGATURES)
    text = unicodedata.normalize("NFC", text)

    # Pandoc leftovers (attribute spans / fenced divs), never real content.
    text = re.sub(r"\[\]\{#[^}]*\}", "", text)
    text = re.sub(r"\{[.#][^{}]*\}", "", text)
    text = re.sub(r"^:::+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\[\]\s*$", "", text, flags=re.MULTILINE)

    # De-hyphenate words broken across a line wrap: "defi-\nnition" -> "definition".
    text = re.sub(r"([A-Za-zÀ-ÿ])-\n([a-zà-ÿ])", r"\1\2", text)

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_title_noise(stem: str) -> str:
    """Drop the z-library / mirror junk that trails most of these filenames."""
    cleaned = re.sub(
        r"\s*\((?:z-library\.sk|1lib\.sk|z-lib\.sk|libgen[^)]*)(?:,\s*[^)]*)*\)",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -_")
    return cleaned or stem


def safe_filename(name: str, limit: int = 50) -> str:
    name = re.sub(r'[\\/*?:"<>|■◆▪·]+', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:limit].strip(" .-") or "senza-titolo"


# --------------------------------------------------------------------------- #
# Chapter splitting  (shared by every engine – all of them emit ATX headings)
# --------------------------------------------------------------------------- #

_H_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*#*$")


def split_into_chapters(markdown: str, book_title: str, book_folder: Path, *, min_words: int) -> int:
    """Split on the top heading level that yields >=2 sections; merge tiny ones forward."""
    book_folder.mkdir(parents=True, exist_ok=True)
    lines = markdown.splitlines()

    heading_levels = sorted(
        {len(m.group(1)) for ln in lines if (m := _H_RE.match(ln)) and len(m.group(1)) <= 3}
    )
    split_level = None
    for lvl in heading_levels:
        if sum(1 for ln in lines if (m := _H_RE.match(ln)) and len(m.group(1)) == lvl) >= 2:
            split_level = lvl
            break

    sections: list[tuple[str, list[str]]] = []
    if split_level is None:
        sections.append(("Testo completo", lines))
    else:
        current_title, buf = "Preambolo", []
        for ln in lines:
            m = _H_RE.match(ln)
            if m and len(m.group(1)) == split_level:
                if buf or sections:
                    sections.append((current_title, buf))
                current_title, buf = m.group(2).strip(), []
            else:
                buf.append(ln)
        sections.append((current_title, buf))

    # Merge sub-threshold sections into the previous one so nothing is lost.
    merged: list[tuple[str, str]] = []
    for title, buf in sections:
        body = "\n".join(buf).strip()
        if not body and not title:
            continue
        if merged and len(body.split()) < min_words:
            prev_title, prev_body = merged[-1]
            merged[-1] = (prev_title, f"{prev_body}\n\n## {title}\n\n{body}".rstrip())
        else:
            merged.append((title, body))
    if not merged:
        merged = [("Testo completo", markdown.strip())]

    index = [f"# {book_title}", "", "## Indice dei capitoli", ""]
    for idx, (title, body) in enumerate(merged, start=1):
        slug = safe_filename(title)
        note_name = f"{idx:02d}_{slug}"
        (book_folder / f"{note_name}.md").write_text(
            _frontmatter(book_title, title, idx) + f"# {title}\n\n{body}\n",
            encoding="utf-8",
        )
        index.append(f"- [[{note_name}|{title}]]")

    (book_folder / "00_Indice.md").write_text(
        _frontmatter(book_title, "Indice", 0) + "\n".join(index) + "\n", encoding="utf-8"
    )
    return len(merged)


def _frontmatter(book_title: str, chapter_title: str, order: int) -> str:
    esc = lambda s: s.replace('"', "'")
    return (
        "---\n"
        f'book: "{esc(book_title)}"\n'
        f'chapter: "{esc(chapter_title)}"\n'
        f"order: {order}\n"
        f'converted: "{date.today().isoformat()}"\n'
        "tags:\n  - source/book\n"
        "---\n\n"
    )


# --------------------------------------------------------------------------- #
# Engine: born-digital PDF  (font-size aware, "words" mode text)
# --------------------------------------------------------------------------- #

_BARE_NUM = re.compile(r"^\s*(\d{1,3}|[IVXLCDM]{1,7})\s*$")
_SUBSUB = re.compile(r"^\s*\d+\.\d+\.\d+\b")
_SECTION = re.compile(r"^\s*\d+\.\d+\b")


def _dominant_size(doc, sample: int = 60) -> float:
    weight: Counter[float] = Counter()
    for i in range(min(sample, len(doc))):
        for block in doc[i].get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        weight[round(span["size"], 1)] += len(span["text"])
    return weight.most_common(1)[0][0] if weight else 10.0


def _rebuild_line(chars: list[tuple[float, float, str]], factor: float = 0.25) -> str:
    """Reconstruct a text line from glyph boxes, inserting spaces from geometry.

    pymupdf drops spaces on tightly-justified lines in this book; comparing the
    gap between consecutive glyphs to the median glyph width recovers them.
    """
    if not chars:
        return ""
    widths = sorted(b - a for a, b, c in chars if c.strip())
    med = widths[len(widths) // 2] if widths else 4.0
    out = [chars[0][2]]
    for (a0, a1, _), (b0, b1, c1) in zip(chars, chars[1:]):
        if b0 - a1 > med * factor and out[-1] not in (" ", "") and c1 != " ":
            out.append(" ")
        out.append(c1)
    return re.sub(r"\s{2,}", " ", "".join(out)).strip()


def _join_block(texts: list[str]) -> str:
    """Join the lines of a paragraph, healing words hyphenated at the line break."""
    buf = ""
    for t in texts:
        if not buf:
            buf = t
        elif re.search(r"[A-Za-zà-ÿ]-$", buf) and t[:1].islower():
            buf = buf[:-1] + t
        else:
            buf += " " + t
    return buf


def _fix_glyph(ch: str, font: str) -> str:
    """Repair unambiguously broken cmap entries in the fonts this book uses.

    Note: the MathType 'MTSYN' font also renders both negation and disjunction
    through the '!' slot with no reliable way to tell them apart, so those are
    left as a literal '!' (grep-able) rather than guessed. Use --engine docling
    for faithful math symbols.
    """
    if "ZapfDingbat" in font and ch == "I":
        return "∎"  # end-of-proof box
    return ch


def _iter_lines(page):
    """Yield (block_no, text, max_size, font, y_top) for every text line, in reading order."""
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        bno = block["number"]
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if any(c["c"].strip() for c in s["chars"])]
            if not spans:
                continue
            chars = [
                (c["bbox"][0], c["bbox"][2], _fix_glyph(c["c"], s["font"]))
                for s in spans
                for c in s["chars"]
            ]
            text = _rebuild_line(chars)
            if not text:
                continue
            size = max(s["size"] for s in spans)
            font = Counter(s["font"] for s in spans).most_common(1)[0][0]
            yield bno, text, size, font, line["bbox"][1]


def _collect_running_headers(doc, sample_pages) -> set[str]:
    """Edge-of-page lines that recur across many pages are headers/footers."""
    seen: Counter[str] = Counter()
    for i in sample_pages:
        page = doc[i]
        h = page.rect.height
        for _bno, text, _size, _font, y in _iter_lines(page):
            if (y < h * 0.11 or y > h * 0.92) and not text.isdigit():
                seen[text.lower()] += 1
    threshold = max(6, int(len(sample_pages) * 0.05))
    return {t for t, c in seen.items() if c >= threshold}


_BOILERPLATE = {"this page intentionally left blank"}


def _toc_headings_by_page(doc, valid_pages: set[int]) -> dict[int, list[str]]:
    """Use the PDF bookmarks as the authoritative chapter structure, when usable."""
    toc = [e for e in doc.get_toc() if e[1].strip().lower() not in {"cover", "title page"}]
    if len(toc) < 3:
        return {}
    target_pages = [p - 1 for _, _, p in toc]
    # A broken/placeholder TOC piles many entries on one early page – don't trust it.
    if max(Counter(target_pages).values()) > 2:
        return {}
    by_page: dict[int, list[str]] = {}
    for level, title, page in toc:
        if (page - 1) not in valid_pages:
            continue
        hashes = "#" if level <= 1 else "##" if level == 2 else "###"
        by_page.setdefault(page - 1, []).append(f"{hashes} {title.strip()}")
    return by_page


def pdf_text_to_markdown(doc, assets_dir: Path, *, book_title: str, page_range=None) -> str:
    body_size = _dominant_size(doc)
    rng = range(*page_range) if page_range else range(len(doc))
    page_list = list(rng)
    headers = _collect_running_headers(doc, page_list[:400])
    title_norm = re.sub(r"\s+", " ", book_title).strip().lower()
    toc_headings = _toc_headings_by_page(doc, set(page_list))
    toc_titles = {h.lstrip("# ").lower() for hs in toc_headings.values() for h in hs}

    out: list[str] = []
    seen_xrefs: set[int] = set()
    xref_pages: Counter[int] = Counter()
    for i in page_list:
        for img in doc[i].get_images(full=True):
            xref_pages[img[0]] += 1

    for i in tqdm(page_list, desc="PDF -> Markdown", unit="pag"):
        page = doc[i]
        h = page.rect.height

        for heading in toc_headings.get(i, []):
            out.append(f"\n{heading}\n")

        blocks: OrderedDict = OrderedDict()
        for bno, text, size, font, y in _iter_lines(page):
            norm = text.lower()
            near_edge = y < h * 0.11 or y > h * 0.92
            if near_edge and (norm in headers or re.fullmatch(r"[\divxlcdm]{1,9}", norm)):
                continue
            if norm in _BOILERPLATE:
                continue
            blocks.setdefault(bno, []).append((text, size, font))

        for entries in blocks.values():
            joined = _join_block([e[0] for e in entries]).strip()
            max_size = max(e[1] for e in entries)
            first_text, first_size, first_font = entries[0]
            is_sans = any(k in first_font for k in ("Myriad", "Helvetica", "Arial"))
            title_line = " ".join(e[0] for e in entries if not _BARE_NUM.match(e[0])).strip()

            is_big = max_size >= body_size * 1.6
            has_words = bool(re.search(r"[A-Za-zÀ-ÿ]{2,}.*[a-zà-ÿ]", joined))  # letters incl. a lowercase
            tl = title_line.lower()

            # Junk that dresses up as a heading: title-page metadata, bare numbers/years.
            if not has_words:
                out.append(joined)
                continue
            # A big-font line that only echoes the book title is a part-divider, not a chapter.
            if is_big and len(tl) >= 10 and (tl in title_norm or title_norm.startswith(tl)):
                continue
            # Already placed from the table of contents – don't emit it twice.
            if toc_titles and re.sub(r"\s+", " ", joined).lower() in toc_titles:
                continue

            if is_big and title_line and len(entries) <= 3 and not toc_headings:
                number = next((e[0] for e in entries if _BARE_NUM.match(e[0])), "")
                out.append(f"\n# {f'{number} {title_line}'.strip()}\n")
            elif _SUBSUB.match(first_text) and len(entries) <= 2 and len(joined.split()) <= 16:
                out.append(f"\n### {joined}\n")
            elif (
                _SECTION.match(first_text)
                or (is_sans and body_size * 1.03 < first_size < body_size * 1.6 and i >= 5)
            ) and len(entries) <= 2 and len(joined.split()) <= 16:
                out.append(f"\n## {joined}\n")
            else:
                out.append(joined)

        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen_xrefs or xref_pages[xref] > 5:
                continue
            seen_xrefs.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if info.get("width", 0) < 64 or info.get("height", 0) < 64:
                continue
            assets_dir.mkdir(parents=True, exist_ok=True)
            fname = f"p{i + 1:04d}_{xref}.{info['ext']}"
            (assets_dir / fname).write_bytes(info["image"])
            out.append(f"\n![](assets/{fname})\n")

    return normalize_text("\n\n".join(p for p in out if p.strip()))


# --------------------------------------------------------------------------- #
# Engine: OCR  (scanned PDF or image-only EPUB)
# --------------------------------------------------------------------------- #

_TESS_CONFIG = r"--oem 1 -l ita+eng"


def _ocr_images(images: list[Image.Image], workers: int) -> str:
    if pytesseract is None:
        raise RuntimeError("pytesseract non installato: impossibile fare OCR.")
    if not shutil.which("tesseract"):
        raise RuntimeError("Binario 'tesseract' non trovato nel PATH.")

    def run(img):
        return pytesseract.image_to_string(img, config=_TESS_CONFIG)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pages = list(tqdm(pool.map(run, images), total=len(images), desc="OCR", unit="pag"))
    return "\n\n".join(pages)


def _grayscale_downscale(img: Image.Image, max_width: int = 1600) -> Image.Image:
    img = img.convert("L")
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.Resampling.LANCZOS)
    return img


def pdf_ocr_to_markdown(doc, *, dpi: int, workers: int, page_range=None) -> str:
    rng = range(*page_range) if page_range else range(len(doc))
    images = []
    for i in tqdm(rng, desc="Rendering PDF", unit="pag"):
        pix = doc[i].get_pixmap(dpi=dpi)
        images.append(_grayscale_downscale(Image.frombytes("RGB", [pix.width, pix.height], pix.samples)))
    return normalize_text(_ocr_images(images, workers))


def epub_images_to_markdown(epub_path: Path, workers: int) -> str:
    images = []
    with zipfile.ZipFile(epub_path) as archive:
        names = sorted(f for f in archive.namelist() if f.lower().endswith((".png", ".jpg", ".jpeg")))
        for name in tqdm(names, desc="Estrazione immagini EPUB", unit="img"):
            images.append(_grayscale_downscale(Image.open(io.BytesIO(archive.read(name)))))
    if not images:
        raise RuntimeError("Nessuna immagine trovata nell'EPUB.")
    return normalize_text(_ocr_images(images, workers))


# --------------------------------------------------------------------------- #
# Engine: EPUB via Pandoc
# --------------------------------------------------------------------------- #


def epub_to_markdown(epub_path: Path, assets_dir: Path) -> str:
    if not shutil.which("pandoc"):
        raise RuntimeError("Binario 'pandoc' non trovato nel PATH.")
    assets_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "pandoc", str(epub_path),
            "-f", "epub", "-t", "gfm",
            "--wrap=none", "--markdown-headings=atx",
            f"--extract-media={assets_dir}",
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc: {proc.stderr.strip()[:400]}")
    return normalize_text(proc.stdout)


# --------------------------------------------------------------------------- #
# Engine: docling  (opt-in, slow, best quality)
# --------------------------------------------------------------------------- #


def docling_to_markdown(src: Path, assets_dir: Path, *, formulas: bool, page_range=None) -> str:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.do_formula_enrichment = formulas
    opts.generate_picture_images = True

    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    kwargs = {}
    if page_range:
        kwargs["page_range"] = (page_range[0] + 1, page_range[1])  # docling is 1-based, inclusive
    result = conv.convert(src, **kwargs)

    assets_dir.mkdir(parents=True, exist_ok=True)
    try:
        result.document.save_all_figures(assets_dir)  # best effort, API varies by version
    except Exception:
        pass
    return normalize_text(result.document.export_to_markdown())


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _looks_born_digital(doc, page_range=None) -> bool:
    rng = list(range(*page_range)) if page_range else range(len(doc))
    probe = list(rng)[: min(12, len(list(rng)))]
    chars = sum(len(doc[i].get_text().strip()) for i in probe)
    return chars > 200 * max(1, len(probe))


def convert_file(src: Path, book_folder: Path, book_title: str, args) -> str:
    """Return Markdown for *src* (always with ATX headings). Raises on failure."""
    assets_dir = book_folder / "assets"
    ext = src.suffix.lower()
    page_range = _parse_pages(args.pages)

    if args.engine == "docling":
        return docling_to_markdown(src, assets_dir, formulas=args.formulas, page_range=page_range)

    if ext == ".pdf":
        with pymupdf.open(str(src)) as doc:
            scanned = args.engine == "ocr" or not _looks_born_digital(doc, page_range)
            if scanned:
                print("  -> PDF scansionato: OCR Tesseract")
                return pdf_ocr_to_markdown(doc, dpi=args.dpi, workers=args.workers, page_range=page_range)
            print("  -> PDF vettoriale: estrazione testo + struttura da font")
            return pdf_text_to_markdown(doc, assets_dir, book_title=book_title, page_range=page_range)

    if ext == ".epub":
        md = epub_to_markdown(src, assets_dir)
        if len(md) < 1000:
            print("  -> [WARNING] EPUB solo-immagini: passo a OCR")
            return epub_images_to_markdown(src, args.workers)
        return md

    raise RuntimeError(f"Formato non supportato: {ext}")


def _parse_pages(spec: str | None):
    if not spec:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", spec)
    if not m:
        raise SystemExit("--pages richiede il formato N-M (es. 20-80)")
    return (int(m.group(1)) - 1, int(m.group(2)))


def _archive(src: Path) -> Path:
    dest = PROCESSED_DIR / src.name
    if dest.exists():
        dest = PROCESSED_DIR / f"{src.stem}_{date.today().isoformat()}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=["auto", "ocr", "docling"], default="auto")
    parser.add_argument("--pages", metavar="N-M", help="converti solo l'intervallo di pagine (test)")
    parser.add_argument("--formulas", action="store_true", help="docling: riconosci le formule in LaTeX (lento)")
    parser.add_argument("--dpi", type=int, default=200, help="risoluzione rendering per l'OCR")
    parser.add_argument("--workers", type=int, default=4, help="thread paralleli per l'OCR")
    parser.add_argument("--min-words", type=int, default=120, help="soglia per accorpare sezioni brevi")
    parser.add_argument("--raw-title", action="store_true", help="non ripulire il nome file dai suffissi mirror")
    parser.add_argument("--keep-going", action="store_true", help="non fermarti al primo errore")
    parser.add_argument("--no-archive", action="store_true", help="non spostare l'originale a fine conversione")
    args = parser.parse_args(argv)

    files = sorted(f for f in INPUT_DIR.iterdir() if f.is_file() and not f.name.startswith("."))
    files = [f for f in files if f.suffix.lower() in SUPPORTED_EXT]
    if not files:
        print("Nessun file .pdf/.epub in 'libri-da-convertire'.")
        return 0

    print(f"Trovati {len(files)} file.\n")
    failures = 0
    for src in files:
        if not src.exists():  # moved/renamed between listing and now
            continue
        book_title = src.stem if args.raw_title else strip_title_noise(src.stem)
        book_folder = OUTPUT_DIR / safe_filename(book_title, limit=120)
        print(f"### {src.name}")
        if book_folder.exists():
            shutil.rmtree(book_folder)  # start clean: no stale notes from a previous run
        try:
            markdown = convert_file(src, book_folder, book_title, args)
            if len(markdown.strip()) < 200:
                raise RuntimeError("output vuoto o troppo corto")
            n = split_into_chapters(markdown, book_title, book_folder, min_words=args.min_words)
            dest = None if args.no_archive else _archive(src)
            print(f"[OK] {n} capitoli in {book_folder.relative_to(BASE_DIR)}/")
            if dest:
                print(f"[OK] originale archiviato: {dest.relative_to(BASE_DIR)}\n")
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            if book_folder.exists():
                shutil.rmtree(book_folder, ignore_errors=True)
            print(f"[FAIL] {src.name}: {exc}\n")
            if not args.keep_going:
                return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
