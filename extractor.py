from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import re
from typing import Callable, Iterable

import ftfy
from PIL import Image
import pypdfium2 as pdfium

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
from tables import DetectedTable, detect_ruled_tables, detect_whitespace_tables


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


_LINE_BREAK_CHARS = frozenset({"\r", "\n"})
_MIN_IMAGE_DIMENSION = 15
_BOLD_WEIGHT_THRESHOLD = 600
BAD_GLYPH_RE = re.compile(r"\ufffd|\(cid:\d+\)")


class PdfExtractor:
    """Extracts page geometry/text/images from a PDF using pypdfium2 (Apache-2.0 / BSD-3-Clause)."""

    def __init__(self, input_path: str | Path, require_text: bool = True) -> None:
        self.input_path = Path(input_path)
        self.doc: pdfium.PdfDocument | None = None
        self.require_text = require_text

    def extract(self) -> ExtractedDocument:
        document = pdfium.PdfDocument(self.input_path)
        self.doc = document
        pages: list[PageData] = []
        any_text = False
        any_images = False
        try:
            created = self._creation_date(document)
            for page_index in range(len(document)):
                page_number = page_index + 1
                page = document[page_index]
                try:
                    width, height = page.get_size()
                    page_height = float(height)
                    text_blocks = self._extract_text_blocks(page, page_number, page_height)
                    images = self._extract_images(page, page_number, page_height)
                    words = self._extract_words(page, page_height)
                    detected_tables = detect_ruled_tables(page, words)
                    detected_tables.extend(
                        detect_whitespace_tables(words, exclude=[table.bbox for table in detected_tables])
                    )
                    tables = tuple(
                        TableData(page_number, table.bbox, table.rows, table.method)
                        for table in detected_tables
                    )
                    visual = compute_visual_stats(page) if page_number <= CANDIDATE_PAGES else None
                    raw_text = ftfy.fix_text(" ".join(word[4] for word in words))
                    raw_chars = len(re.sub(r"\s+", "", raw_text))
                    bad_chars = sum(len(match.group(0)) for match in BAD_GLYPH_RE.finditer(raw_text))
                finally:
                    page.close()
                any_text = any_text or bool(text_blocks) or bool(tables)
                any_images = any_images or bool(images)
                pages.append(
                    PageData(
                        page_number=page_number,
                        width=float(width),
                        height=page_height,
                        text_blocks=tuple(text_blocks),
                        images=tuple(images),
                        tables=tables,
                        visual=visual,
                        raw_text=raw_text,
                        bad_glyph_ratio=(bad_chars / raw_chars) if raw_chars else 0.0,
                    )
                )
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
    def _creation_date(document: pdfium.PdfDocument) -> str | None:
        metadata = document.get_metadata_dict(skip_empty=True)
        raw = metadata.get("CreationDate", "")
        match = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", raw)
        if not match:
            return None
        year, month, day, hour, minute, second = (int(group) if group else 0 for group in match.groups())
        if year < 1980:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"

    def _extract_words(self, page: pdfium.PdfPage, page_height: float) -> list[tuple]:
        textpage = page.get_textpage()
        words: list[tuple] = []
        chars: list[tuple[str, tuple[float, float, float, float]]] = []

        def flush() -> None:
            if not chars:
                return
            text = "".join(char for char, _ in chars)
            boxes = [box for _, box in chars]
            words.append(
                (
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                    text,
                )
            )
            chars.clear()

        try:
            for index in range(textpage.count_chars()):
                char = textpage.get_text_range(index, 1)
                if not char or char.isspace():
                    flush()
                    continue
                box = self._flip_box(textpage.get_charbox(index), page_height)
                chars.append((char, box))
            flush()
        finally:
            textpage.close()
        return words

    def _flip_box(
        self, box: tuple[float, float, float, float], page_height: float
    ) -> tuple[float, float, float, float]:
        """Convert pdfium's bottom-left-origin (left, bottom, right, top) box to a
        top-left-origin (x0, y0, x1, y1) box, matching the convention the rest of the
        pipeline (layout.py/cleaner.py) expects."""
        left, bottom, right, top = box
        return (float(left), page_height - float(top), float(right), page_height - float(bottom))

    def _extract_text_blocks(self, page: pdfium.PdfPage, page_number: int, page_height: float) -> list[TextBlockData]:
        textpage = page.get_textpage()
        blocks: list[TextBlockData] = []
        try:
            total_chars = textpage.count_chars()
            font_cache: dict[int, tuple[str, float, int, float | None]] = {}

            def char_font(index: int) -> tuple[str, float, int, float | None]:
                cached = font_cache.get(index)
                if cached is not None:
                    return cached
                result = ("", 0.0, 0, None)
                try:
                    text_obj = textpage.get_textobj(index)
                except Exception:
                    text_obj = None
                if text_obj is not None:
                    try:
                        size = float(text_obj.get_font_size() or 0.0)
                        font = text_obj.get_font()
                        name = str(font.get_base_name() or "")
                        weight = int(font.get_weight() or 0)
                        flags = 16 if weight >= _BOLD_WEIGHT_THRESHOLD else 0
                        origin_y = page_height - float(text_obj.get_matrix().f)
                        result = (name, size, flags, origin_y)
                    except Exception:
                        pass
                font_cache[index] = result
                return result

            block_index = 0
            line_start = 0
            index = 0
            while index <= total_chars:
                is_break = index == total_chars or textpage.get_text_range(index, 1) in _LINE_BREAK_CHARS
                if is_break:
                    if index > line_start:
                        block = self._build_line_block(
                            textpage, page_number, block_index, line_start, index, page_height, char_font
                        )
                        if block is not None:
                            blocks.append(block)
                            block_index += 1
                    line_start = index + 1
                index += 1
        finally:
            textpage.close()
        return blocks

    def _build_line_block(
        self,
        textpage: pdfium.PdfTextPage,
        page_number: int,
        block_index: int,
        start: int,
        end: int,
        page_height: float,
        char_font: Callable[[int], tuple[str, float, int, float | None]],
    ) -> TextBlockData | None:
        chars: list[tuple[str, tuple[float, float, float, float], str, float, int, float | None]] = []
        for i in range(start, end):
            text = textpage.get_text_range(i, 1)
            box = textpage.get_charbox(i)
            name, size, flags, origin_y = char_font(i)
            chars.append((text, box, name, size, flags, origin_y))

        line_text = "".join(item[0] for item in chars)
        if not line_text.strip():
            return None

        lefts = [item[1][0] for item in chars]
        rights = [item[1][2] for item in chars]
        bottoms = [item[1][1] for item in chars]
        tops = [item[1][3] for item in chars]
        x0, x1 = min(lefts), max(rights)
        y0 = page_height - max(tops)
        y1 = page_height - min(bottoms)
        bbox = (x0, y0, x1, y1)

        spans = self._group_spans(chars)
        dominant = max(spans, key=lambda span: len(span.text.strip()), default=None)
        base_size = dominant.size if dominant else 0.0
        base_y = dominant.origin_y if dominant else None
        positions = [
            span_position(span.size, span.origin_y, base_size, base_y, span.flags)
            for span in spans
        ]
        markup = render_runs(
            [
                StyledRun(
                    span.text,
                    is_bold_span(span.font, span.flags),
                    is_italic_span(span.font, span.flags),
                    position,
                )
                for span, position in zip(spans, positions)
            ]
        ).strip()
        first_visible_position = next(
            (position for span, position in zip(spans, positions) if span.text.strip()),
            "base",
        )
        line_data = LineData(
            text=line_text.strip(),
            bbox=bbox,
            spans=spans,
            markup=markup,
            sup_tokens=tuple(sup_tokens(markup)),
            starts_with_sup=first_visible_position == "sup",
        )
        sizes = [span.size for span in spans if span.text.strip()]
        max_font_size = max(sizes) if sizes else 0.0
        avg_font_size = sum(sizes) / len(sizes) if sizes else 0.0
        bold_ratio = self._bold_ratio(spans)
        visible_spans = [span for span in spans if span.text.strip()]
        is_code = bool(visible_spans) and all(is_mono_font(span.font, span.flags) for span in visible_spans)
        return TextBlockData(
            page_number=page_number,
            block_index=block_index,
            bbox=bbox,
            lines=(line_data,),
            text=line_data.text,
            max_font_size=max_font_size,
            avg_font_size=avg_font_size,
            bold_ratio=bold_ratio,
            is_code=is_code,
        )

    def _group_spans(
        self,
        chars: Iterable[
            tuple[str, tuple[float, float, float, float], str, float, int, float | None]
        ],
    ) -> tuple[SpanData, ...]:
        spans: list[SpanData] = []
        cur_name: str | None = None
        cur_size = 0.0
        cur_flags = 0
        cur_origin_y: float | None = None
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                spans.append(
                    SpanData(
                        text="".join(buffer),
                        font=cur_name or "",
                        size=cur_size,
                        flags=cur_flags,
                        origin_y=cur_origin_y,
                    )
                )
                buffer.clear()

        for text, _box, name, size, flags, origin_y in chars:
            has_font = bool(name) or size > 0.0
            if not has_font:
                if buffer:
                    buffer.append(text)
                continue
            if cur_name is None:
                cur_name, cur_size, cur_flags, cur_origin_y = name, size, flags, origin_y
            elif (
                name != cur_name
                or size != cur_size
                or flags != cur_flags
                or (
                    origin_y is not None
                    and cur_origin_y is not None
                    and abs(origin_y - cur_origin_y) > 1.5
                )
            ):
                flush()
                cur_name, cur_size, cur_flags, cur_origin_y = name, size, flags, origin_y
            buffer.append(text)
        flush()
        if not spans:
            spans.append(
                SpanData(
                    text="".join(item[0] for item in chars),
                    font="",
                    size=0.0,
                    flags=0,
                )
            )
        return tuple(spans)

    def _extract_images(self, page: pdfium.PdfPage, page_number: int, page_height: float) -> list[ImageData]:
        images: list[ImageData] = []
        block_index = 0
        for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
            try:
                bitmap = obj.get_bitmap(render=True)
                pil_image = bitmap.to_pil()
            except Exception:
                continue
            image_bytes, extension, has_alpha = self._encode_image(pil_image)
            if image_bytes is None:
                continue
            try:
                bbox = self._flip_box(obj.get_bounds(), page_height)
            except Exception:
                bbox = (0.0, 0.0, 0.0, 0.0)
            block = {
                "image": image_bytes,
                "width": pil_image.width,
                "height": pil_image.height,
                "ext": extension,
                "alpha": has_alpha,
                "bbox": bbox,
            }
            image_data = self._parse_image_block(page_number, block_index, block)
            if image_data is not None:
                images.append(image_data)
                block_index += 1
        return images

    def _encode_image(self, pil_image: Image.Image) -> tuple[bytes | None, str, bool]:
        """Normalize any colorspace (CMYK, palette-with-transparency, etc.) pdfium's
        renderer hands back into a web-safe PNG or JPEG payload."""
        try:
            if pil_image.mode == "CMYK":
                pil_image = pil_image.convert("RGB")
            if pil_image.mode in ("RGBA", "LA") or (pil_image.mode == "P" and "transparency" in pil_image.info):
                pil_image = pil_image.convert("RGBA")
                buffer = io.BytesIO()
                pil_image.save(buffer, format="PNG")
                return buffer.getvalue(), "png", True
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
            buffer = io.BytesIO()
            pil_image.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue(), "jpg", False
        except (OSError, ValueError):
            return None, "png", False

    def _parse_image_block(self, page_number: int, block_index: int, block: dict) -> ImageData | None:
        image_bytes = block.get("image")
        if not image_bytes:
            return None
        native_width = int(block.get("width", 0) or 0)
        native_height = int(block.get("height", 0) or 0)
        if native_width and native_height and (native_width < _MIN_IMAGE_DIMENSION or native_height < _MIN_IMAGE_DIMENSION):
            return None
        extension = str(block.get("ext", "png"))
        alpha = bool(block.get("alpha", 0))
        bbox = tuple(float(value) for value in block.get("bbox", (0.0, 0.0, 0.0, 0.0)))
        return ImageData(
            page_number=page_number,
            block_index=block_index,
            bbox=bbox,
            image_bytes=image_bytes,
            extension=extension,
            alt="",
            native_width=native_width,
            native_height=native_height,
            has_alpha=alpha,
            xref=None,
        )

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
