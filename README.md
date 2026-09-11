# files-converter

Converte libri **PDF / EPUB** in un vault Markdown pronto per Obsidian: una nota
per capitolo + un indice con i wikilink.

```
libri-da-convertire/  <book>.pdf | <book>.epub        ← metti qui i file
        │
        ▼   uv run converti_libri.py
libri-convertiti/     <book>/00_Indice.md
                      <book>/01_<capitolo>.md ...
                      <book>/assets/           ← immagini estratte
file-originali-convertiti/  <book>.*           ← originale spostato a fine conversione
```

## Uso

```bash
uv run converti_libri.py                 # converte tutto ciò che è in libri-da-convertire/
uv run converti_libri.py --pages 20-80   # solo un intervallo (per fare prove veloci)
uv run converti_libri.py --no-archive    # non spostare l'originale
uv run converti_libri.py --engine docling --formulas   # formule in LaTeX (lento: ~30-60 s/pagina)
```

Opzioni utili: `--engine {auto,ocr,docling}`, `--workers N` (OCR), `--dpi N` (OCR),
`--min-words N` (accorpa sezioni più corte di N parole), `--raw-title`
(non ripulire il nome file dai suffissi dei mirror), `--keep-going`.

## Come sceglie il motore

| Input | Motore | Note |
|-------|--------|------|
| PDF con testo (nativo) | estrazione diretta | struttura dai **bookmark** del PDF, altrimenti dalla dimensione dei font. Spaziatura ricostruita dalla geometria dei glifi. |
| PDF scansionato | Tesseract OCR (`ita+eng`) | rilevato in automatico; forzabile con `--engine ocr` |
| EPUB | Pandoc → Markdown | heading reali, immagini estratte con `--extract-media` |
| EPUB solo-immagini | Tesseract OCR | fallback automatico |
| qualsiasi | `--engine docling` | massima qualità: formule LaTeX, tabelle, layout. Molto lento su CPU. |

## Limiti noti del motore veloce

- Le **formule** restano testo Unicode (`∀x ∈ N`), non LaTeX. Per il LaTeX usare `--engine docling`.
- Le **tabelle** vengono appiattite a testo (nessuna struttura di tabella da PDF nativo).
- Le **figure vettoriali** (grafici, diagrammi) non vengono estratte; solo le immagini raster.
- Alcuni font matematici (MathType) mappano male i glifi: la negazione e la
  disgiunzione possono comparire come `!`. `docling` li rende correttamente.

## Requisiti

- Python + [uv](https://docs.astral.sh/uv/), dipendenze nel `.venv` (`pymupdf`, `pillow`, `tqdm`, `pytesseract`, `docling`).
- Binari esterni nel PATH: **pandoc** (EPUB), **tesseract** con i pacchetti lingua `ita` e `eng` (OCR).
