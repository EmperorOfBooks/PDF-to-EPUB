# Phase 0: baseline metrics for Book Done.pdf

Date: 2026-10-03
Source: current project state before the refactor work.

## Capture summary

The baseline converter was run on the live sample book using the current `PdfExtractor` and `PdfCleaner` pipeline.

## Baseline measurements

- Chapters produced: 36
- Paragraphs produced: 4931
- Lowercase-start paragraphs: 2205
- Lowercase-start ratio: 44.72%
- Median paragraph length: 63 characters
- Paragraphs under 40 chars: 451 (9.14%)
- Unique raw words: 10854
- Unique cleaned words: 10651
- Word overlap: 10651
- Word recall: 98.13%
- Unique spurious words: 0
- Unique missing words: 203
- EPUBCheck baseline: 0 errors, 0 warnings

## Notes

- This confirms the known fragmentation issue: the pipeline emits a very high paragraph count and a large proportion of lowercase-start paragraphs, which is consistent with the paragraph reflow bug described in the diagnosis.
- The current EPUBCheck result is clean, but the body-flow quality is still below the desired reflow and structural quality targets.
- The baseline is intentionally stored before any refactor so the before/after comparison remains reproducible.
