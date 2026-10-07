# Reference-system review

This project reviewed the reference systems proposed in the task and adopted or skipped them deliberately.

## Adopted design patterns

### pypdfium2 / ebooklib
- Adopted: yes
- Why: pypdfium2 wraps Google's permissively-licensed PDFium engine, avoiding the AGPL/commercial dual-licensing obligations the project previously carried with PyMuPDF, while still providing direct PDF text and image extraction suitable for a self-contained Windows/Python workflow.
- License note: pypdfium2 is Apache-2.0 OR BSD-3-Clause (the bundled PDFium library is itself BSD-3-Clause/Apache-2.0 dual-licensed). ebooklib remains AGPL-3.0-or-later for the EPUB packaging step; that obligation is unchanged by this migration.

### EPUB3 semantics and accessibility-oriented packaging
- Adopted: yes, in principle
- Why: EPUB3 with semantic XHTML, headings, landmarks, and accessibility metadata is the right long-term target for clean reflowable output.
- Skipped for now: deep vendor parity, because implementing full DAISY accessibility automation is a larger effort than this current refactor.

### Multi-column text reconstruction heuristics
- Adopted: yes
- Why: the project reconstructs column and paragraph-order signals from per-character geometry returned by `pypdfium2`'s text page API.
- This is the most actionable improvement path before introducing heavier OCR or layout dependencies.

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
- Reason for skip: requires external LLM or OCR infrastructure and would need explicit opt-in to respect the privacy and configuration requirements.

## Open-source references reviewed for approach

- Calibre PDF input heuristics: reviewed for reading-order and chapter heuristics.
- Docling, Marker, MinerU: reviewed for extraction and semantic block grouping, but not copied.
- PyMuPDF4LLM and pymupdf_layout: reviewed for layout analysis ideas, but not required for the current codebase (the project no longer depends on PyMuPDF itself).
- pdfplumber / pdfminer.six: reviewed for block/column analysis approaches.
- OCRmyPDF / Tesseract: reviewed as optional OCR fallback for image-only or OCR-deficient PDFs.
- Ace by DAISY: reviewed as the target for accessibility-report style checks and minor compliance validation.
- W3C EPUBCheck: adopted as the compliance validation tool for local EPUB check passes.

## License note

The project depends on pypdfium2 (Apache-2.0 OR BSD-3-Clause) for PDF extraction and ebooklib (AGPL-3.0-or-later) for EPUB packaging. Any new dependency added later should be checked against the same license obligations before distribution or embedding in commercial workflows. See `NOTICE.md` and `licenses/` for the full third-party inventory.
