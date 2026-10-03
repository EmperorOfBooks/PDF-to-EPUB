from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
from statistics import median
from typing import Iterable

from config import HEADER_FOOTER_MARGIN_RATIO, HEADER_REPEAT_RATIO, MIN_HEADER_REPEAT_COUNT
from extractor import PageData, TextBlockData


@dataclass(frozen=True)
class LayoutStats:
    body_font_size: float
    median_line_height: float


class LayoutAnalyzer:
    """Geometry-only analysis for PDF pages.

    Text semantics stay in the cleaner; this class owns reusable page geometry
    such as font statistics, recurring margin bands, and column ordering.
    """

    def analyze(self, document_pages: Iterable[PageData]) -> LayoutStats:
        sizes: list[float] = []
        line_heights: list[float] = []
        for page in document_pages:
            for block in page.text_blocks:
                for line in block.lines:
                    height = line.bbox[3] - line.bbox[1]
                    if height > 0:
                        line_heights.append(height)
                    for span in line.spans:
                        if span.text.strip() and span.size > 0:
                            sizes.append(round(span.size, 1))
        return LayoutStats(
            body_font_size=float(Counter(sizes).most_common(1)[0][0]) if sizes else 12.0,
            median_line_height=float(median(line_heights)) if line_heights else 12.0,
        )

    def repeated_margin_texts(self, document_pages: Iterable[PageData]) -> set[str]:
        pages = tuple(document_pages)
        counts: Counter[str] = Counter()
        threshold = max(MIN_HEADER_REPEAT_COUNT, ceil(len(pages) * HEADER_REPEAT_RATIO))
        for page in pages:
            top_limit = page.height * HEADER_FOOTER_MARGIN_RATIO
            bottom_limit = page.height * (1.0 - HEADER_FOOTER_MARGIN_RATIO)
            seen: set[str] = set()
            for block in page.text_blocks:
                text = self.normalize(block.text)
                if not text:
                    continue
                if block.bbox[3] <= top_limit or block.bbox[1] >= bottom_limit:
                    if text not in seen:
                        counts[text] += 1
                        seen.add(text)
        return {text for text, count in counts.items() if count >= threshold}

    def column_index(self, page: PageData, x_value: float) -> int:
        centers = sorted(
            {
                (block.bbox[0] + block.bbox[2]) / 2.0
                for block in page.text_blocks
                if self.normalize(block.text)
            }
        )
        if len(centers) < 2 or centers[-1] - centers[0] < max(80.0, page.width * 0.12):
            return 0
        for index, center in enumerate(centers):
            if x_value < center:
                return index
        return len(centers) - 1

    def ordered_blocks(self, page: PageData, blocks: Iterable[TextBlockData] | None = None) -> list[TextBlockData]:
        candidates = list(blocks if blocks is not None else page.text_blocks)
        return sorted(
            candidates,
            key=lambda block: (
                self.column_index(page, block.bbox[0]),
                block.bbox[1],
                block.bbox[0],
            ),
        )

    def is_full_width_image(self, page: PageData, bbox: tuple[float, float, float, float]) -> bool:
        return (bbox[2] - bbox[0]) > page.width * 0.6

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.split())
