# PDF and Office to EPUB converter

This project converts PDF, DOCX, ODT, RTF, and DOC documents into reflowable EPUB3 files using a staged pipeline:

1. Ingest: extract PDF text, images, tables, and page metrics with pypdfium2, or route office input through DOCX/Pandoc/LibreOffice.
2. Structure analysis: detect covers and title pages, reconstruct reading order, identify deficient OCR pages, and filter recurring noise.
3. Semantics + packaging: create accessible EPUB3 XHTML and a conversion audit report.

## Key modules

- `extractor.py`: PDF text, image, table, and page-metric extraction using pypdfium2.
- `cover.py`: cover/title-page classification and PDFium-backed cover rendering.
- `ocr.py`: optional OCR engine adapters; install `rapidocr-onnxruntime` or `pytesseract` to enable OCR.
- `telemetry.py`: word accounting, dropped-region tracking, and report.json generation.
- `office_extractor.py`: DOCX semantic extraction and Pandoc-backed ODT/RTF/DOC ingestion.
- `router.py`: extension-based unified ingestion into `CleanedDocument`.
- `layout.py`: geometry statistics, XY-cut reading order, and margin-artifact detection.
- `cleaner.py`: normalizes text, filters noise, removes repeated headers/footers, detects headings, and groups content into reading order.
- `builder.py`: creates accessible EPUB content, including tables, linked notes, cover/title pages, and deterministic packaging.
- `epub_builder.py`: compatibility import shim for the builder.
- `pipeline.py`: orchestration layer for a single conversion pipeline.
- `main.py`: CLI entrypoint.

## Usage

Single file:

```bash
python main.py --input "G:\Book Done.pdf" --output "G:\Book Done.epub"
python main.py --input "G:\manuscript.docx" --output "G:\manuscript.epub"
python main.py --input "G:\Book Done.pdf" --output "G:\Book Done.epub" --report "G:\Book Done.audit.json"
```

Batch-style use from Python:

```python
from pipeline import convert_pdf

output = convert_pdf("G:\manuscript.docx", "G:\manuscript.epub")
print(output)
```

## Quality notes

The current pipeline is designed for reflowable EPUB3 output and focuses on common document conversion problems:

- multi-column layout ordering
- chapter and section heading detection
- redundant header/footer removal
- image deduplication and responsive sizing in XHTML
- DOCX embedded-image extraction and Pandoc-backed office conversion
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

- scanned pages when no optional OCR engine is installed or OCR cannot recover text
- complex tables with merged cells or rotated text
- mathematical expressions and code blocks that need richer semantic markup
- RTL scripts and mixed-language layout tuning
- very large image-heavy books, where memory and image compression need more tuning

For those cases, the next recommended iteration is OCR + layout analysis (Tesseract, OCRmyPDF, or a more advanced zone-based parser) before final EPUB packaging.
