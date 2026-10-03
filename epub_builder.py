from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from ebooklib import epub

from cleaner import ChapterContent, CleanedDocument, FlowImageUnit, FlowTextUnit
from config import DEFAULT_LANGUAGE, DEFAULT_TITLE


@dataclass(frozen=True)
class BuildResult:
    output_path: Path


class EpubBuilder:
    def __init__(self, output_path: str | Path, title: str = DEFAULT_TITLE, language: str = DEFAULT_LANGUAGE) -> None:
        self.output_path = Path(output_path)
        self.title = title
        self.language = language

    def build(self, document: CleanedDocument) -> BuildResult:
        book = epub.EpubBook()
        book.set_identifier(self._identifier())
        book.set_title(self.title)
        book.set_language(self.language)
        book.add_author("Unknown")
        epub_chapters: list[epub.EpubHtml] = []
        toc_entries = []
        for chapter_index, chapter in enumerate(document.chapters, start=1):
            html_item, image_items = self._build_chapter(chapter_index, chapter)
            for image_item in image_items:
                book.add_item(image_item)
            book.add_item(html_item)
            epub_chapters.append(html_item)
            toc_entries.append(epub.Link(html_item.file_name, chapter.title, f"chapter_{chapter_index}"))
        book.toc = tuple(toc_entries)
        book.spine = ["nav", *epub_chapters]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        epub.write_epub(str(self.output_path), book)
        return BuildResult(output_path=self.output_path)

    def _build_chapter(self, chapter_index: int, chapter: ChapterContent) -> tuple[epub.EpubHtml, list[epub.EpubItem]]:
        html_parts: list[str] = []
        image_items: list[epub.EpubItem] = []
        image_index = 0
        previous_heading: str | None = None
        deduped_images: dict[str, str] = {}
        for item in sorted(chapter.items, key=lambda value: (value.page_number, value.y, 0 if value.kind == "heading" else 1)):
            if isinstance(item, FlowTextUnit):
                if item.kind == "heading":
                    heading_text = item.text.strip()
                    if previous_heading and self._is_title_variant(previous_heading, heading_text):
                        html_parts.append(f"<h2>{html.escape(heading_text)}</h2>")
                    else:
                        html_parts.append(f"<h1>{html.escape(heading_text)}</h1>")
                        previous_heading = heading_text
                else:
                    html_parts.append(f"<p>{html.escape(item.text)}</p>")
            elif isinstance(item, FlowImageUnit):
                image_key = f"{item.extension}:{hash(item.image_bytes[:256])}"
                if image_key in deduped_images:
                    continue
                deduped_images[image_key] = "seen"
                image_index += 1
                image_file_name = f"images/chapter_{chapter_index}_{image_index}.{self._normalize_extension(item.extension)}"
                media_type = self._media_type_for_extension(item.extension)
                image_uid = f"image_{chapter_index}_{image_index}"
                image_item = epub.EpubItem(uid=image_uid, file_name=image_file_name, media_type=media_type, content=item.image_bytes)
                image_items.append(image_item)
                alt_text = html.escape(item.alt or "Figure illustration")
                caption = f"<figcaption>{alt_text}</figcaption>" if item.alt else ""
                html_parts.append(
                    f"<figure><img src='../{html.escape(image_file_name)}' alt='{alt_text}' style='max-width:100%;height:auto;aspect-ratio:4/3;object-fit:contain;' />{caption}</figure>"
                )
        if not html_parts:
            html_parts.append("<p></p>")
        content = self._wrap_xhtml(chapter.title, "".join(html_parts))
        html_item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"text/chapter_{chapter_index}.xhtml",
            lang=self.language,
            uid=f"chapter_{chapter_index}",
        )
        html_item.set_content(content.encode("utf-8"))
        return html_item, image_items

    def _wrap_xhtml(self, title: str, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            '<head>'
            '<meta charset="utf-8" />'
            '<title>' + html.escape(title) + '</title>'
            '<style>'
            'html,body{margin:0;padding:0;background:#fff;color:#111;font-family:Georgia,"Times New Roman",serif;line-height:1.5;text-rendering:optimizeLegibility;}'
            'body{padding:2.2em 1.2em 3rem;max-width:780px;margin:0 auto;}'
            'section{display:block;}'
            'h1,h2,h3{font-weight:700;letter-spacing:-0.02em;color:#111;page-break-inside:avoid;break-inside:avoid;}'
            'h1{margin:0 0 0.3em;font-size:clamp(2.1rem,3.3vw,3.1rem);line-height:1.08;page-break-before:always;break-before:page;}'
            'h2{margin:0.55em 0 0.2em;font-size:clamp(1.55rem,2.4vw,2.2rem);line-height:1.18;page-break-before:always;break-before:page;}'
            'body > h1:first-child{page-break-before:avoid;break-before:auto;}'
            'p{margin:0 0 0.9em;font-size:1.03rem;line-height:1.5;text-align:left;text-indent:0;}'
            'figure{margin:1.6em auto 1.7em;max-width:100%;width:100%;display:block;text-align:center;}'
            'figure img{display:block;max-width:100%;height:auto;aspect-ratio:4/3;object-fit:contain;margin:0 auto;}'
            'figcaption{font-size:0.9rem;color:#444;margin-top:0.4em;text-align:center;}'
            'pre,code{font-family:SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace;}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:1em;background:#f6f6f6;border-radius:6px;}'
            'table{width:100%;border-collapse:collapse;margin:1em 0;}'
            'th,td{padding:0.5em;border:1px solid #ddd;text-align:left;vertical-align:top;}'
            'div{margin:0.5em 0;}'
            '</style>'
            '</head>'
            '<body><section epub:type="chapter" id="chapter">' + body + '</section></body>'
            '</html>'
        )

    def _media_type_for_extension(self, extension: str) -> str:
        normalized = self._normalize_extension(extension)
        if normalized in {"jpg", "jpeg"}:
            return "image/jpeg"
        if normalized == "png":
            return "image/png"
        if normalized == "gif":
            return "image/gif"
        if normalized == "webp":
            return "image/webp"
        if normalized == "bmp":
            return "image/bmp"
        return "application/octet-stream"

    def _normalize_extension(self, extension: str) -> str:
        normalized = extension.lower().strip().lstrip(".")
        return normalized or "png"

    def _is_title_variant(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        left_norm = self._title_key(left)
        right_norm = self._title_key(right)
        if not left_norm or not right_norm:
            return False
        if left_norm == right_norm:
            return True
        if left_norm in right_norm or right_norm in left_norm:
            return True
        left_words = set(left_norm.split())
        right_words = set(right_norm.split())
        if not left_words or not right_words:
            return False
        overlap = len(left_words & right_words)
        return overlap >= max(2, min(len(left_words), len(right_words)) - 1) and overlap >= 2

    def _title_key(self, value: str) -> str:
        text = html.unescape(value).strip().lower()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _identifier(self) -> str:
        return str(uuid.uuid4())
