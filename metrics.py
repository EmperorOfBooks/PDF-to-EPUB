from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Iterable, Sequence

LETTER_TOKEN_RE = re.compile(r"[A-Za-z]{3,}")
PAGE_NUMBER_GARBAGE_RE = re.compile(r"^(?:\d+[A-Za-z]+|[A-Za-z]+\d+|[A-Za-z]?\d+[A-Za-z]?|&[A-Za-z0-9])$", re.IGNORECASE)


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


def letters_only_multiset(texts: Sequence[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        for token in LETTER_TOKEN_RE.findall(text):
            counts[token.lower()] += 1
    return counts


def word_recall(reference_texts: Sequence[str], extracted_texts: Sequence[str]) -> dict[str, float | int | list[str]]:
    reference_counts = letters_only_multiset(reference_texts)
    extracted_counts = letters_only_multiset(extracted_texts)
    missing_counts = reference_counts - extracted_counts
    spurious_counts = extracted_counts - reference_counts
    missing_tokens = sorted(missing_counts.elements())
    spurious_tokens = sorted(spurious_counts.elements())
    total_reference = sum(reference_counts.values())
    total_extracted = sum(extracted_counts.values())
    overlap = sum(min(reference_counts[token], extracted_counts[token]) for token in reference_counts.keys() & extracted_counts.keys())
    recall = overlap / total_reference if total_reference else 1.0
    return {
        'reference_token_count': total_reference,
        'extracted_token_count': total_extracted,
        'overlap_token_count': overlap,
        'recall': recall,
        'missing_token_count': len(missing_tokens),
        'spurious_token_count': len(spurious_tokens),
        'missing_word_count': len(missing_tokens),
        'spurious_word_count': len(spurious_tokens),
        'missing_tokens': missing_tokens,
        'spurious_tokens': spurious_tokens,
    }


def detect_junk_paragraphs(paragraphs: Sequence[str], *, page_top_frac: float = 0.12, page_bottom_frac: float = 0.88, page_positions: Sequence[tuple[float, float]] | None = None) -> list[str]:
    junk: list[str] = []
    for index, text in enumerate(normalize_paragraphs(paragraphs)):
        compact = re.sub(r"\s+", "", text)
        if not compact:
            continue
        if len(compact) <= 6 and re.fullmatch(r"[\W\d_]+", compact):
            junk.append(text)
            continue
        if PAGE_NUMBER_GARBAGE_RE.fullmatch(compact):
            junk.append(text)
            continue
        if page_positions and len(page_positions) > index:
            top_fraction, bottom_fraction = page_positions[index]
            if top_fraction <= page_top_frac or bottom_fraction >= page_bottom_frac:
                if len(compact) <= 6:
                    junk.append(text)
    return junk


def detect_junk_paragraphs_by_page(paragraphs: Sequence[str], page_blocks: Sequence[tuple[str, tuple[float, float, float, float]]]) -> list[str]:
    junk: list[str] = []
    for (_, bbox), text in zip(page_blocks, paragraphs):
        y0, _, _, y1 = bbox
        compact = re.sub(r"\s+", "", text)
        if len(compact) <= 6 and PAGE_NUMBER_GARBAGE_RE.fullmatch(compact):
            junk.append(text)
            continue
        if len(compact) <= 6 and (y0 <= 0.12 or y1 >= 0.88):
            junk.append(text)
    return junk


def detect_chapter_sequence(chapter_titles: Sequence[str]) -> dict[str, object]:
    digits: list[int] = []
    for title in chapter_titles:
        match = re.search(r"\bchapter\s+(\d+)\b", title, flags=re.IGNORECASE)
        if match:
            digits.append(int(match.group(1)))
    gaps = []
    duplicates = []
    for idx in range(1, len(digits)):
        previous = digits[idx - 1]
        current = digits[idx]
        if current == previous:
            duplicates.append((previous, current))
        if current - previous > 1:
            gaps.append((previous, current))
    return {
        'numbers': digits,
        'gaps': gaps,
        'duplicates': duplicates,
    }


def detect_junk_paragraphs_for_baseline(paragraphs: Sequence[str], page_positions: Sequence[tuple[float, float]]) -> list[str]:
    return detect_junk_paragraphs(paragraphs, page_positions=page_positions)
