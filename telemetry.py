"""Conversion telemetry: word accounting, drop tracking, and report.json generation."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from inline import strip_markup

REPORT_SCHEMA_VERSION = 1
RECALL_THRESHOLD = 0.98

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_LINE_HYPHEN_RE = re.compile(r"[-\u00ad]\s*\n\s*")
_INNER_HYPHEN_RE = re.compile(r"(?<=\w)[-\u00ad](?=\w)")

# Intentional removals; their words are excluded from the recall denominator.
BOILERPLATE_REASONS = frozenset(
    {"running_header_footer", "page_number", "table_of_contents", "cover_page", "margin_artifact", "noise_text", "ocr_replaced_scan"}
)


def normalize_for_words(text: str) -> str:
    text = strip_markup(text)
    text = _LINE_HYPHEN_RE.sub("", text)
    return _INNER_HYPHEN_RE.sub("", text).lower()


def tokenize_words(text: str) -> list[str]:
    return _WORD_RE.findall(normalize_for_words(text))


def count_words(text: str) -> int:
    return len(tokenize_words(text))


@dataclass
class DroppedRegion:
    page: int
    bbox: tuple[float, float, float, float] | None
    reason: str
    tokens: list[str] = field(default_factory=list)
    whole_page: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page - 1,
            "page_number": self.page,
            "bbox": [round(float(v), 2) for v in self.bbox] if self.bbox else None,
            "reason": self.reason,
            "words": len(self.tokens),
            "whole_page": self.whole_page,
        }


@dataclass
class ConversionStats:
    input_tokens: list[str] | None = None
    drops: list[DroppedRegion] = field(default_factory=list)
    ocr_pages: list[dict[str, Any]] = field(default_factory=list)
    ocr_engine: str | None = None
    ocr_skipped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cover: dict[str, Any] = field(default_factory=dict)
    scanned: bool = False
    source_format: str = "pdf"

    def drop(self, page: int, bbox, reason: str, text: str = "", whole_page: bool = False) -> None:
        self.drops.append(
            DroppedRegion(page=page, bbox=tuple(bbox) if bbox is not None else None, reason=reason, tokens=tokenize_words(text), whole_page=whole_page)
        )

    def boilerplate_tokens(self) -> list[str]:
        return [token for item in self.drops if item.reason in BOILERPLATE_REASONS for token in item.tokens]


def output_tokens(document) -> list[str]:
    """Visible word tokens of the cleaned document model (text, tables, notes, title page, figure alt text)."""
    tokens: list[str] = []
    title_page = getattr(document, "title_page", None)
    if title_page is not None:
        tokens.extend(tokenize_words(" ".join(title_page.all_lines())))
    for chapter in document.chapters:
        for item in chapter.items:
            kind = getattr(item, "kind", "")
            if kind in {"paragraph", "heading", "code", "note"}:
                tokens.extend(tokenize_words(item.text))
            elif kind == "table":
                for row in item.rows:
                    for cell in row:
                        tokens.extend(tokenize_words(cell))
            elif kind == "image":
                if getattr(item, "is_vector", False):
                    tokens.extend(tokenize_words(item.alt or ""))
                tokens.extend(tokenize_words(item.caption or ""))
    return tokens


def multiset_recall(input_tokens: list[str], produced_tokens: list[str], expected_drops: list[str]) -> float:
    expected = Counter(input_tokens)
    expected.subtract(Counter(expected_drops))
    expected = +expected
    total = sum(expected.values())
    if total == 0:
        return 1.0
    produced = Counter(produced_tokens)
    return sum(min(count, produced[token]) for token, count in expected.items()) / total


def build_report(
    *,
    stats: ConversionStats,
    document,
    chapter_files: int,
    runtime_seconds: float,
    input_path: Path,
    output_path: Path,
    counts: dict[str, int],
) -> dict[str, Any]:
    produced = output_tokens(document)
    drops = stats.boilerplate_tokens()
    if stats.input_tokens is None:
        input_words = expected_words = recall = recall_raw = None
    else:
        input_words = len(stats.input_tokens)
        expected_words = max(0, input_words - len(drops))
        recall = round(multiset_recall(stats.input_tokens, produced, drops), 4)
        recall_raw = round(min(1.0, len(produced) / input_words), 4) if input_words else 1.0
    gate_applies = recall is not None and not stats.scanned
    cover = dict(stats.cover) if stats.cover else {"classification": "NONE", "confidence": 0.0, "page_number": None}
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "input": {"path": input_path.name, "format": stats.source_format},
        "output": {"path": output_path.name},
        "words": {
            "input": input_words,
            "output": len(produced),
            "expected_after_boilerplate_drops": expected_words,
            "recall": recall,
            "recall_raw": recall_raw,
            "recall_threshold": RECALL_THRESHOLD,
            "recall_gate_applies": gate_applies,
            "recall_passed": (recall >= RECALL_THRESHOLD) if gate_applies else None,
        },
        "dropped_pages": [item.to_dict() for item in stats.drops if item.whole_page],
        "dropped_regions": [item.to_dict() for item in stats.drops if not item.whole_page],
        "cover": cover,
        "counts": counts,
        "chapters": {"file_count": chapter_files},
        "diagnostics": {
            "runtime_seconds": round(runtime_seconds, 3),
            "scanned_input": stats.scanned,
            "ocr_engine": stats.ocr_engine,
            "ocr_pages": stats.ocr_pages,
            "ocr_skipped": stats.ocr_skipped,
            "warnings": stats.warnings,
        },
    }


def recall_gate_failed(report: dict[str, Any]) -> bool:
    return report["words"].get("recall_passed") is False


def write_report(path: Path, report: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


REQUIRED_REPORT_KEYS = ("schema_version", "input", "output", "words", "dropped_pages", "dropped_regions", "cover", "counts", "chapters", "diagnostics")


def validate_report(report: dict[str, Any]) -> list[str]:
    """Return a list of schema problems (empty when the report is valid)."""
    problems = [f"missing key: {key}" for key in REQUIRED_REPORT_KEYS if key not in report]
    if problems:
        return problems
    for key in ("input", "output", "recall", "recall_threshold", "recall_gate_applies"):
        if key not in report["words"]:
            problems.append(f"words.{key} missing")
    if report["cover"].get("classification") not in {"COVER_IMAGE", "TITLE_PAGE", "NONE"}:
        problems.append("cover.classification invalid")
    if not isinstance(report["cover"].get("confidence"), (int, float)):
        problems.append("cover.confidence must be numeric")
    for key in ("tables", "images", "notes"):
        if not isinstance(report["counts"].get(key), int):
            problems.append(f"counts.{key} must be int")
    if not isinstance(report["chapters"].get("file_count"), int):
        problems.append("chapters.file_count must be int")
    if "runtime_seconds" not in report["diagnostics"]:
        problems.append("diagnostics.runtime_seconds missing")
    for entry in report["dropped_pages"] + report["dropped_regions"]:
        if not {"page_index", "bbox", "reason"} <= set(entry):
            problems.append("dropped entry missing page_index/bbox/reason")
            break
    return problems
