# PDF and Office to EPUB converter

This project converts PDF, DOCX, ODT, RTF, and DOC documents into reflowable EPUB3 files using a staged pipeline:

1. Ingest: extract page-level PDF structure or route office input through the DOCX/Pandoc extractors.
2. Structure analysis: classify covers and title pages, reconstruct reading order, tables, notes, code, and inline typography; OCR is routed only to pages with insufficient text.
3. Semantics + packaging: create accessible EPUB 3 XHTML, deterministic ZIP packaging, and a conversion audit report.

## Key modules

- `extractor.py`: PDF text, images, vector diagrams, tables, and per-page OCR quality signals.
- `office_extractor.py`: DOCX semantic extraction and Pandoc-backed ODT/RTF/DOC ingestion.
- `router.py`: extension-based unified ingestion into `CleanedDocument`.
- `layout.py`: geometry statistics, column classification, and margin-artifact detection.
- `cleaner.py`: normalizes text, filters noise, removes repeated headers/footers, detects headings, and groups content into reading order.
- `builder.py`: creates deterministic EPUB 3 packages, accessible navigation, and a11y metadata.
- `cover.py`: distinguishes graphical covers from typographic title pages and creates a shelf-cover fallback.
- `inline.py`: preserves bold, italic, superscript, subscript, and footnote-reference semantics.
- `telemetry.py`: word recall, dropped-content audit, and `report.json` generation.
- `epubcheck_harvester.py`: selects and downloads the matching EPUBCheck release archive from GitHub metadata.
- `epub_builder.py`: compatibility import shim for the builder.
- `pipeline.py`: orchestration layer for a single conversion pipeline.
- `main.py`: CLI entrypoint.

## Usage

Single file:

```bash
python main.py --input "Book.pdf" --output "Book.epub"
python main.py --input "manuscript.docx" --output "manuscript.epub"
python main.py --input "Book.pdf" --output "Book.epub" --report "audit.json"
```

Batch-style use from Python:

```python
from pipeline import convert_pdf

output = convert_pdf("manuscript.docx", "manuscript.epub")
print(output)
```

## Quality notes

The current pipeline is designed for reflowable EPUB3 output and focuses on common document conversion problems:

- multi-column layout ordering
- chapter and section heading detection
- redundant header/footer removal
- image deduplication and responsive sizing in XHTML
- graphic-cover/title-page disambiguation and semantic title-page markup
- page-level OCR fallback for sparse or badly mapped text
- footnote links, structured tables, vector figure crops, and inline typography
- deterministic EPUB archives and per-conversion word-recall reports
- DOCX embedded-image extraction and Pandoc-backed office conversion
- EPUB validation with `epubcheck`

Each conversion writes `<output-stem>.report.json` beside the EPUB unless `--report` is supplied. The report includes input/output word counts, recall, dropped pages and regions, cover confidence, detected structures, and runtime/OCR diagnostics. A non-scanned PDF conversion exits with status 1 after writing artifacts if word recall is below 0.98. Title pages and footnotes retain their `epub:type` semantics; supported ARIA roles are used so the XHTML remains EPUBCheck-valid.

For OCR, install either optional engine:

```bash
python -m pip install rapidocr-onnxruntime
# or
python -m pip install pytesseract
```

`pytesseract` also requires the Tesseract executable to be installed. `qa_loop.py` and `run_broad_validation.py` use the EPUBCheck release harvester; EPUBCheck is downloaded from the matching `epubcheck-*.zip` GitHub release asset when no local executable is available.
