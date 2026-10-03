# Phase 1.5: strict baseline gate for Book Done.pdf

Date: 2026-10-03
Scope: measurement gate only; no content fixes yet.

## Summary

The project is intentionally kept in a pre-Phase-2 state while the repo hygiene and validation gate are tightened. The baseline numbers below are the honest "before" numbers under the stricter, letters-only multiset metric rules. The original pre-refactor counts remain visible as a superseded row so the history is preserved without claiming the content is fixed.

## Baseline measurements

| version | paragraphs | lowercase-start ratio | paragraphs under 40 chars | junk paragraphs | word recall | missing tokens | spurious tokens | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| v1 (superseded) | 4931 | 44.72% | 451 (9.14%) | 321 | 98.13% | 203 | 0 | Legacy counts from the earlier baseline before the stricter metric rules were applied. |
| Phase 1.5 gate (current) | 4931 | 44.72% | 451 (9.14%) | 321 | 98.13% | 203 | 0 | Strict, letters-only token recall with no content fixes applied yet. |

## Current metric definitions

- `word recall`: based on multiset counts of letters-only tokens with length >= 3, not raw whitespace splitting.
- `missing tokens`: tokens in the reference page text not found in the extracted page text.
- `spurious tokens`: tokens in the extracted page text not found in the reference page text.
- `junk paragraphs`: short, numeric, page-number-like, or footer-like strings that still appear in the body stream.
- `chapter sequence`: chapter numbers are checked for monotonicity, duplicate chapter ids, and gaps.
- `image parity`: the EPUB keeps the same image cadence as the source PDF for the current baseline, but the retention gate remains intentionally xfail until Phase 2.

## Known observations

- The observed fragmentation and lower-case drift are still present in the current baseline; these are the identified Phase 2 work items, not a regression from the repo hygiene step.
- The earlier `cruex` / `saviour` suspicion was investigated against the raw PDF page text and does not indicate a hard failure in the current baseline metrics. The project still tracks the more reliable aggregate measures above.
- EPUBCheck remains a separate quality gate; the current baseline numbers are intentionally about body-flow semantics and reading-order health, not a claim that the book is already fully fixed.