# PDF-to-EPUB metrics and architecture record

Date: 2026-10-03
Scope: historical baseline, Phase 2 content improvements, and current modular architecture.

## Summary

The baseline numbers below preserve the honest pre-refactor state under the stricter, letters-only multiset metric rules. Later rows record measured improvements without overwriting the original comparison point.

## Baseline measurements

| version | paragraphs | lowercase-start ratio | paragraphs under 40 chars | junk paragraphs | word recall | missing tokens | spurious tokens | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| v1 (superseded) | 4931 | 44.72% | 451 (9.14%) | 321 | 98.13% | 203 | 0 | Legacy counts from the earlier baseline before the stricter metric rules were applied. |
| Phase 1.5 gate (current) | 4931 | 44.72% | 451 (9.14%) | 321 | 98.13% | 203 | 0 | Strict, letters-only token recall with no content fixes applied yet. |
| Phase 2A (paragraphs + chapter order) | 1999 | 14.61% | 165 (8.25%) | 46 | 99.93% | 89 | 0 | Measured after block-level paragraph joining and targeted OCR heading normalization. |
| Phase 2B (cross-page paragraph flow) | 1713 | 0.35% | 124 (7.24%) | 23 | 99.93% | 89 | 0 | Measured after merging lowercase paragraph continuations across page boundaries. |
| Phase 2C (OCR page-number cleanup) | 1628 | 0.37% | 76 (4.67%) | 0 | 99.93% | 89 | 0 | Measured after filtering the observed short OCR-corrupted page-number forms. |
| Phase 3A (modular layout/extractor/builder) | 1628 | 0.37% | 76 (4.67%) | 0 | 99.93% | 89 | 0 | Same content metrics after responsibility split; EPUBCheck and broad validation remain clean. |
| Phase 4A (geometry, image, and heading hardening) | 1601 | 0.31% | 71 (4.43%) | 0 | 99.93% | 89 | 0 | Wide-block column handling, geometric margins, XRef image filtering, stable chapter splits, heading consolidation, and page continuation fixes. |

Phase 2A also produced 37 total chapters, with the main narrative sequence 1 through 16 monotonic and gap-free. Source image retention remained 5/5.

Phase 2B keeps the chapter sequence gap-free and reduces lowercase-start paragraphs below the 2% target.

Phase 2C removes all observed OCR page-number junk and brings short paragraphs below the 5% target. Chapter sequencing and source-image retention now run as real-fixture regressions; remaining skips cover synthetic corpus, Ace, and Playwright coverage that has not been added yet.

Phase 3A separates geometry analysis into `layout.py`, extraction contracts into `extractor.py`, and EPUB packaging into `builder.py`. `epub_builder.py` remains a compatibility shim. The generated package contains a shared stylesheet, EPUB namespace-qualified semantic sections, a generated nav document, and geometry-anchored figures.

Phase 4A produces 49 total chapters with the narrative sequence 1 through 16 gap-free, retains 5/5 source images, and passes the verified EPUB and deterministic QA loop.

## Current metric definitions

- `word recall`: based on multiset counts of letters-only tokens with length >= 3, not raw whitespace splitting.
- `missing tokens`: tokens in the reference page text not found in the extracted page text.
- `spurious tokens`: tokens in the extracted page text not found in the reference page text.
- `junk paragraphs`: short, numeric, page-number-like, or footer-like strings that still appear in the body stream.
- `chapter sequence`: chapter numbers are checked for monotonicity, duplicate chapter ids, and gaps.
- `image parity`: source and semantic-flow image counts are compared on the representative fixture; current retention is 5/5.

## Known observations

- The original fragmentation and lower-case drift remain documented in the baseline rows; the Phase 2 rows record their measured reduction.
- The earlier `cruex` / `saviour` suspicion was investigated against the raw PDF page text and does not indicate a hard failure in the current baseline metrics. The project still tracks the more reliable aggregate measures above.
- EPUBCheck, deterministic QA, and broad public-PDF validation are separate package-level gates and currently pass.