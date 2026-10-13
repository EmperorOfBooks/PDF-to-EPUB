# PDF and Office to EPUB Converter

A high-performance, security-hardened document conversion engine that transforms PDF, DOCX, ODT, RTF, and DOC documents into accessible, reflowable EPUB3 publications. Accelerated with a native Rust core (`pdf_to_epub_core`) using PyO3 and Rayon, and engineered for IngramSpark and W3C EPUBCheck compliance.

---

## Architecture & Staged Pipeline

The conversion pipeline operates across five deterministic stages:

```
[ Input Document ]
        │
        ▼ (Stage 1)
[ Ingestion & Archive Defense ] ── Safe zip parsing, defused XML, pypdfium2 extraction
        │
        ▼ (Stage 2)
[ Spatial Layout & Rust Core ]  ── Multi-column sort, Rayon parallel geometry, table grid
        │
        ▼ (Stage 3)
[ Content Cleaning & Semantics ] ── Heading detection, boilerplate filtering, footnote linking
        │
        ▼ (Stage 4)
[ EPUB3 Generation & Styling ]  ── Semantic HTML5, CSS typography, ARIA landmarks, NCX/Nav
        │
        ▼ (Stage 5)
[ Deterministic Packaging ]     ── Uncompressed mimetype, safe OCF archive, audit report
        │
        ▼
[ Validated EPUB3 Output ]
```

1. **Ingestion & Archive Defense**: Extracts text spans, native bitmaps, vector illustrations, and layout geometry from PDF documents via `pypdfium2`, or routes office formats (`.docx`, `.odt`, `.rtf`, `.doc`) through semantic OOXML parsers, Pandoc, and LibreOffice. All archive extractions enforce strict Zip Slip and decompression bomb validation.
2. **Spatial Layout & Rust Acceleration**: High-performance multi-column detection, topological reading order sorting, and table cell coordinate grid snapping executed in Rust via `pdf_to_epub_core`. Heavy geometry computations run across CPU threads via Rayon with the Python GIL released (`py.allow_threads`).
3. **Content Cleaning & Semantics**: Normalizes text encodings with `ftfy`, resolves discretionary hyphenation with `pyphen`, filters repeating running headers/footers, pairs footnotes and note references, detects chapter structures, and preserves verse/poetry formatting.
4. **Accessible EPUB3 Semantic Assembly**: Emits clean HTML5 with ARIA roles (`role="doc-chapter"`, `role="doc-toc"`, `role="doc-pagebreak"`), landmark navigation (`epub:type="landmarks"`), NCX backward compatibility (`toc.ncx`), and responsive, book-grade CSS typography.
5. **Deterministic Packaging & Validation**: Writes uncompressed `mimetype` first-entry archives according to the IDPF Open Container Format (OCF), verified against W3C EPUBCheck standards. Generates comprehensive `report.json` telemetry detailing word recall, dropped regions, and layout branches.

---

## Key Modules

| Module | Language | Description |
| :--- | :--- | :--- |
| `src/` (`pdf_to_epub_core`) | Rust | Native extension for spatial reading order, Rayon parallel layout analysis, table grid reconstruction, and safe OCF packaging. |
| `pdf_to_epub/` | Python | Package namespace hosting CLI entrypoint (`cli.py`) and type stubs (`pdf_to_epub_core.pyi`). |
| [`rust_bridge.py`](rust_bridge.py) | Python | Bridge module routing layout operations to Rust with type-annotated pure-Python fallbacks. |
| [`archive_security.py`](archive_security.py) | Python | Zip Slip prevention, path canonicalization, and decompression bomb defense (size and ratio limits). |
| [`extractor.py`](extractor.py) | Python | PDF extraction layer parsing text spans, fonts, bounding boxes, and images with `pypdfium2`. |
| [`cleaner.py`](cleaner.py) | Python | Pipeline cleaner normalizing text, detecting chapter breaks, isolating verse, and pairing notes. |
| [`builder.py`](builder.py) | Python | Accessible EPUB3 builder generating package manifests, navigation documents, stylesheets, and XHTML chapters. |
| [`layout.py`](layout.py) | Python | Geometry heuristics, XY-cut spatial analysis, and margin artifact detection. |
| [`office_extractor.py`](office_extractor.py) | Python | OOXML/DOCX semantic parser and Pandoc/LibreOffice office format ingestion with defused XML. |
| [`cover.py`](cover.py) | Python | Graphic cover and typographical title page classification and PDFium rendering. |
| [`ocr.py`](ocr.py) | Python | Optional OCR fallback adapter for scanned pages (RapidOCR or Tesseract) with isolated subprocess execution. |
| [`telemetry.py`](telemetry.py) | Python | Conversion auditing, word recall gates, dropped content accounting, and sanitized execution metrics. |
| [`pipeline.py`](pipeline.py) | Python | End-to-end pipeline orchestrator managing ingestion, cleaning, building, and reporting. |
| [`main.py`](main.py) | Python | Command-line interface and entrypoint. |

---

## Security Hardening

- **Decompression Bomb Defense**: Enforces strict caps on uncompressed data (250 MB total archive limit, 50 MB per individual entry, and maximum 100:1 compression ratio). Streams are read in bounded 64 KB chunks to protect against deceptive archive headers.
- **Zip Slip Prevention**: Canonicalizes all archive entry paths, rejecting directory traversal attempts (`..`), absolute paths, and drive-letter prefixes before extraction or packaging.
- **XML Parsing Hardening**: Enforces safe XML handling with `defusedxml.defuse_stdlib()`, preventing external entity expansion (XXE), billion laughs attacks, DTD retrieval, and external schema access.
- **Subprocess Sanitization**: All external tools (Tesseract, LibreOffice, Java/EPUBCheck) execute with `shell=False`, argument lists, UUID4-generated filenames in isolated temporary directories, and strict timeouts.
- **ReDoS Protection**: Audited regular expressions with bounded repetitions and line-by-line processing prevent catastrophic backtracking on hostile or malformed input text.
- **Telemetry Sanitization**: Strips local usernames and directory paths from emitted `report.json` telemetry to ensure safe audit log distribution.

---

## Installation & Setup

### Prerequisites
- Python 3.10 or newer
- Rust toolchain (Cargo and rustc, edition 2021) for compiling native extensions
- *(Optional)* Java JRE 8+ for running EPUBCheck
- *(Optional)* Tesseract OCR or LibreOffice for scanned PDFs or legacy `.doc` files

### Install Dependencies
```bash
# Clone the repository
git clone https://github.com/EmperorOfBooks/PDF-to-EPUB.git
cd PDF-to-EPUB

# Install Python requirements
pip install -r requirements.txt

# Compile and install the Rust native extension
maturin develop
```

Or install the package directly:
```bash
pip install .
```

---

## Usage

### Command-Line Interface

Convert a document using the CLI script:
```bash
# Using the installed script entrypoint
pdf-to-epub --input "manuscript.pdf" --output "book.epub"

# Or running directly via Python
python main.py --input "manuscript.pdf" --output "book.epub"
```

Office documents are converted directly:
```bash
pdf-to-epub --input "document.docx" --output "document.epub"
pdf-to-epub --input "draft.odt" --output "draft.epub"
```

Generate a conversion audit report:
```bash
pdf-to-epub --input "book.pdf" --output "book.epub" --report "book.audit.json"
```

Append a generated colophon with conversion metadata:
```bash
pdf-to-epub --input "book.pdf" --output "book.epub" --include-colophon
```

Enable extra heuristics tuned for OCR-derived documents:
```bash
pdf-to-epub --input "scanned_doc.pdf" --output "scanned_doc.epub" --ocr-heuristics
```

### Python API

```python
from pathlib import Path
from pipeline import ConversionPipeline

# Run conversion
output_path = ConversionPipeline(
    input_path=Path("manuscript.pdf"),
    output_path=Path("book.epub"),
    title="My Book Title",
    include_colophon=False,
).run()

print(f"EPUB created at {output_path}")
```

Direct access to Rust acceleration:
```python
from rust_bridge import is_rust_available, sort_reading_order, reconstruct_table_grid

print(f"Rust native core loaded: {is_rust_available()}")

# Sort page blocks into reading order
blocks = [
    {"bbox": (320.0, 100.0, 550.0, 120.0), "text": "Right column header"},
    {"bbox": (50.0, 100.0, 280.0, 120.0), "text": "Left column header"},
]
ordered = sort_reading_order(blocks, page_width=600.0)
```

---

## Verification & Testing

Run the full automated test suite:

```bash
# Run Python unit and integration tests
pytest

# Run Rust unit tests for geometry and archive algorithms
cargo test

# Run static type checking
pyright
```

---

## License & Provenance

This project is licensed under the **EmperorOfBooks Source-Available and Provenance License (Version 1.0)**.

- **Noncommercial Use**: Free for personal reading, study, academic research, and non-profit educational instruction.
- **Commercial Use**: Prohibited without a separate, written Commercial License Agreement and Royalty Schedule executed directly with EmperorOfBooks.
- **AI & Model Training Provenance**: Any Transformed Corpus and any machine learning model trained, aligned, or fine-tuned on data processed by this engine must publicly acknowledge the EmperorOfBooks PDF-to-EPUB engine and preserve authorial and source metadata.

For full license terms, refer to [`LICENSE.md`](LICENSE.md).  
For third-party dependency licenses and notices, refer to [`NOTICE.md`](NOTICE.md) and the [`licenses/`](licenses/) directory.
