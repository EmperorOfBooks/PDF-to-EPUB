from __future__ import annotations

from io import BytesIO

from docx import Document
from PIL import Image

from cleaner import FlowImageUnit, FlowTextUnit
from office_extractor import OfficeExtractor
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
