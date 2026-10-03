from __future__ import annotations

import re
from statistics import median
from typing import Iterable, Sequence

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")


def normalize_paragraphs(paragraphs: Iterable[str]) -> list[str]:
    return [str(text).strip() for text in paragraphs if str(text).strip()]


def paragraph_stats(paragraphs: Sequence[str]) -> dict[str, float | int]:
    texts = normalize_paragraphs(paragraphs)
    if not texts:
        return {
            'paragraph_count': 0,
            'lowercase_start_ratio': 0.0,
            'median_chars': 0,
            'under_40_chars': 0,
            'junk_count': 0,
        }

    lowercase_count = sum(
        1
        for text in texts
        if text and text[0].islower() and not text[0].isdigit()
    )
    lengths = [len(text) for text in texts]
    compact_junk = sum(
        1
        for text in texts
        if len(re.sub(r"\s+", "", text)) <= 3 and re.fullmatch(r"[\W\d_]+", re.sub(r"\s+", "", text))
    )

    return {
        'paragraph_count': len(texts),
        'lowercase_start_ratio': lowercase_count / len(texts),
        'median_chars': int(median(lengths)),
        'under_40_chars': sum(1 for length in lengths if length < 40),
        'junk_count': compact_junk,
    }


def word_recall(reference_texts: Sequence[str], extracted_texts: Sequence[str]) -> dict[str, float | int]:
    reference_tokens = set()
    extracted_tokens = set()

    for text in reference_texts:
        reference_tokens.update(TOKEN_RE.findall(text.lower()))
    for text in extracted_texts:
        extracted_tokens.update(TOKEN_RE.findall(text.lower()))

    overlap = len(reference_tokens & extracted_tokens)
    reference_count = len(reference_tokens)
    return {
        'reference_word_count': reference_count,
        'extracted_word_count': len(extracted_tokens),
        'overlap_word_count': overlap,
        'recall': overlap / reference_count if reference_count else 1.0,
        'missing_word_count': len(reference_tokens - extracted_tokens),
        'spurious_word_count': len(extracted_tokens - reference_tokens),
    }


def detect_junk_paragraphs(paragraphs: Sequence[str]) -> list[str]:
    junk: list[str] = []
    for text in normalize_paragraphs(paragraphs):
        compact = re.sub(r"\s+", "", text)
        if not compact:
            continue
        if len(compact) <= 3 and re.fullmatch(r"[\W\d_]+", compact):
            junk.append(text)
    return junk
