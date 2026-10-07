from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
import time

from builder import EpubBuilder
from cleaner import CleanedDocument, PdfCleaner

from extractor import ExtractedDocument, PageData, PdfExtractor, ScannedPdfError, TextBlockData, LineData
from office_extractor import OfficeExtractor
from ocr import OcrEngine, OcrEngineUnavailable
from telemetry import ConversionStats, build_report, recall_gate_failed, tokenize_words, write_report

logger = logging.getLogger("pdf2epub")

OCR_MIN_CHARS = 50
OCR_BAD_GLYPH_RATIO = 0.30
OCR_MIN_PAGE_AREA = 40000.0


def needs_ocr(char_count: int, bad_glyph_ratio: float, page_width: float, page_height: float) -> bool:
    return (
        (char_count < OCR_MIN_CHARS and page_width * page_height >= OCR_MIN_PAGE_AREA)
        or bad_glyph_ratio >= OCR_BAD_GLYPH_RATIO
    )


def route_pages(
    document: ExtractedDocument,
    stats: ConversionStats,
    ocr_engine=None,
) -> ExtractedDocument:
    """OCR only deficient pages; preserve the digital extraction on all other pages."""
    engine = ocr_engine if ocr_engine is not None else OcrEngine()
    engine_name = getattr(engine, "name", None) or (
        None if isinstance(engine, OcrEngine) else engine.__class__.__name__
    )
    updated: list[PageData] = []
    pdf = None
    for page in document.pages:
        raw_text = page.raw_text or "\n".join(block.text for block in page.text_blocks)
        char_count = len("".join(raw_text.split()))
        if not needs_ocr(char_count, page.bad_glyph_ratio, page.width, page.height):
            updated.append(replace(page, raw_text=raw_text))
            continue
        if engine_name is None:
            stats.ocr_skipped.append({"page_number": page.page_number, "reason": "no_ocr_engine"})
            updated.append(replace(page, raw_text=raw_text))
            continue
        try:
            import pypdfium2 as pdfium

            if pdf is None:
                pdf = pdfium.PdfDocument(document.source_path)
            pdf_page = pdf[page.page_number - 1]
            try:
                image = pdf_page.render(scale=300 / 72).to_pil().convert("RGB")
            finally:
                pdf_page.close()
            text = engine.recognize(image)
            blocks = _ocr_text_blocks(text, page)
            kept_images = []
            for image_data in page.images:
                bbox = image_data.bbox
                coverage = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
                if page.width * page.height and coverage / (page.width * page.height) >= 0.85:
                    stats.drop(page.page_number, bbox, "ocr_replaced_scan")
                else:
                    kept_images.append(image_data)
            updated.append(
                replace(page, text_blocks=blocks, images=tuple(kept_images), raw_text=text, ocr_used=True)
            )
            stats.ocr_pages.append({"page_number": page.page_number, "characters": len(text)})
            stats.ocr_engine = engine_name
        except OcrEngineUnavailable:
            stats.ocr_skipped.append({"page_number": page.page_number, "reason": "no_ocr_engine"})
            updated.append(replace(page, raw_text=raw_text))
        except Exception as error:
            stats.warnings.append(f"OCR failed on page {page.page_number}: {error}")
            updated.append(replace(page, raw_text=raw_text))
    if pdf is not None:
        pdf.close()
    stats.scanned = bool(document.pages) and all(not page.text_blocks and not page.tables for page in document.pages)
    if not any(page.text_blocks or page.tables for page in updated):
        raise ScannedPdfError("The PDF has no usable text. OCR is unavailable or did not recover text.")
    return replace(document, pages=tuple(updated))


def _ocr_text_blocks(text: str, page: PageData) -> tuple[TextBlockData, ...]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ()
    line_height = page.height / len(lines)
    blocks = []
    for index, value in enumerate(lines):
        y0 = min(page.height, index * line_height)
        y1 = min(page.height, y0 + line_height)
        bbox = (page.width * 0.04, y0, page.width * 0.96, y1)
        line = LineData(value, bbox, (), markup=value)
        blocks.append(
            TextBlockData(
                page_number=page.page_number,
                block_index=index,
                bbox=bbox,
                lines=(line,),
                text=value,
                max_font_size=12.0,
                avg_font_size=12.0,
                bold_ratio=0.0,
            )
        )
    return tuple(blocks)


class ConversionPipeline:
    def __init__(
        self,
        input_path: str | Path,
        output_path: str | Path,
        title: str | None = None,
        include_colophon: bool = False,
        ocr_heuristics: bool = False,
        report_path: str | Path | None = None,
        ocr_engine=None,
    ) -> None:
        self.input_path = Path(input_path).expanduser().resolve()
        self.output_path = Path(output_path).expanduser().resolve()
        self.report_path = (
            Path(report_path).expanduser().resolve()
            if report_path is not None
            else self.output_path.with_suffix(".report.json")
        )
        self.title = title or self.input_path.stem
        self.include_colophon = include_colophon
        self.ocr_heuristics = ocr_heuristics
        self.ocr_engine = ocr_engine

    def run(self) -> Path:
        started = time.perf_counter()
        stats = ConversionStats(source_format=self.input_path.suffix.lower().lstrip("."))
        if self.input_path.suffix.lower() == ".pdf":
            logger.info("Extracting PDF pages from %s", self.input_path)
            extracted = PdfExtractor(self.input_path, require_text=False).extract()
            extracted = route_pages(extracted, stats, self.ocr_engine)
            stats.input_tokens = [token for page in extracted.pages for token in tokenize_words(page.raw_text)]
            cleaned = PdfCleaner(ocr_heuristics=self.ocr_heuristics, stats=stats).clean(extracted)
        else:
            cleaned = OfficeExtractor(self.input_path).extract()

        result = EpubBuilder(self.output_path, title=self.title).build(
            cleaned, include_colophon=self.include_colophon
        )
        report = build_report(
            stats=stats,
            document=cleaned,
            chapter_files=result.chapter_files,
            runtime_seconds=time.perf_counter() - started,
            input_path=self.input_path,
            output_path=result.output_path,
            counts=result.counts or {},
        )
        write_report(self.report_path, report)
        if recall_gate_failed(report):
            logger.error(
                "Word recall %.4f is below required threshold %.2f",
                report["words"]["recall"],
                report["words"]["recall_threshold"],
            )
        return result.output_path


def convert_pdf(input_path: str | Path, output_path: str | Path, title: str | None = None) -> Path:
    try:
        return ConversionPipeline(input_path=input_path, output_path=output_path, title=title).run()
    except ScannedPdfError:
        raise
    except Exception as error:  # pragma: no cover - explicit pass-through for CLI handling
        raise RuntimeError(f"Conversion failed: {error}") from error
