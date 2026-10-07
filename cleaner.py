from __future__ import annotations

from dataclasses import dataclass, replace
import re

import ftfy
import pyphen

from config import (
    BOLD_RATIO_THRESHOLD,
    CHAPTER_PATTERNS,
    FONT_SIZE_HEADLINE_DELTA,
    FONT_SIZE_HEADLINE_MULTIPLIER,
    PAGE_NUMBER_PATTERN,
    PARAGRAPH_X_TOLERANCE,
)
from cover import COVER_IMAGE, CoverInfo, TitlePage, detect_cover, parse_title_page
from extractor import ExtractedDocument, PageData, TableData, TextBlockData
from inline import (
    LEADING_MARKER_RE,
    link_note_refs,
    normalize_invisible_separators,
    normalize_marker,
    strip_markup,
)
from layout import LayoutAnalyzer
from telemetry import ConversionStats


@dataclass(frozen=True)
class FlowTextUnit:
    kind: str
    text: str
    y: float
    page_number: int
    font_size: float
    bold_ratio: float
    level: int = 0
    is_chapter_heading: bool = False


@dataclass(frozen=True)
class FlowImageUnit:
    kind: str
    y: float
    page_number: int
    image_bytes: bytes
    extension: str
    alt: str
    x: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    native_width: int = 0
    native_height: int = 0
    full_width: bool = False
    caption: str = ""
    is_vector: bool = False


@dataclass(frozen=True)
class FlowTableUnit:
    kind: str
    y: float
    page_number: int
    rows: tuple[tuple[str, ...], ...]
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    x: float = 0.0


@dataclass(frozen=True)
class FlowNoteUnit:
    kind: str
    note_id: int
    marker: str
    text: str
    page_number: int
    y: float = 0.0


@dataclass(frozen=True)
class FlowLineUnit:
    kind: str
    text: str
    y: float
    x: float
    page_number: int
    font_size: float
    bold_ratio: float
    is_regex_heading: bool = False
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    is_chapter_heading: bool = False


@dataclass(frozen=True)
class ChapterContent:
    title: str
    items: tuple[FlowTextUnit | FlowImageUnit | FlowTableUnit | FlowNoteUnit, ...]


@dataclass(frozen=True)
class CleanedDocument:
    chapters: tuple[ChapterContent, ...]
    cover: CoverInfo | None = None
    title_page: TitlePage | None = None
    created: str | None = None


class PdfCleaner:
    def __init__(self, layout: LayoutAnalyzer | None = None, stats: ConversionStats | None = None) -> None:
        self.layout = layout or LayoutAnalyzer()
        self.hyphenator = pyphen.Pyphen(lang="en_US")
        self.stats = stats
        self._note_counter = 0

    def _drop(self, page: PageData, bbox, reason: str, text: str = "", whole_page: bool = False) -> None:
        if self.stats is not None:
            if whole_page and bbox is None:
                bbox = (0.0, 0.0, page.width, page.height)
            self.stats.drop(page.page_number, bbox, reason, text, whole_page)

    def clean(self, document: ExtractedDocument) -> CleanedDocument:
        body_font_size = self.layout.analyze(document.pages).body_font_size
        repeated_margin_texts = self._detect_repeated_margin_texts(document)
        detection = detect_cover(document.pages)
        title_page: TitlePage | None = None
        if self.stats is not None:
            self.stats.cover = detection.info.to_report()
        chapters: list[ChapterContent] = []
        current_title = "Front Matter"
        current_items: list[FlowTextUnit | FlowImageUnit | FlowTableUnit] = []
        current_notes: list[FlowNoteUnit] = []
        current_paragraph_count = 0
        self._note_counter = 0

        for page in document.pages:
            if page.page_number == detection.cover_page:
                self._drop(page, None, "cover_page", self._page_text(page), whole_page=True)
                continue
            if page.page_number == detection.title_page:
                title_page = parse_title_page(page)
                continue
            if self._is_toc_page(page, repeated_margin_texts):
                self._drop(page, None, "table_of_contents", self._page_text(page), whole_page=True)
                continue
            for item in self._page_items(page, body_font_size, repeated_margin_texts):
                if isinstance(item, FlowNoteUnit):
                    current_notes.append(item)
                    continue
                if isinstance(item, FlowTextUnit) and item.kind == "heading":
                    if item.text and item.text.lower() != current_title.lower() and self._should_split_on_heading(item, current_paragraph_count):
                        if current_items:
                            chapters.append(ChapterContent(title=current_title, items=tuple(current_items + current_notes)))
                        current_title = item.text
                        current_items = [item]
                        current_notes = []
                        current_paragraph_count = 0
                    else:
                        if not current_items:
                            current_title = item.text or current_title
                        elif current_paragraph_count == 0:
                            current_title = self._merge_titles(current_title, item.text)
                        if current_items and current_paragraph_count == 0 and isinstance(current_items[-1], FlowTextUnit) and current_items[-1].kind == "heading":
                            previous = current_items[-1]
                            merged = self._merge_titles(previous.text, item.text)
                            current_items[-1] = FlowTextUnit(
                                kind="heading",
                                text=merged,
                                y=previous.y,
                                page_number=previous.page_number,
                                font_size=max(previous.font_size, item.font_size),
                                bold_ratio=max(previous.bold_ratio, item.bold_ratio),
                                level=previous.level,
                                is_chapter_heading=previous.is_chapter_heading,
                            )
                            current_title = merged
                        else:
                            current_items.append(item)
                else:
                    if self._can_merge_page_continuation(current_items, item):
                        previous = current_items[-1]
                        if not isinstance(previous, FlowTextUnit) or not isinstance(item, FlowTextUnit):
                            raise AssertionError("Page continuation requires text flow units")
                        separator = "" if strip_markup(previous.text).endswith("-") else " "
                        current_items[-1] = FlowTextUnit(
                            kind="paragraph",
                            text=self._strip_trailing_hyphen(previous.text) + separator + item.text,
                            y=previous.y,
                            page_number=previous.page_number,
                            font_size=max(previous.font_size, item.font_size),
                            bold_ratio=max(previous.bold_ratio, item.bold_ratio),
                            level=previous.level,
                        )
                    else:
                        current_items.append(item)
                    if isinstance(item, FlowTextUnit) and item.kind == "paragraph":
                        current_paragraph_count += 1

        if current_items or current_notes:
            chapters.append(ChapterContent(title=current_title, items=tuple(current_items + current_notes)))
        if not chapters:
            chapters.append(ChapterContent(title="Chapter 1", items=tuple()))
        return CleanedDocument(
            chapters=tuple(chapters),
            cover=detection.info,
            title_page=title_page,
            created=document.created,
        )

    @staticmethod
    def _page_text(page: PageData) -> str:
        parts = [block.text for block in page.text_blocks]
        parts.extend(cell for table in page.tables for row in table.rows for cell in row)
        return " ".join(parts)

    def _infer_body_font_size(self, document: ExtractedDocument) -> float:
        return self.layout.analyze(document.pages).body_font_size

    def _detect_repeated_margin_texts(self, document: ExtractedDocument) -> set[str]:
        return self.layout.repeated_margin_texts(document.pages)

    def _is_toc_page(self, page: PageData, repeated_margin_texts: set[str]) -> bool:
        candidate_texts: list[str] = []
        toc_like_count = 0
        for block in page.text_blocks:
            normalized = self._normalize_text(block.text)
            if not normalized or normalized in repeated_margin_texts:
                continue
            if PAGE_NUMBER_PATTERN.match(normalized):
                continue
            candidate_texts.append(normalized)
            if normalized.lower().startswith("contents") or self._looks_like_toc_entry(normalized):
                toc_like_count += 1
        joined_text = " ".join(candidate_texts).lower()
        if "contents" in joined_text and len(re.findall(r"\b(?:chapter|part|appendix)\b", joined_text)) >= 3:
            return True
        if len(candidate_texts) < 6:
            return False
        return toc_like_count / len(candidate_texts) >= 0.4

    def _page_items(
        self,
        page: PageData,
        body_font_size: float,
        repeated_margin_texts: set[str],
    ) -> list[FlowTextUnit | FlowImageUnit | FlowTableUnit | FlowNoteUnit]:
        elements: list[FlowLineUnit | FlowImageUnit | FlowTableUnit] = []
        note_units, note_block_ids, note_refs = self._collect_notes(page, body_font_size)
        used_note_refs: set[str] = set()

        for block in page.text_blocks:
            if id(block) in note_block_ids:
                continue
            normalized_block_text = self._normalize_text(block.text)
            if not normalized_block_text:
                continue
            if self.layout.is_watermark(page, block, body_font_size):
                self._drop(page, block.bbox, "watermark", block.text)
                continue
            if normalized_block_text in repeated_margin_texts:
                self._drop(page, block.bbox, "running_header_footer", block.text)
                continue
            if self.layout.is_margin_artifact(page, block, body_font_size):
                self._drop(page, block.bbox, "margin_artifact", block.text)
                continue
            if PAGE_NUMBER_PATTERN.match(normalized_block_text):
                self._drop(page, block.bbox, "page_number", block.text)
                continue

            if self._is_scene_break(normalized_block_text):
                elements.append(
                    FlowLineUnit(
                        kind="scene-break",
                        text=normalized_block_text,
                        y=float(block.bbox[1]),
                        x=float(block.bbox[0]),
                        page_number=page.page_number,
                        font_size=block.max_font_size or body_font_size,
                        bold_ratio=block.bold_ratio,
                        bbox=block.bbox,
                    )
                )
                continue

            if block.is_code:
                elements.append(self._code_element(page, block, body_font_size))
                continue

            heading_info = self._heading_info(block, body_font_size, page.height)
            if heading_info is not None:
                _, is_regex_heading, is_chapter_heading = heading_info
                if self._is_noise_text(normalized_block_text):
                    self._drop(page, block.bbox, "noise_text", block.text)
                    continue
                elements.append(
                    FlowLineUnit(
                        kind="heading",
                        text=normalized_block_text,
                        y=block.bbox[1],
                        x=block.bbox[0],
                        page_number=page.page_number,
                        font_size=block.max_font_size,
                        bold_ratio=block.bold_ratio,
                        is_regex_heading=is_regex_heading,
                        bbox=block.bbox,
                        is_chapter_heading=is_chapter_heading,
                    )
                )
                continue

            if block.lines:
                line_texts: list[str] = []
                for line in block.lines:
                    markup = link_note_refs(line.markup or line.text, note_refs, used_note_refs)
                    text = self._normalize_text(markup)
                    if text and not self._is_noise_text(text):
                        line_texts.append(text)
                    else:
                        self._drop(page, line.bbox, "noise_text", line.text)
                if not line_texts:
                    continue
                elements.append(
                    FlowLineUnit(
                        kind="line",
                        text=self._join_block_lines(line_texts),
                        y=float(block.bbox[1]),
                        x=float(block.bbox[0]),
                        page_number=page.page_number,
                        font_size=block.max_font_size or body_font_size,
                        bold_ratio=block.bold_ratio,
                        bbox=block.bbox,
                    )
                )
                continue

            if self._is_noise_text(normalized_block_text):
                self._drop(page, block.bbox, "noise_text", block.text)
                continue
            line_font_size = block.max_font_size or body_font_size
            elements.append(
                FlowLineUnit(
                    kind="line",
                    text=normalized_block_text,
                    y=float(block.bbox[1]),
                    x=float(block.bbox[0]),
                    page_number=page.page_number,
                    font_size=line_font_size,
                    bold_ratio=block.bold_ratio,
                    bbox=block.bbox,
                )
            )

        for image in page.images:
            elements.append(
                FlowImageUnit(
                    kind="image",
                    y=(image.bbox[1] + image.bbox[3]) / 2.0,
                    page_number=page.page_number,
                    image_bytes=image.image_bytes,
                    extension=image.extension,
                    alt=image.alt,
                    x=(image.bbox[0] + image.bbox[2]) / 2.0,
                    bbox=image.bbox,
                    native_width=image.native_width,
                    native_height=image.native_height,
                    full_width=self.layout.is_full_width_image(page, image.bbox),
                    is_vector=getattr(image, "is_vector", False),
                )
            )

        for table in page.tables:
            elements.append(
                FlowTableUnit(
                    kind="table",
                    y=table.bbox[1],
                    page_number=page.page_number,
                    rows=table.rows,
                    bbox=table.bbox,
                    x=table.bbox[0],
                )
            )

        order = self.layout.xy_cut_order([element.bbox for element in elements], page.width, page.height)
        elements = [elements[index] for index in order]

        items: list[FlowTextUnit | FlowImageUnit | FlowTableUnit | FlowNoteUnit] = []
        current_text = ""
        current_y = 0.0
        current_font_size = 0.0
        current_bold_ratio = 0.0
        current_page = page.page_number
        current_x = 0.0
        current_column = 0

        caption_indices: set[int] = set()
        captions: dict[int, str] = {}
        for index, element in enumerate(elements[:-1]):
            if not isinstance(element, FlowImageUnit):
                continue
            candidate = elements[index + 1]
            if not isinstance(candidate, FlowLineUnit) or candidate.kind != "line":
                continue
            if candidate.y < element.bbox[3] or candidate.y - element.bbox[3] > body_font_size * 2.0:
                continue
            if candidate.font_size > body_font_size * 0.95:
                continue
            if self.layout.column_index(page, candidate.x) != self.layout.column_index(page, element.x):
                continue
            captions[index] = candidate.text
            caption_indices.add(index + 1)

        for index, element in enumerate(elements):
            if index in caption_indices:
                continue
            if isinstance(element, (FlowImageUnit, FlowTableUnit)):
                if index in captions and isinstance(element, FlowImageUnit):
                    element = replace(element, caption=captions[index])
                if current_text:
                    items.append(
                        FlowTextUnit(
                            kind="paragraph",
                            text=current_text.strip(),
                            y=current_y,
                            page_number=current_page,
                            font_size=current_font_size,
                            bold_ratio=current_bold_ratio,
                            level=0,
                        )
                    )
                    current_text = ""
                items.append(element)
                continue

            if element.kind == "scene-break":
                if current_text:
                    items.append(
                        FlowTextUnit(
                            kind="paragraph",
                            text=current_text.strip(),
                            y=current_y,
                            page_number=current_page,
                            font_size=current_font_size,
                            bold_ratio=current_bold_ratio,
                            level=0,
                        )
                    )
                    current_text = ""
                items.append(
                    FlowTextUnit(
                        kind="scene-break",
                        text=element.text,
                        y=element.y,
                        page_number=element.page_number,
                        font_size=element.font_size,
                        bold_ratio=element.bold_ratio,
                    )
                )
                continue

            if element.kind == "code":
                if current_text:
                    items.append(
                        FlowTextUnit(
                            kind="paragraph",
                            text=current_text.strip(),
                            y=current_y,
                            page_number=current_page,
                            font_size=current_font_size,
                            bold_ratio=current_bold_ratio,
                            level=0,
                        )
                    )
                    current_text = ""
                previous_item = items[-1] if items else None
                if isinstance(previous_item, FlowTextUnit) and previous_item.kind == "code":
                    items[-1] = replace(previous_item, text=previous_item.text + "\n" + element.text)
                else:
                    items.append(
                        FlowTextUnit(
                            kind="code",
                            text=element.text,
                            y=element.y,
                            page_number=element.page_number,
                            font_size=element.font_size,
                            bold_ratio=0.0,
                            level=0,
                        )
                    )
                continue

            if element.kind == "heading":
                if current_text:
                    items.append(
                        FlowTextUnit(
                            kind="paragraph",
                            text=current_text.strip(),
                            y=current_y,
                            page_number=current_page,
                            font_size=current_font_size,
                            bold_ratio=current_bold_ratio,
                            level=0,
                        )
                    )
                    current_text = ""
                heading_text = self._clean_heading_text(element.text)
                items.append(
                    FlowTextUnit(
                        kind="heading",
                        text=heading_text,
                        y=element.y,
                        page_number=element.page_number,
                        font_size=element.font_size,
                        bold_ratio=element.bold_ratio,
                        level=1,
                        is_chapter_heading=element.is_chapter_heading,
                    )
                )
                continue

            if not current_text:
                current_text = element.text
                current_y = element.y
                current_font_size = element.font_size
                current_bold_ratio = element.bold_ratio
                current_page = element.page_number
                current_x = element.x
                current_column = self._column_index_for_page(page, element.x)
                continue

            element_column = self._column_index_for_page(page, element.x)
            if (element_column != current_column or abs(element.x - current_x) > PARAGRAPH_X_TOLERANCE) and element.page_number == current_page:
                items.append(
                    FlowTextUnit(
                        kind="paragraph",
                        text=current_text.strip(),
                        y=current_y,
                        page_number=current_page,
                        font_size=current_font_size,
                        bold_ratio=current_bold_ratio,
                        level=0,
                    )
                )
                current_text = element.text
                current_y = element.y
                current_font_size = element.font_size
                current_bold_ratio = element.bold_ratio
                current_page = element.page_number
                current_x = element.x
                current_column = element_column
                continue

            plain_current = strip_markup(current_text)
            plain_element = strip_markup(element.text)
            if plain_current.endswith("-") and plain_element and plain_element[0].islower():
                current_text = self._strip_trailing_hyphen(current_text) + element.text.lstrip()
                current_font_size = max(current_font_size, element.font_size)
                current_bold_ratio = max(current_bold_ratio, element.bold_ratio)
            elif not self._ends_sentence(current_text):
                current_text = f"{current_text} {element.text}"
                current_font_size = max(current_font_size, element.font_size)
                current_bold_ratio = max(current_bold_ratio, element.bold_ratio)
            else:
                items.append(
                    FlowTextUnit(
                        kind="paragraph",
                        text=current_text.strip(),
                        y=current_y,
                        page_number=current_page,
                        font_size=current_font_size,
                        bold_ratio=current_bold_ratio,
                        level=0,
                    )
                )
                current_text = element.text
                current_y = element.y
                current_font_size = element.font_size
                current_bold_ratio = element.bold_ratio
                current_page = element.page_number
            current_x = getattr(element, 'x', current_x)

        if current_text:
            items.append(
                FlowTextUnit(
                    kind="paragraph",
                    text=current_text.strip(),
                    y=current_y,
                    page_number=current_page,
                    font_size=current_font_size,
                    bold_ratio=current_bold_ratio,
                    level=0,
                )
            )

        return items + note_units

    def _can_merge_page_continuation(
        self,
        current_items: list[FlowTextUnit | FlowImageUnit],
        item: FlowTextUnit | FlowImageUnit,
    ) -> bool:
        if not isinstance(item, FlowTextUnit) or item.kind != "paragraph" or not item.text:
            return False
        if not current_items:
            return False
        previous = current_items[-1]
        if not isinstance(previous, FlowTextUnit) or previous.kind != "paragraph":
            return False
        if previous.page_number == item.page_number:
            return False
        return not self._ends_sentence(previous.text)

    @staticmethod
    def _strip_trailing_hyphen(text: str) -> str:
        return re.sub(r"-([\ue001-\ue00b]*)$", r"\1", text.rstrip())

    def _code_element(self, page: PageData, block: TextBlockData, body_font_size: float) -> FlowLineUnit:
        lines: list[str] = []
        for line in block.lines:
            width = line.bbox[2] - line.bbox[0]
            pitch = width / max(len(line.text), 1)
            indent = int(round((line.bbox[0] - block.bbox[0]) / pitch)) if pitch > 0 else 0
            text = normalize_invisible_separators(line.text).strip()
            lines.append(" " * max(indent, 0) + text)
        return FlowLineUnit(
            kind="code",
            text="\n".join(lines),
            y=float(block.bbox[1]),
            x=float(block.bbox[0]),
            page_number=page.page_number,
            font_size=block.max_font_size or body_font_size,
            bold_ratio=0.0,
            bbox=block.bbox,
        )

    def _collect_notes(
        self, page: PageData, body_font_size: float
    ) -> tuple[list[FlowNoteUnit], set[int], dict[str, int]]:
        """Pair footer-band note blocks with superscript markers in the running text."""
        band_top = page.height * 0.55
        candidates: list[tuple[TextBlockData, list[tuple[str, str]]]] = []
        reference_markers: set[str] = set()
        for block in page.text_blocks:
            notes: list[tuple[str, str]] = []
            small = block.max_font_size and block.max_font_size <= body_font_size * 0.95
            if block.bbox[1] >= band_top and block.lines and (small or block.lines[0].starts_with_sup):
                notes = self._split_notes(block)
            if notes:
                candidates.append((block, notes))
            else:
                for line in block.lines:
                    reference_markers.update(line.sup_tokens)
        units: list[FlowNoteUnit] = []
        block_ids: set[int] = set()
        mapping: dict[str, int] = {}
        for block, notes in candidates:
            keys = [normalize_marker(marker) for marker, _ in notes]
            if not all(key in reference_markers and key not in mapping for key in keys) or len(set(keys)) != len(keys):
                continue
            block_ids.add(id(block))
            for (marker, text), key in zip(notes, keys):
                self._note_counter += 1
                mapping[key] = self._note_counter
                units.append(
                    FlowNoteUnit(
                        kind="note",
                        note_id=self._note_counter,
                        marker=marker,
                        text=text,
                        page_number=page.page_number,
                        y=block.bbox[1],
                    )
                )
        return units, block_ids, mapping

    def _split_notes(self, block: TextBlockData) -> list[tuple[str, str]]:
        notes: list[tuple[str, list[str]]] = []
        for line in block.lines:
            match = LEADING_MARKER_RE.match(line.text.strip())
            if match:
                notes.append((match.group(1), [self._strip_leading_marker(line.markup or line.text)]))
            elif notes:
                notes[-1][1].append(line.markup or line.text)
            else:
                return []
        return [(marker, self._join_block_lines([self._normalize_text(part) for part in parts if part.strip()])) for marker, parts in notes]

    @staticmethod
    def _strip_leading_marker(markup: str) -> str:
        pattern = r"^[\ue001\ue003\ue005\ue007\s]*(?:\[\d{1,3}\]|\d{1,3}(?!\d)|[*†‡§¶]{1,3})[\ue002\ue004\ue006\ue008]*[\.\):]?\s*"
        return re.sub(pattern, "", markup, count=1)

    def _column_index_for_page(self, page: PageData, x_value: float) -> int:
        return self.layout.column_index(page, x_value)

    def _heading_info(self, block: TextBlockData, body_font_size: float, page_height: float) -> tuple[bool, bool, bool] | None:
        text = self._normalize_text(block.text)
        if not text:
            return None
        regex_heading = any(pattern.match(text) for pattern in CHAPTER_PATTERNS)
        regex_heading = regex_heading or bool(re.match(r"^chapter\s+[&s](?=\s|$)", text, flags=re.IGNORECASE))
        major_heading = len(text) <= 120 and (
            block.max_font_size >= body_font_size + FONT_SIZE_HEADLINE_DELTA
            or block.max_font_size >= body_font_size * FONT_SIZE_HEADLINE_MULTIPLIER
            or (len(text) <= 80 and block.bold_ratio >= BOLD_RATIO_THRESHOLD and block.max_font_size > body_font_size)
        )
        if regex_heading or major_heading:
            chapter_heading = regex_heading or (
                len(text) < 60
                and block.max_font_size >= body_font_size * 1.5
                and block.bbox[1] < page_height * 0.35
            )
            return major_heading or regex_heading, regex_heading, chapter_heading
        return None

    def _should_split_on_heading(self, heading: FlowTextUnit, current_paragraph_count: int) -> bool:
        if heading.kind != "heading":
            return False
        return heading.is_chapter_heading

    def _is_fragment_heading(self, text: str) -> bool:
        cleaned = self._clean_heading_text(text)
        if not cleaned:
            return True
        if self._looks_like_chapter_heading(cleaned):
            return False
        words = cleaned.split()
        return len(words) <= 2 and not any(ch.isdigit() for ch in cleaned)

    def _clean_heading_text(self, text: str) -> str:
        t = text.strip()
        t = re.sub(r"\bpg\.?\s*\d+\b$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\bpage\s*\d+\b$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\bpg\.?\s*\d+\b", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s+pg\.?\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*[:\-–—]+\s*$", "", t)
        t = re.sub(r"^(chapter)\s+1&(?=\s|$)", r"\1 14", t, flags=re.IGNORECASE)
        t = re.sub(r"^(chapter)\s+&(?=\s|$)", r"\1 4", t, flags=re.IGNORECASE)
        t = re.sub(r"^(chapter)\s+s(?=\s|$)", r"\1 5", t, flags=re.IGNORECASE)

        # Keep valid chapter labels but strip OCR marker noise like "Chapter &" or "Chapter S".
        t = re.sub(r"^(chapter\s+[0-9ivxlcdm]+)\s*&\s*", r"\1 ", t, flags=re.IGNORECASE)
        t = re.sub(r"^(chapter)\s*&\s*", r"\1 ", t, flags=re.IGNORECASE)
        t = re.sub(r"^(chapter)\s+[A-Za-z]\s+(?=[A-Z])", r"\1 ", t, flags=re.IGNORECASE)

        # Remove OCR-like ampersand artifacts around chapter numbers and titles.
        t = re.sub(r"(?<=\d)\s*&\s*(?=[A-Za-z])", "", t)
        t = re.sub(r"(?<=\d)\s*&\s*(?=\d)", "", t)
        t = re.sub(r"\b&\b", " ", t)
        t = re.sub(r"(\d)&(\d)", r"\1\2", t)
        t = re.sub(r"(\d)&([A-Za-z])", r"\1\2", t)
        t = re.sub(r"([A-Za-z])&(\d)", r"\1\2", t)

        return self._normalize_text(t)

    def _looks_like_chapter_heading(self, text: str) -> bool:
        return any(pattern.match(text) for pattern in CHAPTER_PATTERNS)

    def _looks_like_toc_entry(self, text: str) -> bool:
        return bool(re.search(r"\.\.+\s*\d+\s*$", text) or self._looks_like_chapter_heading(text))

    def _merge_titles(self, left: str, right: str) -> str:
        if not left:
            return right
        if not right:
            return left

        left = self._clean_heading_text(left)
        right = self._clean_heading_text(right)
        left_lower = left.lower()
        right_lower = right.lower()

        if re.match(r"^(chapter|part)\s+(?:&|[a-z])\s*", left_lower):
            return right
        if re.match(r"^(chapter|part)\s+(?:&|[a-z])\s*", right_lower):
            return left
        if left_lower in right_lower:
            return right
        if right_lower in left_lower:
            return left
        return f"{left} {right}".strip()

    def _line_bold_ratio(self, spans) -> float:
        spans_list = [span for span in spans if span.text.strip()]
        if not spans_list:
            return 0.0
        bold_count = sum(1 for span in spans_list if self._is_bold(span.font, span.flags))
        return bold_count / len(spans_list)

    def _join_block_lines(self, lines: list[str]) -> str:
        joined = lines[0]
        for line in lines[1:]:
            plain_joined = strip_markup(joined)
            if plain_joined.endswith("-") and line and strip_markup(line)[:1].islower():
                candidate = self._strip_trailing_hyphen(joined) + line
                if self._is_dictionary_hyphenation(strip_markup(candidate)):
                    joined = candidate
                else:
                    joined = f"{joined} {line}"
            else:
                joined = f"{joined} {line}"
        return self._normalize_text(joined)

    def _is_dictionary_hyphenation(self, word: str) -> bool:
        letters_only = re.sub(r"[^A-Za-z]", "", word)
        if not letters_only:
            return False
        return self.hyphenator.inserted(letters_only) != letters_only

    def _is_bold(self, font_name: str, flags: int) -> bool:
        lowered = font_name.lower()
        if any(keyword in lowered for keyword in ("bold", "black", "heavy", "semibold", "demi")):
            return True
        return bool(flags & 16)

    def _normalize_text(self, text: str) -> str:
        normalized = normalize_invisible_separators(ftfy.fix_text(text))
        return re.sub(r"\s+", " ", normalized).strip()

    def _is_noise_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", strip_markup(text))
        if not compact:
            return True
        if self._is_scene_break(text):
            return False
        if len(compact) <= 4 and re.fullmatch(r"[0-9&GS]+", compact, flags=re.IGNORECASE):
            return True
        if re.search(r"[A-Za-z]", compact):
            return False
        if len(compact) <= 6:
            return True
        return bool(re.fullmatch(r"[\W\d_]+", compact))

    @staticmethod
    def _ends_sentence(text: str) -> bool:
        stripped = re.sub(r"""[\s"'”’»›)\]]+$""", "", text)
        return bool(stripped and stripped[-1] in (".", "!", "?", ":"))

    @staticmethod
    def _is_scene_break(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return bool(re.fullmatch(r"(?:\*{3,}|~{3,}|-{3,})", compact))
