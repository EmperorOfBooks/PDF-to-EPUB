# Reference-system review

This project reviewed the reference systems proposed in the task and adopted or skipped them deliberately.

## Adopted design patterns

### PyMuPDF / ebooklib
- Adopted: yes
- Why: this project already uses them and they remain the best fit for direct PDF text extraction and EPUB packaging in a self-contained Windows/Python workflow.
- License note: PyMuPDF is AGPL. That is a legal consideration for redistributing or embedding the converter in a closed-source context. The project remains compatible with the current local implementation, but any commercial deployment should review AGPL obligations before distribution.

### EPUB3 semantics and accessibility-oriented packaging
- Adopted: yes, in principle
- Why: EPUB3 with semantic XHTML, headings, landmarks, and accessibility metadata is the right long-term target for clean reflowable output.
- Skipped for now: deep vendor parity, because implementing full DAISY accessibility automation is a larger effort than this current refactor.

### Multi-column text reconstruction heuristics
- Adopted: yes
- Why: the project already contains real column and paragraph-order signals from `PyMuPDF` page geometry.
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
- PyMuPDF4LLM and pymupdf_layout: reviewed for layout analysis ideas, but not required for the current codebase.
- pdfplumber / pdfminer.six: reviewed for block/column analysis approaches.
- OCRmyPDF / Tesseract: reviewed as optional OCR fallback for image-only or OCR-deficient PDFs.
- Ace by DAISY: reviewed as the target for accessibility-report style checks and minor compliance validation.
- W3C EPUBCheck: adopted as the compliance validation tool for local EPUB check passes.

## License note

The project currently depends on PyMuPDF, which is AGPL. Any new dependency added later should be checked against the same license obligations before distribution or embedding in commercial workflows.
