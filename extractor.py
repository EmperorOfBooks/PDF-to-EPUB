from __future__ import annotations

from dataclasses import dataclass, field
import io
from pathlib import Path
import re
from typing import Iterable

import ftfy
from PIL import Image
import pymupdf as fitz

from cover import CANDIDATE_PAGES, PageVisualStats, compute_visual_stats
from inline import (
    StyledRun,
    is_bold_span,
    is_italic_span,
    is_mono_font,
    render_runs,
    span_position,
    sup_tokens,
)
from tables import detect_ruled_tables, detect_whitespace_tables

VECTOR_RASTER_DPI = 300
VECTOR_MIN_SIDE = 36.0
VECTOR_MIN_PATHS = 3
VECTOR_MAX_LABEL_WORDS = 80
MAX_RASTER_SIDE_PX = 6000
BAD_GLYPH_RE = re.compile(r"\ufffd|\(cid:\d+\)")


@dataclass(frozen=True)
class SpanData:
    text: str
    font: str
    size: float
    flags: int
    origin_y: float | None = None


@dataclass(frozen=True)
class LineData:
    text: str
    bbox: tuple[float, float, float, float]
    spans: tuple[SpanData, ...]
    markup: str = ""
    sup_tokens: tuple[str, ...] = ()
    starts_with_sup: bool = False
    is_code: bool = False


@dataclass(frozen=True)
class TextBlockData:
    page_number: int
    block_index: int
    bbox: tuple[float, float, float, float]
    lines: tuple[LineData, ...]
    text: str
    max_font_size: float
    avg_font_size: float
    bold_ratio: float
    is_code: bool = False


@dataclass(frozen=True)
class ImageData:
    page_number: int
    block_index: int
    bbox: tuple[float, float, float, float]
    image_bytes: bytes
    extension: str
    alt: str = ""
    native_width: int = 0
    native_height: int = 0
    has_alpha: bool = False
    xref: int | None = None
    is_vector: bool = False


@dataclass(frozen=True)
class TableData:
    page_number: int
    bbox: tuple[float, float, float, float]
    rows: tuple[tuple[str, ...], ...]
    method: str = "ruled"


@dataclass(frozen=True)
class PageData:
    page_number: int
    width: float
    height: float
    text_blocks: tuple[TextBlockData, ...]
    images: tuple[ImageData, ...]
    tables: tuple[TableData, ...] = ()
    visual: PageVisualStats | None = None
    raw_text: str = ""
    bad_glyph_ratio: float = 0.0
    ocr_used: bool = False


@dataclass(frozen=True)
class ExtractedDocument:
    pages: tuple[PageData, ...]
    source_path: str | None = None
    created: str | None = None


class ScannedPdfError(RuntimeError):
    pass


class PdfExtractor:
    def __init__(self, input_path: str | Path, require_text: bool = True) -> None:
        self.input_path = Path(input_path)
        self.doc: fitz.Document | None = None
        self.require_text = require_text

    def extract(self) -> ExtractedDocument:
        document = fitz.open(self.input_path)
        self.doc = document
        pages: list[PageData] = []
        any_text = False
        any_images = False
        try:
            created = self._creation_date(document)
            for page_number, page in enumerate(document, start=1):
                page_data = self._extract_page(page_number, page)
                any_text = any_text or bool(page_data.text_blocks) or bool(page_data.tables)
                any_images = any_images or bool(page_data.images)
                pages.append(page_data)
        finally:
            document.close()
        if self.require_text:
            if not any_text and any_images:
                raise ScannedPdfError(
                    "The PDF appears to be scanned images without an OCR text layer. Add OCR before conversion."
                )
            if not any_text:
                raise ScannedPdfError("No extractable text layer was found in the PDF.")
        return ExtractedDocument(pages=tuple(pages), source_path=str(self.input_path), created=created)

    @staticmethod
    def _creation_date(document: fitz.Document) -> str | None:
        raw = (document.metadata or {}).get("creationDate") or ""
        match = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", raw)
        if not match:
            return None
        year, month, day, hour, minute, second = (int(group) if group else 0 for group in match.groups())
        if year < 1980:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"

    def _extract_page(self, page_number: int, page: fitz.Page) -> PageData:
        page_dict = page.get_text("dict")
        text_blocks: list[TextBlockData] = []
        images: list[ImageData] = []
        for block_index, block in enumerate(page_dict.get("blocks", [])):
            block_type = block.get("type", 0)
            if block_type == 0:
                text_block = self._parse_text_block(page_number, block_index, block)
                if text_block is not None:
                    text_blocks.append(text_block)
            elif block_type == 1:
                image_block = self._parse_image_block(page_number, block_index, block)
                if image_block is not None:
                    images.append(image_block)

        words = page.get_text("words")
        table_boxes: list[tuple[float, float, float, float]] = []
        tables: list[TableData] = []
        detected = detect_ruled_tables(page)
        detected += detect_whitespace_tables(words, exclude=[t.bbox for t in detected])
        for table in detected:
            tables.append(TableData(page_number, table.bbox, table.rows, table.method))
            table_boxes.append(table.bbox)
        if table_boxes:
            text_blocks = [block for block in text_blocks if not self._center_inside(block.bbox, table_boxes, pad=2.0)]

        figures, text_blocks = self._extract_vector_figures(page, page_number, text_blocks, images, table_boxes)
        images.extend(figures)

        raw_text = ftfy.fix_text(" ".join(str(word[4]) for word in words))
        raw_chars = len(re.sub(r"\s+", "", raw_text))
        bad_chars = sum(len(match.group(0)) for match in BAD_GLYPH_RE.finditer(raw_text))
        visual = compute_visual_stats(page) if page_number <= CANDIDATE_PAGES else None
        return PageData(
            page_number=page_number,
            width=float(page.rect.width),
            height=float(page.rect.height),
            text_blocks=tuple(text_blocks),
            images=tuple(images),
            tables=tuple(tables),
            visual=visual,
            raw_text=raw_text,
            bad_glyph_ratio=(bad_chars / raw_chars) if raw_chars else 0.0,
        )

    @staticmethod
    def _center_inside(bbox, regions, pad: float = 0.0) -> bool:
        cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
        return any(r[0] - pad <= cx <= r[2] + pad and r[1] - pad <= cy <= r[3] + pad for r in regions)

    def _extract_vector_figures(
        self,
        page: fitz.Page,
        page_number: int,
        text_blocks: list[TextBlockData],
        images: list[ImageData],
        table_boxes: list[tuple[float, float, float, float]],
    ) -> tuple[list[ImageData], list[TextBlockData]]:
        """Cluster vector paths (rects, lines, curves) and rasterize each cluster to a 300 DPI PNG."""
        try:
            drawings = page.get_drawings()
        except Exception:
            return [], text_blocks
        page_rect = page.rect
        usable = []
        horizontal_rules = []
        for drawing in drawings:
            rect = fitz.Rect(drawing["rect"])
            if rect.width >= page_rect.width * 0.9 and rect.height >= page_rect.height * 0.9:
                continue
            if rect.height <= 2.0 and rect.width > rect.height * 4:
                horizontal_rules.append(drawing)
                continue
            if table_boxes and self._center_inside(tuple(rect), table_boxes, pad=2.0):
                continue
            usable.append(drawing)
        for rule in horizontal_rules:
            rule_rect = fitz.Rect(rule["rect"]) + (-1, -1, 1, 1)
            intersections = sum(
                1 for drawing in usable if rule_rect.intersects(fitz.Rect(drawing["rect"]))
            )
            if intersections >= 2:
                usable.append(rule)
        if len(usable) < VECTOR_MIN_PATHS:
            return [], text_blocks
        try:
            clusters = page.cluster_drawings(drawings=usable, x_tolerance=6, y_tolerance=6)
        except Exception:
            return [], text_blocks

        figures: list[ImageData] = []
        remaining = list(text_blocks)
        for cluster_index, cluster in enumerate(sorted(clusters, key=lambda r: (r.y0, r.x0))):
            region = fitz.Rect(cluster) & page_rect
            if region.width < VECTOR_MIN_SIDE or region.height < VECTOR_MIN_SIDE:
                continue
            inside = sum(1 for d in usable if fitz.Rect(d["rect"]).intersects(region))
            if inside < VECTOR_MIN_PATHS:
                continue
            if any(self._overlap_ratio(image.bbox, region) >= 0.5 for image in images):
                continue
            labels = [block for block in remaining if self._center_inside(block.bbox, [tuple(region)], pad=2.0)]
            label_words = sum(len(block.text.split()) for block in labels)
            if label_words > VECTOR_MAX_LABEL_WORDS:
                continue
            clip = fitz.Rect(region.x0 - 2, region.y0 - 2, region.x1 + 2, region.y1 + 2) & page_rect
            zoom = VECTOR_RASTER_DPI / 72.0
            zoom = min(zoom, MAX_RASTER_SIDE_PX / max(clip.width, clip.height))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csRGB, alpha=False)
            alt = " ".join(" ".join(block.text.split()) for block in labels)[:400] or "Diagram"
            figures.append(
                ImageData(
                    page_number=page_number,
                    block_index=10_000 + cluster_index,
                    bbox=tuple(float(v) for v in clip),
                    image_bytes=pixmap.tobytes("png"),
                    extension="png",
                    alt=alt,
                    native_width=pixmap.width,
                    native_height=pixmap.height,
                    xref=None,
                    is_vector=True,
                )
            )
            label_ids = {id(block) for block in labels}
            remaining = [block for block in remaining if id(block) not in label_ids]
        return figures, remaining

    @staticmethod
    def _overlap_ratio(bbox, region: fitz.Rect) -> float:
        rect = fitz.Rect(bbox)
        area = rect.width * rect.height
        if area <= 0:
            return 0.0
        inter = rect & region
        return (inter.width * inter.height) / area if not inter.is_empty else 0.0

    def _parse_text_block(self, page_number: int, block_index: int, block: dict) -> TextBlockData | None:
        lines: list[LineData] = []
        spans: list[SpanData] = []
        texts: list[str] = []
        for line in block.get("lines", []):
            line_spans: list[SpanData] = []
            runs: list[StyledRun] = []
            raw_spans = [span for span in line.get("spans", []) if span.get("text", "")]
            if not raw_spans:
                continue
            dominant = max(raw_spans, key=lambda span: len(span["text"].strip()))
            base_size = float(dominant.get("size", 0.0))
            base_y = dominant.get("origin", (0.0, None))[1] if dominant.get("origin") else None
            first_visible = next((span for span in raw_spans if span["text"].strip()), None)
            starts_with_sup = False
            all_mono = True
            for span in raw_spans:
                text = span["text"]
                font = str(span.get("font", ""))
                flags = int(span.get("flags", 0))
                size = float(span.get("size", 0.0))
                origin_y = span["origin"][1] if span.get("origin") else None
                span_data = SpanData(text=text, font=font, size=size, flags=flags, origin_y=origin_y)
                line_spans.append(span_data)
                spans.append(span_data)
                position = span_position(size, origin_y, base_size, base_y, flags)
                if span is first_visible and position == "sup":
                    starts_with_sup = True
                runs.append(StyledRun(text, is_bold_span(font, flags), is_italic_span(font, flags), position))
                if text.strip():
                    if not is_mono_font(font, flags):
                        all_mono = False
            line_text = "".join(span.text for span in line_spans).strip()
            if not line_text:
                continue
            is_code = all_mono
            markup = render_runs(runs).strip()
            lines.append(
                LineData(
                    text=line_text,
                    bbox=tuple(float(value) for value in line.get("bbox", block.get("bbox", (0, 0, 0, 0)))),
                    spans=tuple(line_spans),
                    markup=markup,
                    sup_tokens=tuple(sup_tokens(markup)),
                    starts_with_sup=starts_with_sup,
                    is_code=is_code,
                )
            )
            texts.append(line_text)
        if not lines:
            return None
        sizes = [span.size for span in spans if span.text.strip()]
        max_font_size = max(sizes) if sizes else 0.0
        avg_font_size = sum(sizes) / len(sizes) if sizes else 0.0
        bold_ratio = self._bold_ratio(spans)
        return TextBlockData(
            page_number=page_number,
            block_index=block_index,
            bbox=tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0))),
            lines=tuple(lines),
            text="\n".join(texts).strip(),
            max_font_size=max_font_size,
            avg_font_size=avg_font_size,
            bold_ratio=bold_ratio,
            is_code=(
                len(lines) >= 1
                and all(line.is_code for line in lines)
                and sum(len(line.text) for line in lines) >= 8
            ),
        )

    def _parse_image_block(self, page_number: int, block_index: int, block: dict) -> ImageData | None:
        image_bytes = block.get("image")
        if not image_bytes:
            return None
        native_width = int(block.get("width", 0) or 0)
        native_height = int(block.get("height", 0) or 0)
        if native_width and native_height and (native_width < 15 or native_height < 15):
            return None
        extension = str(block.get("ext", "png"))
        alpha = bool(block.get("alpha", 0))
        xref = int(block["xref"]) if block.get("xref") else None
        if xref and self.doc is not None:
            image_bytes, extension, alpha, native_width, native_height = self._extract_xref_image(
                xref,
                image_bytes,
                extension,
                alpha,
                native_width,
                native_height,
            )
            if not image_bytes:
                return None
        return ImageData(
            page_number=page_number,
            block_index=block_index,
            bbox=tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0))),
            image_bytes=image_bytes,
            extension=extension,
            alt="",
            native_width=native_width,
            native_height=native_height,
            has_alpha=alpha,
            xref=xref,
        )

    def _extract_xref_image(
        self,
        xref: int,
        image_bytes: bytes,
        extension: str,
        alpha: bool,
        native_width: int,
        native_height: int,
    ) -> tuple[bytes, str, bool, int, int]:
        """Preserve ordinary streams and compose an XRef-backed soft mask when present."""
        try:
            assert self.doc is not None
            _, smask_value = self.doc.xref_get_key(xref, "SMask")
            smask_match = re.search(r"\b(\d+)\s+0\s+R\b", smask_value or "")
            base_pixmap = fitz.Pixmap(self.doc, xref)
            native_width = native_width or base_pixmap.width
            native_height = native_height or base_pixmap.height
            if native_width < 15 or native_height < 15:
                return b"", extension, alpha, native_width, native_height
            if not smask_match:
                return image_bytes, extension, alpha or base_pixmap.alpha, native_width, native_height
            mask_pixmap = fitz.Pixmap(self.doc, int(smask_match.group(1)))
            if base_pixmap.colorspace is not None and base_pixmap.colorspace.n >= 4:
                base_pixmap = fitz.Pixmap(fitz.csRGB, base_pixmap)
            base_image = Image.frombytes("RGB", [base_pixmap.width, base_pixmap.height], base_pixmap.samples)
            mask_image = Image.frombytes("L", [mask_pixmap.width, mask_pixmap.height], mask_pixmap.samples)
            if mask_image.size != base_image.size:
                mask_image = mask_image.resize(base_image.size, Image.Resampling.LANCZOS)
            base_image.putalpha(mask_image)
            buffer = io.BytesIO()
            base_image.save(buffer, format="PNG")
            return buffer.getvalue(), "png", True, base_image.width, base_image.height
        except (RuntimeError, ValueError, AssertionError):
            return image_bytes, extension, alpha, native_width, native_height

    def _bold_ratio(self, spans: Iterable[SpanData]) -> float:
        span_list = [span for span in spans if span.text.strip()]
        if not span_list:
            return 0.0
        bold_count = sum(1 for span in span_list if self._is_bold(span.font, span.flags))
        return bold_count / len(span_list)

    def _is_bold(self, font_name: str, flags: int) -> bool:
        lowered = font_name.lower()
        if any(keyword in lowered for keyword in ("bold", "black", "heavy", "semibold", "demi")):
            return True
        return bool(flags & 16)
