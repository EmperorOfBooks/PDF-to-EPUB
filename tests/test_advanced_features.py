from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pymupdf as fitz
from PIL import Image
import pytest

from builder import EpubBuilder
from cleaner import ChapterContent, CleanedDocument, FlowNoteUnit, FlowTableUnit, FlowTextUnit, PdfCleaner
from cover import COVER_IMAGE, TITLE_PAGE, CoverInfo, TitlePage, detect_cover, render_cover_jpeg
from epubcheck_harvester import select_epubcheck_asset
from extractor import ExtractedDocument, ImageData, LineData, PageData, PdfExtractor, SpanData, TextBlockData
from inline import (
    EM_CLOSE,
    EM_OPEN,
    NOTE_CLOSE,
    NOTE_MID,
    NOTE_OPEN,
    STRONG_CLOSE,
    STRONG_OPEN,
    SUB_CLOSE,
    SUB_OPEN,
    SUP_CLOSE,
    SUP_OPEN,
)
from pipeline import ConversionPipeline, needs_ocr, route_pages
from telemetry import ConversionStats, build_report, validate_report


def _block(text: str, bbox, size: float = 12.0, *, markup: str | None = None, supers: tuple[str, ...] = ()):
    line = LineData(
        text=text,
        bbox=bbox,
        spans=(SpanData(text=text, font="Times", size=size, flags=0),),
        markup=markup or text,
        sup_tokens=supers,
    )
    return TextBlockData(
        page_number=1,
        block_index=0,
        bbox=bbox,
        lines=(line,),
        text=text,
        max_font_size=size,
        avg_font_size=size,
        bold_ratio=0.0,
    )


def test_cover_and_titlepage_classifier_on_ten_sample_pdfs():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for index in range(5):
            path = root / f"cover-{index}.pdf"
            pdf = fitz.open()
            page = pdf.new_page(width=612, height=792)
            buffer = io.BytesIO()
            Image.new("RGB", (600, 780), (28 + index, 42, 85)).save(buffer, format="PNG")
            page.insert_image(page.rect, stream=buffer.getvalue())
            page.insert_text((80, 150), f"Sample Book {index}", fontsize=24, color=(1, 1, 1))
            page.insert_text((80, 200), "by A. Writer", fontsize=14, color=(1, 1, 1))
            pdf.save(path)
            pdf.close()
            assert detect_cover(PdfExtractor(path).extract().pages).info.kind == COVER_IMAGE

        for index in range(5):
            path = root / f"titlepage-{index}.pdf"
            pdf = fitz.open()
            page = pdf.new_page(width=612, height=792)
            page.insert_text((140, 150), f"Sample Title {index}", fontsize=28)
            page.insert_text((170, 300), "A Collected Work", fontsize=16)
            page.insert_text((190, 460), "By A. Author", fontsize=14)
            page.insert_text((180, 680), "Example Press 2024", fontsize=12)
            pdf.save(path)
            pdf.close()
            assert detect_cover(PdfExtractor(path).extract().pages).info.kind == TITLE_PAGE


def test_rendered_cover_jpeg_uses_srgb_target_canvas():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "cover.pdf"
        pdf = fitz.open()
        page = pdf.new_page(width=612, height=792)
        page.draw_rect(page.rect, color=(0.1, 0.2, 0.4), fill=(0.1, 0.2, 0.4))
        pdf.save(source)
        pdf.close()
        content, width, height = render_cover_jpeg(source, 1)
    image = Image.open(io.BytesIO(content))
    assert image.format == "JPEG"
    assert image.mode == "RGB"
    assert (width, height) == (1600, 2560)


def test_footnote_markup_has_bidirectional_epub_links():
    marker = f"{SUP_OPEN}1{SUP_CLOSE}"
    page = PageData(
        1,
        612,
        792,
        (
            _block("Body text 1 continues.", (40, 100, 400, 120), markup=f"Body text {marker} continues.", supers=("1",)),
            _block("1. A linked note.", (40, 650, 400, 665), size=8),
        ),
        (),
    )
    units = PdfCleaner()._page_items(page, 12, set())
    chapter = ChapterContent("Notes", tuple(units))
    html_item, _ = EpubBuilder("notes.epub")._build_chapter(1, chapter)
    content = html_item.content.decode()
    assert '<a href="#fn1" id="fnref1" epub:type="noteref">1</a>' in content
    assert '<aside id="fn1" epub:type="footnote" role="note">' in content
    assert '<a href="#fnref1">1.</a>' in content


def test_table_emits_structured_xhtml():
    table = FlowTableUnit(
        kind="table",
        y=10,
        page_number=1,
        rows=(("Name", "Value"), ("alpha", "1")),
        bbox=(0, 0, 100, 40),
    )
    chapter = ChapterContent("Table", (table,))
    html_item, _ = EpubBuilder("table.epub")._build_chapter(1, chapter)
    content = html_item.content.decode()
    assert "<table><thead><tr><th scope=\"col\">Name</th>" in content
    assert "<tbody><tr><td>alpha</td><td>1</td></tr></tbody></table>" in content


def test_ruled_pdf_table_is_extracted_as_rows_and_cells():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "table.pdf"
        pdf = fitz.open()
        page = pdf.new_page(width=612, height=792)
        columns, rows = (100, 250, 400), (150, 190, 230)
        for x in columns:
            page.draw_line((x, rows[0]), (x, rows[-1]))
        for y in rows:
            page.draw_line((columns[0], y), (columns[-1], y))
        for value, x, y in (("Name", 110, 176), ("Value", 260, 176), ("alpha", 110, 216), ("1", 260, 216)):
            page.insert_text((x, y), value)
        pdf.save(source)
        pdf.close()
        extracted = PdfExtractor(source, require_text=False).extract()
    assert extracted.pages[0].tables
    assert extracted.pages[0].tables[0].rows == (("Name", "Value"), ("alpha", "1"))


def test_inline_bold_italic_and_baseline_markup_renders_semantically():
    styled = (
        f"{STRONG_OPEN}bold{STRONG_CLOSE} "
        f"{EM_OPEN}italic{EM_CLOSE} "
        f"{SUP_OPEN}up{SUP_CLOSE} {SUB_OPEN}down{SUB_CLOSE}"
    )
    chapter = ChapterContent("Inline", (FlowTextUnit("paragraph", styled, 10, 1, 12, 0),))
    html_item, _ = EpubBuilder("inline.epub")._build_chapter(1, chapter)
    content = html_item.content.decode()
    assert "<strong>bold</strong>" in content
    assert "<em>italic</em>" in content
    assert "<sup>up</sup>" in content
    assert "<sub>down</sub>" in content


def test_per_page_ocr_only_replaces_scan_page_and_records_drop():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "mixed.pdf"
        pdf = fitz.open()
        digital = pdf.new_page(width=612, height=792)
        digital.insert_text((40, 80), "This digital page already contains more than fifty useful characters for extraction.")
        scanned = pdf.new_page(width=612, height=792)
        image_bytes = io.BytesIO()
        Image.new("RGB", (850, 1100), "white").save(image_bytes, format="PNG")
        scanned.insert_image(scanned.rect, stream=image_bytes.getvalue())
        pdf.save(source)
        pdf.close()

        extracted = PdfExtractor(source, require_text=False).extract()

        class FakeEngine:
            name = "test-ocr"

            def recognize(self, image):
                return "OCR recovered this scanned page with enough recognized words to distinguish a regular interior text page from a sparse graphic cover image, while retaining all extracted content."

        stats = ConversionStats()
        routed = route_pages(extracted, stats, FakeEngine())
        assert not routed.pages[0].ocr_used
        assert routed.pages[1].ocr_used
        assert "enough recognized words" in routed.pages[1].raw_text
        assert not routed.pages[1].images
        assert stats.ocr_engine == "test-ocr"
        assert stats.drops[0].reason == "ocr_replaced_scan"
        assert not stats.scanned


def test_ocr_thresholds_are_page_local():
    assert needs_ocr(49, 0.0, 612, 792)
    assert needs_ocr(200, 0.30, 612, 792)
    assert not needs_ocr(50, 0.0, 612, 792)
    assert not needs_ocr(5, 0.0, 100, 100)


def test_vector_cluster_is_cropped_to_png():
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "diagram.pdf"
        pdf = fitz.open()
        page = pdf.new_page(width=612, height=792)
        for offset in range(5):
            page.draw_rect(
                fitz.Rect(100 + offset * 8, 150 + offset * 8, 220 - offset * 8, 260 - offset * 8),
                color=(0, 0, 0),
            )
        pdf.save(source)
        pdf.close()
        extracted = PdfExtractor(source, require_text=False).extract()
        vector_images = [image for image in extracted.pages[0].images if image.is_vector]
        assert vector_images
        assert vector_images[0].extension == "png"
        assert vector_images[0].image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert vector_images[0].native_width > 100
        assert vector_images[0].bbox[0] >= 90 and vector_images[0].bbox[2] <= 230


def test_report_schema_and_deterministic_epub_bytes():
    chapter = ChapterContent(
        "Chapter 1",
        (
            FlowTextUnit("heading", "Chapter 1", 1, 1, 18, 0.8, is_chapter_heading=True),
            FlowTextUnit("paragraph", "A deterministic paragraph.", 20, 1, 12, 0),
        ),
    )
    document = CleanedDocument((chapter,), created="2024-01-02T03:04:05Z")
    with tempfile.TemporaryDirectory() as temporary:
        first = Path(temporary) / "first.epub"
        second = Path(temporary) / "second.epub"
        first_result = EpubBuilder(first, title="Stable").build(document)
        EpubBuilder(second, title="Stable").build(document)
        assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
        with zipfile.ZipFile(first) as archive:
            assert archive.namelist()[0] == "mimetype"
            assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
            assert archive.getinfo("mimetype").date_time == (2024, 1, 2, 3, 4, 4)
            assert archive.namelist()[1:] == sorted(archive.namelist()[1:])
            opf = archive.read("EPUB/content.opf").decode()
            assert 'property="schema:accessMode">textual' in opf
            assert 'role="doc-chapter"' in archive.read("EPUB/text/chapter_1.xhtml").decode()
        stats = ConversionStats(input_tokens=["chapter", "1", "a", "deterministic", "paragraph"])
        report = build_report(
            stats=stats,
            document=document,
            chapter_files=first_result.chapter_files,
            runtime_seconds=0.25,
            input_path=Path("input.pdf"),
            output_path=first,
            counts=first_result.counts,
        )
        assert validate_report(report) == []
        assert report["words"]["recall"] == 1.0
        assert json.loads(json.dumps(report))["chapters"]["file_count"] == 1


def test_conversion_pipeline_writes_report_next_to_epub():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source.pdf"
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text(
            (72, 90),
            "This conversion report checks that extracted words are preserved in a structured audit.",
            fontsize=12,
        )
        pdf.save(source)
        pdf.close()
        output = root / "result.epub"
        ConversionPipeline(source, output).run()
        report_path = output.with_suffix(".report.json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert output.exists()
        assert validate_report(report) == []
        assert report["input"]["format"] == "pdf"
        assert report["words"]["input"] > 0


def test_cli_returns_failure_when_recall_is_below_gate(monkeypatch, tmp_path, capsys):
    import main

    input_path = tmp_path / "input.pdf"
    input_path.write_bytes(b"test")
    output_path = tmp_path / "output.epub"
    report_path = output_path.with_suffix(".report.json")
    report_path.write_text(
        json.dumps({"words": {"recall": 0.97, "recall_threshold": 0.98, "recall_passed": False}}),
        encoding="utf-8",
    )

    class FakePipeline:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return output_path

    monkeypatch.setattr(main, "ConversionPipeline", FakePipeline)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--input", str(input_path), "--output", str(output_path)],
    )
    assert main.main() == 1
    assert "below the required" in capsys.readouterr().err


def test_epubcheck_asset_selector_ignores_unrelated_first_release_asset():
    selected = select_epubcheck_asset(
        [
            {"name": "checksums.txt", "browser_download_url": "https://invalid/checksums.txt"},
            {"name": "epubcheck-5.3.0.zip", "browser_download_url": "https://invalid/epubcheck.zip"},
        ]
    )
    assert selected["name"] == "epubcheck-5.3.0.zip"


def test_accessible_feature_epub_passes_epubcheck_when_available():
    configured_jar = os.environ.get("EPUBCHECK_JAR")
    jar = Path(configured_jar) if configured_jar else next(Path("epubcheck").rglob("epubcheck.jar"), None)
    if jar is None or not jar.exists() or shutil.which("java") is None:
        pytest.skip("EPUBCheck jar and Java are required for package validation")
    cover_buffer = io.BytesIO()
    Image.new("RGB", (160, 256), "navy").save(cover_buffer, format="JPEG")
    reference = f"{NOTE_OPEN}1{NOTE_MID}1{NOTE_CLOSE}"
    document = CleanedDocument(
        chapters=(
            ChapterContent(
                "Notes and tables",
                (
                    FlowTextUnit("paragraph", f"A sentence with note {reference}.", 20, 1, 12, 0),
                    FlowTableUnit("table", 40, 1, (("Column",), ("Value",))),
                    FlowNoteUnit("note", 1, "1", "An accessible linked note.", 1),
                ),
            ),
        ),
        cover=CoverInfo(COVER_IMAGE, 0.99, 1, jpeg=cover_buffer.getvalue(), width=160, height=256),
        title_page=TitlePage(2, "Book Title", "Subtitle", ("An Author",), ("Example Press 2024",)),
    )
    with tempfile.TemporaryDirectory() as temporary:
        epub_path = Path(temporary) / "accessible.epub"
        EpubBuilder(epub_path, "Book Title").build(document)
        result = subprocess.run(
            [shutil.which("java"), "-jar", str(jar), str(epub_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "No errors or warnings detected" in output
