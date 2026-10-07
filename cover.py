"""Cover disambiguation: graphic cover image vs. typographic title page."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from inline import normalize_invisible_separators, strip_markup

if TYPE_CHECKING:  # pragma: no cover
    from extractor import PageData

COVER_IMAGE = "COVER_IMAGE"
TITLE_PAGE = "TITLE_PAGE"
NONE = "NONE"

CANDIDATE_PAGES = 3
COVER_IMAGE_RATIO = 0.70
COVER_TINT_RATIO = 0.85
COVER_MAX_WORDS = 35
TITLE_WHITESPACE_RATIO = 0.70
TITLE_MAX_IMAGE_RATIO = 0.15
TITLE_MAX_WORDS = 90
TITLE_MAX_WORDS_PER_BLOCK = 14
TITLE_MIN_VERTICAL_SPAN = 0.25

COVER_TARGET_SIZE = (1600, 2560)
NONWHITE_LUMA = 235


@dataclass(frozen=True)
class PageVisualStats:
    image_ratio: float = 0.0
    vector_ratio: float = 0.0
    nonwhite_ratio: float = 0.0

    @property
    def graphic_ratio(self) -> float:
        return max(self.image_ratio, self.vector_ratio)

    @property
    def whitespace_ratio(self) -> float:
        return 1.0 - self.nonwhite_ratio


@dataclass(frozen=True)
class PageCoverDecision:
    page_number: int
    kind: str
    confidence: float
    word_count: int
    block_count: int
    image_ratio: float
    nonwhite_ratio: float
    whitespace_ratio: float

    def to_dict(self) -> dict:
        return {
            "page_number": self.page_number,
            "classification": self.kind,
            "confidence": self.confidence,
            "word_count": self.word_count,
            "image_ratio": round(self.image_ratio, 3),
            "nonwhite_ratio": round(self.nonwhite_ratio, 3),
            "whitespace_ratio": round(self.whitespace_ratio, 3),
        }


@dataclass(frozen=True)
class TitlePage:
    page_number: int
    title: str
    subtitle: str = ""
    authors: tuple[str, ...] = ()
    imprints: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()

    def all_lines(self) -> list[str]:
        return [value for value in (self.title, self.subtitle, *self.authors, *self.imprints, *self.extras) if value]


@dataclass(frozen=True)
class CoverInfo:
    kind: str
    confidence: float
    page_number: int | None
    decisions: tuple[PageCoverDecision, ...] = ()
    jpeg: bytes | None = None
    width: int = 0
    height: int = 0

    def to_report(self) -> dict:
        return {
            "classification": self.kind,
            "confidence": self.confidence,
            "page_number": self.page_number,
            "candidates": [decision.to_dict() for decision in self.decisions],
        }


def compute_visual_stats(page) -> PageVisualStats:
    """Measure image and background coverage using pypdfium2 page primitives."""
    from PIL import Image
    import pypdfium2 as pdfium

    page_width, page_height = page.get_size()
    page_area = float(page_width * page_height) or 1.0
    image_area = 0.0
    try:
        for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
            x0, y0, x1, y1 = obj.get_bounds()
            image_area = max(image_area, abs(x1 - x0) * abs(y1 - y0))
    except Exception:
        pass
    gray = page.render(scale=0.25, grayscale=True).to_pil().convert("L")
    histogram = gray.histogram()
    total = sum(histogram) or 1
    nonwhite = sum(histogram[:NONWHITE_LUMA]) / total
    return PageVisualStats(
        image_ratio=min(1.0, image_area / page_area),
        vector_ratio=0.0,
        nonwhite_ratio=nonwhite,
    )


def classify_page_metrics(
    visual: PageVisualStats,
    word_count: int,
    block_count: int,
    vertical_span_ratio: float,
) -> tuple[str, float]:
    graphic = visual.graphic_ratio
    graphic_hit = graphic >= COVER_IMAGE_RATIO or visual.nonwhite_ratio > COVER_TINT_RATIO
    if graphic_hit and word_count <= COVER_MAX_WORDS:
        margin = max(
            (graphic - COVER_IMAGE_RATIO) / (1.0 - COVER_IMAGE_RATIO),
            (visual.nonwhite_ratio - COVER_TINT_RATIO) / (1.0 - COVER_TINT_RATIO),
            0.0,
        )
        confidence = 0.6 + 0.3 * min(1.0, margin) + (0.1 if word_count <= 15 else 0.05)
        return COVER_IMAGE, round(min(confidence, 0.99), 3)
    words_per_block = word_count / block_count if block_count else 0.0
    if (
        visual.whitespace_ratio >= TITLE_WHITESPACE_RATIO
        and visual.graphic_ratio <= TITLE_MAX_IMAGE_RATIO
        and 0 < word_count <= TITLE_MAX_WORDS
        and block_count >= 2
        and words_per_block <= TITLE_MAX_WORDS_PER_BLOCK
        and vertical_span_ratio >= TITLE_MIN_VERTICAL_SPAN
    ):
        margin = min(1.0, (visual.whitespace_ratio - TITLE_WHITESPACE_RATIO) / (1.0 - TITLE_WHITESPACE_RATIO))
        confidence = 0.55 + 0.2 * margin + (0.15 if vertical_span_ratio >= 0.4 else 0.05) + (0.1 if block_count >= 3 else 0.0)
        return TITLE_PAGE, round(min(confidence, 0.99), 3)
    return NONE, 0.0


def decide_page(page: "PageData") -> PageCoverDecision | None:
    visual = getattr(page, "visual", None)
    if visual is None:
        return None
    from telemetry import count_words

    word_count = sum(count_words(block.text) for block in page.text_blocks)
    ys = [value for block in page.text_blocks for value in (block.bbox[1], block.bbox[3])]
    span = (max(ys) - min(ys)) / page.height if ys and page.height else 0.0
    kind, confidence = classify_page_metrics(visual, word_count, len(page.text_blocks), span)
    return PageCoverDecision(
        page_number=page.page_number,
        kind=kind,
        confidence=confidence,
        word_count=word_count,
        block_count=len(page.text_blocks),
        image_ratio=visual.graphic_ratio,
        nonwhite_ratio=visual.nonwhite_ratio,
        whitespace_ratio=visual.whitespace_ratio,
    )


@dataclass(frozen=True)
class CoverDetection:
    info: CoverInfo
    cover_page: int | None
    title_page: int | None


def detect_cover(pages: Sequence["PageData"]) -> CoverDetection:
    decisions = [decision for page in pages[:CANDIDATE_PAGES] if (decision := decide_page(page)) is not None]
    cover = next((d for d in decisions if d.kind == COVER_IMAGE), None)
    title = next((d for d in decisions if d.kind == TITLE_PAGE and (cover is None or d.page_number > cover.page_number)), None)
    if cover is None and title is None:
        return CoverDetection(CoverInfo(NONE, 0.0, None, tuple(decisions)), None, None)
    primary = cover or title
    info = CoverInfo(primary.kind, primary.confidence, primary.page_number, tuple(decisions))
    return CoverDetection(info, cover.page_number if cover else None, title.page_number if title else None)


_IMPRINT_RE = re.compile(r"\b(press|publishers?|publishing|publications|books|editions?|printing|university|copyright|(?:1[5-9]|20)\d\d)\b|©", re.IGNORECASE)
_BY_RE = re.compile(r"^(by|edited by|translated by|written by)\b", re.IGNORECASE)


def parse_title_page(page: "PageData") -> TitlePage:
    """Map a typographic title page onto title / subtitle / author / imprint roles."""
    lines: list[tuple[str, float, float]] = []
    for block in page.text_blocks:
        for line in block.lines:
            text = " ".join(normalize_invisible_separators(strip_markup(line.text)).split())
            if text:
                size = max((span.size for span in line.spans if span.text.strip()), default=block.max_font_size)
                lines.append((text, round(size * 2) / 2, line.bbox[1]))
    lines.sort(key=lambda item: item[2])
    if not lines:
        return TitlePage(page.page_number, "")

    imprints = [text for text, size, y in lines if y > page.height * 0.78 or (_IMPRINT_RE.search(text) and y > page.height * 0.5)]
    rest = [item for item in lines if item[0] not in imprints]
    if not rest:
        rest, imprints = lines, []
    sizes = sorted({size for _, size, _ in rest}, reverse=True)
    title_lines = [text for text, size, _ in rest if size == sizes[0]]
    others = [(text, size) for text, size, _ in rest if size != sizes[0]]
    subtitle = ""
    authors: list[str] = []
    if others:
        other_sizes = sorted({size for _, size in others}, reverse=True)
        candidate = " ".join(text for text, size in others if size == other_sizes[0])
        nameish = len(candidate.split()) <= 4 and all(word[:1].isupper() for word in candidate.split())
        if len(other_sizes) >= 2 and not _BY_RE.match(candidate) and not nameish:
            subtitle = candidate
            authors = [text for text, size in others if size != other_sizes[0]]
        elif len(other_sizes) == 1 and len(candidate) > 30 and not nameish and not _BY_RE.match(candidate):
            subtitle = candidate
        else:
            authors = [text for text, _ in others]
    return TitlePage(
        page_number=page.page_number,
        title=" ".join(title_lines),
        subtitle=subtitle,
        authors=tuple(authors),
        imprints=tuple(imprints),
    )


def render_cover_jpeg(pdf_path: str | Path, page_number: int) -> tuple[bytes, int, int]:
    """Render a PDF page as an sRGB JPEG that fits the 1600x2560 target box."""
    from PIL import Image, ImageOps
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            zoom = min(COVER_TARGET_SIZE[0] / width, COVER_TARGET_SIZE[1] / height)
            image = page.render(scale=zoom).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        document.close()
    if image.width > COVER_TARGET_SIZE[0] or image.height > COVER_TARGET_SIZE[1]:
        image = ImageOps.contain(image, COVER_TARGET_SIZE, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", COVER_TARGET_SIZE, "white")
    canvas.paste(image, ((COVER_TARGET_SIZE[0] - image.width) // 2, (COVER_TARGET_SIZE[1] - image.height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=90, optimize=True, subsampling=0)
    return buffer.getvalue(), canvas.width, canvas.height


def _wrap(text: str, limit: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > limit and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def synthesize_cover_svg(title: str, author: str = "", subtitle: str = "") -> bytes:
    """Typographic SVG cover used as the shelf thumbnail when no graphic cover exists."""
    width, height = COVER_TARGET_SIZE
    title_lines = _wrap(title or "Untitled", 18)[:6]
    size = 190 if len(title_lines) <= 3 else 150
    y = 900 - (len(title_lines) - 1) * size * 0.6
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
        f"<title>{escape(title or 'Untitled')}</title>",
        f'<rect width="{width}" height="{height}" fill="#1f2a44"/>',
        f'<rect x="80" y="80" width="{width - 160}" height="{height - 160}" fill="none" stroke="#c9b37e" stroke-width="6"/>',
        '<g fill="#f5efe0" font-family="Georgia, \'Times New Roman\', serif" text-anchor="middle">',
    ]
    for index, line in enumerate(title_lines):
        parts.append(f'<text x="{width // 2}" y="{int(y + index * size * 1.2)}" font-size="{size}" font-weight="bold">{escape(line)}</text>')
    next_y = int(y + len(title_lines) * size * 1.2 + 40)
    if subtitle:
        for index, line in enumerate(_wrap(subtitle, 34)[:3]):
            parts.append(f'<text x="{width // 2}" y="{next_y + index * 100}" font-size="80" font-style="italic">{escape(line)}</text>')
    if author:
        parts.append(f'<text x="{width // 2}" y="{height - 360}" font-size="100">{escape(author)}</text>')
    parts.append("</g></svg>")
    return "".join(parts).encode("utf-8")
