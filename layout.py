from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
from statistics import median
from typing import Iterable

from config import HEADER_FOOTER_MARGIN_RATIO, HEADER_REPEAT_RATIO, MARGIN_ARTIFACT_BOTTOM_RATIO, MARGIN_ARTIFACT_TOP_RATIO, MIN_HEADER_REPEAT_COUNT
from extractor import PageData, TextBlockData


WATERMARK_FONT_SIZE_MULTIPLIER = 3.0


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

    def repeated_margin_texts(self, document_pages: Iterable[PageData], body_font_size: float | None = None) -> set[str]:
        """Repeated boilerplate text: classic header/footer margin bands, plus
        oversized repeated watermark text (e.g. a diagonal "PROOF" stamp) that
        spans well outside the margin bands but recurs across most pages."""
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
                in_margin = block.bbox[3] <= top_limit or block.bbox[1] >= bottom_limit
                is_oversized = (
                    body_font_size is not None
                    and block.max_font_size >= body_font_size * WATERMARK_FONT_SIZE_MULTIPLIER
                )
                if (in_margin or is_oversized) and text not in seen:
                    counts[text] += 1
                    seen.add(text)
        return {text for text, count in counts.items() if count >= threshold}

    def column_index(self, page: PageData, x_value: float) -> int:
        widths = [block.bbox[2] - block.bbox[0] for block in page.text_blocks if self.normalize(block.text)]
        if any(width > page.width * 0.55 for width in widths):
            return 0
        left_edges = sorted({block.bbox[0] for block in page.text_blocks if self.normalize(block.text)})
        if len(left_edges) < 2 or left_edges[-1] - left_edges[0] < max(80.0, page.width * 0.12):
            return 0
        split = (left_edges[0] + left_edges[-1]) / 2.0
        return 0 if x_value < split else 1

    def is_margin_artifact(self, page: PageData, block: TextBlockData, body_font_size: float) -> bool:
        if len(block.lines) != 1 or block.max_font_size > body_font_size * 1.05:
            return False
        return block.bbox[3] <= page.height * MARGIN_ARTIFACT_TOP_RATIO or block.bbox[1] >= page.height * MARGIN_ARTIFACT_BOTTOM_RATIO

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
