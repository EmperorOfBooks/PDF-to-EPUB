# Reference-system review

This project reviewed the reference systems proposed in the task and adopted or skipped them deliberately.

## Adopted design patterns

### pypdfium2 / ebooklib
- Adopted: yes
- Why: pypdfium2 wraps Google's permissively-licensed PDFium engine, avoiding the AGPL/commercial dual-licensing obligations the project previously carried with PyMuPDF, while still providing direct PDF text and image extraction suitable for a self-contained Windows/Python workflow.
- License note: pypdfium2 is Apache-2.0 OR BSD-3-Clause (the bundled PDFium library is itself BSD-3-Clause/Apache-2.0 dual-licensed). ebooklib remains AGPL-3.0-or-later for the EPUB packaging step; that obligation is unchanged by this migration.

### Rust Native Extension (pdf_to_epub_core via PyO3 & Maturin)
- Adopted: yes
- Why: Provides high-performance spatial geometry, multi-column reading order topological sorting, line clustering, and table cell coordinate grid reconstruction. Rayon parallelism accelerates multi-page batches while releasing the Python GIL (`py.allow_threads`). Safe OCF archive packaging runs uncompressed mimetype writes directly in Rust with path traversal validation.
- Dependencies: PyO3 (0.23, Apache-2.0 OR MIT), Rayon (1.10, Apache-2.0 OR MIT), zip (2.2, MIT), regex (1.11, Apache-2.0 OR MIT).

### Security-Hardened Archive & Parser Architecture
- Adopted: yes
- Why: Comprehensive defense against Zip Slip directory traversal (`archive_security.py`), decompression bombs (250 MB total / 50 MB entry / 100:1 ratio caps with bounded 64 KB chunk reads), defused XML parsing (`defusedxml` blocking XXE and entity expansion), safe subprocess execution (`shell=False`, list arguments, UUID4 filenames in temp directories, timeouts), and ReDoS hardening on text assembly regexes.

### EPUB3 semantics and accessibility-oriented packaging
- Adopted: yes
- Why: EPUB3 with semantic XHTML, headings, ARIA document landmarks, NCX backward compatibility, and accessibility metadata is the standard for clean, vendor-compliant, reflowable output conforming to W3C EPUBCheck and IngramSpark guidelines.

### Multi-column text reconstruction heuristics
- Adopted: yes
- Why: The project reconstructs column boundaries and reading order signals from per-character geometry returned by `pypdfium2`'s text page API and sorts them efficiently via Rust topological sort routines with Rayon concurrency.

## Studied but not adopted

### toolkit.bot/pdf2epub
- Capability reviewed: EPUB3 semantics, reading-order heuristics, accessibility checks, OCR, batch design.
- Status: studied for architecture only; not copied.
- Reason for skip: the project is a compact local converter and should not import a proprietary or closed source pipeline.

### Cisdem-style converters
- Capability reviewed: layout fidelity for tables and complex formatting.
- Status: studied conceptually only.
- Reason for skip: their implementation details are proprietary and not available for direct code reuse.

### AI-powered academic converters
- Capability reviewed: formula/code handling, TOC repair, multilingual support.
- Status: not enabled by default.
- Reason for skip: requires external LLM or cloud infrastructure and would need explicit opt-in to respect privacy and offline configuration requirements.

## Open-source references reviewed for approach

- Calibre PDF input heuristics: reviewed for reading-order and chapter heuristics.
- Docling, Marker, MinerU: reviewed for extraction and semantic block grouping, but not copied.
- PyMuPDF4LLM and pymupdf_layout: reviewed for layout analysis ideas, but not required for the current codebase (the project no longer depends on PyMuPDF itself).
- pdfplumber / pdfminer.six: reviewed for block/column analysis approaches.
- OCRmyPDF / Tesseract: reviewed as optional OCR fallback for image-only or OCR-deficient PDFs.
- Ace by DAISY: reviewed as the target for accessibility-report style checks and compliance validation.
- W3C EPUBCheck: adopted as the compliance validation tool for local EPUB check passes.

## License note

The project is licensed under the EmperorOfBooks Source-Available and Provenance License (Version 1.0). Third-party dependencies include pypdfium2 (Apache-2.0 OR BSD-3-Clause), ebooklib (AGPL-3.0-or-later), and Rust crates (PyO3, Rayon, Zip, Regex). See `LICENSE.md`, `NOTICE.md`, and `licenses/` for complete terms and third-party inventory.
