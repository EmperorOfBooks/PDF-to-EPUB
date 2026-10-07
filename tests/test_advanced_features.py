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

from PIL import Image
import pytest

from builder import EpubBuilder
from cleaner import ChapterContent, CleanedDocument, FlowNoteUnit, FlowTableUnit, FlowTextUnit, PdfCleaner
from cover import COVER_IMAGE, TITLE_PAGE, CoverInfo, PageVisualStats, TitlePage, classify_page_metrics, detect_cover, render_cover_jpeg
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
from tables import detect_whitespace_tables


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


def _minimal_pdf(content: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return data


def test_cover_and_titlepage_classifier_uses_extracted_page_metrics():
    kind, _ = classify_page_metrics(PageVisualStats(image_ratio=0.9, nonwhite_ratio=0.9), 12, 2, 0.2)
    assert kind == COVER_IMAGE
    kind, _ = classify_page_metrics(PageVisualStats(nonwhite_ratio=0.1), 12, 4, 0.6)
    assert kind == TITLE_PAGE


def test_rendered_cover_jpeg_uses_srgb_target_canvas(monkeypatch, tmp_path):
    import pypdfium2

    class FakePage:
        def get_size(self):
            return 612, 792

        def render(self, scale):
            return type("Bitmap", (), {"to_pil": lambda _: Image.new("RGB", (800, 1000), "navy")})()

        def close(self):
            pass

    class FakeDocument:
        def __init__(self, _path):
            pass

        def __getitem__(self, _index):
            return FakePage()

        def close(self):
            pass

    monkeypatch.setattr(pypdfium2, "PdfDocument", FakeDocument)
    content, width, height = render_cover_jpeg(tmp_path / "cover.pdf", 1)
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


def test_borderless_table_is_reconstructed_from_column_alignment():
    words = []
    for row_index, values in enumerate((("Name", "Value", "Count"), ("alpha", "1", "3"), ("beta", "2", "4"))):
        y = 100 + row_index * 25
        for value, x in zip(values, (100, 250, 400)):
            words.append((x, y, x + 40, y + 10, value))
    tables = detect_whitespace_tables(words)
    assert tables
    assert tables[0].rows[0] == ("Name", "Value", "Count")


def test_ruled_pdf_table_is_extracted_as_rows_and_cells(tmp_path):
    content = (
        b"100 642 m 400 642 l S\n100 602 m 400 602 l S\n100 562 m 400 562 l S\n"
        b"100 642 m 100 562 l S\n250 642 m 250 562 l S\n400 642 m 400 562 l S\n"
        b"BT /F1 12 Tf 110 616 Td (Name) Tj ET\nBT /F1 12 Tf 260 616 Td (Value) Tj ET\n"
        b"BT /F1 12 Tf 110 576 Td (alpha) Tj ET\nBT /F1 12 Tf 260 576 Td (1) Tj ET\n"
    )
    source = tmp_path / "table.pdf"
    source.write_bytes(_minimal_pdf(content))
    extracted = PdfExtractor(source, require_text=False).extract()
    assert extracted.pages[0].tables
    assert extracted.pages[0].tables[0].rows == (("Name", "Value"), ("alpha", "1"))
    items = [item for chapter in PdfCleaner().clean(extracted).chapters for item in chapter.items]
    assert sum(isinstance(item, FlowTableUnit) for item in items) == 1
    assert not any(isinstance(item, FlowTextUnit) and "alpha" in item.text for item in items)


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


def test_per_page_ocr_only_replaces_scan_page_and_records_drop(monkeypatch):
    import pypdfium2

    class FakePage:
        def render(self, scale):
            return type("Bitmap", (), {"to_pil": lambda _: Image.new("RGB", (100, 100), "white")})()

        def close(self):
            pass

    class FakeDocument:
        def __init__(self, _path):
            pass

        def __getitem__(self, _index):
            return FakePage()

        def close(self):
            pass

    class FakeEngine:
        name = "test-ocr"

        def recognize(self, image):
            return "OCR recovered this scanned page with enough recognized words to distinguish a regular interior text page from a sparse graphic cover image, while retaining all extracted content."

    monkeypatch.setattr(pypdfium2, "PdfDocument", FakeDocument)
    scanned_image = ImageData(2, 0, (0, 0, 612, 792), b"image", "png")
    pages = (
        PageData(1, 612, 792, (_block("This digital page already contains more than fifty useful characters for extraction.", (40, 80, 500, 100)),), ()),
        PageData(2, 612, 792, (), (scanned_image,)),
    )
    extracted = ExtractedDocument(pages, source_path="mixed.pdf")
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


def test_pdfium_extracts_empty_pages_when_text_is_not_required(tmp_path):
    import pypdfium2 as pdfium

    source = tmp_path / "blank.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(612, 792)
    document.save(str(source))
    document.close()
    extracted = PdfExtractor(source, require_text=False).extract()
    assert len(extracted.pages) == 1
    assert extracted.pages[0].visual is not None


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


def test_conversion_pipeline_writes_report_next_to_epub(monkeypatch):
    import pipeline

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source.pdf"
        source.write_bytes(b"synthetic input")
        text = "This conversion report checks that extracted words are preserved in a structured audit."
        page = PageData(1, 612, 792, (_block(text, (72, 80, 500, 100)),), (), raw_text=text)

        class FakeExtractor:
            def __init__(self, _path, require_text=True):
                pass

            def extract(self):
                return ExtractedDocument((page,), source_path=str(source))

        monkeypatch.setattr(pipeline, "PdfExtractor", FakeExtractor)
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


def test_epub_has_no_cover_page_or_cover_image():
    document = CleanedDocument(
        chapters=(ChapterContent("One", (FlowTextUnit("paragraph", "Body text.", 20, 1, 12, 0),)),),
        cover=CoverInfo(COVER_IMAGE, 0.99, 1, jpeg=b"not-used", width=160, height=256),
        title_page=TitlePage(2, "Book Title", "", ("An Author",), ()),
    )
    with tempfile.TemporaryDirectory() as temporary:
        epub_path = Path(temporary) / "nocover.epub"
        EpubBuilder(epub_path, "Book Title").build(document)
        with zipfile.ZipFile(epub_path) as archive:
            names = archive.namelist()
            opf = archive.read("EPUB/content.opf").decode()
            nav = archive.read("EPUB/nav.xhtml").decode()

    assert not any("cover" in name for name in names)
    assert "cover" not in opf
    assert 'epub:type="cover"' not in nav