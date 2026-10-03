from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import re
from typing import Iterable

from PIL import Image
import pymupdf as fitz


@dataclass(frozen=True)
class SpanData:
    text: str
    font: str
    size: float
    flags: int


@dataclass(frozen=True)
class LineData:
    text: str
    bbox: tuple[float, float, float, float]
    spans: tuple[SpanData, ...]


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


@dataclass(frozen=True)
class PageData:
    page_number: int
    width: float
    height: float
    text_blocks: tuple[TextBlockData, ...]
    images: tuple[ImageData, ...]


@dataclass(frozen=True)
class ExtractedDocument:
    pages: tuple[PageData, ...]


class ScannedPdfError(RuntimeError):
    pass


class PdfExtractor:
    def __init__(self, input_path: str | Path) -> None:
        self.input_path = Path(input_path)
        self.doc: fitz.Document | None = None

    def extract(self) -> ExtractedDocument:
        document = fitz.open(self.input_path)
        self.doc = document
        pages: list[PageData] = []
        any_text = False
        any_images = False
        try:
            for page_number, page in enumerate(document, start=1):
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
                any_text = any_text or bool(text_blocks)
                any_images = any_images or bool(images)
                pages.append(
                    PageData(
                        page_number=page_number,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        text_blocks=tuple(text_blocks),
                        images=tuple(images),
                    )
                )
        finally:
            document.close()
        if not any_text and any_images:
            raise ScannedPdfError(
                "The PDF appears to be scanned images without an OCR text layer. Add OCR before conversion."
            )
        if not any_text:
            raise ScannedPdfError("No extractable text layer was found in the PDF.")
        return ExtractedDocument(pages=tuple(pages))

    def _parse_text_block(self, page_number: int, block_index: int, block: dict) -> TextBlockData | None:
        lines: list[LineData] = []
        spans: list[SpanData] = []
        texts: list[str] = []
        for line in block.get("lines", []):
            line_spans: list[SpanData] = []
            line_text_parts: list[str] = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                span_data = SpanData(
                    text=text,
                    font=str(span.get("font", "")),
                    size=float(span.get("size", 0.0)),
                    flags=int(span.get("flags", 0)),
                )
                line_spans.append(span_data)
                spans.append(span_data)
                line_text_parts.append(text)
            if line_text_parts:
                line_text = "".join(line_text_parts).strip()
                if line_text:
                    lines.append(
                        LineData(
                            text=line_text,
                            bbox=tuple(float(value) for value in line.get("bbox", block.get("bbox", (0, 0, 0, 0)))),
                            spans=tuple(line_spans),
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
