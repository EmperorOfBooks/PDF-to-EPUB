from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document
from PIL import Image

from cleaner import FlowImageUnit, FlowTableUnit, FlowTextUnit
from office_extractor import OfficeExtractionError, OfficeExtractor
from router import DocumentRouter


def make_docx(path):
    document = Document()
    document.add_heading("Chapter 1", level=1)
    document.add_paragraph("Office body text.")
    image_buffer = BytesIO()
    Image.new("RGB", (32, 24), "red").save(image_buffer, format="PNG")
    document.add_picture(BytesIO(image_buffer.getvalue()))
    document.add_heading("A subsection", level=2)
    document.add_paragraph("More body text.")
    document.save(path)


def test_docx_extracts_semantic_blocks_and_embedded_images(tmp_path):
    source = tmp_path / "sample.docx"
    make_docx(source)

    cleaned = OfficeExtractor(source).extract()
    items = [item for chapter in cleaned.chapters for item in chapter.items]

    assert any(isinstance(item, FlowTextUnit) and item.kind == "heading" and item.is_chapter_heading for item in items)
    assert any(isinstance(item, FlowTextUnit) and item.kind == "paragraph" and "Office body" in item.text for item in items)
    assert any(isinstance(item, FlowImageUnit) and item.full_width for item in items)


def test_router_dispatches_docx_to_office_extractor(tmp_path):
    source = tmp_path / "sample.docx"
    make_docx(source)

    cleaned = DocumentRouter().extract(source)

    assert cleaned.chapters
    assert cleaned.chapters[0].items


def test_docx_extracts_table_in_document_order(tmp_path):
    source = tmp_path / "with_table.docx"
    document = Document()
    document.add_heading("Chapter 1", level=1)
    document.add_paragraph("Intro text.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Header A"
    table.rows[0].cells[1].text = "Header B"
    table.rows[1].cells[0].text = "Val 1"
    table.rows[1].cells[1].text = "Val 2"
    document.add_paragraph("After table.")
    document.save(source)

    cleaned = OfficeExtractor(source).extract()
    items = [item for chapter in cleaned.chapters for item in chapter.items]
    tables = [item for item in items if isinstance(item, FlowTableUnit)]

    assert len(tables) == 1
    assert tables[0].rows == (("Header A", "Header B"), ("Val 1", "Val 2"))
    assert tables[0].has_header_row


def test_doc_extraction_without_libreoffice_raises_clear_error(tmp_path, monkeypatch):
    import office_extractor

    monkeypatch.setattr(office_extractor.shutil, "which", lambda name: None)
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"not a real doc file")

    with pytest.raises(OfficeExtractionError, match="LibreOffice"):
        OfficeExtractor(source).extract()
