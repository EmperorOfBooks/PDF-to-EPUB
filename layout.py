from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
from statistics import median
from typing import Iterable, Sequence

from config import (
    HEADER_FOOTER_MARGIN_RATIO,
    HEADER_REPEAT_RATIO,
    MIN_HEADER_REPEAT_COUNT,
    XY_CUT_MIN_BAND_GAP,
    XY_CUT_MIN_GUTTER,
)
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
        return block.bbox[3] <= page.height * 0.07 or block.bbox[1] >= page.height * 0.93

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

    def xy_cut_order(
        self,
        boxes: Sequence[tuple[float, float, float, float]],
        page_width: float,
        page_height: float = 0.0,
    ) -> list[int]:
        """Reading order of bounding boxes via recursive XY-cut over projection profiles.

        Vertical whitespace valleys (gutters) are tried first so that multi-column
        pages are segmented into columns before any line assembly happens; if the
        region has no gutter, horizontal valleys split it into bands, which are
        then recursed.
        """
        min_gap_x = max(XY_CUT_MIN_GUTTER, page_width * 0.01)
        return self._xy_cut(list(range(len(boxes))), boxes, min_gap_x)

    def _xy_cut(self, indices: list[int], boxes: Sequence[tuple[float, float, float, float]], min_gap_x: float) -> list[int]:
        if len(indices) <= 1:
            return indices
        column_groups = self._split_by_valleys(indices, boxes, axis=0, min_gap=min_gap_x)
        if len(column_groups) > 1:
            return [index for group in column_groups for index in self._xy_cut(group, boxes, min_gap_x)]
        band_groups = self._split_by_valleys(indices, boxes, axis=1, min_gap=XY_CUT_MIN_BAND_GAP)
        if len(band_groups) > 1:
            return [index for group in band_groups for index in self._xy_cut(group, boxes, min_gap_x)]
        return sorted(indices, key=lambda index: (boxes[index][1], boxes[index][0]))

    @staticmethod
    def _split_by_valleys(
        indices: list[int],
        boxes: Sequence[tuple[float, float, float, float]],
        axis: int,
        min_gap: float,
    ) -> list[list[int]]:
        lo, hi = axis, axis + 2
        ordered = sorted(indices, key=lambda index: (boxes[index][lo], boxes[index][hi]))
        groups: list[list[int]] = [[ordered[0]]]
        reach = boxes[ordered[0]][hi]
        for index in ordered[1:]:
            if boxes[index][lo] - reach > min_gap:
                groups.append([index])
            else:
                groups[-1].append(index)
            reach = max(reach, boxes[index][hi])
        return groups

    @staticmethod
    def _groups_overlap_vertically(groups: list[list[int]], boxes: Sequence[tuple[float, float, float, float]]) -> bool:
        """A gutter only counts when the groups it separates share vertical extent (true columns)."""
        extents = [(min(boxes[i][1] for i in group), max(boxes[i][3] for i in group)) for group in groups]
        for position, (top, bottom) in enumerate(extents):
            others = [extent for other, extent in enumerate(extents) if other != position]
            other_top = min(extent[0] for extent in others)
            other_bottom = max(extent[1] for extent in others)
            overlap = min(bottom, other_bottom) - max(top, other_top)
            if overlap < 0.3 * max(bottom - top, 1.0):
                return False
        return True

    def is_full_width_image(self, page: PageData, bbox: tuple[float, float, float, float]) -> bool:
        return (bbox[2] - bbox[0]) > page.width * 0.6

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.split())
