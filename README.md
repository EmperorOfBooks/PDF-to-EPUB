# PDF to EPUB converter

This project converts standard PDF books into reflowable EPUB3 files using a staged pipeline:

1. Ingest: parse the PDF with PyMuPDF and collect text blocks, lines, and images.
2. Structure analysis: detect heading candidates, repeated headers/footers, and multi-column reading order.
3. Semantics + packaging: build XHTML chapter files, preserve image figures, and package them into EPUB3.

## Key modules

- `extractor.py`: raw PDF extraction from PyMuPDF, including text and bitmap image capture.
- `cleaner.py`: normalizes text, filters noise, removes repeated headers/footers, detects headings, and groups content into reading order.
- `epub_builder.py`: creates EPUB chapter XHTML, image figure markup, and EPUB manifest/spine metadata.
- `pipeline.py`: orchestration layer for a single conversion pipeline.
- `main.py`: CLI entrypoint.

## Usage

Single file:

```bash
python main.py --input "G:\Book Done.pdf" --output "G:\Book Done.epub"
```

Batch-style use from Python:

```python
from pipeline import convert_pdf

output = convert_pdf("G:\Book Done.pdf", "G:\Book Done.epub")
print(output)
```

## Quality notes

The current pipeline is designed for reflowable EPUB3 output and focuses on the most common book-PDF problems:

- multi-column layout ordering
- chapter and section heading detection
- redundant header/footer removal
- image deduplication and responsive sizing in XHTML
- EPUB validation with `epubcheck`

## Before / after

Before:
- page-height sorting could misorder left/right columns
- image markup was bare and rigid
- fallback handling for text blocks without line metadata was brittle

After:
- column-aware ordering keeps left-column reading sequence intact
- semantic XHTML wraps chapters in `<section>` markup
- figures use responsive `max-width`, `aspect-ratio`, and accessible captions
- duplicate images are filtered before packaging

## Remaining edge cases

The converter is still limited in a few areas:

- heavily scanned PDFs without OCR text layers
- complex tables with merged cells or rotated text
- mathematical expressions and code blocks that need richer semantic markup
- RTL scripts and mixed-language layout tuning
- very large image-heavy books, where memory and image compression need more tuning

For those cases, the next recommended iteration is OCR + layout analysis (Tesseract, OCRmyPDF, or a more advanced zone-based parser) before final EPUB packaging.
