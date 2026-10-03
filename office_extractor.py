from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import ftfy
import pypandoc
from docx import Document
from docx.oxml.ns import qn

from cleaner import ChapterContent, CleanedDocument, FlowImageUnit, FlowTextUnit


class OfficeExtractionError(RuntimeError):
    pass


class OfficeExtractor:
    """Convert OOXML and Pandoc-supported office formats into the shared flow model."""

    def __init__(self, input_path: str | Path) -> None:
        self.input_path = Path(input_path).expanduser().resolve()

    def extract(self) -> CleanedDocument:
        suffix = self.input_path.suffix.lower()
        if suffix == ".docx":
            return self._extract_docx()
        if suffix in {".odt", ".rtf", ".doc"}:
            return self._extract_with_pandoc()
        raise OfficeExtractionError(f"Unsupported office format: {suffix or '<none>'}")

    def _extract_docx(self) -> CleanedDocument:
        try:
            document = Document(str(self.input_path))
        except Exception as error:
            raise OfficeExtractionError(f"Unable to read DOCX: {self.input_path}") from error

        chapters: list[ChapterContent] = []
        current_title = "Front Matter"
        current_items: list[FlowTextUnit | FlowImageUnit] = []
        image_index = 0
        seen_relationships: set[str] = set()

        for position, paragraph in enumerate(document.paragraphs):
            text = self._fix_text(paragraph.text)
            level = self._heading_level(paragraph.style.name if paragraph.style else "")
            images = self._paragraph_images(paragraph, document, seen_relationships, image_index, position)
            image_index += len(images)
            if level:
                heading = FlowTextUnit(
                    kind="heading",
                    text=text or f"Section {position + 1}",
                    y=float(position),
                    page_number=1,
                    font_size=self._font_size(paragraph, level),
                    bold_ratio=self._bold_ratio(paragraph),
                    level=level,
                    is_chapter_heading=level == 1,
                )
                if level == 1 and current_items:
                    chapters.append(ChapterContent(title=current_title, items=tuple(current_items)))
                    current_items = []
                if level == 1:
                    current_title = heading.text
                current_items.append(heading)
            elif text:
                current_items.append(
                    FlowTextUnit(
                        kind="paragraph",
                        text=text,
                        y=float(position),
                        page_number=1,
                        font_size=self._font_size(paragraph, 0),
                        bold_ratio=self._bold_ratio(paragraph),
                    )
                )
            current_items.extend(images)

        if current_items or not chapters:
            chapters.append(ChapterContent(title=current_title, items=tuple(current_items)))
        return CleanedDocument(chapters=tuple(chapters))

    def _extract_with_pandoc(self) -> CleanedDocument:
        try:
            payload = pypandoc.convert_file(str(self.input_path), to="json")
            ast = json.loads(payload)
        except Exception as error:
            raise OfficeExtractionError(
                f"Unable to convert {self.input_path.name}; install Pandoc or provide a DOCX input"
            ) from error

        chapters: list[ChapterContent] = []
        current_title = "Front Matter"
        current_items: list[FlowTextUnit | FlowImageUnit] = []
        position = 0
        for block in ast.get("blocks", []):
            block_type = block.get("t")
            content = block.get("c", [])
            if block_type == "Header":
                level = int(content[0]) if content else 2
                text = self._pandoc_inlines(content[2] if len(content) > 2 else [])
                heading = FlowTextUnit(
                    kind="heading",
                    text=text,
                    y=float(position),
                    page_number=1,
                    font_size=max(12.0, 24.0 - level * 2.0),
                    bold_ratio=1.0,
                    level=level,
                    is_chapter_heading=level == 1,
                )
                if level == 1 and current_items:
                    chapters.append(ChapterContent(title=current_title, items=tuple(current_items)))
                    current_items = []
                if level == 1:
                    current_title = text
                current_items.append(heading)
            elif block_type in {"Para", "Plain"}:
                text = self._pandoc_inlines(content)
                if text:
                    current_items.append(
                        FlowTextUnit(kind="paragraph", text=text, y=float(position), page_number=1, font_size=12.0, bold_ratio=0.0)
                    )
                current_items.extend(self._pandoc_images(content, position))
            position += 1

        if current_items or not chapters:
            chapters.append(ChapterContent(title=current_title, items=tuple(current_items)))
        return CleanedDocument(chapters=tuple(chapters))

    def _paragraph_images(self, paragraph: Any, document: Any, seen: set[str], start_index: int, position: int) -> list[FlowImageUnit]:
        images: list[FlowImageUnit] = []
        for run in paragraph.runs:
            for blip in run._element.xpath('.//*[local-name()="blip"]'):
                relationship = blip.get(qn("r:embed"))
                if not relationship or relationship in seen:
                    continue
                part = document.part.related_parts.get(relationship)
                if part is None or not hasattr(part, "blob"):
                    continue
                seen.add(relationship)
                extension = self._extension_for_content_type(getattr(part, "content_type", "image/png"))
                images.append(
                    FlowImageUnit(
                        kind="image",
                        y=float(position) + 0.1,
                        page_number=1,
                        image_bytes=part.blob,
                        extension=extension,
                        alt="Embedded document image",
                        full_width=True,
                    )
                )
        return images

    @staticmethod
    def _heading_level(style_name: str) -> int:
        match = re.search(r"heading\s*([1-3])", style_name or "", flags=re.IGNORECASE)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _font_size(paragraph: Any, level: int) -> float:
        sizes = [run.font.size.pt for run in paragraph.runs if run.font.size is not None]
        return max(sizes, default=max(12.0, 22.0 - level * 2.0))

    @staticmethod
    def _bold_ratio(paragraph: Any) -> float:
        runs = [run for run in paragraph.runs if run.text.strip()]
        return sum(bool(run.bold) for run in runs) / len(runs) if runs else 0.0

    @staticmethod
    def _fix_text(text: str) -> str:
        return " ".join(ftfy.fix_text(text).split())

    @staticmethod
    def _pandoc_inlines(inlines: Iterable[dict[str, Any]]) -> str:
        parts: list[str] = []
        for inline in inlines:
            kind = inline.get("t")
            content = inline.get("c", "")
            if kind == "Str":
                parts.append(str(content))
            elif kind == "Space":
                parts.append(" ")
            elif kind in {"Code", "Math"} and isinstance(content, list):
                parts.append(str(content[-1]))
            elif kind in {"Emph", "Strong", "Underline", "Strikeout", "Quoted"} and isinstance(content, list):
                parts.append(OfficeExtractor._pandoc_inlines(content[-1] if kind == "Quoted" else content))
            elif kind == "SoftBreak":
                parts.append(" ")
        return " ".join("".join(parts).split())

    def _pandoc_images(self, inlines: Iterable[dict[str, Any]], position: int) -> list[FlowImageUnit]:
        images: list[FlowImageUnit] = []
        for inline in inlines:
            if inline.get("t") != "Image":
                continue
            content = inline.get("c", [])
            target = content[2][0] if len(content) > 2 and content[2] else ""
            image_path = (self.input_path.parent / target).resolve()
            if not image_path.exists():
                continue
            extension = image_path.suffix.lower().lstrip(".") or "png"
            images.append(
                FlowImageUnit(
                    kind="image",
                    y=float(position) + 0.1,
                    page_number=1,
                    image_bytes=image_path.read_bytes(),
                    extension=extension,
                    alt=self._pandoc_inlines(content[1] if len(content) > 1 else []),
                    full_width=True,
                )
            )
        return images

    @staticmethod
    def _extension_for_content_type(content_type: str) -> str:
        return {
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
        }.get(content_type, "png")
